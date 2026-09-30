// Concrete offline stereo-effect lifecycle. Included by host.cpp so SDK IIDs have one owner.
// These objects are prepared and destroyed on the child main thread, never an audio callback.
struct OpaqueState : IBStream {
    STACK_REFS;
    static constexpr int64 limit=8*1024*1024;
    std::vector<unsigned char> bytes;
    int64 position=0;
    bool exceeded=false;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        if(!obj) return kInvalidArgument;
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IBStream::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IBStream*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    tresult PLUGIN_API read(void* buffer,int32 count,int32* done) override {
        if(done) *done=0;
        if(count<0 || (!buffer && count)) return kInvalidArgument;
        auto available=std::max<int64>(0,static_cast<int64>(bytes.size())-position);
        auto n=static_cast<int32>(std::min<int64>(count,available));
        if(n) std::memcpy(buffer,bytes.data()+position,n);
        position+=n; if(done) *done=n;
        // EOF/short reads report the actual count; they are not stream failures.
        return kResultOk;
    }
    tresult PLUGIN_API write(void* buffer,int32 count,int32* done) override {
        if(done) *done=0;
        if(count<0 || (!buffer && count)) return kInvalidArgument;
        if(count>limit-position) { exceeded=true; return kResultFalse; }
        if(position+count>static_cast<int64>(bytes.size())) bytes.resize(position+count);
        if(count) std::memcpy(bytes.data()+position,buffer,count);
        position+=count; if(done) *done=count; return kResultOk;
    }
    tresult PLUGIN_API seek(int64 offset,int32 mode,int64* result) override {
        int64 base=mode==kIBSeekSet?0:mode==kIBSeekCur?position:
            mode==kIBSeekEnd?static_cast<int64>(bytes.size()):-1;
        if(base<0 || offset < -base || offset > limit-base) return kInvalidArgument;
        position=base+offset; if(result) *result=position; return kResultOk;
    }
    tresult PLUGIN_API tell(int64* result) override {
        if(!result) return kInvalidArgument; *result=position; return kResultOk;
    }
};

static void verifyStateStream() {
    OpaqueState stream;
    std::array<unsigned char,4> input{1,2,3,4},output{}; int32 done=-1;
    success(stream.write(input.data(),4,&done),"state stream write failed");
    check(done==4,"state stream write count mismatch");
    success(stream.seek(0,IBStream::kIBSeekSet,nullptr),"state stream rewind failed");
    success(stream.read(output.data(),3,&done),"state stream read failed");
    check(done==3 && output[0]==1 && output[2]==3,"state stream read mismatch");
    success(stream.read(output.data(),4,&done),"state stream short read failed");
    check(done==1 && output[0]==4,"state stream short read count mismatch");
    success(stream.read(output.data(),4,&done),"state stream EOF failed");
    check(done==0,"state stream EOF count mismatch");
    check(stream.seek(-1,IBStream::kIBSeekSet,nullptr)==kInvalidArgument,"state stream negative seek accepted");
    check(stream.seek(INT64_MAX,IBStream::kIBSeekCur,nullptr)==kInvalidArgument,"state stream overflow seek accepted");
    success(stream.seek(OpaqueState::limit,IBStream::kIBSeekSet,nullptr),"state stream limit seek failed");
    check(stream.write(input.data(),1,&done)==kResultFalse && done==0 && stream.exceeded && stream.bytes.size()==4,
        "state stream exceeded cap or mutated on failure");
}

struct EffectInstance {
    IComponent* component=nullptr;
    IAudioProcessor* processor=nullptr;
    IEditController* controller=nullptr;
    IConnectionPoint* componentConnection=nullptr;
    IConnectionPoint* controllerConnection=nullptr;
    bool initialized=false, separate=false, controllerInitialized=false;
    bool componentConnected=false, controllerConnected=false, active=false, processing=false;
    EffectInstance(Module& module,const TUID cid,Host& host,Handler& handler,int32 mode=kOffline) {
        try {
            success(module.factory->createInstance(cid,IComponent::iid,
                reinterpret_cast<void**>(&component)),"create effect failed");
            check(component!=nullptr,"effect component is null");
            success(component->initialize(&host),"effect initialize failed"); initialized=true;
            success(component->queryInterface(IAudioProcessor::iid,
                reinterpret_cast<void**>(&processor)),"effect processor unavailable");
            check(processor!=nullptr,"effect processor is null");
            auto result=component->queryInterface(IEditController::iid,reinterpret_cast<void**>(&controller));
            if((result==kNoInterface || result==kResultFalse) && !controller) {
                TUID controllerCid{};
                success(component->getControllerClassId(controllerCid),"separate controller CID unavailable");
                success(module.factory->createInstance(controllerCid,IEditController::iid,
                    reinterpret_cast<void**>(&controller)),"create separate controller failed");
                check(controller!=nullptr,"separate controller is null"); separate=true;
                success(controller->initialize(&host),"separate controller initialize failed");
                controllerInitialized=true;
            } else success(result,"combined controller query failed");
            check(controller!=nullptr,"effect controller is null");
            success(controller->setComponentHandler(&handler),"effect handler attach failed");
            if(separate) {
                auto cr=component->queryInterface(IConnectionPoint::iid,reinterpret_cast<void**>(&componentConnection));
                auto er=controller->queryInterface(IConnectionPoint::iid,reinterpret_cast<void**>(&controllerConnection));
                check(((cr==kNoInterface || cr==kResultFalse) && (er==kNoInterface || er==kResultFalse) && !componentConnection && !controllerConnection) ||
                    (cr==kResultOk && er==kResultOk && componentConnection && controllerConnection),
                    "incompatible component/controller connection points");
                if(componentConnection && controllerConnection) {
                    success(componentConnection->connect(controllerConnection),"component connect failed");
                    componentConnected=true;
                    success(controllerConnection->connect(componentConnection),"controller connect failed");
                    controllerConnected=true;
                }
            }
            check(component->getBusCount(kAudio,kInput)==1 && component->getBusCount(kAudio,kOutput)==1,
                "offline effect requires one audio input and output bus");
            check(component->getBusCount(kEvent,kInput)==0 && component->getBusCount(kEvent,kOutput)==0,
                "offline effect probe does not support event buses");
            SpeakerArrangement stereo=SpeakerArr::kStereo;
            success(processor->setBusArrangements(&stereo,1,&stereo,1),"effect rejected stereo buses");
            for(auto direction:{kInput,kOutput}) {
                BusInfo bus{}; success(component->getBusInfo(kAudio,direction,0,bus),"effect bus info failed");
                check(bus.channelCount==2,"effect bus is not stereo");
                success(component->activateBus(kAudio,direction,0,true),"effect bus activation failed");
            }
            success(processor->canProcessSampleSize(kSample32),"effect rejected float32");
            ProcessSetup setup{mode,kSample32,256,48000};
            success(processor->setupProcessing(setup),"effect offline setup failed");
        } catch(...) { close(false); throw; }
    }
    EffectInstance(const EffectInstance&)=delete;
    EffectInstance& operator=(const EffectInstance&)=delete;
    void start() {
        success(component->setActive(true),"effect activate failed"); active=true;
        success(processor->setProcessing(true),"effect start failed"); processing=true;
    }
    void stop() {
        if(processing) { success(processor->setProcessing(false),"effect stop failed"); processing=false; }
        if(active) { success(component->setActive(false),"effect deactivate failed"); active=false; }
    }
    void close(bool checked) {
        // Cleanup always continues after a failed lifecycle operation.
        bool ok=true;
        if(processing) { ok &= processor->setProcessing(false)==kResultOk; processing=false; }
        if(active) { ok &= component->setActive(false)==kResultOk; active=false; }
        if(componentConnected) { ok &= componentConnection->disconnect(controllerConnection)==kResultOk; componentConnected=false; }
        if(controllerConnected) { ok &= controllerConnection->disconnect(componentConnection)==kResultOk; controllerConnected=false; }
        if(controller) ok &= controller->setComponentHandler(nullptr)==kResultOk;
        if(controllerInitialized) { ok &= controller->terminate()==kResultOk; controllerInitialized=false; }
        if(initialized) { ok &= component->terminate()==kResultOk; initialized=false; }
        if(controllerConnection) { controllerConnection->release(); controllerConnection=nullptr; }
        if(componentConnection) { componentConnection->release(); componentConnection=nullptr; }
        if(controller) { controller->release(); controller=nullptr; }
        if(processor) { processor->release(); processor=nullptr; }
        if(component) { component->release(); component=nullptr; }
        if(checked) check(ok,"effect teardown operation failed");
    }
    ~EffectInstance() { close(false); }
};

static std::string asciiTitle(const String128 title) {
    std::string result;
    for(int i=0;i<128 && title[i];++i) result+=title[i]<128?static_cast<char>(title[i]):'?';
    return result;
}
static std::string effectProbe(Module& module) {
    verifyStateStream();
    PClassInfo selected{}; int effects=0;
    int classes=module.factory->countClasses();
    check(classes>0 && classes<=128,"effect class count outside limit");
    for(int i=0;i<classes;++i) {
        PClassInfo info{}; success(module.factory->getClassInfo(i,&info),"effect class info failed");
        if(bounded(info.category)==kVstAudioEffectClass) { selected=info; ++effects; }
    }
    check(effects==1,"effect probe requires exactly one audio class");
    Host host; Handler handler;
    OpaqueState state,controllerState;
    ParamID chosen=0; std::string title; bool hasControllerState=false, separate=false, connected=false, componentSync=true;
    uint32 latency=0; double delta=0; int count=0;
    std::vector<float> reference(8192*2), modified(8192*2), restored(8192*2);
    // The child owns buffers and all plugin lifecycle calls. No device or callback is involved.
    auto render=[&](EffectInstance& instance,ParamValue value,bool send,std::vector<float>& destination) {
        std::array<float,256> left{},right{},outLeft{},outRight{};
        Sample32* inChannels[]={left.data(),right.data()};
        Sample32* outChannels[]={outLeft.data(),outRight.data()};
        AudioBusBuffers input{},output{};
        input.numChannels=output.numChannels=2; input.channelBuffers32=inChannels; output.channelBuffers32=outChannels;
        Changes changes; changes.queue.id=chosen; changes.queue.offset=16; changes.queue.value=value;
        ProcessContext context{}; context.sampleRate=48000; context.state=ProcessContext::kPlaying|ProcessContext::kTempoValid;
        context.tempo=120;
        ProcessData data{}; data.processMode=kOffline; data.symbolicSampleSize=kSample32;
        data.numSamples=256; data.numInputs=data.numOutputs=1; data.inputs=&input; data.outputs=&output;
        data.processContext=&context;
        if(handler.restartFlags!=0) throw std::runtime_error("unsupported restart flags="+std::to_string(handler.restartFlags.load()));
        instance.start();
        for(int block=0;block<32;++block) {
            for(int i=0;i<256;++i) {
                auto frame=block*256+i;
                left[i]=static_cast<float>(.1*std::sin(frame*6.283185307179586*440/48000));
                right[i]=static_cast<float>(.07*std::sin(frame*6.283185307179586*660/48000));
            }
            outLeft.fill(0); outRight.fill(0); input.silenceFlags=output.silenceFlags=0;
            context.projectTimeSamples=block*256;
            data.inputParameterChanges=send && block==0?&changes:nullptr;
            success(instance.processor->process(data),"third-party offline process failed");
            if(handler.restartFlags!=0) throw std::runtime_error("unsupported restart flags="+std::to_string(handler.restartFlags.load()));
            for(int i=0;i<256;++i) {
                check(std::isfinite(outLeft[i]) && std::isfinite(outRight[i]),"effect produced nonfinite audio");
                destination[(block*256+i)*2]=outLeft[i]; destination[(block*256+i)*2+1]=outRight[i];
            }
        }
        instance.stop();
    };
    {
        EffectInstance instance(module,selected.cid,host,handler);
        separate=instance.separate; connected=instance.componentConnected && instance.controllerConnected;
        latency=instance.processor->getLatencySamples();
        count=instance.controller->getParameterCount(); check(count>0 && count<=4096,"effect parameter count outside limit");
        bool found=false; std::set<ParamID> ids;
        for(int i=0;i<count;++i) {
            ParameterInfo info{}; success(instance.controller->getParameterInfo(i,info),"effect parameter metadata failed");
            check(ids.insert(info.id).second,"duplicate effect parameter ID");
            if(!found && info.stepCount==0 && (info.flags & ParameterInfo::kCanAutomate) &&
                !(info.flags & (ParameterInfo::kIsReadOnly|ParameterInfo::kIsBypass))) {
                chosen=info.id; title=asciiTitle(info.title); found=true;
            }
        }
        check(found,"no continuous automatable effect parameter");
        render(instance,.2,true,reference);
        render(instance,.8,true,modified);
        for(size_t i=0;i<reference.size();++i) delta=std::max(delta,std::abs(static_cast<double>(reference[i]-modified[i])));
        check(delta>1e-6,"automation did not measurably change effect output");
        success(instance.controller->setParamNormalized(chosen,.8),"controller parameter sync failed");
        success(instance.component->getState(&state),"effect component state save failed");
        check(!state.exceeded && !state.bytes.empty(),"effect component state empty or oversized");
        auto result=instance.controller->getState(&controllerState);
        check(!controllerState.exceeded,"controller state oversized");
        hasControllerState=result==kResultOk;
        check(hasControllerState || result==kNotImplemented || result==kResultFalse,"controller state save failed");
        instance.close(true);
    }
    {
        EffectInstance fresh(module,selected.cid,host,handler);
        state.position=0;
        auto restoreResult=fresh.component->setState(&state);
        if(restoreResult!=kResultOk) throw std::runtime_error("fresh effect state restore failed: result="+
            std::to_string(restoreResult)+" bytes="+std::to_string(state.bytes.size())+" read="+std::to_string(state.position));
        state.position=0;
        auto syncResult=fresh.controller->setComponentState(&state);
        check(syncResult==kResultOk || (syncResult==kNotImplemented && connected),"fresh controller sync failed");
        componentSync=syncResult==kResultOk;
        // Some connected controllers synchronize through their shared component and do not
        // implement setComponentState. Verify before applying any independent controller state.
        check(std::abs(fresh.controller->getParamNormalized(chosen)-.8)<1e-5,"component state parameter mismatch");
        if(hasControllerState) {
            controllerState.position=0;
            success(fresh.controller->setState(&controllerState),"fresh controller state restore failed");
        }
        check(std::abs(fresh.controller->getParamNormalized(chosen)-.8)<1e-5,"restored effect parameter mismatch");
        render(fresh,0,false,restored);
        double energy=0; for(float sample:restored) energy+=sample*sample;
        check(energy>1e-8,"restored effect produced silence");
        fresh.close(true);
    }
    double restoredDelta=0;
    {
        EffectInstance fresh(module,selected.cid,host,handler);
        state.position=0; success(fresh.component->setState(&state),"comparison state restore failed");
        render(fresh,.2,true,reference);
        for(size_t i=0;i<reference.size();++i)
            restoredDelta=std::max(restoredDelta,std::abs(static_cast<double>(reference[i]-restored[i])));
        check(restoredDelta>1e-6,"restored DSP state did not differ from automation override");
        fresh.close(true);
    }
    check(host.refs==1 && handler.refs==1 && state.refs==1 && controllerState.refs==1,"effect host reference imbalance");
    return "{\"ok\":true,\"name\":"+quote(bounded(selected.name))+",\"cid\":"+quote(cidString(selected.cid))+
        ",\"frames_processed\":32768,\"sample_rate\":48000,\"parameter_count\":"+std::to_string(count)+
        ",\"automated_parameter\":{\"id\":"+std::to_string(chosen)+",\"title\":"+quote(title)+"},\"automation_output_delta\":"+
        std::to_string(delta)+",\"restored_output_delta\":"+std::to_string(restoredDelta)+",\"controller_component_sync\":"+(componentSync?"true":"false")+",\"component_state_bytes\":"+std::to_string(state.bytes.size())+
        ",\"controller_state_bytes\":"+std::to_string(controllerState.bytes.size())+
        ",\"separate_controller\":"+(separate?"true":"false")+",\"connections\":"+(connected?"true":"false")+
        ",\"latency_samples\":"+std::to_string(latency)+",\"fresh_instance_state\":true,\"terminated\":true}";
}

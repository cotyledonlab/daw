// Private binary offline-worker protocol. The Rust owner bounds bytes and child lifetime.
#include <memory>
template<class T> static T readJob() {
    T value{}; std::cin.read(reinterpret_cast<char*>(&value),sizeof(value));
    check(static_cast<bool>(std::cin),"truncated processing job"); return value;
}
template<class T> static void writeJob(T value) {
    std::cout.write(reinterpret_cast<const char*>(&value),sizeof(value));
}
struct JobQueue final : IParamValueQueue {
    STACK_REFS;
    ParamID id=0; ParamValue base=0;
    std::vector<std::pair<uint32,ParamValue>> saved;
    std::vector<std::pair<int32,ParamValue>> block;
    size_t next=0;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IParamValueQueue::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IParamValueQueue*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    ParamID PLUGIN_API getParameterId() override { return id; }
    int32 PLUGIN_API getPointCount() override { return static_cast<int32>(block.size()); }
    tresult PLUGIN_API getPoint(int32 index,int32& offset,ParamValue& value) override {
        if(index<0 || static_cast<size_t>(index)>=block.size()) return kInvalidArgument;
        offset=block[index].first; value=block[index].second; return kResultOk;
    }
    tresult PLUGIN_API addPoint(int32,ParamValue,int32&) override { return kNotImplemented; }
};
struct JobChanges : IParameterChanges {
    STACK_REFS;
    std::vector<std::unique_ptr<JobQueue>> queues;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IParameterChanges::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IParameterChanges*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    int32 PLUGIN_API getParameterCount() override { return static_cast<int32>(queues.size()); }
    IParamValueQueue* PLUGIN_API getParameterData(int32 index) override {
        return index>=0 && static_cast<size_t>(index)<queues.size()?queues[index].get():nullptr;
    }
    IParamValueQueue* PLUGIN_API addParameterData(const ParamID&,int32&) override { return nullptr; }
};
static void readState(OpaqueState& state) {
    auto size=readJob<uint32>(); check(size<=65536,"job state exceeds 64 KiB");
    state.bytes.resize(size);
    if(size) std::cin.read(reinterpret_cast<char*>(state.bytes.data()),size);
    check(static_cast<bool>(std::cin),"truncated job state");
}
static void writeText(const TChar* text) {
    std::string utf8;
    for(size_t i=0;i<128 && text[i];++i) {
        uint32 cp=static_cast<uint16>(text[i]);
        if(cp>=0xD800 && cp<=0xDBFF) {
            if(i+1<128 && text[i+1]>=0xDC00 && text[i+1]<=0xDFFF) { cp=0x10000+((cp-0xD800)<<10)+(text[++i]-0xDC00); }
            else cp=0xFFFD;
        } else if(cp>=0xDC00 && cp<=0xDFFF) cp=0xFFFD;
        if(cp<0x80) utf8.push_back(cp);
        else if(cp<0x800) { utf8.push_back(0xC0|(cp>>6)); utf8.push_back(0x80|(cp&63)); }
        else if(cp<0x10000) { utf8.push_back(0xE0|(cp>>12)); utf8.push_back(0x80|((cp>>6)&63)); utf8.push_back(0x80|(cp&63)); }
        else { utf8.push_back(0xF0|(cp>>18)); utf8.push_back(0x80|((cp>>12)&63)); utf8.push_back(0x80|((cp>>6)&63)); utf8.push_back(0x80|(cp&63)); }
    }
    writeJob<uint32>(utf8.size()); std::cout.write(utf8.data(),utf8.size());
}
static void processJob(Module& module,const std::string& cid,bool metadata=false) {
    check(cid.size()==32,"job requires exact class CID");
    PClassInfo selected{}; bool found=false;
    auto classes=module.factory->countClasses(); check(classes>0 && classes<=128,"job class count outside limit");
    for(int i=0;i<classes;++i) {
        PClassInfo info{}; success(module.factory->getClassInfo(i,&info),"job class metadata failed");
        if(cidString(info.cid)==cid && bounded(info.category)==kVstAudioEffectClass) { selected=info; found=true; }
    }
    check(found,"job class CID unavailable");
    check(readJob<uint32>()==0x34565744,"invalid job magic"); // DWV4, little-endian
    OpaqueState state,controllerState; readState(state); readState(controllerState);
    JobChanges changes; std::set<ParamID> ids; uint32 totalPoints=0;
    auto params=readJob<uint32>(); check(params<=64,"too many job parameters");
    for(uint32 i=0;i<params;++i) {
        auto queue=std::make_unique<JobQueue>(); queue->id=readJob<uint32>(); queue->base=readJob<double>();
        check(ids.insert(queue->id).second && std::isfinite(queue->base) && queue->base>=0 && queue->base<=1,
            "invalid or duplicate job parameter");
        auto points=readJob<uint32>(); check(points<=16384-totalPoints,"too many job automation points"); totalPoints+=points;
        for(uint32 j=0;j<points;++j) {
            auto frame=readJob<uint32>(); auto value=readJob<double>();
            check(std::isfinite(value) && value>=0 && value<=1 && (j==0 || frame>queue->saved.back().first),
                "invalid job automation"); queue->saved.emplace_back(frame,value);
        }
        queue->block.reserve(257); changes.queues.push_back(std::move(queue));
    }
    auto frames=readJob<uint32>(); check(!metadata || (frames==0 && totalPoints==0),"metadata job contains audio or automation"); check(frames<=480000,"offline plugin render exceeds 10 seconds");
    std::vector<float> audio(static_cast<size_t>(frames)*2);
    if(frames) std::cin.read(reinterpret_cast<char*>(audio.data()),audio.size()*sizeof(float));
    check(static_cast<bool>(std::cin),"truncated job audio");
    for(float sample:audio) check(std::isfinite(sample),"nonfinite job input");
    check(std::cin.peek()==std::char_traits<char>::eof(),"trailing job bytes");
    Host host; Handler handler;
    {
        EffectInstance effect(module,selected.cid,host,handler);
        check(effect.processor->getLatencySamples()==0,"nonzero plugin latency is unsupported");
        if(!state.bytes.empty()) {
            success(effect.component->setState(&state),"job component state restore failed"); state.position=0;
            auto result=effect.controller->setComponentState(&state);
            check(result==kResultOk || (result==kNotImplemented && effect.componentConnected),"job controller sync failed");
        }
        if(!controllerState.bytes.empty()) success(effect.controller->setState(&controllerState),"job controller restore failed");
        auto count=effect.controller->getParameterCount(); check(count>=0 && count<=4096,"job parameter count outside limit");
        for(auto& queue:changes.queues) {
            bool valid=false;
            for(int i=0;i<count;++i) {
                ParameterInfo info{}; success(effect.controller->getParameterInfo(i,info),"job parameter info failed");
                if(info.id==queue->id && (metadata || ((info.flags & ParameterInfo::kCanAutomate) && !(info.flags & ParameterInfo::kIsReadOnly)))) valid=true;
            }
            check(valid,"job parameter ID unavailable or not automatable");
        }
        if(metadata) {
            std::vector<std::pair<ParameterInfo,double>> records;
            for(auto& queue:changes.queues) {
                ParameterInfo selectedParam{}; bool foundParam=false;
                for(int i=0;i<count;++i) {
                    ParameterInfo info{}; success(effect.controller->getParameterInfo(i,info),"metadata parameter info failed");
                    if(info.id==queue->id) { check(!foundParam,"duplicate plugin parameter ID"); selectedParam=info; foundParam=true; }
                }
                check(foundParam && selectedParam.stepCount>=0,"invalid plugin parameter metadata");
                auto current=effect.controller->getParamNormalized(queue->id);
                check(std::isfinite(current) && current>=0 && current<=1 && std::isfinite(selectedParam.defaultNormalizedValue) && selectedParam.defaultNormalizedValue>=0 && selectedParam.defaultNormalizedValue<=1,"invalid normalized metadata value");
                records.emplace_back(selectedParam,current);
            }
            check(handler.restartFlags==0,"metadata requires unsupported restart");
            effect.close(true);
            check(host.refs==1 && handler.refs==1 && state.refs==1 && controllerState.refs==1,"metadata reference imbalance");
            check(module.close(),"metadata module exit failed");
            writeJob<uint32>(0x314D5744); // DWM1
            writeJob<uint32>(records.size());
            for(auto& record:records) {
                auto& info=record.first;
                writeJob<uint32>(info.id); writeText(info.title); writeText(info.shortTitle); writeText(info.units);
                writeJob<double>(info.defaultNormalizedValue); writeJob<double>(record.second);
                writeJob<uint32>((info.flags & ParameterInfo::kCanAutomate)?1:0);
                writeJob<uint32>((info.flags & ParameterInfo::kIsReadOnly)?1:0);
                writeJob<uint32>(info.stepCount);
            }
            check(static_cast<bool>(std::cout),"metadata output failed");
            return;
        }
        std::array<float,256> left{},right{},outLeft{},outRight{};
        Sample32* inputs[]={left.data(),right.data()},*outputs[]={outLeft.data(),outRight.data()};
        AudioBusBuffers in{},out{}; in.numChannels=out.numChannels=2; in.channelBuffers32=inputs; out.channelBuffers32=outputs;
        ProcessContext context{}; context.sampleRate=48000; context.tempo=120; context.state=ProcessContext::kPlaying|ProcessContext::kTempoValid;
        ProcessData data{}; data.processMode=kOffline; data.symbolicSampleSize=kSample32;
        data.numInputs=data.numOutputs=1; data.inputs=&in; data.outputs=&out; data.inputParameterChanges=&changes; data.processContext=&context;
        effect.start();
        // A validation/capture job runs one silent block to flush saved base parameters.
        auto processingFrames=frames?frames:256;
        for(uint32 position=0;position<processingFrames;position+=256) {
            auto n=std::min<uint32>(256,processingFrames-position); data.numSamples=n; context.projectTimeSamples=position;
            left.fill(0); right.fill(0); outLeft.fill(0); outRight.fill(0); in.silenceFlags=out.silenceFlags=0;
            if(frames) for(uint32 i=0;i<n;++i) { left[i]=audio[(position+i)*2]; right[i]=audio[(position+i)*2+1]; }
            for(auto& queue:changes.queues) {
                queue->block.clear(); if(position==0) queue->block.emplace_back(0,queue->base);
                if(frames) while(queue->next<queue->saved.size() && queue->saved[queue->next].first<position+n) {
                    auto point=queue->saved[queue->next++];
                    if(point.first==0) queue->block[0].second=point.second;
                    else queue->block.emplace_back(point.first-position,point.second);
                }
            }
            check(handler.restartFlags==0,"job requires unsupported component restart");
            success(effect.processor->process(data),"job processing failed");
            check(handler.restartFlags==0,"job requested unsupported component restart");
            if(frames) for(uint32 i=0;i<n;++i) {
                check(std::isfinite(outLeft[i]) && std::isfinite(outRight[i]),"job produced nonfinite audio");
                audio[(position+i)*2]=outLeft[i]; audio[(position+i)*2+1]=outRight[i];
            }
        }
        effect.stop();
        // Controller state must reflect the queued processor values, without replaying DSP edits.
        for(auto& queue:changes.queues) {
            auto value=queue->next?queue->saved[queue->next-1].second:queue->base;
            success(effect.controller->setParamNormalized(queue->id,value),"job controller parameter sync failed");
        }
        check(handler.restartFlags==0,"job capture requires unsupported component restart");
        state.bytes.clear(); state.position=0; state.exceeded=false;
        success(effect.component->getState(&state),"job component capture failed");
        check(!state.exceeded && state.bytes.size()<=65536,"job component state exceeds session limit");
        controllerState.bytes.clear(); controllerState.position=0; controllerState.exceeded=false;
        auto result=effect.controller->getState(&controllerState);
        check(result==kResultOk || result==kNotImplemented || result==kResultFalse,"job controller capture failed");
        if(result!=kResultOk) controllerState.bytes.clear();
        check(!controllerState.exceeded && controllerState.bytes.size()<=65536,"job controller state exceeds session limit");
        effect.close(true);
    }
    check(host.refs==1 && handler.refs==1 && state.refs==1 && controllerState.refs==1 && changes.refs==1,
        "job host reference imbalance");
    for(auto& queue:changes.queues) check(queue->refs==1,"job parameter queue reference imbalance");
    check(module.close(),"job module exit failed"); // No successful output before teardown.
    writeJob<uint32>(0x34565744); writeJob<uint32>(state.bytes.size());
    std::cout.write(reinterpret_cast<const char*>(state.bytes.data()),state.bytes.size());
    writeJob<uint32>(controllerState.bytes.size());
    std::cout.write(reinterpret_cast<const char*>(controllerState.bytes.data()),controllerState.bytes.size());
    writeJob<uint32>(frames);
    if(frames) std::cout.write(reinterpret_cast<const char*>(audio.data()),audio.size()*sizeof(float));
    check(static_cast<bool>(std::cout),"job output failed");
}

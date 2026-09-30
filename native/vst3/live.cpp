// In-process DSP worker bridge. No editor or lifecycle operation runs in an audio callback.
#define DAW_VST3_LIBRARY
#include "host.cpp"
#include <thread>

struct ConfigReader {
    const unsigned char* data; size_t remaining;
    template<class T> T read() {
        check(remaining>=sizeof(T),"truncated native plugin config");
        T value; std::memcpy(&value,data,sizeof(T)); data+=sizeof(T); remaining-=sizeof(T); return value;
    }
    void state(OpaqueState& target) {
        auto n=read<uint32>(); check(n<=65536 && n<=remaining,"invalid native state size");
        target.bytes.assign(data,data+n); data+=n; remaining-=n;
    }
};
struct LivePlugin {
    const std::thread::id owner=std::this_thread::get_id();
    Host host; Handler handler; Module module;
    std::unique_ptr<EffectInstance> instance;
    JobChanges changes;
    std::array<float,256> left{},right{},outLeft{},outRight{};
    Sample32* inputs[2]={left.data(),right.data()};
    Sample32* outputs[2]={outLeft.data(),outRight.data()};
    AudioBusBuffers input{},output{};
    ProcessContext context{}; ProcessData data{};
    LivePlugin(const char* path,const char* cid,const unsigned char* bytes,size_t size,double tempo):module(path) {
        check(std::isfinite(tempo) && tempo>=20 && tempo<=300,"invalid native tempo");
        ConfigReader config{bytes,size}; check(config.read<uint32>()==0x34565744,"invalid native config magic");
        OpaqueState state,controllerState; config.state(state); config.state(controllerState);
        auto count=config.read<uint32>(); check(count<=64,"too many native parameters");
        uint32 total=0; std::set<ParamID> ids;
        for(uint32 i=0;i<count;++i) {
            auto queue=std::make_unique<JobQueue>(); queue->id=config.read<uint32>(); queue->base=config.read<double>();
            check(ids.insert(queue->id).second && std::isfinite(queue->base) && queue->base>=0 && queue->base<=1,"invalid native parameter");
            auto n=config.read<uint32>(); check(n<=16384-total,"native automation exceeds limit"); total+=n;
            for(uint32 j=0;j<n;++j) {
                auto frame=config.read<uint32>(); auto value=config.read<double>();
                check(std::isfinite(value) && value>=0 && value<=1 && (j==0 || frame>queue->saved.back().first),"invalid native automation");
                queue->saved.emplace_back(frame,value);
            }
            queue->block.reserve(257); changes.queues.push_back(std::move(queue));
        }
        check(config.read<uint32>()==0 && config.remaining==0,"native config contains audio or trailing bytes");
        PClassInfo selected{}; bool found=false; auto classes=module.factory->countClasses();
        check(classes>0 && classes<=128,"native class count outside limit");
        for(int i=0;i<classes;++i) {
            PClassInfo info{}; success(module.factory->getClassInfo(i,&info),"native class info failed");
            if(cidString(info.cid)==cid && bounded(info.category)==kVstAudioEffectClass) { selected=info; found=true; }
        }
        check(found,"native class CID unavailable");
        instance=std::make_unique<EffectInstance>(module,selected.cid,host,handler,kRealtime);
        check(instance->processor->getLatencySamples()==0,"native nonzero plugin latency unsupported");
        if(!state.bytes.empty()) {
            success(instance->component->setState(&state),"native component state restore failed"); state.position=0;
            auto result=instance->controller->setComponentState(&state);
            check(result==kResultOk || (result==kNotImplemented && instance->componentConnected),"native controller sync failed");
        }
        if(!controllerState.bytes.empty()) success(instance->controller->setState(&controllerState),"native controller state restore failed");
        auto params=instance->controller->getParameterCount(); check(params>=0 && params<=4096,"native parameter count outside limit");
        for(auto& queue:changes.queues) {
            bool valid=false;
            for(int i=0;i<params;++i) {
                ParameterInfo info{}; success(instance->controller->getParameterInfo(i,info),"native parameter metadata failed");
                if(info.id==queue->id && (info.flags & ParameterInfo::kCanAutomate) && !(info.flags & ParameterInfo::kIsReadOnly)) valid=true;
            }
            check(valid,"native parameter unavailable or not automatable");
        }
        input.numChannels=output.numChannels=2; input.channelBuffers32=inputs; output.channelBuffers32=outputs;
        context.sampleRate=48000; context.tempo=tempo; context.state=ProcessContext::kPlaying|ProcessContext::kTempoValid;
        data.processMode=kRealtime; data.symbolicSampleSize=kSample32; data.numInputs=data.numOutputs=1;
        data.inputs=&input; data.outputs=&output; data.inputParameterChanges=&changes; data.processContext=&context;
        check(handler.restartFlags==0,"native setup requires unsupported restart");
        instance->start();
    }
    ~LivePlugin() { instance.reset(); }
    bool process(double* stereo,uint32 frames,uint64 position) {
        if(std::this_thread::get_id()!=owner || !frames || frames>256 || handler.restartFlags!=0) return false;
        for(uint32 i=0;i<frames;++i) {
            left[i]=static_cast<float>(stereo[i*2]); right[i]=static_cast<float>(stereo[i*2+1]);
            if(!std::isfinite(left[i]) || !std::isfinite(right[i])) return false;
        }
        for(auto& queue:changes.queues) {
            queue->block.clear(); if(position==0) queue->block.emplace_back(0,queue->base);
            while(queue->next<queue->saved.size() && queue->saved[queue->next].first<position+frames) {
                auto point=queue->saved[queue->next++];
                if(point.first<position) return false;
                if(point.first==0) queue->block[0].second=point.second;
                else queue->block.emplace_back(point.first-position,point.second);
            }
        }
        outLeft.fill(0); outRight.fill(0); input.silenceFlags=output.silenceFlags=0;
        data.numSamples=frames; context.projectTimeSamples=position;
        if(instance->processor->process(data)!=kResultOk || handler.restartFlags!=0) return false;
        for(uint32 i=0;i<frames;++i) {
            if(!std::isfinite(outLeft[i]) || !std::isfinite(outRight[i])) return false;
            stereo[i*2]=outLeft[i]; stereo[i*2+1]=outRight[i];
        }
        return true;
    }
};
extern "C" __attribute__((visibility("default")))
void* daw_vst3_create(const char* path,const char* cid,const unsigned char* config,size_t size,double tempo,char* error,size_t capacity) noexcept {
    try { return new LivePlugin(path,cid,config,size,tempo); }
    catch(const std::exception& failure) {
        if(error && capacity) { std::strncpy(error,failure.what(),capacity-1); error[capacity-1]=0; }
        return nullptr;
    } catch(...) { if(error && capacity) { std::strncpy(error,"unknown native plugin exception",capacity-1); error[capacity-1]=0; } return nullptr; }
}
extern "C" __attribute__((visibility("default")))
int daw_vst3_process(void* handle,double* stereo,uint32 frames,uint64 position) noexcept {
    if(!handle || !stereo) return -1;
    try { return static_cast<LivePlugin*>(handle)->process(stereo,frames,position)?0:-1; }
    catch(...) { return -1; }
}
extern "C" __attribute__((visibility("default")))
int daw_vst3_destroy(void* handle) noexcept {
    if(!handle) return 0;
    auto* plugin=static_cast<LivePlugin*>(handle);
    if(std::this_thread::get_id()!=plugin->owner) return -1;
    bool ok=true;
    try { plugin->instance->close(true); }
    catch(...) { ok=false; }
    ok &= plugin->host.refs==1 && plugin->handler.refs==1;
    ok &= plugin->module.close();
    delete plugin; return ok?0:-1;
}

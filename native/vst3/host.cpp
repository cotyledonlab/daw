// Offline VST3 lifecycle spike. No foreign code is loaded in the DAW controller.
#define INIT_CLASS_IID
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivstevents.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vstspeaker.h"
#include <CoreFoundation/CoreFoundation.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <set>

using namespace Steinberg;
using namespace Steinberg::Vst;

static void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
static void success(tresult result, const char* message) { check(result == kResultOk, message); }
static std::string quote(const std::string& value) {
    std::string result = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char ch : value) {
        if (ch == '"' || ch == '\\') { result += '\\'; result += ch; }
        else if (ch < 32) {
            result += "\\u00"; result += hex[ch >> 4]; result += hex[ch & 15];
        } else result += ch;
    }
    return result + '"';
}
template<size_t N> static std::string bounded(const char (&text)[N]) {
    return std::string(text, strnlen(text, N));
}
static std::string cidString(const TUID cid) {
    std::string result;
    const char* hex = "0123456789ABCDEF";
    for (int i=0; i<16; ++i) {
        auto byte = static_cast<unsigned char>(cid[i]);
        result += hex[byte >> 4]; result += hex[byte & 15];
    }
    return result;
}

struct Module {
    CFBundleRef bundle = nullptr;
    IPluginFactory* factory = nullptr;
    bool entered = false;
    using Exit = bool (*)();
    Exit exit = nullptr;
    explicit Module(const char* path) {
        auto url = CFURLCreateFromFileSystemRepresentation(nullptr,
            reinterpret_cast<const UInt8*>(path), std::strlen(path), true);
        check(url != nullptr, "invalid bundle path");
        bundle = CFBundleCreate(nullptr, url);
        CFRelease(url);
        if (!bundle) throw std::runtime_error("cannot open plugin bundle");
        try {
            check(CFBundleLoadExecutable(bundle), "cannot load plugin executable");
            auto entry = reinterpret_cast<bool (*)(CFBundleRef)>(
                CFBundleGetFunctionPointerForName(bundle, CFSTR("bundleEntry")));
            exit = reinterpret_cast<Exit>(
                CFBundleGetFunctionPointerForName(bundle, CFSTR("bundleExit")));
            check(entry && exit, "missing VST3 bundle entry/exit");
            check(entry(bundle), "plugin bundle entry failed"); entered = true;
            auto getFactory = reinterpret_cast<IPluginFactory* (*)()>(
                CFBundleGetFunctionPointerForName(bundle, CFSTR("GetPluginFactory")));
            check(getFactory != nullptr, "missing plugin factory");
            factory = getFactory();
            check(factory != nullptr, "plugin factory returned null");
        } catch (...) { close(); throw; }
    }
    Module(const Module&) = delete;
    Module& operator=(const Module&) = delete;
    bool close() {
        bool exited=true;
        if (factory) { factory->release(); factory=nullptr; }
        if (entered) { exited=exit(); entered=false; }
        if (bundle) { CFBundleUnloadExecutable(bundle); CFRelease(bundle); bundle=nullptr; }
        return exited;
    }
    ~Module() { close(); }
};

#define STACK_REFS uint32 PLUGIN_API addRef() override { return ++refs; } \
    uint32 PLUGIN_API release() override { return --refs; } \
    std::atomic<uint32> refs{1}

struct Host : IHostApplication {
    STACK_REFS;
    tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
        *obj=nullptr;
        if (FUnknownPrivate::iidEqual(iid,IHostApplication::iid) ||
            FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IHostApplication*>(this); addRef(); return kResultOk;
        }
        return kNoInterface;
    }
    tresult PLUGIN_API getName(String128 name) override {
        const char16_t text[]=u"DAW offline spike";
        std::copy(std::begin(text),std::end(text),name); return kResultOk;
    }
    tresult PLUGIN_API createInstance(TUID,TUID,void** obj) override {
        *obj=nullptr; return kNoInterface;
    }
};
// No editor or asynchronous restart support in this offline probe.
struct Handler : IComponentHandler {
    STACK_REFS;
    std::atomic<int32> restartFlags{0};
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IComponentHandler::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IComponentHandler*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    tresult PLUGIN_API beginEdit(ParamID) override { return kNotImplemented; }
    tresult PLUGIN_API performEdit(ParamID,ParamValue) override { return kNotImplemented; }
    tresult PLUGIN_API endEdit(ParamID) override { return kNotImplemented; }
    tresult PLUGIN_API restartComponent(int32 flags) override {
        // No normalized-value cache: every inspection asks the controller again.
        // Parameter-value notifications require no graph restart; other changes are unsupported.
        auto unsupported=flags & ~kParamValuesChanged;
        if(unsupported) { restartFlags.fetch_or(unsupported); return kNotImplemented; }
        return kResultOk;
    }
};
struct Memory : IBStream {
    STACK_REFS;
    std::array<unsigned char,1024> bytes{};
    int64 position=0,size=0;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if (FUnknownPrivate::iidEqual(iid,IBStream::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IBStream*>(this); addRef(); return kResultOk;
        }
        return kNoInterface;
    }
    tresult PLUGIN_API read(void* buffer,int32 count,int32* done) override {
        if (done) *done=0;
        if (count<0 || !buffer) return kInvalidArgument;
        auto n=static_cast<int32>(std::min<int64>(count,size-position));
        std::memcpy(buffer,bytes.data()+position,n); position+=n;
        if (done) *done=n; return kResultOk;
    }
    tresult PLUGIN_API write(void* buffer,int32 count,int32* done) override {
        if (done) *done=0;
        if (count<0 || !buffer || position+count>static_cast<int64>(bytes.size())) return kInvalidArgument;
        std::memcpy(bytes.data()+position,buffer,count); position+=count; size=std::max(size,position);
        if (done) *done=count; return kResultOk;
    }
    tresult PLUGIN_API seek(int64 offset,int32 mode,int64* result) override {
        int64 base=mode==kIBSeekSet?0:mode==kIBSeekCur?position:mode==kIBSeekEnd?size:-1;
        if (base<0 || offset < -base || offset > size-base) return kInvalidArgument;
        position=base+offset; if (result) *result=position; return kResultOk;
    }
    tresult PLUGIN_API tell(int64* result) override {
        if (!result) return kInvalidArgument; *result=position; return kResultOk;
    }
};
struct Queue : IParamValueQueue {
    STACK_REFS;
    int32 offset=16; ParamValue value=.25; ParamID id=0;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if (FUnknownPrivate::iidEqual(iid,IParamValueQueue::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IParamValueQueue*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    ParamID PLUGIN_API getParameterId() override { return id; }
    int32 PLUGIN_API getPointCount() override { return 1; }
    tresult PLUGIN_API getPoint(int32 index,int32& sampleOffset,ParamValue& result) override {
        if(index!=0) return kInvalidArgument; sampleOffset=offset; result=value; return kResultOk;
    }
    tresult PLUGIN_API addPoint(int32,ParamValue,int32&) override { return kNotImplemented; }
};
struct Changes : IParameterChanges {
    STACK_REFS;
    Queue queue;
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IParameterChanges::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IParameterChanges*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    int32 PLUGIN_API getParameterCount() override { return 1; }
    IParamValueQueue* PLUGIN_API getParameterData(int32 index) override { return index==0?&queue:nullptr; }
    IParamValueQueue* PLUGIN_API addParameterData(const ParamID&,int32&) override { return nullptr; }
};
struct Events : IEventList {
    STACK_REFS;
    Event event{};
    bool enabled=true;
    Events() { event.type=Event::kNoteOnEvent; event.busIndex=0; event.sampleOffset=48;
        event.noteOn.channel=0; event.noteOn.pitch=60; event.noteOn.velocity=.5f; event.noteOn.noteId=1; }
    tresult PLUGIN_API queryInterface(const TUID iid,void** obj) override {
        *obj=nullptr;
        if(FUnknownPrivate::iidEqual(iid,IEventList::iid) || FUnknownPrivate::iidEqual(iid,FUnknown::iid)) {
            *obj=static_cast<IEventList*>(this); addRef(); return kResultOk;
        } return kNoInterface;
    }
    int32 PLUGIN_API getEventCount() override { return enabled?1:0; }
    tresult PLUGIN_API getEvent(int32 index,Event& result) override {
        if(index!=0 || !enabled) return kInvalidArgument; result=event; return kResultOk;
    }
    tresult PLUGIN_API addEvent(Event&) override { return kNotImplemented; }
};

struct Instance {
    IComponent* component=nullptr;
    IAudioProcessor* processor=nullptr;
    IEditController* controller=nullptr;
    bool initialized=false,active=false,processing=false;
    ~Instance() {
        if(processing) processor->setProcessing(false);
        if(active) component->setActive(false);
        if(controller) controller->setComponentHandler(nullptr);
        if(initialized) component->terminate();
        if(controller) controller->release();
        if(processor) processor->release();
        if(component) component->release();
    }
};

static std::string scan(Module& module) {
    int count=module.factory->countClasses();
    check(count>=0 && count<=128,"plugin class count outside limit");
    std::string output="{\"ok\":true,\"classes\":[";
    for(int index=0;index<count;++index) {
        PClassInfo info{};
        success(module.factory->getClassInfo(index,&info),"class info failed");
        if(index) output+=",";
        output+="{\"cid\":"+quote(cidString(info.cid))+",\"name\":"+quote(bounded(info.name))+
            ",\"category\":"+quote(bounded(info.category))+"}";
    }
    return output+"]}";
}
static void verifyStateStream();
static std::string probe(Module& module) {
    verifyStateStream();
    const FUID expected(0xDA012345,0x67894ABC,0xBDEF0123,0x456789AB);
    PClassInfo info{};
    check(module.factory->countClasses()==1,"probe accepts only the DAW fixture");
    success(module.factory->getClassInfo(0,&info),"class info failed");
    check(FUnknownPrivate::iidEqual(info.cid,expected),"probe accepts only the DAW fixture CID");
    Host host; Handler handler;
    Instance instance;
    success(module.factory->createInstance(info.cid,IComponent::iid,
        reinterpret_cast<void**>(&instance.component)),"create component failed");
    check(instance.component!=nullptr,"component is null");
    success(instance.component->initialize(&host),"initialize failed"); instance.initialized=true;
    success(instance.component->queryInterface(IAudioProcessor::iid,
        reinterpret_cast<void**>(&instance.processor)),"audio processor unavailable");
    check(instance.processor!=nullptr,"processor is null");
    success(instance.component->queryInterface(IEditController::iid,
        reinterpret_cast<void**>(&instance.controller)),"combined controller unavailable");
    check(instance.controller!=nullptr,"controller is null");
    success(instance.controller->setComponentHandler(&handler),"handler attach failed");
    check(instance.controller->getParameterCount()==1,"expected one gain parameter");
    ParameterInfo parameter{};
    success(instance.controller->getParameterInfo(0,parameter),"parameter metadata failed");
    check(parameter.id==0 && parameter.stepCount==0 && parameter.defaultNormalizedValue==.5 &&
        (parameter.flags & ParameterInfo::kCanAutomate),"gain metadata mismatch");
    check(instance.controller->createView("editor")==nullptr,"fixture unexpectedly has an editor");
    success(instance.controller->setParamNormalized(0,.9),"controller value change failed");
    check(instance.controller->getParamNormalized(0)==.9,"controller value mismatch");
    check(instance.controller->setParamNormalized(0,2)==kInvalidArgument,"invalid controller gain accepted");
    String128 display{}; ParamValue parsed=0;
    success(instance.controller->getParamStringByValue(0,.25,display),"gain display failed");
    success(instance.controller->getParamValueByString(0,display,parsed),"gain parsing failed");
    check(parsed==.25,"gain display roundtrip failed");
    // Controller/UI changes must not alter processor gain until a process queue arrives.

    check(instance.component->getBusCount(kAudio,kInput)==1 &&
        instance.component->getBusCount(kAudio,kOutput)==1,"fixture requires one input/output audio bus");
    SpeakerArrangement arrangement=SpeakerArr::kStereo;
    success(instance.processor->setBusArrangements(&arrangement,1,&arrangement,1),"stereo arrangement rejected");
    for(auto direction:{kInput,kOutput}) {
        BusInfo bus{};
        success(instance.component->getBusInfo(kAudio,direction,0,bus),"bus info failed");
        check(bus.channelCount==2,"fixture bus must be stereo");
        success(instance.component->activateBus(kAudio,direction,0,true),"audio bus activation failed");
    }
    check(instance.component->getBusCount(kEvent,kInput)==1,"fixture requires event input");
    success(instance.component->activateBus(kEvent,kInput,0,true),"event activation failed");
    success(instance.processor->canProcessSampleSize(kSample32),"float buffers rejected");
    ProcessSetup setup{kOffline,kSample32,256,48000};
    success(instance.processor->setupProcessing(setup),"setup failed");
    check(instance.processor->getLatencySamples()==0,"fixture latency must be zero");
    success(instance.component->setActive(true),"activation failed"); instance.active=true;
    success(instance.processor->setProcessing(true),"processing activation failed"); instance.processing=true;
    std::array<float,64> left,right,outLeft{},outRight{};
    left.fill(.8f); right.fill(-.4f);
    Sample32* inputChannels[]={left.data(),right.data()};
    Sample32* outputChannels[]={outLeft.data(),outRight.data()};
    AudioBusBuffers input{},output{}; input.numChannels=output.numChannels=2;
    input.channelBuffers32=inputChannels; output.channelBuffers32=outputChannels;
    Changes changes; Events events;
    ProcessData data{}; data.processMode=kOffline; data.symbolicSampleSize=kSample32;
    data.numSamples=64; data.numInputs=data.numOutputs=1; data.inputs=&input; data.outputs=&output;
    data.inputParameterChanges=&changes; data.inputEvents=&events;
    success(instance.processor->process(data),"process failed");
    for(int i=0;i<64;++i) {
        float gain=i<16?.5f:i<48?.25f:.125f;
        check(std::abs(outLeft[i]-.8f*gain)<1e-6f &&
              std::abs(outRight[i]+.4f*gain)<1e-6f,"audio/parameter/event offset mismatch");
    }
    success(instance.processor->setProcessing(false),"processing stop failed"); instance.processing=false;
    success(instance.component->setActive(false),"deactivate failed"); instance.active=false;
    Memory state; success(instance.component->getState(&state),"save state failed");
    check(state.size==8,"fixture state must contain one double");
    state.position=0;
    success(instance.controller->setComponentState(&state),"controller state sync failed");
    check(instance.controller->getParamNormalized(0)==.25,"controller state mismatch");
    // Perturb gain in another block, then restore the captured component state.
    changes.queue.offset=0; changes.queue.value=.75; events.enabled=false;
    success(instance.component->setActive(true),"reactivate failed"); instance.active=true;
    success(instance.processor->setProcessing(true),"restart failed"); instance.processing=true;
    success(instance.processor->process(data),"parameter perturbation failed");
    for(int i=0;i<64;++i)
        check(std::abs(outLeft[i]-.6f)<1e-6f && std::abs(outRight[i]+.3f)<1e-6f,"gain perturbation was not applied");
    success(instance.processor->setProcessing(false),"processing stop failed"); instance.processing=false;
    success(instance.component->setActive(false),"deactivate failed"); instance.active=false;
    state.position=0;
    success(instance.component->setState(&state),"restore state failed");
    data.inputParameterChanges=nullptr; data.inputEvents=nullptr;
    success(instance.component->setActive(true),"restore activation failed"); instance.active=true;
    success(instance.processor->setProcessing(true),"restore processing failed"); instance.processing=true;
    success(instance.processor->process(data),"restored processing failed");
    for(int i=0;i<64;++i)
        check(std::abs(outLeft[i]-.2f)<1e-6f && std::abs(outRight[i]+.1f)<1e-6f,"restored state mismatch");
    success(instance.processor->setProcessing(false),"final processing stop failed"); instance.processing=false;
    success(instance.component->setActive(false),"final deactivate failed"); instance.active=false;
    success(instance.controller->setComponentHandler(nullptr),"handler detach failed");
    success(instance.component->terminate(),"terminate failed"); instance.initialized=false;
    instance.controller->release(); instance.controller=nullptr;
    instance.processor->release(); instance.processor=nullptr;
    instance.component->release(); instance.component=nullptr;
    // Restore into a fresh instance, so state cannot pass by leaving old object fields intact.
    success(module.factory->createInstance(info.cid,IComponent::iid,
        reinterpret_cast<void**>(&instance.component)),"recreate component failed");
    check(instance.component!=nullptr,"recreated component is null");
    success(instance.component->initialize(&host),"reinitialize failed"); instance.initialized=true;
    success(instance.component->queryInterface(IAudioProcessor::iid,
        reinterpret_cast<void**>(&instance.processor)),"recreated processor unavailable");
    check(instance.processor!=nullptr,"recreated processor is null");
    success(instance.component->queryInterface(IEditController::iid,
        reinterpret_cast<void**>(&instance.controller)),"fresh controller unavailable");
    check(instance.controller!=nullptr,"fresh controller is null");
    success(instance.controller->setComponentHandler(&handler),"fresh handler attach failed");
    success(instance.processor->setBusArrangements(&arrangement,1,&arrangement,1),"restore buses failed");
    success(instance.processor->setupProcessing(setup),"restore setup failed");
    state.position=0;
    success(instance.component->setState(&state),"fresh instance state restore failed");
    state.position=0;
    success(instance.controller->setComponentState(&state),"fresh controller state sync failed");
    check(instance.controller->getParamNormalized(0)==.25,"fresh controller state mismatch");
    success(instance.component->setActive(true),"fresh instance activate failed"); instance.active=true;
    success(instance.processor->setProcessing(true),"fresh instance start failed"); instance.processing=true;
    success(instance.processor->process(data),"fresh instance process failed");
    for(int i=0;i<64;++i)
        check(std::abs(outLeft[i]-.2f)<1e-6f && std::abs(outRight[i]+.1f)<1e-6f,"fresh instance state mismatch");
    success(instance.processor->setProcessing(false),"fresh instance stop failed"); instance.processing=false;
    success(instance.component->setActive(false),"fresh instance deactivate failed"); instance.active=false;
    success(instance.controller->setComponentHandler(nullptr),"fresh handler detach failed");
    success(instance.component->terminate(),"fresh instance terminate failed"); instance.initialized=false;
    instance.controller->release(); instance.controller=nullptr;
    instance.processor->release(); instance.processor=nullptr;
    instance.component->release(); instance.component=nullptr;
    check(handler.refs==1 && host.refs==1 && state.refs==1,"host interface reference imbalance");
    return "{\"ok\":true,\"frames_checked\":256,\"sample_rate\":48000,\"stereo\":true,\"sample_offset_parameters\":true,\"note_event\":true,\"state_restored\":true,\"new_instance_state\":true,\"terminated\":true,\"combined_controller\":true,\"controller_state\":true,\"controller_ui_independent\":true}";
}

#include "effect_probe.inc"
#include "process_job.inc"

int main(int argc,char** argv) {
    if(argc==4 && std::string(argv[1])=="process") {
        try { Module module(argv[2]); processJob(module,argv[3]); return 0; }
        catch(const std::exception& error) { std::cerr<<error.what()<<"\n"; return 1; }
    }
    if(argc!=3 || (std::string(argv[1])!="scan" && std::string(argv[1])!="probe" && std::string(argv[1])!="effect-probe")) {
        std::cout<<"{\"ok\":false,\"error\":\"usage: vst3-host scan|probe|effect-probe BUNDLE\"}\n"; return 2;
    }
    try {
        std::string result;
        { Module module(argv[2]); result=std::string(argv[1])=="scan"?scan(module):
              std::string(argv[1])=="probe"?probe(module):effectProbe(module);
          check(module.close(),"plugin bundle exit failed"); }
        std::cout<<result<<"\n"; return 0;
    } catch(const std::exception& error) {
        std::cout<<"{\"ok\":false,\"error\":"<<quote(error.what())<<"}\n"; return 1;
    }
}

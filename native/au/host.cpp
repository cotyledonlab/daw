// Bounded AUv2 offline worker and lifecycle proof. No hardware stream is opened.
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <array>
#include <algorithm>
#include <bit>
#include <cstdint>
#include <limits>
#include <set>
#include <cmath>
#include <cstring>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static constexpr UInt32 blockFrames=256, totalFrames=4096;
static constexpr double rate=48000;
static void check(bool valid,const char* message) { if(!valid) throw std::runtime_error(message); }
static void success(OSStatus status,const char* operation) {
    if(status!=noErr) throw std::runtime_error(std::string(operation)+" (OSStatus "+std::to_string(status)+")");
}
static std::string number(double value) {
    check(std::isfinite(value),"nonfinite result value");std::ostringstream output;output<<std::setprecision(17)<<value;return output.str();
}
static std::string quote(const std::string& text) {
    std::string result="\""; const char* hex="0123456789abcdef";
    for(unsigned char c:text) {
        if(c=='"' || c=='\\') {result+='\\';result+=static_cast<char>(c);}
        else if(c<32) {result+="\\u00";result+=hex[c>>4];result+=hex[c&15];}
        else result+=static_cast<char>(c);
    }
    return result+'"';
}
static std::string string(CFStringRef value) {
    check(value && CFStringGetLength(value)<=512,"invalid or oversized component name");
    std::array<char,2049> bytes{};
    check(CFStringGetCString(value,bytes.data(),bytes.size(),kCFStringEncodingUTF8),"component name conversion failed");
    return bytes.data();
}
static std::string fourCC(OSType value) {
    std::string text(4,' '); for(int i=0;i<4;++i) text[i]=static_cast<char>(value>>(24-i*8)); return text;
}
static OSType parseFourCC(const char* text) {
    check(std::strlen(text)==4,"component identity must have four ASCII characters"); OSType value=0;
    for(int i=0;i<4;++i) {auto c=static_cast<unsigned char>(text[i]);check(c>=32 && c<=126,"invalid component identity");value=(value<<8)|c;}
    return value;
}
static std::string componentJSON(AudioComponent component) {
    AudioComponentDescription description{}; success(AudioComponentGetDescription(component,&description),"read component identity");
    CFStringRef name=nullptr; success(AudioComponentCopyName(component,&name),"read component name");
    std::string label; try {label=string(name);} catch(...) {if(name) CFRelease(name);throw;} CFRelease(name);
    return "{\"type\":"+quote(fourCC(description.componentType))+",\"subtype\":"+quote(fourCC(description.componentSubType))+",\"manufacturer\":"+quote(fourCC(description.componentManufacturer))+",\"name\":"+quote(label)+"}";
}
static std::string scan() {
    AudioComponentDescription description{};description.componentType=kAudioUnitType_Effect;description.componentManufacturer=kAudioUnitManufacturer_Apple;
    AudioComponent component=nullptr;std::string result="{\"components\":[";unsigned count=0;
    while((component=AudioComponentFindNext(component,&description))) {
        if(count==128) break;
        if(count++) result+=',';
        result+=componentJSON(component);
    }
    return result+"],\"truncated\":"+(component?"true":"false")+"}";
}
struct Input {
    std::array<std::array<float,blockFrames>,2> samples{};
    UInt32 calls=0;
    const std::vector<float>* pcm=nullptr;
};
static OSStatus input(void* context,AudioUnitRenderActionFlags*,const AudioTimeStamp* time,UInt32 bus,UInt32 frames,AudioBufferList* buffers) noexcept {
    auto* source=static_cast<Input*>(context);
    if(!source || !time || !(time->mFlags&kAudioTimeStampSampleTimeValid) || !std::isfinite(time->mSampleTime) || time->mSampleTime<0 || bus!=0 || frames>blockFrames || !buffers || buffers->mNumberBuffers!=2) return kAudio_ParamError;
    if(source->pcm && (std::floor(time->mSampleTime)!=time->mSampleTime || time->mSampleTime+frames>source->pcm->size()/2)) return kAudio_ParamError;
    for(UInt32 channel=0;channel<2;++channel) {
        auto& buffer=buffers->mBuffers[channel];
        if(buffer.mNumberChannels!=1 || (buffer.mData && buffer.mDataByteSize<frames*sizeof(float))) return kAudio_ParamError;
        if(!buffer.mData) buffer.mData=source->samples[channel].data();
        buffer.mDataByteSize=frames*sizeof(float);
        auto* samples=static_cast<float*>(buffer.mData);
        for(UInt32 i=0;i<frames;++i) samples[i]=source->pcm ? (*source->pcm)[(static_cast<size_t>(time->mSampleTime)+i)*2+channel] : static_cast<float>(0.1*std::sin(2*3.14159265358979323846*1000*(time->mSampleTime+i)/rate));
    }
    ++source->calls;return noErr;
}
struct Unit {
    AudioUnit unit=nullptr;bool initialized=false;Input source;
    explicit Unit(AudioComponent component) {
        success(AudioComponentInstanceNew(component,&unit),"instantiate Audio Unit");
        try {
            for(auto scope:{kAudioUnitScope_Input,kAudioUnitScope_Output}) {
                UInt32 count=0,size=sizeof(count);success(AudioUnitGetProperty(unit,kAudioUnitProperty_ElementCount,scope,0,&count,&size),"read bus count");check(count==1,"only one input/output bus is supported");
                AudioStreamBasicDescription format{};format.mSampleRate=rate;format.mFormatID=kAudioFormatLinearPCM;
                format.mFormatFlags=static_cast<UInt32>(kAudioFormatFlagsNativeFloatPacked)|static_cast<UInt32>(kAudioFormatFlagIsNonInterleaved);
                format.mBytesPerPacket=format.mBytesPerFrame=sizeof(float);format.mFramesPerPacket=1;format.mChannelsPerFrame=2;format.mBitsPerChannel=32;
                success(AudioUnitSetProperty(unit,kAudioUnitProperty_StreamFormat,scope,0,&format,sizeof(format)),"set stereo float32 format");
            }
            UInt32 maximum=blockFrames;success(AudioUnitSetProperty(unit,kAudioUnitProperty_MaximumFramesPerSlice,kAudioUnitScope_Global,0,&maximum,sizeof(maximum)),"set maximum block frames");
            AURenderCallbackStruct callback{input,&source};success(AudioUnitSetProperty(unit,kAudioUnitProperty_SetRenderCallback,kAudioUnitScope_Input,0,&callback,sizeof(callback)),"set input callback");
            success(AudioUnitInitialize(unit),"initialize Audio Unit");initialized=true;
            verifyFormats();
            Float64 latency=0;UInt32 size=sizeof(latency);success(AudioUnitGetProperty(unit,kAudioUnitProperty_Latency,kAudioUnitScope_Global,0,&latency,&size),"read latency");check(latency==0,"nonzero latency is unsupported in this proof");
        } catch(...) {close(false);throw;}
    }
    Unit(const Unit&)=delete;Unit& operator=(const Unit&)=delete;
    void close(bool checked=true) {
        OSStatus uninitialize=noErr,dispose=noErr;
        if(unit) {if(initialized) uninitialize=AudioUnitUninitialize(unit);initialized=false;dispose=AudioComponentInstanceDispose(unit);unit=nullptr;}
        if(checked) {success(uninitialize,"uninitialize Audio Unit");success(dispose,"dispose Audio Unit");}
    }
    ~Unit() {close(false);}
    void verifyFormats() {
        for(auto scope:{kAudioUnitScope_Input,kAudioUnitScope_Output}) {
            AudioStreamBasicDescription format{};UInt32 size=sizeof(format);
            success(AudioUnitGetProperty(unit,kAudioUnitProperty_StreamFormat,scope,0,&format,&size),"verify stream format");
            check(size==sizeof(format) && format.mSampleRate==rate && format.mFormatID==kAudioFormatLinearPCM
                && format.mFormatFlags==(static_cast<UInt32>(kAudioFormatFlagsNativeFloatPacked)|static_cast<UInt32>(kAudioFormatFlagIsNonInterleaved))
                && format.mChannelsPerFrame==2 && format.mBytesPerFrame==sizeof(float) && format.mBytesPerPacket==sizeof(float)
                && format.mFramesPerPacket==1 && format.mBitsPerChannel==32,"Audio Unit negotiated an unsupported format");
        }
    }
    void cutoff(float value) {success(AudioUnitSetParameter(unit,kLowPassParam_CutoffFrequency,kAudioUnitScope_Global,0,value,0),"set cutoff");}
    float cutoff() {float value=0;success(AudioUnitGetParameter(unit,kLowPassParam_CutoffFrequency,kAudioUnitScope_Global,0,&value),"read cutoff");return value;}
    std::vector<unsigned char> state() {
        CFPropertyListRef value=nullptr;UInt32 size=sizeof(value);
        success(AudioUnitGetProperty(unit,kAudioUnitProperty_ClassInfo,kAudioUnitScope_Global,0,&value,&size),"capture AU state");
        check(value!=nullptr,"empty AU state");CFErrorRef error=nullptr;
        auto bytes=CFPropertyListCreateData(nullptr,value,kCFPropertyListBinaryFormat_v1_0,0,&error);CFRelease(value);
        if(error) CFRelease(error);
        check(bytes!=nullptr,"serialize AU state");
        auto count=CFDataGetLength(bytes);
        if(count<=0 || count>65536) {CFRelease(bytes);throw std::runtime_error("AU state exceeds 64 KiB limit");}
        std::vector<unsigned char> result(CFDataGetBytePtr(bytes),CFDataGetBytePtr(bytes)+count);CFRelease(bytes);return result;
    }
    void restore(const std::vector<unsigned char>& state) {
        check(!state.empty() && state.size()<=65536,"invalid state size");
        auto bytes=CFDataCreate(nullptr,state.data(),state.size());check(bytes!=nullptr,"state data allocation failed");
        CFErrorRef error=nullptr;auto value=CFPropertyListCreateWithData(nullptr,bytes,kCFPropertyListImmutable,nullptr,&error);CFRelease(bytes);if(error) CFRelease(error);
        check(value!=nullptr,"parse AU state failed");
        if(CFGetTypeID(value)!=CFDictionaryGetTypeID()) {CFRelease(value);throw std::runtime_error("state is not a dictionary");}
        auto dictionary=static_cast<CFDictionaryRef>(value);
        const std::array<CFStringRef,3> keys={CFSTR("type"),CFSTR("subtype"),CFSTR("manufacturer")};
        const std::array<SInt64,3> expected={kAudioUnitType_Effect,kAudioUnitSubType_LowPassFilter,kAudioUnitManufacturer_Apple};
        for(size_t i=0;i<keys.size();++i) {
            auto item=CFDictionaryGetValue(dictionary,keys[i]);SInt64 number=0;
            if(!item || CFGetTypeID(item)!=CFNumberGetTypeID() || !CFNumberGetValue(static_cast<CFNumberRef>(item),kCFNumberSInt64Type,&number) || number!=expected[i]) {
                CFRelease(value);throw std::runtime_error("state component identity mismatch");
            }
        }
        auto status=AudioUnitSetProperty(unit,kAudioUnitProperty_ClassInfo,kAudioUnitScope_Global,0,&value,sizeof(value));CFRelease(value);success(status,"restore AU state");verifyFormats();
    }
    std::vector<float> render(const std::vector<float>* pcm=nullptr) {
        source.pcm=pcm;
        const UInt32 count=pcm ? static_cast<UInt32>(pcm->size()/2) : totalFrames;
        success(AudioUnitReset(unit,kAudioUnitScope_Global,0),"reset filter history");
        std::vector<float> result(count*2);
        std::array<std::array<float,blockFrames>,2> output{};
        struct StereoBuffers {UInt32 count;AudioBuffer buffers[2];} list{};
        static_assert(offsetof(StereoBuffers,buffers)==offsetof(AudioBufferList,mBuffers));
        for(UInt32 position=0;position<count;position+=blockFrames) {
            const UInt32 frames=std::min(blockFrames,count-position);
            list.count=2;for(unsigned channel=0;channel<2;++channel) list.buffers[channel]={1,static_cast<UInt32>(frames*sizeof(float)),output[channel].data()};
            AudioTimeStamp time{};time.mFlags=kAudioTimeStampSampleTimeValid;time.mSampleTime=position;
            AudioUnitRenderActionFlags flags=0;
            success(AudioUnitRender(unit,&flags,&time,0,frames,reinterpret_cast<AudioBufferList*>(&list)),"render Audio Unit");
            check(list.count==2,"output buffer layout changed");
            for(unsigned channel=0;channel<2;++channel) {
                check(list.buffers[channel].mNumberChannels==1 && list.buffers[channel].mData && list.buffers[channel].mDataByteSize>=frames*sizeof(float),"invalid output buffer");
                const auto* data=static_cast<const float*>(list.buffers[channel].mData);
                for(UInt32 i=0;i<frames;++i) {check(std::isfinite(data[i]),"nonfinite AU output");result[(position+i)*2+channel]=data[i];}
            }
        }
        return result;
    }
};
static double rms(const std::vector<float>& samples) {double sum=0;for(size_t i=1024*2;i<samples.size();++i) sum+=samples[i]*samples[i];return std::sqrt(sum/(samples.size()-2048));}
static std::string probe(const AudioComponentDescription& description) {
    check(description.componentType==kAudioUnitType_Effect && description.componentSubType==kAudioUnitSubType_LowPassFilter && description.componentManufacturer==kAudioUnitManufacturer_Apple,"unsupported identity: this proof processes Apple AULowpass only");
    auto component=AudioComponentFindNext(nullptr,&description);check(component!=nullptr,"Apple AULowpass is unavailable");
    AudioComponentDescription actual{};success(AudioComponentGetDescription(component,&actual),"read registered component description");
    check(!(actual.componentFlags&kAudioComponentFlag_IsV3AudioUnit),"AUv3 extensions are unsupported");
    auto identity=componentJSON(component);
    Unit open(component);AudioUnitParameterInfo info{};UInt32 size=sizeof(info);
    success(AudioUnitGetProperty(open.unit,kAudioUnitProperty_ParameterInfo,kAudioUnitScope_Global,kLowPassParam_CutoffFrequency,&info,&size),"read parameter metadata");
    std::string name;
    try {name=(info.flags&kAudioUnitParameterFlag_HasCFNameString)?string(info.cfNameString):std::string(info.name,strnlen(info.name,sizeof(info.name)));}
    catch(...) {if(info.flags&kAudioUnitParameterFlag_CFNameRelease) {if(info.cfNameString) CFRelease(info.cfNameString);if(info.unitName) CFRelease(info.unitName);}throw;}
    if(info.flags&kAudioUnitParameterFlag_CFNameRelease) {if(info.cfNameString) CFRelease(info.cfNameString);if(info.unitName) CFRelease(info.unitName);}
    check(std::isfinite(info.minValue) && std::isfinite(info.maxValue) && std::isfinite(info.defaultValue) && info.minValue<=200 && info.maxValue>=10000 && (info.flags&kAudioUnitParameterFlag_IsWritable) && (info.flags&kAudioUnitParameterFlag_IsReadable),"unsupported cutoff metadata");
    open.cutoff(10000);auto bright=open.render();
    Unit closed(component);closed.cutoff(200);auto state=closed.state();auto dark=closed.render();
    Unit restored(component);restored.cutoff(10000);restored.restore(state);auto restoredCutoff=restored.cutoff();auto again=restored.render();
    double difference=0;for(size_t i=0;i<dark.size();++i) difference=std::max(difference,std::abs(static_cast<double>(dark[i])-again[i]));
    auto calls=open.source.calls+closed.source.calls+restored.source.calls;
    check(rms(bright)>rms(dark)*5 && rms(dark)>0 && difference<1e-6 && std::abs(restoredCutoff-200)<0.01 && calls>0,"parameter/state processing verification failed");
    open.close();closed.close();restored.close();
    return "{\"component\":"+identity+",\"sample_rate\":48000,\"channels\":2,\"frames\":4096,\"block_frames\":256,\"parameter\":{\"id\":0,\"name\":"+quote(name)+",\"min\":"+number(info.minValue)+",\"max\":"+number(info.maxValue)+",\"default\":"+number(info.defaultValue)+",\"unit\":"+std::to_string(info.unit)+"},\"open_rms\":"+number(rms(bright))+",\"closed_rms\":"+number(rms(dark))+",\"restored_rms\":"+number(rms(again))+",\"max_restore_error\":"+number(difference)+",\"state_bytes\":"+std::to_string(state.size())+",\"restored_cutoff\":"+number(restoredCutoff)+",\"input_callback_calls\":"+std::to_string(calls)+",\"resources_released\":true}";
}

static constexpr uint32_t magic=0x31554144;
static uint64_t readInteger(size_t size) {
    uint64_t value=0;
    for(size_t i=0;i<size;++i) {int byte=std::cin.get();check(byte!=EOF,"truncated AU request");value|=static_cast<uint64_t>(byte)<<(i*8);}
    return value;
}
static void writeInteger(uint64_t value,size_t size) {
    for(size_t i=0;i<size;++i) std::cout.put(static_cast<char>(value>>(i*8)));
}
static void process(const AudioComponentDescription& description) {
    check(description.componentType==kAudioUnitType_Effect && description.componentSubType==kAudioUnitSubType_LowPassFilter && description.componentManufacturer==kAudioUnitManufacturer_Apple,"unsupported component: only Apple AULowpass is supported");
    check(readInteger(4)==magic,"invalid AU request magic");
    auto stateSize=readInteger(4);check(stateSize<=65536,"AU state exceeds 64 KiB");
    std::vector<unsigned char> state(stateSize);
    for(auto& byte:state) byte=static_cast<unsigned char>(readInteger(1));
    auto parameterCount=readInteger(4);check(parameterCount<=64,"too many AU parameters");
    std::vector<std::pair<UInt32,double>> parameters;std::set<UInt32> ids;
    for(size_t i=0;i<parameterCount;++i) {
        auto id=static_cast<UInt32>(readInteger(4));double value=std::bit_cast<double>(readInteger(8));
        check(ids.insert(id).second && std::isfinite(value) && std::isfinite(static_cast<float>(value)),"invalid or duplicate AU parameter");
        parameters.emplace_back(id,value);
    }
    auto frames=readInteger(4);check(frames<=480000,"AU renders are limited to ten seconds");
    std::vector<float> audio(frames*2);
    for(auto& value:audio) {value=std::bit_cast<float>(static_cast<uint32_t>(readInteger(4)));check(std::isfinite(value),"nonfinite AU input");}
    check(std::cin.get()==EOF,"trailing AU request data");
    auto component=AudioComponentFindNext(nullptr,&description);check(component!=nullptr,"Apple AULowpass is unavailable");
    AudioComponentDescription actual{};success(AudioComponentGetDescription(component,&actual),"read component");
    check(!(actual.componentFlags&kAudioComponentFlag_IsV3AudioUnit),"AUv3 is unsupported");
    Unit unit(component);
    if(!state.empty()) unit.restore(state);
    for(const auto& [id,value]:parameters) {
        AudioUnitParameterInfo info{};UInt32 size=sizeof(info);
        auto status=AudioUnitGetProperty(unit.unit,kAudioUnitProperty_ParameterInfo,kAudioUnitScope_Global,id,&info,&size);
        if(status!=noErr) throw std::runtime_error("unknown AU parameter");
        if(info.flags&kAudioUnitParameterFlag_CFNameRelease) {if(info.cfNameString) CFRelease(info.cfNameString);if(info.unitName) CFRelease(info.unitName);}
        check(size==sizeof(info) && std::isfinite(info.minValue) && std::isfinite(info.maxValue) && value>=info.minValue && value<=info.maxValue && (info.flags&kAudioUnitParameterFlag_IsWritable) && (info.flags&kAudioUnitParameterFlag_IsReadable),"AU parameter outside native writable range");
        success(AudioUnitSetParameter(unit.unit,id,kAudioUnitScope_Global,0,static_cast<float>(value),0),"set native AU parameter");
        float restored=0;success(AudioUnitGetParameter(unit.unit,id,kAudioUnitScope_Global,0,&restored),"read native AU parameter");
        check(std::isfinite(restored) && std::abs(restored-static_cast<float>(value))<=std::max(1e-5f,std::abs(restored)*1e-6f),"AU parameter readback mismatch");
    }
    unit.verifyFormats();
    Float64 latency=0;UInt32 size=sizeof(latency);success(AudioUnitGetProperty(unit.unit,kAudioUnitProperty_Latency,kAudioUnitScope_Global,0,&latency,&size),"verify restored latency");check(latency==0,"nonzero AU latency is unsupported");
    auto captured=unit.state();auto output=unit.render(&audio);unit.close();
    writeInteger(magic,4);writeInteger(captured.size(),4);
    for(auto byte:captured) std::cout.put(static_cast<char>(byte));
    writeInteger(frames,4);for(float value:output) writeInteger(std::bit_cast<uint32_t>(value),4);
    std::cout.flush();check(std::cout.good(),"write AU response failed");
}
int main(int argc,char** argv) {
    try {
        if(argc==5 && std::string(argv[1])=="process") {
            AudioComponentDescription description{};description.componentType=parseFourCC(argv[2]);description.componentSubType=parseFourCC(argv[3]);description.componentManufacturer=parseFourCC(argv[4]);
            process(description);return 0;
        }
        std::string result;
        if(argc==2 && std::string(argv[1])=="scan") result=scan();
        else if((argc==2 || argc==5) && std::string(argv[1])=="probe") {
            AudioComponentDescription description{};description.componentType=kAudioUnitType_Effect;description.componentSubType=kAudioUnitSubType_LowPassFilter;description.componentManufacturer=kAudioUnitManufacturer_Apple;
            if(argc==5) {description.componentType=parseFourCC(argv[2]);description.componentSubType=parseFourCC(argv[3]);description.componentManufacturer=parseFourCC(argv[4]);}
            result=probe(description);
        } else throw std::runtime_error("usage: au-host scan | probe [type subtype manufacturer]");
        std::cout<<"{\"schema_version\":1,\"ok\":true,\"result\":"<<result<<"}\n";return 0;
    } catch(const std::exception& error) {
        if(argc>1 && std::string(argv[1])=="process") {std::cerr<<error.what()<<"\n";return 1;}
        std::cout<<"{\"schema_version\":1,\"ok\":false,\"error\":{\"code\":\"au_error\",\"message\":"<<quote(error.what())<<"}}\n";return 1;
    }
}

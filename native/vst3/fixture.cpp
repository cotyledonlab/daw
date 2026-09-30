#define INIT_CLASS_IID

#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivstevents.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vstspeaker.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>

#if defined(__APPLE__)
#include <CoreFoundation/CoreFoundation.h>
#include <unistd.h>
#endif

namespace {

using namespace Steinberg;
using namespace Steinberg::Vst;

constexpr ParamID kGainParam = 0;
const FUID kFixtureCid (0xDA012345, 0x67894ABC, 0xBDEF0123, 0x456789AB);

bool iidIs (const TUID iid, const FUID& expected) {
	return FUnknownPrivate::iidEqual (iid, expected);
}

void fixtureFailureMode () {
	const char* mode = std::getenv ("DAW_VST3_FIXTURE_MODE");
	if (!mode) return;
	if (std::strcmp (mode, "crash") == 0) std::abort ();
	if (std::strcmp (mode, "hang") == 0) {
#if defined(__APPLE__)
		for (;;) sleep (3600);
#else
		for (;;) {}
#endif
	}
}

class TestGain final : public IComponent, public IAudioProcessor, public IEditController {
public:
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override {
		if (!obj) return kInvalidArgument;
		*obj = nullptr;
		if (iidIs (iid, FUnknown::iid) || iidIs (iid, IComponent::iid))
			*obj = static_cast<IComponent*> (this);
		else if (iidIs (iid, IAudioProcessor::iid))
			*obj = static_cast<IAudioProcessor*> (this);
		else if (iidIs (iid, IEditController::iid))
			*obj = static_cast<IEditController*> (this);
		else
			return kNoInterface;
		addRef ();
		return kResultOk;
	}

	uint32 PLUGIN_API addRef () override { return ++refs_; }
	uint32 PLUGIN_API release () override {
		const uint32 remaining = --refs_;
		if (remaining == 0) delete this;
		return remaining;
	}

	tresult PLUGIN_API initialize (FUnknown*) override {
		if (initialized_) return kResultFalse;
		eventInputActive_ = std::getenv("DAW_VST3_FIXTURE_NO_EVENTS") == nullptr;
		initialized_ = true;
		return kResultOk;
	}
	tresult PLUGIN_API terminate () override {
		if (!initialized_ || active_ || processing_ || handler_) return kResultFalse;
		initialized_ = false;
		return kResultOk;
	}
	tresult PLUGIN_API getControllerClassId (TUID) override { return kNoInterface; }
	tresult PLUGIN_API setIoMode (IoMode) override { return initialized_ ? kResultOk : kResultFalse; }
	int32 PLUGIN_API getBusCount (MediaType type, BusDirection dir) override {
		if (!initialized_) return 0;
		if (type == kAudio) return dir == kInput || dir == kOutput ? 1 : 0;
		if (type == kEvent) return dir == kInput && eventInputActive_ ? 1 : 0;
		return 0;
	}
	tresult PLUGIN_API getBusInfo (MediaType type, BusDirection dir, int32 index,
	                              BusInfo& bus) override {
		if (!initialized_ || index != 0 ||
		    (type != kAudio && type != kEvent) ||
		    (dir != kInput && dir != kOutput) || (type == kEvent && dir == kOutput))
			return kInvalidArgument;
		bus.mediaType = type;
		bus.direction = dir;
		bus.channelCount = type == kAudio ? 2 : 16;
		bus.busType = kMain;
		bus.flags = BusInfo::kDefaultActive;
		const char* name = type == kAudio
		                       ? (dir == kInput ? "Stereo In" : "Stereo Out")
		                       : "Events In";
		String128 wideName {};
		for (size_t i = 0; name[i] && i + 1 < 128; ++i)
			wideName[i] = static_cast<char16> (name[i]);
		std::memcpy (bus.name, wideName, sizeof (wideName));
		return kResultOk;
	}
	tresult PLUGIN_API getRoutingInfo (RoutingInfo&, RoutingInfo&) override { return kNotImplemented; }
	tresult PLUGIN_API activateBus (MediaType type, BusDirection dir, int32 index,
	                               TBool state) override {
		if (!initialized_ || active_ || index != 0 ||
		    (type != kAudio && type != kEvent) || dir != kInput && dir != kOutput ||
		    (type == kEvent && dir == kOutput)) return kResultFalse;
		if (type == kAudio && dir == kInput) audioInputActive_ = state != 0;
		if (type == kAudio && dir == kOutput) audioOutputActive_ = state != 0;
		if (type == kEvent) eventInputActive_ = state != 0;
		return kResultOk;
	}
	tresult PLUGIN_API setActive (TBool state) override {
		if (!initialized_ || processing_) return kResultFalse;
		active_ = state != 0;
		if (!active_) velocity_ = 1.0;
		return kResultOk;
	}
	tresult PLUGIN_API setState (IBStream* stream) override {
		if (!stream) return kInvalidArgument;
		double value = 0.0;
		int32 read = 0;
		if (stream->read (&value, static_cast<int32> (sizeof value), &read) != kResultOk ||
		    read != static_cast<int32> (sizeof value) || !std::isfinite (value) ||
		    value < 0.0 || value > 1.0) return kResultFalse;
		gain_ = value;
		velocity_ = 1.0;
		return kResultOk;
	}
	tresult PLUGIN_API getState (IBStream* stream) override {
		if (!stream) return kInvalidArgument;
		int32 written = 0;
		return stream->write (&gain_, static_cast<int32> (sizeof gain_), &written) == kResultOk &&
		               written == static_cast<int32> (sizeof gain_)
		           ? kResultOk
		           : kResultFalse;
	}
	tresult PLUGIN_API setComponentState (IBStream* stream) override {
		if (!stream) return kInvalidArgument;
		double value = 0.0;
		int32 read = 0;
		if (stream->read (&value, static_cast<int32> (sizeof value), &read) != kResultOk ||
		    read != static_cast<int32> (sizeof value) || !std::isfinite (value) ||
		    value < 0.0 || value > 1.0) return kResultFalse;
		controllerGain_ = value;
		return kResultOk;
	}
	int32 PLUGIN_API getParameterCount () override { return 1; }
	tresult PLUGIN_API getParameterInfo (int32 index, ParameterInfo& info) override {
		if (index != 0) return kInvalidArgument;
		info = {};
		info.id = kGainParam;
		setAscii (info.title, "Gain");
		setAscii (info.shortTitle, "Gain");
		setAscii (info.units, "linear");
		info.stepCount = 0;
		info.defaultNormalizedValue = 0.5;
		info.unitId = 0;
		info.flags = ParameterInfo::kCanAutomate;
		return kResultOk;
	}
	tresult PLUGIN_API getParamStringByValue (ParamID id, ParamValue normalized, String128 text) override {
		if (id != kGainParam || !text || !std::isfinite (normalized) || normalized < 0.0 || normalized > 1.0)
			return kInvalidArgument;
		char buffer[32] {};
		if (std::snprintf (buffer, sizeof buffer, "%.6g", normalized) <= 0) return kResultFalse;
		setAscii (text, buffer);
		return kResultOk;
	}
	tresult PLUGIN_API getParamValueByString (ParamID id, TChar* text, ParamValue& normalized) override {
		if (id != kGainParam || !text) return kInvalidArgument;
		char buffer[64] {};
		size_t i = 0;
		for (; i + 1 < sizeof buffer && text[i] != 0; ++i) {
			if (text[i] > 0x7f) return kResultFalse;
			buffer[i] = static_cast<char> (text[i]);
		}
		if (text[i] != 0 || i == 0) return kResultFalse;
		char* end = nullptr;
		const double value = std::strtod (buffer, &end);
		if (end == buffer || *end != '\0' || !std::isfinite (value) || value < 0.0 || value > 1.0)
			return kResultFalse;
		normalized = value;
		return kResultOk;
	}
	ParamValue PLUGIN_API normalizedParamToPlain (ParamID id, ParamValue value) override {
		return id == kGainParam ? value : 0.0;
	}
	ParamValue PLUGIN_API plainParamToNormalized (ParamID id, ParamValue value) override {
		return id == kGainParam ? value : 0.0;
	}
	ParamValue PLUGIN_API getParamNormalized (ParamID id) override {
		return id == kGainParam ? controllerGain_ : 0.0;
	}
	tresult PLUGIN_API setParamNormalized (ParamID id, ParamValue value) override {
		if (id != kGainParam || !std::isfinite (value) || value < 0.0 || value > 1.0)
			return kInvalidArgument;
		controllerGain_ = value;
		return kResultOk;
	}
	tresult PLUGIN_API setComponentHandler (IComponentHandler* handler) override {
		if (handler == handler_) return kResultOk;
		if (handler) handler->addRef ();
		auto* previous = handler_;
		handler_ = handler;
		if (previous) previous->release ();
		return kResultOk;
	}
	IPlugView* PLUGIN_API createView (FIDString) override { return nullptr; }

	tresult PLUGIN_API setBusArrangements (SpeakerArrangement* inputs, int32 numInputs,
	                                      SpeakerArrangement* outputs, int32 numOutputs) override {
		if (!initialized_ || active_ || numInputs != 1 || numOutputs != 1 || !inputs || !outputs ||
	    inputs[0] != SpeakerArr::kStereo || outputs[0] != SpeakerArr::kStereo) return kResultFalse;
		return kResultOk;
	}
	tresult PLUGIN_API getBusArrangement (BusDirection dir, int32 index,
	                                     SpeakerArrangement& arrangement) override {
		if (!initialized_ || index != 0 || (dir != kInput && dir != kOutput)) return kInvalidArgument;
		arrangement = SpeakerArr::kStereo;
		return kResultOk;
	}
	tresult PLUGIN_API canProcessSampleSize (int32 size) override {
		return size == kSample32 ? kResultOk : kResultFalse;
	}
	uint32 PLUGIN_API getLatencySamples () override { return 0; }
	tresult PLUGIN_API setupProcessing (ProcessSetup& setup) override {
		if (!initialized_ || active_ || setup.processMode != kOffline ||
		    setup.symbolicSampleSize != kSample32 || setup.sampleRate != 48000.0 ||
		    setup.maxSamplesPerBlock != 256) return kResultFalse;
		setupDone_ = true;
		return kResultOk;
	}
	tresult PLUGIN_API setProcessing (TBool state) override {
		if (!initialized_ || !active_ || !setupDone_ || processing_ == (state != 0)) return kResultFalse;
		processing_ = state != 0;
		return kResultOk;
	}
	tresult PLUGIN_API process (ProcessData& data) override {
		if (!processing_ || data.symbolicSampleSize != kSample32 || data.numSamples <= 0 ||
		    data.numSamples > 256 || data.numInputs != 1 || data.numOutputs != 1 ||
		    !data.inputs || !data.outputs || !audioOutputActive_) return kResultFalse;
		auto& input = data.inputs[0];
		auto& output = data.outputs[0];
		if (!output.channelBuffers32 || output.numChannels < 2) return kResultFalse;
		const float* in[2] = {nullptr, nullptr};
		if (audioInputActive_ && input.channelBuffers32 && input.numChannels >= 2) {
			in[0] = input.channelBuffers32[0];
			in[1] = input.channelBuffers32[1];
		}

		int32 paramCount = data.inputParameterChanges
		                       ? data.inputParameterChanges->getParameterCount ()
	                       : 0;
		int32 paramIndex = 0;
		int32 paramOffset = std::numeric_limits<int32>::max ();
		ParamValue paramValue = gain_;
		IParamValueQueue* gainQueue = nullptr;
		for (int32 q = 0; q < paramCount; ++q) {
			auto* queue = data.inputParameterChanges->getParameterData (q);
			if (queue && queue->getParameterId () == kGainParam) {
				gainQueue = queue;
				break;
			}
		}
		if (gainQueue && gainQueue->getPointCount () > 0) {
			if (gainQueue->getPoint (0, paramOffset, paramValue) != kResultOk) return kResultFalse;
			paramIndex = 1;
		}

		int32 eventCount = data.inputEvents && eventInputActive_
		                       ? data.inputEvents->getEventCount ()
		                       : 0;
		int32 eventIndex = 0;
		Event event {};
		bool hasEvent = eventCount > 0 && data.inputEvents->getEvent (0, event) == kResultOk;
		if (eventCount > 0 && !hasEvent) return kResultFalse;

		for (int32 frame = 0; frame < data.numSamples; ++frame) {
			while (gainQueue && paramOffset <= frame) {
				if (!std::isfinite (paramValue) || paramValue < 0.0 || paramValue > 1.0) return kResultFalse;
				gain_ = paramValue;
				if (paramIndex >= gainQueue->getPointCount ()) {
                    paramOffset = std::numeric_limits<int32>::max ();
                    break;
                }
				if (gainQueue->getPoint (paramIndex++, paramOffset, paramValue) != kResultOk) return kResultFalse;
			}
			while (hasEvent && event.sampleOffset <= frame) {
				if (event.sampleOffset < 0 || event.sampleOffset >= data.numSamples) return kResultFalse;
				if (event.type == Event::kNoteOnEvent) velocity_ = std::clamp<double> (event.noteOn.velocity, 0.0, 1.0);
				else if (event.type == Event::kNoteOffEvent) velocity_ = 1.0;
				if (++eventIndex >= eventCount) hasEvent = false;
				else if (data.inputEvents->getEvent (eventIndex, event) != kResultOk) return kResultFalse;
			}
			const float multiplier = static_cast<float> (gain_ * velocity_);
			for (int32 channel = 0; channel < 2; ++channel) {
				if (!output.channelBuffers32[channel]) return kResultFalse;
				output.channelBuffers32[channel][frame] = in[channel] ? in[channel][frame] * multiplier : 0.0f;
			}
		}
		return kResultOk;
	}
	uint32 PLUGIN_API getTailSamples () override { return kNoTail; }

private:
	template <size_t N>
	static void setAscii (char16 (&destination)[N], const char* source) {
		size_t i = 0;
		for (; source[i] && i + 1 < N; ++i) destination[i] = static_cast<char16> (source[i]);
		destination[i] = 0;
	}
	static void setAscii (char16* destination, const char* source) {
		size_t i = 0;
	for (; source[i] && i + 1 < 128; ++i) destination[i] = static_cast<char16> (source[i]);
		destination[i] = 0;
	}
	~TestGain () = default;
	std::atomic<uint32> refs_ {1};
	bool initialized_ = false;
	bool setupDone_ = false;
	bool active_ = false;
	bool processing_ = false;
	bool audioInputActive_ = true;
	bool audioOutputActive_ = true;
	bool eventInputActive_ = true;
	double gain_ = 0.5;
	double controllerGain_ = 0.5;
	double velocity_ = 1.0;
	IComponentHandler* handler_ = nullptr;
};

class TestFactory final : public IPluginFactory {
public:
	tresult PLUGIN_API queryInterface (const TUID iid, void** obj) override {
		if (!obj) return kInvalidArgument;
		*obj = nullptr;
		if (!iidIs (iid, FUnknown::iid) && !iidIs (iid, IPluginFactory::iid)) return kNoInterface;
		*obj = static_cast<IPluginFactory*> (this);
		addRef ();
		return kResultOk;
	}
	uint32 PLUGIN_API addRef () override { return ++refs_; }
	uint32 PLUGIN_API release () override {
		const uint32 remaining = --refs_;
		if (remaining == 0) delete this;
		return remaining;
	}
	tresult PLUGIN_API getFactoryInfo (PFactoryInfo* info) override {
		if (!info) return kInvalidArgument;
		*info = PFactoryInfo ("DAW", "", "", PFactoryInfo::kNoFlags);
		return kResultOk;
	}
	int32 PLUGIN_API countClasses () override { return 1; }
	tresult PLUGIN_API getClassInfo (int32 index, PClassInfo* info) override {
		if (index != 0 || !info) return kInvalidArgument;
		*info = PClassInfo (kFixtureCid, PClassInfo::kManyInstances, kVstAudioEffectClass,
		                    "DAW test gain");
		return kResultOk;
	}
	tresult PLUGIN_API createInstance (FIDString cid, FIDString iid, void** obj) override {
		if (!cid || !iid || !obj) return kInvalidArgument;
		*obj = nullptr;
		if (!iidIs (cid, kFixtureCid)) return kNoInterface;
		auto* component = new TestGain;
		const tresult result = component->queryInterface (iid, obj);
		component->release ();
		return result;
	}

private:
	~TestFactory () = default;
	std::atomic<uint32> refs_ {1};
};

} // namespace

extern "C" __attribute__ ((visibility ("default"))) Steinberg::IPluginFactory* PLUGIN_API
GetPluginFactory () {
	fixtureFailureMode ();
	return new TestFactory;
}

#if defined(__APPLE__)
extern "C" __attribute__ ((visibility ("default"))) bool bundleEntry (CFBundleRef) { return true; }
extern "C" __attribute__ ((visibility ("default"))) bool bundleExit () { return true; }
#else
extern "C" __attribute__ ((visibility ("default"))) bool bundleEntry (void*) { return true; }
extern "C" __attribute__ ((visibility ("default"))) bool bundleExit () { return true; }
#endif

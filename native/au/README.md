# Standalone Audio Unit proof

This macOS-only AUv2 spike checks that a desktop Audio Unit can be found, instantiated, configured, rendered offline in bounded blocks, and restored from saved state. It is an isolated host experiment; it does not add Audio Unit support to the DAW session model, renderer, native player, or GUI.

## Build and run

```sh
python3 native/au/build.py
output/au-spike/au-host scan
python3 -m native.au.probe
```

The build uses the system `xcrun clang++` in C++20 mode and links only the macOS AudioToolbox and CoreFoundation frameworks. The executable is written under ignored `output/au-spike/`. `scan` lists at most 128 registered Apple effect components. The Python wrapper runs the default Apple AULowpass proof in an owned child process. To select a component explicitly, pass its three four-character ASCII fields:

```sh
python3 -m native.au.probe aufx lpas appl
```

The scanner can report other registered Apple effects, but processing is deliberately limited to `aufx` / `lpas` / `appl` (Apple AULowpass). The probe is not a general plugin compatibility test. The wrapper bounds the child to 10 seconds, 256 KiB stdout, and 64 KiB stderr, and terminates its process group on timeout or excess output. Results use a standalone `schema_version: 1` JSON envelope with structured failures; this is not the DAW JSONL protocol.

## What the proof exercises

It configures one stereo, non-interleaved float32 input/output at 48 kHz with a 256-frame maximum block. A preallocated input callback supplies a 1 kHz sine at amplitude 0.1; no hardware device is opened. Each instance renders 4096 frames through `AudioUnitRender`. The proof reads the cutoff parameter metadata, compares 10 kHz and 200 Hz renders, captures the latter instance's `kAudioUnitProperty_ClassInfo` state as a bounded binary property list, and restores that state into a fresh third instance. It resets filter history before each render, checks that the restored output matches the original low-cutoff output to less than 1e-6 maximum sample error, and requires the high-cutoff RMS to exceed the low-cutoff RMS by five times. All three instances are uninitialized and disposed before reporting success.

On the tested host, Apple component registration exposed 23 Apple effects, including AULowpass. The proof's measured open-cutoff RMS was 0.070968; the closed and restored RMS were 0.002876. These are fixture evidence from a synthetic signal, not a performance or audio-device measurement. No callback allocation instrumentation, throughput benchmark, or acoustic verification was performed.

## Scope and next step

This is T10a's standalone lifecycle proof, not completion of T10. It does not establish a portable session identity or parameter/state contract, transactional DAW load/render integration, live playback, or general AU compatibility. AUv3 extensions, third-party discovery and processing, instruments, plugin windows, events, automation, latency conversion, and live callback hosting are unsupported. T10b should derive a strict identity, native parameter-range, and serialized-state contract from this working example, then integrate a transactional offline child worker before any live-hosting design.

Apple API references: [AudioComponentFindNext](https://developer.apple.com/documentation/audiotoolbox/audiocomponentfindnext(_:_:)), [AudioUnitRender](https://developer.apple.com/documentation/audiotoolbox/audiounitrender(_:_:_:_:_:_:)), and [Audio Unit properties](https://developer.apple.com/documentation/audiotoolbox/general-audio-unit-properties).

Five repeated Apple AULowpass lifecycle runs with AddressSanitizer and UndefinedBehaviorSanitizer completed without diagnostics. This instruments the host proof, not Apple’s closed-source DSP, and does not establish live callback safety.

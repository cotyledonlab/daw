# Audio Unit hosting: standalone proof boundary

The first Audio Unit step is a small macOS-only AUv2 proof under `native/au/`. It is useful evidence for lifecycle mechanics, not an application hosting contract. The DAW must continue to report Audio Units as unavailable until a later ticket adds a validated session representation and transactional offline integration.

## Established by the proof

The native program scans registered Apple effect components with `AudioComponentFindNext`, capped at 128 entries. It can instantiate and initialize Apple's `aufx` / `lpas` / `appl` AULowpass, set stereo non-interleaved float32 formats at 48 kHz, install an input render callback, and render through `AudioUnitRender` in fixed 256-frame blocks. It rejects layouts other than one input and one output bus, requires zero reported latency, and uses a 4096-frame synthetic 1 kHz signal at amplitude 0.1. No hardware stream is opened.

The callback uses preallocated sample arrays and fills the requested input buffers. Allocation and callback timing were not instrumented, so this does not establish a real-time safety or performance claim. The proof reads cutoff metadata, sets values of 10 kHz and 200 Hz on separate instances, and demonstrates that the resulting RMS differs by more than a factor of five. It captures `kAudioUnitProperty_ClassInfo` as a binary property list limited to 64 KiB, restores it into a fresh instance, and verifies the restored cutoff and render. Filter history is reset before each render; restored samples match the original low-cutoff render within 1e-6 maximum absolute error. The three instances are uninitialized and disposed before success is returned.

The process boundary is owned by `native/au/probe.py`: it validates optional four-character printable ASCII identity fields, uses an argument array, starts a process group, caps stdout at 256 KiB and stderr at 64 KiB, and enforces a maximum 10-second timeout. The child returns a versioned JSON envelope with structured failures. This wrapper and envelope belong only to the spike; they do not alter the DAW's protocol or error contract.

## Not established

Only the Apple AULowpass identity is accepted for rendering, even though scanning can list Apple effects. No third-party unit was tested. The reported registration scan found 23 Apple effects on the test host; baseline discovery from the restricted test environment had returned none. Installed-plugin registration and actual processing therefore depend on the host environment. Measured synthetic-signal RMS values were 0.070968 at 10 kHz and 0.002876 at 200 Hz (also after restore). These values verify this known effect path; they say nothing about acoustic output or speed.

There is no session schema, stable serialized component identity policy, parameter normalization/range policy, saved state compatibility policy, Rust adapter, offline render integration, live playback, or GUI capability. AUv3, instruments, event buses, plugin windows, automation, latency compensation, and general component discovery/processing remain unsupported. The C++ sample callback is not the DAW's hardware callback and the current offline renderer is not a callback-ready hosting design.

## Decision for the next slice

Treat this work as T10a only. T10b should define identity from the exact component tuple, preserve native parameter IDs and ranges without guessing universal normalization, and establish bounded state serialization using evidence from multiple working adapters before making it a general contract. Integrate through an owned offline child with full validation and preparation before session replacement; child failures must leave the active session unchanged. Keep discovery separate from saved-session loading, and keep live hosting out of scope until a separate ownership and callback design is reviewed.

Primary API references: [AudioComponentFindNext](https://developer.apple.com/documentation/audiotoolbox/audiocomponentfindnext(_:_:)), [AudioUnitRender](https://developer.apple.com/documentation/audiotoolbox/audiounitrender(_:_:_:_:_:)), and [Audio Unit properties](https://developer.apple.com/documentation/audiotoolbox/general-audio-unit-properties). The SDK headers used by the macOS build are also authoritative for constants, layouts, and property signatures.

Five repeated Apple AULowpass lifecycle runs with AddressSanitizer and UndefinedBehaviorSanitizer completed without diagnostics. This instruments the host proof, not Apple’s closed-source DSP, and does not establish live callback safety.

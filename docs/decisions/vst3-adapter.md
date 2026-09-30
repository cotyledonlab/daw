# VST3 adapter boundaries

T09c implements the offline boundary through schema-v4 sessions and JSONL rendering. T09d adds an experimental in-process native playback path behind `vst3-live`. `native/vst3/effect_probe.inc` also remains an offline diagnostic. `src/engine.rs` prepares source stems and gains; `src/plugin_render.rs` serially applies foreign effects through `src/hosting.rs` before final mixing.

## First supported slice

Start with an offline session-processing adapter for serial track effects. The first contract should accept one selected plugin class identified by the exact pair `(bundle path, class CID)`, and require exactly one enabled stereo audio input bus and one stereo audio output bus, float32 processing, offline mode, 48 kHz, and a maximum block of 256 frames. Reject other sample rates, channel arrangements, bus counts, sample formats, and block sizes with structured errors. Do not silently fold, drop, or remap channels.

The adapter owns component, processor, controller, connection points, and host interfaces as one prepared instance. It handles both a combined controller and a distinct controller with the VST3 connection handshake. It receives bounded per-block parameter queues keyed by VST3 `ParamID`, with normalized values and sample offsets within the block; invalid IDs, values, ordering, or offsets fail validation before processing. UI/controller edits are outside this slice. The actual probe's sample-offset event check is evidence for queue plumbing, not a DAW automation contract.

Probe state is capped at 8 MiB; the session adapter caps each component/controller blob at 64 KiB, with 256 KiB aggregate decoded state. State capture, validation, instance creation, bus negotiation, parameter metadata preparation, restore, and all destruction happen off the audio callback. The session adapter rejects nonzero plugin latency; the probe reports latency. Compensation is not implemented. State stream reads must report actual byte counts, including short reads and EOF; writes and seeks must enforce the cap without partial over-limit mutation.

Teardown returns ownership to a designated worker or preparation thread. It must never drop plugin objects, unload a module, or run lifecycle calls from an audio callback. Process failures, unsupported restart requests, and teardown failures become explicit adapter errors; callers discard the affected instance. Restart requests that imply reconfiguration are unsupported in the first slice and require rebuilding a prepared instance off the processing path.

## Preparation and renderer boundary

Session validation resolves an exact plugin identity and validates the supported layout before committing a replacement. Preparation loads the module, creates and configures the instance, applies saved state, and allocates fixed input/output buffers and event storage. The resulting prepared processor is owned by the offline render job. Each block supplies track audio and bounded automation; it returns processed stereo audio or a structured failure. The existing built-in effect chain remains the model for serial track routing, but no generic processor trait should be introduced until this concrete plugin adapter is used through the DAW's real session and render path.

The first integration should use an offline render/session path only. It should preserve output no-overwrite behavior and validate plugin identity, state sizes, and layouts before output creation. It should exercise load, process, automation, save/restore into a fresh instance, repeated teardown, and failure handling through the DAW path before any playback capability is exposed.

## Isolation and real-time decision

The current child-process scanner and processing probe have bounded timeouts and output, which contain hangs and crashes at the process boundary. They are not a security sandbox: plugin code still has the caller's permissions. The offline probe also provides no real-time safety evidence.

The original ownership investigation considered a Rust runner and a C++ shim. T09d instead implements a dedicated in-process DSP worker with a fixed SPSC handoff to CoreAudio. The callback does not allocate, lock, access files or networks, start subprocesses, initialize foreign code, or destroy plugin objects. This scoped implementation does not establish general realtime safety for third-party plugins.

## Delivery status

- **T09c — bounded offline adapter and session integration (complete).** Wire the contract above through validated session data and offline rendering using one known third-party plugin. Verify deterministic errors for unsupported layouts, sample rates, restarts, oversized state, and child timeout/crash. Offline worker isolation remains enabled regardless of the separate live feature.
- **T09d — experimental native live path (implemented).** `vst3-live` enables the in-process worker with `vst3-offline` and `native-audio`. The same dedicated DSP thread creates, processes, and destroys plugin instances. A fixed 1024-frame SPSC queue separates DSP work from the CoreAudio callback; the callback consumes queued stereo frames without foreign calls, allocation, or locks. This documents the callback boundary, not a general realtime guarantee.

T09c is complete for scripted offline processing. T09d is implemented experimentally. No general plugin compatibility, sandbox, realtime safety, or latency compensation is claimed; silent native callbacks and release were verified with ValhallaFreqEcho; acoustic delivery was not verified.

## Experimental live path

Build `native/vst3/build.py` to produce `output/vst3-spike/libdaw-vst3.dylib` as the fallback library. `DAW_VST3_LIBRARY` can select another library using an absolute path. Build Rust with `--features vst3-live`; this feature implies `vst3-offline` and `native-audio`.

Live sessions require a 48 kHz session and device, stereo float32 plugin processing, no event buses, and zero-latency plugins. The transport supports play, pause, resume, volume, and stop, for up to 60 seconds. Seek and loop are rejected for plugin sessions. Editing or replacing the session stops playback.

The queue holds 1024 frames, about 21.3 ms at 48 kHz, before device latency. An underrun emits silence and leaves the timeline unchanged; `plugin_worker_underruns` is exposed in transport status and CLI output. Startup times out after five seconds. Shutdown waits two seconds; if the worker remains hung, the engine detaches it and reports explicit stop failure. In-process plugin crashes can terminate the engine. Offline plugin rendering remains isolated in owned child processes.

The browser editor continues to support schema v1 only and has no plugin controls. The feature is currently for the native CLI.

## Implemented worker boundary

The private little-endian `DWV4` job carries bounded hex-decoded state, parameter bases and frame points, and interleaved float32 stereo audio. Each child owns one complete module lifecycle on its main thread. Validation/capture runs a silent 256-frame block to flush base parameters; render runs at most 480000 frames. Responses carry captured state and finite stereo output. Rust creates a distinct process group, drains bounded output concurrently, applies a 15-second timeout, and reaps the child before decoding. No subprocess, I/O, allocation, or foreign lifecycle call enters an audio callback. A configured `DAW_VST3_HOST` must be an absolute executable path; the checkout build is the default.

All foreign validation occurs before session commit. Empty state is filled once during commit; rendering never mutates the saved snapshot. Bypassed plugins are validated on load but do not process stems. Initial unsupported or corrupt state leaves the old session and revision intact. The completed mix is prepared before a fresh WAV destination is created; intermediate gains and plugin outputs are not clipped. Memory and duration are bounded by the ten-second render cap. The host and portable core remain independently built.

## T09d verification (2026-09-30)

Portable, native-audio, and vst3-live builds passed fmt, clippy with warnings denied, and Rust tests. The separately enabled worker fixture test restored saved state, applied exact frame-32 automation, performed ten lifecycles, and stopped a producer blocked on the full queue. A thread-local allocator counted zero allocation/free operations during callback consumption, including underrun and channel conversion; no third-party allocation claim follows from that measurement. Ring tests transferred 100000 frames once in order and covered counter wrapping. The C bridge rejects process/destruction from a different thread.

ValhallaFreqEcho passed three realtime-mode runs of 64 blocks with finite, nonzero audio and successful teardown. The silent native hardware smoke used MacBook Air Speakers at 48 kHz, tested three pause/resume/stop cycles and replacement, and observed no underruns or over-budget callbacks (largest measured callback 86.583 microseconds in the first run). These short tests do not establish sustained operation under load or acoustic delivery. No live sanitizer evidence is claimed.

The separate silent CLI completion check submitted all 96000 frames over two seconds, reported 188 callbacks (512 frames maximum), zero underruns, zero over-budget callbacks, and `stream_released:true`. Fifteen normal-build Python probe/offline/live integration tests passed with the installed effect enabled and no skips; six browser live-player tests also passed.

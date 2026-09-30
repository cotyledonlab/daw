# Proposed VST3 adapter boundary

T09c implements the offline boundary through schema-v4 sessions and JSONL rendering. Native callback hosting remains proposed. `native/vst3/effect_probe.inc` now processes a third-party stereo effect offline, saves/restores state, exercises parameter changes, and tears instances down. `src/engine.rs` prepares source stems and gains; `src/plugin_render.rs` serially applies foreign effects through `src/hosting.rs` before final mixing. The standalone probe remains a diagnostic; plugin processing is not in playback.

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

Production playback ownership remains undecided. The Rust candidate's ordinary `Arc<Mutex<Plugin>>` processing route violates this project's callback policy. Its `RealtimePluginRunner` is only a candidate: audit its ownership, queues, buffer behavior, teardown handoff, and third-party behavior against this DAW's callback requirements before selecting it. A thin C++ shim remains the alternative and needs the same audit. The C++ child is selected for the next offline slice; neither native callback route is established by the trial. No callback may allocate, lock, access files or networks, start subprocesses, initialize foreign code, or destroy plugin objects.

## Proposed sequence

- **T09c — bounded offline adapter and session integration (complete).** Wire the contract above through validated session data and offline rendering using one known third-party plugin. Verify deterministic errors for unsupported layouts, sample rates, restarts, oversized state, and child timeout/crash. Keep plugin capabilities false for live playback.
- **T09d — native callback integration after ownership audit.** Select Rust runner or C++ shim only after a strong-model review of lifecycle and callback ownership. Measure allocation, lock, and timing behavior independently of acoustic hardware checks; prove teardown always returns to a non-callback owner. Expose live plugin capability only after those checks pass.

T09c is complete for scripted offline processing. T09d remains outstanding. No general compatibility, sandbox, realtime safety, live plugin playback, or latency compensation is claimed.

## Implemented worker boundary

The private little-endian `DWV4` job carries bounded hex-decoded state, parameter bases and frame points, and interleaved float32 stereo audio. Each child owns one complete module lifecycle on its main thread. Validation/capture runs a silent 256-frame block to flush base parameters; render runs at most 480000 frames. Responses carry captured state and finite stereo output. Rust creates a distinct process group, drains bounded output concurrently, applies a 15-second timeout, and reaps the child before decoding. No subprocess, I/O, allocation, or foreign lifecycle call enters an audio callback. A configured `DAW_VST3_HOST` must be an absolute executable path; the checkout build is the default.

All foreign validation occurs before session commit. Empty state is filled once during commit; rendering never mutates the saved snapshot. Bypassed plugins are validated on load but do not process stems. Initial unsupported or corrupt state leaves the old session and revision intact. The completed mix is prepared before a fresh WAV destination is created; intermediate gains and plugin outputs are not clipped. Memory and duration are bounded by the ten-second render cap. The host and portable core remain independently built.

# Proposed VST3 adapter boundary

This is a design proposal informed by the working standalone effect probe and the Rust-host trial. It does not mean the DAW can load or process plugins. `native/vst3/effect_probe.inc` now processes a third-party stereo effect offline, saves/restores state, exercises parameter changes, and tears instances down. `src/engine.rs` still prepares only built-in sine, note, clip, and gain-effect state; the probe is not on the session, renderer, or playback path.

## First supported slice

Start with an offline session-processing adapter for serial track effects. The first contract should accept one selected plugin class identified by the exact pair `(bundle path, class CID)`, and require exactly one enabled stereo audio input bus and one stereo audio output bus, float32 processing, offline mode, 48 kHz, and a maximum block of 256 frames. Reject other sample rates, channel arrangements, bus counts, sample formats, and block sizes with structured errors. Do not silently fold, drop, or remap channels.

The adapter owns component, processor, controller, connection points, and host interfaces as one prepared instance. It handles both a combined controller and a distinct controller with the VST3 connection handshake. It receives bounded per-block parameter queues keyed by VST3 `ParamID`, with normalized values and sample offsets within the block; invalid IDs, values, ordering, or offsets fail validation before processing. UI/controller edits are outside this slice. The actual probe's sample-offset event check is evidence for queue plumbing, not a DAW automation contract.

Opaque component and controller state are each capped at 8 MiB. State capture, validation, instance creation, bus negotiation, parameter metadata preparation, restore, and all destruction happen off the audio callback. The adapter reports plugin latency, as the probe does, but the first slice does not compensate it or claim latency-aligned mixes. State stream reads must report actual byte counts, including short reads and EOF; writes and seeks must enforce the cap without partial over-limit mutation.

Teardown returns ownership to a designated worker or preparation thread. It must never drop plugin objects, unload a module, or run lifecycle calls from an audio callback. Process failures, unsupported restart requests, and teardown failures become explicit adapter errors; callers discard the affected instance. Restart requests that imply reconfiguration are unsupported in the first slice and require rebuilding a prepared instance off the processing path.

## Preparation and renderer boundary

Session validation resolves an exact plugin identity and validates the supported layout before committing a replacement. Preparation loads the module, creates and configures the instance, applies saved state, and allocates fixed input/output buffers and event storage. The resulting prepared processor is owned by the offline render job. Each block supplies track audio and bounded automation; it returns processed stereo audio or a structured failure. The existing built-in effect chain remains the model for serial track routing, but no generic processor trait should be introduced until this concrete plugin adapter is used through the DAW's real session and render path.

The first integration should use an offline render/session path only. It should preserve output no-overwrite behavior and validate plugin identity, state sizes, and layouts before output creation. It should exercise load, process, automation, save/restore into a fresh instance, repeated teardown, and failure handling through the DAW path before any playback capability is exposed.

## Isolation and real-time decision

The current child-process scanner and processing probe have bounded timeouts and output, which contain hangs and crashes at the process boundary. They are not a security sandbox: plugin code still has the caller's permissions. The offline probe also provides no real-time safety evidence.

Production playback ownership remains undecided. The Rust candidate's ordinary `Arc<Mutex<Plugin>>` processing route violates this project's callback policy. Its `RealtimePluginRunner` is only a candidate: audit its ownership, queues, buffer behavior, teardown handoff, and third-party behavior against this DAW's callback requirements before selecting it. A thin C++ shim remains the alternative and needs the same audit. The C++ child is selected for the next offline slice; neither native callback route is established by the trial. No callback may allocate, lock, access files or networks, start subprocesses, initialize foreign code, or destroy plugin objects.

## Proposed sequence

- **T09c — bounded offline adapter and session integration.** Wire the contract above through validated session data and offline rendering using one known third-party plugin. Verify deterministic errors for unsupported layouts, sample rates, restarts, oversized state, and child timeout/crash. Keep plugin capabilities false for live playback.
- **T09d — native callback integration after ownership audit.** Select Rust runner or C++ shim only after a strong-model review of lifecycle and callback ownership. Measure allocation, lock, and timing behavior independently of acoustic hardware checks; prove teardown always returns to a non-callback owner. Expose live plugin capability only after those checks pass.

Until T09c and T09d are complete, the third-party offline effect probe remains standalone experimental evidence. It does not imply DAW plugin support, general VST3 compatibility, sandboxing, real-time safety, or latency compensation.

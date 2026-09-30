# DAW plan and implementation tickets

## Product scope

Build a small macOS-first DAW whose musical state can be inspected and changed by scripts and AI agents. Keep the session model and offline rendering portable. The usable DAW milestone includes track device chains, audio/MIDI clips on a timeline, transport, automation, save/load, one native plugin format, and one programmable device runtime. A thin UI will use the same command interface as agents.

The Max for Live analogy means programmable devices participating in track audio, events, parameters, and saved state. Launching a program or sending OSC alone does not provide that integration. VST means VST3 initially. Legacy VST2, AUv3 extensions, CLAP, time stretching, notation, collaboration, and a visual patch editor are outside the first milestone.

## Architecture

```text
Python / other scripts / future UI or MCP adapter
                |
       versioned commands (JSONL now)
                |
      controller + validated session model
                |
      prepared render state / graph (future)
                |
      offline render or live audio callback
                |
      built-ins / native plugins / runtime adapters
```

The control module owns validation, persistence, and edits. Audio code receives prepared state rather than parsing commands. Future live edits cross a bounded queue at a block boundary. Preparation, allocation, plugin loading, and destruction happen off the audio thread. Offline rendering remains the reference path for tests.

Prefer a Rust core with narrow C++/Objective-C bridges if native hosting needs them. Choose the bridge after a working host spike. Do not define a generic processor interface until a second concrete implementation exposes what varies. Plugin editors stay outside DSP. A scanner process isolates discovery failures; isolation during playback is a separate decision.

Scripts run outside the engine initially, using JSONL from any language. Later add parameter metadata, validated batch edits, revision checks, event subscriptions, and render jobs. An MCP wrapper can translate this interface once stable. Do not execute model-generated code in an audio callback.

## S0: completed scaffold

- One Rust crate with `daw serve` and bounded JSONL input.
- `capabilities`, `session.get`, transactional `session.replace`, `session.save`, `session.load`, and `render`.
- Strict versioned sessions: 0–64 tracks, 8–192 kHz, unique IDs, validated sine frequency/gain.
- Offline stereo PCM16 rendering, 0.001–60 seconds, clipping report, fresh output files only.
- Python client, tests, CI definition, protocol documentation.

See [PROTOCOL.md](PROTOCOL.md) for the exact contract. No live audio, plugins, transport, clips, or external runtimes are implemented yet.

## S1: minimal GUI brought forward

The browser interface in `gui/` provides track add/remove, sine frequency/gain editing, sample rate, session JSON downloads/imports, and offline WAV downloads. A Python standard-library loopback bridge owns one Rust JSONL subprocess. Rust remains the source of validation and rendered audio. There is no GUI framework build or additional package dependency.

Edits are drafts until applied; save/render apply them before proceeding. Invalid edits preserve the engine session. This slice deliberately does not add live transport, plugin controls, or a fake timeline. T13 still owns the later timeline UI, render jobs, and subscriptions. Use one browser editing window until T02 adds revision-based conflict handling.

Verified on macOS: track creation/edit/apply, invalid and blank frequency feedback, session download/import, and a downloaded two-second stereo PCM16 WAV at 48 kHz. The 390-pixel layout has no horizontal overflow. Seven bridge integration tests and eleven Rust tests pass. Browser automation stalled on the native file chooser before eventually completing the import; the loaded session was confirmed in the interface. The GUI remains an offline editor with synchronous rendering.

## S2: live sine audition brought forward

The GUI now has one Play/Pause button (hold or Escape to Stop), immediate sine frequency/gain edits, and a measured output meter through Web Audio. Listening volume defaults to 25%; summed track gains above one are normalized for monitor headroom. Stop, Escape, page exit, invalid edits, and session import stop playback. The browser chooses its device rate and enforces its Nyquist limit. Draft audition does not require Apply, while persistence and WAV export still use validated Rust sessions.

This does not complete T03/T04: native callback playback, scriptable transport, audio-device selection, and plugins still need the planned Rust engine work. Browser phase, smoothing, and monitor normalization can differ from exported WAV audio. Next prioritize T03 then T04; T01 can run independently.

Verified in the browser: audio context running at 48 kHz, nonzero measured output, editing while playing, and zero output after Stop. Five live-player lifecycle tests cover cleanup, normalization, Nyquist validation, and interrupted startup. Web Audio references: [context resume](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/resume) and [parameter smoothing](https://developer.mozilla.org/en-US/docs/Web/API/AudioParam/setTargetAtTime).

## Working method

Use GPT-6 Luna for bounded tickets. Supply the ticket ID, prerequisites, named files, current protocol, and verification command. Complete one logical ticket per commit and update its status here. Do not implement adjacent tickets speculatively. Run the checks in [AGENTS.md](../AGENTS.md).

Parallelize tests/examples/docs only with stable interfaces and separate file ownership. One owner integrates code. A stronger model reviews schema changes, real-time concurrency, FFI, and plugin lifecycle. These are technical review checkpoints, not extra user-approval requirements. Ask the user only when a product decision or blocked action requires it.

## Backlog

T01, T02, T03, T04, T04b, the T05 design ticket, T06, T07a, T07b, T08a, and T08b are complete; other tickets below remain pending. New module names are proposed. Acceptance checks supplement the standard format/lint/test commands.

### T01: machine-readable discovery (complete)

**Depends on:** S0. **Owner:** Luna. **Files:** `src/control.rs`, `docs/PROTOCOL.md`, `tests/control.rs`.

Extend capabilities with parameter descriptions, units, defaults, limits, session limits, and file behavior. Preserve existing fields. Acceptance: a client constructs a valid sine session from metadata alone; limits match validation tests. No future devices or new transport.

Implemented additive session limits, device parameter descriptions/defaults/units, a structured rate-dependent Nyquist bound, file behavior, and the minimum render duration. Validation and discovery share rate, gain, ID, track-count, and duration constants. The Python demo constructs its device from metadata. Boundary tests exercise metadata against the authoritative validator.

### T02: revision-checked atomic edits (complete)

**Depends on:** T01 and stronger-model protocol review. **Owner:** Luna after review. **Files:** `src/control.rs`, `tests/control.rs`, `docs/PROTOCOL.md`.

Introduce a revision and one batch command for add/remove track and parameter edits. Require the expected revision; edit a copy, validate, then commit. Define replacement/load revision behavior and compatibility with v1 clients. Acceptance: stale revision conflicts, a failed middle edit preserves all state, and a successful batch advances revision once. No undo or concurrency yet.

Implemented `session.inspect` and `session.edit` with decimal-string process revisions, 1–128 sequential validated operations, and one commit per successful batch. Replace/load support optional revision guards while preserving legacy response shapes. Session files do not persist revisions. The Python demo uses a checked add-track batch. Five integration tests and a revision-exhaustion unit test pass, along with portable/native lint and test suites. A silent CoreAudio check confirmed rejected/stale edits preserve playback and a successful batch stops it before committing. Browser editing remains unconditional; do not claim cross-tab conflict protection until a separate GUI migration.

### T03: block renderer with persistent phase (complete)

**Depends on:** S0. **Owner:** Luna; stronger-model review before live use. **Files:** `src/render.rs`, proposed `src/engine.rs`, `tests/render.rs`.

Implemented `src/engine.rs` with validated preparation, preallocated sine voices, wrapped phase, a frame cursor, and caller-owned stereo buffers. WAV export uses 256-frame stack blocks. Tests verify exact output across uneven block sizes, analytical output within one PCM16 step, silence, clipping, cursor advancement, and invalid preparation. A thread-local allocator counter observed zero allocations, reallocations, or frees across 100 blocks with 64 voices. This establishes prepared block rendering, not audio-device deadline guarantees. No hardware dependency was added.

### T04: macOS playback spike (complete)

**Depends on:** T03. **Owner:** stronger model for lifecycle, Luna for harness/docs. **Files:** proposed `src/audio.rs`, `src/main.rs`, `Cargo.toml`, `docs/decisions/live-audio.md`.

Evaluate CPAL on the actual device with an explicit bounded playback command. Handle format, channels, callback sizes, stop, device errors, and sample-rate mismatch without pitch changes. Acceptance: manually verify a short tone and stream release, record configuration/callback timing, test buffer bounds and silence on error in a fake callback harness. CI is not live-device acceptance. No recording or hot-swap.

T04 shipped `daw devices` and bounded `daw play SESSION SECONDS [VOLUME]` behind optional `native-audio` on macOS. CoreAudio device callbacks and teardown were observed on MacBook Air Speakers. Portable tests cover buffer behavior; instrumentation covers allocations and callback timing. See [the decision and measurements](decisions/live-audio.md). T04b below connects the native engine to the GUI.

### T04b: native transport and GUI selection (complete)

**Depends on:** T04; protocol review before implementation. **Owner:** stronger model for lifecycle, Luna for UI/tests. **Files:** `src/audio.rs`, `src/control.rs`, `gui/server.py`, `gui/app.js`, protocol tests.

Add an owned stream with explicit inspect/play/pause/stop commands and a GUI output-mode selector. Keep native and browser playback mutually exclusive. Bound control queues and native playback duration, report errors asynchronously, and decide how draft edits become prepared state before implementing live edits. Acceptance: play/pause/resume/stop/restart and process exit release resources; default-device errors remain visible; the GUI never reports playback solely because a command was sent. No plugin hosting or recording.

Implemented six JSONL transport commands, an eight-slot owner-thread queue, callback-observed state/peak metering, and a browser/native output selector. Native play applies a validated session snapshot; track edits are locked until stopped. Pause preserves the frame cursor; wall time including pauses remains bounded to 60 seconds. Stop/restart, volume, automatic completion, and callback-driven playing/paused states were verified against MacBook Air Speakers. The GUI was exercised against the real engine. Portable and native Rust checks, bridge tests, browser-player tests, and the scripting demo pass. Device disconnect/hot-swap remain unverified; there is no native live graph editing.

### T05: timeline contract (complete)

**Depends on:** T02, T03. **Owner:** stronger model for design, Luna for fixtures. **Files:** proposed `docs/decisions/timeline.md`, `examples/sessions/`.

Specify integer sample positions, constant tempo conversion, clip lengths, stable event order, loops, automation timing, and migration from v1. Acceptance: fixtures for adjacent clips, simultaneous notes, a loop boundary, and tempo conversion include expected frames. Review before implementing T06/T07. No tempo maps or stretching.

The [timeline contract](decisions/timeline.md) defines session-rate integer frames, exact constant-tempo placement, half-open clips, note lifetimes, stable ordering, loop/seek behavior, and explicit v1 upgrade rules. Four design-only fixture cases pass `python3 examples/check_timeline_contract.py`; these are reference arithmetic/event checks, not production audio acceptance. T06 now implements the note subset; the loop reference remains future behavior.

### T06: note sequencing (complete)

**Depends on:** T05. **Owner:** Luna. **Files:** `src/session.rs`, `src/engine.rs`, `src/render.rs`, `tests/timeline.rs`.

Follow the bounded commits and exact envelope/voice limits in [the timeline contract](decisions/timeline.md). Implement v2 alongside unchanged v1 behavior, then note sequences, sample-offset events, a bounded voice count, note-off behavior, and short attack/release envelopes. Acceptance: note timing is frame-accurate, voice limits hold, block size does not change output, and sessions round-trip. No hardware MIDI or piano roll.

Implemented strict dual-schema sessions, exact tick conversion, a prepared onset schedule, fixed active voice storage, per-frame attack/release, clip-bounded tails, and stable sequenced mixing. Existing v1 and explicitly upgraded continuous sessions render identically. Native v2 playback requires equal device/session rates. Eight DSP tests, five schema tests, allocation instrumentation, portable/native suites, and ten bridge tests pass. A two-second arpeggio submitted 96000 frames at 48 kHz on MacBook Air Speakers with zero measured callback overruns; maximum observed render time was 195.042 microseconds for buffers up to 512 frames. This is callback evidence, not independent acoustic capture. GUI v2 editing is visibly blocked; no piano roll is claimed.

### T07a: PCM audio clips (complete)

**Depends on:** T05, T06; T04 for live checks. **Owner:** separate Luna commits. **Files:** `src/session.rs`, `src/engine.rs`, `src/audio.rs`, `src/control.rs`, `tests/timeline.rs`.

Add PCM WAV clips at the session rate, preloaded off the callback, with gain and source offset. Define project-relative assets and missing-file reporting. Acceptance: impulse clips mix at exact offsets, unsupported formats/rates fail, asset failures preserve the active session, and references survive save/reload. No resampling, recording, compressed audio, seek, or loops in this slice.

T07a implements [the asset contract](decisions/audio-assets.md): strict audio descriptors, bounded PCM decoding, immutable shared buffers, source offsets/gains, stereo mixing, and transactional asset validation. Exact impulse, malformed-input, persistence, and allocation checks cover the working adapter. Audio sessions save within their current project directory; copying assets is not implemented.

### T07b: seek and loop transport (complete)

**Depends on:** T07a. **Owner:** stronger model for lifecycle, Luna for fixtures. **Files:** `src/engine.rs`, `src/audio.rs`, `src/control.rs`, transport tests.

Implement explicit seek and loop-region controls on prepared snapshots, following [the timeline contract](decisions/timeline.md). Acceptance: stop/seek/wrap clear voices, audio resumes from the correct source offset, loop end is excluded, output position remains monotonic, and callback boundaries do not affect samples. Keep preparation and file access off the callback. No recording or time stretching.

Implemented native JSONL seek/loop controls with a single pending callback command, bounded scheduling resets, independent timeline/output positions, and temporary loop regions. Portable tests cover source offsets, no note chase, loop boundaries, uneven blocks, duration limits, and allocation-free resets. Native callback checks cover paused seeks, looping, disabling loops, monotonic submitted frames, unchanged session revision, stop/restart, and automatic release. See [transport validation](decisions/seek-loop.md). Browser controls remain unchanged.

### T08a: serial track gain effects (complete)

**Depends on:** T07b. **Owner:** stronger-model interface review, Luna fixtures. **Files:** `src/session.rs`, `src/engine.rs`, `src/control.rs`, `tests/effects.rs`.

Schema v3 adds required per-track effect arrays with bounded serial gain processors and bypass. Each track sums into an unclipped stereo frame before effects; the master clips once after mixing tracks. Schema v1/v2 retain their previous path. Tests cover analytical gain, source/effect clipping order, stereo preservation, strict schema validation, persistence, seek/loops, and callback allocations. The [effect contract](decisions/track-effects.md) records the concrete processing interface and the remaining design work.

### T08b: parameter automation and processor interface (complete)

**Depends on:** T08a. **Owner:** stronger-model interface and callback review, Luna tests/examples.

Implemented optional schema-v3 step/hold effect-gain lanes, resolved by track/effect identity during preparation. Point order, ranges, targets, duplicate lanes, and session-wide limits validate before commit. A concrete prepared chain owns parameter cursors and reconstructs gain on seek/wrap without callback allocation. The [automation contract](decisions/automation.md) records the working stereo processing interface and its zero latency. Tests cover exact point samples, persistence, bypass, backward seeks, loop reconstruction, and rollback. Live parameter edits remain snapshot replacement.

### T09: VST3 host proof of concept

**Depends on:** T08b; research may start earlier. **Owner:** stronger model, Luna fixtures/docs. **Files:** proposed `native/`, `src/hosting/`, `docs/decisions/plugin-hosting.md`.

Compare official SDK plus thin C++ shim against maintained Rust bindings using one known test plugin. Choose from working lifecycle coverage. Scan in a child process with timeouts. Implement buses, buffers, events, automation, state, and teardown before editors. Acceptance: discover/load/process/automate/save/restore one plugin; scanner failure cannot kill the controller. Document playback isolation honestly and verify pinned SDK notices. No blanket compatibility claim.

### T09a: standalone VST3 fixture lifecycle (complete)

`native/vst3` builds a macOS C++ probe against pinned official 3.8.0 interfaces and a project-owned gain plugin. It verifies stereo buses, sample-offset parameters and notes, combined controller metadata and state, component state restore into a fresh instance, and teardown. Scanner tests cover crashes, hangs, oversized output, malformed responses, and continued Rust controller responsiveness. SDK notices accompany ignored build outputs. Dexed and ValhallaFreqEcho factory metadata scans successfully; third-party processing is unverified. See [hosting evidence](decisions/plugin-hosting.md).

### T09b: candidate adapter and third-party offline validation (complete)

**Depends on:** T09a. **Owner:** stronger model for lifecycle/ownership, Luna tests/docs.

The C++ effect probe handles separate controllers/connections, bounded opaque state, explicit stereo bus negotiation, and offline ValhallaFreqEcho processing. Automation changes its output; component state restores into fresh instances; repeated normal/sanitizer runs and controller isolation pass. The Rust host candidate at a verified pin builds and completes a short offline render of the same effect, with finite nonzero output. Its automation/state and callback paths remain unverified. Choose the tested C++ child for the next offline slice; native ownership remains undecided. See [hosting evidence](decisions/plugin-hosting.md), [Rust trial](decisions/vst3-rust-trial.md), and [adapter boundary](decisions/vst3-adapter.md). T09 overall remains incomplete until DAW processing uses the adapter.

### T09c: bounded offline effect adapter and session integration (complete)

**Depends on:** T09b. **Owner:** stronger model for schema/worker/ownership, Luna example and persistence/error tests.

Turn the tested C++ child into an offline processor for prepared track audio, bounded normalized automation, and opaque component/controller state. Validate exact bundle-path/CID identity, supported stereo layout, 48 kHz rate, and state limits before output creation. Add strict session/protocol shapes with the working adapter, preserve revision rollback and no-overwrite outputs, and keep portable builds independent of native SDK tooling. Reject unsupported events, restart/reconfiguration requests, and unavailable hosts with structured errors. Acceptance: a scripted DAW session renders a track through the known effect, automation changes samples, save/load restores state, failures preserve session/revision and clean partial outputs, and owned worker timeout/crash does not kill the controller. No live plugin capability or editor claim.

Implemented schema-v4 serial track VST3 effects through the owned C++ child, with exact class identity, normalized saved parameter points, captured component/controller state, and transactional foreign preparation. Offline stems preserve headroom and plugin work precedes WAV creation. Portable builds reject foreign preparation explicitly; native playback stays disabled for plugin sessions. First limits are 48 kHz, zero latency, no events, ten-second render, 8 effects, 64 parameters/effect, and 64 KiB/state. See [protocol](PROTOCOL.md) and [adapter record](decisions/vst3-adapter.md). GUI remains v1-only.

### T09d: native effect ownership and callback integration — complete (experimental)

**Depends on:** T09c. **Owner:** stronger model, Luna bounded fixtures/docs.

Review the Rust realtime runner and a thin C++ shim against DAW-owned buffers and lifecycle. Choose callback ownership and playback isolation explicitly, prepare instances off callback, and return destruction to the owner thread. Measure allocation, locks, automation/reset behavior, latency handling, and callback bounds before enabling live plugin playback. Acceptance: known effect plays through the DAW native path with saved state and bounded automation; stop/replacement reliably release resources off callback. Hardware/acoustic checks are separate from callback evidence. Editors and instruments remain separate slices.

Implemented optional `vst3-live` with a C++ realtime-mode shim on an owned DSP worker and a fixed 1024-frame SPSC callback consumer. Same-thread initialization/process/destruction, allocation-free consumption, exact fixture automation/state restoration, repeated release, and silent Valhalla native pause/resume/stop/replacement are verified. Zero underruns and zero over-budget callbacks in the short hardware run; no acoustic claim. Seek/loop, editors, instruments, crash isolation, and general plugin compatibility are excluded. Startup/shutdown timeouts report failures; hung foreign workers are detached, requiring an engine restart. See the adapter record for limits.

### T09e: continuous sine effect-chain GUI — complete

**Depends on:** T09d. **Owner:** stronger model integration, Luna HTTP guards/tests/docs.

The browser accepts v1 and schema-v4 continuous, clip-free sine sessions. Add gain explicitly upgrades v1 to v4. Saved VST3 session uploads pass Rust foreign validation before commit; their effects populate a bounded in-memory selector for reuse. Gain and saved normalized plugin parameter values, bypass, removal, native transport, JSON save, and WAV export are supported. Opaque state and saved automation points are preserved; automation-point editing, plugin discovery/windows, notes, audio clips, and v2/v3 editing are excluded. V4 playback uses native audio; plugins need vst3-live and 48 kHz. VST3 renders retain the ten-second cap.

Acceptance verified: thirteen bridge tests and thirteen JavaScript tests pass; browser loaded Valhalla, edited/applied bypass and parameter 48, added serial gain/reused VST3 effects, played/paused/resumed/stopped silently, and downloaded a WAV through the real engine. The existing preview's sine edits were saved and restored during restart.

### T09f: names and metadata for validated plugin parameters — complete

**Depends on:** T09e. Inspect only effects already in the authoritative session through a bounded owned child. Add names/defaults/flags to the protocol and GUI without guessing ParamID meanings or changing opaque state. Keep metadata errors explicit and editing limited to supported automatable parameters. Acceptance: Valhalla's saved parameter displays its actual name, restored base value and automation remain intact, and metadata failures preserve the session. No arbitrary HTTP file browsing or native plugin windows.

Implementation notes for T09f: the primary model owns the protocol/native interface; Luna can add display and bounded fixtures after it is specified. Command `effect.inspect` takes only track/effect IDs from the active session, never client paths. Use the existing owned child/timeouts and bounded state restore. Return bounded parameter metadata with actual UTF-16 names decoded safely, numeric IDs, automatable/read-only flags, and current restored normalized values. Inspect must not process or commit the session, replace playback, or overwrite saved bases/points. Extend the existing controller/child tests for invalid IDs, corrupt metadata and worker failures; GUI shows names but preserves ID-based serialization. Keep snapshot values authoritative and editable parameters limited to the already saved list for this slice.

Implemented read-only `effect.inspect` through the bounded child, with strict metadata decoding, default/restored values, units and flags. The authenticated GUI route accepts only active-session IDs; display updates preserve drafts and focused controls. Fixture tests cover malformed responses, crashes, bounded Unicode conversion, and inspection without DSP; Valhalla reports parameter 48 as `wetDry`. Browser checks against the real engine verify stopped edits, apply/reload, and preserved automation. Portable, combined offline/native, and live Rust checks, thirteen JavaScript tests, and twenty-six bridge/metadata/offline integration tests pass. The preview was restarted with the user's current session restored. Playback parameter edits remain unsupported.

### T09g: live edits to saved VST3 parameter values — complete (experimental)

**Depends on:** T09f. **Owner:** stronger model for worker control, state semantics and integration; Luna for independent protocol/fixture tests and docs.

Start with the already saved, automatable VST3 parameters and reject targets that have saved automation for this slice. Specify a strict ID/value/revision command and its acknowledgement before implementing it. Send bounded changes to the existing DSP owner without creating or replacing plugin instances; the callback continues to consume only prepared audio. Define queue-full errors, stop/replacement races, timing relative to the queued audio, and how acknowledged values persist in the authoritative session. Avoid holding the controller indefinitely for foreign acknowledgements. No foreign calls, allocation, locks or destruction in the callback.

Acceptance: a deterministic fixture test changes gain at a defined block boundary during playback without restarting phase or the worker; acknowledgements identify applied changes; invalid/stale/full-queue requests preserve the session. Save/reload retains acknowledged bases and existing opaque state. Browser controls remain responsive during play/pause, and silent installed-effect hardware checks record callback evidence separately from acoustic verification. Defer bypass, oscillator/track edits, automation override modes, discovery, windows and new parameter insertion. Document the measured control delay and any unsupported paused-state behavior.

Implemented strict revision-checked `effect.set_parameter` with an eight-entry DSP-owner queue. Acceptance commits the saved base; successful block processing reports revision/frame acknowledgements separately. Eligible saved VST3 controls remain editable during native play/pause without rebuilding the instance. Automated and bypassed targets, structural controls, and new parameter insertion remain excluded. Stop/failure can cancel DSP delivery while accepted bases remain saved. The callback path is unchanged.

Deterministic worker tests verify gain changes at block boundaries without phase reset, queue bounds, reused-module teardown and exclusive worker ownership. Concurrent fixture tests exposed module unloading during processing; a process-wide worker lease prevents competing DSP owners, including after a hung-worker detach, and reused instances retain shared modules until final teardown. All four Rust build configurations pass fmt/clippy/tests; thirteen JavaScript tests and forty-two bridge/native/metadata/offline integration tests pass with installed-plugin and silent-hardware coverage. The browser changed Valhalla wet/dry while playing and paused, resumed, stopped, and retained the base after refresh. A short silent hardware run reported zero underruns and over-budget callbacks; acoustic output and live sanitizer checks remain unverified.

### T10: Audio Unit host proof of concept

**Depends on:** T09 host contract. **Owner:** stronger model, Luna harness/docs. **Files:** `native/`, `src/hosting/`, macOS integration tests.

Start with desktop Audio Units: discovery, instantiation, formats, resources, cached render blocks, parameters/state, teardown. Contain Objective-C and window lifecycle in native code. Acceptance: known Apple AU renders and restores state; portable builds still work; unsupported units fail clearly. No AUv3 extensions.

### T10a: standalone Apple Audio Unit lifecycle — complete

**Depends on:** T09g. **Owner:** primary native/lifecycle review; Luna bounded child harness/tests/docs.

`native/au` discovers registered Apple effects and processes the exact AUv2 tuple `aufx/lpas/appl` through stereo planar float32 at 48 kHz. The 256-frame synthetic-input callback is preallocated; no hardware stream opens. Three independent instances verify cutoff control, bounded binary property-list state, fresh-instance restoration, filter-history reset, and checked teardown. The Python owner limits child time/output and kills its process group on failure. This is a standalone proof; T10 overall remains incomplete and the DAW has no AU session or playback capability.

The host found 23 Apple effects. Apple AULowpass's low-cutoff state was 167 bytes and restored with zero maximum sample error. Ten child-harness/native tests pass, including corruption, timeout/crash, output bounds, unsupported identity and descendant cleanup. Five ASan/UBSan lifecycle runs completed without diagnostics. Portable Rust fmt/clippy/tests pass and the feature-enabled preview binary remains available. See the [AU decision](decisions/audio-units.md) for evidence and exclusions.

### T10b: bounded offline AU session adapter — complete

**Depends on:** T10a. **Owner:** primary schema/worker integration; Luna independent fixtures/examples/docs after the contract is fixed.

Derive an exact component-tuple identity, native parameter IDs/ranges/units and bounded property-list state from the working proof. Decide the new schema version explicitly; old versions must reject AU effects. First support the known stereo Apple effect and reject unsupported components/layouts/rates/latency or events. Replace the fixed synthetic source with bounded DAW-owned track PCM in an owned child, and validate state, parameters and buffers before session commit or output creation. Keep native AU playback and GUI edits unavailable until separate tickets implement them.

Acceptance: a scripted sine session exports through the known AU, parameter changes affect output, save/load restores native values and state, and malformed identity/state/child failures preserve session/revision and create no partial output. Portable builds parse only supported contract shapes and reject unavailable hosting explicitly. Preserve VST3 and built-in behavior; derive any common processor interface only from both implemented adapters. Split bounded worker decoder/tests, scripting example/persistence tests and docs for Luna; the primary owns integration and reviews all foreign lifecycle/error paths.

T10b delivers schema-v5 AU state/native parameter bases and owned-child processing for Apple AULowpass. Real track PCM replaces the proof source; state dictionaries must identify the selected component, parameter writes are checked against native metadata and read back, and filter history resets before each render. Session preparation and complete effect rendering precede commit/output creation. Native AU playback and v5 GUI imports are explicitly rejected. Old schemas and VST3 behavior remain distinct.

Verification: six real controller tests cover cutoff response, exact saved/state-only restoration, corrupt/mismatched property lists, invalid identity/parameters, serial bypass, malformed/oversized worker output and timeout rollback. Five portable schema/rollback tests (four when AU hosting is enabled) cover strict fields, unavailable-host rollback and shared VST3/AU count/state limits. The standalone ten-test lifecycle suite remains passing. Portable and combined feature fmt/clippy/tests pass, as do four worker codec tests, both ignored VST3 DSP-owner tests, 20 GUI bridge tests and 13 editor/live-player tests. The isolated browser successfully applied a sine session and exported a WAV without touching the user preview. A scripted session render through the ASan/UBSan host completed without diagnostics. VST3 installed-plugin/hardware checks remain separately conditional. This supports only the known Apple effect; other units, native AU playback and GUI controls remain future work.

### T11: SuperCollider jobs and control

**Depends on:** S0 for offline spike; T08b for track integration. **Owner:** Luna process harness, stronger-model audio-routing review. **Files:** proposed `src/runtimes/supercollider.rs`, runtime tests, example score.

First launch a configured `scsynth` with a prepared NRT score, fresh WAV path, argument arrays, timeout, and captured errors. Then add owned child-process OSC with completion handling. Never use a shell or terminate unrelated servers. Acceptance: installed engine renders a fixture, missing executable/timeout errors work, interactive create/free is acknowledged. Live audio transport needs separate measured design; OSC success alone is not track integration.

### T11a: owned SuperCollider NRT jobs — complete

The JSONL `supercollider.render` command accepts a bounded binary score snapshot and renders through an explicitly configured Unix `scsynth`. It preserves session/revision/transport, owns its child process group, limits diagnostics and elapsed child time, validates stereo PCM16 output and trims trailing block padding before exclusive publication. The installed 3.14.1 server accepts the generated SCgf-v2 fixture without a language interpreter or audio device. NRT success proves a rendered WAV; per-command completion acknowledgements are not available in this mode. See [the job decision](decisions/supercollider.md).

Verification: 13 controller/native tests pass, including a real 440 Hz stereo render, minimum/non-block-aligned durations, unresolved/invalid definitions, special-file rejection, exclusive output, malformed/oversized child results, timeout and descendant-pipe cleanup. Three parser regressions and portable/combined-feature fmt/clippy/tests pass. Both the baseline script and configured SuperCollider demo export successfully. The existing GUI preview remains untouched. This completes the NRT job slice; T11 overall remains incomplete until saved track devices and interactive completion/audio routing are implemented. No hardware/acoustic or arbitrary UGen compatibility claim is made.

### T11b: saved programmable SuperCollider track source

**Depends on:** T11a, T08b. **Owner:** primary schema/audio preparation; Luna examples, state/parameter tests and independent docs after the contract is fixed.

Derive a saved SynthDef identity/program and native control/event shape from the working score renderer. Validate the actual program, metadata and requested control names before committing. Prepare owned track PCM through NRT outside callbacks, then route it through existing gain/plugin chains and rate-matched native playback. Preserve portable parsing, project assets, revisions and output rollback; bound total runtime/decoded resources. Acceptance: scripts create two tracks with distinct program/control data, save/reload them, export through existing chains, and audition the same prepared audio natively; malformed program/control/runtime jobs preserve the active project. Document that prepared PCM playback does not provide interactive SC DSP.

### T11c: owned interactive OSC and live runtime routing

**Depends on:** T11a/b. Keep T11's remaining interactive create/free acknowledgement and measured audio-routing work explicit. Own only launched servers, use loopback argument arrays and completion responses, and establish an actual track-audio path before claiming live runtime hosting. OSC control alone is insufficient. Keep foreign server work outside the hardware callback.

### T12: Csound and Pure Data adapters

**Depends on:** T11 job conventions; T08b for embedded devices. **Owner:** Luna offline harnesses, stronger model FFI. **Files:** proposed `src/runtimes/csound.rs`, `src/runtimes/pd.rs`, separate tests.

Use separate commits. Start Csound with CLI rendering, then assess libcsound blocks. Spike libpd with one patch, explicit search paths, controlled externals, block sizes, and thread ownership. Acceptance for each: known audio fixture, state/parameters, missing-runtime errors, cleanup, capability flag, and preserved dependency notices. Do not claim arbitrary externals/opcodes work. No general sandbox.

### T13: agent jobs and thin UI

**Depends on:** T02, T07b, T08b, one native plugin, one runtime device. **Owner:** separate Luna tasks after interface review.

First add cancellable render jobs with progress and subscriptions. Then build minimal track/clip/device/transport/meter views through the same commands. Optional MCP translates discovery, inspect, edit, and jobs without another session model. Acceptance: Python creates/edits/saves/reloads/renders the arrangement shown by the UI, failed edits preserve state, and cancellation cleans output. Defer a visual patch editor and elaborate mixer.

## Next starting point

T11a establishes owned SuperCollider NRT score jobs. Continue with T11b saved programmable track sources; T11c interactive acknowledgements/audio routing and T12/T13 remain outstanding. Preserve the distinction between exported WAV jobs, prepared runtime track audio and live runtime DSP. Keep notes/audio clips and v2/v3 sessions locked in the GUI, and preserve native callback ownership, offline containment, and the play/pause/hold-to-stop interaction. Eligible saved VST3 bases can change during native playback; automated targets and structural edits require stopped playback.

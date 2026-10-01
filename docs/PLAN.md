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

Split into T11b1 program inspection and T11b2 saved sources. T11b1 adds portable inspection of one bounded SCgf-v2 SynthDef, including named control array defaults and graph references. Both source slices are complete. T11c retains interactive completion and measured live runtime audio routing.

#### T11b1: bounded SynthDef inspection — complete

Implement `supercollider.inspect` without runtime launch or session/transport mutation. Read control names/default arrays from actual binary data; reject malformed/oversized definitions, invalid graph references and unsupported variants. Keep structural inspection distinct from runtime compatibility. Verification: four same-process controller tests cover metadata/array spans, every truncated program prefix, malformed graph/count/name/rate/hex fields and preservation of a populated session/revision/transport. Two portable Rust parser tests pass. Baseline and all-feature fmt/clippy/tests pass; 13 NRT job tests still pass, and the updated example inspects and renders the real one-second stereo fixture. Inspection is structural; saved devices and runtime preparation remain pending.

#### T11b2: saved sources — complete

**Depends on:** T11a, T08b. **Owner:** primary schema/audio preparation; Luna examples, state/parameter tests and independent docs after the contract is fixed.

Derive a saved SynthDef identity/program and native control/event shape from the working score renderer. Validate the actual program, metadata and requested control names before committing. Prepare owned track PCM through NRT outside callbacks, then route it through existing gain/plugin chains and rate-matched native playback. Preserve portable parsing, project assets, revisions and output rollback; bound total runtime/decoded resources. Acceptance: scripts create two tracks with distinct program/control data, save/reload them, export through existing chains, and audition the same prepared audio natively; malformed program/control/runtime jobs preserve the active project. Document that prepared PCM playback does not provide interactive SC DSP.

Implemented schema v6 with embedded single SynthDefs, native control arrays, saved frame events, finite source durations and track gain. Structural validation and full float32 NRT preparation occur before commit; owned PCM is keyed by program/control/event/rate inputs and reused for render/native snapshots. Creation supplies all bases and frame-zero overrides in /s_new; known init-rate slots reject subsequent points. Up to four sources share ten seconds total duration and 512 points; runtime PCM reserves part of the existing asset budget before decode. Sources route through gain automation and offline VST3/AU; live VST preparation captures runtime audio on the caller before starting its owner worker. GUI v6 import/editing remains unsupported.

Verification: ten source/controller tests cover save/reload, timed controls, init-rate bases/frame-zero behavior, schema/control/runtime rollback, float headroom and cache reuse/invalidation. Three routing tests cover VST3 scaling/state, Apple lowpass processing and opt-in live VST3 playback. The opt-in source/routing suite passed all 13 tests with silent host audio access. The two-source example saved/reloaded/rendered and submitted native callback frames before clean stop. A portable cached-PCM regression covers gain chains, source end, seek and loop. Baseline, native-only, live-VST and combined-feature Rust fmt/clippy/tests pass; both ignored live worker tests pass, 17 score/inspection regressions pass, and 41 plugin/routing regressions pass with eight documented optional skips. Malformed float output cases preserve the project; acoustic delivery and arbitrary UGen compatibility remain unverified.

### T11c: owned interactive OSC and live runtime routing — in progress

**Depends on:** T11a/b. Keep T11's remaining interactive create/free acknowledgement and measured audio-routing work explicit. Own only launched servers, use loopback argument arrays and completion responses, and establish an actual track-audio path before claiming live runtime hosting. OSC control alone is insufficient. Keep foreign server work outside the hardware callback.

T11c1 provides an owned macOS live-server diagnostic: loopback port ownership, definition/buffer completion, node create/free notifications, native control readback, finite private-bus stereo capture and acknowledged clean quit. Two installed-server cycles measured 440 -> 660 Hz and approximately half RMS gain, with captured output buses silent. Portable OSC reply validation and opt-in child timeout/crash/diagnostic-overflow cleanup tests pass. This is independent of DAW transport and adds no live-runtime capability claim.

T11c2a completes a finite macOS arm64 streaming prototype: a project-owned UGen maps/locks a fixed queue at plugin load, then publishes 64-frame stereo blocks using lock-free counters and one producer lease. A separate C++ process drains 512 blocks (32,768 frames), with measured 440 -> 660 Hz and half-gain live edits and silent output buses. A 12,000-block two-process test verifies sample ordering across counter wrap; installed-server tests verify overflow, duplicate-producer faults and explicit destructor lease release. ABI/nonce/rate/layout/permissions and exclusive outputs are checked. Ten combined queue/stream/lifecycle tests pass. No DAW callback or product live-runtime API is connected yet.

T11c2b1 completes the source-to-native diagnostic: a C ABI consumer maps the queue on a Rust owner worker, applies the existing prepared gain chain, and publishes into the same fixed 1024-frame callback queue used by live VST3. The hardware callback performs no IPC/foreign initialization. A paused startup gate prevents CoreAudio construction-time callbacks from consuming an empty queue. `daw sc-stream-play` and the muted Python harness are separate from session transport. A six-second installed-server run delivers all 288,000 frames with equal source/callback sample-order fingerprints and zero underruns; two repeated runs and producer-death timeout/lease cleanup pass. Four bridge ABI tests and the native allocation-free callback regression pass, as do baseline/native/all-feature Rust checks, both real VST worker regressions, the scripting demo and six browser live-player regressions.

T11c2b2a completes bounded saved-session CLI ownership: `sc-session-play` runs schema-v6 sources live, with one owned server per source and private SynthDef renaming, stereo bus capture/muting, timestamped native control points, source duration/gain, serial gain automation/bypass and whole-session built-in mixing. It uses the fixed native callback queue without NRT preparation. The CLI reports source/callback continuity and reaped PIDs; startup/log/reply/production failures and SIGINT/SIGTERM run owned cleanup. AU/VST3 effects are explicitly rejected in this mode. Verified macOS arm64 playback is muted; acoustic verification remains outstanding. The browser still rejects v6 imports/edits; ordinary JSONL transport retains prepared v6 playback. Nine opt-in saved-session tests pass: same-name sources plus built-in mixing, timed native gain/duration, hard-coded bus-zero muting, gain automation/bypass, strict malformed/old-schema and unsupported-effect rejection, missing runtime, fake crash/log overflow/OSC timeout cleanup, SIGINT cleanup and actual owned producer death with all PIDs reaped. Seven queue bridge tests and three existing native streaming tests pass, together with baseline/native/all-feature Rust checks, the scripting demo and 13 browser player/editor regressions.

T11c2b2b is partly complete: live native start/stop, ownership and revision-checked control edits are implemented below. Continue browser access, sustained control latency and clock drift measurements, and pause semantics. Establish multiple-program identity, captured/muted source buses and whole-session mixing/effect routing. The standalone external-source diagnostic still uses a bounded timeout; owned live transport detects actual child death and releases the other sources. A native live-runtime capability is exposed; GUI access remains pending. Built-in scope buffers publish snapshots and are unsuitable as an assumed lossless transport. Read the [streaming decision and gates](decisions/supercollider.md#streaming-decision-and-remaining-gates-t11c2) before implementation.

Next bounded implementation slices:

1. **T11c2b2b1 — native transport live start/stop — complete.** Primary owns `src/audio.rs`, `src/sc_session.rs` and controller state; Luna owns independent integration tests and examples after the parameter contract is fixed. Add an explicit live-source selection to native transport without changing ordinary prepared playback. Start must validate before changing active state, retain owned servers/worker, and expose startup versus DSP readiness distinctly. Stop must acknowledge callback silence plus queue/child release. Decide pause behavior from an actual owned-server experiment before advertising it. Acceptance: JSONL starts the saved two-source project live, reports advancing frames, stops/restarts repeatedly, and producer death leaves transport stopped with a structured error and no orphan servers.
T11c2b2b1 adds explicit `transport.play` source mode, preserving prepared playback as the default. Native transport owns the SC worker and callback stream, waits for prefill before acknowledging startup, exposes source PIDs and callback telemetry, and retains final release/continuity reports. Stop and protocol EOF release the stream before joining/reaping sources. Child/device failures become stopped snapshots with structured asynchronous errors. Six installed-runtime transport tests pass for two-source start/stop/restart, natural 48,000-frame continuity with zero underruns, invalid mode/overlength rollback, unsupported pause/timeline commands, owned producer death and EOF cleanup. Pause/resume, seek and loop explicitly reject live SC rather than freezing a callback while sources continue. Source control edits and pause semantics remain for the next slice; this ticket does not claim latency/drift, acoustic or GUI acceptance. Baseline/native/all-feature and live-VST Rust checks pass, including the two real VST worker regressions. The new muted scripting example, ordinary demo, 20 GUI bridge tests, 13 browser regressions and five native VST parameter tests pass; optional installed-plugin checks remain conditional.

2. **T11c2b2b2 — saved native control edits — complete.** Primary fixes revision and control-array semantics plus queue/DSP acknowledgments; Luna writes bounded tests and scripting examples. Apply a native named-control base while running, preserve saved values and revision consistency, reject init-rate/automated targets explicitly, and measure command-to-callback response. Acceptance: confirmed live changes affect captured samples and round-trip state; rejected/stale updates preserve revision and DSP state.
T11c2b2b2 adds `source.set_control` with strict saved named-array validation and revision checks. Queue acceptance commits the base while invalidating prepared PCM; native `/s_getn` readback and publication of a later captured block establish the DSP watermark, with callback observation reported separately. At most eight unacknowledged edits are allowed. OSC polling is bounded/nonblocking on the source worker; an initial blocking implementation caused underruns and was replaced. Init-rate and any automated targets reject without revision/DSP mutation. Six muted installed-runtime tests cover scalar/array native readback, save/load, post-edit rendering, stale/invalid rollback and stopped/prepared rejection. The four-second scalar case has zero underruns, matching 192,000-frame source/callback digests and a measured command-to-callback observation 59–70 ms across two runs (25 ms status polling included). This is bounded host evidence, not an acoustic or sustained latency guarantee. Queue capacity/acknowledgment and portable strict-command regressions pass. Baseline/native/all-feature Rust checks, six prior live transport tests, seven bridge tests, 20 GUI bridge tests, 13 browser regressions and both scripting demos pass. GUI access is next; pause/drift/foreign chains remain separate gates.

3. **T11c2b2b3 — GUI access — complete.** One owner edits GUI state/imports; Luna handles server tests and user documentation. Permit validated v6 import and a read-only source summary first, then route the existing native Play/Stop interaction through live transport. Add only controls backed by the revision-checked command above. Exercise the real engine in an isolated browser session and preserve the user's preview session.

T11c2b2b3 adds validated continuous gain-only v6 sine/SuperCollider imports, saved source summaries and inspected native scalar/array controls. Portable inspection exposes an additive initialization-rate flag; automated/init-rate and failed-inspection controls remain read-only. Stopped bases use revision-checked replacement/runtime validation; running bases use the bounded live command with callback-observed notices. Native Play/Stop selects explicit live mode and the longest saved source duration. Existing v1/v4 playback and scripting-only v2/v3/v5 boundaries remain. Token/host/origin/body-size restrictions remain, and no new HTTP filesystem routes are added. Baseline/native/all-feature Rust checks, 27 bridge regressions, a real-engine muted HTTP import/edit/natural-cleanup test, six portable inspector tests and 17 browser regressions pass. An isolated in-app browser verified file-chooser import, stopped Apply, two-value live edits and callback acknowledgment, structural locks, automated-control read-only state and Play/Stop. Native HTTP completion has zero underruns and matching source/callback fingerprints. Desktop (1120 px) and compact (375 px) layouts were inspected; no horizontal overflow at compact width. The user's original preview/session stays running. HTTP bridge discovery negotiates checked replacement/source routes so refreshed pages remain compatible with older running servers. The browser showed the Save success state, but download-path observation timed out; the saved download file was not independently verified through the browser automation API.

Clock drift, source synchronization, AU/VST3 chains and longer playback remain separate acceptance gates; they must not block honest delivery of the bounded live start/stop slice.



### T12: Csound and Pure Data adapters

**Depends on:** T11 job conventions; T08b for embedded devices. **Owner:** Luna offline harnesses, stronger model FFI. **Files:** proposed `src/runtimes/csound.rs`, `src/runtimes/pd.rs`, separate tests.

Use separate commits. Start Csound with CLI rendering, then assess libcsound blocks. Spike libpd with one patch, explicit search paths, controlled externals, block sizes, and thread ownership. Acceptance for each: known audio fixture, state/parameters, missing-runtime errors, cleanup, capability flag, and preserved dependency notices. Do not claim arbitrary externals/opcodes work. No general sandbox.

### T12a: owned Csound CLI render jobs (implemented)

`csound.render` snapshots a bounded CSD and runs an explicit Unix executable in an owned process group. It validates exact-duration stereo PCM16 output before exclusive publication, preserving session/revision/transport. Capabilities separate implementation/configuration from track/live support. The two-fixture demo measures saved frequency/gain changes against the official 7.0.0-beta.17 runtime; no system installation or binaries are included. See [the contract and next tickets](decisions/csound.md).

Verification: all 13 Csound integration tests pass, including three real-runtime tests for signal, 44.1 kHz override/ignored device options and invalid-program rollback. Portable and all-feature fmt/clippy/tests/build and both demos pass; two existing opt-in live VST3 Rust tests remain ignored. Coverage includes strict params/input, missing runtime, timeout/descendant cleanup, malformed/oversized output and destination preservation. Dependency notices are recorded. Csound 6/other platforms, arbitrary opcodes, assets, live block processing and saved track devices remained outside this standalone-job slice. T12 overall remains open.

### T12a2: owned libcsound block/control proof (implemented)

The separate Unix `native/csound/block_probe.py` loads the pinned Csound 7 double-sample API in an owned child. One thread controls initialization, compile/start, 64-frame spin/spout access, control readback, DSP, reset/recompile and destruction. Two passes produce identical checked stereo audio with a 440/660 Hz and 0.1/0.05 gain change at frame 24,576. Host input offsets prove stereo input routing. The finite source completes at 49,152 frames; no hardware stream, saved device or protocol command is added. See [the measured contract](decisions/csound.md#csound-7-blockcontrol-proof-t12a2).

Verification: all nine block-diagnostic tests pass, including real-library signal/reset and invalid-compilation checks. Missing/invalid libraries, malformed reports, output limits, deadlines and descendant cleanup are covered. Portable Rust fmt/clippy/tests and the baseline demo pass; the all-feature binary is restored.

### T12a3a: saved Csound sources with prepared playback (implemented)

Schema v7 adds embedded CSD sources, named scalar control bases and 64-frame-aligned saved step events. Session load/replacement prepares finite stereo float64 PCM through an owned Python/ctypes worker before commit; Rust then routes the cached PCM through source/track gain and the existing render/native prepared-playback paths. Preparation requires an explicit absolute `DAW_CSOUND_LIBRARY` using the tested Csound 7 double-sample ABI. Saved controls are set after `csoundStart`, before the first perform call, so initialization-rate semantics are not guaranteed. Final partial 64-frame blocks are trimmed to the exact duration. Explicit live DSP and live scalar edits are described by T12a3b2a/b; T12a3b3 adds the constrained GUI schema-v7 workflow below. Standalone `csound.render` remains a separate PCM16 job.

Verification: six Rust schema/transaction tests and all nine Python source-worker tests pass, including three real Csound 7 cases, mixed SC/Csound preparation, shared source limits, worker deadline/descendant cleanup and state preservation. The two-source demo produces 440/660 Hz audio at a peak of 819; muted native playback submitted 2048 callback frames and released resources. This is prepared-audio and callback evidence, not acoustic proof. GUI bridge tests pass (27), as do the SuperCollider source tests (10; one optional hardware test skipped). Portable and all-feature Rust fmt/clippy/tests passed. See [the schema-v7 contract](PROTOCOL.md#schema-v7-prepared-csound-sources) and [Csound decision record](decisions/csound.md#schema-v7-saved-csound-sources-t12a3a).

**T12a3b1 — Csound queue diagnostic — implemented.** The macOS arm64 producer uses the existing SC queue ABI without changing its layout. An owned Csound worker copies 64-frame stereo double blocks, checks and converts them to float32 at 48 kHz, then publishes with exclusive leases and bounded full-queue retry. A separate paced diagnostic consumer waits for up to four published blocks and verifies exact frame count and matching producer/consumer digests. The 15-second probe has bounded logs/reports and no hardware audio or DAW transport. One installed-runtime run consumed 750 blocks/48,000 frames with matching SHA-256 `553babc97e5c75361f148f6e166086d7ffde91fa2d60f230baa033b7664634cb`; frequency/gain changed from 440/0.1 to 660/0.05. Backpressure wait count is machine-dependent. See [the queue diagnostic record](decisions/csound.md#csound-producer-queue-diagnostic-t12a3b1).

Verification: eight stream tests and four producer ABI tests pass, including real Csound signal/restart/partial-block checks, stalled consumers, producer death, bounded startup reports, owner cancellation, descendant cleanup and CLI SIGTERM cleanup. The shared block-loop regression pass also passed nine saved-source tests and seven existing SC bridge tests. Portable Rust fmt/clippy/tests and the baseline demo pass; the all-feature binary is restored. This diagnostic itself does not exercise DAW transport or acoustic output. JSONL live SuperCollider remains available for schema v6; Pure Data/libpd remains T12b.

**T12a3b2a — live Csound transport — implemented.** On macOS arm64, `transport.play` with `source_mode:"live"` starts owned Csound producers for schema-v7 tracks and routes their fixed queues through the Rust worker and native callback. The same path supports schema-v6 SC, schema-v7 Csound, and mixed SC/Csound sessions alongside built-in tracks, with a 48 kHz device, ten-second maximum and gain-only effects. Saved Csound controls/events are applied by the worker. Status identifies `runtime` as `supercollider`, `csound`, or `mixed`; stop/status/volume work, while pause/resume/seek/loop reject. The callback performs no foreign calls. Csound uses `DAW_CSOUND_LIBRARY`, the queue from `python3 native/csound/build_queue.py` (or absolute `DAW_CSOUND_QUEUE_LIBRARY`), and optional `DAW_CSOUND_STREAM_WORKER`/`DAW_CSOUND_PYTHON` paths. Nine native Csound tests pass, including 48/48,001-frame completion, duration truncation, zero-tail and mixed-source routing. The combined Csound source/stream/ABI and SC bridge regressions pass 28 tests; the SC live transport/control regressions pass 12. Baseline/native/all-feature Rust tests and clippy pass, as do 17 browser tests. Runs used muted native output and establish callback/child cleanup evidence, not acoustic delivery. Forced cleanup may kill a hung worker after two seconds; `resources_released:true` confirms child reaping, not a per-instance graceful reset after forced stop.

**T12a3b2b — revision-checked live Csound controls — implemented.** `source.set_control` accepts exactly one finite f64 for an existing declared scalar input channel with no saved points, using the existing `expected_revision`, `track_id`, `control_name` and `values` fields. SC keeps its finite f32 array semantics. Acceptance commits the Csound base/revision and invalidates prepared PCM; live edits do not run offline preparation. A bounded 4096-byte regular-file mailbox carries one in-flight command to the owner child, which performs set and exact native readback between DSP blocks. Acknowledgment follows publication of a changed block to the Rust callback ring. Csound `applied_frame` is the end of that block, capped at the effective source duration (the shorter of saved duration and requested playback); callback observation is reported when the Rust timeline reaches it. The eight-update limit is shared by SC and Csound. Invalid shape, stale revision, unknown/nonscalar channel and any automation point reject without model/DSP mutation; prepared/stopped playback and a full queue return `audio_error`. Bad acknowledgment, producer death or a two-second timeout stops transport asynchronously while retaining the accepted base; Stop/EOF cancel pending work. This proves named channel set/readback and block publication only, not initialization-rate behavior or arbitrary CSD audio response.

The opt-in `native.csound.test_live_controls` suite uses `DAW_TEST_CSOUND_CONTROLS=1` and contains seven cases, including three worker-fault subcases and optional mixed SC/Csound coverage. All seven cases pass, including exact float64 readback, changed callback RMS, save/reload and prepared-cache freshness, and alternating SC/Csound edits. The nine Csound transport and twelve SC transport/control regressions pass. Portable/native/all-feature Rust fmt, clippy, tests and builds pass; seventeen browser player/editor tests pass. Saved-source (9), stream lifecycle (10), producer ABI (4), and SC bridge (7) tests pass. The stream diagnostics now confirm a worker has exited with bounded waitpid before tolerating a denied group signal during its exit transition. All native checks use monitor volume zero; acoustic delivery remains unverified.

**T12a3b3 — constrained GUI Csound authoring and editing — implemented.** The editor accepts supported v7 continuous sine/SuperCollider/Csound tracks with empty clips and gain-only effects. Add Csound uploads embedded UTF-8 CSD up to 60 KiB, requests duration (1 ms–10 s), and adds a gain-0.5 source with named scalar fields. It explicitly upgrades supported v1/v4/v6 sessions to v7 or appends to v7; only the supported model is editable. Rust remains authoritative: Apply validates and prepares every source before commit, and failure preserves the applied session. Controls are scripted named scalar bases; the GUI does not parse programs to find channels. Eligible nonautomated scalar bases can be edited while stopped or through live `source.set_control`; the existing saved/live value constraints remain. Notes, audio clips and non-gain effects are outside this GUI scope. Verification: 24 Node player/editor tests and 31 HTTP bridge tests pass. The opt-in `DAW_TEST_CSOUND_GUI=1 python3 -m unittest gui.test_csound` test passes against the installed Csound 7 library and all-feature Rust engine, covering import preservation, rejected channel/automation/stale edits, scalar callback acknowledgment, exact four-second completion and owned cleanup. Browser checks cover CSD authoring, named controls, Apply, live scalar acknowledgment and structural locking. Portable/native/all-feature Rust clippy/tests and fmt pass; the baseline build/demo pass and the all-feature binary is restored. Native checks use monitor volume zero; acoustic delivery remains unverified. T12b/libpd, recording and T13 remain open.

### T12b1: owned libpd block/message proof (implemented)

The separate Unix `native/puredata/block_probe.py` uses pinned libpd 0.16.1/Pd 0.56.5 on macOS arm64, built with `MULTI=true UTIL=false EXTRA=false DOUBLE=false`. An owned child snapshots a patch and abstraction into explicit private search paths, runs stereo 48 kHz/64-frame float blocks, checks named float message echoes, rejects a missing receiver, changes frequency/amplitude at frame 24,576, and verifies host input routing. Two fresh instances reproduce 49,152-frame audio and return the instance count to the main instance. Initialization, patch lifecycle, messages, hooks, DSP and teardown stay on one child thread; no hardware callback or DAW transport is involved. Parent logs/report/time and process-group cleanup are bounded. See [the pinned build, measurements, notices and next gates](decisions/pure-data.md).

Verification: all 12 block-diagnostic tests pass, including installed-library signal/recreation and unknown-object rejection, bad/missing runtime, patch bounds, malformed/oversized/nonfinite/symlink reports, both diagnostic pipe limits, deadline/descendant cleanup, SIGTERM cleanup and macOS exit-transition handling. Portable Rust fmt/clippy/tests pass; the existing all-feature binary and user previews are preserved. T12b2 adds schema-v8 prepared Pd session devices; the separate live block-to-queue proof is T12b3. GUI editing, live Pd playback and arbitrary external compatibility remain unavailable. T12 overall, recording and T13 remain open.

### T12b2: prepared Pure Data tracks (implemented)

Schema v8 adds embedded Pd patches, explicit abstractions, finite durations, source gains and named scalar receiver bases with saved step events. Owned libpd preparation runs before session commit, validates receiver existence and finite stereo float PCM, then caches prepared float64 output for WAV rendering and rate-matched native playback. `$0-` control names are resolved against each patch instance; other names pass through unchanged. Initialization actions run before saved bases/frame-zero values are sent and DSP is enabled; later events occur at 64-frame boundaries. This does not promise init-rate behavior or native value readback. Patch/abstraction bytes are capped at 60 KiB total; sessions share four runtime sources, ten total source-seconds, 512 points and the audio asset budget. The example in `examples/puredata_tracks_demo.py` saves/reloads two tracks and renders the fixture's 440/660 Hz control change through source and track gain. Its muted `--native` check submitted 2,048 callback frames and released resources. The GUI rejects schema v8; T12b4 below adds explicit live Pd transport. Verification: all 11 Python worker/source tests and eight Rust schema/transaction tests pass, covering mixed Csound/Pd preparation, 48,001-frame trimming, 44.1 kHz, saved events and rollback for missing receivers/objects/abstractions, invalid PCM, output limits and deadline/descendant cleanup. Prepared-cache gain/seek/loop coverage passes. Portable/native/all-feature Rust fmt/clippy/tests, build and baseline demo pass; the all-feature binary is restored. The 31 HTTP bridge and 24 Node player/editor tests pass, as do 12 libpd block and nine Csound source regressions. Native output was muted; acoustic verification is pending.

### T12b3: live libpd fixed-queue diagnostic (implemented)

An owned libpd worker now produces live blocks through the existing Csound producer ABI and SC fixed-queue consumer, with 64 slots of stereo 64-frame float32 audio at 48 kHz. It processes the patch directly, with bounded backpressure, four-block prefill, saved scalar events, and silent final-block padding. Parent and producer digests must match; the parent also checks exact block count and reopens the producer lease to verify release. Initialization, patch lifecycle, messages, DSP and teardown stay on the owner worker, outside hardware callbacks. Logs, reports and process lifetime are bounded; cancellation and failure reap the owned process group. This diagnostic adds no DAW transport or live capability.

Verification: all 14 stream diagnostic cases pass; the CLI SIGTERM process-inventory case was run separately with host access after the sandbox denied `ps`. Coverage includes matching signal/event digests, source gain left unapplied, 48,001-frame padding/trimming, restart/backpressure, producer lease release, malformed/symlink/oversized reports, both log limits, missing receiver/object, stall, producer death, cancellation and deadline/descendant cleanup. The 12 libpd block tests and four existing producer ABI tests pass. Rust fmt/default clippy/tests and the baseline scripting demo pass; the all-feature binary is restored. No hardware callback or acoustic check is part of this diagnostic.

### T12b4: Pure Data native live transport (implemented)

Primary owns Rust native ownership and session integration; Luna owns independent tests and a scripting example. Add a concrete Pd source owner using the proven queue worker and existing session mixer. Extend the worker with compile/start/stop handshakes so all sources initialize before playback begins; no foreign initialization or DSP may run on the hardware callback. Accept validated schema-v8 continuous Pd/SC/Csound/sine sessions with gain-only effects at 48 kHz, preserving default prepared playback. Use source duration/gain and saved 64-frame control events, handle the final partial block, and publish runtime-specific metadata and readiness. Keep live Pd receiver edits explicitly unavailable until a separate acknowledgment contract is implemented.

Acceptance: JSONL play/status/stop/restart and EOF clean up owned workers and queues; a two-Pd-source project and mixed Pd/Csound project complete with matching source/callback continuity; producer death stops and releases the whole session; invalid mode/rate/effect/startup failures preserve active model and revision. Exercise muted callback output separately from acoustic verification. Keep unsupported pause/seek/loop and GUI-v8 imports rejected. Update capability discovery only after the live transport path passes these checks. GUI authoring, live receiver edits, arbitrary externals, recording and T13 remain separate slices.

T12b4 implements owned Pd compile/start/stop handshakes and schema-v8 native live playback through the existing fixed callback ring. Runtime snapshots include per-track runtime/PID metadata. Fresh DSP uses saved control events; Rust applies duration, source gain, serial gain effects and automation. Natural completion validates the child report and actual queue publication count; failed startup/producer/teardown releases the whole session. Pd `source.set_control` targets reject before model/DSP changes. The muted two-Pd demo completed 48,000 exact frames with matching source/callback digest, zero underruns, expected gain/amplitude-event RMS and both PIDs reaped. All 13 native Pd transport cases and nine existing Csound transport cases pass, including mixed Pd/Csound and Pd/SC, short/partial/extended durations, valid unsupported effects, malformed initialization/completion reports, startup timeout, post-final worker error, source death and EOF. The 11 prepared-source and 14 queue diagnostic regressions pass; 31 GUI bridge and 24 Node player/editor tests pass. Portable/native/all-feature Rust fmt/clippy/tests and the baseline demo pass, with final native/all-feature library checks after the queue-count guard and all-feature binary restored. Twelve existing SC transport/control regressions pass. Two optional VST worker tests remain ignored. Native checks use monitor volume zero; acoustic, sustained latency/drift and Pd GUI acceptance remain pending.

### T12b5: revision-checked live Pd receiver edits (planned)

Primary owns the receiver delivery/publication contract and native integration; Luna owns bounded scripts and fault tests. Extend `source.set_control` for a saved unautomated Pd scalar. Resolve `$0-` names on the owning patch, require `libpd_exists`/successful `libpd_float`, retain the saved float64 base and acknowledge its actual float32 conversion. Pd has no arbitrary receiver-value introspection: describe this as message delivery plus processed-block publication, not native readback. Report callback observation separately, bound command/acknowledgment size and count, and stop asynchronously on failed/late acknowledgments. Reject stale revisions, invalid/automated targets and full queues before changing the model. Acceptance: observed callback RMS changes, unchanged rejected/stale session state, save/load/prepared-cache freshness and owned failure cleanup. Preserve source durations and saved automation.

### T12b6: constrained Pd GUI access (planned)

One owner edits GUI state/imports; Luna handles independent server/editor tests and docs. Add validated schema-v8 continuous sine/SC/Csound/Pd import, a saved Pd source/abstraction summary, native Play/Stop, stopped receiver-base edits through checked replacement, and eligible live edits using T12b5. Add patch and explicit abstraction uploads only through browser file input, with no HTTP filesystem browsing. Rust validates/prepares all sources before Apply. Preserve opaque patch text, saved events, token/host/origin guards and the existing preview. Unsupported notes/clips/foreign effects stay locked. Exercise source authoring, rejected patch/receiver recovery, live callback acknowledgment, save/load and desktop/compact layout against the real engine in an isolated preview.

### T13: agent jobs and thin UI

**Depends on:** T02, T07b, T08b, one native plugin, one runtime device. **Owner:** separate Luna tasks after interface review.

First add cancellable render jobs with progress and subscriptions. Then build minimal track/clip/device/transport/meter views through the same commands. Optional MCP translates discovery, inspect, edit, and jobs without another session model. Acceptance: Python creates/edits/saves/reloads/renders the arrangement shown by the UI, failed edits preserve state, and cancellation cleans output. Defer a visual patch editor and elaborate mixer.

## Next starting point

T11a establishes owned SuperCollider NRT score jobs; T11b adds inspected saved sources and prepared native playback. T11c1 proves owned OSC lifecycle and finite live-server capture; T11c2a proves a fixed shared-memory stream into a separate diagnostic process. T11c2b1 also proves finite live source/gain/native callback routing. T11c2b2a adds bounded saved-session CLI live ownership/mixing. T11c2b2b1 connects live native start/stop through JSONL. T11c2b2b2 adds revision-checked saved-SC-control edits with native/callback acknowledgment. T11c2b2b3 adds GUI imports, saved controls and live Play/Stop. T12a3a completes schema-v7 saved Csound sources with prepared playback; T12a3b1 proves Csound queue production to a paced diagnostic consumer; T12a3b2a connects live Csound producers and mixed SC/Csound sessions to JSONL native transport; T12a3b2b adds revision-checked live Csound scalar edits with block/readback and callback acknowledgment; T12a3b3 adds constrained GUI schema-v7 Csound authoring/import/source controls. T12b1 proves the standalone libpd block/message API; T12b2 completes schema-v8 prepared Pd tracks. T12b3 proves live libpd block production into a fixed queue with a paced diagnostic consumer. T12b4 connects that live source to native transport. Live SC/Csound/Pd transport supports start/status/stop/volume, not pause/seek/loop; live Pd receiver edits and GUI access are next. T11c transport measurements, live Pd edits/GUI, recording and T13 remain outstanding. Preserve the distinction between exported WAV jobs, prepared runtime track audio and live runtime DSP. Keep notes/audio clips and v2/v3 sessions locked in the GUI, and preserve native callback ownership, offline containment, and the play/pause/hold-to-stop interaction. Eligible saved VST3 bases can change during native playback; automated targets and structural edits require stopped playback.

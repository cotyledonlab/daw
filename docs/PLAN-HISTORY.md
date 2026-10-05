# Historical implementation tickets

Archived on 2026-10-03 when the roadmap changed to prioritize a usable sequencer. This preserves the original completion records and adapter acceptance contracts. Historical “next”, pending statuses, model assignments and prerequisites are not the current execution order; use [PLAN.md](PLAN.md). Evidence here describes earlier checks, not verification repeated during the roadmap review.

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


# Superseded sequencer roadmap and UI plan — 2026-10-03

Snapshot preserved before the follow-up repository assessment simplified execution into three delivery gates. References to immediate next tasks below describe the superseded order; current priorities are in PLAN.md. These records include the completed M1 implementation and the external review reconciliation.

# DAW roadmap

Updated 2026-10-03. This is the active execution order. Previous T01–T13 records and detailed adapter checks are preserved in [PLAN-HISTORY.md](PLAN-HISTORY.md); protocol and decision documents remain the authority for implemented behavior.

## Product direction

Build a macOS-first DAW that lets a person or script create, arrange, play, save and export music. The long-term reference is the user's “Ableton v3 parity”: an early Live-style musical workflow, with programmable devices provided by Pure Data, SuperCollider and Csound. This is a functional direction, not a claim of exact release parity. Arrangement editing comes first; clip launching, recording and deeper device integration follow. A small useful instrument/effect collection is part of the product, not an optional final polish pass.

The next deliverable is a usable sequencer, not another host diagnostic. Reuse the Rust session model, block engine, JSONL controller and existing browser UI. No engine rewrite, desktop-framework migration or second session model is planned.

## Assessment of progress

The engineering foundations are substantial. Transactional revision-checked edits, save/load, deterministic note scheduling, PCM audio clips, native transport, seek/loop and gain automation exist. Optional VST3 effects and SC/Csound/Pd source adapters provide working integration paths. The owned-worker, bounded-queue and callback discipline should be retained.

At the assessment baseline, product progress lagged engine progress: the GUI accepted continuous clip-free sessions in selected schemas and rejected sequenced notes/audio clips. M1 now exposes built-in sine note arrangements and a basic piano roll; audio-clip GUI editing is still missing. The only built-in note instrument is sine and the built-in effect is gain. Recording, a portable GUI audio-project workflow and clip launching are absent. Native playback and ordinary export stop at 60 seconds; runtime sources and foreign offline effect renders have tighter ten-second bounds. These are real usability constraints, not details to hide behind a parity claim.

The previous plan put jobs/subscriptions before the timeline and repeatedly expanded adapter diagnostics, transport and controls. Recent commit history is dominated by runtime adapters while the musical workflow remains script-only. Those proofs reduced genuine integration risk, but further breadth now has lower value than making the existing sequencer usable. The eight schema generations also create growing GUI compatibility work; avoid repeating it for every UI feature.

Review evidence: source/model/tests and recent commits inspected; `cargo fmt --check`, baseline Clippy with warnings denied, `cargo test --locked --offline`, 24 Node editor/player tests and 31 Python GUI bridge tests pass on this working tree. Bridge tests required loopback socket access. Native hardware, all-feature builds, installed runtimes, acoustics and end-to-end composition were not reverified in this review. Passing portable tests does not establish release readiness.

## Current capability boundary

| Area | Available now | Missing for a usable DAW |
| --- | --- | --- |
| Sequencing | GUI sine-note arrangement/piano roll; scripted PCM clips, frame timing | GUI audio-clip editing and richer note tools |
| Transport | Native prepared play/pause/stop/seek/loop and GUI note playhead; browser sine audition | Longer practical sessions and live graph edits |
| Devices | Sine, gain; optional constrained VST3 effects and Apple AULowpass offline | Small musical instrument/effect set; broader plugin workflow |
| Programmable sources | Saved SC/Csound/Pd programs, prepared playback; constrained native live paths | Note/event-driven devices, consistent GUI access, longer sources |
| Persistence | Validated JSON sessions, project-relative audio assets in headless workflows | Browser asset import and portable project round-trip |
| Editing | Checked replacement/batches; note/clip tools; bounded applied undo/redo; source/plugin controls | Automation editor, mixer essentials and drag gestures |

Pre-existing uncommitted Pd receiver-control changes and tests are present (old T12b5). They are work in progress, not a completed capability claim. The working-tree deletion of `AGENTS.md` is left intact. Do not overwrite or discard either change as part of roadmap work.

## Execution order

The immediate usability patch and next musical-device milestone receive detailed implementation tickets. Ship a playable result at each gate; do not require all adapters or schema variants to reach feature parity before proceeding.

### M1 — Compose and loop a note arrangement (implemented; early MVP)

**Goal:** make a 16-bar, 120 BPM, two-track phrase in the browser, change notes, duplicate a phrase, loop it, save/reload and export it without writing JSON. This fits existing duration limits. Native audio is the arrangement playback path; keep Web Audio sine audition as its existing separate mode.

1. **M1a — Show an existing arrangement and control its transport.** Load the arpeggio fixture, show track lanes, beat/bar ruler and clips, and wire native play/pause/stop, playhead, seek and loop to existing commands/status. Start with built-in sine note sessions in v2/v3 and equivalent supported v4 shapes. Preserve existing continuous-session editing. Report unsupported sessions visibly and retain their data; never coerce a runtime or plugin session into a supported subset. Gate: fixture timing and loop match native status; save/reload preserves it.
2. **M1b — Author clips and notes.** Add/delete/move/resize/duplicate note clips and a basic piano roll with add/delete/move/resize notes, velocity and grid snap. Display MIDI pitches but convert to the existing saved Hz representation. Convert authoring beats to frames using the exact timeline contract; a tempo edit changes the grid, not existing clip positions. Apply checked replacements while stopped; no new live graph-edit contract. Gate: create the two-track phrase, make an edit, reject an invalid edit without losing the applied arrangement, then save/reload/export the same notes.
3. **M1c — Make editing recoverable.** Add bounded editor undo/redo for successful arrangement edits, keyboard delete/duplicate and clear dirty/conflict/error feedback. History must respect engine revisions and reset on import; failed edits must not advance it. Gate: undo/redo clip and note edits through the authoritative engine, then repeat the complete phrase workflow against the real GUI and native engine.

Likely files: `gui/editor.js`, `gui/app.js`, `gui/index.html`, `gui/style.css`, `gui/server.py`, and focused editor/bridge tests. Touch Rust only where an actual interface gap is demonstrated. Use polling already available for transport; render jobs, subscriptions, MCP, waveform drawing and a generic scene/graph system are not M1 prerequisites.

### M1d — Make the current sequencer practical (next patch)

Close the short list of editor/transport correctness and usability gaps before increasing device scope:

- **Make the workspace read as a sequencer.** Follow [UI-PLAN.md](UI-PLAN.md): prioritize the arrangement and persistent transport, consolidate track creation, make clip placement explicit, keep selection/keyboard focus stable, and put validation beside the selected editor. Move settings and device detail out of the main composition flow. Verify readable desktop and narrow-window layouts. This is a focused pass over existing controls; drag editing, waveforms and a panel framework can follow.

- **Preserve untouched data.** Frames remain authoritative. Round absolute tick positions once, snap in tick space, and do not re-quantize untouched notes after changing tempo or editing another field. Display off-grid timing honestly. Use A4=440 Hz/12-TET only for explicit pitch edits; non-12-TET saved Hz must remain unchanged when editing velocity/timing or saving. Add off-grid timing and microtonal load/edit/save cases to the workflow regression.
- **Keep clip operations non-destructive.** Notes remain clip-relative, duplicates are independent deep copies, and same-pitch overlaps are allowed. The current schema cannot represent notes beyond a clip end, so keep rejecting a resize that would cut a note gate; do not silently hide, trim or delete it. Make that reason visible. Existing half-open clip and voice rules remain authoritative.
- **Remove the musical loop cliff for built-ins.** Add an explicit until-stopped playback mode restricted to bounded built-in-only prepared sessions. Verify looping continues past 60 seconds, while Stop, EOF, errors and restart release resources. Retain bounded queues/storage and all existing runtime/plugin timeouts; ordinary finite playback/export stays supported. This is transport work with native checks, not a global timeout deletion.
- **Offer an explicit Stop & edit workflow.** Keep the stopped structural replacement contract, but let the user enter editing through a clear action while playback is running. Automatic restart/position restoration can wait. Validate candidate edits before disrupting playback where possible; revision/engine rejection must preserve the applied session and offer recovery from the authoritative state without silently losing a local draft.

Gate: one bridge-driven regression creates and edits a phrase, rejects an invalid/stale edit, saves/reloads and compares note/clip frames and pitch values, then compares WAV output. Add a bounded native check past the old 60-second loop limit and one quiet listening pass. Use this end-to-end gate alongside focused boundary tests; it does not replace allocation, ownership or failure-path checks.

### M2 — Give the sequencer drums and a useful synth

Prioritize a small built-in sound set ahead of user-audio import and ZIP packaging. M1's note editor already gives it a place to play. Basic music creation must work without installing SC, Csound or Pd.

1. **M2a — One compact drum kit.** Trigger project-owned kick, snare and hat samples from note clips, with a fixed pitch-to-pad mapping, gain and one-shot behavior. Reuse decoded PCM buffers and scheduling/voice storage where appropriate; PCM clip playback alone does not yet provide note-triggered sample voices. Save stable bundled sample IDs, resolve them to a versioned app-owned registry outside callbacks, and reject unknown IDs transactionally. Provide one shipped beat preset. No file browser, arbitrary user assets, pad routing matrix or generic sample format is needed.
2. **M2b — One simple polyphonic synth.** Add a saw/square voice with envelope, one useful low-pass filter and a few bass/lead presets. Reuse note scheduling and the bounded voice contract. Match saved parameters, GUI controls, offline/native behavior and explicit reset semantics. Add only concrete device fields and explicit migration/old-file coverage. No generic processor/device rewrite.

Gate: author a 16-bar drum/bass/lead arrangement in the GUI using the shipped devices, loop it, save/reload, and export it. Inspect basic audible quality as well as timing, headroom and allocation behavior. These are planned devices, not present capabilities.

The wider one-shot sampler, extra kits, richer synth controls and filter/EQ, delay and saturation effects follow this gate in small slices. A full device collection is not required before the first drum-and-synth arrangement ships. Only use project-owned or appropriately licensed sounds/presets with pinned notices.

### M3 — Import audio and carry the project with it

Add PCM WAV import through browser file input, audio track lanes and clip move/trim/duplicate/gain/source-offset editing. Use the current integer-PCM and matching-rate contract first; report unsupported inputs clearly. Waveforms can follow a working clip workflow.

Now implement the user-asset/project flow against the concrete sampler and clip contracts: bounded upload into an owned project directory, checked replacement using that root, and project export/import containing JSON plus relative assets. A ZIP package is sufficient; validate paths and sizes, reject traversal, and clean temporary files outside callbacks. Bundled IDs stay resolvable through the shipped registry; user samples need portable project assets. Keep JSON-only save for asset-free sessions. Do not expose arbitrary host filesystem paths through HTTP.

Gate: import a WAV, combine it with the M2 musical arrangement, trim/duplicate it, save the project, reload from a different directory and export identical clip placement/audio. Missing or invalid assets leave the applied project intact.

### M4 — Finish a short track reliably

Add track mute/solo/pan and metering, basic gain automation editing, useful clip fades and reliable project recovery. Extend built-in/prepared playback and export to a three-minute arrangement through explicit bounded resource policy; the built-in until-stopped loop mode from M1d remains available. Do not simply remove every timeout or foreign-runtime memory bound. Longer export may require progress/cancellation here, driven by measured UI blocking rather than as a timeline prerequisite.

Gate: finish and reopen a three-minute arrangement with notes, samples, automation and effects; loop while editing between playback runs; render it; verify native start/stop/restart and resource release. Run at least one quiet acoustic listening check as well as deterministic export and callback tests. Failure/asset recovery and audible quality belong to this milestone, not only harness statistics.

## After the usable sequencer

Expand one workflow at a time, chosen from actual music-making needs:

- **Recording and performance:** MIDI input/recording, audio recording, monitoring and latency behavior, then clip launch/stop quantization, scenes and recording a performance into the arrangement.
- **Programmable devices:** unify visible device controls/presets, then one note/event-driven SC, Csound or Pd instrument end to end. Extend that proven contract to the other runtimes, including saved state, automation and explicit seek/loop/reset behavior. Retain prepared/frozen audio as a useful fallback. A visual patch editor is a separate later choice.
- **Plugin workflow:** improve the existing VST3 route before expanding formats: safe discovery, state/editor workflow, note-capable instruments and then latency compensation as supported plugins require it. AU expansion waits for a specific compatibility need.
- **Musical depth:** routing/returns, tempo retiming/maps, resampling/time stretching, richer automation, longer projects and performance hardening. Agent jobs/subscriptions/MCP follow demonstrated client needs.

These are not completed capabilities or a literal Ableton release checklist. When M4 is usable, build a workflow-based parity matrix and choose the next gap. Exact “v3” release scope can be clarified then without delaying the sequencer.

## How to move faster without losing the foundation

- Keep one active product milestone and one integration owner. Use small commits, but assess progress by a demonstrated musical workflow rather than the number of adapter subtickets closed.
- Reuse checked replacement, existing DSP and transport. Prefer stopped structural edits, status polling and bounded UI history before introducing live graph mutation, event infrastructure or generic abstractions.
- Isolate format compatibility in editor adapters/helpers rather than scattering more version checks through every view. Preserve opaque unsupported data and explicit migrations; old session formats remain supported by the engine.
- Park new host formats, more standalone runtime probes, broad refactors and expanded live receiver controls unless they fix a demonstrated regression or directly unblock the current gate. Preserve the in-flight Pd work for a focused completion/review; it is not a sequencer dependency. Old T12b6 GUI parity, remaining T11 measurements and T13 infrastructure are deferred, not erased.
- Run focused regressions while iterating and baseline checks at completion. Add native/FFI/installed-runtime suites when those paths change; documentation-only revisions do not require every hardware adapter to be rebuilt. Retain transactional validation, bounded ownership and callback constraints.
- After each gate, demonstrate create/edit/play/save/reload/export against the real engine, record remaining limitations, and refine only the next milestone. Investigate recurring failures before adding more breadth.

No allocation, blocking locks, filesystem/network operations, process startup or foreign initialization belongs in the audio callback. Outputs remain exclusive, session failures preserve state, and stdout remains the JSONL protocol. Faster product delivery comes from narrower scope, not weakening those contracts.

## Execution record — 2026-10-03

Three GPT-6.1 Sol agents at medium reasoning implemented the editor model/history, bridge, and timeline view in parallel; the primary integrated app state and playback. Internal review caught and fixed history transition ordering, stale transport polls and loop-overlay updates. M1a/b/c now provide track lanes, a beat/bar ruler, piano-roll numeric editing with grid snap, clip authoring/duplication, native playhead/seek/loop/Stop, and bounded applied-edit undo/redo. Existing session and audio contracts remain unchanged. The shipped `examples/sessions/note-demo.json` is a two-track, 16-bar/32-second phrase.

Verified: 33 Node player/editor/history tests; 38 HTTP bridge tests; a 481-pixel browser layout with no page overflow; Rust format/native Clippy/native tests; real-browser clip duplication, note-pitch changes, undo/redo, track/clip/note creation and fixture import; muted native play/pause/seek/loop/stop with frame-48000 paused seek acknowledgment. All-feature engine build restored after native checks. No acoustic quality claim or installed-runtime regression claim is made by these checks. Editing uses fields/buttons rather than drag gestures; visual polish, recording and richer instruments remain later work. The 32-second demo saves/reloads through the engine and renders byte-identical stereo PCM16 WAVs with zero clipping. Browser save/export were exercised, but the in-app browser did not provide a download-event artifact for independent file inspection.

The requested Opus 5.5 review completed through pi's OpenCode provider after the user selected OpenCode. The full response is preserved in [OPUS-REVIEW.md](OPUS-REVIEW.md). The earlier OpenRouter transfer was rejected and was not performed.

Accepted feedback: move drums/simple synth ahead of user-asset packaging; bring practical built-in looping forward; explicitly preserve untouched frame/Hz data; keep snapshot undo; use one musical workflow gate; defer waveforms, velocity lanes, automatic restart and infrastructure. The review also supports the existing separate playhead updates and field-based velocity editing. Keep the current shared session helpers rather than introduce another canonical model or broad schema migration. Clip-end hiding conflicts with the engine schema, so retain non-destructive resize rejection instead. Stop & edit is an explicit next-patch affordance, not a claim that current playback editing is already supported.

The user clarified that the desired external critique was UI/UX/usability. A second Opus 5.5 review completed through OpenCode after explicit approval for the demo screenshots and layout source; the full response is in [OPUS-UI-REVIEW.md](OPUS-UI-REVIEW.md). Its focused recommendations are reconciled in [UI-PLAN.md](UI-PLAN.md): sticky transport, arrangement-first hierarchy, contextual inspectors, readable canvas geometry, stable focus, local persistent feedback and explicit clip placement. Existing mute/solo scope and the stored velocity representation are unchanged. Review evidence is a narrow screenshot plus source; a desktop visual pass remains part of implementation verification.

## Immediate next task

Start **M1d** with the unchanged-field pitch/timing regression and focused [UI plan](UI-PLAN.md): editing trust, then arrangement-first hierarchy. Built-in until-stopped transport work can proceed independently once the Stop & edit behavior is agreed in the UI. Then implement **M2a** drums and **M2b** synth as separate device slices. Independent fixture/preset/UI work can run in parallel once the concrete saved-device contracts are settled. Full user-audio import/project packaging is M3. Avoid new runtime adapters, render subscriptions and wholesale schema changes as prerequisites.


# Sequencer UI plan

Updated 2026-10-03. This is the UI workstream for [M1d in PLAN.md](PLAN.md#m1d--make-the-current-sequencer-practical-next-patch). It narrows the next interface pass; it does not add an engine rewrite or another product milestone.

## Evidence and assessment

This review combines the shipped demo screenshots, a local source audit of `gui/index.html`, `gui/style.css`, `gui/timeline.js` and the app interaction handlers, and the completed [Opus 5.5 UI review through OpenCode](OPUS-UI-REVIEW.md). The user explicitly approved sending the two screenshots and layout/timeline files. The screenshots are from a 481-pixel viewport. The first viewport shows the header, large title, sample-rate setting, playback card and competing track actions; the musical canvas starts farther down. Desktop layout recommendations are source-based inferences and must be checked in a desktop viewport. A new live capture timed out; the external reviewer did not inspect the running app.

The dark palette, clear button styling and existing arrangement/piano-roll foundation are useful. The largest problem is hierarchy: settings, host explanations and session actions consume the space needed to compose. The interface still presents separate track-control cards and an arrangement editor as equally important surfaces. A sequencer should make selection, musical position and the next edit obvious.

## Ranked changes

| Priority | Change | Why / completion evidence |
| --- | --- | --- |
| P1 | Preserve untouched note fields | Updating velocity currently rebuilds snapped timing and rounded MIDI pitch. Keep the original frame/Hz values unless the corresponding field is explicitly changed. Test an off-grid, non-12-TET note through selection, velocity edit, undo and save/reload. This is already an M1d requirement. |
| P1 | Put the arrangement in the first viewport | Replace the large page title and playback card with a compact studio header/transport. Move sample rate, output selection and technical playback help into Settings/help. At 1280×800, show the transport, ruler and two track lanes without scrolling; selecting a clip should reveal a useful piano roll in the same workspace. |
| P1 | Make feedback local and editing state explicit | Put note/clip validation beside the editor that initiated it and retain it until corrected/dismissed; state changes must not replace it with boilerplate. Distinguish “Applied to engine” from “Saved to file”; the current applied state is not proof of a disk save. Show one clear Stop & edit action when playback locks editing, rather than only disabled controls and explanatory text. Preserve the applied arrangement on failed edits. |
| P2 | Use one composition entry point | Make Add track the main creation action, starting with a note instrument. Move continuous sine/runtime-source creation into a secondary device/source choice. Put New, demo, load/save and export in session actions. The empty state should offer Create note track and Open demo, leading to a clip and piano roll. |
| P2 | Give clip insertion its own visible position | Add clip currently reads the unrelated Seek field. Give it an explicit start position beside the action, or an action label that states the destination. Use the selected track as its context. Repeated insertion must not silently keep targeting beat zero. |
| P2 | Keep selection and keyboard focus stable | Selecting a clip or note rebuilds its SVG and removes the focused node. Restore focus to the selected item and expose selected state accessibly. Enter/Space, Delete, duplicate and undo must work repeatedly after selection and successful edits; do not intercept shortcuts while typing in a field. |
| P2 | Keep ruler text and notes readable at real size | The 1000-unit SVG shrinks 10-unit labels and 11-unit notes substantially at its 640-pixel minimum width. Stop scaling the whole musical canvas to fit. Preserve readable text and note rows, allow canvas scrolling and retain track/pitch labels. Remove the 590-pixel shell cap that wastes space below the 980-pixel breakpoint. Verify at 1280, 900 and 481 pixels wide. |
| P2 | Group controls by the selected object | Separate transport/loop/grid from the clip inspector and note inspector. Reduce equal-weight Move/Resize/Update/Delete button groups; Enter should commit the relevant field group without triggering a different action. Keep exact numeric authoring, expose clear units, and make delete secondary. Use one consistent bar/beat convention; avoid requiring users to translate zero-based beats and one-based bar labels mentally. |
| P2 | Distinguish playback, selection and unavailable actions | The playhead and selected note currently share a yellow color. Give the playhead a distinct appearance and selected notes/clips a clear outline as well as fill. Make enabled/disabled states easy to distinguish, with an accessible explanation of unavailable actions. Keep one primary action per region. These are design changes, not claims of measured contrast failure. |

These priorities concern authoring trust and workspace clarity. They do not require drums, a new device format, live graph editing or a full DAW gesture system.

## Target workspace

Desktop: a compact top row contains session name, file actions and undo/redo. A persistent transport row contains Play/Pause, Stop, musical position, tempo, loop and listening level. The arrangement is the main central area, with track names/essential level controls alongside its lanes. Selecting a clip opens the piano roll beneath it; a compact selection inspector contains clip or note values. Device controls occupy the selected track's inspector rather than repeating as large cards beneath the timeline. Settings and export open on demand and return focus when closed.

Narrow windows: preserve transport and the selected editing context. Stack the inspector below the canvas or show it as a drawer. Scroll the arrangement/piano roll horizontally inside the canvas region, retain readable rows, and avoid shrinking the whole application to a phone card. Keep secondary session/settings actions in a menu. A complete mobile music-making interface is not this milestone's goal.

Keep the current restrained dark visual language. Use accent color for selection and the primary action, distinguish the playhead from the loop range, and give disabled/locked controls a nearby explanation. Do not use color as the only indication of selection or pending state. Prefer tooltips or concise contextual help to permanent paragraphs about backend behavior; provide actual capability/limit messages when they affect the current action.

## Opus feedback adopted and qualified

Adopt the compact sticky transport, removal of the hero/standing technical prose, one composition entry point, clip/track detail grouping, readable pixel-sized geometry, local persistent errors, explicit clip placement, stable focus and changed-field-only note patches. Add note names beside MIDI pitch. A one-based bar/beat display is the target for the musical position/ruler; keep exact saved frames/Hz available in advanced detail and show off-grid values without rounding them into a false snapped position.

Qualify the wider suggestions to keep this pass small:

- Fold existing gain/effect controls into lane/detail views. Do not add mute solely because it appears in the review's proposed header; mute/solo/pan remain M4 work.
- Retain the stored continuous velocity range of 0–1. Label it explicitly in the numeric MVP; a MIDI-style or percentage presentation can follow without quantizing stored values or changing the session schema.
- Make the disabled state visible and its reason available by keyboard as well as pointer. The proposed dashed borders are optional styling; a `title` on a disabled button is not a sufficient explanation.
- Retain the existing New/demo replacement confirmation in `gui/app.js`; Opus did not receive that handler and missed the safeguard. Demote the actions visually rather than add another confirmation flow. Keep file-save/export wording distinct from engine-applied state, without claiming a download is durably saved when the browser cannot confirm it.
- Keep numeric fields and explicit commits. Enter-to-commit is useful; drag editing, Space transport shortcuts, audition and arrow nudging can wait for deliberate focus/shortcut behavior.

## Two focused implementation batches

1. **Editing trust and discoverability.** Preserve untouched values; restore selection focus; make clip placement explicit; show local persistent validation and Stop & edit. Clarify applied/file-save wording and retain the existing New/demo guard. These changes can use the existing replacement/transport contract. Gate: a keyboard-only user selects a clip/note, changes velocity without retuning/snapping it, duplicates/deletes/undoes, sees a rejected edit beside its controls after transport state updates, and enters editing from playback without losing the session. Verify the existing replacement guard still works after moving its controls.
2. **Workspace hierarchy.** Compact the header/sticky transport, consolidate track creation, move Settings/export out of the main canvas, group selection inspectors, improve selection/playhead/disabled styling and fix canvas sizing. Gate: at desktop size, a new user opens the demo, identifies tracks/loop/playhead, selects a phrase, changes a note, duplicates it and finds save/export without scrolling past unrelated device cards. At narrow size transport remains visible, the same controls remain reachable and the canvas alone scrolls horizontally.

The hierarchy pass can run in parallel with independent built-in transport work after the editing-state contract is settled. Assign one owner to `gui/app.js` integration; avoid simultaneous broad edits to the same UI files. Use current session fixtures and the real engine for workflow verification. Add targeted interaction regressions for data preservation/focus; do not turn CSS rearrangement into a broad testing project.

## What can wait

Drag/multi-select editing, sophisticated zoom, velocity lanes, waveform drawing, a resizable panel system, custom icon libraries, a theme redesign, animation and device-browser polish can follow actual music-making use. Essential readable geometry, visible selection, reliable keyboard actions and nearby errors belong in this pass. Once the basic layout works, simple drag/move/resize gestures are a useful later speed improvement rather than a prerequisite for reorganizing the existing controls.

# Roadmap snapshot before the Live 4 reassessment (2026-10-03)

Superseded by the current PLAN.md. Preserves gate-1/M3 implementation evidence and prior acceptance contracts; the status and next actions below are historical.

# DAW roadmap

Updated 2026-10-03 after a fresh repository assessment. This is the execution order. [PLAN-HISTORY.md](PLAN-HISTORY.md) preserves completed tickets, the superseded detailed roadmap and review records; [PROTOCOL.md](PROTOCOL.md) defines implemented behavior.

## Direction and assessment

Build a macOS-first DAW for making music: a workable sequencer/timeline, a small built-in instrument/effect collection, and programmable devices using Pd, SuperCollider and Csound. “Ableton v3 parity” is the long-term workflow reference, not an exact historical release checklist. Arrangement composition comes first; recording, clip launching/scenes, routing and stretching remain part of the destination.

**The foundation is useful; delivery has been too integration-heavy.** The repository has transactional edits, persistence, deterministic note scheduling, preloaded PCM clips, native transport, gain automation and constrained foreign-device paths. Most of the last 35 commits concern runtime/plugin integration or its documentation. Those proofs bought real safety and lifecycle knowledge, but they have not yet become a cohesive music-making workflow. The latest note editor is a meaningful change of direction.

**The previous roadmap already chose the right priorities, but its next patch was too broad.** Correctness, a layout overhaul, indefinite transport and interaction refinements were bundled ahead of musical instruments. Split that work, keep the essential fixes, and ship drums/synth without waiting for every UI recommendation. Stop adding adapter breadth until an existing adapter participates in an actual composition.

### What the code supports today

| Area | Implemented | Main gap |
| --- | --- | --- |
| Notes/timeline | Sine/drum/synth GUI arrangement, piano roll, clip operations, exact untouched-field preservation, snapshot undo/redo | Numeric editing; drag editing and multiselect deferred |
| Audio | Owned browser PCM WAV import; move/trim/duplicate/source-offset/gain audio lanes; portable project ZIP save/reopen; native playback/export | Waveforms, drag editing, fades, resampling and longer export |
| Transport | Prepared native play/pause/stop/seek/loop; GUI playhead; built-in/PCM/gain-only until-stopped playback | 60-second export and finite foreign-device playback; native rate matching; hard-reset loop discontinuities |
| Devices/mixing | Sine, factory drum kit, polyphonic saw/square synth with bass/lead presets, gain; saved step gain automation; constrained VST3 effects and offline Apple AULowpass | Basic mixer and small dependable effect collection |
| Programmable sources | Saved SC/Csound/Pd sources; prepared and constrained native live paths | Note-driven instruments/effects with timeline reset/loop behavior; consistent GUI access |
| GUI source support | Selected continuous SC/Csound sessions and controls | Pd GUI support and mixed sequenced-device workflow |
| Performance | No recording or clip launcher | MIDI/audio recording, scenes, routing, latency and stretching |

The assessment found note Update rewriting untouched frames/Hz, insertion sharing Seek, focus lost on redraw, and settings competing with composition. These are now corrected and covered through the real timeline handler. `src/audio.rs` retains finite deadlines and adds an explicit built-in until-stopped path; `src/render.rs` still independently caps ordinary export at 60 seconds. Drum and synth voices reuse the scheduler and prepared DSP path.

Initial assessment checks passed before implementation. The gate-1 implementation evidence below supersedes those counts; prior integration/native evidence remains in history. Installed foreign runtimes were not reverified for this built-in slice.

Pre-existing Pd receiver-control edits/tests and the deletion of `AGENTS.md` remain untouched. Pd live receiver edits are work in progress, not a shipped capability. Complete/review that work separately when resumed; it is not on the sequencer's critical path.

## Three delivery gates

Keep one gate active. Finish each slice through model, sound, GUI, save/reload and export; do not build a whole subsystem headlessly and postpone its user workflow. Existing IDs remain useful: gate 1 covers M1d/M2, gate 2 combines M3/M4.

### Gate 1 — Make a musical loop (implemented; listening sign-off pending)

**Done when:** create a 16-bar drum/bass/lead arrangement without JSON or installed runtimes, edit and duplicate phrases, loop until stopped, undo an edit, save/reload and export the arrangement. Demonstrate through the real GUI/engine and listen quietly. A restart must release the previous transport/resources.

Three Sol 6.1 medium-thinking agents delivered independent engine, timeline and workspace/transport slices; the root owned integration. An authorized pi/OpenCode [Opus 5.5 review](OPUS-MVP-REVIEW.md) informed the generated bank, named drum rows, template, pitch feedback and regression coverage.

| Slice | Delivered behavior |
| --- | --- |
| M1d.1 — Trust note edits | Update patches changed fields only; off-grid frames and microtonal Hz survive single-field edits/tempo changes. Float64 JSON parsing roundtrips exactly. Persistent errors, explicit reload recovery and Stop & edit with note focus preserve stopped-only edits. |
| M2a — Drum kit | Schema 9 `factory-v1` generated kick/snare/hat, MIDI 36/38/42 mapping, velocity/gain, named drum roll and shipped 16-bar beat. Seeded bank prepared outside callbacks; unknown kit/pitch rejected before commit; PCM16 golden hashes at 44.1/48k. |
| M2b — Polyphonic synth | Schema 9 band-limited saw/square, bounded linear attack/release, one-pole low-pass, bass/lead presets and saved GUI controls. Independent bounded voices, deterministic release/seek/loop/clip-end rules, spectral and allocation checks. |
| M1d.2 — Practical playback | Explicit until-stopped native mode for sine/synth/drumkit plus gain only; finite and foreign-device limits retained. Pause/seek/loop/stop/restart/EOF use existing ownership. |
| M1d.3 — Composition workspace | Compact sticky transport, arrangement before devices/settings, one instrument chooser, independent clip insertion beat, stable SVG focus, exact pitch feedback. Device cards remain below the arrangement; a full selected-device inspector is deferred. |

**Evidence:** 159 portable Rust tests, 170 native-audio Rust tests, 42 Node interaction/editor/player/history tests and 39 HTTP bridge/workflow tests passed. Rust format and baseline/all-feature Clippy with warnings denied passed; the `vst3-live,au-offline` build was restored for the GUI. The same 16-bar/32-second project saves/reloads exactly and exports twice with byte-identical WAVs, zero clipping and peak 18265/32767. A muted MacBook Air Speakers/48k native run continued for 65.38 seconds through 32 loop wraps, checked pause/seek/resume, stop/restart and EOF release, and reported zero callbacks over budget (maximum render 1.73 ms). The real browser exercised note editing, undo, clip copy, failed resize, explicit recovery, native loop/pause, Stop & edit, JSON download and 32-second WAV export. At 1280×800 all three lanes and transport fit; 900/481 widths retain canvas scrolling without page overflow. CI includes the new browser-model and bridge tests.

**Remaining sign-off:** listen quietly to the exported project and native loops; hard-reset seek/loop can click, and the minimal filter/envelope may need musical tuning. Automated/muted checks do not prove acoustic delivery. Treat that as focused product feedback, not a reason to delay audio import or begin another architecture review. Export stays at 60 seconds; without a loop, until-stopped playback continues silently after the arrangement. No recording or mixer is claimed; gate 2 below adds the audio GUI.

Use the same drum/bass/lead project as the acceptance fixture. Verify frames, pitches, saved device settings, failed/stale edits, reload and deterministic WAV output. Musical quality and ability to find controls are separate checks from deterministic output. Keep exports within the current 60-second limit for this gate.

### Gate 2 — Finish and reopen a short song (active)

**Done when:** arrange and export a three-minute piece with the gate-1 instruments, an imported WAV, automation and basic effects; reopen the project elsewhere with all user assets present.

Deliver in small end-to-end slices:

- **Audio clips and project portability (M3 — implemented).** Owned PCM WAV upload; audio lanes with move/trim/duplicate/gain/source offset; bounded ZIP save/reopen in a fresh engine. PCM16/24/32 mono/stereo and matching-rate restrictions are visible at import. Failed/stale/invalid imports preserve the project; HTTP only accepts registered owned assets. JSON-only save remains for asset-free sessions. Prepared PCM now participates in until-stopped playback without callback file I/O/allocation. See [GUI audio projects](decisions/gui-audio-projects.md).
- **Mixing and tools (M4).** Track mute/solo/pan, level meters and master headroom; edit the existing gain automation; basic clip fades and recovery of unsaved work. Start with the mixer functions actually needed by the fixture.
- **Small effect set.** Add a track filter or simple EQ, delay, then saturation, one usable device at a time with bypass, saved settings and presets. Preallocate state, define reset/tail behavior and compare offline/native output. These built-ins provide the first dependable collection; broad third-party plugin compatibility is not a prerequisite.
- **Longer projects.** Raise built-in/PCM arrangement playback/export to a measured, bounded three-minute policy. Until-stopped transport does not extend the render limit. Stream export from existing blocks rather than prerendering the entire song; add progress/cancellation when measured blocking requires it. Keep foreign runtime/plugin limits explicit instead of globally deleting timeouts.

**M3 evidence:** 162 portable Rust tests, 173 native Rust tests, 49 Node tests and 52 HTTP bridge tests passed. The four-second mixed project exports with zero clipping and reopens its ZIP in a fresh engine with byte-identical WAV output. A muted native PCM/instrument loop, pause/seek/resume and stream-release check reported zero callback overruns (maximum render 1.88 ms). A portable 64-second mixed PCM/instrument loop test deletes the source file after preparation and observes zero callback allocations/frees. Real browser import at a chosen beat, trim/offset/gain, duplication/undo, rejected source-range preservation and ZIP download were verified. Waveforms/resampling/fades, mixer/effects and three-minute export remain open.

Waveforms, user-sample loading into the kit and simple drag/move/resize gestures can follow the working import flow. Do not make those a prerequisite for saving a portable song. The current finite runtime-source path need not cover three minutes to close this built-in/PCM gate.

### Gate 3 — Make one programmable device part of the song

**Done when:** load one Pd, SC or Csound device/preset, sequence it from the piano roll, edit exposed controls, save/reopen, loop/seek with defined reset behavior and export matching music.

Choose one existing runtime with a concrete working program and installed test coverage when this gate starts. Prove note on/off, parameter/state persistence and transport behavior in the same composition UI before extending the contract to the other two runtimes. The present continuous ten-second source demos do not satisfy this gate. A prepared/frozen audio fallback can ship first if it avoids solving live reset/clock behavior prematurely; document the distinction. Runtime effects, longer sources and richer live controls follow actual device needs.

Use one shared visible device/preset workflow while retaining each runtime's ownership/lifecycle boundary. Do not build a patch editor, universal graph or complete GUI parity across eight schemas before the first sequenced device works. Existing integration contracts and fault containment remain valuable.

## Long-term workflow map

Keep this map visible now, refine the next slice after each demonstration. It is an intended scope, not a completion percentage or a claim about Live 3's precise feature set. Ableton's [official concepts](https://www.ableton.com/en/live-manual/12/live-concepts/) provide a workflow vocabulary; this project's instruments and programmable-device goals come from the user.

| Workflow | Delivery point |
| --- | --- |
| Compose/play/edit/save/export note arrangements with drums and synth | Gate 1 |
| Arrange user audio, mix, automate, use a small effect/tool set and carry a project | Gate 2 |
| Drop in a sequenced programmable instrument; extend to Pd/SC/Csound devices | Gate 3 |
| Play/record notes from a MIDI keyboard; import/export MIDI | Next composition workflow |
| Record audio with monitoring and measured latency | Next recording workflow |
| Launch quantized clips/scenes and capture a performance into the arrangement | Next performance workflow; reuse existing clips/scheduler |
| Tempo-aware audio loops, resampling/stretching and musical retiming | Required for the longer-term Live-style loop workflow |
| Sends/returns, routing, freeze/resample and reliable plugin discovery/state/editor workflow | Expand when a song demonstrates the need |
| Broader VST3 instruments/latency compensation and performance hardening | Later compatibility/release work; AU expansion needs a concrete use case |

## Working rules for faster progress

- Choose the next task by what it adds to a playable project. After each slice update status with a demonstrated result and its remaining limits; avoid another planning/review cycle unless new evidence changes the order.
- Reuse Rust, JSONL, the browser bridge, checked replacement, polling and bounded snapshot undo. Keep stopped structural edits. No framework migration, second canonical session model or speculative processor abstraction.
- Centralize capability/version handling in existing editor helpers as touched. Preserve old formats and unsupported data; do not require every runtime/schema to support a feature before shipping it for built-ins.
- Freeze new host formats, standalone runtime probes, AU expansion, render subscriptions/MCP and broad refactors. Resume only for a reproduced regression or a dependency of the active musical workflow.
- Run focused checks during iteration; complete Rust format/lint/tests and the relevant Node/bridge checks at the slice boundary. Add native/FFI/installed-runtime suites when those paths change. Gate demonstrations add save/reload/export and quiet listening; they do not replace allocation/failure-path checks.
- Preserve transactional failure behavior, exclusive output publication and bounded resource ownership. No allocation, blocking locks, I/O, process startup or foreign initialization in the audio callback.

**Next action:** basic mixer for the same mixed audio/instrument project: track mute/solo/pan, level meters and master headroom. Then add one useful delay/filter slice and extend measured export to three minutes. Keep portable ZIP and native loop checks as acceptance fixtures. Do not start another runtime diagnostic or full UI overhaul.

## Fresh Live 4 roadmap assessment — 2026-10-03

Assessed the current dirty working tree and last 35 commits. Replaced the active plan's Live 3 reference with the user's Live 4 direction, split gate 2 into mixer, gestures, longer export, effects and finishing slices, and kept one sequenced programmable device as gate 3. Updated UI-PLAN.md and the README direction paragraph. The immediately preceding snapshot preserves all earlier gate-1/M3 evidence. No implementation files were changed for this assessment.

Checks repeated for this assessment:

- `cargo test --locked --features native-audio`: 173 passed; includes portable engine/allocation and transport tests, but is not a fresh audible hardware run.
- The six CI Node suites: 49 passed.
- `python3 -m unittest gui.test_server gui.test_musical_workflow gui.test_audio_projects`: 52 passed. Initial sandbox runs could not bind localhost; rerunning with local-server access passed.
- `cargo fmt --check` and the four-case timeline reference check passed. `cargo build --locked --features vst3-live,au-offline` passed; no installed-plugin/runtime matrix or fresh Clippy run was performed.
- `examples/musical_workflow_demo.py`: exact persistence, checked failure/stale preservation and repeatable 32-second WAV passed, peak 18265/32767. Artifacts: `output/musical-workflow-nh6npkiv/`.
- `examples/audio_project_workflow_demo.py`: four-second mixed project, portable ZIP reopen in a fresh engine, byte-identical export and zero clipped frames passed. Artifacts: `output/audio-project-wp79gbyh/`. Native hardware mode was not requested.

Listening quality, loop/seek discontinuities, three-minute export, mixer, recording and note-driven runtime devices remain unverified or unimplemented as described in the active plan. Earlier muted hardware/browser evidence remains historical rather than being presented as repeated today.

## Parallel MVP delivery — 2026-10-03

Delivered gate 2a (basic mixer), 2b (direct arrangement/piano-roll gestures) and 2c (three-minute built-in/PCM export) with three Sol 6.1 medium agents and one integration owner. The active plan now puts filter/EQ and delay next, followed by automation/fades. Gate 2 as a whole remains open; programmable-device sequencing remains gate 3.

- Schema 10 adds persisted track gain/pan/mute/solo for built-in instruments, PCM and gain effects. Native/export share mixing behavior; callback meters are bounded and allocation-free. Existing schemas remain supported. GUI edits use checked replacement and undo; stopped structural editing remains the policy.
- Clips support drag/move and edge resize. The piano roll supports note draw, move/pitch and gate resize, with one checked edit per completed gesture, cancellation and numeric fallbacks.
- Built-in/PCM offline export supports 180 seconds using bounded blocks and buffered WAV writes. Native finite playback and prepared runtime exports retain 60-second limits; plugins retain 10 seconds. Capability-driven GUI limits expose these distinctions.

Verification at the integrated boundary:

- `cargo test --locked`: 172 passed; `cargo test --locked --features native-audio`: 184 passed.
- All CI Node suites: 68 passed. HTTP bridge suites: 55 passed.
- Rust formatting, all-feature Clippy with warnings denied, and diff whitespace checks passed.
- `examples/song_workflow_demo.py`: a real 180-second, 48 kHz, four-track, 270-clip project with owned PCM reopened its portable ZIP in a fresh engine and exported byte-identical WAVs. 8,640,000 frames, zero clipping, audible samples in the final second; debug exports took 7.369 and 7.193 seconds. Evidence: `output/song-workflow-utkufdkg/evidence.json`.
- `examples/mixer_native_demo.py`: muted hardware checks passed for solo/hard pan, meters, pause/resume, mute, stop and EOF stream cleanup. This does not establish acoustic quality or listening latency.
- Real-browser checks passed for mixer edits, muted native meters, clip dragging, note drawing/moving/resizing, Delete/Undo and the 180-second export control. Independent review prompted fixes for unsaved mixer numeric drafts and click suppression/focus after tiny gestures. Screenshot: `output/mvp-sequencer-mixer.jpg`.

The requested external Opus 5.5 review is pending approval of the prepared payload/destination. Automatic approval review rejected transmission of the scoped roadmap/UI/mixer prompt to the external service; no external response was received or incorporated. See [review provenance](OPUS-MIXER-REVIEW.md). Independent local review and its fixes were completed. No installed-runtime matrix or acoustic listening pass is claimed. Preexisting dirty changes were preserved; this delivery remains uncommitted.

## Authorized Opus mixer review reconciliation — 2026-10-03

The user explicitly approved sending `output/opus-mixer-prompt.txt` to Opus 5.5 through pi/OpenCode. The scoped, tools-disabled, medium-thinking invocation of `opencode/claude-opus-5-5` succeeded. Its complete response and disposition are saved in [OPUS-MIXER-REVIEW.md](OPUS-MIXER-REVIEW.md), with the prompt hash. This resolves the earlier pending authorization; it does not revise the historical rejection record.

The prompt describes the pre-implementation mixer plan, so recommendations were checked against the already completed slices. Existing code already supplies transactional foreign/schema validation, shared prepared mixing, exact hard-pan silence, callback-safe meters and export clipped-frame feedback. Added focused checks for unrelated note/clip edits preserving mixer state through undo/redo/serialization and the real HTTP/portable ZIP path, plus step automation multiplying independent mixer gain. All 6 Rust mixer tests, 9 Node mixer tests and 3 HTTP mixer tests passed; formatting and diff whitespace checks passed. No production DSP/GUI code changed, and full suites were not repeated.

The active plan/UI checklist now record this review and meter/live-update follow-ups. Retained the implemented non-consuming latest-buffer meter and strict serialization contracts; alternative `swap(0)` meter/default-elision proposals were not adopted. Filter/simple EQ and delay remain next. No additional acoustic listening or installed-runtime verification is claimed.

## Useful processing and mixing console — 2026-10-03

Three Sol 6.1 medium-thinking agents delivered Rust DSP/schema, GUI effects and console strips in parallel; root handled project packaging, integration and acceptance. Existing Opus feedback was reused. No new external review was needed. Preexisting changes and the intentional AGENTS.md deletion were preserved.

- Schema 11 adds independent stereo one-pole lowpass and feedback delay, saved numeric controls/bypass, three GUI presets each, checked stopped edits and undo. Legacy schemas keep their contracts; foreign devices/effects reject in 11. Gain automation and mixer settings remain independent.
- Classic console strips now provide vertical gain faders, pan sliders, M/S, stereo track meters and a read-only master strip. Exact numeric edits remain available. Desktop/narrow checks at 1280/900/481 widths showed no document overflow. Actual browser controls exercised adding effects, presets/numeric edits, Apply, mixer edits, keyboard fader and Undo.
- Delay storage is preallocated and capped at 64 MiB. Review caught bulk delay clearing inside loop/seek callbacks; constant-time reset now invalidates old ring contents until overwrite. Regression checks cover stale feedback, one-frame loops, deterministic block splits, impulse spacing/stereo, filtering, bypass, serialization, callback/export parity and zero callback allocations.
- 180 portable Rust tests, 192 native-audio Rust tests, 76 JavaScript tests and 57 HTTP bridge/project tests passed. Format, portable and vst3-live/au-offline Clippy with warnings denied passed; the combined-feature binary was restored. Baseline protocol/demo and timeline checks passed.
- `python3 examples/processing_workflow_demo.py --song --native` exported a four-track/270-clip 180-second song with PCM, mixer, lowpass/delay and saved gain automation. A fresh engine reopened its ZIP and exported a byte-identical WAV, zero clipping, PCM peak 11975/32767 and final-second peak 11515/32767; debug exports took 8.907 and 9.020 seconds. Evidence: `output/processing-workflow-qvuw67px/evidence.json`; WAV SHA256 `e66f95497e06c5cf34943a73b85d9994e78cee81347e91cc09e5762d0f24074c`.
- Muted native loop/pause/seek/resume/Stop and resource release passed for the processed mixed fixture: 109568 submitted frames, max render 1.458 ms, zero callback overruns. This is callback evidence, not an acoustic listening pass.

Gate 2d is implemented. Next is 2e: GUI gain automation and audio clip fades. Live mixer edits, waveforms and wider polish remain follow-ups; gate 3 still requires a sequenced programmable device. Work remains in the working tree.

## Gain automation editing and clip fades — 2026-10-03

Three Sol 6.1 agents delivered the fade engine, fade timeline UI and gain automation editor in parallel; root integrated checked edits, draft/focus preservation, project acceptance and documentation. Existing dirty work and the intentional AGENTS.md deletion were preserved. No new runtime adapter or external review was added.

- Audio clips optionally save zero-default `fade_in_frames`/`fade_out_frames` in existing schemas 2–11. Linear, nonoverlapping fade regions must fit the clip. Zero keeps legacy output unchanged; a one-frame fade mutes the edge sample. Stateless timeline-relative envelopes multiply source audio before effects/mixer and use the same prepared engine for native/export. Callback allocation regressions exercise fades through seek/loop.
- Selected audio clips expose exact fade frame counts, unchanged-field patches and compact slope previews. Invalid counts or clip shortening reject without changing the project. Gain effects expose exact-frame add/update/delete for saved step automation, with explicit base/held-value semantics. Both use checked stopped edits and undo. Rebuilding track cards retains other automation drafts and local errors; successful edits restore focus.
- 186 portable Rust tests, 198 native-audio Rust tests, 91 JavaScript tests and 58 HTTP bridge/project tests passed. Rust formatting, portable and vst3-live/au-offline Clippy with warnings denied, JS syntax, timeline reference and diff whitespace checks passed. The combined-feature executable was restored for the preview. No installed foreign-plugin/runtime matrix was repeated.
- `python3 examples/finishing_workflow_demo.py --song --native` exported a four-track/270-clip 180-second song with built-in instruments, owned PCM, mixer, lowpass/delay, edited step gain automation and 240/960-frame fades. Fresh-engine portable ZIP reopen preserved the project and produced a byte-identical WAV, zero clipped frames, PCM peak 11019/32767 and final-second peak 8658/32767. Debug exports took 9.218 and 10.676 seconds. Evidence: `output/finishing-workflow-dcz827y6/evidence.json`; WAV SHA256 `eec67f0eab130a00cb896d9db9605fdf345f8ad49bfdb29108d7dd406c065f70`.
- Muted native loop/pause/seek/resume/Stop and resource release passed: 109056 submitted frames, maximum render 3.550 ms and zero callback overruns. This is callback evidence, not an acoustic listening pass.
- The actual browser loaded the portable ZIP, saved gain automation while retaining another typed row, restored focus, applied/undid a clip fade, and rejected oversized fades with persistent local feedback. At the current 481-pixel panel there was no document overflow. Screenshots: `output/mvp-finishing.jpg` and `output/mvp-automation.jpg`. The test preview remains on an applied project.

Gate 2e and the three-minute arrangement/export workflow are implemented. Quiet listening and musical feedback remain pending; gate 3 is next: one sequenced programmable musical device. Smooth ramps, live mixer updates and waveforms remain follow-ups. Changes remain uncommitted in the existing working tree.

## 2026-10-04 — Gate 3 sequenced Pd instrument

Delivered schema 12 with one embedded vanilla Pd Filtered Sine preset, typed cutoff metadata and sequenced frequency/velocity/gate input. Its saved device package participates in the browser piano roll, checked stopped gain/cutoff edits, undo, mixer, gain automation, lowpass/delay, PCM/audio fades and portable ZIP. Real libpd DSP owns private instances and feeds native playback through a fixed worker ring; export schedules notes directly with no frozen PCM substitute.

Acceptance: direct receiver/pitch/gate/velocity/cutoff/reset proof; four real HTTP tests including independent edits/undo, invalid/stale transactional preservation and missing-runtime session/ZIP rejection; mixed five-track 180-second fresh-engine ZIP reopen with byte-identical unclipped WAV (8,640,000 frames, 34,560,044 bytes, final-second peak 12,813); muted until-stopped loop/seek/pause/resume/Stop with Pd lane signal, zero steady worker underruns and callback budget overruns. Transport wait buffers after queue invalidation are separate telemetry. Reproduce with `examples/pd_instrument_workflow_demo.py --song --native` and the configured multi-instance libpd library.

Final checks: 191 portable Rust tests and 204 native-audio Rust tests passed; 96 JavaScript tests and 62 HTTP/project tests passed. Five installed-libpd engine tests and the direct Python receiver proof passed. Portable and combined-feature Clippy (warnings denied), formatting, timeline contract and diff whitespace checks passed. Safari exercised preset creation, note velocity/cutoff edits, precise cutoff undo/redo, WAV import and portable ZIP import. Browser download permission was rejected by automatic approval review; the production HTTP ZIP save/fresh-reopen path passed independently. Changes remain uncommitted; existing unfinished Pd receiver-control work and the AGENTS.md deletion were preserved.

Limits: exact preset only, 48 kHz, nonoverlapping monophonic gates of at least 64 frames, next-tick event timing, no held-note chasing, brief silence during transport rebuffering, approximate consumed-block Pd peaks, stopped control edits. Listening/composition feedback, arbitrary patches/externals, envelopes, polyphony and sustained latency/clock drift remain follow-ups.

Safari UI acceptance verified Pd addition, clip/note editing, exact cutoff 1234.56789 with undo/redo, velocity 0.723456789, WAV export and mixed ZIP import across a fresh server restart. Muted native GUI Play advanced the timeline at 48 kHz and enforced stopped control editing. The valid mixed project remains in the native preview at port 55384; screenshot is `output/mvp-pd-instrument.jpg`. Browser ZIP download permission was rejected by automatic approval review and canceled; scripted real HTTP ZIP save/reopen acceptance passed.


## 2026-10-04 — Keyboard/Web MIDI preview and stopped step entry

Delivered the next small play/capture slice through three GPT-6.1 Sol agents at medium thinking, with root integration of the app and acceptance checks. Preserved the pre-existing dirty tree and deleted AGENTS.md. No new saved schema or tempo-retiming contract was introduced.

- Actual-engine `note.preview` and authenticated `/api/note/preview` render a 0.25-second note gate with a 0.5-second clip/output limit. Saved instrument/processing, frame-zero automation and mixer gain/pan remain; mute/solo are ignored for audition. Project/revision/assets remain unchanged. Native playing/paused transport rejects previews; actual libpd signal and missing-runtime failure preservation were verified.
- Opt-in computer keyboard and Web MIDI use bounded WAV playback/cache, resume from gestures and cancel stale work on disable, blur, visibility/target/project changes and Stop. MIDI access is explicit with no sysex request. Fixed gates and one-shot drum mappings are clear; held-note/sustain and physical hardware input remain follow-ups.
- Dedicated stopped step cursor/gate fields round absolute tick endpoints and accept one checked insertion/undo entry at a time. Rejection never advances or wraps. Pending previews cannot redirect notes after selection/cursor changes or capture an audition armed later. Rapid concurrent entry reports the need to wait and press again.

Acceptance: 114 Node tests pass, including actual root capture-function and asynchronous cursor/arming regressions. Portable and native Rust suites pass; preview tests assert actual synth release and full kick tail after the initial clip-boundary truncation was fixed. Portable/native Clippy with warnings denied, formatting, timeline-reference and whitespace checks pass. The 68-test HTTP run passed all behavior except the old exact capability-object assertion; after adding the new capability to that assertion, all 44 tests in the affected server/preview/workflow suites pass, including installed Pd, runtime disappearance and muted native playing/paused rejection. Unaffected HTTP suites had already passed in that same full run.

The final `output/note-input-workflow-0ubqe1uq` fixture captures eight notes with imported PCM preserved, checked snapshot undo/redo and stale rejection, saves its portable ZIP, then reopens in a fresh engine and exports byte-identical unclipped four-second audio: 192000 stereo frames, PCM peak 12073, SHA-256 `d41a56eb60c9eee4ed1e06be64262ad37c52bd52f3d1781921fff73e6fd9fbe2`. Each actual instrument preview leaves session inspection and offline export unchanged.

In-app browser acceptance on the final server verifies C3 preview, A/D entries at beats 0/0.25, advance to 0.5, one-note undo/redo and Stop disabling input. Listening level was 0.01; no acoustic quality claim is made. The 481-pixel layout stays contained. Screenshot: `output/mvp-note-input.jpg`; preview server port 51667 retains the demonstration, separate from earlier servers. Physical MIDI hardware, measured input latency, count-in/metronome and real-time recording are not yet verified/implemented. Roadmap next action advances to transport-aligned metronome/count-in followed by real-time MIDI record/overdub.


## 2026-10-04 — Native count-in and held-gate overdub

Delivered the next MVP slice with parallel Sol 6.1 medium agents and root integration, preserving the existing dirty tree. Native built-in prepared playback has a 4/4 accented listening-only metronome and 0/1/2-bar count-in; exports remain unchanged. Keyboard/Web MIDI held gates overdub one selected clip from timeline zero. Count-in notes are ignored, Stop closes held gates, and new gates crossing the clip boundary are shortened with explicit feedback. Loop/seek/pause are disabled during recording. There is no live input monitoring; timing estimates use sampled native status plus the browser clock, and physical MIDI/input latency remain unverified.

Takes buffer independently of saved state, preserve PCM/mixer/effects/off-grid microtonal notes, and apply as one undoable checked replacement. Rejected validation/stale requests retain the take for retry/discard. Browser testing exposed a response-envelope mismatch in the existing session route; the dedicated atomic `/api/note/take` endpoint commits and returns session inspection with its new revision under the project lock. Actual app-handler tests assert this endpoint, start/reset safety, fixed target, Stop ordering and single history entry. The real HTTP recording acceptance verifies stale rejection, whole-take undo/redo, portable ZIP fresh-engine reopen and byte-identical unclipped export. Stopped preview and step entry remain available.

Verification at this update: native Rust suite 215 passed; Node suite 127 passed; HTTP suite ran 69 tests, with 63 passing and 6 optional skips. Final bridge/recording checks passed all 39 tests after the atomic endpoint correction. Eight final metronome checks also pass, including early runtime-source rejection; finite playback retains its prior paused-time wall deadline with the count-in duration added. Muted native count-in/pause/seek/stop reported zero callback overruns. Final in-app browser acceptance verifies a one-bar count-in at listening volume zero, three recorded keyboard notes, successful Stop/application, whole-take Undo removing all notes and Redo restoring all three. Screenshot: `output/mvp-note-recording.png`. No acoustic quality is claimed. Next action advances to quantize/MIDI-file import/export while collecting quiet composition feedback.


## 2026-10-05 — Commit and push the accumulated MVP

The user requested a Git checkpoint and push. Isolated legacy live Pd receiver-control changes in a WIP commit, including their worker handoff, Rust acknowledgments, opt-in tests and example. That preserves T12b5 progress without claiming completed native acceptance or GUI parity. The arrangement MVP and its documentation, regression tests and expanded CI are committed separately. The existing deletion of `AGENTS.md` is included; generated output, local runtimes, audio, screenshots and build artifacts remain ignored.

Before publication, formatting, default/native Clippy with warnings denied, default/native Rust suites, 127 Node tests and four timeline-contract fixtures passed. The 69-test HTTP/workflow run passed with seven optional runtime/native skips while the portable binary was selected. Legacy Pd receiver-control opt-in native tests remain unverified for this checkpoint. Previously recorded muted playback and portable save/reopen/export evidence remains in the slice histories above.

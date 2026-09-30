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

### T09d: native effect ownership and callback integration

**Depends on:** T09c. **Owner:** stronger model, Luna bounded fixtures/docs.

Review the Rust realtime runner and a thin C++ shim against DAW-owned buffers and lifecycle. Choose callback ownership and playback isolation explicitly, prepare instances off callback, and return destruction to the owner thread. Measure allocation, locks, automation/reset behavior, latency handling, and callback bounds before enabling live plugin playback. Acceptance: known effect plays through the DAW native path with saved state and bounded automation; stop/replacement reliably release resources off callback. Hardware/acoustic checks are separate from callback evidence. Editors and instruments remain separate slices.

### T10: Audio Unit host proof of concept

**Depends on:** T09 host contract. **Owner:** stronger model, Luna harness/docs. **Files:** `native/`, `src/hosting/`, macOS integration tests.

Start with desktop Audio Units: discovery, instantiation, formats, resources, cached render blocks, parameters/state, teardown. Contain Objective-C and window lifecycle in native code. Acceptance: known Apple AU renders and restores state; portable builds still work; unsupported units fail clearly. No AUv3 extensions.

### T11: SuperCollider jobs and control

**Depends on:** S0 for offline spike; T08b for track integration. **Owner:** Luna process harness, stronger-model audio-routing review. **Files:** proposed `src/runtimes/supercollider.rs`, runtime tests, example score.

First launch a configured `scsynth` with a prepared NRT score, fresh WAV path, argument arrays, timeout, and captured errors. Then add owned child-process OSC with completion handling. Never use a shell or terminate unrelated servers. Acceptance: installed engine renders a fixture, missing executable/timeout errors work, interactive create/free is acknowledged. Live audio transport needs separate measured design; OSC success alone is not track integration.

### T12: Csound and Pure Data adapters

**Depends on:** T11 job conventions; T08b for embedded devices. **Owner:** Luna offline harnesses, stronger model FFI. **Files:** proposed `src/runtimes/csound.rs`, `src/runtimes/pd.rs`, separate tests.

Use separate commits. Start Csound with CLI rendering, then assess libcsound blocks. Spike libpd with one patch, explicit search paths, controlled externals, block sizes, and thread ownership. Acceptance for each: known audio fixture, state/parameters, missing-runtime errors, cleanup, capability flag, and preserved dependency notices. Do not claim arbitrary externals/opcodes work. No general sandbox.

### T13: agent jobs and thin UI

**Depends on:** T02, T07b, T08b, one native plugin, one runtime device. **Owner:** separate Luna tasks after interface review.

First add cancellable render jobs with progress and subscriptions. Then build minimal track/clip/device/transport/meter views through the same commands. Optional MCP translates discovery, inspect, edit, and jobs without another session model. Acceptance: Python creates/edits/saves/reloads/renders the arrangement shown by the UI, failed edits preserve state, and cancellation cleans output. Defer a visual patch editor and elaborate mixer.

## Next starting point

T09c is complete. Continue with T09d native callback ownership and integration review. Scripted offline VST3 processing now works; live plugin capability remains false and the browser has no plugin controls. Preserve the tested transactional state, process containment, and unclipped effect routing. Browser clip/effect editing remains a separate UI slice.

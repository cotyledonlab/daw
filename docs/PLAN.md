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

# DAW roadmap

Updated 2026-10-07 after the Opus UI consistency review and plan cleanup. This file contains outstanding work and its execution order. [UI-PLAN.md](UI-PLAN.md) defines the immediate UI passes; [PROTOCOL.md](PROTOCOL.md) defines existing contracts. Completed records live in [PLAN-HISTORY.md](archive/PLAN-HISTORY.md). The previous [roadmap](archive/PLAN-2026-10-04.md) and [UI checklist](archive/UI-PLAN-2026-10-04.md) are archived intact.

## Destination and baseline

Build a macOS-first arrangement DAW with dependable instruments/effects and drop-in programmable devices. Active scope is improving the existing arrangement workflow and then quantize/MIDI-file handling. Existing runtime/plugin paths are retained for compatibility; their presence does not authorize expansion. Ableton-style parity and broader studio workflows are historical direction, not current acceptance requirements.

The three implementation gates are delivered: a musical drum/bass/lead loop; a portable three-minute built-in/PCM song with mixer, processing, automation and fades; and one sequenced Pd instrument. Keyboard/Web MIDI preview, stopped step entry, native metronome/count-in and held-gate overdub are also delivered. These are the baseline, not active tickets. Quiet listening, physical MIDI and sustained latency validation remain open; automated and muted native evidence does not establish acoustic quality.

## Active delivery order

### 1. Restore UI consistency

Address the [Opus consistency review and source qualifications](OPUS-UI-CONSISTENCY-REVIEW.md) through the focused passes in [UI-PLAN.md](UI-PLAN.md). The review is complete; its recommended fixes are not implemented.

1. Consolidate CSS and control typography/geometry; fix undefined tokens and inconsistent statuses without changing behavior.
2. Clarify gain stages, musical units, device names and selection/state colors while preserving exact stored values.
3. Regroup transport, step-entry, history and mixer actions; verify stopped and playback/recording layouts.
4. Explain draft versus applied edits and file persistence. Reconcile mixer Apply scope and track-deletion behavior in a separate checked handler pass.

Acceptance: coherent desktop/narrow layouts, keyboard focus and visible disabled reasons; unchanged frames/Hz and untouched gain/pan values; retained drafts and rejected takes; checked undo and portable save/reopen/export where behavior changes. Preserve readable canvas geometry and validate pointer mapping before changing SVG sizing. No full inspector or framework migration is required.

### 2. Quantize and MIDI-file import/export

Continue the small composition/recording slice after the consistency pass. Define supported MIDI events, tempo-map handling, note pairing and clip placement before implementation. Make unsupported data and intentional timing conversion explicit; do not silently retime existing frame-based projects.

Acceptance: quantize one selected phrase with checked undo; import/export a supported MIDI phrase with explicit pitch/gate/velocity/timing behavior; preserve unrelated notes, PCM, mixer and effects; reject malformed/unsupported inputs without partial changes. Retain exact persistence and fresh-engine portable reopen/export checks. Sustain, input-device selection and physical MIDI timing remain separate follow-ups unless needed by this fixture.

## Deferred possibilities — outside active scope

| Order | Outstanding workflow | Initial scope |
| --- | --- | --- |
| 3 | Sampled instruments | Single-sample pitched instrument and user-sample drum pads using prepared PCM; basic envelopes/filter/presets. |
| 4 | Audio recording | Input selection, arm/monitor/record, measured latency and portable takes; comping later. |
| 5 | Clip/scenes performance | Quantized launch/stop/scenes and arrangement capture, reusing the clip scheduler and session model. |
| 6 | Tempo-aware audio | Import resampling first; then beat-based audio, tempo following/stretch/warp, groove and reverse. Sample-rate conversion and stretching are separate slices. |
| 7 | Studio expansion | Sends/returns/routing, freeze/resample, broader parameter automation, reliable VST3 discovery/state/editor/instruments and latency compensation. Broaden AU for a concrete musical need. |

This table preserves possible later directions, not a delivery commitment or permission to implement them. Reopen a deferred slice only for a concrete user request or a demonstrated dependency of the active work.

## Open validation and follow-ups

- **Listening/composition:** quietly build a phrase from scratch, balance the mixed song and audition Pd gates/seek/loop boundaries. Turn observed clicks, silence or editing friction into small fixes.
- **Input/transport:** physical Web MIDI, measured input latency, sustain/input-device selection and long-run runtime clock/latency behavior remain unverified or unimplemented. The final keyboard-recording browser check is completed in history; do not reopen it as unfinished delivery.
- **Editing/audio polish:** multiselect/zoom, velocity lanes, waveforms, unsaved-work recovery, smooth automation ramps, other parameter lanes, live mixer edits/smoothing and peak hold. Prioritize demonstrated workflow or data-loss problems.
- **Programmable devices:** Pd polyphony/envelopes, arbitrary patches/externals, live controls, programmable effects and a second sequenced runtime. Preserve the current 48 kHz monophonic preset limits until explicitly expanded.
- **Separate WIP:** legacy continuous Pd receiver controls are checkpointed in `878f711`; native acceptance and contract reconciliation remain unfinished. They are separate from the delivered schema-12 note instrument.

## Working rules

- Measure progress by music someone can make, reopen and export. Commit completed slices at their acceptance boundary. Preserve unrelated working-tree changes.
- Reuse Rust, JSONL, the browser bridge, checked replacement, polling and bounded snapshot undo. Keep one session model and existing capability helpers; no speculative abstraction or format migration.
- Preserve untouched frames, Hz, gain/pan and drafts. Tempo currently changes the authoring grid without moving saved frames. Distinguish applied engine state from downloaded project files.
- Keep structural edits stopped, failures transactional and publication exclusive. No allocation, blocking locks, I/O, process startup or foreign initialization in audio callbacks.
- Verify relevant formatting/lint/tests and save/reopen/export at behavioral slice boundaries. Use installed-runtime/FFI/native checks when those paths change; do not rerun unrelated adapter matrices for documentation or CSS-only edits.
- Freeze new host formats, standalone probes, AU breadth, subscriptions/MCP and broad refactors unless they fix a regression or block the active musical slice.
- When parallel work is explicitly requested, use distinct file owners and one integration owner, particularly for `gui/app.js`. Close work through one shared musical fixture.

**Next action:** UI pass A from [UI-PLAN.md](UI-PLAN.md), collecting quiet listening/composition feedback alongside it. Complete the remaining consistency passes before quantize/MIDI-file work.

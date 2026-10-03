# Opus 5.5 roadmap review

Received 2026-10-03 through pi using the OpenCode provider, model `claude-opus-5-5`, medium thinking. The reviewer received the original revised roadmap and a scoped implementation summary, with tools disabled. M1 implementation completed while that review was being arranged; references to future M1 work should be read in that context. This is design feedback, not an implementation or hardware verification report. Accepted changes and deliberate differences are recorded in [PLAN.md](PLAN.md).

# Review: macOS Rust DAW roadmap

**Overall:** The roadmap is pointed the right way. It puts product first, reuses the engine, defers adapters and preserves the Pd work. My changes below are about sequencing and correctness traps, not direction.

## Must-fix correctness

**1. Make frames authoritative and never re-quantize untouched data.**
Ticks are only an authoring view, and they will not always map to whole frames:
- At 48 kHz and 120 BPM, one tick is exactly 25 frames.
- At 44.1 kHz, one tick is 22.96875 frames.

Rules for the editor helpers:
- Compute every edit from absolute tick positions, rounding to frames once at the end. Never accumulate frame deltas.
- Snap by converting frames to the nearest tick, then snapping in ticks.
- Notes that were not edited keep their exact saved frames, even if they sit off-grid after a tempo change.
- Test: load, save with no edit, and get a byte- or field-identical file.

**2. Pin down the MIDI↔Hz policy.**
Display MIDI pitch, but:
- Write Hz with a single canonical formula (A4 = 440 Hz, 12-TET).
- Treat a saved Hz value as a MIDI note only if it round-trips within a tight tolerance.
- Show non-12-TET pitches from scripts as off-grid. Preserve them unless the user moves the note.

Without this, a load/save pass can silently retune scripted sessions.

**3. Define clip/note semantics before building the piano roll.**
- Notes are clip-relative.
- Duplicate makes an independent deep copy. No linked clips in the MVP.
- Shortening a clip hides notes past the end rather than deleting them, if the schema can represent that. Otherwise say plainly that resizing deletes notes, and make it undoable.
- Overlapping notes on the same pitch are either rejected or allowed explicitly. Pick one and test it.

These choices are hard to change after users have saved files.

**4. Move "looping does not expire on wall-clock time" into M1.**
- A 16-bar phrase at 120 BPM lasts 32 seconds.
- With a 60-second wall-time limit, looped playback dies after about two passes.

For built-in sine sessions, bound the run by resources or session length, not elapsed time. This is a narrow change to an existing limit, not a new engine, and it is the difference between a demo and something usable. Keep the 10-second limit for runtime sources as it is.

## Simplify scope and sequencing

**5. Narrow schema support to one editor model with load adapters.**
"v2/v3 and equivalent v4 shapes" invites per-view version checks. Instead:
- Adapt each supported version into one in-memory arrangement model.
- Write back either in the original version or through one explicit upgrade. Choose one policy.
- Show everything else read-only, with the reason.

If v2/v3 add cost, start with the newest note-capable schema only. The arpeggio fixture can be migrated once.

**6. Keep stopped-only editing, but make it feel automatic.**
Hard-disabling edits during playback feels broken. Instead, an edit made during playback should:
1. Stop.
2. Apply the checked replacement.
3. Optionally restart from the previous position or loop start.

This keeps the no-live-mutation contract and gives you most of the "loop while editing" feel. Treat the restart as polish; the stop-then-apply behavior is the M1b requirement.

**7. Use whole-session snapshot undo and one scripted end-to-end gate.**
- Undo/redo can store full session JSON for each successful revision, with a bounded depth. Undo is just a checked replacement. Avoid operation-based undo for now.
- Collapse each milestone gate into one automated bridge script (create, edit, invalid edit, save, reload, export, compare notes and frames). Add one manual listening and demo pass on top.

This replaces many narrow acceptance tests with one that proves the actual workflow.

## Timeline and piano-roll UI risks

- **One coordinate transform.** Use a single tick↔pixel function plus scroll and zoom for both the ruler and the piano roll. Bugs from two transforms disagreeing are the most common timeline failure.
- **Separate playhead rendering.** Keep the playhead on its own layer, interpolated with `requestAnimationFrame` between status polls. Never re-render the whole SVG on each poll. SVG is fine at MVP scale (hundreds of notes) if you re-render per lane or clip on edit only.
- **Hit-testing.** Short notes need a minimum pixel width and resize handles that stay grabbable. Default snap to the grid, with Alt (or another modifier) to bypass it.
- **Velocity.** Edit it in an inspector field or by drag first. A velocity lane is polish.
- **Errors.** A rejected edit must restore the view from the authoritative engine state, not from optimistic local state.

## What can wait

Waveforms, velocity lanes, multi-select marquee, tempo maps, clip launching, MIDI input, render subscriptions, progress/cancel (until export exceeds about 60 seconds), and further adapter or receiver controls.

## Move sampler/drums ahead of project packaging (swap M2 and M3, partly)

**8. Make M2 a basic drum sampler plus a simple synth; move WAV import and ZIP packaging to M3.**

- **Musical value.** A two-track sine phrase is not music. A beat is the fastest step toward something people will actually make. Drums plus one non-sine voice (saw or square with a filter and envelope) is the smallest set that feels like a DAW.
- **It avoids the packaging problem.** A bundled, licensed kit is app-owned, so sessions can reference sample IDs instead of user files. There is no upload, path validation, traversal checking or portable-asset round-trip yet. Packaging is mostly plumbing with security surface and little musical payoff.
- **It reuses what exists.** One-shot sample playback triggered by notes reuses the PCM clip machinery, so the engine risk is lower than it looks.
- **It sharpens the device schema.** Building a concrete sampler first gives M3's audio-import work a real sample-device contract to target, instead of designing the asset format in the abstract.

Keep the schema change minimal: one saved device type with explicit migration and old-file coverage, as the roadmap already requires. User audio import and the project ZIP then follow, once there is an arrangement worth carrying around.

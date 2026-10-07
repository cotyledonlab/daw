# MVP checklist

Updated 2026-10-07. This is the only plan. Ship a small macOS arrangement DAW in which someone can make a phrase, record notes, mix a song, save/reopen it and export WAV. The implementation is delivered; close the remaining usability and listening checks before expanding features.

## Completed

- [x] Rust session model, JSONL discovery and revision-checked transactional edits; deterministic stereo WAV rendering.
- [x] Local browser editor and macOS native Play/Pause/Stop, seek/loop and until-stopped arrangement playback.
- [x] Musical loop: 16-bar drum/bass/lead demo, new arrangements, note tracks/clips and precise piano-roll edits.
- [x] Independent clip copies, drag/draw/move/resize, keyboard delete/duplicate and checked Undo/Redo.
- [x] PCM WAV import, audio placement/trim/copy and portable ZIP save/load with owned assets.
- [x] Saved mixer level/pan/mute/solo and native track/master peak meters.
- [x] Three-minute built-in/PCM exports; fresh-engine reopen with byte-identical unclipped WAVs.
- [x] Lowpass/delay, bypass and presets; step gain automation and exact audio clip fades.
- [x] One sequenced Pd Filtered Sine preset with note/control editing, mixer/effects, portable reopen/export and muted native transport acceptance.
- [x] Computer-keyboard/Web MIDI preview and stopped step entry.
- [x] Native metronome/count-in and held-gate note overdub; atomic take application, retry/discard and whole-take Undo/Redo.
- [x] Browser recording acceptance, including Stop/application and whole-take Undo/Redo (2026-10-04).
- [x] UI consistency review and CSS/control geometry pass, including narrow layouts and ruler labels (2026-10-07, `afbd2b7`).

These checks record delivered work and prior acceptance, not new verification performed during this documentation cleanup. Reproducible evidence lives in `tests/`, `gui/test_*` and the workflow demos in `examples/`.

## Remaining before calling the MVP accepted

1. [ ] Clarify editing/persistence in the existing UI: distinguish applied edits from device/effect drafts and downloaded files; explain Undo disabled by drafts; make mixer Apply wording match its all-strip numeric-draft scope. Keep this a small fix and preserve typed drafts/exact values.
2. [ ] Quietly use the app end to end: create a phrase, preview/record notes, Undo/Redo a take, import WAV, balance/process/fade the mix, save ZIP, reopen in a fresh server and listen to the exported WAV. Audition Pd gates/seek/loop when libpd is configured. Check clicks, silence and confusing or failed edits; fix observed blockers with focused regressions.

Existing automated export/reopen and muted native checks are complete. Listening/composition feedback remains open; physical MIDI, measured input latency and sustained runtime clock/latency tests remain unverified and are follow-ups, not new MVP implementation gates.

## After MVP

Quantize/MIDI-file import/export can be the next feature slice when requested. Broader UI regrouping/labels, multiselect/zoom/waveforms, sampled instruments, audio recording, scenes, tempo-aware audio and hosting/routing expansion are deferred. None should delay acceptance of the existing arrangement workflow.

Legacy continuous Pd live receiver controls are separate unfinished work (`878f711`), not the delivered sequenced preset. Keep their native acceptance/contract reconciliation open without expanding the MVP.

**Next action:** the small editing/persistence clarity fix, then the listening workflow. For engineering and PR review/merge rules, use [AGENTS.md](../AGENTS.md); for current contracts, use [PROTOCOL.md](PROTOCOL.md).

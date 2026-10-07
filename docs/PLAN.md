# MVP checklist

Updated 2026-10-07. This is the only plan. Ship a small macOS arrangement DAW in which someone can make a phrase, record notes, mix a song, save/reopen it and export WAV. The arrangement implementation is delivered. The user also requested integrated studio prompt control; keep acoustic listening acceptance distinct from the delivered implementation.

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
- [x] Repository cleanup: remove obsolete local test/demo outputs, review dumps and caches; preserve current contracts, regression fixtures, optional runtime dependencies and requested studio-tour projects.

These checks record delivered work and prior acceptance, not new verification performed during this documentation cleanup. Reproducible evidence lives in `tests/`, `gui/test_*` and the workflow demos in `examples/`.

## Remaining before calling the MVP accepted

1. [x] Clarify editing/persistence in the existing UI: distinguish applied edits from device/effect drafts and downloaded files; explain Undo disabled by drafts; make mixer Apply wording match its all-strip numeric-draft scope. Labels now distinguish in-memory edits from downloads, explain draft-blocked Undo and all-strip mixer Apply. Narrow browser layout and existing exact-value regressions checked.
2. [ ] Quietly use the app end to end: create a phrase, preview/record notes, Undo/Redo a take, import WAV, balance/process/fade the mix, save ZIP, reopen in a fresh server and listen to the exported WAV. Audition Pd gates/seek/loop when libpd is configured. Check clicks, silence and confusing or failed edits; fix observed blockers with focused regressions.

Browser validation feedback now appears beside the note/audio fields (reproduce with a note ending beyond its clip), and the explicit clip picker reaches overlapping copies without moving saved frames. Focused timeline regressions verify both. The earlier Studio HTTP 403 was corrected by the provider HTTP client; real producer composition and producer-to-engineer relative mixing passed.

Existing automated export/reopen and muted native checks are complete. Listening/composition feedback remains open; physical MIDI, measured input latency and sustained runtime clock/latency tests remain unverified and are follow-ups, not new MVP implementation gates.

## Requested integrated studio

- [x] Add producer, engineer and musician prompt control through OpenCode Zen using server-side `OPENCODE_API_KEY`, relative edits and selected-track/clip scope. Producer delegates bounded specialist calls; edits reuse editor validation, checked replacement and one bounded Undo entry. Regression checks cover role/scope, stale requests, typing/playback/pending takes, rejected batches and responsive controls. A real producer created a four-note phrase in the browser; whole-batch Undo/Redo was verified.
- [x] Add optional ElevenLabs push-to-talk transcription and spoken replies through server-side `ELEVEN_API_KEY`; review transcripts before sending. Text control works independently. Provider/bridge failure tests pass.
- [ ] Verify live voice with a valid credential: the configured key returns HTTP 401 on `POST /v1/text-to-speech`; no successful speech/transcription or physical microphone acceptance is claimed.

The finishing and note-recording workflow demos passed ZIP/fresh-server reopen and byte-identical unclipped export; the finishing demo also passed muted native callbacks. This does not close acoustic listening acceptance. Agents currently inspect session data, not audio; runtime program/plugin-hosting expansion remains outside this slice.

## After MVP

Quantize/MIDI-file import/export can be the next feature slice when requested. Broader UI regrouping/labels, multiselect/zoom/waveforms, sampled instruments, audio recording, scenes, tempo-aware audio and hosting/routing expansion are deferred. None should delay acceptance of the existing arrangement workflow.

Legacy continuous Pd live receiver controls are separate unfinished work (`878f711`), not the delivered sequenced preset. Keep their native acceptance/contract reconciliation open without expanding the MVP.

**Next action:** listen quietly to the reopened finishing-workflow WAV and report any clicks, silence or mix/editing problems; retry the voice smoke check after a valid ElevenLabs key is configured. For engineering and PR review/merge rules, use [AGENTS.md](../AGENTS.md); for current contracts, use [PROTOCOL.md](PROTOCOL.md).

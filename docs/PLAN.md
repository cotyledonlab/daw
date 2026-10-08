# MVP checklist

Updated 2026-10-08. This is the only plan. Ship a small macOS arrangement DAW in which someone can make a phrase, record notes, mix a song, save/reopen it and export WAV. The arrangement implementation is delivered. The user also requested integrated studio prompt control; keep acoustic listening acceptance distinct from the delivered implementation.

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

The requested UX fixes are delivered: note/audio validation appears beside the fields; overlapping clips have visible markers and remain reachable through Selected clip; Studio agents and metronome/count-in controls collapse; project replacement clears obsolete input messages while retaining rejected takes. Browser checks verified desktop/narrow layouts, inline rejected-note feedback and covered-clip selection; focused regressions verify message reset and take preservation. The browser replacement-confirmation check was blocked by the in-app browser tooling, so it is not claimed as passed. The earlier Studio HTTP 403 was corrected by the provider HTTP client; real producer composition and producer-to-engineer relative mixing passed.

Existing automated export/reopen and muted native checks are complete. Listening/composition feedback remains open; physical MIDI, measured input latency and sustained runtime clock/latency tests remain unverified and are follow-ups, not new MVP implementation gates.

## Requested integrated studio

- [x] Add producer, engineer and musician prompt control through OpenCode Zen using server-side `OPENCODE_API_KEY`, relative edits and selected-track/clip scope. Producer delegates bounded specialist calls; edits reuse editor validation, checked replacement and one bounded Undo entry. Regression checks cover role/scope, stale requests, typing/playback/pending takes, rejected batches and responsive controls. A real producer created a four-note phrase in the browser; whole-batch Undo/Redo was verified.
- [x] Add optional ElevenLabs push-to-talk transcription and spoken replies through server-side `ELEVEN_API_KEY`; review transcripts before sending. Text control works independently. Provider/bridge failure tests pass.
- [x] Prefer the current vetted Zen stealth candidate: refresh model availability per prompt; prefer Space Bunny, then Big Pickle, with GLM fallback when neither is listed and an explicit model override. Keep one model across producer/specialists; catalog failures retain the last selection. Focused catalog/override/delegation regressions passed; a live Space Bunny response passed the Studio operation contract.
- [ ] Complete live voice acceptance: the studio-tour credential now passes live ElevenLabs speech/transcription roundtrip and the integrated Studio speech endpoint. Physical browser microphone acceptance remains open.

The finishing and note-recording workflow demos passed ZIP/fresh-server reopen and byte-identical unclipped export; the finishing demo also passed muted native callbacks. This does not close acoustic listening acceptance. Agents currently inspect session data, not audio; runtime program/plugin-hosting expansion remains outside this slice.

## Requested live arrangement editing

- [x] Keep built-in native playback running while applying mixer, sound/effect, automation, note and clip edits. Prepared worker updates retain untouched voice/effect state; stale/unsupported/queue-full failures preserve the applied project. Focused DSP/callback/bridge/editor regressions and the browser passed live note/clip/mixer edits, Undo and a real engineer prompt. The muted live-editing workflow passed snapshot Undo/Redo, pause/resume, rejected updates and ZIP/fresh-server byte-identical unclipped exports with no callback budget overruns or steady worker underruns. Track/device-kind/rate/runtime replacement remains stopped; acoustic listening remains open.

## Requested visual polish

- [x] Refresh the editor with an early Ableton-inspired light gray workspace, flat panels, restrained orange accents, consistent range handles and horizontal mixer fader caps. Keep transport prominent, move Studio agents below the editing workspace, tuck persistence guidance into Editing & saving, and let the timeline fill wide screens while keeping narrow scrolling local. Browser inspection passed at 320, 390, 768 and 1440 pixels, including clip selection, piano roll, expanded help/Studio controls and keyboard fader edits; focused mixer/timeline/recording regressions passed. Acoustic and physical-input acceptance remain open above.

- [x] Follow system light/dark appearance with native CSS preference detection, including controls, timeline, piano roll, statuses and mixer. Verified the current light preference in the browser and visually inspected the dark palette using an isolated static fixture; physical OS switching was not exercised.

## After MVP

Quantize/MIDI-file import/export can be the next feature slice when requested. Further UI regrouping/labels, multiselect/zoom/waveforms, sampled instruments, audio recording, scenes, tempo-aware audio and hosting/routing expansion are deferred. None should delay acceptance of the existing arrangement workflow.

Legacy continuous Pd live receiver controls are separate unfinished work (`878f711`), not the delivered sequenced preset. Keep their native acceptance/contract reconciliation open without expanding the MVP.

**Next action:** review the refreshed visual treatment in the browser, then listen quietly to the reopened live-editing WAV and audition note/clip/mix adjustments during playback for clicks or silence; verify browser push-to-talk with a physical microphone using the working ElevenLabs credential. For engineering and PR review/merge rules, use [AGENTS.md](../AGENTS.md); for current contracts, use [PROTOCOL.md](PROTOCOL.md).

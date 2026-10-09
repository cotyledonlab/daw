# MVP checklist

Updated 2026-10-09. This is the only plan. Ship a small macOS arrangement DAW in which someone can make a phrase, record notes, mix a song, save/reopen it and export WAV. The arrangement implementation is delivered. The user also requested integrated studio prompt control; keep acoustic listening acceptance distinct from the delivered implementation.

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

- [x] Add producer, engineer and musician prompt control through OpenCode Zen using server-side `OPENCODE_API_KEY`, relative edits and selected-track/clip scope. Producer splits a musical brief into bounded scoped specialist tasks; edits reuse editor validation, checked replacement and one bounded Undo entry. Regression checks cover role/scope, stale requests, typing/playback/pending takes, rejected batches and responsive controls. A real producer created a four-note phrase in the browser; whole-batch Undo/Redo was verified.
- [x] Add optional ElevenLabs push-to-talk transcription and spoken replies through server-side `ELEVEN_API_KEY`; review transcripts before sending. Text control works independently. Provider/bridge failure tests pass.
- [x] Refresh Zen model availability per prompt and preserve explicit model overrides. Current selection prefers inexpensive GLM Flash, then Space Bunny and Big Pickle when listed. Keep one model across producer/specialists; catalog failures retain the last selection. Focused catalog/override/delegation regressions passed; a live Space Bunny response passed the Studio operation contract.
- [ ] Complete live voice acceptance: the studio-tour credential now passes live ElevenLabs speech/transcription roundtrip and the integrated Studio speech endpoint. Physical browser microphone acceptance remains open.

The finishing and note-recording workflow demos passed ZIP/fresh-server reopen and byte-identical unclipped export; the finishing demo also passed muted native callbacks. This does not close acoustic listening acceptance. Agents currently inspect session data, not audio; runtime program/plugin-hosting expansion remains outside this slice.

## Requested private phone UI access

- [x] Add a loopback gateway for private Tailscale Serve access to the existing DAW UI without restarting or replacing its engine/session. Preserve token, revision, upload/download/CSP and Studio progress behavior; reject unrelated origins/hosts and invalid framing. Focused gateway, bridge and Studio regressions passed. WAV import and portable ZIP fresh-engine reopen through the gateway produced byte-identical WAV exports; required upload metadata is relayed with duplicate checks. Native playback remains on the Mac; viewing-device browser previews/downloads/voice retain existing behavior. See [setup](../README.md#private-phone-ui-through-tailscale).
- [ ] Confirm the private HTTPS URL opens from the user's phone. The live gateway returned the existing session at unchanged revision 4 and Serve is configured without Funnel. Same-Mac HTTPS attempts failed at DNS/connection before reaching the gateway; physical phone acceptance is not claimed.

## Requested studio progress and diagnostics

- [x] Show active role/model, elapsed time, producer delegation tasks and completed proposal summaries during inference; preserve visible proposals but apply nothing on specialist failure or a dropped stream. Add fixed HTTP/timeout/DNS/TLS/connection diagnostics with role/model context. Focused bridge and tracked JavaScript regressions passed. Live Space Bunny producer and two-specialist requests passed; the browser showed intermediate tasks, applied a 13-operation batch and passed whole-batch Undo/Redo. The original user failure was not reproduced outside the restricted test network; its cause remains unknown because the old error discarded the failure category.
- [x] Allow 120-second inference socket waits after the reported whole-session style prompt reproduced a musician timeout at 60 seconds on `examples/sessions/musical-demo.json` (producer reply at 42 seconds). Focused Studio/gateway tests passed, including timeout role/model diagnostics, no mutation, lock release and a JSON gateway wait covering the inference attempts (now ten with four tasks). A second live synthetic attempt hit the longer 120-second producer timeout; broad-request completion remains unverified. The live server was running stale Python; restart and portable restore verified exact session data and the existing phone gateway. With explicit user approval, the exact prompt on the restored user project reproduced a producer/Space Bunny timeout at 120 seconds through the live NDJSON endpoint (2026-10-09); session and revision remained unchanged. A short no-edit control on the same session and model succeeded in 6.1 seconds, also without mutation. This confirms the current broad-request timeout, not the category discarded by the original old-server error.
- [x] Complete internal producer task splitting: one short musical brief, up to four scoped specialist tasks, compact planning/mixing context and inexpensive GLM Flash inference, with one validated application/Undo entry. A real browser request using the reported broad style prompt on the public 16-bar demo completed four specialist tasks and applied 68 operations; all lead/drum clips changed, with more limited bass note changes. Originally identical patterns propagate with bounded `copyNotes`; different harmonies and earlier target edits reject. Focused regressions passed for scope isolation, truncation/correction, final-task rollback and one interaction. Whole-batch browser Undo/Redo restored exact states; portable ZIP fresh-engine reopen and byte-identical unclipped full-song exports passed. This verifies integration mechanics, not musical quality or reliable completion of every probabilistic request. Jev routing remains optional; its typed decisions cannot generate musical instructions.
- [x] Brainstorm Jev utilities without expanding implementation scope. [Zen’s Jev contract](https://docs.opencode.ai/docs/zen/#jev) uses typed yes/no, choice and score decisions through `/systemone`, rather than chat text. Candidate uses: route prompts to engineer/musician or both; detect ambiguous targets and request clarification; rank locally generated phrase variations against a brief; select compatible sound/effect presets; classify section energy from note/rhythm descriptors; flag possible intent mismatches before applying a plan. First candidate: benchmark routing against a labeled prompt set, with uncertain cases sent to the existing producer. Model probabilities need calibration and are not creative sampling probabilities or proof of musical quality. Engine validation, exact timing/math and acoustic checks stay deterministic or human. These are deferred experiments; no Jev integration or model-quality acceptance is claimed.

## Requested live arrangement editing

- [x] Keep built-in native playback running while applying mixer, sound/effect, automation, note and clip edits. Prepared worker updates retain untouched voice/effect state; stale/unsupported/queue-full failures preserve the applied project. Focused DSP/callback/bridge/editor regressions and the browser passed live note/clip/mixer edits, Undo and a real engineer prompt. The muted live-editing workflow passed snapshot Undo/Redo, pause/resume, rejected updates and ZIP/fresh-server byte-identical unclipped exports with no callback budget overruns or steady worker underruns. Track/device-kind/rate/runtime replacement remains stopped; acoustic listening remains open.

## Requested visual polish

- [x] Refresh the editor with an early Ableton-inspired light gray workspace, flat panels, restrained orange accents, consistent range handles and horizontal mixer fader caps. Keep transport prominent, move Studio agents below the editing workspace, tuck persistence guidance into Editing & saving, and let the timeline fill wide screens while keeping narrow scrolling local. Browser inspection passed at 320, 390, 768 and 1440 pixels, including clip selection, piano roll, expanded help/Studio controls and keyboard fader edits; focused mixer/timeline/recording regressions passed. Acoustic and physical-input acceptance remain open above.

- [x] Follow system light/dark appearance with native CSS preference detection, including controls, timeline, piano roll, statuses and mixer. Verified the current light preference in the browser and visually inspected the dark palette using an isolated static fixture; physical OS switching was not exercised.

## Requested selected-clip quantize

- [x] Quantize note starts in the selected clip to its current clip-relative grid as one checked, undoable edit. Preserve durations, Hz/velocity, clip placement, other clips and drafts; reject the entire edit if a gate no longer fits or validation fails. Focused regressions in `gui/test_quantize.cjs` and `gui/test_timeline.cjs` passed; desktop/narrow browser checks covered quantize, whole-clip Undo/Redo, draft protection, No snap and transactional clip-end rejection. A mixed note/PCM session saved/reopened in a fresh engine with byte-identical unclipped WAV exports. Listening and microphone acceptance remain open and are deferred at the user’s request.

## Requested selected-clip MIDI export

- [x] Download the applied selected note clip as a format-0 MIDI file with clip-relative timing, 960 PPQ, tempo, 4/4 and note gates/velocities. Keep project state/history exact; reject same-rounded-pitch overlaps and out-of-range pitches, preserve drafts and pending takes. Independent byte-decoding and actual app/timeline regressions passed in `gui/test_midi_export.cjs` and `gui/test_timeline.cjs`; browser checks covered the download action/status, draft rejection and 390-pixel layout. The browser tooling timed out waiting for the completed file event, so file-delivery and external-DAW acceptance are not claimed. Exact project saving remains separate; listening/microphone acceptance is still deferred.

## Requested MIDI import and quieter workspace

- [x] Import format-0/1 PPQ MIDI notes into new lanes at Insert at, using project tempo and one checked stopped edit with whole-import Undo/Redo. Preserve drafts, pending takes and existing project/audio data; bound/reject malformed or ambiguous files. Parser/editor/app regressions passed, including one-note, channels/tracks, running status, exact endpoint conversion, rollback and budgets. Browser file-picker import, placement, automatic selection, whole-import Undo/Redo, draft protection and malformed rejection passed. A mixed MIDI-note/PCM project saved and reopened in a fresh engine with byte-identical unclipped WAV exports.
- [x] Reduce routine workspace copy: fold arrangement, clip, input, import and persistence explanations into keyboard-accessible help disclosures; add hover hints and remove repeated idle instructions/mixer prose. Keep inline validation and take/error status visible. Browser help expansion/keyboard collapse and 320/390/768/1440 layouts passed. System appearance still follows native CSS preferences; physical switching/listening/microphone acceptance remains open.

## Requested Opus UI/UX findings

- [x] Give the arrangement the first viewport: consolidate transport/tempo/position/loop/history, group session and import actions, remove redundant headings/help bands and move device Apply beside devices. Separate new-note controls from the selected-note inspector; show Apply only for drafts, move step fields to their enabled input context, use pitch previews/piano keys/bar labels and distinguish focus, selection, editable fields and disabled controls. Enter applies precise clip/note/loop values; tempo/position also apply on blur. Rejected field and mixer values remain editable across rollback. Tracked JavaScript regressions and browser checks at 320/390/768/1280/1440 passed, including selected-note Enter/Undo/Redo, rejected tempo/note/loop drafts, contextual step controls and muted native seek/loop/Stop. The timeline starts at 195px at 1280×720 with all three demo lanes visible. Dark palette inspected in an isolated static fixture; physical OS theme switching and acoustic/physical-input acceptance remain unverified.

## After MVP

Requested MIDI note-file import and selected-clip export are delivered above. Multiselect/zoom/waveforms, sampled instruments, audio recording, scenes, tempo-aware audio and hosting/routing expansion are deferred. None should delay acceptance of the existing arrangement workflow.

Legacy continuous Pd live receiver controls are separate unfinished work (`878f711`), not the delivered sequenced preset. Keep their native acceptance/contract reconciliation open without expanding the MVP.

**Next action:** confirm the Tailscale UI URL on the phone; the gateway retains the existing DAW session. Try one whole-song producer request with the updated server and review its musical result; acoustic quality remains unverified. Capture role/model/failure category if it recurs. Jev routing evaluation is deferred until requested; quiet listening and microphone acceptance remain deferred until the user is ready. For engineering and PR review/merge rules, use [AGENTS.md](../AGENTS.md); for current contracts, use [PROTOCOL.md](PROTOCOL.md).

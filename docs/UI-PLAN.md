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

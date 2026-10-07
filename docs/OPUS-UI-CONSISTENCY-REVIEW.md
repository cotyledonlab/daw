# Opus UI consistency review — 2026-10-07

Completed through Pi using OpenCode inference, provider `opencode`, model `claude-opus-5-5`, medium thinking. The invocation exited successfully with empty stderr. Tools, extensions, skills, templates, themes, context-file discovery and session storage were disabled. The user explicitly approved the prepared source/design-note payload and the two screenshots for this destination after automatic approval review initially rejected the transfer. That initial transfer was not performed.

Reviewed commit: `f80e51a` on `codex/minimal-gui`. The working tree remained clean before the review. Screenshots were captured on 2026-10-06 in an isolated local server with the musical demo and first lead clip selected, stopped, at 1280×800 and 481×800 viewport sizes. They are full-page captures. The reviewer had no live browser access. This is a design/source review, not playback, recording, acoustic or current test-suite verification.

Payload: `gui/index.html`, `gui/style.css`, `gui/app.js`, `gui/timeline.js`, `gui/mixer.js`, `gui/note_input.js`, `gui/note_recording.js`, `gui/automation.js`, and `docs/UI-PLAN.md`. The exact prompt and raw response are preserved in ignored `output/opus-ui-consistency-prompt.txt` and `output/opus-ui-consistency-response.txt`; screenshots are `output/opus-ui-current-desktop.png` and `output/opus-ui-current-narrow.png`.

## Source cross-check and disposition

The response below is preserved in full. Its recommendations are proposed work; no UI behavior or styles changed as part of this review.

Confirmed in current source:

- Commit behavior differs: synth/drum/audio device fields and effects modify drafts; Pd fields apply checked edits on change; mixer values apply checked edits on change/Enter and slider release. The mixer `edit()` gathers numeric drafts across strips. Per-strip Apply labels therefore imply a narrower scope than the handler actually has. This broader gathering deliberately preserves typed drafts and should not be removed without a preservation check.
- Synth/drum/audio deletion splices the local draft, while Pd deletion uses checked `deleteTrack`. Any harmonization must preserve unapplied drafts, asset references, transactional rejection and undo semantics.
- Repeated Gain labels identify different audio stages. Clarifying labels is useful; stages themselves must remain distinct.
- The 26/36/44px control heights, mixed font families, 8–9px supporting text, unstyled `.step-readout`, checkbox `!important` rules, duplicate CSS declarations, raw device-kind labels and undefined `--text-secondary` are present.

Qualifications before implementation:

- “Saved on change” is potentially misleading: these edits apply to the in-memory engine. File persistence still requires downloading JSON/ZIP. Prefer “Applied on change” and “Draft · Apply changes,” with a separate file-save state.
- Never round editable stored gain/pan values to two decimals merely for consistency. A display formatter must preserve exact untouched values, just as beat formatting must preserve frames/Hz. Keep formatting changes away from mutation paths.
- The raw report includes `.metadata-error` among 9px statuses, but that selector explicitly overrides the size to 12px. Audit computed styles per element rather than treating every `.output-hint` instance as 9px.
- A source-only width change to `width:100%` can scale a fixed SVG viewBox and its glyphs. Check canvas geometry, pointer mapping, labels and gesture targets at all supported widths before adopting it; do not trade readable fixed canvas geometry for filling empty space.
- Mixer meter edge clipping, narrow wrapping, playback/recording state geometry and disabled-state comprehension require a live browser check. The screenshots establish only stopped-state appearance.
- Color assignments, compact control dimensions, hierarchy changes and a state pill are design preferences, not established correctness defects. Titles alone should not carry essential disabled reasons.
- The full selected-device inspector is already documented deferred work. This review does not require it, new audio features or a framework migration.

Recommended scope: start with CSS/style consistency and labels; then regroup transport, step-entry and history controls; treat commit behavior changes as a separate focused pass with preservation regressions. Do not implement all suggestions mechanically.

Prompt SHA-256: `096f3887bbc1a9e1472c2c82db503146954ee692df90469c6ef11e21b60ed788`.

---

## Full Opus response

# DAW UI Consistency Review — commit f80e51a

Evidence comes from the supplied source and the two stopped-state screenshots: desktop at 1280×800 and narrow at 481×800, with the musical demo loaded and lead/phrase selected. I have not interacted with the app. Tags mean:

- **[Visible]**: seen in a screenshot.
- **[Source]**: read in the code.
- **[Inferred]**: concluded from code paths, not observed.

## 1. Assessment

The core composition surface is still coherent: transport, ruler, lanes, piano roll and numeric fallback. The drift is concentrated where recent features were added beside older surfaces rather than integrated into them.

- The metronome/count-in, note-input/step/record panel and mixer each brought their own geometry, type size, label grammar and save model.
- The most serious problem is not visual. The page now has **three commit models** for adjacent controls, and the UI does not say which one applies.
- Next comes **four things called "Gain"** with different ranges, decimal formats and save behavior.
- The rest is mechanical CSS drift, which is cheap to fix:
  - stacked later rules overriding earlier ones;
  - an `!important` patch;
  - a mix of bare `monospace` and `sans-serif` with `'DM Mono'`;
  - 9px status text;
  - control heights of 26, 36 and 44px.

The "all device cards below the lanes" layout is the documented deferred inspector work. It is not a regression, and I do not ask for it here. The step readout, the beat-controls row, the mixer per-strip Apply and the 9px statuses are new drift.

## 2. Ranked findings

**F1 — Three save models, unlabeled (defect).** [Source] [Visible]

What happens today:

- **Immediate checked edits:** arrangement actions, Pd card fields (`numeric()` → `editArrangement` on `change`), automation, and mixer (`applyMixerEdit` → `applyDraft`).
- **Draft until "Apply changes":** synth, drum and audio card fields (`makeInstrumentCard`/`makeAudioCard` → `markEdited`), effect parameters, bypass, add/remove effect, and lowpass/delay presets.
- **Per-strip Apply:** the mixer strip button runs `edit(index)`, which commits every strip's numeric drafts (the `for (const row of numeric)` loop), not just its own.
- **Track removal differs by device:** synth/drum `×` splices the draft, while Pd `×` uses `deleteTrack`.

Impact: undo is disabled whenever the page is dirty (`isDirty() || !editHistory.canUndo`). After a card gain edit, Undo greys out with no explanation. The "Apply changes" button sits in `.toolbar`, far above the cards that made the page dirty.

Smallest fix:

1. Keep the models; do not re-architect them.
2. Label the draft surfaces: add a "Draft · Apply changes to save" hint in `makeEffectPanel` headings and instrument cards when `isDirty()`.
3. Give `#undo-button` a `title` when disabled-by-dirty ("Apply or revert changes first").
4. Rename the mixer strip button to "Save mix" and place one per mixer heading, matching its actual scope. Alternatively, restrict `edit()` to its own row; the heading button is the smaller change.
5. Make synth/drum `×` use `editArrangement({type:'deleteTrack'})`, like Pd, inside arrangement sessions.

**F2 — Four "Gain" stages presented identically (defect in labeling).** [Visible] [Source]

| Stage | Label | Range | Format | Location |
|---|---|---|---|---|
| Device gain | "Gain" | 0–1 | "0.13" | Track card |
| Mixer gain | "Gain" | 0–2 | "1" | Strip; fader scale "2/1/0" |
| Effect gain | "Gain" via "Add gain" | 0–4 | `toFixed(3)` | Effect row |
| Listening volume | "Listening volume" | 0–1 | — | Collapsed Audio settings |

Audio clip gain (`audioGain`, 0–1, step 0.05) is a fifth stage. The Master strip says "Listening volume is separate", but the control is hidden in a closed disclosure.

Smallest fix, labels only:

- Card: "Instrument level".
- Mixer: "Fader" (number label and `aria-label`).
- Effect: "Gain stage" in the row title (`{gain:'Gain stage'}`) and an "Add gain stage" button.
- Audio clip: "Clip gain".
- Use two decimals everywhere via `formatGain`, so the mixer input shows `1.00`.
- Move `#monitor-volume` and `#output-level` into `.live-panel`. They are listening controls, not settings.

**F3 — Beat controls bolted into the transport with editor styling (drift).** [Visible] [Source]

- `#beat-controls` reuses `.arrangement-controls`, so it inherits `margin: 10px 0`, label-on-top grid labels and `align-items: flex-end`.
- The Metronome checkbox (inline label) sits on a different baseline from Count-in (stacked label).
- The 9px help text wraps under both.
- The sticky bar grows taller, which costs vertical space on every scroll position.
- "Stop" is followed by plain text "Stopped", which reads as a duplicate label.
- "Escape to stop" floats in the middle of the bar.

Fix:

1. Give the row its own class, `.transport-options`, with `display:flex; align-items:center; gap:8px; margin:0`.
2. Render Count-in as an inline label with a select.
3. Move help text into `title`/`aria-describedby`.
4. Style `#play-state` as a pill: `.save-state`-like, with the dot reused.

**F4 — `.note-input-toggle` `!important` override and native checkboxes (CSS drift).** [Source] [Visible]

- `display:flex !important; flex-direction:row !important` exists only to beat `.arrangement-controls label {display:grid}`.
- Checkboxes have no `accent-color` and no 44px row height, so they look like browser defaults beside 44px themed buttons. This is visible for Metronome, Play keyboard and Step entry.

Fix: replace the `!important` rule with `.arrangement-controls label.note-input-toggle { display:flex; ... min-height:44px }`. Add `input[type=checkbox]{accent-color:var(--teal); width:16px; height:16px}`. Bypass and Step entry then share one look.

**F5 — Status text at 9px.** [Visible] [Source]

`.output-hint { font-size: 9px }` is used for `#record-notes-status`, `#midi-status`, `#note-input-status`, `#beat-controls-help`, `#audio-import-hint`, `#effects-hint` and `.metadata-error`. Statuses carrying take results and errors are the smallest text on the page. `#note-input-status.error` uses `#ff8b8b` rather than `--danger`.

Fix:

- `.output-hint` becomes 11px.
- Introduce a `.status-line` at 12px for `role="status"` paragraphs.
- Error color comes only from `var(--danger)`.
- Show `#record-notes-status` and `#midi-status` only when they say something new. Otherwise four always-visible paragraphs (visible on desktop) read as boilerplate.

**F6 — Step entry is split across two panels; its readout is unstyled.** [Visible] [Source]

- The Step entry checkbox is in `#note-input`.
- Step position and Gate fields (`.step-controls`) are in the clip editor above it.
- `.step-readout` has no CSS, so "Next: beat 0 · gate 0.250 beats" renders at inherited body size. It is visibly the largest text in the editor after headings.
- The cursor uses `stroke: currentColor`, which is white, the same as the focus ring stroke (`.roll-note:focus-visible rect {stroke:white}`).

Fix:

1. Move the `step-entry-enabled` label into `.step-controls`, or move `.step-controls` into `#note-input`. Only the DOM order changes; IDs and handlers stay.
2. Style `.step-readout` like `.position-readout`.
3. Give the cursor a class, `.step-cursor { stroke: var(--teal) }`, distinct from the playhead's yellow.

**F7 — Unit and number formatting drift (defect against UI-PLAN's "one consistent musical position convention").** [Visible] [Source]

| Element | Current wording |
|---|---|
| Positions | "Insert at (beat)", "Clip start (beat)", "Note start (beat)", "Seek (beat)", "Step position in clip (beat)" |
| Lengths | "Clip length (beats)", "Duration (beats)" |
| Loop fields | "Loop start", "Loop end": no unit |
| Note length | "Duration" in the field, "gate" in the readout and errors |

Decimals are also inconsistent: `Beat 0.00` (2), step readout `0` or `toFixed(3)`, `fmt` (5), and the loop status uses `fmt`.

Fix:

- Use "(beat)" for zero-based positions and "(beats)" for lengths, including "Loop start (beat)" and "Loop end (beat)".
- Name the note field "Gate (beats)", matching step "Gate (grid steps)".
- Display beats with up to 3 decimals and trailing zeros trimmed: one helper shared by `drawStepCursor`, `updateTransport` and the loop status. Field values keep `fmt` precision so frames are preserved exactly.

**F8 — Font-family and alignment drift in numeric fields.** [Visible] [Source]

- Arrangement inputs: `font: 12px monospace`, left-aligned.
- `.number-input`: `11px 'DM Mono'`, right-aligned.
- Effect and automation inputs: inherit sans.
- SVG uses bare `monospace` and `sans-serif` (`.ruler-text`, `.lane-name`, `.clip-label`, `.position-readout`, `.loop-status`, `.mixer-channel-type`).

The screenshot shows "120" left-aligned in Tempo but "0.13" right-aligned in cards. Fix: define `--mono: 'DM Mono', ui-monospace, monospace` and `--sans`, then use them in every one of these selectors. All numeric inputs get the same alignment; I recommend right alignment.

**F9 — Control height drift.** [Visible] [Source]

| Control | Height |
|---|---|
| Global target (`.button`, select, `.number-input`) | 44px |
| `.effect-parameter input[type=number]`, `.automation-point input` | 36px |
| Mixer `.number-input`, `.mixer-apply`, `.mixer-toggle` | 26px |
| Checkboxes | native |

The 26px mixer controls are an intentional density choice, but they undercut the stated 44px target rule. The fix is to reduce this to two tiers: 44px standard, and 32px compact for the mixer and automation only. Apply the tiers through `.control-compact` rather than per-selector `min-height` overrides.

**F10 — Identity labels for the same track differ by surface.** [Visible] [Source]

| Surface | Synth | Drum kit |
|---|---|---|
| Instrument select | "Synth" | "Drum kit" |
| Lane | "synth notes" | "drumkit notes" |
| Mixer | "CH 01 · SYNTH" | "CH 03 · DRUMKIT" |
| Card | "Polyphonic synth" | "Factory kit · …" |

The raw `device.kind` leaks into the lane and mixer labels. Fix: add a single `deviceLabel(kind)` used in `timeline.js` lane text, `mixer.js` channel type and the card subtitles, reusing the `#note-device` option text.

**F11 — Hierarchy inversion.** [Visible] [Source]

- `.effect-heading h3` is 14px, but `.track-title h2` (the track name) is 13px.
- The "Effects" heading out-ranks the track it belongs to.
- `.mixer-heading strong` is 14px, while the arrangement h2 is 17px. The mixer is a peer of the arrangement but sits a level lower.

Fix: track name 14px/600, effects h3 12px/600 muted, and mixer heading styled like `.arrangement-heading h2`.

**F12 — Yellow is overloaded.** [Source]

`#f1d68e` is used for the playhead, the selected note (`.roll-note.selected`), `.loop-pending` and `.gesture-preview`; the dirty state uses `#e3bb71`. Selection on clips is teal, but on notes it is yellow. Fix: make the selected note teal-light (like `.timeline-clip.selected`) and reserve yellow for time and pending states. This is a preference with real disambiguation value.

**F13 — Transport controls inside the editor.** [Visible]

- Seek, Set loop and Clear loop sit in the stopped editor row, disabled, with a sentence explaining why.
- On narrow, "Seek" wraps alone on its own row.
- Seek and loop being playback-only is a current contract, so keep the behavior.

Fix: group these four fields and three buttons into a `.playback-controls` sub-row with a "During playback" eyebrow, so disabled-while-stopped reads as designed. Remove the redundant clause from `showStatus`.

**F14 — Action row mixes scopes.** [Visible]

`.arrangement-actions` combines:

- track creation: Instrument, Add note track, Load Pd preset, Import WAV;
- session replacement: Open musical demo, New arrangement;
- history: Undo, Redo.

On narrow, "Redo" ends up alone on a full-width row. Fix: three groups with separators. Move Undo/Redo next to `#apply-button` in `.toolbar` so all commit and history state sits together. Session replacement could move to the topbar beside Load/Save.

**F15 — CSS cascade drift.** [Source]

- `.workspace-head`, `.toolbar`, `.arrangement-section`, `.arrangement-heading`, `.arrangement-controls`, `.live-panel` and `.timeline-clip` cursor are each declared twice or more, with later values winning.
- `.render-panel` direction flips across three media blocks: 980 → column, a later 980 → row, then 640 → column.
- `--dim` (#9aa8a2) is *brighter* than `--muted`, so the name is inverted.
- `.pd-program summary` uses an undefined `var(--text-secondary)`.
- Focus outline offset is set to 2px, then 3px.

Fix: collapse the duplicates into their final values, define or replace `--text-secondary`, and rename `--dim` to `--subtle`. Values that are not overridden do not change.

**F16 — Unused width and edge clipping in canvases.** [Visible]

- `.arrangement-svg` and `.piano-roll-svg` have a fixed `width: 1000px`, leaving an empty dark band to their right on desktop.
- The last ruler label is clipped ("1" at the right edge).

Fix: `min-width:1000px; width:100%` keeps the documented readable minimum. Add right padding in the viewBox, or skip the final label.

**F17 — Mixer meters and scale.** [Visible] [Inferred]

- Meter bars appear as faint lines at or beyond the strip's right border. This is inferred from the absolutely positioned, rotated `meter` with `width:142px` inside a 22px box.
- The fader scale "2 · 1 · 0" is linear while readouts are dB.
- Peak text is 8px.

Fix: confirm geometry in the browser and constrain with `overflow:hidden` on `.mixer-meter-bars`. Label the scale as ×2, ×1 and 0 (linear multiplier), raise peak text to 10px, and keep the dB readout.

Deferred, not regressions: the full selected-device inspector, live mixer edits, waveforms and clip-latch metering.

## 3. Compact UI specification

**Type.** Use `--sans` for labels and copy and `--mono` for every number, readout and ruler. Allowed sizes:

| Size | Use |
|---|---|
| 21 | Page h1 |
| 17 | Section headings: Arrangement, Mixer |
| 14 | Sub-section headings and track names |
| 12 | Body, controls, statuses |
| 11 | Labels and hints |
| 10 | Mono meta |

Nothing below 10px.

**Controls.**

- Standard height is 44px. Compact height (mixer, automation) is 32px.
- Checkboxes use `accent-color: var(--teal)`, inside a 44px inline label.
- Labels go above fields. The only inline labels are checkboxes and transport options.

**Buttons.**

- Primary (teal) is limited to Play, Add note track and the empty-state add.
- "Add note" becomes quiet, because drawing in the roll is the primary path.
- Destructive actions use the verb **Delete** for project data (note, clip, track, point) and **Remove** for chain membership (effect, control). Track cards use `×` with `aria-label="Delete track …"`.

**Units and labels.**

- "(beat)" marks a zero-based position; "(beats)" marks a length.
- Also: "(Hz)", "(ms)", "(frames)" and "(grid steps)".
- Velocity is labeled "Velocity (0–1)".
- Gain stages: Instrument level, Clip gain, Gain stage, Fader, Listening volume.
- Gain values always display with two decimals; beats display with up to three, trimmed.

**State colors.**

| Color | Meaning |
|---|---|
| Teal | Selected or active |
| Yellow | Time: playhead, pending loop, gesture preview |
| Amber | Unapplied draft |
| `--danger` | Error |
| Orange | Mute only |

Disabled controls use opacity .45 plus `cursor:not-allowed` and a `title` giving the reason.

**Commit language.** Each editing surface states its model once in its heading area:

- "Saved on change" for arrangement, mixer, Pd and automation;
- "Draft — Apply changes" for cards and effects.

## 4. Implementation order

1. **Pass A — CSS hygiene, no behavior change (F4, F8, F9, F11, F15, F16).**
   - Add font tokens and the two-tier height rule.
   - Remove `!important`, collapse duplicate rules and fix the undefined variable.
   - Add checkbox `accent-color`, the SVG width fix and the heading sizes.
   - Review it as a pure style diff, comparing screenshots at 1280 and 481.
2. **Pass B — Labels and formatting (F2, F7, F10, F12).**
   - Add `deviceLabel()` and the shared beat formatter, and give gain formatting two decimals.
   - Rename the gain stages, unify unit suffixes and recolor note selection.
   - Strings and formatters only; data and frames untouched.
3. **Pass C — Placement (F3, F5, F6, F13, F14, F17).**
   - Create the `.transport-options` row and move listening volume and meter into the transport.
   - Merge step-entry controls, style `.step-readout` and the cursor, and group playback-only controls.
   - Regroup the action row and move Undo/Redo beside Apply.
   - Add the status-line style and fix meter clipping.
   - DOM order changes only; keep all IDs so existing tests hold.
4. **Pass D — Commit semantics (F1).**
   - Add commit-model hints and the dirty-aware Undo title.
   - Replace per-strip Apply with mixer-level "Save mix".
   - Make synth/drum/audio track deletion use `deleteTrack` in arrangement sessions.
   - This is the only pass touching handlers, so add regressions for single-undo deletion and the Undo-disabled title.

## 5. Browser acceptance checks

- **Desktop 1280×800 with the demo:**
  - The sticky transport is one row tall: Play, Stop, a state pill, Metronome, Count-in and listening volume.
  - No text renders below 10px; check `.output-hint` and `.mixer-peak` computed sizes.
  - Every number field uses DM Mono with the same alignment.
  - The arrangement and piano-roll canvases fill their panels, and the last ruler label is not clipped.
- **Gain stages and save models:**
  - Edit the lead card's Instrument level. The amber "Unapplied changes" state appears, the card shows its draft hint, and Undo's tooltip explains why it is disabled.
  - Apply, then Undo restores 0.13.
  - Mixer Fader input shows `1.00`.
  - "Save mix" commits typed pan from two strips in one undo step.
- **Step entry:**
  - The checkbox, position, gate and readout are adjacent.
  - The readout uses the mono readout style.
  - The cursor is teal, distinct from the white focus ring and the yellow playhead.
- **Selection colors:** a selected note is teal, matching a selected clip; yellow appears only on the playhead and pending loop.
- **Labels:** all position fields end "(beat)" and lengths end "(beats)"; Loop start and Loop end include units.
- **Track naming:** lane, mixer and card all say "Synth" or "Drum kit"; no raw `drumkit`.
- **Narrow 481px:**
  - No lone full-width "Redo" or "Seek" rows.
  - Checkbox rows align with 44px controls.
  - The Master strip is reachable inside the mixer scroll area.
  - No page-level horizontal overflow.
- **Keyboard:** focus outlines use one offset across buttons, inputs, summary and SVG targets.
- **Exactness regression:** an off-grid or microtonal note round-trips through Pass B formatting with identical frames and Hz. Edit only velocity, save and reload, then diff.

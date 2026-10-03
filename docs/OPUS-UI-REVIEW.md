# Opus UI review — 2026-10-03

Requested UI/UX/usability review, completed using pi with the OpenCode provider, `claude-opus-5-5`, medium thinking, without tools or repository write access. The user explicitly approved sending the two saved demo UI screenshots and `gui/index.html`, `gui/style.css` and `gui/timeline.js` in the review prompt. The earlier rejected transfer was not performed.

Evidence: shipped UI at a 481-pixel viewport, plus the layout and timeline source. The screenshots predate this review; no UI code changed between capture and review. Live recapture timed out. Desktop recommendations are inferred from source, not verified in a desktop browser. Recommendations are proposals, not implemented capabilities; the accepted/qualified scope is in [UI-PLAN.md](UI-PLAN.md).

The response below is preserved in full. Its “Bug” labels mix source defects and visual judgments; the UI plan distinguishes those when deciding implementation scope. In particular, mute is still a later product feature, the stored velocity range remains 0–1, and no exact screenshot coordinates or measured contrast claims are adopted as acceptance criteria. A local source cross-check also confirms New/demo replacement already has a confirmation in `gui/app.js`, which was not included in the external prompt. Error replacement happens on renders/lock-state changes, not every transport poll. Those qualifications are incorporated in the UI plan.

---

# DAW UI/UX review

**Evidence basis:** The attached 481px screenshots plus the HTML, CSS, and `timeline.js` source. Desktop observations are **inferred** from the CSS. They are not fresh inspection.

**Tags:** **[Bug]** = confirmed in source. **[Inferred]** = likely from the code, not observed. **[Pref]** = design judgment.

## 1. Candid assessment

The UI reads like a settings page or landing page, not an instrument.

- **Hierarchy.** The most prominent things are a hero title ("Session workspace / Track editor") and Sample rate. The arrangement starts about 1,000px down at 481px. The piano roll starts about 2,400px down.
- **Transport is far from the work.** Play/Stop sit in a card above everything. Once you scroll to the piano roll, you can't reach them.
- **Too many teal buttons.** "Add sine track", "Add note track", "Add note" and Play all use the teal primary style, so nothing stands out.
- **Disabled vs. enabled is unclear.** At 0.52 opacity, disabled quiet buttons (Stop, Seek, Set loop, Undo, Redo, Update selected note) look almost the same as enabled ones in the screenshot.
- **Text is too small or too wordy.** CSS uses 9–10px labels and hints. There are four paragraphs of standing help text, plus a permanent status line ("Edits are checked and applied…") that adds noise without telling the user anything.
- **The timeline is unreadable at narrow width.** The 1000-unit SVG is forced to at least 640px. Ruler text renders around 6px, note rows around 8px, and notes around 7px tall. **[Bug, visible]**
- **Colour collision.** The playhead and the selected note are both yellow (`#f1d68e`). A selected clip (`#2d725e`) is barely distinguishable from an unselected one (`#225347`). **[Bug, visible]**

## 2. Ranked UI changes

1. **Move the transport into a sticky bar.** It should hold Play/Stop, position, tempo, grid and loop.
   - Evidence: the live panel and the tempo/seek/loop fields are separated, and none of them are sticky.
   - Task: audition while editing notes.
2. **Fix focus loss after selection. [Bug]**
   - `activate → selectClip → render → svg.replaceChildren()` destroys the focused element, so focus drops to `<body>`.
   - **[Inferred]** Because the Delete and Cmd‑D handler listens on `container`, keypresses from `<body>` never reach it. The shortcuts likely don't fire after a click-select.
   - Fix: after re-rendering, restore focus by `data-clip-id` / `data-note-id`.
3. **Make note edits patch only what changed. [Bug]**
   - "Update selected note" rebuilds the whole note: it grid-snaps start and end and rounds frequency to the nearest MIDI pitch.
   - Result: changing only velocity can silently move or retune a note.
   - Fix: diff against the selected note and send only the edited fields.
4. **Make clip placement explicit. [Bug, UX]**
   - "Add 4-beat clip" reads the *Seek* field, a hidden coupling.
   - Fix: add a "Start (bar)" field next to the button, defaulting to the end of the last clip on that track.
5. **Show feedback next to the action.**
   - `#notice` sits below Render (confirmed in the screenshot). Arrangement errors appear in a status line above the timeline, far from the note fields. **[Bug]** `setState` can overwrite an error with boilerplate.
   - Fix: one status line in the transport bar plus an inline error under the field group that failed. Show errors in red text.
6. **Merge the legacy track cards into lane headers.**
   - The lead/bass gain and effects cards repeat the lanes, and "Add sine track" competes with "Add note track". **[Confirmed in screenshot]**
   - Fix: put name, gain and mute in the lane header. Put effects in a "Track" tab of the detail panel. Hide "Add sine track" under an "Add ▾" menu.
7. **Use musician units and labels. [Pref, strong]**
   - Position: replace "Beat 2.00 · frame 48000" with **1.3.1**, bar.beat.sixteenth, 1-based.
   - Ruler: replace "1 · 0" with bar numbers.
   - Pitch: show the note name next to MIDI pitch ("60 · C4").
   - Velocity: use 1–127.
   - Remove "frame" from the main UI.
8. **Fix legibility of the timeline and piano roll.**
   - Size in real pixels, not a scaled viewBox: about 32px per beat, lanes about 56px, note rows about 14px, text 11px.
   - Use horizontal scroll inside `.timeline-scroll` only.
9. **Fix the breakpoint. [Bug]**
   - At ≤980px, `.app-shell` is capped at 590px, so a 970px window wastes about 40% of the width.
   - Fix: use `width: calc(100% - 24px)` at that breakpoint.
10. **Demote setup and destructive actions.**
    - Sample rate and Audio output go into a "Session settings" disclosure.
    - "New arrangement" needs confirmation and should not sit next to the primary action.
    - Undo and Redo belong in the top bar.

## 3. Screen organization

### Desktop (inferred ≥1000px)

| Region | Contents |
|---|---|
| **Top bar (sticky)** | Session name + dirty dot · ▶ ■ · position `1.3.1` · Tempo · Grid · Loop toggle + start/end · Undo/Redo · Save / Load / Export ▾ / ⚙ Settings |
| **Arrangement (flex)** | Lane headers (name, gain, mute) on the left, clips on the right. "+ Note track" and "+ Clip" sit at the end of the lanes. |
| **Detail panel (bottom)** | Tabs: **Clip** · **Track** |
| Clip tab | Clip strip (start, length, Duplicate, Delete) · piano roll · note inspector (pitch, start, length, velocity) |
| Track tab | Gain, effects |
| **Status line** | Inside the top bar |

Remove the hero, intro text and footer. Move help text into tooltips or a "?" popover.

### Narrow panel (~480px)

- Sticky compact bar with ▶ ■, position, Loop toggle and a "⋯" menu (Tempo, Grid, Settings, Save/Load/Export).
- Arrangement with internal horizontal scroll.
- Detail panel stacked below, with a segmented Clip / Notes / Track control.
- Note inspector as a two-column field grid, with the action buttons on one row.

## 4. One focused pass (highest impact)

1. **Sticky transport bar.**
   - Move Play/Stop, position, tempo, grid and loop into it.
   - Move Sample rate and Output into Settings.
   - Delete the hero and the standing help paragraphs.
2. **Interaction bug fixes.**
   - Restore focus after re-render (#2).
   - Send diff-only note patches (#3).
   - Give the clip creator its own Start field (#4).
   - Commit fields on Enter.
3. **Feedback relocation.**
   - One status slot in the bar, plus inline field errors.
   - Remove the boilerplate status text.
   - Make disabled buttons clearly different: dashed border and muted text, with a `title` explaining why.
4. **SVG legibility and colour.**
   - Pixel-based sizing and the breakpoint fix.
   - Playhead in a distinct colour (e.g. white or orange).
   - Selected clip and note get a bright outline plus a lighter fill.
5. **Fold the track cards into lane headers and a Track tab.**
   - One primary button per region.

## 5. Can wait

- Drag to move/resize clips and notes, zoom, and draw-to-create.
- Click-to-audition notes, computer-keyboard MIDI input.
- Space to play/pause (adding it is cheap, but it conflicts with buttons focused by Space), and arrow-key nudge.
- Context menus, per-track colours, mobile touch polish.
- Visual refinements to the meter and volume.

Numeric fields are fine for the MVP as long as they're grouped beside the thing they edit and commit predictably.

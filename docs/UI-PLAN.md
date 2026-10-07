# Active UI consistency checklist

Updated 2026-10-07. This checklist supports [PLAN.md](PLAN.md); it is not a second roadmap. The [Opus review](OPUS-UI-CONSISTENCY-REVIEW.md) is complete, but all implementation passes below remain open. Completed sequencer/MVP delivery and acceptance are in [PLAN-HISTORY.md](archive/PLAN-HISTORY.md) and the [archived UI checklist](archive/UI-PLAN-2026-10-04.md).

## Pass A — CSS, legibility and control geometry

Status: pending. Primary file: `gui/style.css`. Review findings F4, F5, F8, F9, F11 and F15; investigate F16/F17 before changing canvas/meter geometry.

- Consolidate duplicate rules into their effective values; replace undefined `--text-secondary` and remove the checkbox `!important` workaround with scoped selectors.
- Use consistent sans/mono tokens, numeric alignment, label/body/status scales and focus treatment. Eliminate unreadable 8–9px status/peak text; verify computed styles per element, including the existing 12px metadata-error override.
- Define standard and compact control tiers. Keep mixer density deliberate, with usable keyboard/touch targets; do not copy proposed dimensions without checking layout.
- Align checkbox rows and apply shared styling. Use the danger token for errors and clear section/track/effect heading hierarchy.
- Verify ruler-edge labels, desktop canvas empty space and mixer meter bounds. Preserve readable fixed canvas geometry until a browser-verified replacement also preserves gestures and pointer mapping.

Acceptance: compare 1280×800, 900px and 481px layouts, plus 320px overflow/target checks. No page-wide overflow, clipped labels or unreadable statuses; canvas/mixer scrolling stays local. Verify focus and disabled styles. CSS-only movement does not require new regressions; test geometry/pointer behavior that actually changes.

## Pass B — Labels, units and state identity

Status: pending. Files: timeline, mixer and app display helpers plus CSS. Findings F2, F7, F10 and F12.

- Identify audio stages: instrument/device level, clip gain, effect gain stage, mixer level and listening volume. Avoid language implying listening volume changes the exported mix.
- Use shared musician-facing device labels instead of raw `device.kind` strings.
- Use `(beat)` for zero-based positions and `(beats)` for lengths; label loop positions, note gate, velocity 0–1, Hz, ms, frames and grid steps consistently.
- Use one beat readout formatter while retaining precise editable values. Cosmetic gain/pan formatting must not round untouched stored values or feed rounded values into checked replacements.
- Give selection, playhead, draft, pending and error states distinguishable meanings; pair color with text/focus/pressed state.

Acceptance: compare matching concepts across lane, piano roll, mixer, device and effects surfaces. Change only velocity on an off-grid/microtonal note; undo/reopen and verify exact frames/Hz. Verify unrelated gain/pan values remain exact after formatting and edits.

## Pass C — Group controls by task

Status: pending. Files: `gui/index.html`, `gui/timeline.js`, `gui/app.js`, `gui/mixer.js` and CSS. Findings F3, F5, F6, F13, F14 and browser-qualified F17.

- Give metronome/count-in an aligned transport-options row. Make playback state clear and keep listening volume accessible; check sticky transport height at each width/state.
- Put step enable, clip-relative position, gate and readout together. Style the readout and distinguish its cursor from focus/playhead. Keep IDs, target selection and pending-preview cancellation semantics.
- Separate track creation, project replacement and history actions. Keep Undo/Redo and Apply state easy to reach; group playback-only seek/loop controls with visible disabled reasons.
- Reduce standing help/repeated idle messages while keeping failures, take results, MIDI state and recovery actions visible without hover.
- Verify master/track meter bounds in the running app before altering rotated meter layout; review screenshots show stopped state only.

Acceptance: desktop/narrow stopped/playing/paused/recording/error views; no stranded actions or hidden essential controls. Keyboard preview/step entry, count-in/record/Stop, retry/discard and selection/focus continue to work. Presentation changes must not redirect asynchronous edits or recordings.

## Pass D — Commit scope and editing trust

Status: pending. Files: app and mixer handlers. Finding F1. Keep behavior changes separate from style/placement passes.

- Label current models as **Applied on change** versus **Draft · Apply changes**. Neither means a project file has been saved. Keep file download/persistence wording explicit.
- Explain disabled Undo when unapplied drafts exist, with visible recovery; do not promise a new revert action unless implemented.
- Reconcile per-strip Apply wording with its all-strip numeric-draft scope. Prefer an accurately scoped mixer action; retain preservation of typed drafts across strips/toggles.
- Harmonize arrangement track deletion through checked edits only after establishing how unapplied device/effect drafts and audio assets survive. Preserve legacy continuous-session behavior.
- Keep persistent local validation, stale-write recovery, exact numeric fallback, independent clip copies and stopped-only structural edits.

Acceptance: typed drafts survive unrelated edits/polling; mixer actions have advertised scope and one checked undo entry. Deletion/undo preserves PCM assets, effects, mixer and selection. Invalid/stale replacement retains the applied project and recovery state. Fresh-engine ZIP reopen/export agrees with saved state after handler changes. Add focused preservation regressions for changed handlers.

## Follow-ups after the consistency pass

Quantize and MIDI-file import/export are the next feature slice in [PLAN.md](PLAN.md). A full selected-device inspector, multiselect/zoom, velocity lanes, waveforms, resizable panels, icon/theme redesign and animation remain deferred until a concrete musical task warrants them. Live mixer editing/smoothing and peak hold are separate behavior work.

Quiet composition/listening, physical MIDI and measured latency remain open. The final keyboard-recording browser recheck was completed on 2026-10-04, including Stop/application and whole-take Undo/Redo; evidence remains in history. Recheck recording only where a new UI change affects it.

**Next action:** Pass A; preserve current contracts and use the review's source qualifications rather than mechanically applying every suggestion.

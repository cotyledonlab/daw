# Opus 5.5 musical MVP design review

> Historical archive. Retained for evidence, not agent instructions or current scope. Old next steps, model assignments and expansion proposals are superseded by [the active plan](../../PLAN.md) and [current contracts](../../PROTOCOL.md). Do not implement archived proposals without a current task.

Received 2026-10-03 through pi/OpenCode, model `claude-opus-5-5`, medium thinking, tools/context discovery disabled. The user authorized this review. The prompt contained the active roadmap, UI checklist and scoped device implementation summary; no credentials, unrelated files or runtime state were supplied. This is design feedback, not code/hardware verification.

Adopted: generated/versioned factory sample bank, seeded noise and PCM16 golden hashes at 44.1/48k; one musical template; three named drum rows; selected microtonal Hz/cents feedback; single-field note-edit regressions; one-click Stop & edit; bounded synth envelope and modest presets. Synth attack now starts at 1ms. The until-stopped gate retains foreign-device limits.

Qualified: drum pitches reject unmapped notes transactionally with an explicit error, rather than silently dropping them or using a 50-cent remap. Existing independent voices and validated 64-voice bound remain; no stealing or same-pitch retrigger policy is introduced. Seek/loop retain documented hard resets and clip-end truncation; click-free discontinuities and tail carry can follow listening feedback. Unknown fields still reject, old schemas stay unchanged, and upgrades only accompany explicit new-device authoring. Until-stopped means Stop is required even after an unlooped arrangement ends; the template makes loop controls visible. These preserve tested contracts and keep the slice small.

The complete external response follows. Its proposed semantics below are recommendations, not shipped capabilities; PLAN.md and PROTOCOL.md describe accepted behavior.

# Gate 1 review: drum/bass/lead slice

**Verdict:** The scope is right. A generated, versioned factory bank is a sound way to avoid licensing and asset-packaging work. Most remaining risk is in undefined behavior at the edges: pitch mapping, tails, loop wrap, seek, and schema migration. Merge conflicts between the three delegated tasks are the other risk. Settle these now, because they are cheap before build and expensive after.

## Must-fix now (blockers and correctness)

1. **Assign `gui/timeline.js` focus work to one task.** Tasks 1 and 3 both claim "stable focus." Give focus restoration to task 1 and have task 3 consume it. Root owns `gui/app.js` and merges in this order: 1 → 2 (engine/schema) → 3. Do not land task 3's layout before task 1's handler fix, or the regression test will target moving DOM.

2. **Define drum-track pitch handling.** Notes are stored as Hz, but the kit maps MIDI 36/38/42.
   - Convert Hz to the nearest MIDI number with a stated tolerance, for example ±50 cents.
   - Unmapped notes are silent but preserved, never deleted.
   - The piano roll for a drum track shows three named rows (Kick/Snare/Hat) instead of a chromatic keyboard. Otherwise users must know that C1 means kick.
   - New drum notes get a short default length, since duration is ignored for one-shots.

3. **Keep factory bank generation deterministic.**
   - Use a fixed-seed integer PRNG for noise, not `rand` or OS entropy.
   - Use explicit, bounded lengths.
   - Generate at the session rate during prepare and cache it.
   - Add a golden hash test per sample at 44.1k and 48k. Any change to `factory-v1` output fails the test and forces `factory-v2`.
   - Reject unknown `kit_id` before commit, as planned.

4. **Specify voice lifecycle once, shared by drum and synth.**
   - Set a fixed maximum voice count, with oldest-voice stealing and a short fade (about 2–5 ms) on steal.
   - Retriggering the same pitch restarts that voice instead of stacking voices.
   - **Seek and Stop:** all voices are cut with a short fade, and filter and envelope state reset.
   - **Loop wrap:** pick one rule and test it. I recommend letting release tails continue across the wrap, since a hard cut sounds broken on bass. Notes that start before the wrap point but end after it get a note-off at the wrap.
   - **Export end:** either include the release tail up to a bounded cap or truncate at the arrangement end. Choose one deterministically and document it.

5. **Clamp synth parameters.**
   - `attack_ms` ≥ ~1 ms to prevent clicks; `release_ms` has a bounded maximum.
   - `cutoff_hz` is clamped below Nyquist and smoothed per block to avoid zipper noise.
   - Default gains assume four to six simultaneous voices without clipping the master.
   - A one-pole low-pass is only 6 dB/oct, which makes it weak as a "useful low-pass." It is acceptable for this slice, but presets should not depend on a resonant sweep it cannot produce.

6. **Schema 9 migration.**
   - Schema 8 files load unchanged. Sine tracks stay sine, and unknown fields are preserved.
   - Saving writes schema 9.
   - An older build opening schema 9 gets a clear error rather than a partial load.
   - Add a test that loads, saves, and reloads a schema 8 file.

7. **Until-stopped scope.**
   - It applies only when every active device is built-in. Otherwise fall back to finite mode with a visible notice.
   - Without a loop, playback stops at the arrangement end; it does not play silence forever.
   - The GUI playhead wraps correctly past 60 s of wall time.

8. **Handler fix covers all single-field edits.** Pitch-only edits preserve frames, timing-only edits preserve Hz, and velocity-only edits preserve both. Microtonal notes display cents rather than a rounded note name, so users are not misled.

## Fastest useful scope reductions

- **Ship one template, "New drum/bass/lead loop."** It contains the beat fixture plus bass and lead presets on three tracks. This is the quickest route to the gate and doubles as the acceptance fixture. Track creation from empty can be rougher.
- **Disallow device-type changes on tracks that contain notes** in this gate. Switching synth to drum otherwise silently mutes notes.
- **Two presets per device are enough** (bass, lead; one kit). Skip a preset browser.
- **Leave out of the synth for now:** velocity-to-filter, hi-hat choke, decay/sustain stages, and pan.
- **Tempo limit:** 16 bars stays under the 60 s export cap at about 64 BPM or faster. Clamp or warn in export rather than raising the cap now.

## User workflow traps

- **Locked editing during playback** is the most likely frustration. Stop & edit must be one click, visible in the persistent transport, and return focus to the selected note.
- **Errors clearing on poll.** Errors that disappear on the next status poll will look like silent failures. Make sure task 3's layout does not reintroduce this.
- **Clip insertion target.** Show the target track name beside Add clip. Users will otherwise insert at the playhead on the wrong track.
- **Duplicated phrases.** It must be obvious that duplicates are independent. Editing one should visibly leave the other unchanged.
- **Engine vs. file state.** The "Applied to engine" versus "Saved file" distinction matters more once there is real music to lose.

## Can wait (polish later)

Drag editing, velocity lanes, the full layout at 481 px, kit sample replacement, filter resonance or a better filter, swing, choke groups, mixer, waveforms, automatic restart after edit, and three-minute exports.

## Acceptance steps for this slice

Run these through the real GUI, bridge and native engine:

1. Create a project from the template. At 1280×800, transport, ruler, and three labeled lanes are visible without scrolling.
2. Play with the loop on. It loops past 90 s, and pause/resume continues at the same position.
3. Press Stop and confirm that voices fade, with no stuck notes or clicks.
4. Restart playback and confirm the previous transport and resources were released.
5. Edit the velocity of an off-grid, +37-cent lead note. Frames and Hz are bit-identical before and after, and also after undo, redo, save, and reload.
6. Duplicate the bass phrase. Edit one note in the copy, and confirm the original is unchanged.
7. Undo the edit.
8. Try to shorten a clip so it would cut a note gate. Confirm the edit is rejected, the explanation stays visible across polls, and the session is unchanged.
9. Submit a stale edit. Confirm it is rejected, the draft is retained, and recovery is offered.
10. During playback, attempt an edit and confirm the Stop & edit path works. Focus returns, and Enter, Delete, and duplicate repeat without retyping.
11. Place a microtonal note on the drum track. It maps to the nearest of the three pads within tolerance; otherwise it is silent and preserved. The drum rows show Kick/Snare/Hat.
12. Change the synth cutoff and release, then save, reload, and confirm the values persist.
13. Hand-edit an unknown `kit_id` into the file. Loading rejects it and keeps the prior session.
14. Open a schema 8 project. It loads and plays as before, and saves as schema 9.
15. Export twice at the same rate and confirm the WAV hashes are identical. Confirm the factory bank golden hashes pass at 44.1k and 48k.
16. Export and confirm the tail rule matches the documented choice.
17. Listen quietly on output. There should be no aliasing whine on high square-wave lead notes, no master clipping with all tracks playing, no clicks at the loop wrap, and an audible synth release.
18. Run Rust format, Clippy, and tests, then the Node and bridge suites. The new timeline handler test must fail on the old code.

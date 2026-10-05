# Opus 5.5 mixer design review

Received 2026-10-03 through pi/OpenCode, provider `opencode`, model `claude-opus-5-5`, medium thinking. Tools, extensions, skills, prompt/theme discovery, context-file discovery and session storage were disabled. The user explicitly approved the exact prepared payload and destination after the initial automatic approval rejection. The invocation exited successfully; no model substitution was requested.

The prompt contains the pre-implementation active roadmap, compact UI summary and mixer contract. It contains no credentials, unrelated files or runtime state. The prompt and raw response are in ignored `output/opus-mixer-prompt.txt` and `output/opus-mixer-response.txt`. Prompt SHA-256: `98e6331a6b6ef538f3121b24a902576c629bbd2823c8159beddd6a23e0c823db`.

This is a scoped design review, not code/hardware verification. Implementation of slices 2a–2c had already completed when it was received. The initial rejection and pending status are preserved chronologically in PLAN-HISTORY.md; authorization is now resolved.

## Disposition against the implemented code

- **Adopted and verified:** mixer preservation through unrelated note/clip edits, undo/redo and serialization; the real HTTP/ZIP test now performs an unrelated note edit before fresh-engine reopen. Added a focused engine regression proving step effect automation multiplies the independent saved mixer gain. These checks passed.
- **Already implemented:** strict transactional validation and foreign-device rejection; explicit authoring upgrades without downgrade; shared prepared f64 gain coefficients/rendering and exact hard-pan zero; inclusive solo/mute precedence; callback allocation checks; pre-monitor/pre-clamp meters, fixed 64-track bound and snapshot track-ID mapping. Export already returns `clipped_frames`, the bridge sends `X-Clipped-Frames`, and the GUI reports clipping. Export never applies monitor volume.
- **Clarified:** gain is linear amplitude. Step automation chooses the current effect gain rather than adding another processing stage; that effect result multiplies mixer gain. Authored schema-10 defaults remain explicit if saved; missing mixer objects retain defaults. Older schemas are preserved unless the user explicitly authors mixer settings. Do not change serialization rules based on a design suggestion alone.
- **Retained existing meter contract:** f64 atomic snapshots of the latest callback buffer with bounded seqlock retries, multiple non-consuming readers, `clipped >= 1`, no latch, and null when unavailable. The suggested f32 `swap(0)` meter would change this contract and let readers steal peaks. Idle supported meters reset to zero; the UI clears on stop. The report's instantaneous-meter threshold differs from exported `clipped_frames`, which counts actual overs (`> 1`).
- **Follow-up from listening evidence:** latest-buffer meters can miss transients between polls; peak hold or accumulated-window metering can follow if it impairs balancing. Bounded live mixer updates/smoothing can follow a demonstrated Stop & edit problem. Neither blocks the implemented slices.
- **Order unchanged:** filter/simple EQ, delay, gain automation UI and clip fades, then one sequenced programmable device. Do not reopen completed gesture/export slices or add meter/pan-law polish before useful processing.

Validation for this review reconciliation: 6 Rust mixer tests, 9 Node mixer tests, and the 3 real HTTP mixer workflow tests. Formatting and diff whitespace checks also passed. Earlier full-suite results remain in the history; they were not rerun for these test/documentation additions.

## Complete external response

The recommendations below are the model's original response, not claims about shipped behavior.

# Mixer design review (2a)

**Verdict:** The contract is mostly sound and appropriately small. Resolve the blockers below in the contract and tests now. Everything else can wait.

## Correctness blockers (fix before calling 2a done)

1. **Fields can be dropped during GUI round-trips (data-loss risk).** The GUI and engine are being built in parallel. Any GUI path that rebuilds a track or project for a checked replacement from an older model will silently drop `mixer`. This includes renames, clip edits, device edits, undo snapshots and ZIP save.
   - Every replacement must carry `mixer` through.
   - Test: set the mixer, edit an unrelated field, undo/redo, save the ZIP, reopen, and assert the mixer is unchanged.

2. **Schema rules are underdefined.**
   - **Upgrades:** a legacy project becomes schema 10 only when a non-default mixer is authored. Say whether resetting it to defaults downgrades the schema; recommend not downgrading.
   - **Defaults:** omit default mixers when serializing so legacy files stay byte-identical.
   - **Older builds:** an older build must *reject* a schema 10 file, never load it and drop fields.
   - **Bad values:** out-of-range or non-finite values must be rejected transactionally, not clamped.

3. **Unsupported tracks need an explicit rule.** Mixer support covers "built-in/PCM/gain-only". A mixer authored on an SC/Csound/Pd track must be **rejected at check time**. Silently ignoring it in one path would break native/export parity.

4. **Signal order is unstated.** Define it once:

   `source → clip gain → instrument gain → serial gain effects → step automation → mixer gain → pan → mute/solo → track meter → master sum → master meter → [native: monitor volume → clamp] / [export: clamp/quantize]`

   - Mixer gain and existing gain automation must be **multiplicative and independent**. Automation must never write the mixer field.
   - State that gain is **linear amplitude** (2.0 ≈ +6.02 dB). The UI may display dB.

5. **Export clipping is silent.** With gain up to 2, master overs will clip in export with no feedback, since meters are native-only. Choose one:
   - return `peak`/`clipped` in the export result (cheap, recommended); or
   - document that export clips silently.

   Confirm export never applies monitor volume.

6. **Native and export must use identical math.** Compute per-track `(gL, gR)` once at prepare time with one shared function. Use the same float type and the same multiply order in both paths.
   - `cos(π/2)` is about 6e-17, not 0. Special-case `|pan| == 1 → 0.0` so hard pan is truly silent.
   - Test bit-exact equality of a short native offline render against export, if a harness for that exists.

## Meter semantics to pin down (small)

- **Tap point:** track meters are post-fader, post-pan, post-mute/solo, so they show what reaches the master. A muted track reads 0.
- **Atomic peak:** store `abs(sample)` as f32 bits in `AtomicU32` and use `fetch_max`. Positive-float bit order is monotonic. Sanitize NaN to 0 first, because NaN bits compare greater than inf.
- **Read semantics:** the poller uses `swap(0)`, giving the peak since the last poll. A single consumer must own reads; otherwise a second reader steals peaks. Decay and hold belong in the GUI.
- **Clipped:** threshold is `abs > 1.0`. It latches until play starts or the user clicks the indicator. Specify which.
- **Slots:** use a fixed capacity N. State the behavior when tracks exceed N; recommend rejecting at prepare.
- **Mapping:** map slots by prepared index and report track IDs from the prepared snapshot. Reset all slots on prepare and stop so deleted tracks never show stale peaks.
- **Idle status:** `mixer_meters` should return zeros, not omit the field. This keeps GUI parsing simple.

## Can fix later

- **Live updates:** "Stop & edit" for mixer changes is tolerable for acceptance but painful for balancing (see order notes below).
- **Zipper noise:** gain smoothing is irrelevant until live updates exist.
- **Pan law:** constant-power or −3 dB pan, mono-source handling and a stereo-width control. Balance is fine for an MVP.
- **Meter polish:** peak-hold display, RMS/LUFS meters and dB scales.
- **UI extras:** multi-solo exclusivity modifiers and solo-safe.
- **Undo history:** coalescing mute/solo toggles into fewer undo entries.

## Fastest acceptance path

1. **Engine:** parse/validate/serialize the mixer, then reject it on unsupported tracks. Add the prepare-time gain table and the mixing stage in the shared block renderer, which gives export and native the same code. Add meter atomics and the status field.
2. **Round-trip test** (blocker 1) before any GUI work lands.
3. **GUI:** gain slider in dB, pan slider with double-click to center, M/S toggles, and two bar meters per track and master with a clip latch, all via existing Stop & edit transactions.
4. **Fixture run:** on the drum/bass/lead/WAV fixture, solo bass, mute drums, pan lead, and push master gain to show clipping. Then undo, save the ZIP, reopen in a fresh engine, export and compare.
5. Record the evidence in the plan history, commit, and move on.

## Minimal tests

- **Validation:** missing mixer equals defaults; out-of-range or non-finite values are rejected; an unsupported-track mixer is rejected; schema 9 files load unchanged; schema 10 with default mixers serializes without mixer fields.
- **Gain/pan math:** at pan 0, both channels are unity. At pan +1, L == 0 exactly and R is unchanged; at −1, the mirror. At pan 0.5, L ≈ cos(π/4).
- **Solo/mute truth table:**
  - no solos: unmuted tracks play;
  - one solo: only it plays;
  - two solos: both play;
  - soloed and muted: silent.
- **Parity:** a mixer-enabled render is identical between native offline and export, or within a tight tolerance if paths differ by design.
- **Automation:** step automation and mixer gain multiply, and edits to either leave the other intact.
- **Meters:** a known-amplitude sine gives the expected peak. Over 1.0 sets `clipped`. NaN input does not corrupt the meter. Slots reset after prepare. Muted tracks read 0.
- **Persistence:** the round-trip test from blocker 1, including the ZIP.
- **Callback checks:** the existing allocation checks pass with mixer and meters active.

## Concrete risks to watch

- **Export:** WAV export with an integer format clamps silently (blocker 5).
- **Concurrent edits:** GUI-side models lagging the schema can drop the mixer (blocker 1).
- **Meter readers:** two pollers, such as the GUI plus a debug status call, can steal each other's `swap(0)` peaks.
- **Clicks on resume:** a mixer change applied on resume makes an instantaneous gain step. That is acceptable under stopped-only edits, but list it with the known reset clicks.

## Order feedback (limited)

- **Live mixer updates before 2b:** add bounded live gain/pan/mute/solo updates, with per-track atomic targets and a ~10 ms linear ramp, before 2b if Stop & edit hurts the fixture balancing session. This is small and does more for "musical listening feedback" than anything in 2b. Otherwise keep it as the first follow-up.
- **Fold export peak reporting into 2c:** if you defer blocker 5, report export peak/clipped there, since 2c already promises "matching unclipped audio" and needs to measure it.
- **Keep the order otherwise:** 2b, then 2c, then 2d. Don't let mixer polish (pan law, meter ballistics) creep into 2a.

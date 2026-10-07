# Effect gain automation

> Historical archive. Retained for evidence, not agent instructions or current scope. Old next steps, model assignments and expansion proposals are superseded by [the active plan](../../PLAN.md) and [current contracts](../../PROTOCOL.md). Do not implement archived proposals without a current task.

Current scope, reconciled 2026-10-07: saved step gain lanes and their stopped GUI editor are implemented. The dated verification below is historical evidence; the active work order is in [PLAN.md](../../PLAN.md).

T08b adds optional, saved automation lanes to schema-v3 tracks. Existing v3 tracks may omit the field and retain their saved shape. Explicit null is rejected. Schema v1/v2 reject automation even when the array is empty.

Each lane has exactly `effect_id`, `parameter: "gain"`, `interpolation: "step"`, and `points`. An effect ID refers to an existing effect in the containing track, independent of array position. A track may have at most 16 lanes and at most one lane per effect. Points must be nonempty and strictly increasing by integer `frame`, within 0–9007199254740991. Values are finite linear gains within 0–4. The entire session permits at most 16,384 points. Bypassed effects still validate their lanes.

Before the first point, use the saved effect gain. A point applies before its frame's sample is processed. Between points, hold the preceding value; after the final point, hold that value. Step changes can click. No smoothing is implicit and no unsupported interpolation names are accepted.

Seek and loop wrap reconstruct each lane using the last point at or before the destination. Before the first point, they restore the saved value. This applies independently of previously processed frames, including backward seeks, exact point destinations, and bypassed effects. Source note-chase rules remain unchanged.

## Processing and ownership

The prepared chain owns gains and their point arrays. Session validation resolves target identity and rejects duplicate targets before any commit. Preparation clones point storage off the callback. Normal processing advances a cursor and applies due values before processing stereo track audio. Seek/wrap uses binary search over prepared points. Neither path allocates or accesses files.

The shared processing seam is a stereo track frame, its absolute timeline frame, and an explicit discontinuity reset. Sine/note and PCM sources both use the same prepared chain in offline and native engines. Source events remain in source scheduling; gain automation belongs to effect parameter state. The chain has zero latency and no tail. No generic foreign-processor trait is introduced before a working plugin adapter establishes its event/bus/state requirements.

Lanes are edited with revision-checked session replacement. Successful replacement stops playback and commits one revision; invalid lanes preserve the old session and active playback. Live editing of these step gain lanes is not implemented; eligible VST3/source base controls use separate live handoffs. Saved lanes do not mutate the effect's base gain or transient session revision.

The browser now edits step gain lanes in supported built-in/Pd arrangements while stopped, with checked undo. Deleting the final point removes the lane and restores the base-only gain. Agents can inspect, replace, save, load, and render automation through JSONL; native playback requires equal device/session rates.

## Verification on 2026-09-30

Portable/native format, lint, and test suites passed. Seven DSP tests cover point timing, effect identity with bypass, stereo clips, uneven blocks, destination reconstruction, and loop resets. Five schema tests cover limits, parsing, invalid-edit rollback, and persistence. Allocation instrumentation exercised 64 tracks, 16 effects/lanes per track, and all 16,384 permitted points during rendering, seek, and wrap with zero heap operations.

A two-second native example on MacBook Air Speakers submitted all 96,000 frames at 48 kHz and released its stream. The maximum observed callback render time was 490.834 microseconds for buffers up to 512 frames, with zero reported overruns. A silent native transport check passed pause/seek/loop/disable/restart and automatic release. An invalid point-order replacement left active playback, the inspected session, and revision unchanged. Offline export produced a two-second stereo WAV with zero clipped frames.

These checks establish sample behavior and observed callbacks/lifecycle. There was no independent acoustic capture or maximum-size hardware deadline test. Browser automation could not refresh the user's restarted preview because the browser tool rejected URL access; the server itself restarted successfully on port 58845.

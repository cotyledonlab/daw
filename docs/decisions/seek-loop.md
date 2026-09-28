# Native seek and loop transport

T07b adds temporary controls to an active prepared native snapshot. JSONL uses `transport.seek` and `transport.loop`; the session schema is unchanged. Native frame positions use the reported device rate. Schema-v2 playback requires that rate to equal the session rate. Offline exports still prepare a fresh linear engine at frame zero.

## Callback handoff

The native owner is the only command producer. The callback is the only consumer. A single atomic tag holds empty, seek, enable/change loop, or disable loop. Two atomic integers hold the payload. The owner writes the payload then publishes the tag with Release ordering. The callback reads the tag with Acquire, applies at most one command before checking pause, publishes its resulting timeline position, then releases the slot. The owner must acquire an empty slot before rewriting either payload field. A pending command causes a later seek/loop to fail without changing the slot or requested loop region.

The callback never waits, retries, locks, allocates, or loads files for these commands. All command values are validated before publication. An unexpected engine rejection silences the callback and marks the stream failed. Stop destroys the entire prepared snapshot and any pending command on the stream owner.

Accepted commands can still be pending. Clients poll `timeline_command_pending` until false before issuing another timeline command. A stopped/error snapshot means the stream ended; it does not prove a pending command was applied. Status fields are independent telemetry rather than one atomic snapshot. `loop_region` is the requested region, with the pending flag distinguishing publication from application.

## Engine behavior and bounds

Seek and wrap reset continuous oscillator phase, clear note gates/release tails, and binary-search the prepared note onset schedule. Notes before the destination are not chased. Audio clips spanning the destination use their corresponding source offset. Audio reset scans at most 1,024 prepared descriptors into fixed storage in identity order. Each emitted frame checks for wrap before processing onsets, so the loop end is excluded and no silence frame is inserted.

Timeline position may jump; engine output position and native submitted frames remain monotonic within one playback. A paused seek changes timeline position without producing frames. At/after loop end, the requested position wraps before the next sample, including after resuming. Disabling a loop preserves current phase and position. Repeated seeks and loops do not replenish the playback frame budget or change the wall-time deadline.

Very short loops can repeatedly scan the descriptor set. The scan is bounded but worst-case 1-frame loops with a full 1,024-clip session have not been accepted against a hardware deadline. There are no crossfades, note chase, live edits, persisted loop settings, or browser seek/loop controls.

## Verification on 2026-09-28

Portable tests cover loop-end exclusion, no note chase, voice resets, mid-clip offsets, output monotonicity, disable-loop behavior, uneven block sizes, command validation, and unchanged duration budgets. Allocation instrumentation includes successful seeks and wraps over continuous, note, and preloaded audio sessions after source deletion.

`python3 examples/transport_demo.py [SESSION]` runs a silent bounded hardware check. On MacBook Air Speakers at 48 kHz it observed paused seek acknowledgments, repeated wraps within [1200,2400), increasing submitted frames, linear playback after disabling the loop, unchanged session revision, stop/restart with no retained loop, and automatic stream release. The note run reported maximum callback render time 209.708 microseconds; the PCM stereo run reported 205.625 microseconds. Both reported zero callback budget overruns.

The first PCM attempt used a portable binary overwritten by a concurrent test build and returned `audio_unavailable`. Rebuilding with `native-audio` before the hardware check resolved it. Keep final hardware checks and builds under one owner.

These silent runs validate callback state and lifecycle, not acoustic output. Device disconnect and hot-swap remain unverified.

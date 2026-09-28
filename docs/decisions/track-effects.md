# Serial track gain effects

T08a introduces schema v3 for per-track serial effects. Schema v1 and v2 remain accepted with their previous serialization and summation behavior. No automatic migration occurs.

A v3 session keeps v2 timing, source devices, and clips. Every track additionally requires an `effects` array, which may be empty. V1/v2 reject that field. Each effect currently has exactly `{"kind":"gain","id":"trim","gain":0.5,"bypass":false}`. IDs are unique within the track, nonempty, and at most 128 UTF-8 bytes. Gain is finite and within 0–4; bypass is a required boolean. At most 16 effects are allowed per track. Bypassed effects are still validated and saved.

## Processing interface

Preparation validates the whole session and loads PCM assets before playback. It resolves source voices and clips to fixed track indices and prepares effect gains in their declared order. The callback sums each track's source voices into its own stereo frame, processes that frame through the serial chain, then sums processed tracks in serialized track order. Within a track, notes and audio retain their stable identity order. Hard clipping happens once at the master output. There is no clip between source voices, between effects, or between tracks.

This changes summation grouping for v3, including an empty chain, so upgrading a multi-track arrangement can change floating-point rounding. V1/v2 retain the original mixing path. Gain multipliers are not collapsed during preparation, preserving declared arithmetic order. Bypass passes the input through. A boost followed by attenuation can therefore preserve signals that exceed one between effects.

The working processing seam is stereo track audio flowing into the prepared effect chain. Sine/note and PCM sources both feed it. Gain has no delay, tail, note/event input, or mutable runtime state; latency is zero. Seek and loop resets affect source state as before; gain settings remain fixed. Persisted state is the effect descriptor. Changes use revision-checked session replacement, which stops playback before commit. No live effect-edit command or automation is claimed.

A generic plugin processor trait would currently mix generator scheduling with a memoryless effect and guess at foreign-plugin buses, events, and latency. T08a therefore keeps a concrete gain implementation. T08b should derive the next shared interface from gain parameter automation and the existing source preparation, before native hosting extends it.

## Next slice: T08b automation

Keep effect identity separate from array position: resolve a target by track ID and effect ID during preparation. Specify bounded lane and point counts, parameter ranges, sorted frame positions, and duplicate-target handling before implementation. Follow the timeline contract's step/hold values unless a separately explicit smoothing mode defines interpolation. Seek/wrap must reconstruct the destination value independently of earlier playback.

Do not add automation fields that only round-trip. The acceptance slice must cover generated samples, seek/loop reconstruction, block independence, allocation limits, invalid-edit rollback, and saved state. Live parameter changes require a reviewed callback handoff; the existing snapshot replacement behavior remains until that is implemented.

Browser editing remains schema-v1 only. The bridge rejects v2/v3 uploads and the client shows an existing timeline/effect session as read-only.

## Verification on 2026-09-28

Portable and native Rust format/lint/test checks passed, including eight DSP effect cases, six schema/persistence cases, and allocation instrumentation with 64 tracks and 16 effects per track during render, seek, and loop. Ten bridge tests and six browser-player tests passed. The real browser showed a v3 session as read-only with editing, audition, save, and export disabled.

A quiet two-second native gain-chain example on MacBook Air Speakers submitted 96,000 frames at 48 kHz and released the stream. The maximum measured callback render time was 660.458 microseconds for buffers up to 512 frames, with zero measured callback overruns. A separate silent transport check verified seek/loop/pause and restart with the same v3 session. These observations verify callbacks and lifecycle; no independent acoustic capture was made. Maximum-size chains were allocation-tested, not hardware deadline-tested.

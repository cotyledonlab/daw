# Timeline contract

Status: T06 implements schema-v2 notes, linear offline rendering, and rate-matched native playback alongside schema v1. T07a implements audio clips; T07b implements native seek/loop transport. T08 automation remains pending.

## Positions and tempo

A frame is one sample instant across all channels. Persist positions and lengths as integer session-rate frames, in the inclusive range 0 through 9,007,199,254,740,991. Use checked integer arithmetic for every end position and reject overflow or sums beyond that limit. Positive lengths are required. The render's existing 60-second maximum remains separate from arrangement length.

All intervals are half-open: a clip at start S with length L owns frames S through S+L-1. An event at the end belongs to the next interval. Within a block [B,B+N), events at B apply before its first sample; events at B+N wait for the next block. A block size of one and uneven block sizes must produce the same events and samples.

Store constant tempo as integer `tempo_milli_bpm`, 20,000–300,000, default 120,000. A beat means a quarter note. Beat placement uses 960 ticks per quarter note, with nonnegative integer tick positions bounded by the same maximum as frames. Convert absolute ticks to frames using exact integer arithmetic:

```text
numerator = ticks * sample_rate * 60000
denominator = 960 * tempo_milli_bpm
frame = floor((2 * numerator + denominator) / (2 * denominator))
```

Products use checked u128 in Rust. Ties round upward. Convert onset and end positions independently, then subtract to obtain duration; never accumulate rounded beat lengths. Reject a placement whose rounded end is not after its start. Tick positions are authoring inputs, not a second persisted timing source. Editing tempo changes the beat grid for future placement; it does not move existing frames. Retiming existing material must be an explicit later operation. Tempo maps, negative pre-roll, swing, and time stretching are deferred.

In-place session sample-rate editing is not exposed initially. Full replacement supplies a new arrangement with explicit frame positions and never rescales those positions automatically. An explicit future resampling/retiming operation must define that conversion. Native timeline playback initially requires the device rate to equal the session rate. The existing procedural v1 sine audition can retain its rate adaptation, but applying that adaptation directly to frame-indexed clips would change their timing.

## Note subset for schema v2 (T06)

The following is a supported schema-v2 note session:

```json
{
  "schema_version": 2,
  "sample_rate": 48000,
  "tempo_milli_bpm": 120000,
  "tracks": [{
    "id": "lead",
    "mode": "sequenced",
    "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.15},
    "clips": [{
      "kind": "notes",
      "id": "phrase",
      "start_frame": 0,
      "length_frames": 48000,
      "notes": [{
        "id": "n1",
        "start_frame": 12000,
        "duration_frames": 12000,
        "frequency_hz": 660,
        "velocity": 0.8
      }]
    }]
  }]
}
```

All shown fields are required; reject unknown fields. Root and device validation retain v1 limits. Track mode is `continuous` or `sequenced`. Continuous tracks require empty clips and retain v1 oscillator behavior. Sequenced tracks are silent without active notes; each note's frequency overrides the device's base frequency for that voice. Editing the base frequency does not transpose notes. Gain multiplies every voice's velocity before summing. Notes use Hz initially; MIDI pitch and tuning conversion belong to a later authoring adapter.

Clip start is relative to the session; note start is relative to its clip. Note duration is its gate duration, not its release tail. Require 0 <= note.start < clip.length and note.start + duration <= clip.length. Do not silently trim invalid note gates. Note frequency must be positive and below session Nyquist; velocity is finite, inclusive 0–1. Overlapping clips and overlapping same-pitch notes are allowed and summed. Each note owns a separate voice identified by (track ID, clip ID, note ID).

Track IDs remain unique within the session; clip IDs are unique within a track; note IDs are unique within a clip. Every ID is 1–128 UTF-8 bytes. T06 limits: 64 tracks, 1,024 note clips total, 16,384 notes total, and 64 simultaneous voices across the session, including release tails. Validation computes peak overlap of voice lifetimes and rejects excess polyphony rather than stealing voices; this must happen before session commit as well as preparation. A continuous track consumes one voice slot. Validate before publishing any replacement. Keep the 1 MiB request/session-file bound; it may limit content before the item caps do.

T06 originally implemented notes only; T07a now supports the audio clips described in [the asset contract](audio-assets.md). Persisted loop fields and automation fields remain unsupported. Native transport.seek and transport.loop now implement the temporary controls described below. It exposes only working note rendering and its actual validation limits in capabilities. Native playback is enabled only at the matching session rate, after callback preparation and lifecycle verification. Browser Web Audio must also reject unsupported v2 audition rather than sounding every track continuously.

## Note envelope and clip ends

Each note starts at phase zero. Let A = max(1, round_half_up(sample_rate * 0.005)) and R = max(1, round_half_up(sample_rate * 0.005)). These fixed five-millisecond attack/release lengths are derived during preparation, not saved as additional parameters in T06.

For a gate [s,e), the envelope at frame f inside the gate is min(1, (f-s)/A). At note-off frame e, start release from min(1, (e-s)/A). For frames e <= f < e+R, multiply that release-start level by (1-(f-e)/R). At e+R the voice is freed before sampling. The voice continues advancing phase through release. A gate shorter than the attack therefore releases from its partial attack level, without jumping to full volume.

No tail may escape its owning clip: at the clip end, free all its remaining voices before producing that frame. Voice lifetime is [s,min(e+R,clip_end)); count the full interval for polyphony, even for zero velocity. This rule prevents overlapping tails across adjacent clips but may create a hard cut at a boundary. No implicit crossfade is claimed. The gate fixtures test note-on/off scheduling only; envelope acceptance in T06 must additionally test these sample formulas and clip cuts.

## Same-frame order

At each frame, process these stages before generating audio:

1. Apply a transport discontinuity, if any: clear all voices and reset the timeline position.
2. Free completed release tails and voices cut by clip ends.
3. Apply note-offs to their exact voice identities.
4. Apply parameter automation at this frame (T08).
5. Start note-ons at phase zero.
6. Sum continuous tracks in serialized order, followed by sequenced voices in stable identity order, then hard-clip the output as today.

Within an event stage, compare track ID, clip ID, then note ID by UTF-8 byte order. Array order must not decide which sequenced note wins or alter the sequenced voice summation order. Stable ordering does not promise identical floating-point samples across platforms. An off for one note never releases a different same-pitch note. An off whose voice was already cut at a boundary is harmless and may be omitted from the prepared schedule. Preparation computes bounded event/voice storage; the callback cannot sort, allocate, parse JSON, load assets, or prepare foreign processors.

The fixtures encode note-off priority 1 and note-on priority 3, with reset priority 0; these are reference trace tags, not public wire enums. They omit release reclamation and automation stages. The full order above remains authoritative.

## Seek, stop, and loops (T07)

Keep output position separate from timeline position. Output position counts emitted frames monotonically; timeline position can jump. Pause retains phase and position while emitting device silence. Stop clears voices and sets timeline position to zero. Play after Stop starts at zero; future seek explicitly chooses another start. Existing wall-time audition limits remain independent of either position.

A loop is [start,end), with end > start. Playing from before the region plays that lead-in once, then loops; playing or seeking at/after end wraps to start before sampling when the loop is enabled. Seeking inside the region uses the requested frame. At a wrap, clear all voices, including release tails, reset automation to the destination value, then process events exactly at loop start. Do not process source events at the excluded loop end. The wrap does not emit an extra silent frame.

Initial seek and loop wrap use no note chase: a note whose onset was before the destination is not retriggered. Notes beginning at the destination do trigger. Audio clips (once implemented) begin at their corresponding source offset even when entered partway through. Stop/seek/wrap may click because they clear active voices; crossfades and note chase are deferred explicitly. Offline export is a linear frame-zero render that ignores the live loop setting; rendering repeated loops needs a separate explicit option later.

## Audio and automation boundaries

T07 audio clips have a session start, positive length, source offset, and gain. They own the same half-open interval as note clips. Source reads are `source_offset + (timeline_frame - clip_start)`; reject source ranges past the loaded file end. Overlaps sum and adjacent clips share no sample. Load PCM WAV assets fully before playback; sample-rate mismatches fail until resampling is implemented. Asset identity, project-relative paths, and missing-file errors are defined by the implemented [T07a asset contract](audio-assets.md).

T08 automation positions are absolute session frames, with one lane per target parameter. Points are strictly increasing; duplicate positions for a target fail validation. Before the first point use the saved parameter value; at a point apply its value before that frame's note-ons; after the last point hold its value. Initial interpolation is step/hold, not an unspecified smoothing curve. Seek/wrap evaluates the lane at the destination, including the latest preceding point, independently of callback size. Parameter target identity and schemas follow the second working processor interface in T08. No automation fields are accepted before then.

## Compatibility and implementation handoff

Keep the protocol envelope at version 1. Schema v1 reads, saves, edits, and rendering retain their behavior; do not silently migrate a session on load. In T06 support v2 alongside v1 and expose `supported_session_schema_versions: [1,2]` while retaining existing capability fields for v1 clients. A v2-aware client must inspect a session's own schema version. Keep the old browser editor protected from v2 import/edit/play until it understands the format.

An explicit client-side upgrade maps each v1 track to `mode: "continuous"`, `clips: []`, preserving ID, device, sample rate, and track order, and adds tempo 120000. It must preserve v1 rendered samples, including summation order for migrated continuous tracks. Thus the v2 stable identity ordering above applies to sequenced voices; continuous tracks retain serialized order and are mixed first. Mixed sessions then sum sequenced voices in identity order. No automatic downgrade is available for sequenced content. Revisions remain process-local: validated replacement with the upgraded session advances the current revision once.

T06 implementation checklist (completed together to keep v2 behavior coherent):

1. Add v2 note models and strict validation alongside unchanged v1 paths; add explicit upgrade example, capability metadata, and unsupported-client guards. Test v1 compatibility, limits, duplicate identities, ranges, and upgrade round trips. Do not expose successful v2 rendering until step 2 works; reject it explicitly in intermediate commits.
2. Prepare sorted note events and a fixed voice pool off the callback. Implement offsets/envelopes and linear offline rendering. Adapt the fixture cases into real Rust engine tests, including uneven blocks, adjacent clip tails, short gates, simultaneous notes, and polyphony rejection. Retain the allocation counter check.
3. Verify rate-matched native note playback and teardown before advertising it. Keep native playback snapshot-based and reject rate mismatch. GUI piano-roll work, audio clips, seek, loops, and automation remain their later tickets.

Review each commit's error and callback paths with the primary model. Luna can own fixtures, bounded validation, and examples. A feature is complete only after the production engine passes the fixtures and audio checks; the reference checker below verifies the design arithmetic alone.

## Fixtures and review

[Timeline fixtures](../../examples/sessions/timeline-contract.json) are marked `design_only_not_loadable`; they are not sessions and must not be passed to `session.load`. Run `python3 examples/check_timeline_contract.py` to check hand-authored expected frames against the reference calculations. Cases cover absolute tick rounding, a half-frame tie, adjacent half-open note gates, same-frame stable order, and loop-end exclusion/reset without note chase.

Reviewed tradeoffs: frame anchoring avoids accidental retiming but offers no automatic musical stretch; clip-bounded tails prevent spill but can click; rejecting excess voices avoids nondeterministic stealing; no note chase makes seek semantics simple but omits held notes spanning the destination. These choices are deliberate first-version limits, not inferred implementation defaults.

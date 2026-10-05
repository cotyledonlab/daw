# PCM audio clips

T07a adds working audio clips to schema v2. Seek and loop transport are T07b; recording, resampling, compressed files, streaming from disk, and plugin processing remain unsupported.

## Shape and mixing

An audio track uses `device: {"kind":"audio","gain":1}` and `mode: "sequenced"`. Its `clips` contain:

```json
{
  "kind": "audio",
  "id": "take",
  "start_frame": 24000,
  "length_frames": 48000,
  "source_path": "assets/take.wav",
  "source_offset_frames": 12000,
  "gain": 0.8
}
```

The fields above are required. Optional `fade_in_frames` and `fade_out_frames` default to zero and are omitted when zero on save. Unknown fields and null are rejected. Sine tracks accept note clips; audio tracks accept audio clips. Audio devices are not valid in schema v1 or continuous mode. Track and clip gains are finite 0–1 and multiply. A base frequency edit on an audio track returns `invalid_params`.

The clip owns [start,start+length), reading source frames [offset,offset+length). Length must be positive, integer ends must fit the existing MAX_FRAME bound, and the source range must fit the decoded file. No implicit padding, looping, fade, or resampling occurs. Explicit linear fades are supported across schemas 2–11: each is a nonnegative integer no longer than the clip, and their sum cannot exceed its length. Clip IDs remain unique within a track. The 1,024-clip session cap includes both note and audio clips. Each active audio clip consumes one of the shared 64 voice slots; note release tails and continuous sine tracks count toward that same limit.

For relative clip frame `i`, length `L`, and fade count `N >= 2`, fade-in multiplies the first N samples by `i/(N-1)` and fade-out multiplies the last N by `(L-1-i)/(N-1)`; elsewhere the factor is one. A one-frame fade mutes its edge sample. Zero leaves the old audio unchanged. These envelopes use the clip timeline rather than its source offset and multiply before track effects/mixer. Seek and loop recompute the same factor without state, allocation or asset changes. Shortening a clip rejects when its existing fades no longer fit.

Mono sources are duplicated into stereo. Stereo channels remain separate. Continuous sine tracks mix first in serialized order, followed by sequenced notes in stable identity order, then audio clips in (track ID, clip ID) UTF-8 order. Clamp the final stereo sum per channel; `clipped_frames` counts a frame once if either channel exceeds [-1,1]. Native mono output averages the two already-mixed channels; extra device channels remain silent.

## Project paths and persistence

The containing directory of a successfully loaded session is its project root. `daw play SESSION.json` uses that same directory. An initial `session.replace` uses the process working directory; later replacements and edit batches inherit the active project's root. The root is runtime context and is not a JSON field. `session.load` switches roots only after the new session and all referenced assets validate. Failed loads preserve the old session, root, revision, and playback.

`source_path` is a relative path of at most 4,096 UTF-8 bytes. Absolute paths, parent traversal, backslashes, and colon-containing paths are rejected. Canonical paths must remain inside the canonical project root, including symlinks. This restricts accidental external asset references; the headless process still runs with the caller's filesystem permissions and is not a sandbox against concurrent filesystem changes.

`session.save` with an audio track requires the destination's parent to be the active project root. It writes a fresh JSON file and leaves relative asset references unchanged. Saving elsewhere returns `invalid_params` before creating output. There is no implicit asset copying or relocation. To move a project, copy its directory with its assets, then load the copied session. Sessions without audio tracks retain existing save behavior.

The original T07a slice was script-controlled. The browser now imports owned WAV assets and represents note/audio arrangements; it cannot read arbitrary WAV paths from JSON. Portable ZIP save/reopen and private temporary roots are defined in the [GUI audio-project contract](gui-audio-projects.md). The headless root/save restrictions above are unchanged.

## Bounded preparation

Only regular WAV files containing integer PCM16, PCM24, or PCM32 are accepted, with one or two channels and exactly the session sample rate. Float WAV, compressed WAV, rate mismatch, truncated/partial frames, and unsupported channels fail explicitly. Signed PCM integers normalize by 2^(bits-1). Native v2 playback also requires the device rate to match.

Each encoded file is limited to 32 MiB. At most 128 unique canonical asset paths may be loaded, with a total of 128 MiB decoded stereo f64 frames. Declared frame counts and remaining decoded budget are checked before allocating decoded storage. Reads are bounded even if a file grows. Canonical paths deduplicate decoded buffers; overlapping clips share immutable data. A transient encoded file buffer adds at most 32 MiB plus one byte to decoded storage during preparation, excluding parser/container overhead and any already-playing snapshot.

Session mutations first validate the model and final candidate assets, then stop native playback and commit once. Batch model validation occurs after each operation; file validation is once for the final candidate, so temporary references removed within that batch need not exist. Failures return `invalid_session` for model/range syntax, `asset_error` for asset preparation, or existing transport errors. Validation/preparation are synchronous and may block the command thread.

Each render/play prepares a new snapshot. Files changed or removed after session commit may make the next preparation fail. A prepared stream continues from its in-memory data even if the file disappears. Rendering prepares assets before creating its output file. Data callbacks perform no filesystem access, decoding, allocation, reference-count changes, or buffer destruction at clip boundaries; fixed active lists index buffers retained by the prepared engine until teardown.

## Verification and next work

`tests/audio_clips.rs` covers exact source and timeline offsets, gain, channel preservation, adjacent intervals, overlaps, clipping, block independence, deletion after preparation, supported bit depths, malformed assets, path confinement, and save/load failures. Allocation instrumentation covers clip starts, ends, and source-file deletion. `python3 examples/audio_clip_demo.py` generates an ignored project, saves it, and renders it through the actual JSONL controller.

Verified on MacBook Air Speakers at 48 kHz: the generated stereo clip demo submitted 72,000 frames across 141 callbacks, released the stream, and observed zero callback overruns; maximum measured render time was 501.042 microseconds for buffers up to 512 frames. A silent interactive check confirmed a missing-asset replacement preserves session, revision, and ongoing playback. These are callback observations, not an independent acoustic capture or latency guarantee.

T07b can add seek and looping against prepared snapshots using the timeline contract. It must reset note voices, select audio source offsets at the destination, and keep output-frame count separate from timeline position. Do not perform new file loads on seek or wrap. Browser editing is a separate slice.

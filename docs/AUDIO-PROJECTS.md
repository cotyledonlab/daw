# GUI audio import and portable projects

The browser can import an integer PCM WAV into a new audio lane, edit its timeline placement, and save/reopen a ZIP project containing the session and its audio. Rust remains authoritative for session, clip-range and asset validation. The headless asset contract below and [protocol](PROTOCOL.md#pcm-audio-clips-in-schema-v2) define PCM preparation and clip behavior.

## HTTP contract

All routes use the existing loopback Host/Origin checks and `X-DAW-Token` authentication. Binary POST requests require exactly one positive `Content-Length`, an exact body read, no `Transfer-Encoding`, and one `X-DAW-Metadata` JSON header of at most 2 KiB. Duplicate JSON fields and nonfinite JSON numbers are rejected.

| Route | Input | Result |
| --- | --- | --- |
| `POST /api/audio/import` | Raw WAV; `audio/wav` or `application/octet-stream`; metadata exactly `{expected_revision, track_id, clip_id, start_frame}` | `{session, revision, asset: {source_path, frames, sample_rate, channels, bits_per_sample}}` |
| `GET /api/project` | Authenticated request | `application/zip` attachment named `session.daw.zip`, containing `session.json` and exactly its referenced WAV assets |
| `POST /api/project` | Raw ZIP; `application/zip` or `application/octet-stream`; metadata exactly `{expected_revision}` | `{session, revision}` with newly owned asset paths |

Revisions are canonical unsigned 64-bit decimal strings. Audio import appends a new, uniquely named track; track and clip IDs contain 1–128 UTF-8 bytes. `start_frame` is an integer within the existing timeline limit, and the imported full-length clip must fit that limit. Import supports v1/v2/v3/v4/v9/v10/v11/v12 sessions. Eligible devices are sine/audio/synth/drumkit, plus the constrained Pd instrument in v12. Effects are gain-only through v10, with lowpass/delay also accepted in v11/v12. It explicitly upgrades v1 to v9, retaining its sine devices and adding continuous mode, empty clips/effects and a 120 BPM authoring grid. Other supported versions remain unchanged. Runtime/plugin session scope is unchanged.

Discovery adds `gui_bridge.audio_projects: true` and `gui_bridge.audio_project_limits` with `audio_bytes`, `project_bytes`, `metadata_bytes`, `decoded_bytes` and `assets`.

## Ownership and durable saving

Each Server owns a private temporary project directory and starts its Engine with that directory as its working directory. Imported files are exclusively created at opaque `assets/<random>.wav` paths. Runtime worker/host fallback paths remain viable because Rust resolves them against its build manifest directory.

`POST /api/session` accepts audio references only when every path is already registered to that Server. It cannot introduce a repository WAV, absolute path, traversal path or another server's asset. Files are removed after engine teardown when the Server closes. A JSON download with temporary audio references is therefore not a durable audio project: use the project ZIP, whose reopen operation copies/remaps assets into the receiving Server. Sessions without audio retain JSON saving.

## Limits and ZIP validation

- WAV: at most 32 MiB encoded; mono/stereo integer PCM16/24/32; exactly the session sample rate. Float/compressed WAV, malformed/truncated chunks and incomplete frames are rejected. No resampling occurs.
- Project: at most 128 MiB encoded ZIP, including its session and container overhead; `session.json` at most 1 MiB. Expanded entries together also remain within 128 MiB.
- Active audio: at most 128 shared assets and 128 MiB decoded stereo f64 frames. Repeated references share one asset budget entry.
- ZIP: exactly one session and its referenced assets, at most 129 entries; bounded central directory checked before per-entry allocation. Only stored/deflated regular files are accepted. Unsafe paths, duplicates (including case collisions), symlinks, encryption, extra/missing assets, ZIP64 and split archives are rejected. Entry size/expansion, PCM format and CRC checks precede replacement.

Unused imported assets remain available for undo, bounded to 128 retained files and 128 MiB encoded storage. Opening a ZIP stages its independently bounded bundle alongside the old store, temporarily permitting old plus staged storage, then reclaims all old assets after successful replacement. Project reopen resets frontend undo history, so it cannot refer to reclaimed files.

## Transaction and playback behavior

Session/project operations share a project lock; export observes one consistent session and asset snapshot. Imports check the expected revision, validate/write staged assets, and issue one checked replacement. Stale or rejected imports preserve the prior session/revision/assets and remove staged files. The successful replacement's revision is the checked revision plus one; there is no fallible postcommit inspection that could delete newly committed assets.

If an engine connection loss/timeout leaves the commit outcome unknown, staged files are retained until Server teardown. They might belong to the committed session; automatic rollback would risk deleting active audio. The error directs the user to restart the server. Cleanup after a known successful replacement never rolls back its active assets.

Missing registered files fail authoritative preparation/replacement before commit; a prepared stream already owns its decoded snapshot. ZIP saving/reopening requires the files themselves. Native until-stopped playback supports built-in instruments, the sequenced Pd preset and preloaded PCM with gain/lowpass/delay: seek/loop use memory and callbacks perform no file loading. Until-stopped controls live playback duration; WAV export retains its separate duration limit. See [native transport](PROTOCOL.md#native-transport) for reset behavior.

## Headless asset contract

A successfully loaded session's canonical parent is its project root. Initial replacement uses the process working directory; later replacements/edit batches inherit the active root. `daw play` uses the saved session's directory. Failed loads preserve the old root, session, revision and playback. The root is runtime context, never saved JSON.

`source_path` is project-relative, at most 4096 UTF-8 bytes, with no absolute paths, parent traversal, backslashes or colons. Canonical asset paths must remain inside the canonical project root, including symlinks. Headless audio-session save requires a fresh destination in that same root; it leaves references unchanged and never copies assets. To relocate a headless project, copy the directory and assets together. The GUI instead owns temporary assets and portable ZIPs as described above.

Only regular matching-rate mono/stereo integer PCM16/24/32 WAVs are accepted. Signed samples normalize by `2^(bits-1)`; mono duplicates into stereo and stereo channels stay separate. Source ranges must fit decoded audio; no padding, implicit looping or resampling occurs. Clip gain multiplies source/device gain before track effects and mixer. Explicit fades use the [protocol formulas](PROTOCOL.md#pcm-audio-clips-in-schema-v2). Clamp at the master; `clipped_frames` counts a frame once when either channel exceeds [-1,1].

Encoded files are bounded to 32 MiB each; at most 128 unique canonical paths share immutable decoded buffers within the 128 MiB stereo-f64 budget. Declared frame counts and remaining budget are checked before allocation. A transient encoded read adds at most 32 MiB plus one byte, excluding parser overhead and any active snapshot.

Replacement/load mutations validate the model and final candidate assets before stopping playback and committing. Eligible [live arrangement updates](PROTOCOL.md#live-arrangement-updates) prepare assets before publication and retain running playback. Each render/play prepares a new snapshot, so changed/missing files can fail the next preparation; an already prepared stream owns its audio in memory. Rendering prepares assets before creating output. Callback clip boundaries perform no filesystem access, decoding, allocation, reference-count changes or buffer destruction.

## Verification

`tests/audio_clips.rs` covers PCM decoding, exact offsets/gain, path confinement, save/load and preparation failures. `gui/test_audio_projects.py` covers HTTP framing/authentication, asset ownership, budgets, transactional imports and ZIP validation. `examples/audio_project_workflow_demo.py` checks mixed PCM/instrument edits, fresh-server ZIP reopen and byte-identical WAV export. These checks establish data behavior; acoustic acceptance is tracked in [PLAN.md](PLAN.md).

## Standalone browser capture

Record audio holds a bounded, local mono take (up to two minutes/16 MiB of encoded capture) and converts it to matching-rate PCM16 before upload. Use take uses the existing authenticated `/api/audio/import` metadata and WAV validation, exclusive asset publication and project ownership; no microphone payload is sent to Studio providers. The normal 32 MiB WAV limit still applies. A rejected or stale import retains the browser take; Download take saves a local WAV. Browser capture is transient until downloaded or imported, and imported assets become part of ZIP saving/Undo through existing rules. This is stopped standalone capture, not synchronized audio overdubbing or remote live input mixing.

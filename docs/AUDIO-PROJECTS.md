# GUI audio import and portable projects

Implemented 2026-10-03. The browser can import an integer PCM WAV into a new audio lane, edit its timeline placement, and save/reopen a ZIP project containing the session and its audio. Rust remains authoritative for session, clip-range and asset validation. This extends the [PCM clip contract](archive/decisions/audio-assets.md) without adding arbitrary filesystem routes.

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

Missing registered files fail authoritative preparation/replacement before commit; a prepared stream already owns its decoded snapshot. ZIP saving/reopening requires the files themselves. Native until-stopped playback supports built-in instruments, the sequenced Pd preset and preloaded PCM with gain/lowpass/delay: seek/loop use memory and callbacks perform no file loading. Until-stopped controls live playback duration; WAV export retains its separate duration limit. See [seek and loop](archive/decisions/seek-loop.md) for transport reset behavior.

## Validation

The 13 new HTTP tests cover PCM variants, v1 upgrade/version preservation, framing/authentication, stale/invalid imports, registered-path confinement, decoded budgets, transactional near-limit staging/reclamation, failed replacement preservation, concurrent revisions, unsafe ZIPs, cleanup, and reopening in a second private root with byte-identical WAV rendering. The preceding combined bridge run passed 51 tests; root's final HTTP run passed 52. These checks establish protocol/data behavior, not acoustic quality.

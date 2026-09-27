# Control protocol v1

Run `daw serve`. Each UTF-8 input line receives one JSON response in the same order. The last line may end at EOF. Flush after each request; the server flushes each response. Stdout contains protocol responses only; process diagnostics use stderr. Invalid requests do not terminate the server. EOF exits normally. Command errors return `ok: false` but do not set the process exit code; clients must inspect every response.

The envelope is `{ "protocol_version": 1, "id": "caller-id", "method": "session.get", "params": {} }`. IDs must contain 1–128 UTF-8 bytes. Omitted params default to `{}`. Unknown fields are rejected. Protocol version and session schema version are independent.

Success: `{ "protocol_version": 1, "id": "caller-id", "ok": true, "result": ... }`.

Failure: `{ "protocol_version": 1, "id": "caller-id", "ok": false, "error": { "code": "invalid_params", "message": "..." } }`.

Malformed envelopes, invalid IDs, invalid UTF-8, and oversized requests return `id: null`. Other errors echo the ID. Error codes are stable; message text is diagnostic and may change. The protocol is a small project-specific interface, not JSON-RPC 2.0 or MCP.

## Commands

| Method | Params | Result |
| --- | --- | --- |
| `capabilities` | `{}` | Methods, implemented devices, session version, render limits; `live_audio` reflects the native macOS build; plugin hosting is false |
| `session.get` | `{}` | Current session |
| `session.replace` | `{ "session": <session> }` | Validated replacement session |
| `session.save` | `{ "path": "session.json" }` | `{ "path": "session.json" }` |
| `session.load` | `{ "path": "session.json" }` | Validated loaded session |
| `render` | `{ "path": "tone.wav", "seconds": 1.0 }` | `{ "frames": 48000, "sample_rate": 48000, "channels": 2, "clipped_frames": 0 }` |

Command responses are synchronous and serial. Native playback continues on its owner thread between commands. There is no render cancellation, undo, request deduplication, or concurrent editing yet. Replacement/load validate before stopping native playback and changing state; validation failures preserve both. A stop failure preserves the session. Retrying a save/render uses a new path because output creation never overwrites. Paths are relative to the server's working directory unless absolute. Parent directories must exist. A write error attempts to delete incomplete output; a process crash can leave an incomplete file. Successful writes are synced, but there is no crash-recovery journal or atomic publication to other readers.

## Native transport

Build with `--features native-audio` on macOS. `capabilities.live_audio` means this build supports native playback, not that a working device is connected. The following methods are recognized in every build; unsupported builds return `audio_unavailable`, except status and stop, which return stopped.

| Method | Params | Result |
| --- | --- | --- |
| `transport.status` | `{}` | Current transport snapshot |
| `transport.play` | `{ "seconds": 60, "volume": 0.25 }` | Snapshot after starting the current session |
| `transport.pause` | `{}` | Snapshot after requesting silence without advancing phase |
| `transport.resume` | `{}` | Snapshot after requesting continued playback |
| `transport.stop` | `{}` | Stopped snapshot after stream release; idempotent |
| `transport.volume` | `{ "volume": 0.25 }` | Snapshot after updating monitor volume; no-op while stopped |

Both play fields are required: seconds is 0.001–60 and volume is 0–1. Playback uses the default output device and a prepared copy of the session at its device rate. Play while active fails; stop first. Pause/resume while stopped fail. Volume changes are applied at callback boundaries without smoothing and do not affect saved sessions or WAV exports.

Snapshots always contain `state` and `level`. State is `stopped`, `starting` (a callback transition is pending), `playing`, `paused`, or `error`. Active snapshots also contain `device`, `sample_rate`, `submitted_frames`, and `volume`. Error snapshots contain diagnostic `error` text. `level` is the latest callback peak after monitor volume. Playing/paused states reflect callback observation, not just command dispatch. Poll status to observe transitions and later device errors. Submitted frames count DSP output, not acoustic delivery.

Playback ends at either the requested frame count or the wall-time deadline, including pauses, followed by a short bounded buffer drain. The owner checks deadlines without needing status requests. Stop releases the stream before acknowledgment; EOF terminates the engine and releases process resources. The control queue holds at most eight messages and commands time out after ten seconds; a timed-out command has an uncertain result, so restart the engine before relying on playback state. Audio callbacks use atomics and never wait on the command queue. Default device selection and host permissions remain platform limitations.

The GUI uses a 60-second native snapshot, locks session edits while active, and retains browser mode for live draft editing. Use one editing window: native transport is shared, while Web Audio players belong to individual tabs. Closing a page requests native stop on a best-effort basis; the engine's duration limit still applies.

```jsonl
{"protocol_version":1,"id":"play","method":"transport.play","params":{"seconds":10,"volume":0.25}}
{"protocol_version":1,"id":"inspect","method":"transport.status"}
{"protocol_version":1,"id":"pause","method":"transport.pause"}
{"protocol_version":1,"id":"resume","method":"transport.resume"}
{"protocol_version":1,"id":"quiet","method":"transport.volume","params":{"volume":0.1}}
{"protocol_version":1,"id":"stop","method":"transport.stop"}
```

## Session schema v1

```json
{
  "schema_version": 1,
  "sample_rate": 48000,
  "tracks": [
    {"id": "tone", "device": {"kind": "sine", "frequency_hz": 440.0, "gain": 0.15}}
  ]
}
```

Sample rate is an integer from 8,000 to 192,000. There may be zero through 64 tracks; an empty session renders silence. Track IDs are unique, nonempty, and at most 128 UTF-8 bytes. Sine frequency must be finite, greater than zero, and below half the sample rate. Gain must be finite and from zero to one. Unknown fields, device kinds, and schema versions are rejected. There is no migration yet.

New servers start with an empty 48 kHz session. Each render starts all sine oscillators at phase zero, sums tracks, duplicates mono into stereo, and hard-clips the sum to [-1, 1] before PCM16 conversion. `clipped_frames` counts frames whose sum exceeded that range. Output length is `round(seconds * sample_rate)` frames. Durations are 0.001–60 seconds. No fades are applied, so abrupt endpoints can click. Repeat renders are deterministic in the same build/platform; cross-platform bit identity is not promised.

Requests and loaded session files are limited to 1 MiB; the terminating newline is excluded from request size. Oversized lines are drained before reading the next request. This is a trusted local interface, not a filesystem sandbox. Loading a special file may block; use regular JSON files. Long renders block subsequent commands.

## Error codes

| Code | Meaning |
| --- | --- |
| `invalid_request` | Malformed JSON, invalid envelope/ID, or invalid UTF-8 |
| `request_too_large` | Input exceeds the request limit |
| `unsupported_version` | Protocol version is not 1 |
| `unknown_method` | Method is not implemented |
| `invalid_params` | Wrong or unknown params; malformed replacement shape; invalid duration |
| `invalid_session` | Session validation or load parsing failed; session file too large |
| `io_error` | File open/write/read/sync or WAV encoding failed |
| `audio_unavailable` | Native playback is not enabled for this build/platform |
| `audio_error` | Native device/setup/control failure or invalid transport state |
| `internal_error` | Unexpected session serialization failure |

## Complete command examples

Use fresh output paths for this sequence:

```jsonl
{"protocol_version":1,"id":"1","method":"capabilities"}
{"protocol_version":1,"id":"2","method":"session.get","params":{}}
{"protocol_version":1,"id":"3","method":"session.replace","params":{"session":{"schema_version":1,"sample_rate":48000,"tracks":[{"id":"tone","device":{"kind":"sine","frequency_hz":440,"gain":0.15}}]}}}
{"protocol_version":1,"id":"4","method":"session.save","params":{"path":"session.json"}}
{"protocol_version":1,"id":"5","method":"session.load","params":{"path":"session.json"}}
{"protocol_version":1,"id":"6","method":"render","params":{"path":"tone.wav","seconds":1}}
```

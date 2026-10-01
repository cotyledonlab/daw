# Control protocol v1

The separate SuperCollider shared-memory stream/native callback diagnostics do not add protocol commands or change runtime capabilities. `daw sc-stream-play QUEUE NONCE BLOCKS VOLUME GAIN` is a finite CLI diagnostic with a final JSON report and stderr readiness marker; it does not use the active session or transport. Schema-v6 sources still prepare PCM offline for native playback; live SC control/audio routing into DAW transport remains pending.

Run `daw serve`. Each UTF-8 input line receives one JSON response in the same order. The last line may end at EOF. Flush after each request; the server flushes each response. Stdout contains protocol responses only; process diagnostics use stderr. Invalid requests do not terminate the server. EOF exits normally. Command errors return `ok: false` but do not set the process exit code; clients must inspect every response.

The envelope is `{ "protocol_version": 1, "id": "caller-id", "method": "session.get", "params": {} }`. IDs must contain 1–128 UTF-8 bytes. Omitted params default to `{}`. Unknown fields are rejected. Protocol version and session schema version are independent.

Success: `{ "protocol_version": 1, "id": "caller-id", "ok": true, "result": ... }`.

Failure: `{ "protocol_version": 1, "id": "caller-id", "ok": false, "error": { "code": "invalid_params", "message": "..." } }`.

Malformed envelopes, invalid IDs, invalid UTF-8, and oversized requests return `id: null`. Other errors echo the ID. Error codes are stable; message text is diagnostic and may change. The protocol is a small project-specific interface, not JSON-RPC 2.0 or MCP.

## Commands

| Method | Params | Result |
| --- | --- | --- |
| `capabilities` | `{}` | Methods, implemented devices, session version, render limits; `live_audio` reflects native playback support; `plugin_hosting` reflects experimental live VST3 support; `parameter_metadata.implemented` reflects VST3 metadata inspection support; `live_parameter_edits` describes live saved-parameter edits |
| `session.get` | `{}` | Current session (legacy shape) |
| `session.inspect` | `{}` | `{ "revision": "0", "session": <session> }` |
| `session.edit` | `{ "expected_revision": "0", "operations": [...] }` | Updated revision and session |
| `session.replace` | `{ "session": <session> }` | Validated replacement session |
| `session.save` | `{ "path": "session.json" }` | `{ "path": "session.json" }` |
| `session.load` | `{ "path": "session.json" }` | Validated loaded session |
| `effect.inspect` | `{ "track_id": "tone", "effect_id": "echo" }` | Active VST3 effect identity, revision, and metadata for its saved parameters; read-only |
| `effect.set_parameter` | `{ "expected_revision": "4", "track_id": "tone", "effect_id": "echo", "parameter_id": 48, "value": 0.7 }` | Accepted session and revision plus `queued: true`; DSP application is acknowledged through transport status |
| `supercollider.inspect` | `{ "synthdef_hex": "53436766..." }` | Program name, named control indices/default arrays, and UGen count; structural inspection only |
| `supercollider.render` | `{ "score_path": "score.osc", "path": "fresh.wav", "sample_rate": 48000 }` | Validated offline WAV path, frames, rate and channels |
| `render` | `{ "path": "tone.wav", "seconds": 1.0 }` | `{ "frames": 48000, "sample_rate": 48000, "channels": 2, "clipped_frames": 0 }` |

Command responses are synchronous and serial. Native playback continues on its owner thread between commands. There is no render cancellation, undo, request deduplication, or parallel command execution. Revision checks detect stale edits between serialized commands. Replacement/load validate before stopping native playback and changing state; validation failures preserve both. A stop failure preserves the session. Retrying a save/render uses a new path because output creation never overwrites. Paths are relative to the server's working directory unless absolute. Parent directories must exist. A write error attempts to delete incomplete output; a process crash can leave an incomplete file. Successful writes are synced, but there is no crash-recovery journal or atomic publication to other readers.

## Revision-checked edits

Use `session.inspect` to read the session and its revision in one response. Revisions are opaque decimal strings to clients, starting at `"0"` in each engine process. They are not saved in session files, and must be discarded when reconnecting to a restarted engine. A revision is not a global session identity or a durable retry token.

`session.edit` requires `expected_revision` and 1–128 `operations`. The supplied revision must exactly match the current token. A stale token returns `revision_conflict`; inspect again and reconcile the intended change rather than blindly retrying it with the newer token.

Operations are applied in order to a copy, with session validation after each operation:

| Operation | Fields besides `op` | Behavior |
| --- | --- | --- |
| `add_track` | `track` containing an existing schema-v1 track | Append a track; its ID must be unique |
| `remove_track` | `track_id` | Remove an existing track |
| `set_parameter` | `track_id`, `parameter`, `value` | Set `frequency_hz` or `gain` on an existing sine track |

Unknown fields, operations, parameters, or missing targets return `invalid_params`. Invalid track/session values return `invalid_session`. Even an intermediate invalid state fails the entire batch; a later operation cannot repair it. No earlier operation becomes visible on failure. After all operations validate, the engine stops native playback and commits the session and next revision together. A failed validation or stale revision leaves playback alone. A native stop failure prevents the session commit.

Every successful edit, replace, or load advances the revision exactly once, including identical replacements and batches whose final state is unchanged. Reads, saves, renders, and transport commands do not advance it. Errors never advance it. Exhausting the unsigned 64-bit counter returns `revision_exhausted` before playback or session mutation.

`session.replace` and `session.load` accept an optional string `expected_revision`, checked before validation/file access. Without it they retain legacy unconditional behavior. They still return a bare session; use inspect afterward to obtain a fresh paired snapshot. `session.get`, saved JSON, and schema version 1 are unchanged. Explicit null, numeric revisions, and noncanonical strings such as `"01"` or `"+1"` are invalid. Clients sharing an engine should use checked writes consistently; the browser editor should remain in one editing window.

```jsonl
{"protocol_version":1,"id":"snapshot","method":"session.inspect"}
{"protocol_version":1,"id":"edit","method":"session.edit","params":{"expected_revision":"0","operations":[{"op":"add_track","track":{"id":"lead","device":{"kind":"sine","frequency_hz":440,"gain":0.15}}},{"op":"set_parameter","track_id":"lead","parameter":"frequency_hz","value":660}]}}
{"protocol_version":1,"id":"replace","method":"session.replace","params":{"expected_revision":"1","session":{"schema_version":1,"sample_rate":48000,"tracks":[]}}}
```

This example assumes a fresh engine. The edit returns revision `"1"`; the checked replacement advances it to `"2"`. `capabilities.editing` reports the operation names, `max_operations`, and `revision_type: "decimal_string"`. No sample-rate edits, clip/note-specific edit operations, undo, or native live graph updates are introduced by the batch command. For v2, `add_track` requires `mode` and `clips`; parameter edits affect the track device, not individual note frequencies. Use guarded full replacement for clip edits.

## Discovery metadata

`capabilities` preserves its existing fields and adds `session`, `device_metadata`, and `file_behavior`. Clients should ignore unfamiliar capability fields. This is project-specific metadata, not JSON Schema. No session fields become optional: `default` is a suggested value for constructing a new session, not an implicit parser default.

- `session.sample_rate` describes an integer in Hz, with inclusive `minimum`/`maximum` and a `default` of 48000. `session.tracks` gives `min_items` and `max_items`. `session.track_id` gives UTF-8 byte limits and requires uniqueness within the session. `session.unknown_fields` is `reject`.
- `device_metadata` is keyed by the implemented kind names in `devices`. Each entry has a description and `parameters` keyed by the persisted parameter names. Parameters describe their type, unit, default, required/finite flags, and bounds. `minimum`/`maximum` are inclusive; `exclusive_minimum` is strict.
- Sine `frequency_hz` has unit `Hz`, default 440, and exclusive minimum 0. Its `maximum_from` is `{ "field": "session.sample_rate", "factor": 0.5, "exclusive": true }`: multiply the chosen session rate by the factor to obtain the strict upper bound. This is a field reference, not executable code. Native/browser playback can impose a lower limit from the actual output rate.
- Sine `gain` uses linear amplitude, defaults to 0.15, and has inclusive bounds 0 and 1. It is not decibels or listening volume.
- `file_behavior` describes the headless interface: relative paths use `process_working_directory`, `overwrite` is false for save/render, `parent_directories` is `must_exist`, and `max_session_bytes` limits loaded files. Browser file uploads/downloads remain constrained by the bridge.
- `render.min_seconds` adds the inclusive lower duration bound (0.001) alongside the existing maximum (60).

For example, after reading capabilities, construct a device by selecting a name from `devices`, setting `kind` to that name, and copying each parameter's default from `device_metadata[kind].parameters`. Use `session.sample_rate.default` and `session_schema_version` for the containing session, and supply a unique track ID. [The Python demo](../examples/demo.py) does this without hard-coded oscillator parameters, then saves and renders the result. Metadata does not replace server validation.

## Native transport

Build with `--features native-audio` on macOS. `capabilities.live_audio` means this build supports native playback, not that a working device is connected. Build `--features vst3-live` to add experimental plugin playback; this feature implies `vst3-offline` and `native-audio`. The following methods are recognized in every build; unsupported builds return `audio_unavailable`, except status and stop, which return stopped.

| Method | Params | Result |
| --- | --- | --- |
| `transport.status` | `{}` | Current transport snapshot |
| `transport.play` | `{ "seconds": 60, "volume": 0.25 }` | Snapshot after starting the current session |
| `transport.pause` | `{}` | Snapshot after requesting silence without advancing phase |
| `transport.resume` | `{}` | Snapshot after requesting continued playback |
| `transport.stop` | `{}` | Stopped snapshot after stream release; idempotent |
| `transport.volume` | `{ "volume": 0.25 }` | Snapshot after updating monitor volume; no-op while stopped |
| `transport.seek` | `{ "frame": 12345 }` | Snapshot after requesting a timeline seek |
| `transport.loop` | `{ "region": null }` or `{ "region": { "start_frame": 0, "end_frame": 48000 } }` | Snapshot after setting or clearing the loop region |

Both play fields are required: seconds is 0.001–60 and volume is 0–1. Playback uses the default output device and a prepared copy of the session at its device rate. Play while active fails; stop first. Pause/resume while stopped fail. Volume changes are applied at callback boundaries without smoothing and do not affect saved sessions or WAV exports.

`transport.seek` and `transport.loop` are recognized in every build. On a native macOS build they require active playback; paused playback counts as active. A valid request while stopped returns `audio_error`. Builds without native playback return `audio_unavailable` for valid requests. Seek requires exactly an integer `frame` from 0 through `MAX_FRAME` (9007199254740991). Loop requires exactly a `region` field: `null` clears looping, or an object with exactly integer `start_frame` and `end_frame` fields where `0 <= start_frame < end_frame <= MAX_FRAME`. Missing, extra, non-integer, or out-of-range fields return `invalid_params`.

The native timeline command handoff has one pending slot. A successful seek/loop response means the request was accepted and may still be pending application by the audio callback. Poll `transport.status` until `timeline_command_pending` is `false` before issuing another seek or loop request. Issuing either while the slot is occupied returns `audio_error` and leaves the current request unchanged. The requested loop region is reported as `loop_region`; it can therefore reflect the accepted request before the callback applies it. `timeline_frame` is the current timeline position in frames at the native `sample_rate` (v2 playback requires the session rate to match the device rate). `submitted_frames` remains a monotonic count of submitted DSP output. These fields are separate telemetry and are not guaranteed to describe the same instant.

Once a seek is applied, `timeline_frame` reports its destination while paused, and playback resumes from that position. At or after an enabled loop's exclusive `end_frame`, playback wraps to `start_frame` before rendering the next sample. Seek and wrap clear note voices without chasing notes that began before the destination. They reset continuous oscillator phase and resume audio clips at the corresponding source offset. These discontinuities may click. The initial lead-in before the first loop boundary plays once. Loop settings are temporary transport state and are cleared by stop or restart. The play `seconds` limit is unchanged, including time spent paused. Offline WAV rendering starts at frame zero and ignores live transport loops.

Snapshots always contain `state` and `level`. State is `stopped`, `starting` (a callback transition is pending), `playing`, `paused`, or `error`. Active snapshots also contain `device`, `sample_rate`, `submitted_frames`, `timeline_frame`, `timeline_command_pending`, `loop_region`, and `volume`. Callback diagnostics are `callbacks`, `max_render_microseconds`, and `callbacks_over_buffer_budget`. Live VST3 snapshots additionally report `plugin_worker_underruns` and `plugin_parameter_update`, containing `pending` (accepted edits awaiting DSP), `applied_revision` (the latest edit revision acknowledged after a successful block, or null), and `applied_frame` (the beginning frame of that block, or null). Error snapshots contain diagnostic `error` text. `level` is the latest callback peak after monitor volume. Playing/paused states reflect callback observation, not just command dispatch. Poll status to observe transitions and later device errors. Submitted frames count DSP output, not acoustic delivery.

Playback ends at either the requested frame count or the wall-time deadline, including pauses, followed by a short bounded buffer drain. The owner checks deadlines without needing status requests. Stop releases the stream before acknowledgment; EOF terminates the engine and releases process resources. The control queue holds at most eight messages and commands time out after ten seconds; a timed-out command has an uncertain result, so restart the engine before relying on playback state. Audio callbacks use atomics and never wait on the command queue. Default device selection and host permissions remain platform limitations.

The GUI uses a 60-second native snapshot and locks structural session edits while native playback is playing or paused. Saved non-automated controls for active, non-bypassed VST3 effects remain editable in that state through live parameter edits. Use one editing window: native transport is shared, while Web Audio players belong to individual tabs. Closing a page requests native stop on a best-effort basis; the engine's duration limit still applies. Browser audition supports schema-v1 sessions only. V4 sessions require native audio for playback, including sessions without plugins; sessions containing VST3 effects also require `vst3-live`. Browser audition never hosts effects.

### GUI session editing

The GUI edits schema-v1 and schema-v4 continuous sine sessions. It does not edit schema-v2 note sessions, schema-v3 effect sessions, notes, or audio clips. The HTTP upload guard accepts only v1/v4 continuous sine session shapes, and Rust performs full authoritative validation before applying a session. Unsupported sessions are rejected and editing is locked when the current session is outside this scope.

Adding a gain effect is an explicit user action that upgrades a v1 session to v4 and initializes the v4 tempo, continuous mode, empty clips, and effect arrays. Other edits preserve the current format; loading or applying a session never silently upgrades it. The GUI can reuse VST3 effects that have already passed validation in loaded or applied sessions. It keeps an in-memory catalog of at most 64 such effects for selection; it does not scan plugins, run an installer, or expose filesystem browsing over HTTP.

VST3 controls use saved normalized parameter IDs with names from metadata inspection when available. Saved automation points remain read-only in the GUI; base values can be edited while stopped or changed live under the rules below. Users can bypass or remove an effect while stopped; removing an effect also clears only its gain automation lanes. Offline plugin renders are limited to ten seconds. These GUI controls do not imply browser plugin playback or plugin discovery.

```jsonl
{"protocol_version":1,"id":"play","method":"transport.play","params":{"seconds":10,"volume":0.25}}
{"protocol_version":1,"id":"inspect","method":"transport.status"}
{"protocol_version":1,"id":"pause","method":"transport.pause"}
{"protocol_version":1,"id":"resume","method":"transport.resume"}
{"protocol_version":1,"id":"quiet","method":"transport.volume","params":{"volume":0.1}}
{"protocol_version":1,"id":"seek","method":"transport.seek","params":{"frame":24000}}
{"protocol_version":1,"id":"loop","method":"transport.loop","params":{"region":{"start_frame":0,"end_frame":48000}}}
{"protocol_version":1,"id":"timeline","method":"transport.status"}
{"protocol_version":1,"id":"stop","method":"transport.stop"}
```

The example commands must be sent interactively: poll status until `timeline_command_pending` is false between seek and loop. If playback stops or fails before acknowledgment, start a new playback before issuing further timeline commands. [The transport demo](../examples/transport_demo.py) implements this polling.

## Session schema v2

Schema v2 implements the note subset of the [timeline contract](decisions/timeline.md): required root `tempo_milli_bpm`, required per-track `mode` and `clips`, and `kind: "notes"` clips containing frame-positioned note gates. See [the runnable arpeggio](../examples/sessions/arpeggio.json) for the full shape. Schema-v1 fields and behavior remain unchanged, and no load silently upgrades them.

`capabilities.supported_session_schema_versions` is `[1,2,3,4,5]`; the legacy `session_schema_version` remains 1. `capabilities.sequencing` describes limits and envelope duration. Frames are integers 0–9007199254740991, gates and clips have positive length, notes fit wholly inside their clip, and note frequencies must be below session Nyquist. Tempo is 20000–300000 milli-BPM. Clip/note IDs follow the existing byte limits and are unique within track/clip respectively. Unknown fields, missing required fields, and explicit null are rejected. Maximum counts are 1024 clips and 16384 notes; 64 simultaneous voices include continuous tracks and release tails. The existing 1 MiB file/request bound also applies.

Continuous v2 tracks require empty clips and retain v1 oscillator behavior. Sequenced tracks are silent without notes. Notes override the device frequency, multiply velocity by track gain, start at phase zero, and use 5 ms attack/release envelopes. Tails end at clip boundaries. Rendering starts at frame zero, ignores tempo for already positioned notes, and remains bounded to 60 seconds. Native playback requires matching session/device rates for all v2 sessions; a mismatch reports `audio_error`. Browser audition is unavailable for v2. Audio clips and native seek/loop controls are supported as described above; effect automation requires schema v3.

`session.replace`, `session.load`, and whole-track batches validate notes and peak polyphony before stopping playback or committing a new revision. `session.save` preserves the selected schema. The explicit upgrade example maps v1 tracks to continuous v2 tracks without changing their sound. In-place sample-rate editing is not exposed: a full replacement supplies a new arrangement and does not rescale any positions automatically.

The separate `timeline-contract.json` fixtures remain design-only reference cases and are not loadable sessions.

## PCM audio clips in schema v2

`devices` also lists `audio`, with gain metadata in `device_metadata.audio`. It requires a v2 sequenced track and audio clips shaped as `{ "kind":"audio", "id":"take", "start_frame":0, "length_frames":48000, "source_path":"assets/take.wav", "source_offset_frames":0, "gain":1 }`. Gain is finite 0–1. Note clips remain valid only on sine tracks. The combined note/audio/continuous voice limit is 64; the 1024 clip limit includes both clip kinds.

`capabilities.audio_clips` advertises supported PCM formats/channels, rate matching, unique-file and byte limits, project-relative paths, and the lack of save-as outside the project. The [asset contract](decisions/audio-assets.md) defines decoding, mixing, preparation, and path behavior. A successful load sets the project root to the session file's canonical parent; replace/edit inherit the active root (initially process working directory). Runtime roots never appear in saved JSON. Audio-session save requires a destination in the same root and does not copy assets.

Mutation validates final candidate assets before committing. A failed asset load leaves the session and revision unchanged. Render prepares assets before output creation; native preparation failures use `audio_error`. Assets are reloaded for each play/render snapshot. No disk I/O occurs during callback rendering.

## Serial effects in schema v3

V3 retains v2 timeline/source fields and requires `effects` on every track, including an empty array. V1/v2 reject that field. Each effect has exactly `{"kind":"gain","id":"trim","gain":0.5,"bypass":false}`. IDs are unique within the track, nonempty, and at most 128 UTF-8 bytes. Gain is finite and 0–4 inclusive. Bypass is required and does not exempt invalid settings from validation. There are at most 16 effects per track.

The source's voices are summed without clipping, then effects run in their array order on that track's stereo audio. Processed tracks are summed in serialized order and clipped once at the master. Gain effects have zero latency. V3's per-track grouping can change floating-point rounding relative to v2 even with empty chains; v1/v2 retain their original path.

`capabilities.effects` reports the supported kind, limits, routing, bypass, latency, and supported automation. Use revision-checked full replacement for gain-effect edits. Existing `set_parameter` addresses the source device only. Effect changes validate before committing, stop native playback, and advance the revision once. Saved sessions preserve chain order, IDs, gain, and bypass. Live gain-effect mutation is not supported; VST3 parameter base edits during native playback are described below. Saved automation lanes are described below.

V3 native playback requires matching device/session rates. Browser editing and uploads are restricted to v1. See [the effect contract](decisions/track-effects.md) and [runnable example](../examples/sessions/gain-chain.json).

## Saved effect gain automation

Schema-v3 tracks may include `automation`; omission preserves the previous v3 shape. Explicit null is rejected. V1/v2 reject the field. A lane is `{"effect_id":"trim","parameter":"gain","interpolation":"step","points":[{"frame":12000,"value":0.5}]}`. Each field is required and unknown fields are rejected. The target must be an existing effect in that track. A target may have one lane, with at most 16 lanes per track.

Points must be nonempty, strictly increasing by integer frame, and within the existing frame limit. Values are finite 0–4 inclusive. There are at most 16384 points across the session. Before the first point use the saved effect gain; at each point apply its value before that frame's sample; afterward hold the most recent value. Bypass leaves audio unchanged while its lane remains validated and persisted. Step changes can click.

Seek and wrap restore the last value at or before the destination, or the base gain before the first point. They use prepared data and perform no file loading. Automation is part of the snapshot shared by offline and native rendering. Use revision-checked full replacement to edit lanes: invalid edits preserve session/revision/playback; successful edits stop playback before commit.

`capabilities.automation` reports parameters, interpolation, point/lane limits, and `live_edits:false`. `capabilities.effects.automation` is true. [The automation contract](decisions/automation.md) gives the preparation rules; [the example](../examples/sessions/gain-automation.json) is loadable and playable. The GUI can preserve saved v4 gain lanes and removes lanes targeting an effect when that effect is removed; it does not edit automation points.

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
| `revision_conflict` | Expected revision does not match; inspect and reconcile before retrying |
| `revision_exhausted` | The process revision counter cannot advance |
| `asset_error` | Missing, invalid, oversized, or out-of-project WAV asset; preparation failed |
| `audio_unavailable` | Native playback is not enabled for this build/platform |
| `audio_error` | Native device/setup/control failure or invalid transport state |
| `runtime_error` | SuperCollider program/score validation, executable, owned child or output validation/publication failure |
| `plugin_error` | VST3 or AU validation, processing worker, or plugin operation failed |
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

## Standalone VST3 spike

The macOS probe in [native/vst3](../native/vst3/README.md) has its own child-process scanner output. The original probes add no JSONL methods and remain separate diagnostics. T09c provides scripted offline session processing as documented below. `capabilities.native_vst3` reports experimental status, in-process isolation, queue size, rate, and unsupported seek/loop/editors/instruments. `capabilities.plugin_hosting` reports whether experimental live hosting was compiled; `capabilities.offline_vst3.implemented` describes offline support only.

## Schema-v4 offline VST3 effects

V4 retains v3 timeline, gain chains, and gain lanes. It adds this effect shape in track `effects` (all fields required; unknown fields and null rejected):

```json
{"kind":"vst3","id":"echo","bypass":false,"bundle_path":"/absolute/Effect.vst3","class_id":"5653544671456876616C68616C6C6166","state_hex":"","controller_state_hex":"","parameters":[{"id":48,"value":0.2,"points":[{"frame":24000,"value":0.8}]}]}
```

`class_id` is exactly 32 uppercase hex characters and selects an Audio Module Class in that bundle. `bundle_path` is an absolute `.vst3` path, at most 4096 UTF-8 bytes; relocation/path resolution is not implemented. No executable or shell command is saved in the session. V1–v3 reject VST3 effects. V4 requires track effects like v3, and requires 48 kHz when any plugin appears, including bypassed plugins.

State strings are even-length hex, at most 64 KiB decoded each; aggregate decoded state is at most 256 KiB per session. Empty state means initialize the plugin and capture its initial state during successful load/replace. Nonempty component/controller blobs are restored; empty controller state is allowed when the plugin has none. Preparation fills omitted empty blobs in the committed response, then validates the captured session and its save/load size. Foreign state validation runs in a child; corrupt/incompatible state fails rather than changing the active session. Saved state is the prepared base state, not a snapshot of the last render's DSP history.

At most 8 VST3 effects exist per session, with at most 64 distinct parameter IDs per effect. Every parameter requires `id`, normalized finite `value` in 0–1, and `points` (empty allowed). Points require `frame` and normalized `value`; frames strictly increase up to `MAX_FRAME`. Plugin and gain automation together have at most 16384 points. Base values are queued at frame zero; a point at zero overrides the base, and points apply in their containing 256-frame block at the indicated offset. Plugin interpolation/smoothing is plugin-specific. Existing gain automation lanes cannot target plugin effects; plugin automation resides inside `parameters`.

Build `--features vst3-offline` on macOS and build the offline worker with `native/vst3/build.py`. Other builds parse/validate v4 statically but return `plugin_error` on load/replace of a plugin session. `capabilities.offline_vst3.implemented` reports compiled offline support, not installed plugin compatibility or worker availability. No new command is introduced: use revision-checked `session.replace`, `session.save`, `session.load`, and `render`.

Supported offline layout is one stereo input/output audio bus, no event buses, float32 at 48 kHz, maximum block 256, and zero latency. Nonzero latency, unsupported parameters/layouts/restarts, missing workers/bundles/CIDs, child crashes, oversized output, or 15-second worker timeout return `plugin_error`. Parameter-value restart notifications use current controller reads; graph/metadata/latency changes are unsupported. The GUI upload guard accepts only v1/v4 continuous sine session shapes; plugin sessions still require the Rust validation and preparation described above.

## Schema-v5 offline Audio Unit effects

V5 retains the v4 timeline fields and built-in/VST3 behavior, and adds AUv2 effects. V1–v4 reject AU effects. AU effects use the exact shape `{"kind":"au","id":"lowpass","bypass":false,"component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl","state_hex":"","parameters":[{"id":0,"value":10000.0}]}`. The only accepted component is Apple's AULowpass (`aufx/lpas/appl`). All fields are required, unknown fields and null are rejected, IDs are unique within a track, state is even-length hex, and parameter IDs are unsigned 32-bit integers with finite values validated against the component's native metadata range. AU parameters have no automation points; the `value` is in the AU's native units, not a normalized range.

Build on macOS with `--features au-offline` and build the worker with `python3 native/au/build.py`. The worker path comes from absolute `DAW_AU_HOST`, falling back to `output/au-spike/au-host`; the feature flag alone does not install or build the worker. Portable builds parse and validate v5 but return `plugin_error` when AU preparation/rendering is requested. `capabilities.offline_au.implemented` reports compiled host support, not worker presence or plugin compatibility. No separate command is added: use `session.replace`, `session.load`, `session.save`, and `render`.

AU processing requires 48 kHz, stereo planar float32, blocks up to 256 frames, and zero reported latency. A render containing plugins is limited to ten seconds. At most eight foreign effects and 64 parameters per effect are accepted. AU state is capped at 64 KiB per effect; aggregate foreign state is capped at 256 KiB. An empty state captures initial component state during successful preparation; native parameter writes are read back before preparation succeeds; saved bases override restored state. Each render restores saved state and parameter bases, then resets filter history before processing. The owned child has a 15-second timeout, 4,000,000-byte stdout limit, and 64 KiB stderr limit. Validate/prepare fully before session commit, and finish render processing before creating output. Failures return `plugin_error` and preserve session/revision or leave no partial output. AUv3, other components, event buses, instruments, plugin windows, parameter automation, and AU live playback are unsupported.

### VST3 parameter metadata inspection

Build with `--features vst3-offline` on macOS. `capabilities.parameter_metadata.implemented` is true when metadata inspection is enabled in the build; the worker must also be built to use it. `vst3-live` implies `vst3-offline`. Inspection accepts only the `track_id` and `effect_id` of an existing VST3 effect in the active, validated session. Each ID must be a nonempty string of at most 128 UTF-8 bytes. No bundle path or other client-selected plugin information is accepted.

```jsonl
{"protocol_version":1,"id":"inspect","method":"effect.inspect","params":{"track_id":"tone","effect_id":"echo"}}
```

The result contains `track_id`, `effect_id`, the current decimal-string `revision`, and `parameters` for the parameters already saved on that effect, up to 64. Each parameter record contains `id`, `name`, `short_name`, `unit`, normalized `default_value`, normalized `restored_value`, `automatable`, `read_only`, and `step_count`. It never appends parameters that the session does not save. `restored_value` reports the plugin's current value after saved state is restored; it is informational and does not replace the session's saved base `value` or automation points.

The command runs an owned child metadata job against the saved plugin state. It does not activate or process the plugin, capture new state, commit a session, advance the revision, change base values or points, or stop playback. The job has a 15-second timeout, a 128 KiB stdout limit, and a 64 KiB stderr limit. Plugin names and units originate as bounded UTF-16 fields of at most 128 code units; malformed surrogate pairs are replaced with U+FFFD before UTF-8 output. Rust validates UTF-8, numeric bounds, IDs, flags, and trailing output. A missing active effect, non-VST3 effect, worker failure, timeout, malformed metadata, or unavailable worker returns a structured error without changing the session.

The loopback GUI exposes this as authenticated `POST /api/effect/inspect` with a JSON object containing exactly `track_id` and `effect_id`; the route never accepts paths. It queries metadata only for VST3 effects in the applied session, serializes metadata requests, and defers them during render or native playback. Replies from an older applied-session generation are ignored. The editor uses actual names and units for the saved parameter controls while retaining draft/session base values; it preserves focused controls, shows a per-effect fallback error, and disables parameters marked read-only or non-automatable. Parameter editing normally requires stopped playback, with eligible base-value changes available live as described below.

### Live edits to saved VST3 parameters

`capabilities.live_parameter_edits` is `{ "implemented": true, "max_pending": 8, "automation_override": false }` when this feature is available. This reports build support; a working audio device and a valid active plugin session are still required. `effect.set_parameter` accepts exactly `expected_revision`, `track_id`, `effect_id`, `parameter_id`, and `value`. The revision must be the current canonical unsigned decimal string. IDs must select an existing VST3 effect and one of its already saved parameter IDs; `parameter_id` is an unsigned 32-bit integer and `value` is finite and normalized to 0–1.

An edit is allowed only during active native plugin transport, including paused transport, and only for a non-bypassed effect's saved automatable parameter with no saved automation points. It does not add parameters, replace or recreate an instance, invoke the foreign controller's setter, or capture new component/controller state. A successful response immediately updates the saved base value, advances the session revision, and returns `{ "revision": "5", "session": <session>, "queued": true }`. Call `session.inspect` to pair the current revision with the authoritative session. Invalid targets, stale revisions, a full queue, or a transport that is finishing leave the session and revision unchanged.

```jsonl
{"protocol_version":1,"id":"edit","method":"effect.set_parameter","params":{"expected_revision":"4","track_id":"tone","effect_id":"echo","parameter_id":48,"value":0.7}}
{"protocol_version":1,"id":"status","method":"transport.status"}
```

The worker applies one accepted edit at the beginning of a 256-frame DSP block, and acknowledges it only after that block processes successfully. `transport.status` reports the count of pending edits, the latest applied revision, and that block's beginning frame in `plugin_parameter_update`. The fixed 1024-frame audio queue plus one 256-frame in-flight block adds at most about 26.7 ms of queued audio at 48 kHz, before device latency. A paused edit may remain pending until resume. Stop or worker failure cancels edits not yet delivered to DSP; their accepted base values remain committed and persist through save/reload. An acknowledgement confirms DSP block processing, not acoustic delivery. The audio callback still consumes only prepared audio and does not call plugin code.

The GUI exposes this as authenticated `POST /api/effect/parameter` with exactly the command fields. It obtains the current revision from `GET /api/session/inspect`, updates the displayed saved value after acceptance, and uses transport status for application acknowledgement. Saved non-automated controls for active, non-bypassed VST3 effects remain editable while playing or paused; structural edits remain locked. Browser audition still does not host plugins.

### Experimental live VST3 playback (macOS)

Build the shared library with `python3 native/vst3/build.py`, then build Rust with `cargo build --locked --features vst3-live`. This feature implies `vst3-offline` and `native-audio`. The script writes `output/vst3-spike/libdaw-vst3.dylib` as a fallback; `DAW_VST3_LIBRARY` may name a different library using an absolute path. Start playback from a saved schema-v4 session with `target/debug/daw play session.json 60 0.25`.

Live plugin sessions require both the session and CoreAudio device to run at 48 kHz, stereo float32 plugin processing, zero-latency plugins, and no event buses. The engine supports play, pause, resume, volume, and stop for up to 60 seconds. Seek and loop requests on plugin sessions return `audio_error`. Replacing the session or making structural edits stops playback; eligible saved VST3 parameter base edits use the live queue described above.

An owned in-process worker creates, processes, and destroys plugin instances on the same DSP thread. The CoreAudio callback consumes a fixed 1024-frame SPSC queue and performs no foreign calls, allocation, or locks. At 48 kHz the queue represents about 21.3 ms, in addition to device latency. On underrun the callback emits silence and does not advance the timeline; status and CLI transport output include `plugin_worker_underruns`. Worker startup times out after five seconds. Shutdown waits two seconds; if the in-process worker is hung, the engine detaches it and reports an explicit stop failure. Because plugin code runs in-process, a plugin crash can terminate the engine. Offline rendering continues to use its isolated child workers. A silent ValhallaFreqEcho check on MacBook Air Speakers at 48 kHz passed three play/pause/resume/stop cycles and session replacement, with zero underruns and zero callbacks over budget; acoustic delivery was not verified.

Plugin renders accept 0.001–10 seconds. The renderer preserves headroom between serial effects and across tracks, then clips once at the master to PCM16. Source/assets and all child processing complete before destination creation. Failures create no output and preserve session/revision; the usual fresh-path rule still applies. Each child owns its process group and is terminated/reaped; stdout audio/state and stderr diagnostics are bounded. This is crash containment, not a security sandbox or realtime proof. Plugin code runs with caller permissions. See [the VST3 example](../examples/vst3_demo.py).

### Standalone Audio Unit lifecycle proof

The original `native/au.probe` AUv2 lifecycle probe remains a separate diagnostic with its own schema-version-1 result envelope and owned process harness. It opens no audio device and is distinct from schema-v5 session processing. See [the worker and proof notes](../native/au/README.md) and [the AU adapter contract](decisions/audio-units.md).

## SuperCollider NRT jobs

`capabilities.supercollider_nrt` reports Unix implementation support, whether `DAW_SCSYNTH` names an existing absolute file, limits and `session_device:false`, `native_playback:false`. Configuration does not prove executable permissions, runtime version or installed UGen compatibility; actual launch and rendering are checked per job. Set `DAW_SCSYNTH` before starting the controller. Windows builds recognize the command but explicitly report unavailable rendering.

```jsonl
{"protocol_version":1,"id":"sc-render","method":"supercollider.render","params":{"score_path":"/absolute/score.osc","path":"/absolute/fresh.wav","sample_rate":48000}}
```

Params contain exactly `score_path`, `path`, and integer `sample_rate`. Nonempty UTF-8 paths may be at most 4096 bytes and resolve relative to the controller working directory. Rates are 8000–192000. The result is `{ "path": <requested path>, "frames": <integer>, "sample_rate": <rate>, "channels": 2 }`. The command is synchronous, does not load/change the session or advance its revision, and does not start/stop transport. It has no HTTP GUI route. Wrong JSON params use `invalid_params`; score/executable/worker/output failures use `runtime_error`.

The input is a regular binary score file capped at 1 MiB, snapshotted into a private job directory. Each record is a big-endian 32-bit length and a flat OSC bundle of 16–65516 bytes; at most 16384 records are allowed. Relative timestamps begin at zero, never decrease, and end between 0.001 and ten seconds. Messages use string addresses, zero padding, valid UTF-8 strings and scalar/string/blob type tags; nested bundles, integer command addresses and arrays are unsupported. Nonfinite floating arguments and malformed/trailing data are rejected before launch. See [the score contract](decisions/supercollider.md) for accepted tags and [the fixture writer](../native/supercollider/score.py).

The owned Unix child runs `scsynth -N` with two output channels, no input, 64-frame blocks and a private restricted path for file-accessing OSC commands. Supply SynthDefs using `/d_recv`; automatic defaults and external-file assets are not loaded. No hardware audio or realtime networking starts. The process group is terminated on completion/failure, and the direct child is reaped. Timeout is 15 seconds, with 64 KiB each for stdout/stderr. Captured server error diagnostics fail even if the process returns exit status zero. Output must be bounded, stereo PCM16 at the requested rate. Up to 128 trailing block frames are trimmed to `round(last_timestamp * sample_rate)`. All samples are validated before exclusive destination creation; failure leaves no partial destination and never overwrites an existing output. This is process ownership, not a security sandbox or a live callback design.

OSC validation checks framing and scalar/string/blob encoding, plus the SynthDef file header for `/d_recv`. It does not interpret every server command or UGen graph. A successful result proves that a bounded WAV was produced and validated; NRT provides no per-command completion acknowledgements, and commands that fail silently in the runtime cannot all be detected. Interactive completion handling remains required before claiming the full T11 control contract.

## SuperCollider program inspection

`supercollider.inspect` is available in every build and does not require `DAW_SCSYNTH`. Params contain exactly `synthdef_hex`, a nonempty even-length hexadecimal string encoding at most 65536 bytes. Uppercase and lowercase hex are accepted. JSON shape or hex errors return `invalid_params`; unsupported/malformed binary programs return `runtime_error`. Inspection launches no child, accesses no files, and leaves the active session, revision and transport unchanged on success or failure. There is no GUI route.

The result is `{ "name": "daw_sine_fixture", "controls": [{ "name": "freq", "index": 0, "default_values": [440.0] }, ...], "ugen_count": 4 }`. Defaults retain native float32 precision. Control records retain the binary name-table order; an array spans from its index to the next greater named index, or the end of the parameter array. Unnamed parameter prefixes are omitted. Names and named indices must be unique.

The accepted subset is SCgf version 2, exactly one definition, and no variants or trailing bytes. Names are nonempty UTF-8 Pascal strings without NUL (at most 255 bytes). Limits are 4096 constants, 256 parameter slots, 64 control names, 1024 UGens, 4096 inputs and 256 outputs per UGen, and 16384 input/output ports in total. Counts cannot be negative. Constants/defaults must be finite. Calculation rates are 0–3; inputs must reference valid constants or outputs of earlier UGens. The recognized Control, AudioControl, TrigControl and LagControl output spans must fit the parameter array.

`capabilities.supercollider_programs` publishes these principal limits and reports `inspection:true`, `runtime_validation:false`, `session_device:true` (saved source support is described separately). Inspection validates encoding and graph references; it does not establish that an installed UGen exists, accepts a particular input/output layout, produces audio, or responds successfully to runtime commands. Use the owned NRT job for actual rendering evidence. Schema-v6 sources use this inspection before owned runtime preparation; interactive routing remains pending.

## Schema-v6 prepared SuperCollider sources

V6 retains earlier timeline/effect behavior and adds `supercollider` devices. Older schemas reject this device; there is no implicit upgrade. Example track (replace the illustrative hex with a complete program):

```json
{"id":"runtime","mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"level","gain":0.5,"bypass":false}],"device":{"kind":"supercollider","synthdef_hex":"53436766...","synth_name":"daw_sine_fixture","duration_frames":48000,"gain":1.0,"controls":[{"name":"freq","values":[440.0],"points":[{"frame":24000,"values":[660.0]}]}]}}
```

All device/control/point fields shown are required. Unknown fields and null are rejected. Sessions require the existing tempo, mode, clips and effects fields. Sources use continuous mode with empty clips, start at frame zero, end at `duration_frames`, then contribute silence; musical changes are saved control events rather than note clips. The embedded program must be one accepted SCgf-v2 definition, capped at 60 KiB, and its name must exactly match `synth_name`. No executable paths, language source or external assets are saved. Compile programs externally; `DAW_SCSYNTH` selects the installed runtime for preparation.

Controls use actual named parameter spans from inspection. Names must be unique and present in the definition; `values` must exactly match the span's array length and be finite when converted to native float32. There are no generic frequency/gain constraints on native controls: those meanings belong to the program. Omitted controls retain the program defaults. Point frames strictly increase within the source duration; a point at zero overrides its saved base during synth creation. Saved bases are supplied in `/s_new`, including array slots. Known initialization-rate control slots reject points after frame zero. Events use `/n_setn` at frame-derived NRT timestamps; UGen control-rate/block behavior still applies, so these events do not promise sample-accurate DSP changes. Programs must direct their output to buses 0/1 to appear in the stereo source.

At most four sources and 512 runtime control points are allowed per session, with at most ten seconds of summed source duration. Each duration is at least `ceil(sample_rate/1000)` frames. Decoded runtime audio shares the 128 MiB asset budget. Saved sessions must fit the existing save/load byte limit (with 4096 bytes reserved for command envelopes). Existing foreign effect limits also apply.

`session.replace`, `session.load` and checked edits inspect and render every changed source through owned NRT children before stopping transport or committing session/revision. A runtime failure returns `runtime_error` and preserves the active project. Statically invalid session/program/control data returns `invalid_session`. Preparation uses stereo floating-point WAV internally, preserves source headroom, validates every sample and trims block padding to the saved frame count. Each source retains the existing 15-second worker deadline and bounded process diagnostics. Preparation can take multiple worker deadlines and is synchronous; clients need a suitable command timeout. Arbitrary UGen compatibility is not assumed, and a valid silent program is accepted.

Prepared PCM remains in an internal cache keyed by the program, name, controls/events, duration and rate. That cache is neither accepted in JSON nor saved. Renders and native snapshots reuse it after a successful commit; gain edits reuse it because track gain is applied in the DAW. Reload regenerates sources, so random UGens may produce new audio on reload. A missing runtime after successful preparation does not invalidate the already prepared snapshot. Unchanged saved inputs do not imply bit-identical output across new preparation jobs or runtime versions.

Sources feed the existing stereo track chain, including gain automation and supported offline VST3/AU effects, before final master clipping. Native playback requires a matching device rate and supports the existing seek/loop semantics through prepared PCM. With experimental live VST3, source preparation runs on the caller before the DSP worker starts; callbacks consume only the prepared stream. AU remains offline. There is no interactive SuperCollider DSP, live control mutation, GUI editing/import support, language interpreter or external-file runtime asset contract. `session.edit` can change track gain; native control/event edits use revision-checked full replacement and stop the active snapshot. `capabilities.supercollider_sources` distinguishes these capabilities from standalone jobs and program inspection.

Run `DAW_SCSYNTH=/absolute/scsynth python3 examples/supercollider_tracks_demo.py` to create two programs, save/reload, and export through gain chains. Add `--native` with a native-audio build for a bounded silent hardware smoke test; that verifies submitted callback frames, not acoustic delivery.

### Interactive SuperCollider diagnostic

`native/supercollider/live_probe.py` is a separate owned macOS server diagnostic, not a JSONL command or DAW transport adapter. It proves completion/node/control responses and finite private-bus PCM changes, with silent output-bus capture and clean quit. Existing SuperCollider source playback still uses prepared PCM. Streaming runtime routing, live runtime edits and GUI imports remain unavailable; see the [interactive proof and streaming gates](decisions/supercollider.md#interactive-server-proof-t11c1).

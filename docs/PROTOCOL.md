# Control protocol v1

The separate SuperCollider/Csound/Pure Data shared-memory queue diagnostics do not add protocol commands or change capabilities. `daw sc-stream-play QUEUE NONCE BLOCKS VOLUME GAIN` and the Csound/Pure Data stream probes are finite diagnostics; they do not use the active session or transport. JSONL transport defaults to prepared runtime PCM (including schema-v6/v7 SuperCollider, schema-v7 Csound, and schema-v8 Pure Data); `transport.play` can explicitly select `source_mode:"live"` for owned live SC, Csound, Pure Data, or mixed runtime DSP. The legacy saved-session CLI also supports live DSP independently of active protocol state.

The [libpd block/message proof](../native/puredata/block_probe.py) is a separate owned diagnostic. It still records `daw_transport:false` and `hardware_audio:false`; schema-v8 Pure Data session devices use a separate owned preparation worker described below.

Run `daw serve`. Each UTF-8 input line receives one JSON response in the same order. The last line may end at EOF. Flush after each request; the server flushes each response. Stdout contains protocol responses only; process diagnostics use stderr. Invalid requests do not terminate the server. EOF exits normally. Command errors return `ok: false` but do not set the process exit code; clients must inspect every response.

The envelope is `{ "protocol_version": 1, "id": "caller-id", "method": "session.get", "params": {} }`. IDs must contain 1–128 UTF-8 bytes. Omitted params default to `{}`. Unknown fields are rejected. Protocol version and session schema version are independent.

Success: `{ "protocol_version": 1, "id": "caller-id", "ok": true, "result": ... }`.

Failure: `{ "protocol_version": 1, "id": "caller-id", "ok": false, "error": { "code": "invalid_params", "message": "..." } }`.

Malformed envelopes, invalid IDs, invalid UTF-8, and oversized requests return `id: null`. Other errors echo the ID. Error codes are stable; message text is diagnostic and may change. The protocol is a small project-specific interface, not JSON-RPC 2.0 or MCP.

## Commands

| Method | Params | Result |
| --- | --- | --- |
| `capabilities` | `{}` | Methods, implemented devices, session version, render limits; `live_audio` reflects native playback support; `plugin_hosting` reflects experimental live VST3 support; `parameter_metadata.implemented` reflects VST3 metadata inspection support; `puredata_sources` describes schema-v8 prepared-source support; `puredata_live_transport` describes separate native live support; `live_parameter_edits` describes live saved-parameter edits |
| `session.get` | `{}` | Current session (legacy shape) |
| `session.inspect` | `{}` | `{ "revision": "0", "session": <session> }` |
| `session.edit` | `{ "expected_revision": "0", "operations": [...] }` | Updated revision and session |
| `session.update_live` | `{ "session": {…}, "expected_revision": "4" }` | Checked prepared update without stopping eligible native playback; returns the applied session |
| `session.replace` | `{ "session": <session> }` | Validated replacement session |
| `session.save` | `{ "path": "session.json" }` | `{ "path": "session.json" }` |
| `session.load` | `{ "path": "session.json" }` | Validated loaded session |
| `effect.inspect` | `{ "track_id": "tone", "effect_id": "echo" }` | Active VST3 effect identity, revision, and metadata for its saved parameters; read-only |
| `effect.set_parameter` | `{ "expected_revision": "4", "track_id": "tone", "effect_id": "echo", "parameter_id": 48, "value": 0.7 }` | Accepted session and revision plus `queued: true`; DSP application is acknowledged through transport status |
| `source.set_control` | `{ "expected_revision": "4", "track_id": "runtime", "control_name": "gain", "values": [0.02] }` | Accepted saved base/revision plus `queued:true`; native readback and callback observation appear in transport status |
| `supercollider.inspect` | `{ "synthdef_hex": "53436766..." }` | Program name, named control indices/default arrays, and UGen count; structural inspection only |
| `supercollider.render` | `{ "score_path": "score.osc", "path": "fresh.wav", "sample_rate": 48000 }` | Validated offline WAV path, frames, rate and channels |
| `render` | `{ "path": "tone.wav", "seconds": 1.0 }` | `{ "frames": 48000, "sample_rate": 48000, "channels": 2, "clipped_frames": 0 }` |
| `note.preview` | `{ "expected_revision": "4", "track_id": "lead", "frequency_hz": 440, "velocity": 0.8, "path": "fresh-preview.wav" }` | Fixed half-second stereo PCM16 WAV report, using one saved sequenced instrument; leaves session/revision unchanged |

Command responses are synchronous and serial. Native playback continues on its owner thread between commands. There is no render cancellation, undo, request deduplication, or parallel command execution. Revision checks detect stale edits between serialized commands. Replacement/load validate before stopping native playback and changing state; validation failures preserve both. A stop failure preserves the session. Retrying a save/render uses a new path because output creation never overwrites. Paths are relative to the server's working directory unless absolute. Parent directories must exist. A write error attempts to delete incomplete output; a process crash can leave an incomplete file. Successful writes are synced, but there is no crash-recovery journal or atomic publication to other readers.

### Live arrangement updates

`session.update_live` accepts the same `session` object as `session.replace` and requires `expected_revision`. It validates and prepares the complete replacement outside the hardware callback, queues it on the owned render worker, then commits the session/revision. Unsupported playback, changed track count/order/IDs, changed device kind/mode/rate, stale revisions and queue-full failures preserve the applied session. This command never starts or stops transport. `session.replace` retains its stopped replacement behavior.

Supported macOS native prepared playback advertises `live_arrangement_edits:true` in transport status; discovery publishes `capabilities.live_arrangement_edits`. Sine/synth/drumkit/owned PCM tracks with built-in effects support mixer, numeric device/effect parameters, gain automation, tempo, notes and clips. Track/runtime changes and Pd/plugin sessions require Stop. A single pending prepared update is bounded; callers retry a rejected queue-full edit explicitly. Ordinary editor edits, agent batches and snapshot Undo/Redo use authenticated `POST /api/session/live` while native playback is active. Recorded takes retain their stopped application path.

The worker adopts existing timeline/output position, loop, held-note oscillator/filter state and matching-ID lowpass/delay history (delay time changes reset that delay). New notes whose onset is already past are not chased; moved/deleted notes follow the new schedule, with a 64-frame crossfade at the update boundary. Existing queued audio plays first (up to four 256-frame blocks plus a partially consumed block). Paused playback retains its buffered audio; accepted changes become audible on resume. `audible_session_revision` is a decimal string identifying the most recent updated block consumed by the callback, initially `"0"`. The applied saved snapshot may lead audible state. Metronome tempo follows consumed update blocks; saved note/clip frames do not move with tempo. Callback consumption uses fixed storage and atomics without heap operations, I/O, locks or foreign initialization. `pd_worker_underruns` / `pd_transport_wait_buffers` remain the existing worker diagnostic names on both built-in and Pd playback.

## Stopped note preview

`note.preview` requires the current revision and a sequenced sine, synth, drumkit or Pd instrument with built-in gain/lowpass/delay effects. Frequency must be positive and below session Nyquist, velocity is 0–1, and drum pitches retain their exact supported mapping. Unknown fields and stale revisions reject. Native starting/playing/paused transport rejects with `transport_active`; rendering never stops or replaces playback.

An owned single-track snapshot places one note at frame zero with a gate of `floor(sample_rate / 4)` frames and a clip spanning `floor(sample_rate / 2)` frames. Rendering produces half a second, with releases/effect tails bounded by that limit. Saved device/processing and mixer gain/pan remain; mute/solo are ignored for audition. Saved automation is evaluated on this preview timeline from frame zero. The current session, revision and audio asset root remain untouched. Output follows existing exclusive-publication rules; rejected preparation creates no output. Pd uses actual libpd DSP and requires its installed runtime.

The browser bridge exposes authenticated `POST /api/note/preview` with the same fields except `path`; the server owns its temporary output and returns WAV bytes with `X-Clipped-Frames`. Discovery includes `note_preview` and `gui_bridge.note_preview`. Browser listening volume scales only preview playback. MIDI/keyboard input is opt-in, previews a fixed gate rather than following held keys, and optionally inserts stopped step notes through existing revision-checked replacement and undo. Preview itself adds no saved schema, timing/tempo migration or held-gate recording command. The separate GUI recording workflow and atomic take endpoint are documented in [recorded note takes](#listening-click-count-in-and-recorded-note-takes).

## Revision-checked edits

Use `session.inspect` to read the session and its revision in one response. Revisions are opaque decimal strings to clients, starting at `"0"` in each engine process. They are not saved in session files, and must be discarded when reconnecting to a restarted engine. A revision is not a global session identity or a durable retry token.

`session.edit` requires `expected_revision` and 1–128 `operations`. The supplied revision must exactly match the current token. A stale token returns `revision_conflict`; inspect again and reconcile the intended change rather than blindly retrying it with the newer token.

Operations are applied in order to a copy, with session validation after each operation:

| Operation | Fields besides `op` | Behavior |
| --- | --- | --- |
| `add_track` | `track` containing an existing schema-v1 track | Append a track; its ID must be unique |
| `remove_track` | `track_id` | Remove an existing track |
| `set_parameter` | `track_id`, `parameter`, `value` | Set sine `frequency_hz`, or source `gain` (including drumkit/synth); use full replacement for other synth settings |

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
- `render.min_seconds` adds the inclusive lower duration bound (0.001) alongside the session-dependent maximum: 180 for built-in/PCM/schema-12 Pd-instrument export with gain/lowpass/delay, 60 for prepared runtime sessions and 10 for plugin sessions. Finite native playback remains capped at 60.

For example, after reading capabilities, construct a device by selecting a name from `devices`, setting `kind` to that name, and copying each parameter's default from `device_metadata[kind].parameters`. Use `session.sample_rate.default` and `session_schema_version` for the containing session, and supply a unique track ID. [The Python demo](../examples/demo.py) does this without hard-coded oscillator parameters, then saves and renders the result. Metadata does not replace server validation.

## Native transport

Build with `--features native-audio` on macOS. `capabilities.live_audio` means this build supports native playback, not that a working device is connected. Build `--features vst3-live` to add experimental plugin playback; this feature implies `vst3-offline` and `native-audio`. The following methods are recognized in every build; unsupported builds return `audio_unavailable`, except status and stop, which return stopped.

| Method | Params | Result |
| --- | --- | --- |
| `transport.status` | `{}` | Current transport snapshot |
| `transport.play` | `{ "seconds": 60, "volume": 0.25 }` or `{ "until_stopped": true, "volume": 0.25 }` | Snapshot after starting the current session |
| `transport.pause` | `{}` | Snapshot after requesting silence without advancing phase |
| `transport.resume` | `{}` | Snapshot after requesting continued playback |
| `transport.stop` | `{}` | Stopped snapshot after stream release; idempotent |
| `transport.volume` | `{ "volume": 0.25 }` | Snapshot after updating monitor volume; no-op while stopped |
| `transport.seek` | `{ "frame": 12345 }` | Snapshot after requesting a timeline seek |
| `transport.loop` | `{ "region": null }` or `{ "region": { "start_frame": 0, "end_frame": 48000 } }` | Snapshot after setting or clearing the loop region |

Volume is required, finite and 0–1. Finite mode requires seconds from 0.001–60. Optional `until_stopped` is a boolean, default false. When true, seconds must be absent (explicit null rejects), source mode must be prepared, and all devices must be eligible sine/synth/drumkit/audio/pd_instrument tracks with gain/lowpass/delay effects. Audio uses the existing bounded preloaded PCM path. Foreign sources/effects reject, including bypassed foreign effects. `capabilities.timeline_transport.until_stopped` reports native build support; `until_stopped_devices` lists eligible kinds. Invalid params preserve session/revision without starting audio. A valid request on a portable build returns `audio_unavailable`. Until-stopped playback has no frame-count/wall-time expiry, including while paused; without a loop it continues silently after the last note. Stop/error/EOF releases the stream. This does not extend export or finite foreign-device limits. Optional `source_mode` is exactly `"prepared"` (default) or `"live"`; missing mode preserves existing playback behavior. Live runtime mode has a stricter ten-second limit and supports schema-v6 SuperCollider, schema-v7 Csound, schema-v8 Pure Data, or mixed runtime sessions on macOS arm64. Prepared mode uses the default output device and prepares a copy of the session at its device rate, including cached SuperCollider, Csound and Pure Data source PCM. Play while active fails; stop first. Pause/resume while stopped fail. Volume changes are applied at callback boundaries without smoothing and do not affect saved sessions or WAV exports.

`transport.seek` and `transport.loop` are recognized in every build. On a native macOS build they require active playback; paused playback counts as active. A valid request while stopped returns `audio_error`. Builds without native playback return `audio_unavailable` for valid requests. Seek requires exactly an integer `frame` from 0 through `MAX_FRAME` (9007199254740991). Loop requires exactly a `region` field: `null` clears looping, or an object with exactly integer `start_frame` and `end_frame` fields where `0 <= start_frame < end_frame <= MAX_FRAME`. Missing, extra, non-integer, or out-of-range fields return `invalid_params`.

The native timeline command handoff has one pending slot. A successful seek/loop response means the request was accepted and may still be pending application by the audio callback. Poll `transport.status` until `timeline_command_pending` is `false` before issuing another seek or loop request. Issuing either while the slot is occupied returns `audio_error` and leaves the current request unchanged. The requested loop region is reported as `loop_region`; it can therefore reflect the accepted request before the callback applies it. `timeline_frame` is the current timeline position in frames at the native `sample_rate` (v2 playback requires the session rate to match the device rate). `submitted_frames` remains a monotonic count of submitted DSP output. These fields are separate telemetry and are not guaranteed to describe the same instant.

Once a seek is applied, `timeline_frame` reports its destination while paused, and playback resumes from that position. At or after an enabled loop's exclusive `end_frame`, playback wraps to `start_frame` before rendering the next sample. Seek and wrap clear note voices without chasing notes that began before the destination. They reset continuous oscillator phase and resume audio clips at the corresponding source offset. These discontinuities may click. The initial lead-in before the first loop boundary plays once. Loop settings are temporary transport state and are cleared by stop or restart. The play `seconds` limit is unchanged, including time spent paused. Offline WAV rendering starts at frame zero and ignores live transport loops.

Snapshots always contain `state` and `level`. State is `stopped`, `starting` (a callback transition is pending), `playing`, `paused`, or `error`. Active snapshots also contain `device`, `sample_rate`, `submitted_frames`, `timeline_frame`, `timeline_command_pending`, `loop_region`, `volume`, and `until_stopped`. Callback diagnostics are `callbacks`, `max_render_microseconds`, and `callbacks_over_buffer_budget`. Live VST3 snapshots additionally report `plugin_worker_underruns` and `plugin_parameter_update`, containing `pending` (accepted edits awaiting DSP), `applied_revision` (the latest edit revision acknowledged after a successful block, or null), and `applied_frame` (the beginning frame of that block, or null). Error snapshots contain diagnostic `error` text. `level` is the latest callback peak after monitor volume. Playing/paused states reflect callback observation, not just command dispatch. Poll status to observe transitions and later device errors. Submitted frames count DSP output, not acoustic delivery.

Finite playback ends at either the requested frame count or the wall-time deadline, including pauses, followed by a short bounded buffer drain. The owner checks deadlines without needing status requests. Stop releases the stream before acknowledgment; EOF terminates the engine and releases process resources. The control queue holds at most eight messages and commands time out after ten seconds (live SC start allows sixty seconds for bounded startup/cleanup, and stop allows twenty-five seconds); a timed-out command has an uncertain result, so restart the engine before relying on playback state. Audio callbacks use atomics and never wait on the command queue. Default device selection and host permissions remain platform limitations.

The GUI uses until-stopped playback for eligible built-in note/audio arrangements when advertised, and finite snapshots for other native sessions. Eligible built-in playback and pause permit live note/clip, sound, effects, automation and mixer edits; track/runtime replacement remains stopped. Stop & edit is shown for unsupported live-edit paths and restores selected note/clip focus. Saved non-automated controls for active, non-bypassed VST3 effects remain editable in that state through live parameter edits. Use one editing window: native transport is shared, while Web Audio players belong to individual tabs. Closing a page requests native stop on a best-effort basis; until-stopped playback has no deadline fallback, so Stop or engine shutdown releases it if that request fails. Browser continuous playback supports schema-v1 sessions only; isolated WAV note preview supports the sequenced instruments described above. V4 sessions require native audio for playback, including sessions without plugins; sessions containing VST3 effects also require `vst3-live`. The browser never hosts native effects itself; note previews use engine-rendered WAVs with the saved built-in effects.

### Live runtime transport (macOS arm64)

`capabilities.supercollider_live_transport.implemented` and `capabilities.csound_live_transport.implemented` identify native build support for the live source path. SC sources require an installed absolute `DAW_SCSYNTH` and project capture UGen/queue library. Csound sources require the Csound 7 double-sample library and the fixed queue bridge built by `native/csound/build_queue.py`; `DAW_CSOUND_QUEUE_LIBRARY`, `DAW_CSOUND_STREAM_WORKER`, and `DAW_CSOUND_PYTHON` optionally select absolute files. Capability flags do not prove that runtime libraries or an output device are available. Live mode requires a 48 kHz default output device, schema v6 SC and/or schema v7 Csound sources, at most ten seconds, and gain-only effect chains. Built-in tracks can accompany runtime sources. Session replace/load still prepares and validates saved sources before committing the model; live transport start creates fresh owned producers and does not reuse prepared PCM.

On macOS arm64 native-audio builds, `csound_live_transport.live_control_edits` indicates support for eligible Csound scalar edits, and `max_pending_controls` reports the shared limit of eight accepted, unacknowledged SC/Csound updates. These controls require an active live transport. `gui_bridge.csound_sources` separately reports GUI Csound authoring/import support.

```json
{"protocol_version":1,"id":"live","method":"transport.play","params":{"seconds":1,"volume":0,"source_mode":"live"}}
```

Start retains owned source producers/queues and mixes their live streams with built-in tracks and saved gain effects/automation. SC workers capture/mute stereo buses 0/1. A response with `startup:"prefilled"` means the fixed callback queue contains source frames; `state:"starting"` remains possible until the hardware callback runs. Poll until `playing` and advancing `submitted_frames` for callback observation. Snapshots include `source_mode:"live"`, `runtime:"supercollider"`, `"csound"`, `"puredata"`, or `"mixed"`, `source.owned_pids`, `source.runtime_sources`, `source.source_processes` (track ID, runtime and PID), `live_source_underruns`, `callback_signal_peak` before monitor volume and `callback_source_digest`. Monitor volume 0 permits silent callback checks without muting the measured source signal. Independent SC server clocks are not sample-synchronized. Callback processing uses fixed queues and does not call foreign runtimes.

Stop destroys the native stream, finishes the owner worker and reaps servers before acknowledging `state:"stopped"` and `resources_released:true`. Natural completion retains the final snapshot, including `source` frame/digest/RMS/output-bus/release evidence described for the saved-session CLI. Full natural completion should match source and callback frame counts/fingerprints. Explicit stop may leave more produced than submitted frames because of queue prefill; it does not claim those frames reached the device. EOF waits for owned transport cleanup. Abrupt engine process termination cannot guarantee cleanup.

Worker/device failures stop live transport. Later `transport.status` returns a stopped snapshot with `error:{"code":"runtime_error","message":"..."}`; `resources_released` indicates whether owned cleanup completed (false on a worker stop timeout). This is a successful status response containing an asynchronous failure, distinct from an immediate command response with `ok:false` and `audio_error`. Invalid mode or live duration above ten seconds returns `invalid_params` without changing session/revision or starting children. Starting while active fails and preserves the running stream.

Pause/resume, seek and loop commands currently return `audio_error` while live runtime playback is active and leave it running. Volume changes remain supported. `source.set_control` supports eligible saved SC controls and eligible saved Csound scalar channels. SC values remain finite float32 arrays matching the inspected span. Csound accepts exactly one finite float64 value for an existing declared scalar input channel that has no saved points. Pause/timeline semantics, sustained control latency/drift, foreign effects in this mode and longer playback remain separate tickets. `supercollider_sources` and `csound_sources` describe prepared source paths; `supercollider_live_transport` and `csound_live_transport` describe optional explicit live mode. Acoustic delivery remains unverified.

### Live saved source controls

`source.set_control` requires exactly `expected_revision`, `track_id`, `control_name`, and `values`. It edits an already saved named control on an active live SC or Csound source. SC values are finite float32 arrays matching the inspected native span; known initialization-rate controls remain ineligible for live edits. Csound requires `values` to contain exactly one finite JSON number, and the named channel must exist as a declared scalar input. Csound controls with any saved points, including frame-zero points, are automated and cannot be edited live. Stale revisions return `revision_conflict`; missing targets, invalid shapes, nonfinite values and automated targets return `invalid_params`. Stopped/prepared playback and a full shared update queue return `audio_error`; unsupported builds return `audio_unavailable`. Rejected commands preserve the saved model, revision and running DSP.

```jsonl
{"protocol_version":1,"id":"edit","method":"source.set_control","params":{"expected_revision":"1","track_id":"runtime","control_name":"gain","values":[0.02]}}
{"protocol_version":1,"id":"ack","method":"transport.status"}
```

Acceptance queues the change and commits the saved base/revision together, returning `{revision,session,queued:true}`. At most eight unacknowledged changes are allowed across SC and Csound, advertised by each relevant live transport capability's `max_pending_controls`; `live_control_edits` identifies build support. No NRT preparation runs on an edit. The prepared source cache is invalidated, so a subsequent render/prepared playback regenerates audio from the edited base. Save persists the base; reload performs ordinary runtime validation/preparation.

For SC, the source owner sends `/n_setn`, obtains exact float32 `/s_getn` readback, and acknowledges a later captured source block after publishing it into the native callback queue. For Csound, one Rust in-flight command is sent through a bounded 4096-byte regular-file mailbox; the owned child performs channel set and exact native readback between DSP blocks. The child publishes its native acknowledgment after the changed block enters its source queue. Rust reports acknowledgment after mixing and publishing that block to the callback ring. `applied_frame` is the end of that block (`min(block_start + 64, effective_source_duration)`); `callback_observed:true` means the callback timeline has reached that frame. Status includes `source_control_update:{pending,applied_revision,applied_frame,callback_observed}`. Network polling for SC is bounded/nonblocking; Csound uses bounded file IPC on the owner thread. The hardware callback does no IPC. These fields measure submission, readback and callback observation, not acoustic delivery or arbitrary program audio response.

Queue acceptance is not delivery acknowledgment. Poll for the accepted revision and callback observation before treating it as delivered. A stopped source, bad acknowledgment, readback mismatch or two-second reply timeout fails transport asynchronously using the existing structured status error. The accepted saved base remains committed if asynchronous delivery fails. Stop or finite playback completion may cancel pending delivery; its final snapshot retains the observed watermark and outstanding count. Csound's live guarantee covers declared scalar channel set/readback and block publication; it does not promise initialization-rate behavior or that arbitrary CSD output audibly responds. Sustained latency, clock drift and acoustic delivery remain unverified.

### Live Csound saved scalar controls

```jsonl
{"protocol_version":1,"id":"cs-edit","method":"source.set_control","params":{"expected_revision":"1","track_id":"csound-tone","control_name":"amplitude","values":[0.04]}}
{"protocol_version":1,"id":"cs-ack","method":"transport.status"}
```

The same `source.set_control` command accepts one finite number in `values` for an existing declared Csound scalar input channel with no saved points. Rust updates the saved base and revision and invalidates prepared PCM only after revision/target/shape validation and queue acceptance. The live worker does not run offline preparation for the edit. One bounded in-flight command is sent to the owned Csound child, which performs the set and exact native readback between blocks. The revision is acknowledged only after a block using the changed value is published to the Rust callback ring. `applied_frame` reports that block's ending frame, capped at the shorter of saved source duration and requested playback; callback observation follows when the native timeline reaches it. The global limit of eight accepted, unacknowledged edits is shared with SC updates.

Unknown channels, nonscalar channels, malformed/nonfinite values, stale revisions and any saved points are rejected without changing the saved model or DSP. Prepared/stopped playback and a full queue return `audio_error`. A bad acknowledgment, producer failure or two-second timeout stops live transport asynchronously; the accepted saved base remains in the session and can be saved. Stop/EOF cancel commands still pending. These semantics verify named channel set/readback and block publication, not initialization-rate effects or arbitrary CSD audio response.

### GUI session editing


The browser accepts schema-v6 continuous sine/SuperCollider sessions and supported schema-v7 continuous sine/SuperCollider/Csound sessions, all with empty clips and gain-only effects. Rust validates and prepares imported or applied sessions before commit. `capabilities.gui_bridge.csound_sources` reports whether the running bridge exposes the Csound editor path. The GUI's Add Csound action uploads embedded UTF-8 CSD text up to 60 KiB, with a 1 ms–10 s duration and initial gain 0.5. It upgrades a supported v1, v4 or v6 session to v7 explicitly, or appends a source to a v7 session. The editor can add named scalar control bases; it does not parse the CSD to discover channels. Apply is the authoritative Rust check: compilation, declared channel checks and source preparation must succeed before the new draft is applied; failure preserves the current applied session. Notes, audio clips and effects other than gain remain unsupported in v7.

SC source cards show saved program identity, duration, gain, control arrays and automation count. Csound cards show embedded program size, duration, gain and named scalar values/automation. SC controls require successful metadata inspection to be editable; Csound controls need no SC metadata but must be finite scalars with no saved points. Values can be edited while stopped using checked replacement. During live transport, SC arrays and eligible Csound scalar bases use `source.set_control`; status distinguishes queue acceptance, native readback/publication and callback observation. Structural edits, save and render require stopped playback. Browser arrangement playback remains native; browser audio supports the legacy v1 sine player and isolated WAV note previews described above.

The authenticated loopback bridge adds POST `/api/source/inspect` with exactly `{synthdef_hex}` and POST `/api/source/control` with the exact live-control params above. Neither endpoint accepts paths. POST `/api/session` accepts optional canonical uint64 `expected_revision`; updated browser writes supply it when `/api/capabilities.gui_bridge.checked_replacement` is true. The HTTP capability response also adds `gui_bridge.supercollider_sources` and `gui_bridge.csound_sources`; these flags describe the running Python bridge, not the Rust protocol. A refreshed page on an older running bridge retains its legacy replacement payload until that server is restarted. Existing token, exact host/origin, body-size and temporary download restrictions remain. An import failure preserves the previous project; rejected live edits reconcile the browser with the authoritative session and display an error. The browser may have a locally edited input before queue acceptance; that is not a saved or audible change until the corresponding acknowledgment.

The GUI edits schema-v1/v4 continuous sine sessions, supported v6/v7 continuous sine/source sessions, sine/audio arrangements in v2/v3 and equivalent v4 shapes, and schema-v9/v10/v11 sine/drumkit/synth/audio arrangements, with saved mixer controls in v10/v11 and gain/lowpass/delay in v11; sequenced VST3, AU and Pd imports remain outside this GUI scope. The HTTP upload guard accepts represented session families, while Rust performs full authoritative validation/preparation. Unsupported shapes are rejected without changing the active session.

The note timeline/piano roll uses checked `session.replace` for stopped structural edits. MIDI pitch is an explicit authoring conversion to saved Hz; untouched Hz and integer frames remain exact. Update patches changed fields only; tempo changes the grid without retiming saved notes. Exact float64 JSON parsing preserves pitch bits through replacement/save/load. The browser’s Quantize clip action moves all selected-clip note starts to the nearest clip-relative Grid line (¼, ½ or 1 beat), rounding exact midpoints later. Durations, Hz, velocity, IDs and other saved fields remain exact. No snap disables quantize; pending drafts/takes block it. Clip-end, monophonic/voice-limit or checked engine failures reject the entire operation. Accepted changes use one bounded snapshot Undo entry; aligned/empty clips are no-ops. It reuses session.replace or the eligible session.update_live path, with no new protocol command or schema. Drum rolls show Kick/Snare/Hat; selected notes show saved Hz/cents. Clip insertion has its own beat field. Failed edits retain explanations through polling; explicit Reload engine state discards local edits and resets history. Undo/redo stores bounded session snapshots in the browser and moves history only after a checked engine replacement succeeds; import/refresh resets history. Unapplied device/effect drafts and locked playback disable history traversal; eligible built-in live arrangement edits also support Undo/Redo.

The loopback bridge additionally exposes authenticated `POST /api/transport/seek` with exactly `{frame}` and `POST /api/transport/loop` with exactly `{region}` using the native parameter contracts above. Bounds, integer types (excluding booleans), region order and extra fields are checked before forwarding. Responses are transport snapshots; errors use HTTP 422. The GUI serializes native commands, invalidates stale polls, and respects `timeline_command_pending` before another timeline command. Live runtime transport remains outside these seek/loop controls.

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

## Timing and terminology

A frame is one sample instant across all channels. Saved positions/lengths are integer session-rate frames; clips, gates and loops use half-open intervals `[start,end)`. A note gate ends at its release trigger; its voice may continue through a release tail bounded by the clip. Timeline position can jump/loop; output position counts submitted frames monotonically. Listening volume is transport state, independent of saved mix levels and exports.

A beat is a quarter note. Authoring uses 960 ticks per beat and constant `tempo_milli_bpm`. Convert absolute endpoints separately with checked integer arithmetic, ties upward:

```text
n = ticks * sample_rate * 60000
d = 960 * tempo_milli_bpm
frame = (2*n + d) // (2*d)
```

Subtract rounded endpoints for the gate length; do not accumulate rounded beat lengths. Reject collapsed or overflowing intervals. Frames/ticks are bounded to 9007199254740991; Rust products use checked u128 arithmetic. Ticks are authoring inputs, not another saved timing source. Changing tempo changes the grid without moving saved frames. Sample-rate replacement never automatically rescales positions.

Events at a block's beginning apply before its first sample; events at its excluded end wait for the next block. On a discontinuity, clear voices/reset state; free expired tails/clip-cut voices, apply note-offs and automation, then start note-ons. Sequenced identities order by track/clip/note UTF-8 bytes. Seek/wrap does not chase earlier notes; PCM resumes from its source offset. Relevant behavior is tested in `tests/timeline.rs`, `tests/seek_loop.rs` and `tests/audio_clips.rs`; `examples/check_timeline_contract.py` checks reference arithmetic only.

## Session schema v2

Schema v2 implements the note subset of the frame-based timeline contract: required root `tempo_milli_bpm`, required per-track `mode` and `clips`, and `kind: "notes"` clips containing frame-positioned note gates. See [the runnable arpeggio](../examples/sessions/arpeggio.json) for the full shape. Schema-v1 fields and behavior remain unchanged, and no load silently upgrades them.

`capabilities.supported_session_schema_versions` includes `[1,2,3,4,5,6,7,8,9,10,11,12]`; the legacy `session_schema_version` remains 1. `capabilities.sequencing` describes limits and envelope duration. Frames are integers 0–9007199254740991, gates and clips have positive length, notes fit wholly inside their clip, and note frequencies must be below session Nyquist. Tempo is 20000–300000 milli-BPM. Clip/note IDs follow the existing byte limits and are unique within track/clip respectively. Unknown fields, missing required fields, and explicit null are rejected. Maximum counts are 1024 clips and 16384 notes; 64 simultaneous voices include continuous tracks and release tails. The existing 1 MiB file/request bound also applies.

Continuous v2 tracks require empty clips and retain v1 oscillator behavior. Sequenced tracks are silent without notes. Notes override the device frequency, multiply velocity by track gain, start at phase zero, and use 5 ms attack/release envelopes. Tails end at clip boundaries. Rendering starts at frame zero, ignores tempo for already positioned notes, and allows up to 180 seconds for built-in/PCM sessions with supported built-in effects. Native playback requires matching session/device rates for all v2 sessions; a mismatch reports `audio_error`. Browser continuous audition is unavailable for v2; stopped sequenced-note previews use the engine-rendered WAV path. Audio clips and native seek/loop controls are supported as described above; effect automation requires schema v3.

`session.replace`, `session.load`, and whole-track batches validate notes and peak polyphony before stopping playback or committing a new revision. `session.save` preserves the selected schema. The explicit upgrade example maps v1 tracks to continuous v2 tracks without changing their sound. In-place sample-rate editing is not exposed: a full replacement supplies a new arrangement and does not rescale any positions automatically.

The separate `timeline-contract.json` fixtures remain design-only reference cases and are not loadable sessions.

## Musical built-ins in schema v9

Schema 9 adds `drumkit` and `synth` devices to the existing model. They require `mode:"sequenced"`, note clips, and the existing required `effects` array (which may be empty). Older schemas reject these kinds. Existing schemas load/save unchanged; there is no automatic migration. Unknown/missing fields and explicit null still reject. Schema 9 retains earlier supported devices/effects in the engine; the GUI represents sine/drumkit/synth/audio arrangements with gain effects.

```json
{"kind":"drumkit","kit_id":"factory-v1","gain":0.45}
{"kind":"synth","waveform":"saw","gain":0.13,"attack_ms":8,"release_ms":90,"cutoff_hz":2800}
```

Device gain is finite 0–1. Drumkit accepts only `factory-v1`, a project-authored deterministic bank synthesized with fixed-seed noise at the session rate during preparation. MIDI 36/38/42 frequencies trigger kick/snare/closed hat with a relative Hz tolerance of 1e-6; other pitches reject the candidate session before commit. Samples last 500/250/120 ms, ignore note gate duration, and truncate at the half-open clip end. Notes still require positive gates fitting inside the clip. Velocity multiplies device gain. Golden PCM16 hashes at 44.1/48k protect the versioned bank.

Synth waveform is exactly `saw` or `square`, using PolyBLEP band-limited oscillators. Attack is finite 1–2000 ms; release is finite 0–2000 ms, with at least one frame internally. Envelope durations round once to session frames. A linear attack and release scale velocity/gain; early note-off releases from the current attack level. Cutoff is finite 20–20000 Hz and strictly below session Nyquist, with an independent one-pole low-pass per voice. There is no resonance or decay/sustain. Eligible [live arrangement updates](#live-arrangement-updates) preserve ongoing voice phase/filter state and crossfade changes; track/device-kind replacement requires Stop.

Independent voices share the existing limit of 64, including synth release and drum sample tails. Preparation validates peak lifetime overlap; overflow rejects rather than stealing voices. Clip ends truncate tails. Seek/loop/Stop clear voice/filter state; no note chasing, same-pitch replacement or tail carry is introduced. Discontinuities may click. Prepared offline/native rendering uses the same scheduler and built-in DSP; native timeline sessions require matching device/session rates. No sample generation/allocation occurs in callback rendering. Built-in/PCM WAV export with gain, lowpass and delay supports 0.001–180 seconds; explicit native until-stopped playback is separate.

The [musical fixture](../examples/sessions/musical-demo.json) has sixteen bars of synth lead/bass and factory drums. The [workflow check](../examples/musical_workflow_demo.py) verifies exact session persistence, rejected/stale edits, undo-style replacement and deterministic unclipped 32-second output. The [muted native check](../examples/musical_native_demo.py) verifies looping beyond the finite deadline, pause/seek/resume, restart and EOF stream release. These checks do not establish acoustic quality.

## Song-length offline export

Built-in sine/synth/drumkit/PCM and schema-12 Pd instrument sessions with gain, lowpass and delay support 0.001–180 seconds, regardless of saved schema. Prepared runtime sessions keep the prior 60-second export policy; program/source payload and live runtime limits remain ten seconds. Plugin exports remain ten seconds. Finite native playback still uses 0.001–60 seconds; until-stopped playback is a separate mode.

`capabilities.render` publishes `max_seconds`, `builtin_max_seconds`, `runtime_max_seconds`, `plugin_max_seconds` and `native_max_seconds`, with `song_devices`/`song_effects` identifying eligible built-in compositions. The GUI derives the export maximum from this metadata and the current project. Both direct/prepared Rust export APIs and JSONL validate duration before publication/preparation. WAVs stream through 256-frame render blocks and bounded buffered output; no whole-song DSP buffer is allocated. HTTP still returns a complete bounded WAV response. The [three-minute song check](../examples/song_workflow_demo.py) verifies mixed-song ZIP reopen, matching exports, audible ending and safe 181-second rejection.

## Basic mixer in schema v10

Schema 10 adds an optional saved `mixer` object on each track. This first mixer format accepts only built-in sine/synth/drumkit/audio devices and gain effects; foreign plugin/runtime devices and effects reject before preparation. Existing schemas keep their behavior and reject mixer fields. GUI mixer edits explicitly upgrade a legacy supported built-in project to 10 and preserve 11; ordinary load/save does not upgrade it. Missing mixer objects use unity gain, centered balance, no mute and no solo. An explicit null, unknown field or incomplete mixer object rejects.

```json
{"mixer":{"gain":1.0,"pan":0.0,"mute":false,"solo":false}}
```

Mixer gain is finite 0–2, independent of instrument/clip gain. Pan is finite -1–1 and performs stereo balance: center leaves both channels unchanged; positive pan attenuates the left by `cos(pan * pi / 2)` while retaining the right; negative pan mirrors this. Hard left/right silences the opposite channel. It does not sum stereo audio into the selected side. Multiple solos are inclusive; when any track is soloed, non-soloed tracks are silent. Mute always wins, including a muted solo track. Mixer processing follows track effects and precedes the final sum/clamp, identically in offline and prepared native playback. Listening volume affects only monitoring.

Gain is linear amplitude: 2 is approximately +6.02 dB. Step automation selects the current gain for its targeted effect, then the processed track signal multiplies the independent mixer gain; automation does not write mixer settings. Resetting authored mixer values to defaults retains schema 10 and the saved object. Missing objects stay omitted on serialization.

Native transport status provides `mixer_meters` when built-in metering is available:

```json
{"mixer_meters":{"tracks":[{"id":"bass","peak":[0.2,0.1],"clipped":false}],"master":{"peak":[0.2,0.1],"clipped":false}}}
```

Peaks are absolute linear stereo levels after track mixing and before output clamp/listening volume, including master summed levels. `clipped` means a channel reached or exceeded 1. Meter storage/publication is bounded and preallocated; JSON serialization occurs off the callback. Built-in/Pd worker meters report peaks of consumed 256-frame blocks, with up to 255 frames of lookahead inside the block. The UI reports these recent peaks, not RMS or loudness, and clears them when transport is stopped. Unsupported playback paths may report null metering. Meter polling does not apply edits or change revisions. Mixer edits use checked replacement and snapshot undo, including [live arrangement updates](#live-arrangement-updates) on eligible native playback.

Meters describe the latest completed callback buffer, not peaks accumulated between status polls; short transients may be missed by polling. Multiple status readers do not consume each other's peaks. Indicators do not latch; pause/completion reset supported native peaks to zero. Export reports actual overs as `clipped_frames` (`abs(sample) > 1`), exposes `X-Clipped-Frames` through the bridge and displays a GUI warning; monitor volume is excluded from export.

The [HTTP acceptance test](../gui/test_mixer_workflow.py) checks pan/solo/mute in rendered audio, checked failure preservation and portable mixed-project ZIP reopen. The [muted native check](../examples/mixer_native_demo.py) checks meters independently of monitor volume and native lifecycle.

## Built-in processing in schema v11

Schema 11 supports built-in sine/synth/drumkit/audio tracks, the schema-10 mixer, gain effects/step automation, and two additional serial stereo effects. Older schemas reject these new effect kinds. Adding a lowpass or delay explicitly upgrades a supported built-in project to 11; loading, bypassing, removing effects and editing mixer/instruments do not downgrade it. Runtime sources and foreign effects reject in this format.

```json
{"kind":"lowpass","id":"tone","cutoff_hz":4000.0,"bypass":false}
{"kind":"delay","id":"echo","time_ms":250.0,"feedback":0.3,"mix":0.25,"bypass":false}
```

All fields are required; unknown fields, null, nonfinite and out-of-range values reject transactionally, even when bypassed. Lowpass cutoff is 20–20000 Hz and strictly below session Nyquist. It uses an independent one-pole filter per stereo channel. Delay time is 1–2000 ms, rounded once to the nearest frame; feedback is 0–0.95 and mix is 0–1. Each channel has its own feedback line, storing `dry + delayed * feedback` and outputting `dry * (1 - mix) + delayed * mix`. Effect order is saved array order, before the mixer; gain automation stays independent.

Coefficients and stereo delay buffers are prepared before playback. Aggregate delay storage is limited to 64 MiB per session, including bypassed effects. Processing and timeline reset perform no allocation or file/runtime operations. Seek and loop wrap clear filter history and delay tails; pause preserves them. Bypass passes through the input. Export starts with clear state and retains the exact requested duration: effects may ring into silence within that duration, but no extra tail is appended. Discontinuities can click. Only gain effects support saved automation in this slice; eligible built-in effect edits also support [live arrangement updates](#live-arrangement-updates).

`capabilities.effect_metadata` publishes algorithms, parameter bounds/defaults and automation support. `effects.kinds`, `render.song_effects` and `timeline_transport.until_stopped_effects` include gain/lowpass/delay. Built-in processed projects retain 180-second WAV export, until-stopped native playback and portable ZIP support. The browser exposes numeric controls, bypass, removal and three presets for each new effect. The mixer presents vertical faders, pan sliders, M/S and stereo meters with exact numeric fallback; master remains a pre-monitor output meter.

Run [the processing workflow](../examples/processing_workflow_demo.py) with `--song` for a three-minute mixed instrument/PCM project, fresh-engine ZIP reopen and byte-identical export; add `--native` for muted transport checks. [HTTP regressions](../gui/test_processing_workflow.py) exercise real control changes, bypass, checked undo snapshots, validation/stale failures, independent mixer/notes/automation and ZIP persistence.

## Sequenced Pure Data instrument in schema v12

Schema 12 adds `pd_instrument` to the built-in/PCM arrangement family with schema-11 mixer, gain automation, audio fades, lowpass and delay. It requires a 48 kHz session, `mode:"sequenced"` and notes clips. Older schemas reject this kind; adding/loading its preset explicitly upgrades the arrangement to 12. Other foreign runtime sources/effects remain separate formats.

The [portable preset package](../examples/devices/pd-sine.json) stores the complete device JSON. It contains `kind:"pd_instrument"`, `program` (the exact embedded [Filtered Sine patch](../native/puredata/instrument.pd)), `abstractions:[]`, finite `gain` from 0–1 and one control:

```json
{"name":"cutoff","type":"float","default":4000,"min":100,"max":12000,"value":4000}
```

Metadata is fixed; only the bounded value and gain vary. Unknown fields, alternate programs, abstractions, missing/invalid control metadata and unavailable libpd reject before session/revision commit. CRLF program text normalizes to LF for checking. This first device deliberately supports one vanilla preset rather than arbitrary external objects or a patch editor. The process loads `DAW_LIBPD_LIBRARY`, an existing absolute libpd library with multi-instance support. Each instrument owns a private patch instance/tree, prepared before rendering or playback. Session JSON and project ZIP embed its program and controls; the runtime library must be installed on the reopening machine.

The scheduler sends `$0-frequency` in Hz, `$0-velocity` in 0–1 and `$0-gate` on/off. A note must last at least 64 frames. Absolute note on/off positions round up to the next 64-frame Pd tick; saved positions remain exact. Gates may touch but cannot overlap across any clips on the track after quantization. Note off is delivered before note on at a shared tick. The preset uses `osc~`, `lop~`, gate/velocity multiplication and stereo output; cutoff targets `$0-cutoff`. Note-on resets oscillator/filter phase for repeatable phrase playback. Source gain precedes the ordinary saved track effects and mixer.

Export runs actual note-scheduled libpd DSP in bounded blocks and retains the ordinary 180-second arrangement policy. Native transport runs the same DSP on an owned rendering worker feeding a fixed ring; the hardware callback only consumes prepared ring frames and publishes meters. It supports muted/until-stopped playback, pause/resume, seek, loop and Stop. Seek/loop reset oscillator/filter/gate through `$0-reset`, clear effect history, and wait for the next absolute Pd tick; notes already sounding at the destination are not chased. Seek/loop commands can briefly emit silence while the worker primes a fresh generation; `pd_transport_wait_buffers` counts those buffers separately from steady-state starvation. Pause preserves DSP state. There is no frozen PCM fallback, Pd control automation, polyphony, live control editing or cross-platform bit identity promise. Structural/control edits remain checked stopped replacements with undo.

`capabilities.pd_instrument` advertises schema/rate, preset, monophony, event timing, runtime configuration, worker ownership and reset policy. During native playback, transport reports `runtime:"puredata_instrument"` and `pd_worker_underruns`. Pd mixer meters report peaks of consumed 256-frame worker blocks, with up to 255 frames of lookahead within a consumed block; the capabilities describe this scope.

In the browser, choose **Pd · Filtered Sine** and **Add note track**, or **Load Pd preset** using the package JSON. The existing piano roll sequences notes. The track card exposes gain/cutoff, **Save Pd preset** and the embedded program. Portable ZIP save/reopen preserves the device and imported audio alongside the arrangement.

The [workflow demonstration](../examples/pd_instrument_workflow_demo.py) isolates scheduled 440/660 Hz notes and a silent note-off gap, then checks a five-track built-in/Pd/PCM song with processing, independent note/control edits, checked snapshot undo, fresh-engine ZIP reopen and byte-identical unclipped WAVs. Run with `--song` for the full 180-second mixed arrangement, or add `--native` for muted loop/seek/pause/resume/Stop. [HTTP regressions](../gui/test_pd_instrument_workflow.py) also check invalid programs/controls/overlap, stale edits and missing-runtime replacement/ZIP failure preservation; the [direct runtime proof](../native/puredata/test_instrument.py) checks note receivers, cutoff, velocity and deterministic reset against real libpd blocks.

## PCM audio clips in schema v2

`devices` also lists `audio`, with gain metadata in `device_metadata.audio`. It requires a sequenced track in schema v2 or later and audio clips shaped as `{ "kind":"audio", "id":"take", "start_frame":0, "length_frames":48000, "source_path":"assets/take.wav", "source_offset_frames":0, "gain":1 }`. Gain is finite 0–1. In v2, note clips remain valid only on sine tracks; schema 9 also permits drumkit/synth notes. The combined note/audio/continuous voice limit is 64; the 1024 clip limit includes both clip kinds.

Audio clips optionally save `fade_in_frames` and `fade_out_frames` in supported audio-clip schemas 2–12. Absent fields mean zero; zero fields are omitted on serialization. Values are unsigned integers, each at most `length_frames`, with sum at most that length. Fades multiply the source before effects/mixer and use clip-relative timeline frames. For N >= 2, the first N samples use `i/(N-1)` and the last N use `(length_frames-1-i)/(N-1)`; a one-frame fade mutes its edge sample. Seek/loop and uneven render blocks produce the same factor. Old clips remain unchanged. `capabilities.audio_clips.fades` advertises the fields and linear curve.

`capabilities.audio_clips` advertises supported PCM formats/channels, rate matching, unique-file and byte limits, project-relative paths, and the lack of save-as outside the project. The [audio-project contract](AUDIO-PROJECTS.md#headless-asset-contract) defines decoding, preparation and path behavior. A successful load sets the project root to the session file's canonical parent; replace/edit inherit the active root (initially process working directory). Runtime roots never appear in saved JSON. Audio-session save requires a destination in the same root and does not copy assets.

Mutation validates final candidate assets before committing. A failed asset load leaves the session and revision unchanged. Render prepares assets before output creation; native preparation failures use `audio_error`. Assets are reloaded for each play/render snapshot. No disk I/O occurs during callback rendering.

## GUI WAV import and portable projects

The loopback bridge runs its engine with a private temporary asset root. JSON session replacement accepts audio references only to already registered owned assets. It never loads arbitrary repository/user WAV paths over HTTP. **Import WAV** uploads raw PCM into a new audio lane at the chosen insertion beat. Selected audio clips expose move/length/source-offset/gain, fade-in/out frames, duplication/deletion and checked snapshot undo. Missing/out-of-range assets and failed/stale imports preserve the applied session/revision. Native until-stopped playback includes prepared PCM; foreign limits and WAV export limits are unchanged.

`gui_bridge.audio_projects` and `audio_project_limits` advertise this bridge feature. Authenticated `POST /api/audio/import` accepts raw WAV bytes with a bounded `X-DAW-Metadata` header containing exactly `{expected_revision,track_id,clip_id,start_frame}`; it returns `{session,revision,asset}`. WAVs are integer PCM16/24/32, mono/stereo, matching-rate and at most 32 MiB. `GET /api/project` downloads ZIP bytes containing `session.json` and referenced assets. `POST /api/project` accepts a bounded ZIP plus metadata `{expected_revision}` and returns `{session,revision}` after validated checked replacement. Project ZIPs are at most 128 MiB encoded/expanded, with 128 assets and the existing 128 MiB decoded audio budget; session JSON stays at 1 MiB. Paths, entry counts, CRC, duplicate/symlink/encryption and expansion rules are checked before commit.

With audio present, GUI save downloads a portable project ZIP; Load session accepts that ZIP in a fresh server. Asset-free sessions keep JSON-only save. Successful ZIP import resets browser history, remaps paths to fresh owned files and reclaims old assets after commit. Failed imports retain the old project/assets. Imported files are temporary until saved in a ZIP; server shutdown releases the engine before deleting its private folder. The [GUI audio-project contract](AUDIO-PROJECTS.md) gives exact headers, ownership and transaction/budget details. The [workflow demo](../examples/audio_project_workflow_demo.py) checks mixed PCM/instrument trim/copy, fresh-server ZIP reopen and byte-identical WAV export, with optional muted native transport checks.

## Serial effects in schema v3

V3 retains v2 timeline/source fields and requires `effects` on every track, including an empty array. V1/v2 reject that field. Each effect has exactly `{"kind":"gain","id":"trim","gain":0.5,"bypass":false}`. IDs are unique within the track, nonempty, and at most 128 UTF-8 bytes. Gain is finite and 0–4 inclusive. Bypass is required and does not exempt invalid settings from validation. There are at most 16 effects per track.

The source's voices are summed without clipping, then effects run in their array order on that track's stereo audio. Processed tracks are summed in serialized order and clipped once at the master. Gain effects have zero latency. V3's per-track grouping can change floating-point rounding relative to v2 even with empty chains; v1/v2 retain their original path.

`capabilities.effects` reports the supported kind, limits, routing, bypass, latency, and supported automation. Use revision-checked full replacement for gain-effect edits. Existing `set_parameter` addresses the source device only. Effect changes validate before committing, stop native playback, and advance the revision once. Saved sessions preserve chain order, IDs, gain, and bypass. Live gain-effect mutation is not supported; VST3 parameter base edits during native playback are described below. Saved automation lanes are described below.

V3 native playback requires matching device/session rates. The browser edits supported v3 note/audio arrangements and gain effects through checked replacements. See the [runnable example](../examples/sessions/gain-chain.json).

## Saved effect gain automation

Schema-v3 tracks may include `automation`; omission preserves the previous v3 shape. Explicit null is rejected. V1/v2 reject the field. A lane is `{"effect_id":"trim","parameter":"gain","interpolation":"step","points":[{"frame":12000,"value":0.5}]}`. Each field is required and unknown fields are rejected. The target must be an existing effect in that track. A target may have one lane, with at most 16 lanes per track.

Points must be nonempty, strictly increasing by integer frame, and within the existing frame limit. Values are finite 0–4 inclusive. There are at most 16384 points across the session. Before the first point use the saved effect gain; at each point apply its value before that frame's sample; afterward hold the most recent value. Bypass leaves audio unchanged while its lane remains validated and persisted. Step changes can click.

The browser edits these gain lanes with exact absolute frame/value rows: add, update and delete use revision-checked replacement or eligible live arrangement updates and create undo snapshots. Deleting the final point removes the lane, restoring the base-only gain effect. Unrelated frame/pitch/mixer data and other typed automation rows survive an edit. Failed/stale replacement leaves the applied project unchanged and retains the local input/error. Smooth ramps and plugin/source control automation editing remain separate work.

Seek and wrap restore the last value at or before the destination, or the base gain before the first point. They use prepared data and perform no file loading. Automation is part of the snapshot shared by offline and native rendering. Use revision-checked full replacement while stopped or session.update_live on eligible playback to edit lanes. Invalid edits preserve session/revision/playback; accepted live edits retain the running transport.

`capabilities.automation` reports parameters, interpolation, point/lane limits, and build-dependent `live_edits` for eligible native arrangement updates. `capabilities.effects.automation` is true. [the example](../examples/sessions/gain-automation.json) is loadable and playable. The GUI edits step gain points in supported arrangements and removes lanes targeting an effect when that effect is removed; loaded continuous-session lanes are preserved.

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

New servers start with an empty 48 kHz session. Each render starts all sine oscillators at phase zero, sums tracks, duplicates mono into stereo, and hard-clips the sum to [-1, 1] before PCM16 conversion. `clipped_frames` counts frames whose sum exceeded that range. Output length is `round(seconds * sample_rate)` frames. Built-in WAV export durations are 0.001–180 seconds. No fades are applied, so abrupt endpoints can click. Repeat renders are deterministic in the same build/platform; cross-platform bit identity is not promised.

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
| `runtime_error` | SuperCollider/Csound input, executable, owned child or output validation/publication failure |
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

The macOS probe in [native/vst3](../native/vst3/README.md) has its own child-process scanner output. The original probes add no JSONL methods and remain separate diagnostics. Scripted offline session processing is documented below. `capabilities.native_vst3` reports experimental status, in-process isolation, queue size, rate, and unsupported seek/loop/editors/instruments. `capabilities.plugin_hosting` reports whether experimental live hosting was compiled; `capabilities.offline_vst3.implemented` describes offline support only.

## Schema-v4 offline VST3 effects

V4 retains v3 timeline, gain chains, and gain lanes. It adds this effect shape in track `effects` (all fields required; unknown fields and null rejected):

```json
{"kind":"vst3","id":"echo","bypass":false,"bundle_path":"/absolute/Effect.vst3","class_id":"5653544671456876616C68616C6C6166","state_hex":"","controller_state_hex":"","parameters":[{"id":48,"value":0.2,"points":[{"frame":24000,"value":0.8}]}]}
```

`class_id` is exactly 32 uppercase hex characters and selects an Audio Module Class in that bundle. `bundle_path` is an absolute `.vst3` path, at most 4096 UTF-8 bytes; relocation/path resolution is not implemented. No executable or shell command is saved in the session. V1–v3 reject VST3 effects. V4 requires track effects like v3, and requires 48 kHz when any plugin appears, including bypassed plugins.

State strings are even-length hex, at most 64 KiB decoded each; aggregate decoded state is at most 256 KiB per session. Empty state means initialize the plugin and capture its initial state during successful load/replace. Nonempty component/controller blobs are restored; empty controller state is allowed when the plugin has none. Preparation fills omitted empty blobs in the committed response, then validates the captured session and its save/load size. Foreign state validation runs in a child; corrupt/incompatible state fails rather than changing the active session. Saved state is the prepared base state, not a snapshot of the last render's DSP history.

At most 8 VST3 effects exist per session, with at most 64 distinct parameter IDs per effect. Every parameter requires `id`, normalized finite `value` in 0–1, and `points` (empty allowed). Points require `frame` and normalized `value`; frames strictly increase up to `MAX_FRAME`. Plugin and gain automation together have at most 16384 points. Base values are queued at frame zero; a point at zero overrides the base, and points apply in their containing 256-frame block at the indicated offset. Plugin interpolation/smoothing is plugin-specific. Existing gain automation lanes cannot target plugin effects; plugin automation resides inside `parameters`.

Build `--features vst3-offline` on macOS and build the offline worker with `native/vst3/build.py`. Other builds parse/validate v4 statically but return `plugin_error` on load/replace of a plugin session. `capabilities.offline_vst3.implemented` reports compiled offline support, not installed plugin compatibility or worker availability. No new command is introduced: use revision-checked `session.replace`, `session.save`, `session.load`, and `render`.

Supported offline layout is one stereo input/output audio bus, no event buses, float32 at 48 kHz, maximum block 256, and zero latency. Nonzero latency, unsupported parameters/layouts/restarts, missing workers/bundles/CIDs, child crashes, oversized output, or 15-second worker timeout return `plugin_error`. Parameter-value restart notifications use current controller reads; graph/metadata/latency changes are unsupported. GUI VST3 editing is limited to v4 continuous sine sessions (legacy v1 may be explicitly upgraded). Other built-in arrangement and runtime families have their own GUI guards; plugin sessions still require the Rust validation and preparation described above.

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

The original `native/au.probe` AUv2 lifecycle probe remains a separate diagnostic with its own schema-version-1 result envelope and owned process harness. It opens no audio device and is distinct from schema-v5 session processing. See the [AU worker build notes](../native/au/README.md).

## SuperCollider NRT jobs

`capabilities.supercollider_nrt` reports Unix implementation support, whether `DAW_SCSYNTH` names an existing absolute file, limits and `session_device:false`, `native_playback:false`. Configuration does not prove executable permissions, runtime version or installed UGen compatibility; actual launch and rendering are checked per job. Set `DAW_SCSYNTH` before starting the controller. Windows builds recognize the command but explicitly report unavailable rendering.

```jsonl
{"protocol_version":1,"id":"sc-render","method":"supercollider.render","params":{"score_path":"/absolute/score.osc","path":"/absolute/fresh.wav","sample_rate":48000}}
```

Params contain exactly `score_path`, `path`, and integer `sample_rate`. Nonempty UTF-8 paths may be at most 4096 bytes and resolve relative to the controller working directory. Rates are 8000–192000. The result is `{ "path": <requested path>, "frames": <integer>, "sample_rate": <rate>, "channels": 2 }`. The command is synchronous, does not load/change the session or advance its revision, and does not start/stop transport. It has no HTTP GUI route. Wrong JSON params use `invalid_params`; score/executable/worker/output failures use `runtime_error`.

The input is a regular binary score file capped at 1 MiB, snapshotted into a private job directory. Each record is a big-endian 32-bit length and a flat OSC bundle of 16–65516 bytes; at most 16384 records are allowed. Relative timestamps begin at zero, never decrease, and end between 0.001 and ten seconds. Messages use string addresses, zero padding, valid UTF-8 strings and scalar/string/blob type tags; nested bundles, integer command addresses and arrays are unsupported. Nonfinite floating arguments and malformed/trailing data are rejected before launch. Accepted OSC tags are `i`, `f`, `c`, `h`, `d`, `s`, `S`, `b`, `T`, `F`, `N` and `I`; see the [validator](../src/supercollider.rs) and [fixture writer](../native/supercollider/score.py).

The owned Unix child runs `scsynth -N` with two output channels, no input, 64-frame blocks and a private restricted path for file-accessing OSC commands. Supply SynthDefs using `/d_recv`; automatic defaults and external-file assets are not loaded. No hardware audio or realtime networking starts. The process group is terminated on completion/failure, and the direct child is reaped. Timeout is 15 seconds, with 64 KiB each for stdout/stderr. Captured server error diagnostics fail even if the process returns exit status zero. Output must be bounded, stereo PCM16 at the requested rate. Up to 128 trailing block frames are trimmed to `round(last_timestamp * sample_rate)`. All samples are validated before exclusive destination creation; failure leaves no partial destination and never overwrites an existing output. This is process ownership, not a security sandbox or a live callback design.

OSC validation checks framing and scalar/string/blob encoding, plus the SynthDef file header for `/d_recv`. It does not interpret every server command or UGen graph. A successful result proves that a bounded WAV was produced and validated; NRT provides no per-command completion acknowledgements, and commands that fail silently in the runtime cannot all be detected. Runtime completion and control acknowledgments are separate from validating an NRT output WAV.

## SuperCollider program inspection

`supercollider.inspect` is available in every build and does not require `DAW_SCSYNTH`. Params contain exactly `synthdef_hex`, a nonempty even-length hexadecimal string encoding at most 65536 bytes. Uppercase and lowercase hex are accepted. JSON shape or hex errors return `invalid_params`; unsupported/malformed binary programs return `runtime_error`. Inspection launches no child, accesses no files, and leaves the active session, revision and transport unchanged on success or failure. The GUI exposes bounded inspection through `/api/source/inspect`.

The result is `{ "name": "daw_sine_fixture", "controls": [{ "name": "freq", "index": 0, "default_values": [440.0], "initialization_rate": false }, ...], "ugen_count": 4 }`. Defaults retain native float32 precision. Each control has additive `initialization_rate`, true if any slot is recognized as initialization-rate by the inspected Control-family graph; false does not prove arbitrary runtime compatibility. Control records retain the binary name-table order; an array spans from its index to the next greater named index, or the end of the parameter array. Unnamed parameter prefixes are omitted. Names and named indices must be unique.

The accepted subset is SCgf version 2, exactly one definition, and no variants or trailing bytes. Names are nonempty UTF-8 Pascal strings without NUL (at most 255 bytes). Limits are 4096 constants, 256 parameter slots, 64 control names, 1024 UGens, 4096 inputs and 256 outputs per UGen, and 16384 input/output ports in total. Counts cannot be negative. Constants/defaults must be finite. Calculation rates are 0–3; inputs must reference valid constants or outputs of earlier UGens. The recognized Control, AudioControl, TrigControl and LagControl output spans must fit the parameter array.

`capabilities.supercollider_programs` publishes these principal limits and reports `inspection:true`, `runtime_validation:false`, `session_device:true` (saved source support is described separately). Inspection validates encoding and graph references; it does not establish that an installed UGen exists, accepts a particular input/output layout, produces audio, or responds successfully to runtime commands. Use the owned NRT job for actual rendering evidence. Schema-v6 sources use this inspection before owned runtime preparation.

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

Sources feed the existing stereo track chain, including gain automation and supported offline VST3/AU effects, before final master clipping. Native playback requires a matching device rate and supports the existing seek/loop semantics through prepared PCM. With experimental live VST3, source preparation runs on the caller before the DSP worker starts; callbacks consume only the prepared stream. AU remains offline. The prepared path has no interactive SuperCollider DSP or live control mutation. Explicit live transport and saved-control edits are described above; The GUI imports continuous gain-only v6 sine/SuperCollider sessions and edits eligible saved controls; a language interpreter and external-file runtime assets remain unavailable. `session.edit` can change track gain; native control/event edits use revision-checked full replacement and stop the active snapshot. `capabilities.supercollider_sources` distinguishes these capabilities from standalone jobs and program inspection.

Run `DAW_SCSYNTH=/absolute/scsynth python3 examples/supercollider_tracks_demo.py` to create two programs, save/reload, and export through gain chains. Add `--native` with a native-audio build for a bounded silent hardware smoke test; that verifies submitted callback frames, not acoustic delivery.

### Interactive SuperCollider diagnostic

`native/supercollider/live_probe.py` is a separate owned macOS server diagnostic, not a JSONL command or DAW transport adapter. It proves completion/node/control responses and finite private-bus PCM changes, with silent output-bus capture and clean quit. Default source playback uses prepared PCM; explicit live transport/control and GUI access are described above.


## Saved-session live SuperCollider CLI (macOS)

`daw sc-session-play SESSION.json SECONDS [VOLUME]` requires `native-audio`, an installed absolute `DAW_SCSYNTH`, and the project capture UGen/queue library built with `native/supercollider/build_stream.py`. It reads the bounded saved JSON through Rust validation, requires schema v6/48 kHz and at most ten seconds, then owns one isolated loopback server and fresh private queue per SC track. SynthDef names are rewritten privately to avoid capture-name collisions. Source graphs must write stereo buses 0/1; other bus routing is not discovered. A tail synth publishes those buses and replaces them with silence before hardware output. This is caller-trusted foreign code, not a sandbox.

Saved native control bases/frame-zero points are applied before nodes run; later points are scheduled in timestamped OSC bundles. All servers receive a common future start time, but independent server clocks are not sample-synchronized. Rust mixes captured sources with built-in tracks, applies saved source gain/duration and serial gain effects/automation/bypass, clamps the master, and feeds a fixed native callback queue. AU/VST3 effects are rejected before startup. No NRT source preparation occurs in this mode. Callback work remains fixed-buffer processing without IPC or foreign initialization.

Volume defaults to 0.25 and must be 0..1; use 0 for silent verification. Stderr emits `DAW_SC_STREAM_READY` after device stream construction; it is not a DSP acknowledgment. Success prints one final JSON object with `saved_session:true`, `source_preparation:"owned_live_sc"`, `session_transport:false`, source/submitted frame counts, source/callback fingerprints, underruns, pre-monitor peak and release flags. `source` also includes `runtime_sources`, `owned_pids`, `hardware_bus_peaks` and four `rms_quarters` measured before master clamping. Output-bus peaks are captured after the clearing synth and must be zero. Fingerprints are noncryptographic ordering evidence. PIDs are reaped before successful reporting. Errors print to stderr with a nonzero exit and no success JSON; saved files are never modified. Owned startup, replies, logs and production are bounded; child exits fail playback. SIGINT/SIGTERM stop playback and release owned children; SIGKILL cannot run cleanup.

This CLI does not use or mutate an active JSONL session/revision. Default `transport.play` remains prepared playback; explicit `source_mode:"live"` uses the owned transport above. Pause/seek/loop, AU/VST3 routing in this mode and sustained latency/clock drift remain pending. Revision-checked control edits are available through JSONL live transport above. Verified platform is macOS arm64 with SuperCollider 3.14.1; acoustic delivery is unverified.

## Csound standalone offline jobs

`capabilities.csound_offline` distinguishes Unix implementation support from `configured` (an existing absolute `DAW_CSOUND` file). Configuration does not prove executable permission, runtime compatibility or opcode availability. Windows recognizes the command and reports unavailable execution. `session_device` and `native_playback` are false; `asset_preparation` is false. No HTTP route is provided.

```json
{"protocol_version":1,"id":"cs-render","method":"csound.render","params":{"csd_path":"/absolute/sine.csd","path":"/absolute/fresh.wav","sample_rate":48000,"duration_frames":48000}}
```

Params contain exactly the four fields above. Paths are nonempty UTF-8 strings up to 4096 bytes without NUL; relative paths resolve from the controller working directory. Rate is an integer 8000–192000. Duration is an integer 1 through `sample_rate*10`. Null, unknown/missing fields, booleans and fractional integers are rejected with `invalid_params`. Success returns `{path,frames,sample_rate,channels:2}`. The command is synchronous and preserves the active session, revision and transport on both success and failure. Input/runtime/output failures use `runtime_error`.

The CSD is a nonempty regular UTF-8 file without NUL, at most 1 MiB, copied into a private job directory. `CsOptions` are ignored; the runner imposes WAV/PCM16, requested rate, `ksmps=1`, no displays and null realtime audio/MIDI modules. Empty owned rc files replace user rc configuration. The program's score must itself produce exactly `duration_frames`; the parameter verifies output rather than rewriting the score or cutting a longer render. Wrong channel/format/rate, truncated payload, mismatched frames, child failure, missing output and oversized files/diagnostics fail without publishing. Output is capped at ten seconds of stereo PCM16 plus 64 KiB headers, checked during child execution and afterward.

The child process group has a fifteen-second deadline and each diagnostic pipe is drained with a 64 KiB retention limit. Owned descendants are terminated before joining readers. Existing destinations, including dangling symlinks, are preserved; final creation is exclusive. Publication occurs only after successful child exit and full PCM validation. A failed publication removes only the output created by this job. No cancellable job ID is introduced.

This is caller-trusted code execution, not a sandbox. CSD opcodes, includes, embedded resources or absolute paths may access files, launch code or perform other side effects. Relative assets are not copied/resolved against the original CSD directory. This standalone job does not claim arbitrary opcode compatibility, a saved device, live block processing or GUI support. Saved prepared tracks are specified separately below. See the [source fixture](../native/csound/source_fixture.csd).

The separate `native/csound/block_probe.py` diagnostic verifies Csound 7 host buffers/control changes and reset/destruction with explicit `DAW_CSOUND_LIBRARY`. Its report marks `daw_transport:false` and `hardware_audio:false`. Saved prepared tracks below use the same ABI in an owned worker, but this diagnostic itself remains separate from DAW playback.

## Schema-v7 prepared Csound sources

Schema v7 adds embedded Csound source devices. Earlier schemas reject these devices; loading never silently upgrades a session. A track uses continuous mode with no clips and the existing required session/track fields. Example (the CSD text is shortened only for readability):

```json
{"schema_version":7,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{"id":"tone","mode":"continuous","clips":[],"effects":[],"device":{"kind":"csound","program":"<embedded UTF-8 CSD>","duration_frames":48000,"gain":1.0,"controls":[{"name":"frequency","value":440.0,"points":[{"frame":24000,"value":660.0}]}]}}]}
```

The device fields are all required and unknown fields are rejected. `program` is nonempty UTF-8 without NUL, at most 60 KiB. `duration_frames` is 1 ms through ten seconds at the session rate. `gain` is finite and from zero through one. There may be at most 64 unique nonempty control names per source (each at most 128 UTF-8 bytes, without NUL); saved bases and point values must be finite float64. Each point has exactly `frame` and `value`; frames strictly increase, are multiples of 64 including zero, and precede the source end. Frame-zero points replace the saved base. Across schema-v6 SuperCollider and schema-v7 Csound sources, sessions allow at most four sources, ten total source-seconds and 512 points. Prepared float64 stereo source PCM shares the existing 128 MiB decoded asset budget and the session save/load size limit.

`capabilities.csound_sources` describes saved devices separately from `capabilities.csound_offline`, the standalone `csound.render` job. Source preparation is implemented on Unix and requires an existing absolute `DAW_CSOUND_LIBRARY` for the Csound 7 double-sample ABI. `DAW_CSOUND_PYTHON`, if set, must be an absolute executable; otherwise `python3` is used. `DAW_CSOUND_WORKER`, if set, must be an absolute worker file; otherwise the checkout's `native/csound/source_worker.py` is used. The owned worker imposes the session rate, `ksmps=64`, stereo output and `0dbfs=1`; it accepts at most two input channels and supplies zero input. Output must be finite. A final incomplete 64-frame block is performed and trimmed to the exact saved duration. Csound 6 and other platform ABIs are unsupported/unverified.

Each Csound control must exist at runtime as a declared scalar input channel. The worker starts the CSD, then applies saved bases (with frame-zero overrides) before the first `csoundPerformKsmps`; later points are applied at their aligned block boundaries. Since controls are applied after `csoundStart`, the saved interface does not promise initialization-rate behavior. Csound events therefore change at block boundaries, not arbitrary sample positions. During explicit live transport, `source.set_control` can change only a saved scalar channel with no points; the owned worker verifies the changed value by native readback and publishes an acknowledgment after a changed block enters the Rust callback ring.

Session replacement/load validates and prepares all source PCM before stopping transport or committing the new session/revision. Runtime failure returns `runtime_error` and leaves the active session intact; structurally invalid data returns `invalid_session`. Prepared PCM is cached by source program, duration, controls/events and sample rate; source/track gain is applied by Rust and does not require rerendering. Rendering and ordinary native transport consume this prepared PCM at a matching device rate. Prepared playback has no running Csound instance. Separate explicit live transport on macOS arm64 supports schema-v6 SC, schema-v7 Csound, schema-v8 Pd, and mixed sessions with saved controls/events, plus revision-checked Csound scalar edits for nonautomated channels. It rejects pause, seek and loop. The GUI's schema-v7 support is limited to validated continuous sine/SC/Csound tracks with gain-only effects; notes, clips and Csound program parsing remain outside its scope.

The CSD is caller-trusted code. The private worker directory and disabled Csound host audio I/O do not sandbox opcodes or prevent filesystem/process side effects. CSD includes and relative external assets are not collected from the original file location. Arbitrary opcode compatibility is not claimed. Standalone WAV rendering, prepared source playback, and live Csound DSP make distinct capability claims.

The separate macOS arm64 `native/csound/stream_probe.py` checks Csound-owned block production through the existing fixed queue and a paced diagnostic consumer. That diagnostic still opens no hardware and does not use DAW transport; the live capability is supplied by the separate owned transport path. See the [queue tests](../native/csound/test_queue.py).

## Schema-v8 prepared Pure Data sources

Schema v8 adds embedded Pure Data patch devices, while retaining supported sine, audio, SuperCollider and Csound devices and existing track effects. Earlier schemas reject Pure Data devices; loading never silently upgrades a session. A track uses continuous mode with no clips. The patch and every required abstraction are stored in the session:

```json
{"schema_version":8,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{"id":"tone","mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"trim","gain":0.5,"bypass":false}],"device":{"kind":"puredata","program":"<embedded UTF-8 Pd patch>","abstractions":[{"name":"daw-offset","program":"<embedded Pd abstraction>"}],"duration_frames":48000,"gain":0.5,"controls":[{"name":"$0-frequency","value":440,"points":[{"frame":24576,"value":660}]},{"name":"$0-amplitude","value":0.1,"points":[]}]}}]}
```

All device fields are required and unknown fields are rejected. `program` and abstraction source are nonempty UTF-8 without NUL; their combined size is at most 60 KiB. There may be at most 16 abstractions, each with a unique ASCII name matching `[A-Za-z_][A-Za-z0-9_-]{0,63}`. Duration is 1 ms–10 seconds at the session rate. Device gain is a finite JSON number from 0–1 and is applied by Rust. Scalar bases and point values must remain finite when converted to float32; the saved JSON numbers are preserved, while Pd receives their float32 conversion. There are at most 64 unique control names per source (nonempty, at most 128 UTF-8 bytes, without NUL). Points have exactly `frame` and `value`, are strictly increasing multiples of 64, and precede the source end. At most 512 points are allowed across all runtime sources. SC v6, Csound v7, and Pd v8 sources share a maximum of four sources, ten total source-seconds and the existing 128 MiB decoded-audio budget.

`capabilities.puredata_sources` describes schema-v8 preparation separately from live transport and GUI support. Preparation is available on Unix and requires `DAW_LIBPD_LIBRARY` to name an existing absolute libpd library built with multi-instance support; the tested runtime is libpd 0.16.1. Optional `DAW_LIBPD_PYTHON` and `DAW_LIBPD_WORKER` must be absolute executable/worker paths when set; otherwise the worker uses `python3` and this checkout's `native/puredata/source_worker.py`. It accepts the public float API with 64-frame blocks and stereo I/O. Input is zero-filled. The worker validates float32 output and converts it to interleaved float64 PCM for Rust. Output must be finite, and a final partial block is trimmed to the exact duration. Patch code executes in a bounded owned worker, but this is not a general sandbox or a compatibility claim for arbitrary externals.

The worker opens the patch and explicit abstractions, then applies saved scalar bases after load-time patch actions and before enabling DSP. Names beginning with `$0-` are resolved to that patch instance's dollar-zero ID; all other names are sent as written. Missing receivers fail preparation. This proves receiver existence and successful message delivery, not arbitrary value readback or initialization-rate semantics. Frame-zero points override bases; later events are sent at their 64-frame boundary. Programs should use built-in objects and declare receivers they intend to control.

Session replacement/load prepares each Pd source before committing the session/revision. A worker failure preserves the active session. Source audio is cached as stereo float64 after finite float32 PCM has been validated; Rust applies source gain and the existing serial track effects during rendering and native prepared playback. Native playback requires the default device rate to match the session rate. The [Pure Data example](../examples/puredata_tracks_demo.py) saves/reloads a two-source project, renders 440/660 Hz control changes, checks stereo identity and expected gain, and supports a muted `--native` callback check. The GUI rejects schema v8. Explicit `source_mode:"live"` uses owned Pd DSP on macOS arm64 as described below. The standalone [block/message diagnostic](../native/puredata/block_probe.py) remains a separate proof and makes no DAW-transport or hardware-audio claim.

### Schema-v8 live Pure Data transport

`puredata_live_transport.implemented` advertises the macOS arm64 native-audio path; it requires configured libpd and a queue bridge, and remains separate from `puredata_sources` preparation. `source_mode:"live"` accepts schema v8 at 48 kHz with gain-only effects and at most ten seconds. Pd may mix with SC/Csound and built-in tracks. Earlier SC/Csound schemas remain accepted. Pure Data controls use saved bases/frame-zero overrides and later 64-frame events. The separate unfinished legacy live-control path adds revision-checked `source.set_control` delivery for saved unautomated Pd scalars, with float32 delivery and processed-block publication acknowledgments. Its native capability is advertised in supported builds, but the opt-in native acceptance and final contract reconciliation remain unfinished; it is separate from the accepted sequenced Pd instrument workflow.

Each Pd track starts a fresh owned worker and private patch/abstraction tree. It validates receivers and publishes an initialization report before the owner releases the start gate. The callback ring must be prefilled before Play acknowledges. Source duration is capped to requested playback duration, with events beyond that end removed from the private snapshot; saved session data is unchanged. The fixed producer queue zero-pads a final partial block, while Rust mixes only exact source/playback frames and silence after source end. Natural completion verifies the worker's bounded completion report; failed initialization, diagnostics, publication or teardown stop the live session. Stop/EOF request graceful libpd teardown and bound hung children before process-group cleanup. Startup failure preserves saved model/revision and leaves transport stopped.

Set `DAW_LIBPD_LIBRARY` as for preparation. Optional absolute `DAW_LIBPD_QUEUE_LIBRARY` defaults to `output/csound-stream/libdaw-csound-queue.dylib`, built by `native/csound/build_queue.py`; optional `DAW_LIBPD_STREAM_WORKER` defaults to `native/puredata/stream_worker.py`. `DAW_LIBPD_PYTHON` applies to prepared and live workers. The historical queue symbol names select the reused ABI, not the DSP runtime. This does not add pause/seek/loop, Pd GUI imports, acoustic verification, arbitrary external support or a general patch sandbox.


## Listening click, count-in and recorded note takes

`transport.play` accepts optional `metronome: true|false` (default false) and `count_in_bars: 0|1|2` (default 0). `capabilities.timeline_transport` advertises the native click and its eligible devices. The first slice supports prepared built-in sine/synth/drumkit/PCM tracks with built-in effects; Pd/plugin/live source click requests reject explicitly. The accented 4/4 click follows the existing integer beat-to-frame grid. Count-in emits clicks even if the ongoing metronome is disabled, freezes timeline/output frames, and starts the arrangement after its final frame. Pause freezes the renderer, and seek/loop do not restart count-in. Finite playback retains its wall-time safety deadline, including paused time, extended by the initial count-in duration. Until-stopped playback still requires Stop or engine shutdown.

Native snapshots add `metronome`, `count_in_remaining_frames` and `output_frame`. Count-in does not advance `timeline_frame`, `output_frame` or `submitted_frames`; the latter two count arrangement-rendered frames. The click is listening-only, follows listening volume, is clipped safely with the mix, and is absent from saved sessions, exports and track/master mix meters.

The GUI **Record notes** action freezes the selected note clip and revision, then starts native playback at frame zero. Computer keyboard/Web MIDI note-on/off events buffer held gates as clip-relative frames using sampled transport status and a monotonic browser clock. Notes pressed during count-in or outside the clip are ignored. Loop/seek/pause recording are excluded in this slice. New gates that cross the clip end are shortened with explicit feedback. Focus loss, transport jumps or event/held-note overflow discard the take with visible feedback; existing session data is preserved. There is no live input monitoring or hardware input-latency compensation.

Stop closes held gates at the final confirmed timeline frame and submits the whole overdub to authenticated `POST /api/note/take` with exactly `{session, expected_revision}`. The bridge validates and performs checked replacement, then returns `{session, revision}` under the same project lock. This leaves the existing `/api/session` response unchanged. One successful take adds one Undo entry; rejected validation/stale requests retain the pending take for retry or discard. Existing notes, PCM assets, mixer/effects and exact off-grid values remain intact. The [recording workflow](../examples/note_recording_workflow_demo.py) checks whole-take undo/redo and byte-identical portable ZIP reopen/export. Physical MIDI hardware and measured latency remain unverified.

## Integrated studio bridge

The optional studio lives in the existing authenticated loopback HTTP bridge, outside the Rust/audio callback. It uses the same Host/Origin checks and `X-DAW-Token` as the editor. `gui/studio_contract.json` is the operation/role contract; `gui/studio.js` maps allowed operations to existing editor helpers. No new session schema or asset ownership model is introduced.

- `GET /api/studio`: availability booleans, model, roles and operation contract; no credentials.
- `POST /api/studio/prompt`: exactly `{prompt, role, scope, expected_revision, history}`. Roles are `producer`, `engineer`, `musician`; prompt is 1–4000 characters; history holds at most six `{role:user|assistant,content}` entries of at most 4000 characters. Scope is null, `{track_id}` or `{track_id,clip_id}`. The bridge obtains `session.inspect` under the project lock and releases the lock before provider I/O. Stale revision/selection rejects before inference. One studio prompt can be active per server.
- File-picker commands reveal a local studio action button; the browser opens its picker from the user click. A successful prompt returns `{revision,scope,parts:[{role,reply,operations}]}`. The producer may delegate to at most four engineer/musician tasks, each `{role,prompt,scope?}`. Omitted scope inherits the user scope; an explicit scope must exist in the saved snapshot and may narrow but never widen it. Musician tasks on existing arrangements select one track each, with track-only scope unless the user selected a clip; this keeps repeated patterns available within the small task budget. Producer and engineer receive saved track/clip overviews with note counts; a scoped musician receives the exact selected track/clip notes. Later specialists also see earlier uncommitted proposals and the common producer musical direction. Producer direct edits are capped at 16 operations, each delegated task at 24; direct specialist prompts retain the 128-operation limit. Each role may correct one malformed or truncated plan once (at most ten inference calls for a fully delegated prompt, plus the catalog lookup described below). Truncated content is never treated as an applicable plan. A successful response contains up to five ordered parts applied as one batch. At most 128 operations total; a command runs alone without delegation. Inference never mutates engine state. Errors return 422; provider bodies and authorization headers are not reflected. Provider redirects are refused, reads are bounded, and inference requests use a 120-second socket timeout, voice requests 60 seconds and catalog lookup 10 seconds. The private UI gateway allows a 1300-second upstream socket wait for Studio prompts, including non-streaming JSON clients that receive headers only after all delegated/correction calls finish.
- The browser checks the captured revision, draft, selection and project generation again. Edit batches require stopped transport or eligible built-in native playback, no browser playback, no typed drafts and no pending take. Each operation enforces role and scope, and uses the existing immutable editor validation. Only the finished batch is sent to revision-checked `POST /api/session` while stopped or `POST /api/session/live` during eligible native playback; successful acceptance commits one existing bounded Undo entry. Agent device edits do not reread old device controls over the proposed values. Asset registration and Rust validation remain authoritative.
- Provider plan parsing accepts a single JSON object after an optional prose/fence prefix and normalizes literal escaped whitespace only outside quoted strings. Duplicate keys, nonfinite values, trailing prose/objects, malformed or truncated JSON still reject; prefix text is not displayed or applied.
- `copyNotes` is a producer/musician editor operation with `{track_id,clip_id,target_clip_ids}`: copy the edited source notes to 1–64 distinct existing note clips on that track. Source and targets must have originally identical note patterns including pitch, ignoring note IDs. All targets must be within scope; previously edited targets reject rather than discard pending edits. Clip placement/length and devices remain unchanged. The final editor validation checks every copied gate and voice budget before one checked replacement; an invalid target rejects the whole batch. Provider context includes `matching_note_patterns` source/target groups derived from the saved snapshot, never persisted session fields.
- `POST /api/studio/prompt` with `Accept: application/x-ndjson` opts into a close-delimited, no-store progress stream. Newline JSON events are `{type:progress|summary|delegation,role,message}`, followed by exactly one `{type:result,plan}` or `{type:error,error}`. Progress identifies catalog lookup, role/model requests and correction attempts; summaries contain validated reply text and delegations contain task text, with no operations applied. Once streaming starts, errors use the terminal event under HTTP 200; pre-stream request/snapshot errors still use 422. A dropped or incomplete stream applies nothing. JSON clients retain the existing response. Network diagnostics use fixed timeout/DNS/TLS/connection categories and HTTP status guidance, never upstream bodies or raw exception text.
- `POST /api/studio/transcribe`: raw microphone audio (`audio/webm`, `audio/mp4`, `audio/ogg`, `audio/wav`), at most 4 MiB; returns `{text}` of at most 4000 characters. Browser recording is limited to 30 seconds, releases microphone tracks on completion/failure, and fills the prompt for review rather than executing it.
- `POST /api/studio/speak`: exactly `{text}` of 1–4000 characters; returns bounded MP3 audio. Blob playback is allowed by the local CSP. Voice failures do not roll back accepted musical edits.

Server environment: `OPENCODE_API_KEY`, optional `ELEVEN_API_KEY`/`ELEVENLABS_API_KEY`, `DAW_STUDIO_MODEL` (explicit chat-completions-compatible Zen model override), `DAW_STUDIO_VOICE_ID`. Without an override, each validated prompt performs one bounded public `GET https://opencode.ai/zen/v1/models` (10-second timeout) after acquiring the studio lock and outside the project lock. Selection prefers `glm-5.3-flash`, then `space-bunny-free`, then `big-pickle`, otherwise GLM Flash; catalog failure retains the last selection, initially GLM Flash. GLM Flash requests use `reasoning_effort:low`; other model payloads omit that provider-specific setting. Producer output is capped at 2000 tokens, delegated tasks at 4000, direct specialist prompts at 8000. Selection stays fixed across correction/delegation calls; inference failures do not retry another model. The catalog provides availability, not quality ranking or stealth classification; the candidate order is maintained in `gui/studio.py`. `GET /api/studio` reports the current selection without making provider requests. Keys are never page config, project data or conversation content. Provider calls use fixed HTTPS hosts and cannot choose paths from a prompt. Studio agents have no acoustic listening input, shell execution, arbitrary file access or authority to change saved runtime programs.

## Selected-clip MIDI download

The GUI's **Export clip MIDI** is a read-only browser download from the applied selected note clip, using [Standard MIDI Files](https://midi.org/standard-midi-files). It adds no Rust/HTTP command or session schema. Pending drafts, takes and editing locks block the download without consuming edits or adding Undo history. Format 0 contains one track, 960 PPQ, rounded integer microseconds-per-quarter tempo, 4/4, note-on/off events and end-of-track at the clip length (or final gate if tick rounding extends it). Placement in the song is excluded; notes start from clip zero. Note-offs precede note-ons at equal ticks. Drumkit uses MIDI channel 10; other supported note devices use channel 1 without a program change.

Hz round to nearest MIDI semitone (out-of-range pitches reject); velocities round to 1–127 and exact zero-velocity notes are omitted. Frame endpoints round independently to ticks, with gates kept at least one tick. Overlapping gates of the same rounded pitch reject before download. Empty clips are valid. These conversions affect only the file: exact project data, audio assets, processing, mix and rejected takes stay untouched. Sound/preset interchange remains deferred.

## MIDI note-file import

**Import MIDI** reads a local file in the browser and appends note lanes using the existing validated, revision-checked `POST /api/session` replacement and one bounded Undo entry. There is no new JSONL command, asset type or session schema. Structural import requires stopped playback, no drafts/pending take and an unchanged project/revision across the asynchronous read. Rejected parsing, authoring validation and checked replacement preserve applied state and history. Existing audio assets, exact saved frames, Hz and mix are retained.

`gui/midi_file.js` accepts Standard MIDI File formats 0/1, a six-byte header, 1–64 `MTrk` chunks and nonzero PPQ division (SMPTE/format 2 are unsupported). The file is at most 1 MiB, 65,536 events, 16,384 note-ons and 64 resulting lanes, also subject to the existing complete session/voice limits. VLQs are bounded to four bytes; chunks/data bytes, running status, positive matched gates and end markers are checked. Duplicate held channel/pitch, unmatched note-off, unfinished gates, missing end-of-track and trailing data reject. Note-on velocity zero closes a gate.

Each source track/channel containing notes becomes a new lane with one clip at **Insert at** (or zero without an arrangement). Tick endpoints convert directly to frames using the file PPQ and current project tempo, round nearest with midpoint later, and keep collapsed gates at least one frame. Source tempo/time-signature metadata does not alter the project's grid or existing frames. Melodic channels use the existing synth; channel 10 uses drumkit and rejects pitches outside 36/38/42. Pitch is equal-tempered Hz and velocity is MIDI velocity / 127. A clip includes source-track trailing silence through end-of-track. Empty files without notes reject; filename-derived lane IDs remain unique. The first new clip is selected after acceptance.

Program/controller/pressure/pitch-bend and SysEx events are skipped with a concise import notice; instrument sounds, sustain/expression and tempo maps are not reproduced. The import tooltip and README describe this note-only scope. These are note interchange limits, not acoustic/physical-MIDI acceptance.

## Private Tailscale UI gateway

`gui/tailscale_ui.py --upstream-port PORT --origin https://machine.tailnet.ts.net [--port 8790]` runs a separate loopback HTTP gateway for private Tailscale Serve. It connects only to the explicit `127.0.0.1:PORT` DAW and owns no engine/session/assets. The DAW continues accepting only its own loopback Host/Origin; the gateway accepts one exact configured root HTTPS `*.ts.net` origin/Host (optional nondefault port; configured `:443` is normalized away), rejects duplicate Host/Origin, and rewrites only validated requests to that DAW's loopback authority. Forwarded host/identity headers are not trusted or passed through. Tailscale policy supplies remote access control; the gateway does not implement separate user accounts, and authorized clients receive the same page token as local clients.

Only GET/POST are relayed. POST requires one valid Content-Length; Transfer-Encoding, duplicate control headers, GET bodies, absolute request targets and uploads above the existing 128 MiB portable-project ceiling reject. Body transfer is chunked internally with bounded buffers; it leaves downstream route-specific limits, token/revision/project validation unchanged. Responses retain content lengths/types, CSP/nonces and download/clipping metadata. Close-delimited Studio progress is flushed as it arrives; it does not become an applied edit. The gateway cannot start/stop/replace the engine by its lifecycle; disconnects leave session ownership with the existing DAW. See [the phone workflow](../README.md#private-phone-ui-through-tailscale) for setup and viewing-device versus Mac audio behavior.

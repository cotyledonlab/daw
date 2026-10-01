# DAW

A minimal, agent-controllable DAW project. A local browser GUI sits on a Rust core with versioned sessions, a JSON Lines command interface, and offline stereo WAV rendering of built-in sine tracks.

Optional macOS builds support scripted schema-v4 VST3 sessions and schema-v5 Audio Unit sessions offline, plus experimental in-process live VST3 playback. AU support is limited to Apple's AULowpass; AU live playback and GUI editing remain unavailable. Schema-v6 SuperCollider and schema-v7 Csound programs can be saved as tracks and prepared through owned offline jobs for rendering and rate-matched native playback. On macOS arm64, JSONL native transport also has an explicit live mode for SC, Csound, or mixed SC/Csound tracks, alongside built-in tracks and gain effects. Live mode supports start/status/stop/volume and checked saved-control edits for SC arrays and eligible Csound scalar channels. Pause, seek, and loop remain unsupported. Pure Data and recording remain unavailable. Schema-v2 sine notes and PCM WAV clips can be sequenced through the scripting interface. The [plan](docs/PLAN.md) defines remaining slices and the [integration notes](docs/INTEGRATIONS.md) record hosting options.

## Run

Install Rust 1.85 or newer and Python 3.10 or newer, then from this directory:

```sh
cargo build --locked
python3 gui/server.py
```

The launcher opens a browser window. Add sine tracks, edit frequency/gain, and apply your changes. Save session downloads a JSON file; Load session imports one. Render WAV applies the current edits and downloads a stereo WAV. The browser manages download locations. Press Play for immediate browser sine playback; adjust frequency/gain while playing. The same button toggles Play/Pause; hold it for 0.6 seconds or press Escape to Stop. Pause preserves oscillator state, while Stop releases the player. Invalid edits stop playback; loading a session also stops it. Listening volume is separate from WAV gain.

The server binds only to `127.0.0.1`, chooses a free port, and starts one Rust engine. Use one editing window; multiple tabs share that engine and do not have conflict detection. Save your session before stopping the server with Ctrl+C. Refresh discards unapplied edits; stopping the server discards the in-memory session. For a specific port or manual browser opening, run `python3 gui/server.py --port 8765 --no-open`.

For the headless scripting demo:

```sh
python3 examples/demo.py
```

The demo starts `target/debug/daw serve`, inspects capabilities, constructs a sine track from the discovered parameter defaults, adds it using a revision-checked batch, saves the session, and renders one second to a fresh directory under `output/`. It prints the resulting paths. On macOS, use `afplay <printed WAV path>` to listen at a comfortable volume.

Send commands from any language that can launch a child process and read/write JSON. No embedded scripting language or agent framework is required:

```sh
printf '%s\n' '{"protocol_version":1,"id":"1","method":"capabilities"}' | cargo run --quiet --locked -- serve
```

Read the [protocol](docs/PROTOCOL.md) for all commands and errors. The headless interface runs with the caller's filesystem permissions and has no network listener. The optional GUI adds the loopback bridge described below. IDs correlate responses; they do not provide deduplication. Rendering is synchronous and each invocation starts at frame zero.

## Live playback

Live sine playback uses Web Audio at the browser/device sample rate. It auditions local draft edits, including before Apply. Apply/save still validate and persist through Rust. Frequencies must be below both the session and live-device Nyquist limits. The monitor starts at 25% volume and normalizes summed track gain above one to provide headroom; these settings are not saved and do not change WAV exports. Live parameter smoothing and oscillator phase differ from offline rendering. Each browser tab has its own player.

Browser output is built-in sine audition for schema-v1 sessions only. Native output below runs the Rust engine and is required to play v4 sessions, including v4 sessions without plugins; VST3 sessions additionally require the experimental `vst3-live` build. Native playback also supports scripted schema-v2 notes and audio clips at a matching device rate.

The GUI edits continuous sine sessions in v1 and v4. It also imports schema-v6 continuous sine/SuperCollider sessions with gain effects, shows saved program identity/duration/control arrays, and preserves embedded programs and automation. It can add serial gain effects, explicitly upgrading a v1 session to v4, and edit validated VST3 effects already present in loaded or applied sessions. The format is otherwise unchanged by editing. VST3 effects can be selected from a 64-entry in-memory catalog of effects seen in those sessions; the GUI does not scan plugins, install them, or browse the filesystem over HTTP. On macOS builds with `vst3-offline`, it can inspect metadata for those active-session effects and show the plugin's parameter names and units for saved parameters. The saved normalized values remain the editable base values; metadata's default and restored values are informational, while automation points are preserved as read-only data. Unavailable inspection leaves generic parameter labels; failed inspection also shows a per-effect error. It does not add unsaved plugin parameters. Bypass and removal are available, and removing an effect also removes its targeted gain automation lanes. Notes, audio clips, and schema-v2/v3 sessions are outside the GUI editing scope.

## Native playback (macOS)

```sh
cargo build --locked --features native-audio
target/debug/daw devices
target/debug/daw play path/to/session.json 2 0.25
```

This plays a saved session through the default CoreAudio output for the requested number of seconds (maximum 60). Ctrl+C stops it. The optional final argument is monitor volume, defaulting to 0.25. Device configuration and callback timing are reported. After this build, restart `python3 gui/server.py` and choose **Native audio** in the GUI. Play applies the draft and starts the Rust engine. The same button pauses/resumes; hold it or press Escape to Stop. Track editing is locked while native playback is playing or paused; listening volume remains adjustable. Native playback uses a fixed session snapshot and ends after 60 seconds of wall time, including time paused. Switching output stops the previous player in that window. Browser output remains available for immediate draft edits. See [native audio notes](docs/decisions/live-audio.md) for tested hardware and limitations.

## Scripted note sequencing

[The arpeggio example](examples/sessions/arpeggio.json) contains four notes at 48 kHz. Play it using the native build above:

```sh
target/debug/daw play examples/sessions/arpeggio.json 2 0.25
```

The default device must use the same sample rate as the note session. Notes have frame positions, independent voices, and fixed 5 ms attack/release envelopes. At most 64 simultaneous voices are allowed, including release tails. Export through `session.load` and `render` in the JSONL interface; native transport uses the same prepared note engine. Use revision-checked full replacement for clip edits; existing batch operations can add/remove whole tracks and change track gain.

The browser editor rejects note-session and audio-clip sessions and locks editing if it encounters them. There is no piano roll or clip editor. Native seek, loops, and schema-v3 effect automation are available through JSONL; the GUI's effect editing is limited to gain chains and existing validated VST3 effects in supported v4 sessions.

To preserve an existing sine session while explicitly upgrading its format:

```sh
python3 examples/upgrade_session.py old-session.json new-session.json
```

This validates both versions, preserves continuous playback and track order, and refuses to overwrite the destination. Sessions are never silently upgraded on load.

## Native seeking and looping

With the native build, run `python3 examples/transport_demo.py` for a silent hardware check of pause, seek, loop, resume, and stream release. An optional session path tests a PCM clip project instead.

Scripts can issue `transport.seek` with `{"frame":1800}` and `transport.loop` with `{"region":{"start_frame":1200,"end_frame":2400}}` while native playback is active or paused. Send `{"region":null}` to disable looping. Poll `timeline_command_pending` until false before sending the next timeline command. Positions use the reported native sample rate. See the [protocol](docs/PROTOCOL.md) for limits and acknowledgments.

Loops and seeks clear note voices without retriggering notes that began before the destination; audio clips resume at their corresponding source offset. Discontinuities can click. Live loop settings are temporary, do not change WAV exports, and do not extend the 60-second playback limit. The browser has no seek or loop controls yet.

## PCM WAV clips

```sh
python3 examples/audio_clip_demo.py
```

This creates a project under ignored `output/`, with a stereo PCM WAV, two clips, a saved session, and a rendered mix. Play its printed session path with `daw play SESSION 1.5 0.25` using the native build.

Assets use paths relative to the session file's folder. Only integer PCM16/24/32 mono/stereo WAVs at the session rate are supported. Files are preloaded, with 32 MiB per-file and 128 MiB decoded-session limits. Saving an audio session must stay in the same project folder; asset copying is not implemented. See [audio asset rules](docs/decisions/audio-assets.md) for the exact contract.

## Serial track effects

Schema v3 adds per-track gain effects, applied in array order before the final mix. Gain ranges from 0 to 4; each effect has a saved ID and bypass setting. There is no intermediate clipping. V1/v2 sessions keep their existing behavior and are never silently upgraded.

Play the [gain-chain example](examples/sessions/gain-chain.json) with the native build:

```sh
target/debug/daw play examples/sessions/gain-chain.json 2 0.25
```

Edit chains through revision-checked `session.replace`, then save or render using JSONL. Playback uses a prepared snapshot; changing a chain stops it. Saved gain automation is available through JSONL, and the GUI clears lanes targeting an effect when that effect is removed. See the [effect contract](docs/decisions/track-effects.md).

## Saved gain automation

Schema-v3 tracks can add step/hold gain lanes targeting an effect ID. Values apply at exact frame positions and are restored on seek or loop wrap. Play the [automation example](examples/sessions/gain-automation.json) with `daw play examples/sessions/gain-automation.json 2 0.25`. Save/load and WAV rendering use the same lane data.

Before a lane's first point, its effect uses the saved base gain. There is no implicit smoothing. Editing a lane uses session replacement and stops the active snapshot. See [the protocol](docs/PROTOCOL.md) for the strict shape and limits.

## Scripted offline VST3 effects (macOS)

```sh
python3 native/vst3/build.py --fetch-sdk
cargo build --locked --features native-audio,vst3-offline
python3 examples/vst3_demo.py "$HOME/Library/Audio/Plug-Ins/VST3/ValhallaFreqEcho.vst3" 5653544671456876616C68616C6C6166 --parameter 48
```

This creates a fresh project under ignored `output/`, captures initial plugin state, saves/reloads a schema-v4 session, and renders two one-second WAVs with wet/dry automation. It does not play audio. Other effects require their exact class CID and parameter IDs; compatibility is not assumed. Configure an absolute worker executable with `DAW_VST3_HOST` when running outside this checkout.

V4 preserves v3 track gain chains and adds serial `vst3` effects. Supported plugins have one stereo input/output, no event buses, float32 offline processing, and zero reported latency. Sessions containing plugins require 48 kHz, and plugin renders stop at ten seconds. Parameters are normalized and points use exact frame positions; plugin DSP may smooth changes. State has strict byte limits. Load/replace prepares plugins in owned child processes before committing; failures preserve the active session and revision. Rendering finishes all plugin work before creating the destination WAV. Bypass skips DSP but still validates the plugin on load. See [the protocol](docs/PROTOCOL.md) for the shape and [the adapter record](docs/decisions/vst3-adapter.md) for limits.

The GUI can edit supported v4 sessions and reuse validated VST3 effects already loaded or applied, but it does not scan or install plugins and provides no plugin editor window. Parameter metadata inspection uses the offline worker without activating or processing the plugin; it does not change the session or stop playback. Native VST3 playback is an experimental feature requiring both offline VST3 and native audio support. It runs third-party plugin code in-process; a plugin crash can terminate the engine.

## Experimental live VST3 playback (macOS)

Build the worker library and the feature-enabled engine, then play a saved schema-v4 session containing VST3 effects:

```sh
python3 native/vst3/build.py
cargo build --locked --features vst3-live
target/debug/daw play path/to/vst3-session.json 60 0.25
```

`vst3-live` implies `vst3-offline` and `native-audio`. The build script writes `libdaw-vst3.dylib` under `output/vst3-spike`; set `DAW_VST3_LIBRARY` to an absolute library path to select another build. Live sessions require a 48 kHz session and device, stereo float32 plugin processing, zero-latency effects, and no event buses. Playback is limited to 60 seconds. Play, pause, resume, volume, and stop are supported. Seek and loop are explicitly rejected for plugin sessions. While playing or paused, the GUI can change a saved normalized value for an active, non-bypassed VST3 effect when that parameter has no saved automation. Structural session edits remain locked during playback.

The experimental worker creates, processes, and destroys plugins on one dedicated DSP thread. A fixed 1024-frame SPSC queue feeds the CoreAudio callback, which consumes prepared audio without foreign calls, allocation, or locks. The queue holds about 21.3 ms at 48 kHz, in addition to device latency. Parameter edits enter a bounded queue of eight commands; one command is applied at the start of a successfully processed 256-frame block. The worker also has one in-flight command, so queued audio plus that block adds at most about 26.7 ms before device latency. A paused edit can remain pending until playback resumes. Acceptance commits the normalized base value and advances the session revision immediately; `transport.status` reports the applied revision and block-start frame after DSP succeeds. Stop or worker failure cancels edits not yet delivered to DSP, while their accepted base values remain in the session and are saved. Applying an edit does not recapture plugin controller/component state. Underruns output silence without advancing the timeline; `plugin_worker_underruns` is reported in transport status and CLI output. Startup has a five-second timeout. Shutdown waits two seconds and then detaches a hung in-process worker, reporting an explicit stop failure; in-process plugin crashes can terminate the engine. Offline rendering retains its separate child-process isolation. A silent ValhallaFreqEcho check on MacBook Air Speakers at 48 kHz passed three play/pause/resume/stop cycles and session replacement, with zero underruns and zero callbacks over budget; acoustic delivery was not verified.

## Scripted offline Audio Unit effects (macOS)

Build the AUv2 worker and feature-enabled engine, then run the scripted example:

```sh
python3 native/au/build.py
cargo build --locked --features au-offline
python3 examples/au_demo.py
```

Schema v5 retains the v4 timeline and built-in/VST3 effects, and adds the exact Apple `aufx/lpas/appl` AULowpass effect. Renders require 48 kHz and are limited to ten seconds. `DAW_AU_HOST` may select an absolute worker path; the fallback is `output/au-spike/au-host`. The worker is owned and bounded. AU live playback and GUI imports/edits remain unavailable; the GUI supports its documented v1/v4 sine and gain-only v6 sine/SuperCollider sessions. See the [AU adapter contract](docs/decisions/audio-units.md) and [protocol](docs/PROTOCOL.md).

## SuperCollider score jobs

```sh
cargo build --locked
export DAW_SCSYNTH=/Applications/SuperCollider.app/Contents/Resources/scsynth
python3 examples/supercollider_demo.py
python3 -m unittest native.supercollider.test_daw
```

On Unix, `supercollider.render` runs a prepared binary OSC score through an explicitly configured `scsynth` executable. It writes a fresh stereo PCM16 WAV, supports 8–192 kHz and scores up to ten seconds, and captures child errors without changing your session or transport. The portable `supercollider.inspect` command reads program names, native control defaults and graph structure without launching a runtime. The example inspects its own SynthDef and creates a score without starting a language interpreter or audio device. The configured executable and UGens must already be installed; this project bundles neither. The [job contract](docs/decisions/supercollider.md) describes bounds and restrictions.

These are synchronous offline jobs. A script can use the exported WAV in an audio-clip project. Saved programmable SuperCollider tracks are available as described below; live OSC playback is a separate native transport mode.

## Develop

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
python3 -m unittest gui.test_server
DAW_TEST_NATIVE_AUDIO=1 python3 -m unittest native.vst3.test_parameters
DAW_VST3_FIXTURE_NO_EVENTS=1 DAW_VST3_FIXTURE_REALTIME=1 cargo test --locked --features vst3-live actual_live_worker -- --ignored
node --test gui/test_live.cjs
```

`src/session.rs` owns the serializable model and validation. `src/control.rs` owns commands and persistence. `src/engine.rs` owns prepared block DSP and persistent oscillator phase; `src/render.rs` owns WAV encoding. `src/main.rs` owns bounded input framing and stdout responses. The offline renderer is a reference implementation, not a real-time audio callback.

`gui/server.py` is a standard-library Python bridge; `gui/index.html`, `gui/style.css`, and `gui/app.js` are the browser interface. It exposes capabilities, native transport, session inspection/checked replacement, saved SC control inspection/edits, and temporary WAV downloads, rather than arbitrary engine filesystem commands. Requests require a per-launch token and exact loopback host/origin checks. The bridge is for trusted local use, not deployment on a public server. Its tests start a loopback HTTP server and need local socket permissions.

T09a/b provide a [standalone macOS VST3 lifecycle spike](native/vst3/README.md) with a project-owned fixture, child-process scanning, and real ValhallaFreqEcho offline automation/state checks. T09c connects VST3 effects to scripted session save/load and WAV rendering. T09d adds experimental native live playback; short silent hardware checks verify callbacks and transport, while acoustic output remains unverified. T09f adds bounded read-only metadata inspection and actual saved-parameter labels in the GUI. [AGENTS.md](AGENTS.md) gives future agents the working rules.

## Saved SuperCollider tracks

```sh
cargo build --locked --features native-audio
DAW_SCSYNTH=/Applications/SuperCollider.app/Contents/Resources/scsynth python3 examples/supercollider_tracks_demo.py
```

This creates a schema-v6 project with two embedded SynthDefs, saved named control values/events and serial gain effects; it saves, reloads and exports the arrangement. Add `--native` for a bounded silent hardware smoke test. Preparation captures floating-point source audio outside callbacks, then the DAW routes it through its effects. A source has a finite duration; at most four sources and ten seconds of summed source duration are allowed. Reload regenerates audio, while playback/rendering reuse the prepared snapshot. Native playback requires a matching sample rate. Runtime/control failures preserve the active session. The browser imports gain-only continuous sine/SuperCollider v6 sessions and edits inspected saved control bases; prepared playback does not provide interactive SuperCollider DSP. See the [schema-v6 contract](docs/PROTOCOL.md#schema-v6-prepared-supercollider-sources).

Saved v6 sources can run in live mode through JSONL native transport as well as through the standalone CLI below. Prepared playback remains the default. Select live mode explicitly:

```jsonl
{"protocol_version":1,"id":"play","method":"transport.play","params":{"seconds":1,"volume":0,"source_mode":"live"}}
{"protocol_version":1,"id":"status","method":"transport.status"}
```

The JSONL live mode requires a macOS `native-audio` build and a 48 kHz device. SC sources additionally require the project capture plugin and absolute `DAW_SCSYNTH`; Csound sources require the Csound 7 double-sample library, the queue bridge, and the owned stream worker. Build the bridge with `python3 native/csound/build_queue.py`; `DAW_CSOUND_QUEUE_LIBRARY`, `DAW_CSOUND_STREAM_WORKER`, and `DAW_CSOUND_PYTHON` optionally select absolute paths. `source_mode:"live"` accepts schema v6 SC, schema v7 Csound, and sessions mixing both, for at most ten seconds and gain-only effect chains. It starts owned workers and routes their fixed queues through the Rust worker into the native callback. Loading/replacing still validates and prepares the session before commit. `source.set_control` edits eligible saved SC arrays or one saved Csound scalar input with no automation; Csound requires an existing declared channel. Acceptance commits the saved base and revision, while status separates native readback/publication from callback observation. Pause/resume, seek, and loop remain unavailable. The browser imports remain limited to gain-only continuous v6 sine/SuperCollider sessions; browser Web Audio does not host these sources. Callback frame/digest evidence is separate from acoustic verification.

Run the bounded two-track example with:

```sh
cargo build --locked --features native-audio
python3 native/supercollider/build_stream.py --sdk /absolute/path/to/supercollider-3.14.1
DAW_SCSYNTH=/Applications/SuperCollider.app/Contents/Resources/scsynth python3 examples/supercollider_live_demo.py
```

The example uses a muted monitor and checks one second of source audio, ordering digests, underruns, and server/queue release. The `serve` process cleans up owned servers when its JSONL input closes; it does not install CLI SIGINT/SIGTERM handlers.

The standalone finite macOS CLI remains available for direct saved-session playback:

```sh
cargo build --locked --features native-audio
python3 native/supercollider/build_stream.py --sdk /absolute/path/to/supercollider-3.14.1
DAW_SCSYNTH=/Applications/SuperCollider.app/Contents/Resources/scsynth target/debug/daw sc-session-play path/to/session.json 1 0
```

The last argument is monitor volume (0 for a muted callback check; default 0.25). This CLI requires a 48 kHz default output device, schema v6/v7 and at most ten seconds. It starts an owned producer per SC/Csound track, preserving same-name SC programs, captures and mutes SC stereo buses 0/1, and feeds their queues plus saved controls/events, source duration/gain, serial gain effects (including automation/bypass), built-in sine/notes and rate-matched PCM clips into the DAW's fixed native callback queue. Csound live mode requires the Csound 7 library and queue bridge. AU/VST3 effects are rejected in this mode. Final JSON reports frame counts, source/callback sample-order fingerprints, quarter RMS, output-bus peaks and released worker PIDs; readiness uses stderr. Ctrl+C or SIGTERM performs owned cleanup. Abrupt process termination (SIGKILL) cannot run that cleanup. JSONL transport is the preferred interface. Clock-drift/latency characterization and long playback remain pending. Callback checks use a muted monitor; acoustic delivery and arbitrary SC UGens are unverified.

The separate macOS [interactive SC probe](native/supercollider/live_probe.py) verifies owned OSC lifecycle and a control change reaching finite private-bus audio capture. It keeps output buses silent and is distinct from the DAW live transport. The [streaming gates](docs/decisions/supercollider.md#streaming-decision-and-remaining-gates-t11c2) record the remaining SC limits.

A custom SuperCollider UGen/shared-memory streaming diagnostic is also available under `native/supercollider/`; it verifies finite stereo streaming and live control changes into a separate reader process. It is not connected to DAW transport or GUI controls. See [build, evidence and remaining routing gates](docs/decisions/supercollider.md#fixed-queue-prototype-t11c2a).

The [native streaming diagnostic](native/supercollider/native_probe.py) routes a live SC source through the Rust gain effect and native callback. It requires the built queue bridge and a `native-audio` binary, defaults to a muted monitor, and reports exact frame counts, sample-order fingerprints and underruns. It is separate from session transport; explicit live transport is described above. See [native routing evidence](docs/decisions/supercollider.md#source-to-native-diagnostic-t11c2b1).

## Prepared Csound tracks (schema v7)

Set `DAW_CSOUND_LIBRARY` to an absolute Csound 7 double-sample library path and run `python3 examples/csound_tracks_demo.py`. The example saves and reloads two embedded CSD programs, applies saved control events, renders through gain effects, and can use `--native` for a muted callback smoke test. Session load/replacement prepares each Csound source before commit; prepared PCM is reused for render and native playback at the session's sample rate. A source lasts 1 ms–10 seconds, and all SuperCollider/Csound sources together are limited to four sources, ten seconds total, 512 control points, and the shared 128 MiB decoded-audio budget.

Prepared Csound source playback uses the Csound 7 double-sample ABI in an owned Python worker. It requires 64-frame `ksmps`, stereo output, `0dbfs=1`, and the session sample rate. Saved named scalar controls and 64-frame-aligned step events are applied after `csoundStart`; frame-zero points override the saved base before the first perform call. This does not guarantee initialization-rate controls. In ordinary prepared playback, only the DAW's source/track gain and effects run; explicit live transport instead runs Csound on its owned producer worker. Set `DAW_CSOUND_PYTHON` to an absolute executable to select Python (default `python3`), or `DAW_CSOUND_WORKER` to an absolute preparation worker path (default the checkout worker). Csound 6 and other platforms are not verified. The browser GUI rejects schema-v7 sessions.

The [schema-v7 protocol section](docs/PROTOCOL.md#schema-v7-prepared-csound-sources) documents the saved shape, bounds, preparation and capabilities. These prepared tracks are separate from the standalone `csound.render` WAV job below.

## Csound offline jobs

Set `DAW_CSOUND` to an absolute Csound executable and run `python3 examples/csound_demo.py`. The demo renders two saved CSD fixtures, checks their frequency/gain changes, and verifies the active session/revision are preserved. `csound.render` is a synchronous Unix JSONL command; it validates a stereo PCM16 WAV before publishing to a fresh destination. Rates are 8000–192000 Hz, duration is at most ten seconds, and the caller supplies the exact expected frame count. CSD options are ignored; sample rate and a one-frame control block are imposed by the job runner.

`csound.render` remains a standalone WAV job: it uses `DAW_CSOUND`, writes validated PCM16 to a fresh destination, and does not load or change a session. It is distinct from schema-v7 source preparation, which uses `DAW_CSOUND_LIBRARY` and caches float64 PCM for prepared playback. Programs run with the caller's permissions. Relative assets/includes are not prepared; arbitrary opcodes and third-party plugins remain unverified. The [Csound contract](docs/decisions/csound.md) records the tested runtime and dependency notices.

A separate [Csound block diagnostic](native/csound/block_probe.py) accepts absolute `DAW_CSOUND_LIBRARY` for the tested Csound 7 double-sample API. It verifies host input/output buffers, frequency/gain channel readback and audio changes, finite score completion, and reset/recompile/destruction in an owned child process. It opens no audio hardware. Saved prepared Csound tracks use that library ABI in an owned worker; the explicit live mode owns a separate Csound producer and routes its fixed queue to the native callback. See [measured results and remaining gates](docs/decisions/csound.md#csound-7-blockcontrol-proof-t12a2).

The [Csound queue diagnostic](native/csound/stream_probe.py) adds a Csound-owned producer for the existing macOS arm64 fixed-queue ABI and compares a paced, finite consumer stream with the producer digest. Build it with `python3 native/csound/build_queue.py`, then run `DAW_CSOUND_LIBRARY=/absolute/CsoundLib64 python3 native/csound/stream_probe.py`. It uses 48 kHz, stereo 64-frame float32 queue blocks and bounded prefill/backpressure, but opens no hardware and is not connected to DAW transport or its native callback; the separate live transport now connects an owned producer through the Rust worker and callback. Schema-v7 GUI imports remain unsupported.

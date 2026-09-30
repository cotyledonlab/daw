# DAW

A minimal, agent-controllable DAW project. A local browser GUI sits on a Rust core with versioned sessions, a JSON Lines command interface, and offline stereo WAV rendering of built-in sine tracks.

Optional macOS builds support scripted schema-v4 sessions through VST3 effects offline, and an experimental in-process live VST3 mode. Audio Units, SuperCollider/Csound/Pure Data, and recording remain unavailable. Schema-v2 sine notes and PCM WAV clips can be sequenced through the scripting interface. The [plan](docs/PLAN.md) defines those next slices and their acceptance criteria. The [integration notes](docs/INTEGRATIONS.md) record the hosting options.

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

The GUI edits continuous sine sessions in v1 and v4. It can add serial gain effects, explicitly upgrading a v1 session to v4, and edit validated VST3 effects already present in loaded or applied sessions. The format is otherwise unchanged by editing. VST3 effects can be selected from a 64-entry in-memory catalog of effects seen in those sessions; the GUI does not scan plugins, install them, or browse the filesystem over HTTP. On macOS builds with `vst3-offline`, it can inspect metadata for those active-session effects and show the plugin's parameter names and units for saved parameters. The saved normalized values remain the editable base values; metadata's default and restored values are informational, while automation points are preserved as read-only data. Unavailable inspection leaves generic parameter labels; failed inspection also shows a per-effect error. It does not add unsaved plugin parameters. Bypass and removal are available, and removing an effect also removes its targeted gain automation lanes. Notes, audio clips, and schema-v2/v3 sessions are outside the GUI editing scope.

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

`gui/server.py` is a standard-library Python bridge; `gui/index.html`, `gui/style.css`, and `gui/app.js` are the browser interface. It exposes capabilities, native transport, session inspection/replacement, and temporary WAV downloads, rather than arbitrary engine filesystem commands. Requests require a per-launch token and exact loopback host/origin checks. The bridge is for trusted local use, not deployment on a public server. Its tests start a loopback HTTP server and need local socket permissions.

T09a/b provide a [standalone macOS VST3 lifecycle spike](native/vst3/README.md) with a project-owned fixture, child-process scanning, and real ValhallaFreqEcho offline automation/state checks. T09c connects VST3 effects to scripted session save/load and WAV rendering. T09d adds experimental native live playback; short silent hardware checks verify callbacks and transport, while acoustic output remains unverified. T09f adds bounded read-only metadata inspection and actual saved-parameter labels in the GUI. [AGENTS.md](AGENTS.md) gives future agents the working rules.

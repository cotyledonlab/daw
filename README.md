# DAW

A minimal, agent-controllable DAW project. A local browser GUI sits on a Rust core with versioned sessions, a JSON Lines command interface, and offline stereo WAV rendering of built-in sine tracks.

It does **not yet host VST3 or Audio Units**, run SuperCollider/Csound/Pure Data, record, or sequence clips. The [plan](docs/PLAN.md) defines those next slices and their acceptance criteria. The [integration notes](docs/INTEGRATIONS.md) record the hosting options.

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

Browser output is built-in sine audition. Native output below runs the Rust engine. Neither mode has a timeline or plugin hosting yet.

## Native playback (macOS)

```sh
cargo build --locked --features native-audio
target/debug/daw devices
target/debug/daw play path/to/session.json 2 0.25
```

This plays a saved session through the default CoreAudio output for the requested number of seconds (maximum 60). Ctrl+C stops it. The optional final argument is monitor volume, defaulting to 0.25. Device configuration and callback timing are reported. After this build, restart `python3 gui/server.py` and choose **Native audio** in the GUI. Play applies the draft and starts the Rust engine. The same button pauses/resumes; hold it or press Escape to Stop. Track editing is locked during native playback; listening volume remains adjustable. Native playback uses a fixed session snapshot and ends after 60 seconds of wall time, including time paused. Switching output stops the previous player in that window. Browser output remains available for immediate draft edits. See [native audio notes](docs/decisions/live-audio.md) for tested hardware and limitations.

## Develop

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
python3 -m unittest gui.test_server
node --test gui/test_live.cjs
```

`src/session.rs` owns the serializable model and validation. `src/control.rs` owns commands and persistence. `src/engine.rs` owns prepared block DSP and persistent oscillator phase; `src/render.rs` owns WAV encoding. `src/main.rs` owns bounded input framing and stdout responses. The offline renderer is a reference implementation, not a real-time audio callback.

`gui/server.py` is a standard-library Python bridge; `gui/index.html`, `gui/style.css`, and `gui/app.js` are the browser interface. It exposes capabilities, native transport, session inspection/replacement, and temporary WAV downloads, rather than arbitrary engine filesystem commands. Requests require a per-launch token and exact loopback host/origin checks. The bridge is for trusted local use, not deployment on a public server. Its tests start a loopback HTTP server and need local socket permissions.

T01–T04b and the T05 design are complete. The [timeline contract](docs/decisions/timeline.md) and checked reference fixtures guide T06 note sequencing in [docs/PLAN.md](docs/PLAN.md). [AGENTS.md](AGENTS.md) gives future agents the working rules.

# DAW

A minimal, agent-controllable DAW project. The first slice is a headless Rust scaffold: versioned sessions, a JSON Lines command interface, and offline stereo WAV rendering of built-in sine tracks.

It does **not yet host VST3 or Audio Units**, run SuperCollider/Csound/Pure Data, play live audio, record, sequence clips, or show a GUI. The [plan](docs/PLAN.md) defines those next slices and their acceptance criteria. The [integration notes](docs/INTEGRATIONS.md) record the hosting options.

## Run

Install Rust 1.85 or newer, then from this directory:

```sh
cargo build --locked
python3 examples/demo.py
```

The demo starts `target/debug/daw serve`, inspects capabilities, creates two sine tracks, saves the session, and renders one second to a fresh directory under `output/`. It prints the resulting paths. On macOS, use `afplay <printed WAV path>` to listen at a comfortable volume.

Send commands from any language that can launch a child process and read/write JSON. No embedded scripting language or agent framework is required:

```sh
printf '%s\n' '{"protocol_version":1,"id":"1","method":"capabilities"}' | cargo run --quiet --locked -- serve
```

Read the [protocol](docs/PROTOCOL.md) for all commands and errors. This is a local trusted-process interface with the caller's filesystem permissions. There is no network listener or authentication. IDs correlate responses; they do not provide deduplication. Rendering is synchronous and each invocation starts at frame zero.

## Develop

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
```

`src/session.rs` owns the serializable model and validation. `src/control.rs` owns commands and persistence. `src/render.rs` owns offline DSP and WAV encoding. `src/main.rs` owns bounded input framing and stdout responses. The offline renderer is a reference implementation, not a real-time audio callback.

Start the next implementation with ticket T01 in [docs/PLAN.md](docs/PLAN.md). [AGENTS.md](AGENTS.md) gives future agents the working rules.

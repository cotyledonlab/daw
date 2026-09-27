# Working on DAW

Read README.md, docs/PROTOCOL.md, and the selected ticket in docs/PLAN.md before editing. The current product has a browser GUI with live sine audition and Rust WAV export, plus optional macOS native playback through JSONL, the GUI, and CLI; plugin hosting is not implemented. Keep that distinction in capabilities and user-facing documentation.

- Implement one bounded ticket per logical commit. Use `type(scope): description`, stage only the task's files, and push working states when authentication is available. Use a `codex/` branch for risky changes.
- Prefer GPT-6 Luna for bounded examples, tests, documentation, and straightforward commands. Parallelize independent files when useful. One agent owns integration and shared file edits. Native hosting, real-time concurrency, and schema design need a stronger model's review.
- Preserve versioned JSON contracts and structured errors. Validate fully before changing the active session. Never overwrite outputs implicitly. Stdout belongs to the protocol.
- Do not add speculative format stubs, generic processor traits, or unimplemented commands that claim success. Establish an interface from a second working adapter.
- No allocation, locks, filesystem/network access, subprocess startup, or foreign-code initialization in future audio callbacks. The current offline renderer is not suitable for direct callback use.
- Run `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and `cargo test --locked`. For a client change, also run `cargo build --locked` and `python3 examples/demo.py`. Use `--offline` with an available cache when networking is unavailable.
- Keep generated audio under ignored `output/`. Do not commit recordings, temporary app state, credentials, or plugin binaries.
- For live-player changes, run `node --test gui/test_live.cjs`. Browser audition is separate from native engine capabilities; do not claim live plugin support.
- For GUI changes, run `python3 -m unittest gui.test_server` and exercise the browser against the real Rust engine. The Python bridge requires Python 3.10+ and loopback socket access. Keep HTTP file access constrained to browser uploads/downloads, preserve per-launch token/host/origin checks, and keep the Rust model authoritative.
- Update ticket status, protocol examples, and limitations with the change. Record unverified platform behavior honestly. Routine reversible implementation needs no extra approval.

- For native-audio changes on macOS, also run `cargo clippy --locked --features native-audio --all-targets -- -D warnings` and `cargo test --locked --features native-audio`. Hardware smoke tests require host audio access; keep tones quiet and bounded. Record callback evidence separately from acoustic verification.

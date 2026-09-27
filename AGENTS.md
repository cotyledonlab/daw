# Working on DAW

Read README.md, docs/PROTOCOL.md, and the selected ticket in docs/PLAN.md before editing. The current product is an offline scaffold, not a live DAW or plugin host. Keep that distinction in capabilities and user-facing documentation.

- Implement one bounded ticket per logical commit. Use `type(scope): description`, stage only the task's files, and push working states when authentication is available. Use a `codex/` branch for risky changes.
- Prefer GPT-6 Luna for bounded examples, tests, documentation, and straightforward commands. Parallelize independent files when useful. One agent owns integration and shared file edits. Native hosting, real-time concurrency, and schema design need a stronger model's review.
- Preserve versioned JSON contracts and structured errors. Validate fully before changing the active session. Never overwrite outputs implicitly. Stdout belongs to the protocol.
- Do not add speculative format stubs, generic processor traits, or unimplemented commands that claim success. Establish an interface from a second working adapter.
- No allocation, locks, filesystem/network access, subprocess startup, or foreign-code initialization in future audio callbacks. The current offline renderer is not suitable for direct callback use.
- Run `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, and `cargo test --locked`. For a client change, also run `cargo build --locked` and `python3 examples/demo.py`. Use `--offline` with an available cache when networking is unavailable.
- Keep generated audio under ignored `output/`. Do not commit recordings, temporary app state, credentials, or plugin binaries.
- Update ticket status, protocol examples, and limitations with the change. Record unverified platform behavior honestly. Routine reversible implementation needs no extra approval.

# DAW

A small, agent-controllable, macOS-first arrangement DAW. A Rust engine owns validated sessions and audio rendering; a local browser editor and a versioned JSON Lines interface share that engine.

The musical MVP is implemented: drum/bass/lead arrangements, note and audio editing, native transport, mixer, filter/delay, gain automation, clip fades, portable projects, keyboard/Web MIDI note entry and overdub recording. One optional sequenced Pure Data instrument is also delivered. Remaining acceptance and blocking fixes are in [the plan](docs/PLAN.md).

## Run

Install Rust 1.85+ and Python 3.10+. On macOS:

```sh
cargo build --locked --features native-audio
python3 gui/server.py
```

The server opens the editor. Choose **Open musical demo** for a 16-bar drum/bass/lead arrangement, or **New arrangement** to start from scratch. Select a clip on the timeline or in **Selected clip** (including overlapping copies) to edit notes; drag clips/notes to move or resize them, or use exact numeric fields. Completed edits support Undo/Redo.

Expand **Studio agents** for prompt controls and **Metronome & count-in** for listening-click settings. Overlapping clips have dashed amber borders and a lane count; use **Selected clip** to reach a covered clip.

**Play** starts native playback and pauses/resumes it; **Stop** or Escape releases playback. Native built-in arrangements support live mixer, sound/effect, automation, note and clip edits, including Undo/Redo. Edits reach playback after the short buffered queue and use a brief crossfade; held notes and compatible effect tails continue. Adding/removing tracks, changing device kind/rate, foreign runtimes and loading another project require Stop. Eligible arrangements play until stopped, with temporary seek/loop controls. Native note/audio playback requires the default device rate to match the session rate.

Enable **Play keyboard / MIDI notes** to preview the selected instrument while stopped. **A W S E D F T G Y H U J K** play a chromatic octave; drum tracks use **A / S / D** for kick/snare/hat. **Connect MIDI** requests browser MIDI access. **Step entry** inserts notes at the clip-relative cursor. **Record notes** captures held keyboard/MIDI gates into the selected clip from timeline zero with optional metronome/count-in. Stop applies the take as one undo entry; rejected takes can be retried or discarded. Recording has no live input monitoring; physical MIDI and measured latency remain unverified.

Import matching-rate mono/stereo integer PCM16/24/32 WAVs into audio lanes. The mixer saves level, pan, mute and solo. Lowpass/delay, step gain automation and audio clip fades are available. **Listening volume** affects monitoring only; exports use saved mix levels. Built-in/PCM/Pd-instrument projects with built-in effects can export up to 180 seconds; foreign-device limits are narrower.

## Integrated studio prompts

The **Studio agents** panel connects to OpenCode Zen using `OPENCODE_API_KEY` from the server environment. Restart the server after setting credentials. Keys stay on the server; they are never embedded in the page or saved project.

Choose **Producer** to coordinate up to two independent specialist calls, **Sound engineer** for mixer/processing/fades/automation, or **Studio musician** for tracks/clips/notes and relative transposition. Choose whole-session, selected-track or selected-clip scope. Try “lower the bass by 3 dB” or “transpose this clip up an octave.” Edits apply as one checked, undoable batch, including during supported native arrangement playback. Typed drafts, pending takes and project/selection changes during inference block the edit without discarding your work. Failed or unsupported plans make no changes. Download the project to keep applied edits.

Producer prompts also reach the existing playback, seek/loop, monitoring, Undo/Redo, note recording/preview, import/load file pickers, save and WAV export controls. Commands run individually; file prompts reveal a studio file button so the browser opens its picker from your click. New/demo arrangements retain the existing replacement confirmation. Arbitrary runtime programs, plugin hosting changes and unsupported features remain available only through their existing interfaces; agents cannot run shell commands or access arbitrary files. The engineer sees session data, not an audio feed, and cannot certify acoustic listening.

Optional `ELEVEN_API_KEY` (also accepts `ELEVENLABS_API_KEY`) enables **Record voice prompt** and **Speak replies**. Recording requires browser microphone access, ends after 30 seconds and is limited to 4 MiB. Review the transcript before sending it. This is push-to-talk with spoken replies, not a continuous voice call. Text control works without voice. `DAW_STUDIO_VOICE_ID` selects an ElevenLabs voice; `DAW_STUDIO_MODEL` selects a Zen model supporting the chat-completions endpoint (default `glm-5.3-flash`).

Prompts, recent conversation and session JSON go to [OpenCode Zen](https://opencode.ai/docs/zen/); microphone recordings and reply text go to [ElevenLabs](https://elevenlabs.io/docs/api-reference/speech-to-text/convert). These calls use your provider accounts. WAV assets themselves are not sent to Zen. Conversation is bounded and kept only in the current browser window; **Clear conversation** clears it. It is not part of the project file.

## Save your work

Some device/effect fields remain drafts until **Apply changes**; arrangement and mixer edits apply through checked updates. Applied engine state is still in memory. **Save session** downloads JSON for asset-free sessions or a portable ZIP containing audio assets. **Load session** accepts both. **Export WAV** applies pending edits and downloads a stereo WAV.

Use one editing window: tabs share the engine, and stale writes are rejected. Refresh discards unapplied edits; stopping the server discards the in-memory project and temporary audio assets. Download your project before closing the server.

The bridge binds to `127.0.0.1` and chooses a free port. For a fixed port or manual browser opening:

```sh
python3 gui/server.py --port 8765 --no-open
```

## Scripting and optional devices

For portable headless use:

```sh
cargo build --locked
python3 examples/demo.py
```

Launch `target/debug/daw serve` from any language and exchange one JSON request/response per line. Inspect `capabilities` for build-dependent support. See [the protocol](docs/PROTOCOL.md) for commands, schemas, timing, transport and limits; [audio projects](docs/AUDIO-PROJECTS.md) for WAV/ZIP ownership and validation.

Optional integrations are existing compatibility paths; they are not MVP prerequisites:

| Integration | Current scope |
| --- | --- |
| Sequenced Pd instrument | One 48 kHz monophonic **Filtered Sine** preset in the arrangement. Requires a configured multi-instance libpd library via `DAW_LIBPD_LIBRARY`; missing runtime rejects without changing the project. |
| SuperCollider / Csound / continuous Pd | Prepared scripted sources and constrained macOS arm64 live transport. Live runtime pause/seek/loop are unsupported; legacy continuous Pd live-control acceptance is unfinished. |
| VST3 | Constrained macOS offline effects and experimental live effects; saved-effect metadata/parameter editing. No plugin editor, instrument hosting or GUI discovery. [Build notes](native/vst3/README.md). |
| Audio Units | Apple's AULowpass offline only; no GUI or live playback. [Build notes](native/au/README.md). |

Browser continuous sine audition and stopped engine-rendered note previews are separate from native arrangement transport. Audio recording, quantize/MIDI files, sampled instruments and broader studio features are outside the MVP.

## Develop

Read [AGENTS.md](AGENTS.md) for implementation and PR review/merge instructions. [docs/PLAN.md](docs/PLAN.md) is the only task checklist. Historical plans and reviews are available in Git history.

`src/session.rs` owns the model/validation; `src/control.rs` owns commands/persistence; `src/engine.rs` owns prepared DSP; `src/render.rs` owns WAV output; `src/audio.rs` owns native playback. `gui/server.py` is the Python standard-library loopback bridge; `gui/` contains the plain JavaScript/CSS editor. There is no frontend build step.

Generated test/demo exports, screenshots and logs live in ignored `output/`; keep reusable fixtures in `tests/`, `gui/test_*`, `native/` and `examples/`. Remove individual obsolete output directories after checking their contents. `output/` also holds optional installed runtimes, SDKs, native hosts and requested media projects, so do not clear it wholesale. With Cargo idle, `target/debug/incremental/` and Python `__pycache__/` directories can be removed; they regenerate on demand. Keep historical plans and reviews in Git history rather than adding archive folders.

Core checks (Node 22+ for browser tests):

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked
python3 examples/check_timeline_contract.py
python3 -m unittest discover -s gui -p 'test_*.py'
node --test gui/test_*.cjs
```

On macOS, also run native-audio Clippy/tests when changing audio or transport. `python3 examples/live_editing_workflow_demo.py` checks muted live edits, rollback, pause/resume and portable ZIP/fresh-server export after a native-audio build. Installed-runtime, FFI and hardware tests are opt-in and relevant only when those paths change. The [CI workflow](.github/workflows/ci.yml) defines the portable/macOS checks. Muted playback, meters and deterministic exports establish data/callback behavior; quiet listening is still needed for acoustic acceptance.

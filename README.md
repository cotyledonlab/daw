# DAW

A small, agent-controllable, macOS-first arrangement DAW. A Rust engine owns validated sessions and audio rendering; a local browser editor and a versioned JSON Lines interface share that engine.

The musical MVP is implemented: drum/bass/lead arrangements, note and audio editing, native transport, mixer, filter/delay, gain automation, clip fades, portable projects, keyboard/Web MIDI note entry and overdub recording. One optional sequenced Pure Data instrument is also delivered. Remaining acceptance and blocking fixes are in [the plan](docs/PLAN.md).

## Run

Install Rust 1.85+ and Python 3.10+. On macOS:

```sh
cargo build --locked --features native-audio
python3 gui/server.py
```

The server opens the editor. Choose **Demo** for a 16-bar drum/bass/lead arrangement, or **New** to start from scratch. Select a clip on the timeline or in **Selected clip** (including overlapping copies) to edit notes; drag clips/notes to move or resize them, or use exact numeric fields. Completed edits support Undo/Redo.

The editor follows your system’s light or dark appearance automatically. Select a note clip, choose **Grid**, and use **Quantize clip** to snap all note starts from clip zero as one undoable edit. Durations, pitch and velocity stay exact; No snap disables quantize. A gate that would extend past the clip end rejects the whole edit. Apply or revert pending drafts first.

**Export clip MIDI** downloads the selected note clip as a format-0 `.mid` file from clip zero, at 960 ticks per beat with session tempo and 4/4 time. Pitch rounds to MIDI semitones, velocity to 1–127, and timing to ticks (gates stay at least one tick); silent notes are omitted. Drum clips use channel 10. Same-pitch overlaps after rounding reject export. Apply or revert drafts first. This download carries notes rather than instrument sounds, effects, audio assets or mix; save your project to retain those. **Import MIDI** reads format-0/1 PPQ files up to 1 MiB into new lanes at **Insert at** (beat 0 in an empty project). Notes follow the current project tempo; source tempo, sounds, controllers, pitch bend and SysEx are not applied. Each source track/channel becomes a lane, with channel 10 using the three-pad drum kit (36/38/42 only). Import requires stopped playback and no drafts or pending take, applies atomically, selects the first imported clip and supports whole-import Undo/Redo. Malformed/unfinished or ambiguous same-channel/pitch gates reject the whole file. Existing project limits still apply.

Tempo, position, loop and Undo/Redo share the transport strip. Tempo and position apply on Enter or when you leave the field; loop endpoints and clip start/length apply on Enter. Selected-note fields apply together with Enter or the contextual **Apply note** button; **+ Note** appears only when no note is selected. Numeric drafts retain an underline, and rejected values stay editable with a red border and inline feedback. Mixer number drafts apply together on Enter, blur or **Apply numbers**. **Clip help**, **Keyboard & MIDI help** and **Editing & saving help** remain available below the work area; less obvious controls have hover hints.

Expand **Studio agents** for prompt controls and **Metronome & count-in** for listening-click settings. Overlapping clips have dashed amber borders and a lane count; use **Selected clip** to reach a covered clip.

**Play** starts native playback and pauses/resumes it; **Stop** or Escape releases playback. Native built-in arrangements support live mixer, sound/effect, automation, note and clip edits, including Undo/Redo. Edits reach playback after the short buffered queue and use a brief crossfade; held notes and compatible effect tails continue. Adding/removing tracks, changing device kind/rate, foreign runtimes and loading another project require Stop. Eligible arrangements play until stopped, with temporary seek/loop controls. Native note/audio playback requires the default device rate to match the session rate.

Enable **Keys** to preview the selected instrument while stopped. **A W S E D F T G Y H U J K** play a chromatic octave; drum tracks use **A / S / D** for kick/snare/hat. **MIDI · off** requests browser MIDI access. **Step entry** inserts notes at the clip-relative cursor. **Record notes** captures held keyboard/MIDI gates into the selected clip from timeline zero with optional metronome/count-in. Stop applies the take as one undo entry; rejected takes can be retried or discarded. Recording has no live input monitoring; physical MIDI and measured latency remain unverified.

Import matching-rate mono/stereo integer PCM16/24/32 WAVs into audio lanes. The mixer saves level, pan, mute and solo. Lowpass/delay, step gain automation and audio clip fades are available. **Listening volume** affects monitoring only; exports use saved mix levels. Built-in/PCM/Pd-instrument projects with built-in effects can export up to 180 seconds; foreign-device limits are narrower.

## Integrated studio prompts

The **Studio agents** panel connects to OpenCode Zen using `OPENCODE_API_KEY` from the server environment. Restart the server after setting credentials. Keys stay on the server; they are never embedded in the page or saved project.

Choose **Producer** for one musical brief that is split internally into up to four small specialist tasks, **Sound engineer** for mixer/processing/fades/automation, or **Studio musician** for tracks/clips/notes and relative transposition. Choose whole-session, selected-track or selected-clip scope. Try “lower the bass by 3 dB” or “transpose this clip up an octave.” Edits apply as one checked, undoable batch, including during supported native arrangement playback. Typed drafts, pending takes and project/selection changes during inference block the edit without discarding your work. Failed or unsupported plans make no changes. Download the project to keep applied edits.

While a prompt runs, the panel shows the active role/model, elapsed time, delegation tasks and each completed agent’s proposal summary. Proposals apply only after the complete batch passes validation. A failed specialist leaves earlier summaries visible and applies no edits. Provider errors distinguish HTTP status, timeout, DNS, TLS and connection failures with role/model context; credentials and upstream response bodies stay private.

The producer plans from a compact track/clip overview and passes a shared musical direction to the specialists. Musical tasks can narrow to an existing track or clip, so a whole-song style prompt can work on the parts separately and finish with the mix. For originally identical repeated clips, a musician can edit one pattern and copy its notes across the matching clips without moving them or overwriting different harmonies. Each delegated task is capped at 32 operations; the producer's direct plan at 16. The final result still applies once as one Undo entry, with nothing applied if any task fails. Each inference request uses a 120-second socket timeout; the complete interaction may take several minutes. Restart a running server after updating its Python code.

Producer prompts also reach the existing playback, seek/loop, monitoring, Undo/Redo, note recording/preview, import/load file pickers, save and WAV export controls. Commands run individually; file prompts reveal a studio file button so the browser opens its picker from your click. New/demo arrangements retain the existing replacement confirmation. Arbitrary runtime programs, plugin hosting changes and unsupported features remain available only through their existing interfaces; agents cannot run shell commands or access arbitrary files. The engineer sees session data, not an audio feed, and cannot certify acoustic listening.

Optional `ELEVEN_API_KEY` (also accepts `ELEVENLABS_API_KEY`) enables **Record voice prompt** and **Speak replies**. Recording requires browser microphone access, ends after 30 seconds and is limited to 4 MiB. Review the transcript before sending it. This is push-to-talk with spoken replies, not a continuous voice call. Text control works without voice. `DAW_STUDIO_VOICE_ID` selects an ElevenLabs voice; `DAW_STUDIO_MODEL` explicitly selects a Zen model supporting the chat-completions endpoint. With no override, each prompt checks Zen’s live catalog and prefers the inexpensive `glm-5.3-flash` with low reasoning effort, then `space-bunny-free`, then `big-pickle` if Flash is not listed. A catalog outage retains the last selection (initially GLM Flash); inference errors remain visible and do not trigger a hidden model retry. Producer and specialists use the same selection for the entire prompt. Zen does not publish quality rankings or stealth labels in its catalog: this is a maintained preference order, not automatic quality or speed discovery. GLM Flash is billed through your Zen account; see [current Zen pricing](https://opencode.ai/docs/zen/#pricing).

Prompts, recent conversation and session JSON go to [OpenCode Zen](https://opencode.ai/docs/zen/); microphone recordings and reply text go to [ElevenLabs](https://elevenlabs.io/docs/api-reference/speech-to-text/convert). These calls use your provider accounts. WAV assets themselves are not sent to Zen. Conversation is bounded and kept only in the current browser window; **Clear conversation** clears it. It is not part of the project file.

## Save your work

Some device/effect fields remain drafts until **Apply device changes**; arrangement and mixer edits apply through checked updates. Applied engine state is still in memory. **Save** (or **Save ZIP** for audio projects) downloads JSON for asset-free sessions or a portable ZIP containing audio assets. **Load** accepts both. **Export WAV** applies pending edits and downloads a stereo WAV.

Use one editing window: tabs share the engine, and stale writes are rejected. Refresh discards unapplied edits; stopping the server discards the in-memory project and temporary audio assets. Download your project before closing the server.

The bridge binds to `127.0.0.1` and chooses a free port. For a fixed port or manual browser opening:

```sh
python3 gui/server.py --port 8765 --no-open
```

## Private phone UI through Tailscale

Connect the Mac and phone to your tailnet. Keep the existing DAW running and note its loopback port. Start the gateway in another terminal (replace `8789` and the hostname with your actual values):

```sh
python3 gui/tailscale_ui.py --upstream-port 8789 --origin https://your-mac.your-tailnet.ts.net
tailscale serve --bg http://127.0.0.1:8790
```

On macOS, if `tailscale` is absent from PATH, use `/Applications/Tailscale.app/Contents/MacOS/Tailscale`. Open the printed Tailscale HTTPS URL in the phone browser. Serve may require enabling HTTPS for the tailnet. The gateway accepts only that exact root HTTPS origin/host and retains the DAW's token checks; it owns no engine or project. Use private [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve), with access controlled by your tailnet policy. Anyone your policy allows to reach this URL can use the editor and its configured provider accounts. Stop this route with `tailscale serve --https=443 off`; Ctrl+C stops the gateway without stopping the DAW.

Use one active editing window; the phone controls the same in-memory session, with existing revision checks. Native playback remains on the Mac. Browser previews, downloaded files and optional voice UI use the viewing device; this does not add live audio streaming or change phone MIDI/microphone support. The Mac, DAW and gateway must remain running. No server restart is needed to expose an existing session.

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

Browser continuous sine audition and stopped engine-rendered note previews are separate from native arrangement transport. Audio recording, sampled instruments and broader studio features are outside the MVP.

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

# VST3 hosting decision record

> Historical archive. Retained for evidence, not agent instructions or current scope. Old next steps, model assignments and expansion proposals are superseded by [the active plan](../../PLAN.md) and [current contracts](../../PROTOCOL.md). Do not implement archived proposals without a current task.

## Current result

The reference probes under [`native/vst3`](../../../native/vst3/README.md) remain diagnostic tools. T09c now loads and renders scripted schema-v4 VST3 effect sessions through owned children. An optional experimental native player hosts VST3 effects through a dedicated DSP worker; the browser GUI exposes controls for effects loaded from saved continuous sine sessions. The engine remains authoritative, and its offline renderer is not suitable for direct audio-callback use.

The spike uses Steinberg's official `pluginterfaces` source pinned to commit `31d6eeba6daaa3e2a8bfbe3e7a90ca0b7fbfbc1c` (`v3.8.0_build_66`). It builds with the system `clang++` and CoreFoundation, without CMake. The build copies the SDK's MIT license notice to `output/vst3-spike/STEINBERG-LICENSE.txt`; generated SDK material and binaries stay under ignored `output/`.

The project-owned fixture provides offline evidence for stereo processing, sample-offset parameter and note events, component state restoration (including restoration into a fresh instance), and teardown. Factory scanning accepts explicit third-party VST3 bundle paths and returns factory metadata. Dexed and ValhallaFreqEcho have been scanned successfully. The new effect probe processes ValhallaFreqEcho through automation and fresh-instance state restoration; Dexed processing is unverified. Scanning runs in a child process with bounded timeout and stdout/stderr capture; timeout or crash handling kills the process group. This limits a failed scan's effect on the controller but is not a sandbox, and plugin code runs with the caller's permissions.

The spike does not cover editor windows, instrument processing, general layouts, editor commands or real-time callbacks. Separate effect controllers and their connection handshake are now covered. Combined controller metadata/state, text conversion, UI/DSP parameter separation, and balanced host-handler references are verified by the fixture probe. The [pinned Rust candidate](vst3-rust-trial.md) builds and renders the same installed effect offline; its automation/state and callback paths have not received equivalent validation.

## Decision

Expose the completed offline adapter through `offline_vst3` capabilities and schema-v4 JSONL rendering. Experimental live plugin-hosting capabilities are now advertised by macOS `vst3-live` builds; the adapter record describes the bounded worker/callback evidence and remaining limits. The original scanner/probes remain diagnostics.

Use the existing C++ child as the reference for the next bounded offline adapter: its lifecycle, state bounds, failure containment, and effect automation have direct tests here. The Rust candidate remains viable, but a short render does not validate the same ownership/state constraints. This is a choice for the offline slice, not a decision about production callback hosting. No new dependency enters the portable Rust core in this ticket.

## Original delivery steps (completed)

1. **T09c: offline session integration.** Follow [the adapter boundary](vst3-adapter.md): pin exact identity, validate buses/state/parameters before output creation, feed prepared track audio into an owned bounded child, and propagate explicit failures. Introduce session/protocol fields only with working processing. Preserve fresh outputs, transactional replacement, and portable builds.
2. **T09d: native ownership and callback review.** Compare the Rust `RealtimePluginRunner` and a thin C++ shim against DAW-owned buffers, automation queues, teardown handoff, and callback allocation/timing. Choose playback isolation separately. Plugin loading, state, and destruction remain off callback. Add live capability only after measured integration.

These steps do not imply blanket VST3 compatibility. Unsupported layouts, plugin behaviors, or lifecycle operations must fail clearly. Plugin editor support and general sandboxing require separate design and evidence.

## Verification

On macOS on 2026-09-30, the host and fixture built with `-Wall -Wextra -Werror`, both normally and with address/undefined-behavior sanitizers. Three Python integration tests passed in both builds: factory metadata and ten lifecycle probes; failure containment while an independent Rust controller remained responsive; and invalid/oversized child output rejection. Each lifecycle probe checked 256 stereo frames, UI/DSP parameter separation, controller synchronization, fresh-instance state, and host interface reference balance. The portable and native-feature Rust fmt/clippy/test checks passed. No plugin audio device was opened, and no acoustic or real-time plugin claim follows from these tests.

T09b adds five passing integration tests in normal and sanitizer builds, including five repeated ValhallaFreqEcho effect runs per suite. Each effect run processed 32768 stereo frames at 48 kHz, with finite samples, automation/output differences, a restored parameter, connection teardown, and balanced host references. The bounded state stream also checks short reads, EOF, invalid/overflow seeks, and rejection of over-limit writes without mutation. The Rust candidate produced 11025 stereo float frames at 44.1 kHz, all finite, with peak 0.0227149; its effect response, automation, and state were not checked. No acoustic or callback evidence is claimed.

T09c now implements the [offline session boundary](vst3-adapter.md). Existing JSONL replacement/load/save/render commands drive captured state and serial plugin automation; no callback or GUI plugin support is claimed. The stronger model owns native lifecycle integration, with Luna handling schema/examples and bounded test files. T09d now implements experimental native playback; see the adapter record for measured evidence and its in-process limits.

T09c verification on 2026-09-30: portable, native-audio, and combined native-audio/vst3-offline Rust fmt/clippy/test checks passed. Ten Python worker/controller integration tests passed in normal and address/undefined sanitizer builds, including all five DAW tests with no skips and the installed Valhalla effect test. The scripted Valhalla example saved/reloaded captured state and produced identical PCM in this run. Six browser live-player lifecycle tests passed. No GUI plugin controls or acoustic/live-plugin evidence is claimed.

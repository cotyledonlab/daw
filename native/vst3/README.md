# macOS VST3 offline spike

This directory contains a standalone experiment for loading and inspecting VST3 bundles on macOS. The original diagnostic probes remain standalone. T09c adds a private worker used by scripted schema-v4 session validation and offline rendering. The GUI and playback do not host plugins; live plugin capability remains unavailable.

The spike uses Steinberg's official `pluginterfaces` repository at commit `31d6eeba6daaa3e2a8bfbe3e7a90ca0b7fbfbc1c`, tag `v3.8.0_build_66`. The build intentionally avoids CMake: `build.py` invokes the system `clang++` and links CoreFoundation. The SDK checkout, binaries, fixture bundle, and copied `STEINBERG-LICENSE.txt` are all placed under ignored `output/`.

On macOS, build with:

```sh
python3 native/vst3/build.py --fetch-sdk
```

If the pinned SDK checkout already exists, omit `--fetch-sdk`. The script rejects a missing SDK unless fetching is requested, and rejects a different or modified revision. `--sanitize` enables address and undefined-behavior sanitizers.

Build the Rust controller used by the isolation check, then run the fixture lifecycle tests:

```sh
cargo build --locked
python3 -m unittest native.vst3.test_spike
```

The `--probe` mode accepts only the project-owned `DawTestGain.vst3` fixture. It exercises stereo buses, 48 kHz setup, 64-sample processing blocks within a 256-frame maximum block setup, sample-offset parameter changes, a note event at offset 48, state save/restore, restoration into a fresh instance, and orderly teardown. The fixture and test report are offline checks; they provide no evidence about acoustic output or real-time callback suitability.

Factory scanning is separate. Pass explicit bundle paths to `scan.py`; it loads each bundle in a child process and reports factory class identifiers, names, and categories. Scanning does not process third-party audio. For example, Dexed and ValhallaFreqEcho were successfully scanned for factory metadata, and the effect probe now processes ValhallaFreqEcho. Dexed processing remains unverified.

```sh
python3 native/vst3/scan.py /path/to/Plugin.vst3
```

The child process is bounded by a timeout of at most 30 seconds and stdout/stderr limits of 64 KiB each. Timeout and crash handling kills the child process group and returns a structured result. This is process-level failure containment, not a security sandbox: plugin code runs with the user's permissions. Only the fixture may be passed to `--probe` processing.

The fixture covers a combined component/controller, parameter metadata and text conversion, component-to-controller state synchronization, and UI-only parameter changes that leave DSP untouched until queued automation arrives. Host handler references are cleared before termination. The effect probe also handles separate controllers and bidirectional connection points. Editors, instruments, generic layouts, real-time callbacks, DAW commands, and a production adapter remain unimplemented. The pinned Rust host candidate also builds and completes a short offline effect render; see its trial notes for the more limited evidence. See [the binding comparison](../../docs/decisions/vst3-bindings.md) and [the hosting decision record](../../docs/decisions/plugin-hosting.md).

## Third-party stereo effect probe

```sh
python3 native/vst3/scan.py --effect-probe --timeout 15 /path/to/Effect.vst3
DAW_VST3_EFFECT=/path/to/Effect.vst3 python3 -m unittest native.vst3.test_spike native.vst3.test_effect
```

This mode requires exactly one audio class, one stereo input/output bus, no event buses, float32 offline processing at 48 kHz, and maximum blocks of 256 frames. It selects the first continuous automatable parameter (excluding bypass/read-only), queues values at offset 16, and requires measurable output changes. It captures component state and optional controller state, each bounded to 8 MiB; restores a fresh instance; verifies the saved parameter; and compares restored DSP output with an automation override from another fresh instance. It processes 32768 frames in total per run. Parameter-value restart notifications use current controller reads; requests for graph, layout, latency, or metadata changes fail explicitly. Latency is reported, not compensated.

ValhallaFreqEcho passed five repeated probes in both normal and sanitizer builds. Its `wetDry` parameter (ID 48) changed output, and 534 bytes of component state restored the normalized value 0.8. Its separate controller has connection points and returns `kNotImplemented` for component-state synchronization; the host accepts that only when a connection exists and the restored value is already correct before independent controller state is applied. This is evidence for this installed plugin, not general compatibility or exact sample-offset response in third-party DSP. No device was opened.

## Session worker

Build the Rust controller with `--features vst3-offline` (combine with `native-audio` for existing built-in playback). `src/hosting.rs` drives the private binary `process BUNDLE CID` job; use JSONL session/render commands rather than invoking binary jobs manually. See [schema v4](../../docs/PROTOCOL.md) and [the example](../../examples/vst3_demo.py). The worker validates exact class identity and parameter IDs, restores state, processes stereo blocks, captures bounded state, and exits before Rust accepts successful output. Session limits are stricter than the diagnostic probe: 64 KiB state blobs and zero latency, with ten-second renders.

```sh
DAW_VST3_EFFECT=/path/to/ValhallaFreqEcho.vst3 python3 -m unittest native.vst3.test_daw
```

The integration tests use a project-owned gain fixture with event buses disabled in the test environment. They verify exact frame automation, state persistence, native rejection, bad identities/state/parameters, no-overwrite and duration errors, and child crash/timeout rollback. The optional installed-effect test exercises the real Valhalla session through the Rust controller.

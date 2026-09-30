# macOS VST3 offline spike

This directory contains a standalone experiment for loading and inspecting VST3 bundles on macOS. It is not integrated with the DAW engine, session format, GUI, or playback. DAW capabilities and user-facing documentation must continue to report plugin hosting as unavailable.

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

The only bundle accepted for processing is the project-owned `DawTestGain.vst3` fixture. It exercises stereo buses, 48 kHz setup, 64-sample processing blocks within a 256-frame maximum block setup, sample-offset parameter changes, a note event at offset 48, state save/restore, restoration into a fresh instance, and orderly teardown. The fixture and test report are offline checks; they provide no evidence about acoustic output or real-time callback suitability.

Factory scanning is separate. Pass explicit bundle paths to `scan.py`; it loads each bundle in a child process and reports factory class identifiers, names, and categories. Scanning does not process third-party audio. For example, Dexed and ValhallaFreqEcho were successfully scanned for factory metadata, but were not processed.

```sh
python3 native/vst3/scan.py /path/to/Plugin.vst3
```

The child process is bounded by a timeout of at most 30 seconds and stdout/stderr limits of 64 KiB each. Timeout and crash handling kills the child process group and returns a structured result. This is process-level failure containment, not a security sandbox: plugin code runs with the user's permissions. Only the fixture may be passed to `--probe` processing.

The fixture covers a combined component/controller, parameter metadata and text conversion, component-to-controller state synchronization, and UI-only parameter changes that leave DSP untouched until queued automation arrives. Host handler references are cleared before termination. Editors and separate-controller hosting are not covered. Generic third-party processing, real-time callbacks, DAW commands, and a production adapter are not implemented. The choice between production C++ and Rust remains open; the Rust candidates have not been built here, so no comparative runtime evidence exists. See [the binding comparison](../../docs/decisions/vst3-bindings.md) and [the hosting decision record](../../docs/decisions/plugin-hosting.md).

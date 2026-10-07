# macOS VST3 workers

This directory provides constrained schema-v4 offline effect workers, read-only saved-effect metadata and an experimental live DSP bridge. Standalone scanners/probes are diagnostics. No GUI discovery, plugin editors, instruments or general bus-layout support. Current limits and saved state/parameter contracts are in [the protocol](../../docs/PROTOCOL.md#schema-v4-offline-vst3-effects).

## Build and check

```sh
python3 native/vst3/build.py --fetch-sdk
cargo build --locked --features vst3-offline
python3 -m unittest native.vst3.test_spike native.vst3.test_daw native.vst3.test_metadata
python3 examples/vst3_demo.py --help
```

The SDK is Steinberg's official `pluginterfaces` repository at commit `31d6eeba6daaa3e2a8bfbe3e7a90ca0b7fbfbc1c` (`v3.8.0_build_66`). The script invokes system `clang++` and CoreFoundation without CMake; SDK, binaries, fixture and copied license stay in ignored `output/`. Omit `--fetch-sdk` for an existing pinned checkout; modified/different revisions reject. `--sanitize` enables ASan/UBSan. Rust features do not build/install workers. `DAW_VST3_HOST` selects an alternate worker by absolute executable path.

`scan.py /absolute/path/Plugin.vst3` reports factory metadata through a bounded child process. `--probe` processes only the project-owned `DawTestGain.vst3` fixture; `--effect-probe` tests an explicitly supplied stereo effect. These jobs contain process failures but run with the user's permissions. A successful scan is not processing compatibility. Installed-effect tests require explicit opt-in; the session test below expects ValhallaFreqEcho:

```sh
DAW_VST3_EFFECT=/absolute/path/ValhallaFreqEcho.vst3 python3 -m unittest native.vst3.test_effect native.vst3.test_daw
```

`examples/vst3_demo.py` accepts an installed bundle path, its 32-character uppercase class ID and an optional parameter ID; `--help` lists the arguments.

Session workers require exact class identity, stereo float32 at 48 kHz, zero latency, no event buses and blocks up to 256 frames. Renders are at most ten seconds; saved state is capped at 64 KiB per blob and 256 KiB per session. `src/hosting.rs` owns jobs; use JSONL session/render commands instead of private binary worker jobs. Metadata inspection reads only effects/parameter IDs already saved in the active session and never changes revision or saved values.

## Experimental live effects

The build also produces `libdaw-vst3.dylib`. Rust `vst3-live` implies `vst3-offline` and `native-audio`. Plugin DSP runs on an owned worker; CoreAudio consumes a fixed audio queue. Live parameter edits target saved, automatable parameters without saved automation; acceptance commits the base/revision before DSP acknowledgment. Stop/failure can cancel delivery while accepted saved bases remain. See [live parameter edits](../../docs/PROTOCOL.md#live-edits-to-saved-vst3-parameters) for queue, timing and failure details.

```sh
cargo build --locked --features vst3-live
python3 -m unittest native.vst3.test_live
```

Hardware checks additionally require an available 48 kHz device and the project fixture:

```sh
DAW_TEST_NATIVE_AUDIO=1 python3 -m unittest native.vst3.test_parameters
DAW_VST3_FIXTURE_NO_EVENTS=1 DAW_VST3_FIXTURE_REALTIME=1 cargo test --locked --features vst3-live actual_live_worker -- --ignored
```

`examples/vst3_live_demo.py saved-session.json` runs a bounded muted transport check. Fixture/installed-effect tests establish only their tested scope; muted hardware checks do not establish acoustic quality or general third-party compatibility.

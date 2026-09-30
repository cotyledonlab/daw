# Rust VST3 host candidate trial

Trial date: 2026-09-30. This is a bounded source and build-feasibility check of `HelgeSverre/rust-vst3-host` v0.9.0. It does not establish DAW plugin support.

## Revision and build result

The remote tag `v0.9.0` was verified with `git ls-remote`; it resolves to `ed054908cfe057694d8cf037d0c39dfb5eb4c2ca`. The source checkout under ignored `output/vst3-rust-trial/source` is at that exact revision.

The initial offline attempt was:

```sh
cargo build --offline --no-default-features -p vst3-host --example test_loading
```

That attempt stopped before compilation because the local registry cache had no `crossbeam-queue` package. The subsequent network-enabled command first failed to resolve `index.crates.io` in the restricted environment. After network access was authorized, the same command downloaded its locked dependency set and completed successfully in 41.57 seconds:

```sh
cargo build --locked --no-default-features -p vst3-host --example test_loading
```

Result: `Finished dev profile ... target(s) in 41.57s`. This built the library and bundled hardware-independent loader example. `--no-default-features` avoids the default CPAL playback feature, though the workspace's development dependencies still caused Cargo to compile a wide GUI/audio-related dependency set. No audio device or editor was opened. The bundle path exists at `/Users/johnmaher/Library/Audio/Plug-Ins/VST3/ValhallaFreqEcho.vst3`.

## API and integration observations

At this revision the workspace contains a `vst3-host` library, a `vst3-inspector` GUI application, and a test plugin. The README's default example calls `simple::play`, which opens the default audio output; that example is unsuitable for this no-playback trial. The bundled `test_loading` example is hardware-independent, but it starts processing and sends MIDI, and would not test this audio effect's processing with input. The bundled `trance_timeline_demo` renders offline to WAV, but it expects a synthesizer and sends a musical timeline, so it is not an appropriate run against ValhallaFreqEcho. The library does expose `SignalSource` and `simple::render_to_wav_with_input`, so a small standalone smoke harness was created under the ignored trial directory. It loaded `/Users/johnmaher/Library/Audio/Plug-Ins/VST3/ValhallaFreqEcho.vst3`, reported `ValhallaFreqEcho (1 inputs, 1 outputs)`, and rendered 0.25 seconds from a 440 Hz, 0.05-amplitude sine input with no MIDI. The output was `output/vst3-rust-trial/valhalla-frequency-echo-smoke.wav` (stereo, 44.1 kHz, IEEE float). The harness ran with `cargo run --offline` after the main build fetched dependencies. This confirms this candidate can load the installed effect and complete its offline render path on this machine; the WAV was not auditioned or analyzed for effect response. Primary-agent inspection confirmed 11025 stereo float frames at 44.1 kHz, all finite and nonzero, with peak 0.0227149.

The crate documents plugin discovery/loading, parameter access and automation, MIDI, component state, offline WAV rendering, and opt-in crash isolation. The build and minimal render above validate the relevant build and offline-load/render path for this installed effect. Other documented capabilities were not exercised.

The callback ownership tradeoff is clear in source. The ordinary playback path wraps `Plugin` in `Arc<Mutex<Plugin>>`, with the callback taking that lock; the source describes this path as correctness-first and not hard-realtime-safe. `RealtimePluginRunner` instead owns `Plugin` on the audio thread and drains a bounded lock-free command queue, with a teardown handoff intended to avoid destroying COM objects on the callback thread. Its documentation says host-side steady-state processing is allocation-free only for fixed-size buffers and in-process use, and explicitly does not guarantee third-party plugin processing is allocation-free. The isolated path marshals audio through IPC and is documented as not allocation-free. This model would require integrating our own command/snapshot boundaries with an owned audio-thread runner and respecting its teardown protocol; compatibility with this DAW's fixed callback buffers and lifecycle is unproven.

The candidate and its `vst3-inspector` and `test-plugin` manifests declare MIT. The workspace depends on `vst3 = 0.3.0`, documented by the existing comparison as MIT OR Apache-2.0. Cargo fetched a broad dependency graph, including optional audio/UI dependencies, but dependency notices were not audited. Any eventual vendoring or distribution still needs notices for the exact resolved dependency set.

## Outcome

This trial confirms the pinned candidate builds and completes a short offline render through the installed ValhallaFreqEcho. It does not establish production suitability, DAW integration compatibility, realtime safety of the plugin itself, or perceptual correctness of the rendered effect output. The build requires fetching dependencies in a clean environment; the offline cache initially lacked `crossbeam-queue`.

The ignored smoke harness used this source (with a local path dependency on the pinned `vst3-host` checkout and `default-features = false`):

```rust
use vst3_host::{audio::SignalSource, simple, MidiEvent};
fn main() -> vst3_host::Result<()> {
    let mut plugin = simple::load_plugin("/path/to/ValhallaFreqEcho.vst3")?;
    let mut input = SignalSource::sine(440.0, 0.05);
    simple::render_to_wav_with_input(
        &mut plugin, 0.25, &[] as &[MidiEvent], &mut input, "fresh-output.wav")?;
    Ok(())
}
```

Run this experiment only with a fresh destination under ignored `output/`; it is not the DAW's no-overwrite render interface. No candidate sources or binaries are vendored into this repository.

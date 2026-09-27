# Native audio spike

T04 adds optional macOS playback through CPAL 0.15.3. That version was available in the local dependency cache, compiled successfully in this workspace and provides the needed CoreAudio lifecycle. It is pinned for this spike; upgrading it is a separate compatibility check. The GUI still uses Web Audio. No plugin host, native live editing, or transport service is introduced here.

## Commands

```sh
cargo build --locked --features native-audio
target/debug/daw devices
target/debug/daw play path/to/session.json 2
target/debug/daw play path/to/session.json 2 0.1
```

`devices` returns JSON describing outputs and the default output name. `play` accepts a regular session JSON file, duration from 0.001 to 60 seconds, and optional volume from 0 to 1 (default 0.25). It uses the system's current default output without changing system settings. Playback ends automatically; Ctrl+C terminates the process and releases its resources. Session state is never written back. Logs go to stderr and the final measurement report goes to stdout. The standalone commands are separate from the JSONL `serve` interface; `serve` continues to report no live playback.

Enable `native-audio` on macOS. Other builds retain the portable core and return an explicit error for these commands. CPAL supports other platforms, but this spike only enables its dependency on macOS. CI compiles and tests the optional path on macOS without attempting hardware playback.

## Rate, channels, and callback work

The selected device's default rate and layout are used. Sine state is prepared at that rate while retaining frequency in Hz. A tone beyond the actual device Nyquist limit is rejected; there is no silent pitch shift. This works for procedural oscillators and does not implement audio-file resampling.

Supported device samples are F32, F64, I16, and U16. Mono receives the mono mix; stereo receives identical left/right signals; channels beyond stereo receive silence. Devices must have 1–32 channels and a rate supported by the session model (8–192 kHz). Other defaults return explicit errors. No device selection or fallback to another device is attempted.

The callback adapter starts by filling the device buffer with format-correct silence, then renders fixed stack blocks into the available complete frames. At the duration limit the remainder stays silent. Malformed buffers return an error without advancing state. The data callback uses prepared oscillator state, bounded stack storage, atomics, and clock reads; it does not parse JSON, allocate, wait on locks, log, or perform file/network operations. Stream errors set an atomic flag; the owner thread tears down the stream and returns an error. A stalled callback times out after the requested duration plus three seconds. CPAL stream initialization receives a five-second timeout, whose enforcement depends on the backend.

Timing measurements cover this application's render callback, not the entire CoreAudio stack. A short tail wait allows the final submitted buffer to drain before stream destruction. The submitted frame count is not a measurement of sound reaching the speakers. The native mix uses the Rust hard-clipped sum followed by monitor volume; browser audition's normalization can produce a different level.

## Verification on 2026-09-27

The sandbox returned no devices. Running with host audio access found MacBook Air Speakers, two channels, F32 at 48,000 Hz. A one-second session specified 44,100 Hz and a 440 Hz sine at gain 0.1, with monitor volume 0.25:

| Measurement | Observed |
| --- | --- |
| Submitted audio frames | 48,000 |
| Callbacks, including final silence | 105 |
| Largest callback | 512 frames |
| Corresponding buffer duration | 10.67 ms |
| Maximum measured render time | 182.583 microseconds |
| Callbacks exceeding their buffer duration | 0 |
| Stream dropped before success response | Yes |

This confirms device setup, callback execution, bounded completion, and teardown. It is not an independent acoustic recording or a general latency/underrun guarantee. Device disconnect and hot-swap were not exercised.

Portable tests check mono/stereo/multichannel mapping, unsigned silence, malformed buffers, variable callback sizes, end-of-duration silence, validation, and analytical frequency at a different device rate. Allocation instrumentation detects no allocations/reallocations/frees in the prepared buffer adapter. It does not instrument CPAL/CoreAudio internals.

## Next slice

T04b should introduce an owned native stream behind explicit inspect/play/pause/stop commands, then connect a GUI output-mode selector. Keep one owner for stream lifecycle, bound command queues, preserve draft/revision semantics, expose device errors, and test stop/restart races before replacing the browser player.

Sources: [pinned CPAL documentation](https://docs.rs/cpal/0.15.3/cpal/) and [CPAL repository](https://github.com/RustAudio/cpal).

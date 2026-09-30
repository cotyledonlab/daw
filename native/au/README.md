# Audio Unit support

`native/au` contains the macOS AUv2 child worker used by schema-v5 offline AU effects and the earlier standalone lifecycle probe. The DAW adapter supports Apple's AULowpass only (`aufx/lpas/appl`); scanning registered components does not imply support for them.

## Build and verify

```sh
python3 native/au/build.py
cargo build --locked --features au-offline
python3 examples/au_demo.py
python3 -m unittest native.au.test_daw native.au.test_probe
```

The build writes `output/au-spike/au-host`. Set `DAW_AU_HOST` to an absolute executable path to use another worker. `au-offline` enables the Rust adapter but does not build/install the worker. `python3 -m native.au.probe` remains the standalone lifecycle proof; it opens no device and its own version-1 result envelope is separate from JSONL.

## Adapter contract

Sessions use schema v5 and the `au` effect shape documented in [the protocol](../../docs/PROTOCOL.md). Processing uses 48 kHz stereo planar float32, blocks up to 256 frames, zero latency, and at most ten seconds per render. The owned worker is bounded to 15 seconds, 4,000,000 bytes of stdout, 64 KiB stderr, and the session's 64 KiB per-effect / 256 KiB aggregate state limits. Only the exact AULowpass identity is accepted. Session preparation completes before replacement commits; child rendering completes before output creation.

## Original proof evidence

The isolated T10a probe found 23 registered Apple effects on its test host. It configured three AULowpass instances, compared 10 kHz and 200 Hz cutoff renders, captured/restored binary property-list state, reset filter history, and checked teardown. RMS was 0.070968 at 10 kHz and 0.002876 at 200 Hz; restored samples matched within 1e-6 maximum absolute error. Five ASan/UBSan lifecycle runs completed without diagnostics. The synthetic callback opened no audio device, and these checks establish neither acoustic output nor real-time callback safety.

AUv3, other components, instruments, event buses, windows, automation, and AU live playback are unsupported. GUI schema-v5 import/editing is unavailable. VST3 GUI capabilities remain limited to their existing documented v1/v4 flow.

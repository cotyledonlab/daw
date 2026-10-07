# macOS Audio Unit worker

`native/au` contains the AUv2 child worker for schema-v5 offline effects. Only Apple's AULowpass (`aufx/lpas/appl`) is supported; enumerating registered components does not imply compatibility. No AU live playback, GUI editing, AUv3 or instruments.

## Build and check

```sh
python3 native/au/build.py
cargo build --locked --features au-offline
python3 examples/au_demo.py
python3 -m unittest native.au.test_daw native.au.test_probe
```

The build writes `output/au-spike/au-host`. `DAW_AU_HOST` can select another worker by absolute executable path. The Rust feature enables the adapter but does not build/install its worker. `python3 -m native.au.probe` is a separate offline lifecycle diagnostic with its own version-1 result envelope.

Processing is 48 kHz stereo planar float32, blocks up to 256 frames, zero latency and at most ten seconds per render. Session preparation precedes replacement commit; child rendering finishes before output creation. Worker jobs are bounded to 15 seconds, 4,000,000 stdout bytes and 64 KiB stderr; saved state is capped at 64 KiB per effect and 256 KiB per session. The [protocol](../../docs/PROTOCOL.md#schema-v5-offline-audio-unit-effects) defines the saved shape and errors. Tests/probes establish offline lifecycle/data behavior, not acoustic output or callback safety.

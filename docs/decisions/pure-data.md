# Pure Data / libpd spike (T12b)

## Research findings

libpd is the upstream embeddable C API for Pure Data. Its header exposes initialization, explicit abstraction/external search paths, patch open/close, audio setup, fixed block-size query, and interleaved float processing. `libpd_process_float(ticks, in, out)` sizes each buffer as `ticks * libpd_blocksize() * channels`; success is zero. A one-tick stereo call is suitable for DAW's 64-frame blocks only when `libpd_blocksize()` reports 64. The API does not let the caller select an arbitrary tick size, so a build/runtime mismatch must fail clearly rather than adapt blocks silently. See the [upstream C API](https://github.com/libpd/libpd/blob/master/libpd_wrapper/z_libpd.h).

`libpd_init()` clears the extra search paths and changes SIGFPE handling. libpd has no default extra search paths; relative paths resolve from the process working directory. The host should initialize once, set any process signal policy afterward, then add only explicit private paths before opening a patch by filename and parent directory. Patch handles are opaque and must be closed before teardown. Upstream recommends a recursive Git checkout because the Pure Data source is a submodule; a source archive can omit it. Pin a release and submodule revision rather than consuming `master`.

libpd provides `libpd_float()` for sending a scalar to a patch receiver, `libpd_bind()` plus `libpd_set_floathook()` for receiving named float messages, and typed message APIs for other Pd messages. The float hook is one slot per instance and mutually exclusive with that instance's double hook; upstream says to avoid changing it while DSP runs. Hooks therefore need installation before processing and must do bounded work; they should copy data into host-owned bounded state, not call DAW control code synchronously.

The libpd change log says its C core became thread-safe in 0.11.0. Its multi-instance API is conditional on building with `PDINSTANCE`/thread support; otherwise `libpd_new_instance()` returns null and instance selection does nothing. The project should still use one owned worker thread as the sole owner of init, patch lifecycle, messages, processing, and destruction. This follows the DAW's foreign-code and callback ownership rules; it is a stricter host policy, not a claim that upstream libpd is inherently single-threaded. Do not call libpd from the hardware callback. See the [upstream change log](https://github.com/libpd/libpd/blob/master/CHANGES.txt) and [instance API](https://github.com/libpd/libpd/blob/master/libpd_wrapper/z_libpd.h).

The libpd repository's `LICENSE.txt` identifies the Standard Improved BSD License. The bundled Pure Data source is a submodule with its own license file; externals can carry additional per-file licenses. Any future build or redistribution must preserve applicable notices for libpd, the exact Pd submodule revision, and each compiled external. See [libpd's license](https://github.com/libpd/libpd/blob/master/LICENSE.txt), [the Pd submodule license](https://github.com/pure-data/pure-data/blob/master/LICENSE.txt), and [the repository layout/build guidance](https://github.com/libpd/libpd).

## T12b1 block/message proof

`native/puredata/block_probe.py` is an owned child diagnostic using the public libpd float API. It opens the checked-in patch and a checked-in abstraction from separate private directories, sets only the explicit abstraction search path, and verifies 64-frame stereo blocks at 48 kHz. Patch-local `$0` receivers accept frequency/amplitude bases; patch messages echo the float32 values through bound hooks before DSP. Missing receivers reject. At frame 24,576, bases change from 440/0.1 to 660/0.05. Host input supplies distinct left/right offsets, and output measurements verify both the tone and stereo input routing. Hook echo is fixture evidence, not arbitrary native parameter introspection.

One child thread owns initialization, messages, patch open/close, DSP and instance destruction. It disables DSP, unbinds receivers, closes the patch and frees each instance; recreating the instance reproduces the full sample digest. `libpd_num_instances()` returns to the one main instance after each pass. This proves recreation of this fixture, not simultaneous-instance isolation. The parent bounds logs/report to 64 KiB and time to 15 seconds, validates signal evidence, and reaps its process group on exit, failure, timeout or SIGTERM/interrupt. Deliberately escaped descendants are outside containment.

## Pinned build and measured evidence

Tested on macOS arm64 with **libpd 0.16.1**, commit `ba0dc63262901d658af8bbda5e619a60fa975e78`, and its Pure Data submodule `f009fd8d7b537e209e09898d487fdf1bf547da2b` (headers report Pd 0.56.5). Sources and the locally built library remain under ignored `output/`; no system installation or dependency binary is committed. Reproduce the tested configuration:

```sh
git clone --branch 0.16.1 --recurse-submodules https://github.com/libpd/libpd.git output/libpd-runtime/source
make -C output/libpd-runtime/source -j4 UTIL=false EXTRA=false MULTI=true DOUBLE=false
DAW_LIBPD_LIBRARY="$PWD/output/libpd-runtime/source/libs/libpd.dylib" python3 native/puredata/block_probe.py
DAW_LIBPD_LIBRARY="$PWD/output/libpd-runtime/source/libs/libpd.dylib" python3 -m unittest native.puredata.test_blocks
```

Verify both checkout revisions before building. `MULTI=true` enables `PDINSTANCE` and `PDTHREADS`; `DOUBLE=false` retains default internal float32. The public interleaved float API and float message/hook signatures are used without exposing internal Pd structures. `UTIL=false EXTRA=false` omits optional wrapper utilities and bundled extra externals. Standard built-in objects remain available, including objects with filesystem/network effects; this is not a patch sandbox. Arbitrary externals, user assets and unsupported platforms remain unverified.

The first real-library run processed **768 blocks / 49,152 frames per pass**, twice, with matching SHA-256 `46fbd9d850e155ca8efb583de4226100a1a8ff275d6a3e9db1c516b7b46bddcc`. Measured frequency was 440.003/660.005 Hz, peak 0.100000004/0.049999999, and RMS ratio 0.500016. Maximum channel-offset error was 6.26e-9. No audio hardware or DAW callback was opened. All 12 boundary/runtime tests pass, including missing/invalid libraries, patch/report bounds, diagnostic overflow, deadline/descendant cleanup, SIGTERM cancellation and unknown-object rejection. Portable Rust fmt/clippy/tests also pass.

Preserve [libpd's pinned BSD-style license](https://github.com/libpd/libpd/blob/ba0dc63262901d658af8bbda5e619a60fa975e78/LICENSE.txt) and [Pd's pinned license](https://github.com/pure-data/pure-data/blob/f009fd8d7b537e209e09898d487fdf1bf547da2b/LICENSE.txt), plus individual-file notices (including IRCAM notices on compiled expression objects), before redistribution. This project does not bundle the library. A redistribution inventory remains a separate gate.

## Next adapter gates

T12b2 should add a concrete saved patch contract with explicit abstraction/assets and receiver names, bounded preparation, structured missing-runtime errors and transaction rollback. Prove a second working audio adapter before introducing a shared processor interface. Saved sources, GUI imports, native queue/transport, simultaneous-instance isolation and live messages remain unimplemented for Pd. There is no Pd engine capability or successful protocol command in this diagnostic slice. Recording and T13 jobs/subscriptions also remain open.

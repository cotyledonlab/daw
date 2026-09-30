# VST3 hosting decision record

## Current result

The current work is a standalone macOS offline spike under [`native/vst3`](../../native/vst3/README.md), not DAW plugin support. The DAW's Rust session model, renderer, native player, JSONL protocol, and browser GUI do not load or host VST3 plugins. The engine remains authoritative, and its offline renderer is not suitable for direct audio-callback use.

The spike uses Steinberg's official `pluginterfaces` source pinned to commit `31d6eeba6daaa3e2a8bfbe3e7a90ca0b7fbfbc1c` (`v3.8.0_build_66`). It builds with the system `clang++` and CoreFoundation, without CMake. The build copies the SDK's MIT license notice to `output/vst3-spike/STEINBERG-LICENSE.txt`; generated SDK material and binaries stay under ignored `output/`.

The project-owned fixture provides offline evidence for stereo processing, sample-offset parameter and note events, component state restoration (including restoration into a fresh instance), and teardown. Factory scanning accepts explicit third-party VST3 bundle paths and returns factory metadata. Dexed and ValhallaFreqEcho have been scanned successfully, but neither has been processed. Scanning runs in a child process with bounded timeout and stdout/stderr capture; timeout or crash handling kills the process group. This limits a failed scan's effect on the controller but is not a sandbox, and plugin code runs with the caller's permissions.

The spike does not cover editor windows, separate controller hosting, generic third-party audio processing, DAW commands, or real-time callbacks. Combined controller metadata/state, text conversion, UI/DSP parameter separation, and balanced host-handler references are verified by the fixture probe. No production binding decision can be supported yet: the Rust host candidates have not been built or compared against the C++ path in this repository.

## Decision

Keep the spike standalone while defining a production adapter contract. Do not expose plugin-hosting capabilities or describe the DAW as supporting VST3 until a plugin is integrated into the DAW's actual processing path and its lifecycle and failure behavior are verified.

The existing C++ spike is evidence for a narrow offline lifecycle experiment only. The Rust binding comparison in [`vst3-bindings.md`](vst3-bindings.md) is research, not implementation evidence. Revisit language and binding choice after both candidates have been measured against the same bounded adapter contract and known plugin fixture.

## Next steps

1. **T09b: bounded adapter contract.** Define ownership and limits for plugin identity, buses and channel layouts, maximum block size, events and sample-offset automation, opaque state, errors, and teardown. Make preparation and callback responsibilities explicit, including the zero-allocation, zero-lock, and no-filesystem/no-network rules for future audio callbacks. Keep foreign module loading and discovery outside the controller process.
2. **Known third-party offline validation.** Build the chosen candidate adapter and exercise one known third-party plugin through load, offline processing, automation, state save/restore, and repeated teardown. Add bounded crash/hang tests and compare evidence with the fixture results. Scanning metadata alone does not satisfy this step.
3. **Callback integration only after review.** Integrate a prepared plugin instance into native playback only after the adapter contract and callback behavior have been reviewed and measured. Verify callback bounds and allocation behavior independently of acoustic hardware checks; add explicit capability and protocol behavior only with the working adapter.

These steps do not imply blanket VST3 compatibility. Unsupported layouts, plugin behaviors, or lifecycle operations must fail clearly. Plugin editor support and general sandboxing require separate design and evidence.

## Verification

On macOS on 2026-09-30, the host and fixture built with `-Wall -Wextra -Werror`, both normally and with address/undefined-behavior sanitizers. Three Python integration tests passed in both builds: factory metadata and ten lifecycle probes; failure containment while an independent Rust controller remained responsive; and invalid/oversized child output rejection. Each lifecycle probe checked 256 stereo frames, UI/DSP parameter separation, controller synchronization, fresh-instance state, and host interface reference balance. The portable and native-feature Rust fmt/clippy/test checks passed. No plugin audio device was opened, and no acoustic or real-time plugin claim follows from these tests.

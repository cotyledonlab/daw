# Audio integrations: research notes

> Historical archive. Retained for evidence, not agent instructions or current scope. Old next steps, model assignments and expansion proposals are superseded by [the active plan](../PLAN.md) and [current contracts](../PROTOCOL.md). Do not implement archived proposals without a current task.

Research snapshot: 2026-09-27. These are historical integration research notes, not the active execution plan or current capability inventory. The native callback and constrained VST3/AU/SC/Csound/Pd paths have since been implemented; see [README.md](../../README.md), [PROTOCOL.md](../PROTOCOL.md) and the decision records for their limits. The active [roadmap](../PLAN.md) prioritizes a musical sequencer and built-in devices before further adapter breadth. Interfaces and licenses should be checked against the exact versions pinned before distribution. This is not legal advice.

## Original proposed sequence (historical)

1. Keep the current Rust session model and built-in offline synth as the reference behavior. When live audio work starts, spike device input/output with [CPAL](https://github.com/RustAudio/cpal). Treat it as a candidate: verify macOS device selection, callback behavior, device changes, and the required buffer/sample-rate handling in a small end-to-end spike.
2. Add SuperCollider as a separately launched `scsynth` process controlled over OSC. It is the lowest-friction external engine for a scriptable DAW.
3. Add Csound offline rendering through its command-line program first. Consider the C API for live block processing only when an in-process use case justifies its FFI and lifecycle work.
4. Add Pure Data through an external Pd process first. Consider `libpd` embedding if in-process control or audio is needed and a build/FFI spike succeeds.
5. Consider VST3 hosting, then Audio Unit hosting, after the device callback, graph, automation, plugin state, and render lifecycle are understood. These formats are substantial host subsystems, not simple synth adapters.

Do not add a general engine trait before there is a second adapter. Keep the current built-in path direct; when a second concrete engine exists, extract only the common behavior those two paths need.

## Engine interfaces

### SuperCollider

SuperCollider separates its language client from the `scsynth` or `supernova` audio server. The server accepts OSC over TCP or UDP, and another OSC client can send commands directly. This makes process launch plus OSC a practical control adapter without embedding the SuperCollider language runtime. Commands are asynchronous, so track IDs and use replies/completion messages where sequencing depends on server work. OSC is a control channel, not an audio stream.

Use normal realtime server mode for interactive control. Non-realtime (`-N`) mode consumes a prepared OSC score, processes it without synchronization to hardware, and does not accept live network interaction.

Sources: [Client/server guide](https://doc.sccode.org/Guides/ClientVsServer.html), [server command reference](https://doc.sccode.org/Reference/Server-Command-Reference.html), [non-realtime synthesis](https://doc.sccode.org/Guides/Non-Realtime-Synthesis.html), [project license](https://github.com/supercollider/supercollider).

### Csound

Csound documents a stable C API in `csound.h`. A host can compile a CSD or orchestra, start the engine, repeatedly call `csoundPerformKsmps`, exchange control/audio channels, then clean up and destroy the instance. This supports an in-process path, but requires explicit handling of Csound's control-block size and audio buffers. Command-line offline rendering is a smaller first integration.

Sources: [API overview and examples](https://csound.com/docs/api/), [control and event functions](https://csound.com/docs/api/group__CONTROLEVENTS.html), [realtime audio I/O](https://csound.com/docs/api/group__RTAUDIOIO.html), [project and license](https://github.com/csound/csound).

### Pure Data

`libpd` describes itself as Pure Data packaged as an embeddable audio synthesis library. It offers a C boundary suitable for a Rust FFI wrapper. Its build uses the Pd source as a Git submodule; the project warns that a plain GitHub ZIP omits required submodule files. Validate macOS universal builds, patch search paths, external availability, and callback/thread ownership in a bounded spike before making it a supported backend.

Sources: [libpd build and embedding notes](https://github.com/libpd/libpd), [libpd license file](https://github.com/libpd/libpd/blob/master/LICENSE.txt), [Pd license file](https://github.com/pure-data/pure-data/blob/master/LICENSE.txt).

## Device audio and plugin hosting

### Live device I/O

CPAL is a plausible Rust device-I/O layer, but should remain a tentative choice until a macOS spike confirms the actual device and callback requirements. Keep session edits, file access, process management, and IPC off the audio callback. Prepare buffers and resources before rendering; pass parameter changes through bounded, preallocated queues. The callback must meet each device's deadline, and block size/sample rate may differ from the offline renderer's assumptions.

Source: [CPAL repository](https://github.com/RustAudio/cpal).

### VST3 and Audio Units

VST3 hosting entails discovery and loading, component/controller lifecycle, bus layouts, audio/event buffers, parameter automation, state serialization, editor handling, and plugin failure behavior. Steinberg's VST 3.8 developer portal says the SDK is MIT-licensed since 3.8, with VSTGUI and mda files under BSD-like terms. Verify the exact SDK files and version used.

Apple's Audio Component APIs discover and load Audio Units on Apple platforms. The AU render block is the audio processing entry point; Apple's documentation says a host should fetch and cache it before invoking it from a realtime context. A small native Objective-C/C++ shim is a reasonable starting option for AU integration. The choice between such a shim and Rust wrappers should be settled by a focused macOS spike comparing build reliability, ABI/lifecycle coverage, and host compatibility; do not assume an unproven wrapper is production-ready.

Sources: [Steinberg VST 3 licensing](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST%2B3%2BLicensing/Index.html), [VST SDK documentation](https://steinbergmedia.github.io/vst3_doc/), [Apple Audio Components](https://developer.apple.com/documentation/audiotoolbox/audio-components), [Apple AU render block](https://developer.apple.com/documentation/audiotoolbox/auaudiounit/renderblock).

## Real-time and process boundaries

An external synth process is useful for isolation and independent development, but OSC/MIDI control does not move rendered audio into the DAW. If live audio must cross that boundary, choose and measure an audio transport separately; account for buffering, synchronization, latency, and failure recovery. Until then, treat process adapters as control or offline-render integrations.

Keep realtime work bounded: no locks that can wait, heap allocation, filesystem/network access, process startup, or unbounded work in the render callback. Move setup and teardown off the callback, use preallocated command paths, and make buffer size, sample rate, latency, and engine errors visible to the host.

## Licensing note

The sources currently identify SuperCollider as GPL-3.0, Csound as LGPL-2.1-or-later, and Pd/libpd as BSD-style. Steinberg's current VST 3.8 SDK page describes MIT licensing with noted exceptions. These are repository-level pointers, not a distribution review: inspect notices and licenses for the exact pinned release and every bundled dependency before shipping. Keep engines in separate processes when that fits the product, and obtain appropriate legal review for distribution decisions.

# Audio Unit offline adapter

> Historical archive. Retained for evidence, not agent instructions or current scope. Old next steps, model assignments and expansion proposals are superseded by [the active plan](../../PLAN.md) and [current contracts](../../PROTOCOL.md). Do not implement archived proposals without a current task.

Schema v5 adds a bounded, macOS-only offline AUv2 effect adapter. It is derived from the standalone T10a lifecycle proof and currently accepts Apple's AULowpass only. The adapter is an owned child-process renderer; it does not add AU support to the GUI or native live player.

## Session contract

An AU effect is serialized as:

```json
{"kind":"au","id":"lowpass","bypass":false,"component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl","state_hex":"","parameters":[{"id":0,"value":10000.0}]}
```

The three component fields form an exact four-character identity tuple. Only `aufx/lpas/appl` is accepted. Parameters retain unsigned native AU parameter IDs and finite values checked against AULowpass metadata ranges; values use native units. They have no automation points. State is a binary property-list payload represented as even-length hex. AU requires schema v5; schema v1–v4 reject it. V5 keeps v4 timeline requirements and built-in/VST3 behavior.

At most eight foreign effects and 64 parameters per effect may occur in one session, with no more than 64 KiB per AU state blob (VST3 keeps its separate component/controller limits) and 256 KiB aggregate foreign state. Empty AU state causes initial state capture during successful load/replace preparation. Saved parameter bases override restored state; initial state is captured only when state_hex is empty. Each render creates fresh child instances from the saved state and values and resets filter history before rendering, so render history is not persisted.

## Processing and failure behavior

The worker accepts only Apple AULowpass with one stereo input/output, planar float32, 48 kHz, blocks no larger than 256 frames, and zero reported latency. AU-containing renders are limited to ten seconds. The worker is built with `python3 native/au/build.py`; Rust enables hosting with `--features au-offline`. An absolute `DAW_AU_HOST` selects the executable; otherwise the engine uses `output/au-spike/au-host`. The feature flag indicates build support and does not ensure a worker binary is installed.

Load and replace validate and prepare every foreign effect before committing a new session or revision. Rendering completes all child processing before destination creation. The child is bounded to 15 seconds, 4,000,000 bytes of stdout, and 64 KiB stderr. Invalid identity/state/parameters, unsupported layout, timeout, crash, or malformed/oversized child output returns structured `plugin_error`; session state remains unchanged and rendering leaves no partial output. Portable builds parse the validated schema shape but report AU hosting unavailable when preparation is requested.

## Proof evidence and limits

The T10a standalone proof found 23 registered Apple effects on the tested host and established lifecycle, cutoff response, binary property-list restore, and teardown for AULowpass. It used a synthetic 1 kHz source and opened no audio device. Its measured RMS values were 0.070968 at 10 kHz and 0.002876 at 200 Hz; restored output matched within 1e-6 maximum absolute sample error. Five proof runs with AddressSanitizer and UndefinedBehaviorSanitizer completed without diagnostics. These checks cover the proof host and Apple's closed-source DSP was not instrumented. They do not establish acoustic output or live callback safety.

AUv3, third-party/other AU components, instruments, event buses, plugin windows, automation, non-stereo layouts, other rates, and native AU playback remain unsupported. The browser editor and upload guard do not support schema v5. VST3 editing/reuse in its supported schema-v4 GUI flow is unchanged. See [the protocol](../../PROTOCOL.md) and [the standalone proof notes](../../../native/au/README.md).

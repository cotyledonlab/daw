"""Small, dependency-free OSC and SuperCollider NRT score writer.

The SynthDef below is a deliberately tiny SCgf v2 fixture.  It contains only
Control, SinOsc, BinaryOpUGen (multiply), and Out UGens, so it can be used to
prove an installed scsynth render without compiling SuperCollider source.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Iterable, Sequence


SYNTH_NAME = "daw_sine_fixture"


def _padded(data: bytes) -> bytes:
    return data + b"\0" * ((-len(data)) % 4)


def osc_string(value: str) -> bytes:
    """Encode an OSC string as UTF-8, NUL terminated and 4-byte padded."""
    encoded = value.encode("utf-8")
    if b"\0" in encoded:
        raise ValueError("OSC strings cannot contain NUL")
    return _padded(encoded + b"\0")


def osc_blob(value: bytes) -> bytes:
    """Encode an OSC blob with a big-endian signed length and zero padding."""
    return struct.pack(">i", len(value)) + _padded(value)


def osc_message(address: str, *arguments: object) -> bytes:
    """Encode a basic OSC message (string, blob, int32, or float32 arguments)."""
    if not address.startswith("/"):
        raise ValueError("OSC address must begin with '/'")
    tags = [","]
    payload = bytearray()
    for argument in arguments:
        if isinstance(argument, str):
            tags.append("s")
            payload.extend(osc_string(argument))
        elif isinstance(argument, (bytes, bytearray, memoryview)):
            tags.append("b")
            payload.extend(osc_blob(bytes(argument)))
        elif isinstance(argument, bool):
            raise TypeError("OSC booleans are not supported")
        elif isinstance(argument, int):
            if not -(2**31) <= argument < 2**31:
                raise ValueError("OSC int32 argument is out of range")
            tags.append("i")
            payload.extend(struct.pack(">i", argument))
        elif isinstance(argument, float):
            if not math.isfinite(argument):
                raise ValueError("OSC float32 argument must be finite")
            tags.append("f")
            payload.extend(struct.pack(">f", argument))
        else:
            raise TypeError(f"unsupported OSC argument type: {type(argument).__name__}")
    return osc_string(address) + osc_string("".join(tags)) + bytes(payload)


def osc_timetag(seconds: float) -> bytes:
    """Encode NRT score time as an OSC 64-bit seconds/fraction timetag.

    OSC's NTP epoch offset is intentionally omitted: scsynth NRT score files
    use seconds relative to the start of the render, as Score.write does.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("score time must be a finite non-negative number")
    # Round upward by at most one OSC tick so the documented minimum
    # duration does not become shorter through fixed-point quantization.
    ticks = math.ceil(seconds * 2**32)
    return struct.pack(">Q", ticks)


def osc_bundle(seconds: float, messages: Iterable[bytes]) -> bytes:
    """Wrap encoded OSC messages in one timed OSC bundle."""
    packet = bytearray(b"#bundle\0" + osc_timetag(seconds))
    count = 0
    for message in messages:
        packet.extend(struct.pack(">i", len(message)))
        packet.extend(message)
        count += 1
    if count == 0:
        raise ValueError("OSC bundle must contain at least one message")
    return bytes(packet)


def _pstring(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 255 or b"\0" in raw:
        raise ValueError("SynthDef pstring must be at most 255 bytes and contain no NUL")
    return bytes((len(raw),)) + raw


def _ugen(name: str, rate: int, inputs: Sequence[tuple[int, int]], outputs: Sequence[int], special: int = 0) -> bytes:
    result = bytearray(_pstring(name))
    result.extend(struct.pack(">BiiH", rate, len(inputs), len(outputs), special))
    for source, index in inputs:
        result.extend(struct.pack(">ii", source, index))
    result.extend(bytes(outputs))
    return bytes(result)


def sine_synthdef(frequency: float = 440.0, gain: float = 0.1) -> bytes:
    """Return a native SCgf SynthDef v2 for a stereo sine fixture.

    Controls are ``freq``, ``gain``, and ``out``. Constants are deduplicated
    as required by the SynthDef format. BinaryOpUGen special index 2 is '*'.
    """
    if not math.isfinite(frequency) or frequency <= 0:
        raise ValueError("frequency must be finite and positive")
    if not math.isfinite(gain) or not 0 <= gain <= 1:
        raise ValueError("gain must be finite and between 0 and 1")
    constants = [0.0]
    # Control has one output per control parameter, in parameter-array order.
    ugens = [
        _ugen("Control", 1, [], [1, 1, 1]),
        _ugen("SinOsc", 2, [(0, 0), (-1, 0)], [2]),
        _ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
        _ugen("Out", 2, [(0, 2), (2, 0), (2, 0)], []),
    ]
    definition = bytearray(_pstring(SYNTH_NAME))
    definition.extend(struct.pack(">i", len(constants)))
    definition.extend(struct.pack(">f", constants[0]))
    definition.extend(struct.pack(">i", 3))
    definition.extend(struct.pack(">fff", frequency, gain, 0.0))
    names = (("freq", 0), ("gain", 1), ("out", 2))
    definition.extend(struct.pack(">i", len(names)))
    for name, index in names:
        definition.extend(_pstring(name))
        definition.extend(struct.pack(">i", index))
    definition.extend(struct.pack(">i", len(ugens)))
    definition.extend(b"".join(ugens))
    definition.extend(struct.pack(">H", 0))  # no variants
    return b"SCgf" + struct.pack(">iH", 2, 1) + bytes(definition)


def score_packets(
    frequency: float = 440.0,
    gain: float = 0.1,
    duration: float = 0.1,
) -> list[tuple[float, bytes]]:
    """Build ordered NRT packets for one bounded stereo sine synth."""
    if not math.isfinite(duration) or not 0.001 <= duration <= 10.0:
        raise ValueError("duration must be between 0.001 and 10 seconds")
    synthdef = sine_synthdef(frequency, gain)
    synth_id = 1000
    group_id = 1
    start = [
        osc_message("/d_recv", synthdef),
        osc_message("/g_new", group_id, 0, 0),  # add head to the default group
        osc_message("/s_new", SYNTH_NAME, synth_id, 0, group_id,
                    "freq", frequency, "gain", gain, "out", 0.0),
    ]
    # The final c_set is the conventional NRT completion marker.
    end = [osc_message("/n_free", synth_id), osc_message("/c_set", 0, 0.0)]
    return [(0.0, osc_bundle(0.0, start)), (duration, osc_bundle(duration, end))]


def write_score(path: str | Path, frequency: float = 440.0, gain: float = 0.1,
                duration: float = 0.1) -> Path:
    """Write NRT file framing: BE uint32 packet length, then OSC packet."""
    destination = Path(path)
    packets = score_packets(frequency, gain, duration)
    with destination.open("xb") as output:
        for _, packet in packets:
            output.write(struct.pack(">I", len(packet)))
            output.write(packet)
    return destination

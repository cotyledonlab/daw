#!/usr/bin/env python3
"""Versioned owned source preparation worker. No DAW hardware callback calls here."""
from __future__ import annotations

import ctypes as C
import json
import math
import os
from pathlib import Path
import struct
import sys

if __package__:
    from .block_probe import _bind
else:
    from block_probe import _bind


def validate_job(job):
    if not isinstance(job, dict) or set(job) != {"job_version", "sample_rate", "source"} or type(job["job_version"]) is not int or job["job_version"] != 1:
        raise ValueError("unsupported Csound source job")
    rate, source = job["sample_rate"], job["source"]
    if type(rate) is not int or not 8000 <= rate <= 192000 or not isinstance(source, dict) or set(source) != {"program", "duration_frames", "gain", "controls"}:
        raise ValueError("invalid Csound source job fields")
    program, frames, controls = source["program"], source["duration_frames"], source["controls"]
    if not isinstance(program, str) or not program or len(program.encode("utf-8")) > 61440 or "\0" in program or type(frames) is not int or not (rate + 999)//1000 <= frames <= rate*10:
        raise ValueError("invalid Csound source program/duration")
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)
    if not finite(source["gain"]) or not 0 <= source["gain"] <= 1 or not isinstance(controls, list) or len(controls) > 64:
        raise ValueError("invalid Csound source gain/controls")
    names, count = set(), 0
    for control in controls:
        if not isinstance(control, dict) or set(control) != {"name", "value", "points"}:
            raise ValueError("invalid Csound control fields")
        name = control["name"]
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 128 or "\0" in name or name in names or not finite(control["value"]) or not isinstance(control["points"], list):
            raise ValueError("invalid Csound control name/value/points")
        names.add(name)
        previous = -1
        for point in control["points"]:
            if not isinstance(point, dict) or set(point) != {"frame", "value"} or type(point["frame"]) is not int or not previous < point["frame"] < frames or point["frame"] % 64 or not finite(point["value"]):
                raise ValueError("invalid Csound control event")
            previous = point["frame"]
        count += len(control["points"])
    if count > 512:
        raise ValueError("too many Csound control events")
    return rate, source


def load_job(job_path):
    with open(job_path, "rb") as stream:
        payload = stream.read(1048577)
    if len(payload) > 1048576:
        raise ValueError("Csound source job exceeds 1 MiB")
    return validate_job(json.loads(payload))


def perform_source(rate, source, library, emit_block, before_dsp=None):
    """The owning worker copies blocks before passing them to the concrete sink."""
    validate_job({"job_version": 1, "sample_rate": rate, "source": source})
    if not Path(library).is_absolute() or not Path(library).is_file():
        raise ValueError("Csound library must be an existing absolute file")
    api = _bind(library)
    fn = api.csoundGetChannelPtr
    fn.restype, fn.argtypes = C.c_int32, [C.c_void_p, C.POINTER(C.c_void_p), C.c_char_p, C.c_int32]
    if api.csoundInitialize(3):
        raise RuntimeError("Csound initialization failed")
    engine = api.csoundCreate(None, os.fsencode(Path.cwd() / "empty-opcodes"))
    if not engine:
        raise RuntimeError("Csound instance creation failed")
    def set_control(control, value):
        name = control["name"].encode("utf-8")
        api.csoundSetControlChannel(engine, name, value)
        error = C.c_int32()
        actual = api.csoundGetControlChannel(engine, name, C.byref(error))
        if error.value or actual != value:
            raise RuntimeError(f"Csound control readback failed: {control['name']}")
    try:
        api.csoundSetHostAudioIO(engine)
        for option in ("-n", "-d", "-m0", "-+ignore_csopts=1", "--no-default-paths", "-+rtaudio=null", "-+rtmidi=null", f"-r{rate}", "--ksmps=64"):
            if api.csoundSetOption(engine, option.encode()):
                raise RuntimeError("Csound source option failed")
        if api.csoundCompileCSD(engine, source["program"].encode("utf-8"), 1, 0):
            raise RuntimeError("Csound source compilation failed")
        if api.csoundStart(engine):
            raise RuntimeError("Csound source start failed")
        events = {}
        for control in source["controls"]:
            pointer = C.c_void_p()
            # Query with type zero does not fabricate a missing channel.
            kind = api.csoundGetChannelPtr(engine, C.byref(pointer), control["name"].encode("utf-8"), 0)
            if kind < 0 or kind & 15 != 1 or not kind & 16:
                raise RuntimeError(f"Csound control is not a declared scalar input channel: {control['name']}")
            base = control["value"]
            for point in control["points"]:
                if point["frame"] == 0:
                    base = point["value"]
                else:
                    events.setdefault(point["frame"], []).append((control, point["value"]))
            set_control(control, base)
        inputs = api.csoundGetChannels(engine, 1)
        if api.csoundGetSr(engine) != rate or api.csoundGetKsmps(engine) != 64 or api.csoundGetChannels(engine, 0) != 2 or inputs > 2 or api.csoundGet0dBFS(engine) != 1.0:
            raise RuntimeError("Csound sources require requested rate, ksmps=64, stereo output, 0..2 input channels and 0dbfs=1")
        if before_dsp is not None:
            before_dsp()
        for frame in range(0, source["duration_frames"], 64):
            for control, value in events.get(frame, ()):
                set_control(control, value)
            spin = api.csoundGetSpin(engine)
            if inputs and not spin:
                raise RuntimeError("Csound source input buffer missing")
            for index in range(inputs*64):
                spin[index] = 0.0
            status = api.csoundPerformKsmps(engine)
            if status:
                raise RuntimeError(f"Csound score ended before saved duration at frame {frame} (code {status})")
            spout = api.csoundGetSpout(engine)
            if not spout:
                raise RuntimeError("Csound source output buffer missing")
            block = bytearray()
            for index in range(min(64, source["duration_frames"] - frame)):
                left, right = spout[index*2], spout[index*2+1]
                if not math.isfinite(left) or not math.isfinite(right):
                    raise RuntimeError("Csound source produced nonfinite PCM")
                block.extend(struct.pack("<dd", left, right))
            spin = spout = None
            emit_block(frame, bytes(block))
    finally:
        # No borrowed buffers survive the last perform scope into teardown.
        spin = spout = None
        api.csoundReset(engine)
        api.csoundDestroy(engine)


def prepare(job_path, output, library):
    rate, source = load_job(job_path)
    with open(output, "xb") as stream:
        stream.write(b"DCS1" + struct.pack("<IQI", rate, source["duration_frames"], 2))
        perform_source(rate, source, library, lambda frame, block: stream.write(block))


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError("Usage: source_worker.py JOB OUTPUT LIBRARY")
        prepare(*sys.argv[1:])
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": str(error)}}), file=sys.stderr)
        raise SystemExit(1)

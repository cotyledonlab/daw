#!/usr/bin/env python3
"""Owned libcsound-7 block/control diagnostic; no DAW transport or hardware I/O."""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

FIXTURE = Path(__file__).with_name("block_fixture.csd")
MAX_LOG = 65536
RATE, BLOCK, FRAMES, CONTROL_FRAME = 48000, 64, 49152, 24576


def _bind(library):
    """Signatures from the pinned 7.0.0-beta.17 SDK; do not apply them to v6."""
    api = C.CDLL(str(library))
    signatures = {
        "csoundGetVersion": (C.c_int32, []),
        "csoundGetSizeOfMYFLT": (C.c_int32, []),
        "csoundInitialize": (C.c_int32, [C.c_int32]),
        "csoundCreate": (C.c_void_p, [C.c_void_p, C.c_char_p]),
        "csoundDestroy": (None, [C.c_void_p]),
        "csoundReset": (None, [C.c_void_p]),
        "csoundSetHostAudioIO": (None, [C.c_void_p]),
        "csoundSetOption": (C.c_int32, [C.c_void_p, C.c_char_p]),
        "csoundCompileCSD": (C.c_int32, [C.c_void_p, C.c_char_p, C.c_int32, C.c_int32]),
        "csoundStart": (C.c_int32, [C.c_void_p]),
        "csoundPerformKsmps": (C.c_int32, [C.c_void_p]),
        "csoundGetSr": (C.c_double, [C.c_void_p]),
        "csoundGetKsmps": (C.c_uint32, [C.c_void_p]),
        "csoundGetChannels": (C.c_uint32, [C.c_void_p, C.c_int32]),
        "csoundGet0dBFS": (C.c_double, [C.c_void_p]),
        "csoundGetSpin": (C.POINTER(C.c_double), [C.c_void_p]),
        "csoundGetSpout": (C.POINTER(C.c_double), [C.c_void_p]),
        "csoundSetControlChannel": (None, [C.c_void_p, C.c_char_p, C.c_double]),
        "csoundGetControlChannel": (C.c_double, [C.c_void_p, C.c_char_p, C.POINTER(C.c_int32)]),
    }
    # Only no-instance scalar queries precede the ABI guard.
    for name in ("csoundGetVersion", "csoundGetSizeOfMYFLT"):
        fn = getattr(api, name)
        fn.restype, fn.argtypes = signatures[name]
    if api.csoundGetVersion() != 7000 or api.csoundGetSizeOfMYFLT() != 8:
        raise RuntimeError("This diagnostic requires the Csound 7 double-sample ABI")
    for name, (result, arguments) in signatures.items():
        fn = getattr(api, name)
        fn.restype, fn.argtypes = result, arguments
    return api


def _measure(samples, offset):
    centered = [v - offset for v in samples]
    if not centered or any(not math.isfinite(v) for v in centered):
        raise RuntimeError("nonfinite or empty Csound output")
    crossings = [i for i in range(1, len(centered)) if centered[i - 1] <= 0 < centered[i]]
    if len(crossings) < 3:
        raise RuntimeError("Csound fixture produced no measurable sine")
    return {"frequency_hz": (len(crossings) - 1) * RATE / (crossings[-1] - crossings[0]),
            "rms": math.sqrt(sum(v * v for v in centered) / len(centered)),
            "peak": max(abs(v) for v in centered)}


def _controls(api, engine, frequency, gain):
    for name, value in ((b"frequency", frequency), (b"gain", gain)):
        api.csoundSetControlChannel(engine, name, value)
        error = C.c_int32()
        readback = api.csoundGetControlChannel(engine, name, C.byref(error))
        if error.value or readback != value:
            raise RuntimeError(f"control readback failed for {name.decode()}")
    return {"frequency": frequency, "gain": gain}


def _pass(api, engine, source):
    api.csoundSetHostAudioIO(engine)
    for option in (b"-n", b"-d", b"-m0", b"-+ignore_csopts=1", b"--no-default-paths",
                   b"-+rtaudio=null", b"-+rtmidi=null"):
        if api.csoundSetOption(engine, option):
            raise RuntimeError(f"Csound option failed: {option!r}")
    if api.csoundCompileCSD(engine, source, 1, 0):
        raise RuntimeError("Csound fixture compilation failed")
    before = _controls(api, engine, 440.0, 0.1)
    if api.csoundStart(engine):
        raise RuntimeError("Csound start failed")
    if (api.csoundGetSr(engine), api.csoundGetKsmps(engine),
        api.csoundGetChannels(engine, 0), api.csoundGetChannels(engine, 1),
        api.csoundGet0dBFS(engine)) != (RATE, BLOCK, 2, 2, 1.0):
        raise RuntimeError("Csound fixture audio layout/rate/block/scale mismatch")
    left, right = [], []
    digest = hashlib.sha256()
    changed = None
    blocks = 0
    while blocks * BLOCK < FRAMES:
        frame = blocks * BLOCK
        if frame == CONTROL_FRAME:
            changed = _controls(api, engine, 660.0, 0.05)
        # Borrow spin only for this block, write every input sample before DSP.
        spin = api.csoundGetSpin(engine)
        if not spin:
            raise RuntimeError("Csound input buffer missing")
        for i in range(BLOCK):
            spin[2 * i], spin[2 * i + 1] = 0.01, -0.02
        result = api.csoundPerformKsmps(engine)
        if result:
            raise RuntimeError(f"Csound ended early at frame {frame} (code {result})")
        # Spout belongs to Csound. Copy before the next perform/reset call.
        spout = api.csoundGetSpout(engine)
        if not spout:
            raise RuntimeError("Csound output buffer missing")
        for i in range(BLOCK):
            a, b = spout[2 * i], spout[2 * i + 1]
            if not math.isfinite(a) or not math.isfinite(b):
                raise RuntimeError("Csound output contains nonfinite samples")
            left.append(a)
            right.append(b)
            digest.update(struct.pack("<dd", a, b))
        blocks += 1
    terminal = api.csoundPerformKsmps(engine)
    if terminal <= 0:
        raise RuntimeError(f"Csound score did not finish cleanly (code {terminal})")
    if max(abs((a - b) - 0.03) for a, b in zip(left, right)) > 1e-12:
        raise RuntimeError("Csound host input/channel routing mismatch")
    phases = [_measure(left[:CONTROL_FRAME], 0.01), _measure(left[CONTROL_FRAME:], 0.01)]
    for phase, frequency, gain in zip(phases, (440, 660), (0.1, 0.05)):
        if abs(phase["frequency_hz"] - frequency) > 2 or abs(phase["peak"] - gain) > 0.001:
            raise RuntimeError("Csound control edit did not reach captured audio")
    ratio = phases[1]["rms"] / phases[0]["rms"]
    if not 0.48 < ratio < 0.52:
        raise RuntimeError("Csound captured gain ratio mismatch")
    return {"digest": digest.hexdigest(), "phases": phases, "rms_ratio": ratio,
            "readbacks": [before, changed], "blocks": blocks,
            "input_channel_difference": 0.03, "terminal_code": terminal}


def _worker(library, result, fixture):
    # All loading, compilation, channel access, DSP and teardown share this thread.
    api = _bind(library)
    if api.csoundInitialize(3):  # NO_SIGNAL_HANDLER | NO_ATEXIT from pinned headers.
        raise RuntimeError("Csound initialization failed")
    engine = api.csoundCreate(None, os.fsencode(Path.cwd() / "empty-opcodes"))
    if not engine:
        raise RuntimeError("Csound instance creation failed")
    try:
        source = Path(fixture).read_bytes()
        if not source or len(source) > 1048576 or b"\0" in source:
            raise RuntimeError("invalid fixture snapshot")
        source.decode("utf-8")
        first = _pass(api, engine, source)
        api.csoundReset(engine)
        second = _pass(api, engine, source)
        if first != second:
            raise RuntimeError("reset/recompile did not reproduce fixture state/audio")
    finally:
        # No borrowed spin/spout pointers survive into reset/destruction.
        api.csoundReset(engine)
        api.csoundDestroy(engine)
    report = {"sample_rate": RATE, "ksmps": BLOCK, "frames": FRAMES,
              "control_frame": CONTROL_FRAME, "passes": 2, "reset_equal": True,
              "resources_released": True, "source_mode": "diagnostic",
              "daw_transport": False, "hardware_audio": False, "version": 7000,
              "myflt_bytes": 8, "audio": first}
    with open(result, "x", encoding="utf-8") as stream:
        json.dump(report, stream, allow_nan=False)


def _drain(pipe, state):
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                return
            room = MAX_LOG - len(state["bytes"])
            state["bytes"].extend(chunk[:room])
            state["overflow"] |= len(chunk) > room
    except Exception as error:
        state["error"] = str(error)
    finally:
        pipe.close()


def _validate(report):
    expected = {"sample_rate": RATE, "ksmps": BLOCK, "frames": FRAMES,
                "control_frame": CONTROL_FRAME, "passes": 2, "reset_equal": True,
                "resources_released": True, "source_mode": "diagnostic",
                "daw_transport": False, "hardware_audio": False, "version": 7000,
                "myflt_bytes": 8}
    if not isinstance(report, dict) or set(report) != set(expected) | {"audio"}:
        raise RuntimeError("invalid Csound diagnostic report")
    for key, value in expected.items():
        if type(report[key]) is not type(value) or report[key] != value:
            raise RuntimeError(f"invalid Csound diagnostic {key}")
    audio = report["audio"]
    if (not isinstance(audio, dict) or audio.get("blocks") != FRAMES // BLOCK
        or not isinstance(audio.get("digest"), str) or len(audio["digest"]) != 64
        or any(ch not in "0123456789abcdef" for ch in audio["digest"])
        or audio.get("readbacks") != [{"frequency": 440.0, "gain": 0.1}, {"frequency": 660.0, "gain": 0.05}]
        or audio.get("input_channel_difference") != 0.03
        or type(audio.get("terminal_code")) is not int or audio["terminal_code"] <= 0):
        raise RuntimeError("invalid Csound diagnostic audio evidence")
    phases = audio.get("phases")
    if not isinstance(phases, list) or len(phases) != 2:
        raise RuntimeError("invalid Csound diagnostic phases")
    for phase, frequency, gain in zip(phases, (440, 660), (0.1, 0.05)):
        if not isinstance(phase, dict) or set(phase) != {"frequency_hz", "rms", "peak"}:
            raise RuntimeError("invalid Csound diagnostic phase")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in phase.values()):
            raise RuntimeError("invalid Csound diagnostic signal values")
        if abs(phase["frequency_hz"] - frequency) > 2 or abs(phase["peak"] - gain) > 0.001 or not gain * .68 < phase["rms"] < gain * .73:
            raise RuntimeError("Csound diagnostic signal does not match fixture")
    ratio = audio.get("rms_ratio")
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not .48 < ratio < .52:
        raise RuntimeError("invalid Csound diagnostic gain ratio")
    return report


def _reject_constant(value):
    raise ValueError(f"nonfinite JSON constant: {value}")


def run_probe(library=None, *, worker_script=None, timeout=15.0):
    if os.name != "posix":
        raise RuntimeError("Csound block diagnostic requires Unix process ownership")
    selected = library if library is not None else os.environ.get("DAW_CSOUND_LIBRARY")
    if not selected or not Path(selected).is_absolute() or not Path(selected).is_file():
        raise RuntimeError("DAW_CSOUND_LIBRARY must name an existing absolute libcsound library")
    library = Path(selected).resolve()
    output = Path(__file__).resolve().parents[2] / "output"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="csound-blocks-", dir=output) as directory:
        cwd = Path(directory)
        result = cwd / "result.json"
        fixture = cwd / "fixture.csd"
        fixture.write_bytes(FIXTURE.read_bytes())
        rc = cwd / "empty.rc"
        rc.write_bytes(b"")
        (cwd / "empty-opcodes").mkdir()
        environment = dict(os.environ, CSOUND6RC=str(rc), CSOUND7RC=str(rc))
        script = Path(worker_script or __file__).resolve()
        child = subprocess.Popen([sys.executable, str(script), "--worker", str(library),
                                  str(result), str(fixture)], cwd=cwd, env=environment,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        logs = [{"bytes": bytearray(), "overflow": False} for _ in range(2)]
        readers = [threading.Thread(target=_drain, args=(pipe, state), daemon=True)
                   for pipe, state in zip((child.stdout, child.stderr), logs)]
        for reader in readers:
            reader.start()
        failure = None
        started = time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic() - started >= timeout:
                    failure = "Csound block worker timed out"
                    break
                if any(log["overflow"] for log in logs):
                    failure = "Csound block diagnostics exceeded 64 KiB"
                    break
                if result.exists() and (not result.is_file() or result.is_symlink() or result.stat().st_size > MAX_LOG):
                    failure = "Csound block report outside file limit"
                    break
                time.sleep(.01)
        finally:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                failure = "Csound worker escaped pipe cleanup"
        if failure:
            raise RuntimeError(failure)
        if any(log["overflow"] or log.get("error") for log in logs):
            raise RuntimeError("Csound block diagnostics exceeded limit or failed")
        if child.returncode:
            detail = b"\n".join(bytes(log["bytes"]) for log in logs).decode("utf-8", "replace")[:1024]
            raise RuntimeError(f"Csound block worker failed ({child.returncode}): {detail}")
        if result.is_symlink() or not result.is_file() or result.stat().st_size > MAX_LOG:
            raise RuntimeError("Csound block report missing or outside file limit")
        try:
            report = json.loads(result.read_text(), parse_constant=_reject_constant)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError("invalid Csound diagnostic report JSON") from error
        return _validate(report)


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == "--worker":
            _worker(*sys.argv[2:])
            return
        if len(sys.argv) != 1:
            raise RuntimeError("Usage: DAW_CSOUND_LIBRARY=/absolute/library python3 native/csound/block_probe.py")
        print(json.dumps(run_probe(), allow_nan=False))
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": str(error)}}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

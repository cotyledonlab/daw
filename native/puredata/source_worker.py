#!/usr/bin/env python3
"""Owned libpd source preparation worker; never calls DAW audio callbacks."""
from __future__ import annotations

import ctypes as C
import json
import math
import os
from pathlib import Path
import re
import struct
import stat
import sys
import tempfile

if __package__:
    from .block_probe import PRINT_HOOK as _PRINT_HOOK, _bind, _dsp
else:
    from block_probe import PRINT_HOOK as _PRINT_HOOK, _bind, _dsp


_BLOCK = 64
_MAX_PROGRAM_BYTES = 61440
_MAX_JOB_BYTES = 1048576
_MAX_LOG_BYTES = 65536
_ABSTRACTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z", re.ASCII)


def _utf8_size(value, label):
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error


def _finite_float32(value):
    if type(value) not in (int, float):
        return False
    try:
        if not math.isfinite(value):
            return False
        return math.isfinite(C.c_float(value).value)
    except (OverflowError, TypeError, ValueError):
        return False


def validate_job(job):
    if (not isinstance(job, dict)
            or set(job) != {"job_version", "sample_rate", "source"}
            or type(job["job_version"]) is not int or job["job_version"] != 1):
        raise ValueError("unsupported Pure Data source job")
    rate, source = job["sample_rate"], job["source"]
    if (type(rate) is not int or not 8000 <= rate <= 192000
            or not isinstance(source, dict)
            or set(source) != {"program", "duration_frames", "gain", "controls", "abstractions"}):
        raise ValueError("invalid Pure Data source job fields")

    program, frames = source["program"], source["duration_frames"]
    if (not isinstance(program, str) or not program or "\0" in program
            or type(frames) is not int
            or not (rate + 999) // 1000 <= frames <= rate * 10):
        raise ValueError("invalid Pure Data program or duration")
    program_bytes = _utf8_size(program, "Pure Data program")
    if program_bytes > _MAX_PROGRAM_BYTES:
        raise ValueError("Pure Data source programs exceed 61440 UTF-8 bytes")
    if (not _finite_float32(source["gain"])
            or not 0 <= source["gain"] <= 1):
        raise ValueError("invalid Pure Data source gain")

    abstractions = source["abstractions"]
    if not isinstance(abstractions, list) or len(abstractions) > 16:
        raise ValueError("invalid Pure Data abstractions")
    abstraction_names = set()
    for abstraction in abstractions:
        if not isinstance(abstraction, dict) or set(abstraction) != {"name", "program"}:
            raise ValueError("invalid Pure Data abstraction fields")
        name, text = abstraction["name"], abstraction["program"]
        if (not isinstance(name, str) or not _ABSTRACTION_NAME.fullmatch(name)
                or name in abstraction_names or not isinstance(text, str) or not text
                or "\0" in text):
            raise ValueError("invalid or duplicate Pure Data abstraction")
        abstraction_names.add(name)
        program_bytes += _utf8_size(text, f"Pure Data abstraction {name}")
        if program_bytes > _MAX_PROGRAM_BYTES:
            raise ValueError("Pure Data source programs exceed 61440 UTF-8 bytes")

    controls = source["controls"]
    if not isinstance(controls, list) or len(controls) > 64:
        raise ValueError("invalid Pure Data controls")
    names, event_count = set(), 0
    for control in controls:
        if (not isinstance(control, dict)
                or set(control) != {"name", "value", "points"}):
            raise ValueError("invalid Pure Data control fields")
        name, points = control["name"], control["points"]
        if (not isinstance(name, str) or not name or "\0" in name
                or _utf8_size(name, "Pure Data control name") > 128
                or name in names or not _finite_float32(control["value"])
                or not isinstance(points, list)):
            raise ValueError("invalid Pure Data control name, value, or points")
        names.add(name)
        previous = -1
        for point in points:
            if (not isinstance(point, dict) or set(point) != {"frame", "value"}
                    or type(point["frame"]) is not int
                    or not previous < point["frame"] < frames
                    or point["frame"] % _BLOCK
                    or not _finite_float32(point["value"])):
                raise ValueError("invalid Pure Data control event")
            previous = point["frame"]
        event_count += len(points)
    if event_count > 512:
        raise ValueError("too many Pure Data control events")
    return rate, source


def _reject_constant(value):
    raise ValueError(f"nonfinite JSON constant {value}")


def load_job(job_path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(job_path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Pure Data job must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(_MAX_JOB_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > _MAX_JOB_BYTES:
        raise ValueError("Pure Data source job exceeds 1 MiB")
    return validate_job(json.loads(payload, parse_constant=_reject_constant))


def _actual_receiver(name, dollarzero):
    if name.startswith("$0-"):
        return f"{dollarzero}{name[2:]}"
    return name


def perform_source(rate, source, library, emit_block, before_dsp=None):
    """Render complete libpd ticks and copy trimmed interleaved float64 PCM."""
    validate_job({"job_version": 1, "sample_rate": rate, "source": source})
    library_path = Path(library)
    if not library_path.is_absolute() or not library_path.is_file():
        raise ValueError("libpd library must be an existing absolute file")

    api = _bind(library_path)
    exists = api.libpd_exists
    exists.restype, exists.argtypes = C.c_int, [C.c_char_p]
    if api.libpd_init() not in (0, -1):
        raise RuntimeError("libpd initialization failed")
    instance = api.libpd_new_instance()
    if not instance:
        raise RuntimeError("libpd requires a MULTI=true build")

    patch = None
    patch_directory = None
    temp_directory = None
    hook_state = {"log": bytearray(), "log_overflow": False}

    @_PRINT_HOOK
    def on_print(message):
        chunk = message or b""
        room = _MAX_LOG_BYTES - len(hook_state["log"])
        hook_state["log"].extend(chunk[:room])
        if len(chunk) > room:
            hook_state["log_overflow"] = True

    try:
        temp_directory = tempfile.TemporaryDirectory(
            prefix="daw-libpd-source-", dir=Path.cwd())
        api.libpd_set_instance(instance)
        api.libpd_set_printhook(on_print)
        api.libpd_clear_search_path()

        patch_directory = Path(temp_directory.name) / "patch"
        abstraction_directory = Path(temp_directory.name) / "abstractions"
        patch_directory.mkdir()
        abstraction_directory.mkdir()
        patch_path = patch_directory / "source.pd"
        patch_path.write_text(source["program"], encoding="utf-8")
        for abstraction in source["abstractions"]:
            (abstraction_directory / f"{abstraction['name']}.pd").write_text(
                abstraction["program"], encoding="utf-8")
        api.libpd_add_to_search_path(os.fsencode(abstraction_directory))

        if api.libpd_init_audio(2, 2, rate) != 0 or api.libpd_blocksize() != _BLOCK:
            raise RuntimeError("libpd requires stereo float audio and 64-frame blocks")
        patch = api.libpd_openfile(b"source.pd", os.fsencode(patch_directory))
        if not patch:
            raise RuntimeError("Pure Data patch could not be opened")
        if _has_pd_error(hook_state):
            raise RuntimeError("Pure Data patch reported an error: " + _diagnostic(hook_state))

        dollarzero = api.libpd_getdollarzero(patch)
        if dollarzero <= 0:
            raise RuntimeError("Pure Data patch identity is unavailable")

        resolved_names = set()
        for control in source["controls"]:
            receiver = _actual_receiver(control["name"], dollarzero).encode("utf-8")
            if receiver in resolved_names:
                raise ValueError("Pure Data controls resolve to a duplicate receiver")
            resolved_names.add(receiver)
            if not exists(receiver):
                raise RuntimeError(f"Pure Data receiver does not exist: {control['name']}")

        def set_control(control, value):
            receiver = _actual_receiver(control["name"], dollarzero).encode("utf-8")
            if not exists(receiver) or api.libpd_float(receiver, C.c_float(value).value) != 0:
                raise RuntimeError(f"Pure Data receiver does not exist: {control['name']}")

        events = {}
        for control in source["controls"]:
            base = control["value"]
            for point in control["points"]:
                if point["frame"] == 0:
                    base = point["value"]
                else:
                    events.setdefault(point["frame"], []).append((control, point["value"]))
            set_control(control, base)

        inputs = (C.c_float * (_BLOCK * 2))()
        outputs = (C.c_float * (_BLOCK * 2))()
        _check_callbacks(hook_state)
        if before_dsp is not None:
            before_dsp()
        _dsp(api, True)
        for frame in range(0, source["duration_frames"], _BLOCK):
            for control, value in events.get(frame, ()):
                set_control(control, value)
            if api.libpd_process_float(1, inputs, outputs) != 0:
                raise RuntimeError(f"libpd processing failed at frame {frame}")
            count = min(_BLOCK, source["duration_frames"] - frame)
            block = bytearray()
            for sample in range(count * 2):
                value = outputs[sample]
                if not math.isfinite(value):
                    raise RuntimeError("Pure Data source produced nonfinite PCM")
                block.extend(struct.pack("<d", float(value)))
            emit_block(frame, bytes(block))
            _check_callbacks(hook_state)
    finally:
        try:
            api.libpd_set_instance(instance)
            _dsp(api, False)
        finally:
            try:
                if patch:
                    api.libpd_closefile(patch)
            finally:
                try:
                    api.libpd_free_instance(instance)
                finally:
                    if temp_directory is not None:
                        temp_directory.cleanup()
                    _ = (on_print, patch_directory)


def _diagnostic(state):
    return bytes(state["log"]).decode("utf-8", "replace")[:512]


def _has_pd_error(state):
    lowered = bytes(state["log"]).lower()
    return b"error:" in lowered or b"couldn't create" in lowered


def _check_callbacks(state):
    if state["log_overflow"] or _has_pd_error(state):
        raise RuntimeError("Pure Data diagnostics exceeded bounds: " + _diagnostic(state))


def prepare(job_path, output, library):
    rate, source = load_job(job_path)
    with open(output, "xb") as stream:
        try:
            stream.write(b"DPD1" + struct.pack("<IQI", rate, source["duration_frames"], 2))
            perform_source(rate, source, library,
                           lambda _frame, block: stream.write(block))
        except BaseException:
            stream.close()
            try:
                os.unlink(output)
            except FileNotFoundError:
                pass
            raise


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4:
            raise ValueError("Usage: source_worker.py JOB OUTPUT LIBRARY")
        prepare(*sys.argv[1:])
    except Exception as error:
        print(json.dumps({"ok": False, "error": {"code": "runtime_error", "message": str(error)}}),
              file=sys.stderr)
        raise SystemExit(1)

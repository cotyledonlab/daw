#!/usr/bin/env python3
"""Bounded wrapper for the owned Audio Unit probe process."""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import signal
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "output/au-spike/au-host"
STDOUT_LIMIT = 256 * 1024
STDERR_LIMIT = 64 * 1024
TIMEOUT = 10.0
IDENTITY_PARTS = ("aufx", "lpas", "appl")


def _failure(code: str, message: str) -> dict:
    return {"schema_version": 1, "ok": False,
            "error": {"code": code, "message": message}}


def _identity(parts):
    if len(parts) != 3:
        raise ValueError("component identity needs type, subtype, and manufacturer")
    for part in parts:
        if (not isinstance(part, str) or len(part) != 4 or not part.isascii()
                or any(ord(char) < 32 or ord(char) > 126 for char in part)):
            raise ValueError("component identity fields must each be four printable ASCII characters")
    return tuple(parts)


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _bounded_name(value, field):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 2048:
        raise ValueError(f"{field} must be a nonempty string of at most 2048 UTF-8 bytes")
    if len(value.encode("utf-16-le")) // 2 > 512:
        raise ValueError(f"{field} exceeds 512 UTF-16 code units")


def probe(*identity: str, host=HOST, timeout=TIMEOUT) -> dict:
    """Run one exact component probe and return a validated JSON envelope."""
    try:
        identity = _identity(identity or IDENTITY_PARTS)
    except ValueError as error:
        return _failure("invalid_identity", str(error))
    if identity != IDENTITY_PARTS:
        return _failure("unsupported_identity", "only Apple AULowpass (aufx/lpas/appl) is supported")
    if not _finite_number(timeout) or not 0 < timeout <= TIMEOUT:
        return _failure("invalid_timeout", "timeout must be in (0,10]")

    try:
        child = subprocess.Popen([str(host), "probe", *identity], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
    except OSError as error:
        return _failure("host_unavailable", str(error))

    overflow = threading.Event()
    streams = [bytearray(), bytearray()]
    limits = [STDOUT_LIMIT, STDERR_LIMIT]

    def kill_group():
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def drain(stream, data, limit):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                room = limit - len(data)
                data.extend(chunk[:room])
                if len(chunk) > room:
                    overflow.set()
                    kill_group()
                    break
        finally:
            stream.close()

    readers = [threading.Thread(target=drain, args=(stream, data, limit), daemon=True)
               for stream, data, limit in zip((child.stdout, child.stderr), streams, limits)]
    for reader in readers:
        reader.start()
    timed_out = False
    cleanup_failed = False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_group()
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            cleanup_failed = True
    # The process is in its own session. Kill the whole group even after its
    # leader exits, since a descendant may have inherited either output pipe.
    kill_group()
    if child.returncode is None:
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            cleanup_failed = True
    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        cleanup_failed = True
    if cleanup_failed:
        return _failure("probe_protocol", "child process group or output pipes did not clean up")
    if overflow.is_set():
        return _failure("probe_output_too_large", "child output exceeded its per-stream limit")
    if timed_out:
        return _failure("probe_timeout", "Audio Unit probe timed out")
    if child.returncode < 0:
        return _failure("probe_crashed", f"child terminated by signal {-child.returncode}")

    try:
        envelope = json.loads(streams[0].decode("utf-8"))
        if not isinstance(envelope, dict) or type(envelope.get("schema_version")) is not int or envelope["schema_version"] != 1:
            raise ValueError("expected schema_version 1 envelope")
        if type(envelope.get("ok")) is not bool:
            raise ValueError("expected boolean ok field")
        if envelope["ok"]:
            if child.returncode != 0 or not isinstance(envelope.get("result"), dict):
                raise ValueError("success envelope requires exit status 0 and a result object")
            result = envelope["result"]
            required = ("component", "sample_rate", "channels", "frames", "block_frames",
                        "parameter", "open_rms", "closed_rms", "restored_rms",
                        "max_restore_error", "state_bytes", "restored_cutoff",
                        "input_callback_calls", "resources_released")
            if any(key not in result for key in required):
                raise ValueError("success result is missing required probe fields")
            if not isinstance(result["component"], dict) or not isinstance(result["parameter"], dict):
                raise ValueError("component and parameter must be objects")
            component = result["component"]
            expected_identity = dict(zip(("type", "subtype", "manufacturer"), identity))
            if any(component.get(key) != value for key, value in expected_identity.items()):
                raise ValueError("component identity does not match the requested identity")
            _bounded_name(component.get("name"), "component name")
            if result["resources_released"] is not True:
                raise ValueError("probe did not confirm resource release")
            if any(type(result[key]) is not int for key in ("sample_rate", "channels", "frames", "block_frames")):
                raise ValueError("probe format fields must be integers")
            if (result["sample_rate"], result["channels"], result["frames"], result["block_frames"]) != (48000, 2, 4096, 256):
                raise ValueError("probe format must be 48 kHz stereo, 4096 frames in 256-frame blocks")
            for key, lower, upper in (("state_bytes", 1, 65536), ("input_callback_calls", 1, 4096)):
                if type(result[key]) is not int or not lower <= result[key] <= upper:
                    raise ValueError(f"{key} must be an integer in [{lower},{upper}]")
            for key in ("open_rms", "closed_rms", "restored_rms", "max_restore_error", "restored_cutoff"):
                if not _finite_number(result[key]):
                    raise ValueError(f"{key} must be finite")
            if result["open_rms"] < 0 or result["closed_rms"] <= 0 or result["restored_rms"] < 0:
                raise ValueError("RMS values must be nonnegative and closed RMS must be positive")
            if result["open_rms"] <= result["closed_rms"] * 5:
                raise ValueError("open RMS must exceed closed RMS by more than five times")
            if abs(result["restored_rms"] - result["closed_rms"]) > 1e-6:
                raise ValueError("restored RMS does not match closed RMS")
            if not 0 <= result["max_restore_error"] < 1e-6:
                raise ValueError("restore error must be in [0,1e-6)")
            if abs(result["restored_cutoff"] - 200) > 0.01:
                raise ValueError("restored cutoff must be 200 Hz")
            parameter = result["parameter"]
            if type(parameter.get("id")) is not int or parameter["id"] != 0:
                raise ValueError("parameter ID must be zero")
            _bounded_name(parameter.get("name"), "parameter name")
            for key in ("min", "max", "default"):
                if not _finite_number(parameter.get(key)):
                    raise ValueError(f"parameter {key} must be finite")
            if parameter["min"] > 200 or parameter["max"] < 10000 or not parameter["min"] <= parameter["default"] <= parameter["max"]:
                raise ValueError("parameter range must include 200 Hz and 10000 Hz and contain its default")
            if type(parameter.get("unit")) is not int or parameter["unit"] != 8:
                raise ValueError("parameter unit must be Audio Unit Hertz (8)")
        else:
            error = envelope.get("error")
            if child.returncode == 0 or not isinstance(error, dict) or not isinstance(error.get("code"), str) or not isinstance(error.get("message"), str):
                raise ValueError("failure envelope requires nonzero exit and structured error")
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        return _failure("probe_protocol", str(error))
    return envelope


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity", nargs="*", help="optional four-character type subtype manufacturer")
    args = parser.parse_args(argv)
    if args.identity and len(args.identity) != 3:
        parser.error("provide either no identity or exactly type subtype manufacturer")
    result = probe(*args.identity)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bounded child-process VST3 factory inspection and fixture lifecycle probing."""
import argparse
import json
import math
import os
import pathlib
import signal
import subprocess
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "output/vst3-spike/vst3-host"
MAX_OUTPUT_BYTES = 65536


def inspect_plugin(path, *, mode="scan", timeout=5.0, host=HOST, env=None):
    if mode not in ("scan", "probe") or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("mode must be scan/probe and timeout must be in (0,30]")
    path = str(pathlib.Path(path).resolve())
    try:
        process = subprocess.Popen([str(host), mode, path], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, start_new_session=True)
    except OSError as error:
        return {"ok": False, "path": path, "code": "host_unavailable", "error": str(error)}
    overflow = threading.Event()
    captured = [bytearray(), bytearray()]

    def terminate_group():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def drain(stream, destination):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                available = MAX_OUTPUT_BYTES - len(destination)
                destination.extend(chunk[:available])
                if len(chunk) > available:
                    overflow.set()
                    terminate_group()
                    break
        finally:
            stream.close()

    readers = [threading.Thread(target=drain, args=(stream, destination), daemon=True)
               for stream, destination in zip((process.stdout, process.stderr), captured)]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_group()
        process.wait(timeout=2)
    # Kill descendants retaining pipes even after the immediate child has exited.
    for reader in readers:
        reader.join(timeout=0.2)
    if any(reader.is_alive() for reader in readers):
        terminate_group()
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            return {"ok": False, "path": path, "code": "probe_protocol",
                    "error": "child retained output pipes"}
    if overflow.is_set():
        return {"ok": False, "path": path, "code": "probe_output_too_large",
                "error": "child output exceeded 64 KiB per stream"}
    if timed_out:
        return {"ok": False, "path": path, "code": "probe_timeout", "error": "child timed out"}
    if process.returncode < 0:
        return {"ok": False, "path": path, "code": "probe_crashed",
                "error": f"child terminated by signal {-process.returncode}"}
    try:
        result = json.loads(captured[0].decode("utf-8"))
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise ValueError("expected one result object")
        if process.returncode != 0 and result["ok"]:
            raise ValueError("success result with failed exit")
        if result["ok"] and mode == "scan" and not isinstance(result.get("classes"), list):
            raise ValueError("missing class list")
    except (ValueError, UnicodeError) as error:
        return {"ok": False, "path": path, "code": "probe_protocol", "error": str(error)}
    return {**result, "path": path, **({"code": "plugin_error"} if not result["ok"] else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="explicit VST3 bundle paths; maximum 256")
    parser.add_argument("--probe", action="store_true", help="process only the DAW test fixture")
    parser.add_argument("--timeout", type=float, default=5)
    args = parser.parse_args()
    if len(args.paths) > 256:
        parser.error("at most 256 paths are allowed")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 30:
        parser.error("timeout must be in (0,30]")
    results = [inspect_plugin(path, mode="probe" if args.probe else "scan",
                              timeout=args.timeout) for path in args.paths]
    print(json.dumps({"results": results}))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

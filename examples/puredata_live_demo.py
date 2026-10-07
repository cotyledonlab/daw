#!/usr/bin/env python3
"""Run saved Pure Data tracks through one second of muted live native transport."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.puredata_tracks_demo import make_track  # noqa: E402
from gui.server import Engine  # noqa: E402


def request(engine: Engine, method: str, params: dict | None = None):
    try:
        return engine.call(method, params)
    except Exception as error:
        raise RuntimeError(f"{method} failed: {error}") from error


def wait_for_stop(engine: Engine, timeout: float = 12.0):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = request(engine, "transport.status")
        if latest.get("state") == "stopped":
            if latest.get("error"):
                raise RuntimeError(f"live Pure Data transport failed: {latest['error']}")
            return latest
        time.sleep(0.025)
    raise RuntimeError(f"live Pure Data transport did not finish within {timeout:g} seconds: {latest}")


def assert_digest(value, label: str) -> None:
    # Existing runtime digests are 64-bit hex; the queue proof uses SHA-256.
    if not isinstance(value, str) or re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{64})", value) is None:
        raise RuntimeError(f"{label} is not a supported hexadecimal audio digest: {value!r}")


def assert_pids_reaped(pids) -> None:
    if not isinstance(pids, list) or not pids or any(type(pid) is not int or pid <= 0 for pid in pids):
        raise RuntimeError(f"live Pure Data transport did not report its owned worker PIDs: {pids!r}")
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            raise RuntimeError(f"owned Pure Data worker PID {pid} still exists")
        except OSError as error:
            if error.errno == errno.ESRCH:
                continue
            raise RuntimeError(f"could not verify reaping of Pure Data worker PID {pid}: {error}") from error
        raise RuntimeError(f"owned Pure Data worker PID {pid} was not reaped")


def main() -> None:
    library = os.environ.get("DAW_LIBPD_LIBRARY")
    if not library or not Path(library).is_absolute() or not Path(library).is_file():
        raise RuntimeError("set DAW_LIBPD_LIBRARY to an absolute path to the multi-instance libpd library")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="puredata-live-", dir=output))
    session_path = project / "session.json"

    audible_pd = make_track("pd-live", 440.0, 0.5)
    audible_pd["device"]["controls"][1]["points"] = [{"frame": 24576, "value": 0.05}]

    session = {
        "schema_version": 8,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [
            audible_pd,
            # Keep a second live libpd instance in the session while muting its mix contribution.
            make_track("pd-muted", 330.0, 0.0),
            {
                "id": "sine-muted",
                "mode": "continuous",
                "clips": [],
                "effects": [],
                "device": {"kind": "sine", "frequency_hz": 220.0, "gain": 0.0},
            },
        ],
    }

    engine = Engine(ROOT / "target/debug/daw")
    active = False
    try:
        capabilities = request(engine, "capabilities")
        if not capabilities.get("live_audio"):
            raise RuntimeError("the selected DAW binary has no native-audio support; build it with --features native-audio")
        if not capabilities.get("puredata_sources", {}).get("configured"):
            raise RuntimeError("the DAW engine cannot configure libpd; check DAW_LIBPD_LIBRARY")
        live_capability = capabilities.get("puredata_live_transport", {})
        if not live_capability.get("implemented"):
            raise RuntimeError("this DAW binary does not implement Pure Data live transport")

        request(engine, "session.replace", {"session": session})
        saved = request(engine, "session.get")
        if saved.get("schema_version") != 8 or len(saved.get("tracks", [])) != 3:
            raise RuntimeError(f"session.replace did not retain two Pd tracks and the muted sine track: {saved}")
        request(engine, "session.save", {"path": str(session_path)})
        request(engine, "session.load", {"path": str(session_path)})
        restored = request(engine, "session.get")
        persisted = json.loads(session_path.read_text(encoding="utf-8"))
        if restored != saved or persisted != restored:
            raise RuntimeError("saved/reloaded schema-v8 session differs from the prepared project")

        before = request(engine, "session.inspect")
        started = request(engine, "transport.play", {
            "seconds": 1.0,
            "volume": 0.0,
            "source_mode": "live",
        })
        active = True
        if started.get("state") not in ("starting", "playing"):
            raise RuntimeError(f"unexpected live transport startup state: {started}")
        if started.get("source_mode") != "live" or started.get("runtime") != "puredata":
            raise RuntimeError(f"transport did not select live Pure Data: {started}")
        if started.get("startup") != "prefilled":
            raise RuntimeError(f"native stream was not prefilled at startup: {started}")
        startup_source = started.get("source", {})
        if startup_source.get("runtime_sources") != 2 or len(startup_source.get("owned_pids", [])) != 2:
            raise RuntimeError(f"expected two separately owned Pure Data workers: {started}")
        if request(engine, "session.inspect") != before:
            raise RuntimeError("starting live transport changed the saved session or revision")

        stopped = wait_for_stop(engine)
        active = False
        source = stopped.get("source", {})
        if stopped.get("source_mode") != "live" or stopped.get("runtime") != "puredata":
            raise RuntimeError(f"final snapshot lost live Pure Data identity: {stopped}")
        if stopped.get("resources_released") is not True:
            raise RuntimeError(f"live Pure Data transport did not release its resources: {stopped}")
        if source.get("source_frames") != 48000 or stopped.get("submitted_frames") != 48000:
            raise RuntimeError(f"expected exactly 48000 live source and callback frames: {stopped}")
        if source.get("runtime_sources") != 2:
            raise RuntimeError(f"expected both Pure Data sources in the final report: {source}")
        if stopped.get("live_source_underruns") != 0:
            raise RuntimeError(f"live Pure Data source underruns occurred: {stopped}")
        source_digest = source.get("source_digest")
        callback_digest = stopped.get("callback_source_digest")
        assert_digest(source_digest, "source digest")
        assert_digest(callback_digest, "callback source digest")
        if source_digest != callback_digest:
            raise RuntimeError(f"source and callback frame digests differ: {stopped}")
        if (source.get("owned_processes_released") is not True
                or source.get("owned_servers_released") is not True
                or source.get("queue_released") is not True):
            raise RuntimeError(f"Owned Pure Data processes or servers were not released: {source}")
        quarters = source.get("rms_quarters", [])
        if (len(quarters) != 4 or abs(quarters[0] - 0.025 / (2 ** 0.5)) > 0.0001
                or abs(quarters[3] - 0.0125 / (2 ** 0.5)) > 0.0001
                or abs(source.get("pre_master_peak", 0) - 0.025) > 0.0001):
            raise RuntimeError(f"Saved amplitude event or source/effect gain did not reach live audio: {source}")
        assert_pids_reaped(source.get("owned_pids"))
        if request(engine, "session.inspect") != before:
            raise RuntimeError("live transport changed the saved session or revision")

        print(json.dumps({"monitor_volume": 0, "transport": stopped}, indent=2))
    finally:
        if active:
            try:
                status = request(engine, "transport.status")
                if status.get("state") != "stopped":
                    request(engine, "transport.stop")
            except Exception:
                pass
        engine.close()


if __name__ == "__main__":
    main()

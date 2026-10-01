#!/usr/bin/env python3
"""Run two saved SuperCollider tracks through muted JSONL native transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.supercollider_tracks_demo import make_track  # noqa: E402
from gui.server import Engine  # noqa: E402


def request(engine, method, params=None):
    try:
        return engine.call(method, params)
    except Exception as error:
        raise RuntimeError(f"{method} failed: {error}") from error


def wait_for_stop(engine, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = request(engine, "transport.status")
        if status.get("state") == "stopped":
            if "error" in status:
                raise RuntimeError(f"live SuperCollider transport failed: {status['error']}")
            return status
        time.sleep(0.025)
    raise RuntimeError("live SuperCollider transport did not finish within 12 seconds")


def assert_digest(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{16}", value) is None:
        raise RuntimeError(f"{label} is not a 64-bit digest: {value!r}")


def main():
    scsynth = os.environ.get("DAW_SCSYNTH")
    if not scsynth or not Path(scsynth).is_absolute() or not Path(scsynth).is_file():
        raise RuntimeError("set DAW_SCSYNTH to an absolute installed scsynth executable")
    binary = ROOT / "target" / "debug" / "daw"
    engine = Engine(binary)
    active = False
    try:
        capabilities = request(engine, "capabilities")
        if not capabilities.get("live_audio"):
            raise RuntimeError("build with --features native-audio on macOS first")
        if not capabilities.get("supercollider_live_transport", {}).get("implemented"):
            raise RuntimeError("this DAW build does not include live SuperCollider transport")

        session = {
            "schema_version": 6,
            "sample_rate": 48000,
            "tempo_milli_bpm": 120000,
            "tracks": [
                make_track("live-low", 220.0, 0.12, 0.7, 1.0, 1.0),
                make_track("live-high", 330.0, 0.08, 0.7, 1.0, 1.0),
            ],
        }
        request(engine, "session.replace", {"session": session})
        saved = request(engine, "session.get")
        if saved.get("schema_version") != 6 or len(saved.get("tracks", [])) != 2:
            raise RuntimeError(f"session.replace did not retain both SC tracks: {saved}")

        started = request(engine, "transport.play", {
            "seconds": 1,
            "volume": 0,
            "source_mode": "live",
        })
        active = True
        if started.get("state") not in ("starting", "playing"):
            raise RuntimeError(f"unexpected live transport startup state: {started}")
        if started.get("source_mode") != "live" or started.get("runtime") != "supercollider":
            raise RuntimeError(f"transport did not select live SuperCollider: {started}")
        if started.get("startup") != "prefilled":
            raise RuntimeError(f"native stream was not prefilled at startup: {started}")
        startup_source = started.get("source", {})
        if startup_source.get("runtime_sources") != 2 or len(startup_source.get("owned_pids", [])) != 2:
            raise RuntimeError(f"expected two separately owned SuperCollider sources: {started}")

        stopped = wait_for_stop(engine)
        active = False
        source = stopped.get("source", {})
        if stopped.get("source_mode") != "live" or stopped.get("resources_released") is not True:
            raise RuntimeError(f"live transport did not report released resources: {stopped}")
        if source.get("source_frames") != 48000 or stopped.get("submitted_frames") != 48000:
            raise RuntimeError(f"expected 48000 source frames: {source}")
        if source.get("runtime_sources") != 2 or len(source.get("owned_pids", [])) != 2:
            raise RuntimeError(f"final report lost owned SuperCollider sources: {source}")
        if source.get("owned_servers_released") is not True or source.get("queue_released") is not True:
            raise RuntimeError(f"SuperCollider server or queue was not released: {source}")
        if stopped.get("live_source_underruns") != 0:
            raise RuntimeError(f"live source underruns occurred: {stopped}")
        assert_digest(source.get("source_digest"), "source digest")
        assert_digest(stopped.get("callback_source_digest"), "callback source digest")
        if stopped["callback_source_digest"] != source["source_digest"]:
            raise RuntimeError(f"source and callback ordering digests differ: {stopped}")
        if stopped.get("callback_signal_peak", 0) <= 0:
            raise RuntimeError(f"callback observed no source signal: {stopped}")
        if len(source.get("rms_quarters", [])) != 4 or not any(source["rms_quarters"]):
            raise RuntimeError(f"source RMS report is missing or silent: {source}")
        if len(source.get("hardware_bus_peaks", [])) != 2 or any(source["hardware_bus_peaks"]):
            raise RuntimeError(f"SuperCollider hardware output buses were not silent: {source}")
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

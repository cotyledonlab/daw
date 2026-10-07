#!/usr/bin/env python3
"""Silent, bounded native VST3 transport check against a saved schema-v4 session."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.server import Engine, EngineError


def wait_for(engine, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = engine.call("transport.status")
        if predicate(status):
            return status
        if status["state"] == "error":
            raise RuntimeError(status)
        time.sleep(0.025)
    raise RuntimeError("native transport observation timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    engine = Engine(root / "target/debug/daw")
    try:
        if not engine.call("capabilities")["plugin_hosting"]:
            raise RuntimeError("build with --features vst3-live first")
        engine.call("session.load", {"path": str(args.session.resolve())})
        reports = []
        for _ in range(3):
            engine.call("transport.play", {"seconds": 3, "volume": 0})
            running = wait_for(engine, lambda s: s.get("submitted_frames", 0) >= 4096)
            engine.call("transport.pause")
            paused = wait_for(engine, lambda s: s["state"] == "paused")
            time.sleep(0.08)
            assert engine.call("transport.status")["timeline_frame"] == paused["timeline_frame"]
            for method, params in [("transport.seek", {"frame": 0}), ("transport.loop", {"region": None})]:
                try:
                    engine.call(method, params)
                except EngineError:
                    pass
                else:
                    raise AssertionError(f"{method} unexpectedly succeeded")
            engine.call("transport.resume")
            resumed = wait_for(engine, lambda s: s.get("timeline_frame", 0) > paused["timeline_frame"])
            assert resumed["callbacks_over_buffer_budget"] == 0, resumed
            assert resumed["plugin_worker_underruns"] == 0, resumed
            reports.append(resumed)
            assert engine.call("transport.stop")["state"] == "stopped"
        # Valid replacement also stops/releases the active snapshot before committing.
        engine.call("transport.play", {"seconds": 3, "volume": 0})
        engine.call("session.replace", {"session": engine.call("session.get")})
        assert engine.call("transport.status")["state"] == "stopped"
        print(json.dumps({"volume": 0, "acoustic_verification": False, "cycles": reports, "replacement_released": True}, indent=2))
    finally:
        engine.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Verify seek/loop callback acknowledgments with a bounded, silent native run."""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
session = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples/sessions/arpeggio.json"
process = subprocess.Popen(
    [str(ROOT / "target/debug/daw"), "serve"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
)
counter = 0


def command(method, params=None):
    global counter
    counter += 1
    process.stdin.write(json.dumps({
        "protocol_version": 1, "id": str(counter),
        "method": method, "params": params or {},
    }) + "\n")
    process.stdin.flush()
    response = json.loads(process.stdout.readline())
    assert response["ok"], response
    return response["result"]


def observe(predicate):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        status = command("transport.status")
        if predicate(status):
            return status
        time.sleep(0.01)
    raise AssertionError(f"callback transition timed out: {status}")


try:
    command("session.load", {"path": str(session.resolve())})
    revision = command("session.inspect")["revision"]
    command("transport.play", {"seconds": 3, "volume": 0})
    observe(lambda s: s["state"] == "playing")
    command("transport.pause")
    paused = observe(lambda s: s["state"] == "paused")
    count = paused["submitted_frames"]
    command("transport.loop", {"region": {"start_frame": 1200, "end_frame": 2400}})
    observe(lambda s: not s["timeline_command_pending"])
    command("transport.seek", {"frame": 1800})
    at = observe(lambda s: not s["timeline_command_pending"])
    assert at["timeline_frame"] == 1800 and at["submitted_frames"] == count, at
    command("transport.resume")
    playing = observe(lambda s: s["state"] == "playing" and s["submitted_frames"] > count + 4800)
    assert 1200 <= playing["timeline_frame"] <= 2400, playing
    command("transport.pause")
    observe(lambda s: s["state"] == "paused")
    command("transport.loop", {"region": None})
    observe(lambda s: not s["timeline_command_pending"])
    command("transport.seek", {"frame": 5000})
    observe(lambda s: not s["timeline_command_pending"])
    command("transport.resume")
    linear = observe(lambda s: s["timeline_frame"] > 6000)
    assert linear["submitted_frames"] >= playing["submitted_frames"], linear
    assert command("session.inspect")["revision"] == revision
    assert command("transport.stop")["state"] == "stopped"
    command("transport.play", {"seconds": 0.2, "volume": 0})
    restarted = observe(lambda s: s["state"] == "playing")
    assert restarted["loop_region"] is None
    observe(lambda s: s["state"] == "stopped")
    print(json.dumps({"looping": playing, "linear": linear, "restart_and_release": True}, indent=2))
finally:
    process.stdin.close()
    process.wait(timeout=5)

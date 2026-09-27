#!/usr/bin/env python3
"""Standard-library agent client. Run `cargo build --locked` first."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="demo-", dir=output))
    session = {
        "schema_version": 1,
        "sample_rate": 48000,
        "tracks": [
            {"id": "a", "device": {"kind": "sine", "frequency_hz": 220, "gain": 0.1}},
            {"id": "b", "device": {"kind": "sine", "frequency_hz": 330, "gain": 0.1}},
        ],
    }
    commands = [
        ("capabilities", {}),
        ("session.replace", {"session": session}),
        ("session.save", {"path": str(directory / "session.json")}),
        ("render", {"path": str(directory / "demo.wav"), "seconds": 1}),
    ]
    with subprocess.Popen(
        [str(ROOT / "target/debug/daw"), "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT,
    ) as server:
        try:
            for index, (method, params) in enumerate(commands):
                request_id = str(index)
                server.stdin.write(json.dumps({"protocol_version": 1, "id": request_id, "method": method, "params": params}) + "\n")
                server.stdin.flush()
                response = json.loads(server.stdout.readline())
                if response.get("id") != request_id or not response.get("ok"):
                    raise RuntimeError(response)
                print(json.dumps(response))
        finally:
            server.stdin.close()
        if server.wait(timeout=5) != 0:
            raise RuntimeError("DAW process failed")
    print(f"Session: {directory / 'session.json'}")
    print(f"WAV: {directory / 'demo.wav'}")


if __name__ == "__main__":
    main()

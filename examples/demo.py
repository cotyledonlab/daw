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
    with subprocess.Popen(
        [str(ROOT / "target/debug/daw"), "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT,
    ) as server:
        try:
            def call(method, params):
                request_id = method
                server.stdin.write(json.dumps({"protocol_version": 1, "id": request_id, "method": method, "params": params}) + "\n")
                server.stdin.flush()
                response = json.loads(server.stdout.readline())
                if response.get("id") != request_id or not response.get("ok"):
                    raise RuntimeError(response)
                print(json.dumps(response))
                return response["result"]

            capabilities = call("capabilities", {})
            kind = capabilities["devices"][0]
            parameters = capabilities["device_metadata"][kind]["parameters"]
            device = {"kind": kind, **{name: info["default"] for name, info in parameters.items()}}
            session = {
                "schema_version": capabilities["session_schema_version"],
                "sample_rate": capabilities["session"]["sample_rate"]["default"],
                "tracks": [{"id": "demo", "device": device}],
            }
            call("session.replace", {"session": session})
            call("session.save", {"path": str(directory / "session.json")})
            call("render", {"path": str(directory / "demo.wav"), "seconds": 1})
        finally:
            server.stdin.close()
        if server.wait(timeout=5) != 0:
            raise RuntimeError("DAW process failed")
    print(f"Session: {directory / 'session.json'}")
    print(f"WAV: {directory / 'demo.wav'}")


if __name__ == "__main__":
    main()

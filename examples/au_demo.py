#!/usr/bin/env python3
"""Save, reload, and render two cutoff settings through an offline Audio Unit."""
import json
from pathlib import Path
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]


def make_session(cutoff):
    return {"schema_version": 5, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [{"id": "sine", "mode": "continuous", "clips": [],
                        "device": {"kind": "sine", "frequency_hz": 440.0, "gain": 0.1},
                        "effects": [{"kind": "au", "id": "lowpass", "bypass": False,
                                     "component_type": "aufx", "component_subtype": "lpas",
                                     "component_manufacturer": "appl", "state_hex": "",
                                     "parameters": [{"id": 0, "value": cutoff}]}]}]}


def main():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="au-demo-", dir=output))
    session_path = project / "session.json"
    low_wav = project / "cutoff-200.wav"
    high_wav = project / "cutoff-10000.wav"
    commands = [
        ("capabilities", {}),
        ("session.replace", {"session": make_session(200.0)}),
        ("session.save", {"path": str(session_path)}),
        ("render", {"path": str(low_wav), "seconds": 1}),
        ("session.load", {"path": str(session_path)}),
        ("session.replace", {"session": make_session(10000.0)}),
        ("render", {"path": str(high_wav), "seconds": 1}),
    ]
    wire = "".join(json.dumps({"protocol_version": 1, "id": f"au-demo-{index}",
                               "method": method, "params": params}, allow_nan=False) + "\n"
                   for index, (method, params) in enumerate(commands))
    result = subprocess.run([str(ROOT / "target/debug/daw"), "serve"], input=wire,
                            text=True, capture_output=True, cwd=ROOT, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError(f"DAW controller exited {result.returncode}: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    if len(lines) != len(commands):
        raise RuntimeError(f"Expected {len(commands)} responses, received {len(lines)}: {result.stderr.strip()}")
    responses = [json.loads(line) for line in lines]
    for index, ((method, _), response) in enumerate(zip(commands, responses)):
        if response.get("id") != f"au-demo-{index}" or not response.get("ok"):
            raise RuntimeError(f"{method} failed: {response}")
    if not responses[0]["result"].get("offline_au", {}).get("implemented"):
        raise RuntimeError("This daw binary does not implement offline Audio Units; build with au-offline")
    with wave.open(str(low_wav), "rb") as low, wave.open(str(high_wav), "rb") as high:
        print(f"Session: {session_path}")
        print(f"200 Hz cutoff WAV: {low_wav} ({low.getnframes()} frames)")
        print(f"10 kHz cutoff WAV: {high_wav} ({high.getnframes()} frames)")


if __name__ == "__main__":
    main()

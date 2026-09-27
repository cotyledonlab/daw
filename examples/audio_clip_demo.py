#!/usr/bin/env python3
"""Create a disposable PCM clip project and render it using the Rust controller."""
import json
from pathlib import Path
import math
import struct
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]


def main():
    (ROOT / "output").mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="audio-clips-", dir=ROOT / "output"))
    with wave.open(str(project / "tone.wav"), "wb") as audio:
        audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        samples = bytearray()
        for frame in range(24000):
            envelope = min(1, frame / 240, (23999 - frame) / 240)
            left = round(32767 * 0.2 * envelope * math.sin(2 * math.pi * 330 * frame / 48000))
            right = round(32767 * 0.2 * envelope * math.sin(2 * math.pi * 440 * frame / 48000))
            samples.extend(struct.pack("<hh", left, right))
        audio.writeframes(samples)
    clips = [{"kind": "audio", "id": f"c{index}", "start_frame": start,
              "length_frames": 24000, "source_offset_frames": 0,
              "source_path": "tone.wav", "gain": 0.7} for index, start in enumerate([0, 36000])]
    session = {"schema_version": 2, "sample_rate": 48000, "tempo_milli_bpm": 120000,
               "tracks": [{"id": "audio", "mode": "sequenced",
                           "device": {"kind": "audio", "gain": 0.8}, "clips": clips}]}
    session_path = project / "session.json"
    session_path.write_text(json.dumps(session, indent=2) + "\n")
    commands = [("session.load", {"path": str(session_path)}),
                ("session.save", {"path": str(project / "saved.json")}),
                ("render", {"path": str(project / "mix.wav"), "seconds": 1.5})]
    wire = "".join(json.dumps({"protocol_version": 1, "id": method, "method": method,
                              "params": params}) + "\n" for method, params in commands)
    result = subprocess.run([str(ROOT / "target/debug/daw"), "serve"], input=wire,
                            text=True, capture_output=True, check=True, timeout=30)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    if len(responses) != len(commands):
        raise RuntimeError("Missing engine responses")
    for (method, _), response in zip(commands, responses):
        if response.get("id") != method or not response.get("ok"):
            raise RuntimeError(response)
    print(f"Session: {session_path}")
    print(f"WAV: {project / 'mix.wav'}")


if __name__ == "__main__":
    main()

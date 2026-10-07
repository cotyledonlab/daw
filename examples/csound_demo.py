#!/usr/bin/env python3
"""Render two Csound fixtures through the DAW JSONL API without playback."""

import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "native/csound/sine.csd"
RATE = 48000
FRAMES = 48000


def read_wav(path):
    with wave.open(str(path), "rb") as wav:
        metadata = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes())
        raw = wav.readframes(wav.getnframes())
    channels, width, rate, frames = metadata
    if (channels, width, rate, frames) != (2, 2, RATE, FRAMES):
        raise RuntimeError(f"Unexpected WAV format for {path}: {metadata}")
    samples = struct.unpack("<" + "h" * (frames * channels), raw)
    left = samples[::2]
    right = samples[1::2]
    if left != right:
        raise RuntimeError(f"Expected identical stereo channels in {path}")
    peak = max(abs(sample) for sample in left)
    crossings = [i for i in range(1, len(left)) if left[i - 1] <= 0 < left[i]]
    frequency = ((len(crossings) - 1) * RATE / (crossings[-1] - crossings[0])) if len(crossings) > 1 else None
    rms = math.sqrt(sum(sample * sample for sample in left) / len(left))
    if peak < 100 or rms < 10 or frequency is None:
        raise RuntimeError(f"Rendered audio is silent or invalid in {path}: peak={peak}, rms={rms}")
    return {"peak": peak, "rms": rms, "frequency": frequency}


def main():
    executable = os.environ.get("DAW_CSOUND")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise RuntimeError("Set DAW_CSOUND to the absolute path of an installed Csound executable")
    engine = ROOT / "target/debug/daw"
    if not engine.is_file():
        raise RuntimeError("Build the DAW engine first (cargo build --locked)")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="csound-demo-", dir=output))
    source = FIXTURE.read_text(encoding="utf-8")
    fixture_a = project / "sine-440.csd"
    fixture_b = project / "sine-660.csd"
    fixture_a.write_text(source, encoding="utf-8")
    fixture_b.write_text(source.replace("i1 0 1 440 .1", "i1 0 1 660 .05"), encoding="utf-8")
    wav_a, wav_b = project / "sine-440.wav", project / "sine-660.wav"

    requests = [
        {"protocol_version": 1, "id": "before", "method": "session.inspect", "params": {}},
        {"protocol_version": 1, "id": "render-a", "method": "csound.render",
         "params": {"csd_path": str(fixture_a.resolve()), "path": str(wav_a.resolve()),
                    "sample_rate": RATE, "duration_frames": FRAMES}},
        {"protocol_version": 1, "id": "render-b", "method": "csound.render",
         "params": {"csd_path": str(fixture_b.resolve()), "path": str(wav_b.resolve()),
                    "sample_rate": RATE, "duration_frames": FRAMES}},
        {"protocol_version": 1, "id": "after", "method": "session.inspect", "params": {}},
    ]
    wire = "".join(json.dumps(request, allow_nan=False) + "\n" for request in requests)
    try:
        process = subprocess.run([str(engine), "serve"], input=wire, text=True,
                                 capture_output=True, cwd=ROOT, timeout=45, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("DAW Csound renders timed out") from error
    if process.returncode:
        raise RuntimeError(f"DAW controller failed: {process.stderr.strip()}")
    lines = process.stdout.splitlines()
    if len(lines) != len(requests):
        raise RuntimeError(f"Expected {len(requests)} JSONL responses, got {len(lines)}: {process.stderr.strip()}")
    responses = [json.loads(line) for line in lines]
    for request, response in zip(requests, responses):
        if response.get("id") != request["id"] or not response.get("ok"):
            raise RuntimeError(f"{request['method']} failed: {response}")

    before, rendered_a, rendered_b, after = [response["result"] for response in responses]
    if before != after:
        raise RuntimeError("Csound rendering changed the active session or revision")
    for result, path in ((rendered_a, wav_a), (rendered_b, wav_b)):
        if result.get("frames") != FRAMES or result.get("sample_rate") != RATE or result.get("channels") != 2:
            raise RuntimeError(f"Unexpected render response: {result}")
        if not path.is_file():
            raise RuntimeError(f"Csound did not create {path}")

    audio_a = read_wav(wav_a)
    audio_b = read_wav(wav_b)
    if abs(audio_a["frequency"] - 440) > 2 or abs(audio_b["frequency"] - 660) > 2:
        raise RuntimeError(f"Unexpected sine frequencies: {audio_a['frequency']:.2f}, {audio_b['frequency']:.2f} Hz")
    ratio = audio_b["rms"] / audio_a["rms"]
    if not 0.43 <= ratio <= 0.57:
        raise RuntimeError(f"Expected the second fixture's half gain (RMS ratio near 0.5), got {ratio:.3f}")
    print(f"Fixture A: {wav_a} ({audio_a['frequency']:.2f} Hz, peak {audio_a['peak']})")
    print(f"Fixture B: {wav_b} ({audio_b['frequency']:.2f} Hz, peak {audio_b['peak']}, RMS ratio {ratio:.3f})")
    print(f"Session/revision preserved: {before['revision']}")


if __name__ == "__main__":
    main()

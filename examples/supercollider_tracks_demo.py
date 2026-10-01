#!/usr/bin/env python3
"""Save, reload, render, and optionally silently audition prepared SC tracks."""

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.server import Engine  # noqa: E402
from native.supercollider.score import SYNTH_NAME, sine_synthdef  # noqa: E402
from native.vst3.scan import ROOT as PROJECT_ROOT  # noqa: E402


def make_track(track_id, frequency, synth_gain, device_gain, effect_gain, final_gain):
    definition = sine_synthdef(frequency, synth_gain)
    return {
        "id": track_id,
        "mode": "continuous",
        "clips": [],
        "effects": [
            {"kind": "gain", "id": f"{track_id}-shape", "gain": effect_gain, "bypass": False},
            {"kind": "gain", "id": f"{track_id}-level", "gain": final_gain, "bypass": False},
        ],
        "device": {
            "kind": "supercollider",
            "synthdef_hex": definition.hex(),
            "synth_name": SYNTH_NAME,
            "duration_frames": 48000,
            "gain": device_gain,
            "controls": [
                {"name": "freq", "values": [frequency],
                 "points": [{"frame": 24000, "values": [frequency * 1.5]}]},
                {"name": "gain", "values": [synth_gain], "points": []},
                {"name": "out", "values": [0.0], "points": []},
            ],
        },
    }


def request(engine, method, params=None):
    try:
        return engine.call(method, params)
    except Exception as error:
        raise RuntimeError(f"{method} failed: {error}") from error


def assert_render(path, result):
    if result.get("frames") != 48000 or result.get("sample_rate") != 48000 or result.get("channels") != 2:
        raise RuntimeError(f"Unexpected rendered WAV metadata: {result}")
    with wave.open(str(path), "rb") as wav:
        if (wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (48000, 48000, 2, 2):
            raise RuntimeError(f"Unexpected WAV header in {path}")
        pcm = wav.readframes(wav.getnframes())
    samples = struct.unpack("<{}h".format(len(pcm) // 2), pcm)
    peak = max(map(abs, samples), default=0)
    rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
    if peak < 32 or rms < 8:
        raise RuntimeError(f"Rendered samples are unexpectedly quiet (peak={peak}, rms={rms:.1f})")
    return peak, rms


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true", help="also run a silent, bounded native monitor check")
    args = parser.parse_args()

    scsynth = os.environ.get("DAW_SCSYNTH")
    if not scsynth or not Path(scsynth).is_absolute() or not Path(scsynth).is_file():
        parser.error("set DAW_SCSYNTH to the absolute path of an installed scsynth executable")
    if PROJECT_ROOT != ROOT:
        raise RuntimeError("example import roots disagree")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(__import__("tempfile").mkdtemp(prefix="supercollider-tracks-", dir=output))
    session_path = project / "session.json"
    wav_path = project / "mix.wav"
    session = {
        "schema_version": 6,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [
            make_track("low", 220.0, 0.12, 0.5, 1.5, 0.6),
            make_track("high", 330.0, 0.08, 0.5, 1.25, 0.7),
        ],
    }

    engine = Engine(ROOT / "target/debug/daw")
    native_status = None
    try:
        capabilities = request(engine, "capabilities")
        if not capabilities.get("supercollider_nrt", {}).get("configured"):
            raise RuntimeError("DAW_SCSYNTH was not visible to the DAW engine")
        if args.native and not capabilities.get("live_audio"):
            raise RuntimeError("--native requires a macOS native-audio build")

        request(engine, "session.replace", {"session": session})
        saved = request(engine, "session.get")
        if saved.get("schema_version") != 6 or len(saved.get("tracks", [])) != 2:
            raise RuntimeError(f"session.get did not return the two-track schema-v6 project: {saved}")
        request(engine, "session.save", {"path": str(session_path)})
        request(engine, "session.load", {"path": str(session_path)})
        restored = request(engine, "session.get")
        with session_path.open(encoding="utf-8") as saved_file:
            persisted = json.load(saved_file)
        if restored != saved or persisted != restored:
            raise RuntimeError("Saved/reloaded session differs from the prepared project")
        for wanted, actual in zip(session["tracks"], restored["tracks"]):
            if actual["device"] != wanted["device"]:
                raise RuntimeError(f"Saved control values were not preserved for track {wanted['id']}")

        rendered = request(engine, "render", {"path": str(wav_path), "seconds": 1})
        peak, rms = assert_render(wav_path, rendered)
        if args.native:
            request(engine, "transport.play", {"seconds": 2, "volume": 0})
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                native_status = request(engine, "transport.status")
                if native_status.get("state") == "playing" and native_status.get("submitted_frames", 0) > 0:
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"Native transport did not submit audio frames: {native_status}")
            stopped = request(engine, "transport.stop")
            if stopped.get("state") != "stopped":
                raise RuntimeError(f"Native transport did not stop cleanly: {stopped}")
    finally:
        try:
            status = request(engine, "transport.status")
            if status.get("state") in ("playing", "paused"):
                request(engine, "transport.stop")
        finally:
            engine.close()

    print(f"Project: {project}")
    print(f"Session: {session_path}")
    print(f"WAV: {wav_path} ({rendered['frames']} frames, peak={peak}, rms={rms:.1f})")
    if native_status is not None:
        print("Native monitor: silent playback submitted frames; stopped successfully")


if __name__ == "__main__":
    main()

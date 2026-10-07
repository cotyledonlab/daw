#!/usr/bin/env python3
"""Save, reload, render, and optionally silently audition prepared Pd tracks."""

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui.server import Engine  # noqa: E402


def make_track(track_id: str, frequency: float, source_gain: float):
    return {
        "id": track_id,
        "mode": "continuous",
        "clips": [],
        "effects": [
            {"kind": "gain", "id": f"{track_id}-trim", "gain": 0.5, "bypass": False},
        ],
        "device": {
            "kind": "puredata",
            "program": (ROOT / "native/puredata/block_fixture.pd").read_text(encoding="utf-8"),
            "abstractions": [{
                "name": "daw-offset",
                "program": (ROOT / "native/puredata/daw-offset.pd").read_text(encoding="utf-8"),
            }],
            "duration_frames": 48000,
            "gain": source_gain,
            "controls": [
                {"name": "$0-frequency", "value": frequency,
                 "points": [{"frame": 24576, "value": 660.0}]},
                {"name": "$0-amplitude", "value": 0.1, "points": []},
            ],
        },
    }


def request(engine, method, params=None):
    try:
        return engine.call(method, params)
    except Exception as error:
        raise RuntimeError(f"{method} failed: {error}") from error


def check_render(path: Path, result: dict):
    expected = (48000, 48000, 2, 2)
    if (result.get("frames"), result.get("sample_rate"), result.get("channels")) != expected[:3]:
        raise RuntimeError(f"Unexpected rendered WAV metadata: {result}")
    with wave.open(str(path), "rb") as wav:
        layout = (wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if layout != expected:
            raise RuntimeError(f"Unexpected WAV header in {path}: {layout}")
        pcm = struct.unpack("<96000h", wav.readframes(48000))
    left, right = pcm[::2], pcm[1::2]
    peak = max(map(abs, pcm), default=0)
    if left != right or not 810 <= peak <= 830:
        raise RuntimeError(f"Source/effect gain or stereo routing mismatch (peak={peak})")

    measured = []
    for start, expected_hz in ((1000, 440.0), (28000, 660.0)):
        samples = left[start:start + 12000]
        crossings = [i for i in range(1, len(samples)) if samples[i - 1] <= 0 < samples[i]]
        if len(crossings) < 2:
            raise RuntimeError("Rendered Pure Data source has too few zero crossings")
        frequency = (len(crossings) - 1) * 48000 / (crossings[-1] - crossings[0])
        if abs(frequency - expected_hz) > 2:
            raise RuntimeError(f"Saved Pure Data control event did not reach audio: {frequency:.3f} Hz")
        measured.append(frequency)
    return peak, measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true",
                        help="also run a silent, bounded native monitor check")
    args = parser.parse_args()

    library = os.environ.get("DAW_LIBPD_LIBRARY")
    if not library or not Path(library).is_absolute() or not Path(library).is_file():
        parser.error("set DAW_LIBPD_LIBRARY to an absolute libpd library path")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="puredata-tracks-", dir=output))
    session_path = project / "session.json"
    wav_path = project / "tracks.wav"
    session = {
        "schema_version": 8,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [
            make_track("tone", 440.0, 0.5),
            # This second instance is prepared and validated, but contributes silence.
            make_track("second", 330.0, 0.0),
        ],
    }

    engine = Engine(ROOT / "target/debug/daw")
    native_status = None
    try:
        capabilities = request(engine, "capabilities")
        if not capabilities.get("puredata_sources", {}).get("configured"):
            raise RuntimeError("DAW_LIBPD_LIBRARY was not visible to the DAW engine")
        if args.native and not capabilities.get("live_audio"):
            raise RuntimeError("--native requires a macOS native-audio build")

        request(engine, "session.replace", {"session": session})
        saved = request(engine, "session.get")
        if saved.get("schema_version") != 8 or len(saved.get("tracks", [])) != 2:
            raise RuntimeError(f"session.get did not return the two-track schema-v8 project: {saved}")
        request(engine, "session.save", {"path": str(session_path)})
        request(engine, "session.load", {"path": str(session_path)})
        restored = request(engine, "session.get")
        with session_path.open(encoding="utf-8") as session_file:
            persisted = json.load(session_file)
        if restored != saved or persisted != restored:
            raise RuntimeError("Saved/reloaded session differs from the prepared project")

        before = request(engine, "session.inspect")
        rendered = request(engine, "render", {"path": str(wav_path), "seconds": 1.0})
        if request(engine, "session.inspect") != before:
            raise RuntimeError("Render changed saved state or revision")
        peak, frequencies = check_render(wav_path, rendered)

        if args.native:
            request(engine, "transport.play", {"seconds": 2.0, "volume": 0.0})
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                native_status = request(engine, "transport.status")
                if (native_status.get("state") == "playing"
                        and native_status.get("submitted_frames", 0) >= 2048):
                    break
                if native_status.get("error"):
                    raise RuntimeError(f"Native transport failed: {native_status['error']}")
                time.sleep(0.02)
            else:
                raise RuntimeError(f"Native transport did not submit audio frames: {native_status}")
            stopped = request(engine, "transport.stop")
            if stopped.get("state") != "stopped" or not stopped.get("resources_released"):
                raise RuntimeError(f"Native transport resources were not released: {stopped}")
            if request(engine, "session.inspect") != before:
                raise RuntimeError("Native transport changed saved state or revision")
    finally:
        try:
            status = request(engine, "transport.status")
            if status.get("state") in ("playing", "paused"):
                request(engine, "transport.stop")
        finally:
            engine.close()

    print(f"Project: {project}")
    print(f"Session: {session_path}")
    print(f"WAV: {wav_path} ({rendered['frames']} frames, frequencies "
          f"{frequencies[0]:.3f}/{frequencies[1]:.3f} Hz, peak={peak})")
    if native_status is not None:
        print(f"Muted native callbacks: {native_status['submitted_frames']} frames")


if __name__ == "__main__":
    main()

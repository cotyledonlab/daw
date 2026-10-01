#!/usr/bin/env python3
"""Save/reload/render embedded Csound tracks; --native checks muted callbacks."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui.server import Engine


def track(name, frequency=440.0, gain=0.5):
    return {"id": name, "mode": "continuous", "clips": [],
            "effects": [{"kind": "gain", "id": "trim", "gain": .5, "bypass": False}],
            "device": {"kind": "csound", "program": (ROOT / "native/csound/source_fixture.csd").read_text(),
                       "duration_frames": 48000, "gain": gain,
                       "controls": [{"name": "frequency", "value": frequency,
                                     "points": [{"frame": 24576, "value": 660.0}]},
                                    {"name": "amplitude", "value": .1, "points": []}]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    library = os.environ.get("DAW_CSOUND_LIBRARY")
    if not library or not Path(library).is_absolute() or not Path(library).is_file():
        raise RuntimeError("Set DAW_CSOUND_LIBRARY to an absolute Csound 7 double-sample library")
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="csound-tracks-", dir=output))
    saved, wav = directory / "session.json", directory / "tracks.wav"
    engine = Engine(ROOT / "target/debug/daw")
    try:
        session = {"schema_version": 7, "sample_rate": 48000, "tempo_milli_bpm": 120000,
                   "tracks": [track("tone"), track("second", 330.0, 0.0)]}
        engine.call("session.replace", {"session": session, "expected_revision": "0"})
        engine.call("session.save", {"path": str(saved)})
        engine.call("session.load", {"path": str(saved)})
        if engine.call("session.get") != session or json.loads(saved.read_text()) != session:
            raise RuntimeError("embedded program/control save/load mismatch")
        before = engine.call("session.inspect")
        engine.call("render", {"path": str(wav), "seconds": 1.0})
        if engine.call("session.inspect") != before:
            raise RuntimeError("render changed saved state/revision")
        with wave.open(str(wav), "rb") as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getnframes()) != (48000, 2, 2, 48000):
                raise RuntimeError("unexpected rendered WAV layout")
            pcm = struct.unpack("<96000h", stream.readframes(48000))
        if pcm[::2] != pcm[1::2] or not 810 <= max(abs(v) for v in pcm) <= 830:
            raise RuntimeError("source/effect gain or stereo routing mismatch")
        frequencies = []
        for start, expected in ((1000, 440), (28000, 660)):
            values = pcm[::2][start:start+12000]
            crossings = [i for i in range(1, len(values)) if values[i-1] <= 0 < values[i]]
            frequency = (len(crossings)-1)*48000/(crossings[-1]-crossings[0])
            if abs(frequency-expected) > 2:
                raise RuntimeError("saved Csound event did not reach audio")
            frequencies.append(frequency)
        print(f"Session: {saved}")
        print(f"WAV: {wav}; frequencies {frequencies[0]:.3f}/{frequencies[1]:.3f} Hz; peak {max(abs(v) for v in pcm)}")
        if args.native:
            started = False
            try:
                engine.call("transport.play", {"seconds": 1.0, "volume": 0.0})
                started = True
                deadline = time.monotonic()+3
                observed = None
                while time.monotonic() < deadline:
                    status = engine.call("transport.status")
                    if status.get("state") == "playing" and status.get("submitted_frames", 0) >= 2048:
                        observed = status
                        break
                    if status.get("error"):
                        raise RuntimeError(str(status["error"]))
                    time.sleep(.02)
                if observed is None:
                    raise RuntimeError("no advancing native callback evidence")
                print(f"Muted native callbacks: {observed['submitted_frames']} frames, mode {observed.get('source_mode')}")
            finally:
                stopped = engine.call("transport.stop")
                if started and (stopped.get("state") != "stopped" or not stopped.get("resources_released")):
                    raise RuntimeError("native playback resources not released")
            if engine.call("session.inspect") != before:
                raise RuntimeError("native transport changed saved state")
    finally:
        engine.close()


if __name__ == "__main__":
    main()

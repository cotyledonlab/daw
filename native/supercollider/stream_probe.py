#!/usr/bin/env python3
"""Finite shared-queue live SC diagnostic; no DAW transport or audible routing."""
from pathlib import Path
import argparse
import json
import os
import secrets
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.supercollider.live_probe import OwnedServer, measure, CAPTURE_FRAMES, capture_synthdef
from native.supercollider.score import _pstring, _ugen, sine_synthdef, SYNTH_NAME


def stream_synthdef():
    # Source writes private buses 16/17; this tail synth publishes both inputs.
    ugens = [_ugen("In", 2, [(-1, 0)], [2, 2]),
             _ugen("DawStream", 2, [(0, 0), (0, 1)], [2])]
    body = _pstring("daw_stream_capture") + struct.pack(">ifiii", 1, 16.0, 0, 0, 2)
    return b"SCgf" + struct.pack(">ih", 2, 1) + body + b"".join(ugens) + struct.pack(">h", 0)


def probe(executable, reader, plugins):
    reader, plugins = Path(reader).resolve(), Path(plugins).resolve()
    with tempfile.TemporaryDirectory(prefix="daw-sc-stream-") as directory:
        queue, pcm = Path(directory) / "queue", Path(directory) / "capture.f32"
        nonce = secrets.randbits(64) or 1
        subprocess.run([str(reader), "create", str(queue), str(nonce)], check=True, timeout=2,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = None
        try:
            with OwnedServer(executable, stream_plugin=plugins, stream_path=queue, stream_nonce=nonce) as server:
                server.done("/notify", 1)
                server.node("/g_new", 1, 0, 0)
                server.done("/d_recv", sine_synthdef())
                server.done("/d_recv", stream_synthdef())
                server.done("/d_recv", capture_synthdef(0, "daw_output_capture"))
                server.done("/b_alloc", 0, CAPTURE_FRAMES, 2)
                child = subprocess.Popen([str(reader), "drain", str(queue), str(nonce), "512", str(pcm)],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                server.send("/s_new", SYNTH_NAME, 1000, 0, 1, "out", 16.0)
                server.wait(lambda address, values: address == "/n_go" and values[0] == 1000)
                server.send("/s_new", "daw_stream_capture", 1001, 1, 1)
                server.wait(lambda address, values: address == "/n_go" and values[0] == 1001)
                time.sleep(0.3)
                server.send("/n_set", 1000, "freq", 660.0, "gain", 0.05)
                server.send("/s_get", 1000, "freq", "gain")
                controls = server.wait(lambda address, values: address == "/n_set" and values[0] == 1000)
                if controls[1:] != ["freq", 660.0, "gain", struct.unpack(">f", struct.pack(">f", 0.05))[0]]:
                    raise RuntimeError("stream control readback mismatch")
                stdout, stderr = child.communicate(timeout=5)
                server.node("/n_free", 1001)
                if child.returncode or len(stdout) > 4096 or len(stderr) > 4096:
                    raise RuntimeError(f"stream reader failed: {stderr[:1024]!r}")
                reader_report = json.loads(stdout)
                output_bus = server.capture(1002, "daw_output_capture")
                if any(output_bus):
                    raise RuntimeError("stream diagnostic output buses are not silent")
                server.node("/n_free", 1000)
                server.node("/n_free", 1)
                server.done("/b_free", 0)
                server.done("/quit")
                if server.process.wait(timeout=2):
                    raise RuntimeError("stream server failed to quit")
            raw = pcm.read_bytes()
            if len(raw) != 512 * 64 * 2 * 4:
                raise RuntimeError("stream frame count mismatch")
            samples = struct.unpack("=" + str(len(raw)//4) + "f", raw)
            before = measure(samples[:CAPTURE_FRAMES * 2])
            after = measure(samples[-CAPTURE_FRAMES * 2:])
            if before["frequency_hz"] != 440 or after["frequency_hz"] != 660:
                raise RuntimeError("live edit did not reach shared PCM")
            if not 0.4 < after["rms"] / before["rms"] < 0.6:
                raise RuntimeError("live gain edit did not reach shared PCM")
            return {"schema_version": 1, "reader": reader_report, "before": before, "after": after,
                    "hardware_bus_peak": 0.0, "daw_transport": False, "acoustic_verified": False}
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default=os.environ.get("DAW_SCSYNTH"), required=False)
    parser.add_argument("--reader", default=ROOT / "output/sc-stream/reader")
    parser.add_argument("--plugins", default=ROOT / "output/sc-stream/plugins")
    args = parser.parse_args()
    if not args.executable:
        parser.error("set DAW_SCSYNTH or pass --executable")
    print(json.dumps(probe(args.executable, args.reader, args.plugins), allow_nan=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""SC private-bus stream -> Rust gain effect -> native callback, muted by default."""
from pathlib import Path
import argparse
import json
import os
import secrets
import select
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.supercollider.live_probe import OwnedServer, CAPTURE_FRAMES, capture_synthdef
from native.supercollider.score import sine_synthdef, SYNTH_NAME
from native.supercollider.stream_probe import stream_synthdef


def native_probe(executable, blocks=1500, volume=0.0, crash=False):
    if not 750 <= blocks <= 7500:
        raise ValueError("control-change harness requires 750..7500 blocks (1..10 seconds)")
    reader = ROOT / "output/sc-stream/reader"
    plugins = ROOT / "output/sc-stream/plugins"
    child = None
    with tempfile.TemporaryDirectory(prefix="daw-sc-native-") as directory:
        queue = Path(directory) / "queue"
        nonce = secrets.randbits(64) or 1
        subprocess.run([str(reader), "create", str(queue), str(nonce)], check=True, timeout=2,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with OwnedServer(executable, seconds=15, stream_plugin=plugins, stream_path=queue, stream_nonce=nonce) as server:
                server.done("/notify", 1)
                server.node("/g_new", 1, 0, 0)
                server.done("/d_recv", sine_synthdef())
                server.done("/d_recv", stream_synthdef())
                server.done("/d_recv", capture_synthdef(0, "daw_output_capture"))
                server.done("/b_alloc", 0, CAPTURE_FRAMES, 2)
                child = subprocess.Popen([str(ROOT / "target/debug/daw"), "sc-stream-play", str(queue),
                                          str(nonce), str(blocks), str(volume), "0.5"],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logs = bytearray()
                deadline = time.monotonic() + 4
                while b"DAW_SC_STREAM_READY" not in logs:
                    if child.poll() is not None or time.monotonic() >= deadline or len(logs) > 65536:
                        raise RuntimeError(f"native SC startup failed: {logs[-1024:]!r}")
                    if select.select([child.stderr], [], [], 0.05)[0]:
                        logs.extend(os.read(child.stderr.fileno(), 4096))
                server.send("/s_new", SYNTH_NAME, 1000, 0, 1, "out", 16.0)
                server.wait(lambda address, values: address == "/n_go" and values[0] == 1000)
                server.send("/s_new", "daw_stream_capture", 1001, 1, 1)
                server.wait(lambda address, values: address == "/n_go" and values[0] == 1001)
                time.sleep(0.4)
                server.send("/n_set", 1000, "freq", 660.0, "gain", 0.05)
                server.send("/s_get", 1000, "freq", "gain")
                controls = server.wait(lambda address, values: address == "/n_set" and values[0] == 1000)
                if controls[1:] != ["freq", 660.0, "gain", struct.unpack(">f", struct.pack(">f", 0.05))[0]]:
                    raise RuntimeError("native stream control readback mismatch")
                if crash:
                    server.process.kill()
                    server.process.wait(timeout=2)
                stdout, stderr = child.communicate(timeout=blocks * 64 / 48000 + 5)
                if len(stdout) > 65536 or len(stderr) + len(logs) > 65536:
                    raise RuntimeError("native stream diagnostic limit exceeded")
                if crash:
                    if child.returncode == 0:
                        raise RuntimeError("native playback accepted an incomplete crashed producer")
                    if struct.unpack_from("=I", queue.read_bytes(), 132)[0] != 0:
                        raise RuntimeError("native consumer lease was not released after producer death")
                    return {"producer_crash_rejected": True, "consumer_released": True,
                            "native_exit_code": child.returncode, "error": stderr.decode(errors="replace").strip()}
                server.node("/n_free", 1001)
                if child.returncode:
                    raise RuntimeError(f"native SC playback failed: {stderr[:1024]!r}")
                report = json.loads(stdout)
                if report["submitted_frames"] != blocks * 64 or report["source"]["source_frames"] != blocks * 64:
                    raise RuntimeError("source/native frame counts differ")
                if report["callback_source_digest"] != report["source"]["source_digest"]:
                    raise RuntimeError("source/callback sample-order digest mismatch")
                if not 0.049 < report["callback_signal_peak"] < 0.051 or not 0.049 < report["source"]["post_effect_peak"] < 0.051:
                    raise RuntimeError("SC source gain chain did not reach native callback")
                output = server.capture(1002, "daw_output_capture")
                if any(output):
                    raise RuntimeError("SC hardware output buses were not silent")
                server.node("/n_free", 1000)
                server.node("/n_free", 1)
                server.done("/b_free", 0)
                server.done("/quit")
                if server.process.wait(timeout=2):
                    raise RuntimeError("SC server failed to quit")
                if struct.unpack_from("=I", queue.read_bytes(), 132)[0] != 0:
                    raise RuntimeError("native consumer lease was not released")
                return {"native": report, "sc_hardware_bus_peak": 0.0,
                        "sc_exit_code": 0, "session_transport": False}
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=2)
                for stream in (child.stdout, child.stderr):
                    stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default=os.environ.get("DAW_SCSYNTH"))
    parser.add_argument("--blocks", type=int, default=1500)
    parser.add_argument("--volume", type=float, default=0.0)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    if not args.executable:
        parser.error("set DAW_SCSYNTH or pass --executable")
    print(json.dumps(native_probe(args.executable, args.blocks, args.volume, args.crash), allow_nan=False))


if __name__ == "__main__":
    main()

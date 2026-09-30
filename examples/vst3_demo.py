#!/usr/bin/env python3
"""Render a sine track through one offline VST3 effect using the Rust controller."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[1]


def read_pcm(path):
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 2 or audio.getframerate() != 48000 or audio.getsampwidth() != 2:
            raise RuntimeError(f"Unexpected WAV format in {path}")
        return audio.readframes(audio.getnframes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_path", type=Path, help="path to the VST3 bundle")
    parser.add_argument("class_id", help="32-character uppercase hexadecimal VST3 class ID")
    parser.add_argument("--parameter", type=int, default=48,
                        help="automatable parameter ID (default: ValhallaFreqEcho wetDry ID 48)")
    args = parser.parse_args()

    bundle_path = args.bundle_path.expanduser().resolve()
    if bundle_path.suffix != ".vst3" or not bundle_path.is_dir():
        parser.error("bundle_path must name an existing .vst3 bundle directory")
    if not re.fullmatch(r"[0-9A-F]{32}", args.class_id):
        parser.error("class_id must be exactly 32 uppercase hexadecimal characters")
    if not 0 <= args.parameter <= 0xFFFFFFFF:
        parser.error("--parameter must be an unsigned 32-bit integer")

    output_root = ROOT / "output"
    output_root.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="vst3-demo-", dir=output_root))
    session_path = project / "session.json"
    first_wav = project / "before-load.wav"
    second_wav = project / "after-load.wav"
    session = {
        "schema_version": 4,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [{
            "id": "sine",
            "mode": "continuous",
            "clips": [],
            "device": {"kind": "sine", "frequency_hz": 440.0, "gain": 0.1},
            "effects": [{
                "kind": "vst3",
                "id": "echo",
                "bypass": False,
                "bundle_path": str(bundle_path),
                "class_id": args.class_id,
                "state_hex": "",
                "controller_state_hex": "",
                "parameters": [{
                    "id": args.parameter,
                    "value": 0.2,
                    "points": [{"frame": 24000, "value": 0.8}],
                }],
            }],
        }],
    }

    deadline = time.monotonic() + 90

    def run_commands(commands, start_index):
        wire = "".join(json.dumps({
            "protocol_version": 1, "id": f"vst3-demo-{start_index + index}",
            "method": method, "params": params,
        }) + "\n" for index, (method, params) in enumerate(commands))
        result = subprocess.run(
            [str(ROOT / "target/debug/daw"), "serve"], input=wire, text=True,
            capture_output=True, cwd=ROOT, timeout=max(0.1, deadline - time.monotonic()),
        )
        if result.returncode != 0:
            raise RuntimeError(f"DAW controller exited {result.returncode}: {result.stderr.strip()}")
        lines = result.stdout.splitlines()
        if len(lines) != len(commands):
            raise RuntimeError(f"Expected {len(commands)} responses, received {len(lines)}: {result.stderr.strip()}")
        results = []
        for index, ((method, _), line) in enumerate(zip(commands, lines)):
            response = json.loads(line)
            expected_id = f"vst3-demo-{start_index + index}"
            if response.get("id") != expected_id or not response.get("ok"):
                raise RuntimeError(f"{method} failed or returned a mismatched ID: {response}")
            results.append(response["result"])
        return results

    capabilities, = run_commands([("capabilities", {})], 0)
    if not capabilities.get("offline_vst3", {}).get("implemented"):
        raise RuntimeError("This daw binary does not implement offline VST3; build with vst3-offline")

    commands = [
        ("session.replace", {"session": session}),
        ("session.save", {"path": str(session_path)}),
        ("render", {"path": str(first_wav), "seconds": 1}),
        ("session.load", {"path": str(session_path)}),
        ("render", {"path": str(second_wav), "seconds": 1}),
    ]
    responses = run_commands(commands, 1)
    replaced = responses[0]
    loaded = responses[3]
    if replaced != loaded:
        raise RuntimeError("The loaded session differs from the captured, saved plugin session")
    first_pcm = read_pcm(first_wav)
    second_pcm = read_pcm(second_wav)
    print(f"Session: {session_path}")
    print(f"First WAV: {first_wav}")
    print(f"Reloaded WAV: {second_wav}")
    print(f"PCM identical after reload: {first_pcm == second_pcm} (informational; third-party DSP may vary)")


if __name__ == "__main__":
    main()

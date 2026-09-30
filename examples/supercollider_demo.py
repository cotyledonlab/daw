#!/usr/bin/env python3
"""Render the native SuperCollider sine SynthDef through the DAW JSONL API."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# noqa: E402 -- add the checkout root before importing its example module.
from native.supercollider.score import write_score


def main():
    executable = os.environ.get("DAW_SCSYNTH")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise RuntimeError("Set DAW_SCSYNTH to the absolute path of an installed scsynth executable")

    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="supercollider-demo-", dir=output))
    score_path = project / "sine.osc"
    wav_path = project / "sine.wav"
    write_score(score_path, frequency=440.0, gain=0.1, duration=1.0)

    requests = [
        {"protocol_version": 1, "id": "supercollider-capabilities", "method": "capabilities", "params": {}},
        {"protocol_version": 1, "id": "supercollider-render", "method": "supercollider.render",
         "params": {"score_path": str(score_path.resolve()), "path": str(wav_path.resolve()),
                    "sample_rate": 48000}},
    ]
    wire = "".join(json.dumps(request, allow_nan=False) + "\n" for request in requests)
    try:
        process = subprocess.run([str(ROOT / "target/debug/daw"), "serve"], input=wire,
                                 text=True, capture_output=True, cwd=ROOT, timeout=25,
                                 check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("DAW SuperCollider render timed out") from error
    if process.returncode:
        raise RuntimeError(f"DAW controller failed: {process.stderr.strip()}")
    lines = process.stdout.splitlines()
    if len(lines) != len(requests):
        raise RuntimeError(f"Expected two responses, received {len(lines)}: {process.stderr.strip()}")
    responses = [json.loads(line) for line in lines]
    for request, response in zip(requests, responses):
        if response.get("id") != request["id"] or not response.get("ok"):
            raise RuntimeError(f"{request['method']} failed: {response}")
    capabilities = responses[0]["result"]
    support = capabilities.get("supercollider_nrt", {})
    if not support.get("implemented"):
        raise RuntimeError("This daw binary does not implement SuperCollider NRT rendering")
    if not support.get("configured"):
        raise RuntimeError("DAW_SCSYNTH is not configured to an existing absolute executable")
    result = responses[1]["result"]

    if result.get("sample_rate") != 48000 or result.get("channels") != 2:
        raise RuntimeError(f"Unexpected rendered WAV metadata: {result}")
    print(f"Score: {score_path}")
    print(f"WAV: {wav_path} ({result['frames']} frames, {result['sample_rate']} Hz, {result['channels']} channels)")


if __name__ == "__main__":
    main()

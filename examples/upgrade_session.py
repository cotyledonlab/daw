#!/usr/bin/env python3
"""Explicitly upgrade a validated v1 session to continuous v2 tracks; never overwrite."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def validate(session):
    wire = json.dumps({"protocol_version": 1, "id": "validate", "method": "session.replace",
                       "params": {"session": session}}, allow_nan=False)
    process = subprocess.run([str(ROOT / "target/debug/daw"), "serve"],
                             input=wire + "\n", text=True, capture_output=True,
                             check=True, timeout=10)
    response = json.loads(process.stdout)
    if not response["ok"]:
        raise ValueError(response["error"]["message"])
    return response["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.input.open("rb") as source:
        raw = source.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("Session exceeds 1 MiB")
    session = validate(json.loads(raw))
    if session["schema_version"] != 1:
        raise ValueError("Upgrade input must use schema version 1")
    session["schema_version"] = 2
    session["tempo_milli_bpm"] = 120000
    for track in session["tracks"]:
        track.update(mode="continuous", clips=[])
    upgraded = validate(session)
    encoded = json.dumps(upgraded, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise ValueError("Upgraded session exceeds 1 MiB")
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(encoded)
    print(args.output)


if __name__ == "__main__":
    main()

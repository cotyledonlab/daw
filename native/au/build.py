#!/usr/bin/env python3
"""Build the small macOS Audio Unit inspection host."""
from __future__ import annotations

import pathlib
import platform
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = ROOT / "native/au/host.cpp"
OUTPUT = ROOT / "output/au-spike/au-host"


def main() -> int:
    if platform.system() != "Darwin":
        print("Audio Unit host builds require macOS", file=sys.stderr)
        return 1
    if not SOURCE.is_file():
        print(f"missing host source: {SOURCE}", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["xcrun", "clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(SOURCE), "-framework", "AudioToolbox", "-framework",
                        "CoreFoundation", "-o", str(OUTPUT)], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Audio Unit host build failed: {error}", file=sys.stderr)
        return 1
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the macOS streaming diagnostic against an explicit SC 3.14.1 source tree."""
from pathlib import Path
import argparse
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    if platform.system() != "Darwin":
        parser.error("this diagnostic is macOS-only")
    if not (sdk / "include/plugin_interface/SC_PlugIn.h").is_file():
        parser.error("SDK must contain the official pinned 3.14.1 include tree")
    output = ROOT / "output/sc-stream"
    (output / "plugins").mkdir(parents=True, exist_ok=True)
    common = ["xcrun", "clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror"]
    subprocess.run(common + [str(ROOT / "native/supercollider/stream_reader.cpp"),
                             "-o", str(output / "reader")], check=True)
    includes = ["-I" + str(sdk / path) for path in ("include/plugin_interface", "include/common", "common")]
    subprocess.run(common + ["-Wno-unused-parameter", "-bundle", "-undefined", "dynamic_lookup",
                             *includes, str(ROOT / "native/supercollider/stream_ugen.cpp"),
                             "-o", str(output / "plugins/DawStream.scx")], check=True)
    print(output)


if __name__ == "__main__":
    main()

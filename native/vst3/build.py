#!/usr/bin/env python3
"""Build the macOS offline VST3 host probe and its project-owned fixture."""
import argparse
import pathlib
import plistlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SDK_COMMIT = "31d6eeba6daaa3e2a8bfbe3e7a90ca0b7fbfbc1c"
SDK_URL = "https://github.com/steinbergmedia/vst3_pluginterfaces.git"
SDK = ROOT / "output/vst3-sdk"
BUILD = ROOT / "output/vst3-spike"


def run(args, timeout=45):
    subprocess.run([str(arg) for arg in args], check=True, timeout=timeout, cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-sdk", action="store_true",
                        help="fetch pinned official headers into ignored output/")
    parser.add_argument("--sanitize", action="store_true", help="enable address/undefined sanitizers")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("this spike currently supports macOS only")
    interfaces = SDK / "pluginterfaces"
    if not interfaces.exists():
        if not args.fetch_sdk:
            parser.error("SDK missing; rerun with --fetch-sdk")
        interfaces.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", "v3.8.0_build_66",
             SDK_URL, interfaces])
    actual = subprocess.check_output(["git", "-C", str(interfaces), "rev-parse", "HEAD"],
                                     text=True, timeout=10).strip()
    if actual != SDK_COMMIT:
        parser.error(f"SDK revision mismatch: expected {SDK_COMMIT}, got {actual}")
    dirty = subprocess.check_output(["git", "-C", str(interfaces), "status", "--porcelain"],
                                    text=True, timeout=10)
    if dirty:
        parser.error("SDK checkout has modifications; restore the pinned checkout before building")
    BUILD.mkdir(parents=True, exist_ok=True)
    fixture = BUILD / "DawTestGain.vst3/Contents"
    (fixture / "MacOS").mkdir(parents=True, exist_ok=True)
    (fixture / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "DawTestGain", "CFBundleIdentifier": "org.cotyledonlab.DawTestGain",
        "CFBundleName": "DAW test gain", "CFBundlePackageType": "BNDL",
        "CFBundleVersion": "1.0", "CFBundleSupportedPlatforms": ["MacOSX"],
    }))
    flags = ["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O2", "-g", "-I", SDK]
    if args.sanitize:
        flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    common = [interfaces / "base/funknown.cpp", "-framework", "CoreFoundation"]
    run(flags + [ROOT / "native/vst3/host.cpp"] + common + ["-o", BUILD / "vst3-host"])
    run(flags + ["-bundle", ROOT / "native/vst3/fixture.cpp"] + common +
        ["-o", fixture / "MacOS/DawTestGain"])
    shutil.copyfile(interfaces / "LICENSE.txt", BUILD / "STEINBERG-LICENSE.txt")
    print(f"Host: {BUILD / 'vst3-host'}")
    print(f"Fixture: {fixture.parent}")
    print(f"SDK: {SDK_COMMIT}; MIT notice copied beside binaries")


if __name__ == "__main__":
    main()

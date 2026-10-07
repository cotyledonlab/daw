#!/usr/bin/env python3
"""Build the macOS Csound producer and existing fixed-queue consumer bridge."""
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[2]

def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('The shared queue ABI is verified only on macOS arm64')
    output = ROOT / 'output/csound-stream'
    output.mkdir(parents=True, exist_ok=True)
    library = output / 'libdaw-csound-queue.dylib'
    subprocess.run(['xcrun', 'clang++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                    '-dynamiclib', str(ROOT / 'native/csound/queue_producer.cpp'),
                    str(ROOT / 'native/supercollider/stream_bridge.cpp'), '-o', str(library)], check=True)
    print(library)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Owned macOS SC server proof: OSC acknowledgements and private-bus PCM capture.

This diagnostic is separate from DAW session/native transport. It emits no
hardware tone: the synth writes bus 16, and a second synth records that bus.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.supercollider.score import _pstring, _ugen, osc_message, sine_synthdef, SYNTH_NAME

MAX_LOG = 65536
MAX_PACKET = 65507
CAPTURE_FRAMES = 4096


def decode_message(packet):
    """Decode the bounded scalar reply subset, with strict framing/padding."""
    if not packet or len(packet) > MAX_PACKET or len(packet) % 4:
        raise ValueError("invalid OSC reply length")
    position = 0

    def string():
        nonlocal position
        end = packet.find(b"\0", position)
        if end < 0:
            raise ValueError("unterminated OSC string")
        value = packet[position:end].decode("utf-8")
        padded = (end + 4) & ~3
        if padded > len(packet) or any(packet[end:padded]):
            raise ValueError("invalid OSC string padding")
        position = padded
        return value

    address, tags = string(), string()
    if not address.startswith("/") or not tags.startswith(","):
        raise ValueError("invalid OSC reply address/tags")
    values = []
    for tag in tags[1:]:
        if tag == "s":
            values.append(string())
            continue
        formats = {"i": (4, ">i"), "f": (4, ">f"), "d": (8, ">d")}
        if tag not in formats:
            raise ValueError("unsupported OSC reply type")
        width, format_code = formats[tag]
        if position + width > len(packet):
            raise ValueError("truncated OSC reply")
        value = struct.unpack_from(format_code, packet, position)[0]
        position += width
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite OSC reply")
        values.append(value)
    if position != len(packet):
        raise ValueError("trailing OSC reply bytes")
    return address, values


def capture_synthdef(bus=16, name="daw_private_capture"):
    """In.ar(16,2) -> nonlooping RecordBuf.ar(buffer0), no hardware output."""
    constants = (0.0, 1.0, float(bus))
    ugens = [
        _ugen("In", 2, [(-1, 2)], [2, 2]),
        _ugen("RecordBuf", 2, [(-1, 0), (-1, 0), (-1, 1), (-1, 0),
                              (-1, 1), (-1, 0), (-1, 1), (-1, 0),
                              (0, 0), (0, 1)], [2]),
    ]
    body = bytearray(_pstring(name))
    body.extend(struct.pack(">i3fiii", len(constants), *constants, 0, 0, len(ugens)))
    body.extend(b"".join(ugens))
    body.extend(struct.pack(">h", 0))
    return b"SCgf" + struct.pack(">ih", 2, 1) + body


class OwnedServer:
    """Only the launched server is contacted or terminated; no attach mode."""
    def __init__(self, executable, seconds=10, stream_plugin=None, stream_path=None, stream_nonce=None):
        if sys.platform != "darwin":
            raise RuntimeError("this hardware/socket-ownership proof currently requires macOS")
        if not math.isfinite(seconds) or not 0 < seconds <= 15:
            raise ValueError("owned server deadline must be between zero and 15 seconds")
        executable = Path(executable)
        if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("select an existing absolute scsynth executable")
        stream_args = []
        environment = os.environ.copy()
        environment.pop("DAW_SC_STREAM_PATH", None)
        environment.pop("DAW_SC_STREAM_NONCE", None)
        if any(value is not None for value in (stream_plugin, stream_path, stream_nonce)):
            if not all(value is not None for value in (stream_plugin, stream_path, stream_nonce)):
                raise ValueError("stream diagnostic needs plugin, queue and nonce")
            plugin = Path(stream_plugin)
            queue_path = Path(stream_path)
            builtins = executable.parent / "plugins"
            if not plugin.is_absolute() or not plugin.is_dir() or not builtins.is_dir() or ":" in str(plugin):
                raise ValueError("stream diagnostic requires private and bundled plugin directories")
            if not queue_path.is_absolute() or not queue_path.is_file() or not 0 < stream_nonce < 2**64:
                raise ValueError("invalid stream queue/nonce")
            stream_args = ["-U", str(builtins) + ":" + str(plugin)]
            environment["DAW_SC_STREAM_PATH"] = str(queue_path)
            environment["DAW_SC_STREAM_NONCE"] = str(stream_nonce)
        self.temp = tempfile.TemporaryDirectory(prefix="daw-sc-live-")
        self.process = None
        self.socket = None
        self.threads = []
        self.logs = bytearray()
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.overflow = threading.Event()
        self.stop_readers = threading.Event()
        self.deadline = time.monotonic() + seconds
        self.address = None
        try:
            self.process = subprocess.Popen([
                str(executable), "-u", "0", "-B", "127.0.0.1", "-i", "0", "-o", "2",
                "-S", "48000", "-z", "64", "-a", "32", "-b", "16", "-n", "64",
                "-d", "16", "-D", "0", "-R", "0", "-P", self.temp.name, *stream_args,
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               start_new_session=True, env=environment)
            for stream in (self.process.stdout, self.process.stderr):
                thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
                thread.start()
                self.threads.append(thread)
            startup = min(self.deadline, time.monotonic() + 5)
            while not self.ready.wait(0.02):
                self.check()
                if time.monotonic() >= startup:
                    raise RuntimeError("SC server readiness timed out")
            self.check()
            # -u0 asks the server itself to reserve a free port. Query this
            # exact child PID, avoiding attach-to-existing-server/port races.
            found = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(self.process.pid),
                                    "-iUDP", "-Fn"], capture_output=True, timeout=2, check=False)
            endpoints = [line[1:] for line in found.stdout.decode().splitlines() if line.startswith("n")]
            if len(endpoints) != 1 or not endpoints[0].startswith("127.0.0.1:"):
                raise RuntimeError("cannot prove owned loopback UDP endpoint")
            port = int(endpoints[0].rsplit(":", 1)[1])
            self.address = ("127.0.0.1", port)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(("127.0.0.1", 0))
        except BaseException:
            self.close()
            raise

    def _drain(self, stream):
        while not self.stop_readers.is_set():
            if not select.select([stream.fileno()], [], [], 0.1)[0]:
                continue
            data = os.read(stream.fileno(), 4096)
            if not data:
                break
            with self.lock:
                room = MAX_LOG - len(self.logs)
                self.logs.extend(data[:room])
                if len(data) > room:
                    self.overflow.set()
                if b"server ready" in self.logs:
                    self.ready.set()

    def check(self):
        if time.monotonic() >= self.deadline:
            raise RuntimeError("owned SC probe exceeded its deadline")
        if self.overflow.is_set():
            raise RuntimeError("SC diagnostic limit exceeded")
        if self.process.poll() is not None:
            with self.lock:
                logs = bytes(self.logs).decode(errors="replace")
            raise RuntimeError(f"owned SC child exited: {logs[-1024:]}")

    def wait(self, predicate, seconds=2):
        deadline = min(self.deadline, time.monotonic() + seconds)
        while time.monotonic() < deadline:
            self.check()
            self.socket.settimeout(min(0.1, deadline-time.monotonic()))
            try:
                packet, sender = self.socket.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            if sender != self.address:
                continue
            address, values = decode_message(packet)
            if address == "/fail":
                raise RuntimeError(f"SC command failure: {values}")
            if predicate(address, values):
                return values
        raise RuntimeError("OSC acknowledgement timed out")

    def send(self, address, *values):
        self.check()
        packet = osc_message(address, *values)
        if len(packet) > MAX_PACKET:
            raise ValueError("OSC request exceeds UDP limit")
        self.socket.sendto(packet, self.address)

    def done(self, address, *values):
        self.send(address, *values)
        return self.wait(lambda reply, args: reply == "/done" and args and args[0] == address)

    def node(self, address, node_id, *values):
        self.send(address, node_id, *values)
        expected = "/n_end" if address == "/n_free" else "/n_go"
        return self.wait(lambda reply, args: reply == expected and args and args[0] == node_id)

    def capture(self, node_id, definition="daw_private_capture"):
        self.done("/b_zero", 0)
        self.send("/s_new", definition, node_id, 1, 1)
        self.wait(lambda reply, args: reply == "/n_go" and args[0] == node_id)
        time.sleep(CAPTURE_FRAMES / 48000 + 0.04)
        self.node("/n_free", node_id)
        samples = []
        for offset in range(0, CAPTURE_FRAMES*2, 256):
            count = min(256, CAPTURE_FRAMES*2-offset)
            self.send("/b_getn", 0, offset, count)
            values = self.wait(lambda reply, args: reply == "/b_setn" and args[:3] == [0, offset, count])
            if len(values) != count + 3 or any(not isinstance(v, float) for v in values[3:]):
                raise RuntimeError("invalid captured PCM reply")
            samples.extend(values[3:])
        return samples

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
            # Stop inherited pipe holders in the owned group before joining.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.stop_readers.set()
            for thread in self.threads:
                thread.join(timeout=1)
            for stream in (self.process.stdout, self.process.stderr):
                stream.close()
        self.temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def measure(samples):
    left, right = samples[::2], samples[1::2]
    if left != right or len(left) != CAPTURE_FRAMES:
        raise RuntimeError("capture is not complete, identical stereo")
    window = left[256:]
    frequencies = (400, 440, 480, 620, 660, 700)
    energy = lambda hz: abs(sum(value * complex(math.cos(2*math.pi*hz*i/48000),
                                                math.sin(2*math.pi*hz*i/48000))
                                for i, value in enumerate(window)))
    return {"frames":len(left),"peak":max(map(abs, window)),
            "rms":math.sqrt(sum(v*v for v in window)/len(window)),
            "frequency_hz":max(frequencies, key=energy)}


def probe(executable):
    with OwnedServer(executable) as server:
        server.send("/status")
        status = server.wait(lambda address, _: address == "/status.reply")
        if len(status) != 9 or status[-2] != 48000:
            raise RuntimeError(f"unexpected server sample rate/status: {status}")
        server.done("/notify", 1)
        server.node("/g_new", 1, 0, 0)
        server.done("/d_recv", sine_synthdef())
        server.done("/d_recv", capture_synthdef())
        server.done("/d_recv", capture_synthdef(0, "daw_output_capture"))
        server.done("/b_alloc", 0, CAPTURE_FRAMES, 2)
        server.send("/s_new", SYNTH_NAME, 1000, 0, 1, "out", 16.0)
        server.wait(lambda address, values: address == "/n_go" and values[0] == 1000)
        before = measure(server.capture(1001))
        server.send("/n_set", 1000, "freq", 660.0, "gain", 0.05)
        server.send("/s_get", 1000, "freq", "gain")
        readback = server.wait(lambda address, values: address == "/n_set" and values[0] == 1000)
        if readback[1:] != ["freq", 660.0, "gain", struct.unpack(">f",struct.pack(">f",0.05))[0]]:
            raise RuntimeError(f"control readback mismatch: {readback}")
        after = measure(server.capture(1002))
        if before["frequency_hz"] != 440 or after["frequency_hz"] != 660:
            raise RuntimeError(f"control change did not reach captured audio: {before}, {after}")
        if not 0.08 < before["peak"] < 0.12 or not 0.4 < after["rms"]/before["rms"] < 0.6:
            raise RuntimeError("gain change did not reach captured audio")
        output_bus = server.capture(1003, "daw_output_capture")
        if any(output_bus):
            raise RuntimeError("hardware output buses were not silent")
        server.node("/n_free", 1000)
        server.node("/n_free", 1)
        server.done("/b_free", 0)
        server.done("/quit")
        if server.process.wait(timeout=2) != 0:
            raise RuntimeError("SC server did not quit cleanly")
        return {"schema_version":1,"owned_pid":server.process.pid,"sample_rate":48000,
                "acknowledgements":["notify","definitions","buffer","node_create","control_readback","node_free","quit"],
                "before":before,"after":after,"hardware_output":"captured_silence", "hardware_bus_peak":0.0, "child_exit_code":server.process.returncode,
                "daw_transport":False,"streaming_audio":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default=os.environ.get("DAW_SCSYNTH"))
    args = parser.parse_args()
    if not args.executable:
        parser.error("set DAW_SCSYNTH or pass an absolute --executable")
    result = probe(args.executable)
    print(json.dumps(result, allow_nan=False))

if __name__ == "__main__":
    main()

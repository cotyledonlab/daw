"""Portable OSC decoder tests and opt-in owned-server probe coverage."""
from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import textwrap
import unittest

from native.supercollider.score import osc_message
from native.supercollider.live_probe import OwnedServer, decode_message, probe


LIVE = os.environ.get("DAW_TEST_SC_LIVE") == "1"
SCSYNTH = os.environ.get("DAW_SCSYNTH")


class DecodeMessageTests(unittest.TestCase):
    def test_decodes_scalar_types_from_fixture(self):
        packet = osc_message("/reply", 23, 0.25, "ready")
        self.assertEqual(decode_message(packet), ("/reply", [23, struct.unpack(">f", struct.pack(">f", 0.25))[0], "ready"]))

        # The production request writer emits f32; exercise supported f64 reply parsing too.
        packet = b"/r\0\0,id\0" + struct.pack(">id", 7, 0.125)
        self.assertEqual(decode_message(packet), ("/r", [7, 0.125]))

    def test_rejects_bad_lengths_strings_padding_and_framing(self):
        good = osc_message("/r", "x")
        cases = [
            b"",
            b"/r\0",  # not word aligned
            b"/r\0\0,t\0\0",  # unsupported type
            b"/r\0\0,s",  # missing tag terminator
            b"/r\0\0,s\0\0\0x\0\0\0",  # nonzero string padding
            good + b"\0\0\0\0",  # trailing bytes
            b"/r\0\0,\0\0\0\0\0\0\0\0",  # unsupported blob tag
            b"/r\0\0,f\0\0" + struct.pack(">f", math.nan),
            b"/r\0\0,d\0\0" + struct.pack(">d", math.inf),
        ]
        for packet in cases:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode_message(packet)

    def test_rejects_truncated_numeric_payload_and_invalid_address(self):
        for packet in (b"/r\0\0,i\0\0", b"r\0\0\0,\0\0\0", b"/r\0\0,i\0\0\0\0\0"):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode_message(packet)


@unittest.skipUnless(LIVE, "set DAW_TEST_SC_LIVE=1 for owned live-server checks")
class OwnedServerTests(unittest.TestCase):
    def test_installed_server_pcm_control_and_cleanup(self):
        if sys.platform != "darwin":
            self.skipTest("owned scsynth probe currently requires macOS")
        if not SCSYNTH:
            self.skipTest("set DAW_SCSYNTH to an absolute scsynth executable")
        for run in range(2):
            result = probe(SCSYNTH)
            self.assertFalse(result["streaming_audio"])
            self.assertFalse(result["daw_transport"])
            self.assertEqual(result["child_exit_code"], 0)
            self.assertEqual(result["hardware_bus_peak"], 0.0)
            self.assertEqual(result["before"]["frames"], 4096)
            self.assertEqual(result["after"]["frames"], 4096)
            self.assertEqual(result["before"]["frequency_hz"], 440)
            self.assertEqual(result["after"]["frequency_hz"], 660)
            self.assertGreater(result["before"]["peak"], 0.09)
            self.assertLess(result["before"]["peak"], 0.12)
            ratio = result["after"]["rms"] / result["before"]["rms"]
            self.assertGreater(ratio, 0.4)
            self.assertLess(ratio, 0.6)
            with self.subTest(run=run), self.assertRaises(ProcessLookupError):
                os.kill(result["owned_pid"], 0)

    def test_child_exit_and_log_overflow_are_bounded_and_reaped(self):
        if sys.platform != "darwin":
            self.skipTest("OwnedServer endpoint discovery uses macOS lsof")
        with tempfile.TemporaryDirectory(prefix="daw-sc-failure-") as directory:
            marker = Path(directory) / "pid"
            worker = Path(directory) / "fake-scsynth"
            for mode in ('exit', 'overflow'):
                worker.write_text(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import os, socket, sys, time
                    open({str(marker)!r}, 'w').write(str(os.getpid()))
                    if {mode!r} == 'exit': sys.exit(17)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.bind(('127.0.0.1',0))
                    print('server ready',flush=True)
                    sys.stdout.write('x'*70000)
                    sys.stdout.flush()
                    time.sleep(30)
                    """))
                worker.chmod(0o755)
                expected = "child exited" if mode == "exit" else "diagnostic limit"
                with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, expected):
                    with OwnedServer(worker, seconds=2) as owner:
                        owner.wait(lambda *_:False, seconds=1)
                pid = int(marker.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid,0)

    def test_fake_ready_worker_times_out_and_is_reaped(self):
        if sys.platform != "darwin":
            self.skipTest("OwnedServer endpoint discovery uses macOS lsof")
        with tempfile.TemporaryDirectory(prefix="daw-sc-fake-") as directory:
            executable = Path(directory) / "fake-scsynth"
            executable.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import socket
                import sys
                import time
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", 0))
                print("server ready", flush=True)
                while True:
                    time.sleep(1)
            """))
            executable.chmod(0o755)
            owner = OwnedServer(executable, seconds=5)
            pid = owner.process.pid
            try:
                with self.assertRaisesRegex(RuntimeError, "acknowledgement timed out"):
                    owner.wait(lambda _address, _values: False, seconds=0.1)
            finally:
                owner.close()
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()

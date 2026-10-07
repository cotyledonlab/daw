"""Opt-in compiled queue and silent installed-SC streaming diagnostics."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest

from native.supercollider.live_probe import OwnedServer
from native.supercollider.score import sine_synthdef, SYNTH_NAME
from native.supercollider.stream_probe import ROOT, probe, stream_synthdef

READER = ROOT / "output/sc-stream/reader"
PLUGINS = ROOT / "output/sc-stream/plugins"
LIVE = os.environ.get("DAW_TEST_SC_STREAM") == "1"


@unittest.skipUnless(READER.is_file(), "build the streaming diagnostic first")
class QueueTests(unittest.TestCase):
    def test_multiprocess_fifo_wrap_and_overflow(self):
        result = subprocess.run([str(READER), "self-test"], capture_output=True, check=True, timeout=8)
        self.assertEqual(json.loads(result.stdout)["blocks"], 12000)

    def test_invalid_identity_permissions_and_exclusive_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            queue, output = Path(directory) / "queue", Path(directory) / "pcm"
            subprocess.run([str(READER), "create", str(queue), "123"], check=True, timeout=2)
            original = queue.read_bytes()
            duplicate = subprocess.run([str(READER), "create", str(queue), "456"], capture_output=True, timeout=2)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(queue.read_bytes(), original)
            output.write_bytes(b"keep me")
            duplicate = subprocess.run([str(READER), "drain", str(queue), "123", "1", str(output)],
                                       capture_output=True, timeout=2)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(output.read_bytes(), b"keep me")
            output.unlink()
            for field, value in ((0, 0), (4, 99), (8, 44100), (12, 128), (16, 1), (20, 128), (24, 999)):
                damaged = bytearray(original)
                struct.pack_into("=I", damaged, field, value)
                queue.write_bytes(damaged)
                result = subprocess.run([str(READER), "drain", str(queue), "123", "1", str(output)],
                                        capture_output=True, timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
            queue.write_bytes(original)
            queue.chmod(0o644)
            result = subprocess.run([str(READER), "drain", str(queue), "123", "1", str(output)],
                                    capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


@unittest.skipUnless(LIVE, "set DAW_TEST_SC_STREAM=1 with built diagnostic and DAW_SCSYNTH")
class InstalledStreamTests(unittest.TestCase):
    def test_live_controls_stereo_frames_and_silent_output(self):
        executable = os.environ["DAW_SCSYNTH"]
        for _ in range(2):
            result = probe(executable, READER, PLUGINS)
            self.assertEqual(result["reader"]["count"], 512)
            self.assertEqual(result["hardware_bus_peak"], 0)
            self.assertFalse(result["daw_transport"])

    def test_overflow_and_duplicate_producer_latch_fault_and_remove_output(self):
        for duplicate, expected in ((False, 1), (True, 3)):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                queue, output = Path(directory) / "queue", Path(directory) / "pcm"
                subprocess.run([str(READER), "create", str(queue), "123"], check=True, timeout=2)
                with OwnedServer(os.environ["DAW_SCSYNTH"], stream_plugin=PLUGINS,
                                 stream_path=queue, stream_nonce=123) as server:
                    server.done("/notify", 1)
                    server.node("/g_new", 1, 0, 0)
                    server.done("/d_recv", sine_synthdef())
                    server.done("/d_recv", stream_synthdef())
                    server.send("/s_new", SYNTH_NAME, 1000, 0, 1, "out", 16.0)
                    server.wait(lambda address, values: address == "/n_go" and values[0] == 1000)
                    for node in ((1001, 1002) if duplicate else (1001,)):
                        server.send("/s_new", "daw_stream_capture", node, 1, 1)
                        server.wait(lambda address, values: address == "/n_go" and values[0] == node)
                    time.sleep(0.2)
                    self.assertEqual(struct.unpack_from("=I", queue.read_bytes(), 68)[0], expected)
                    result = subprocess.run([str(READER), "drain", str(queue), "123", "1", str(output)],
                                            capture_output=True, timeout=2)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())
                    server.node("/n_free", 1)
                    # A node destructor releases its lease. Abrupt process exit
                    # can leave it set; disposable queues are never reused.
                    self.assertEqual(struct.unpack_from("=I", queue.read_bytes(), 72)[0], 0)
                self.assertEqual(struct.unpack_from("=I", queue.read_bytes(), 72)[0], 0)


if __name__ == "__main__":
    unittest.main()

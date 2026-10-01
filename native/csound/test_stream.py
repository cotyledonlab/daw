"""Tests for the owned, finite Csound fixed-queue streaming proof."""
import copy
import hashlib
import math
import os
import platform
from pathlib import Path
import struct
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from native.csound.stream_probe import BRIDGE, ROOT, WORKER, fixture_job, probe


@unittest.skipUnless(sys.platform == 'darwin' and platform.machine() == 'arm64' and BRIDGE.is_file(),
                     'requires the built macOS arm64 queue bridge')
class StreamProbeTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="csound-stream-test-", dir=output)
        self.directory = Path(self.project.name)
        # The probe only checks that the Csound library is a regular file before
        # handing it to its owned worker; these cases do not load this marker.
        self.library = self.directory / "libcsound-test.dylib"
        self.library.write_bytes(b"test marker")
        self.worker = self.directory / "worker.py"

    def tearDown(self):
        self.project.cleanup()

    def worker_script(self, body):
        self.worker.write_text("#!/usr/bin/env python3\n" + body)
        self.worker.chmod(0o755)
        return self.worker

    def invoke(self, *, job=None, body="raise SystemExit(23)\n", **kwargs):
        worker = self.worker_script(body)
        return probe(str(self.library), bridge=BRIDGE, job=job, worker=worker, **kwargs)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_STREAM") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_STREAM=1 and DAW_CSOUND_LIBRARY")
    def test_missing_or_invalid_program_channel_fails_in_owned_worker(self):
        cases = []
        missing = fixture_job()
        missing["source"]["program"] = missing["source"]["program"].replace(
            'chn_k "frequency", 1', "")
        cases.append((missing, "worker failed"))
        invalid = fixture_job()
        invalid["source"]["program"] = invalid["source"]["program"].replace(
            "oscili", "not_an_opcode")
        cases.append((invalid, "worker failed"))
        for job, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                probe(os.environ["DAW_CSOUND_LIBRARY"], job=job)

    @unittest.skipUnless(BRIDGE.is_file(), "build the macOS arm64 Csound stream bridge")
    def test_owned_worker_early_exit_and_diagnostic_overflow_are_bounded(self):
        for body, error in (("raise SystemExit(23)\n", "worker failed|before prefill"),
                            ("import sys\nsys.stderr.write('x' * 70000)\n", "diagnostics exceeded")):
            with self.subTest(error=error):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, error):
                    self.invoke(body=body)
                self.assertLess(time.monotonic() - started, 5)

    def test_prefill_requires_bounded_typed_report_and_published_audio(self):
        for payload, error in (
            ('not json', 'Malformed'),
            ('x' * 5000, 'oversized'),
            ('{}', 'prefill acknowledgment'),
            ('{"version":true,"prefill_blocks":4}', 'prefill acknowledgment'),
            ('{"version":1,"prefill_blocks":4}', 'precedes queue publication'),
        ):
            script = f'''import os, sys
ready = sys.argv[6]
with open(ready + '.partial', 'x') as stream: stream.write({payload!r})
os.link(ready + '.partial', ready)
os.unlink(ready + '.partial')
'''
            with self.subTest(payload=payload[:60]), self.assertRaisesRegex(RuntimeError, error):
                self.invoke(body=script)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_STREAM") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_STREAM=1 and DAW_CSOUND_LIBRARY")
    def test_stall_cancel_and_worker_death_fail_within_probe_deadline(self):
        scenarios = (({"stall": True}, "stalled|deadline"),
                     ({"cancel_after_blocks": 1}, "cancelled by owner"),
                     ({"kill_after_blocks": 1}, "worker failed|missing queue blocks"))
        for options, error in scenarios:
            with self.subTest(options=options):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, error):
                    probe(os.environ["DAW_CSOUND_LIBRARY"], **options)
                self.assertLess(time.monotonic() - started, 8)

    @unittest.skipUnless(os.environ.get('DAW_TEST_CSOUND_STREAM') == '1' and
                         os.environ.get('DAW_CSOUND_LIBRARY'), 'requires the configured Csound runtime')
    def test_cli_sigterm_unwinds_owned_worker(self):
        parent = subprocess.Popen([sys.executable, str(ROOT / 'native/csound/stream_probe.py'),
            '--library', os.environ['DAW_CSOUND_LIBRARY']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and parent.poll() is None:
                inventory = subprocess.check_output(['ps', '-axo', 'pid,ppid,command'], text=True)
                for line in inventory.splitlines()[1:]:
                    fields = line.strip().split(None, 2)
                    if len(fields) == 3 and int(fields[1]) == parent.pid and str(WORKER) in fields[2]:
                        child_pid = int(fields[0])
                        break
                if child_pid is not None:
                    break
                time.sleep(.005)
            self.assertIsNotNone(child_pid, 'probe never launched its owned worker')
            parent.send_signal(signal.SIGTERM)
            stdout, stderr = parent.communicate(timeout=3)
            self.assertEqual(parent.returncode, 130, stderr.decode())
            self.assertEqual(stdout, b'')
            self.assertIn(b'cancelled', stderr)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.02)
            else:
                self.fail('owned Csound worker survived CLI cancellation')
        finally:
            if parent.poll() is None:
                parent.send_signal(signal.SIGTERM)
                parent.communicate(timeout=3)

    @unittest.skipUnless(BRIDGE.is_file(), "build the macOS arm64 Csound stream bridge")
    def test_cancel_reaps_worker_and_ordinary_descendant(self):
        pid_file = self.directory / "descendant.pid"
        script = f'''\
import json, os, subprocess, sys, time
sys.path.insert(0, {str(ROOT)!r})
from native.csound.queue_api import bind_queue
job, queue, nonce, bridge, library, ready, report = sys.argv[1:]
api = bind_queue(bridge)
error = __import__('ctypes').create_string_buffer(256)
producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
assert producer, error.value
block = (__import__('ctypes').c_double * 128)(*([0.0] * 128))
for _ in range(4):
    while api.daw_cs_queue_push(producer, block) == 0:
        time.sleep(.001)
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open({str(pid_file)!r}, 'w').write(str(descendant.pid))
temporary = ready + '.partial'
with open(temporary, 'x') as stream:
    json.dump({{'version': 1, 'prefill_blocks': 4}}, stream)
os.link(temporary, ready)
os.unlink(temporary)
while True:
    api.daw_cs_queue_push(producer, block)
    time.sleep(.001)
'''
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "cancelled by owner"):
            self.invoke(body=script, cancel_after_blocks=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(pid_file.is_file())
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(.02)
        else:
            self.fail(f"owned worker descendant {pid} survived cancellation")

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_STREAM") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_STREAM=1 and DAW_CSOUND_LIBRARY")
    def test_installed_csound_controlled_signal_backpressure_and_restart(self):
        library = Path(os.environ["DAW_CSOUND_LIBRARY"])
        if not library.is_absolute() or not library.is_file():
            self.skipTest("DAW_CSOUND_LIBRARY must name an existing absolute library")
        reports = [probe(str(library)) for _ in range(2)]
        for result in reports:
            self.assertEqual(result["consumed_frames"], 48000)
            self.assertEqual(result["prefill_blocks"], 4)
            self.assertFalse(result["hardware_audio"])
            self.assertFalse(result["daw_transport"])
            self.assertGreater(result["producer"]["backpressure_waits"], 0)
            self.assertTrue(result["producer"]["csound_released"])
            pcm = result["pcm"]
            self.assertEqual(hashlib.sha256(pcm).hexdigest(), result["consumer_sha256"])
            self.assertEqual(result["producer"]["sha256"], result["consumer_sha256"])
            left = struct.unpack("<{}f".format(len(pcm)//4), pcm)[::2]
            for start, frequency, amplitude in ((1000, 440, .1), (28000, 660, .05)):
                segment = left[start:start + 12000]
                crossings = [i for i in range(1, len(segment))
                             if segment[i-1] <= 0 < segment[i]]
                measured = (len(crossings)-1)*48000/(crossings[-1]-crossings[0])
                rms = math.sqrt(sum(value*value for value in segment)/len(segment))
                self.assertAlmostEqual(measured, frequency, delta=2)
                self.assertAlmostEqual(rms, amplitude/math.sqrt(2), delta=.002)
        self.assertEqual(reports[0]["producer"]["sha256"], reports[1]["producer"]["sha256"])

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_STREAM") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_STREAM=1 and DAW_CSOUND_LIBRARY")
    def test_partial_last_block_and_one_block_prefill_are_trimmed(self):
        library = Path(os.environ["DAW_CSOUND_LIBRARY"])
        if not library.is_absolute() or not library.is_file():
            self.skipTest("DAW_CSOUND_LIBRARY must name an existing absolute library")
        job = fixture_job()
        job["source"]["duration_frames"] = 48001
        job["source"]["program"] = job["source"]["program"].replace("i1 0 1", "i1 0 2")
        result = probe(str(library), job=job)
        self.assertEqual(result["consumed_frames"], 48001)
        self.assertEqual(result["consumed_blocks"], 751)
        self.assertEqual(result["prefill_blocks"], 4)
        self.assertEqual(len(result["pcm"]), 48001 * 8)
        # Duration validation has a 1ms floor; 48 frames exercises the minimum
        # one-block prefill and ensures the final padded samples are not returned.
        tiny = copy.deepcopy(fixture_job())
        tiny["source"]["duration_frames"] = 48
        for control in tiny["source"]["controls"]:
            control["points"] = []
        tiny["source"]["program"] = tiny["source"]["program"].replace("i1 0 1", "i1 0 0.002")
        short = probe(str(library), job=tiny, paced=False)
        self.assertEqual(short["consumed_frames"], 48)
        self.assertEqual(short["prefill_blocks"], 1)
        self.assertEqual(len(short["pcm"]), 48 * 8)


if __name__ == "__main__":
    unittest.main()

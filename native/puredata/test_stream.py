"""Tests for the owned Pure Data fixed-queue streaming diagnostic."""
from __future__ import annotations

import hashlib
import math
import os
import platform
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from native.puredata.stream_probe import BRIDGE, ROOT, WORKER, fixture_job, measure, probe, reap_worker


class WorkerReapingTests(unittest.TestCase):
    def test_terminal_worker_permission_race_still_reaps_child(self):
        child = Mock(pid=123)
        with patch('native.puredata.stream_probe.os.killpg', side_effect=ProcessLookupError):
            reap_worker(child)
        child.wait.assert_called_once_with(timeout=2)
        child.kill.assert_not_called()

    def test_terminal_permission_race_is_confirmed_by_waitpid(self):
        child = Mock(pid=123)
        child.wait.return_value = -9
        with patch('native.puredata.stream_probe.os.killpg', side_effect=PermissionError):
            reap_worker(child)
        self.assertEqual(child.wait.call_args_list[0].kwargs, {'timeout': .2})
        self.assertEqual(child.wait.call_count, 1)
        child.kill.assert_not_called()

    def test_live_worker_signal_denial_is_not_hidden(self):
        child = Mock(pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired('worker', .2), -9]
        with patch('native.puredata.stream_probe.os.killpg', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                reap_worker(child)
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, {'timeout': 2})


@unittest.skipUnless(sys.platform == 'darwin' and platform.machine() == 'arm64' and BRIDGE.is_file(),
                     'requires the macOS arm64 Csound queue bridge')
class StreamProbeTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / 'output'
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix='puredata-stream-test-', dir=output)
        self.directory = Path(self.project.name)
        # Fake-worker cases validate the probe protocol without loading libpd.
        self.library = self.directory / 'libpd-test.dylib'
        self.library.write_bytes(b'test marker')
        self.worker = self.directory / 'worker.py'

    def tearDown(self):
        self.project.cleanup()

    def worker_script(self, body):
        self.worker.write_text('#!/usr/bin/env python3\n' + body)
        self.worker.chmod(0o755)
        return self.worker

    def invoke(self, *, job=None, body='raise SystemExit(23)\n', **kwargs):
        return probe(str(self.library), bridge=BRIDGE, job=job,
                     worker=self.worker_script(body), **kwargs)

    def test_worker_early_exit_and_diagnostic_overflow_are_bounded(self):
        for body, error in (('raise SystemExit(23)\n', 'worker failed|before prefill'),
                            ("import sys\nsys.stderr.write('x' * 70000)\n", 'diagnostics exceeded'),
                            ("import sys\nsys.stdout.write('x' * 70000)\nsys.stdout.flush()\n", 'diagnostics exceeded')):
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                self.invoke(body=body)

    def test_prefill_requires_bounded_typed_report_and_published_audio(self):
        for payload, error in (
                ('not json', 'Malformed'), ('x' * 5000, 'oversized'), ('{}', 'prefill acknowledgment'),
                ('{"version":true,"prefill_blocks":4}', 'prefill acknowledgment'),
                ('{"version":1,"prefill_blocks":4}', 'precedes queue publication')):
            script = f'''import os, sys
ready = sys.argv[6]
with open(ready + '.partial', 'x') as stream: stream.write({payload!r})
os.link(ready + '.partial', ready)
os.unlink(ready + '.partial')
'''
            with self.subTest(payload=payload[:60]), self.assertRaisesRegex(RuntimeError, error):
                self.invoke(body=script)

    def test_malformed_and_oversized_producer_reports_are_rejected(self):
        # Publish a valid queue/prefill, then leave a malformed/oversized report.
        prefix = '''import ctypes as C, json, os, sys, time
sys.path.insert(0, %r)
from native.csound.queue_api import bind_queue
job, queue, nonce, bridge, library, ready, report = sys.argv[1:]
api = bind_queue(bridge); error = C.create_string_buffer(256)
producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
block = (C.c_double * 128)(*([0.0] * 128))
job_value = json.load(open(job))
total = (job_value['source']['duration_frames'] + 63) // 64
for index in range(total):
    while api.daw_cs_queue_push(producer, block) == 0: time.sleep(.001)
    if index == 3:
        tmp = ready + '.partial'
        with open(tmp, 'x') as f: json.dump({'version':1,'prefill_blocks':4}, f)
        os.link(tmp, ready); os.unlink(tmp)
'''
        for payload, error in (('not json', 'Malformed'), ('x' * 5000, 'oversized')):
            body = prefix % str(ROOT) + f"\nwith open(report, 'x') as f: f.write({payload!r})\n"
            with self.subTest(payload=payload[:30]), self.assertRaisesRegex(RuntimeError, error):
                self.invoke(body=body)

        # Complete publication and then leave the report as an untrusted symlink.
        body = prefix % str(ROOT) + '''
os.symlink('/dev/null', report)
'''
        with self.assertRaisesRegex(RuntimeError, 'Invalid or oversized'):
            self.invoke(body=body)

    def test_unreleased_producer_lease_is_rejected(self):
        script = f'''import ctypes as C, hashlib, json, os, struct, sys, time
sys.path.insert(0, {str(ROOT)!r})
from native.csound.queue_api import bind_queue
job, queue, nonce, bridge, library, ready, report = sys.argv[1:]
api = bind_queue(bridge); error = C.create_string_buffer(256)
producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
job_value = json.load(open(job)); blocks = (job_value['source']['duration_frames'] + 63) // 64
block = (C.c_double * 128)(*([0.0] * 128)); pcm_block = struct.pack('<128f', *([0.0] * 128))
digest = hashlib.sha256(pcm_block * blocks).hexdigest()
for index in range(blocks):
    while api.daw_cs_queue_push(producer, block) == 0: time.sleep(.001)
    if index == 3:
        tmp = ready + '.partial'
        with open(tmp, 'x') as stream: json.dump({{'version':1,'prefill_blocks':4}}, stream)
        os.link(tmp, ready); os.unlink(tmp)
with open(report, 'x') as stream:
    json.dump({{'version':1,'published_blocks':blocks,'source_frames':job_value['source']['duration_frames'],
      'queue_frames':blocks*64,'sha256':digest,'source_gain_applied':False,
      'puredata_released':True,'backpressure_waits':0}}, stream)
# Deliberately leak the producer lease until the process exits.
'''
        with self.assertRaisesRegex(RuntimeError, 'did not release its queue lease'):
            self.invoke(body=script)

    @unittest.skipUnless(os.environ.get('DAW_TEST_LIBPD_STREAM') == '1' and os.environ.get('DAW_LIBPD_LIBRARY'),
                         'set DAW_TEST_LIBPD_STREAM=1 and DAW_LIBPD_LIBRARY')
    def test_missing_receiver_and_unknown_object_fail_in_owned_worker(self):
        cases = []
        missing = fixture_job()
        missing['source']['program'] = missing['source']['program'].replace('r \\$0-frequency', 'r \\$0-absent')
        cases.append((missing, 'worker failed'))
        invalid = fixture_job()
        invalid['source']['program'] = invalid['source']['program'].replace('osc~', 'daw_unknown_object')
        cases.append((invalid, 'worker failed'))
        library = Path(os.environ['DAW_LIBPD_LIBRARY'])
        if not library.is_absolute() or not library.is_file():
            self.skipTest('DAW_LIBPD_LIBRARY must name an existing absolute library')
        for job, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                probe(str(library), job=job)

    @unittest.skipUnless(os.environ.get('DAW_TEST_LIBPD_STREAM') == '1' and os.environ.get('DAW_LIBPD_LIBRARY'),
                         'set DAW_TEST_LIBPD_STREAM=1 and DAW_LIBPD_LIBRARY')
    def test_stall_cancel_and_worker_death_fail_within_probe_deadline(self):
        library = os.environ['DAW_LIBPD_LIBRARY']
        for options, error in (({'stall': True}, 'stalled|deadline'),
                               ({'cancel_after_blocks': 1}, 'cancelled by owner'),
                               ({'kill_after_blocks': 1}, 'worker failed|missing queue blocks')):
            with self.subTest(options=options):
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, error):
                    probe(library, **options)
                self.assertLess(time.monotonic() - started, 8)

    @unittest.skipUnless(os.environ.get('DAW_TEST_LIBPD_STREAM') == '1' and os.environ.get('DAW_LIBPD_LIBRARY'),
                         'requires the configured libpd runtime')
    def test_cli_sigterm_unwinds_owned_worker(self):
        parent = subprocess.Popen([sys.executable, str(ROOT / 'native/puredata/stream_probe.py'),
            '--library', os.environ['DAW_LIBPD_LIBRARY']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and parent.poll() is None:
                try:
                    inventory = subprocess.check_output(['ps', '-axo', 'pid,ppid,command'], text=True)
                except PermissionError:
                    self.skipTest('process inventory is unavailable in this sandbox')
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
                self.fail('owned libpd worker survived CLI cancellation')
        finally:
            if parent.poll() is None:
                parent.send_signal(signal.SIGTERM)
                parent.communicate(timeout=3)

    def test_cancel_reaps_worker_and_ordinary_descendant(self):
        pid_file = self.directory / 'descendant.pid'
        body = f'''\
import ctypes as C, json, os, subprocess, sys, time
sys.path.insert(0, {str(ROOT)!r})
from native.csound.queue_api import bind_queue
job, queue, nonce, bridge, library, ready, report = sys.argv[1:]
api = bind_queue(bridge); error = C.create_string_buffer(256)
producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
block = (C.c_double * 128)(*([0.0] *  128))
for _ in range(4):
    while api.daw_cs_queue_push(producer, block) == 0: time.sleep(.001)
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open({str(pid_file)!r}, 'w').write(str(descendant.pid))
tmp = ready + '.partial'
with open(tmp, 'x') as stream: json.dump({{'version':1,'prefill_blocks':4}}, stream)
os.link(tmp, ready); os.unlink(tmp)
while True:
    api.daw_cs_queue_push(producer, block)
    time.sleep(.001)
'''
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'cancelled by owner'):
            self.invoke(body=body, cancel_after_blocks=1)
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
            self.fail(f'owned worker descendant {pid} survived cancellation')

    def test_worker_deadline_reaps_worker_and_ordinary_descendant(self):
        pid_file = self.directory / 'deadline-descendant.pid'
        body = f'''import os, subprocess, sys, time
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
open({str(pid_file)!r}, 'w').write(str(descendant.pid))
time.sleep(60)
'''
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'deadline exceeded'):
            self.invoke(body=body)
        self.assertLess(time.monotonic() - started, 20)
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
            self.fail(f'owned worker descendant {pid} survived deadline cleanup')

    @unittest.skipUnless(os.environ.get('DAW_TEST_LIBPD_STREAM') == '1' and os.environ.get('DAW_LIBPD_LIBRARY'),
                         'requires the configured libpd runtime')
    def test_installed_libpd_signal_continuity_backpressure_and_restart(self):
        library = Path(os.environ['DAW_LIBPD_LIBRARY'])
        if not library.is_absolute() or not library.is_file():
            self.skipTest('DAW_LIBPD_LIBRARY must name an existing absolute library')
        job = fixture_job()
        job['source']['gain'] = .25
        reports = [probe(str(library), job=job) for _ in range(2)]
        for result in reports:
            self.assertEqual(result['consumed_frames'], 48000)
            self.assertEqual(result['prefill_blocks'], 4)
            self.assertFalse(result['hardware_audio'])
            self.assertFalse(result['daw_transport'])
            producer = result['producer']
            self.assertGreater(producer['backpressure_waits'], 0)
            self.assertTrue(producer['puredata_released'])
            self.assertFalse(producer['source_gain_applied'])
            pcm = result['pcm']
            digest = hashlib.sha256(pcm).hexdigest()
            self.assertEqual(digest, result['consumer_sha256'])
            self.assertEqual(producer['sha256'], digest)
            for start, frequency, amplitude in ((1000, 440, .1), (28000, 660, .05)):
                measured = measure(pcm, start)
                self.assertAlmostEqual(measured['frequency_hz'], frequency, delta=2)
                self.assertAlmostEqual(measured['rms'], amplitude / math.sqrt(2), delta=.002)
        self.assertEqual(reports[0]['producer']['sha256'], reports[1]['producer']['sha256'])

    @unittest.skipUnless(os.environ.get('DAW_TEST_LIBPD_STREAM') == '1' and os.environ.get('DAW_LIBPD_LIBRARY'),
                         'requires the configured libpd runtime')
    def test_partial_last_block_is_trimmed(self):
        library = Path(os.environ['DAW_LIBPD_LIBRARY'])
        if not library.is_absolute() or not library.is_file():
            self.skipTest('DAW_LIBPD_LIBRARY must name an existing absolute library')
        job = fixture_job()
        job['source']['duration_frames'] = 48001
        result = probe(str(library), job=job)
        self.assertEqual(result['consumed_frames'], 48001)
        self.assertEqual(result['consumed_blocks'], 751)
        self.assertEqual(result['prefill_blocks'], 4)
        self.assertEqual(len(result['pcm']), 48001 * 8)
        padded = result['pcm'] + bytes(63 * 8)
        self.assertEqual(hashlib.sha256(padded).hexdigest(), result['consumer_sha256'])


if __name__ == '__main__':
    unittest.main()

"""Opt-in muted JSONL tests for live Pure Data receiver edits.

Run on macOS arm64 with DAW_TEST_LIBPD_CONTROLS=1,
DAW_LIBPD_LIBRARY set to the absolute libpd library, and a target/debug/daw
binary built with native-audio and the fixed queue bridge.
"""
from __future__ import annotations

import json
import math
import os
import platform
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.puredata_tracks_demo import make_track as pd_track  # noqa: E402
from native.puredata.test_live_transport import (  # noqa: E402
    DAW, LIBRARY, JsonlClient, builtin_track,
)

ENABLED = os.environ.get('DAW_TEST_LIBPD_CONTROLS') == '1'
CSOUND_LIBRARY = os.environ.get('DAW_CSOUND_LIBRARY')


def control_session() -> dict:
    track = pd_track('live-pd-one', 440.0, .5)
    track['device']['duration_frames'] = 4 * 48_000
    for control in track['device']['controls']:
        if control['name'] == '$0-amplitude':
            control['points'] = []
        else:
            control['points'] = [point for point in control['points']
                                 if point['frame'] < track['device']['duration_frames']]
    return {'schema_version': 8, 'sample_rate': 48_000, 'tempo_milli_bpm': 120_000,
            'tracks': [track, builtin_track(0.0)]}


def control_params(revision: str, values: list, *, track_id='live-pd-one', name='$0-amplitude') -> dict:
    return {'expected_revision': revision, 'track_id': track_id,
            'control_name': name, 'values': values}


def as_float32(value: float) -> float:
    return struct.unpack('<f', struct.pack('<f', value))[0]


@unittest.skipUnless(ENABLED, 'set DAW_TEST_LIBPD_CONTROLS=1 for live Pd control checks')
class LivePureDataControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            raise unittest.SkipTest('live Pd controls require macOS arm64')
        if not DAW.is_file():
            raise unittest.SkipTest('build target/debug/daw with native-audio first')
        if not LIBRARY or not Path(LIBRARY).is_absolute() or not Path(LIBRARY).is_file():
            raise unittest.SkipTest('set DAW_LIBPD_LIBRARY to an absolute libpd library')

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='pd-live-control-')
        self.addCleanup(self.tempdir.cleanup)
        self.client = JsonlClient()
        self.addCleanup(self.client.close)
        capabilities = self.client.request('capabilities')
        if not capabilities.get('live_audio'):
            self.skipTest('build target/debug/daw with native-audio')
        if not capabilities.get('puredata_live_transport', {}).get('live_control_edits'):
            self.skipTest('build target/debug/daw with live Pd receiver edits')
        self.client.request('session.replace', {'session': control_session()})
        self.revision = self.client.request('session.inspect')['revision']

    def use_stream_worker(self, worker: Path, session=None, extra_env=None):
        self.client.close()
        overrides = {'DAW_LIBPD_STREAM_WORKER': str(worker)}
        if extra_env:
            overrides.update(extra_env)
        self.client = JsonlClient(overrides)
        self.addCleanup(self.client.close)
        capabilities = self.client.request('capabilities')
        self.assertTrue(capabilities.get('puredata_live_transport', {}).get('live_control_edits'))
        self.client.request('session.replace', {'session': session or control_session()})
        self.revision = self.client.request('session.inspect')['revision']

    def start_live(self) -> dict:
        started = self.client.request('transport.play', {
            'seconds': 4.0, 'volume': 0.0, 'source_mode': 'live',
        }, timeout=40)
        self.assertIn(started['state'], ('starting', 'playing'))
        self.assertEqual(started['volume'], 0.0)
        self.assertTrue(started['source']['owned_pids'])
        return started

    def wait_submitted(self, minimum: int, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            latest = self.client.request('transport.status', timeout=3)
            if latest.get('state') == 'playing' and latest.get('submitted_frames', 0) >= minimum:
                return latest
            if latest.get('state') == 'stopped':
                self.fail(f'transport stopped before callback submitted {minimum} frames: {latest}')
            time.sleep(.01)
        self.fail(f'callback did not submit {minimum} frames: {latest}')

    def wait_update_observed(self, revision: str, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            latest = self.client.request('transport.status', timeout=3)
            update = latest.get('source_control_update') or {}
            if (not update.get('pending') and update.get('applied_revision') == revision
                    and update.get('callback_observed')):
                return latest
            if latest.get('state') not in ('starting', 'playing'):
                self.fail(f'transport stopped before callback observed control update: {latest}')
            time.sleep(.01)
        self.fail(f'control update was not observed by callback: {latest}')

    def assert_pd_delivery(self, status: dict, revision: str, value: float):
        update = status['source_control_update']
        self.assertFalse(update['pending'])
        self.assertEqual(update['applied_revision'], revision)
        self.assertIsInstance(update['applied_frame'], int)
        self.assertTrue(update['callback_observed'])
        self.assertEqual(update['delivered_value'], as_float32(value))
        self.assertEqual(update['delivery'], 'receiver_message_and_block_publication')

    def assert_released(self, report: dict):
        self.assertEqual(report['state'], 'stopped')
        self.assertTrue(report.get('resources_released'))
        source = report.get('source') or {}
        self.assertTrue(source.get('owned_processes_released'))
        self.assertTrue(source.get('queue_released'))

    def assert_pids_reaped(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def request_nonfinite_control(self, revision: str) -> dict:
        client = self.client
        client.next_id += 1
        request_id = str(client.next_id)
        body = json.dumps({
            'protocol_version': 1, 'id': request_id, 'method': 'source.set_control',
            'params': control_params(revision, ['__NONFINITE__']),
        }, separators=(',', ':')).replace('"__NONFINITE__"', 'NaN')
        client.process.stdin.write(body.encode() + b'\n')
        client.process.stdin.flush()
        deadline = time.monotonic() + 3
        while True:
            newline = client.buffer.find(b'\n')
            if newline >= 0:
                line = bytes(client.buffer[:newline])
                del client.buffer[:newline + 1]
                response = json.loads(line)
                self.assertFalse(response.get('ok'))
                self.assertEqual(response.get('error', {}).get('code'), 'invalid_request')
                return response
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'timed out waiting for non-finite request rejection')
            readable, _, _ = select.select([client.process.stdout], [], [], remaining)
            self.assertTrue(readable, 'timed out waiting for non-finite request rejection')
            client.buffer.extend(os.read(client.process.stdout.fileno(), 4096))

    def test_live_amplitude_edit_changes_callback_rms_and_reports_delivery_contract(self):
        started = self.start_live()
        self.wait_submitted(48_000)
        queued = self.client.request('source.set_control', control_params(self.revision, [.025]))
        self.revision = queued['revision']
        self.assertTrue(queued.get('queued'))
        observed = self.wait_update_observed(self.revision)
        self.assert_pd_delivery(observed, self.revision, .025)
        saved = self.client.request('session.get')
        amplitude = next(control for control in saved['tracks'][0]['device']['controls']
                         if control['name'] == '$0-amplitude')
        self.assertEqual(amplitude['value'], .025)
        self.assertEqual(amplitude['points'], [])

        deadline = time.monotonic() + 8
        final = None
        while time.monotonic() < deadline:
            final = self.client.request('transport.status', timeout=3)
            if final.get('state') == 'stopped':
                break
            time.sleep(.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
        quarters = final['source']['rms_quarters']
        self.assertEqual(len(quarters), 4)
        self.assertAlmostEqual(quarters[0], .1 * .5 * .5 / math.sqrt(2), delta=.0002)
        self.assertAlmostEqual(quarters[-1], .025 * .5 * .5 / math.sqrt(2), delta=.0002)
        self.assert_pids_reaped(started['source']['owned_pids'])

    def test_f64_base_float32_delivery_save_load_and_prepared_cache_freshness(self):
        started = self.start_live()
        self.wait_submitted(48_000)
        precise = 0.030000000000001
        queued = self.client.request('source.set_control', control_params(self.revision, [precise]))
        self.revision = queued['revision']
        self.assertTrue(queued.get('queued'))
        observed = self.wait_update_observed(self.revision)
        self.assert_pd_delivery(observed, self.revision, precise)
        self.assertNotEqual(as_float32(precise), precise)

        saved = self.client.request('session.get')
        amplitude = next(control for control in saved['tracks'][0]['device']['controls']
                         if control['name'] == '$0-amplitude')
        self.assertEqual(amplitude['value'], precise)
        inspect_before = self.client.request('session.inspect')
        deadline = time.monotonic() + 8
        final = None
        while time.monotonic() < deadline:
            final = self.client.request('transport.status', timeout=3)
            if final.get('state') == 'stopped':
                break
            time.sleep(.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
        self.assert_pids_reaped(started['source']['owned_pids'])

        output_root = ROOT / 'output'
        output_root.mkdir(exist_ok=True)
        output_temp = tempfile.TemporaryDirectory(prefix='pd-live-control-render-', dir=output_root)
        self.addCleanup(output_temp.cleanup)
        output_dir = Path(output_temp.name)
        output = output_dir / 'fresh.wav'
        saved_path = output_dir / 'session.json'
        self.client.request('session.save', {'path': str(saved_path)})
        self.assertEqual(json.loads(saved_path.read_text()), saved)
        self.client.request('render', {'path': str(output), 'seconds': 1.0})
        self.assertEqual(self.client.request('session.inspect'), inspect_before)
        self.client.request('session.load', {'path': str(saved_path)})
        self.assertEqual(self.client.request('session.get'), saved)
        with wave.open(str(output), 'rb') as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.getsampwidth()),
                             (48_000, 2, 2))
            raw = stream.readframes(stream.getnframes())
        self.assertEqual(len(raw) // 4, 48_000)
        pcm = struct.unpack('<{}h'.format(len(raw) // 2), raw)
        self.assertGreater(max(map(abs, pcm)), 0)
        # The base and track/effect gains are used after fresh preparation.
        self.assertAlmostEqual(max(map(abs, pcm)) / 32767,
                               precise * .5 * .5, delta=.0001)
        for start, frequency in ((1000, 440), (28_000, 660)):
            samples = pcm[::2][start:start + 12_000]
            crossings = [i for i in range(1, len(samples)) if samples[i - 1] <= 0 < samples[i]]
            measured = (len(crossings) - 1) * 48_000 / (crossings[-1] - crossings[0])
            self.assertAlmostEqual(measured, frequency, delta=2)

    def test_stale_invalid_automated_overflow_and_nonscalar_edits_preserve_live_state(self):
        automated = control_session()
        amplitude = next(control for control in automated['tracks'][0]['device']['controls']
                         if control['name'] == '$0-amplitude')
        amplitude['points'] = [{'frame': 0, 'value': .1}]
        self.client.request('session.replace', {'session': automated,
                                                  'expected_revision': self.revision})
        self.revision = self.client.request('session.inspect')['revision']
        started = self.start_live()
        before = self.wait_submitted(1_024)
        saved_before = self.client.request('session.get')
        cases = (
            (control_params('0', [.04]), 'revision_conflict'),
            (control_params(self.revision, []), 'invalid_params'),
            (control_params(self.revision, [.04, .05]), 'invalid_params'),
            (control_params(self.revision, [1e100]), 'invalid_params'),
            ({**control_params(self.revision, [.04]), 'control_name': 'missing'}, 'invalid_params'),
            (control_params(self.revision, [.04]), 'invalid_params'),  # saved points make it automated
        )
        for params, code in cases:
            with self.subTest(params=params):
                error = self.client.request_error('source.set_control', params)
                self.assertEqual(error.code, code)
                self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
                self.assertEqual(self.client.request('session.get'), saved_before)
                after = self.client.request('transport.status')
                self.assertEqual(after['state'], 'playing')
                self.assertGreaterEqual(after['submitted_frames'], before['submitted_frames'])
        self.request_nonfinite_control(self.revision)
        self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
        self.assertEqual(self.client.request('session.get'), saved_before)
        self.assertEqual(self.client.request('transport.status')['state'], 'playing')
        stopped = self.client.request('transport.stop', timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started['source']['owned_pids'])

    def test_stopped_and_prepared_playback_reject_pd_receiver_changes(self):
        error = self.client.request_error('source.set_control', control_params(self.revision, [.04]))
        self.assertEqual(error.code, 'audio_error')
        self.client.request('transport.play', {'seconds': .5, 'volume': 0.0}, timeout=30)
        error = self.client.request_error('source.set_control', control_params(self.revision, [.04]))
        self.assertEqual(error.code, 'audio_error')
        stopped = self.client.request('transport.stop', timeout=30)
        self.assertTrue(stopped.get('resources_released'))
        self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)

    def write_fault_worker(self, fault: str) -> Path:
        path = Path(self.tempdir.name) / f'{fault}-worker.py'
        fault_statements = []
        if fault == 'missing_ack':
            fault_statements.append('        return')
        if fault == 'late_ack':
            fault_statements.append('        time.sleep(3.0)')
        if fault == 'producer_death':
            fault_statements.append('        os._exit(23)')
        if fault == 'bad_ack':
            fault_statements.append("        value = {**value, 'value': value['value'] + .01}")
        body = (
            '#!/usr/bin/env python3\n'
            'import os, sys, time\n'
            f'sys.path.insert(0, {str(ROOT)!r})\n'
            'import native.puredata.stream_worker as worker\n'
            'original = worker.publish_json\n'
            'def publish(path, value):\n'
            "    if str(path).endswith('/ack.json'):\n"
            + ('\n'.join(fault_statements) + '\n' if fault_statements else '')
            + '    original(path, value)\n'
            + 'worker.publish_json = publish\n'
            + 'worker.stream(*sys.argv[1:])\n'
        )
        compile(body, str(path), 'exec')
        path.write_text(body)
        path.chmod(0o755)
        return path

    def test_bad_late_ack_and_producer_death_stop_and_reap_all_workers(self):
        for fault in ('bad_ack', 'missing_ack', 'late_ack', 'producer_death'):
            with self.subTest(fault=fault):
                self.use_stream_worker(self.write_fault_worker(fault))
                started = self.start_live()
                queued = self.client.request('source.set_control', control_params(self.revision, [.04]))
                self.revision = queued['revision']
                self.assertTrue(queued.get('queued'))
                deadline = time.monotonic() + 8
                failed = None
                while time.monotonic() < deadline:
                    failed = self.client.request('transport.status', timeout=3)
                    if failed.get('state') == 'stopped':
                        break
                    time.sleep(.025)
                self.assertIsNotNone(failed)
                self.assertEqual(failed['state'], 'stopped', failed)
                self.assertEqual(failed['error']['code'], 'runtime_error')
                self.assertTrue(failed.get('resources_released'))
                self.assert_pids_reaped(started['source']['owned_pids'])
                saved = self.client.request('session.get')
                amplitude = next(control for control in saved['tracks'][0]['device']['controls']
                                 if control['name'] == '$0-amplitude')
                self.assertEqual(amplitude['value'], .04)
                self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)

    @unittest.skipUnless(CSOUND_LIBRARY and Path(CSOUND_LIBRARY).is_absolute() and Path(CSOUND_LIBRARY).is_file(),
                         'set DAW_CSOUND_LIBRARY for mixed Pd/Csound control coverage')
    def test_pd_and_csound_controls_can_be_edited_alternately_in_one_session(self):
        from examples.csound_tracks_demo import track as csound_track

        session = control_session()
        cs = csound_track('live-csound-control', 330.0, .2)
        cs['device']['duration_frames'] = 4 * 48_000
        cs['device']['program'] = cs['device']['program'].replace('i1 0 1\n', 'i1 0 10\n')
        for control in cs['device']['controls']:
            control['points'] = []
        session['tracks'] = [session['tracks'][0], cs]
        self.use_stream_worker(Path(ROOT / 'native/puredata/stream_worker.py'), session,
                              {'DAW_CSOUND_LIBRARY': str(CSOUND_LIBRARY)})
        started = self.start_live()
        for track_id, control_name, value in (
                ('live-pd-one', '$0-amplitude', .05),
                ('live-csound-control', 'amplitude', .04),
                ('live-pd-one', '$0-amplitude', .025)):
            queued = self.client.request('source.set_control', control_params(
                self.revision, [value], track_id=track_id, name=control_name))
            self.revision = queued['revision']
            self.assertTrue(queued.get('queued'))
            status = self.wait_update_observed(self.revision)
            if track_id == 'live-pd-one':
                self.assert_pd_delivery(status, self.revision, value)
        stopped = self.client.request('transport.stop', timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started['source']['owned_pids'])


if __name__ == '__main__':
    unittest.main()

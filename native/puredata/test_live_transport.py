"""Opt-in JSONL integration tests for native live Pure Data transport.

Run on macOS arm64 with DAW_TEST_LIBPD_TRANSPORT=1,
DAW_LIBPD_LIBRARY set to the absolute tested libpd library, and
target/debug/daw built with native-audio. All callback output is muted.
"""
from __future__ import annotations

import json
import os
import platform
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.puredata_tracks_demo import make_track as pd_track  # noqa: E402

ENABLED = os.environ.get('DAW_TEST_LIBPD_TRANSPORT') == '1'
LIBRARY = os.environ.get('DAW_LIBPD_LIBRARY')
CSOUND_LIBRARY = os.environ.get('DAW_CSOUND_LIBRARY')
SCSYNTH = os.environ.get('DAW_SCSYNTH')
DAW = ROOT / 'target/debug/daw'
MAX_LINE = 1_048_577


class ProtocolError(AssertionError):
    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


class JsonlClient:
    """Small protocol client that isolates libpd setup from Csound tests."""

    def __init__(self, overrides=None):
        env = os.environ.copy()
        env.update(overrides or {})
        env['DAW_LIBPD_LIBRARY'] = str(LIBRARY)
        env['DAW_LIBPD_PYTHON'] = os.path.realpath(sys.executable)
        self.process = subprocess.Popen(
            [str(DAW), 'serve'], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.next_id = 0
        self.buffer = bytearray()

    def request(self, method: str, params: dict | None = None, timeout: float = 20) -> dict:
        self.next_id += 1
        request_id = str(self.next_id)
        payload = json.dumps({
            'protocol_version': 1, 'id': request_id, 'method': method,
            'params': params or {},
        }, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        if len(payload) > MAX_LINE:
            raise AssertionError('test protocol request exceeded 1 MiB')
        if self.process.poll() is not None or self.process.stdin is None:
            raise AssertionError('JSONL engine exited before request')
        self.process.stdin.write(payload)
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            newline = self.buffer.find(b'\n')
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                response = json.loads(line)
                if response.get('id') != request_id:
                    raise AssertionError(f'unexpected JSONL response id: {response}')
                if not response.get('ok'):
                    error = response.get('error') or {}
                    raise ProtocolError(error.get('code', 'unknown'), error.get('message', ''))
                return response['result']
            if len(self.buffer) >= MAX_LINE:
                raise AssertionError('JSONL engine response exceeded 1 MiB')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'timed out waiting for {method} response')
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not readable:
                raise TimeoutError(f'timed out waiting for {method} response')
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise AssertionError('JSONL engine closed stdout before response')
            self.buffer.extend(chunk)

    def request_error(self, method: str, params: dict | None = None) -> ProtocolError:
        try:
            self.request(method, params)
        except ProtocolError as error:
            return error
        raise AssertionError(f'{method} unexpectedly succeeded')

    def close_eof(self, timeout: float = 30) -> int:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        return self.process.wait(timeout=timeout)

    def close(self):
        if self.process.poll() is None:
            try:
                self.close_eof(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


def builtin_track(gain=0.08):
    return {'id': 'builtin', 'mode': 'continuous', 'clips': [], 'effects': [],
            'device': {'kind': 'sine', 'frequency_hz': 220.0, 'gain': gain}}


def _bounded_pd_track(track_id, frequency=440.0, gain=0.5, frames=48_000):
    track = pd_track(track_id, frequency, gain)
    track['device']['duration_frames'] = frames
    for control in track['device']['controls']:
        if control['name'] == '$0-amplitude' and 24_576 < frames:
            control['points'] = [{'frame': 24_576, 'value': 0.05}]
        else:
            control['points'] = [point for point in control['points'] if point['frame'] < frames]
    return track


def session_fixture() -> dict:
    return {'schema_version': 8, 'sample_rate': 48_000, 'tempo_milli_bpm': 120_000,
            'tracks': [_bounded_pd_track('live-pd-one', 440.0, 0.5),
                       _bounded_pd_track('live-pd-two', 330.0, 0.0), builtin_track()]}


def session_with_pd_duration(frames: int) -> dict:
    session = session_fixture()
    for item in session['tracks'][:2]:
        item['device']['duration_frames'] = frames
        for control in item['device']['controls']:
            control['points'] = [point for point in control['points'] if point['frame'] < frames]
    return session


@unittest.skipUnless(ENABLED, 'set DAW_TEST_LIBPD_TRANSPORT=1 for native live Pd transport checks')
class LivePureDataTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            raise unittest.SkipTest('live Pure Data transport requires macOS arm64')
        if not DAW.is_file():
            raise unittest.SkipTest('build target/debug/daw with native-audio first')
        if not LIBRARY or not Path(LIBRARY).is_absolute() or not Path(LIBRARY).is_file():
            raise unittest.SkipTest('set DAW_LIBPD_LIBRARY to the tested absolute libpd library')

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix='pd-live-transport-')
        self.addCleanup(self.tempdir.cleanup)
        self.client = JsonlClient()
        self.addCleanup(self.client.close)
        capabilities = self.client.request('capabilities')
        if not capabilities.get('live_audio'):
            self.skipTest('build target/debug/daw with native-audio')
        if not capabilities.get('puredata_live_transport', {}).get('implemented'):
            self.skipTest('build target/debug/daw with live Pure Data transport')
        self.client.request('session.replace', {'session': session_fixture()})
        self.revision = self.client.request('session.inspect')['revision']

    def start_live(self, seconds: float = 2.0) -> dict:
        status = self.client.request('transport.play', {
            'seconds': seconds, 'volume': 0.0, 'source_mode': 'live',
        }, timeout=40)
        allowed_states = ('starting', 'playing', 'stopped') if seconds <= 48 / 48_000 else ('starting', 'playing')
        self.assertIn(status['state'], allowed_states)
        self.assertEqual(status['source_mode'], 'live')
        self.assertIn(status['runtime'], ('puredata', 'mixed'))
        self.assertEqual(status['startup'], 'prefilled')
        self.assertEqual(status['volume'], 0.0)
        self.assertTrue(status['source'].get('runtime_sources'))
        pids = status['source']['owned_pids']
        self.assertTrue(pids)
        self.assertTrue(all(int(pid) > 0 for pid in pids))
        return status

    def wait_playing(self, timeout: float = 8.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.client.request('transport.status', timeout=3)
            if status.get('state') == 'playing' and status.get('submitted_frames', 0) > 0:
                self.assertGreater(status.get('callback_signal_peak', 0), 0)
                return status
            if status.get('state') == 'stopped':
                self.fail(f'transport stopped before callback playback: {status}')
            time.sleep(.025)
        self.fail('live transport did not enter playing state')

    def assert_pids_reaped(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def use_stream_worker(self, worker: Path, session=None):
        self.client.close()
        self.client = JsonlClient({'DAW_LIBPD_STREAM_WORKER': str(worker)})
        self.addCleanup(self.client.close)
        capabilities = self.client.request('capabilities')
        self.assertTrue(capabilities.get('puredata_live_transport', {}).get('implemented'))
        self.client.request('session.replace', {'session': session or session_fixture()})
        self.revision = self.client.request('session.inspect')['revision']

    def write_fake_stream_worker(self, mode: str) -> Path:
        path = Path(self.tempdir.name) / f'{mode}-stream-worker.py'
        if mode in ('bad-compiled-json', 'compiled-timeout'):
            body = ("import sys, time\n"
                    "gate = sys.argv[8]\n"
                    + ("open(gate + '.compiled', 'x').write('not json')\n" if mode == 'bad-compiled-json' else "")
                    + "time.sleep(60)\n")
        else:
            completed = 'malformed' if mode == 'bad-completed-report' else 'valid'
            exit_code = 23 if mode == 'postfinal-worker-exit' else 0
            body = f'''import ctypes as C, hashlib, json, os, sys, time
sys.path.insert(0, {str(ROOT)!r})
from native.csound.queue_api import bind_queue
job_path, queue_path, nonce, bridge, library, ready, report, gate = sys.argv[1:]
api = bind_queue(bridge); error = C.create_string_buffer(256)
producer = api.daw_cs_queue_open(os.fsencode(queue_path), int(nonce), error, len(error))
assert producer, error.value.decode('utf-8', 'replace')
with open(gate + '.compiled', 'x') as stream:
    json.dump({{'version':1,'sample_rate':48000,'blocksize':64,'channels':2}}, stream)
while not os.path.exists(gate): time.sleep(.001)
job = json.load(open(job_path)); frames = job['source']['duration_frames']
blocks = (frames + 63) // 64; block = (C.c_double * 128)(*([0.0] * 128))
digest = hashlib.sha256(); packed = bytes(512); waits = 0
for index in range(blocks):
    while True:
        result = api.daw_cs_queue_push(producer, block)
        if result == 1: break
        if result < 0: raise RuntimeError('queue push failed')
        waits += 1; time.sleep(.001)
    digest.update(packed)
    if index + 1 == min(4, blocks):
        tmp = ready + '.partial'
        with open(tmp, 'x') as stream: json.dump({{'version':1,'prefill_blocks':min(4,blocks)}}, stream)
        os.link(tmp, ready); os.unlink(tmp)
api.daw_cs_queue_close(producer)
if {completed!r} == 'malformed':
    with open(report, 'x') as stream: stream.write('{{')
else:
    with open(report, 'x') as stream:
        json.dump({{'version':1,'published_blocks':blocks,'source_frames':frames,
          'queue_frames':blocks*64,'sha256':digest.hexdigest(),'backpressure_waits':waits,
          'source_gain_applied':False,'puredata_released':True}}, stream)
raise SystemExit({exit_code})
'''
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o755)
        return path

    def assert_released(self, report: dict):
        self.assertEqual(report['state'], 'stopped')
        self.assertTrue(report.get('resources_released'))
        source = report.get('source') or {}
        self.assertTrue(source.get('owned_processes_released'))
        self.assertTrue(source.get('queue_released'))

    def test_start_stop_restart_owns_two_pd_sources_and_mixes_builtin(self):
        for _ in range(2):
            started = self.start_live()
            self.assertEqual(started['runtime'], 'puredata')
            self.assertEqual(started['source']['runtime_sources'], 2)
            playing = self.wait_playing()
            self.assertGreater(playing['submitted_frames'], 0)
            self.assertEqual(playing['volume'], 0.0)
            self.assertGreater(playing.get('callback_signal_peak', 0), 0)
            self.assertNotEqual(playing['callback_source_digest'], '0000000000000000')
            stopped = self.client.request('transport.stop', timeout=30)
            self.assert_released(stopped)
            self.assert_pids_reaped(started['source']['owned_pids'])

    def test_natural_completion_digest_control_rms_and_saved_gain_automation(self):
        session = session_fixture()
        session['tracks'] = [session['tracks'][0], builtin_track(0.0)]
        first = session['tracks'][0]
        first['automation'] = [{
            'effect_id': 'live-pd-one-trim', 'parameter': 'gain', 'interpolation': 'step',
            'points': [{'frame': 36_000, 'value': 0.25}],
        }]
        self.client.request('session.replace', {'session': session, 'expected_revision': self.revision})
        self.revision = self.client.request('session.inspect')['revision']
        started = self.start_live(seconds=1.0)
        deadline = time.monotonic() + 16
        final = None
        while time.monotonic() < deadline:
            final = self.client.request('transport.status', timeout=3)
            if final.get('state') == 'stopped':
                break
            time.sleep(.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        source = final['source']
        self.assertEqual(source['source_frames'], 48_000)
        self.assertEqual(final['submitted_frames'], 48_000)
        self.assertEqual(final['live_source_underruns'], 0)
        self.assertEqual(source['source_digest'], final['callback_source_digest'])
        self.assertEqual(len(source['rms_quarters']), 4)
        # Quarter zero confirms source and effect gain are applied. The saved
        # amplitude steps at frame 24,576 and the gain lane at frame 36,000.
        quarters = source['rms_quarters']
        self.assertAlmostEqual(quarters[0], .1 * .5 * .5 / (2 ** .5), delta=.0002)
        self.assertAlmostEqual(quarters[1], quarters[0], delta=0.0002)
        self.assertLess(quarters[2], quarters[1] * 0.8)
        self.assertAlmostEqual(quarters[3], .05 * .5 * .25 / (2 ** .5), delta=.0002)
        self.assert_pids_reaped(started['source']['owned_pids'])

    def test_exact_short_and_partial_block_source_durations_complete_cleanly(self):
        for frames in (48, 48_001):
            with self.subTest(source_frames=frames):
                self.client.request('session.replace', {
                    'session': session_with_pd_duration(frames), 'expected_revision': self.revision,
                })
                self.revision = self.client.request('session.inspect')['revision']
                started = self.start_live(seconds=frames / 48_000)
                deadline = time.monotonic() + 12
                final = None
                while time.monotonic() < deadline:
                    final = self.client.request('transport.status', timeout=3)
                    if final.get('state') == 'stopped':
                        break
                    time.sleep(.025)
                self.assertIsNotNone(final)
                self.assert_released(final)
                self.assertEqual(final['submitted_frames'], frames)
                self.assertEqual(final['source']['source_frames'], frames)
                self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
                self.assert_pids_reaped(started['source']['owned_pids'])

    def test_playback_duration_shorter_and_longer_than_source_is_exact(self):
        cases = ((48_000, .5, 24_000), (48_000, 1.5, 72_000))
        for source_frames, seconds, expected_frames in cases:
            with self.subTest(seconds=seconds):
                self.client.request('session.replace', {
                    'session': session_with_pd_duration(source_frames), 'expected_revision': self.revision,
                })
                self.revision = self.client.request('session.inspect')['revision']
                started = self.start_live(seconds=seconds)
                deadline = time.monotonic() + 12
                final = None
                while time.monotonic() < deadline:
                    final = self.client.request('transport.status', timeout=3)
                    if final.get('state') == 'stopped':
                        break
                    time.sleep(.05)
                self.assertIsNotNone(final)
                self.assert_released(final)
                self.assertEqual(final['submitted_frames'], expected_frames)
                self.assertEqual(final['source']['source_frames'], expected_frames)
                self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
                if expected_frames > source_frames:
                    self.assertAlmostEqual(final['source']['rms_quarters'][-1], .08 / (2 ** .5), delta=.0001)
                self.assert_pids_reaped(started['source']['owned_pids'])

    @unittest.skipUnless(CSOUND_LIBRARY and Path(CSOUND_LIBRARY).is_absolute() and Path(CSOUND_LIBRARY).is_file(),
                         'set DAW_CSOUND_LIBRARY to an installed absolute Csound library for mixed coverage')
    def test_mixed_pd_and_csound_sources_complete_muted(self):
        from examples.csound_tracks_demo import track as csound_track

        session = session_fixture()
        session['tracks'] = [session['tracks'][0], csound_track('live-csound-mixed', 330.0, .06)]
        self.client.request('session.replace', {'session': session, 'expected_revision': self.revision})
        self.revision = self.client.request('session.inspect')['revision']
        started = self.start_live(seconds=1.0)
        self.assertEqual(started['runtime'], 'mixed')
        self.assertEqual(started['source']['runtime_sources'], 2)
        deadline = time.monotonic() + 16
        final = None
        while time.monotonic() < deadline:
            final = self.client.request('transport.status', timeout=3)
            if final.get('state') == 'stopped':
                break
            time.sleep(.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        self.assertEqual(final['submitted_frames'], 48_000)
        self.assertEqual(final['source']['source_frames'], 48_000)
        self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
        self.assert_pids_reaped(started['source']['owned_pids'])

    @unittest.skipUnless(SCSYNTH and Path(SCSYNTH).is_absolute() and Path(SCSYNTH).is_file(),
                         'set DAW_SCSYNTH to an installed absolute scsynth for mixed-runtime coverage')
    def test_mixed_pd_and_supercollider_sources_complete_muted(self):
        from examples.supercollider_tracks_demo import make_track as sc_track

        session = session_fixture()
        session['tracks'] = [session['tracks'][0], sc_track('live-sc-mixed', 330.0, .06, .5, 1.0, 1.0)]
        self.client.request('session.replace', {'session': session, 'expected_revision': self.revision})
        self.revision = self.client.request('session.inspect')['revision']
        started = self.start_live(seconds=1.0)
        self.assertEqual(started['runtime'], 'mixed')
        self.assertEqual(started['source']['runtime_sources'], 2)
        deadline = time.monotonic() + 16
        final = None
        while time.monotonic() < deadline:
            final = self.client.request('transport.status', timeout=3)
            if final.get('state') == 'stopped':
                break
            time.sleep(.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        self.assertEqual(final['submitted_frames'], 48_000)
        self.assertEqual(final['source']['source_frames'], 48_000)
        self.assertEqual(final['source']['source_digest'], final['callback_source_digest'])
        self.assert_pids_reaped(started['source']['owned_pids'])

    def assert_no_engine_children(self):
        children = subprocess.run(['/usr/bin/pgrep', '-P', str(self.client.process.pid)],
                                  capture_output=True, text=True, timeout=2, check=False)
        self.assertNotEqual(children.returncode, 0, children.stdout)

    def test_invalid_mode_rate_and_effects_preserve_model_revision_and_create_no_workers(self):
        invalid_effect = session_fixture()
        invalid_effect['tracks'][0]['effects'].append({'kind': 'unknown', 'id': 'bad'})
        legacy_schema = session_fixture()
        legacy_schema['schema_version'] = 7
        for bad_session in (invalid_effect, legacy_schema):
            error = self.client.request_error('session.replace', {'session': bad_session})
            self.assertIn(error.code, ('invalid_session', 'invalid_params'))
            self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
        for params in (
                {'seconds': 1.0, 'volume': 0.0, 'source_mode': 'unknown'},
                {'seconds': 10.1, 'volume': 0.0, 'source_mode': 'live'}):
            error = self.client.request_error('transport.play', params)
            self.assertEqual(error.code, 'invalid_params')
            self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
            self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
            self.assert_no_engine_children()

        wrong_rate = session_fixture()
        wrong_rate['sample_rate'] = 44_100
        self.client.request('session.replace', {'session': wrong_rate, 'expected_revision': self.revision})
        self.revision = self.client.request('session.inspect')['revision']
        committed = self.client.request('session.inspect')
        error = self.client.request_error('transport.play', {
            'seconds': 1.0, 'volume': 0.0, 'source_mode': 'live',
        })
        self.assertIn(error.code, ('invalid_params', 'audio_error', 'audio_unavailable'))
        self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
        self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
        self.assert_no_engine_children()

        # This installed AU effect is valid session data, but live Pd only accepts
        # gain effects. The failed play must preserve the committed model.
        valid_but_unsupported = session_fixture()
        valid_but_unsupported['tracks'][0]['effects'] = [{
            'kind': 'au', 'id': 'lowpass', 'bypass': False,
            'component_type': 'aufx', 'component_subtype': 'lpas',
            'component_manufacturer': 'appl', 'state_hex': '',
            'parameters': [{'id': 0, 'value': 10_000.0}],
        }]
        self.client.request('session.replace', {
            'session': valid_but_unsupported, 'expected_revision': self.revision,
        })
        self.revision = self.client.request('session.inspect')['revision']
        committed = self.client.request('session.inspect')
        error = self.client.request_error('transport.play', {
            'seconds': 1.0, 'volume': 0.0, 'source_mode': 'live',
        })
        self.assertIn(error.code, ('audio_unavailable', 'audio_error'))
        self.assertEqual(self.client.request('session.inspect')['revision'], self.revision)
        self.assertEqual(self.client.request('session.inspect'), committed)
        self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
        self.assert_no_engine_children()

    def test_failed_owned_worker_start_rolls_back_transport_and_revision(self):
        failed_worker = Path(self.tempdir.name) / 'early-exit-worker.py'
        failed_worker.write_text('#!/usr/bin/env python3\nraise SystemExit(23)\n')
        failed_worker.chmod(0o755)
        self.use_stream_worker(failed_worker)
        revision = self.client.request('session.inspect')['revision']
        error = self.client.request_error('transport.play', {
            'seconds': 1.0, 'volume': 0.0, 'source_mode': 'live',
        })
        self.assertIn(error.code, ('audio_error', 'runtime_error'))
        self.assertEqual(self.client.request('session.inspect')['revision'], revision)
        self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
        self.assert_no_engine_children()

    def test_invalid_compiled_report_and_startup_timeout_are_bounded(self):
        for mode in ('bad-compiled-json', 'compiled-timeout'):
            with self.subTest(mode=mode):
                self.use_stream_worker(self.write_fake_stream_worker(mode))
                revision = self.client.request('session.inspect')['revision']
                started_at = time.monotonic()
                error = self.client.request_error('transport.play', {
                    'seconds': 1.0, 'volume': 0.0, 'source_mode': 'live',
                })
                self.assertIn(error.code, ('audio_error', 'runtime_error'))
                self.assertLess(time.monotonic() - started_at, 9.0)
                self.assertEqual(self.client.request('session.inspect')['revision'], revision)
                self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
                self.assert_no_engine_children()

    def test_bad_completed_report_and_postfinal_worker_exit_do_not_report_success(self):
        session = session_fixture()
        session['tracks'] = [session['tracks'][0], builtin_track(0.0)]
        for mode in ('bad-completed-report', 'postfinal-worker-exit'):
            with self.subTest(mode=mode):
                self.use_stream_worker(self.write_fake_stream_worker(mode), session)
                revision = self.client.request('session.inspect')['revision']
                try:
                    self.client.request('transport.play', {
                        'seconds': 1.0, 'volume': 0.0, 'source_mode': 'live',
                    }, timeout=20)
                except ProtocolError as error:
                    self.assertIn(error.code, ('audio_error', 'runtime_error'))
                else:
                    deadline = time.monotonic() + 8
                    final = None
                    while time.monotonic() < deadline:
                        final = self.client.request('transport.status', timeout=3)
                        if final.get('state') == 'stopped' and final.get('error'):
                            break
                        time.sleep(.025)
                    self.assertIsNotNone(final)
                    self.assertEqual(final['state'], 'stopped')
                    self.assertEqual(final['error']['code'], 'runtime_error')
                    self.assertTrue(final.get('resources_released'))
                self.assertEqual(self.client.request('session.inspect')['revision'], revision)
                self.assertEqual(self.client.request('transport.status')['state'], 'stopped')
                self.assert_no_engine_children()

    def test_live_pd_receiver_edit_and_timeline_commands_are_rejected_without_stopping(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        commands = (
            ('transport.pause', {}, 'audio_error'),
            ('transport.resume', {}, 'audio_error'),
            ('transport.seek', {'frame': 0}, 'audio_error'),
            ('transport.loop', {'region': {'start_frame': 0, 'end_frame': 48_000}}, 'audio_error'),
            ('source.set_control', {'expected_revision': self.revision, 'track_id': 'live-pd-one',
                                    'control_name': '$0-frequency', 'values': [660.0]}, 'invalid_params'),
        )
        for method, params, code in commands:
            with self.subTest(method=method):
                before = self.client.request('transport.status')
                error = self.client.request_error(method, params)
                self.assertEqual(error.code, code)
                after = self.client.request('transport.status')
                self.assertEqual(before['state'], 'playing')
                self.assertEqual(after['state'], 'playing')
        stopped = self.client.request('transport.stop', timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started['source']['owned_pids'])

    def test_owned_producer_death_stops_transport_and_reaps_all_processes(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        pids = [int(pid) for pid in started['source']['owned_pids']]
        direct = subprocess.run(['/usr/bin/pgrep', '-P', str(self.client.process.pid)],
                                capture_output=True, text=True, timeout=2, check=False)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertTrue(set(pids).issubset({int(line) for line in direct.stdout.splitlines()}))
        os.kill(pids[0], signal.SIGKILL)
        deadline = time.monotonic() + 12
        failed = None
        while time.monotonic() < deadline:
            failed = self.client.request('transport.status', timeout=3)
            if failed.get('state') == 'stopped' and failed.get('error'):
                break
            time.sleep(.05)
        self.assertIsNotNone(failed)
        self.assertEqual(failed['state'], 'stopped')
        self.assertTrue(failed.get('resources_released'))
        self.assertEqual(failed['error']['code'], 'runtime_error')
        self.assert_pids_reaped(pids)

    def test_protocol_eof_releases_queues_and_owned_processes(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        pids = started['source']['owned_pids']
        self.assertEqual(self.client.close_eof(timeout=30), 0)
        self.assert_pids_reaped(pids)


if __name__ == '__main__':
    unittest.main()

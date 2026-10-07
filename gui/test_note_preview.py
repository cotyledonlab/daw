"""Actual HTTP/JSONL/DSP note previews preserve the running project."""
import http.client
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

from examples.audio_project_workflow_demo import Client
from examples.pd_instrument_workflow_demo import instrument_fixture


def fixture():
    return {'schema_version': 11, 'sample_rate': 48000, 'tempo_milli_bpm': 120000,
            'tracks': [{'id': 'lead', 'mode': 'sequenced',
                        'device': {'kind': 'sine', 'frequency_hz': 220, 'gain': .4},
                        'clips': [], 'effects': [], 'automation': [],
                        'mixer': {'gain': .6, 'pan': 1, 'mute': True, 'solo': True}}]}


class NotePreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = Client().__enter__()
        self.client.replace(fixture())

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def params(self, **changes):
        return {**{'expected_revision': self.client.inspect()['revision'], 'track_id': 'lead',
                   'frequency_hz': 440, 'velocity': .5}, **changes}

    def raw(self, data, authenticated=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.client.server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['X-DAW-Token'] = self.client.server.token
        connection.request('POST', '/api/note/preview', json.dumps(data), headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_actual_wav_has_saved_pan_velocity_pitch_and_bounded_frames(self):
        before = self.client.inspect()
        original = self.client.request('POST', '/api/render', {'seconds': .5})[0]
        body, headers = self.client.request('POST', '/api/note/preview', self.params())
        self.assertEqual(headers['Content-Type'], 'audio/wav')
        self.assertEqual(headers['X-Clipped-Frames'], '0')
        with wave.open(io.BytesIO(body)) as wav:
            self.assertEqual((wav.getnframes(), wav.getframerate(), wav.getnchannels()), (24000, 48000, 2))
            samples = struct.unpack('<48000h', wav.readframes(24000))
        self.assertTrue(all(x == 0 for x in samples[::2]))
        self.assertGreater(max(samples[1::2]), 100)
        right = samples[1:24000:2]
        crossings = sum(a <= 0 < b for a, b in zip(right, right[1:]))
        self.assertAlmostEqual(crossings / .25, 440, delta=5)
        self.assertEqual(self.client.request('POST', '/api/note/preview', self.params())[0], body)
        self.assertNotEqual(self.client.request('POST', '/api/note/preview', self.params(frequency_hz=880))[0], body)
        self.assertEqual(self.client.inspect(), before)
        self.assertEqual(self.client.request('POST', '/api/render', {'seconds': .5})[0], original)

    def test_auth_strict_validation_and_stale_requests_preserve_project(self):
        before = self.client.inspect()
        self.assertEqual(self.raw(self.params(), authenticated=False)[0], 403)
        for changes in ({'path': '/tmp/user.wav'}, {'seconds': 1}, {'velocity': True},
                        {'velocity': -1}, {'frequency_hz': 24000}, {'frequency_hz': 0},
                        {'track_id': 'unknown'}, {'expected_revision': '0'},
                        {'expected_revision': 1}, {'frequency_hz': float('nan')}):
            status, body = self.raw(self.params(**changes))
            self.assertEqual(status, 422, body)
            self.assertEqual(self.client.inspect(), before)
        unsupported = fixture()
        unsupported['tracks'][0]['device'] = {'kind': 'audio', 'gain': .4}
        self.client.replace(unsupported)
        before = self.client.inspect()
        self.assertEqual(self.raw(self.params())[0], 422)
        self.assertEqual(self.client.inspect(), before)

    def test_playing_and_paused_native_transport_reject_without_changes(self):
        if not self.client.json('GET', '/api/capabilities')['live_audio']:
            self.skipTest('native audio build required')
        before = self.client.inspect()
        try:
            self.client.json('POST', '/api/transport', {'action': 'play', 'until_stopped': True, 'volume': 0})
            for state in ('playing', 'paused'):
                if state == 'paused':
                    self.client.json('POST', '/api/transport', {'action': 'pause'})
                deadline = time.monotonic() + 5
                while self.client.json('GET', '/api/transport')['state'] != state:
                    self.assertLess(time.monotonic(), deadline, f'transport failed to reach {state}')
                    time.sleep(.02)
                status, body = self.raw(self.params())
                self.assertEqual(status, 422, body)
                self.assertIn(b'stop transport', body)
                self.assertEqual(self.client.json('GET', '/api/transport')['state'], state)
                self.assertEqual(self.client.inspect(), before)
        finally:
            self.client.json('POST', '/api/transport', {'action': 'stop'})

    @unittest.skipUnless(os.environ.get('DAW_LIBPD_LIBRARY') and Path(os.environ['DAW_LIBPD_LIBRARY']).is_file(), 'installed libpd required')
    def test_missing_pd_runtime_preserves_project_assets_and_output(self):
        library = Path(os.environ['DAW_LIBPD_LIBRARY']).resolve()
        with tempfile.TemporaryDirectory(prefix='daw-preview-runtime-') as directory:
            alias = Path(directory) / 'libpd.dylib'
            alias.symlink_to(library)
            with patch.dict(os.environ, {'DAW_LIBPD_LIBRARY': str(alias)}):
                with Client() as missing:
                    missing.replace(instrument_fixture())
                    before = missing.inspect()
                    alias.unlink()
                    params = {'expected_revision': before['revision'], 'track_id': 'pd-lead',
                              'frequency_hz': 440, 'velocity': .5}
                    with self.assertRaises(RuntimeError):
                        missing.request('POST', '/api/note/preview', params)
                    self.assertEqual(missing.inspect(), before)

    @unittest.skipUnless(os.environ.get('DAW_LIBPD_LIBRARY') and Path(os.environ['DAW_LIBPD_LIBRARY']).is_file(), 'installed libpd required')
    def test_real_pd_preview_keeps_controls_and_project(self):
        session = instrument_fixture()
        self.client.replace(session)
        track_id = 'pd-lead'
        before = self.client.inspect()
        body, _ = self.client.request('POST', '/api/note/preview', self.params(track_id=track_id))
        with wave.open(io.BytesIO(body)) as wav:
            self.assertEqual(wav.getnframes(), 24000)
            self.assertTrue(any(wav.readframes(24000)))
        self.assertEqual(self.client.inspect(), before)


if __name__ == '__main__':
    unittest.main()

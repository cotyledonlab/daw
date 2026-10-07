"""Real HTTP mixer edits, audible output and portable mixed-project reopen."""
import copy
import http.client
import io
import json
import struct
import unittest
import wave

from examples.audio_project_workflow_demo import Client, ROOT, source_wav


def samples(body):
    with wave.open(io.BytesIO(body), 'rb') as audio:
        assert audio.getnchannels() == 2 and audio.getsampwidth() == 2
        data = audio.readframes(audio.getnframes())
    return struct.iter_unpack('<hh', data)


class MixerWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = Client().__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def fixture(self):
        session = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
        session['schema_version'] = 10
        for track in session['tracks']:
            track['mixer'] = {'gain': 1, 'pan': 0, 'mute': False, 'solo': False}
        return session

    def raw_replace(self, session, revision):
        server = self.client.server
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        connection.request('POST', '/api/session',
                           json.dumps({'session': session, 'expected_revision': revision}),
                           {'X-DAW-Token': server.token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def export(self, client=None):
        body, headers = (client or self.client).request('POST', '/api/render', {'seconds': 1})
        self.assertEqual(headers['X-Clipped-Frames'], '0')
        return body

    def test_audio_solo_balance_zip_reopen_undo_and_mute_change_the_real_mix(self):
        session = self.fixture()
        original_notes = copy.deepcopy([track['clips'] for track in session['tracks']])
        self.client.replace(session)
        imported = self.client.json('POST', '/api/audio/import', source_wav(), binary=True,
                                    metadata={'expected_revision': self.client.inspect()['revision'],
                                              'track_id': 'audio', 'clip_id': 'source', 'start_frame': 0})
        session = imported['session']
        self.assertEqual(session['schema_version'], 10)
        session['tracks'][-1]['mixer'] = {'gain': 0.5, 'pan': -1, 'mute': False, 'solo': True}
        self.client.replace(session)
        mixed = self.export()
        pairs = list(samples(mixed))
        self.assertGreater(max(abs(left) for left, _ in pairs), 100)
        self.assertTrue(all(right == 0 for _, right in pairs), 'Solo audio hard-left must silence right')
        self.assertEqual([track['clips'] for track in session['tracks'][:3]], original_notes)

        # An unrelated note edit must survive the real checked replacement and
        # subsequent portable ZIP path without dropping any saved mixer.
        mixers = copy.deepcopy([track.get('mixer') for track in session['tracks']])
        session['tracks'][0]['clips'][0]['notes'][0]['velocity'] = 0.37
        self.client.replace(session)
        self.assertEqual([track.get('mixer') for track in self.client.inspect()['session']['tracks']], mixers)
        self.assertEqual(self.export(), mixed)  # Non-solo track remains inaudible.

        bundle, _ = self.client.request('GET', '/api/project')
        with Client() as fresh:
            reopened = fresh.json('POST', '/api/project', bundle, binary=True,
                                  metadata={'expected_revision': fresh.inspect()['revision']})
            self.assertEqual(reopened['session']['schema_version'], 10)
            self.assertEqual([t.get('mixer') for t in reopened['session']['tracks']],
                             [t.get('mixer') for t in session['tracks']])
            self.assertEqual(self.export(fresh), mixed)

        muted = copy.deepcopy(session)
        muted['tracks'][-1]['mixer']['mute'] = True
        self.client.replace(muted)
        self.assertTrue(all(left == right == 0 for left, right in samples(self.export())))
        self.client.replace(session)  # Same checked snapshot replacement used by Undo.
        self.assertEqual(self.export(), mixed)

    def test_bad_or_stale_mixer_edits_and_legacy_fields_preserve_the_applied_project(self):
        session = self.fixture()
        self.client.replace(session)
        before = self.client.inspect()
        candidates = []
        for key, value in [('gain', 2.01), ('pan', -1.01), ('solo', 1), ('mute', 'yes')]:
            invalid = copy.deepcopy(session)
            invalid['tracks'][0]['mixer'][key] = value
            candidates.append(invalid)
        legacy = copy.deepcopy(session)
        legacy['schema_version'] = 9
        candidates.append(legacy)
        null = copy.deepcopy(session)
        null['tracks'][0]['mixer'] = None
        candidates.append(null)
        for candidate in candidates:
            status, body = self.raw_replace(candidate, before['revision'])
            self.assertEqual(status, 422, body)
            self.assertEqual(self.client.inspect(), before)
        changed = copy.deepcopy(session)
        changed['tracks'][0]['mixer']['pan'] = 0.25
        status, body = self.raw_replace(changed, '0')
        self.assertEqual(status, 422, body)
        self.assertEqual(self.client.inspect(), before)

    def test_mixer_asset_is_served_and_schema10_foreign_device_is_rejected_before_preparation(self):
        body, headers = self.client.request('GET', '/mixer.js')
        self.assertIn('javascript', headers['Content-Type'])
        self.assertTrue(body)
        before = self.client.inspect()
        session = self.fixture()
        session['tracks'][0]['device'] = {'kind': 'puredata', 'program': '#N canvas;',
                                         'abstractions': [], 'controls': [],
                                         'duration_frames': 48000, 'gain': 0.2}
        status, body = self.raw_replace(session, before['revision'])
        self.assertEqual(status, 422, body)
        self.assertEqual(self.client.inspect(), before)


if __name__ == '__main__':
    unittest.main()

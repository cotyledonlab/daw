"""Actual bridge/DSP acceptance of schema11 processing and portable projects."""
import copy
import http.client
import json
import unittest

from examples.audio_project_workflow_demo import Client
from examples.processing_workflow_demo import import_fixture
from examples.song_workflow_demo import portable_data


class ProcessingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = Client().__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def export(self, client=None):
        body, headers = (client or self.client).request('POST', '/api/render', {'seconds': 4})
        self.assertEqual(headers['X-Clipped-Frames'], '0')
        return body

    def raw_replace(self, session, revision):
        server = self.client.server
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        connection.request('POST', '/api/session', json.dumps({'session': session, 'expected_revision': revision}),
                           {'X-DAW-Token': server.token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_processing_controls_bypass_undo_and_zip_reopen_preserve_the_song(self):
        session = import_fixture(self.client)
        original = self.export()
        saved_mixers = [t['mixer'] for t in session['tracks']]
        saved_clips = [t['clips'] for t in session['tracks']]
        saved_automation = copy.deepcopy(session['tracks'][2]['automation'])
        for track_index, effect_index, key, value in [(1, 1, 'cutoff_hz', 150), (2, 1, 'mix', 0.8),
                                                     (3, 0, 'bypass', True), (3, 1, 'bypass', True)]:
            changed = copy.deepcopy(session)
            changed['tracks'][track_index]['effects'][effect_index][key] = value
            self.client.replace(changed)
            self.assertNotEqual(self.export(), original, key)
            applied = self.client.inspect()['session']
            self.assertEqual([t['mixer'] for t in applied['tracks']], saved_mixers)
            self.assertEqual([t['clips'] for t in applied['tracks']], saved_clips)
            self.assertEqual(applied['tracks'][2]['automation'], saved_automation)
            self.client.replace(session)  # Checked snapshot replacement used by Undo.
            self.assertEqual(self.export(), original)
        bundle, _ = self.client.request('GET', '/api/project')
        with Client() as fresh:
            fresh.json('POST', '/api/project', bundle, binary=True,
                       metadata={'expected_revision': fresh.inspect()['revision']})
            self.assertEqual(portable_data(fresh.inspect()['session']), portable_data(session))
            self.assertEqual(self.export(fresh), original)

    def test_invalid_legacy_and_stale_processing_replacements_are_transactional(self):
        session = import_fixture(self.client)
        before = self.client.inspect()
        candidates = []
        for track_index, effect_index, key, value in [(1, 1, 'cutoff_hz', 24000), (1, 1, 'cutoff_hz', 19),
                                                     (2, 1, 'feedback', 1), (2, 1, 'time_ms', 2001),
                                                     (2, 1, 'mix', -0.01), (2, 1, 'bypass', 1)]:
            invalid = copy.deepcopy(session)
            invalid['tracks'][track_index]['effects'][effect_index][key] = value
            candidates.append(invalid)
        invalid = copy.deepcopy(session)
        invalid['schema_version'] = 10
        candidates.append(invalid)
        invalid = copy.deepcopy(session)
        invalid['tracks'][1]['effects'][1]['unknown'] = 0
        candidates.append(invalid)
        for candidate in candidates:
            status, body = self.raw_replace(candidate, before['revision'])
            self.assertEqual(status, 422, body)
            self.assertEqual(self.client.inspect(), before)
        changed = copy.deepcopy(session)
        changed['tracks'][1]['effects'][1]['cutoff_hz'] = 500
        status, body = self.raw_replace(changed, '0')
        self.assertEqual(status, 422, body)
        self.assertEqual(self.client.inspect(), before)


if __name__ == '__main__':
    unittest.main()

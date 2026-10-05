"""One HTTP bridge workflow acceptance test using the shipped musical fixture.

Requires a freshly built schema-9 engine. Run:
    python3 -m unittest gui.test_musical_workflow
"""
import copy
import http.client
import json
import threading
import unittest

from examples.musical_workflow_demo import FIXTURE, ROOT, SECONDS, check_wav
from gui.server import Server


class MusicalWorkflowTests(unittest.TestCase):
    def setUp(self):
        binary = ROOT / 'target/debug/daw'
        if not binary.is_file():
            self.fail('Build the schema-9 engine before running workflow acceptance.')
        self.server = Server(binary, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=120)
        headers = {'X-DAW-Token': self.server.token, 'Host': f'127.0.0.1:{self.server.server_port}'}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def inspect(self):
        status, body, _ = self.request('GET', '/api/session/inspect')
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def replace(self, session, revision):
        return self.request('POST', '/api/session', {'session': session, 'expected_revision': revision})

    def test_checked_edit_undo_json_reopen_and_deterministic_full_song_export(self):
        fixture = json.loads(FIXTURE.read_text())
        initial = self.inspect()
        status, body, _ = self.replace(fixture, initial['revision'])
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), fixture)
        applied = self.inspect()
        invalid = copy.deepcopy(fixture)
        invalid['tracks'][2]['clips'][0]['notes'][0]['frequency_hz'] = 440
        for candidate, revision in [(invalid, applied['revision']), (fixture, initial['revision'])]:
            status, body, _ = self.replace(candidate, revision)
            self.assertEqual(status, 422, body)
            self.assertEqual(self.inspect(), applied)
        edited = copy.deepcopy(fixture)
        edited['tracks'][0]['clips'][0]['notes'][0]['velocity'] = 0.74
        status, body, _ = self.replace(edited, applied['revision'])
        self.assertEqual(status, 200, body)
        # Snapshot undo, matching the GUI history mechanism.
        status, body, _ = self.replace(fixture, self.inspect()['revision'])
        self.assertEqual(status, 200, body)
        status, body, _ = self.request('GET', '/api/session')
        self.assertEqual(status, 200, body)
        downloaded = json.loads(body)
        self.assertEqual(downloaded, fixture)
        devices = [track['device'] for track in downloaded['tracks']]
        exports = []
        for reopen in [False, True]:
            if reopen:
                # Browser save/reopen is JSON download/upload, without server file paths.
                status, body, _ = self.replace(json.loads(json.dumps(downloaded)), self.inspect()['revision'])
                self.assertEqual(status, 200, body)
                self.assertEqual([track['device'] for track in json.loads(body)['tracks']], devices)
            status, body, headers = self.request('POST', '/api/render', {'seconds': SECONDS})
            self.assertEqual(status, 200, body[:200])
            self.assertEqual(headers['Content-Type'], 'audio/wav')
            self.assertEqual(headers['X-Clipped-Frames'], '0')
            check_wav(body)
            exports.append(body)
        self.assertEqual(exports[0], exports[1])


if __name__ == '__main__':
    unittest.main()

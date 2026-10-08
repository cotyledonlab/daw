"""Origin isolation, unchanged engine ownership, uploads/downloads and progress relay."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from gui.server import Server, ROOT
from gui.studio import StudioError
from gui.tailscale_ui import Gateway, MAX_UPLOAD, validate_origin

ORIGIN = 'https://studio.tailtest.ts.net'
HOST = 'studio.tailtest.ts.net'


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.daw = Server(ROOT / 'target/debug/daw')
        self.gateway = Gateway(self.daw.server_port, ORIGIN)
        self.threads = []
        for server in (self.daw, self.gateway):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)

    def tearDown(self):
        for server, thread in zip((self.gateway, self.daw), reversed(self.threads)):
            server.shutdown()
            thread.join(5)
            server.server_close()

    def request(self, path, body=None, *, host=HOST, origin=ORIGIN, token=True, extra=None):
        client = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        headers = {'Host': host, 'Origin': origin, 'Content-Type': 'application/json'}
        if token:
            headers['X-DAW-Token'] = self.daw.token
        headers.update(extra or {})
        client.request('GET' if body is None else 'POST', path, body, headers)
        response = client.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        client.close()
        return result

    def test_only_exact_origin_is_allowed_and_forwarded_headers_cannot_override_it(self):
        for kwargs in ({'host': 'attacker.example'}, {'origin': 'https://attacker.example'},
                       {'origin': 'http://studio.tailtest.ts.net'}, {'origin': 'null'},
                       {'host': HOST+':443'},
                       {'host': 'attacker.example', 'extra': {'X-Forwarded-Host': HOST, 'X-Forwarded-Proto': 'https'}}):
            with patch.object(self.daw.engine, 'call') as call:
                self.assertEqual(self.request('/api/session/inspect', **kwargs)[0], 403)
                call.assert_not_called()
        self.assertEqual(self.request('/api/session/inspect', token=False)[0], 403)
        self.assertEqual(self.request('/api/session/inspect')[0], 200)

    def test_root_preserves_csp_token_and_exact_downloads(self):
        status, root, headers = self.request('/', token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.daw.token.encode(), root)
        self.assertIn('nonce-'+self.daw.token, headers['Content-Security-Policy'])
        session = {'schema_version': 1, 'sample_rate': 48000, 'tracks': [
            {'id': 'tone', 'device': {'kind': 'sine', 'frequency_hz': 440.123, 'gain': 0.125}}]}
        self.assertEqual(self.request('/api/session', json.dumps({'session': session}))[0], 200)
        status, wire, headers = self.request('/api/render', json.dumps({'seconds': 0.01}))
        self.assertEqual(status, 200)
        self.assertTrue(wire.startswith(b'RIFF'))
        self.assertEqual(len(wire), int(headers['Content-Length']))
        self.assertIn('Content-Disposition', headers)
        before = self.request('/api/session/inspect')[1]
        self.assertEqual(self.request('/api/project', b'not a zip', extra={'Content-Type': 'application/zip'})[0], 422)
        self.assertEqual(self.request('/api/session/inspect')[1], before)
        # Stopping the gateway leaves the existing engine and its revision intact.
        self.gateway.shutdown()
        self.assertEqual(self.daw.engine.call('session.inspect'), json.loads(before))

    def test_stream_progress_is_not_buffered_and_failed_batch_changes_nothing(self):
        release = threading.Event()
        before = self.daw.engine.call('session.inspect')
        def prompt(_data, snapshot, progress):
            progress({'type': 'summary', 'role': 'producer', 'message': 'Proposal'})
            release.wait(3)
            raise StudioError('No studio edits applied.')
        with patch.object(self.daw.studio, 'prompt', side_effect=prompt):
            client = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=2)
            client.request('POST', '/api/studio/prompt', json.dumps({'expected_revision': '0'}),
                           {'Host': HOST, 'Origin': ORIGIN, 'X-DAW-Token': self.daw.token,
                            'Content-Type': 'application/json', 'Accept': 'application/x-ndjson'})
            response = client.getresponse()
            try:
                self.assertEqual(json.loads(response.readline())['message'], 'Proposal')
                self.assertEqual(self.daw.engine.call('session.inspect'), before)
            finally:
                release.set()
            self.assertEqual(json.loads(response.readline())['type'], 'error')
            self.assertEqual(response.read(), b'')
            client.close()
        self.assertEqual(self.daw.engine.call('session.inspect'), before)

    def test_invalid_framing_and_oversized_requests_reject_before_upstream(self):
        for headers, expected in [({'Content-Length': str(MAX_UPLOAD+1)}, 413),
                                  ({'Content-Length': '-1'}, 400),
                                  ({'Transfer-Encoding': 'chunked'}, 400)]:
            with patch.object(self.daw.engine, 'call') as upstream:
                self.assertEqual(self.request('/api/session', b'{}', extra=headers)[0], expected)
                upstream.assert_not_called()
        client = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        client.putrequest('GET', '/', skip_host=True)
        client.putheader('Host', HOST)
        client.putheader('Host', 'attacker.example')
        client.endheaders()
        response = client.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        client.close()

    def test_origin_configuration_is_private_https_and_root_only(self):
        for invalid in ('http://studio.tailtest.ts.net', 'https://example.com', ORIGIN+'/',
                        ORIGIN+'/path', ORIGIN+'?q=1', ORIGIN+':65536', 'https://user@studio.tailtest.ts.net'):
            with self.assertRaises(ValueError):
                validate_origin(invalid)
        self.assertEqual(validate_origin(ORIGIN+':8443'), ORIGIN+':8443')
        self.assertEqual(validate_origin(ORIGIN+':443'), ORIGIN)

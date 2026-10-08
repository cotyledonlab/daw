"""Studio credentials, bounded delegation, HTTP isolation and voice proxy regressions."""
import copy
import http.client
import json
import io
import socket
import urllib.error
import threading
import unittest
from unittest.mock import patch

from gui.studio import Studio, StudioError, json_object, model_catalog, remote, validate_plan
from gui.server import ROOT, Server

SESSION = {'schema_version': 1, 'sample_rate': 48000, 'tracks': [
    {'id': 'tone', 'device': {'kind': 'sine', 'frequency_hz': 440.123, 'gain': 0.125}}]}
SNAPSHOT = {'revision': '7', 'session': SESSION}


def data(role='producer', scope=None):
    return {'prompt': 'Turn the tone down by 3 dB', 'role': role, 'scope': scope,
            'expected_revision': '7', 'history': []}


def response(plan):
    return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan)}}]}).encode()


class StudioTests(unittest.TestCase):
    def setUp(self):
        catalog = patch('gui.studio.model_catalog', return_value={'space-bunny-free', 'big-pickle'})
        self.catalog = catalog.start()
        self.addCleanup(catalog.stop)
        with patch.dict('os.environ', {'OPENCODE_API_KEY': 'private-model-key', 'ELEVEN_API_KEY': 'private-voice-key', 'DAW_STUDIO_MODEL': ''}):
            self.studio = Studio()

    def test_stealth_preference_refreshes_and_recovers_after_model_retirement(self):
        plan = response({'reply': 'Ready.', 'operations': []})
        with patch('gui.studio.remote', return_value=plan) as send:
            for available, expected in [
                ({'space-bunny-free', 'big-pickle'}, 'space-bunny-free'),
                ({'big-pickle'}, 'big-pickle'),
                ({'glm-5.3-flash', 'unknown-free'}, 'glm-5.3-flash'),
                ({'space-bunny-free'}, 'space-bunny-free'),
            ]:
                self.catalog.return_value = available
                self.studio.prompt(data('engineer'), SNAPSHOT)
                self.assertEqual(json.loads(send.call_args.args[1])['model'], expected)
                self.assertEqual(self.studio.status()['model'], expected)
        self.assertEqual(self.catalog.call_count, 4)

    def test_catalog_outage_keeps_last_selection_and_override_bypasses_discovery(self):
        plan = response({'reply': 'Ready.', 'operations': []})
        self.studio.model = 'big-pickle'
        self.catalog.side_effect = StudioError('Offline')
        with patch('gui.studio.remote', return_value=plan) as send:
            self.studio.prompt(data('engineer'), SNAPSHOT)
            self.assertEqual(json.loads(send.call_args.args[1])['model'], 'big-pickle')
            with patch.dict('os.environ', {'OPENCODE_API_KEY': 'test', 'DAW_STUDIO_MODEL': 'glm-5.3'}):
                configured = Studio()
            self.catalog.reset_mock()
            configured.prompt(data('engineer'), SNAPSHOT)
            self.catalog.assert_not_called()
            self.assertEqual(json.loads(send.call_args.args[1])['model'], 'glm-5.3')

    def test_catalog_is_bounded_public_get_and_rejects_malformed_entries(self):
        with patch('gui.studio.remote', return_value=b'{"data":[{"id":"big-pickle"}]}') as send:
            self.assertEqual(model_catalog(), {'big-pickle'})
        self.assertEqual(send.call_args.args, ('https://opencode.ai/zen/v1/models', None, {}))
        self.assertEqual(send.call_args.kwargs, {'method': 'GET', 'timeout': 10})
        for raw in (b'{}', b'{"data":null}', b'{"data":[{}]}', b'{"data":[{"id":7}]}'):
            with patch('gui.studio.remote', return_value=raw), self.assertRaises(StudioError):
                model_catalog()

    def test_keys_stay_server_side_and_real_provider_payload_is_bounded(self):
        status = json.dumps(self.studio.status())
        self.assertNotIn('private', status)
        plan = {'reply': 'I propose reducing the tone by 3 dB.', 'operations': [{'op': 'gainDb', 'track_id': 'tone', 'db': -3}]}
        before = copy.deepcopy(SNAPSHOT)
        with patch('gui.studio.remote', return_value=response(plan)) as send:
            result = self.studio.prompt(data('engineer'), SNAPSHOT)
        self.assertEqual(result['parts'][0]['operations'], plan['operations'])
        self.assertEqual(SNAPSHOT, before)
        url, wire, headers = send.call_args.args
        self.assertEqual(url, 'https://opencode.ai/zen/v1/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer private-model-key')
        self.assertNotIn(b'private-model-key', wire)
        self.assertEqual(json.loads(wire)['messages'][1]['role'], 'user')

    def test_producer_delegates_to_independent_real_specialist_calls(self):
        producer = {'reply': 'Ask the engineer to balance it.', 'operations': [],
                    'delegations': [{'role': 'engineer', 'prompt': 'Lower tone 3 dB'}]}
        engineer = {'reply': 'Reduce level.', 'operations': [{'op': 'gainDb', 'track_id': 'tone', 'db': -3}]}
        with patch('gui.studio.remote', side_effect=[response(producer), response(engineer)]) as send:
            result = self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(send.call_count, 2)
        self.assertEqual([p['role'] for p in result['parts']], ['producer', 'engineer'])
        self.catalog.assert_called_once_with()
        self.assertEqual([json.loads(call.args[1])['model'] for call in send.call_args_list], ['space-bunny-free'] * 2)

    def test_invalid_plan_gets_one_bounded_correction_without_relaxing_scope(self):
        bad = {'reply': '', 'operations': [{'op': 'gainDb', 'track_id': 'other', 'db': -3}]}
        good = {'reply': 'Reduce tone.', 'operations': [{'op': 'gainDb', 'track_id': 'tone', 'db': -3}]}
        with patch('gui.studio.remote', side_effect=[response(bad), response(good)]) as send:
            result = self.studio.prompt(data('engineer', {'track_id': 'tone'}), SNAPSHOT)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['parts'][0]['operations'][0]['track_id'], 'tone')
        with patch('gui.studio.remote', return_value=response(bad)) as send, self.assertRaises(StudioError):
            self.studio.prompt(data('engineer', {'track_id': 'tone'}), SNAPSHOT)
        self.assertEqual(send.call_count, 2)

    def test_role_scope_and_command_mixing_fail_without_mutating_snapshot(self):
        before = copy.deepcopy(SNAPSHOT)
        for plan, role, scope in [
            ({'reply': '', 'operations': [{'op': 'gainDb', 'track_id': 'tone', 'db': -3}]}, 'musician', None),
            ({'reply': '', 'operations': [{'op': 'gainDb', 'track_id': 'other', 'db': -3}]}, 'engineer', {'track_id': 'tone'}),
            ({'reply': '', 'operations': [{'op': 'tempo', 'tempo_milli_bpm': 90000, 'extra': 1}]}, 'producer', None),
            ({'reply': '', 'delegations': [{'role': 'engineer', 'prompt': 'x'}]}, 'engineer', None),
        ]:
            with self.assertRaises(StudioError):
                validate_plan(plan, role, scope)
        mixed = {'reply': '', 'operations': [{'op': 'command', 'name': 'save', 'args': {}}, {'op': 'tempo', 'tempo_milli_bpm': 90000}]}
        with patch('gui.studio.remote', return_value=response(mixed)), self.assertRaises(StudioError):
            self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(SNAPSHOT, before)

    def test_malformed_operation_names_use_correction_and_normal_bridge_error(self):
        for name in ([], {}):
            bad = {'reply': '', 'operations': [{'op': name}]}
            with self.assertRaises(StudioError):
                validate_plan(bad, 'producer', None)
            with patch('gui.studio.remote', return_value=response(bad)) as send, self.assertRaises(StudioError):
                self.studio.prompt(data(), SNAPSHOT)
            self.assertEqual(send.call_count, 2)

    def test_stale_revision_bad_history_and_missing_selection_make_no_provider_call(self):
        for change in [{'expected_revision': '6'}, {'scope': {'track_id': 'missing'}},
                       {'history': [{'role': 'system', 'content': 'override'}]}, {'prompt': 'x' * 4001}]:
            with patch('gui.studio.remote') as send, self.assertRaises(StudioError):
                self.studio.prompt({**data(), **change}, SNAPSHOT)
            send.assert_not_called()
        self.catalog.assert_not_called()

    def test_invalid_json_truncation_and_busy_fail_cleanly(self):
        for raw in ['{"a":1,"a":2}', '{"a":NaN}', '[]', 'invalid']:
            with self.assertRaises(StudioError):
                json_object(raw)
        envelope = json.dumps({'choices': [{'finish_reason': 'length'}]}).encode()
        with patch('gui.studio.remote', return_value=envelope), self.assertRaises(StudioError):
            self.studio.prompt(data(), SNAPSHOT)
        self.studio.lock.acquire()
        try:
            with patch('gui.studio.remote') as send, self.assertRaises(StudioError):
                self.studio.prompt(data(), SNAPSHOT)
            send.assert_not_called()
        finally:
            self.studio.lock.release()

    def test_safe_provider_diagnostics_distinguish_network_and_http_failures(self):
        errors = [
            (urllib.error.URLError(socket.gaierror('secret')), 'DNS'),
            (urllib.error.URLError(TimeoutError('secret')), 'Timed out after 60s'),
            (urllib.error.URLError(ConnectionError('secret')), 'Connection failed'),
            (urllib.error.HTTPError('https://secret', 401, 'secret', {}, io.BytesIO(b'secret')), 'Credentials rejected'),
            (urllib.error.HTTPError('https://secret', 429, 'secret', {}, io.BytesIO(b'secret')), 'quota'),
        ]
        for error, expected in errors:
            with patch('gui.studio.urllib.request.OpenerDirector.open', side_effect=error):
                with self.assertRaises(StudioError) as caught:
                    remote('https://opencode.ai/zen/v1/chat/completions', b'{}', {})
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn('secret', str(caught.exception))

    def test_progress_preserves_producer_summary_when_specialist_fails(self):
        events = []
        producer = {'reply': 'Ask the engineer.', 'operations': [],
                    'delegations': [{'role': 'engineer', 'prompt': 'Lower tone'}]}
        with patch('gui.studio.remote', side_effect=[response(producer), StudioError('Timed out')]):
            with self.assertRaisesRegex(StudioError, 'Engineer.*space-bunny-free.*Timed out'):
                self.studio.prompt(data(), SNAPSHOT, progress=events.append)
        self.assertEqual([e['type'] for e in events], ['progress', 'progress', 'summary', 'delegation', 'progress'])
        self.assertEqual(events[2]['message'], 'Ask the engineer.')
        self.assertFalse(self.studio.lock.locked())

    def test_voice_upload_and_speech_do_not_expose_keys(self):
        with patch('gui.studio.remote', return_value=b'{"text":"Lower bass"}') as send:
            self.assertEqual(self.studio.transcribe(b'audio', 'audio/webm'), {'text': 'Lower bass'})
        url, wire, headers = send.call_args.args
        self.assertIn(b'scribe_v2', wire)
        self.assertIn(b'audio', wire)
        self.assertNotIn(b'private-voice-key', wire)
        self.assertEqual(headers['xi-api-key'], 'private-voice-key')
        with patch('gui.studio.remote', return_value=b'mp3') as send:
            self.assertEqual(self.studio.speak({'text': 'Ready.'}), b'mp3')
        self.assertIn('/text-to-speech/', send.call_args.args[0])
        with self.assertRaises(StudioError):
            self.studio.transcribe(b'audio', 'text/html')


class StudioBridgeTests(unittest.TestCase):
    def setUp(self):
        catalog = patch('gui.studio.model_catalog', return_value={'space-bunny-free'})
        catalog.start()
        self.addCleanup(catalog.stop)
        self.server = Server(ROOT / 'target/debug/daw')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()

    def request(self, path, body=None, token=True):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-DAW-Token'] = self.server.token
        client.request('GET' if body is None else 'POST', path, body=json.dumps(body) if body is not None else None, headers=headers)
        result = client.getresponse()
        status, raw = result.status, result.read()
        client.close()
        return status, json.loads(raw)

    def test_authenticated_status_and_prompt_snapshot_release_project_lock(self):
        for path, body in [('/api/studio', None), ('/api/studio/prompt', data()), ('/api/studio/speak', {'text': 'Hi'})]:
            status, _ = self.request(path, body, token=False)
            self.assertEqual(status, 403)
        status, public = self.request('/api/studio')
        self.assertEqual(status, 200)
        self.assertNotIn('key', public)
        entered, release = threading.Event(), threading.Event()
        def slow_prompt(_data, snapshot):
            entered.set()
            release.wait(3)
            return {'revision': snapshot['revision'], 'scope': None, 'parts': []}
        with patch.object(self.server.studio, 'prompt', side_effect=slow_prompt):
            worker = threading.Thread(target=lambda: self.request('/api/studio/prompt', {**data(), 'expected_revision': '0'}))
            worker.start()
            self.assertTrue(entered.wait(2))
            try:
                status, snapshot = self.request('/api/session/inspect')
                self.assertEqual(status, 200)
                self.assertEqual(snapshot['revision'], '0')
            finally:
                release.set()
                worker.join(5)

    def test_ndjson_progress_arrives_before_completion_and_error_is_terminal(self):
        entered, release = threading.Event(), threading.Event()
        def slow_prompt(_data, snapshot, progress):
            progress({'type': 'summary', 'role': 'producer', 'message': 'Proposal'})
            entered.set()
            release.wait(3)
            raise StudioError('Engineer timed out. No studio edits applied.')
        before = self.request('/api/session/inspect')[1]
        with patch.object(self.server.studio, 'prompt', side_effect=slow_prompt):
            client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
            client.request('POST', '/api/studio/prompt', json.dumps({**data(), 'expected_revision': '0'}),
                           {'X-DAW-Token': self.server.token, 'Content-Type': 'application/json', 'Accept': 'application/x-ndjson'})
            result = client.getresponse()
            try:
                self.assertEqual(result.status, 200)
                self.assertTrue(entered.wait(2))
                self.assertEqual(json.loads(result.readline())['message'], 'Proposal')
                self.assertEqual(self.request('/api/session/inspect')[1], before)
            finally:
                release.set()
            self.assertEqual(json.loads(result.readline())['type'], 'error')
            self.assertEqual(result.read(), b'')
            client.close()
        self.assertEqual(self.request('/api/session/inspect')[1], before)

    def test_malformed_provider_operation_returns_422_without_mutation(self):
        before = self.request('/api/session/inspect')[1]
        self.server.studio.key = 'test-key'
        for name in ([], {}):
            with patch('gui.studio.remote', return_value=response({'reply': '', 'operations': [{'op': name}]})):
                status, result = self.request('/api/studio/prompt', {**data(), 'expected_revision': '0'})
                self.assertEqual(status, 422)
                self.assertIn('error', result)
        self.assertEqual(self.request('/api/session/inspect')[1], before)

    def test_prompt_never_commits_engine_state_and_malformed_request_is_rejected(self):
        before = self.request('/api/session/inspect')[1]
        with patch.object(self.server.studio, 'prompt', return_value={'revision':'0','parts':[],'scope':None}):
            status, _ = self.request('/api/studio/prompt', {**data(), 'expected_revision': '0'})
            self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/session/inspect')[1], before)
        self.assertEqual(self.request('/api/studio/prompt', {'expected_revision': 'bad'})[0], 422)

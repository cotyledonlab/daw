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

from gui.studio import Studio, StudioError, json_object, plan_object, model_catalog, remote, validate_plan
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

    def test_cheap_flash_preference_preserves_explicit_override(self):
        self.catalog.return_value = {'glm-5.3-flash', 'space-bunny-free'}
        with patch('gui.studio.remote', return_value=response({'reply': 'Ready.', 'operations': []})) as send:
            self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(json.loads(send.call_args.args[1])['model'], 'glm-5.3-flash')
        self.assertEqual(json.loads(send.call_args.args[1])['reasoning_effort'], 'low')

    def test_truncated_planning_is_compacted_once_without_applying_partial_content(self):
        truncated = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': '{"reply":"partial'}}]}).encode()
        with patch('gui.studio.remote', side_effect=[truncated, response({'reply': 'Ready.', 'operations': []})]) as send:
            plan = self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(plan['parts'][0]['operations'], [])
        messages = json.loads(send.call_args.args[1])['messages']
        self.assertIn('shorter complete JSON', messages[-1]['content'])
        self.assertEqual(messages[-1]['role'], 'user')

    def test_malformed_provider_choice_returns_diagnostic_and_releases_lock(self):
        for choice in (None, [], 42):
            with patch('gui.studio.remote', return_value=json.dumps({'choices': [choice]}).encode()):
                with self.assertRaisesRegex(StudioError, 'no studio response'):
                    self.studio.prompt(data(), SNAPSHOT)
            self.assertFalse(self.studio.lock.locked())

    def test_whole_song_is_one_bounded_plan_with_scoped_musicians_and_shared_brief(self):
        song = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
        snapshot = {'revision': '7', 'session': song}
        before = copy.deepcopy(snapshot)
        tasks = [{'role': 'musician', 'prompt': 'French-house part', 'scope': {'track_id': track}}
                 for track in ('lead', 'bass', 'drums')]
        tasks.append({'role': 'engineer', 'prompt': 'Balance/filter the parts', 'scope': None})
        producer = {'reply': 'Tight disco groove with filtered synths.', 'operations': [], 'delegations': tasks}
        children = [{'reply': 'Change timbre.', 'operations': [
            {'op': 'device', 'track_id': track, 'patch': {'gain': 0.1}}]}
                    for track in ('lead', 'bass', 'drums')]
        children.append({'reply': 'Balance lead.', 'operations': [{'op': 'gainDb', 'track_id': 'lead', 'db': -3}]})
        events = []
        with patch('gui.studio.remote', side_effect=[response(p) for p in [producer, *children]]) as send:
            result = self.studio.prompt(data(), snapshot, progress=events.append)
        self.assertEqual(len(result['parts']), 5)
        self.assertEqual(snapshot, before)
        wires = [json.loads(call.args[1]) for call in send.call_args_list]
        overview = json.loads(wires[0]['messages'][1]['content'])['session']
        self.assertNotIn('notes', overview['tracks'][0]['clips'][0])
        self.assertEqual(overview['tracks'][0]['clips'][0]['note_count'], len(song['tracks'][0]['clips'][0]['notes']))
        self.assertEqual(wires[0]['max_tokens'], 2000)
        for wire, track in zip(wires[1:4], song['tracks']):
            context = json.loads(wire['messages'][1]['content'])
            view = context['session']['tracks'][0]
            self.assertEqual({k: v for k, v in view.items() if k != 'matching_note_patterns'}, track)
            self.assertIn(producer['reply'], context['prompt'])
            self.assertEqual(wire['max_tokens'], 4000)
        mix = json.loads(wires[4]['messages'][1]['content'])
        self.assertNotIn('notes', mix['session']['tracks'][0]['clips'][0])
        self.assertEqual(len(mix['pending_proposals']), 4)
        self.assertEqual([e['type'] for e in events].count('delegation'), 4)

    def test_delegation_cannot_widen_track_or_clip_scope_or_name_missing_tracks(self):
        song = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
        snapshot = {'revision': '7', 'session': song}
        selected = {'track_id': 'lead', 'clip_id': song['tracks'][0]['clips'][0]['id']}
        for scope in (None, {'track_id': 'bass'}, {'track_id': 'lead'}, {'track_id': 'missing'}, {'clip_id': 'x'}):
            bad = {'reply': 'Split it.', 'operations': [], 'delegations': [
                {'role': 'musician', 'prompt': 'Change it', 'scope': scope}]}
            with patch('gui.studio.remote', return_value=response(bad)) as send, self.assertRaises(StudioError):
                self.studio.prompt(data(scope=selected), snapshot)
            self.assertEqual(send.call_count, 2)
        inherited = {'reply': 'Split it.', 'operations': [], 'delegations': [
            {'role': 'musician', 'prompt': 'Change this clip'}]}
        with patch('gui.studio.remote', side_effect=[response(inherited), response({'reply': 'Ready.', 'operations': []})]) as send:
            self.studio.prompt(data(scope=selected), snapshot)
        child = json.loads(json.loads(send.call_args.args[1])['messages'][1]['content'])['session']
        self.assertEqual(len(child['tracks']), 1)
        self.assertEqual(child['tracks'][0]['clips'], [song['tracks'][0]['clips'][0]])

    def test_musician_tasks_keep_all_patterns_of_one_track_available(self):
        song = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
        for task_scope in (None, {'track_id': 'drums', 'clip_id': 'beat-1'}):
            plan = {'reply': 'Change all patterns.', 'delegations': [
                {'role': 'musician', 'prompt': 'Edit and propagate', 'scope': task_scope}]}
            with self.assertRaisesRegex(StudioError, 'one existing track'):
                validate_plan(plan, 'producer', None, session=song)
        plan['delegations'][0]['scope'] = {'track_id': 'drums'}
        validate_plan(plan, 'producer', None, session=song)

    def test_task_operation_budget_and_last_task_failure_leave_snapshot_unchanged(self):
        before = copy.deepcopy(SNAPSHOT)
        producer = {'reply': 'Split it.', 'operations': [], 'delegations': [
            {'role': 'engineer', 'prompt': 'First'}, {'role': 'engineer', 'prompt': 'Second'}]}
        valid = {'reply': 'Level.', 'operations': [{'op': 'gainDb', 'track_id': 'tone', 'db': -3}]}
        oversized = {'reply': 'Too much.', 'operations': valid['operations'] * 33}
        with patch('gui.studio.remote', side_effect=[response(producer), response(valid), response(oversized), response(oversized)]):
            with self.assertRaisesRegex(StudioError, 'Too many studio operations'):
                self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(SNAPSHOT, before)
        self.assertFalse(self.studio.lock.locked())

    def test_provider_formatting_preserves_strings_and_rejects_ambiguous_or_invalid_json(self):
        expected = {'reply': 'Line one\nLine two\\n', 'operations': []}
        formatted = json.dumps(expected, indent=2).replace('\n', '\\n')
        self.assertEqual(plan_object('A musical explanation.\n' + formatted), expected)
        self.assertEqual(plan_object('```json\n' + json.dumps(expected) + '\n```'), expected)
        for text in ('{"reply":"a","reply":"b"}', '{"reply":NaN}',
                     '{"reply":"a"} {"reply":"b"}', '{"reply":"a"} trailing text',
                     '[{"reply":"a"}]', '{"reply":"unfinished'):
            with self.assertRaises(StudioError):
                plan_object(text)

    def test_copy_notes_only_uses_matching_existing_patterns_inside_scope(self):
        song = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
        before = copy.deepcopy(song)
        track = song['tracks'][2]
        op = {'op': 'copyNotes', 'track_id': track['id'], 'clip_id': track['clips'][0]['id'],
              'target_clip_ids': [c['id'] for c in track['clips'][1:]]}
        plan = {'reply': 'Propagate the groove.', 'operations': [op]}
        validate_plan(plan, 'musician', {'track_id': track['id']}, session=song)
        with self.assertRaises(StudioError):
            validate_plan(plan, 'engineer', None, session=song)
        with self.assertRaises(StudioError):
            validate_plan(plan, 'musician', {'track_id': track['id'], 'clip_id': op['clip_id']}, session=song)
        track['clips'][1]['notes'][0]['frequency_hz'] += 0.125
        with self.assertRaisesRegex(StudioError, 'originally identical'):
            validate_plan(plan, 'musician', None, session=song)
        song = before
        for targets in ([], ['missing'], [op['clip_id']], ['beat-2', 'beat-2'], [42]):
            bad = {'reply': '', 'operations': [{**op, 'target_clip_ids': targets}]}
            with self.assertRaises(StudioError):
                validate_plan(bad, 'musician', None, session=song)

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

    def test_inference_has_longer_socket_timeout_and_remains_transactional(self):
        before = copy.deepcopy(SNAPSHOT)
        with patch('gui.studio.urllib.request.OpenerDirector.open', side_effect=TimeoutError('secret')) as send:
            with self.assertRaisesRegex(StudioError, 'Producer.*Timed out after 120s.*No studio edits applied'):
                self.studio.prompt(data(), SNAPSHOT)
        self.assertEqual(send.call_args.kwargs['timeout'], 120)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(SNAPSHOT, before)
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

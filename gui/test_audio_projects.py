"""Actual HTTP tests of private audio imports and portable project transactions."""
import copy
import http.client
import io
import json
import socket
import stat
import struct
import threading
import unittest
import warnings
import wave
import zipfile
from unittest.mock import patch

from gui.server import ROOT, Server
from gui.audio_projects import MAX_AUDIO_BODY, MAX_METADATA, MAX_PROJECT_BODY

BINARY = ROOT / 'target/debug/daw'


def wav(rate=48000, channels=1, bits=16, frames=120):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(bits // 8)
        stream.setframerate(rate)
        stream.writeframes(b'\x01' * (channels * bits // 8 * frames))
    return output.getvalue()


def bundle(session, assets, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        archive.writestr('session.json', json.dumps(session))
        for name, body in assets.items():
            archive.writestr(name, body)
    return output.getvalue()


class AudioProjectTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(BINARY.is_file(), 'Build engine before bridge tests.')
        self.server = Server(BINARY)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        root = self.server.asset_root
        self.server.shutdown()
        self.thread.join(5)
        self.server.server_close()
        self.assertFalse(root.exists(), 'Owned assets must be removed after engine teardown.')

    def request(self, method, path, body=None, *, meta=None, headers=None, server=None, truncate=False):
        server = server or self.server
        options = {'X-DAW-Token': server.token, 'Host': server.origin.removeprefix('http://')}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            options['Content-Type'] = 'application/json'
        elif body is not None:
            options['Content-Type'] = 'audio/wav' if path == '/api/audio/import' else 'application/zip'
        if meta is not None:
            options['X-DAW-Metadata'] = json.dumps(meta)
        options.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=options)
        if truncate:
            connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def inspect(self):
        status, body, _ = self.request('GET', '/api/session/inspect')
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def import_audio(self, **changes):
        meta = {'expected_revision': self.inspect()['revision'], 'track_id': 'audio',
                'clip_id': 'clip', 'start_frame': 0, **changes}
        return self.request('POST', '/api/audio/import', wav(), meta=meta)

    def assert_preserved(self, before, files):
        self.assertEqual(self.inspect(), before)
        self.assertEqual(set(self.server.projects.assets), files)
        self.assertEqual({str(path.relative_to(self.server.asset_root)) for path in (self.server.asset_root / 'assets').iterdir()}, files)

    def test_import_pcm_variants_and_discovery(self):
        for bits in (16, 24, 32):
            for channels in (1, 2):
                meta = {'expected_revision': self.inspect()['revision'], 'track_id': f'a{bits}{channels}',
                        'clip_id': 'c', 'start_frame': 0}
                status, body, _ = self.request('POST', '/api/audio/import', wav(bits=bits, channels=channels, frames=121), meta=meta)
                self.assertEqual(status, 200, body)
                result = json.loads(body)
                self.assertEqual(result['session']['schema_version'], 9)
                self.assertEqual(result['asset']['frames'], 121)
                self.assertEqual(result['asset']['bits_per_sample'], bits)
                self.assertEqual(result['asset']['channels'], channels)
                self.assertRegex(result['asset']['source_path'], r'^assets/[a-f0-9]{32}\.wav$')
        status, body, _ = self.request('GET', '/api/capabilities')
        self.assertEqual(status, 200)
        bridge = json.loads(body)['gui_bridge']
        self.assertTrue(bridge['audio_projects'])
        self.assertEqual(bridge['audio_project_limits']['audio_bytes'], MAX_AUDIO_BODY)
        self.assertEqual(bridge['audio_project_limits']['project_bytes'], MAX_PROJECT_BODY)

    def test_v1_upgrade_preserves_continuous_tone_and_v2_v3_v4_v9_retain_version(self):
        for version in (1, 2, 3, 4, 9):
            track = {'id': 'tone', 'device': {'kind': 'sine', 'frequency_hz': 220, 'gain': 0.1}}
            session = {'schema_version': version, 'sample_rate': 48000, 'tracks': [track]}
            if version != 1:
                session['tempo_milli_bpm'] = 120000
                track.update(mode='continuous', clips=[])
                if version != 2:
                    track['effects'] = []
            status, body, _ = self.request('POST', '/api/session', {'session': session})
            self.assertEqual(status, 200, body)
            status, body, _ = self.import_audio()
            self.assertEqual(status, 200, body)
            imported = json.loads(body)['session']
            self.assertEqual(imported['schema_version'], 9 if version == 1 else version)
            self.assertEqual(imported['tracks'][0]['device'], track['device'])

    def test_invalid_wavs_and_stale_metadata_preserve_assets_and_revision(self):
        self.assertEqual(self.import_audio()[0], 200)
        before, files = self.inspect(), set(self.server.projects.assets)
        floating = bytearray(wav())
        struct.pack_into('<H', floating, 20, 3)
        for audio in (b'', b'not wav', wav()[:-1], wav(rate=44100), wav(bits=8), wav(channels=3), bytes(floating)):
            status, body, _ = self.request('POST', '/api/audio/import', audio,
                meta={'expected_revision': before['revision'], 'track_id': 'new', 'clip_id': 'c', 'start_frame': 0})
            self.assertIn(status, (413, 422), body)
            self.assert_preserved(before, files)
        for changes in ({'expected_revision': '0'}, {'start_frame': True}, {'start_frame': -1},
                        {'start_frame': 9007199254740991}, {'track_id': 'audio'}, {'track_id': ''}, {'clip_id': 'é' * 65}):
            status, body, _ = self.import_audio(**changes)
            self.assertEqual(status, 422, body)
            self.assert_preserved(before, files)

    def test_authentication_metadata_bounds_content_type_and_framing(self):
        before = self.inspect()
        meta = {'expected_revision': before['revision'], 'track_id': 'a', 'clip_id': 'c', 'start_frame': 0}
        for headers in ({'X-DAW-Token': ''}, {'Host': 'attacker.example'}, {'Origin': 'http://attacker.example'}):
            self.assertEqual(self.request('POST', '/api/audio/import', wav(), meta=meta, headers=headers)[0], 403)
        self.assertEqual(self.request('GET', '/api/project', headers={'X-DAW-Token': ''})[0], 403)
        for headers, expected in (({'Content-Type': 'text/plain'}, 415),
                                  ({'Transfer-Encoding': 'chunked'}, 422),
                                  ({'Content-Length': str(MAX_AUDIO_BODY + 1)}, 413),
                                  ({'X-DAW-Metadata': 'x' * (MAX_METADATA + 1)}, 422),
                                  ({'X-DAW-Metadata': '{"expected_revision":"0","expected_revision":"1"}'}, 422)):
            self.assertEqual(self.request('POST', '/api/audio/import', wav(), meta=meta, headers=headers)[0], expected)
        self.assertEqual(self.request('POST', '/api/audio/import', wav(), meta=meta,
                         headers={'Content-Length': str(len(wav()) + 1)}, truncate=True)[0], 422)
        self.assert_preserved(before, set())

    def test_arbitrary_audio_paths_are_rejected_before_engine_reads(self):
        self.assertEqual(self.import_audio()[0], 200)
        before, files = self.inspect(), set(self.server.projects.assets)
        for path in ('examples/sessions/source.wav', '../source.wav', '/tmp/source.wav', 'assets/missing.wav'):
            candidate = copy.deepcopy(before['session'])
            candidate['tracks'][-1]['clips'][0]['source_path'] = path
            status, body, _ = self.request('POST', '/api/session', {'session': candidate, 'expected_revision': before['revision']})
            self.assertEqual(status, 422, body)
            self.assertIn(b'registered', body)
            self.assert_preserved(before, files)

    def test_failed_checked_replacement_rolls_back_new_file(self):
        before = self.inspect()
        original = self.server.engine.call
        def fail(method, params=None):
            if method == 'session.replace':
                from gui.server import EngineError
                raise EngineError('Stopped edits only.')
            return original(method, params)
        with patch.object(self.server.engine, 'call', side_effect=fail):
            self.assertEqual(self.import_audio()[0], 422)
        self.assert_preserved(before, set())

    def test_decoded_budget_rejection_preserves_project(self):
        before = self.inspect()
        with patch('gui.audio_projects.MAX_DECODED_BYTES', 16):
            self.assertEqual(self.import_audio()[0], 422)
        self.assert_preserved(before, set())

    def test_project_reopens_in_second_owned_root_and_renders_identical_bytes(self):
        self.assertEqual(self.import_audio()[0], 200)
        status, original, _ = self.request('POST', '/api/render', {'seconds': 0.01})
        self.assertEqual(status, 200, original)
        status, packed, headers = self.request('GET', '/api/project')
        self.assertEqual(status, 200, packed)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        other = Server(BINARY)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        try:
            status, body, _ = self.request('POST', '/api/project', packed, meta={'expected_revision': '0'}, server=other)
            self.assertEqual(status, 200, body)
            reopened = json.loads(body)
            old = self.inspect()['session']['tracks'][0]['clips'][0]['source_path']
            new = reopened['session']['tracks'][0]['clips'][0]['source_path']
            self.assertNotEqual(old, new)
            status, rendered, _ = self.request('POST', '/api/render', {'seconds': 0.01}, server=other)
            self.assertEqual(status, 200, rendered)
            self.assertEqual(rendered, original)
        finally:
            other.shutdown()
            thread.join(5)
            root = other.asset_root
            other.server_close()
            self.assertFalse(root.exists())

    def test_unsafe_duplicate_symlink_extra_missing_and_bomb_zip_entries_preserve(self):
        self.assertEqual(self.import_audio()[0], 200)
        before, files = self.inspect(), set(self.server.projects.assets)
        session = copy.deepcopy(before['session'])
        source = session['tracks'][0]['clips'][0]['source_path']
        bad = [b'not zip', bundle(session, {}), bundle(session, {source: wav(), 'extra.wav': wav()}),
               bundle(session, {source: wav(rate=44100)})]
        for name in ('../escape.wav', '/tmp/escape.wav', 'assets/../escape.wav', 'assets\\escape.wav'):
            bad.append(bundle(session, {source: wav(), name: wav()}))
        for kind in ('symlink', 'duplicate', 'bomb'):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('session.json', json.dumps(session))
                info = zipfile.ZipInfo(source)
                if kind == 'symlink':
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, wav())
                if kind == 'duplicate':
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        archive.writestr(source, wav())
                if kind == 'bomb':
                    archive.writestr('assets/bomb.wav', b'0' * (MAX_AUDIO_BODY + 1))
            bad.append(output.getvalue())
        for packed in bad:
            status, body, _ = self.request('POST', '/api/project', packed, meta={'expected_revision': before['revision']})
            self.assertEqual(status, 422, body)
            self.assert_preserved(before, files)

    def test_invalid_bundle_candidate_rolls_back_staged_assets(self):
        self.assertEqual(self.import_audio()[0], 200)
        before, files = self.inspect(), set(self.server.projects.assets)
        candidate = copy.deepcopy(before['session'])
        source = candidate['tracks'][0]['clips'][0]['source_path']
        candidate['tracks'][0]['clips'][0]['length_frames'] = 999999
        packed = bundle(candidate, {source: wav()})
        status, body, _ = self.request('POST', '/api/project', packed, meta={'expected_revision': before['revision']})
        self.assertEqual(status, 422, body)
        self.assert_preserved(before, files)

    def test_bundle_reopen_reclaims_retired_assets_with_transactional_staging(self):
        audio = wav(frames=2000)
        self.assertEqual(self.request('POST', '/api/audio/import', audio, meta={
            'expected_revision': self.inspect()['revision'], 'track_id': 'audio',
            'clip_id': 'clip', 'start_frame': 0})[0], 200)
        before, old_files = self.inspect(), set(self.server.projects.assets)
        status, packed, _ = self.request('GET', '/api/project')
        self.assertEqual(status, 200)
        self.assertGreater(len(audio) * 2, len(packed) + 1)
        # The bundle alone fits, while old+staged ownership temporarily exceeds
        # the limit. Reopening must reclaim old ownership only after commit.
        with patch('gui.audio_projects.MAX_PROJECT_BODY', len(packed) + 1), patch('gui.audio_projects.MAX_ASSETS', 1):
            status, body, _ = self.request('POST', '/api/project', packed,
                                           meta={'expected_revision': before['revision']})
            self.assertEqual(status, 200, body)
        self.assertEqual(len(self.server.projects.assets), 1)
        self.assertFalse(old_files & set(self.server.projects.assets))
        before, current = self.inspect(), set(self.server.projects.assets)
        candidate = copy.deepcopy(before['session'])
        path = candidate['tracks'][0]['clips'][0]['source_path']
        candidate['tracks'][0]['clips'][0]['length_frames'] += 1
        rejected = bundle(candidate, {path: audio})
        with patch('gui.audio_projects.MAX_PROJECT_BODY', len(rejected) + 1), patch('gui.audio_projects.MAX_ASSETS', 1):
            self.assertEqual(self.request('POST', '/api/project', rejected,
                meta={'expected_revision': before['revision']})[0], 422)
        self.assert_preserved(before, current)

    def test_no_fallible_postcommit_inspect_deletes_applied_asset(self):
        original = self.server.engine.call
        committed = False
        def inspect_fails_after_commit(method, params=None):
            nonlocal committed
            if committed and method == 'session.inspect':
                from gui.server import EngineError
                raise EngineError('Postcommit inspection failed.')
            result = original(method, params)
            if method == 'session.replace':
                committed = True
            return result
        with patch.object(self.server.engine, 'call', side_effect=inspect_fails_after_commit):
            status, body, _ = self.import_audio()
            self.assertEqual(status, 200, body)
        session = self.inspect()['session']
        path = session['tracks'][0]['clips'][0]['source_path']
        self.assertTrue((self.server.asset_root / path).is_file())

    def test_concurrent_same_revision_imports_only_commit_once(self):
        revision = self.inspect()['revision']
        results = []
        def run(index):
            results.append(self.request('POST', '/api/audio/import', wav(), meta={
                'expected_revision': revision, 'track_id': str(index), 'clip_id': 'c', 'start_frame': 0})[0])
        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(results), [200, 422])
        self.assertEqual(len(self.server.projects.assets), 1)


if __name__ == '__main__':
    unittest.main()

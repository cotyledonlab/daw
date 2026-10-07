"""Real bridge/DSP acceptance for the schema12 sequenced Pd preset."""
import copy
import io
import wave
import http.client
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from examples.audio_project_workflow_demo import Client
from examples.pd_instrument_workflow_demo import import_fixture, instrument_fixture, instrument_track, note_signal_evidence
from examples.song_workflow_demo import portable_data

LIBRARY = os.environ.get('DAW_LIBPD_LIBRARY')


@unittest.skipUnless(LIBRARY and Path(LIBRARY).is_absolute() and Path(LIBRARY).is_file(),
                     'set DAW_LIBPD_LIBRARY to an absolute libpd library')
class PdInstrumentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = Client().__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def export(self, client=None):
        body, headers = (client or self.client).request('POST', '/api/render', {'seconds': 4})
        self.assertEqual(headers['X-Clipped-Frames'], '0')
        return body

    def raw_replace(self, session, revision, client=None):
        server = (client or self.client).server
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        connection.request('POST', '/api/session', json.dumps({'session': session, 'expected_revision': revision}),
                           {'X-DAW-Token': server.token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_real_note_input_has_two_pitches_and_silent_note_off_gap(self):
        track = instrument_track()
        track['effects'] = []
        track['mixer']['pan'] = 0
        session = instrument_fixture()
        session['tracks'] = [track]
        self.client.replace(session)
        evidence = note_signal_evidence(self.export())
        self.assertEqual(evidence['between_notes_peak_pcm16'], 0)
        self.assertEqual(len(evidence['scheduled_frequencies_hz']), 2)
        track['clips'][0]['notes'] = []
        self.client.replace(session)
        with wave.open(io.BytesIO(self.export()), 'rb') as stream:
            self.assertEqual(set(stream.readframes(stream.getnframes())), {0})

    def test_edits_undo_and_fresh_zip_reopen_preserve_mixed_song(self):
        session = import_fixture(self.client)
        original = self.export()
        pd_index = next(i for i, t in enumerate(session['tracks']) if t['id'] == 'pd-lead')
        for field, value in [('frequency_hz', 880), ('velocity', .1), ('start_frame', 2000), ('cutoff', 150)]:
            changed = copy.deepcopy(session)
            track = changed['tracks'][pd_index]
            if field == 'cutoff':
                track['device']['controls'][0]['value'] = value
            else:
                track['clips'][0]['notes'][0][field] = value
            self.client.replace(changed)
            self.assertNotEqual(self.export(), original, field)
            applied = self.client.inspect()['session']
            for i, saved in enumerate(session['tracks']):
                if i != pd_index:
                    self.assertEqual(applied['tracks'][i], saved)
            self.assertEqual(applied['tracks'][pd_index]['mixer'], session['tracks'][pd_index]['mixer'])
            self.assertEqual(applied['tracks'][pd_index]['effects'], session['tracks'][pd_index]['effects'])
            self.client.replace(session)
            self.assertEqual(self.export(), original)
        bundle, _ = self.client.request('GET', '/api/project')
        with Client() as fresh:
            fresh.json('POST', '/api/project', bundle, binary=True,
                       metadata={'expected_revision': fresh.inspect()['revision']})
            self.assertEqual(portable_data(fresh.inspect()['session']), portable_data(session))
            self.assertEqual(self.export(fresh), original)

    def test_invalid_patch_controls_overlap_and_stale_edits_are_transactional(self):
        session = import_fixture(self.client)
        before = self.client.inspect()
        pd_index = next(i for i, t in enumerate(session['tracks']) if t['id'] == 'pd-lead')
        candidates = []
        for field, value in [('program', '#N canvas 0 0 100 100 10;\n'), ('gain', 2), ('abstractions', [{'name': 'missing', 'program': ''}])]:
            candidate = copy.deepcopy(session)
            candidate['tracks'][pd_index]['device'][field] = value
            candidates.append(candidate)
        for field, value in [('value', 99), ('value', 12001), ('name', 'missing'), ('type', 'integer')]:
            candidate = copy.deepcopy(session)
            candidate['tracks'][pd_index]['device']['controls'][0][field] = value
            candidates.append(candidate)
        candidate = copy.deepcopy(session)
        candidate['tracks'][pd_index]['clips'][0]['notes'][1]['start_frame'] = 64
        candidates.append(candidate)
        candidate = copy.deepcopy(session)
        candidate['schema_version'] = 11
        candidates.append(candidate)
        for candidate in candidates:
            status, body = self.raw_replace(candidate, before['revision'])
            self.assertEqual(status, 422, body)
            self.assertEqual(self.client.inspect(), before)
        status, body = self.raw_replace(session, '0')
        self.assertEqual(status, 422, body)
        self.assertEqual(self.client.inspect(), before)
        self.assertGreater(len(self.export()), 44)

    def test_missing_runtime_replacement_and_zip_reopen_preserve_existing_project(self):
        import_fixture(self.client)
        bundle, _ = self.client.request('GET', '/api/project')
        with patch.dict(os.environ, {'DAW_LIBPD_LIBRARY': '/nonexistent/daw-missing-libpd.dylib'}):
            with Client() as fresh:
                before = fresh.inspect()
                status, body = self.raw_replace(instrument_fixture(), before['revision'], fresh)
                self.assertEqual(status, 422, body)
                self.assertEqual(fresh.inspect(), before)
                connection = http.client.HTTPConnection('127.0.0.1', fresh.server.server_port, timeout=30)
                connection.request('POST', '/api/project', bundle,
                                   {'X-DAW-Token': fresh.server.token, 'Content-Type': 'application/zip',
                                    'X-DAW-Metadata': json.dumps({'expected_revision': before['revision']})})
                response = connection.getresponse()
                status, body = response.status, response.read()
                connection.close()
                self.assertEqual(status, 422, body)
                self.assertEqual(fresh.inspect(), before)


if __name__ == '__main__':
    unittest.main()

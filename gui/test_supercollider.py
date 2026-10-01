"""Opt-in real-engine HTTP checks for saved SuperCollider GUI sessions."""
import json
import os
import time
import unittest

from examples.supercollider_tracks_demo import make_track
from gui import test_server as bridge_tests


@unittest.skipUnless(os.environ.get('DAW_TEST_SC_GUI') == '1', 'set DAW_TEST_SC_GUI=1 for muted native GUI checks')
class SuperColliderGuiTests(unittest.TestCase):
    # Reuse HTTP lifecycle/helpers without inheriting unrelated test cases.
    setUp = bridge_tests.ServerIntegrationTests.setUp
    tearDown = bridge_tests.ServerIntegrationTests.tearDown
    request = bridge_tests.ServerIntegrationTests.request
    post = bridge_tests.ServerIntegrationTests.post
    get_session = bridge_tests.ServerIntegrationTests.get_session
    def test_live_source_import_control_ack_and_natural_cleanup(self):
        track = make_track('gui-source', 440, 0.1, 0.5, 1, 1)
        track['device']['duration_frames'] = 192000
        session = {'schema_version': 6, 'sample_rate': 48000,
                   'tempo_milli_bpm': 120000, 'tracks': [track]}
        status, body, _ = self.post('/api/session', {'session': session, 'expected_revision': '0'})
        self.assertEqual(status, 200, body)
        status, body, _ = self.post('/api/source/inspect', {'synthdef_hex': track['device']['synthdef_hex']})
        self.assertEqual(status, 200, body)
        self.assertFalse(next(c for c in json.loads(body)['controls'] if c['name'] == 'gain')['initialization_rate'])
        status, body, _ = self.post('/api/transport', {'action': 'play', 'source_mode': 'live', 'seconds': 4, 'volume': 0})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['source_mode'], 'live')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            _, body, _ = self.request('GET', '/api/transport')
            snapshot = json.loads(body)
            if snapshot.get('submitted_frames', 0) >= 67200:
                break
            time.sleep(0.025)
        self.assertGreaterEqual(snapshot.get('submitted_frames', 0), 67200)
        change = {'expected_revision': '1', 'track_id': 'gui-source', 'control_name': 'gain', 'values': [0.02]}
        status, body, _ = self.post('/api/source/control', change)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['revision'], '2')
        status, _, _ = self.post('/api/source/control', change)
        self.assertEqual(status, 422)  # stale revision does not replace accepted values
        deadline = time.monotonic() + 8
        observed = False
        while time.monotonic() < deadline:
            _, body, _ = self.request('GET', '/api/transport')
            snapshot = json.loads(body)
            update = snapshot.get('source_control_update', {})
            observed |= update.get('applied_revision') == '2' and update.get('callback_observed') is True
            if snapshot['state'] == 'stopped':
                break
            time.sleep(0.025)
        self.assertTrue(observed)
        self.assertNotIn('error', snapshot)
        self.assertEqual(snapshot['state'], 'stopped')
        self.assertTrue(snapshot['resources_released'])
        self.assertEqual(snapshot['live_source_underruns'], 0)
        self.assertEqual(snapshot['source']['source_digest'], snapshot['callback_source_digest'])
        controls = self.get_session()['tracks'][0]['device']['controls']
        self.assertEqual(next(c for c in controls if c['name'] == 'gain')['values'], [0.02])
        self.assertEqual(next(c for c in controls if c['name'] == 'freq')['points'], track['device']['controls'][0]['points'])


"""Bridge acceptance for automation/fades, checked undo and portable persistence."""
import copy
import unittest

from examples.audio_project_workflow_demo import Client
from examples.finishing_workflow_demo import finish_fixture
from examples.processing_workflow_demo import import_fixture
from examples.song_workflow_demo import portable_data


class FinishingWorkflowTests(unittest.TestCase):
    def test_finishing_edits_undo_reopen_and_rejected_edits_preserve_the_song(self):
        with Client() as client:
            original = import_fixture(client)
            before_audio, _ = client.request('POST', '/api/render', {'seconds': 4})
            finished = finish_fixture(original)
            client.replace(finished)
            finished = client.inspect()['session']
            after_audio, headers = client.request('POST', '/api/render', {'seconds': 4})
            self.assertNotEqual(after_audio, before_audio)
            self.assertEqual(headers['X-Clipped-Frames'], '0')
            self.assertEqual([t['mixer'] for t in finished['tracks']], [t['mixer'] for t in original['tracks']])
            self.assertEqual(finished['tracks'][0], original['tracks'][0])
            bundle, _ = client.request('GET', '/api/project')
            before = client.inspect()
            invalid = copy.deepcopy(finished)
            clip = invalid['tracks'][-1]['clips'][0]
            clip['fade_out_frames'] = clip['length_frames']
            with self.assertRaisesRegex(RuntimeError, '422'):
                client.replace(invalid)
            self.assertEqual(client.inspect(), before)
            with self.assertRaisesRegex(RuntimeError, '422'):
                client.json('POST', '/api/session', {'session': original, 'expected_revision': '0'})
            self.assertEqual(client.inspect(), before)
            client.replace(original)  # Same checked snapshot replacement used by Undo.
            undone, _ = client.request('POST', '/api/render', {'seconds': 4})
            self.assertEqual(undone, before_audio)
            client.replace(finished)
            redone, _ = client.request('POST', '/api/render', {'seconds': 4})
            self.assertEqual(redone, after_audio)
            page, _ = client.request('GET', '/')
            script, _ = client.request('GET', '/automation.js')
            self.assertIn(b'/automation.js', page)
            self.assertIn(b'AutomationView', script)
        with Client() as fresh:
            fresh.json('POST', '/api/project', bundle, binary=True,
                       metadata={'expected_revision': fresh.inspect()['revision']})
            self.assertEqual(portable_data(fresh.inspect()['session']), portable_data(finished))
            restored, headers = fresh.request('POST', '/api/render', {'seconds': 4})
            self.assertEqual(restored, after_audio)
            self.assertEqual(headers['X-Clipped-Frames'], '0')


if __name__ == '__main__':
    unittest.main()

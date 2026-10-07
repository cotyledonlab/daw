"""Actual-engine HTTP acceptance for stopped audition and checked step capture."""
import unittest

from examples.note_input_workflow_demo import workflow


class NoteInputWorkflowTests(unittest.TestCase):
    def test_preview_step_capture_undo_stale_rejection_and_portable_reopen(self):
        evidence, bundle, before, after, previews = workflow()
        self.assertEqual(evidence['captured_notes'], 8)
        self.assertEqual(evidence['clipped_frames'], 0)
        self.assertTrue(evidence['last_note_audible'])
        self.assertTrue(evidence['preserved_pcm_and_other_tracks'])
        self.assertEqual(before, after)
        self.assertEqual(len(previews), 8)
        self.assertTrue(bundle.startswith(b'PK'))


if __name__ == '__main__':
    unittest.main()

"""Real-engine acceptance for buffered overdub capture and portable persistence."""
import unittest

from examples.note_recording_workflow_demo import workflow


class NoteRecordingWorkflowTests(unittest.TestCase):
    def test_overdub_single_undo_stale_rejection_and_portable_reopen(self):
        evidence, bundle, before, after = workflow()
        self.assertEqual(evidence['recorded_notes'], 3)
        self.assertEqual(evidence['clipped_frames'], 0)
        self.assertTrue(evidence['count_in_note_ignored'])
        self.assertTrue(evidence['held_gate_closed_at_stop'])
        self.assertTrue(evidence['single_take_undo_redo'])
        self.assertTrue(evidence['atomic_take_revision_envelope'])
        self.assertTrue(evidence['preserved_pcm_mixer_effects_and_offgrid_note'])
        self.assertEqual(before, after)
        self.assertTrue(bundle.startswith(b'PK'))


if __name__ == '__main__':
    unittest.main()

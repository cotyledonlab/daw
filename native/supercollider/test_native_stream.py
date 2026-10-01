"""Opt-in muted end-to-end SC -> gain -> CPAL tests."""
import os
import unittest
from native.supercollider.native_probe import native_probe


@unittest.skipUnless(os.environ.get("DAW_TEST_SC_NATIVE") == "1", "set DAW_TEST_SC_NATIVE=1 with native build and SC runtime")
class NativeStreamTests(unittest.TestCase):
    def test_sustained_source_effect_callback_counts_and_order(self):
        report = native_probe(os.environ["DAW_SCSYNTH"], blocks=4500)
        native = report["native"]
        self.assertEqual(native["submitted_frames"], 288000)
        self.assertEqual(native["source"]["source_frames"], 288000)
        self.assertEqual(native["callback_source_digest"], native["source"]["source_digest"])
        self.assertTrue(native["stream_released"])
        self.assertTrue(native["source"]["queue_released"])
        self.assertEqual(native["underruns"], 0)
        self.assertGreater(native["callbacks"], 0)
        self.assertFalse(native["acoustic_verified"])
        self.assertEqual(report["sc_hardware_bus_peak"], 0)

    def test_two_launches_release_consumers_and_streams(self):
        for _ in range(2):
            native = native_probe(os.environ["DAW_SCSYNTH"])["native"]
            self.assertEqual(native["submitted_frames"], 96000)
            self.assertEqual(native["callback_source_digest"], native["source"]["source_digest"])
            self.assertEqual(native["underruns"], 0)

    def test_producer_death_fails_incomplete_stream_and_releases_consumer(self):
        report = native_probe(os.environ["DAW_SCSYNTH"], crash=True)
        self.assertTrue(report["producer_crash_rejected"])
        self.assertTrue(report["consumer_released"])
        self.assertNotEqual(report["native_exit_code"], 0)
        self.assertIn("timed out", report["error"])


if __name__ == "__main__":
    unittest.main()

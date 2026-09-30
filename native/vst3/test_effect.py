"""Opt-in integration checks against an explicitly selected installed effect."""
import os
import unittest

from native.vst3.scan import ROOT, inspect_plugin


class OfflineEffectTests(unittest.TestCase):
    def test_event_layout_is_rejected_without_affecting_fixture(self):
        fixture = ROOT / "output/vst3-spike/DawTestGain.vst3"
        result = inspect_plugin(fixture, mode="effect-probe")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["code"], "plugin_error")
        self.assertIn("event buses", result["error"])
        self.assertTrue(inspect_plugin(fixture, mode="probe")["ok"])

    @unittest.skipUnless(os.environ.get("DAW_VST3_EFFECT"), "set DAW_VST3_EFFECT to an installed stereo VST3 effect")
    def test_installed_effect_repeated_state_automation_and_cleanup(self):
        from gui.server import Engine

        engine = Engine(ROOT / "target/debug/daw")
        try:
            before = engine.call("session.inspect")
            for _ in range(5):
                result = inspect_plugin(os.environ["DAW_VST3_EFFECT"], mode="effect-probe", timeout=15)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["frames_processed"], 32768)
                self.assertGreater(result["automation_output_delta"], 1e-6)
                self.assertGreater(result["restored_output_delta"], 1e-6)
                self.assertGreater(result["component_state_bytes"], 0)
                self.assertLessEqual(result["component_state_bytes"], 8 * 1024 * 1024)
                self.assertTrue(result["fresh_instance_state"])
                self.assertTrue(result["terminated"])
                self.assertEqual(engine.call("session.inspect"), before)
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()

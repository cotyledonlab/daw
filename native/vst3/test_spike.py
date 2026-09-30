"""Runs the real compiled host against the project-owned bundle."""
import os
import pathlib
import tempfile
import unittest

from native.vst3.scan import HOST, ROOT, inspect_plugin


class Vst3SpikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.is_file():
            raise RuntimeError("build the spike first: python3 native/vst3/build.py")
        cls.fixture = ROOT / "output/vst3-spike/DawTestGain.vst3"

    def test_scan_and_repeated_lifecycle_probe(self):
        result = inspect_plugin(self.fixture)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["classes"], [{
            "cid": "DA01234567894ABCBDEF0123456789AB",
            "name": "DAW test gain", "category": "Audio Module Class",
        }])
        for _ in range(10):
            result = inspect_plugin(self.fixture, mode="probe")
            self.assertTrue(result["ok"], result)
            for field in ("sample_offset_parameters", "note_event", "state_restored", "new_instance_state", "terminated", "combined_controller", "controller_state", "controller_ui_independent"):
                self.assertTrue(result[field])
            self.assertEqual(result["frames_checked"], 256)

    def test_scanner_rejects_unbounded_and_invalid_child_output(self):
        cases = (
            ("import sys; sys.stdout.write('x' * 100000)", "probe_output_too_large"),
            ("import sys; sys.stderr.write('x' * 100000)", "probe_output_too_large"),
            ("print('not json')", "probe_protocol"),
            ("print('{\"ok\":true,\"classes\":[]}'); raise SystemExit(1)", "probe_protocol"),
            ("print('{\"ok\":true}')", "probe_protocol"),
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "output") as directory:
            child = pathlib.Path(directory) / "fake-host"
            for source, code in cases:
                child.write_text("#!/usr/bin/env python3\n" + source + "\n")
                child.chmod(0o700)
                result = inspect_plugin(self.fixture, host=child)
                self.assertEqual(result["code"], code, result)
            result = inspect_plugin(self.fixture, host=child.with_name("absent-host"))
            self.assertEqual(result["code"], "host_unavailable")

    def test_missing_plugin_and_child_crash_timeout_are_isolated(self):
        from gui.server import Engine
        engine = Engine(ROOT / "target/debug/daw")
        try:
            before = engine.call("session.inspect")
            missing = inspect_plugin(self.fixture.parent / "Missing.vst3")
            self.assertFalse(missing["ok"], missing)
            for mode, code in (("crash", "probe_crashed"), ("hang", "probe_timeout")):
                result = inspect_plugin(self.fixture, timeout=0.3,
                                        env={**os.environ, "DAW_VST3_FIXTURE_MODE": mode})
                self.assertEqual(result["code"], code, result)
                self.assertEqual(engine.call("session.inspect"), before)
                recovered = inspect_plugin(self.fixture)
                self.assertTrue(recovered["ok"], recovered)
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()

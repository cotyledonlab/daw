"""Real Rust-controller integration; build with vst3-offline before running."""
import copy
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT


def session(bundle, cid, parameter=0):
    return {"schema_version": 4, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [{"id": "lead", "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.1},
                        "mode": "continuous", "clips": [], "effects": [{
                            "kind": "vst3", "id": "fx", "bypass": False,
                            "bundle_path": str(bundle), "class_id": cid, "state_hex": "",
                            "controller_state_hex": "", "parameters": [{"id": parameter, "value": 0.2,
                                "points": [{"frame": 32, "value": 0.8}]}]}]}]}


class DawPluginTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DAW_VST3_FIXTURE_NO_EVENTS": "1"})
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        if not self.engine.call("capabilities")["offline_vst3"]["implemented"]:
            self.engine.close()
            self.environment.stop()
            self.skipTest("build with --features vst3-offline")
        self.project = tempfile.TemporaryDirectory(dir=ROOT / "output")
        self.fixture = ROOT / "output/vst3-spike/DawTestGain.vst3"
        self.session = session(self.fixture, "DA01234567894ABCBDEF0123456789AB")

    def tearDown(self):
        self.engine.close()
        if hasattr(self, "project"):
            self.project.cleanup()
        self.environment.stop()

    def render(self, name):
        path = Path(self.project.name) / name
        report = self.engine.call("render", {"path": str(path), "seconds": 0.001})
        self.assertEqual(report["frames"], 48)
        with wave.open(str(path)) as audio:
            import struct
            samples = struct.unpack("<96h", audio.readframes(48))
        return path, samples

    def test_scripted_automation_state_persistence_and_native_rejection(self):
        result = self.engine.call("session.replace", {"session": self.session, "expected_revision": "0"})
        self.assertEqual(len(result["tracks"][0]["effects"][0]["state_hex"]), 16)
        path, samples = self.render("before.wav")
        for frame in range(48):
            gain = 0.2 if frame < 32 else 0.8
            expected = round(math.sin(2 * math.pi * frame * 440 / 48000) * 0.1 * gain * 32767)
            self.assertLessEqual(abs(samples[frame * 2] - expected), 1)
            self.assertEqual(samples[frame * 2], samples[frame * 2 + 1])
        saved = Path(self.project.name) / "saved.json"
        self.engine.call("session.save", {"path": str(saved)})
        self.engine.call("session.load", {"path": str(saved)})
        _, restored = self.render("after.wav")
        self.assertEqual(samples, restored)
        with self.assertRaises(EngineError):
            self.engine.call("render", {"path": str(path), "seconds": 0.001})
        self.assertTrue(path.is_file())
        with self.assertRaises(EngineError):
            self.engine.call("transport.play", {"seconds": 1, "volume": 0})

    def test_bad_plugin_state_identity_and_parameters_preserve_revision(self):
        self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        for field, value in (("class_id", "0" * 32), ("state_hex", "00"), ("bundle_path", "/tmp/Missing.vst3")):
            invalid = copy.deepcopy(self.session)
            invalid["tracks"][0]["effects"][0][field] = value
            with self.assertRaises(EngineError):
                self.engine.call("session.replace", {"session": invalid})
            self.assertEqual(self.engine.call("session.inspect"), before)
        invalid = copy.deepcopy(self.session)
        invalid["tracks"][0]["effects"][0]["parameters"][0]["id"] = 999
        with self.assertRaises(EngineError):
            self.engine.call("session.replace", {"session": invalid})
        self.assertEqual(self.engine.call("session.inspect"), before)
        output = Path(self.project.name) / "too-long.wav"
        with self.assertRaises(EngineError):
            self.engine.call("render", {"path": str(output), "seconds": 11})
        self.assertFalse(output.exists())

    def test_render_host_failure_creates_no_output_and_preserves_session(self):
        self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        held = self.fixture.with_name("HeldDawTestGain.vst3")
        self.assertFalse(held.exists())
        output = Path(self.project.name) / "failed.wav"
        self.fixture.rename(held)
        try:
            with self.assertRaises(EngineError):
                self.engine.call("render", {"path": str(output), "seconds": 0.001})
            self.assertFalse(output.exists())
            self.assertEqual(self.engine.call("session.inspect"), before)
        finally:
            held.rename(self.fixture)

    def test_child_crash_and_timeout_leave_controller_alive(self):
        self.engine.close()
        for mode in ("crash", "hang"):
            with patch.dict(os.environ, {"DAW_VST3_FIXTURE_MODE": mode}):
                engine = Engine(ROOT / "target/debug/daw")
            try:
                before = engine.call("session.inspect")
                with self.assertRaises(EngineError):
                    engine.call("session.replace", {"session": self.session})
                self.assertEqual(engine.call("session.inspect"), before)
            finally:
                engine.close()

    @unittest.skipUnless(os.environ.get("DAW_VST3_EFFECT"), "set DAW_VST3_EFFECT to ValhallaFreqEcho")
    def test_installed_valhalla_session_restores_and_renders(self):
        project = session(os.environ["DAW_VST3_EFFECT"], "5653544671456876616C68616C6C6166", 48)
        loaded = self.engine.call("session.replace", {"session": project})
        self.assertGreater(len(loaded["tracks"][0]["effects"][0]["state_hex"]), 0)
        self.render("valhalla.wav")


if __name__ == "__main__":
    unittest.main()

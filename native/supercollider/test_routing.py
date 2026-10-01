"""Prepared SC sources through working foreign effect adapters."""
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from examples.supercollider_tracks_demo import make_track
from gui.server import Engine
from native.vst3.scan import ROOT

SCSYNTH = Path("/Applications/SuperCollider.app/Contents/Resources/scsynth")

class SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        self.environment = patch.dict(os.environ, {
            "DAW_SCSYNTH":str(SCSYNTH), "DAW_VST3_FIXTURE_NO_EVENTS":"1",
            "DAW_VST3_FIXTURE_REALTIME":"1",
        })
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        self.project = tempfile.TemporaryDirectory(dir=ROOT / "output")
        self.capabilities = self.engine.call("capabilities")
        self.track = make_track("source", 10000.0, 0.1, 0.1, 1.0, 1.0)
        self.track["effects"] = []
        self.track["device"]["controls"][1]["values"] = [3.0]
        self.session = {"schema_version":6,"sample_rate":48000,"tempo_milli_bpm":120000,
                        "tracks":[self.track]}

    def tearDown(self):
        self.engine.close()
        self.environment.stop()
        self.project.cleanup()

    def render(self, name):
        path = Path(self.project.name) / name
        self.engine.call("render", {"path":str(path),"seconds":0.1})
        with wave.open(str(path)) as wav:
            return struct.unpack("<9600h", wav.readframes(4800))

    def test_float_source_through_vst3_preserves_headroom_and_saved_state(self):
        if not self.capabilities["offline_vst3"]["implemented"]:
            self.skipTest("build vst3-offline")
        self.engine.call("session.replace", {"session":self.session})
        dry = self.render("dry.wav")
        self.track["effects"] = [{"kind":"vst3","id":"gain","bypass":False,
            "bundle_path":str(ROOT / "output/vst3-spike/DawTestGain.vst3"),
            "class_id":"DA01234567894ABCBDEF0123456789AB", "state_hex":"",
            "controller_state_hex":"", "parameters":[{"id":0,"value":0.5,"points":[]}]}]
        self.engine.call("session.replace", {"session":self.session})
        wet = self.render("wet.wav")
        self.assertGreater(max(map(abs,dry)), 8000)
        self.assertLessEqual(max(abs(w * 2 - d) for w,d in zip(wet,dry)), 2)
        saved = Path(self.project.name) / "session.json"
        self.engine.call("session.save", {"path":str(saved)})
        self.engine.call("session.load", {"path":str(saved)})
        self.assertEqual(wet, self.render("restored.wav"))

    def test_prepared_source_through_apple_lowpass(self):
        if not self.capabilities["offline_au"]["implemented"]:
            self.skipTest("build au-offline")
        self.engine.call("session.replace", {"session":self.session})
        dry = self.render("dry-au.wav")
        self.track["effects"] = [{"kind":"au","id":"lowpass","bypass":False,
            "component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl",
            "state_hex":"","parameters":[{"id":0,"value":1000.0}]}]
        self.engine.call("session.replace", {"session":self.session})
        wet = self.render("wet-au.wav")
        self.assertGreater(max(map(abs,wet)), 0)
        self.assertLess(sum(v*v for v in wet[1000:]), sum(v*v for v in dry[1000:]) / 100)

    @unittest.skipUnless(os.environ.get("DAW_TEST_NATIVE_AUDIO") == "1", "opt-in silent hardware check")
    def test_source_snapshot_into_native_vst3_worker(self):
        if not self.capabilities["native_vst3"]["implemented"]:
            self.skipTest("build vst3-live")
        self.track["effects"] = [{"kind":"vst3","id":"gain","bypass":False,
            "bundle_path":str(ROOT / "output/vst3-spike/DawTestGain.vst3"),
            "class_id":"DA01234567894ABCBDEF0123456789AB", "state_hex":"",
            "controller_state_hex":"", "parameters":[{"id":0,"value":0.5,"points":[]}]}]
        self.engine.call("session.replace", {"session":self.session})
        self.engine.call("transport.play", {"seconds":0.3,"volume":0})
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            status = self.engine.call("transport.status")
            if status["submitted_frames"] > 0:
                break
            time.sleep(0.01)
        else:
            self.fail(f"no native frames: {status}")
        self.assertEqual(self.engine.call("transport.stop")["state"],"stopped")

if __name__ == "__main__":
    unittest.main()

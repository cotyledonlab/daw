"""Integration coverage for the versioned effect.inspect protocol."""
import json
import os
from pathlib import Path
import struct
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT
from native.vst3.test_daw import session


CID = "DA01234567894ABCBDEF0123456789AB"


class EffectMetadataTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"DAW_VST3_FIXTURE_NO_EVENTS": "1"})
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        if not self.engine.call("capabilities")["parameter_metadata"]["implemented"]:
            self.engine.close()
            self.environment.stop()
            self.skipTest("build on macOS with --features vst3-offline")
        self.project = tempfile.TemporaryDirectory(dir=ROOT / "output")
        self.fixture = ROOT / "output/vst3-spike/DawTestGain.vst3"
        self.session = session(self.fixture, CID)

    def tearDown(self):
        self.engine.close()
        self.project.cleanup()
        self.environment.stop()

    def install_session(self, project=None):
        return self.engine.call("session.replace", {"session": project or self.session})

    def inspect(self, track="lead", effect="fx"):
        return self.engine.call("effect.inspect", {"track_id": track, "effect_id": effect})

    def test_fixture_metadata_shape_values_and_repeated_inspection(self):
        self.install_session()
        before = self.engine.call("session.inspect")
        result = self.inspect()
        self.assertEqual(set(result), {"track_id", "effect_id", "revision", "parameters"})
        self.assertEqual((result["track_id"], result["effect_id"], result["revision"]),
                         ("lead", "fx", before["revision"]))
        self.assertEqual(len(result["parameters"]), 1)
        parameter = result["parameters"][0]
        self.assertEqual(set(parameter), {"id", "name", "short_name", "unit", "default_value",
                                         "restored_value", "automatable", "read_only", "step_count"})
        self.assertEqual(parameter, {"id": 0, "name": "Gain", "short_name": "Gain", "unit": "linear",
                                     "default_value": 0.5, "restored_value": 0.2,
                                     "automatable": True, "read_only": False, "step_count": 0})
        self.assertEqual(self.inspect(), result)
        self.assertEqual(self.engine.call("session.inspect"), before)

    def test_bad_ids_unknown_params_and_non_vst_effect_are_rejected_without_mutation(self):
        self.install_session()
        self.engine.call("session.replace", {"session": {
            "schema_version": 4, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [{"id": "lead", "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.1},
                        "mode": "continuous", "clips": [], "effects": [{"kind": "gain", "id": "gain", "bypass": False, "gain": 0.5},
                                                                            self.session["tracks"][0]["effects"][0]]}]}})
        before = self.engine.call("session.inspect")
        for params in ({"track_id": "absent", "effect_id": "fx"},
                       {"track_id": "lead", "effect_id": "absent"},
                       {"track_id": "lead", "effect_id": "gain"},
                       {"track_id": "", "effect_id": "fx"},
                       {"track_id": "lead", "effect_id": "fx", "extra": True}):
            with self.subTest(params=params), self.assertRaises(EngineError):
                self.engine.call("effect.inspect", params)
            self.assertEqual(self.engine.call("session.inspect"), before)

    @staticmethod
    def _metadata_bytes(mode):
        u32 = lambda value: struct.pack("<I", value)
        f64 = lambda value: struct.pack("<d", value)
        def string(value):
            raw = value if isinstance(value, bytes) else value.encode()
            return u32(len(raw)) + raw
        data = u32(0x314D5744) + u32(1) + u32(0)
        data += string("Gain") + string("Gain") + string("")
        data += f64(0.5) + f64(0.2) + u32(1) + u32(0) + u32(0)
        if mode == "magic":
            return u32(0) + data[4:]
        if mode == "count":
            return data[:4] + u32(2) + data[8:]
        if mode == "id":
            return data[:8] + u32(1) + data[12:]
        if mode == "utf8":
            return data[:12] + string(b"\xff") + data[12 + 8:]
        if mode == "nonfinite":
            return data[:32] + f64(float("nan")) + data[40:]
        if mode == "flags":
            return data[:48] + u32(2) + data[52:]
        if mode == "trailing":
            return data + b"x"
        if mode == "oversized":
            return b"x" * 131073
        raise AssertionError(mode)

    def test_malformed_worker_metadata_and_crash_preserve_revision(self):
        self.install_session()
        before = self.engine.call("session.inspect")
        control = Path(self.project.name) / "mode.json"
        actual_host = ROOT / "output/vst3-spike/vst3-host"
        wrapper = Path(self.project.name) / "host-wrapper.py"
        wrapper.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            if sys.argv[1] == 'process':
                os.environ.pop('DAW_VST3_FIXTURE_FAIL_PROCESS', None)
                os.execv(os.environ['DAW_TEST_METADATA_REAL_HOST'], [os.environ['DAW_TEST_METADATA_REAL_HOST'], *sys.argv[1:]])
            mode = json.loads(pathlib.Path(os.environ['DAW_TEST_METADATA_MODE']).read_text())
            if mode == 'delegate':
                os.execv(os.environ['DAW_TEST_METADATA_REAL_HOST'], [os.environ['DAW_TEST_METADATA_REAL_HOST'], *sys.argv[1:]])
            if mode == 'crash':
                sys.exit(7)
            if mode == 'hang':
                import time; time.sleep(20)
            sys.stdin.buffer.read()
            sys.stdout.buffer.write(bytes.fromhex(mode))
        """), encoding="utf-8")
        wrapper.chmod(0o755)
        self.engine.close()
        with patch.dict(os.environ, {"DAW_VST3_HOST": str(wrapper),
                                    "DAW_TEST_METADATA_MODE": str(control),
                                    "DAW_TEST_METADATA_REAL_HOST": str(actual_host)}):
            self.engine = Engine(ROOT / "target/debug/daw")
            control.write_text(json.dumps("delegate"))
            self.install_session()
            before = self.engine.call("session.inspect")
            for mode in ("magic", "count", "id", "utf8", "nonfinite", "flags", "trailing", "oversized", "crash"):
                control.write_text(json.dumps(self._metadata_bytes(mode).hex() if mode not in ("crash",) else mode))
                with self.subTest(mode=mode), self.assertRaises(EngineError):
                    self.inspect()
                self.assertEqual(self.engine.call("session.inspect"), before)

    def test_metadata_text_conversion_bounds_and_metadata_avoids_dsp_processing(self):
        control = Path(self.project.name) / "mode.json"
        control.write_text(json.dumps("delegate"))
        actual_host = ROOT / "output/vst3-spike/vst3-host"
        wrapper = Path(self.project.name) / "host-wrapper.py"
        wrapper.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            if sys.argv[1] == 'process':
                os.environ.pop('DAW_VST3_FIXTURE_FAIL_PROCESS', None)
            os.execv(os.environ['DAW_TEST_METADATA_REAL_HOST'], [os.environ['DAW_TEST_METADATA_REAL_HOST'], *sys.argv[1:]])
        """), encoding="utf-8")
        wrapper.chmod(0o755)
        self.engine.close()
        env = {"DAW_VST3_HOST": str(wrapper), "DAW_TEST_METADATA_MODE": str(control),
               "DAW_TEST_METADATA_REAL_HOST": str(actual_host), "DAW_VST3_FIXTURE_FAIL_PROCESS": "1"}
        for mode, expected in (("unicode", "λ🎵"), ("surrogate", "�"), ("full", "λ" * 128)):
            with patch.dict(os.environ, {**env, "DAW_VST3_FIXTURE_METADATA_TEXT": mode}):
                self.engine = Engine(ROOT / "target/debug/daw")
                self.install_session()
                result = self.inspect()
                self.assertEqual(result["parameters"][0]["name"], expected)
                self.assertEqual(result["revision"], self.engine.call("session.inspect")["revision"])
                self.engine.close()

    @unittest.skipUnless(os.environ.get("DAW_VST3_EFFECT"), "set DAW_VST3_EFFECT to ValhallaFreqEcho")
    def test_installed_valhalla_parameter_48_metadata(self):
        project = session(os.environ["DAW_VST3_EFFECT"], "5653544671456876616C68616C6C6166", 48)
        self.install_session(project)
        result = self.inspect()
        self.assertEqual((result["track_id"], result["effect_id"], len(result["parameters"])), ("lead", "fx", 1))
        parameter = result["parameters"][0]
        self.assertEqual(parameter["id"], 48)
        self.assertIn("wet", parameter["name"].lower())
        self.assertEqual(parameter["default_value"], 0.5)
        self.assertAlmostEqual(parameter["restored_value"], 0.2, places=6)


if __name__ == "__main__":
    unittest.main()

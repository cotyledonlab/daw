"""Real Rust-controller integration tests for schema-v5 offline Audio Units."""
import copy
import math
import plistlib
import os
from pathlib import Path
import struct
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import wave

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT


def session(cutoff=200.0, *, state_hex="", bypass=False, effects=None):
    if effects is None:
        effects = [{"kind": "au", "id": "lowpass", "bypass": bypass,
                    "component_type": "aufx", "component_subtype": "lpas",
                    "component_manufacturer": "appl", "state_hex": state_hex,
                    "parameters": [{"id": 0, "value": cutoff}]}]
    return {"schema_version": 5, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [{"id": "lead", "device": {"kind": "sine", "frequency_hz": 440,
                        "gain": 0.1}, "mode": "continuous", "clips": [], "effects": effects}]}


class DawAudioUnitTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(ROOT / "target/debug/daw")
        if not self.engine.call("capabilities").get("offline_au", {}).get("implemented"):
            self.engine.close()
            self.skipTest("build with --features au-offline on macOS")
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="au-daw-test-", dir=output)
        self.session = session()

    def tearDown(self):
        self.engine.close()
        if hasattr(self, "project"):
            self.project.cleanup()

    def render(self, name, seconds=0.1):
        path = Path(self.project.name) / name
        report = self.engine.call("render", {"path": str(path), "seconds": seconds})
        self.assertEqual(report["sample_rate"], 48000)
        self.assertEqual(report["channels"], 2)
        with wave.open(str(path), "rb") as audio:
            self.assertEqual(audio.getframerate(), 48000)
            self.assertEqual(audio.getnchannels(), 2)
            frames = audio.readframes(audio.getnframes())
        return path, struct.unpack("<" + "h" * (len(frames) // 2), frames)

    @staticmethod
    def rms(samples):
        mono = samples[::2]
        return math.sqrt(sum(value * value for value in mono) / len(mono))

    def test_cutoff_changes_audio_and_saved_state_restores_equivalent_render(self):
        low = self.engine.call("session.replace", {"session": self.session})
        captured = low["tracks"][0]["effects"][0]["state_hex"]
        self.assertTrue(captured)
        _, quiet_samples = self.render("low.wav")

        high_session = session(10000.0)
        self.engine.call("session.replace", {"session": high_session})
        _, open_samples = self.render("high.wav")
        self.assertGreater(self.rms(open_samples), self.rms(quiet_samples) * 1.5)

        saved = Path(self.project.name) / "saved.json"
        self.engine.call("session.replace", {"session": low})
        self.engine.call("session.save", {"path": str(saved)})
        self.engine.call("session.load", {"path": str(saved)})
        _, restored = self.render("restored.wav")
        self.assertEqual(quiet_samples, restored)

    def test_invalid_state_identity_and_parameters_preserve_revision_and_session(self):
        self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        mutations = (
            ("component_type", "aumu"),
            ("component_subtype", "xxxx"),
            ("component_manufacturer", "xxxx"),
            ("state_hex", "not-hex"),
            ("state_hex", "00"),
        )
        for field, value in mutations:
            invalid = copy.deepcopy(self.session)
            invalid["tracks"][0]["effects"][0][field] = value
            with self.subTest(field=field), self.assertRaises(EngineError):
                self.engine.call("session.replace", {"session": invalid})
            self.assertEqual(self.engine.call("session.inspect"), before)

        for parameter in ({"id": 99, "value": 200.0}, {"id": 0, "value": 23761.0},
                          {"id": 0, "value": 1e100}):
            invalid = copy.deepcopy(self.session)
            invalid["tracks"][0]["effects"][0]["parameters"] = [parameter]
            with self.subTest(parameter=parameter), self.assertRaises(EngineError):
                self.engine.call("session.replace", {"session": invalid})
            self.assertEqual(self.engine.call("session.inspect"), before)

    def test_state_identity_and_state_only_restoration(self):
        saved = self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        effect = saved["tracks"][0]["effects"][0]
        state = plistlib.loads(bytes.fromhex(effect["state_hex"]))
        state["subtype"] = int.from_bytes(b"xxxx", "big")
        invalid = copy.deepcopy(saved)
        invalid["tracks"][0]["effects"][0]["state_hex"] = plistlib.dumps(state, fmt=plistlib.FMT_BINARY).hex()
        with self.assertRaises(EngineError):
            self.engine.call("session.replace", {"session": invalid})
        self.assertEqual(self.engine.call("session.inspect"), before)
        _, original = self.render("with-bases.wav")
        effect["parameters"] = []
        self.engine.call("session.replace", {"session": saved})
        _, state_only = self.render("state-only.wav")
        self.assertEqual(original, state_only)
        with self.assertRaises(EngineError) as unavailable:
            self.engine.call("transport.play", {"seconds": 0.1, "volume": 0})
        self.assertIn("offline rendering only", str(unavailable.exception))

    def test_bypass_preserves_serial_gain_behavior(self):
        effects = [{"kind": "gain", "id": "trim", "bypass": False, "gain": 0.5},
                   {"kind": "au", "id": "lowpass", "bypass": True,
                    "component_type": "aufx", "component_subtype": "lpas",
                    "component_manufacturer": "appl", "state_hex": "",
                    "parameters": [{"id": 0, "value": 200.0}]}]
        self.engine.call("session.replace", {"session": session(effects=effects)})
        _, actual = self.render("bypass.wav", 0.01)
        for frame in range(480):
            expected = round(math.sin(2 * math.pi * frame * 440 / 48000) * 0.05 * 32767)
            self.assertLessEqual(abs(actual[frame * 2] - expected), 1)
            self.assertEqual(actual[frame * 2], actual[frame * 2 + 1])

    def test_invalid_and_oversized_worker_responses_leave_no_partial_render(self):
        self._restart_with_fake_host()
        self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        fake = self.fake_host
        output = Path(self.project.name) / "failed.wav"
        for mode in ("invalid", "oversized"):
            self.marker.write_text(mode)
            with self.subTest(response=mode), self.assertRaises(EngineError):
                self.engine.call("render", {"path": str(output), "seconds": 0.001})
            self.assertFalse(output.exists())
            self.assertEqual(self.engine.call("session.inspect"), before)

    def test_worker_timeout_leaves_no_partial_render(self):
        self._restart_with_fake_host()
        self.engine.call("session.replace", {"session": self.session})
        before = self.engine.call("session.inspect")
        self.marker.write_text("timeout")
        output = Path(self.project.name) / "timeout.wav"
        with self.assertRaises(EngineError):
            self.engine.call("render", {"path": str(output), "seconds": 0.001})
        self.assertFalse(output.exists())
        self.assertEqual(self.engine.call("session.inspect"), before)

    def _restart_with_fake_host(self):
        self.engine.close()
        directory = Path(self.project.name)
        real_host = ROOT / "output/au-spike/au-host"
        self.marker = directory / "worker-mode"
        self.fake_host = directory / "dispatch-au-host"
        self.fake_host.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os, sys, time
            marker = {str(self.marker)!r}
            mode = open(marker).read() if os.path.exists(marker) else "delegate"
            if mode == "delegate":
                os.execv({str(real_host)!r}, [{str(real_host)!r}, *sys.argv[1:]])
            if mode == "timeout":
                time.sleep(30)
            if mode == "oversized":
                sys.stdout.buffer.write(b"x" * 4_100_000)
            else:
                sys.stdout.buffer.write(b"bad")
            """))
        self.fake_host.chmod(0o755)
        with patch.dict(os.environ, {"DAW_AU_HOST": str(self.fake_host)}):
            self.engine = Engine(ROOT / "target/debug/daw")


if __name__ == "__main__":
    unittest.main()

"""Schema-v6 SuperCollider source preparation and routing integration tests."""
import copy
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
import wave

from examples.supercollider_tracks_demo import make_track
from gui.server import Engine, EngineError
from native.supercollider.score import SYNTH_NAME, sine_synthdef
from native.vst3.scan import ROOT


SCSYNTH = Path("/Applications/SuperCollider.app/Contents/Resources/scsynth")


def init_rate_sine_synthdef():
    """Return the fixture with scalar init-rate Control outputs."""
    program = sine_synthdef()
    control = b"\x07Control\x01" + struct.pack(">iiH", 0, 3, 0) + bytes((1, 1, 1))
    init_control = b"\x07Control\x00" + struct.pack(">iiH", 0, 3, 0) + bytes((0, 0, 0))
    if program.count(control) != 1:
        raise AssertionError("unexpected Control UGen encoding in sine fixture")
    return program.replace(control, init_control)


class DawSuperColliderSourceTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="sc-source-test-", dir=output)
        self.directory = Path(self.project.name)
        self.engine = None
        self.environment = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        self.project.cleanup()

    def start_engine(self, executable=None):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        selected = executable or SCSYNTH
        self.environment = patch.dict(os.environ, {"DAW_SCSYNTH": str(selected)})
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        return self.engine

    def session(self, tracks=None):
        return {"schema_version": 6, "sample_rate": 48000,
                "tempo_milli_bpm": 120000,
                "tracks": tracks if tracks is not None else [
                    make_track("low", 220.0, 0.12, 0.5, 1.5, 0.6),
                    make_track("high", 330.0, 0.08, 0.5, 1.25, 0.7),
                ]}

    def replace(self, session):
        return self.engine.call("session.replace", {"session": session})

    def state(self):
        return (self.engine.call("session.inspect"),
                self.engine.call("transport.status"))

    def test_installed_sources_save_reload_render_and_retain_control_data(self):
        if not SCSYNTH.is_file():
            self.skipTest(f"installed scsynth not found at {SCSYNTH}")
        self.start_engine(SCSYNTH.resolve())
        wanted = self.session()
        # Keep the second, distinct source silent so the timed pitch change of
        # the first can be measured without frequency interference.
        wanted["tracks"][1]["device"]["gain"] = 0.0
        self.replace(wanted)
        saved = self.engine.call("session.get")
        path = self.directory / "session.json"
        self.engine.call("session.save", {"path": str(path)})
        self.engine.call("session.load", {"path": str(path)})
        restored = self.engine.call("session.get")
        self.assertEqual(restored, saved)
        for expected, actual in zip(wanted["tracks"], restored["tracks"]):
            self.assertEqual(actual["device"]["synth_name"], SYNTH_NAME)
            self.assertEqual(actual["device"]["controls"], expected["device"]["controls"])
            self.assertNotIn("prepared", actual["device"])

        output = self.directory / "sources.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getnframes()),
                             (2, 48000, 48000))
            raw = wav.readframes(wav.getnframes())
        samples = struct.unpack("<{}h".format(len(raw) // 2), raw)
        rms = math.sqrt(sum(v * v for v in samples) / len(samples))
        self.assertGreater(rms, 100)
        left = samples[::2]
        for start, expected_hz in ((0, 220), (24000, 330)):
            window = left[start + 1000:start + 11000]
            crossings = sum(a * b < 0 for a, b in zip(window, window[1:]))
            self.assertAlmostEqual(crossings / 2 / (len(window) / 48000),
                                   expected_hz, delta=30)

    def test_gain_chain_and_automation_render_below_full_scale(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        self.start_engine(SCSYNTH.resolve())
        track = make_track("headroom", 440.0, 0.1, 0.1, 0.5, 1.0)
        next(control for control in track["device"]["controls"]
             if control["name"] == "gain")["values"] = [3.0]
        track["effects"] = [{"kind": "gain", "id": "shape", "gain": 0.5,
                             "bypass": False}]
        track["automation"] = [{"effect_id": "shape", "parameter": "gain",
                                "interpolation": "step",
                                "points": [{"frame": 24000, "value": 0.25}]}]
        self.replace(self.session([track]))
        output = self.directory / "headroom.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as wav:
            raw = wav.readframes(wav.getnframes())
        samples = struct.unpack("<{}h".format(len(raw) // 2), raw)
        self.assertGreater(max(map(abs, samples)), 500)
        # The saved synth gain is 3.0, but the source gain and serial effect
        # keep prepared float PCM well below full scale before WAV encoding.
        self.assertLess(max(map(abs, samples)), 8000)

    def test_init_rate_defaults_saved_overrides_and_frame_zero_override(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable for init-rate control check")
        self.start_engine(SCSYNTH.resolve())
        program = init_rate_sine_synthdef()
        track = make_track("init", 440.0, 0.1, 1.0, 1.0, 1.0)
        source = track["device"]
        source["synthdef_hex"] = program.hex()
        source["controls"] = [
            {"name": "freq", "values": [660.0], "points": []},
            {"name": "gain", "values": [0.3], "points": []},
            {"name": "out", "values": [0.0], "points": []},
        ]
        self.replace(self.session([track]))
        baseline_path = self.directory / "init-base.wav"
        self.engine.call("render", {"path": str(baseline_path), "seconds": 1.0})
        with wave.open(str(baseline_path), "rb") as wav:
            baseline = struct.unpack("<{}h".format(wav.getnframes() * 2),
                                     wav.readframes(wav.getnframes()))[::2]
        window = baseline[4800:24000]
        measured_hz = max(
            range(400, 801, 2),
            key=lambda frequency: sum(
                sample * math.cos(2 * math.pi * frequency * index / 48000)
                for index, sample in enumerate(window)) ** 2 + sum(
                sample * math.sin(2 * math.pi * frequency * index / 48000)
                for index, sample in enumerate(window)) ** 2,
        )
        self.assertAlmostEqual(measured_hz, 660, delta=8)
        baseline_rms = math.sqrt(sum(v * v for v in baseline) / len(baseline))

        source["controls"][1]["points"] = [{"frame": 0, "values": [0.2]}]
        self.replace(self.session([track]))
        override_path = self.directory / "init-frame-zero.wav"
        self.engine.call("render", {"path": str(override_path), "seconds": 1.0})
        with wave.open(str(override_path), "rb") as wav:
            overridden = struct.unpack("<{}h".format(wav.getnframes() * 2),
                                       wav.readframes(wav.getnframes()))[::2]
        override_rms = math.sqrt(sum(v * v for v in overridden) / len(overridden))
        self.assertGreater(override_rms, baseline_rms * 0.55)
        self.assertLess(override_rms, baseline_rms * 0.8)

    def test_init_rate_control_rejects_later_points_before_commit(self):
        self.start_engine(SCSYNTH if SCSYNTH.is_file() else None)
        seed = {"schema_version": 1, "sample_rate": 48000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 440.0, "gain": 0.1}}]}
        self.replace(seed)
        before = self.state()
        track = make_track("init-late", 440, .1, .2, 1, 1)
        track["device"]["synthdef_hex"] = init_rate_sine_synthdef().hex()
        track["device"]["controls"][0]["points"] = [
            {"frame": 1, "values": [660.0]}]
        with self.assertRaisesRegex(EngineError, "initialization-rate controls"):
            self.replace(self.session([track]))
        self.assertEqual(self.state(), before)

    def test_invalid_schema_and_source_contracts_rollback_active_session(self):
        self.start_engine(SCSYNTH if SCSYNTH.is_file() else None)
        seed = {"schema_version": 1, "sample_rate": 48000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 440.0, "gain": 0.1}}]}
        self.replace(seed)
        baseline = self.state()
        valid = self.session([make_track("candidate", 220, 0.1, 0.2, 1, 1)])

        cases = []
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["controls"][0]["name"] = "missing"
        cases.append(("unknown control", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["synth_name"] = "wrong_name"
        cases.append(("name mismatch", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["controls"][0]["values"] = [220, 221]
        cases.append(("array arity", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["controls"][0]["values"] = [1e300]
        cases.append(("float32 overflow", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["synthdef_hex"] = "not-hex"
        cases.append(("malformed definition", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["synthdef_hex"] = "53436766"
        cases.append(("truncated definition", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["unexpected"] = True
        cases.append(("unknown field", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["prepared"] = {}
        cases.append(("cache injection", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["device"]["controls"][0]["unknown"] = 1
        cases.append(("unknown control field", changed))
        changed = copy.deepcopy(valid); changed["schema_version"] = 5
        cases.append(("old schema", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["mode"] = None
        cases.append(("null mode", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["clips"] = None
        cases.append(("null clips", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0].pop("effects")
        cases.append(("missing effects", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["mode"] = "sequenced"
        cases.append(("non-continuous mode", changed))
        changed = copy.deepcopy(valid); changed["tracks"][0]["clips"] = [{"kind": "notes"}]
        cases.append(("nonempty clips", changed))
        cases.append(("too many sources", self.session([
            make_track(f"s{i}", 200 + i * 10, .1, .1, 1, 1) for i in range(5)])))
        long_tracks = [make_track(f"long{i}", 200 + i * 10, .1, .1, 1, 1)
                       for i in range(2)]
        for track in long_tracks:
            track["device"]["duration_frames"] = 240001
        cases.append(("total duration", self.session(long_tracks)))
        changed = copy.deepcopy(valid)
        control = changed["tracks"][0]["device"]["controls"][0]
        control["points"] = [{"frame": i + 1, "values": [220 + i]} for i in range(513)]
        cases.append(("too many points", changed))

        for name, candidate in cases:
            with self.subTest(name=name), self.assertRaises(EngineError):
                self.replace(candidate)
            self.assertEqual(self.state(), baseline, name)

    def test_unavailable_runtime_preserves_session_revision_and_transport(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable for unknown-UGen runtime check")
        self.start_engine(SCSYNTH.resolve())
        seed = {"schema_version": 1, "sample_rate": 48000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 440.0, "gain": 0.1}}]}
        self.replace(seed)
        before = self.state()
        candidate = self.session([make_track("candidate", 220, .1, .2, 1, 1)])
        candidate["tracks"][0]["device"]["synthdef_hex"] = sine_synthdef().replace(
            b"SinOsc", b"BadOsc").hex()
        with self.assertRaises(EngineError):
            self.replace(candidate)
        self.assertEqual(self.state(), before)

    def test_child_failure_preserves_session_revision_and_transport(self):
        worker = self.directory / "failing-scsynth"
        worker.write_text("#!/bin/sh\nexit 17\n")
        worker.chmod(0o755)
        self.start_engine(worker)
        seed = {"schema_version": 1, "sample_rate": 48000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 440.0, "gain": 0.1}}]}
        self.replace(seed)
        before = self.state()
        candidate = self.session([make_track("candidate", 220, .1, .2, 1, 1)])
        with self.assertRaises(EngineError):
            self.replace(candidate)
        self.assertEqual(self.state(), before)

    def test_invalid_float_worker_output_preserves_the_active_project(self):
        mode_path = self.directory / "float-mode"
        worker = self.directory / "float-worker"
        worker.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import struct, sys
            mode = open({str(mode_path)!r}).read()
            args = sys.argv[1:]
            index = args.index('-N')
            destination = args[index+3]
            rate = int(args[index+4])
            frames = 48000
            if mode == 'pcm':
                payload = bytes(frames*4)
                format_tag, bits = 1, 16
            else:
                sample = float('nan') if mode == 'nan' else 0.0
                payload = struct.pack('<f',sample)*frames*2
                format_tag, bits = 3, 32
            block = bits//8*2
            if mode == 'rate': rate += 1
            header = (b'RIFF'+struct.pack('<I',36+len(payload))+b'WAVEfmt '
                      +struct.pack('<IHHIIHH',16,format_tag,2,rate,rate*block,block,bits)
                      +b'data'+struct.pack('<I',len(payload)))
            if mode == 'truncated': payload = payload[:-4]
            open(destination,'wb').write(header+payload)
            """))
        worker.chmod(0o755)
        self.start_engine(worker)
        self.replace({"schema_version":1,"sample_rate":48000,"tracks":[{
            "id":"kept","device":{"kind":"sine","frequency_hz":330,"gain":0.1}}]})
        before = self.state()
        for mode in ('nan','pcm','rate','truncated'):
            mode_path.write_text(mode)
            with self.subTest(mode=mode), self.assertRaises(EngineError):
                self.replace(self.session([make_track('candidate',220,.1,.2,1,1)]))
            self.assertEqual(self.state(),before)

    def test_prepared_pcm_cache_renders_after_worker_executable_is_removed(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        real = SCSYNTH.resolve()
        wrapper = self.directory / "scsynth-wrapper"
        marker = self.directory / "invoked"
        wrapper.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo invoked >> {str(marker)!r}
            exec {str(real)!r} "$@"
        """))
        wrapper.chmod(0o755)
        self.start_engine(wrapper)
        self.replace(self.session([make_track("cached", 440, .1, .2, 1, 1)]))
        self.assertTrue(marker.is_file())
        calls = marker.read_text().splitlines()
        wrapper.unlink()
        before_path = self.directory / "cached-before.wav"
        self.engine.call("render", {"path": str(before_path), "seconds": 1.0})
        with wave.open(str(before_path), "rb") as wav:
            before_pcm = struct.unpack("<{}h".format(wav.getnframes() * 2),
                                       wav.readframes(wav.getnframes()))
        revision = self.engine.call("session.inspect")["revision"]
        self.engine.call("session.edit", {
            "expected_revision": revision,
            "operations": [{"op": "set_parameter", "track_id": "cached",
                            "parameter": "gain", "value": 0.05}],
        })
        output = self.directory / "cached.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        self.assertEqual(marker.read_text().splitlines(), calls)
        self.assertTrue(output.is_file())
        with wave.open(str(output), "rb") as wav:
            after_pcm = struct.unpack("<{}h".format(wav.getnframes() * 2),
                                      wav.readframes(wav.getnframes()))
        self.assertLess(max(map(abs, after_pcm)), max(map(abs, before_pcm)) * 0.3)

        changed = self.engine.call("session.get")
        changed["tracks"][0]["device"]["controls"][0]["values"] = [550.0]
        before_failure = self.state()
        with self.assertRaises(EngineError):
            self.replace(changed)
        self.assertEqual(self.state(), before_failure)

    @unittest.skipUnless(os.environ.get("DAW_TEST_NATIVE_AUDIO") == "1",
                         "opt-in native hardware check")
    def test_native_transport_prepares_source_and_reports_no_serialized_cache(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        self.start_engine(SCSYNTH.resolve())
        native = make_track("native", 440, .1, .1, 1, 1)
        next(control for control in native["device"]["controls"]
             if control["name"] == "gain")["values"] = [3.0]
        self.replace(self.session([native]))
        saved = self.engine.call("session.get")
        self.assertNotIn("prepared", json.dumps(saved))
        self.engine.call("transport.play", {"seconds": .5, "volume": 0})
        deadline = time.monotonic() + 1
        status = self.engine.call("transport.status")
        while time.monotonic() < deadline and status.get("submitted_frames", 0) == 0:
            time.sleep(.02)
            status = self.engine.call("transport.status")
        self.assertGreater(status.get("submitted_frames", 0), 0)
        self.engine.call("transport.stop")


if __name__ == "__main__":
    unittest.main()

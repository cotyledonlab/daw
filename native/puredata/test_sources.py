"""Schema-v8 Pure Data worker and prepared-source integration tests."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
import wave

from gui.server import Engine, EngineError
from native.puredata import block_probe, source_worker
from native.vst3.scan import ROOT

LIBPD = os.environ.get("DAW_LIBPD_LIBRARY")
CSOUND = os.environ.get("DAW_CSOUND_LIBRARY")


class DawPureDataSourceTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="puredata-source-test-", dir=output)
        self.directory = Path(self.project.name)
        self.library = self.directory / "fake-libpd.dylib"
        self.library.write_bytes(b"fake libpd marker")
        self.worker = self.directory / "fake-worker.py"
        self.mode = self.directory / "mode"
        self.marker = self.directory / "calls.jsonl"
        self.child_pid = self.directory / "descendant.pid"
        self.engine = None
        self.environment = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        self.project.cleanup()

    @classmethod
    def source(cls, rate=48_000, duration=49_152, gain=0.5,
               frequency=440.0, amplitude=0.1, points=True):
        return {
            "kind": "puredata",
            "program": block_probe.FIXTURE.read_text(encoding="utf-8"),
            "abstractions": [{"name": "daw-offset",
                              "program": block_probe.ABSTRACTION.read_text(encoding="utf-8")}],
            "duration_frames": duration,
            "gain": gain,
            "controls": [
                {"name": "$0-frequency", "value": frequency,
                 "points": ([{"frame": 24_576, "value": 660.0}] if points else [])},
                {"name": "$0-amplitude", "value": amplitude,
                 "points": ([{"frame": 24_576, "value": 0.05}] if points else [])},
            ],
        }

    @classmethod
    def track(cls, track_id="pd", **kwargs):
        return {"id": track_id, "device": cls.source(**kwargs),
                "mode": "continuous", "clips": [], "effects": [], "automation": []}

    @classmethod
    def session(cls, tracks=None, rate=48_000):
        return {"schema_version": 8, "sample_rate": rate, "tempo_milli_bpm": 120_000,
                "tracks": tracks if tracks is not None else [cls.track()]}

    def start_engine(self, worker=None, library=None, extra_env=None):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        env = {
            "DAW_LIBPD_LIBRARY": str(library or self.library),
            "DAW_LIBPD_WORKER": str(worker or ROOT / "native/puredata/source_worker.py"),
            "DAW_LIBPD_PYTHON": os.path.realpath(sys.executable),
        }
        if extra_env:
            env.update(extra_env)
        self.environment = patch.dict(os.environ, env)
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        return self.engine

    def replace(self, value):
        return self.engine.call("session.replace", {"session": value})

    def state(self):
        return (self.engine.call("session.get"), self.engine.call("session.inspect"),
                self.engine.call("transport.status"))

    def fake_worker(self, mode="ok"):
        marker, mode_path, pid_path = map(str, (self.marker, self.mode, self.child_pid))
        self.worker.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(f"""
            import json, math, os, struct, sys, time
            job_path, destination, library = sys.argv[1:]
            assert os.path.isabs(job_path) and os.path.isabs(destination)
            assert os.path.isabs(library)
            job = json.load(open(job_path))
            with open({marker!r}, 'a') as stream:
                stream.write(json.dumps(job, allow_nan=False) + "\\n")
            mode = open({mode_path!r}).read() if os.path.exists({mode_path!r}) else {mode!r}
            if mode == 'crash': sys.exit(17)
            if mode == 'hang':
                child = os.fork()
                if child == 0:
                    time.sleep(60); os._exit(0)
                with open({pid_path!r}, 'w') as stream: stream.write(str(child))
                time.sleep(60)
            if mode == 'logoverflow': sys.stderr.write('x' * 100000); sys.stderr.flush()
            source = job['source']; frames = source['duration_frames']; rate = job['sample_rate']
            if mode == 'wrongheader': header = b'BAD!'
            else: header = b'DPD1' + struct.pack('<IQI', rate + (1 if mode == 'wrongrate' else 0), frames, 2)
            pcm = bytearray()
            for frame in range(frames):
                frequency, amplitude = 440.0, 0.1
                for point in source['controls'][0]['points']:
                    if point['frame'] <= frame: frequency = point['value']
                for point in source['controls'][1]['points']:
                    if point['frame'] <= frame: amplitude = point['value']
                sample = amplitude * math.sin(2 * math.pi * frequency * frame / rate)
                if mode == 'nonfinite' and frame == 7: sample = float('nan')
                pcm.extend(struct.pack('<dd', sample, sample))
            if mode == 'symlink': os.symlink('/dev/null', destination)
            else:
                if mode == 'truncated': pcm = pcm[:-16]
                with open(destination, 'xb') as stream: stream.write(header + pcm)
        """))
        self.worker.chmod(0o755)
        return self.worker

    def test_public_job_validation_enforces_fields_and_bounds(self):
        source = self.source()
        job = {"job_version": 1, "sample_rate": 48_000,
               "source": {key: value for key, value in source.items() if key != "kind"}}
        self.assertEqual(source_worker.validate_job(job), (48_000, job["source"]))
        invalid = []
        bad = copy.deepcopy(job); bad["job_version"] = 2; invalid.append(bad)
        bad = copy.deepcopy(job); bad["extra"] = True; invalid.append(bad)
        bad = copy.deepcopy(job); bad["sample_rate"] = 7_999; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["extra"] = True; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["program"] = ""; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["program"] = "x\0y"; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["program"] = "x" * 61_441; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["duration_frames"] = 47; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["gain"] = 1.1; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["abstractions"].append(
            {"name": "../escape", "program": "x"}); invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["abstractions"][0]["program"] = ""; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["abstractions"] = [
            {"name": f"a{i}", "program": "x"} for i in range(17)]; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["program"] = "p" * 40_000; bad["source"]["abstractions"] = [
            {"name": "extra", "program": "a" * 21_441}]; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"][0]["value"] = 1e100; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"][0]["value"] = float("nan"); invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"][0]["points"] = [
            {"frame": 64, "value": 0.0}, {"frame": 64, "value": 1.0}]; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"][0]["points"] = [
            {"frame": 49_152, "value": 0.0}]; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"][0]["points"] = [
            {"frame": 64, "value": 1e100}]; invalid.append(bad)
        bad = copy.deepcopy(job); bad["source"]["controls"].append(
            copy.deepcopy(bad["source"]["controls"][0])); invalid.append(bad)
        for candidate in invalid:
            with self.subTest(candidate=str(candidate)[:140]), self.assertRaises(ValueError):
                source_worker.validate_job(candidate)

    def test_worker_load_rejects_symlink_job_and_prepare_never_overwrites_output(self):
        source = self.source()
        job = {"job_version": 1, "sample_rate": 48_000,
               "source": {key: value for key, value in source.items() if key != "kind"}}
        job_path = self.directory / "job.json"
        job_path.write_text(json.dumps(job, allow_nan=False))
        link = self.directory / "job-link.json"
        link.symlink_to(job_path)
        with self.assertRaises(OSError):
            source_worker.load_job(link)

        output = self.directory / "existing.pcm"
        output.write_bytes(b"keep-existing")
        with self.assertRaises(FileExistsError):
            source_worker.prepare(job_path, output, self.library)
        self.assertEqual(output.read_bytes(), b"keep-existing")

    def test_fake_worker_prepares_report_shape_routes_gain_and_roundtrips(self):
        self.start_engine(self.fake_worker())
        wanted = self.session([self.track("low", gain=0.5), self.track("silent", gain=0.0)])
        wanted["tracks"][0]["effects"] = [{"kind": "gain", "id": "trim",
                                             "gain": 0.5, "bypass": False}]
        self.replace(wanted)
        saved = self.engine.call("session.get")
        path = self.directory / "session.json"
        self.engine.call("session.save", {"path": str(path)})
        self.engine.call("session.load", {"path": str(path)})
        self.assertEqual(self.engine.call("session.get"), saved)
        self.assertEqual(saved["tracks"][0]["device"]["abstractions"][0]["name"], "daw-offset")
        self.assertNotIn("prepared", json.dumps(saved))
        jobs = [json.loads(line) for line in self.marker.read_text().splitlines()]
        self.assertEqual(len(jobs), 4)
        self.assertEqual([job["sample_rate"] for job in jobs], [48_000] * 4)
        output = self.directory / "fake.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as stream:
            self.assertEqual((stream.getnchannels(), stream.getframerate(), stream.getnframes()),
                             (2, 48_000, 48_000))
            pcm = struct.unpack("<{}h".format(stream.getnframes() * 2), stream.readframes(48_000))
        self.assertGreater(max(map(abs, pcm)), 100)
        self.assertLess(max(map(abs, pcm)), 8_000)

    def test_fake_worker_invalid_headers_pcm_failures_and_symlink_rollback(self):
        self.start_engine(self.fake_worker())
        self.engine.call("session.replace", {"session": {
            "schema_version": 1, "sample_rate": 48_000,
            "tracks": [{"id": "kept", "device": {"kind": "sine",
                        "frequency_hz": 330.0, "gain": 0.1}}]}})
        baseline = self.state()
        for mode in ("crash", "wrongheader", "wrongrate", "truncated", "nonfinite", "symlink", "logoverflow"):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                with self.assertRaises(EngineError):
                    self.replace(self.session())
                self.assertEqual(self.state(), baseline)

    def test_invalid_source_contract_rolls_back_without_worker_launch(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        self.engine.call("session.replace", {"session": {
            "schema_version": 1, "sample_rate": 48_000,
            "tracks": [{"id": "kept", "device": {"kind": "sine",
                        "frequency_hz": 330.0, "gain": 0.1}}]}})
        baseline = self.state()
        cases = []
        bad = self.session(); bad["schema_version"] = 7; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["duration_frames"] = 47; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["abstractions"][0]["name"] = "../bad"; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["controls"][0]["points"][0]["frame"] = 1; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["controls"][0]["value"] = 1e100; cases.append(bad)
        for index, candidate in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(EngineError):
                self.replace(candidate)
            self.assertEqual(self.state(), baseline)
        self.assertFalse(self.marker.exists())

    def test_fake_worker_deadline_reaps_descendant_and_preserves_active_state(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        baseline = self.state()
        self.mode.write_text("hang")
        started = time.monotonic()
        with self.assertRaisesRegex(EngineError, "timed out"):
            self.replace(self.session())
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.state(), baseline)
        pid = int(self.child_pid.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"Pure Data worker descendant {pid} survived deadline cleanup")

    @unittest.skipUnless(LIBPD and Path(LIBPD).is_absolute() and Path(LIBPD).is_file(),
                         "set DAW_LIBPD_LIBRARY for installed libpd source coverage")
    def test_installed_fixture_roundtrips_and_renders_frequency_gain_and_abstraction(self):
        self.start_engine(library=Path(LIBPD))
        source = self.source(duration=49_152, gain=0.5)
        wanted = self.session([self.track("measured", duration=49_152, gain=0.5)])
        wanted["tracks"][0]["effects"] = [{"kind": "gain", "id": "trim",
                                             "gain": 0.5, "bypass": False}]
        self.replace(wanted)
        before = self.engine.call("session.get")
        path = self.directory / "measured.json"
        self.engine.call("session.save", {"path": str(path)})
        self.engine.call("session.load", {"path": str(path)})
        self.assertEqual(self.engine.call("session.get"), before)
        self.assertEqual(before["tracks"][0]["device"]["program"], source["program"])
        output = self.directory / "measured.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as stream:
            self.assertEqual((stream.getnchannels(), stream.getframerate(), stream.getnframes()),
                             (2, 48_000, 48_000))
            pcm = struct.unpack("<{}h".format(stream.getnframes() * 2), stream.readframes(48_000))
        left = pcm[::2]
        for start, expected in ((2_000, 440), (30_000, 660)):
            window = left[start:start + 8_000]
            crossings = [index for index in range(1, len(window))
                         if window[index - 1] <= 0 < window[index]]
            self.assertGreater(len(crossings), 3)
            measured = (len(crossings) - 1) * 48_000 / (crossings[-1] - crossings[0])
            self.assertAlmostEqual(measured, expected, delta=2)
        peak = max(map(abs, left))
        self.assertGreater(peak, 650)
        self.assertLess(peak, 1_000)

    @unittest.skipUnless(LIBPD and Path(LIBPD).is_absolute() and Path(LIBPD).is_file(),
                         "set DAW_LIBPD_LIBRARY for installed libpd partial/rate coverage")
    def test_installed_worker_trims_partial_block_and_uses_requested_44100_rate(self):
        self.start_engine(library=Path(LIBPD))
        partial = self.session([self.track("partial", duration=48_001, points=False)])
        self.replace(partial)
        partial_wav = self.directory / "partial.wav"
        self.engine.call("render", {"path": str(partial_wav), "seconds": 1.1})
        with wave.open(str(partial_wav), "rb") as stream:
            self.assertEqual(stream.getnframes(), int(48_000 * 1.1))
            samples = struct.unpack("<{}h".format(stream.getnframes() * 2),
                                    stream.readframes(stream.getnframes()))[::2]
        self.assertTrue(any(samples[:48_001]))
        self.assertTrue(all(sample == 0 for sample in samples[48_001:]))

        wanted = self.session([self.track("rate", duration=44_100, points=False)], rate=44_100)
        self.replace(wanted)
        rate_wav = self.directory / "rate.wav"
        self.engine.call("render", {"path": str(rate_wav), "seconds": 1.0})
        with wave.open(str(rate_wav), "rb") as stream:
            self.assertEqual((stream.getframerate(), stream.getnframes()), (44_100, 44_100))
            samples = struct.unpack("<{}h".format(stream.getnframes() * 2),
                                    stream.readframes(stream.getnframes()))[::2]
        crossings = [index for index in range(1, len(samples))
                     if samples[index - 1] <= 0 < samples[index]]
        measured = (len(crossings) - 1) * 44_100 / (crossings[-1] - crossings[0])
        self.assertAlmostEqual(measured, 440, delta=2)

    @unittest.skipUnless(LIBPD and Path(LIBPD).is_absolute() and Path(LIBPD).is_file(),
                         "set DAW_LIBPD_LIBRARY for installed libpd transaction tests")
    def test_installed_missing_receiver_unknown_object_and_abstraction_errors_rollback(self):
        self.start_engine(library=Path(LIBPD))
        seed = {"schema_version": 1, "sample_rate": 48_000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 330.0, "gain": 0.1}}]}
        self.engine.call("session.replace", {"session": seed})
        baseline = self.state()
        missing_receiver = self.session()
        missing_receiver["tracks"][0]["device"]["controls"][0]["name"] = "$0-not-a-receiver"
        unknown_object = self.session()
        unknown_object["tracks"][0]["device"]["program"] = (
            unknown_object["tracks"][0]["device"]["program"].replace("osc~", "definitely_unknown_pd_object"))
        bad_abstraction = self.session()
        bad_abstraction["tracks"][0]["device"]["abstractions"][0]["program"] = (
            "#N canvas 0 0 100 100 10;\n#X obj 0 0 definitely_unknown_pd_object;")
        for index, candidate in enumerate((missing_receiver, unknown_object, bad_abstraction)):
            with self.subTest(index=index), self.assertRaises(EngineError):
                self.replace(candidate)
            self.assertEqual(self.state(), baseline)

    @unittest.skipUnless(LIBPD and Path(LIBPD).is_absolute() and Path(LIBPD).is_file(),
                         "set DAW_LIBPD_LIBRARY for installed frame-zero event coverage")
    def test_installed_frame_zero_override_precedes_later_control_events(self):
        self.start_engine(library=Path(LIBPD))
        wanted = self.session([self.track(duration=49_152, amplitude=0.1, points=False)])
        frequency = wanted["tracks"][0]["device"]["controls"][0]
        frequency["points"] = [{"frame": 0, "value": 660.0},
                                {"frame": 24_576, "value": 330.0}]
        self.replace(wanted)
        output = self.directory / "frame-zero.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as stream:
            samples = struct.unpack("<{}h".format(stream.getnframes() * 2),
                                    stream.readframes(stream.getnframes()))[::2]
        for start, expected in ((2_000, 660), (30_000, 330)):
            window = samples[start:start + 8_000]
            crossings = [index for index in range(1, len(window))
                         if window[index - 1] <= 0 < window[index]]
            measured = (len(crossings) - 1) * 48_000 / (crossings[-1] - crossings[0])
            self.assertAlmostEqual(measured, expected, delta=2)

    @unittest.skipUnless(LIBPD and Path(LIBPD).is_absolute() and Path(LIBPD).is_file()
                         and CSOUND and Path(CSOUND).is_absolute() and Path(CSOUND).is_file(),
                         "set DAW_LIBPD_LIBRARY and DAW_CSOUND_LIBRARY for mixed-source coverage")
    def test_mixed_csound_and_puredata_sources_prepare_and_render(self):
        from examples.csound_tracks_demo import track as make_csound_track

        self.start_engine(library=Path(LIBPD), extra_env={"DAW_CSOUND_LIBRARY": CSOUND})
        csound_track = make_csound_track("csound", 220.0, 0.5)
        wanted = self.session([self.track("pd", duration=48_000), csound_track])
        self.replace(wanted)
        output = self.directory / "mixed.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        self.assertTrue(output.is_file())
        with wave.open(str(output), "rb") as stream:
            self.assertEqual((stream.getframerate(), stream.getnframes()), (48_000, 48_000))


if __name__ == "__main__":
    unittest.main()

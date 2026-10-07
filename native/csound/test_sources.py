"""Schema-v7 Csound source preparation and routing integration tests."""
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

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT


class DawCsoundSourceTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="csound-source-test-", dir=output)
        self.directory = Path(self.project.name)
        self.library = self.directory / "libcsound-test.so"
        self.library.write_bytes(b"fake libcsound marker")
        self.worker = self.directory / "fake-worker.py"
        self.mode = self.directory / "mode"
        self.marker = self.directory / "calls.jsonl"
        self.engine = None
        self.environment = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        self.project.cleanup()

    @staticmethod
    def csd(frequency=440):
        return textwrap.dedent(f"""\
            <CsoundSynthesizer>
            <CsOptions>
            </CsOptions>
            <CsInstruments>
            sr = 48000
            ksmps = 64
            nchnls = 2
            0dbfs = 1
            chn_k "frequency", 1
            chn_k "amplitude", 1
            instr 1
              kfreq chnget "frequency"
              kamp chnget "amplitude"
              asig oscili kamp, kfreq
              outs asig, asig
            endin
            </CsInstruments>
            <CsScore>
            i1 0 1
            e
            </CsScore>
            </CsoundSynthesizer>
        """)

    @classmethod
    def source(cls, frequency=440.0, amplitude=0.1, gain=1.0, duration=48000):
        return {"kind": "csound", "program": cls.csd(frequency),
                "duration_frames": duration, "gain": gain,
                "controls": [
                    {"name": "frequency", "value": frequency,
                     "points": [{"frame": 24576, "value": 660.0}]},
                    {"name": "amplitude", "value": amplitude, "points": []},
                ]}

    @classmethod
    def track(cls, track_id, frequency, amplitude=0.1, gain=1.0):
        return {"id": track_id, "device": cls.source(frequency, amplitude, gain),
                "mode": "continuous", "clips": [], "effects": [],
                "automation": []}

    @classmethod
    def session(cls, tracks=None):
        return {"schema_version": 7, "sample_rate": 48000,
                "tempo_milli_bpm": 120000,
                "tracks": tracks if tracks is not None else [cls.track("source", 440)]}

    def start_engine(self, worker=None, library=None):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        env = {"DAW_CSOUND_LIBRARY": str(library or self.library)}
        if worker is not None:
            env["DAW_CSOUND_WORKER"] = str(worker)
        env["DAW_CSOUND_PYTHON"] = os.path.realpath(os.sys.executable)
        self.environment = patch.dict(os.environ, env)
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        return self.engine

    def replace(self, value):
        return self.engine.call("session.replace", {"session": value})

    def state(self):
        return self.engine.call("session.get"), self.engine.call("session.inspect"), \
            self.engine.call("transport.status")

    def write_worker(self, body):
        self.worker.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        self.worker.chmod(0o755)
        return self.worker

    def fake_worker(self, mode="ok"):
        marker, mode_path = str(self.marker), str(self.mode)
        return self.write_worker(f"""
            import json, math, os, struct, sys, time
            job_path, destination, library = sys.argv[1:]
            assert os.path.isabs(job_path) and os.path.isabs(destination)
            assert os.path.isabs(library)
            job = json.load(open(job_path))
            with open({marker!r}, 'a') as stream:
                stream.write(json.dumps(job, allow_nan=False) + "\\n")
            mode = open({mode_path!r}).read() if os.path.exists({mode_path!r}) else {mode!r}
            if mode == 'crash': sys.exit(17)
            if mode == 'hang': time.sleep(45)
            if mode == 'logoverflow': sys.stderr.write('x' * 100000)
            source = job['source']; frames = source['duration_frames']; rate = job['sample_rate']
            if mode == 'wrongheader':
                header = b'BAD!'
            else:
                header = b'DCS1' + struct.pack('<IQI', rate + (1 if mode == 'wrongrate' else 0),
                                                 frames + (1 if mode == 'wronglen' else 0), 2)
            controls = {{item['name']: item for item in source['controls']}}
            frequency = controls['frequency']; amplitude = controls['amplitude']
            output = bytearray()
            for frame in range(frames):
                f = frequency['value']
                a = amplitude['value']
                for point in frequency['points']:
                    if point['frame'] <= frame: f = point['value']
                for point in amplitude['points']:
                    if point['frame'] <= frame: a = point['value']
                sample = a * math.sin(2 * math.pi * f * frame / rate)
                if mode == 'nonfinite' and frame == 7: sample = float('nan')
                output.extend(struct.pack('<dd', sample, sample))
            if mode == 'symlink':
                os.symlink('/dev/null', destination)
            else:
                with open(destination, 'xb') as stream: stream.write(header + output)
        """)

    def test_fake_worker_prepares_saved_sources_and_keeps_gain_routing(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        caps = self.engine.call("capabilities")
        self.assertTrue(caps["csound_sources"]["implemented"])
        self.assertEqual(caps["csound_sources"]["schema_version"], 7)
        self.assertFalse(caps["csound_sources"]["interactive_dsp"])
        wanted = self.session([self.track("low", 220, gain=0.5),
                               self.track("high", 330, amplitude=0.0)])
        wanted["tracks"][0]["effects"] = [{"kind": "gain", "id": "trim",
                                             "gain": 0.5, "bypass": False}]
        wanted["tracks"][0]["automation"] = [{"effect_id": "trim", "parameter": "gain",
            "interpolation": "step", "points": [{"frame": 24576, "value": 0.25}]}]
        self.replace(wanted)
        saved = self.engine.call("session.get")
        session_path = self.directory / "saved.json"
        self.engine.call("session.save", {"path": str(session_path)})
        self.engine.call("session.load", {"path": str(session_path)})
        restored = self.engine.call("session.get")
        self.assertEqual(restored, saved)
        self.assertNotIn("prepared", json.dumps(restored))
        self.assertEqual(restored["tracks"][0]["effects"], wanted["tracks"][0]["effects"])
        self.assertEqual(restored["tracks"][0]["automation"], wanted["tracks"][0]["automation"])
        jobs = [json.loads(line) for line in self.marker.read_text().splitlines()]
        self.assertEqual(len(jobs), 4)  # two tracks prepared on replace and reload
        self.assertEqual([job["job_version"] for job in jobs], [1] * 4)
        self.assertEqual([job["source"]["program"] for job in jobs],
                         [self.csd(220), self.csd(330)] * 2)
        output = self.directory / "render.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        with wave.open(str(output), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getnframes()),
                             (2, 48000, 48000))
            pcm = struct.unpack("<{}h".format(wav.getnframes() * 2), wav.readframes(48000))
        self.assertGreater(max(map(abs, pcm)), 100)
        self.assertLess(max(map(abs, pcm)), 8000)

    def test_gain_only_edit_reuses_prepared_pcm_after_runtime_is_removed(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        self.replace(self.session())
        calls = self.marker.read_text().splitlines()
        worker.unlink()
        self.library.unlink()
        revision = self.engine.call("session.inspect")["revision"]
        self.engine.call("session.edit", {"expected_revision": revision,
            "operations": [{"op": "set_parameter", "track_id": "source",
                            "parameter": "gain", "value": 0.25}]})
        output = self.directory / "cached.wav"
        self.engine.call("render", {"path": str(output), "seconds": 1.0})
        self.assertEqual(self.marker.read_text().splitlines(), calls)
        self.assertTrue(output.is_file())
        changed = self.engine.call("session.get")
        mutations = []
        controlled = json.loads(json.dumps(changed))
        controlled["tracks"][0]["device"]["controls"][0]["value"] = 550.0
        mutations.append(controlled)
        reprogrammed = json.loads(json.dumps(changed))
        reprogrammed["tracks"][0]["device"]["program"] += "\n; changed program\n"
        mutations.append(reprogrammed)
        redurationed = json.loads(json.dumps(changed))
        redurationed["tracks"][0]["device"]["duration_frames"] = 47936
        mutations.append(redurationed)
        for candidate in mutations:
            before = self.state()
            with self.assertRaises(EngineError):
                self.replace(candidate)
            self.assertEqual(self.state(), before)

    def test_bad_worker_results_preserve_active_session_revision_and_transport(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        self.replace({"schema_version": 1, "sample_rate": 48000,
            "tracks": [{"id": "kept", "device": {"kind": "sine",
                "frequency_hz": 330.0, "gain": 0.1}}]})
        baseline = self.state()
        for mode in ("crash", "wrongheader", "wrongrate", "wronglen", "nonfinite", "symlink",
                     "logoverflow"):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                with self.assertRaises(EngineError):
                    self.replace(self.session())
                self.assertEqual(self.state(), baseline)

    def test_invalid_source_contracts_rollback_without_worker_launch(self):
        worker = self.fake_worker()
        self.start_engine(worker)
        self.replace({"schema_version": 1, "sample_rate": 48000,
            "tracks": [{"id": "kept", "device": {"kind": "sine",
                "frequency_hz": 330.0, "gain": 0.1}}]})
        baseline = self.state()
        cases = []
        bad = self.session(); bad["schema_version"] = 6; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["controls"][0]["points"][0]["frame"] = 24577; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["controls"][0]["points"][0]["value"] = None; cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["program"] = "x" * (60 * 1024 + 1); cases.append(bad)
        bad = self.session(); bad["tracks"][0]["device"]["controls"][0]["name"] = "amplitude"; cases.append(bad)
        for index, bad in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(EngineError):
                self.replace(bad)
            self.assertEqual(self.state(), baseline)
        self.assertFalse(self.marker.exists())

    def test_source_deadline_reaps_worker_descendants_and_preserves_state(self):
        pid_path = self.directory / "child.pid"
        worker = self.write_worker(f"""
            import os, time
            child = os.fork()
            if child == 0:
                time.sleep(60)
                os._exit(0)
            with open({str(pid_path)!r}, 'w') as stream: stream.write(str(child))
            time.sleep(60)
        """)
        self.start_engine(worker)
        before = self.state()
        start = time.monotonic()
        with self.assertRaisesRegex(EngineError, "timed out"):
            self.replace(self.session())
        self.assertLess(time.monotonic() - start, 20)
        self.assertEqual(self.state(), before)
        pid = int(pid_path.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            self.fail(f"Csound worker descendant {pid} survived deadline cleanup")

    @unittest.skipUnless(Path('/Applications/SuperCollider.app/Contents/Resources/scsynth').is_file(),
                         'installed scsynth unavailable')
    def test_mixed_runtime_sources_share_budget_and_prepare_in_schema7(self):
        from examples.supercollider_tracks_demo import make_track
        sc = make_track("sc", 220.0, .1, .5, 1.0, .5)
        with patch.dict(os.environ, {"DAW_SCSYNTH": "/Applications/SuperCollider.app/Contents/Resources/scsynth"}):
            self.start_engine(self.fake_worker())
            wanted = self.session([self.track("cs", 440), sc])
            self.replace(wanted)
            self.assertEqual(self.engine.call("session.get"), wanted)
            output = self.directory / "mixed.wav"
            self.engine.call("render", {"path": str(output), "seconds": 1.0})
            self.assertTrue(output.is_file())
            before = self.state()
            wanted["tracks"][0]["device"]["duration_frames"] = 480000
            with self.assertRaises(EngineError):
                self.replace(wanted)
            self.assertEqual(self.state(), before)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_SOURCES") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_SOURCES=1 and DAW_CSOUND_LIBRARY")
    def test_installed_csound_runtime_prepares_distinct_controlled_tracks(self):
        library = Path(os.environ["DAW_CSOUND_LIBRARY"])
        if not library.is_absolute() or not library.is_file():
            self.skipTest("DAW_CSOUND_LIBRARY must name an existing absolute library")
        self.start_engine(library=library)
        wanted = self.session([self.track("measured", 220, amplitude=0.12),
                               self.track("silent", 330, amplitude=0.0)])
        self.replace(wanted)
        path = self.directory / "measured.wav"
        self.engine.call("render", {"path": str(path), "seconds": 1.0})
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            pcm = struct.unpack("<{}h".format(frames * 2), wav.readframes(frames))
        left = pcm[::2]
        for start, expected in ((4000, 220), (30000, 660)):
            window = left[start:start + 8000]
            crossings = [i for i in range(1,len(window)) if window[i-1] <= 0 < window[i]]
            self.assertGreater(len(crossings),3)
            measured = (len(crossings)-1)*48000/(crossings[-1]-crossings[0])
            self.assertAlmostEqual(measured, expected, delta=2)
        rms = math.sqrt(sum(sample * sample for sample in left[4000:12000]) / 8000)
        self.assertGreater(rms, 100)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_SOURCES") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_SOURCES=1 and DAW_CSOUND_LIBRARY")
    def test_installed_source_preserves_headroom_frame_zero_and_partial_blocks(self):
        self.start_engine(library=Path(os.environ["DAW_CSOUND_LIBRARY"]))
        wanted = self.session()
        track = wanted["tracks"][0]
        track["device"]["controls"][0]["points"] = [{"frame": 0, "value": 660.0}]
        track["device"]["controls"][1]["value"] = 3.0
        track["device"]["gain"] = 0.5
        track["effects"] = [{"kind":"gain", "id":"trim", "gain":0.5, "bypass":False}]
        track["device"]["duration_frames"] = 48001
        track["device"]["program"] = track["device"]["program"].replace("i1 0 1", "i1 0 2")
        self.replace(wanted)
        output = self.directory / "headroom.wav"
        self.engine.call("render", {"path":str(output),"seconds":1.1})
        with wave.open(str(output),"rb") as stream:
            pcm=struct.unpack("<"+"h"*(stream.getnframes()*2),stream.readframes(stream.getnframes()))[::2]
        self.assertAlmostEqual(max(abs(v) for v in pcm[:48001]),3*.5*.5*32767,delta=2)
        self.assertTrue(all(v==0 for v in pcm[48001:]))
        crossings=[i for i in range(1,12000) if pcm[i-1]<=0<pcm[i]]
        self.assertAlmostEqual((len(crossings)-1)*48000/(crossings[-1]-crossings[0]),660,delta=2)
        track["device"]["duration_frames"] = 48
        track["device"]["controls"][0]["points"] = []
        self.replace(wanted)
        tiny = self.directory / "tiny.wav"
        self.engine.call("render", {"path":str(tiny),"seconds":.002})
        with wave.open(str(tiny),"rb") as stream:
            samples=struct.unpack("<"+"h"*(stream.getnframes()*2),stream.readframes(stream.getnframes()))[::2]
        self.assertTrue(any(samples[:48]))
        self.assertTrue(all(v==0 for v in samples[48:]))

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_SOURCES") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_SOURCES=1 and DAW_CSOUND_LIBRARY")
    def test_installed_runtime_rejects_bad_program_and_missing_control_channel_transactionally(self):
        library = Path(os.environ["DAW_CSOUND_LIBRARY"])
        if not library.is_absolute() or not library.is_file():
            self.skipTest("DAW_CSOUND_LIBRARY must name an existing absolute library")
        self.start_engine(library=library)
        seed = {"schema_version": 1, "sample_rate": 48000,
                "tracks": [{"id": "kept", "device": {"kind": "sine",
                    "frequency_hz": 330.0, "gain": 0.1}}]}
        self.replace(seed)
        baseline = self.state()
        for broken in (self.csd().replace("oscili kamp", "unknown_opcode kamp"),
                       self.csd().replace('chn_k "frequency", 1', '')):
            candidate = self.session()
            candidate["tracks"][0]["device"]["program"] = broken
            with self.subTest(program=broken[-100:]), self.assertRaises(EngineError):
                self.replace(candidate)
            self.assertEqual(self.state(), baseline)


if __name__ == "__main__":
    unittest.main()

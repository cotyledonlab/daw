"""Controller integration tests for bounded Csound CLI rendering."""
import errno
import json
import math
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
import wave

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT


CSOUND = os.environ.get("DAW_CSOUND")


class DawCsoundTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="csound-daw-test-", dir=output)
        self.directory = Path(self.project.name)
        self.csd = self.directory / "program.csd"
        self.csd.write_text(self._csd())
        self.fake = self.directory / "fake-csound"
        self.mode = self.directory / "mode"
        self.marker = self.directory / "invoked.json"
        self.fake.write_text(self._fake_worker())
        self.fake.chmod(0o755)
        self.engine = None
        self.environment = None

    @staticmethod
    def _csd():
        return textwrap.dedent("""\
            <CsoundSynthesizer>
            <CsOptions>
            </CsOptions>
            <CsInstruments>
            sr = 48000
            ksmps = 1
            nchnls = 2
            0dbfs = 1
            instr 1
              a1 oscili p4, p5
              outs a1, a1
            endin
            </CsInstruments>
            <CsScore>
            i1 0 0.1 0.1 440
            e
            </CsScore>
            </CsoundSynthesizer>
        """)

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        self.project.cleanup()

    def _start_engine(self, executable=None):
        if self.engine is not None:
            self.engine.close()
        if self.environment is not None:
            self.environment.stop()
        self.environment = patch.dict(os.environ, {
            "DAW_CSOUND": str(executable if executable is not None else self.fake),
        })
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        return self.engine

    def _fake_worker(self):
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, sys, time, wave
            marker = {str(self.marker)!r}
            mode_file = {str(self.mode)!r}
            args = sys.argv[1:]
            with open(marker, "w") as stream:
                json.dump({{"args": args, "cwd": os.getcwd(),
                           "rc6": os.environ.get("CSOUND6RC"),
                           "rc7": os.environ.get("CSOUND7RC"),
                           "rc6_exists": os.path.isfile(os.environ["CSOUND6RC"]),
                           "rc7_exists": os.path.isfile(os.environ["CSOUND7RC"]),
                           "rc6_size": os.path.getsize(os.environ["CSOUND6RC"]),
                           "rc7_size": os.path.getsize(os.environ["CSOUND7RC"]),
                           "csd": open(args[-1], "rb").read().decode("utf-8")}}, stream)
            wav_path = args[args.index("-o") + 1]
            mode = open(mode_file).read() if os.path.exists(mode_file) else "wav"
            if mode == "crash":
                sys.exit(17)
            if mode == "hang":
                time.sleep(45)
            if mode == "descendant":
                child = os.fork()
                if child == 0:
                    time.sleep(45)
                    os._exit(0)
                open({str(self.directory / 'child.pid')!r}, "w").write(str(child))
                sys.exit(0)
            if mode == "stdout_overflow":
                sys.stdout.buffer.write(b"x" * 70000)
                sys.exit(0)
            if mode == "stderr_overflow":
                sys.stderr.buffer.write(b"x" * 70000)
                sys.exit(0)
            if mode == "missing":
                sys.exit(0)
            if mode == "badwav":
                open(wav_path, "wb").write(b"not a wav")
                sys.exit(0)
            if mode == "oversizedwav":
                open(wav_path, "wb").write(b"x" * (3 * 1024 * 1024))
                sys.exit(0)
            params = {{"wrongrate": (2, 44100, 2400, 2),
                      "wrongchannels": (1, 48000, 2400, 2),
                      "wrongframes": (2, 48000, 2399, 2),
                      "wrongwidth": (2, 48000, 2400, 1),
                      "compressed": (2, 48000, 2400, 2)}}.get(mode, (2, 48000, 2400, 2))
            channels, rate, frames, width = params
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(channels)
                wav.setsampwidth(width)
                wav.setframerate(rate)
                if mode == "compressed":
                    wav.setcomptype("ULAW", "u-law")
                wav.writeframes(b"\\0" * frames * channels * width)
            if mode == "truncated":
                with open(wav_path, "r+b") as rendered:
                    rendered.truncate(os.path.getsize(wav_path) - 100)
        """)

    def _render(self, output, *, csd=None, sample_rate=48000, duration_frames=2400):
        return self.engine.call("csound.render", {
            "csd_path": str(csd or self.csd), "path": str(output),
            "sample_rate": sample_rate, "duration_frames": duration_frames,
        })

    def _state(self):
        return (self.engine.call("session.inspect"),
                self.engine.call("transport.status"))

    def _seed_session(self):
        return self.engine.call("session.replace", {"session": {
            "schema_version": 3, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [{"id": "active", "device": {"kind": "sine",
                "frequency_hz": 440.0, "gain": 0.1}, "mode": "continuous",
                "clips": [], "effects": []}],
        }})

    def test_fake_worker_renders_exact_pcm_and_uses_isolated_strict_invocation(self):
        self._start_engine()
        self._seed_session()
        caps = self.engine.call("capabilities")
        self.assertIn("csound.render", caps["methods"])
        support = caps["csound_offline"]
        self.assertTrue(support["implemented"])
        self.assertTrue(support["configured"])
        self.assertFalse(support["session_device"])
        self.assertFalse(support["native_playback"])
        self.assertIn("csound", caps["devices"])
        self.assertTrue(caps["csound_sources"]["implemented"])
        before = self._state()
        output = self.directory / "render.wav"
        result = self._render(output)
        self.assertEqual(result, {"path": str(output), "frames": 2400,
                                  "sample_rate": 48000, "channels": 2})
        self.assertEqual(self._state(), before)
        with wave.open(str(output), "rb") as rendered:
            self.assertEqual(rendered.getparams().nchannels, 2)
            self.assertEqual(rendered.getparams().sampwidth, 2)
            self.assertEqual(rendered.getparams().comptype, "NONE")
            self.assertEqual(rendered.getparams().framerate, 48000)
            self.assertEqual(rendered.getnframes(), 2400)
        invocation = json.loads(self.marker.read_text())
        args = invocation["args"]
        self.assertEqual(args[:8], ["-+ignore_csopts=1", "--no-default-paths",
                                    "-+rtaudio=null", "-+rtmidi=null", "-d", "-m0",
                                    "-W", "-s"])
        self.assertEqual(args[8:12], ["-r", "48000", "--ksmps=1", "-o"])
        self.assertTrue(Path(args[12]).is_absolute())
        self.assertEqual(Path(args[12]).name, "render.wav")
        self.assertNotEqual(invocation["cwd"], str(self.directory))
        self.assertEqual(Path(args[-1]).name, "program.csd")
        self.assertEqual(Path(args[-1]).resolve().parent, Path(invocation["cwd"]).resolve())
        self.assertEqual(invocation["csd"], self.csd.read_text())
        self.assertEqual(invocation["rc6_size"], 0)
        self.assertEqual(invocation["rc7_size"], 0)
        self.assertTrue(invocation["rc6_exists"])
        self.assertTrue(invocation["rc7_exists"])

    def test_strict_parameter_contract_rejects_invalid_values_before_launch(self):
        self._start_engine()
        invalid_calls = [
            ({"csd_path": "", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x\x00.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x\x00.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x" * 4097, "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x" * 4097, "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 7999,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 192001,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": True,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 0}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 480001}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1.0}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": True}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1, "extra": 1}),
            ({"csd_path": None, "path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": None, "sample_rate": 48000,
              "duration_frames": 1}),
            ({"csd_path": "x.csd", "path": "x.wav", "sample_rate": 48000}),
            ({"csd_path": "x.csd", "path": "x.wav", "duration_frames": 1}),
            ({"csd_path": "x.csd", "sample_rate": 48000,
              "duration_frames": 1}),
            ({"path": "x.wav", "sample_rate": 48000,
              "duration_frames": 1}),
            None,
        ]
        for params in invalid_calls:
            with self.subTest(params=params), self.assertRaises(EngineError):
                self.engine.call("csound.render", params)
        self.assertFalse(self.marker.exists())

    def test_missing_executable_is_a_runtime_error_and_preserves_state(self):
        self._start_engine(self.directory / "does-not-exist")
        self._seed_session()
        before = self._state()
        commands = [
            {"protocol_version": 1, "id": "seed", "method": "session.replace",
             "params": {"session": {"schema_version": 3, "sample_rate": 48000,
                 "tempo_milli_bpm": 120000, "tracks": []}}},
            {"protocol_version": 1, "id": "before", "method": "session.inspect"},
            {"protocol_version": 1, "id": "status-before", "method": "transport.status"},
            {"protocol_version": 1, "id": "render", "method": "csound.render",
             "params": {"csd_path": str(self.csd),
                        "path": str(self.directory / "missing-runtime.wav"),
                        "sample_rate": 48000, "duration_frames": 2400}},
            {"protocol_version": 1, "id": "after", "method": "session.inspect"},
            {"protocol_version": 1, "id": "status-after", "method": "transport.status"},
        ]
        completed = subprocess.run([str(ROOT / "target/debug/daw"), "serve"],
                                   input="".join(json.dumps(command) + "\n"
                                                 for command in commands),
                                   text=True, capture_output=True, check=True)
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(responses[3]["error"]["code"], "runtime_error")
        self.assertEqual(responses[1]["result"], responses[4]["result"])
        self.assertEqual(responses[2]["result"], responses[5]["result"])
        self.assertFalse((self.directory / "missing-runtime.wav").exists())
        self.assertEqual(self._state(), before)

    def test_invalid_or_oversized_csd_is_rejected_before_worker_launch(self):
        self._start_engine()
        invalid = self.directory / "invalid.csd"
        invalid.write_bytes(b"not utf-8: \xff")
        oversized = self.directory / "oversized.csd"
        oversized.write_bytes(b"x" * (1024 * 1024 + 1))
        for source in (invalid, oversized, self.directory / "missing.csd"):
            with self.subTest(source=source), self.assertRaises(EngineError):
                self._render(self.directory / (source.stem + ".wav"), csd=source)
        self.assertFalse(self.marker.exists())

    def test_nonregular_csd_is_rejected_without_opening_it(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("requires Unix FIFO")
        self._start_engine()
        fifo = self.directory / "pipe.csd"
        os.mkfifo(fifo)
        with self.assertRaises(EngineError):
            self._render(self.directory / "pipe.wav", csd=fifo)
        self.assertFalse(self.marker.exists())

    def test_worker_failures_bad_wavs_and_destination_preservation(self):
        self._start_engine()
        self._seed_session()
        before = self._state()
        for mode in ("crash", "missing", "badwav", "oversizedwav", "truncated",
                     "wrongrate", "wrongchannels",
                     "wrongframes", "wrongwidth", "compressed", "stdout_overflow",
                     "stderr_overflow"):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                output = self.directory / f"{mode}.wav"
                with self.assertRaises(EngineError):
                    self._render(output)
                self.assertFalse(output.exists())
                self.assertEqual(self._state(), before)

    def test_existing_destination_is_never_overwritten(self):
        self._start_engine()
        output = self.directory / "existing.wav"
        output.write_bytes(b"preserve me")
        with self.assertRaises(EngineError):
            self._render(output)
        self.assertEqual(output.read_bytes(), b"preserve me")

    def test_symlink_and_dangling_symlink_destinations_are_preserved(self):
        self._start_engine()
        target = self.directory / "target.wav"
        target.write_bytes(b"preserve target")
        link = self.directory / "link.wav"
        link.symlink_to(target)
        dangling = self.directory / "dangling.wav"
        dangling_target = self.directory / "not-created.wav"
        dangling.symlink_to(dangling_target)
        with self.assertRaises(EngineError):
            self._render(link)
        with self.assertRaises(EngineError):
            self._render(dangling)
        self.assertTrue(link.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertEqual(target.read_bytes(), b"preserve target")
        self.assertFalse(dangling_target.exists())

    def test_worker_timeout_is_bounded_and_preserves_session_and_transport(self):
        self._start_engine()
        self._seed_session()
        self.mode.write_text("hang")
        before = self._state()
        output = self.directory / "timeout.wav"
        started = time.monotonic()
        with self.assertRaises(EngineError):
            self._render(output)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 21)
        self.assertGreaterEqual(elapsed, 10)
        self.assertFalse(output.exists())
        self.assertEqual(self._state(), before)

    def test_descendant_holding_worker_pipes_is_killed_with_process_group(self):
        if not hasattr(os, "fork"):
            self.skipTest("requires Unix fork")
        self._start_engine()
        self.mode.write_text("descendant")
        output = self.directory / "descendant.wav"
        with self.assertRaises(EngineError):
            self._render(output)
        child_pid = int((self.directory / "child.pid").read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except OSError as error:
                if error.errno == errno.ESRCH:
                    break
                raise
            time.sleep(0.05)
        else:
            self.fail(f"worker descendant {child_pid} survived process-group cleanup")
        self.assertFalse(output.exists())

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND") == "1" and CSOUND,
                         "set DAW_TEST_CSOUND=1 and DAW_CSOUND to test installed Csound")
    def test_installed_csound_renders_requested_440_hz_stereo_pcm(self):
        executable = Path(CSOUND)
        if not executable.is_absolute() or not executable.is_file():
            self.skipTest("DAW_CSOUND must name an installed absolute Csound executable")
        self._start_engine(executable.resolve())
        output = self.directory / "installed.wav"
        result = self._render(output, duration_frames=4800)
        self.assertEqual(result["frames"], 4800)
        with wave.open(str(output), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 2)
            self.assertEqual(rendered.getsampwidth(), 2)
            self.assertEqual(rendered.getframerate(), 48000)
            self.assertEqual(rendered.getnframes(), 4800)
            raw = rendered.readframes(4800)
        samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
        self.assertEqual(samples[0::2], samples[1::2])
        self.assertGreater(max(samples), 1000)
        self.assertLess(min(samples), -1000)
        crossings = [index for index, (left, right) in enumerate(
            zip(samples[::2], samples[2::2])) if left <= 0 < right]
        measured_hz = (len(crossings) - 1) / (
            (crossings[-1] - crossings[0]) / 48000)
        self.assertTrue(math.isclose(measured_hz, 440, abs_tol=2), measured_hz)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND") == "1" and CSOUND,
                         "set DAW_TEST_CSOUND=1 and DAW_CSOUND to test installed Csound")
    def test_installed_csound_ignores_audio_device_options_and_honors_rate_override(self):
        executable = Path(CSOUND)
        if not executable.is_absolute() or not executable.is_file():
            self.skipTest("DAW_CSOUND must name an installed absolute Csound executable")
        self._start_engine(executable.resolve())
        self.csd.write_text(self._csd().replace("<CsOptions>\n</CsOptions>",
                                                "<CsOptions>\n-odac -iadc\n</CsOptions>"))
        output = self.directory / "rate-override.wav"
        result = self._render(output, sample_rate=44100, duration_frames=4410)
        self.assertEqual(result["frames"], 4410)
        with wave.open(str(output), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 2)
            self.assertEqual(rendered.getframerate(), 44100)
            self.assertEqual(rendered.getnframes(), 4410)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND") == "1" and CSOUND,
                         "set DAW_TEST_CSOUND=1 and DAW_CSOUND to test installed Csound")
    def test_installed_csound_invalid_program_preserves_session_and_destination(self):
        executable = Path(CSOUND)
        if not executable.is_absolute() or not executable.is_file():
            self.skipTest("DAW_CSOUND must name an installed absolute Csound executable")
        self._start_engine(executable.resolve())
        self._seed_session()
        before = self._state()
        invalid = self.directory / "invalid-program.csd"
        invalid.write_text(self._csd().replace("oscili p4, p5", "not_an_opcode p4, p5"))
        output = self.directory / "invalid-program.wav"
        with self.assertRaises(EngineError):
            self._render(output, csd=invalid)
        self.assertFalse(output.exists())
        self.assertEqual(self._state(), before)


if __name__ == "__main__":
    unittest.main()

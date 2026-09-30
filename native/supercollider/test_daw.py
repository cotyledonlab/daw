"""Controller integration tests for bounded SuperCollider NRT rendering."""
import errno
import math
import os
from pathlib import Path
import signal
import struct
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
import wave

from gui.server import Engine, EngineError
from native.supercollider.score import write_score, sine_synthdef, osc_bundle, osc_message
from native.vst3.scan import ROOT


SCSYNTH = Path("/Applications/SuperCollider.app/Contents/Resources/scsynth")


class DawSuperColliderTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.project = tempfile.TemporaryDirectory(prefix="sc-daw-test-", dir=output)
        self.directory = Path(self.project.name)
        self.score = write_score(self.directory / "sine.osc")
        self.fake = self.directory / "fake-scsynth"
        self.mode = self.directory / "mode"
        self.marker = self.directory / "invoked"
        self.fake.write_text(self._fake_worker())
        self.fake.chmod(0o755)
        self.engine = None
        self.environment = None

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
            "DAW_SCSYNTH": str(executable if executable is not None else self.fake),
        })
        self.environment.start()
        self.engine = Engine(ROOT / "target/debug/daw")
        return self.engine

    def _fake_worker(self):
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import os, struct, sys, time, wave
            marker = {str(self.marker)!r}
            mode_file = {str(self.mode)!r}
            open(marker, "a").write("called\\n")
            mode = open(mode_file).read() if os.path.exists(mode_file) else "wav"
            args = sys.argv[1:]
            wav_path = args[args.index("-N") + 3]
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
            if mode == "oversized":
                sys.stdout.buffer.write(b"x" * 70000)
                sys.exit(0)
            if mode == "missing":
                sys.exit(0)
            if mode == "badwav":
                open(wav_path, "wb").write(b"not a wav")
                sys.exit(0)
            if mode == "stderr_error":
                sys.stderr.write("ERROR: synthetic failure\\n")
                sys.exit(0)
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(48000)
                wav.writeframes(b"\\0\\0\\0\\0" * 4800)
        """)

    def _render(self, output, *, score=None, sample_rate=48000):
        return self.engine.call("supercollider.render", {
            "score_path": str(score or self.score),
            "path": str(output),
            "sample_rate": sample_rate,
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

    def test_fake_worker_renders_pcm_and_reports_metadata_without_changing_engine_state(self):
        self._start_engine()
        self._seed_session()
        before = self._state()
        output = self.directory / "render.wav"
        result = self._render(output)
        self.assertEqual(result, {"path": str(output), "frames": 4800,
                                  "sample_rate": 48000, "channels": 2})
        self.assertEqual(self._state(), before)
        with wave.open(str(output), "rb") as rendered:
            self.assertEqual(rendered.getparams().nchannels, 2)
            self.assertEqual(rendered.getparams().framerate, 48000)
            self.assertEqual(rendered.getnframes(), 4800)

    def test_installed_scsynth_renders_nonzero_stereo_sine(self):
        if not SCSYNTH.is_file():
            self.skipTest(f"installed SuperCollider 3.14.1 not found at {SCSYNTH}")
        self._start_engine(SCSYNTH.resolve())
        output = self.directory / "installed.wav"
        result = self._render(output)
        self.assertEqual(result["frames"], 4800)
        with wave.open(str(output), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 2)
            self.assertEqual(rendered.getframerate(), 48000)
            self.assertEqual(rendered.getnframes(), 4800)
            raw = rendered.readframes(4800)
        samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
        self.assertEqual(samples[0::2], samples[1::2])
        self.assertGreater(max(samples), 1000)
        self.assertLess(min(samples), -1000)
        nonzero = [value for value in samples[::2] if value]
        measured_hz = (sum(a * b < 0 for a, b in zip(nonzero, nonzero[1:]))
                       / 2 / 0.1)
        self.assertAlmostEqual(measured_hz, 440, delta=10)

    def test_installed_scsynth_trims_short_and_nonblock_aligned_scores(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        self._start_engine(SCSYNTH.resolve())
        for rate, duration in [(48000, 0.001), (44100, 0.037)]:
            score = write_score(self.directory / f"short-{rate}.osc", frequency=880.0,
                                gain=0.05, duration=duration)
            output = self.directory / f"short-{rate}.wav"
            result = self._render(output, score=score, sample_rate=rate)
            expected = math.floor(rate * duration + 0.5)
            self.assertEqual(result["frames"], expected)
            with wave.open(str(output), "rb") as rendered:
                self.assertEqual(rendered.getnframes(), expected)
                self.assertEqual(rendered.getframerate(), rate)

    def test_installed_scsynth_rejects_unresolved_synthdef_without_partial_output(self):
        if not SCSYNTH.is_file():
            self.skipTest(f"installed SuperCollider 3.14.1 not found at {SCSYNTH}")
        self._start_engine(SCSYNTH.resolve())
        self._seed_session()
        before = self._state()
        invalid_score = self.directory / "unknown-synth.osc"
        contents = self.score.read_bytes()
        name = b"daw_sine_fixture"
        location = contents.rfind(name)
        self.assertGreaterEqual(location, 0)
        contents = contents[:location] + b"unknown_synth___" + contents[location + len(name):]
        invalid_score.write_bytes(contents)
        output = self.directory / "unknown-synth.wav"
        with self.assertRaises(EngineError):
            self._render(output, score=invalid_score)
        self.assertFalse(output.exists())
        self.assertEqual(self._state(), before)

    def test_installed_scsynth_definition_exception_is_rejected_even_without_a_node(self):
        if not SCSYNTH.is_file():
            self.skipTest("installed scsynth unavailable")
        self._start_engine(SCSYNTH.resolve())
        self._seed_session()
        before = self._state()
        failed_def = sine_synthdef().replace(b"SinOsc", b"BadOsc")
        packets = [osc_bundle(0, [osc_message("/d_recv", failed_def)]),
                   osc_bundle(0.1, [osc_message("/c_set", 0, 0.)])]
        score = self.directory / "failed-definition.osc"
        score.write_bytes(b"".join(struct.pack(">I", len(packet)) + packet for packet in packets))
        output = self.directory / "failed-definition.wav"
        with self.assertRaises(EngineError):
            self._render(output, score=score)
        self.assertFalse(output.exists())
        self.assertEqual(self._state(), before)

    def test_existing_destination_is_never_overwritten(self):
        self._start_engine()
        output = self.directory / "existing.wav"
        output.write_bytes(b"preserve me")
        before = output.read_bytes()
        with self.assertRaises(EngineError):
            self._render(output)
        self.assertEqual(output.read_bytes(), before)

    def test_malformed_score_and_invalid_parameters_fail_before_worker_launch(self):
        self._start_engine()
        malformed = self.directory / "malformed.osc"
        malformed.write_bytes(b"not an OSC score")
        with self.assertRaises(EngineError):
            self._render(self.directory / "malformed.wav", score=malformed)
        self.assertFalse(self.marker.exists())
        for sample_rate in (0, 192001):
            with self.subTest(sample_rate=sample_rate), self.assertRaises(EngineError):
                self._render(self.directory / f"rate-{sample_rate}.wav",
                             sample_rate=sample_rate)
        self.assertFalse(self.marker.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires Unix FIFO")
    def test_nonregular_score_is_rejected_without_opening_it(self):
        self._start_engine()
        fifo = self.directory / "pipe.osc"
        os.mkfifo(fifo)
        with self.assertRaises(EngineError):
            self._render(self.directory / "pipe.wav", score=fifo)
        self.assertFalse(self.marker.exists())

    def test_ten_second_score_limit_fails_before_worker_launch(self):
        self._start_engine()
        too_long = write_score(self.directory / "too-long.osc", duration=10.0)
        framed = bytearray(too_long.read_bytes())
        first_packet_size = struct.unpack_from(">I", framed)[0]
        second_record = 4 + first_packet_size
        struct.pack_into(">II", framed, second_record + 4 + 8, 10,
                         round(0.001 * (2**32)))
        too_long.write_bytes(framed)
        with self.assertRaises(EngineError):
            self._render(self.directory / "too-long.wav", score=too_long)
        self.assertFalse(self.marker.exists())

    def test_missing_executable_fails_without_session_or_transport_changes(self):
        self._start_engine(self.directory / "does-not-exist")
        self._seed_session()
        before = self._state()
        with self.assertRaises(EngineError):
            self._render(self.directory / "missing-exe.wav")
        self.assertFalse((self.directory / "missing-exe.wav").exists())
        self.assertEqual(self._state(), before)

    def test_child_failures_and_invalid_outputs_preserve_state_and_create_no_destination(self):
        self._start_engine()
        self._seed_session()
        before = self._state()
        for mode in ("crash", "oversized", "missing", "badwav", "stderr_error"):
            with self.subTest(mode=mode):
                self.mode.write_text(mode)
                output = self.directory / f"{mode}.wav"
                with self.assertRaises(EngineError):
                    self._render(output)
                self.assertFalse(output.exists())
                self.assertEqual(self._state(), before)

    def test_worker_timeout_is_bounded_and_preserves_state(self):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(25)
        try:
            self._start_engine()
            self._seed_session()
            self.mode.write_text("hang")
            before = self._state()
            output = self.directory / "timeout.wav"
            started = time.monotonic()
            with self.assertRaises(EngineError):
                self._render(output)
            self.assertLess(time.monotonic() - started, 22)
            self.assertFalse(output.exists())
            self.assertEqual(self._state(), before)
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)

    def test_descendant_holding_worker_pipes_is_killed_with_its_process_group(self):
        if not hasattr(os, "fork"):
            self.skipTest("requires Unix fork")
        if hasattr(signal, "SIGALRM"):
            signal.alarm(25)
        try:
            self._start_engine()
            self._seed_session()
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
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)


if __name__ == "__main__":
    unittest.main()

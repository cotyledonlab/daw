"""Failure-boundary tests for the owned libcsound block diagnostic."""
import errno
import json
import math
import os
from pathlib import Path
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from native.csound import block_probe

run_probe = block_probe.run_probe


ROOT = Path(__file__).resolve().parents[2]


class CsoundBlockProbeTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / "output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="csound-blocks-", dir=output)
        self.directory = Path(self.temp.name)
        self.library = self.directory / "libcsound-test.dylib"
        self.library.write_bytes(b"owned fake library marker")
        self.worker = self.directory / "fake_worker.py"

    def tearDown(self):
        self.temp.cleanup()

    def _worker(self, body):
        self.worker.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        self.worker.chmod(0o755)
        return self.worker

    @staticmethod
    def _valid_report():
        return {
            "sample_rate": 48000, "ksmps": 64, "frames": 49152,
            "control_frame": 24576, "passes": 2, "reset_equal": True,
            "resources_released": True, "source_mode": "diagnostic",
            "daw_transport": False, "hardware_audio": False,
            "version": 7000, "myflt_bytes": 8,
            "audio": {
                "digest": "a" * 64,
                "phases": [
                    {"frequency_hz": 440.008, "rms": 0.07071388, "peak": 0.1},
                    {"frequency_hz": 660.002, "rms": 0.03535141, "peak": 0.05},
                ],
                "rms_ratio": 0.49992,
                "readbacks": [{"frequency": 440.0, "gain": 0.1},
                              {"frequency": 660.0, "gain": 0.05}],
                "blocks": 768, "input_channel_difference": 0.03,
                "terminal_code": 2,
            },
        }

    def test_fake_worker_accepts_complete_report_and_private_fixture(self):
        expected = self._valid_report()
        script = self._worker(f"""
            import json, os, sys
            assert sys.argv[1] == "--worker"
            assert os.path.isabs(sys.argv[2])
            assert os.path.basename(sys.argv[3]) == "result.json"
            assert os.path.basename(sys.argv[4]) == "fixture.csd"
            assert os.path.isfile(sys.argv[4])
            assert os.path.getsize(os.environ["CSOUND6RC"]) == 0
            assert os.path.getsize(os.environ["CSOUND7RC"]) == 0
            assert os.path.isdir(os.path.join(os.getcwd(), "empty-opcodes"))
            with open(sys.argv[3], "x") as stream:
                json.dump({expected!r}, stream, allow_nan=False)
        """)
        before = set((ROOT / "output").glob("csound-blocks-*"))
        result = run_probe(str(self.library), worker_script=str(script))
        self.assertEqual(result, expected)
        self.assertFalse(set((ROOT / "output").glob("csound-blocks-*")) - before)

    def _run_failure(self, mode, message=None, **kwargs):
        script = self._worker(f"""
            import os, sys, time
            mode = {mode!r}
            result = sys.argv[3]
            if mode == "crash":
                sys.exit(29)
            if mode == "timeout":
                time.sleep(60)
            if mode == "stdout_overflow":
                sys.stdout.buffer.write(b"x" * 100000)
                sys.stdout.flush()
            if mode == "stderr_overflow":
                sys.stderr.buffer.write(b"x" * 100000)
                sys.stderr.flush()
            if mode == "descendant":
                child = os.fork()
                if child == 0:
                    time.sleep(60)
                    os._exit(0)
                open({str(self.directory / 'child.pid')!r}, "w").write(str(child))
                sys.exit(0)
            if mode == "missing_result":
                sys.exit(0)
            if mode == "bad_json":
                open(result, "wb").write(b"{{broken")
            if mode == "oversized_result":
                open(result, "wb").write(b"x" * 70000)
            if mode == "nonregular_result":
                os.mkfifo(result)
        """)
        with self.assertRaisesRegex((ValueError, RuntimeError, OSError), message or ".*"):
            run_probe(str(self.library), worker_script=str(script), **kwargs)

    def test_missing_library_is_rejected_before_worker_start(self):
        marker = self.directory / "started"
        self._worker(f"open({str(marker)!r}, 'w').write('started')\n")
        for path in (str(self.directory / "missing.dylib"), "relative.dylib",
                     str(self.directory)):
            with self.subTest(path=path), self.assertRaises((ValueError, RuntimeError, OSError)):
                run_probe(path, worker_script=str(self.worker))
        self.assertFalse(marker.exists())

    def test_existing_non_library_is_loaded_only_inside_owned_worker(self):
        with self.assertRaisesRegex(RuntimeError, "worker failed|Csound"):
            run_probe(str(self.library))

    def test_worker_crash_and_invalid_result_documents_fail(self):
        cases = (("crash", "worker failed"), ("missing_result", "report missing"),
                 ("bad_json", "JSON|report|Expecting"),
                 ("oversized_result", "outside file limit"),
                 ("nonregular_result", "outside file limit"))
        for mode, message in cases:
            with self.subTest(mode=mode):
                self._run_failure(mode, message)

    def test_report_rejects_boolean_integer_spoof_nonfinite_and_bad_signal(self):
        variants = []
        boolean_integer = self._valid_report()
        boolean_integer["audio"]["blocks"] = True
        variants.append(boolean_integer)
        nonfinite = self._valid_report()
        nonfinite["audio"]["phases"][0]["rms"] = math.inf
        variants.append(nonfinite)
        wrong_signal = self._valid_report()
        wrong_signal["audio"]["phases"][1]["frequency_hz"] = 880
        variants.append(wrong_signal)
        for report in variants:
            with self.subTest(report=report):
                payload = json.dumps(report, allow_nan=True)
                script = self._worker(f"""
                    import sys
                    with open(sys.argv[3], "x") as stream:
                        stream.write({payload!r})
                """)
                with self.assertRaisesRegex(RuntimeError, "diagnostic"):
                    run_probe(str(self.library), worker_script=str(script))

    def test_worker_output_limits_are_enforced(self):
        for mode in ("stdout_overflow", "stderr_overflow"):
            with self.subTest(mode=mode):
                self._run_failure(mode, "exceeded limit")

    def test_deadline_and_descendant_pipe_holder_are_bounded(self):
        started = time.monotonic()
        self._run_failure("timeout", timeout=0.2)
        self.assertLess(time.monotonic() - started, 5)

        started = time.monotonic()
        self._run_failure("descendant", timeout=2)
        self.assertLess(time.monotonic() - started, 5)
        pid_file = self.directory / "child.pid"
        if pid_file.exists():
            child_pid = int(pid_file.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except OSError as error:
                    if error.errno == errno.ESRCH:
                        break
                    raise
                time.sleep(0.02)
            else:
                self.fail(f"worker descendant {child_pid} survived process-group cleanup")

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_BLOCKS") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_BLOCKS=1 and DAW_CSOUND_LIBRARY")
    def test_installed_csound7_runs_two_controlled_64_frame_passes(self):
        library = Path(os.environ["DAW_CSOUND_LIBRARY"])
        if not library.is_absolute() or not library.is_file():
            self.skipTest("DAW_CSOUND_LIBRARY must name an existing absolute library")
        report = run_probe(str(library))
        for key, value in {
            "sample_rate": 48000, "ksmps": 64, "frames": 49152,
            "control_frame": 24576, "passes": 2, "reset_equal": True,
            "resources_released": True, "source_mode": "diagnostic",
            "daw_transport": False, "hardware_audio": False,
        }.items():
            self.assertEqual(report[key], value)
        audio = report["audio"]
        self.assertEqual(audio["blocks"], 768)
        self.assertEqual(audio["readbacks"], self._valid_report()["audio"]["readbacks"])
        self.assertAlmostEqual(audio["input_channel_difference"], 0.03)
        self.assertAlmostEqual(audio["rms_ratio"], 0.5, delta=0.02)
        for phase, frequency, peak in zip(audio["phases"], (440, 660), (0.1, 0.05)):
            self.assertAlmostEqual(phase["frequency_hz"], frequency, delta=2)
            self.assertAlmostEqual(phase["peak"], peak, delta=0.001)
        self.assertGreater(audio["terminal_code"], 0)

    @unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_BLOCKS") == "1" and
                         os.environ.get("DAW_CSOUND_LIBRARY"),
                         "set DAW_TEST_CSOUND_BLOCKS=1 and DAW_CSOUND_LIBRARY")
    def test_installed_csound7_rejects_invalid_fixture_inside_worker(self):
        fixture = self.directory / "invalid.csd"
        fixture.write_text(block_probe.FIXTURE.read_text().replace("oscili", "not_an_opcode"))
        self.assertNotEqual(fixture.read_text(), block_probe.FIXTURE.read_text())
        with mock.patch.object(block_probe, "FIXTURE", fixture):
            with self.assertRaisesRegex(RuntimeError, "worker failed|fixture compilation"):
                run_probe(os.environ["DAW_CSOUND_LIBRARY"])


if __name__ == "__main__":
    unittest.main()

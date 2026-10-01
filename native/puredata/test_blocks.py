"""Bounded lifecycle, snapshot and report tests for the owned libpd block proof."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest import mock

from native.puredata import block_probe


def valid_report():
    return {
        "sample_rate": 48000, "blocksize": 64, "frames": 49152,
        "control_frame": 24576, "passes": 2, "recreate_equal": True,
        "resources_released": True, "source_mode": "diagnostic",
        "daw_transport": False, "hardware_audio": False,
        "audio": {
            "digest": "a" * 64,
            "phases": [
                {"frequency_hz": 440.0, "rms": 0.0707, "peak": 0.1},
                {"frequency_hz": 660.0, "rms": 0.03535, "peak": 0.05},
            ],
            "rms_ratio": 0.5,
            "readbacks": [{"frequency": 440.0, "amplitude": 0.1},
                          {"frequency": 660.0, "amplitude": 0.05}],
            "blocks": 768, "input_channel_error": 0.0,
            "missing_receiver_rejected": True,
        },
    }


class FakeWorker:
    """Successful or malformed worker stand-in; no library is loaded."""

    def __init__(self, args, *, report="valid", stdout=b"", stderr=b""):
        self.pid = 2_000_000_000
        self.returncode = 0
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.args = args
        result = Path(args[4])
        if report == "valid":
            result.write_text(json.dumps(valid_report(), allow_nan=False))
        elif report == "nonfinite":
            report_data = valid_report()
            report_data["audio"]["rms_ratio"] = float("nan")
            result.write_text(json.dumps(report_data))
        elif report == "oversized":
            result.write_bytes(b"{" + b" " * block_probe.MAX_LOG + b"}")
        elif report == "symlink":
            target = result.with_name("report-target.json")
            target.write_text(json.dumps(valid_report()))
            result.symlink_to(target)
        elif report == "bad":
            result.write_text("{}")
        elif report != "missing":
            raise AssertionError(f"unknown fake report mode {report}")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class LibPdBlockProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="libpd-block-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "libpd.dylib"
        self.library.write_bytes(b"test placeholder")

    def run_fake(self, *, report="valid", stdout=b"", stderr=b""):
        factory = lambda *args, **kwargs: FakeWorker(args[0], report=report,
                                                     stdout=stdout, stderr=stderr)
        with mock.patch.object(block_probe.subprocess, "Popen", side_effect=factory), \
                mock.patch.object(block_probe.os, "killpg", side_effect=ProcessLookupError):
            return block_probe.run_probe(self.library)

    def test_missing_and_relative_library_are_rejected_before_worker_launch(self):
        with mock.patch.object(block_probe.subprocess, "Popen") as popen:
            for library in ("", "relative/libpd.dylib", self.root / "missing.dylib"):
                with self.subTest(library=library), self.assertRaisesRegex(
                        RuntimeError, "DAW_LIBPD_LIBRARY"):
                    block_probe.run_probe(library)
            popen.assert_not_called()

    def test_invalid_existing_library_fails_in_owned_child(self):
        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            block_probe.run_probe(self.library, timeout=4)

    def test_good_report_is_validated_and_returned(self):
        report = self.run_fake()
        self.assertEqual(block_probe._validate(report), report)
        self.assertEqual(report["audio"]["blocks"], 768)

    def test_missing_oversized_symlink_bad_and_nonfinite_reports_reject(self):
        for mode, pattern in (("missing", "report missing"),
                              ("oversized", "report missing or outside file limit"),
                              ("symlink", "report missing or outside file limit"),
                              ("bad", "invalid libpd diagnostic report"),
                              ("nonfinite", "invalid libpd report JSON")):
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, pattern):
                self.run_fake(report=mode)

    def test_stdout_and_stderr_overflow_are_both_detected(self):
        payload = b"x" * (block_probe.MAX_LOG + 1)
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaisesRegex(RuntimeError, "diagnostics exceeded"):
                self.run_fake(**{stream: payload})

    def test_fixture_snapshot_missing_oversized_nul_and_invalid_utf8_reject(self):
        missing = self.root / "missing.pd"
        with self.assertRaises(FileNotFoundError):
            block_probe.run_probe(self.library, fixture=missing)
        for name, data in (("oversized.pd", b"x" * (block_probe.MAX_LOG + 1)),
                            ("nul.pd", b"x\0y"), ("utf8.pd", b"\xff")):
            fixture = self.root / name
            fixture.write_bytes(data)
            with self.subTest(name=name), self.assertRaises((RuntimeError, UnicodeDecodeError)):
                block_probe.run_probe(self.library, fixture=fixture)

    def test_worker_timeout_kills_its_process_group_and_descendant(self):
        marker = self.root / "descendant.pid"
        worker = self.root / "hang_worker.py"
        worker.write_text(
            "import pathlib, subprocess, sys, time\n"
            "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            block_probe.run_probe(self.library, worker_script=worker, timeout=0.3)
        self.assertTrue(marker.is_file())
        pid = int(marker.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"worker descendant {pid} survived owned process-group cleanup")

    def test_reap_permission_error_accepts_exited_child_but_kills_live_child(self):
        class Exited:
            pid = 123

            def wait(self, timeout=None):
                return 0

        with mock.patch.object(block_probe.os, "killpg", side_effect=PermissionError):
            self.assertEqual(block_probe._reap(Exited()), None)

        class Live:
            pid = 124
            killed = False

            def wait(self, timeout=None):
                if timeout == 0.2:
                    raise subprocess.TimeoutExpired("worker", timeout)
                return 0

            def kill(self):
                self.killed = True

        live = Live()
        with mock.patch.object(block_probe.os, "killpg", side_effect=PermissionError):
            with self.assertRaises(subprocess.TimeoutExpired):
                block_probe._reap(live)
        self.assertTrue(live.killed)

    def test_validate_rejects_corrupted_shape_values_and_signal_claims(self):
        corruptions = (
            lambda value: value.update(extra=True),
            lambda value: value.update(sample_rate=44100),
            lambda value: value["audio"].update(digest="not-a-digest"),
            lambda value: value["audio"].update(blocks=1),
            lambda value: value.update(recreate_equal=False),
            lambda value: value["audio"]["phases"][0].update(frequency_hz=float("nan")),
            lambda value: value["audio"]["phases"][1].update(peak=0.2),
            lambda value: value["audio"].update(rms_ratio=0.9),
            lambda value: value["audio"].update(input_channel_error=0.1),
            lambda value: value["audio"].update(missing_receiver_rejected=False),
            lambda value: value["audio"].update(readbacks=[]),
        )
        for corrupt in corruptions:
            report = copy.deepcopy(valid_report())
            corrupt(report)
            with self.subTest(report=report), self.assertRaises(RuntimeError):
                block_probe._validate(report)

    def test_sigterm_cancellation_reaps_child_and_restores_signal_handler(self):
        worker = self.root / "cancel_worker.py"
        marker = self.root / "cancel.pid"
        worker.write_text(
            "import pathlib, subprocess, sys, time\n"
            "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
        previous = signal.getsignal(signal.SIGTERM)
        timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                block_probe.run_probe(self.library, worker_script=worker, timeout=5)
        finally:
            timer.cancel()
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertTrue(marker.is_file())
        pid = int(marker.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail(f"cancelled worker descendant {pid} survived cleanup")

    @unittest.skipUnless(os.environ.get("DAW_LIBPD_LIBRARY"),
                         "set DAW_LIBPD_LIBRARY for installed libpd signal/recreation proof")
    def test_installed_libpd_signal_controls_and_instance_recreation(self):
        report = block_probe.run_probe()
        self.assertTrue(report["recreate_equal"])
        self.assertEqual(report["audio"]["blocks"], 768)
        self.assertAlmostEqual(report["audio"]["rms_ratio"], 0.5, delta=0.02)
        self.assertTrue(report["resources_released"])

    @unittest.skipUnless(os.environ.get("DAW_LIBPD_LIBRARY"),
                         "set DAW_LIBPD_LIBRARY for installed libpd fixture failure test")
    def test_installed_libpd_unknown_object_fixture_fails_without_report(self):
        fixture = self.root / "bad-object.pd"
        source = block_probe.FIXTURE.read_bytes().replace(b"osc~", b"unknown_pd_object")
        fixture.write_bytes(source)
        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            block_probe.run_probe(fixture=fixture)


if __name__ == "__main__":
    unittest.main()

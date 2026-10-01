"""Opt-in JSONL integration tests for owned live Csound transport.

Run on macOS arm64 with DAW_TEST_CSOUND_TRANSPORT=1, DAW_CSOUND_LIBRARY
pointing to the tested absolute Csound 7 double-sample library, and
target/debug/daw built with native-audio after native/csound/build_queue.py.
All hardware playback is muted.
"""
from __future__ import annotations

import json
import os
import platform
import select
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.csound_tracks_demo import track as csound_track  # noqa: E402

ENABLED = os.environ.get("DAW_TEST_CSOUND_TRANSPORT") == "1"
LIBRARY = os.environ.get("DAW_CSOUND_LIBRARY")
SCSYNTH = os.environ.get("DAW_SCSYNTH")
DAW = ROOT / "target/debug/daw"
MAX_LINE = 1_048_577


class ProtocolError(AssertionError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class JsonlClient:
    def __init__(self, overrides=None):
        env = os.environ.copy()
        env.update(overrides or {})
        env["DAW_CSOUND_LIBRARY"] = str(LIBRARY)
        if SCSYNTH:
            env["DAW_SCSYNTH"] = str(SCSYNTH)
        self.process = subprocess.Popen(
            [str(DAW), "serve"], cwd=ROOT, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.next_id = 0
        self.buffer = bytearray()

    def request(self, method: str, params: dict | None = None, timeout: float = 20) -> dict:
        self.next_id += 1
        request_id = str(self.next_id)
        payload = json.dumps({
            "protocol_version": 1, "id": request_id, "method": method,
            "params": params or {},
        }, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(payload) > MAX_LINE:
            raise AssertionError("test protocol request exceeded 1 MiB")
        if self.process.poll() is not None or self.process.stdin is None:
            raise AssertionError("JSONL engine exited before request")
        self.process.stdin.write(payload)
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[:newline + 1]
                response = json.loads(line)
                if response.get("id") != request_id:
                    raise AssertionError(f"unexpected JSONL response id: {response}")
                if not response.get("ok"):
                    error = response.get("error") or {}
                    raise ProtocolError(error.get("code", "unknown"), error.get("message", ""))
                return response["result"]
            if len(self.buffer) >= MAX_LINE:
                raise AssertionError("JSONL engine response exceeded 1 MiB")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {method} response")
            readable, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not readable:
                raise TimeoutError(f"timed out waiting for {method} response")
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise AssertionError("JSONL engine closed stdout before response")
            self.buffer.extend(chunk)

    def request_error(self, method: str, params: dict | None = None) -> ProtocolError:
        try:
            self.request(method, params)
        except ProtocolError as error:
            return error
        raise AssertionError(f"{method} unexpectedly succeeded")

    def close_eof(self, timeout: float = 20) -> int:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        return self.process.wait(timeout=timeout)

    def close(self):
        if self.process.poll() is None:
            try:
                self.close_eof(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


def builtin_track() -> dict:
    return {"id": "builtin", "mode": "continuous", "clips": [], "effects": [],
            "device": {"kind": "sine", "frequency_hz": 220.0, "gain": 0.08}}


def session_fixture() -> dict:
    first = csound_track("live-csound-one", 440.0, 0.5)
    second = csound_track("live-csound-two", 330.0, 0.0)
    for item, seconds in ((first, 1.0), (second, 1.0)):
        item["device"]["duration_frames"] = round(seconds * 48_000)
        item["device"]["controls"] = [
            {**control, "points": [point for point in control["points"]
                                    if point["frame"] < item["device"]["duration_frames"]]}
            for control in item["device"]["controls"]
        ]
    return {"schema_version": 7, "sample_rate": 48_000,
            "tempo_milli_bpm": 120_000,
            "tracks": [first, second, builtin_track()]}


def session_with_csound_duration(frames: int) -> dict:
    session = session_fixture()
    for item in session["tracks"][:2]:
        item["device"]["duration_frames"] = frames
        item["device"]["program"] = item["device"]["program"].replace("i1 0 1\n", "i1 0 3\n")
        item["device"]["controls"] = [
            {**control, "points": [point for point in control["points"]
                                    if point["frame"] < frames]}
            for control in item["device"]["controls"]
        ]
    return session


@unittest.skipUnless(ENABLED, "set DAW_TEST_CSOUND_TRANSPORT=1 for live transport checks")
class LiveCsoundTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise unittest.SkipTest("live Csound transport requires macOS arm64")
        if not DAW.is_file():
            raise unittest.SkipTest("build target/debug/daw with native-audio first")
        if not LIBRARY or not Path(LIBRARY).is_absolute() or not Path(LIBRARY).is_file():
            raise unittest.SkipTest("set DAW_CSOUND_LIBRARY to the tested absolute Csound 7 library")

    def setUp(self):
        self.client = JsonlClient()
        self.addCleanup(self.client.close)
        capabilities = self.client.request("capabilities")
        if not capabilities.get("live_audio"):
            self.skipTest("build target/debug/daw with native-audio")
        self.client.request("session.replace", {"session": session_fixture()})
        self.revision = self.client.request("session.inspect")["revision"]

    def start_live(self, seconds: float = 2.0) -> dict:
        status = self.client.request("transport.play", {
            "seconds": seconds, "volume": 0.0, "source_mode": "live",
        }, timeout=40)
        self.assertIn(status["state"], ("starting", "playing"))
        self.assertEqual(status["source_mode"], "live")
        self.assertIn(status["runtime"], ("csound", "mixed"))
        self.assertEqual(status["startup"], "prefilled")
        self.assertEqual(status["volume"], 0.0)
        self.assertTrue(status["source"].get("runtime_sources"))
        source = status["source"]
        pids = source["owned_pids"]
        self.assertTrue(pids)
        self.assertTrue(all(int(pid) > 0 for pid in pids))
        return status

    def wait_playing(self, timeout: float = 8.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.client.request("transport.status", timeout=3)
            if status.get("state") == "playing" and status.get("submitted_frames", 0) > 0:
                self.assertGreater(status.get("callback_signal_peak", 0), 0)
                return status
            if status.get("state") == "stopped":
                self.fail(f"transport stopped before callback playback: {status}")
            time.sleep(0.025)
        self.fail("live transport did not enter playing state")

    def assert_pids_reaped(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def assert_released(self, report: dict):
        self.assertEqual(report["state"], "stopped")
        self.assertTrue(report.get("resources_released"))
        source = report.get("source") or {}
        self.assertTrue(source.get("owned_processes_released"))
        self.assertTrue(source.get("queue_released"))

    def test_start_stop_restart_owns_two_csound_sources_and_mixes_builtin(self):
        for _ in range(2):
            started = self.start_live()
            playing = self.wait_playing()
            self.assertGreater(playing["submitted_frames"], 0)
            self.assertEqual(playing["volume"], 0.0)
            self.assertGreater(playing.get("callback_signal_peak", 0), 0)
            self.assertNotEqual(playing["callback_source_digest"], "0000000000000000")
            stopped = self.client.request("transport.stop", timeout=30)
            self.assert_released(stopped)
            self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_natural_completion_reports_exact_frames_matching_digest_and_quarters(self):
        started = self.start_live(seconds=1.0)
        deadline = time.monotonic() + 16
        final = None
        while time.monotonic() < deadline:
            final = self.client.request("transport.status", timeout=3)
            if final.get("state") == "stopped":
                break
            time.sleep(0.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        source = final["source"]
        self.assertEqual(source["source_frames"], 48_000)
        self.assertEqual(final["submitted_frames"], 48_000)
        self.assertEqual(final["live_source_underruns"], 0)
        self.assertEqual(source["source_digest"], final["callback_source_digest"])
        self.assertTrue(source.get("rms_quarters"))
        self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_exact_short_and_partial_block_source_durations_complete_cleanly(self):
        for frames in (48, 48_001):
            with self.subTest(source_frames=frames):
                self.client.request("session.replace", {
                    "session": session_with_csound_duration(frames),
                    "expected_revision": self.revision,
                })
                self.revision = self.client.request("session.inspect")["revision"]
                started = self.start_live(seconds=frames / 48_000)
                deadline = time.monotonic() + 12
                final = None
                while time.monotonic() < deadline:
                    final = self.client.request("transport.status", timeout=3)
                    if final.get("state") == "stopped":
                        break
                    time.sleep(0.025)
                self.assertIsNotNone(final)
                self.assert_released(final)
                self.assertEqual(final["submitted_frames"], frames)
                self.assertEqual(final["source"]["source_frames"], frames)
                self.assertEqual(final["source"]["source_digest"],
                                 final["callback_source_digest"])
                self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_playback_duration_shorter_and_longer_than_source_is_exact(self):
        cases = ((48_000, 0.5, 24_000), (48_000, 1.5, 72_000))
        for source_frames, seconds, expected_frames in cases:
            with self.subTest(seconds=seconds):
                self.client.request("session.replace", {
                    "session": session_with_csound_duration(source_frames),
                    "expected_revision": self.revision,
                })
                self.revision = self.client.request("session.inspect")["revision"]
                started = self.start_live(seconds=seconds)
                deadline = time.monotonic() + 12
                final = None
                while time.monotonic() < deadline:
                    final = self.client.request("transport.status", timeout=3)
                    if final.get("state") == "stopped":
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(final)
                self.assert_released(final)
                self.assertEqual(final["submitted_frames"], expected_frames)
                if expected_frames > source_frames:
                    self.assertAlmostEqual(final["source"]["rms_quarters"][-1],
                                           0.08 / (2 ** 0.5), delta=0.0001)
                self.assertEqual(final["source"]["source_frames"], expected_frames)
                self.assertEqual(final["source"]["source_digest"],
                                 final["callback_source_digest"])
                self.assert_pids_reaped(started["source"]["owned_pids"])

    @unittest.skipUnless(SCSYNTH and Path(SCSYNTH).is_absolute() and Path(SCSYNTH).is_file(),
                         "set DAW_SCSYNTH to an installed absolute scsynth for mixed-runtime coverage")
    def test_mixed_csound_and_supercollider_sources_complete_muted(self):
        from examples.supercollider_tracks_demo import make_track

        session = session_fixture()
        session["tracks"] = [
            session["tracks"][0],
            make_track("live-sc-mixed", 330.0, 0.06, 0.5, 1.0, 1.0),
            session["tracks"][2],
        ]
        self.client.request("session.replace", {"session": session,
                                                   "expected_revision": self.revision})
        self.revision = self.client.request("session.inspect")["revision"]
        started = self.start_live(seconds=1.0)
        self.assertEqual(started["runtime"], "mixed")
        self.assertEqual(started["source"]["runtime_sources"], 2)
        self.assertEqual(started["volume"], 0.0)
        deadline = time.monotonic() + 16
        final = None
        while time.monotonic() < deadline:
            final = self.client.request("transport.status", timeout=3)
            if final.get("state") == "stopped":
                break
            time.sleep(0.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        self.assertEqual(final["submitted_frames"], 48_000)
        self.assertEqual(final["source"]["source_frames"], 48_000)
        self.assertEqual(final["source"]["source_digest"],
                         final["callback_source_digest"])
        self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_invalid_live_requests_rollback_without_children_or_revision_change(self):
        invalid_effect = session_fixture()
        invalid_effect["tracks"][0]["effects"].append(
            {"kind": "vst3", "id": "unsupported", "plugin": {}, "parameters": []})
        legacy_schema = session_fixture()
        legacy_schema["schema_version"] = 6
        for bad_session in (invalid_effect, legacy_schema):
            error = self.client.request_error("session.replace", {"session": bad_session})
            self.assertIn(error.code, ("invalid_session", "invalid_params"))
            self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)
        for params in (
            {"seconds": 1.0, "volume": 0.0, "source_mode": "unknown"},
            {"seconds": 10.1, "volume": 0.0, "source_mode": "live"},
        ):
            error = self.client.request_error("transport.play", params)
            self.assertEqual(error.code, "invalid_params")
            self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)
            self.assertEqual(self.client.request("transport.status")["state"], "stopped")
            children = subprocess.run(
                ["/usr/bin/pgrep", "-P", str(self.client.process.pid)],
                capture_output=True, text=True, timeout=2, check=False,
            )
            self.assertNotEqual(children.returncode, 0, children.stdout)

    def test_unsupported_timeline_commands_preserve_running_live_transport(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        for method, params in (
            ("transport.pause", {}),
            ("transport.resume", {}),
            ("transport.seek", {"frame": 0}),
            ("transport.loop", {"region": {"start_frame": 0, "end_frame": 48_000}}),
            ("source.set_control", {"expected_revision": self.revision, "track_id": "live-csound-one", "control_name": "frequency", "values": [660.0]}),
        ):
            with self.subTest(method=method):
                before = self.client.request("transport.status")
                error = self.client.request_error(method, params)
                self.assertEqual(error.code, "invalid_params" if method == "source.set_control" else "audio_error")
                after = self.client.request("transport.status")
                self.assertEqual(before["state"], "playing")
                self.assertEqual(after["state"], "playing")
        stopped = self.client.request("transport.stop", timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_owned_source_death_stops_transport_and_reaps_all_processes(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        pids = [int(pid) for pid in started["source"]["owned_pids"]]
        direct = subprocess.run(
            ["/usr/bin/pgrep", "-P", str(self.client.process.pid)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertTrue(set(pids).issubset({int(line) for line in direct.stdout.splitlines()}))
        os.kill(pids[0], signal.SIGKILL)
        deadline = time.monotonic() + 12
        failed = None
        while time.monotonic() < deadline:
            failed = self.client.request("transport.status", timeout=3)
            if failed.get("state") == "stopped" and failed.get("error"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(failed)
        self.assertEqual(failed["state"], "stopped")
        self.assertTrue(failed.get("resources_released"))
        self.assertEqual(failed["error"]["code"], "runtime_error")
        self.assert_pids_reaped(pids)

    def test_protocol_eof_releases_queue_and_owned_processes(self):
        started = self.start_live(seconds=3.0)
        self.wait_playing()
        pids = started["source"]["owned_pids"]
        self.assertEqual(self.client.close_eof(timeout=30), 0)
        self.assert_pids_reaped(pids)


if __name__ == "__main__":
    unittest.main()

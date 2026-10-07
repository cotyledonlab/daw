"""Opt-in JSONL integration tests for live SC transport ownership.

Run on macOS with DAW_TEST_SC_TRANSPORT=1, DAW_SCSYNTH set to an installed
absolute executable, and target/debug/daw built with native-audio.
"""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.supercollider_tracks_demo import make_track  # noqa: E402

ENABLED = os.environ.get("DAW_TEST_SC_TRANSPORT") == "1"
SCSYNTH = os.environ.get("DAW_SCSYNTH")
DAW = ROOT / "target/debug/daw"
MAX_LINE = 1_048_577


class ProtocolError(AssertionError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class JsonlClient:
    def __init__(self):
        env = os.environ.copy()
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


def session_fixture() -> dict:
    track = make_track("live-sc", 440.0, 0.1, 0.5, 1.0, 1.0)
    track["device"]["duration_frames"] = 48_000
    for control in track["device"]["controls"]:
        control["points"] = [point for point in control["points"]
                             if point["frame"] < track["device"]["duration_frames"]]
    return {"schema_version": 6, "sample_rate": 48_000,
            "tempo_milli_bpm": 120_000, "tracks": [track, make_track("live-sc-two",330.0,0.05,0.5,1.0,1.0)]}


@unittest.skipUnless(ENABLED, "set DAW_TEST_SC_TRANSPORT=1 for live transport checks")
class LiveSuperColliderTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("live SC transport requires macOS")
        if not DAW.is_file():
            raise unittest.SkipTest("build target/debug/daw with native-audio first")
        if not SCSYNTH or not Path(SCSYNTH).is_absolute() or not Path(SCSYNTH).is_file():
            raise unittest.SkipTest("set DAW_SCSYNTH to an installed absolute scsynth executable")

    def setUp(self):
        self.client = JsonlClient()
        self.addCleanup(self.client.close)
        capabilities = self.client.request("capabilities")
        if not capabilities.get("live_audio"):
            self.skipTest("build target/debug/daw with native-audio")
        if not capabilities.get("supercollider_nrt", {}).get("configured"):
            self.skipTest("DAW_SCSYNTH is not configured in the engine")
        self.client.request("session.replace", {"session": session_fixture()})
        self.revision = self.client.request("session.inspect")["revision"]

    def start_live(self, seconds: float = 3.0) -> dict:
        status = self.client.request("transport.play", {
            "seconds": seconds, "volume": 0.0, "source_mode": "live",
        }, timeout=30)
        self.assertIn(status["state"], ("starting", "playing"))
        self.assertEqual(status["source_mode"], "live")
        self.assertEqual(status["runtime"], "supercollider")
        self.assertEqual(status["startup"], "prefilled")
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
                self.assertEqual(status.get("live_source_underruns"), 0)
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
        self.assertTrue(source.get("owned_servers_released"))
        self.assertTrue(source.get("queue_released"))

    def test_repeat_live_start_stop_releases_owned_sources(self):
        for _ in range(2):
            started = self.start_live()
            playing = self.wait_playing()
            deadline = time.monotonic() + 3
            later = playing
            while time.monotonic() < deadline and later["submitted_frames"] <= playing["submitted_frames"]:
                time.sleep(0.025)
                later = self.client.request("transport.status")
            self.assertGreater(later["submitted_frames"], playing["submitted_frames"])
            self.assertNotEqual(later["callback_source_digest"], "0000000000000000")
            final = self.client.request("transport.stop", timeout=20)
            self.assert_released(final)
            self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_natural_live_completion_reports_matching_full_source_digest(self):
        started = self.start_live(seconds=1.0)
        deadline = time.monotonic() + 12
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
        self.assertEqual(source["source_digest"], final["callback_source_digest"])
        self.assertEqual(final["live_source_underruns"], 0)
        self.assertGreater(final["callback_signal_peak"], 0)
        self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_volume_and_unsupported_controls_preserve_live_state(self):
        self.start_live()
        self.wait_playing()
        volume = self.client.request("transport.volume", {"volume": 0.0})
        self.assertEqual(volume["state"], "playing")
        self.assertEqual(volume["volume"], 0.0)
        for method, params in (
            ("transport.pause", {}),
            ("transport.resume", {}),
            ("transport.seek", {"frame": 0}),
            ("transport.loop", {"region": {"start_frame": 0, "end_frame": 48_000}}),
        ):
            with self.subTest(method=method):
                before = self.client.request("transport.status")
                error = self.client.request_error(method, params)
                self.assertEqual(error.code, "audio_error")
                after = self.client.request("transport.status")
                self.assertEqual(before["state"], "playing")
                self.assertEqual(after["state"], before["state"])
        stopped = self.client.request("transport.stop", timeout=20)
        self.assert_released(stopped)

    def test_invalid_mode_and_overlength_live_request_leave_revision_and_children_unchanged(self):
        for params, expected_code in (
            ({"seconds": 1.0, "volume": 0.0, "source_mode": "unknown"}, "invalid_params"),
            ({"seconds": 10.1, "volume": 0.0, "source_mode": "live"}, "invalid_params"),
        ):
            error = self.client.request_error("transport.play", params)
            self.assertEqual(error.code, expected_code)
            self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)
            self.assertEqual(self.client.request("transport.status")["state"], "stopped")
            children = subprocess.run(
                ["/usr/bin/pgrep", "-P", str(self.client.process.pid)],
                capture_output=True, text=True, timeout=2, check=False,
            )
            self.assertNotEqual(children.returncode, 0, children.stdout)

    def test_owned_producer_death_stops_transport_and_reaps_all_sources(self):
        started = self.start_live()
        playing = self.wait_playing()
        pids = [int(pid) for pid in playing["source"]["owned_pids"]]
        direct = subprocess.run(
            ["/usr/bin/pgrep", "-P", str(self.client.process.pid)],
            capture_output=True, text=True, timeout=2, check=False,
        )
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertTrue(set(pids).issubset({int(line) for line in direct.stdout.splitlines()}))
        os.kill(pids[0], signal.SIGKILL)
        deadline = time.monotonic() + 10
        failed = None
        while time.monotonic() < deadline:
            failed = self.client.request("transport.status", timeout=3)
            if failed.get("state") == "stopped" and failed.get("error"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(failed)
        self.assertEqual(failed["state"], "stopped")
        self.assertTrue(failed["resources_released"])
        self.assertEqual(failed["error"]["code"], "runtime_error")
        self.assertIn("owned scsynth exited", failed["error"]["message"])
        self.assert_pids_reaped(pids)

    def test_protocol_eof_stops_playback_and_reaps_owned_servers(self):
        started = self.start_live()
        self.wait_playing()
        pids = started["source"]["owned_pids"]
        return_code = self.client.close_eof(timeout=20)
        self.assertEqual(return_code, 0)
        self.assert_pids_reaped(pids)


if __name__ == "__main__":
    unittest.main()

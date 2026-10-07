"""Opt-in muted JSONL tests for live Csound control edits.

Run on macOS arm64 with DAW_TEST_CSOUND_CONTROLS=1,
DAW_CSOUND_LIBRARY set to the tested absolute Csound 7 double-sample library,
and a target/debug/daw binary built with native-audio and native/csound/build_queue.py.
"""
from __future__ import annotations

import json
import os
import platform
import select
import sys
import time
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.csound.test_live_transport import (  # noqa: E402
    DAW, LIBRARY, SCSYNTH, JsonlClient, session_fixture,
)

ENABLED = os.environ.get("DAW_TEST_CSOUND_CONTROLS") == "1"


def control_session() -> dict:
    session = session_fixture()
    tracks = session["tracks"][:2]
    session["tracks"] = tracks
    for track in tracks:
        source = track["device"]
        source["duration_frames"] = 4 * 48_000
        source["program"] = source["program"].replace("i1 0 1", "i1 0 10")
        for control in source["controls"]:
            control["points"] = []
    return session


def control_params(revision: str, values: list) -> dict:
    return {"expected_revision": revision, "track_id": "live-csound-one",
            "control_name": "amplitude", "values": values}


@unittest.skipUnless(ENABLED, "set DAW_TEST_CSOUND_CONTROLS=1 for live control checks")
class LiveCsoundControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin" or platform.machine() != "arm64":
            raise unittest.SkipTest("live Csound controls require macOS arm64")
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
        self.client.request("session.replace", {"session": control_session()})
        self.revision = self.client.request("session.inspect")["revision"]

    def start_live(self) -> dict:
        started = self.client.request("transport.play", {
            "seconds": 4.0, "volume": 0.0, "source_mode": "live",
        }, timeout=40)
        self.assertIn(started["state"], ("starting", "playing"))
        self.assertEqual(started["volume"], 0.0)
        self.assertTrue(started["source"]["owned_pids"])
        return started

    def wait_update_observed(self, revision: str, timeout: float = 3.0) -> dict:
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            latest = self.client.request("transport.status", timeout=3)
            update = latest.get("source_control_update") or {}
            if (not update.get("pending") and update.get("applied_revision") == revision
                    and update.get("callback_observed")):
                return latest
            if latest.get("state") not in ("starting", "playing"):
                self.fail(f"transport stopped before callback observed control update: {latest}")
            time.sleep(0.01)
        self.fail(f"control update was not observed by callback: {latest}")

    def assert_released(self, report: dict):
        self.assertEqual(report["state"], "stopped")
        self.assertTrue(report.get("resources_released"))
        source = report.get("source") or {}
        self.assertTrue(source.get("owned_processes_released"))
        self.assertTrue(source.get("queue_released"))

    def assert_pids_reaped(self, pids):
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid), 0)

    def request_nonfinite_control(self, revision: str) -> dict:
        """Send a JSON numeric token that Python's strict encoder rejects."""
        client = self.client
        client.next_id += 1
        request_id = str(client.next_id)
        body = json.dumps({
            "protocol_version": 1, "id": request_id, "method": "source.set_control",
            "params": control_params(revision, ["__NONFINITE__"]),
        }, separators=(",", ":")).replace('"__NONFINITE__"', "NaN")
        client.process.stdin.write(body.encode("utf-8") + b"\n")
        client.process.stdin.flush()
        deadline = time.monotonic() + 3
        while True:
            newline = client.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(client.buffer[:newline])
                del client.buffer[:newline + 1]
                response = json.loads(line)
                self.assertFalse(response.get("ok"))
                self.assertEqual(response.get("error", {}).get("code"), "invalid_request")
                return response
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "timed out waiting for non-finite request rejection")
            readable, _, _ = select.select([client.process.stdout], [], [], remaining)
            self.assertTrue(readable, "timed out waiting for non-finite request rejection")
            client.buffer.extend(os.read(client.process.stdout.fileno(), 4096))

    def test_live_base_edit_roundtrips_and_changes_later_prepared_render(self):
        started = self.start_live()
        precise = 0.040000000000001
        queued = self.client.request("source.set_control", control_params(self.revision, [precise]))
        self.revision = queued["revision"]
        self.assertTrue(queued.get("queued"))
        saved = self.client.request("session.get")
        amplitude = next(control for control in saved["tracks"][0]["device"]["controls"]
                         if control["name"] == "amplitude")
        self.assertEqual(amplitude["value"], precise)
        observed = self.wait_update_observed(self.revision)
        update = observed["source_control_update"]
        self.assertIsNotNone(update.get("applied_frame"))
        self.assertTrue(update["callback_observed"])

        deadline = time.monotonic() + 8
        final = None
        while time.monotonic() < deadline:
            final = self.client.request("transport.status", timeout=3)
            if final.get("state") == "stopped":
                break
            time.sleep(0.05)
        self.assertIsNotNone(final)
        self.assert_released(final)
        source = final["source"]
        self.assertEqual(final["submitted_frames"], 4 * 48_000)
        self.assertEqual(source["source_frames"], 4 * 48_000)
        self.assertEqual(source["source_digest"], final["callback_source_digest"])
        quarters = source["rms_quarters"]
        self.assertEqual(len(quarters), 4)
        self.assertAlmostEqual(quarters[-1], 0.007071, delta=0.001)
        self.assert_pids_reaped(started["source"]["owned_pids"])

        # The live edit invalidates its prepared PCM cache; render must
        # rebuild from the newly saved base without touching its saved revision.
        inspect_before = self.client.request("session.inspect")
        directory = Path(tempfile.mkdtemp(prefix="csound-live-control-", dir=ROOT / "output"))
        output = directory / "render.wav"
        saved_path = directory / "session.json"
        self.client.request("session.save", {"path": str(saved_path)})
        self.assertEqual(json.loads(saved_path.read_text()), saved)
        self.client.request("render", {"path": str(output), "seconds": 0.25})
        self.addCleanup(output.unlink, missing_ok=True)
        import wave

        with wave.open(str(output), "rb") as stream:
            frames = stream.readframes(stream.getnframes())
        import struct

        pcm = struct.unpack("<{}h".format(len(frames) // 2), frames)
        self.assertGreater(max(map(abs, pcm)), 300)
        self.assertLess(max(map(abs, pcm)), 360)
        self.assertEqual(self.client.request("session.inspect"), inspect_before)
        self.client.request("session.load", {"path": str(saved_path)})
        self.assertEqual(self.client.request("session.get"), saved)

    def test_automation_stale_names_dimensions_and_nonfinite_values_rollback(self):
        automated = control_session()
        amplitude = next(control for control in automated["tracks"][0]["device"]["controls"]
                         if control["name"] == "amplitude")
        amplitude["points"] = [{"frame": 0, "value": 0.1}]
        self.client.request("session.replace", {"session": automated,
                                                  "expected_revision": self.revision})
        self.revision = self.client.request("session.inspect")["revision"]
        cases = (
            (control_params("stale-revision", [0.04]), "invalid_params"),
            (control_params("0", [0.04]), "revision_conflict"),
            (control_params(self.revision, []), "invalid_params"),
            (control_params(self.revision, [0.04, 0.05]), "invalid_params"),
            ({**control_params(self.revision, [0.04]), "control_name": "missing"}, "invalid_params"),
            (control_params(self.revision, [0.04]), "invalid_params"),
        )
        for params, code in cases:
            with self.subTest(params=params):
                error = self.client.request_error("source.set_control", params)
                self.assertEqual(error.code, code)
                self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)
        self.request_nonfinite_control(self.revision)
        self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)
        self.assertEqual(self.client.request("transport.status")["state"], "stopped")

    def test_stopped_and_prepared_playback_reject_live_csound_controls(self):
        error = self.client.request_error("source.set_control", control_params(self.revision, [0.04]))
        self.assertEqual(error.code, "audio_error")
        self.client.request("transport.play", {"seconds": 0.5, "volume": 0.0}, timeout=30)
        error = self.client.request_error("source.set_control", control_params(self.revision, [0.04]))
        self.assertEqual(error.code, "audio_error")
        stopped = self.client.request("transport.stop", timeout=30)
        self.assertTrue(stopped.get("resources_released"))
        self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)

    def test_multiple_queued_edits_stop_with_all_owned_workers_released(self):
        started = self.start_live()
        revision = self.revision
        for amplitude in (0.09, 0.08, 0.07, 0.06):
            queued = self.client.request("source.set_control", control_params(revision, [amplitude]))
            self.assertTrue(queued.get("queued"))
            revision = queued["revision"]
        self.revision = revision
        stopped = self.client.request("transport.stop", timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started["source"]["owned_pids"])
        self.assertEqual(self.client.request("session.inspect")["revision"], revision)

    def test_protocol_eof_releases_after_a_queued_control_update(self):
        started = self.start_live()
        queued = self.client.request("source.set_control", control_params(self.revision, [0.04]))
        self.assertTrue(queued.get("queued"))
        self.assertEqual(self.client.close_eof(timeout=30), 0)
        self.assert_pids_reaped(started["source"]["owned_pids"])

    def test_bad_or_missing_native_ack_fails_transport_preserving_committed_base(self):
        for fault in ("wrong_value", "missing", "death"):
            with self.subTest(fault=fault):
                self.client.close()
                directory = Path(tempfile.mkdtemp(prefix="csound-control-fault-", dir=ROOT / "output"))
                worker = directory / "worker.py"
                worker.write_text(
                    "import sys, os\n"
                    f"sys.path.insert(0, {str(ROOT / 'native/csound')!r})\n"
                    "import stream_worker as worker\n"
                    "original = worker.publish_json\n"
                    "def publish(path, value):\n"
                    "    if str(path).endswith('/ack.json'):\n"
                    f"        fault = {fault!r}\n"
                    "        if fault == 'missing': return\n"
                    "        if fault == 'death': os._exit(23)\n"
                    "        value = {**value, 'value': value['value'] + .01}\n"
                    "    original(path, value)\n"
                    "worker.publish_json = publish\n"
                    "worker.stream(*sys.argv[1:])\n"
                )
                self.client = JsonlClient({"DAW_CSOUND_STREAM_WORKER": str(worker)})
                self.addCleanup(self.client.close)
                self.client.request("session.replace", {"session": control_session()})
                self.revision = self.client.request("session.inspect")["revision"]
                started = self.start_live()
                queued = self.client.request("source.set_control", control_params(self.revision, [0.04]))
                self.revision = queued["revision"]
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    failed = self.client.request("transport.status")
                    if failed["state"] == "stopped":
                        break
                    time.sleep(.02)
                self.assertEqual(failed["state"], "stopped", failed)
                self.assertEqual(failed["error"]["code"], "runtime_error")
                self.assertTrue(failed["resources_released"])
                self.assert_pids_reaped(started["source"]["owned_pids"])
                saved = self.client.request("session.get")
                amplitude = next(control for control in saved["tracks"][0]["device"]["controls"]
                                 if control["name"] == "amplitude")
                self.assertEqual(amplitude["value"], .04)
                self.assertEqual(self.client.request("session.inspect")["revision"], self.revision)

    @unittest.skipUnless(SCSYNTH and Path(SCSYNTH).is_absolute() and Path(SCSYNTH).is_file(),
                         "set DAW_SCSYNTH for mixed-runtime control coverage")
    def test_csound_control_edit_works_with_mixed_supercollider_runtime(self):
        from examples.supercollider_tracks_demo import make_track

        session = control_session()
        session["tracks"] = [session["tracks"][0],
                             make_track("live-sc-control", 330.0, 0.04, 0.5, 1.0, 1.0)]
        self.client.request("session.replace", {"session": session,
                                                  "expected_revision": self.revision})
        self.revision = self.client.request("session.inspect")["revision"]
        started = self.start_live()
        queued = self.client.request("source.set_control", control_params(self.revision, [0.04]))
        self.revision = queued["revision"]
        self.assertTrue(queued.get("queued"))
        self.wait_update_observed(self.revision)
        sc_edit = self.client.request("source.set_control", {
            "expected_revision": self.revision, "track_id": "live-sc-control",
            "control_name": "gain", "values": [0.02],
        })
        self.revision = sc_edit["revision"]
        self.wait_update_observed(self.revision)
        cs_edit = self.client.request("source.set_control", control_params(self.revision, [0.05]))
        self.revision = cs_edit["revision"]
        self.wait_update_observed(self.revision)
        stopped = self.client.request("transport.stop", timeout=30)
        self.assert_released(stopped)
        self.assert_pids_reaped(started["source"]["owned_pids"])


if __name__ == "__main__":
    unittest.main()

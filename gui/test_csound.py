"""Opt-in real-engine HTTP checks for saved Csound GUI sessions."""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

from examples.csound_tracks_demo import track as make_csound_track
from gui import test_server as bridge_tests

ROOT = bridge_tests.ROOT
CSOUND_LIBRARY = Path(os.environ.get(
    "DAW_CSOUND_LIBRARY",
    ROOT / "output/csound-runtime/extracted/Payload/Applications/Csound/"
    "CsoundLib64.framework/Versions/7.0/CsoundLib64",
))


@unittest.skipUnless(os.environ.get("DAW_TEST_CSOUND_GUI") == "1",
                     "set DAW_TEST_CSOUND_GUI=1 for muted native Csound GUI checks")
class CsoundGuiTests(unittest.TestCase):
    # Reuse the real loopback HTTP server helpers without inheriting other tests.
    request = bridge_tests.ServerIntegrationTests.request
    post = bridge_tests.ServerIntegrationTests.post
    get_session = bridge_tests.ServerIntegrationTests.get_session

    @classmethod
    def setUpClass(cls):
        if not bridge_tests.BINARY.is_file():
            raise unittest.SkipTest("existing target/debug/daw binary is required; this test does not build it")
        if not CSOUND_LIBRARY.is_absolute() or not CSOUND_LIBRARY.is_file():
            raise unittest.SkipTest("set DAW_CSOUND_LIBRARY to an installed absolute Csound 7 library")

    def setUp(self):
        self.old_library = os.environ.get("DAW_CSOUND_LIBRARY")
        os.environ["DAW_CSOUND_LIBRARY"] = str(CSOUND_LIBRARY)
        bridge_tests.ServerIntegrationTests.setUp(self)

    def tearDown(self):
        try:
            bridge_tests.ServerIntegrationTests.tearDown(self)
        finally:
            if self.old_library is None:
                os.environ.pop("DAW_CSOUND_LIBRARY", None)
            else:
                os.environ["DAW_CSOUND_LIBRARY"] = self.old_library

    def csound_session(self):
        track = make_csound_track("gui-csound", 440.0, 0.5)
        track["device"]["duration_frames"] = 192_000
        track["device"]["program"] = track["device"]["program"].replace("i1 0 1", "i1 0 10")
        return {"schema_version": 7, "sample_rate": 48_000,
                "tempo_milli_bpm": 120_000, "tracks": [track]}

    def wait_status(self, predicate, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, body, _ = self.request("GET", "/api/transport")
            self.assertEqual(status, 200, body)
            snapshot = json.loads(body)
            if predicate(snapshot):
                return snapshot
            time.sleep(0.025)
        self.fail(f"transport did not reach expected state; latest snapshot: {snapshot}")

    def test_import_control_ack_stale_automation_and_natural_cleanup(self):
        status, body, _ = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200, body)
        capabilities = json.loads(body)
        if not capabilities.get("live_audio"):
            self.skipTest("the existing target/debug/daw binary must include native-audio")
        session = self.csound_session()
        program = session["tracks"][0]["device"]["program"]
        status, body, _ = self.post("/api/session", {"session": session,
                                                       "expected_revision": "0"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), session)
        self.assertEqual(self.get_session(), session)
        self.assertEqual(self.get_session()["tracks"][0]["device"]["program"], program)

        # Rust preparation is authoritative for Csound channel declarations and
        # must retain the previous session if a replacement names no channel.
        bad_channel = json.loads(json.dumps(session))
        bad_channel["tracks"][0]["device"]["controls"][0]["name"] = "missing-channel"
        status, body, _ = self.post("/api/session", {"session": bad_channel,
                                                       "expected_revision": "1"})
        self.assertEqual(status, 422, body)
        self.assertEqual(self.get_session(), session)

        # Saved automation owns the frequency control and cannot be replaced by
        # a live base edit; the rejected edit leaves the revision unchanged.
        automated = {"expected_revision": "1", "track_id": "gui-csound",
                     "control_name": "frequency", "values": [660.0]}
        status, body, _ = self.post("/api/source/control", automated)
        self.assertEqual(status, 422, body)
        self.assertEqual(self.get_session(), session)

        status, body, _ = self.post("/api/transport", {
            "action": "play", "source_mode": "live", "seconds": 4.0, "volume": 0.0,
        })
        self.assertEqual(status, 200, body)
        started = json.loads(body)
        self.assertEqual(started["source_mode"], "live")
        self.assertEqual(started["runtime"], "csound")
        self.assertEqual(started["volume"], 0.0)

        before_edit = self.wait_status(lambda item: item.get("state") == "playing"
                                       and item.get("submitted_frames", 0) >= 24_000)
        self.assertEqual(before_edit["volume"], 0.0)
        change = {"expected_revision": "1", "track_id": "gui-csound",
                  "control_name": "amplitude", "values": [0.04]}
        status, body, _ = self.post("/api/source/control", change)
        self.assertEqual(status, 200, body)
        accepted = json.loads(body)
        self.assertEqual(accepted["revision"], "2")
        self.assertTrue(accepted.get("queued"))
        stale_status, stale_body, _ = self.post("/api/source/control", change)
        self.assertEqual(stale_status, 422, stale_body)
        self.assertEqual(self.get_session()["tracks"][0]["device"]["controls"][1]["value"], 0.04)

        observed = False
        snapshot = None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            _, body, _ = self.request("GET", "/api/transport")
            snapshot = json.loads(body)
            update = snapshot.get("source_control_update", {})
            observed |= (update.get("applied_revision") == "2"
                         and update.get("callback_observed") is True
                         and update.get("applied_frame") is not None)
            if snapshot.get("state") == "stopped":
                break
            time.sleep(0.025)
        self.assertTrue(observed, snapshot)
        self.assertNotIn("error", snapshot)
        self.assertEqual(snapshot["state"], "stopped")
        self.assertTrue(snapshot["resources_released"])
        self.assertEqual(snapshot["live_source_underruns"], 0)
        self.assertEqual(snapshot["submitted_frames"], 192_000)
        self.assertEqual(snapshot["source"]["source_frames"], 192_000)
        self.assertEqual(snapshot["source"]["source_digest"], snapshot["callback_source_digest"])
        self.assertTrue(snapshot["source"]["owned_processes_released"])
        self.assertTrue(snapshot["source"]["queue_released"])


if __name__ == "__main__":
    unittest.main()

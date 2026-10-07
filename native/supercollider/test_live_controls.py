"""Opt-in JSONL tests for saved-base edits during live SC transport.

Run on macOS with DAW_TEST_SC_CONTROLS=1, DAW_SCSYNTH set to an installed
absolute executable, and target/debug/daw built with native-audio.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from examples.supercollider_tracks_demo import make_track  # noqa: E402
from native.supercollider.test_live_transport import (  # noqa: E402
    DAW, JsonlClient, SCSYNTH,
)
from native.supercollider.test_sources import init_rate_sine_synthdef  # noqa: E402
from native.supercollider.test_inspect import pstring, ugen  # noqa: E402

ENABLED = os.environ.get("DAW_TEST_SC_CONTROLS") == "1"


def session_fixture(*, init_rate: bool = False, gain_points: list | None = None) -> dict:
    track = make_track("control-live", 440.0, 0.1, 0.5, 1.0, 1.0)
    source = track["device"]
    source["duration_frames"] = 192_000
    if init_rate:
        source["synthdef_hex"] = init_rate_sine_synthdef().hex()
    for control in source["controls"]:
        control["points"] = []
    if gain_points is not None:
        next(control for control in source["controls"] if control["name"] == "gain")["points"] = gain_points
    return {"schema_version": 6, "sample_rate": 48_000,
            "tempo_milli_bpm": 120_000, "tracks": [track]}


def array_gain_synthdef() -> bytes:
    """Sine SynthDef whose named gain control occupies two consecutive values."""
    ugens = [
        ugen("Control", 1, [], [1, 1, 1, 1]),
        ugen("SinOsc", 2, [(0, 0), (-1, 0)], [2]),
        ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
        ugen("Out", 2, [(0, 3), (2, 0), (2, 0)], []),
    ]
    body = bytearray(pstring("daw_sine_fixture"))
    body.extend(struct.pack(">i", 1))
    body.extend(struct.pack(">f", 0.0))
    defaults = (440.0, 0.1, 0.1, 0.0)
    body.extend(struct.pack(">i", len(defaults)))
    body.extend(struct.pack(">" + "f" * len(defaults), *defaults))
    names = (("freq", 0), ("gain", 1), ("out", 3))
    body.extend(struct.pack(">i", len(names)))
    for name, index in names:
        body.extend(pstring(name))
        body.extend(struct.pack(">i", index))
    body.extend(struct.pack(">i", len(ugens)))
    body.extend(b"".join(ugens))
    body.extend(struct.pack(">H", 0))
    return b"SCgf" + struct.pack(">iH", 2, 1) + body


def array_gain_session() -> dict:
    session = session_fixture()
    source = session["tracks"][0]["device"]
    source["synthdef_hex"] = array_gain_synthdef().hex()
    source["controls"] = [
        {"name": "freq", "values": [440.0], "points": []},
        {"name": "gain", "values": [0.1, 0.1], "points": []},
        {"name": "out", "values": [0.0], "points": []},
    ]
    return session


def control_values(session: dict, name: str) -> list[float]:
    control = next(item for item in session["tracks"][0]["device"]["controls"]
                   if item["name"] == name)
    return control["values"]


@unittest.skipUnless(ENABLED, "set DAW_TEST_SC_CONTROLS=1 for live SC control checks")
class LiveSuperColliderControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("live SC controls require macOS")
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
        self.replace(session_fixture())

    def replace(self, session: dict) -> None:
        self.client.request("session.replace", {"session": session})
        self.revision = self.client.request("session.inspect")["revision"]

    def start_live(self, seconds: float = 4.0) -> dict:
        status = self.client.request("transport.play", {
            "seconds": seconds, "volume": 0.0, "source_mode": "live",
        }, timeout=30)
        self.assertIn(status["state"], ("starting", "playing"))
        self.assertEqual(status["source_mode"], "live")
        self.assertEqual(status["runtime"], "supercollider")
        self.assertEqual(status["startup"], "prefilled")
        self.assertTrue(status["source"]["owned_pids"])
        return status

    def wait_status(self, predicate, timeout: float = 8.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.client.request("transport.status", timeout=3)
            if predicate(status):
                return status
            if status.get("state") == "stopped" and status.get("error"):
                self.fail(f"live playback failed while waiting: {status}")
            time.sleep(0.025)
        self.fail("timed out waiting for live status")

    def wait_submitted(self, minimum: int) -> dict:
        return self.wait_status(lambda status: status.get("submitted_frames", 0) >= minimum,
                                timeout=8.0)

    def wait_control_ack(self, revision: str) -> tuple[dict, dict]:
        def acknowledged(status):
            update = status.get("source_control_update") or {}
            return (not update.get("pending", True)
                    and update.get("applied_revision") == revision
                    and update.get("callback_observed") is True)
        status = self.wait_status(acknowledged, timeout=8.0)
        return status, status["source_control_update"]

    def assert_unchanged_revision(self, revision: str, expected_gain=0.1):
        state = self.client.request("session.inspect")
        self.assertEqual(state["revision"], revision)
        self.assertEqual(control_values(state["session"], "gain"), [expected_gain])

    def test_gain_control_is_applied_acknowledged_and_saved_as_new_base(self):
        self.start_live(seconds=4.0)
        self.wait_submitted(67_200)  # about 1.4 s; leaves most of the session after the edit
        before = self.client.request("transport.status")
        old_revision = self.client.request("session.inspect")["revision"]
        ack_started = time.monotonic()
        queued = self.client.request("source.set_control", {
            "expected_revision": old_revision, "track_id": "control-live",
            "control_name": "gain", "values": [0.02],
        })
        self.assertTrue(queued["queued"])
        revision = queued["revision"]
        self.assertNotEqual(revision, old_revision)
        self.assertEqual(control_values(queued["session"], "gain"), [0.02])
        self.assertEqual(control_values(self.client.request("session.inspect")["session"], "gain"), [0.02])

        _, update = self.wait_control_ack(revision)
        elapsed_ms = (time.monotonic() - ack_started) * 1000
        print(f"SC scalar gain command-to-callback observation: {elapsed_ms:.1f} ms",
              file=sys.stderr)
        self.assertLess(elapsed_ms, 2_000)
        self.assertEqual(update["applied_revision"], revision)
        self.assertGreater(update["applied_frame"], 0)
        self.assertTrue(update["callback_observed"])
        self.assertFalse(update["pending"])
        self.assertEqual(before["source_mode"], "live")

        with tempfile.TemporaryDirectory(prefix="daw-sc-control-save-", dir=ROOT / "output") as temporary:
            path = Path(temporary) / "edited-session.json"
            self.client.request("session.save", {"path": str(path)})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(control_values(saved, "gain"), [0.02])
            deadline = time.monotonic() + 10
            final = None
            while time.monotonic() < deadline:
                final = self.client.request("transport.status", timeout=3)
                if final.get("state") == "stopped":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(final)
            self.assertEqual(final["state"], "stopped")
            self.assertTrue(final["resources_released"])
            report = final["source"]
            self.assertTrue(report["owned_servers_released"])
            self.assertTrue(report["queue_released"])
            self.assertEqual(report["source_frames"], 192_000)
            self.assertEqual(final["submitted_frames"], 192_000)
            self.assertEqual(report["source_digest"], final["callback_source_digest"])
            self.assertEqual(final["live_source_underruns"], 0)
            self.assertTrue(math.isclose(report["rms_quarters"][-1] / report["rms_quarters"][0],
                                         0.2, rel_tol=0.15))
            rendered = Path(temporary) / "edited-base-render.wav"
            self.client.request("render", {"path": str(rendered), "seconds": 0.25})
            with wave.open(str(rendered), "rb") as wav:
                pcm = struct.unpack("<{}h".format(wav.getnframes() * wav.getnchannels()),
                                    wav.readframes(wav.getnframes()))
            render_rms = math.sqrt(sum(value * value for value in pcm) / len(pcm))
            self.assertGreater(render_rms, 50)
            self.assertLess(render_rms, 800)
            loaded = self.client.request("session.load", {
                "path": str(path), "expected_revision": revision,
            })
            self.assertEqual(control_values(loaded, "gain"), [0.02])
            reloaded = self.client.request("session.inspect")
            self.assertEqual(control_values(reloaded["session"], "gain"), [0.02])
            self.assertGreater(int(reloaded["revision"]), int(revision))

    def test_array_control_is_applied_acknowledged_and_round_trips(self):
        self.replace(array_gain_session())
        self.start_live(seconds=4.0)
        self.wait_submitted(67_200)
        old_revision = self.client.request("session.inspect")["revision"]
        values = [0.02, 0.03]
        queued = self.client.request("source.set_control", {
            "expected_revision": old_revision, "track_id": "control-live",
            "control_name": "gain", "values": values,
        })
        self.assertTrue(queued["queued"])
        revision = queued["revision"]
        self.assertEqual(control_values(queued["session"], "gain"), values)
        self.assertEqual(control_values(self.client.request("session.inspect")["session"],
                                        "gain"), values)
        _, update = self.wait_control_ack(revision)
        self.assertEqual(update["applied_revision"], revision)
        self.assertGreater(update["applied_frame"], 0)
        self.assertTrue(update["callback_observed"])
        self.assertFalse(update["pending"])

        with tempfile.TemporaryDirectory(prefix="daw-sc-array-control-save-",
                                         dir=ROOT / "output") as temporary:
            path = Path(temporary) / "array-edited-session.json"
            self.client.request("session.save", {"path": str(path)})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(control_values(saved, "gain"), values)
            loaded = self.client.request("session.load", {
                "path": str(path), "expected_revision": revision,
            })
            self.assertEqual(control_values(loaded, "gain"), values)
            self.assertEqual(control_values(self.client.request("session.inspect")["session"],
                                            "gain"), values)

    def test_bad_shape_ids_and_stale_revision_reject_without_changing_base(self):
        self.start_live()
        self.wait_submitted(256)
        revision = self.client.request("session.inspect")["revision"]
        cases = [
            ({"expected_revision": revision, "track_id": "missing", "control_name": "gain", "values": [0.02]}, "invalid_params"),
            ({"expected_revision": revision, "track_id": "control-live", "control_name": "unknown", "values": [0.02]}, "invalid_params"),
            ({"expected_revision": revision, "track_id": "control-live", "control_name": "gain", "values": [0.02, 0.03]}, "invalid_params"),
            ({"expected_revision": revision, "track_id": "control-live", "control_name": "gain", "values": [1e300]}, "invalid_params"),
            ({"expected_revision": "0", "track_id": "control-live", "control_name": "gain", "values": [0.02]}, "revision_conflict"),
        ]
        for params, code in cases:
            error = self.client.request_error("source.set_control", params)
            self.assertEqual(error.code, code)
            self.assert_unchanged_revision(revision)
            self.assertEqual(self.client.request("transport.status")["state"], "playing")
        self.assertTrue(self.client.request("transport.stop")["resources_released"])

    def test_any_saved_automation_point_blocks_native_base_edit(self):
        self.replace(session_fixture(gain_points=[{"frame": 0, "values": [0.1]}]))
        self.start_live()
        self.wait_submitted(256)
        revision = self.client.request("session.inspect")["revision"]
        error = self.client.request_error("source.set_control", {
            "expected_revision": revision, "track_id": "control-live",
            "control_name": "gain", "values": [0.02],
        })
        self.assertIn("automation", error.message.lower())
        self.assert_unchanged_revision(revision)
        self.assertEqual(self.client.request("transport.status")["state"], "playing")
        self.client.request("transport.stop")

    def test_initialization_rate_control_cannot_be_changed_live(self):
        self.replace(session_fixture(init_rate=True))
        self.start_live()
        self.wait_submitted(256)
        revision = self.client.request("session.inspect")["revision"]
        error = self.client.request_error("source.set_control", {
            "expected_revision": revision, "track_id": "control-live",
            "control_name": "freq", "values": [660.0],
        })
        self.assertIn("initialization", error.message.lower())
        self.assert_unchanged_revision(revision)
        self.assertEqual(self.client.request("transport.status")["state"], "playing")
        self.client.request("transport.stop")

    def test_edit_requires_an_active_live_source(self):
        revision = self.client.request("session.inspect")["revision"]
        params = {"expected_revision": revision, "track_id": "control-live",
                  "control_name": "gain", "values": [0.02]}
        stopped_error = self.client.request_error("source.set_control", params)
        self.assertEqual(stopped_error.code, "audio_error")
        self.client.request("transport.play", {"seconds": 3.0, "volume": 0.0})
        status = self.wait_status(lambda item: item.get("state") == "playing")
        self.assertEqual(status["source_mode"], "prepared")
        prepared_error = self.client.request_error("source.set_control", params)
        self.assertEqual(prepared_error.code, "audio_error")
        self.assertEqual(self.client.request("transport.status")["state"], "playing")
        self.client.request("transport.stop")


if __name__ == "__main__":
    unittest.main()

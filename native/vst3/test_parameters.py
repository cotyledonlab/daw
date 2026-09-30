"""Protocol integration checks for live edits to saved VST3 parameters.

The command-shape check is portable. Tests that open a native audio device are
opt-in with DAW_TEST_NATIVE_AUDIO=1 and require a vst3-live engine plus fixture.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from gui.server import Engine, EngineError
from native.vst3.scan import ROOT


FIXTURE = ROOT / "output/vst3-spike/DawTestGain.vst3"
CID = "DA01234567894ABCBDEF0123456789AB"


def session(*, automated=False, bypass=False, kind="vst3"):
    if kind == "vst3":
        effect = {"kind": kind, "id": "fx", "bypass": bypass,
                  "bundle_path": str(FIXTURE), "class_id": CID, "state_hex": "",
                  "controller_state_hex": "",
                  "parameters": [{"id": 0, "value": 0.2,
                                  "points": ([{"frame": 32, "value": 0.8}]
                                             if automated else [])}]}
    else:
        effect = {"kind": "gain", "id": "fx", "gain": 0.5, "bypass": bypass}
    return {"schema_version": 4, "sample_rate": 48000,
            "tempo_milli_bpm": 120000,
            "tracks": [{"id": "lead", "device": {"kind": "sine",
                         "frequency_hz": 440, "gain": 0.1},
                        "mode": "continuous", "clips": [], "effects": [effect]}]}


class LiveParameterProtocolTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DAW_VST3_FIXTURE_NO_EVENTS": "1",
                                           "DAW_VST3_FIXTURE_REALTIME": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = Engine(ROOT / "target/debug/daw")
        self.addCleanup(self.engine.close)
        self.addCleanup(self.stop_transport)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "output")
        self.addCleanup(self.temp.cleanup)

    def require_live_hardware(self):
        if os.environ.get("DAW_TEST_NATIVE_AUDIO") != "1":
            self.skipTest("set DAW_TEST_NATIVE_AUDIO=1 for opt-in native playback tests")
        caps = self.engine.call("capabilities")
        if caps.get("live_parameter_edits", {}).get("implemented") is not True:
            self.skipTest("engine was not built with vst3-live")
        if not FIXTURE.is_dir():
            self.skipTest("build native/vst3 fixture first")

    def replace(self, value=None):
        return self.engine.call("session.replace", {"session": value or session()})

    def snapshot(self):
        return self.engine.call("session.inspect")

    def stop_transport(self):
        try:
            self.engine.call("transport.stop")
        except EngineError:
            pass

    def edit(self, revision, *, track="lead", effect="fx", parameter=0, value=0.7):
        return self.engine.call("effect.set_parameter", {
            "expected_revision": str(revision), "track_id": track,
            "effect_id": effect, "parameter_id": parameter, "value": value})

    def wait_applied(self, revision, timeout=2.0):
        end = time.monotonic() + timeout
        latest = None
        while time.monotonic() < end:
            latest = self.engine.call("transport.status")
            update = latest.get("plugin_parameter_update", {})
            if update.get("applied_revision") == str(revision):
                return latest
            time.sleep(0.01)
        self.fail(f"parameter revision {revision} was not acknowledged: {latest}")

    def wait_state(self, state, timeout=2.0):
        end = time.monotonic() + timeout
        latest = None
        while time.monotonic() < end:
            latest = self.engine.call("transport.status")
            if latest.get("state") == state:
                return latest
            time.sleep(0.01)
        self.fail(f"transport did not reach {state}: {latest}")

    def start(self):
        return self.engine.call("transport.play", {"seconds": 3, "volume": 0})

    def test_invalid_command_shapes_are_rejected_without_session_change(self):
        # Decode-level failures must remain checkable in portable builds,
        # without requiring an installed plugin or the native fixture.
        before = self.snapshot()
        valid = {"expected_revision": before["revision"], "track_id": "lead", "effect_id": "fx",
                 "parameter_id": 0, "value": 0.7}
        malformed = (
            {},
            {**valid, "expected_revision": 1},
            {**valid, "expected_revision": "01"},
            {**valid, "track_id": "lead", "extra": True},
            {**valid, "parameter_id": -1},
            {**valid, "parameter_id": 2**32},
            {**valid, "value": -0.01},
            {**valid, "value": 1.01},
        )
        for params in malformed:
            with self.subTest(params=params):
                with self.assertRaises(EngineError):
                    self.engine.call("effect.set_parameter", params)
                self.assertEqual(self.snapshot(), before)

        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.engine.call("effect.set_parameter", {**valid, "value": value})
            self.assertEqual(self.snapshot(), before)

    def test_edit_requires_active_live_transport_and_valid_target(self):
        self.require_live_hardware()
        scenarios = (
            (session(), {"track": "missing"}),
            (session(), {"effect": "missing"}),
            (session(), {"parameter": 9}),
            (session(automated=True), {}),
            (session(bypass=True), {}),
            (session(kind="gain"), {}),
        )
        for source, target in scenarios:
            with self.subTest(source=source["tracks"][0]["effects"][0], target=target):
                self.replace(copy.deepcopy(source))
                before = self.snapshot()
                with self.assertRaises(EngineError) as raised:
                    self.edit(before["revision"], **target)
                self.assertNotIn("revision", str(raised.exception).lower())
                self.assertEqual(self.snapshot(), before)

        self.replace()
        before = self.snapshot()
        with self.assertRaises(EngineError) as stopped:
            self.edit(before["revision"])
        self.assertIn("stopped",str(stopped.exception).lower())
        self.assertEqual(self.snapshot(),before)
        with self.assertRaises(EngineError) as raised:
            self.edit(int(before["revision"]) + 1)
        self.assertIn("revision", str(raised.exception).lower())
        self.assertEqual(self.snapshot(), before)

    def test_success_acknowledges_at_future_block_and_save_reload_preserves_state(self):
        self.require_live_hardware()
        self.replace()
        inspected = self.snapshot()
        original = inspected["session"]
        revision = inspected["revision"]
        opaque = (original["tracks"][0]["effects"][0]["state_hex"],
                  original["tracks"][0]["effects"][0]["controller_state_hex"])
        self.start()
        started = self.wait_state("playing")
        initial_frame = started.get("timeline_frame", 0)
        response = self.edit(int(revision), value=0.7)
        expected_revision = str(int(revision) + 1)
        self.assertEqual(response["revision"], expected_revision)
        self.assertIs(response["queued"], True)
        self.assertEqual(response["session"], self.snapshot()["session"])
        self.assertEqual(response["session"]["tracks"][0]["effects"][0]["parameters"][0]["value"], 0.7)
        status = self.wait_applied(int(expected_revision))
        update = status["plugin_parameter_update"]
        self.assertEqual(update["pending"], 0)
        self.assertEqual(update["applied_revision"], expected_revision)
        self.assertIsInstance(update["applied_frame"], int)
        self.assertGreater(update["applied_frame"], initial_frame)
        self.assertEqual(status["state"], "playing")

        path = Path(self.temp.name) / "parameters.json"
        self.engine.call("session.save", {"path": str(path)})
        serialized = json.loads(path.read_text())
        self.assertEqual(serialized["tracks"][0]["effects"][0]["parameters"][0]["value"], 0.7)
        self.engine.call("transport.stop")
        loaded = self.engine.call("session.load", {"path": str(path)})
        effect = loaded["tracks"][0]["effects"][0]
        self.assertEqual(effect["parameters"][0]["value"], 0.7)
        self.assertEqual((effect["state_hex"], effect["controller_state_hex"]), opaque)

    def test_paused_update_is_accepted_but_ack_waits_for_resume(self):
        self.require_live_hardware()
        self.replace()
        revision = int(self.snapshot()["revision"])
        self.start()
        self.wait_state("playing")
        self.engine.call("transport.pause")
        self.wait_state("paused")
        time.sleep(0.05)
        response = self.edit(revision, value=0.6)
        expected_revision = str(revision + 1)
        self.assertEqual(response["revision"], expected_revision)
        update = self.engine.call("transport.status")["plugin_parameter_update"]
        self.assertEqual(update["pending"], 1)
        self.assertIsNone(update["applied_revision"])
        self.engine.call("transport.resume")
        status = self.wait_applied(int(expected_revision))
        self.assertEqual(status["plugin_parameter_update"]["applied_revision"], expected_revision)

    def test_queue_full_rejects_without_advancing_session_revision(self):
        self.require_live_hardware()
        self.replace()
        revision = int(self.snapshot()["revision"])
        self.start()
        self.wait_state("playing")
        self.engine.call("transport.pause")
        self.wait_state("paused")
        time.sleep(0.05)
        accepted = 0
        for index in range(9):
            before = self.snapshot()
            try:
                result = self.edit(revision + accepted, value=0.1 + index / 10)
            except EngineError as exc:
                self.assertNotIn("revision", str(exc).lower())
                self.assertEqual(self.snapshot(), before)
                break
            accepted += 1
            self.assertEqual(result["revision"], str(revision + accepted))
        else:
            self.fail("the eight-entry parameter queue accepted a ninth edit")
        self.assertEqual(accepted, 8)


if __name__ == "__main__":
    unittest.main()

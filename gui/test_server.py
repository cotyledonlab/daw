"""Integration tests for the loopback HTTP bridge."""
import copy
import http.client
import io
import json
import subprocess
import threading
import unittest
import wave
from unittest.mock import patch

from gui.server import EngineError, MAX_FRAME, ROOT, Server


BINARY = ROOT / "target" / "debug" / "daw"
SESSION = {
    "schema_version": 1,
    "sample_rate": 22050,
    "tracks": [
        {"id": "tone", "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.2}}
    ],
}
V4_GAIN_SESSION = {
    "schema_version": 4,
    "sample_rate": 48000,
    "tempo_milli_bpm": 120000,
    "tracks": [{
        "id": "tone",
        "mode": "continuous",
        "clips": [],
        "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.2},
        "effects": [{"kind": "gain", "id": "trim", "gain": 0.5, "bypass": False}],
    }],
}


def schema_v6_session():
    from examples.supercollider_tracks_demo import make_track

    return {"schema_version": 6, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [make_track("low", 220.0, 0.12, 0.5, 1.5, 0.6)]}


def schema_v7_session():
    from examples.csound_tracks_demo import track as make_csound_track

    return {"schema_version": 7, "sample_rate": 48000, "tempo_milli_bpm": 120000,
            "tracks": [
                make_csound_track("csound", 440.0, 0.2),
                {"id": "tone", "mode": "continuous", "clips": [],
                 "device": {"kind": "sine", "frequency_hz": 220.0, "gain": 0.1},
                 "effects": [{"kind": "gain", "id": "trim", "gain": 0.5, "bypass": False}]},
            ]}


class ServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.is_file():
            subprocess.run(["cargo", "build", "--offline", "--locked"], cwd=ROOT, check=True)

    def setUp(self):
        self.server = Server(BINARY, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f"127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, method, path, body=None, *, token=True, host=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": host or self.host}
        if token:
            headers["X-DAW-Token"] = self.server.token
        if origin is not None:
            headers["Origin"] = origin
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read(), response.getheaders()
        connection.close()
        return result

    def post(self, path, body):
        return self.request("POST", path, body)

    def get_session(self):
        status, body, _ = self.request("GET", "/api/session")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_api_session_requires_token(self):
        status, body, _ = self.request("GET", "/api/session", token=False)
        self.assertEqual(status, 403)
        self.assertIn(b"Reload this window", body)
        self.assertEqual(self.get_session()["sample_rate"], 48000)

    def test_session_inspect_is_authenticated_and_forwards_protocol_method(self):
        status, _, _ = self.request("GET", "/api/session/inspect", token=False)
        self.assertEqual(status, 403)
        with patch.object(self.server.engine, "call", return_value={"revision": "7", "session": SESSION}) as call:
            status, body, _ = self.request("GET", "/api/session/inspect")
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("session.inspect",))
        self.assertEqual(json.loads(body)["revision"], "7")

    def test_live_parameter_route_is_authenticated_and_forwards_exact_payload(self):
        request = {"expected_revision": "0", "track_id": "tone", "effect_id": "verb",
                   "parameter_id": 48, "value": 0.25}
        status, _, _ = self.request("POST", "/api/effect/parameter", request, token=False)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/effect/parameter", request, origin="http://attacker.example")
        self.assertEqual(status, 403)
        accepted = {"revision": "1", "session": V4_GAIN_SESSION, "queued": True}
        with patch.object(self.server.engine, "call", return_value=accepted) as call:
            status, body, _ = self.post("/api/effect/parameter", request)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("effect.set_parameter", request))
        self.assertEqual(json.loads(body), accepted)

    def test_live_parameter_route_rejects_extra_fields_and_invalid_values_before_engine(self):
        good = {"expected_revision": "0", "track_id": "tone", "effect_id": "verb",
                "parameter_id": 48, "value": 0.25}
        invalid = (
            {**good, "path": "/tmp/plugin.vst3"},
            {**good, "expected_revision": "00"},
            {**good, "expected_revision": 0},
            {**good, "track_id": ""},
            {**good, "parameter_id": True},
            {**good, "parameter_id": 2**32},
            {**good, "value": True},
            {**good, "value": float("nan")},
            {**good, "value": 1.01},
        )
        with patch.object(self.server.engine, "call") as call:
            for payload in invalid:
                with self.subTest(payload=payload):
                    status, body, _ = self.post("/api/effect/parameter", payload)
                    self.assertEqual(status, 422, body)
            call.assert_not_called()

    def test_live_parameter_engine_rejection_returns_structured_error_without_replacing_session(self):
        status, body, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200, body)
        before = self.get_session()
        request = {"expected_revision": "0", "track_id": "tone", "effect_id": "verb",
                   "parameter_id": 48, "value": 0.25}
        with patch.object(self.server.engine, "call", side_effect=EngineError("Revision conflict.")):
            status, body, _ = self.post("/api/effect/parameter", request)
        self.assertEqual(status, 422, body)
        self.assertEqual(json.loads(body)["error"], "Revision conflict.")
        self.assertEqual(self.get_session(), before)

    def test_capabilities_and_transport_routes_are_authenticated_and_report_stopped(self):
        status, _, _ = self.request("GET", "/api/capabilities", token=False)
        self.assertEqual(status, 403)
        status, body, _ = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200, body)
        capabilities = json.loads(body)
        self.assertIn("transport.status", capabilities["methods"])
        self.assertEqual(capabilities["gui_bridge"], {"checked_replacement": True, "supercollider_sources": True, "csound_sources": True})
        self.assertIsInstance(capabilities["parameter_metadata"]["implemented"], bool)

        status, _, _ = self.request("GET", "/api/transport", token=False)
        self.assertEqual(status, 403)
        status, body, _ = self.request("GET", "/api/transport")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["state"], "stopped")

        status, body, _ = self.post("/api/transport", {"action": "stop"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["state"], "stopped")

    def test_transport_rejects_unknown_actions(self):
        status, body, _ = self.post("/api/transport", {"action": "render", "path": "/tmp/owned.wav"})
        self.assertEqual(status, 422, body)
        self.assertIn(b"Unknown transport action", body)

    def test_effect_inspect_route_is_authenticated_and_forwards_only_ids(self):
        status, _, _ = self.request("POST", "/api/effect/inspect", {"track_id": "tone", "effect_id": "verb"}, token=False)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/effect/inspect", {"track_id": "tone", "effect_id": "verb"}, origin="http://attacker.example")
        self.assertEqual(status, 403)
        expected = {"track_id": "tone", "effect_id": "verb"}
        with patch.object(self.server.engine, "call", return_value={"track_id": "tone", "effect_id": "verb", "revision": "0", "parameters": []}) as call:
            status, body, _ = self.post("/api/effect/inspect", expected)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("effect.inspect", expected))
        self.assertEqual(json.loads(body)["parameters"], [])

    def test_effect_inspect_rejects_extra_fields_and_invalid_ids_without_engine_mutation(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        before = self.get_session()
        invalid = (
            {"track_id": "tone", "effect_id": "fx", "path": "/tmp/plugin.vst3"},
            {"track_id": "", "effect_id": "fx"},
            {"track_id": "tone", "effect_id": "x" * 129},
            {"track_id": "é" * 65, "effect_id": "fx"},
            {"track_id": 12, "effect_id": "fx"},
        )
        with patch.object(self.server.engine, "call") as call:
            for payload in invalid:
                with self.subTest(payload=payload):
                    status, body, _ = self.post("/api/effect/inspect", payload)
                    self.assertEqual(status, 422, body)
            call.assert_not_called()
        self.assertEqual(self.get_session(), before)

    def test_effect_inspect_non_plugin_identity_returns_error_and_preserves_session(self):
        status, body, _ = self.post("/api/session", {"session": V4_GAIN_SESSION})
        self.assertEqual(status, 200, body)
        before = self.get_session()
        status, body, _ = self.post("/api/effect/inspect", {"track_id": "tone", "effect_id": "trim"})
        self.assertEqual(status, 422, body)
        self.assertIsInstance(json.loads(body)["error"], str)
        self.assertEqual(self.get_session(), before)

    def test_invalid_host_and_origin_are_rejected(self):
        status, _, _ = self.request("GET", "/api/session", host="attacker.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/session", origin="http://attacker.example")
        self.assertEqual(status, 403)

    def test_replace_and_render_returns_stereo_riff(self):
        status, body, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), SESSION)

        status, body, headers = self.post("/api/render", {"seconds": 0.05})
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(dict(headers)["Content-Type"], "audio/wav")
        with wave.open(io.BytesIO(body), "rb") as rendered:
            self.assertEqual(rendered.getnchannels(), 2)
            self.assertEqual(rendered.getframerate(), SESSION["sample_rate"])
            self.assertEqual(rendered.getnframes(), 1103)
            self.assertEqual(rendered.getsampwidth(), 2)
        self.assertEqual(body[:4], b"RIFF")

    def test_invalid_replace_preserves_current_session(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        invalid = {**SESSION, "tracks": [{**SESSION["tracks"][0], "id": ""}]}
        status, body, _ = self.post("/api/session", {"session": invalid})
        self.assertEqual(status, 422, body)
        self.assertEqual(self.get_session(), SESSION)

    def test_unsupported_session_formats_are_rejected_before_replacing_engine_session(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        for version in (5, 8):
            with self.subTest(version=version):
                timeline_session = {"schema_version": version, "sample_rate": 48000,
                                    "tempo_milli_bpm": 120000, "tracks": []}
                status, body, _ = self.post("/api/session", {"session": timeline_session})
                self.assertEqual(status, 422, body)
                self.assertIn(b"scripting interface", body)
                self.assertEqual(self.get_session(), SESSION)

    def test_sine_note_and_continuous_sessions_replace_through_rust(self):
        source = json.loads((ROOT / "examples/sessions/arpeggio.json").read_text())
        for version in (2, 3, 4):
            with self.subTest(version=version):
                candidate = copy.deepcopy(source)
                candidate["schema_version"] = version
                candidate["tracks"].append({"id": "drone", "mode": "continuous", "clips": [],
                                            "device": {"kind": "sine", "frequency_hz": 220, "gain": 0.01}})
                if version >= 3:
                    for track in candidate["tracks"]:
                        track["effects"] = [{"kind": "gain", "id": "trim", "gain": 0.5, "bypass": False}]
                status, body, _ = self.post("/api/session", {"session": candidate})
                self.assertEqual(status, 200, body)
                self.assertEqual(self.get_session(), candidate)

    def test_invalid_note_and_stale_timeline_replacement_preserve_revision(self):
        candidate = json.loads((ROOT / "examples/sessions/arpeggio.json").read_text())
        status, body, _ = self.post("/api/session", {"session": candidate, "expected_revision": "0"})
        self.assertEqual(status, 200, body)
        status, body, _ = self.request("GET", "/api/session/inspect")
        self.assertEqual(status, 200, body)
        before = json.loads(body)
        invalid = copy.deepcopy(candidate)
        invalid["tracks"][0]["clips"][0]["notes"][0]["duration_frames"] = 0
        for payload in ({"session": invalid, "expected_revision": before["revision"]},
                        {"session": candidate, "expected_revision": "0"}):
            status, body, _ = self.post("/api/session", payload)
            self.assertEqual(status, 422, body)
            status, body, _ = self.request("GET", "/api/session/inspect")
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body), before)

    def test_continuous_v4_plugin_shape_still_reaches_engine(self):
        candidate = copy.deepcopy(V4_GAIN_SESSION)
        candidate["tracks"][0]["effects"] = [{"kind": "vst3", "id": "foreign"}]
        with patch.object(self.server.engine, "call", return_value=candidate) as call:
            status, body, _ = self.post("/api/session", {"session": candidate})
        self.assertEqual(status, 200, body)
        call.assert_called_once_with("session.replace", {"session": candidate})

    def test_timeline_preflight_rejects_audio_runtime_and_non_gain_shapes(self):
        source = json.loads((ROOT / "examples/sessions/arpeggio.json").read_text())
        before = self.get_session()
        for version in (2, 3, 4):
            base = copy.deepcopy(source)
            base["schema_version"] = version
            if version >= 3:
                base["tracks"][0]["effects"] = []
            candidates = []
            for kind in ("audio", "supercollider", "csound", "puredata"):
                candidate = copy.deepcopy(base)
                candidate["tracks"][0]["device"]["kind"] = kind
                candidates.append(candidate)
            candidate = copy.deepcopy(base)
            candidate["tracks"][0]["clips"][0]["kind"] = "audio"
            candidates.append(candidate)
            if version >= 3:
                candidate = copy.deepcopy(base)
                candidate["tracks"].append({**copy.deepcopy(V4_GAIN_SESSION["tracks"][0]),
                                            "effects": [{"kind": "vst3", "id": "foreign"}]})
                candidates.append(candidate)
            with patch.object(self.server.engine, "call") as call:
                for candidate in candidates:
                    with self.subTest(version=version, candidate=candidate):
                        status, body, _ = self.post("/api/session", {"session": candidate})
                        self.assertEqual(status, 422, body)
                call.assert_not_called()
        self.assertEqual(self.get_session(), before)

    def test_timeline_transport_routes_authenticate_and_forward_exact_payload(self):
        cases = (("seek", {"frame": MAX_FRAME}), ("seek", {"frame": 0}),
                 ("loop", {"region": None}),
                 ("loop", {"region": {"start_frame": 0, "end_frame": MAX_FRAME}}))
        snapshot = {"state": "paused", "timeline_frame": 0, "timeline_command_pending": True}
        for action, payload in cases:
            path = "/api/transport/" + action
            with self.subTest(action=action, payload=payload):
                for headers in ({"token": False}, {"origin": "http://attacker.example"}, {"host": "attacker.example"}):
                    with patch.object(self.server.engine, "call") as call:
                        status, _, _ = self.request("POST", path, payload, **headers)
                        self.assertEqual(status, 403)
                        call.assert_not_called()
                with patch.object(self.server.engine, "call", return_value=snapshot) as call:
                    status, body, _ = self.post(path, payload)
                self.assertEqual(status, 200, body)
                call.assert_called_once_with("transport." + action, payload)
                self.assertEqual(json.loads(body), snapshot)

    def test_invalid_timeline_transport_payloads_never_reach_engine(self):
        invalid_seek = ({}, {"frame": True}, {"frame": 0.5}, {"frame": "0"},
                        {"frame": -1}, {"frame": MAX_FRAME + 1}, {"frame": 0, "extra": 1})
        invalid_loop = ({}, {"region": False}, {"region": []}, {"region": {}, "extra": 1},
                        {"region": {"start_frame": 0, "end_frame": 0}},
                        {"region": {"start_frame": 1, "end_frame": 0}},
                        {"region": {"start_frame": -1, "end_frame": 1}},
                        {"region": {"start_frame": True, "end_frame": 1}},
                        {"region": {"start_frame": 0, "end_frame": 1.5}},
                        {"region": {"start_frame": 0, "end_frame": MAX_FRAME + 1}},
                        {"region": {"start_frame": 0, "end_frame": 1, "extra": 1}})
        before = self.get_session()
        with patch.object(self.server.engine, "call") as call:
            for action, payloads in (("seek", invalid_seek), ("loop", invalid_loop)):
                for payload in payloads:
                    with self.subTest(action=action, payload=payload):
                        status, body, _ = self.post("/api/transport/" + action, payload)
                        self.assertEqual(status, 422, body)
            call.assert_not_called()
        self.assertEqual(self.get_session(), before)

    def test_timeline_transport_engine_errors_preserve_session_and_revision(self):
        status, body, _ = self.request("GET", "/api/session/inspect")
        self.assertEqual(status, 200, body)
        before = json.loads(body)
        for action, payload in (("seek", {"frame": 0}), ("loop", {"region": None})):
            with patch.object(self.server.engine, "call", side_effect=EngineError("Timeline request unavailable.")):
                status, body, _ = self.post("/api/transport/" + action, payload)
            self.assertEqual(status, 422, body)
            self.assertEqual(json.loads(body), {"error": "Timeline request unavailable."})
        status, body, _ = self.request("GET", "/api/session/inspect")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), before)

    def test_source_inspect_is_authenticated_bounded_and_forwards_only_synthdef(self):
        from examples.supercollider_tracks_demo import make_track

        synthdef_hex = make_track("low", 220.0, 0.12, 0.5, 1.5, 0.6)["device"]["synthdef_hex"]
        payload = {"synthdef_hex": synthdef_hex}
        status, _, _ = self.request("POST", "/api/source/inspect", payload, token=False)
        self.assertEqual(status, 403)
        inspected = {"synth_name": "daw_sine", "controls": []}
        with patch.object(self.server.engine, "call", return_value=inspected) as call:
            status, body, _ = self.post("/api/source/inspect", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("supercollider.inspect", {"synthdef_hex": synthdef_hex}))
        self.assertEqual(json.loads(body), inspected)

    def test_source_inspect_rejects_malformed_or_oversized_hex_without_engine_call(self):
        invalid = (
            {}, {"synthdef_hex": ""}, {"synthdef_hex": "0"},
            {"synthdef_hex": "gg"}, {"synthdef_hex": "00", "path": "/tmp/a"},
            {"synthdef_hex": "0" * 131074},
        )
        with patch.object(self.server.engine, "call") as call:
            for payload in invalid:
                with self.subTest(length=len(payload.get("synthdef_hex", ""))):
                    status, body, _ = self.post("/api/source/inspect", payload)
                    self.assertEqual(status, 422, body)
            call.assert_not_called()

    def test_source_control_is_authenticated_and_forwards_exact_payload(self):
        payload = {"expected_revision": "12", "track_id": "low", "control_name": "freq",
                   "values": [220.0]}
        status, _, _ = self.request("POST", "/api/source/control", payload, token=False)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/source/control", payload,
                                    origin="http://attacker.example")
        self.assertEqual(status, 403)
        accepted = {"revision": "13", "session": schema_v6_session()}
        with patch.object(self.server.engine, "call", return_value=accepted) as call:
            status, body, _ = self.post("/api/source/control", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("source.set_control", payload))
        self.assertEqual(json.loads(body), accepted)

    def test_source_control_rejects_invalid_fields_and_array_f32_values_before_engine(self):
        good = {"expected_revision": "12", "track_id": "low", "control_name": "freq",
                "values": [220.0]}
        invalid = (
            {**good, "expected_revision": "00"}, {**good, "expected_revision": 12},
            {**good, "expected_revision": str(2**64)}, {**good, "extra": True},
            {**good, "track_id": ""}, {**good, "track_id": "é" * 65},
            {**good, "control_name": ""}, {**good, "control_name": "é" * 128},
            {**good, "control_name": "x" * 256}, {**good, "values": []},
            {**good, "values": [True]}, {**good, "values": [float("nan")]},
            {**good, "values": [float("inf")]}, {**good, "values": [1e100, 0.5]},
            {**good, "values": [0.0] * 257},
        )
        with patch.object(self.server.engine, "call") as call:
            for payload in invalid:
                with self.subTest(payload=str(payload)[:90]):
                    status, body, _ = self.post("/api/source/control", payload)
                    self.assertEqual(status, 422, body)
            call.assert_not_called()

    def test_source_control_forwards_finite_csound_float64_scalar_to_rust(self):
        payload = {"expected_revision": "12", "track_id": "csound", "control_name": "amplitude",
                   "values": [1e100]}
        accepted = {"revision": "13", "queued": True}
        with patch.object(self.server.engine, "call", return_value=accepted) as call:
            status, body, _ = self.post("/api/source/control", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("source.set_control", payload))
        self.assertEqual(json.loads(body), accepted)

    def test_source_control_rust_rejection_does_not_change_session(self):
        self.assertEqual(self.post("/api/session", {"session": SESSION})[0], 200)
        before = self.get_session()
        payload = {"expected_revision": "0", "track_id": "csound", "control_name": "amplitude",
                   "values": [1e100]}
        with patch.object(self.server.engine, "call", side_effect=EngineError("Csound control value rejected.")) as call:
            status, body, _ = self.post("/api/source/control", payload)
        self.assertEqual(status, 422, body)
        self.assertEqual(call.call_args.args, ("source.set_control", payload))
        self.assertEqual(json.loads(body)["error"], "Csound control value rejected.")
        self.assertEqual(self.get_session(), before)

    def test_source_control_engine_rejection_preserves_session(self):
        self.assertEqual(self.post("/api/session", {"session": SESSION})[0], 200)
        before = self.get_session()
        payload = {"expected_revision": "0", "track_id": "missing", "control_name": "freq",
                   "values": [220.0]}
        with patch.object(self.server.engine, "call", side_effect=EngineError("Unknown source control.")):
            status, body, _ = self.post("/api/source/control", payload)
        self.assertEqual(status, 422, body)
        self.assertEqual(json.loads(body)["error"], "Unknown source control.")
        self.assertEqual(self.get_session(), before)

    def test_v6_session_preflight_and_revision_forwarding(self):
        session = schema_v6_session()
        payload = {"session": session, "expected_revision": "0"}
        with patch.object(self.server.engine, "call", return_value=session) as call:
            status, body, _ = self.post("/api/session", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("session.replace", payload))
        self.assertEqual(json.loads(body), session)

    def test_v6_session_preflight_rejects_unsupported_shapes_before_engine(self):
        self.assertEqual(self.post("/api/session", {"session": SESSION})[0], 200)
        unsupported = []
        valid = schema_v6_session()
        for track in (
            {**valid["tracks"][0], "clips": [{"start_frame": 0}]},
            {**valid["tracks"][0], "device": {"kind": "audio", "gain": 1}},
            {**valid["tracks"][0], "device": {"kind": "midi", "gain": 1}},
            {**valid["tracks"][0], "effects": [{"kind": "vst3", "id": "x"}]},
            {**valid["tracks"][0], "effects": [{"kind": "au", "id": "x"}]},
        ):
            unsupported.append({**valid, "tracks": [track]})
        unsupported.extend((
            {**valid, "schema_version": 2},
            {**valid, "tracks": [{**valid["tracks"][0],
                                   "device": {"kind": "csound", "program": "opaque"}}]},
            {**valid, "expected_revision": "00"},
        ))
        with patch.object(self.server.engine, "call") as call:
            for candidate in unsupported:
                body = {"session": candidate}
                if "expected_revision" in candidate:
                    body["expected_revision"] = candidate["expected_revision"]
                    body["session"] = {k: v for k, v in candidate.items() if k != "expected_revision"}
                status, response, _ = self.post("/api/session", body)
                self.assertEqual(status, 422, response)
            call.assert_not_called()
        self.assertEqual(self.get_session(), SESSION)

    def test_v7_session_preflight_forwards_csound_program_unchanged(self):
        session = schema_v7_session()
        payload = {"session": session, "expected_revision": "0"}
        program = session["tracks"][0]["device"]["program"]
        with patch.object(self.server.engine, "call", return_value=session) as call:
            status, body, _ = self.post("/api/session", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(call.call_args.args, ("session.replace", payload))
        accepted = json.loads(body)
        self.assertEqual(accepted["tracks"][0]["device"]["program"], program)
        self.assertEqual(accepted, session)

    def test_v7_preflight_rejects_unsupported_shapes_before_engine_and_preserves_session(self):
        self.assertEqual(self.post("/api/session", {"session": SESSION})[0], 200)
        valid = schema_v7_session()
        csound = valid["tracks"][0]
        unsupported = (
            {**valid, "tracks": [{**csound, "clips": [{"start_frame": 0}]}]},
            {**valid, "tracks": [{**csound, "mode": "sequenced"}]},
            {**valid, "tracks": [{**csound, "effects": [{"kind": "vst3", "id": "x"}]}]},
            {**valid, "tracks": [{**csound, "effects": [{"kind": "au", "id": "x"}]}]},
            {**valid, "tracks": [{**csound, "device": {"kind": "unknown"}}]},
            {**valid, "tracks": ["malformed"]},
            {**valid, "tracks": [{k: v for k, v in csound.items() if k != "effects"}]},
        )
        with patch.object(self.server.engine, "call") as call:
            for candidate in unsupported:
                with self.subTest(candidate=candidate):
                    status, body, _ = self.post("/api/session", {"session": candidate})
                    self.assertEqual(status, 422, body)
            call.assert_not_called()
        self.assertEqual(self.get_session(), SESSION)

    def test_schema_v4_continuous_sine_gain_session_replaces_through_rust(self):
        status, body, _ = self.post("/api/session", {"session": V4_GAIN_SESSION})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), V4_GAIN_SESSION)
        self.assertEqual(self.get_session(), V4_GAIN_SESSION)

    def test_unsupported_schema_v4_tracks_preserve_current_session(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        unsupported = (
            {"id": "audio", "mode": "sequenced", "clips": [],
             "device": {"kind": "audio", "gain": 1}, "effects": []},
            {"id": "notes", "mode": "sequenced", "clips": [],
             "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.2}, "effects": [{"kind": "vst3"}]},
            {"id": "malformed", "mode": [], "clips": {},
             "device": [], "effects": []},
        )
        for track in unsupported:
            with self.subTest(track=track):
                candidate = {**V4_GAIN_SESSION, "tracks": [track]}
                status, body, _ = self.post("/api/session", {"session": candidate})
                self.assertEqual(status, 422, body)
                self.assertIsInstance(json.loads(body)["error"], str)
                self.assertEqual(self.get_session(), SESSION)

    def test_malformed_schema_v4_and_legacy_v1_errors_are_safe_and_preserve_session(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        malformed_v4 = {"schema_version": 4, "sample_rate": 48000, "tracks": {}}
        status, body, _ = self.post("/api/session", {"session": malformed_v4})
        self.assertEqual(status, 422, body)
        self.assertIsInstance(json.loads(body)["error"], str)

        invalid_v1 = {**SESSION, "tracks": [{"id": "tone", "device": {"kind": "sine"}}]}
        status, body, _ = self.post("/api/session", {"session": invalid_v1})
        self.assertEqual(status, 422, body)
        self.assertIsInstance(json.loads(body)["error"], str)
        self.assertEqual(self.get_session(), SESSION)

    def test_bad_params_and_duration_return_safe_errors(self):
        for path, body in (
            ("/api/session", {"unexpected": True}),
            ("/api/render", {"seconds": 0}),
            ("/api/render", {"seconds": "long"}),
        ):
            with self.subTest(path=path, body=body):
                status, response, headers = self.post(path, body)
                self.assertEqual(status, 422)
                self.assertIn("application/json", dict(headers)["Content-Type"])
                self.assertIsInstance(json.loads(response)["error"], str)

    def test_render_rejects_caller_selected_path(self):
        status, body, _ = self.post("/api/render", {"seconds": 0.01, "path": "/tmp/owned.wav"})
        self.assertEqual(status, 422, body)
        self.assertIn(b"Expected seconds only", body)

    def test_only_exact_static_routes_are_served(self):
        for path in ("/app.js?x=1", "/../README.md", "/%2e%2e/README.md", "/api/session/extra"):
            with self.subTest(path=path):
                status, body, _ = self.request("GET", path)
                self.assertEqual(status, 404, body)
        status, body, headers = self.request("GET", "/editor.js")
        self.assertEqual(status, 200, body)
        self.assertIn("text/javascript", dict(headers)["Content-Type"])


if __name__ == "__main__":
    unittest.main()

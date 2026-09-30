"""Integration tests for the loopback HTTP bridge."""
import http.client
import io
import json
import subprocess
import threading
import unittest
import wave
from unittest.mock import patch

from gui.server import EngineError, ROOT, Server


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

    def test_note_session_upload_is_rejected_before_replacing_engine_session(self):
        status, _, _ = self.post("/api/session", {"session": SESSION})
        self.assertEqual(status, 200)
        for version in (2, 3, 5):
            with self.subTest(version=version):
                timeline_session = {"schema_version": version, "sample_rate": 48000,
                                    "tempo_milli_bpm": 120000, "tracks": []}
                status, body, _ = self.post("/api/session", {"session": timeline_session})
                self.assertEqual(status, 422, body)
                self.assertIn(b"scripting interface", body)
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
             "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.2}, "effects": []},
            {"id": "malformed", "mode": [], "clips": {},
             "device": [], "effects": []},
        )
        for track in unsupported:
            with self.subTest(track=track):
                candidate = {**V4_GAIN_SESSION, "tracks": [track]}
                status, body, _ = self.post("/api/session", {"session": candidate})
                self.assertEqual(status, 422, body)
                self.assertIn(b"continuous sine", body)
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

"""Integration tests for the loopback HTTP bridge."""
import http.client
import io
import json
import subprocess
import threading
import unittest
import wave

from gui.server import ROOT, Server


BINARY = ROOT / "target" / "debug" / "daw"
SESSION = {
    "schema_version": 1,
    "sample_rate": 22050,
    "tracks": [
        {"id": "tone", "device": {"kind": "sine", "frequency_hz": 440, "gain": 0.2}}
    ],
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

    def test_capabilities_and_transport_routes_are_authenticated_and_report_stopped(self):
        status, _, _ = self.request("GET", "/api/capabilities", token=False)
        self.assertEqual(status, 403)
        status, body, _ = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200, body)
        capabilities = json.loads(body)
        self.assertIn("transport.status", capabilities["methods"])

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


if __name__ == "__main__":
    unittest.main()

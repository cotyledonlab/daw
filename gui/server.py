#!/usr/bin/env python3
"""Loopback-only browser UI for the existing Rust JSONL controller."""
import argparse
import hmac
import json
import math
import re
from pathlib import Path
import secrets
import selectors
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 1024 * 1024


class EngineError(Exception):
    pass


def validate_editor_session_shape(session):
    """Limit editor imports to represented session families before replacement.

    Rust remains authoritative for the complete schema and field validation. This
    preflight only rejects session families the browser editor cannot represent.
    """
    if not isinstance(session, dict):
        return
    version = session.get("schema_version")
    if type(version) is int and version in (2, 3, 5, 8):
        raise ValueError("This editor supports continuous sine sessions only; use the scripting interface for timeline and effect sessions.")
    if type(version) is not int or version not in (4, 6, 7):
        return
    if version == 4:
        supported = ("sine",)
        message = "This editor supports continuous sine tracks in v4."
    elif version == 6:
        supported = ("sine", "supercollider")
        message = "This editor supports continuous sine/SuperCollider tracks with gain effects in v6."
    else:
        supported = ("sine", "supercollider", "csound")
        message = "This editor supports continuous sine/SuperCollider/Csound tracks with gain effects in v7."
    tracks = session.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError(message)
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError(message)
        device = track.get("device")
        if (not isinstance(device, dict) or device.get("kind") not in supported
                or track.get("mode") != "continuous"
                or track.get("clips") != []):
            raise ValueError(message)

        if version in (6, 7) and (not isinstance(track.get("effects"), list) or any(not isinstance(effect, dict) or effect.get("kind") != "gain" for effect in track.get("effects", []))):
            raise ValueError(f"Schema-v{version} GUI sessions support gain effects only.")


def validate_revision(value):
    if not isinstance(value, str) or re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is None or int(value) > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("expected_revision must be a canonical unsigned 64-bit decimal string.")


def _finite_number(value):
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


class Engine:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            [str(binary), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=ROOT,
        )
        self.lock = threading.Lock()
        self.sequence = 0

    def call(self, method, params=None):
        with self.lock:
            self.sequence += 1
            request_id = str(self.sequence)
            wire = json.dumps({"protocol_version": 1, "id": request_id,
                               "method": method, "params": params or {}},
                              allow_nan=False).encode() + b"\n"
            if len(wire) > MAX_BODY:
                raise EngineError("Session is too large for the engine protocol.")
            try:
                self.process.stdin.write(wire)
                self.process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout=120):
                        self.process.kill()
                        raise EngineError("Engine timed out. Restart the GUI server.")
                line = self.process.stdout.readline(MAX_BODY + 1)
                response = json.loads(line)
            except (OSError, ValueError) as error:
                raise EngineError("Engine connection lost. Restart the GUI server.") from error
            if response.get("id") != request_id:
                raise EngineError("Engine response did not match the request.")
            if not response.get("ok"):
                raise EngineError(response["error"]["message"])
            return response["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()


class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, binary, port=0):
        self.token = secrets.token_urlsafe(32)
        self.engine = Engine(binary)
        try:
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            self.engine.close()
            raise
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def server_close(self):
        super().server_close()
        self.engine.close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def send_bytes(self, status, body, content_type="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'nonce-" + self.server.token + "'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value).encode())

    def authorized(self, api=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            self.send_json(403, {"error": "Invalid host."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            self.send_json(403, {"error": "Cross-origin requests are not allowed."})
            return False
        if api and not hmac.compare_digest(self.headers.get("X-DAW-Token", ""), self.server.token):
            self.send_json(403, {"error": "Reload this window to connect to the engine."})
            return False
        return True

    def do_GET(self):
        if not self.authorized(api=self.path.startswith("/api/")):
            return
        if self.path in ("/api/session", "/api/session/inspect", "/api/capabilities", "/api/transport"):
            try:
                method = {"/api/session": "session.get", "/api/session/inspect": "session.inspect", "/api/capabilities": "capabilities", "/api/transport": "transport.status"}[self.path]
                result = self.server.engine.call(method)
                if self.path == "/api/capabilities":
                    result = {**result, "gui_bridge": {"checked_replacement": True, "supercollider_sources": True, "csound_sources": True}}
                self.send_json(200, result)
            except EngineError as error:
                self.send_json(422, {"error": str(error)})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/live.js": ("live.js", "text/javascript; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in assets:
            self.send_json(404, {"error": "Not found."})
            return
        filename, content_type = assets[self.path]
        body = (ROOT / "gui" / filename).read_bytes()
        if self.path == "/":
            config = f'<script nonce="{self.server.token}">window.DAW_TOKEN={json.dumps(self.server.token)};</script>'
            body = body.replace(b"<!-- DAW_CONFIG -->", config.encode())
        self.send_bytes(200, body, content_type)

    def do_POST(self):
        if not self.authorized(api=True):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.send_json(413, {"error": "Request must be at most 1 MiB."})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json(415, {"error": "Expected application/json."})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/session":
                if set(data) not in ({"session"}, {"session", "expected_revision"}):
                    raise ValueError("Expected session and optional expected_revision only.")
                if "expected_revision" in data:
                    validate_revision(data["expected_revision"])
                validate_editor_session_shape(data["session"])
                self.send_json(200, self.server.engine.call("session.replace", data))
            elif self.path == "/api/transport":
                action = data.pop("action", None)
                if action not in ("play", "pause", "resume", "stop", "volume"):
                    raise ValueError("Unknown transport action.")
                self.send_json(200, self.server.engine.call("transport." + action, data))
            elif self.path == "/api/source/inspect":
                if set(data) != {"synthdef_hex"}:
                    raise ValueError("Expected synthdef_hex only.")
                program = data["synthdef_hex"]
                if not isinstance(program, str) or not 0 < len(program) <= 131072 or len(program) % 2 or re.fullmatch(r"[0-9a-fA-F]+", program) is None:
                    raise ValueError("Expected bounded even-length SynthDef hexadecimal bytes.")
                self.send_json(200, self.server.engine.call("supercollider.inspect", data))
            elif self.path == "/api/source/control":
                if set(data) != {"expected_revision", "track_id", "control_name", "values"}:
                    raise ValueError("Expected expected_revision, track_id, control_name and values only.")
                validate_revision(data["expected_revision"])
                for key, limit in (("track_id", 128), ("control_name", 255)):
                    value = data[key]
                    if not isinstance(value, str) or not 0 < len(value.encode("utf-8")) <= limit:
                        raise ValueError(f"{key} must be a nonempty string of at most {limit} UTF-8 bytes.")
                values = data["values"]
                if (not isinstance(values, list) or not 1 <= len(values) <= 256
                        or any(type(value) not in (int, float) or not _finite_number(value)
                               for value in values)
                        or (len(values) > 1 and any(abs(value) > 3.4028234663852886e38
                                                   for value in values))):
                    raise ValueError("values must contain 1–256 finite numbers; array values must fit float32.")
                self.send_json(200, self.server.engine.call("source.set_control", data))
            elif self.path == "/api/effect/inspect":
                if set(data) != {"track_id", "effect_id"}:
                    raise ValueError("Expected track_id and effect_id only.")
                for key in ("track_id", "effect_id"):
                    value = data[key]
                    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
                        raise ValueError(f"{key} must be a nonempty string of at most 128 UTF-8 bytes.")
                self.send_json(200, self.server.engine.call("effect.inspect", data))
            elif self.path == "/api/effect/parameter":
                if set(data) != {"expected_revision", "track_id", "effect_id", "parameter_id", "value"}:
                    raise ValueError("Expected expected_revision, track_id, effect_id, parameter_id and value only.")
                revision = data["expected_revision"]
                if not isinstance(revision, str) or not revision or not revision.isascii() or not revision.isdecimal():
                    raise ValueError("expected_revision must be a canonical unsigned decimal string.")
                if str(int(revision)) != revision:
                    raise ValueError("expected_revision must be a canonical unsigned decimal string.")
                for key in ("track_id", "effect_id"):
                    value = data[key]
                    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
                        raise ValueError(f"{key} must be a nonempty string of at most 128 UTF-8 bytes.")
                if type(data["parameter_id"]) is not int or not 0 <= data["parameter_id"] <= 0xFFFFFFFF:
                    raise ValueError("parameter_id must be an unsigned 32-bit integer.")
                value = data["value"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError("value must be a finite number from 0 to 1.")
                self.send_json(200, self.server.engine.call("effect.set_parameter", data))
            elif self.path == "/api/render":
                if set(data) != {"seconds"}:
                    raise ValueError("Expected seconds only.")
                with tempfile.TemporaryDirectory(prefix="daw-render-") as directory:
                    path = Path(directory) / "render.wav"
                    report = self.server.engine.call("render", {"path": str(path), "seconds": data["seconds"]})
                    self.send_bytes(200, path.read_bytes(), "audio/wav", {
                        "Content-Disposition": 'attachment; filename="render.wav"',
                        "X-Clipped-Frames": report["clipped_frames"],
                    })
            else:
                self.send_json(404, {"error": "Not found."})
        except (ValueError, EngineError) as error:
            self.send_json(422, {"error": str(error)})
        except OSError:
            # Includes disconnected browsers: the next request can still use the engine.
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="loopback port (default: choose a free port)")
    parser.add_argument("--no-open", action="store_true", help="print URL without opening a browser")
    args = parser.parse_args()
    binary = ROOT / "target" / "debug" / "daw"
    if not binary.is_file():
        parser.error("Build the engine first with: cargo build --locked")
    with Server(binary, args.port) as server:
        print(f"DAW GUI: {server.origin}", flush=True)
        print("One local session. Ctrl+C stops the server and engine. Save before closing.", flush=True)
        if not args.no_open:
            webbrowser.open(server.origin)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

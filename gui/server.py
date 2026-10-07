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
try:
    from .studio import Studio
    from .audio_projects import AudioProjects, LIMITS, MAX_AUDIO_BODY, MAX_PROJECT_BODY, metadata, strict_json
except ImportError:
    from studio import Studio
    from audio_projects import AudioProjects, LIMITS, MAX_AUDIO_BODY, MAX_PROJECT_BODY, metadata, strict_json

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 1024 * 1024
MAX_FRAME = 9007199254740991


class EngineError(Exception):
    def __init__(self, message, *, commit_outcome_unknown=False):
        super().__init__(message)
        self.commit_outcome_unknown = commit_outcome_unknown


def validate_editor_session_shape(session):
    """Limit editor imports to represented session families before replacement.

    Rust remains authoritative for the complete schema and field validation. This
    preflight only rejects session families the browser editor cannot represent.
    """
    if not isinstance(session, dict):
        return
    version = session.get("schema_version")
    if type(version) is int and version in (5, 8):
        raise ValueError("Use the scripting interface for this session format.")
    if type(version) is not int or version not in (2, 3, 4, 6, 7, 9, 10, 11, 12):
        return
    if version in (2, 3, 4):
        supported = ("sine", "audio")
        message = "This editor supports continuous sine tracks, sine note clips and owned audio clips in v2/v3/v4."
    elif version in (9, 10, 11, 12):
        supported = ("sine", "audio", "drumkit", "synth") + (("pd_instrument",) if version == 12 else ())
        effects = "gain, lowpass and delay" if version >= 11 else "gain"
        message = f"This editor supports built-in note instruments and owned audio clips with {effects} effects in v{version}."
    elif version == 6:
        supported = ("sine", "supercollider")
        message = "This editor supports continuous sine/SuperCollider tracks with gain effects in v6."
    else:
        supported = ("sine", "supercollider", "csound")
        message = "This editor supports continuous sine/SuperCollider/Csound tracks with gain effects in v7."
    tracks = session.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError(message)
    sequenced_session = any(isinstance(track, dict) and track.get("mode") == "sequenced" for track in tracks)
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError(message)
        device = track.get("device")
        clips = track.get("clips")
        mode = track.get("mode")
        if (not isinstance(device, dict) or device.get("kind") not in supported
                or not isinstance(clips, list)
                or (mode == "continuous" and clips != [])
                or (mode == "sequenced" and (version not in (2, 3, 4, 9, 10, 11, 12)
                    or any(not isinstance(clip, dict) or clip.get("kind") != ("audio" if device.get("kind") == "audio" else "notes") for clip in clips)))
                or (device.get("kind") in ("audio", "drumkit", "synth", "pd_instrument") and mode != "sequenced")
                or mode not in ("continuous", "sequenced")):
            raise ValueError(message)

        if (version in (3, 6, 7, 9, 10, 11, 12) or (version == 4 and sequenced_session)) and (not isinstance(track.get("effects"), list) or any(not isinstance(effect, dict) or effect.get("kind") not in (("gain", "lowpass", "delay") if version >= 11 else ("gain",)) for effect in track.get("effects", []))):
            raise ValueError(f"Schema-v{version} GUI sessions require compatible built-in effects.")


def validate_revision(value):
    if not isinstance(value, str) or re.fullmatch(r"0|[1-9][0-9]{0,19}", value) is None or int(value) > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("expected_revision must be a canonical unsigned 64-bit decimal string.")


def _finite_number(value):
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


class Engine:
    def __init__(self, binary, cwd=None):
        self.process = subprocess.Popen(
            [str(Path(binary).resolve()), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=ROOT if cwd is None else cwd,
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
                        raise EngineError("Engine timed out. Restart the GUI server.", commit_outcome_unknown=True)
                line = self.process.stdout.readline(MAX_BODY + 1)
                response = json.loads(line)
            except (OSError, ValueError) as error:
                raise EngineError("Engine connection lost. Restart the GUI server.", commit_outcome_unknown=True) from error
            if response.get("id") != request_id:
                raise EngineError("Engine response did not match the request.", commit_outcome_unknown=True)
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
        self.studio = Studio()
        self.token = secrets.token_urlsafe(32)
        self.project_lock = threading.RLock()
        self.asset_directory = tempfile.TemporaryDirectory(prefix="daw-project-")
        self.asset_root = Path(self.asset_directory.name)
        try:
            self.engine = Engine(binary, cwd=self.asset_root)
            self.projects = AudioProjects(self.engine, self.asset_root, validate_editor_session_shape)
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            if hasattr(self, "engine"):
                self.engine.close()
            self.asset_directory.cleanup()
            raise
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def server_close(self):
        super().server_close()
        self.engine.close()
        self.asset_directory.cleanup()


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
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'nonce-" + self.server.token + "'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
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
        if self.path == "/api/studio":
            self.send_json(200, self.server.studio.status())
            return
        if self.path == "/api/project":
            try:
                with self.server.project_lock:
                    body = self.server.projects.export_project()
                self.send_bytes(200, body, "application/zip", {
                    "Content-Disposition": 'attachment; filename="session.daw.zip"',
                })
            except (ValueError, EngineError) as error:
                self.send_json(422, {"error": str(error)})
            return
        if self.path in ("/api/session", "/api/session/inspect", "/api/capabilities", "/api/transport"):
            try:
                method = {"/api/session": "session.get", "/api/session/inspect": "session.inspect", "/api/capabilities": "capabilities", "/api/transport": "transport.status"}[self.path]
                with self.server.project_lock:
                    result = self.server.engine.call(method)
                if self.path == "/api/capabilities":
                    result = {**result, "gui_bridge": {"checked_replacement": True, "supercollider_sources": True, "csound_sources": True, "audio_projects": True, "audio_project_limits": LIMITS, "note_preview": True}}
                self.send_json(200, result)
            except EngineError as error:
                self.send_json(422, {"error": str(error)})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/live.js": ("live.js", "text/javascript; charset=utf-8"),
                  "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/editor.js": ("editor.js", "text/javascript; charset=utf-8"),
                  "/timeline.js": ("timeline.js", "text/javascript; charset=utf-8"),
                  "/mixer.js": ("mixer.js", "text/javascript; charset=utf-8"),
                  "/automation.js": ("automation.js", "text/javascript; charset=utf-8"),
                  "/pd_instrument.js": ("pd_instrument.js", "text/javascript; charset=utf-8"),
                  "/note_input.js": ("note_input.js", "text/javascript; charset=utf-8"),
                  "/note_recording.js": ("note_recording.js", "text/javascript; charset=utf-8"),
                  "/history.js": ("history.js", "text/javascript; charset=utf-8"),
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
        if self.path in ("/api/studio/prompt", "/api/studio/speak", "/api/studio/transcribe"):
            self._post_studio()
            return
        with self.server.project_lock:
            self._post_locked()

    def _post_studio(self):
        try:
            mime = self.headers.get("Content-Type", "").split(";")[0].strip()
            if self.path == "/api/studio/transcribe":
                body = self.read_body(4 * 1024 * 1024)
                if body is not None:
                    self.send_json(200, self.server.studio.transcribe(body, mime))
                return
            if mime != "application/json":
                raise ValueError("Expected application/json.")
            body = self.read_body(MAX_BODY)
            if body is None:
                return
            data = strict_json(body)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/studio/speak":
                self.send_bytes(200, self.server.studio.speak(data), "audio/mpeg")
                return
            validate_revision(data.get("expected_revision"))
            # Snapshot under the project lock, then release it during network I/O.
            # A slow provider must never block Stop, saves or engine polling.
            with self.server.project_lock:
                snapshot = self.server.engine.call("session.inspect")
            self.send_json(200, self.server.studio.prompt(data, snapshot))
        except (ValueError, EngineError) as error:
            self.send_json(422, {"error": str(error)})

    def read_body(self, limit):
        lengths = self.headers.get_all("Content-Length", [])
        if (len(lengths) != 1 or not re.fullmatch(r"[1-9][0-9]{0,9}", lengths[0])
                or self.headers.get("Transfer-Encoding")):
            raise ValueError("Expected one Content-Length and no Transfer-Encoding.")
        length = int(lengths[0])
        if length > limit:
            self.send_json(413, {"error": f"Request must be at most {limit} bytes."})
            self.close_connection = True
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Request body is truncated.")
        return body

    def _post_locked(self):
        try:
            if self.path in ("/api/audio/import", "/api/project"):
                content_type = self.headers.get("Content-Type", "").split(";")[0].strip()
                allowed = ("audio/wav", "application/octet-stream") if self.path == "/api/audio/import" else ("application/zip", "application/octet-stream")
                if content_type not in allowed:
                    self.send_json(415, {"error": "Expected a binary WAV or project ZIP content type."})
                    self.close_connection = True
                    return
                headers = self.headers.get_all("X-DAW-Metadata", [])
                if len(headers) != 1:
                    raise ValueError("Expected one X-DAW-Metadata header.")
                keys = ("expected_revision", "track_id", "clip_id", "start_frame") if self.path == "/api/audio/import" else ("expected_revision",)
                meta = metadata(headers[0], keys)
                body = self.read_body(MAX_AUDIO_BODY if self.path == "/api/audio/import" else MAX_PROJECT_BODY)
                if body is None:
                    return
                result = self.server.projects.import_audio(meta, body) if self.path == "/api/audio/import" else self.server.projects.import_project(meta, body)
                self.send_json(200, result)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.send_json(413, {"error": "Request must be at most 1 MiB."})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json(415, {"error": "Expected application/json."})
                return
            body = self.read_body(MAX_BODY)
            if body is None:
                return
            data = strict_json(body)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path in ("/api/session", "/api/note/take"):
                if set(data) not in ({"session"}, {"session", "expected_revision"}):
                    raise ValueError("Expected session and optional expected_revision only.")
                if self.path == "/api/note/take" and "expected_revision" not in data:
                    raise ValueError("A recorded take requires expected_revision.")
                if "expected_revision" in data:
                    validate_revision(data["expected_revision"])
                validate_editor_session_shape(data["session"])
                self.server.projects.registered(data["session"])
                result = self.server.engine.call("session.replace", data)
                if self.path == "/api/note/take":
                    result = self.server.engine.call("session.inspect")
                self.send_json(200, result)
            elif self.path == "/api/transport":
                action = data.pop("action", None)
                if action not in ("play", "pause", "resume", "stop", "volume"):
                    raise ValueError("Unknown transport action.")
                self.send_json(200, self.server.engine.call("transport." + action, data))
            elif self.path == "/api/transport/seek":
                if set(data) != {"frame"}:
                    raise ValueError("Expected frame only.")
                if type(data["frame"]) is not int or not 0 <= data["frame"] <= MAX_FRAME:
                    raise ValueError("frame must be an integer from 0 through MAX_FRAME.")
                self.send_json(200, self.server.engine.call("transport.seek", data))
            elif self.path == "/api/transport/loop":
                if set(data) != {"region"}:
                    raise ValueError("Expected region only.")
                region = data["region"]
                if region is not None and (not isinstance(region, dict)
                        or set(region) != {"start_frame", "end_frame"}
                        or type(region["start_frame"]) is not int
                        or type(region["end_frame"]) is not int
                        or not 0 <= region["start_frame"] < region["end_frame"] <= MAX_FRAME):
                    raise ValueError("region must be null or integer frames with 0 <= start_frame < end_frame <= MAX_FRAME.")
                self.send_json(200, self.server.engine.call("transport.loop", data))
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
            elif self.path == "/api/note/preview":
                if set(data) != {"expected_revision", "track_id", "frequency_hz", "velocity"}:
                    raise ValueError("Expected expected_revision, track_id, frequency_hz and velocity only.")
                validate_revision(data["expected_revision"])
                track_id = data["track_id"]
                if not isinstance(track_id, str) or not 0 < len(track_id.encode("utf-8")) <= 128:
                    raise ValueError("track_id must be a nonempty string of at most 128 UTF-8 bytes.")
                for key in ("frequency_hz", "velocity"):
                    value = data[key]
                    if type(value) not in (int, float) or not _finite_number(value):
                        raise ValueError(f"{key} must be a finite number.")
                if not 0 < data["frequency_hz"] < 96000 or not 0 <= data["velocity"] <= 1:
                    raise ValueError("frequency_hz must be positive and below Nyquist; velocity must be from 0 to 1.")
                with tempfile.TemporaryDirectory(prefix="daw-note-preview-") as directory:
                    path = Path(directory) / "preview.wav"
                    report = self.server.engine.call("note.preview", {**data, "path": str(path)})
                    self.send_bytes(200, path.read_bytes(), "audio/wav", {
                        "X-Clipped-Frames": report["clipped_frames"],
                    })
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

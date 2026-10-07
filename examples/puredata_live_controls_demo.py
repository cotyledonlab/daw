#!/usr/bin/env python3
"""Edit one saved Pd receiver during muted live playback, then verify saved render."""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from examples.puredata_tracks_demo import make_track  # noqa: E402
from gui.server import Engine  # noqa: E402

TARGET_AMPLITUDE = 0.03
DELIVERED_AMPLITUDE = ctypes.c_float(TARGET_AMPLITUDE).value


def request(engine: Engine, method: str, params: dict | None = None):
    try:
        return engine.call(method, params)
    except Exception as error:
        raise RuntimeError(f"{method} failed: {error}") from error


def require_file_env(name: str, *, executable: bool = False) -> Path:
    value = os.environ.get(name)
    path = Path(value) if value else None
    if (path is None or not path.is_absolute() or not path.is_file()
            or (executable and not os.access(path, os.X_OK))):
        suffix = "executable file" if executable else "file"
        raise RuntimeError(f"set {name} to an absolute path to an existing {suffix}")
    return path


def check_optional_file_env(name: str, *, executable: bool = False) -> None:
    if name not in os.environ:
        return
    require_file_env(name, executable=executable)


def wait_for_control(engine: Engine, revision: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = request(engine, "transport.status")
        update = latest.get("source_control_update") or {}
        if (not update.get("pending")
                and update.get("applied_revision") == revision
                and update.get("callback_observed") is True):
            if update.get("delivered_value") != DELIVERED_AMPLITUDE:
                raise RuntimeError(f"Pd receiver float32 delivery did not match {DELIVERED_AMPLITUDE!r}: {update}")
            if update.get("delivery") != "receiver_message_and_block_publication":
                raise RuntimeError(f"unexpected Pd control delivery evidence: {update}")
            return latest
        if latest.get("state") not in ("starting", "playing"):
            raise RuntimeError(f"transport stopped before the Pd control reached a callback block: {latest}")
        time.sleep(0.01)
    raise RuntimeError(f"Pd control update was not acknowledged by the callback: {latest}")


def wait_for_stop(engine: Engine, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = request(engine, "transport.status")
        if latest.get("state") == "stopped":
            if latest.get("error"):
                raise RuntimeError(f"live Pure Data transport failed: {latest['error']}")
            return latest
        time.sleep(0.025)
    raise RuntimeError(f"live Pure Data transport did not finish within {timeout:g} seconds: {latest}")


def assert_pids_reaped(pids) -> None:
    if not isinstance(pids, list) or not pids:
        raise RuntimeError(f"transport did not report its owned Pure Data worker PIDs: {pids!r}")
    for pid in pids:
        if type(pid) is not int or pid <= 0:
            raise RuntimeError(f"transport reported an invalid worker PID: {pid!r}")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            raise RuntimeError(f"owned Pure Data worker PID {pid} still exists") from error
        except OSError as error:
            if error.errno == errno.ESRCH:
                continue
            raise RuntimeError(f"could not verify reaping of Pure Data worker PID {pid}: {error}") from error
        raise RuntimeError(f"owned Pure Data worker PID {pid} was not reaped")


def verify_render(path: Path, render: dict) -> int:
    if (render.get("frames"), render.get("sample_rate"), render.get("channels")) != (48000, 48000, 2):
        raise RuntimeError(f"unexpected rendered WAV metadata: {render}")
    with wave.open(str(path), "rb") as wav:
        layout = (wav.getnframes(), wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        if layout != (48000, 48000, 2, 2):
            raise RuntimeError(f"unexpected rendered WAV header: {layout}")
        raw = wav.readframes(48000)
    pcm = struct.unpack(f"<{len(raw) // 2}h", raw)
    peak = max(map(abs, pcm), default=0)
    # .03 source amplitude * .5 source gain * .5 effect gain, converted to PCM16.
    if not 243 <= peak <= 249:
        raise RuntimeError(f"saved Pd value did not produce the expected ~246 PCM16 peak: {peak}")
    if pcm[::2] != pcm[1::2]:
        raise RuntimeError("rendered Pd source lost its identical stereo routing")
    return peak


def main() -> None:
    require_file_env("DAW_LIBPD_LIBRARY")
    check_optional_file_env("DAW_LIBPD_QUEUE_LIBRARY")
    check_optional_file_env("DAW_LIBPD_STREAM_WORKER")
    check_optional_file_env("DAW_LIBPD_PYTHON", executable=True)

    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix="puredata-live-control-", dir=output_dir))
    session_path = project / "session.json"
    render_path = project / "after-edit.wav"

    track = make_track("pd-control", 440.0, 0.5)
    device = track["device"]
    device["duration_frames"] = 4 * 48000
    for control in device["controls"]:
        control["points"] = []
    session = {
        "schema_version": 8,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [track],
    }

    engine = Engine(ROOT / "target/debug/daw")
    active = False
    try:
        capabilities = request(engine, "capabilities")
        if not capabilities.get("live_audio"):
            raise RuntimeError("the selected DAW binary has no native-audio support; build it with --features native-audio")
        if not capabilities.get("puredata_sources", {}).get("configured"):
            raise RuntimeError("the DAW engine cannot configure libpd; check DAW_LIBPD_LIBRARY")
        if not capabilities.get("puredata_live_transport", {}).get("implemented"):
            raise RuntimeError("this DAW binary does not implement Pure Data live transport")
        if not capabilities.get("puredata_live_transport", {}).get("live_control_edits"):
            raise RuntimeError("this DAW binary does not implement live Pure Data receiver edits")

        request(engine, "session.replace", {"session": session})
        request(engine, "session.save", {"path": str(session_path)})
        request(engine, "session.load", {"path": str(session_path)})
        if request(engine, "session.get") != session:
            raise RuntimeError("saved/reloaded Pd control session differs from its requested values")
        before = request(engine, "session.inspect")
        revision = before["revision"]

        started = request(engine, "transport.play", {
            "seconds": 4.0,
            "volume": 0.0,
            "source_mode": "live",
        })
        active = True
        if started.get("state") not in ("starting", "playing") or started.get("volume") != 0.0:
            raise RuntimeError(f"live Pd playback did not start muted: {started}")
        if started.get("source_mode") != "live" or started.get("runtime") != "puredata":
            raise RuntimeError(f"transport did not select live Pure Data: {started}")
        pids = (started.get("source") or {}).get("owned_pids")
        if not isinstance(pids, list) or len(pids) != 1:
            raise RuntimeError(f"expected one owned Pure Data worker: {started}")

        accepted = request(engine, "source.set_control", {
            "expected_revision": revision,
            "track_id": "pd-control",
            "control_name": "$0-amplitude",
            "values": [TARGET_AMPLITUDE],
        })
        if accepted.get("queued") is not True:
            raise RuntimeError(f"Pure Data control edit was not accepted: {accepted}")
        revision = accepted.get("revision")
        if not isinstance(revision, str):
            raise RuntimeError(f"Pure Data control edit did not return its new revision: {accepted}")
        latest = wait_for_control(engine, revision)
        current = request(engine, "session.get")
        saved_control = next(c for c in current["tracks"][0]["device"]["controls"]
                             if c["name"] == "$0-amplitude")
        if saved_control.get("value") != TARGET_AMPLITUDE or saved_control.get("points") != []:
            raise RuntimeError(f"accepted Pd base was not saved exactly as 0.03 without automation: {saved_control}")

        final = wait_for_stop(engine)
        active = False
        source = final.get("source") or {}
        if final.get("submitted_frames") != 4 * 48000 or source.get("source_frames") != 4 * 48000:
            raise RuntimeError(f"expected 192000 source/callback frames: {final}")
        if final.get("live_source_underruns") != 0:
            raise RuntimeError(f"live Pd source underruns occurred: {final}")
        if final.get("resources_released") is not True:
            raise RuntimeError(f"live Pd transport did not release its resources: {final}")
        if (source.get("owned_processes_released") is not True
                or source.get("owned_servers_released") is not True
                or source.get("queue_released") is not True):
            raise RuntimeError(f"live Pd worker or queue was not released: {source}")
        if source.get("owned_pids") != pids:
            raise RuntimeError("final live report does not match the started Pure Data worker")
        assert_pids_reaped(pids)
        update = final.get("source_control_update") or latest.get("source_control_update") or {}
        if (update.get("applied_revision") != revision or update.get("pending") is not False
                or update.get("callback_observed") is not True
                or update.get("delivered_value") != DELIVERED_AMPLITUDE
                or update.get("delivery") != "receiver_message_and_block_publication"):
            raise RuntimeError(f"final transport snapshot lost Pd control delivery evidence: {update}")

        request(engine, "session.save", {"path": str(session_path)})
        if json.loads(session_path.read_text(encoding="utf-8")) != current:
            raise RuntimeError("saved session does not contain the exact accepted amplitude base")
        request(engine, "session.load", {"path": str(session_path)})
        if request(engine, "session.get") != current:
            raise RuntimeError("Pd amplitude base changed during save/reload")
        inspect_before_render = request(engine, "session.inspect")
        render = request(engine, "render", {"path": str(render_path), "seconds": 1.0})
        peak = verify_render(render_path, render)
        if request(engine, "session.inspect") != inspect_before_render:
            raise RuntimeError("offline render changed the saved session or revision")
        print(json.dumps({
            "monitor_volume": 0,
            "delivered_amplitude_float32": DELIVERED_AMPLITUDE,
            "render_peak_pcm16": peak,
            "transport": final,
            "session": str(session_path),
            "render": str(render_path),
        }, indent=2))
    finally:
        if active:
            try:
                status = request(engine, "transport.status")
                if status.get("state") != "stopped":
                    request(engine, "transport.stop")
            except Exception:
                pass
        engine.close()


if __name__ == "__main__":
    main()

"""Opt-in macOS integration checks for saved schema-v6 SuperCollider playback.

Run with DAW_TEST_SC_SAVED=1 and DAW_SCSYNTH=/absolute/path/to/scsynth after
building target/debug/daw with native-audio and the stream capture plugin.
"""
from __future__ import annotations

import json
import math
import os
import select
import signal
import struct
import time
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
from examples.supercollider_tracks_demo import make_track  # noqa: E402
from native.supercollider.score import _pstring, _ugen  # noqa: E402

ENABLED = os.environ.get("DAW_TEST_SC_SAVED") == "1"
SCSYNTH = os.environ.get("DAW_SCSYNTH")
DAW = ROOT / "target/debug/daw"


def fixed_bus_synthdef() -> bytes:
    """Stereo sine that writes directly to bus zero without an `out` control."""
    name = "daw_fixed_bus_sine"
    ugens = [
        _ugen("Control", 1, [], [1, 1]),
        _ugen("SinOsc", 2, [(0, 0), (-1, 0)], [2]),
        _ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
        _ugen("Out", 2, [(-1, 0), (2, 0), (2, 0)], []),
    ]
    definition = bytearray(_pstring(name))
    definition.extend(struct.pack(">i", 1))
    definition.extend(struct.pack(">f", 0.0))
    definition.extend(struct.pack(">i", 2))
    definition.extend(struct.pack(">ff", 220.0, 0.1))
    definition.extend(struct.pack(">i", 2))
    for control_name, index in (("freq", 0), ("gain", 1)):
        definition.extend(_pstring(control_name))
        definition.extend(struct.pack(">i", index))
    definition.extend(struct.pack(">i", len(ugens)))
    definition.extend(b"".join(ugens))
    definition.extend(struct.pack(">H", 0))
    return b"SCgf" + struct.pack(">iH", 2, 1) + bytes(definition)


def install_fixed_bus_source(session: dict) -> dict:
    result = json.loads(json.dumps(session))
    raw = fixed_bus_synthdef().hex()
    for track in result["tracks"]:
        device = track["device"]
        if device["kind"] != "supercollider":
            continue
        device["synthdef_hex"] = raw
        device["synth_name"] = "daw_fixed_bus_sine"
        device["duration_frames"] = 36_000
        device["controls"] = [control for control in device["controls"] if control["name"] != "out"]
    return result


def saved_session() -> dict:
    tracks = [
        make_track("sc-low", 220.0, 0.1, 0.5, 0.8, 0.6),
        make_track("sc-high", 330.0, 0.1, 0.25, 0.4, 0.5),
    ]
    for track, duration in zip(tracks, (36_000, 24_000)):
        source = track["device"]
        source["duration_frames"] = duration
        # Replace the saved synth gain with the timed native control point.
        source["controls"] = [
            ({**control, "values": [0.1], "points": [{"frame": 24_000, "values": [0.02]}]}
             if control["name"] == "gain" else control)
            for control in source["controls"]
        ]
    for track in tracks:
        for control in track["device"]["controls"]:
            control["points"] = [point for point in control["points"] if point["frame"] < track["device"]["duration_frames"]]
    tracks.append({
        "id": "builtin-sine", "mode": "continuous", "clips": [],
        "effects": [{"kind": "gain", "id": "builtin-level", "gain": 1.0, "bypass": False}],
        "device": {"kind": "sine", "frequency_hz": 110.0, "gain": 0.0001},
    })
    return {"schema_version": 6, "sample_rate": 48000,
            "tempo_milli_bpm": 120000, "tracks": tracks}


def run_session(session: dict, directory: Path, *, scsynth: str | None = SCSYNTH,
                seconds: str = "1", volume: str = "0", allow_files: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, Path]:
    session_path = directory / "saved-session.json"
    session_path.write_text(json.dumps(session, separators=(",", ":")), encoding="utf-8")
    original = session_path.read_bytes()
    env = os.environ.copy()
    if scsynth is None:
        env.pop("DAW_SCSYNTH", None)
    else:
        env["DAW_SCSYNTH"] = scsynth
    result = subprocess.run(
        [str(DAW), "sc-session-play", str(session_path), seconds, volume],
        cwd=ROOT, env=env, capture_output=True, timeout=20, check=False,
    )
    if result.returncode != 0:
        if session_path.read_bytes() != original:
            raise AssertionError("failed playback changed the saved session")
        expected_names = sorted(("saved-session.json", *allow_files))
        if sorted(path.name for path in directory.iterdir()) != expected_names:
            raise AssertionError("failed playback created or changed another file")
    return result, session_path


@unittest.skipUnless(ENABLED, "set DAW_TEST_SC_SAVED=1 for saved-session live checks")
class SavedLiveSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("saved live SC playback requires macOS")
        if not DAW.is_file():
            raise unittest.SkipTest("build target/debug/daw with native-audio first")
        if not SCSYNTH or not Path(SCSYNTH).is_absolute() or not Path(SCSYNTH).is_file():
            raise unittest.SkipTest("set DAW_SCSYNTH to an installed absolute scsynth executable")

    def test_saved_sources_mix_with_builtin_gain_and_timed_native_controls(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-saved-") as temporary:
            result, _ = run_session(saved_session(), Path(temporary))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stderr, b"DAW_SC_STREAM_READY\n")
        report = json.loads(result.stdout)
        source = report["source"]
        self.assertTrue(report["saved_session"])
        self.assertEqual(report["source_preparation"], "owned_live_sc")
        self.assertFalse(report["session_transport"])
        self.assertEqual(source["runtime_sources"], 2)
        self.assertEqual(source["source_frames"], 48_000)
        self.assertEqual(source["source_digest"], report["callback_source_digest"])
        self.assertEqual(report["underruns"], 0)
        self.assertTrue(source["owned_servers_released"])
        self.assertTrue(source["queue_released"])
        self.assertEqual(len(source["owned_pids"]), 2)
        for pid in source["owned_pids"]:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        self.assertEqual(len(source["hardware_bus_peaks"]), 2)
        self.assertTrue(all(value == 0 for value in source["hardware_bus_peaks"]))
        quarters = source["rms_quarters"]
        self.assertEqual(len(quarters), 4)
        self.assertGreater(quarters[0], 0)
        self.assertGreater(quarters[1], 0)
        self.assertTrue(math.isclose(quarters[1] / quarters[0], 1.0, rel_tol=0.15))
        # Sources free at 0.5 s and 0.75 s while the native player runs for 1 s.
        self.assertGreater(quarters[2], 0)
        self.assertTrue(math.isclose(quarters[2] / quarters[0], 0.196, rel_tol=0.15))
        self.assertLess(quarters[3], quarters[0] * 0.03)
        # Saved source/device/effect gains keep the measured pre-master mix bounded.
        self.assertGreater(source["pre_master_peak"], 0.015)
        self.assertLess(source["pre_master_peak"], 0.04)
        self.assertEqual(report["submitted_frames"], 48_000)
        self.assertTrue(report["stream_released"])

    def test_hard_coded_out_bus_without_out_control_reaches_callback_and_mutes_buses(self):
        session = install_fixed_bus_source(saved_session())
        session["tracks"] = [session["tracks"][0], session["tracks"][-1]]
        with tempfile.TemporaryDirectory(prefix="daw-sc-fixed-bus-") as temporary:
            result, _ = run_session(session, Path(temporary))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        report = json.loads(result.stdout)
        source = report["source"]
        self.assertEqual(source["runtime_sources"], 1)
        self.assertGreater(source["pre_master_peak"], 0.01)
        self.assertGreater(source["rms_quarters"][0], 0)
        self.assertTrue(all(value == 0 for value in source["hardware_bus_peaks"]))
        self.assertLess(source["rms_quarters"][3], source["rms_quarters"][0] * 0.03)

    def test_gain_chain_automation_and_bypassed_gain_keep_expected_quarter_ratio(self):
        session = saved_session()
        for track in session["tracks"][:2]:
            track["device"]["duration_frames"] = 48_000
            for control in track["device"]["controls"]:
                if control["name"] == "gain":
                    control["points"] = [{"frame": 24_000, "values": [0.02]}]
            shape, level = track["effects"]
            level["bypass"] = True
            automation_frame = min(24_000, track["device"]["duration_frames"] - 1)
            track["automation"] = [{
                "effect_id": shape["id"], "parameter": "gain", "interpolation": "step",
                "points": [{"frame": automation_frame, "value": shape["gain"] * 0.5}],
            }]
        with tempfile.TemporaryDirectory(prefix="daw-sc-gain-chain-") as temporary:
            result, _ = run_session(session, Path(temporary))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        source = json.loads(result.stdout)["source"]
        quarters = source["rms_quarters"]
        self.assertTrue(math.isclose(quarters[3] / quarters[0], 0.1, rel_tol=0.2))
        self.assertGreater(source["pre_master_peak"], 0.025)
        self.assertLess(source["pre_master_peak"], 0.07)

    def test_schema_and_malformed_program_fail_without_mutating_saved_file(self):
        cases = []
        old_schema = saved_session()
        old_schema["schema_version"] = 5
        cases.append(("old schema", old_schema))
        malformed_program = saved_session()
        malformed_program["tracks"][0]["device"]["synthdef_hex"] = "00"
        cases.append(("malformed SynthDef", malformed_program))
        for label, session in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="daw-sc-invalid-") as temporary:
                result, _ = run_session(session, Path(temporary))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")

    def test_missing_runtime_fails_without_mutating_saved_file(self):
        missing = str(Path(tempfile.gettempdir()) / "daw-sc-scsynth-does-not-exist")
        with tempfile.TemporaryDirectory(prefix="daw-sc-no-runtime-") as temporary:
            result, _ = run_session(saved_session(), Path(temporary), scsynth=missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_unsupported_vst3_and_au_routing_fails_without_mutating_saved_file(self):
        vst = saved_session()
        vst["tracks"][0]["effects"] = [{
            "kind": "vst3", "id": "unsupported-vst", "bypass": False,
            "bundle_path": str(ROOT / "output/vst3-spike/DawTestGain.vst3"),
            "class_id": "DA01234567894ABCBDEF0123456789AB", "state_hex": "",
            "controller_state_hex": "", "parameters": [{"id": 0, "value": 0.5, "points": []}],
        }]
        au = saved_session()
        au["tracks"][0]["effects"] = [{
            "kind": "au", "id": "unsupported-au", "bypass": False,
            "component_type": "aufx", "component_subtype": "lpas",
            "component_manufacturer": "appl", "state_hex": "",
            "parameters": [{"id": 0, "value": 1000.0}],
        }]
        for label, session in (("VST3", vst), ("AU", au)):
            with self.subTest(plugin=label), tempfile.TemporaryDirectory(prefix="daw-sc-plugin-") as temporary:
                result, _ = run_session(session, Path(temporary))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")


    def _fake_runtime(self, directory: Path, mode: str) -> tuple[Path, Path]:
        fake_dir = directory / "fake-runtime"
        fake_dir.mkdir()
        (fake_dir / "plugins").mkdir()
        executable = fake_dir / "scsynth"
        pid_file = fake_dir / "pid"
        script = f"""#!/usr/bin/env python3
import os, signal, socket, sys, time
open({str(pid_file)!r}, "w").write(str(os.getpid()))
mode = {mode!r}
if mode == "crash":
    print("fake crash", flush=True)
    sys.exit(17)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 0))
print("server ready", flush=True)
if mode == "overflow":
    sys.stdout.write("x" * 70000)
    sys.stdout.flush()
while True:
    time.sleep(0.1)
"""
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o755)
        return executable, pid_file

    def _assert_pid_reaped(self, pid_file: Path):
        self.assertTrue(pid_file.is_file(), "fake scsynth did not start")
        pid = int(pid_file.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_owned_fake_runtime_crash_overflow_and_startup_hang_are_reaped(self):
        expected = {
            "crash": "owned scsynth exited",
            "overflow": "exceeded combined 64 KiB",
            "hang": "OSC acknowledgement timed out",
        }
        for mode, message in expected.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                prefix="daw-sc-runtime-failure-", dir=ROOT / "output"
            ) as temporary:
                directory = Path(temporary)
                executable, pid_file = self._fake_runtime(directory, mode)
                result, _ = run_session(saved_session(), directory, scsynth=str(executable),
                                        allow_files=("fake-runtime",))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(message.encode(), result.stderr)
                self._assert_pid_reaped(pid_file)

    def test_sigint_during_owned_runtime_readiness_reaps_server(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-sigint-", dir=ROOT / "output") as temporary:
            directory = Path(temporary)
            executable, pid_file = self._fake_runtime(directory, "hang")
            session_path = directory / "saved-session.json"
            session_path.write_text(json.dumps(saved_session()), encoding="utf-8")
            original = session_path.read_bytes()
            env = os.environ.copy()
            env["DAW_SCSYNTH"] = str(executable)
            process = subprocess.Popen(
                [str(DAW), "sc-session-play", str(session_path), "1", "0"],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not pid_file.exists() and process.poll() is None:
                    time.sleep(0.02)
                self.assertTrue(pid_file.exists(), "owned fake scsynth did not start before signal")
                owned_pid = int(pid_file.read_text(encoding="utf-8"))
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0, stderr.decode(errors="replace"))
                self.assertEqual(stdout, b"")
                self.assertEqual(session_path.read_bytes(), original)
                with self.assertRaises(ProcessLookupError):
                    os.kill(owned_pid, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)


    def test_owned_sc_child_death_fails_session_and_reaps_every_server(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-child-death-", dir=ROOT / "output") as temporary:
            directory = Path(temporary)
            session_path = directory / "saved-session.json"
            session_path.write_text(json.dumps(saved_session()), encoding="utf-8")
            original = session_path.read_bytes()
            env = os.environ.copy()
            env["DAW_SCSYNTH"] = str(SCSYNTH)
            process = subprocess.Popen(
                [str(DAW), "sc-session-play", str(session_path), "3", "0"],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            owned_pids = []
            try:
                ready = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    remaining = max(0.0, deadline - time.monotonic())
                    readable, _, _ = select.select([process.stderr], [], [], remaining)
                    if not readable:
                        break
                    line = process.stderr.readline()
                    if not line:
                        break
                    if b"DAW_SC_STREAM_READY" in line:
                        ready = True
                        break
                self.assertTrue(ready, "native player did not report its readiness marker")

                children = subprocess.run(
                    ["/usr/bin/pgrep", "-P", str(process.pid)],
                    capture_output=True, text=True, timeout=2, check=False,
                )
                self.assertEqual(children.returncode, 0, children.stderr)
                owned_pids = [int(line) for line in children.stdout.splitlines() if line.strip()]
                self.assertEqual(len(owned_pids), 2, f"expected two owned scsynth children: {owned_pids}")
                expected_name = Path(SCSYNTH).name
                for pid in owned_pids:
                    details = subprocess.run(
                        ["/bin/ps", "-p", str(pid), "-o", "comm="],
                        capture_output=True, text=True, timeout=2, check=False,
                    )
                    self.assertEqual(details.returncode, 0, details.stderr)
                    self.assertIn(expected_name, details.stdout.strip())

                time.sleep(0.15)
                os.kill(owned_pids[0], signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=20)
                self.assertNotEqual(process.returncode, 0, stderr.decode(errors="replace"))
                self.assertEqual(stdout, b"")
                self.assertIn(b"owned scsynth exited", stderr)
                self.assertEqual(session_path.read_bytes(), original)
                for pid in owned_pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                owned_pids = []  # Already reaped; do not act on reusable PID numbers.
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=3)
                # Cleanup is restricted to exact children recorded and verified above.
                for pid in owned_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()

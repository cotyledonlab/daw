"""Failure containment tests for the Audio Unit child wrapper."""
from __future__ import annotations

import pathlib
import platform
import json
import subprocess
import tempfile
import textwrap
import time
import unittest

from .probe import HOST, probe


def success_envelope():
    return {"schema_version": 1, "ok": True, "result": {
        "component": {"type": "aufx", "subtype": "lpas", "manufacturer": "appl", "name": "Apple Low Pass"},
        "sample_rate": 48000, "channels": 2, "frames": 4096, "block_frames": 256,
        "parameter": {"id": 0, "name": "Cutoff Frequency", "min": 10, "max": 23760,
                      "default": 6900, "unit": 8},
        "open_rms": 0.070968, "closed_rms": 0.002876, "restored_rms": 0.002876,
        "max_restore_error": 0.0, "state_bytes": 167, "restored_cutoff": 200,
        "input_callback_calls": 48, "resources_released": True}}


class ProbeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = pathlib.Path(self.temp.name) / "fake-host"

    def tearDown(self):
        self.temp.cleanup()

    def script(self, body):
        self.host.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        self.host.chmod(0o755)

    def test_success_and_structured_failure(self):
        self.script("import json\nprint(json.dumps(" + repr(success_envelope()) + "))\n")
        self.assertTrue(probe(host=self.host)["ok"])
        failure = {"schema_version": 1, "ok": False,
                   "error": {"code": "unsupported", "message": "not an effect"}}
        self.script("import json,sys\nprint(json.dumps(" + repr(failure) + "))\nsys.exit(2)\n")
        result = probe(host=self.host)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "unsupported")

    def test_malformed_and_success_with_bad_exit(self):
        self.script("print('{bad json')\n")
        self.assertEqual(probe(host=self.host)["error"]["code"], "probe_protocol")

    def test_corrupt_success_envelopes_are_rejected(self):
        mutations = (
            lambda result: result["component"].update(type="aumu"),
            lambda result: result["component"].update(name=""),
            lambda result: result["component"].update(name="x" * 2049),
            lambda result: result.update(sample_rate=44100),
            lambda result: result.update(input_callback_calls=4097),
            lambda result: result.update(state_bytes=65537),
            lambda result: result.update(open_rms=True),
            lambda result: result.update(open_rms=float("nan")),
            lambda result: result.update(open_rms=0.001),
            lambda result: result.update(restored_rms=0.1),
            lambda result: result.update(max_restore_error=1e-6),
            lambda result: result.update(restored_cutoff=201),
            lambda result: result["parameter"].update(unit=0),
            lambda result: result["parameter"].update(default=30000),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                envelope = success_envelope()
                mutate(envelope["result"])
                self.script("import json\nprint(" + repr(json.dumps(envelope)) + ")\n")
                response = probe(host=self.host)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "probe_protocol")
        self.script("import json\nprint(json.dumps(" + repr(success_envelope()) + "))\nraise SystemExit(1)\n")
        self.assertEqual(probe(host=self.host)["error"]["code"], "probe_protocol")

    def test_crash(self):
        self.script("import os,signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        self.assertEqual(probe(host=self.host)["error"]["code"], "probe_crashed")

    def test_timeout_kills_and_reaps_child(self):
        marker = pathlib.Path(self.temp.name) / "descendant-survived"
        self.script("import os,time\n"
                    "pid = os.fork()\n"
                    "if pid == 0:\n"
                    "    time.sleep(0.7)\n"
                    f"    open({str(marker)!r}, 'w').close()\n"
                    "    os._exit(0)\n"
                    "time.sleep(30)\n")
        started = time.monotonic()
        result = probe(host=self.host, timeout=0.15)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["error"]["code"], "probe_timeout")
        time.sleep(0.8)
        self.assertFalse(marker.exists(), "timed-out process group left a descendant running")

    def test_successful_leader_exit_still_cleans_detached_pipe_descendant(self):
        marker = pathlib.Path(self.temp.name) / "detached-descendant-survived"
        payload = json.dumps(success_envelope())
        self.script("import os,time\n"
                    "pid = os.fork()\n"
                    "if pid == 0:\n"
                    "    os.close(1); os.close(2)\n"
                    "    time.sleep(0.7)\n"
                    f"    open({str(marker)!r}, 'w').close()\n"
                    "    os._exit(0)\n"
                    f"print({payload!r})\n")
        response = probe(host=self.host)
        self.assertTrue(response["ok"], response)
        time.sleep(0.8)
        self.assertFalse(marker.exists(), "successful leader left a descendant process running")

    def test_oversized_stdout_and_stderr_are_bounded(self):
        self.script("import sys\nsys.stdout.write('x' * (300 * 1024))\n")
        self.assertEqual(probe(host=self.host)["error"]["code"], "probe_output_too_large")
        self.script("import sys\nsys.stderr.write('x' * (70 * 1024))\n")
        self.assertEqual(probe(host=self.host)["error"]["code"], "probe_output_too_large")

    def test_identity_validation_and_no_success_from_unavailable_host(self):
        self.assertEqual(probe("bad", "identity", "value", host=self.host)["error"]["code"], "invalid_identity")
        self.assertEqual(probe("aufx", "zzzz", "appl", host=self.host)["error"]["code"], "unsupported_identity")
        self.assertEqual(probe(host=self.host.with_name("missing"))["error"]["code"], "host_unavailable")


@unittest.skipUnless(platform.system() == "Darwin", "requires macOS AudioToolbox")
class AppleAudioUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HOST.is_file():
            raise unittest.SkipTest("build native/au/host.cpp with python3 native/au/build.py first")

    def test_apple_lowpass_renders_restores_and_releases_repeatedly(self):
        for _ in range(3):
            response = probe()
            if (not response["ok"] and response.get("error", {}).get("code") == "au_error"
                    and "unavailable" in response["error"].get("message", "")):
                self.skipTest("Apple AULowpass is not registered on this macOS host")
            self.assertTrue(response["ok"], response)
            result = response["result"]
            self.assertEqual(result["component"]["subtype"], "lpas")
            self.assertEqual(result["sample_rate"], 48000)
            self.assertGreater(result["open_rms"], result["closed_rms"] * 5)
            self.assertLess(result["max_restore_error"], 1e-6)
            self.assertAlmostEqual(result["restored_cutoff"], 200, delta=0.01)
            self.assertTrue(result["resources_released"])

    def test_native_host_rejects_unsupported_identity(self):
        process = subprocess.run([str(HOST), "probe", "aufx", "zzzz", "appl"],
                                 capture_output=True, text=True, timeout=3, check=False)
        self.assertNotEqual(process.returncode, 0)
        envelope = json.loads(process.stdout)
        self.assertEqual(envelope["schema_version"], 1)
        self.assertFalse(envelope["ok"])
        self.assertIn("unsupported identity", envelope["error"]["message"])


if __name__ == "__main__":
    unittest.main()

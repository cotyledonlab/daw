"""Portable JSONL integration tests for supercollider.inspect."""
import json
import selectors
import struct
import subprocess
import unittest

from native.vst3.scan import ROOT


def pstring(value):
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return bytes((len(raw),)) + raw


def ugen(name, rate, inputs, outputs, special=0):
    result = bytearray(pstring(name))
    result.extend(struct.pack(">BiiH", rate, len(inputs), len(outputs), special))
    for source, index in inputs:
        result.extend(struct.pack(">ii", source, index))
    result.extend(bytes(outputs))
    return bytes(result)


def synthdef(*, constants=(0.0,), defaults=(440.0, 0.1, 0.0),
             names=(("freq", 0), ("gain", 1), ("out", 2)),
             control_outputs=(1, 1, 1), control_rates=None,
             other_ugens=None, variants=0):
    """Build the known four-UGen SCgf v2 program, with tweakable fields."""
    if control_rates is not None and len(control_rates) != len(control_outputs):
        raise ValueError("one control rate is required per output")
    ugens = [ugen("Control", 1, [],
                  control_outputs if control_rates is None else control_rates)]
    ugens.extend(other_ugens if other_ugens is not None else [
        ugen("SinOsc", 2, [(0, 0), (-1, 0)], [2]),
        ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
        ugen("Out", 2, [(0, 2), (2, 0), (2, 0)], []),
    ])
    body = bytearray(pstring("daw_sine_fixture"))
    body.extend(struct.pack(">i", len(constants)))
    body.extend(struct.pack(">" + "f" * len(constants), *constants))
    body.extend(struct.pack(">i", len(defaults)))
    body.extend(struct.pack(">" + "f" * len(defaults), *defaults))
    body.extend(struct.pack(">i", len(names)))
    for name, index in names:
        body.extend(pstring(name))
        body.extend(struct.pack(">i", index))
    body.extend(struct.pack(">i", len(ugens)))
    body.extend(b"".join(ugens))
    body.extend(struct.pack(">H", variants))
    return b"SCgf" + struct.pack(">iH", 2, 1) + body


class SuperColliderInspectTests(unittest.TestCase):
    def setUp(self):
        self.binary = ROOT / "target/debug/daw"
        if not self.binary.is_file():
            self.skipTest("build target/debug/daw before running integration tests")
        self.process = subprocess.Popen(
            [str(self.binary), "serve"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT,
        )
        self.sequence = 0
        session = {"schema_version": 1, "sample_rate": 48000, "tracks": [{
            "id": "preserved", "device": {"kind": "sine", "frequency_hz": 330.0,
                                                   "gain": 0.2},
        }]}
        self.call("session.replace", {"session": session})
        self.initial_state = self.state()

    def tearDown(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=3)
        self.process.stdout.close()
        if self.process.returncode != 0:
            stderr = self.process.stderr.read().decode(errors="replace")
            self.fail(f"daw serve exited {self.process.returncode}: {stderr}")
        self.process.stderr.close()

    def call(self, method, params=None):
        self.sequence += 1
        request = {"protocol_version": 1, "id": str(self.sequence), "method": method}
        if params is not None:
            request["params"] = params
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                self.fail("daw serve did not reply within 20 seconds")
        line = self.process.stdout.readline()
        self.assertTrue(line, "daw serve closed stdout before replying")
        response = json.loads(line)
        self.assertEqual(response.get("id"), str(self.sequence))
        return response

    def inspect(self, data):
        return self.call("supercollider.inspect", {"synthdef_hex": data.hex()})

    def state(self):
        return (self.call("session.inspect")["result"],
                self.call("transport.status")["result"])

    def assert_error(self, result, code):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error"]["code"], code)

    def test_reports_named_controls_and_preserves_session_revision_and_transport(self):
        before = self.state()
        result = self.inspect(synthdef())
        self.assertTrue(result["ok"], result)
        actual = result["result"]
        self.assertEqual(actual["name"], "daw_sine_fixture")
        self.assertEqual(actual["ugen_count"], 4)
        self.assertEqual([item["name"] for item in actual["controls"]],
                         ["freq", "gain", "out"])
        self.assertEqual([item["index"] for item in actual["controls"]], [0, 1, 2])
        self.assertAlmostEqual(actual["controls"][0]["default_values"][0], 440.0)
        self.assertAlmostEqual(actual["controls"][1]["default_values"][0], 0.1)
        self.assertAlmostEqual(actual["controls"][2]["default_values"][0], 0.0)
        self.assertEqual([item["initialization_rate"] for item in actual["controls"]],
                         [False, False, False])
        self.assertEqual(self.state(), before)

    def test_control_initialization_rate_is_reported_per_named_control(self):
        program = synthdef(control_rates=(0, 1, 1))
        result = self.inspect(program)
        self.assertTrue(result["ok"], result)
        controls = result["result"]["controls"]
        self.assertEqual([(item["name"], item["initialization_rate"]) for item in controls],
                         [("freq", True), ("gain", False), ("out", False)])
        self.assert_state_unchanged()

    def test_array_controls_report_each_default_at_sorted_parameter_indices(self):
        program = synthdef(defaults=(220.0, 330.0, 0.25, 0.0),
                           names=(("gain", 2), ("freq", 0), ("out", 3)),
                           control_outputs=(1, 1, 1, 1))
        result = self.inspect(program)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["controls"], [
            {"name": "gain", "index": 2, "default_values": [0.25],
             "initialization_rate": False},
            {"name": "freq", "index": 0, "default_values": [220.0, 330.0],
             "initialization_rate": False},
            {"name": "out", "index": 3, "default_values": [0.0],
             "initialization_rate": False},
        ])
        self.assert_state_unchanged()

    def test_named_array_is_initialization_rate_if_any_slot_is(self):
        program = synthdef(defaults=(220.0, 330.0, 0.25, 0.0),
                           names=(("freq", 0), ("gain", 2), ("out", 3)),
                           control_outputs=(1, 1, 1, 1),
                           control_rates=(1, 0, 1, 1))
        result = self.inspect(program)
        self.assertTrue(result["ok"], result)
        controls = result["result"]["controls"]
        self.assertEqual([item["initialization_rate"] for item in controls],
                         [True, False, False])
        self.assert_state_unchanged()

    def test_parameter_shape_hex_type_and_decoded_size_are_invalid_params(self):
        for params in (None, {}, {"synthdef_hex": "00", "extra": 1},
                       {"synthdef_hex": 12},
                       {"synthdef_hex": ""}, {"synthdef_hex": "0"}, {"synthdef_hex": "zz"},
                       {"synthdef_hex": "é"},
                       {"synthdef_hex": "x" * (2 * 65537)}):
            with self.subTest(params=str(params)[:50]):
                response = self.call("supercollider.inspect", params)
                self.assert_error(response, "invalid_params")
                self.assert_state_unchanged()

    def assert_state_unchanged(self):
        self.assertEqual(self.state(), self.initial_state)

    def test_malformed_programs_are_runtime_errors(self):
        base = synthdef()
        cases = {
            **{f"truncated_{length}": base[:length]
               for length in range(1, len(base))},
            "trailing": base + b"x",
            "wrong_version": base[:4] + struct.pack(">i", 3) + base[8:],
            "multiple_definitions": base[:8] + struct.pack(">H", 2) + base[10:] + base[10:],
            "variant": base[:-2] + struct.pack(">H", 1),
            "nonfinite_constant": synthdef(constants=(float("nan"),)),
            "nonfinite_default": synthdef(defaults=(float("inf"), 0.1, 0.0)),
            "bad_control_span": synthdef(names=(("freq", 0), ("gain", 3), ("out", 2))),
            "bad_control_ugen_span": synthdef(control_outputs=(1, 1, 1, 1)),
            "bad_output_rate": synthdef(control_outputs=(1, 4, 1)),
            "duplicate_name": synthdef(names=(("freq", 0), ("freq", 1), ("out", 2))),
            "duplicate_index": synthdef(names=(("freq", 0), ("gain", 0), ("out", 2))),
            "empty_name": synthdef(names=(("", 0), ("gain", 1), ("out", 2))),
            "nul_name": synthdef(names=(("freq\0bad", 0), ("gain", 1), ("out", 2))),
            "invalid_utf8_name": synthdef(names=((b"\xff", 0), ("gain", 1), ("out", 2))),
            "bad_rate": synthdef(other_ugens=[
                ugen("SinOsc", 4, [(0, 0), (-1, 0)], [2]),
                ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
                ugen("Out", 2, [(0, 2), (2, 0), (2, 0)], []),
            ]),
            "bad_constant_index": synthdef(other_ugens=[ugen("SinOsc", 2, [(-1, 1)], [2])]),
            "bad_output_index": synthdef(other_ugens=[ugen("SinOsc", 2, [(0, 3)], [2])]),
            "self_reference": synthdef(other_ugens=[ugen("SinOsc", 2, [(1, 0)], [2])]),
            "bad_reference": synthdef(other_ugens=[
                ugen("SinOsc", 2, [(8, 0), (-1, 0)], [2]),
                ugen("BinaryOpUGen", 2, [(1, 0), (0, 1)], [2], special=2),
                ugen("Out", 2, [(0, 2), (2, 0), (2, 0)], []),
            ]),
        }
        for label, program in cases.items():
            with self.subTest(label=label):
                self.assert_error(self.inspect(program), "runtime_error")
                self.assert_state_unchanged()


if __name__ == "__main__":
    unittest.main()

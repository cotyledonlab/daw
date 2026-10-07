"""In-process ctypes checks for the live VST3 bridge; no audio device is opened."""
import ctypes
import math
import os
import struct
import threading
import unittest
from unittest.mock import patch

from native.vst3.scan import ROOT


LIBRARY = ROOT / "output/vst3-spike/libdaw-vst3.dylib"
FIXTURE = ROOT / "output/vst3-spike/DawTestGain.vst3"
CID = b"DA01234567894ABCBDEF0123456789AB"


def config(*, component_state=b"", controller_state=b"", parameter_id=0,
           base=0.2, points=((32, 0.8),)):
    """Encode the private live.cpp config with one gain parameter by default."""
    payload = bytearray(struct.pack("<I", 0x34565744))
    for state in (component_state, controller_state):
        payload.extend(struct.pack("<I", len(state)))
        payload.extend(state)
    payload.extend(struct.pack("<I", 1))
    payload.extend(struct.pack("<IdI", parameter_id, base, len(points)))
    for frame, value in points:
        payload.extend(struct.pack("<Id", frame, value))
    payload.extend(struct.pack("<I", 0))  # no audio payload
    return bytes(payload)


def load_library():
    library = ctypes.CDLL(str(LIBRARY))
    library.daw_vst3_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                        ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                                        ctypes.c_double, ctypes.c_char_p, ctypes.c_size_t]
    library.daw_vst3_create.restype = ctypes.c_void_p
    library.daw_vst3_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double),
                                         ctypes.c_uint32, ctypes.c_uint64]
    library.daw_vst3_process.restype = ctypes.c_int
    library.daw_vst3_set_parameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_double]
    library.daw_vst3_set_parameter.restype = ctypes.c_int
    library.daw_vst3_destroy.argtypes = [ctypes.c_void_p]
    library.daw_vst3_destroy.restype = ctypes.c_int
    return library


class LiveBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not LIBRARY.is_file() or not FIXTURE.is_dir():
            raise unittest.SkipTest("build native/vst3 live bridge and fixture first")
        cls.library = load_library()

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "DAW_VST3_FIXTURE_NO_EVENTS": "1",
            "DAW_VST3_FIXTURE_REALTIME": "1",
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def create(self, bundle=FIXTURE, cid=CID, blob=None):
        blob = config() if blob is None else blob
        data = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        error = ctypes.create_string_buffer(512)
        handle = self.library.daw_vst3_create(os.fsencode(bundle), cid, data, len(blob),
                                              120.0, error, len(error))
        return handle, error.value.decode("utf-8", errors="replace")

    def test_realtime_automation_is_applied_at_exact_frame_and_destroyed(self):
        handle, error = self.create()
        self.assertTrue(handle, error)
        try:
            samples = (ctypes.c_double * 128)(*([1.0] * 128))
            self.assertEqual(self.library.daw_vst3_process(handle, samples, 64, 0), 0)
            expected = [0.2] * 32 + [0.8] * 32
            for channel in (list(samples)[::2], list(samples)[1::2]):
                for actual, target in zip(channel, expected):
                    self.assertAlmostEqual(actual, target, places=7)
            later = (ctypes.c_double * 16)(*([1.0] * 16))
            self.assertEqual(self.library.daw_vst3_process(handle, later, 8, 64), 0)
            for actual in later:
                self.assertAlmostEqual(actual, 0.8, places=7)
        finally:
            self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

    def test_invalid_state_and_parameter_identity_return_errors(self):
        cases = (
            (CID, config(component_state=b"\x00"), "state"),
            (CID, config(parameter_id=999), "parameter"),
            (b"0" * 32, config(), "CID"),
        )
        for cid, blob, expected in cases:
            with self.subTest(expected=expected):
                handle, error = self.create(cid=cid, blob=blob)
                self.assertFalse(handle)
                self.assertIn(expected.lower(), error.lower())

    def test_saved_parameter_change_applies_at_next_block_and_persists(self):
        handle, error = self.create(blob=config(points=()))
        self.assertTrue(handle, error)
        try:
            initial = (ctypes.c_double * 32)(*([1.0] * 32))
            self.assertEqual(self.library.daw_vst3_process(handle, initial, 16, 0), 0)
            self.assertTrue(all(abs(value - 0.2) < 1e-7 for value in initial))
            self.assertEqual(self.library.daw_vst3_set_parameter(handle, 0, 0.7), 0)
            for position in (16, 32):
                changed = (ctypes.c_double * 32)(*([1.0] * 32))
                self.assertEqual(self.library.daw_vst3_process(handle, changed, 16,
                                                               position), 0)
                self.assertTrue(all(abs(value - 0.7) < 1e-7 for value in changed))
        finally:
            self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

    def test_invalid_or_automated_parameter_updates_leave_base_unchanged(self):
        handle, error = self.create(blob=config(points=()))
        self.assertTrue(handle, error)
        try:
            for parameter_id, value in ((99, 0.8), (0, -0.1), (0, 1.1),
                                        (0, float("nan")), (0, float("inf"))):
                self.assertEqual(self.library.daw_vst3_set_parameter(handle,
                                                                     parameter_id,
                                                                     value), -1)
            samples = (ctypes.c_double * 32)(*([1.0] * 32))
            self.assertEqual(self.library.daw_vst3_process(handle, samples, 16, 0), 0)
            self.assertTrue(all(abs(value - 0.2) < 1e-7 for value in samples))
        finally:
            self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

        handle, error = self.create(blob=config(points=((20, 0.8),)))
        self.assertTrue(handle, error)
        try:
            self.assertEqual(self.library.daw_vst3_set_parameter(handle, 0, 0.7), -1)
            samples = (ctypes.c_double * 32)(*([1.0] * 32))
            self.assertEqual(self.library.daw_vst3_process(handle, samples, 16, 0), 0)
            self.assertTrue(all(abs(value - 0.2) < 1e-7 for value in samples))
        finally:
            self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

    def test_repeated_create_process_destroy_lifecycle(self):
        for _ in range(10):
            handle, error = self.create()
            self.assertTrue(handle, error)
            samples = (ctypes.c_double * 32)(*([0.5] * 32))
            self.assertEqual(self.library.daw_vst3_process(handle, samples, 16, 0), 0)
            for actual in samples:
                self.assertAlmostEqual(actual, 0.1, places=7)
            self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

    def test_process_and_destroy_are_rejected_off_owner_thread(self):
        handle, error = self.create()
        self.assertTrue(handle, error)
        samples = (ctypes.c_double * 32)(*([0.5] * 32))
        results = []

        def wrong_thread_calls():
            results.extend((self.library.daw_vst3_process(handle, samples, 16, 0),
                            self.library.daw_vst3_destroy(handle)))

        worker = threading.Thread(target=wrong_thread_calls)
        worker.start()
        worker.join()
        self.assertEqual(results, [-1, -1])
        self.assertEqual(self.library.daw_vst3_process(handle, samples, 16, 0), 0)
        self.assertEqual(self.library.daw_vst3_destroy(handle), 0)

    @unittest.skipUnless(os.environ.get("DAW_VST3_EFFECT") and os.environ.get("DAW_VST3_CID"),
                         "set DAW_VST3_EFFECT and DAW_VST3_CID for an installed effect")
    def test_installed_effect_processes_realtime_blocks_and_releases_repeatedly(self):
        bundle = os.environ["DAW_VST3_EFFECT"]
        cid = os.environ["DAW_VST3_CID"].encode("ascii")
        for _ in range(3):
            handle, error = self.create(bundle=bundle, cid=cid,
                                        blob=config(parameter_id=48, base=0.2,
                                                    points=((8192, 0.8),)))
            self.assertTrue(handle, error)
            peak = 0.0
            try:
                for block in range(64):
                    samples = (ctypes.c_double * 512)()
                    for frame in range(256):
                        value = math.sin(2 * math.pi * (block * 256 + frame) * 440 / 48000)
                        samples[frame * 2] = samples[frame * 2 + 1] = value
                    self.assertEqual(self.library.daw_vst3_process(handle, samples, 256,
                                                                   block * 256), 0)
                    for value in samples:
                        self.assertTrue(math.isfinite(value))
                        peak = max(peak, abs(value))
            finally:
                self.assertEqual(self.library.daw_vst3_destroy(handle), 0)
            self.assertGreater(peak, 1e-6)


if __name__ == "__main__":
    unittest.main()

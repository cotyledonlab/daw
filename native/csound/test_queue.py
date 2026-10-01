"""Bounded producer/consumer ABI checks for the Csound stream queue."""
from __future__ import annotations

import ctypes
import mmap
import os
import platform
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "output" / "csound-stream" / "libdaw-csound-queue.dylib"
ABI_SIZE = 192 + 64 * 64 * 2 * 4
SAMPLES_OFFSET = 192
SLOT_FLOATS = 128
NONCE = 0x123456789ABCDEF
AVAILABLE = sys.platform == "darwin" and platform.machine() == "arm64" and LIBRARY.is_file()


@unittest.skipUnless(AVAILABLE, "requires the built macOS Csound queue dylib")
class CsoundQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = ctypes.CDLL(str(LIBRARY))
        cls.lib.daw_sc_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_uint64,
                                                ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.daw_sc_queue_create.restype = ctypes.c_int
        cls.lib.daw_sc_queue_open.argtypes = [ctypes.c_char_p, ctypes.c_uint64,
                                              ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.daw_sc_queue_open.restype = ctypes.c_void_p
        cls.lib.daw_sc_queue_pop.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)]
        cls.lib.daw_sc_queue_pop.restype = ctypes.c_int
        cls.lib.daw_sc_queue_close.argtypes = [ctypes.c_void_p]
        cls.lib.daw_sc_queue_close.restype = None
        cls.lib.daw_cs_queue_open.argtypes = [ctypes.c_char_p, ctypes.c_uint64,
                                              ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.daw_cs_queue_open.restype = ctypes.c_void_p
        cls.lib.daw_cs_queue_push.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
        cls.lib.daw_cs_queue_push.restype = ctypes.c_int
        cls.lib.daw_cs_queue_close.argtypes = [ctypes.c_void_p]
        cls.lib.daw_cs_queue_close.restype = None

    def setUp(self):
        self.output = ROOT / "output" / "csound-stream"
        self.output.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="queue-test-", dir=self.output)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue"

    def create(self):
        error = ctypes.create_string_buffer(256)
        result = self.lib.daw_sc_queue_create(os.fsencode(self.path), NONCE, error, len(error))
        self.assertEqual(result, 0, error.value.decode("utf-8", "replace"))

    def open_producer(self, path=None, nonce=NONCE):
        error = ctypes.create_string_buffer(256)
        handle = self.lib.daw_cs_queue_open(os.fsencode(path or self.path), nonce, error, len(error))
        return handle, error.value.decode("utf-8", "replace")

    def open_consumer(self):
        error = ctypes.create_string_buffer(256)
        handle = self.lib.daw_sc_queue_open(os.fsencode(self.path), NONCE, error, len(error))
        self.assertTrue(handle, error.value.decode("utf-8", "replace"))
        return handle

    def push(self, handle, values):
        samples = (ctypes.c_double * SLOT_FLOATS)(*values)
        return self.lib.daw_cs_queue_push(handle, samples)

    def test_capacity_full_reclaim_stereo_order_and_float_conversion(self):
        self.create()
        producer, message = self.open_producer()
        self.assertTrue(producer, message)
        consumer = self.open_consumer()
        try:
            output = (ctypes.c_float * SLOT_FLOATS)()
            self.assertEqual(self.lib.daw_sc_queue_pop(consumer, output), 0)
            for block in range(64):
                values = [block + i / 8 for i in range(SLOT_FLOATS)]
                values[0], values[1] = 3.0, -3.0  # headroom must be preserved
                self.assertEqual(self.push(producer, values), 1)
            self.assertEqual(self.push(producer, [0.25] * SLOT_FLOATS), 0)
            self.assertEqual(struct.unpack_from("<I", self.path.read_bytes(), 68)[0], 0)
            for block in range(64):
                self.assertEqual(self.lib.daw_sc_queue_pop(consumer, output), 1)
                expected = [block + i / 8 for i in range(SLOT_FLOATS)]
                expected[0], expected[1] = 3.0, -3.0
                self.assertEqual(list(output), [ctypes.c_float(x).value for x in expected])
            self.assertEqual(self.lib.daw_sc_queue_pop(consumer, output), 0)
            self.assertEqual(self.push(producer, [0.125, -0.25] * 64), 1)
            self.assertEqual(self.lib.daw_sc_queue_pop(consumer, output), 1)
            self.assertEqual(list(output), [0.125, -0.25] * 64)
        finally:
            self.lib.daw_sc_queue_close(consumer)
            self.lib.daw_cs_queue_close(producer)

    def test_producer_lease_is_exclusive_and_released_on_close(self):
        self.create()
        first, message = self.open_producer()
        self.assertTrue(first, message)
        second, message = self.open_producer()
        self.assertFalse(second)
        self.lib.daw_cs_queue_close(first)
        reopened, message = self.open_producer()
        self.assertTrue(reopened, message)
        self.lib.daw_cs_queue_close(reopened)

    def test_wrong_nonce_symlink_permissions_and_exact_size_are_rejected(self):
        self.create()
        handle, message = self.open_producer(nonce=NONCE + 1)
        self.assertFalse(handle)
        self.assertIn("nonce", message.lower())

        link = Path(self.temp.name) / "link"
        link.symlink_to(self.path)
        handle, _ = self.open_producer(path=link)
        self.assertFalse(handle)

        self.path.chmod(0o644)
        handle, _ = self.open_producer()
        self.assertFalse(handle)
        self.path.chmod(0o600)

        with self.path.open("ab") as target:
            target.write(b"x")
        handle, _ = self.open_producer()
        self.assertFalse(handle)

    def test_nonfinite_and_float_overflow_fail_without_publishing(self):
        self.create()
        producer, message = self.open_producer()
        self.assertTrue(producer, message)
        try:
            initial = self.path.read_bytes()
            for bad in (float("nan"), float("inf"), -float("inf"), 1e300, -1e300):
                values = [0.0] * SLOT_FLOATS
                values[73] = bad
                self.assertEqual(self.push(producer, values), -1)
                raw = self.path.read_bytes()
                self.assertEqual(struct.unpack_from("<I", raw, 64)[0], 0)
                self.assertEqual(struct.unpack_from("<I", raw, 68)[0], 0)
                self.assertEqual(raw[SAMPLES_OFFSET:], initial[SAMPLES_OFFSET:])
        finally:
            self.lib.daw_cs_queue_close(producer)


if __name__ == "__main__":
    unittest.main()

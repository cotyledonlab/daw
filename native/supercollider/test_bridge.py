"""Bounded ABI checks for the macOS shared-memory queue consumer."""
from __future__ import annotations

import ctypes
import mmap
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "output" / "sc-stream" / "libdaw-sc-queue.dylib"
ABI_SIZE = 192 + 64 * 64 * 2 * 4
SAMPLES_OFFSET = 192
SLOT_FLOATS = 64 * 2
MAGIC = 0x44534331
NONCE = 0x123456789ABCDEF
AVAILABLE = sys.platform == "darwin" and LIBRARY.is_file()


def make_queue(path: Path, *, nonce: int = NONCE) -> mmap.mmap:
    """Create the exact v1 shared-memory image before calling the C ABI."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.ftruncate(fd, ABI_SIZE)
        mapping = mmap.mmap(fd, ABI_SIZE, access=mmap.ACCESS_WRITE)
    finally:
        os.close(fd)
    struct.pack_into("<6IQ", mapping, 0, MAGIC, 1, 48000, 64, 2, 64, nonce)
    # Atomics begin zeroed by the new file: written@64, fault@68,
    # producer@72, read@128, consumer@132.
    return mapping


@unittest.skipUnless(AVAILABLE, "requires the built macOS queue dylib")
class QueueBridgeTests(unittest.TestCase):
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
        cls.lib.daw_sc_queue_fault.argtypes = [ctypes.c_void_p]
        cls.lib.daw_sc_queue_fault.restype = ctypes.c_uint32
        cls.lib.daw_sc_queue_close.argtypes = [ctypes.c_void_p]
        cls.lib.daw_sc_queue_close.restype = None

    def open_queue(self, path: Path, nonce: int = NONCE):
        error = ctypes.create_string_buffer(256)
        handle = self.lib.daw_sc_queue_open(os.fsencode(path), nonce, error, len(error))
        return handle, error.value.decode("utf-8", "replace")

    def create_queue(self, path: bytes | Path, nonce: int = NONCE):
        error = ctypes.create_string_buffer(256)
        encoded = os.fsencode(path) if isinstance(path, Path) else path
        result = self.lib.daw_sc_queue_create(encoded, nonce, error, len(error))
        return result, error.value.decode("utf-8", "replace")

    def test_create_initializes_exact_header_and_open_release_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-create-") as temp:
            path = Path(temp) / "queue"
            result, message = self.create_queue(path)
            self.assertEqual(result, 0, message)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_size, ABI_SIZE)

            with path.open("rb") as source:
                raw = source.read()
            self.assertEqual(
                struct.unpack_from("<6IQ", raw),
                (MAGIC, 1, 48000, 64, 2, 64, NONCE),
            )
            for offset in (64, 68, 72, 128, 132):
                self.assertEqual(struct.unpack_from("<I", raw, offset)[0], 0)
            self.assertFalse(any(raw[SAMPLES_OFFSET:]))

            handle, message = self.open_queue(path)
            self.assertTrue(handle, message)
            self.lib.daw_sc_queue_close(handle)
            reopened, message = self.open_queue(path)
            self.assertTrue(reopened, message)
            self.lib.daw_sc_queue_close(reopened)

    def test_create_rejects_bad_paths_and_nonce_without_creating_files(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-create-") as temp:
            relative = b"relative-queue"
            result, message = self.create_queue(relative)
            self.assertEqual(result, -1)
            self.assertIn("invalid queue path or nonce", message)
            self.assertFalse(Path(relative.decode()).exists())

            path = Path(temp) / "zero-nonce"
            result, message = self.create_queue(path, 0)
            self.assertEqual(result, -1)
            self.assertIn("invalid queue path or nonce", message)
            self.assertFalse(path.exists())

    def test_create_refuses_existing_output_without_modifying_it(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-create-") as temp:
            path = Path(temp) / "existing"
            original = b"preserve this existing output"
            path.write_bytes(original)
            result, message = self.create_queue(path)
            self.assertEqual(result, -1)
            self.assertIn("exclusively create", message)
            self.assertEqual(path.read_bytes(), original)

    def test_rejects_nonce_abi_and_permissions(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-bridge-") as temp:
            path = Path(temp) / "queue"
            mapping = make_queue(path)
            try:
                handle, message = self.open_queue(path, NONCE + 1)
                self.assertFalse(handle)
                self.assertIn("nonce", message)

                struct.pack_into("<I", mapping, 4, 99)
                handle, message = self.open_queue(path)
                self.assertFalse(handle)
                self.assertIn("ABI or nonce", message)
                struct.pack_into("<I", mapping, 4, 1)
            finally:
                mapping.close()

            path.chmod(0o644)
            handle, message = self.open_queue(path)
            self.assertFalse(handle)
            self.assertIn("0600", message)

    def test_consumer_lease_rejects_second_reader_and_reopens_after_close(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-bridge-") as temp:
            path = Path(temp) / "queue"
            mapping = make_queue(path)
            try:
                first, message = self.open_queue(path)
                self.assertTrue(first, message)
                second, message = self.open_queue(path)
                self.assertFalse(second)
                self.assertIn("already has a consumer", message)
                self.lib.daw_sc_queue_close(first)
                reopened, message = self.open_queue(path)
                self.assertTrue(reopened, message)
                self.lib.daw_sc_queue_close(reopened)
            finally:
                mapping.close()

    def test_pcm_order_empty_pop_and_uint32_wraparound(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-bridge-") as temp:
            path = Path(temp) / "queue"
            mapping = make_queue(path)
            try:
                handle, message = self.open_queue(path)
                self.assertTrue(handle, message)
                output = (ctypes.c_float * SLOT_FLOATS)()
                self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), 0)

                for block in range(2):
                    slot = SAMPLES_OFFSET + block * SLOT_FLOATS * 4
                    values = [float(block * 1000 + i) for i in range(SLOT_FLOATS)]
                    struct.pack_into("<128f", mapping, slot, *values)
                struct.pack_into("<I", mapping, 64, 2)
                for block in range(2):
                    self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), 1)
                    self.assertEqual(list(output), [float(block * 1000 + i) for i in range(SLOT_FLOATS)])
                self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), 0)

                # One published block spans UINT32_MAX -> 0; slot selection wraps modulo 64.
                max_u32 = 0xFFFFFFFF
                struct.pack_into("<I", mapping, 64, max_u32)
                struct.pack_into("<I", mapping, 128, max_u32)
                slot = SAMPLES_OFFSET + (max_u32 % 64) * SLOT_FLOATS * 4
                values = [float(5000 + i) for i in range(SLOT_FLOATS)]
                struct.pack_into("<128f", mapping, slot, *values)
                struct.pack_into("<I", mapping, 64, 0)
                self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), 1)
                self.assertEqual(list(output), values)
                self.assertEqual(struct.unpack_from("<I", mapping, 128)[0], 0)
                self.lib.daw_sc_queue_close(handle)
            finally:
                mapping.close()

    def test_fault_and_impossible_queue_distance_fail(self):
        with tempfile.TemporaryDirectory(prefix="daw-sc-bridge-") as temp:
            path = Path(temp) / "queue"
            mapping = make_queue(path)
            try:
                handle, message = self.open_queue(path)
                self.assertTrue(handle, message)
                output = (ctypes.c_float * SLOT_FLOATS)()
                struct.pack_into("<I", mapping, 68, 1)
                self.assertEqual(self.lib.daw_sc_queue_fault(handle), 1)
                self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), -1)

                struct.pack_into("<I", mapping, 68, 0)
                struct.pack_into("<I", mapping, 128, 0)
                struct.pack_into("<I", mapping, 64, 65)
                self.assertEqual(self.lib.daw_sc_queue_pop(handle, output), -1)
                self.lib.daw_sc_queue_close(handle)
            finally:
                mapping.close()


if __name__ == "__main__":
    unittest.main()

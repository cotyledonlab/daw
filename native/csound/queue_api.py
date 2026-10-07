"""Explicit ctypes signatures for the tested macOS arm64 fixed queue bridge."""
import ctypes as C
from pathlib import Path
import platform


def bind_queue(library):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Queue ABI is verified only on macOS arm64')
    if not Path(library).is_absolute() or not Path(library).is_file():
        raise ValueError('Queue bridge must be an existing absolute library')
    api = C.CDLL(str(library))
    for name, result, args in (
        ('daw_sc_queue_create', C.c_int, [C.c_char_p, C.c_uint64, C.c_char_p, C.c_size_t]),
        ('daw_sc_queue_open', C.c_void_p, [C.c_char_p, C.c_uint64, C.c_char_p, C.c_size_t]),
        ('daw_cs_queue_open', C.c_void_p, [C.c_char_p, C.c_uint64, C.c_char_p, C.c_size_t]),
        ('daw_sc_queue_pop', C.c_int, [C.c_void_p, C.POINTER(C.c_float)]),
        ('daw_cs_queue_push', C.c_int, [C.c_void_p, C.POINTER(C.c_double)]),
        ('daw_sc_queue_fault', C.c_uint32, [C.c_void_p]),
        ('daw_sc_queue_written', C.c_uint32, [C.c_void_p]),
        ('daw_sc_queue_close', None, [C.c_void_p]),
        ('daw_cs_queue_close', None, [C.c_void_p]),
    ):
        fn = getattr(api, name)
        fn.restype, fn.argtypes = result, args
    return api

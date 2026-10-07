#!/usr/bin/env python3
"""Owned libpd block/message proof. No session adapter, transport or hardware I/O."""
from __future__ import annotations

import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

FIXTURE = Path(__file__).with_name('block_fixture.pd')
ABSTRACTION = Path(__file__).with_name('daw-offset.pd')
RATE, BLOCK, FRAMES, CONTROL_FRAME = 48000, 64, 49152, 24576
MAX_LOG = 65536
FLOAT_HOOK = C.CFUNCTYPE(None, C.c_char_p, C.c_float)
PRINT_HOOK = C.CFUNCTYPE(None, C.c_char_p)


def _bind(library):
    # Public libpd 0.16.1 API uses float arguments/buffers regardless of Pd's
    # internal precision. No internal Pd structures cross this boundary.
    api = C.CDLL(str(library))
    signatures = {
        'libpd_init': (C.c_int, []),
        'libpd_blocksize': (C.c_int, []),
        'libpd_new_instance': (C.c_void_p, []),
        'libpd_set_instance': (None, [C.c_void_p]),
        'libpd_free_instance': (None, [C.c_void_p]),
        'libpd_num_instances': (C.c_int, []),
        'libpd_clear_search_path': (None, []),
        'libpd_add_to_search_path': (None, [C.c_char_p]),
        'libpd_init_audio': (C.c_int, [C.c_int, C.c_int, C.c_int]),
        'libpd_openfile': (C.c_void_p, [C.c_char_p, C.c_char_p]),
        'libpd_closefile': (None, [C.c_void_p]),
        'libpd_getdollarzero': (C.c_int, [C.c_void_p]),
        'libpd_bind': (C.c_void_p, [C.c_char_p]),
        'libpd_unbind': (None, [C.c_void_p]),
        'libpd_float': (C.c_int, [C.c_char_p, C.c_float]),
        'libpd_start_message': (C.c_int, [C.c_int]),
        'libpd_add_float': (None, [C.c_float]),
        'libpd_finish_message': (C.c_int, [C.c_char_p, C.c_char_p]),
        'libpd_process_float': (C.c_int, [C.c_int, C.POINTER(C.c_float), C.POINTER(C.c_float)]),
        'libpd_set_floathook': (None, [FLOAT_HOOK]),
        'libpd_set_printhook': (None, [PRINT_HOOK]),
    }
    for name, (result, arguments) in signatures.items():
        fn = getattr(api, name)
        fn.restype, fn.argtypes = result, arguments
    return api


def _dsp(api, enabled):
    if api.libpd_start_message(1):
        raise RuntimeError('libpd DSP message allocation failed')
    api.libpd_add_float(float(enabled))
    if api.libpd_finish_message(b'pd', b'dsp'):
        raise RuntimeError('libpd DSP message failed')


def _measure(samples):
    crossings = [i for i in range(1, len(samples)) if samples[i-1] <= 0 < samples[i]]
    if len(crossings) < 3:
        raise RuntimeError('libpd fixture has no measurable tone')
    return {'frequency_hz': (len(crossings)-1)*RATE/(crossings[-1]-crossings[0]),
            'rms': math.sqrt(sum(v*v for v in samples)/len(samples)),
            'peak': max(abs(v) for v in samples)}


def _pass(api, fixture):
    instance = api.libpd_new_instance()
    if not instance:
        raise RuntimeError('libpd requires a MULTI=true build')
    patch = None
    receivers = []
    seen, messages = {}, bytearray()
    overflow = False

    @FLOAT_HOOK
    def on_float(name, value):
        seen[name.decode('utf-8', 'replace')] = float(value)

    @PRINT_HOOK
    def on_print(message):
        nonlocal overflow
        chunk = message or b''
        room = MAX_LOG-len(messages)
        messages.extend(chunk[:room])
        overflow |= len(chunk) > room

    # Callbacks remain strongly owned until after instance destruction. All
    # foreign calls and hooks execute on this child thread, never an audio callback.
    try:
        api.libpd_set_instance(instance)
        api.libpd_set_floathook(on_float)
        api.libpd_set_printhook(on_print)
        api.libpd_clear_search_path()
        api.libpd_add_to_search_path(os.fsencode(Path(fixture).parent.parent/'abstractions'))
        if api.libpd_init_audio(2, 2, RATE) or api.libpd_blocksize() != BLOCK:
            raise RuntimeError('libpd audio layout must be stereo with 64-frame blocks')
        patch = api.libpd_openfile(os.fsencode(Path(fixture).name), os.fsencode(Path(fixture).parent))
        if not patch or overflow or b"couldn't create" in messages or b'error:' in messages:
            raise RuntimeError('libpd patch/abstraction loading failed: '+messages.decode('utf-8', 'replace')[:512])
        prefix = str(api.libpd_getdollarzero(patch))
        if prefix == '0':
            raise RuntimeError('libpd patch identity missing')
        for name in ('frequency', 'amplitude'):
            receiver = api.libpd_bind(f'{prefix}-{name}-readback'.encode())
            if not receiver:
                raise RuntimeError('libpd readback binding failed')
            receivers.append(receiver)

        def controls(frequency, amplitude):
            for name, value in (('frequency', frequency), ('amplitude', amplitude)):
                if api.libpd_float(f'{prefix}-{name}'.encode(), value):
                    raise RuntimeError('libpd control receiver missing')
                if seen.get(f'{prefix}-{name}-readback') != C.c_float(value).value:
                    raise RuntimeError('libpd patch control readback mismatch')
            return {'frequency': frequency, 'amplitude': amplitude}

        before = controls(440., .1)
        if api.libpd_float(f'{prefix}-missing'.encode(), 1.) != -1:
            raise RuntimeError('missing libpd receiver did not reject')
        _dsp(api, True)
        inputs = (C.c_float*(BLOCK*2))(*([.01, -.02]*BLOCK))
        outputs = (C.c_float*(BLOCK*2))()
        digest = hashlib.sha256()
        left, difference = [], 0.
        changed = None
        for frame in range(0, FRAMES, BLOCK):
            if frame == CONTROL_FRAME:
                changed = controls(660., .05)
            if api.libpd_process_float(1, inputs, outputs):
                raise RuntimeError('libpd block processing failed')
            for i in range(BLOCK):
                a, b = outputs[2*i], outputs[2*i+1]
                if not math.isfinite(a) or not math.isfinite(b):
                    raise RuntimeError('nonfinite libpd output')
                difference = max(difference, abs((a-b)-.03))
                left.append(a-.01)
                digest.update(struct.pack('<ff', a, b))
            if overflow or b'error:' in messages:
                raise RuntimeError('libpd diagnostics exceeded limit or reported an error')
        if difference > 1e-6:
            raise RuntimeError('libpd input/channel routing mismatch')
        phases = [_measure(left[256:CONTROL_FRAME]), _measure(left[CONTROL_FRAME+256:])]
        return {'digest': digest.hexdigest(), 'phases': phases,
                'rms_ratio': phases[1]['rms']/phases[0]['rms'],
                'readbacks': [before, changed], 'blocks': FRAMES//BLOCK,
                'input_channel_error': difference, 'missing_receiver_rejected': True}
    finally:
        try:
            _dsp(api, False)
        finally:
            try:
                for receiver in receivers:
                    api.libpd_unbind(receiver)
                if patch:
                    api.libpd_closefile(patch)
            finally:
                api.libpd_free_instance(instance)


def _worker(library, result, fixture):
    api = _bind(library)
    if api.libpd_init() not in (0, -1):
        raise RuntimeError('libpd initialization failed')
    if api.libpd_num_instances() != 1:
        raise RuntimeError('unexpected initial libpd instances')
    first = _pass(api, fixture)
    if api.libpd_num_instances() != 1:
        raise RuntimeError('libpd instance leaked after first pass')
    second = _pass(api, fixture)
    if first != second or api.libpd_num_instances() != 1:
        raise RuntimeError('libpd recreation did not reproduce state/audio or leaked an instance')
    report = {'sample_rate': RATE, 'blocksize': BLOCK, 'frames': FRAMES,
              'control_frame': CONTROL_FRAME, 'passes': 2, 'recreate_equal': True,
              'resources_released': True, 'source_mode': 'diagnostic',
              'daw_transport': False, 'hardware_audio': False, 'audio': first}
    with open(result, 'x', encoding='utf-8') as stream:
        json.dump(report, stream, allow_nan=False)


def _validate(report):
    expected = {'sample_rate': RATE, 'blocksize': BLOCK, 'frames': FRAMES,
                'control_frame': CONTROL_FRAME, 'passes': 2, 'recreate_equal': True,
                'resources_released': True, 'source_mode': 'diagnostic',
                'daw_transport': False, 'hardware_audio': False}
    if not isinstance(report, dict) or set(report) != set(expected)|{'audio'}:
        raise RuntimeError('invalid libpd diagnostic report')
    for key, value in expected.items():
        if type(report[key]) is not type(value) or report[key] != value:
            raise RuntimeError('invalid libpd diagnostic '+key)
    audio = report['audio']
    if not isinstance(audio, dict) or set(audio) != {'digest','phases','rms_ratio','readbacks','blocks','input_channel_error','missing_receiver_rejected'}:
        raise RuntimeError('invalid libpd audio evidence')
    if (type(audio['blocks']) is not int or audio['blocks'] != FRAMES//BLOCK
            or audio['missing_receiver_rejected'] is not True
            or not isinstance(audio['digest'], str) or len(audio['digest']) != 64
            or any(c not in '0123456789abcdef' for c in audio['digest'])
            or audio['readbacks'] != [{'frequency':440.,'amplitude':.1},{'frequency':660.,'amplitude':.05}]):
        raise RuntimeError('invalid libpd block/control evidence')
    phases = audio['phases']
    if not isinstance(phases, list) or len(phases) != 2:
        raise RuntimeError('invalid libpd phase evidence')
    for phase, frequency, amplitude in zip(phases, (440,660), (.1,.05)):
        if not isinstance(phase, dict) or set(phase) != {'frequency_hz','rms','peak'}:
            raise RuntimeError('invalid libpd phase')
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in phase.values()):
            raise RuntimeError('invalid libpd signal values')
        if abs(phase['frequency_hz']-frequency)>2 or abs(phase['peak']-amplitude)>.001 or not amplitude*.68<phase['rms']<amplitude*.73:
            raise RuntimeError('libpd signal does not match fixture')
    for name, low, high in (('rms_ratio',.48,.52),('input_channel_error',0.,1e-6)):
        value=audio[name]
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
            raise RuntimeError('invalid libpd '+name)
    return report


def _drain(pipe, state):
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                return
            room = MAX_LOG - len(state['bytes'])
            state['bytes'].extend(chunk[:room])
            state['overflow'] |= len(chunk) > room
    except Exception as error:
        state['error'] = str(error)
    finally:
        pipe.close()


def _reap(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Confirm the macOS exit transition through waitpid; EPERM alone is
        # never proof of termination. A genuinely live denied group is failure.
        try:
            child.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
            raise
    child.wait(timeout=2)


def _reject_constant(value):
    raise ValueError('nonfinite JSON constant ' + value)


def run_probe(library=None, *, worker_script=None, timeout=15., fixture=None):
    if os.name != 'posix':
        raise RuntimeError('libpd diagnostic requires Unix process ownership')
    selected = library if library is not None else os.environ.get('DAW_LIBPD_LIBRARY')
    if not selected or not Path(selected).is_absolute() or not Path(selected).is_file():
        raise RuntimeError('DAW_LIBPD_LIBRARY must name an existing absolute library')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise RuntimeError('libpd diagnostic timeout must be finite, positive and at most 15 seconds')
    source_path = Path(fixture or FIXTURE)
    if not source_path.is_file():
        raise FileNotFoundError('libpd patch snapshot must be an existing regular file')
    with source_path.open('rb') as stream:
        source = stream.read(MAX_LOG + 1)
    if not source or len(source) > MAX_LOG or b'\0' in source:
        raise RuntimeError('invalid libpd patch snapshot')
    source.decode('utf-8')
    output = Path(__file__).resolve().parents[2] / 'output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='libpd-blocks-', dir=output) as directory:
        cwd = Path(directory)
        (cwd / 'patch').mkdir()
        (cwd / 'abstractions').mkdir()
        patch = cwd / 'patch' / 'fixture.pd'
        patch.write_bytes(source)
        (cwd / 'abstractions' / 'daw-offset.pd').write_bytes(ABSTRACTION.read_bytes())
        result = cwd / 'result.json'
        child = subprocess.Popen(
            [sys.executable, str(Path(worker_script or __file__).resolve()), '--worker',
             str(Path(selected).resolve()), str(result), str(patch)],
            cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        logs = [{'bytes': bytearray(), 'overflow': False} for _ in range(2)]
        readers = [threading.Thread(target=_drain, args=(pipe, state), daemon=True)
                   for pipe, state in zip((child.stdout, child.stderr), logs)]
        for reader in readers:
            reader.start()
        failure = None
        previous_handler = None
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGTERM, _cancel)
        started = time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic() - started >= timeout:
                    failure = 'libpd block worker timed out'
                    break
                if any(log['overflow'] or log.get('error') for log in logs):
                    failure = 'libpd diagnostics exceeded 64 KiB or failed'
                    break
                if result.exists() and (result.is_symlink() or not result.is_file()
                                        or result.stat().st_size > MAX_LOG):
                    failure = 'libpd report outside file limit'
                    break
                time.sleep(.01)
        finally:
            try:
                _reap(child)
            finally:
                for reader in readers:
                    reader.join(timeout=1)
                if previous_handler is not None:
                    signal.signal(signal.SIGTERM, previous_handler)
            if any(reader.is_alive() for reader in readers):
                failure = 'libpd worker escaped pipe cleanup'
        if failure:
            raise RuntimeError(failure)
        if any(log['overflow'] or log.get('error') for log in logs):
            raise RuntimeError('libpd diagnostics exceeded limit or failed')
        if child.returncode:
            detail = b'\n'.join(bytes(log['bytes']) for log in logs).decode('utf-8', 'replace')[:1024]
            raise RuntimeError(f'libpd block worker failed ({child.returncode}): {detail}')
        if result.is_symlink() or not result.is_file() or result.stat().st_size > MAX_LOG:
            raise RuntimeError('libpd report missing or outside file limit')
        try:
            report = json.loads(result.read_text(), parse_constant=_reject_constant)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('invalid libpd report JSON') from error
        return _validate(report)


def _cancel(_signum, _frame):
    raise KeyboardInterrupt('libpd diagnostic cancelled')


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == '--worker':
            _worker(*sys.argv[2:])
            return
        if len(sys.argv) != 1:
            raise RuntimeError('Usage: DAW_LIBPD_LIBRARY=/absolute/library python3 native/puredata/block_probe.py')
        print(json.dumps(run_probe(),allow_nan=False))
    except KeyboardInterrupt:
        print(json.dumps({'ok':False,'error':{'code':'cancelled','message':'libpd diagnostic cancelled'}}), file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(json.dumps({'ok':False,'error':{'code':'runtime_error','message':str(error)}}),file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

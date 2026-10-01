#!/usr/bin/env python3
"""Finite paced Csound queue proof. No hardware audio or DAW transport."""
from __future__ import annotations
import argparse
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.csound.queue_api import bind_queue
from native.csound.source_worker import validate_job

BRIDGE = ROOT / 'output/csound-stream/libdaw-csound-queue.dylib'
WORKER = Path(__file__).with_name('stream_worker.py')
MAX_LOG = 65536


def reap_worker(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as signal_error:
        # The observed macOS exit transition can still report poll()==None.
        # Only tolerate the denial after waitpid confirms terminal ownership.
        try:
            child.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
            raise signal_error
        return
    child.wait(timeout=2)


def fixture_job():
    return {'job_version': 1, 'sample_rate': 48000, 'source': {
        'program': Path(__file__).with_name('source_fixture.csd').read_text(),
        'duration_frames': 48000, 'gain': 1.0,
        'controls': [{'name': 'frequency', 'value': 440.0,
                      'points': [{'frame': 24576, 'value': 660.0}]},
                     {'name': 'amplitude', 'value': .1,
                      'points': [{'frame': 24576, 'value': .05}]}]}}


def read_json(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
            raise RuntimeError('Invalid or oversized stream report')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise RuntimeError('Oversized stream report')
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError('Malformed Csound stream report') from error
        if not isinstance(value, dict):
            raise RuntimeError('Stream report must be an object')
        return value
    finally:
        os.close(fd)


def measure(raw, start, length=12000):
    left = struct.unpack('<' + str(len(raw)//4) + 'f', raw)[::2][start:start+length]
    if len(left) != length or any(not math.isfinite(v) for v in left):
        raise RuntimeError('Invalid Csound stream signal')
    crossings = [i for i in range(1, len(left)) if left[i-1] <= 0 < left[i]]
    if len(crossings) < 3:
        raise RuntimeError('Csound stream produced no measurable sine')
    return {'frequency_hz': (len(crossings)-1)*48000/(crossings[-1]-crossings[0]),
            'rms': math.sqrt(sum(v*v for v in left)/len(left)),
            'peak': max(abs(v) for v in left)}


def probe(library, bridge=BRIDGE, *, job=None, worker=WORKER, paced=True,
          stall=False, cancel_after_blocks=None, kill_after_blocks=None):
    if not Path(library).is_absolute() or not Path(library).is_file():
        raise ValueError('Set an existing absolute DAW_CSOUND_LIBRARY')
    if not Path(worker).is_absolute() or not Path(worker).is_file():
        raise ValueError('Stream worker must be an existing absolute file')
    job = fixture_job() if job is None else job
    rate, source = validate_job(job)
    if rate != 48000:
        raise ValueError('The fixed queue requires 48000 Hz')
    payload = json.dumps(job, allow_nan=False).encode()
    if len(payload) > 1048576:
        raise ValueError('Stream job exceeds 1 MiB')
    api = bind_queue(bridge)
    nonce = secrets.randbits(64) or 1
    child = None
    consumer = None
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='csound-stream-', dir=output) as directory:
        directory = Path(directory)
        queue, ready, report = [directory / name for name in ('queue', 'ready.json', 'report.json')]
        snapshot, rc = directory / 'job.json', directory / 'empty.rc'
        snapshot.write_bytes(payload)
        rc.write_bytes(b'')
        (directory / 'empty-opcodes').mkdir()
        error = C.create_string_buffer(256)
        if api.daw_sc_queue_create(os.fsencode(queue), nonce, error, len(error)):
            raise RuntimeError(error.value.decode())
        consumer = api.daw_sc_queue_open(os.fsencode(queue), nonce, error, len(error))
        if not consumer:
            raise RuntimeError(error.value.decode())
        try:
            with open(directory / 'stdout', 'wb') as stdout, open(directory / 'stderr', 'wb') as stderr:
                env = {**os.environ, 'CSOUND6RC': str(rc), 'CSOUND7RC': str(rc)}
                child = subprocess.Popen([sys.executable, str(worker), str(snapshot), str(queue),
                    str(nonce), str(bridge), str(library), str(ready), str(report)],
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    cwd=directory, env=env, start_new_session=True)
                deadline = time.monotonic() + 15
                def check():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Csound stream deadline exceeded')
                    for path in (directory / 'stdout', directory / 'stderr'):
                        if path.stat().st_size > MAX_LOG:
                            raise RuntimeError('Csound stream diagnostics exceeded 64 KiB')
                    for path in (ready, report):
                        if path.exists() and (not stat.S_ISREG(path.lstat().st_mode) or path.lstat().st_size > 4096):
                            raise RuntimeError('Invalid or oversized stream report')
                    status = child.poll()
                    if status is not None and status != 0:
                        with open(directory / 'stderr', 'rb') as stream:
                            detail = stream.read(1024).decode('utf-8', 'replace')
                        raise RuntimeError(f'Csound stream worker failed ({status}): {detail}')
                    if api.daw_sc_queue_fault(consumer):
                        raise RuntimeError('Csound stream queue fault')
                while not ready.exists():
                    check()
                    if child.poll() is not None:
                        raise RuntimeError('Csound stream ended before prefill')
                    time.sleep(.001)
                prefill = min(4, (source['duration_frames']+63)//64)
                acknowledgment = read_json(ready, 4096)
                if acknowledgment != {'version': 1, 'prefill_blocks': prefill} or any(type(v) is not int for v in acknowledgment.values()):
                    raise RuntimeError('Invalid Csound prefill acknowledgment')
                if not prefill <= api.daw_sc_queue_written(consumer) <= min(64, (source['duration_frames']+63)//64):
                    raise RuntimeError('Csound prefill acknowledgment precedes queue publication')
                pcm = bytearray()
                buffer = (C.c_float * 128)()
                blocks = (source['duration_frames']+63)//64
                start = time.monotonic()
                while len(pcm) < blocks * 512:
                    check()
                    if stall:
                        time.sleep(.005)
                        continue
                    result = api.daw_sc_queue_pop(consumer, buffer)
                    if result < 0:
                        raise RuntimeError('Csound queue consumption failed')
                    if not result:
                        if child.poll() is not None:
                            raise RuntimeError('Csound source ended with missing queue blocks')
                        time.sleep(.0005)
                        continue
                    if any(not math.isfinite(v) for v in buffer):
                        raise RuntimeError('Csound queue produced nonfinite samples')
                    pcm.extend(struct.pack('<128f', *buffer))
                    consumed = len(pcm)//512
                    if cancel_after_blocks is not None and consumed >= cancel_after_blocks:
                        raise RuntimeError('Csound stream cancelled by owner')
                    if kill_after_blocks is not None and consumed >= kill_after_blocks:
                        os.killpg(child.pid, signal.SIGKILL)
                        kill_after_blocks = None
                    if paced:
                        delay = start + consumed*64/rate - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                while child.poll() is None:
                    check()
                    time.sleep(.001)
                check()
            producer = read_json(report, 4096)
            expected = {'version': 1, 'published_blocks': blocks,
                        'source_frames': source['duration_frames'], 'queue_frames': blocks*64,
                        'sha256': hashlib.sha256(pcm).hexdigest(), 'source_gain_applied': False,
                        'csound_released': True}
            if set(producer) != set(expected) | {'backpressure_waits'} or any(type(producer.get(k)) is not type(v) or producer.get(k) != v for k,v in expected.items()) or type(producer['backpressure_waits']) is not int or producer['backpressure_waits'] < 0:
                raise RuntimeError('Csound producer/consumer continuity or report mismatch')
            return {'version': 1, 'producer': producer, 'consumed_blocks': blocks,
                    'consumed_frames': source['duration_frames'], 'consumer_sha256': expected['sha256'],
                    'prefill_blocks': prefill, 'paced': paced,
                    'hardware_audio': False, 'daw_transport': False,
                    'pcm': bytes(pcm[:source['duration_frames']*8])}
        finally:
            try:
                if child is not None:
                    reap_worker(child)
            finally:
                api.daw_sc_queue_close(consumer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default=os.environ.get('DAW_CSOUND_LIBRARY'))
    parser.add_argument('--bridge', type=Path, default=BRIDGE)
    args = parser.parse_args()
    if not args.library:
        parser.error('set DAW_CSOUND_LIBRARY or --library')
    report = probe(args.library, args.bridge)
    pcm = report.pop('pcm')
    before, after = measure(pcm, 1000), measure(pcm, 28000)
    if abs(before['frequency_hz']-440) > 2 or abs(after['frequency_hz']-660) > 2 or not .49 < after['rms']/before['rms'] < .51:
        raise RuntimeError('Saved control events did not reach streamed audio')
    report.update(before=before, after=after)
    print(json.dumps(report, allow_nan=False))

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': {'code': 'cancelled', 'message': 'Csound stream interrupted by owner'}}), file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': {'code': 'runtime_error', 'message': str(error)}}), file=sys.stderr)
        raise SystemExit(1)

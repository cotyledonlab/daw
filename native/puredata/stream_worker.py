#!/usr/bin/env python3
"""One owned libpd thread publishes live blocks; no hardware callback calls Pd."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from native.csound.queue_api import bind_queue
from native.puredata.source_worker import load_job, perform_source


def publish_json(path, value):
    temporary = str(path) + '.partial'
    with open(temporary, 'x') as stream:
        json.dump(value, stream, allow_nan=False)
    os.link(temporary, path)
    os.unlink(temporary)


def stream(job, queue, nonce, bridge, library, ready, report, gate=None):
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    rate, source = load_job(job)
    if rate != 48000:
        raise ValueError('The fixed queue requires 48000 Hz')
    # Reuse the proven Csound float64-to-float32 producer ABI and SC consumer.
    # The historical symbol names describe the ABI, not the DSP runtime owner.
    api = bind_queue(bridge)
    error = C.create_string_buffer(256)
    producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
    if not producer:
        raise RuntimeError(error.value.decode('utf-8', 'replace'))
    count = waits = 0
    digest = hashlib.sha256()
    blocks = (source['duration_frames'] + 63) // 64
    prefill = min(4, blocks)

    def emit(frame, raw):
        nonlocal count, waits
        if frame != count * 64:
            raise RuntimeError('Pure Data source block discontinuity')
        raw += bytes(1024 - len(raw))
        samples = (C.c_double * 128).from_buffer_copy(raw)
        if any(not math.isfinite(v) or abs(v) > 3.4028234663852886e38 for v in samples):
            raise RuntimeError('Pure Data source cannot fit the float32 queue')
        pcm = struct.pack('<128f', *samples)
        deadline = time.monotonic() + 2
        while True:
            if gate is not None and Path(gate + '.stop').exists():
                raise InterruptedError('Pure Data stream stopped by owner')
            accepted = api.daw_cs_queue_push(producer, samples)
            if accepted == 1:
                break
            if accepted < 0:
                raise RuntimeError('Pure Data queue publication failed')
            if time.monotonic() >= deadline:
                raise RuntimeError('Pure Data queue consumer stalled for two seconds')
            waits += 1
            time.sleep(.001)
        digest.update(pcm)
        count += 1
        if count == prefill:
            publish_json(ready, {'version': 1, 'prefill_blocks': prefill})

    def before_dsp():
        if gate is None:
            return
        publish_json(gate + '.compiled', {'version': 1, 'sample_rate': rate,
                                         'blocksize': 64, 'channels': 2})
        deadline = time.monotonic() + 30
        while not Path(gate).exists():
            if Path(gate + '.stop').exists():
                raise InterruptedError('Pure Data stream stopped before DSP')
            if time.monotonic() >= deadline:
                raise RuntimeError('Pure Data owner start handshake timed out')
            time.sleep(.001)

    try:
        perform_source(rate, source, library, emit, before_dsp)
    finally:
        api.daw_cs_queue_close(producer)
    publish_json(report, {'version': 1, 'published_blocks': count,
                         'source_frames': source['duration_frames'], 'queue_frames': count * 64,
                         'sha256': digest.hexdigest(), 'backpressure_waits': waits,
                         'source_gain_applied': False, 'puredata_released': True})


if __name__ == '__main__':
    try:
        if len(sys.argv) not in (8, 9):
            raise ValueError('Usage: stream_worker.py JOB QUEUE NONCE BRIDGE LIBPD READY REPORT [GATE]')
        stream(*sys.argv[1:])
    except Exception as error:
        print(json.dumps({'ok': False, 'error': {'code': 'runtime_error', 'message': str(error)}}),
              file=sys.stderr)
        raise SystemExit(1)

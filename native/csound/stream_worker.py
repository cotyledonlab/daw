#!/usr/bin/env python3
"""One owned Csound DSP thread publishes blocks with bounded backpressure."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import struct
import stat
import sys
import time

from queue_api import bind_queue
from source_worker import load_job, perform_source


def publish_json(path, value):
    temporary = str(path) + '.partial'
    with open(temporary, 'x') as stream:
        json.dump(value, stream, allow_nan=False)
    os.link(temporary, path)  # publish only complete JSON, refuse an existing result
    os.unlink(temporary)


def stream(job, queue, nonce, bridge, library, ready, report, gate=None):
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    rate, source = load_job(job)
    if rate != 48000:
        raise ValueError('The fixed queue requires 48000 Hz')
    api = bind_queue(bridge)
    error = C.create_string_buffer(256)
    producer = api.daw_cs_queue_open(os.fsencode(queue), int(nonce), error, len(error))
    if not producer:
        raise RuntimeError(error.value.decode('utf-8', 'replace'))
    pending_control = None
    last_revision = 0
    saved_controls = {control["name"]: control for control in source["controls"]}
    def before_block(frame, set_control):
        nonlocal pending_control, last_revision
        if gate is None:
            return
        command = Path(gate).parent / "control.json"
        try:
            fd = os.open(command, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("Csound control command must be a regular file")
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        if len(raw) > 4096:
            raise RuntimeError("Csound control command exceeds 4 KiB")
        edit = json.loads(raw)
        if not isinstance(edit, dict) or set(edit) != {"version", "revision", "name", "value"}:
            raise RuntimeError("invalid Csound control command fields")
        revision = edit["revision"]
        value = edit["value"]
        if (type(edit["version"]) is not int or edit["version"] != 1 or
                not isinstance(revision, str) or not revision.isascii() or
                not revision.isdecimal() or str(int(revision)) != revision or
                not last_revision < int(revision) <= 18446744073709551615 or
                not isinstance(edit["name"], str) or edit["name"] not in saved_controls or
                type(value) not in (int, float) or not math.isfinite(value)):
            raise RuntimeError("invalid Csound control command")
        control = saved_controls[edit["name"]]
        if control["points"] or pending_control is not None:
            raise RuntimeError("Csound control is automated or delivery is pending")
        command.unlink()
        set_control(control, value)  # set and exact native readback on the DSP owner
        last_revision = int(revision)
        pending_control = {"version": 1, "revision": revision, "value": value,
                           "frame": min(frame + 64, source["duration_frames"])}
    count = waits = 0
    digest = hashlib.sha256()
    blocks = (source['duration_frames'] + 63) // 64
    prefill = min(4, blocks)
    def emit(frame, raw):
        nonlocal count, waits, pending_control
        if frame != count * 64:
            raise RuntimeError('Csound source block discontinuity')
        raw += bytes(1024 - len(raw))  # final partial block has silent padding
        samples = (C.c_double * 128).from_buffer_copy(raw)
        if any(not math.isfinite(v) or abs(v) > 3.4028234663852886e38 for v in samples):
            raise RuntimeError('Csound source cannot be represented in the float32 queue')
        pcm = struct.pack('<128f', *samples)
        deadline = time.monotonic() + 2
        while True:
            if gate is not None and Path(gate + '.stop').exists():
                raise InterruptedError('Csound stream stopped by owner')
            accepted = api.daw_cs_queue_push(producer, samples)
            if accepted == 1:
                break
            if accepted < 0:
                raise RuntimeError('Csound queue publication failed')
            if time.monotonic() >= deadline:
                raise RuntimeError('Csound queue consumer stalled for two seconds')
            waits += 1
            time.sleep(.001)
        digest.update(pcm)
        count += 1
        if pending_control is not None:
            publish_json(Path(gate).parent / "ack.json", pending_control)
            pending_control = None
        if count == prefill:
            publish_json(ready, {'version': 1, 'prefill_blocks': prefill})
    def before_dsp():
        if gate is None:
            return
        publish_json(gate + '.compiled', {'version': 1, 'sample_rate': rate, 'ksmps': 64, 'channels': 2})
        deadline = time.monotonic() + 30
        while not Path(gate).exists():
            if Path(gate + '.stop').exists():
                raise InterruptedError('Csound stream stopped before DSP')
            if time.monotonic() >= deadline:
                raise RuntimeError('Csound owner start handshake timed out')
            time.sleep(.001)
    try:
        perform_source(rate, source, library, emit, before_dsp, before_block)
    finally:
        api.daw_cs_queue_close(producer)
    publish_json(report, {'version': 1, 'published_blocks': count,
                   'source_frames': source['duration_frames'], 'queue_frames': count * 64,
                   'sha256': digest.hexdigest(), 'backpressure_waits': waits,
                   'source_gain_applied': False, 'csound_released': True})


if __name__ == '__main__':
    try:
        if len(sys.argv) not in (8, 9):
            raise ValueError('Usage: stream_worker.py JOB QUEUE NONCE BRIDGE CSOUND READY REPORT [GATE]')
        stream(*sys.argv[1:])
    except Exception as error:
        print(json.dumps({'ok': False, 'error': {'code': 'runtime_error', 'message': str(error)}}), file=sys.stderr)
        raise SystemExit(1)

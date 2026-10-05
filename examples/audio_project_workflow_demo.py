#!/usr/bin/env python3
"""Import/trim/copy PCM with the musical fixture; reopen ZIP in a fresh engine.

Run after building: python3 examples/audio_project_workflow_demo.py [--native]
The optional hardware check remains muted. Output is saved under output/.
"""
import argparse
import copy
import http.client
import io
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui.server import Server


def source_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        frames = bytearray()
        for frame in range(96000):
            t = frame / 48000
            envelope = min(1, frame / 480, (95999-frame) / 480)
            left = round(32767 * .08 * envelope * math.sin(2*math.pi*(330*t+40*t*t)))
            right = round(32767 * .06 * envelope * math.sin(2*math.pi*220*t))
            frames.extend(struct.pack('<hh', left, right))
        audio.writeframes(frames)
    return output.getvalue()


class Client:
    def __enter__(self):
        self.server = Server(ROOT / 'target/debug/daw', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, method, path, data=None, *, binary=False, metadata=None):
        headers = {'X-DAW-Token': self.server.token}
        if data is not None:
            headers['Content-Type'] = ('application/zip' if path == '/api/project' else 'audio/wav') if binary else 'application/json'
            if not binary:
                data = json.dumps(data).encode()
        if metadata is not None:
            headers['X-DAW-Metadata'] = json.dumps(metadata)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=120)
        connection.request(method, path, data, headers)
        response = connection.getresponse()
        body = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        if status != 200:
            raise RuntimeError(f'{path}: {status}: {body[:1000]!r}')
        return body, response_headers

    def json(self, method, path, data=None, **kwargs):
        return json.loads(self.request(method, path, data, **kwargs)[0])

    def inspect(self):
        return self.json('GET', '/api/session/inspect')

    def replace(self, session):
        return self.json('POST', '/api/session', {'session':session, 'expected_revision':self.inspect()['revision']})


def native_check(client):
    def transport(action, **params):
        return client.json('POST', '/api/transport', {'action':action, **params})

    def observed(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = client.json('GET', '/api/transport')
            assert status['state'] != 'error', status
            if predicate(status):
                return status
            time.sleep(.02)
        raise AssertionError(status)

    assert client.json('GET', '/api/capabilities')['timeline_transport']['until_stopped']
    transport('play', until_stopped=True, volume=0)
    observed(lambda s: s['state'] == 'playing' and s['until_stopped'])
    client.json('POST', '/api/transport/loop', {'region':{'start_frame':24000, 'end_frame':72000}})
    observed(lambda s: not s['timeline_command_pending'])
    time.sleep(2.2)
    transport('pause')
    paused = observed(lambda s: s['state'] == 'paused')
    client.json('POST', '/api/transport/seek', {'frame':48000})
    sought = observed(lambda s: not s['timeline_command_pending'])
    assert sought['timeline_frame'] == 48000 and sought['submitted_frames'] == paused['submitted_frames'], sought
    transport('resume')
    observed(lambda s: s['state'] == 'playing' and s['submitted_frames'] > paused['submitted_frames'])
    stopped = transport('stop')
    assert stopped['state'] == 'stopped' and stopped['resources_released'], stopped
    assert stopped['callbacks_over_buffer_budget'] == 0, stopped
    return {key:stopped[key] for key in ('submitted_frames', 'max_render_microseconds', 'callbacks_over_buffer_budget')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    args = parser.parse_args()
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='audio-project-', dir=ROOT / 'output'))
    source = source_wav()
    (directory / 'source.wav').write_bytes(source)
    fixture = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
    with Client() as client:
        client.replace(fixture)
        imported = client.json('POST', '/api/audio/import', source, binary=True,
                               metadata={'expected_revision':client.inspect()['revision'],
                                         'track_id':'imported-chirp', 'clip_id':'take-1', 'start_frame':24000})
        session = imported['session']
        clip = session['tracks'][-1]['clips'][0]
        clip.update(source_offset_frames=12000, length_frames=48000, gain=.5)
        duplicate = copy.deepcopy(clip)
        duplicate.update(id='take-2', start_frame=96000, gain=.3)
        session['tracks'][-1]['clips'].append(duplicate)
        client.replace(session)
        wav, headers = client.request('POST', '/api/render', {'seconds':4})
        assert headers['X-Clipped-Frames'] == '0'
        (directory / 'before-reopen.wav').write_bytes(wav)
        bundle, _ = client.request('GET', '/api/project')
        (directory / 'session.daw.zip').write_bytes(bundle)
        hardware = native_check(client) if args.native else None
    with Client() as reopened:
        reopened.json('POST', '/api/project', bundle, binary=True,
                      metadata={'expected_revision':reopened.inspect()['revision']})
        restored = reopened.inspect()['session']
        for original, loaded in zip(session['tracks'][-1]['clips'], restored['tracks'][-1]['clips']):
            assert {k:v for k,v in original.items() if k != 'source_path'} == {k:v for k,v in loaded.items() if k != 'source_path'}
        exported, headers = reopened.request('POST', '/api/render', {'seconds':4})
        assert exported == wav and headers['X-Clipped-Frames'] == '0'
        (directory / 'after-reopen.wav').write_bytes(exported)
    print(json.dumps({'project':str(directory), 'portable_zip':str(directory / 'session.daw.zip'),
                      'deterministic_reopen':True, 'export_seconds':4, 'clipped_frames':0,
                      'native_muted':hardware}, indent=2))


if __name__ == '__main__':
    main()

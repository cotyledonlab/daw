#!/usr/bin/env python3
"""Save/reopen/export a mixed song with lowpass, delay and gain automation.

Run after building: python3 examples/processing_workflow_demo.py [--song] [--native]
The optional native check stays muted; generated files go under ignored output/.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.audio_project_workflow_demo import Client, native_check, source_wav
from examples.song_workflow_demo import inspect_wav, portable_data, song_fixture


def processing_fixture(song=False):
    session = song_fixture() if song else json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
    session['schema_version'] = 11
    for index, track in enumerate(session['tracks']):
        track.setdefault('mixer', {'gain': 0.8, 'pan': [-0.2, 0, 0.2][index], 'mute': False, 'solo': False})
        track['effects'] = [{'kind': 'gain', 'id': 'trim', 'gain': 0.9, 'bypass': False}]
    session['tracks'][1]['effects'].append({'kind': 'lowpass', 'id': 'tone', 'cutoff_hz': 850, 'bypass': False})
    session['tracks'][2]['effects'].append({'kind': 'delay', 'id': 'echo', 'time_ms': 250,
                                          'feedback': 0.35, 'mix': 0.2, 'bypass': False})
    session['tracks'][2]['automation'] = [{'effect_id': 'trim', 'parameter': 'gain', 'interpolation': 'step',
                                         'points': [{'frame': 48000, 'value': 0.65}, {'frame': 96000, 'value': 0.9}]}]
    return session


def import_fixture(client, song=False):
    client.replace(processing_fixture(song))
    imported = client.json('POST', '/api/audio/import', source_wav(), binary=True,
                           metadata={'expected_revision': client.inspect()['revision'],
                                     'track_id': 'audio', 'clip_id': 'take', 'start_frame': 0})
    session = imported['session']
    audio = session['tracks'][-1]
    audio['mixer'] = {'gain': 0.65, 'pan': -0.15, 'mute': False, 'solo': False}
    audio['effects'] = [{'kind': 'lowpass', 'id': 'tone', 'cutoff_hz': 2400, 'bypass': False},
                        {'kind': 'delay', 'id': 'echo', 'time_ms': 375, 'feedback': 0.3,
                         'mix': 0.25, 'bypass': False}]
    if song:
        template = audio['clips'][0]
        audio['clips'] = []
        for index, offset in enumerate(range(0, 48000 * 180, 96000)):
            clip = copy.deepcopy(template)
            clip.update(id=f'take-{index}', start_frame=offset, gain=0.6)
            audio['clips'].append(clip)
    client.replace(session)
    return client.inspect()['session']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song', action='store_true', help='Export the full three-minute fixture.')
    parser.add_argument('--native', action='store_true', help='Also check muted native loop/seek/pause/stop.')
    args = parser.parse_args()
    seconds = 180 if args.song else 4
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='processing-workflow-', dir=ROOT / 'output'))
    with Client() as client:
        session = import_fixture(client, args.song)
        start = time.monotonic()
        first, headers = client.request('POST', '/api/render', {'seconds': seconds})
        first_seconds = time.monotonic() - start
        assert headers['X-Clipped-Frames'] == '0', headers
        (directory / 'before-reopen.wav').write_bytes(first)
        bundle, _ = client.request('GET', '/api/project')
        (directory / 'session.daw.zip').write_bytes(bundle)
        hardware = native_check(client) if args.native else None
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True,
                   metadata={'expected_revision': fresh.inspect()['revision']})
        assert portable_data(fresh.inspect()['session']) == portable_data(session)
        start = time.monotonic()
        second, headers = fresh.request('POST', '/api/render', {'seconds': seconds})
        second_seconds = time.monotonic() - start
        assert second == first and headers['X-Clipped-Frames'] == '0'
        (directory / 'after-reopen.wav').write_bytes(second)
    signal = inspect_wav(first) if args.song else {}
    evidence = {**signal, 'project': str(directory), 'schema_version': 11, 'duration_seconds': seconds,
                'track_count': len(session['tracks']), 'clip_count': sum(len(t['clips']) for t in session['tracks']),
                'effects': ['gain', 'lowpass', 'delay'], 'saved_gain_automation': True,
                'portable_exact_reopen': True, 'byte_identical_export': True, 'clipped_frames': 0,
                'sha256': hashlib.sha256(first).hexdigest(), 'wav_bytes': len(first),
                'export_wall_seconds': {'before_reopen': round(first_seconds, 3), 'after_reopen': round(second_seconds, 3)},
                'native_muted': hardware}
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

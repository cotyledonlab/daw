#!/usr/bin/env python3
"""Finish a mixed song with step gain automation and audio fades; reopen/export.

Run after building: python3 examples/finishing_workflow_demo.py [--song] [--native]
The native transport check is muted. Artifacts go under ignored output/.
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
from examples.audio_project_workflow_demo import Client, native_check
from examples.processing_workflow_demo import import_fixture
from examples.song_workflow_demo import inspect_wav, portable_data


def finish_fixture(session):
    next_session = copy.deepcopy(session)
    next_session['tracks'][2]['automation'][0]['points'] = [
        {'frame': 48001, 'value': 0.45}, {'frame': 96003, 'value': 0.8},
        {'frame': 144005, 'value': 0.65},
    ]
    for clip in next_session['tracks'][-1]['clips']:
        clip.update(fade_in_frames=240, fade_out_frames=960)
    return next_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song', action='store_true', help='Export the three-minute fixture.')
    parser.add_argument('--native', action='store_true', help='Check muted native loop/seek/pause/stop.')
    args = parser.parse_args()
    seconds = 180 if args.song else 4
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='finishing-workflow-', dir=ROOT / 'output'))
    with Client() as client:
        original = import_fixture(client, args.song)
        session = finish_fixture(original)
        client.replace(session)
        session = client.inspect()['session']
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
    evidence = {**signal, 'project': str(directory), 'schema_version': 11,
                'duration_seconds': seconds, 'track_count': len(session['tracks']),
                'clip_count': sum(len(t['clips']) for t in session['tracks']),
                'effects': ['gain', 'lowpass', 'delay'], 'saved_gain_automation': True,
                'audio_clip_fades': {'in_frames': 240, 'out_frames': 960},
                'portable_exact_reopen': True, 'byte_identical_export': True,
                'clipped_frames': 0, 'sha256': hashlib.sha256(first).hexdigest(),
                'wav_bytes': len(first), 'native_muted': hardware,
                'export_wall_seconds': {'before_reopen': round(first_seconds, 3),
                                        'after_reopen': round(second_seconds, 3)}}
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

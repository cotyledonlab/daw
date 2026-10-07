#!/usr/bin/env python3
"""Muted native note/clip/mix updates, rollback, snapshot undo and ZIP reopen.

Run with a native-audio build: python3 examples/live_editing_workflow_demo.py
Hardware callback evidence does not certify acoustic listening.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.audio_project_workflow_demo import Client
from examples.finishing_workflow_demo import finish_fixture
from examples.processing_workflow_demo import import_fixture
from examples.song_workflow_demo import portable_data


def main():
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='live-editing-', dir=ROOT / 'output'))
    with Client() as client:
        assert client.json('GET', '/api/capabilities')['live_arrangement_edits']['implemented']
        original = finish_fixture(import_fixture(client, False))
        client.replace(original)
        original = client.inspect()['session']
        client.json('POST', '/api/transport', {'action': 'play', 'until_stopped': True, 'volume': 0, 'metronome': True})
        client.json('POST', '/api/transport/loop', {'region': {'start_frame': 0, 'end_frame': 192000}})

        def status_until(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = client.json('GET', '/api/transport')
                assert status['state'] != 'error', status
                if predicate(status):
                    return status
                time.sleep(.02)
            raise AssertionError(status)

        first = status_until(lambda s: s['state'] == 'playing' and not s['timeline_command_pending'])
        assert first['live_arrangement_edits']
        updated = copy.deepcopy(original)
        melody = next(t for t in updated['tracks'] if t['device']['kind'] in ('sine', 'synth') and t.get('mode') == 'sequenced')
        clip = melody['clips'][0]
        clip['notes'][0]['frequency_hz'] *= 2
        clip['start_frame'] += 1
        duplicate = copy.deepcopy(clip)
        duplicate['id'] = 'live-copy'
        duplicate['start_frame'] += clip['length_frames']
        melody['clips'].append(duplicate)
        updated['tracks'][2]['mixer']['gain'] *= .5
        updated['tracks'][2]['effects'][0]['gain'] *= .8
        updated['tracks'][2]['automation'][0]['points'][0]['value'] = .35
        updated['tracks'][-1]['clips'][0]['fade_in_frames'] = 480
        updated['tempo_milli_bpm'] = 100000

        def live(session):
            client.json('POST', '/api/session/live', {'session': session, 'expected_revision': client.inspect()['revision']})
            return client.inspect()['revision']

        revision = live(updated)
        audible = status_until(lambda s: s['audible_session_revision'] == revision)
        assert audible['submitted_frames'] > first['submitted_frames'] and audible['loop_region'] == first['loop_region']
        snapshot = client.inspect()
        for bad in ({'session': original, 'expected_revision': '0'},
                    {'session': {**updated, 'tracks': []}, 'expected_revision': revision}):
            try:
                client.json('POST', '/api/session/live', bad)
                raise AssertionError('invalid update accepted')
            except RuntimeError:
                assert client.inspect() == snapshot
                assert client.json('GET', '/api/transport')['state'] == 'playing'
        undo = live(original)
        status_until(lambda s: s['audible_session_revision'] == undo)
        redo = live(updated)
        status_until(lambda s: s['audible_session_revision'] == redo)
        client.json('POST', '/api/transport', {'action': 'pause'})
        paused = status_until(lambda s: s['state'] == 'paused')
        paused_revision = live(original)
        client.json('POST', '/api/transport', {'action': 'resume'})
        status_until(lambda s: s['audible_session_revision'] == paused_revision and s['submitted_frames'] > paused['submitted_frames'])
        final_revision = live(updated)
        status_until(lambda s: s['audible_session_revision'] == final_revision)
        bundle, _ = client.request('GET', '/api/project')
        (directory / 'session.daw.zip').write_bytes(bundle)
        stopped = client.json('POST', '/api/transport', {'action': 'stop'})
        assert stopped['resources_released'] and stopped['callbacks_over_buffer_budget'] == 0, stopped
        first_wav, headers = client.request('POST', '/api/render', {'seconds': 4})
        assert headers['X-Clipped-Frames'] == '0'
        (directory / 'before-reopen.wav').write_bytes(first_wav)
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True, metadata={'expected_revision': fresh.inspect()['revision']})
        assert portable_data(fresh.inspect()['session']) == portable_data(updated)
        second_wav, headers = fresh.request('POST', '/api/render', {'seconds': 4})
        assert second_wav == first_wav and headers['X-Clipped-Frames'] == '0'
        (directory / 'after-reopen.wav').write_bytes(second_wav)
    evidence = {'project': str(directory), 'live_note_clip_mix_effect_automation_fade_tempo': True,
                'stale_and_structural_rollback': True, 'live_snapshot_undo_redo': True,
                'paused_update_then_resume': True, 'exact_zip_reopen_and_export': True,
                'sha256': hashlib.sha256(first_wav).hexdigest(), 'native_muted': stopped}
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

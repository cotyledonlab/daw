#!/usr/bin/env python3
"""Record a deterministic overdub take through browser capture and checked HTTP.

Run after cargo build: python3 examples/note_recording_workflow_demo.py
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.audio_project_workflow_demo import Client
from examples.processing_workflow_demo import import_fixture
from examples.note_input_workflow_demo import export, checked_replace, wav_signal, SECONDS
from examples.song_workflow_demo import portable_data


def recorded_take(session, revision):
    script = r'''
const fs = require('node:fs');
const Recording = require('./gui/note_recording.js');
const {session, revision} = JSON.parse(fs.readFileSync(0, 'utf8'));
let now = 1000;
const recorder = new Recording({clock:()=>now});
const clip = session.tracks[0].clips[0];
recorder.arm({session, revision, trackIndex:0, clipId:clip.id});
recorder.updateTransport({state:'playing', timeline_frame:clip.start_frame,
  sample_rate:session.sample_rate, count_in_remaining_frames:24000});
const event = (type, key, timestamp, hz, velocity) => recorder.capture({type,key,
  timestamp,frequency_hz:hz,velocity,source:'keyboard'});
event('on','count-in',1100,440,.5);
event('off','count-in',1600);
event('on','a',1625,440,.35);
event('on','b',1750,550,.3);
event('off','a',1875);
event('off','b',2000);
event('on','held',2125,660,.25);
const result = recorder.finish({session,revision,transport:{state:'stopped',
  timeline_frame:clip.start_frame+42000}});
if (JSON.stringify(session)!==JSON.stringify(recorder.take.session)) throw Error('capture mutated base');
if (!result || !recorder.accept()) throw Error('take did not finish');
process.stdout.write(JSON.stringify(result));
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps({
        'session': session, 'revision': revision}), text=True, capture_output=True,
        cwd=ROOT, check=True)
    return json.loads(result.stdout)


def apply_take(client, session, revision):
    result = client.json('POST', '/api/note/take', {
        'session': session, 'expected_revision': revision})
    assert result['session'] == session
    assert isinstance(result['revision'], str) and result['revision'] != revision
    assert result == client.inspect(), 'Take response did not include the committed revision.'
    return result


def workflow():
    with Client() as client:
        original = import_fixture(client)
        # A pre-existing microtonal, off-grid note must survive recording exactly.
        original['tracks'][0]['clips'][0]['notes'][0].update(
            start_frame=137, duration_frames=10007, frequency_hz=263.123456789)
        client.replace(original)
        initial = client.inspect()
        original = initial['session']
        baseline = export(client)
        take = recorded_take(original, initial['revision'])
        assert client.inspect() == initial, 'Buffered recording mutated saved project.'
        notes = take['notes']
        assert [n['start_frame'] for n in notes] == [6000, 12000, 30000]
        assert [n['duration_frames'] for n in notes] == [12000, 12000, 12000]
        expected = copy.deepcopy(original)
        expected['tracks'][0]['clips'][0]['notes'].extend(notes)
        assert take['draft'] == expected, 'Overdub changed existing project data.'
        finished = apply_take(client, take['draft'], take['revision'])
        final_audio = export(client)
        assert final_audio != baseline, 'Recorded notes did not affect PCM.'
        try:
            apply_take(client, original, initial['revision'])
        except RuntimeError as error:
            assert '422' in str(error), error
        else:
            raise AssertionError('Stale recording replacement was accepted.')
        assert client.inspect() == finished
        assert export(client) == final_audio
        # One snapshot undo removes the whole take; redo restores it in one edit.
        checked_replace(client, original, finished['revision'])
        assert client.inspect()['session'] == original
        assert export(client) == baseline
        client.replace(take['draft'])
        assert export(client) == final_audio
        bundle, _ = client.request('GET', '/api/project')
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True,
                   metadata={'expected_revision': fresh.inspect()['revision']})
        assert portable_data(fresh.inspect()['session']) == portable_data(take['draft'])
        reopened = export(fresh)
        assert reopened == final_audio
    evidence = {'recorded_notes': len(notes), 'count_in_note_ignored': True,
                'held_gate_closed_at_stop': True, 'single_take_undo_redo': True,
                'stale_edits_preserve_project': True,
                'atomic_take_revision_envelope': True,
                'preserved_pcm_mixer_effects_and_offgrid_note': True,
                'portable_exact_reopen': True, 'byte_identical_export': True,
                'clipped_frames': 0, 'export_seconds': SECONDS,
                **wav_signal(final_audio, SECONDS)}
    return evidence, bundle, final_audio, reopened


def main():
    evidence, bundle, before, after = workflow()
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='note-recording-workflow-', dir=ROOT / 'output'))
    (directory / 'session.daw.zip').write_bytes(bundle)
    (directory / 'before-reopen.wav').write_bytes(before)
    (directory / 'after-reopen.wav').write_bytes(after)
    evidence['project'] = str(directory)
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

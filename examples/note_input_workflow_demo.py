#!/usr/bin/env python3
"""Capture a checked step phrase, audition engine WAV, and reopen a portable ZIP.

Run after cargo build: python3 examples/note_input_workflow_demo.py
Artifacts and acceptance evidence are saved under ignored output/.
"""
import array
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.audio_project_workflow_demo import Client
from examples.processing_workflow_demo import import_fixture
from examples.song_workflow_demo import portable_data

PHRASE = [60, 64, 67, 72, 67, 64, 62, 69]
GRID_TICKS = 240
SECONDS = 4


def editor_edit(session, step=None):
    """Use the same JS editor and exact tick conversion as browser step capture."""
    script = r'''
const fs = require('node:fs');
const Editor = require('./gui/editor.js');
const {session, step, pitches} = JSON.parse(fs.readFileSync(0, 'utf8'));
let next;
if (step === null) {
  next = Editor.addNoteTrack(session, 'step-capture', 'synth');
  next = Editor.addNoteClip(next, next.tracks.length - 1, {
    id:'captured-phrase', start_frame:Editor.ticksToFrames(next,3840),
    length_frames:Editor.ticksToFrames(next,3840), notes:[]
  });
} else {
  const start = Editor.ticksToFrames(session, step * 240);
  const end = Editor.ticksToFrames(session, (step + 1) * 240);
  next = Editor.insertStepNote(session, session.tracks.length - 1, 'captured-phrase', {
    id:`step-${step+1}`, start_frame:start, duration_frames:end-start,
    frequency_hz:Editor.midiToHz(pitches[step]), velocity:0.65
  });
}
process.stdout.write(JSON.stringify(next));
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps({
        'session': session, 'step': step, 'pitches': PHRASE}), text=True,
        capture_output=True, cwd=ROOT, check=True)
    return json.loads(result.stdout)


def wav_signal(body, seconds):
    with wave.open(io.BytesIO(body), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (
            2, 2, 48000, int(48000 * seconds))
        samples = array.array('h', audio.readframes(audio.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(sample) for sample in samples)
    assert 100 < peak < 32767, peak
    return {'frames': int(48000 * seconds), 'pcm_peak': peak,
            'sha256': hashlib.sha256(body).hexdigest()}


def export(client):
    body, headers = client.request('POST', '/api/render', {'seconds': SECONDS})
    assert headers['X-Clipped-Frames'] == '0'
    wav_signal(body, SECONDS)
    return body


def checked_replace(client, session, revision):
    client.json('POST', '/api/session', {'session': session, 'expected_revision': revision})
    return client.inspect()


def workflow():
    previews = []
    with Client() as client:
        capabilities = client.json('GET', '/api/capabilities')
        assert capabilities['note_preview']['implemented']
        assert capabilities['gui_bridge']['note_preview']
        client.replace(editor_edit(import_fixture(client)))
        initial = client.inspect()
        original = initial['session']
        baseline = export(client)
        before_last = None
        for step, midi in enumerate(PHRASE):
            before = client.inspect()
            before_audio = export(client)
            preview, headers = client.request('POST', '/api/note/preview', {
                'expected_revision': before['revision'], 'track_id': 'step-capture',
                'frequency_hz': 440 * 2 ** ((midi - 69) / 12), 'velocity': .65})
            assert headers['Content-Type'] == 'audio/wav'
            assert headers['X-Clipped-Frames'] == '0'
            wav_signal(preview, .5)
            assert client.inspect() == before, 'Audition changed the project/revision.'
            assert export(client) == before_audio, 'Audition changed offline export.'
            previews.append(preview)
            if step == len(PHRASE) - 1:
                before_last = before_audio
            edited = editor_edit(before['session'], step)
            after = checked_replace(client, edited, before['revision'])
            assert len(after['session']['tracks'][-1]['clips'][0]['notes']) == step + 1
        finished = client.inspect()['session']
        final_audio = export(client)
        assert final_audio != baseline
        # Only the last insertion changed this export; it must affect actual PCM.
        assert final_audio != before_last, 'The last captured note was silent.'
        notes = finished['tracks'][-1]['clips'][0]['notes']
        assert [n['start_frame'] for n in notes] == [step * 6000 for step in range(8)]
        assert all(n['duration_frames'] == 6000 for n in notes)
        assert portable_data({
            **finished, 'tracks': finished['tracks'][:-1]}) == portable_data({
            **original, 'tracks': original['tracks'][:-1]})
        current = client.inspect()
        for path, data in [('/api/session', {'session': original, 'expected_revision': initial['revision']}),
                           ('/api/note/preview', {'expected_revision': initial['revision'],
                             'track_id': 'step-capture', 'frequency_hz': 440, 'velocity': .65})]:
            try:
                client.json('POST', path, data)
            except RuntimeError as error:
                assert '422' in str(error), error
            else:
                raise AssertionError('Stale revision was accepted.')
            assert client.inspect() == current
            assert export(client) == final_audio
        # Checked snapshot replacement is the same persistence path used by Undo/Redo.
        checked_replace(client, original, current['revision'])
        assert export(client) == baseline
        client.replace(finished)
        assert export(client) == final_audio
        bundle, _ = client.request('GET', '/api/project')
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True,
                   metadata={'expected_revision': fresh.inspect()['revision']})
        assert portable_data(fresh.inspect()['session']) == portable_data(finished)
        reopened = export(fresh)
        assert reopened == final_audio
    evidence = {'captured_notes': len(notes), 'step_grid_ticks': GRID_TICKS,
                'step_duration_frames': 6000, 'preview_seconds': .5,
                'preview_changes_session': False, 'preview_changes_export': False,
                'stale_edits_preserve_project': True, 'checked_undo_redo': True,
                'preserved_pcm_and_other_tracks': True, 'last_note_audible': True,
                'portable_exact_reopen': True, 'byte_identical_export': True,
                'clipped_frames': 0, 'export_seconds': SECONDS,
                **wav_signal(final_audio, SECONDS)}
    return evidence, bundle, final_audio, reopened, previews


def main():
    evidence, bundle, exported, reopened, previews = workflow()
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='note-input-workflow-', dir=ROOT / 'output'))
    (directory / 'session.daw.zip').write_bytes(bundle)
    (directory / 'before-reopen.wav').write_bytes(exported)
    (directory / 'after-reopen.wav').write_bytes(reopened)
    for index, preview in enumerate(previews):
        (directory / f'preview-{index+1}.wav').write_bytes(preview)
    evidence['project'] = str(directory)
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Export a portable 180-second built-in/PCM song before and after fresh reopen.

Build the 180-second offline export engine first, then run:
python3 examples/song_workflow_demo.py
Artifacts and measured export evidence go under ignored output/song-workflow-*.
"""
import array
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import wave

try:
    from .audio_project_workflow_demo import Client, ROOT, source_wav
except ImportError:
    from audio_project_workflow_demo import Client, ROOT, source_wav

RATE = 48000
SECONDS = 180
FRAMES = RATE * SECONDS
PHRASE_FRAMES = RATE * 32


def song_fixture():
    session = json.loads((ROOT / 'examples/sessions/musical-demo.json').read_text())
    session['schema_version'] = 10
    for index, track in enumerate(session['tracks']):
        original = track['clips']
        track['clips'] = []
        track['mixer'] = {'gain': [0.75, 0.9, 0.8][index],
                          'pan': [-0.25, 0, 0.25][index], 'mute': False, 'solo': False}
        for phrase, offset in enumerate(range(0, FRAMES, PHRASE_FRAMES)):
            for clip in original:
                if clip['start_frame'] + offset + clip['length_frames'] > FRAMES:
                    continue  # Preserve complete clips and note gates at the end.
                repeated = copy.deepcopy(clip)
                repeated['id'] = f"{clip['id']}-song-{phrase}"
                repeated['start_frame'] += offset
                track['clips'].append(repeated)
    return session


def portable_data(session):
    """ZIP reopening may relocate owned PCM paths; every other field must match."""
    normalized = copy.deepcopy(session)
    for track in normalized['tracks']:
        for clip in track['clips']:
            if clip['kind'] == 'audio':
                clip.pop('source_path', None)
    return normalized


def inspect_wav(body):
    with wave.open(io.BytesIO(body), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (2, 2, RATE, FRAMES)
        samples = array.array('h', audio.readframes(FRAMES))
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(sample) for sample in samples)
    assert 100 < peak < 32767, f'Expected audible audio with headroom: {peak}'
    last_second = samples[-RATE * 2:]
    end_peak = max(abs(sample) for sample in last_second)
    assert end_peak > 100, f'Song became silent near its end: {end_peak}'
    return {'frames': FRAMES, 'sample_rate': RATE, 'channels': 2, 'wav_bytes': len(body),
            'pcm_peak': peak, 'last_second_pcm_peak': end_peak,
            'sha256': hashlib.sha256(body).hexdigest()}


def export(client, path):
    started = time.monotonic()
    body, headers = client.request('POST', '/api/render', {'seconds': SECONDS})
    elapsed = time.monotonic() - started
    assert headers['X-Clipped-Frames'] == '0', headers
    path.write_bytes(body)
    return body, elapsed


def reject_too_long(client):
    before = client.inspect()
    try:
        client.request('POST', '/api/render', {'seconds': SECONDS + 1})
    except RuntimeError as error:
        assert '422' in str(error), f'Expected duration validation failure: {error}'
    else:
        raise AssertionError('181-second export unexpectedly succeeded')
    assert client.inspect() == before, 'Rejected export changed session or revision'


def main():
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='song-workflow-', dir=ROOT / 'output'))
    source = source_wav()
    (directory / 'source.wav').write_bytes(source)
    fixture = song_fixture()
    with Client() as client:
        client.replace(fixture)
        imported = client.json('POST', '/api/audio/import', source, binary=True,
                               metadata={'expected_revision': client.inspect()['revision'],
                                         'track_id': 'imported-chirp', 'clip_id': 'take-0',
                                         'start_frame': 0})
        session = imported['session']
        audio = session['tracks'][-1]
        audio['mixer'] = {'gain': 0.7, 'pan': -0.15, 'mute': False, 'solo': False}
        template = audio['clips'][0]
        assert template['length_frames'] == RATE * 2, template
        audio['clips'] = []
        for index, offset in enumerate(range(0, FRAMES, RATE * 2)):
            clip = copy.deepcopy(template)
            clip.update(id=f'take-{index}', start_frame=offset, gain=0.6)
            audio['clips'].append(clip)
        client.replace(session)
        session = client.inspect()['session']
        assert portable_data({'tracks': session['tracks'][:3]}) == portable_data({'tracks': fixture['tracks']})
        reject_too_long(client)
        first, first_seconds = export(client, directory / 'before-reopen.wav')
        evidence = inspect_wav(first)
        bundle, _ = client.request('GET', '/api/project')
        (directory / 'session.daw.zip').write_bytes(bundle)
    # Both the bridge and native engine from the first context have exited.
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True,
                   metadata={'expected_revision': fresh.inspect()['revision']})
        restored = fresh.inspect()['session']
        assert portable_data(restored) == portable_data(session), 'ZIP reopen changed notes/devices/mixer/clip data'
        second, second_seconds = export(fresh, directory / 'after-reopen.wav')
        assert hashlib.sha256(second).hexdigest() == evidence['sha256'], 'Fresh-engine export differs'
        assert second == first, 'Fresh-engine WAV is not byte-identical'
    evidence.update({'project': str(directory), 'zip_bytes': len(bundle), 'duration_seconds': SECONDS,
                     'export_wall_seconds': {'before_reopen': round(first_seconds, 3),
                                             'after_reopen': round(second_seconds, 3)},
                     'clipped_frames': 0, 'portable_exact_reopen': True,
                     'rejected_181_preserves_project': True,
                     'track_count': len(session['tracks']),
                     'clip_count': sum(len(track['clips']) for track in session['tracks'])})
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

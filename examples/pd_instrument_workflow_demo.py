#!/usr/bin/env python3
"""Exercise a note-scheduled Pd preset with built-ins, PCM and processing.

Set DAW_LIBPD_LIBRARY, build native-audio, then run this script [--native].
The optional native transport check is muted. Evidence is saved under output/.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.audio_project_workflow_demo import Client, native_check, source_wav
from examples.processing_workflow_demo import processing_fixture
from examples.song_workflow_demo import inspect_wav, portable_data


def instrument_track():
    return {'id': 'pd-lead', 'mode': 'sequenced',
            'device': {'kind': 'pd_instrument',
                       'program': (ROOT / 'native/puredata/instrument.pd').read_text(),
                       'abstractions': [], 'gain': .16,
                       'controls': [{'name': 'cutoff', 'type': 'float', 'default': 4000,
                                     'min': 100, 'max': 12000, 'value': 4000}]},
            'mixer': {'gain': .8, 'pan': .25, 'mute': False, 'solo': False},
            'effects': [{'kind': 'gain', 'id': 'trim', 'gain': .8, 'bypass': False},
                        {'kind': 'delay', 'id': 'echo', 'time_ms': 125,
                         'feedback': .2, 'mix': .12, 'bypass': False}],
            'clips': [{'kind': 'notes', 'id': 'pd-phrase', 'start_frame': 0,
                       'length_frames': 192000,
                       'notes': [{'id': f'pd-note-{i}', 'start_frame': i * 24000,
                                  'duration_frames': 16000,
                                  'frequency_hz': frequency, 'velocity': .65 + (i % 2) * .15}
                                 for i, frequency in enumerate([440, 660, 550, 440, 330, 440, 550, 660])]}]}


def instrument_fixture(song=False):
    session = processing_fixture(song)
    session['schema_version'] = 12
    track = instrument_track()
    if song:
        template = track['clips'][0]
        track['clips'] = []
        for index, frame in enumerate(range(0, 48000 * 180, 192000)):
            clip = copy.deepcopy(template)
            clip.update(id=f'pd-phrase-{index}', start_frame=frame)
            track['clips'].append(clip)
    session['tracks'].append(track)
    return session


def import_fixture(client, song=False):
    client.replace(instrument_fixture(song))
    result = client.json('POST', '/api/audio/import', source_wav(), binary=True,
                         metadata={'expected_revision': client.inspect()['revision'],
                                   'track_id': 'audio', 'clip_id': 'take', 'start_frame': 0})
    session = result['session']
    audio = session['tracks'][-1]
    audio['mixer'] = {'gain': .55, 'pan': -.15, 'mute': False, 'solo': False}
    audio['effects'] = [{'kind': 'lowpass', 'id': 'tone', 'cutoff_hz': 2400, 'bypass': False}]
    audio['clips'][0].update(fade_in_frames=480, fade_out_frames=480)
    if song:
        template = audio['clips'][0]
        audio['clips'] = []
        for index, frame in enumerate(range(0, 48000 * 180, 96000)):
            clip = copy.deepcopy(template)
            clip.update(id=f'take-{index}', start_frame=frame, gain=.6)
            audio['clips'].append(clip)
    client.replace(session)
    return client.inspect()['session']


def note_signal_evidence(wav):
    with wave.open(io.BytesIO(wav), 'rb') as stream:
        assert stream.getframerate() == 48000 and stream.getnchannels() == 2
        samples = struct.unpack('<{}h'.format(stream.getnframes() * 2), stream.readframes(stream.getnframes()))[::2]
    frequencies = []
    for start, expected in ((2048, 440), (26048, 660)):
        window = samples[start:start + 8000]
        crossings = [i for i in range(1, len(window)) if window[i-1] <= 0 < window[i]]
        assert len(crossings) > 3
        measured = (len(crossings) - 1) * 48000 / (crossings[-1] - crossings[0])
        assert abs(measured - expected) < 3, (expected, measured)
        frequencies.append(round(measured, 3))
    gap_peak = max(abs(v) for v in samples[18000:22000])
    assert gap_peak == 0, gap_peak
    return {'scheduled_frequencies_hz': frequencies, 'between_notes_peak_pcm16': gap_peak}


def pd_native_check(client):
    observed = {'runtime': None, 'pd_peak': 0.0, 'pd_worker_underruns': 0, 'pd_transport_wait_buffers': 0}
    class ObservedClient:
        def json(self, method, path, *args, **kwargs):
            report = client.json(method, path, *args, **kwargs)
            if path == '/api/transport':
                if report.get('runtime') == 'puredata_instrument':
                    observed['runtime'] = report['runtime']
                observed['pd_worker_underruns'] = max(observed['pd_worker_underruns'], report.get('pd_worker_underruns', 0))
                observed['pd_transport_wait_buffers'] = max(observed['pd_transport_wait_buffers'], report.get('pd_transport_wait_buffers', 0))
                meters = report.get('mixer_meters') or {}
                for track in meters.get('tracks', []):
                    if track['id'] == 'pd-lead':
                        observed['pd_peak'] = max(observed['pd_peak'], *track['peak'])
            return report
    result = native_check(ObservedClient())
    assert observed['runtime'] == 'puredata_instrument', observed
    assert observed['pd_peak'] > 0, observed
    assert observed['pd_worker_underruns'] == 0, observed
    return {**result, **observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--song', action='store_true', help='Export a full three-minute mixed Pd song.')
    parser.add_argument('--native', action='store_true', help='Check muted until-stopped loop/seek/pause/stop.')
    args = parser.parse_args()
    seconds = 180 if args.song else 4
    library = os.environ.get('DAW_LIBPD_LIBRARY')
    if not library or not Path(library).is_absolute() or not Path(library).is_file():
        parser.error('set DAW_LIBPD_LIBRARY to an existing absolute libpd library')
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='pd-instrument-', dir=ROOT / 'output'))
    with Client() as client:
        # Isolate the actual note-input runtime before checking the mixed song.
        track = instrument_track()
        track['effects'] = []
        track['mixer']['pan'] = 0
        client.replace({'schema_version': 12, 'sample_rate': 48000,
                        'tempo_milli_bpm': 120000, 'tracks': [track]})
        isolated, _ = client.request('POST', '/api/render', {'seconds': 4})
        signal = note_signal_evidence(isolated)
        session = import_fixture(client, args.song)
        original, headers = client.request('POST', '/api/render', {'seconds': seconds})
        assert headers['X-Clipped-Frames'] == '0'
        changed = copy.deepcopy(session)
        pd = next(t for t in changed['tracks'] if t['id'] == 'pd-lead')
        pd['device']['controls'][0]['value'] = 150
        pd['clips'][0]['notes'][0]['frequency_hz'] = 880
        client.replace(changed)
        edited, _ = client.request('POST', '/api/render', {'seconds': seconds})
        assert original != edited
        client.replace(session)
        restored, _ = client.request('POST', '/api/render', {'seconds': seconds})
        assert original == restored
        bundle, _ = client.request('GET', '/api/project')
        hardware = pd_native_check(client) if args.native else None
    with Client() as fresh:
        fresh.json('POST', '/api/project', bundle, binary=True,
                   metadata={'expected_revision': fresh.inspect()['revision']})
        assert portable_data(fresh.inspect()['session']) == portable_data(session)
        reopened, headers = fresh.request('POST', '/api/render', {'seconds': seconds})
        assert reopened == original and headers['X-Clipped-Frames'] == '0'
    (directory / 'session.daw.zip').write_bytes(bundle)
    (directory / 'before-reopen.wav').write_bytes(original)
    (directory / 'after-reopen.wav').write_bytes(reopened)
    (directory / 'isolated-notes.wav').write_bytes(isolated)
    evidence = {**(inspect_wav(original) if args.song else {}), **signal, 'duration_seconds': seconds, 'project': str(directory), 'schema_version': 12,
                'track_count': len(session['tracks']), 'scheduled_pd_note_input': True,
                'note_and_control_edits_change_audio': True, 'checked_snapshot_undo': True,
                'portable_exact_reopen': True, 'byte_identical_export': True,
                'clipped_frames': 0, 'sha256': hashlib.sha256(original).hexdigest(),
                'native_muted': hardware}
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()

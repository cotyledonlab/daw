#!/usr/bin/env python3
"""Exercise the built-in musical fixture via JSONL; requires a built daw binary.

Run from the repository root: python3 examples/musical_workflow_demo.py
Fresh artifacts go under ignored output/musical-workflow-*.
"""
import array
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/sessions/musical-demo.json'
SECONDS = 32


def check_wav(data, sample_rate=48000):
    with wave.open(io.BytesIO(data), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, sample_rate, sample_rate * SECONDS)
        samples = array.array('h', wav.readframes(wav.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(sample) for sample in samples)
    assert 100 < peak < 32767, f'Expected audible audio with headroom; peak={peak}'
    # All sixteen bars must contain audio, not only the first phrase.
    for bar in range(16):
        start = bar * 2 * sample_rate * 2
        assert any(samples[start:start + 2 * sample_rate * 2]), f'Silent bar {bar + 1}'
    return peak


class Client:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary), 'serve'], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.request_id = 0

    def call(self, method, params, *, error=False):
        self.request_id += 1
        self.process.stdin.write(json.dumps({'protocol_version': 1, 'id': str(self.request_id), 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, f'Engine exited during {method}'
        reply = json.loads(line)
        assert reply.get('id') == str(self.request_id), reply
        assert reply.get('ok') is (not error), reply
        return reply.get('error') if error else reply['result']

    def close(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=10) == 0
        self.process.stdout.close()


def main():
    binary = ROOT / 'target/debug/daw'
    if not binary.is_file():
        raise SystemExit('Build the engine first: cargo build --locked')
    (ROOT / 'output').mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='musical-workflow-', dir=ROOT / 'output'))
    original = json.loads(FIXTURE.read_text())
    client = Client(binary)
    try:
        before = client.call('session.inspect', {})
        applied = client.call('session.replace', {'session': original, 'expected_revision': before['revision']})
        assert applied == original
        snapshot = client.call('session.inspect', {})
        invalid = copy.deepcopy(applied)
        invalid['tracks'][2]['device']['kit_id'] = 'unknown-kit'
        client.call('session.replace', {'session': invalid, 'expected_revision': snapshot['revision']}, error=True)
        assert client.call('session.inspect', {}) == snapshot
        client.call('session.replace', {'session': applied, 'expected_revision': before['revision']}, error=True)
        assert client.call('session.inspect', {}) == snapshot
        # Apply the same velocity-only patch emitted by the timeline UI.
        edited = copy.deepcopy(applied)
        edited['tracks'][0]['clips'][0]['notes'][0]['velocity'] = 0.74
        client.call('session.replace', {'session': edited, 'expected_revision': snapshot['revision']})
        # Undo to the original, then save it as a reopenable acceptance project.
        revision = client.call('session.inspect', {})['revision']
        client.call('session.replace', {'session': applied, 'expected_revision': revision})
        saved = directory / 'session.json'
        client.call('session.save', {'path': str(saved)})
        assert json.loads(saved.read_text()) == original
        reports = []
        for name in ('before-reload.wav', 'after-reload.wav'):
            if name.startswith('after'):
                loaded = client.call('session.load', {'path': str(saved), 'expected_revision': client.call('session.inspect', {})['revision']})
                assert loaded == original
                assert [t['device'] for t in loaded['tracks']] == [t['device'] for t in original['tracks']]
            reports.append(client.call('render', {'path': str(directory / name), 'seconds': SECONDS}))
        for report in reports:
            assert report['frames'] == 48000 * SECONDS, report
            assert report['clipped_frames'] == 0, report
        first = (directory / 'before-reload.wav').read_bytes()
        assert first == (directory / 'after-reload.wav').read_bytes(), 'Reload changed deterministic export'
        peak = check_wav(first)
    finally:
        client.close()
    print(f'Musical workflow passed: checked edits, rejection/stale preservation, undo, save/reload, deterministic 32-second WAV, peak {peak}/32767')
    print(f'Artifacts: {directory}')


if __name__ == '__main__':
    main()

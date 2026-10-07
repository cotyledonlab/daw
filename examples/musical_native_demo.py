#!/usr/bin/env python3
"""Muted native smoke: loop built-ins past 60s, pause/seek/restart and EOF release.

Build with native-audio first. Default wall time is 65 seconds; the output remains
muted (listening volume 0). Run: python3 examples/musical_native_demo.py
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=65)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/daw')
    args = parser.parse_args()
    if args.seconds <= 60:
        parser.error('--seconds must exceed 60 to verify the former duration cap')
    process = subprocess.Popen([str(args.binary.resolve()), 'serve'], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    request_id = 0

    def call(method, params=None):
        nonlocal request_id
        request_id += 1
        process.stdin.write(json.dumps({'protocol_version': 1, 'id': str(request_id),
                                       'method': method, 'params': params or {}}) + '\n')
        process.stdin.flush()
        line = process.stdout.readline()
        assert line, f'Engine exited during {method}'
        reply = json.loads(line)
        assert reply.get('id') == str(request_id) and reply.get('ok'), reply
        return reply['result']

    def observe(predicate, seconds=3):
        deadline = time.monotonic() + seconds
        status = None
        while time.monotonic() < deadline:
            status = call('transport.status')
            assert status['state'] not in ('error',), status
            if predicate(status):
                return status
            time.sleep(0.02)
        raise AssertionError(f'Callback transition timed out: {status}')

    try:
        caps = call('capabilities')
        assert caps['timeline_transport']['until_stopped'], 'Build requires native-audio on macOS'
        before = call('session.inspect')
        call('session.load', {'path': str(ROOT / 'examples/sessions/musical-demo.json'),
                              'expected_revision': before['revision']})
        revision = call('session.inspect')['revision']
        started_at = time.monotonic()
        call('transport.play', {'until_stopped': True, 'volume': 0})
        playing = observe(lambda s: s['state'] == 'playing')
        assert playing['until_stopped'], playing
        call('transport.loop', {'region': {'start_frame': 0, 'end_frame': 96000}})
        previous = observe(lambda s: not s['timeline_command_pending'])
        wraps = 0
        after_sixty = None
        while time.monotonic() - started_at < args.seconds:
            time.sleep(0.1)
            current = call('transport.status')
            assert current['state'] == 'playing' and current['until_stopped'], current
            assert current['submitted_frames'] >= previous['submitted_frames'], current
            assert 0 <= current['timeline_frame'] <= 96000, current
            assert current['level'] == 0, 'Listening volume must remain muted'
            wraps += current['timeline_frame'] < previous['timeline_frame']
            if time.monotonic() - started_at > 60 and after_sixty is None:
                after_sixty = current
            previous = current
        assert after_sixty and previous['submitted_frames'] > after_sixty['submitted_frames']
        assert wraps > 0, 'Loop must wrap while submitted output advances'
        call('transport.pause')
        paused = observe(lambda s: s['state'] == 'paused')
        count = paused['submitted_frames']
        time.sleep(0.1)
        assert call('transport.status')['submitted_frames'] == count
        call('transport.seek', {'frame': 24000})
        at = observe(lambda s: not s['timeline_command_pending'])
        assert at['timeline_frame'] == 24000 and at['submitted_frames'] == count, at
        call('transport.resume')
        resumed = observe(lambda s: s['state'] == 'playing' and s['submitted_frames'] > count)
        assert resumed['until_stopped'], resumed
        stopped = call('transport.stop')
        assert stopped['state'] == 'stopped' and stopped['resources_released'], stopped
        assert call('session.inspect')['revision'] == revision
        call('transport.play', {'until_stopped': True, 'volume': 0})
        restarted = observe(lambda s: s['state'] == 'playing' and s['submitted_frames'] > 0)
        assert restarted['loop_region'] is None and restarted['until_stopped'], restarted
        # EOF with an active stream must release the owner and exit cleanly.
        process.stdin.close()
        assert process.wait(timeout=10) == 0, 'Engine failed to release playback on EOF'
        print(json.dumps({'wall_seconds': round(time.monotonic() - started_at, 2),
                          'submitted_frames': stopped['submitted_frames'], 'loop_wraps': wraps,
                          'pause_seek_resume': True, 'stop_restart_eof_release': True,
                          'max_render_microseconds': stopped['max_render_microseconds'],
                          'callbacks_over_buffer_budget': stopped['callbacks_over_buffer_budget']}, indent=2))
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.wait(timeout=10)
        process.stdout.close()


if __name__ == '__main__':
    main()

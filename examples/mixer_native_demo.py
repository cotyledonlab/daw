#!/usr/bin/env python3
"""Muted real-hardware acceptance for saved mixer state and native peak meters.

Build with native-audio first; run from the root with
python3 examples/mixer_native_demo.py. Listening volume stays zero throughout.
"""
import argparse
import copy
import json
from pathlib import Path
import signal
import time

from musical_workflow_demo import Client, FIXTURE, ROOT


def timeout(_signum, _frame):
    raise TimeoutError('Native engine request exceeded 10 seconds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/daw')
    args = parser.parse_args()
    client = Client(args.binary.resolve())
    previous = signal.signal(signal.SIGALRM, timeout)

    def call(method, params=None):
        signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            return client.call(method, params or {})
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def observe(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        current = None
        while time.monotonic() < deadline:
            current = call('transport.status')
            assert current['state'] != 'error', current
            assert current['level'] == 0, 'Listening output must stay muted'
            if predicate(current):
                return current
            time.sleep(0.025)
        raise AssertionError(f'Native transition/meter timed out: {current}')

    def replace(session):
        revision = call('session.inspect')['revision']
        assert call('session.replace', {'session': session, 'expected_revision': revision}) == session

    def track_values(status):
        snapshot = status.get('mixer_meters')
        if not snapshot:
            return None
        return {track['id']: track for track in snapshot['tracks']}

    session = copy.deepcopy(json.loads(FIXTURE.read_text()))
    session['schema_version'] = 10
    for track in session['tracks']:
        track['mixer'] = {'gain': 1, 'pan': 0, 'mute': False, 'solo': False}
    selected = session['tracks'][0]['id']
    session['tracks'][0]['mixer'].update(solo=True, pan=-1)
    try:
        assert call('capabilities')['timeline_transport']['until_stopped'], 'Build native-audio on macOS first'
        replace(session)
        call('transport.play', {'until_stopped': True, 'volume': 0})

        def audible_left(status):
            tracks = track_values(status)
            if status['state'] != 'playing' or not tracks:
                return False
            assert set(tracks) == {track['id'] for track in session['tracks']}, tracks
            active = tracks[selected]['peak']
            master = status['mixer_meters']['master']['peak']
            assert abs(active[1]) < 1e-12 and abs(master[1]) < 1e-12, status
            assert all(all(peak == 0 for peak in value['peak']) for key, value in tracks.items() if key != selected), status
            return active[0] > 0 and master[0] > 0

        first = observe(audible_left)
        assert first['until_stopped']
        call('transport.pause')
        paused = observe(lambda status: status['state'] == 'paused')
        count = paused['submitted_frames']
        time.sleep(0.1)
        assert call('transport.status')['submitted_frames'] == count
        call('transport.resume')
        observe(lambda status: status['submitted_frames'] > count and audible_left(status))
        stopped = call('transport.stop')
        assert stopped['state'] == 'stopped' and stopped['resources_released'], stopped
        assert call('session.inspect')['session'] == session
        session['tracks'][0]['mixer']['mute'] = True
        replace(session)
        call('transport.play', {'until_stopped': True, 'volume': 0})

        def muted_solo(status):
            tracks = track_values(status)
            if status['state'] != 'playing' or status['submitted_frames'] == 0 or not tracks:
                return False
            assert status['mixer_meters']['master']['peak'] == [0, 0], status
            assert all(value['peak'] == [0, 0] for value in tracks.values()), status
            return True

        observe(muted_solo)
        # EOF while active must release the native owner and terminate cleanly.
        client.close()
        print(json.dumps({'schema': 10, 'solo_track': selected, 'hard_left_pre_monitor': True,
                          'pause_resume': True, 'stop_resources_released': True,
                          'mute_wins_over_solo': True, 'muted_output': True,
                          'active_eof_release': True}, indent=2))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        process = client.process
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
                process.wait(timeout=5)
        if not process.stdout.closed:
            process.stdout.close()


if __name__ == '__main__':
    main()

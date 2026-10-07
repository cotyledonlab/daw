"""Real libpd preset note-input proof, independent of frozen/source PCM paths."""
import ctypes as C
import math
import os
from pathlib import Path
import unittest

from native.puredata.block_probe import _bind, _dsp, _measure

LIBRARY = os.environ.get('DAW_LIBPD_LIBRARY')
PATCH = Path(__file__).with_name('instrument.pd')


@unittest.skipUnless(LIBRARY and Path(LIBRARY).is_absolute() and Path(LIBRARY).is_file(),
                     'set DAW_LIBPD_LIBRARY to an absolute libpd library')
class PdInstrumentRuntimeTests(unittest.TestCase):
    def test_note_receivers_pitch_gate_velocity_cutoff_and_reset_reach_live_blocks(self):
        api = _bind(LIBRARY)
        api.libpd_init()
        instance = api.libpd_new_instance()
        self.assertTrue(instance, 'multi-instance libpd required')
        patch = None
        try:
            api.libpd_set_instance(instance)
            self.assertEqual(api.libpd_init_audio(0, 2, 48000), 0)
            patch = api.libpd_openfile(os.fsencode(PATCH.name), os.fsencode(PATCH.parent))
            self.assertTrue(patch)
            prefix = api.libpd_getdollarzero(patch)
            def send(name, value):
                self.assertEqual(api.libpd_float(f'{prefix}-{name}'.encode(), value), 0)
            def process(blocks=192):
                inputs = (C.c_float * 128)()
                outputs = (C.c_float * 128)()
                left = []
                for _ in range(blocks):
                    self.assertEqual(api.libpd_process_float(1, inputs, outputs), 0)
                    self.assertEqual(list(outputs)[::2], list(outputs)[1::2])
                    self.assertTrue(all(math.isfinite(v) for v in outputs))
                    left.extend(list(outputs)[::2])
                return left
            send('reset', 1)
            send('frequency', 440)
            send('cutoff', 4000)
            send('velocity', .8)
            send('gate', 0)
            _dsp(api, True)
            self.assertEqual(max(map(abs, process())), 0)
            send('reset', 1)
            send('gate', 1)
            first_audio = process()
            first = _measure(first_audio[1024:])
            self.assertAlmostEqual(first['frequency_hz'], 440, delta=2)
            send('frequency', 660)
            second = _measure(process()[1024:])
            self.assertAlmostEqual(second['frequency_hz'], 660, delta=2)
            send('velocity', .4)
            quiet = _measure(process()[1024:])
            self.assertAlmostEqual(quiet['rms'] / second['rms'], .5, delta=.01)
            send('cutoff', 100)
            filtered = _measure(process()[1024:])
            self.assertLess(filtered['rms'], quiet['rms'] * .25)
            send('gate', 0)
            self.assertEqual(max(map(abs, process())), 0)
            send('reset', 1)
            send('cutoff', 4000)
            send('frequency', 440)
            send('velocity', .8)
            send('gate', 1)
            reset_audio = process()
            self.assertEqual(reset_audio, first_audio)
        finally:
            _dsp(api, False)
            if patch:
                api.libpd_closefile(patch)
            api.libpd_free_instance(instance)


if __name__ == '__main__':
    unittest.main()

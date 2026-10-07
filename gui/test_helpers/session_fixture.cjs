const assert = require('node:assert/strict');

// Math.pow may differ by an ULP between V8 versions/platforms. Only generated
// pitch values have that tolerance; every other saved field must match exactly.
function assertSessionFixture(actual, expected) {
  const normalized = structuredClone(actual);
  expected.tracks.forEach((track, trackIndex) => {
    (track.clips || []).forEach((clip, clipIndex) => {
      (clip.notes || []).forEach((note, noteIndex) => {
        const generated = normalized.tracks[trackIndex]?.clips?.[clipIndex]?.notes?.[noteIndex];
        assert.ok(generated && Number.isFinite(generated.frequency_hz), 'missing finite fixture pitch');
        assert.ok(Math.abs(generated.frequency_hz - note.frequency_hz) < 1e-10,
          `fixture pitch mismatch: ${generated.frequency_hz} versus ${note.frequency_hz}`);
        generated.frequency_hz = note.frequency_hz;
      });
    });
  });
  assert.deepEqual(normalized, expected);
}

module.exports = {assertSessionFixture};

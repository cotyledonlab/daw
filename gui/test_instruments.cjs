const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const E = require('./editor.js');
const {assertSessionFixture} = require('./test_helpers/session_fixture.cjs');

test('musical fixture is sixteen bars of independently saved synth/bass/drums presets', () => {
  const session = E.createMusicalDemoSession();
  assert.equal(E.validate(session), null);
  assert.equal(session.schema_version, 9);
  assert.deepEqual(session.tracks.map(track => track.device.kind), ['synth','synth','drumkit']);
  assert.deepEqual(session.tracks.map(track => track.device), [
    {kind:'synth',waveform:'saw',gain:0.13,attack_ms:8,release_ms:90,cutoff_hz:2800},
    {kind:'synth',waveform:'square',gain:0.13,attack_ms:4,release_ms:60,cutoff_hz:700},
    {kind:'drumkit',kit_id:'factory-v1',gain:0.45},
  ]);
  for (const track of session.tracks) assert.equal(Math.max(...track.clips.map(c => c.start_frame + c.length_frames)), 32 * 48000);
  assert.deepEqual(JSON.parse(JSON.stringify(session)), session);
  assertSessionFixture(session, JSON.parse(fs.readFileSync(`${__dirname}/../examples/sessions/musical-demo.json`)));
  const second = E.createMusicalDemoSession();
  session.tracks[2].device.gain = 0;
  session.tracks[2].clips[0].notes[0].velocity = 0;
  assert.equal(second.tracks[2].device.gain, 0.45);
  assert.equal(second.tracks[2].clips[0].notes[0].velocity, 0.85);
});

test('instrument helpers upgrade sine arrangements and clone devices/notes on edit and duplicate', () => {
  const source = E.createDemoSession();
  for (const kind of ['drumkit','synth']) {
    const next = E.addNoteTrack(source, kind, kind);
    assert.equal(next.schema_version, 9); assert.equal(E.validate(next), null);
    assert.equal(next.tracks.at(-1).device.kind, kind);
    next.tracks[0].clips[0].notes[0].velocity = 0;
    assert.notEqual(source.tracks[0].clips[0].notes[0].velocity, 0);
  }
  const session = E.createMusicalDemoSession();
  const duplicate = E.duplicateClip(session, 2, 'beat-1', 'copy', 32 * 48000);
  duplicate.tracks[2].clips.at(-1).notes[0].velocity = 0;
  assert.equal(session.tracks[2].clips[0].notes[0].velocity, 0.85);
  const edited = E.editNote(session, 2, 'beat-1', 'hit-1', {velocity:0.25});
  assert.equal(edited.tracks[2].clips[0].notes[0].velocity, 0.25);
  assert.equal(session.tracks[2].clips[0].notes[0].velocity, 0.85);
});

test('GUI rejects unknown kit IDs, invalid drum pitches and invalid saved synth settings', () => {
  const original = E.createMusicalDemoSession();
  const kit = structuredClone(original); kit.tracks[2].device.kit_id = 'missing';
  assert.match(E.validate(kit), /Unknown built-in drum kit/);
  const pad = structuredClone(original); pad.tracks[2].clips[0].notes[0].frequency_hz = E.midiToHz(60);
  assert.match(E.validate(pad), /MIDI 36 kick/);
  assert.throws(() => E.editNote(original, 2, 'beat-1', 'hit-1', {frequency_hz:E.midiToHz(60)}), /MIDI 36 kick/);
  for (const patch of [{waveform:'triangle'},{attack_ms:-1},{release_ms:2001},{cutoff_hz:24000},{gain:2}]) {
    const candidate = structuredClone(original); Object.assign(candidate.tracks[0].device, patch);
    assert.ok(E.validate(candidate));
  }
  assert.equal(E.validate(original), null);
});

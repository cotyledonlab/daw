const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('./editor.js');
const limits = {max_seconds:180,builtin_max_seconds:180,runtime_max_seconds:60,plugin_max_seconds:10};
test('mixed builtin/PCM song gets extended export, old engines retain 60 seconds', () => {
  const song = E.editMixer(E.createMusicalDemoSession(),0,{pan:0.3});
  const mixed = E.addAudioTrack(song,'audio');
  assert.equal(E.exportMaximum(mixed,limits),180);
  assert.equal(E.exportMaximum(mixed),60);
  assert.equal(E.exportMaximum(mixed,{max_seconds:60}),60);
});
test('runtime and plugin projects keep narrower limits including unknown source kinds', () => {
  for (const kind of ['supercollider','csound','puredata','unknown']) {
    const session = {tracks:[{device:{kind},effects:[]}]};
    assert.equal(E.exportMaximum(session,limits),60);
    session.tracks[0].effects.push({kind:'vst3'});
    assert.equal(E.exportMaximum(session,limits),10);
  }
  assert.equal(E.exportMaximum({tracks:[{device:{kind:'sine'},effects:[{kind:'au'}]}]},limits),10);
});

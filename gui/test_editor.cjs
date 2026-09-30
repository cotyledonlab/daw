const assert = require('node:assert/strict');
const test = require('node:test');
const Editor = require('./editor.js');

function track(id, overrides = {}) {
  return {
    id,
    device: { kind: 'sine', frequency_hz: 440, gain: 0.5 },
    ...overrides,
  };
}

function session(tracks = [track('one')], overrides = {}) {
  return { schema_version: 1, sample_rate: 48000, tracks, ...overrides };
}

test('editor accepts v1 and continuous, clip-free v4 sine sessions only', () => {
  assert.equal(Editor.supported(session()), true);
  const v4 = session([track('one', { mode: 'continuous', clips: [], effects: [] })], { schema_version: 4 });
  assert.equal(Editor.supported(v4), true);
  for (const version of [2, 3]) assert.equal(Editor.supported(session([], { schema_version: version })), false);
  const nonSine = session([track('one', { device: { kind: 'noise' } })]);
  assert.equal(Editor.validate(nonSine), 'Only continuous sine tracks can be edited here.');
  assert.equal(Editor.supported(session([track('one', { device: { kind: 'noise' }, mode: 'continuous', clips: [] })], { schema_version: 4 })), false);
  assert.equal(Editor.supported(session([track('one', { mode: 'sequenced', clips: [] })], { schema_version: 4 })), false);
  assert.equal(Editor.supported(session([track('one', { mode: 'continuous', clips: [{ source: 'a.wav' }] })], { schema_version: 4 })), false);
});

test('adding an effect explicitly upgrades v1 and preserves existing track data', () => {
  const original = session([
    track('first', { name: 'Lead', custom: { keep: [1, 2] } }),
    track('second', { name: 'Bass', custom: 'untouched' }),
  ], { title: 'Draft' });
  const updated = Editor.addEffect(original, 0, { kind: 'gain', gain: 0.75, bypass: false }, 'fx-1');
  assert.equal(updated.schema_version, 4);
  assert.equal(updated.tempo_milli_bpm, 120000);
  assert.equal(updated.tracks[0].mode, 'continuous');
  assert.deepEqual(updated.tracks[0].clips, []);
  assert.deepEqual(updated.tracks[0].custom, { keep: [1, 2] });
  assert.deepEqual(updated.tracks[0].effects, [{ kind: 'gain', gain: 0.75, bypass: false, id: 'fx-1' }]);
  assert.deepEqual(updated.tracks[1].custom, 'untouched');
  assert.deepEqual(updated.tracks[1].effects, []);
  assert.equal(original.schema_version, 1);
  assert.equal(Object.hasOwn(original.tracks[0], 'effects'), false);
});

test('clones opaque VST3 state, parameters, and automation points without mutation', () => {
  const opaque = { kind: 'vst3', class_id: 'cid', state: { bytes: [0, 255], vendor: { token: 'opaque' } }, parameters: [{ id: 9, value: 0.4 }], points: [{ frame: 12, value: 0.8 }] };
  const original = session([track('one', { mode: 'continuous', clips: [], effects: [] })], { schema_version: 4 });
  const input = structuredClone(opaque);
  const updated = Editor.addEffect(original, 0, input, 'plugin-1');
  input.state.bytes[0] = 99;
  input.parameters[0].value = 1;
  input.points[0].frame = 100;
  assert.deepEqual(updated.tracks[0].effects[0], { ...opaque, id: 'plugin-1' });
  updated.tracks[0].effects[0].state.vendor.token = 'changed';
  updated.tracks[0].effects[0].parameters[0].value = 0;
  assert.deepEqual(original.tracks[0].effects, []);
  assert.deepEqual(opaque.state, { bytes: [0, 255], vendor: { token: 'opaque' } });
});

test('enforces per-track and session VST3 limits and the 48 kHz requirement', () => {
  const manyEffects = Array.from({ length: 16 }, (_, i) => ({ id: `fx-${i}`, kind: 'gain' }));
  const one = session([track('one', { mode: 'continuous', clips: [], effects: manyEffects })], { schema_version: 4 });
  assert.throws(() => Editor.addEffect(one, 0, { kind: 'gain' }, 'extra'), /up to 16 effects/);

  const plugin = { kind: 'vst3', class_id: 'cid' };
  const eight = Array.from({ length: 8 }, (_, i) => track(`t${i}`, { mode: 'continuous', clips: [], effects: [{ ...plugin, id: `p${i}` }] }));
  const capped = session(eight, { schema_version: 4 });
  assert.equal(Editor.plugins(capped).length, 8);
  assert.throws(() => Editor.addEffect(capped, 0, plugin, 'ninth'), /48 kHz and at most eight/);

  const wrongRate = session([track('one', { mode: 'continuous', clips: [], effects: [] })], { schema_version: 4, sample_rate: 44100 });
  assert.throws(() => Editor.addEffect(wrongRate, 0, plugin, 'plugin'), /48 kHz and at most eight/);
});

test('removing an effect clears only its automation and preserves all other lanes and state', () => {
  const target = { id: 'remove-me', kind: 'gain', gain: 1, state: { saved: [1, 2] } };
  const keep = { id: 'keep-me', kind: 'vst3', state: { bytes: [8, 9] }, parameters: [{ id: 3, value: 0.5 }], points: [{ frame: 20, value: 0.2 }] };
  const lanes = [
    { effect_id: target.id, parameter: 'gain', points: [{ frame: 1, value: 0.5 }] },
    { effect_id: keep.id, parameter: 'parameter:3', points: [{ frame: 2, value: 0.7 }] },
    { effect_id: 'other', parameter: 'gain', points: [{ frame: 3, value: 1 }] },
  ];
  const original = session([track('one', { effects: [target, keep], automation: lanes }), track('two', { effects: [{ ...target, id: 'same-id-other-track' }], automation: [{ effect_id: 'same-id-other-track' }] })]);
  const updated = Editor.removeEffect(original, 0, target.id);
  assert.deepEqual(updated.tracks[0].effects, [keep]);
  assert.deepEqual(updated.tracks[0].automation, lanes.slice(1));
  assert.deepEqual(updated.tracks[1], original.tracks[1]);
  assert.deepEqual(original.tracks[0].effects, [target, keep]);
  assert.deepEqual(original.tracks[0].automation, lanes);
});

test('validates sample rate and plugin session sample rate bounds', () => {
  assert.match(Editor.validate(session([], { sample_rate: 7999 })), /between 8,000 and 192,000/);
  assert.match(Editor.validate(session([], { sample_rate: 192001 })), /between 8,000 and 192,000/);
  const pluginSession = session([track('one', { mode: 'continuous', clips: [], effects: [{ kind: 'vst3', id: 'p' }] })], { schema_version: 4, sample_rate: 44100 });
  assert.match(Editor.validate(pluginSession), /VST3 effects require a 48 kHz session/);
});

test('plugins returns VST3 effects across tracks and omits other effects', () => {
  const p1 = { kind: 'vst3', id: 'p1' };
  const p2 = { kind: 'vst3', id: 'p2' };
  const data = session([
    track('one', { effects: [{ kind: 'gain', id: 'g1' }, p1] }),
    track('two', { effects: [p2, { kind: 'other', id: 'x' }] }),
  ]);
  assert.deepEqual(Editor.plugins(data), [p1, p2]);
});

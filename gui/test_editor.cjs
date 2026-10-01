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

test('supports v6 only for continuous empty-clip sine and SuperCollider tracks with gain effects', () => {
  const source = {
    kind: 'supercollider', synthdef_hex: '534367660000', synth_name: 'tone',
    duration_frames: 48000, gain: 0.7,
    controls: [{ name: 'freq', values: [440], points: [{ frame: 120, values: [660] }] }],
  };
  const data = session([
    track('sine', { mode: 'continuous', clips: [], effects: [{ kind: 'gain', id: 'g1', gain: 0.8, bypass: false }] }),
    track('sc', { mode: 'continuous', clips: [], effects: [], device: source }),
  ], { schema_version: 6, tempo_milli_bpm: 120000 });
  assert.equal(Editor.supported(data), true);
  assert.equal(Editor.validate(data), null);
  assert.equal(Editor.supported(session([track('sc', { mode: 'continuous', clips: [], effects: [], device: source })], { schema_version: 5 })), false);
  assert.equal(Editor.supported(session([track('sc', { mode: 'continuous', clips: [], effects: [{ kind: 'au', id: 'au' }], device: source })], { schema_version: 6 })), false);
  assert.equal(Editor.supported(session([track('sc', { mode: 'continuous', clips: [], effects: [{ kind: 'vst3', id: 'vst' }], device: source })], { schema_version: 6 })), false);
  assert.equal(Editor.supported(session([track('sc', { mode: 'sequenced', clips: [], effects: [], device: source })], { schema_version: 6 })), false);
  assert.equal(Editor.supported(session([track('sc', { mode: 'continuous', clips: [{ source: 'a.wav' }], effects: [], device: source })], { schema_version: 6 })), false);
});

test('validates v6 SuperCollider native bases and retains control points for Rust validation', () => {
  const device = {
    kind: 'supercollider', synthdef_hex: 'abcd', synth_name: 'tone', duration_frames: 48000, gain: 1,
    controls: [{ name: 'array', values: Array(256).fill(0), points: [{ frame: 240, values: [1] }] }],
  };
  const make = overrides => session([track('sc', { mode: 'continuous', clips: [], effects: [], device: { ...device, ...overrides } })], { schema_version: 6 });
  assert.equal(Editor.validate(make({})), null);
  assert.notEqual(Editor.validate(make({ gain: -0.01 })), null);
  assert.notEqual(Editor.validate(make({ synth_name: '' })), null);
  assert.notEqual(Editor.validate(make({ duration_frames: 0 })), null);
  assert.notEqual(Editor.validate(make({ duration_frames: 1.5 })), null);
  assert.notEqual(Editor.validate(make({ synthdef_hex: 'abc' })), null);
  assert.notEqual(Editor.validate(make({ synthdef_hex: 'zz' })), null);
  assert.notEqual(Editor.validate(make({ synthdef_hex: 'a'.repeat(122882) })), null);
  assert.notEqual(Editor.validate(make({ controls: [{ name: 'x', values: [], points: [] }] })), null);
  assert.notEqual(Editor.validate(make({ controls: [{ name: 'x', values: Array(257).fill(0), points: [] }] })), null);
  assert.notEqual(Editor.validate(make({ controls: [{ name: 'x', values: [Infinity], points: [] }] })), null);
  assert.notEqual(Editor.validate(make({ controls: [{ name: 'x', values: [1e300], points: [] }] })), null);
  assert.deepEqual(make({}).tracks[0].device.controls[0].points, [{ frame: 240, values: [1] }]);
});

test('source controls are editable only when metadata confirms a non-init-rate unautomated control', () => {
  assert.equal(typeof Editor.sourceControlsEditable, 'function');
  const control = { name: 'freq', values: [440], points: [] };
  assert.equal(Editor.sourceControlsEditable(control, { controls: [{ name: 'freq', default_values: [440], initialization_rate: false }] }), true);
  assert.equal(Editor.sourceControlsEditable(control, { controls: [{ name: 'freq', default_values: [440], initialization_rate: true }] }), false);
  assert.equal(Editor.sourceControlsEditable(control, { controls: [{ name: 'freq', default_values: [440, 660], initialization_rate: false }] }), false);
  assert.equal(Editor.sourceControlsEditable(control, { controls: [] }), false);
  assert.equal(Editor.sourceControlsEditable({ ...control, points: [{ frame: 1, values: [880] }] }, { controls: [{ name: 'freq', default_values: [440], initialization_rate: false }] }), false);
});

test('v6 gain editing and removal preserve opaque SynthDef bytes and saved control events', () => {
  const device = {
    kind: 'supercollider', synthdef_hex: '5343676600aaff', synth_name: 'opaque-tone', duration_frames: 24000, gain: 0.5,
    controls: [{ name: 'freq', values: [440], points: [{ frame: 100, values: [880] }] }],
  };
  const original = session([track('sc', { mode: 'continuous', clips: [], effects: [], device })], { schema_version: 6 });
  const withGain = Editor.addEffect(original, 0, { kind: 'gain', gain: 0.9, bypass: false }, 'gain-1');
  assert.deepEqual(withGain.tracks[0].device, device);
  const removed = Editor.removeEffect(withGain, 0, 'gain-1');
  assert.deepEqual(removed.tracks[0].device, device);
  assert.deepEqual(original.tracks[0].device, device);
  assert.throws(() => Editor.addEffect(original, 0, { kind: 'vst3', class_id: 'cid' }, 'foreign'), /SuperCollider|gain/);
});

function csoundTrack(id, overrides = {}) {
  return track(id, {
    mode: 'continuous', clips: [], effects: [],
    device: { kind: 'csound', program: '<CsoundSynthesizer/>', duration_frames: 48000, gain: 0.5, controls: [] },
    ...overrides,
  });
}

test('editor supports schema-v7 continuous sine, SuperCollider, and Csound tracks with gain chains', () => {
  const sc = {
    kind: 'supercollider', synthdef_hex: '534367660000', synth_name: 'tone',
    duration_frames: 48000, gain: 0.7, controls: [{ name: 'freq', values: [440], points: [] }],
  };
  const cs = csoundTrack('cs').device;
  cs.controls = [{ name: 'amplitude', value: 0.1, points: [] }];
  const data = session([
    track('sine', { mode: 'continuous', clips: [], effects: [{ kind: 'gain', id: 'g1' }] }),
    track('sc', { mode: 'continuous', clips: [], effects: [], device: sc }),
    csoundTrack('cs', { device: cs }),
  ], { schema_version: 7, tempo_milli_bpm: 120000 });
  assert.equal(Editor.supported(data), true);
  assert.equal(Editor.validate(data), null);
  assert.deepEqual(Editor.sources(data), [data.tracks[1], data.tracks[2]]);
  assert.equal(Editor.supported(session([csoundTrack('cs', { mode: 'sequenced' })], { schema_version: 7 })), false);
  assert.equal(Editor.supported(session([csoundTrack('cs', { clips: [{ source: 'x.wav' }] })], { schema_version: 7 })), false);
  assert.equal(Editor.supported(session([csoundTrack('cs', { effects: [{ kind: 'vst3', id: 'v' }] })], { schema_version: 7 })), false);
  assert.equal(Editor.supported(session([track('noise', { mode: 'continuous', clips: [], effects: [], device: { kind: 'noise' } })], { schema_version: 7 })), false);
});

test('validates bounded Csound program, duration, gain, and unique saved scalar controls', () => {
  const make = (device, sampleRate = 48000) => session([csoundTrack('cs', { device })], { schema_version: 7, sample_rate: sampleRate });
  const base = csoundTrack('cs').device;
  assert.equal(Editor.validate(make(base)), null);
  assert.equal(Editor.validate(make({ ...base, program: 'é'.repeat(30720) })), null);
  for (const program of ['', 'x\0y', 'é'.repeat(30721), '\ud800']) assert.notEqual(Editor.validate(make({ ...base, program })), null);
  for (const duration of [0, 47, 480001, 1.5]) assert.notEqual(Editor.validate(make({ ...base, duration_frames: duration })), null);
  assert.equal(Editor.validate(make({ ...base, duration_frames: 8 }, 8000)), null);
  for (const gain of [-0.01, 1.01, Infinity]) assert.notEqual(Editor.validate(make({ ...base, gain })), null);
  for (const controls of [
    Array(65).fill({ name: 'x', value: 1, points: [] }),
    [{ name: 'é'.repeat(65), value: 1, points: [] }],
    [{ name: 'bad\0name', value: 1, points: [] }],
    [{ name: 'dup', value: 1, points: [] }, { name: 'dup', value: 2, points: [] }],
    [{ name: 'bad', value: Infinity, points: [] }],
    [{ name: 'bad', value: 1, points: {} }],
  ]) assert.notEqual(Editor.validate(make({ ...base, controls })), null);
  const automated = make({ ...base, controls: [{ name: 'amp', value: 0.5, points: [{ frame: 0, value: 0.7 }] }] });
  assert.equal(Editor.validate(automated), null);
  assert.deepEqual(automated.tracks[0].device.controls[0].points, [{ frame: 0, value: 0.7 }]);
});

test('adding Csound explicitly upgrades v1, v4, or v6 and appends to v7 without mutation', () => {
  const program = '<CsoundSynthesizer>\nopaque é text\n</CsoundSynthesizer>';
  for (const [version, original] of [
    [1, session([track('old', { name: 'legacy' })], { title: 'draft' })],
    [4, session([track('old', { mode: 'continuous', clips: [], effects: [{ kind: 'gain', id: 'g', gain: 0.8 }] })], { schema_version: 4, tempo_milli_bpm: 90000 })],
    [6, session([track('old', { mode: 'continuous', clips: [], effects: [], device: { kind: 'supercollider', synth_name: 'opaque', synthdef_hex: 'aabb', duration_frames: 48000, gain: 1, controls: [] } })], { schema_version: 6, tempo_milli_bpm: 110000 })],
  ]) {
    const snapshot = structuredClone(original);
    const updated = Editor.addCsound(original, program, 'new-cs', 24000);
    assert.equal(updated.schema_version, 7);
    assert.equal(updated.tracks.at(-1).device.program, program);
    assert.equal(updated.tracks.at(-1).device.gain, 0.5);
    assert.deepEqual(updated.tracks.at(-1).device.controls, []);
    assert.equal(updated.tracks.at(-1).device.duration_frames, 24000);
    assert.equal(Editor.validate(updated), null);
    if (version === 1) {
      assert.equal(updated.tempo_milli_bpm, 120000);
      assert.equal(updated.tracks[0].mode, 'continuous');
      assert.deepEqual(updated.tracks[0].clips, []);
      assert.deepEqual(updated.tracks[0].effects, []);
    } else assert.deepEqual(updated.tracks[0], original.tracks[0]);
    assert.deepEqual(original, snapshot);
  }
  const existingV7 = session([csoundTrack('first', { device: { ...csoundTrack('first').device, program: 'existing CSD' } })], { schema_version: 7, tempo_milli_bpm: 100000 });
  const extendedV7 = Editor.addCsound(existingV7, program, 'second-cs', 96000);
  assert.equal(extendedV7.schema_version, 7);
  assert.equal(extendedV7.tempo_milli_bpm, 100000);
  assert.equal(extendedV7.tracks.length, 2);
  assert.equal(extendedV7.tracks[0].device.program, 'existing CSD');
  assert.equal(extendedV7.tracks[1].device.program, program);
  assert.throws(() => Editor.addCsound(session([], { schema_version: 5 }), program, 'cs', 48000), /supports continuous sine sessions/);
  assert.throws(() => Editor.addCsound(session([track('old', { mode: 'continuous', clips: [], effects: [{ kind: 'vst3', id: 'p' }] })], { schema_version: 4 }), program, 'cs', 48000), /gain effects only/);
});

test('Csound creation rejects invalid IDs, text, duration, and duplicate track IDs', () => {
  const base = session();
  for (const [program, id, frames] of [
    ['', 'cs', 48000], ['a\0b', 'cs', 48000], ['é'.repeat(30721), 'cs', 48000],
    ['program', '', 48000], ['program', 'é'.repeat(65), 48000], ['program', 'bad\0id', 48000],
    ['program', 'cs', 47], ['program', 'cs', 480001], ['program', 'cs', 1.25],
  ]) assert.throws(() => Editor.addCsound(base, program, id, frames));
  assert.throws(() => Editor.addCsound(base, 'program', 'one', 48000), /unique/);
});

test('Csound gain effects are allowed in v7 while foreign effects are rejected', () => {
  const original = session([csoundTrack('cs')], { schema_version: 7 });
  const updated = Editor.addEffect(original, 0, { kind: 'gain', gain: 0.75, bypass: false }, 'gain-1');
  assert.equal(updated.tracks[0].effects[0].id, 'gain-1');
  assert.deepEqual(updated.tracks[0].device, original.tracks[0].device);
  assert.throws(() => Editor.addEffect(original, 0, { kind: 'vst3' }, 'plugin'), /gain effects only/);
});

test('Csound controls are editable without SC metadata only for finite scalar values with no points', () => {
  const control = { name: 'freq', value: 440, points: [] };
  assert.equal(Editor.sourceControlsEditable(control, null, 'csound'), true);
  assert.equal(Editor.sourceControlsEditable({ ...control, value: Infinity }, null, 'csound'), false);
  assert.equal(Editor.sourceControlsEditable({ ...control, points: [{ frame: 0, value: 660 }] }, null, 'csound'), false);
  assert.equal(Editor.sourceControlsEditable({ name: 'freq', values: [440], points: [] }, null, 'supercollider'), false);
});

test('adding Csound controls clones state and rejects duplicates, invalid data, wrong sources, and overflow', () => {
  const original = session([track('sine', { mode: 'continuous', clips: [], effects: [] }), csoundTrack('cs')], { schema_version: 7 });
  const updated = Editor.addCsoundControl(original, 1, 'amplitude', 0.2);
  assert.deepEqual(updated.tracks[1].device.controls, [{ name: 'amplitude', value: 0.2, points: [] }]);
  assert.deepEqual(original.tracks[1].device.controls, []);
  for (const [name, value] of [['', 1], ['é'.repeat(65), 1], ['bad\0name', 1], ['unpaired\ud800', 1], ['finite', Infinity]]) {
    assert.throws(() => Editor.addCsoundControl(original, 1, name, value));
  }
  const duplicate = Editor.addCsoundControl(original, 1, 'amplitude', 0.2);
  assert.throws(() => Editor.addCsoundControl(duplicate, 1, 'amplitude', 0.3), /unique/);
  assert.throws(() => Editor.addCsoundControl(original, 0, 'amp', 1), /Csound source/);
  const full = structuredClone(original);
  full.tracks[1].device.controls = Array.from({ length: 64 }, (_, i) => ({ name: `c${i}`, value: i, points: [] }));
  assert.throws(() => Editor.addCsoundControl(full, 1, 'extra', 0), /up to 64/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('./editor.js');
const Mixer = require('./mixer.js');
const History = require('./history.js');
class Node {
  constructor(tag, doc) { this.tag = tag; this.ownerDocument = doc; this.children = []; this.listeners = {}; this.attrs = {}; this.value = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key,value) { this.attrs[key] = String(value); }
  addEventListener(type,listener) { this.listeners[type] = listener; }
  all(tag) { return this.children.flatMap(n => [...(n.tag === tag ? [n] : []), ...n.all(tag)]); }
  async fire(type,event={}) { return await this.listeners[type]?.({preventDefault(){},...event}); }
}
function numberInputs(container) { return container.all('input').filter(n => n.type === 'number'); }
function mixerButtons(container) {
  const buttons = container.all('button'), ids = [...new Set(buttons.map(n => n.attrs['aria-label'].split(' ')[0]))];
  return ids.flatMap(id => ['apply mixer values','mute','solo'].map(label => buttons.find(n => n.attrs['aria-label'] === `${id} ${label}`)));
}
function masterReadout(container) { return container.all('span').find(n => n.attrs['aria-label'] === 'Master peak').children[1]; }
function setup() {
  const doc = {createElement:tag => new Node(tag,doc)};
  const container = doc.createElement('section'), errors = [], edits = [], history = new History();
  let current = E.createMusicalDemoSession(), fail = false;
  const view = Mixer.create(container,{editor:E,onError:e=>errors.push(e),onEdit:async next => {
    edits.push(next);
    if (fail) { errors.push('Could not apply saved mixer.'); view.render(current); return false; }
    history.commit(current,next); current = next; view.render(current); return true;
  }});
  view.render(current);
  return {container,errors,edits,history,view,get current(){return current;},set fail(v){fail=v;}};
}
test('explicit mixer edits upgrade built-in/gain sessions without mutating precise saved data', () => {
  for (const source of [E.createDemoSession(),E.createMusicalDemoSession(),{schema_version:1,sample_rate:48000,tracks:[{id:'tone',device:{kind:'sine',frequency_hz:440.1234,gain:0.125}}]}]) {
    const before = structuredClone(source), next = E.editMixer(source,0,{pan:-1,gain:2,mute:true});
    assert.equal(next.schema_version,10); assert.equal(E.validate(next),null);
    assert.deepEqual(source,before); assert.deepEqual(next.tracks[0].device,source.tracks[0].device);
    assert.deepEqual(next.tracks[0].mixer,{gain:2,pan:-1,mute:true,solo:false});
    if (source.tracks[0].clips) assert.deepEqual(next.tracks[0].clips,source.tracks[0].clips);
    if (next.tracks[0].clips.length) { next.tracks[0].clips[0].notes[0].velocity = 0; assert.deepEqual(source,before); }
  }
});
test('format10 editing adds instruments and audio without downgrading mixer or touching other saved data', () => {
  const source = E.editMixer(E.createDemoSession(),0,{solo:true});
  for (const next of [E.addNoteTrack(source,'synth','synth'),E.addNoteTrack(source,'drums','drumkit'),E.addAudioTrack(source,'audio')]) {
    assert.equal(next.schema_version,10); assert.deepEqual(next.tracks.slice(0,2),source.tracks); assert.equal(E.validate(next),null);
  }
  assert.throws(()=>E.addCsound(source,'program','cs',48000),/format 1, 4, 6 or 7/);
});
test('invalid mixer patches and unsupported source/plugin sessions preserve original input', () => {
  const source = E.createDemoSession(), before = structuredClone(source);
  for (const patch of [{gain:NaN},{gain:2.1},{pan:Infinity},{pan:-1.1},{mute:1},{solo:'yes'},{routing:1},{}]) assert.throws(()=>E.editMixer(source,0,patch));
  assert.deepEqual(source,before);
  const old = structuredClone(source); old.tracks[0].mixer = {gain:1,pan:0,mute:false,solo:false}; assert.match(E.validate(old),/format 10/);
  const plugin = structuredClone(source); plugin.tracks[0].effects.push({kind:'vst3',id:'v',bundle_path:'p'}); assert.throws(()=>E.editMixer(plugin,0,{solo:true}),/built-in/);
  const foreign = {schema_version:6,sample_rate:48000,tracks:[{id:'sc',mode:'continuous',clips:[],effects:[],device:{kind:'supercollider'}}]}; assert.throws(()=>E.editMixer(foreign,0,{mute:true}),/preserved/);
});
test('actual numeric and toggle handlers apply transactionally with independent undo snapshots', async () => {
  const s = setup(), original = structuredClone(s.current);
  numberInputs(s.container)[0].value = '1.5'; await numberInputs(s.container)[0].fire('keydown',{key:'Enter'});
  assert.equal(s.current.tracks[0].mixer.gain,1.5); assert.equal(s.history.canUndo,true);
  assert.deepEqual(s.history.undoTarget(s.current),original);
  await mixerButtons(s.container)[2].fire('click'); await mixerButtons(s.container)[5].fire('click');
  assert.equal(s.current.tracks[0].mixer.solo,true); assert.equal(s.current.tracks[1].mixer.solo,true);
  await mixerButtons(s.container)[1].fire('click'); assert.equal(s.current.tracks[0].mixer.mute,true);
  const before = structuredClone(s.current); s.fail = true;
  numberInputs(s.container)[0].value = '0.1'; await mixerButtons(s.container)[0].fire('click');
  assert.deepEqual(s.current,before); assert.equal(numberInputs(s.container)[0].value,1.5); assert.match(s.errors.at(-1),/Could not apply/);
  const count = s.edits.length; s.view.setState({locked:true}); await mixerButtons(s.container)[0].fire('click'); assert.equal(s.edits.length,count);
});
test('polling updates stereo/clipping readouts without rebuilding controls, consuming drafts or clearing focus', async () => {
  const s = setup(), input = numberInputs(s.container)[0]; input.value = '1.'; s.container.ownerDocument.activeElement = input;
  s.view.updateMeters({tracks:[{id:'lead',peak:[1.2,0.5],clipped:true}],master:{peak:[1.2,0.5],clipped:true}},'playing');
  assert.equal(numberInputs(s.container)[0],input); assert.equal(input.value,'1.'); assert.equal(s.container.ownerDocument.activeElement,input);
  assert.equal(s.container.all('meter')[0].value,1); assert.equal(s.container.all('meter')[1].value,0.5);
  assert.match(masterReadout(s.container).textContent,/CLIP/);
  s.view.updateMeters(null,'stopped'); assert.equal(s.container.all('meter')[0].value,0);
  assert.doesNotMatch(masterReadout(s.container).textContent,/CLIP/);
  input.value = ''; await input.fire('keydown',{key:'Enter'}); assert.match(s.errors.at(-1),/Mixer requires/); assert.equal(s.edits.length,0);
});
test('Apply and Enter commit numeric drafts reliably; toggles carry pending values across rows in one undo entry', async () => {
  const s=setup(), original=structuredClone(s.current), inputs=numberInputs(s.container);
  inputs[1].value='-1'; inputs[2].value='1.25';
  assert.equal(s.edits.length,0);
  await mixerButtons(s.container)[2].fire('click');
  assert.equal(s.current.tracks[0].mixer.pan,-1); assert.equal(s.current.tracks[0].mixer.solo,true); assert.equal(s.current.tracks[1].mixer.gain,1.25);
  assert.equal(s.history.undo.length,1); assert.deepEqual(s.history.undoTarget(s.current),original);
  numberInputs(s.container)[1].value='0.33'; await mixerButtons(s.container)[0].fire('click'); assert.equal(s.current.tracks[0].mixer.pan,0.33);
  numberInputs(s.container)[1].value='0.44'; await numberInputs(s.container)[1].fire('keydown',{key:'Enter'}); assert.equal(s.current.tracks[0].mixer.pan,0.44);
  const count=s.edits.length; await mixerButtons(s.container)[0].fire('click'); assert.equal(s.edits.length,count);
});
test('invalid pending numeric draft blocks toggle transaction while preserving input for correction and project data', async () => {
  const s=setup(),original=structuredClone(s.current), input=numberInputs(s.container)[1];
  input.value='-2'; await mixerButtons(s.container)[2].fire('click');
  assert.equal(s.edits.length,0); assert.equal(input.value,'-2'); assert.deepEqual(s.current,original); assert.match(s.errors.at(-1),/Mixer requires/);
  input.value='-1'; await mixerButtons(s.container)[2].fire('click'); assert.equal(s.current.tracks[0].mixer.pan,-1); assert.equal(s.current.tracks[0].mixer.solo,true);
});
test('blur change still auto-commits typed mixer numbers with Apply/Enter fallback available', async () => {
  const s=setup(); numberInputs(s.container)[1].value='-1'; await numberInputs(s.container)[1].fire('change');
  assert.equal(s.current.tracks[0].mixer.pan,-1); assert.equal(s.history.undo.length,1);
  await mixerButtons(s.container)[2].fire('click'); assert.equal(s.current.tracks[0].mixer.pan,-1); assert.equal(s.current.tracks[0].mixer.solo,true);
});
test('unrelated note and clip edits, undo/redo and JSON reload preserve saved mixers', () => {
  const source = E.editMixer(E.createMusicalDemoSession(),0,{gain:0.63,pan:-0.27,solo:true});
  const clip = source.tracks[0].clips[0], note = clip.notes[0];
  const changed = E.moveClip(E.editNote(source,0,clip.id,note.id,{velocity:0.37}),0,clip.id,12345);
  assert.deepEqual(changed.tracks.map(t=>t.mixer),source.tracks.map(t=>t.mixer));
  const history = new History(); history.commit(source,changed);
  const undone = history.undoTarget(changed); assert.equal(history.acceptUndo(changed),true);
  const redone = history.redoTarget(undone); assert.equal(history.acceptRedo(undone),true);
  for (const snapshot of [undone,redone,JSON.parse(JSON.stringify(redone))]) {
    assert.equal(snapshot.schema_version,10);
    assert.deepEqual(snapshot.tracks.map(t=>t.mixer),source.tracks.map(t=>t.mixer));
  }
});
test('gain fader previews an exact draft and release commits all rows as one undoable transaction', async () => {
  const s=setup(), original=structuredClone(s.current);
  const fader=s.container.all('input').find(n => n.attrs['aria-label'] === 'lead gain fader');
  assert.equal(fader.attrs['aria-orientation'],'vertical');
  fader.value='0.72'; await fader.fire('input');
  assert.equal(numberInputs(s.container)[0].value,'0.72'); assert.equal(s.edits.length,0);
  numberInputs(s.container)[3].value='-0.25';
  await fader.fire('change');
  assert.equal(s.current.tracks[0].mixer.gain,0.72); assert.equal(s.current.tracks[1].mixer.pan,-0.25);
  assert.equal(s.history.undo.length,1); assert.deepEqual(s.history.undoTarget(s.current),original);
});
test('pan slider supports Enter, locks during playback and preserves invalid drafts on other channels', async () => {
  const s=setup(), original=structuredClone(s.current);
  let pan=s.container.all('input').find(n => n.attrs['aria-label'] === 'lead pan slider');
  numberInputs(s.container)[2].value='bad';
  pan.value='-0.4'; await pan.fire('input'); await pan.fire('keydown',{key:'Enter'});
  assert.equal(s.edits.length,0); assert.deepEqual(s.current,original);
  assert.equal(numberInputs(s.container)[2].value,'bad'); assert.equal(numberInputs(s.container)[1].value,'-0.4');
  numberInputs(s.container)[2].value='1'; await pan.fire('keydown',{key:'Enter'});
  assert.equal(s.current.tracks[0].mixer.pan,-0.4);
  pan=s.container.all('input').find(n => n.attrs['aria-label'] === 'lead pan slider');
  s.view.setState({locked:true}); assert.equal(pan.disabled,true);
  pan.value='1'; await pan.fire('input'); await pan.fire('change');
  assert.equal(numberInputs(s.container)[1].value,-0.4); assert.equal(s.edits.length,1);
});
test('failed fader replacement restores saved values and numeric typing synchronizes sliders without saving', async () => {
  const s=setup(); const original=structuredClone(s.current);
  const number=numberInputs(s.container)[0], fader=s.container.all('input').find(n => n.attrs['aria-label'] === 'lead gain fader');
  number.value='1.234567'; await number.fire('input');
  assert.equal(fader.value,1.234567); assert.equal(s.edits.length,0);
  number.value=''; await number.fire('input'); assert.equal(fader.value,1.234567);
  s.fail=true; fader.value='0.3'; await fader.fire('change');
  assert.deepEqual(s.current,original); assert.equal(numberInputs(s.container)[0].value,1);
  assert.equal(s.container.all('input').find(n => n.attrs['aria-label'] === 'lead gain fader').value,1);
});

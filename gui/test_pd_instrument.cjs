const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
const Pd = require('./pd_instrument.js');
const History = require('./history.js');
function song() {
  let session = E.addNoteTrack(E.createMusicalDemoSession(), 'pd', 'pd_instrument');
  session = E.addNoteClip(session, 3, {id:'pd-notes',start_frame:1234,length_frames:48000,notes:[{id:'pd-note',start_frame:99,duration_frames:7777,frequency_hz:443.12345,velocity:0.618123456}]});
  session = E.editMixer(session,3,{gain:0.723456789,pan:-0.312345678});
  session = E.addEffect(session,3,{kind:'gain',gain:0.83456789,bypass:false},'pd-gain');
  session = E.addGainAutomationPoint(session,3,'pd-gain',{frame:4321,value:0.943212345});
  session = E.addEffect(session,3,E.effectPreset('delay','echo'),'pd-delay');
  session = E.addAudioTrack(session,'pcm');
  return E.addAudioClip(session,4,{id:'take',start_frame:4321,length_frames:8888,source_path:'assets/take.wav',source_offset_frames:101,gain:0.621234567,fade_in_frames:99,fade_out_frames:207});
}
test('portable factory Pd preset embeds the canonical patch and validates exact declared metadata', () => {
  const device = Pd.preset();
  assert.equal(device.program,fs.readFileSync(`${__dirname}/../native/puredata/instrument.pd`,'utf8'));
  assert.equal(Pd.validateDevice(device),null);
  assert.deepEqual(Pd.parsePackage(JSON.stringify(device)),device);
  const crlf=structuredClone(device); crlf.program=crlf.program.replace(/\n/g,'\r\n'); assert.equal(Pd.validateDevice(crlf),null);
  for (const patch of [{program:'arbitrary Pd patch'},{gain:1.1},{abstractions:[{name:'file',program:'external'}]},{controls:[]},{extra:1}]) assert.throws(()=>Pd.parsePackage(JSON.stringify({...device,...patch})));
  for (const patch of [{name:'frequency'},{type:'integer'},{default:4001},{min:0},{max:20000},{value:NaN},{value:99},{value:12001},{points:[]}]) {
    const bad=structuredClone(device); Object.assign(bad.controls[0],patch); assert.ok(Pd.validateDevice(bad));
  }
  assert.throws(()=>Pd.parsePackage('{'),/valid JSON/);
  assert.throws(()=>Pd.parsePackage(' '.repeat(Pd.packageByteLimit+1)),/128 KiB/);
  device.controls[0].value=100; assert.equal(Pd.preset().controls[0].value,4000);
});
test('Pd upgrade and every following instrument, mixer and effect edit preserve format12 and precise mixed project state', () => {
  const source=song(), original=structuredClone(source);
  assert.equal(source.schema_version,12); assert.equal(E.validate(source),null);
  for(const edited of [E.addNoteTrack(source,'more-synth','synth'),E.addNoteTrack(source,'more-drums','drumkit'),E.addAudioTrack(source,'more-pcm'),E.editMixer(source,3,{solo:true}),E.addEffect(source,3,E.effectPreset('lowpass','warm'),'filter')]) {
    assert.equal(edited.schema_version,12); assert.equal(E.validate(edited),null);
    assert.deepEqual(edited.tracks[4],source.tracks[4]);
    assert.deepEqual(edited.tracks[3].device,source.tracks[3].device);
    assert.deepEqual(edited.tracks[3].clips,source.tracks[3].clips);
    assert.deepEqual(edited.tracks[3].automation,source.tracks[3].automation);
  }
  const changed=E.editPdInstrument(source,3,{gain:0.213456789,controls:[{...source.tracks[3].device.controls[0],value:1234.56789}]});
  assert.deepEqual(source,original); assert.deepEqual(changed.tracks.slice(0,3),source.tracks.slice(0,3));
  assert.deepEqual(changed.tracks[3].clips,source.tracks[3].clips); assert.deepEqual(changed.tracks[3].mixer,source.tracks[3].mixer);
  assert.deepEqual(changed.tracks[3].effects,source.tracks[3].effects); assert.deepEqual(changed.tracks[3].automation,source.tracks[3].automation);
  assert.deepEqual(JSON.parse(JSON.stringify(changed)),changed);
  const history=new History(); history.commit(source,changed); assert.deepEqual(history.undoTarget(changed),source);
  history.acceptUndo(changed); assert.deepEqual(history.redoTarget(source),changed);
  const loaded=E.addPdInstrument(source,'loaded',Pd.parsePackage(JSON.stringify(changed.tracks[3].device)));
  assert.equal(E.validate(loaded),null); assert.deepEqual(loaded.tracks.at(-1).device,changed.tracks[3].device);
});
test('monophonic Pd edits reject overlapping notes across clips and short gates transactionally', () => {
  const source=song(), before=structuredClone(source), note={id:'new',start_frame:0,duration_frames:1000,frequency_hz:440,velocity:0.5};
  assert.throws(()=>E.addNote(source,3,'pd-notes',note),/monophonic/);
  assert.throws(()=>E.addNoteClip(source,3,{id:'overlap',start_frame:1333,length_frames:1000,notes:[note]}),/monophonic/);
  assert.throws(()=>E.editNote(source,3,'pd-notes','pd-note',{duration_frames:63}),/64 frames/);
  assert.throws(()=>E.editPdInstrument(source,3,{gain:NaN}),/gain/);
  assert.throws(()=>E.editPdInstrument(source,3,{program:'changed'}),/control edit/);
  const boundary=E.addNote(source,3,'pd-notes',{...note,start_frame:7876}); assert.equal(E.validate(boundary),null);
  // Raw overlapping gates are rejected even if rounding shares a Pd boundary.
  assert.throws(()=>E.addNote(source,3,'pd-notes',{...note,start_frame:7875}),/monophonic/);
  const lowerRate=E.createArrangementSession(44100); assert.throws(()=>E.addNoteTrack(lowerRate,'pd','pd_instrument'),/48 kHz/);
  const older=structuredClone(source); older.schema_version=11; assert.ok(E.validate(older));
  assert.deepEqual(source,before);
});
class Node {
  constructor(tag,className='',text='') { this.tag=tag; this.className=className; this.textContent=text; this.children=[]; this.dataset={}; this.listeners={}; this.attrs={}; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener(k,f) { this.listeners[k]=f; }
  all(tag) { return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.all(tag)]); }
  fire(type) { this.listeners[type]?.(); }
}
test('real Pd card change handlers send checked edits and leave applied state for the controller to accept', () => {
  const text=fs.readFileSync(require.resolve('./app.js'),'utf8');
  const start=text.indexOf('  function makePdInstrumentCard('),end=text.indexOf('  function makeInstrumentCard(',start);
  const actions=[], draft=song(), before=structuredClone(draft);
  const ctx={draft,element:(...args)=>new Node(...args),editArrangement:action=>actions.push(action),makeEffectPanel:()=>new Node('section'),Blob,download(){}};
  vm.createContext(ctx); vm.runInContext(text.slice(start,end),ctx);
  const card=ctx.makePdInstrumentCard(draft.tracks[3],3),inputs=card.all('input');
  inputs[0].value='0.123456789'; inputs[0].fire('change');
  inputs[1].value='1234.56789'; inputs[1].fire('change');
  assert.deepEqual(JSON.parse(JSON.stringify(actions[0])),{type:'editPdInstrument',trackIndex:3,patch:{gain:0.123456789}});
  assert.equal(actions[1].patch.controls[0].value,1234.56789);
  assert.deepEqual(draft,before);
  assert.equal(inputs[0].dataset.structural,'true'); assert.equal(inputs[1].dataset.structural,'true');
  card.all('button').find(n=>n.textContent==='×').fire('click'); assert.equal(actions[2].type,'deleteTrack');
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
const History = require('./history.js');
function song() {
  const s = E.editMixer(E.createMusicalDemoSession(), 0, {gain:0.723456789,pan:-0.23456789,solo:true});
  s.tracks[0].clips[0].notes[0].frequency_hz = 440.123456789;
  return s;
}
test('processing upgrade preserves exact musical and mixer data through unrelated edits and undo', () => {
  const source = song(), original = structuredClone(source), history = new History();
  let next = E.addEffect(source,0,E.effectPreset('lowpass','warm'),'filter');
  next = E.addEffect(next,0,E.effectPreset('delay','echo'),'echo');
  history.commit(source,next);
  assert.equal(next.schema_version,11); assert.equal(E.validate(next),null);
  assert.deepEqual(next.tracks[0].mixer,original.tracks[0].mixer);
  assert.deepEqual(next.tracks.map(t=>t.clips),original.tracks.map(t=>t.clips));
  for (const edited of [E.addNoteTrack(next,'more','synth'),E.addAudioTrack(next,'audio'),E.editMixer(next,1,{pan:0.2})]) {
    assert.equal(edited.schema_version,11); assert.deepEqual(edited.tracks[0],next.tracks[0]);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(next)),next);
  assert.equal(E.exportMaximum(next,{builtin_max_seconds:180}),180);
  assert.deepEqual(source,original);
  assert.deepEqual(history.undoTarget(next),original);
});
test('explicit processing authoring upgrades each legacy built-in family without changing import format', () => {
  for (const version of [1,2,3,4,9,10]) {
    let s = version === 1 ? {schema_version:1,sample_rate:48000,tracks:[{id:'tone',device:{kind:'sine',frequency_hz:440,gain:0.15}}]} : E.createDemoSession();
    s.schema_version = version;
    if (version === 2) s.tracks.forEach(t=>delete t.effects);
    const before = structuredClone(s);
    assert.equal(E.validate(s),null);
    const next = E.addEffect(s,0,E.effectPreset('delay','slap'),'echo');
    assert.equal(next.schema_version,11); assert.equal(E.validate(next),null);
    assert.deepEqual(s,before);
  }
});
test('rejects invalid processing and incompatible upgrades transactionally, including strict Nyquist', () => {
  const s = E.addEffect(song(),0,E.effectPreset('lowpass','warm'),'filter'), before = structuredClone(s);
  for (const value of [19,20001,Infinity,NaN]) assert.throws(()=>E.editEffect(s,0,'filter',{cutoff_hz:value}));
  const lowRate = structuredClone(s); lowRate.sample_rate = 8000;
  assert.throws(()=>E.editEffect(lowRate,0,'filter',{cutoff_hz:4000}),/Nyquist/);
  assert.equal(E.effectPreset('lowpass','open',8000).cutoff_hz,3999);
  for (const patch of [{time_ms:0},{time_ms:2001},{feedback:0.951},{mix:1.01},{mix:NaN},{bypass:1}]) {
    const delayed = E.addEffect(s,0,E.effectPreset('delay','echo'),'echo');
    assert.throws(()=>E.editEffect(delayed,0,'echo',patch));
  }
  const legacy = structuredClone(s); legacy.schema_version = 10; assert.ok(E.validate(legacy));
  const foreign = {schema_version:4,sample_rate:48000,tempo_milli_bpm:120000,tracks:[{id:'tone',device:{kind:'sine',frequency_hz:440,gain:0.1},mode:'continuous',clips:[],effects:[{kind:'vst3',id:'p',bundle_path:'/p',parameters:[]}]}]};
  assert.throws(()=>E.addEffect(foreign,0,E.effectPreset('delay','echo'),'echo'),/compatible/);
  assert.deepEqual(s,before);
});
class Node {
  constructor(tag,className='',text='') { this.tag=tag; this.className=className; this.textContent=text; this.children=[]; this.dataset={}; this.listeners={}; this.attrs={}; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener(k,f) { this.listeners[k]=f; }
  all(tag) { return this.children.flatMap(n=>[...(n.tag===tag?[n]:[]),...n.all(tag)]); }
  fire(type) { this.listeners[type]?.(); }
}
test('actual effect card events add controls, edit precise numbers, preset without resetting bypass and remove safely', () => {
  const text = fs.readFileSync(require.resolve('./app.js'),'utf8');
  const start = text.indexOf('  function makeEffectPanel('), end = text.indexOf('  function formatFrequency(',start);
  const ctx = {SessionEditor:E,draft:song(),element:(...args)=>new Node(...args),player:{},starting:false,makeId:()=> 'echo',configureSessionMode(){},renderTracks(){},markEdited(){},announceError(e){throw new Error(e);},effectCatalog:[],metadataCache:new Map()};
  vm.createContext(ctx); vm.runInContext(text.slice(start,end),ctx);
  let card = ctx.makeEffectPanel(ctx.draft.tracks[0],0);
  card.all('button').find(n=>n.textContent==='Add delay').fire('click');
  assert.equal(ctx.draft.schema_version,11);
  card = ctx.makeEffectPanel(ctx.draft.tracks[0],0);
  const time = card.all('input').find(n=>n.attrs['aria-label']?.includes('Time (ms)'));
  time.value='123.456789'; time.fire('input');
  assert.equal(ctx.draft.tracks[0].effects[0].time_ms,123.456789);
  const bypass = card.all('input').find(n=>n.type==='checkbox'); bypass.checked=true; bypass.fire('change');
  const preset = card.all('select').find(n=>n.attrs['aria-label']?.includes('Delay preset'));
  preset.value='spacious'; preset.fire('change');
  assert.equal(ctx.draft.tracks[0].effects[0].time_ms,650);
  assert.equal(ctx.draft.tracks[0].effects[0].bypass,true);
  assert.deepEqual(ctx.draft.tracks[0].mixer,song().tracks[0].mixer);
  card = ctx.makeEffectPanel(ctx.draft.tracks[0],0);
  card.all('button').find(n=>n.textContent==='Remove').fire('click');
  assert.equal(ctx.draft.schema_version,11); assert.equal(ctx.draft.tracks[0].effects.length,0);
});

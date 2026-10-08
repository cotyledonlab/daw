const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Small DOM surface exercising the real timeline handlers, without a browser dependency.
class Element {
  constructor(tag, document) { this.tag = tag; this.document = document; this.attrs = {}; this.children = []; this.listeners = {}; this.value = ''; this.disabled = false; this.dataset = {}; }
  set value(value) { this._value = String(value); }
  get value() { return this._value; }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key.startsWith('data-')) this.dataset[key.slice(5)] = String(value); }
  getAttribute(key) { return this.attrs[key] ?? null; }
  append(child) { child.parent = this; this.children.push(child); if (this.tag === 'select' && this.children.length === 1) this.value = String(child.value); }
  replaceChildren() { this.children = []; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  setPointerCapture(id) { this.captured = id; }
  releasePointerCapture() { this.captured = null; }
  getScreenCTM() { return {inverse:()=>({scale:this.scale || 1,offsetX:this.offsetX || 0,offsetY:this.offsetY || 0})}; }
  createSVGPoint() { return {x:0,y:0,matrixTransform(m) { return {x:(this.x-m.offsetX)/m.scale,y:(this.y-m.offsetY)/m.scale}; }}; }
  get options() { return this.children; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  dispatch(type, extra = {}) { const event = { target: this, preventDefault() {}, stopPropagation() { this.stopped = true; }, ...extra }; for (let el = this; el; el = el.parent) { for (const handler of el.listeners[type] || []) handler(event); if (event.stopped) break; } }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      const tag = part.match(/^[a-z]+/)?.[0];
      if (tag && this.tag !== tag) return false;
      const cls = part.match(/\.([\w-]+)/)?.[1];
      if (cls && !(this.attrs.class || '').split(' ').includes(cls)) return false;
      const attribute = part.match(/\[([^=\]]+)(?:="([^"]*)")?\]/);
      return !attribute || (this.attrs[attribute[1]] !== undefined && (attribute[2] === undefined || this.attrs[attribute[1]] === attribute[2]));
    });
  }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  contains(el) { return el === this || this.children.some(child => child.contains(el)); }
  focus() { this.document.activeElement = this; }
  set innerHTML(html) {
    this.children = [];
    for (const match of html.matchAll(/<(input|select|button|p|svg|section|h2|h3|output|div|span)\b([^>]*)>/g)) {
      const el = new Element(match[1], this.document);
      for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) { el.setAttribute(attr[1], attr[2]); if (attr[1] === 'value') el.value = attr[2]; }
      if (el.dataset.field === 'grid') el.value = '240';
      this.append(el);
    }
  }
}
const E = require('./editor.js');
const History = require('./history.js');
function setup(session) {
  const document = {activeElement:null,createElementNS:(_,tag)=>new Element(tag,document),createElement:tag=>new Element(tag,document)};
  const container = new Element('div',document), window = {SessionEditor:E}, selections = [];
  vm.runInNewContext(fs.readFileSync(`${__dirname}/timeline.js`,'utf8'),{window,document});
  const view = window.ArrangementView.create(container,{onSelectionChange:target=>selections.push(target)});
  view.render(session);
  const field = name => container.querySelector(`[data-field="${name}"]`);
  const select = (trackIndex=0,clipId='phrase') => container.querySelector(`[data-focus-key="clip:${trackIndex}:${clipId}"]`).dispatch('click');
  select();
  return {container,view,field,select,selections};
}
function phrase(tempo=137000,kind='synth') {
  let session = E.addNoteTrack(E.createArrangementSession(48000,tempo),'lead',kind);
  return E.addNoteClip(session,0,{id:'phrase',start_frame:1234,length_frames:100000,notes:[]});
}
test('selected empty clip exposes audition/step context; selection changes notify and audio has no target',()=>{
  let session=E.addAudioTrack(phrase(),'recording');
  session=E.addAudioClip(session,1,{id:'audio',start_frame:0,length_frames:24000,source_path:'take.wav',source_offset_frames:0,gain:0.5});
  const env=setup(session);
  assert.equal(env.view.getNoteTarget().trackId,'lead');
  assert.equal(env.view.getStepTarget().start_frame,0);
  assert.equal(env.selections.at(-1).clipId,'phrase');
  env.select(1,'audio'); assert.equal(env.view.getNoteTarget(),null); assert.equal(env.view.getStepTarget(),null);
  assert.equal(env.container.querySelector('.step-controls').hidden,true);
  env.view.clearSelection(); assert.equal(env.selections.at(-1),null);
});
test('step timing rounds tick endpoints from clip-relative origin and advances without touching note drafts',()=>{
  let session=E.insertStepNote(phrase(),0,'phrase',{id:'saved',start_frame:1234,duration_frames:1777,frequency_hz:443.12345,velocity:0.6});
  const env=setup(session);
  env.container.querySelector('[data-focus-key="note:saved"]').dispatch('click');
  env.field('noteStart').value='1.234567'; env.field('velocity').value='0.1234567';
  env.field('stepBeat').value='0.25'; env.field('stepGate').value='1';
  const target=env.view.getStepTarget();
  assert.equal(target.start_frame,E.ticksToFrames(session,240));
  assert.equal(target.duration_frames,E.ticksToFrames(session,480)-E.ticksToFrames(session,240));
  assert.notEqual(target.duration_frames,E.ticksToFrames(session,240));
  session=E.insertStepNote(session,0,'phrase',{id:'entered',frequency_hz:440,velocity:0.8,start_frame:target.start_frame,duration_frames:target.duration_frames});
  env.view.render(session);
  assert.equal(env.field('stepBeat').value,'0.25');
  assert.equal(env.view.acceptStepAdvance(target),true);
  assert.equal(env.field('stepBeat').value,'0.5');
  assert.equal(env.field('noteStart').value,'1.234567'); assert.equal(env.field('velocity').value,'0.1234567');
  assert.equal(env.view.acceptStepAdvance(target),false);
  assert.equal(env.field('stepBeat').value,'0.5');
});
test('step entries have independent applied history and preserve unrelated exact state; checked rejection does not advance',()=>{
  let session=E.insertStepNote(phrase(),0,'phrase',{id:'saved',start_frame:13,duration_frames:77,frequency_hz:443.12345,velocity:0.6});
  session=E.addEffect(session,0,{kind:'gain',gain:0.7,bypass:false},'level');
  session=E.addGainAutomationPoint(session,0,'level',{frame:111,value:0.3});
  session=E.editMixer(session,0,{gain:0.6,pan:0.2,mute:false,solo:false});
  session=E.addAudioTrack(session,'audio');
  session=E.addAudioClip(session,1,{id:'take',start_frame:7,length_frames:333,source_path:'take.wav',source_offset_frames:11,gain:0.6});
  const before=structuredClone(session),env=setup(session),history=new History();
  for (let i=0;i<2;i++) {
    const target=env.view.getStepTarget();
    const next=E.insertStepNote(session,0,'phrase',{id:`entered-${i}`,...{start_frame:target.start_frame,duration_frames:target.duration_frames},frequency_hz:440,velocity:0.8});
    history.commit(session,next); session=next; env.view.render(session); env.view.acceptStepAdvance(target);
  }
  assert.equal(history.undo.length,2);
  assert.deepEqual(session.tracks[0].clips[0].notes[0],before.tracks[0].clips[0].notes[0]);
  assert.deepEqual(session.tracks[0].device,before.tracks[0].device);
  assert.deepEqual(session.tracks[0].effects,before.tracks[0].effects);
  assert.deepEqual(session.tracks[0].automation,before.tracks[0].automation);
  assert.deepEqual(session.tracks[0].mixer,before.tracks[0].mixer); assert.deepEqual(session.tracks[1],before.tracks[1]);
  const accepted=structuredClone(session),target=env.view.getStepTarget(),position=env.field('stepBeat').value;
  assert.throws(()=>E.insertStepNote(session,0,'phrase',{id:'bad',start_frame:target.start_frame,duration_frames:100000,frequency_hz:440,velocity:0.8}),/fit|within/);
  assert.deepEqual(session,accepted); assert.equal(env.field('stepBeat').value,position); assert.equal(history.undo.length,2);
  const previous=history.undoTarget(session); history.acceptUndo(session); assert.equal(previous.tracks[0].clips[0].notes.length,2);
  const original=history.undoTarget(previous); assert.deepEqual(original,before);
});
test('Pd overlapping gate rejects transactionally, stopped lock and end-of-clip never wrap',()=>{
  let session=phrase(120000,'pd_instrument');
  session=E.insertStepNote(session,0,'phrase',{id:'held',start_frame:0,duration_frames:6000,frequency_hz:440,velocity:0.6});
  const env=setup(session),before=structuredClone(session),target=env.view.getStepTarget();
  assert.throws(()=>E.insertStepNote(session,0,'phrase',{id:'overlap',start_frame:target.start_frame,duration_frames:target.duration_frames,frequency_hz:880,velocity:0.8}),/overlap|monophonic/);
  assert.deepEqual(session,before); assert.equal(env.field('stepBeat').value,'0');
  env.view.setState({locked:true}); assert.throws(()=>env.view.getStepTarget(),/stopped/); assert.equal(env.field('stepBeat').disabled,true);
  env.view.setState({locked:false}); env.field('stepBeat').value='4'; env.field('stepGate').value='1';
  assert.throws(()=>env.view.getStepTarget(),/fit inside/); assert.equal(env.field('stepBeat').value,'4');
  env.field('stepBeat').value='0.5'; const stale=env.view.getStepTarget(); env.field('stepGate').value='2';
  assert.equal(env.view.acceptStepAdvance(stale),false); assert.equal(env.field('stepBeat').value,'0.5');
});

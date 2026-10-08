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
    for (const match of html.matchAll(/<(input|select|button|p|svg|section|h2|h3|output|div)\b([^>]*)>/g)) {
      const el = new Element(match[1], this.document);
      for (const attr of match[2].matchAll(/([\w-]+)="([^"]*)"/g)) { el.setAttribute(attr[1], attr[2]); if (attr[1] === 'value') el.value = attr[2]; }
      if (el.dataset.field === 'grid') el.value = '240';
      this.append(el);
    }
  }
}
function setup(kind = 'sine', onEdit = null, onExportMidi = null) {
  const document = { activeElement: null, createElementNS: (_, tag) => new Element(tag, document), createElement: tag => new Element(tag, document) };
  const container = new Element('div', document), window = {};
  vm.runInNewContext(fs.readFileSync(`${__dirname}/timeline.js`, 'utf8'), { window, document });
  const edits = [];
  const view = window.ArrangementView.create(container, { onEdit: edit => { edits.push(edit); return onEdit?.(edit); }, onExportMidi });
  const session = { sample_rate: 48000, tempo_milli_bpm: 120000, tracks: [{ id: 'track', mode: 'sequenced', device: { kind }, clips: [{ id: 'clip', kind: 'notes', start_frame: 0, length_frames: 96000, notes: [{ id: 'note', start_frame: 1234, duration_frames: 7777, frequency_hz: kind === 'drumkit' ? 440 * Math.pow(2, (36 - 69) / 12) : 443.12345, velocity: 0.6 }] }] }] };
  if (kind === 'audio') { session.tracks[0].device = {kind:'audio',gain:0.8}; session.tracks[0].clips = [{id:'clip',kind:'audio',start_frame:1234,length_frames:7777,source_path:'assets/recording.wav',source_offset_frames:99,gain:0.67}]; }
  view.render(session, { transportAvailable: true });
  const field = name => container.querySelector(`[data-field="${name}"]`);
  const button = name => container.querySelector(`button[data-action="${name}"]`);
  const action = name => button(name).dispatch('click');
  const clip = () => container.querySelector('[data-focus-key="clip:0:clip"]');
  const note = () => container.querySelector('[data-focus-key="note:note"]');
  clip().dispatch('click'); if (kind !== 'audio') note().dispatch('click');
  return { document, container, edits, view, session, field, action, clip, note, button };
}
test('actual Update preserves off-grid frames and microtonal Hz for velocity-only and no-op updates', () => {
  const env = setup();
  env.action('editNote'); assert.equal(env.edits.length, 0);
  env.field('velocity').value = '0.75'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)), { velocity: 0.75 });
  env.session.tempo_milli_bpm = 137000; env.view.render(env.session);
  env.field('velocity').value = '0.9'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)), { velocity: 0.9 });
});
test('changed start keeps exact duration; changed duration snaps the absolute end from exact start', () => {
  const env = setup();
  env.field('noteStart').value = '1'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)), { start_frame: 24000 });
  env.field('noteStart').value = String(Number((1234 / 24000).toFixed(5)));
  env.field('noteLength').value = '0.5'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)), { duration_frames: 12000 - 1234 });
});
test('clip insertion has independent beat field and new built-in devices are selectable', () => {
  for (const kind of ['sine','drumkit','synth','pd_instrument']) {
    const env = setup(kind);
    assert.equal(env.button('addClip').disabled, false);
    env.field('seek').value = '9'; env.field('insertBeat').value = '2'; env.action('addClip');
    assert.equal(env.edits[0].start_frame, 48000);
    assert.equal(env.field('track').options.length, 1);
    if (kind === 'drumkit') {
      env.clip().dispatch('click');
      assert.equal(Number(env.field('pitch').value), 36);
      assert.match(env.container.querySelector('.note-device-help').textContent, /38 = snare/);
      env.action('addNote');
      assert.ok(Math.abs(env.edits[1].note.frequency_hz - 65.40639132514966) < 1e-9);
    }
  }
});
test('errors survive polling and redraw, keyboard selection restores focus and exposes selection', () => {
  const env = setup();
  env.note().focus(); env.view.render(env.session); assert.equal(env.document.activeElement, env.note());
  assert.equal(env.note().getAttribute('aria-pressed'), 'true');
  env.clip().focus(); env.clip().dispatch('keydown', { key: 'Enter' }); assert.equal(env.document.activeElement, env.clip());
  env.note().dispatch('keydown', { key: 'Enter' }); assert.equal(env.document.activeElement, env.note());
  env.field('velocity').value = ''; env.action('editNote');
  const message = env.container.querySelector('.arrangement-status').textContent;
  assert.match(message, /Enter a number/);
  env.view.setState({ locked: true }); env.view.updateTransport({ locked: false }); env.view.render(env.session);
  assert.equal(env.container.querySelector('.arrangement-status').textContent, message);
  env.view.reportError(new Error('Bridge rejected edit')); env.view.render(env.session);
  assert.equal(env.container.querySelector('.arrangement-status').textContent, 'Bridge rejected edit');
});

test('pitch-only patches preserve exact timing; timing-only patches preserve exact saved Hz', () => {
  const env = setup();
  assert.match(env.container.querySelector('.note-pitch-detail').textContent, /443\.12345 Hz/);
  assert.match(env.container.querySelector('.note-pitch-detail').textContent, /\+12\.25 cents from MIDI 69/);
  env.field('pitch').value = '72'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)), { frequency_hz: 440 * Math.pow(2, (72 - 69) / 12) });
  env.field('pitch').value = '69'; env.field('noteStart').value = '1.25'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)), { start_frame: 30000 });
});
test('drum roll has three named rows and 22px note targets with a valid snapped default gate', () => {
  const env = setup('drumkit');
  const roll = env.container.querySelector('.piano-roll-svg');
  assert.equal(roll.getAttribute('viewBox'), '0 0 1000 96');
  const names = roll.querySelectorAll('text').map(el => el.textContent);
  for (const name of ['Kick 36', 'Snare 38', 'Hat 42']) assert.ok(names.includes(name));
  assert.equal(env.note().querySelector('rect').getAttribute('height'), '22');
  env.clip().dispatch('click');
  assert.equal(env.field('noteLength').value, '0.25');
  env.action('addNote');
  assert.equal(env.edits[0].note.duration_frames, 6000);
});

test('audio selection hides note tools, labels source, and sends only changed fields without quantizing saved frames', () => {
  const env = setup('audio');
  assert.equal(env.container.querySelector('.audio-controls').hidden, false);
  assert.equal(env.container.querySelector('.note-roll').hidden, true);
  assert.equal(env.container.querySelector('.note-controls').hidden, true);
  assert.equal(env.container.querySelector('.clip-creator').hidden, true);
  assert.equal(env.container.querySelector('.audio-source-path').textContent, 'assets/recording.wav');
  assert.ok(env.container.querySelector('.arrangement-svg').querySelectorAll('text').some(el => el.textContent === 'AUDIO'));
  env.action('editAudioClip'); env.action('moveClip'); env.action('resizeClip');
  assert.equal(env.edits.length, 0);
  env.field('audioGain').value = '0.25'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)), {gain:0.25});
  env.field('audioGain').value = '0.67'; env.field('audioOffset').value = '123'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)), {source_offset_frames:123});
  env.view.render(env.session); assert.equal(env.field('audioOffset').value,'123');
  env.field('audioOffset').value = '99'; env.field('clipLength').value = '1'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[2].patch)), {length_frames:24000});
});
test('audio UI rejects fractional/unsafe offsets, invalid gain and empty length locally', () => {
  for (const [name,value] of [['audioOffset','0.5'],['audioOffset','-1'],['audioOffset',String(Number.MAX_SAFE_INTEGER)],['audioGain','1.1'],['clipLength','0']]) {
    const env = setup('audio'); env.field(name).value = value; env.action('editAudioClip');
    assert.equal(env.edits.length,0);
    assert.ok(env.container.querySelector('.arrangement-status').textContent);
  }
});

test('audio no-op fields remain exact across tempo redraw, focus persists and rejected edit errors remain visible', () => {
  const env = setup('audio');
  assert.equal(env.container.querySelector('.arrangement-title').textContent, 'Arrangement');
  assert.equal(setup().container.querySelector('.arrangement-title').textContent, 'Note arrangement');
  env.clip().focus(); env.session.tempo_milli_bpm = 137000; env.view.render(env.session);
  assert.equal(env.document.activeElement, env.clip());
  for (const action of ['editAudioClip','moveClip','resizeClip']) env.action(action);
  assert.equal(env.edits.length, 0);
  assert.equal(env.session.tracks[0].clips[0].start_frame,1234);
  assert.equal(env.session.tracks[0].clips[0].length_frames,7777);
  env.field('audioOffset').value = '-1'; env.action('editAudioClip');
  const error = env.container.querySelector('.arrangement-status').textContent;
  assert.match(error,/Source offset/);
  env.view.updateTransport({locked:true}); env.view.updateTransport({locked:false}); env.view.render(env.session);
  assert.equal(env.container.querySelector('.arrangement-status').textContent,error);
  env.field('audioOffset').value = '100'; env.action('editAudioClip');
  assert.equal(env.edits.length,1);
  assert.doesNotMatch(env.container.querySelector('.arrangement-status').textContent,/Source offset/);
});
function pointer(target,type,x,y,extra={}) { target.dispatch(type,{pointerId:1,button:0,clientX:x,clientY:y,...extra}); }
function surfaces(env) { return {svg:env.container.querySelector('.arrangement-svg'),roll:env.container.querySelector('.piano-roll-svg')}; }
test('clip gestures preview locally and emit one snapped move/resize on release at scrolled scaled coordinates', () => {
  const e=setup(), {svg}=surfaces(e), rect=e.clip().children[0];
  svg.scale=0.5; svg.offsetX=-100; svg.offsetY=25;
  pointer(rect,'pointerdown',-25,50); pointer(svg,'pointermove',80,50);
  assert.equal(e.edits.length,0); assert.match(e.clip().getAttribute('transform'),/translate/);
  pointer(svg,'pointerup',80,50);
  assert.equal(e.edits.length,1); assert.equal(e.edits[0].type,'moveClip'); assert.equal(e.edits[0].start_frame,96000);
  const handle=e.clip().children.at(-1);
  pointer(handle,'pointerdown',80,50); pointer(svg,'pointermove',106.25,50); pointer(svg,'pointerup',106.25,50);
  assert.equal(e.edits.length,2); assert.equal(e.edits[1].type,'resizeClip'); assert.equal(e.edits[1].length_frames,120000);
  assert.equal(e.session.tracks[0].clips[0].start_frame,0);
});
test('timing-only note gestures preserve microtonal Hz/duration and pitch-only gestures preserve exact frame data', () => {
  const e=setup(),{roll}=surfaces(e),rect=e.note().children[0];
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointermove',138.125,100); pointer(roll,'pointerup',138.125,100);
  assert.deepEqual(JSON.parse(JSON.stringify(e.edits[0].patch)),{start_frame:6000});
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointermove',80,87); pointer(roll,'pointerup',80,87);
  assert.deepEqual(Object.keys(e.edits[1].patch),['frequency_hz']);
  assert.equal(e.session.tracks[0].clips[0].notes[0].frequency_hz,443.12345);
  const handle=e.note().children.at(-1);
  pointer(handle,'pointerdown',100,100); pointer(roll,'pointermove',158.125,100); pointer(roll,'pointerup',158.125,100);
  assert.deepEqual(JSON.parse(JSON.stringify(e.edits[2].patch)),{duration_frames:18000-1234});
});
test('click/draw adds snapped notes, drum rows map named pads and movement sends no intermediate replacements', () => {
  for (const kind of ['sine','drumkit']) {
    const e=setup(kind),{roll}=surfaces(e),row=roll.querySelectorAll('.roll-row')[0];
    pointer(row,'pointerdown',60,25); assert.equal(e.edits.length,0);
    pointer(roll,'pointermove',176.25,25); assert.equal(e.edits.length,0);
    pointer(roll,'pointerup',176.25,25); assert.equal(e.edits.length,1);
    assert.equal(e.edits[0].type,'addNote'); assert.equal(e.edits[0].note.start_frame,0); assert.equal(e.edits[0].note.duration_frames,12000);
    if (kind==='drumkit') assert.ok(Math.abs(e.edits[0].note.frequency_hz - 440*2**((42-69)/12))<1e-9);
    pointer(row,'pointerdown',292.5,25); pointer(roll,'pointerup',292.5,25); assert.equal(e.edits.length,2); assert.equal(e.edits[1].note.start_frame,24000); assert.ok(e.edits[1].note.duration_frames>0);
  }
});
test('Escape, pointercancel, playback locking and no-op jitter preserve the session and existing feedback', () => {
  const e=setup(), before=structuredClone(e.session),{roll}=surfaces(e),rect=e.note().children[0];
  const x=rect.getAttribute('x');
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointermove',200,100); e.note().dispatch('keydown',{key:'Escape'}); pointer(roll,'pointerup',200,100);
  assert.equal(e.edits.length,0); assert.equal(rect.getAttribute('x'),x);
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointermove',200,100); pointer(roll,'pointercancel',200,100); pointer(roll,'pointerup',200,100);
  assert.equal(e.edits.length,0);
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointerup',81,101); assert.equal(e.edits.length,0);
  e.view.setState({locked:true}); pointer(rect,'pointerdown',80,100); pointer(roll,'pointerup',200,100); assert.equal(e.edits.length,0);
  e.view.setState({locked:false}); e.view.reportError('Rejected project remains unchanged.');
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointerup',80,100); e.view.updateTransport({frame:10});
  assert.match(e.container.querySelector('.arrangement-status').textContent,/Rejected/); assert.deepEqual(e.session,before);
});
test('invalid resize reports persistent feedback and keyboard edits guard numeric drafts', () => {
  const e=setup(),{roll}=surfaces(e),handle=e.note().children.at(-1);
  pointer(handle,'pointerdown',100,100); pointer(roll,'pointerup',0,100); assert.equal(e.edits.length,0);
  assert.match(e.container.querySelector('.arrangement-status').textContent,/positive/);
  e.field('noteStart').dispatch('keydown',{key:'Delete'}); e.field('noteStart').dispatch('keydown',{key:'d',ctrlKey:true}); assert.equal(e.edits.length,0);
  e.note().dispatch('keydown',{key:'Delete'}); assert.equal(e.edits[0].type,'deleteNote');
  e.clip().dispatch('click'); e.clip().dispatch('keydown',{key:'d',metaKey:true}); assert.equal(e.edits[1].type,'duplicateClip');
});
test('completed gesture commits once through checked editing/history while rejected gate resize preserves project and undo', () => {
  const E=require('./editor.js'), History=require('./history.js'), history=new History();
  let env, applied;
  env=setup('sine',action=>{
    try {
      const next=action.type==='resizeClip' ? E.resizeClip(applied,action.trackIndex,action.clipId,action.length_frames) : E.moveClip(applied,action.trackIndex,action.clipId,action.start_frame);
      history.commit(applied,next); applied=next; env.view.render(applied);
    } catch(error) { env.view.reportError(error); }
  });
  applied=structuredClone(env.session); applied.schema_version=3; applied.tracks[0].effects=[]; applied.tracks[0].device={kind:'sine',frequency_hz:440,gain:0.1}; env.view.render(applied);
  const before=structuredClone(applied), {svg}=surfaces(env);
  pointer(env.clip().children[0],'pointerdown',150,60); pointer(svg,'pointermove',202.5,60); assert.equal(history.canUndo,false); pointer(svg,'pointerup',202.5,60);
  assert.equal(history.undo.length,1); assert.deepEqual(history.undoTarget(applied),before); assert.equal(applied.tracks[0].clips[0].start_frame,24000);
  const accepted=structuredClone(applied), handle=env.clip().children.at(-1);
  pointer(handle,'pointerdown',250,60); pointer(svg,'pointermove',52,60); pointer(svg,'pointerup',52,60);
  assert.deepEqual(applied,accepted); assert.equal(history.undo.length,1); assert.match(env.container.querySelector('.arrangement-status').textContent,/positive|within/);
  env.view.updateTransport({frame:5}); assert.match(env.container.querySelector('.arrangement-status').textContent,/positive|within/);
});
test('returning a resize to its exact off-grid start and pitch-only drum movement never quantize timing', () => {
  const e=setup('audio'),{svg}=surfaces(e),handle=e.clip().children.at(-1);
  pointer(handle,'pointerdown',200,60); pointer(svg,'pointermove',250,60); pointer(svg,'pointerup',200,60); assert.equal(e.edits.length,0);
  const d=setup('drumkit'),{roll}=surfaces(d),rect=d.note().children[0];
  pointer(rect,'pointerdown',80,85); pointer(roll,'pointerup',80,61);
  assert.deepEqual(Object.keys(d.edits[0].patch),['frequency_hz']); assert.ok(Math.abs(d.edits[0].patch.frequency_hz-440*2**((38-69)/12))<1e-9);
});
test('surface-targeted captured release click clears drag suppression before the next group selection', () => {
  const e=setup(),{svg,roll}=surfaces(e);
  pointer(e.clip().children[0],'pointerdown',150,60); pointer(svg,'pointermove',202.5,60); pointer(svg,'pointerup',202.5,60);
  svg.dispatch('click'); e.clip().dispatch('click');
  assert.equal(e.view.getSelection().noteId,null);
  e.note().dispatch('click'); assert.equal(e.view.getSelection().noteId,'note');
  pointer(e.note().children[0],'pointerdown',80,100); pointer(roll,'pointermove',138.125,100); pointer(roll,'pointerup',138.125,100);
  roll.dispatch('click'); e.clip().dispatch('click'); assert.equal(e.view.getSelection().noteId,null);
});
test('tiny note pointer gestures retain note focus and keyboard Delete works when capture sends click to the roll', () => {
  const e=setup(),{roll}=surfaces(e),rect=e.note().children[0];
  pointer(rect,'pointerdown',80,100); pointer(roll,'pointerup',80,100); roll.dispatch('click');
  assert.equal(e.document.activeElement,e.note()); assert.equal(e.view.getSelection().noteId,'note');
  e.document.activeElement.dispatch('keydown',{key:'Delete'}); assert.equal(e.edits.length,1); assert.equal(e.edits[0].type,'deleteNote');
});

test('audio fade updates preserve exact untouched frames and gain, defaults remain absent on no-op', () => {
  const env = setup('audio');
  assert.equal(env.field('audioFadeIn').value,'0'); assert.equal(env.field('audioFadeOut').value,'0');
  env.action('editAudioClip'); assert.equal(env.edits.length,0);
  env.field('audioFadeIn').value='17'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)),{fade_in_frames:17});
  env.view.render(env.session); assert.equal(env.field('audioFadeIn').value,'17');
  env.field('audioFadeIn').value='0'; env.field('audioFadeOut').value='7777'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)),{fade_out_frames:7777});
  Object.assign(env.session.tracks[0].clips[0],{fade_in_frames:31,fade_out_frames:59}); env.view.render(env.session);
  env.field('audioGain').value='0.25'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[2].patch)),{gain:0.25});
  const lines=env.clip().querySelectorAll('line'); assert.equal(lines.length,2);
  assert.equal(env.session.tracks[0].clips[0].start_frame,1234); assert.equal(env.session.tracks[0].clips[0].length_frames,7777);
  assert.equal(env.session.tracks[0].clips[0].source_offset_frames,99);
});
test('audio fades reject empty, negative, fractional, unsafe and overlapping values with persistent local feedback', () => {
  for (const value of ['', '-1','0.5',String(Number.MAX_SAFE_INTEGER),'7778']) {
    const env=setup('audio'); env.field('audioFadeIn').value=value; env.action('editAudioClip');
    assert.equal(env.edits.length,0); const error=env.container.querySelector('.arrangement-status').textContent;
    assert.match(error,/number|frame counts|fit/); env.view.updateTransport({frame:42}); env.view.render(env.session);
    assert.equal(env.container.querySelector('.arrangement-status').textContent,error);
  }
  const env=setup('audio'); env.field('audioFadeIn').value='4000'; env.field('audioFadeOut').value='4000'; env.action('editAudioClip');
  assert.equal(env.edits.length,0); assert.match(env.container.querySelector('.arrangement-status').textContent,/fit/);
  env.view.setState({locked:true}); assert.equal(env.field('audioFadeIn').disabled,true); assert.equal(env.field('audioFadeOut').disabled,true);
  env.field('audioFadeIn').value='1'; env.action('editAudioClip'); assert.equal(env.edits.length,0);
});
test('audio resize preserves fades and rejects shorter length, while combined length/fade edit can shorten safely', () => {
  const env=setup('audio'); Object.assign(env.session.tracks[0].clips[0],{fade_in_frames:4000,fade_out_frames:2000}); env.view.render(env.session);
  env.field('clipLength').value='0.25'; env.action('resizeClip'); assert.equal(env.edits.length,1); assert.equal(env.edits[0].length_frames,6000);
  env.field('grid').value='0'; env.field('clipLength').value='0.1'; env.action('resizeClip'); env.action('editAudioClip');
  assert.equal(env.edits.length,1); assert.match(env.container.querySelector('.arrangement-status').textContent,/fit/);
  env.field('audioFadeIn').value='0'; env.field('audioFadeOut').value='0'; env.action('editAudioClip');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[1].patch)),{length_frames:2400,fade_in_frames:0,fade_out_frames:0});
});
test('audio drag resize rejects a fade overlap before a checked edit is dispatched', () => {
  const env=setup('audio'); Object.assign(env.session.tracks[0].clips[0],{fade_in_frames:4000,fade_out_frames:2000}); env.view.render(env.session);
  const {svg}=surfaces(env), handle=env.clip().children.at(-1);
  pointer(handle,'pointerdown',200,60); pointer(svg,'pointerup',192,60);
  assert.equal(env.edits.length,0); assert.match(env.container.querySelector('.arrangement-status').textContent,/fit/);
});
test('actual audio fade Update commits checked editor/history and undo retains exact source and off-grid frames', () => {
  const E=require('./editor.js'), History=require('./history.js'), history=new History();
  let env, applied;
  env=setup('audio', action=>{
    try {
      const next=E.editAudioClip(applied,action.trackIndex,action.clipId,action.patch);
      history.commit(applied,next); applied=next; env.view.render(applied);
    } catch(error) { env.view.reportError(error); }
  });
  applied=structuredClone(env.session); applied.schema_version=3; applied.tracks[0].effects=[]; env.view.render(applied);
  const before=structuredClone(applied);
  env.field('audioFadeIn').value='100'; env.field('audioFadeOut').value='200'; env.action('editAudioClip');
  assert.equal(history.undo.length,1); assert.deepEqual(history.undoTarget(applied),before);
  assert.deepEqual(applied.tracks[0].clips[0],{...before.tracks[0].clips[0],fade_in_frames:100,fade_out_frames:200});
  assert.deepEqual(JSON.parse(JSON.stringify(applied)),applied);
  env.action('editAudioClip'); assert.equal(history.undo.length,1);
  env.field('audioFadeOut').value='7777'; env.action('editAudioClip');
  assert.equal(history.undo.length,1); assert.equal(applied.tracks[0].clips[0].fade_out_frames,200);
  const message=env.container.querySelector('.arrangement-status').textContent; assert.match(message,/fit/);
  env.view.updateTransport({frame:1000}); assert.equal(env.container.querySelector('.arrangement-status').textContent,message);
  const undo=history.undoTarget(applied); assert.equal(history.acceptUndo(applied),true); applied=undo; env.view.render(applied);
  assert.equal(env.field('audioFadeIn').value,'0'); assert.equal(env.field('audioFadeOut').value,'0');
  assert.deepEqual(applied,before); assert.equal(E.validate(applied),null);
});

test('Pd piano roll uses normal precise note editing and displays monophonic constraints', () => {
  const env=setup('pd_instrument');
  assert.match(env.container.querySelector('.note-device-help').textContent,/monophonic/);
  env.field('velocity').value='0.723456789'; env.action('editNote');
  assert.deepEqual(JSON.parse(JSON.stringify(env.edits[0].patch)),{velocity:0.723456789});
  env.view.setState({locked:true}); assert.equal(env.button('addNote').disabled,true);
});

test('studio draft guard detects exact tempo, clip, note and audio typing without consuming it',()=>{
  for(const kind of ['sine','audio']){
    const s=setup(kind);assert.equal(s.view.hasDrafts(),false);
    for(const key of ['tempo','clipStart','clipLength',kind==='audio'?'audioGain':'velocity']){
      const value=s.field(key).value;s.field(key).value='';assert.equal(s.view.hasDrafts(),true);assert.equal(s.field(key).value,'');s.field(key).value=value;assert.equal(s.view.hasDrafts(),false);
    }
  }
});

test('rejected note feedback stays beside note fields, survives redraw and preserves typed input',()=>{
  const s=setup();s.field('noteStart').value='3';s.field('noteLength').value='2';s.action('addNote');
  const feedback=s.container.querySelector('.note-edit-status');assert.equal(feedback.hidden,false);assert.match(feedback.textContent,/fit|inside/i);assert.equal(s.field('noteLength').value,'2');assert.equal(s.edits.length,0);
  s.view.updateTransport({frame:2000});assert.equal(feedback.hidden,false);assert.match(feedback.textContent,/fit|inside/i);
});
test('explicit clip picker selects overlapping copies without changing saved timing or dispatching edits',()=>{
  const s=setup();const duplicate=structuredClone(s.session.tracks[0].clips[0]);duplicate.id='copy:overlap';s.session.tracks[0].clips.push(duplicate);const before=JSON.stringify(s.session);s.view.render(s.session);
  assert.equal(s.field('selectedClip').options.length,3);s.field('selectedClip').value='0:copy:overlap';s.field('selectedClip').dispatch('change');
  assert.equal(s.view.getSelection().clipId,'copy:overlap');assert.equal(JSON.stringify(s.session),before);assert.equal(s.edits.length,0);
});

test('overlapping clips are visibly marked and described; touching clips are not overlaps',()=>{
  const s=setup(), clips=s.session.tracks[0].clips;
  clips.push({...structuredClone(clips[0]),id:'copy'}, {...structuredClone(clips[0]),id:'next',start_frame:96000});
  const before=JSON.stringify(s.session);s.view.render(s.session);
  assert.match(s.clip().getAttribute('class'),/overlapping/);
  assert.match(s.clip().getAttribute('aria-description'),/Overlaps 1 other clip/);
  assert.equal(s.container.querySelector('.overlap-label').textContent,'2 overlapping clips');
  assert.doesNotMatch(s.container.querySelector('[data-focus-key="clip:0:next"]').getAttribute('class'),/overlapping/);
  assert.equal(JSON.stringify(s.session),before);assert.equal(s.edits.length,0);
});

test('quantize sends selected clip/grid only, rejects typed drafts and disables for No snap/audio/empty clips',()=>{
  const s=setup();s.action('quantizeClip');assert.deepEqual(JSON.parse(JSON.stringify(s.edits[0])),{type:'quantizeClip',trackIndex:0,clipId:'clip',gridTicks:240});
  s.field('grid').value='0';s.field('grid').dispatch('change');assert.equal(s.button('quantizeClip').disabled,true);s.action('quantizeClip');assert.equal(s.edits.length,1);
  s.field('grid').value='480';s.field('grid').dispatch('change');assert.equal(s.button('quantizeClip').disabled,false);
  s.field('velocity').value='';s.action('quantizeClip');assert.equal(s.edits.length,1);assert.equal(s.field('velocity').value,'');assert.match(s.container.querySelector('.note-edit-status').textContent,/typed/);
  s.view.updateTransport({frame:100});assert.match(s.container.querySelector('.note-edit-status').textContent,/typed/);
  s.view.setState({locked:true});assert.equal(s.button('quantizeClip').disabled,true);
  const a=setup('audio');assert.equal(a.button('quantizeClip').disabled,true);assert.equal(a.container.querySelector('.quantize-help').hidden,true);
  const e=setup();e.session.tracks[0].clips[0].notes=[];e.view.render(e.session);assert.equal(e.button('quantizeClip').disabled,true);
});

test('selected note MIDI export reports selection and preserves/rejects typed drafts, locked and audio clips',()=>{
  let target=null;const env=setup('sine',null,value=>{target=value;});
  const view=env.view;
  env.action('exportMidiClip');assert.deepEqual(JSON.parse(JSON.stringify(target)),{trackIndex:0,clipId:'clip'});assert.equal(env.edits.length,0);target=null;
  env.field('velocity').value='0.7';env.action('exportMidiClip');assert.equal(env.field('velocity').value,'0.7');assert.equal(target,null);
  assert.match(env.container.querySelector('.note-edit-status').textContent,/before exporting MIDI/);
  env.field('velocity').value='0.6';env.action('exportMidiClip');assert.equal(env.container.querySelector('.note-edit-status').hidden,true);
  view.setState({locked:true});assert.equal(env.button('exportMidiClip').disabled,true);
  const audio=setup('audio');assert.equal(audio.button('exportMidiClip').disabled,true);
});

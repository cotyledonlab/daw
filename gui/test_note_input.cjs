const test = require('node:test');
const assert = require('node:assert/strict');
const NoteInput = require('./note_input.js');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  emit(name, event = {}) { this.listeners.get(name)?.(event); }
}
function setup(extra = {}) {
  const window = new Events(), document = new Events();
  const sources = [], errors = [], notes = [], requests = [];
  const context = {
    currentTime: 0, destination: {}, resumes: 0, closed: 0,
    resume() { this.resumes++; return Promise.resolve(); },
    close() { this.closed++; return Promise.resolve(); },
    createGain: () => ({ gain: { value: 0, setTargetAtTime() {} }, connect() {}, disconnect() {} }),
    decodeAudioData: async bytes => ({ bytes }),
    createBufferSource() {
      const source = { started: 0, stopped: 0, disconnected: 0,
        start() { this.started++; }, stop() { this.stopped++; },
        connect() {}, disconnect() { this.disconnected++; } };
      sources.push(source); return source;
    },
  };
  let target = { key: 'track-1', drum: false };
  const player = new NoteInput({ window, document, createContext: () => context,
    getTarget: () => target, requestPreview: async note => { requests.push(note); return new ArrayBuffer(10); },
    onNote: note => notes.push(note), onError: error => errors.push(error), ...extra });
  player.setEnabled(true);
  const key = (value, extra = {}) => window.emit('keydown', { key: value, code: `Key${value.toUpperCase()}`, target: {}, ...extra });
  return { player, window, document, context, sources, errors, notes, requests, key, setTarget: value => { target = value; } };
}

test('keyboard opts in, ignores editable/modifier/repeat/holds, maps chromatic and drums', async () => {
  const s = setup();
  s.player.setEnabled(false); s.key('a');
  assert.equal(s.context.resumes, 0);
  s.player.setEnabled(true);
  s.key('a', { target: { tagName: 'INPUT' } });
  s.key('a', { target: { isContentEditable: true } });
  s.key('a', { repeat: true }); s.key('a', { ctrlKey: true });
  assert.equal(s.context.resumes, 0);
  s.key('w'); s.key('w');
  assert.equal(s.context.resumes, 1, 'resume is immediate before network');
  await flush();
  assert.equal(s.notes[0].midi, 49);
  s.window.emit('keyup', { key: 'w', code: 'KeyW' });
  s.setTarget({ key: 'drums', drum: true });
  for (const key of ['a', 's', 'd', 'w']) s.key(key);
  await flush();
  assert.deepEqual(s.notes.slice(1).map(n => n.midi), [36, 38, 42]);
  s.player.dispose();
});

test('blur/hidden/disable cancel late fetch and target change cancels decode', async () => {
  for (const action of ['blur', 'hidden', 'disable', 'target']) {
    let resolve;
    const s = setup({ requestPreview: () => new Promise(r => { resolve = r; }) });
    const playing = s.player.playNote(60);
    await flush();
    if (action === 'blur') s.window.emit('blur');
    if (action === 'hidden') { s.document.hidden = true; s.document.emit('visibilitychange'); }
    if (action === 'disable') s.player.setEnabled(false);
    if (action === 'target') s.setTarget({ key: 'new' });
    resolve(new ArrayBuffer(10));
    assert.equal(await playing, false);
    assert.equal(s.sources.length, 0);
    assert.equal(s.notes.length, 0);
    s.player.dispose();
  }
  let decode;
  const s = setup();
  s.context.decodeAudioData = () => new Promise(r => { decode = r; });
  const playing = s.player.playNote(60); await flush();
  s.player.stop(); decode({});
  assert.equal(await playing, false);
  s.player.dispose();
});

test('bounded pending fetches, voices and cache; stop aborts and dispose cleans listeners', async () => {
  const resolvers = [], signals = [];
  const s = setup({ requestPreview: n => { signals.push(n.signal); return new Promise(r => resolvers.push(r)); } });
  const calls = Array.from({ length: 20 }, (_, i) => s.player.playNote(40 + i));
  await flush();
  assert.equal(resolvers.length, 3);
  s.player.stop();
  assert.ok(signals.every(signal => signal.aborted));
  for (const resolve of resolvers) resolve(new ArrayBuffer(1));
  await Promise.all(calls);
  assert.equal(s.player.pending.size, 0);
  s.player.options.requestPreview = async () => new ArrayBuffer(1);
  for (let i = 0; i < 30; i++) await s.player.playNote(40 + i);
  assert.equal(s.player.voices.size, 6);
  assert.equal(s.player.cache.size, 24);
  const before = s.sources.length;
  await s.player.playNote(69);
  assert.equal(s.sources.length, before + 1);
  s.player.dispose(); s.player.dispose();
  assert.equal(s.player.voices.size, 0);
  assert.equal(s.context.closed, 1);
  assert.equal(s.window.listeners.size, 0);
  assert.ok(s.sources.every(source => source.disconnected === 1));
});

test('failed render produces error without step entry; failed capture is observed', async () => {
  const failure = new Error('render failed');
  const s = setup({ requestPreview: async () => { throw failure; } });
  assert.equal(await s.player.playNote(60), false);
  assert.deepEqual(s.errors, [failure]); assert.equal(s.notes.length, 0);
  s.player.options.requestPreview = async () => new ArrayBuffer(1);
  s.player.options.onNote = async () => { throw new Error('entry failed'); };
  assert.equal(await s.player.playNote(60), true); await flush();
  assert.equal(s.errors.at(-1).message, 'entry failed');
  s.player.dispose();
});

test('explicit MIDI connection handles channels, zero velocity, note off and unplug', async () => {
  const input = { id: 'keyboard', state: 'connected' };
  const access = { inputs: new Map([[input.id, input]]) };
  const permissions = [], statuses = [];
  const s = setup({ navigator: { requestMIDIAccess: async options => { permissions.push(options); return access; } },
    onMIDIStatus: status => statuses.push(status) });
  assert.equal(permissions.length, 0);
  assert.equal(await s.player.connectMIDI(), 'connected');
  assert.deepEqual(permissions, [{ sysex: false }]);
  input.onmidimessage({ data: [0x92, 64, 100] });
  input.onmidimessage({ data: [0x92, 65, 0] });
  input.onmidimessage({ data: [0x82, 64, 100] });
  await flush();
  assert.equal(s.notes.length, 1); assert.equal(s.notes[0].channel, 2);
  assert.equal(s.notes[0].velocity, 100 / 127);
  input.state = 'disconnected'; access.onstatechange();
  assert.equal(input.onmidimessage, null); assert.equal(s.player.voices.size, 0);
  s.player.dispose(); assert.equal(access.onstatechange, null);
  const unsupported = setup({ navigator: {} });
  assert.equal(await unsupported.player.connectMIDI(), 'unsupported'); unsupported.player.dispose();
});

test('cache reuses only matching tone target and velocity; natural end releases voice', async () => {
  const s = setup();
  await s.player.play(60, .5);
  assert.equal(s.requests.length, 1);
  s.sources[0].onended();
  assert.equal(s.player.voices.size, 0);
  await s.player.play(60, .5);
  assert.equal(s.requests.length, 1);
  await s.player.play(60, .6);
  assert.equal(s.requests.length, 2);
  s.setTarget({ key: 'other' });
  await s.player.play(60, .5);
  assert.equal(s.requests.length, 3);
  s.player.reset();
  await s.player.play(60, .5);
  assert.equal(s.requests.length, 4);
  s.player.dispose();
});

test('unlock happens directly in gesture and rejected resume/MIDI permission are handled', async () => {
  const statuses = [];
  const s = setup({ navigator: { requestMIDIAccess: () => { throw new Error('permission denied'); } },
    onMIDIStatus: status => statuses.push(status) });
  const unlocked = s.player.unlock();
  assert.equal(s.context.resumes, 1);
  assert.equal(await unlocked, true);
  assert.equal(await s.player.connectMIDI(), 'unavailable');
  assert.equal(statuses.at(-1), 'unavailable');
  s.context.resume = () => Promise.reject(new Error('audio denied'));
  assert.equal(await s.player.play(60), false);
  assert.equal(s.notes.length, 0);
  assert.equal(s.errors.at(-1).message, 'audio denied');
  s.player.dispose();
});

test('performance on/off is immediate without preview during native playback; repeat and changed pitch release original',async()=>{
  let now=10;const events=[];
  const s=setup({clock:()=>now,getPerformanceTarget:()=>({key:'record',drum:false}),onPerformance:n=>events.push(n)});
  s.setTarget(null);s.key('a');s.key('a');
  assert.equal(events.length,1);assert.equal(events[0].type,'on');assert.equal(events[0].timestamp,10);
  assert.equal(s.context.resumes,0);assert.equal(s.requests.length,0);
  s.player.setBaseMidi(60);now=25;s.window.emit('keyup',{code:'KeyA',key:'a'});
  assert.equal(events[1].type,'off');assert.equal(events[1].midi,48);assert.equal(events[1].timestamp,25);
  s.key('a');assert.equal(events[2].midi,60);s.window.emit('blur');assert.ok(events.some(e=>e.type==='cancel'));
  assert.equal(events.at(-1).cancelled,true);assert.equal(s.player.performanceHeld.size,0);
  s.player.dispose();
});
test('performance capture precedes async preview and MIDI zero velocity releases matching channel',async()=>{
  const events=[],input={id:'keys',state:'connected'},access={inputs:new Map([['keys',input]])};
  const s=setup({onPerformance:n=>events.push(n),navigator:{requestMIDIAccess:async()=>access}});
  s.key('a');assert.equal(events[0].type,'on');assert.equal(s.requests.length,0);await flush();assert.equal(s.requests.length,1);
  await s.player.connectMIDI();input.onmidimessage({data:[0x92,64,100]});input.onmidimessage({data:[0x93,64,100]});
  input.onmidimessage({data:[0x92,64,0]});assert.equal(events.at(-1).type,'off');assert.equal(events.at(-1).channel,2);
  assert.ok(s.player.performanceHeld.has('midi:keys:3:64'));
  s.player.dispose();
});

test('recording Escape leaves held gates for transport Stop; preview Escape cancels',()=>{
  const events=[];let target={key:'record'};
  const s=setup({getPerformanceTarget:()=>target,onPerformance:n=>events.push(n)});
  s.key('a');s.window.emit('keydown',{key:'Escape'});
  assert.equal(events.length,1);assert.equal(s.player.performanceHeld.size,1);
  target=null;s.window.emit('keydown',{key:'Escape'});
  assert.equal(s.player.performanceHeld.size,0);assert.ok(events.some(e=>e.type==='cancel'));s.player.dispose();
});

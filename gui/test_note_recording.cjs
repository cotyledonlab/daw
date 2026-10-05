const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('./note_recording.js');
const E = require('./editor.js');
function setup(options={}) {
  let session=E.addNoteTrack(E.createArrangementSession(),'lead','synth');
  session=E.addNoteClip(session,0,{id:'phrase',start_frame:4800,length_frames:48000,notes:[{id:'recorded-1',start_frame:0,duration_frames:100,frequency_hz:220,velocity:.6}]});
  const r=new R({clock:()=>0,...options});r.arm({session,revision:'rev',trackIndex:0,clipId:'phrase'});
  r.updateTransport({state:'playing',timeline_frame:4800,sample_rate:48000,timestamp:0});
  const on=(timestamp=100,key='a')=>r.capture({type:'on',timestamp,key,frequency_hz:440,velocity:.8});
  const off=(timestamp=300,key='a')=>r.capture({type:'off',timestamp,key});
  const finish=(frame=52800)=>r.finish({session,revision:'rev',transport:{state:'stopped',timeline_frame:frame}});
  return {r,session,on,off,finish};
}
test('release duration, frozen overdub draft, collision-free IDs and retry/accept',()=>{
  const s=setup(),before=structuredClone(s.session);s.on();s.off();
  assert.deepEqual(s.session,before);assert.equal(s.r.count,1);
  assert.throws(()=>s.r.finish({session:s.session,revision:'rev',transport:{state:'playing'}}),/Stop/);
  const result=s.finish();assert.equal(result.notes[0].start_frame,4800);assert.equal(result.notes[0].duration_frames,9600);
  assert.equal(result.notes[0].id,'recorded-2');assert.deepEqual(result.draft.tracks[0].clips[0].notes[0],before.tracks[0].clips[0].notes[0]);
  assert.equal(E.validate(result.draft),null);assert.deepEqual(s.session,before);assert.equal(s.r.active,false);
  result.draft.tracks[0].clips[0].notes=[];assert.equal(s.finish().draft.tracks[0].clips[0].notes.length,2);
  assert.equal(s.r.accept(),true);assert.equal(s.finish(),null);
});
test('duplicate onset ignored, distinct MIDI channels overlap and stop closes held note',()=>{
  const s=setup();assert.equal(s.on(),true);assert.equal(s.on(150),false);s.on(200,'midi:1:60');s.off(300);
  const result=s.finish(24000);assert.equal(result.notes.length,2);assert.equal(result.notes[1].duration_frames,9600);
});
test('count-in onset ignored through release, subsequent onset recorded',()=>{
  const s=setup();s.r.updateTransport({state:'playing',timeline_frame:4800,sample_rate:48000,timestamp:0,count_in_remaining_frames:4800});
  assert.equal(s.on(50),false);assert.equal(s.on(150),false);assert.equal(s.off(200),false);
  s.on(250);s.off(350);const result=s.finish();assert.equal(result.notes[0].start_frame,7200);assert.equal(result.notes[0].duration_frames,4800);
});
test('bounds, cancelled input, backwards or jumping anchors and loop discard whole take',()=>{
  for (const check of [s=>{s.on();s.off();s.on(400);},s=>{s.on();s.on(200,'b');},s=>s.r.capture({type:'cancel'}),
    s=>s.r.updateTransport({state:'playing',timeline_frame:0,sample_rate:48000,timestamp:100}),
    s=>s.r.updateTransport({state:'playing',timeline_frame:100000,sample_rate:48000,timestamp:100}),
    s=>s.r.updateTransport({state:'playing',timeline_frame:4800,sample_rate:48000,timestamp:100,loop:{enabled:true}})]) {
    const s=setup({maxEvents:2,maxHeld:1});check(s);assert.equal(s.r.active,false);assert.equal(s.finish(),null);
  }
});
test('stale revision or externally mutated session prevents a draft',()=>{
  for (const change of ['revision','session']) {const s=setup();s.on();s.off();if(change==='session')s.session.tracks[0].device.gain=.3;
    assert.equal(s.r.finish({session:s.session,revision:change==='revision'?'other':'rev',transport:{state:'stopped',timeline_frame:52800}}),null);}
});
test('clip bounds only truncate new gates with explicit feedback',()=>{
  const s=setup();s.on(900);s.off(1200);const result=s.finish();assert.equal(result.notes[0].duration_frames,4800);assert.equal(result.truncated,1);
  assert.match(s.r.message,/shortened/);assert.equal(s.session.tracks[0].clips[0].notes[0].duration_frames,100);
});

test('native loop region and external pause discard the buffered take explicitly',()=>{
  for (const change of [{loop_region:{start_frame:0,end_frame:48000}},{state:'paused'}]) {
    const s=setup();s.on();s.off();
    assert.equal(s.r.updateTransport({state:'playing',timeline_frame:4800,sample_rate:48000,timestamp:500,...change}),false);
    assert.equal(s.r.active,false);assert.equal(s.finish(),null);assert.match(s.r.message,change.state?/paused/:/loop/);
  }
});

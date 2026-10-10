const test=require('node:test'),assert=require('node:assert/strict'),Capture=require('./audio_capture.js');
test('captured WAV contains bounded PCM16 at the saved project rate',()=>{const v=new DataView(Capture.wav(new Float32Array([-2,-1,0,1,2]),44100));assert.equal(v.getUint32(24,true),44100);assert.equal(v.getUint16(22,true),1);assert.equal(v.getUint32(40,true),10);assert.deepEqual(Array.from({length:5},(_,i)=>v.getInt16(44+i*2,true)),[-32768,-32768,0,32767,32767]);});
test('cancel during microphone permission releases late devices and publishes no take',async()=>{let resolve,closed=0,stopped=0;const permission=new Promise(r=>resolve=r);const capture=new Capture({Recorder:class{},getUserMedia:()=>permission,createContext:()=>({resume:async()=>{},close:async()=>{closed++;}})});const start=capture.start(48000);await new Promise(r=>setImmediate(r));capture.discard();resolve({getTracks:()=>[{stop(){stopped++;}}]});await start;assert.equal(capture.state,'stopped');assert.equal(capture.take,null);assert.equal(stopped,1);assert.equal(closed,1);});
test('microphone rejection frees browser audio resources',async()=>{let closed=0;const capture=new Capture({Recorder:class{},getUserMedia:async()=>{throw new Error('Denied');},createContext:()=>({resume:async()=>{},close:async()=>{closed++;}})});await assert.rejects(()=>capture.start(48000),/Denied/);assert.equal(closed,1);assert.equal(capture.state,'stopped');});

test('cancelled permission rejection cannot release a newer recording',async()=>{
 let reject,closed=0,stopped=0,calls=0;const oldPermission=new Promise((_,r)=>reject=r);
 class Recorder{constructor(){this.state='inactive';}start(){this.state='recording';}stop(){this.state='inactive';}}
 const capture=new Capture({Recorder,getUserMedia:()=>++calls===1?oldPermission:Promise.resolve({getTracks:()=>[{stop(){stopped++;}}]}),createContext:()=>({resume:async()=>{},close:async()=>{closed++;}})});
 const old=capture.start(48000);await new Promise(r=>setImmediate(r));capture.stop();await capture.start(48000);
 reject(new Error('Old request denied'));await old;
 assert.equal(capture.state,'recording');assert.ok(capture.stream);assert.ok(capture.context);assert.equal(closed,1);assert.equal(stopped,0);capture.discard();
});

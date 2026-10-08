const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
function song(kind='synth', rate=48000, tempo=120000) {
  return E.addNoteClip(E.addNoteTrack(E.createArrangementSession(rate,tempo),'lead',kind),0,
    {id:'phrase',start_frame:12345,length_frames:96000,notes:[
      {id:'a',start_frame:0,duration_frames:24000,frequency_hz:E.midiToHz(kind==='drumkit'?36:60),velocity:0.8},
      {id:'b',start_frame:24000,duration_frames:24000,frequency_hz:E.midiToHz(kind==='drumkit'?38:60),velocity:0.4}]});
}
// Independent decoder verifies the bytes a MIDI reader receives, not writer helpers.
function decode(bytes) {
  const b=Buffer.from(bytes);assert.equal(b.subarray(0,4).toString(),'MThd');assert.equal(b.readUInt32BE(4),6);
  assert.equal(b.readUInt16BE(8),0);assert.equal(b.readUInt16BE(10),1);assert.equal(b.readUInt16BE(12),960);
  assert.equal(b.subarray(14,18).toString(),'MTrk');assert.equal(b.readUInt32BE(18),b.length-22);
  let p=22,tick=0;const events=[];
  const vlq=()=>{let n=0,count=0,v;do {assert.ok(++count<=4);v=b[p++];assert.notEqual(v,undefined);n=n*128+(v&127);}while(v&128);return n;};
  while(p<b.length) {
    tick+=vlq();const status=b[p++];assert.ok(status>=128);
    if(status===255) {const type=b[p++],length=vlq(),data=[...b.subarray(p,p+length)];p+=length;events.push({tick,type,data});}
    else {assert.ok([0x80,0x90].includes(status&240));const pitch=b[p++],velocity=b[p++];assert.ok(pitch<128&&velocity<128);events.push({tick,status,pitch,velocity});}
  }
  assert.equal(p,b.length);assert.equal(events.at(-1).type,47);assert.deepEqual(events.at(-1).data,[]);return events;
}
test('format 0 MIDI has tempo, 4/4, clip-relative gates, off-before-on and trailing clip length',()=>{
  const s=song(),before=structuredClone(s),events=decode(E.exportMidiClip(s,0,'phrase'));
  assert.deepEqual(events,[{tick:0,type:81,data:[7,161,32]},{tick:0,type:88,data:[4,2,24,8]},
    {tick:0,status:144,pitch:60,velocity:102},{tick:960,status:128,pitch:60,velocity:0},
    {tick:960,status:144,pitch:60,velocity:51},{tick:1920,status:128,pitch:60,velocity:0},{tick:3840,type:47,data:[]}]);
  assert.deepEqual(s,before);
});
test('drums use channel 10; empty and silent clips export without accidental note-ons',()=>{
  assert.equal(decode(E.exportMidiClip(song('drumkit'),0,'phrase'))[2].status,153);
  for(const silent of [true,false]) {const s=song();if(silent)s.tracks[0].clips[0].notes.forEach(n=>n.velocity=0);else s.tracks[0].clips[0].notes=[];
    assert.equal(decode(E.exportMidiClip(s,0,'phrase')).length,3);}
});
test('fractional tempos/rates round endpoints once and tiny gates stay audible without changing source',()=>{
  for(const rate of [8000,44100,48000,192000])for(const tempo of [20000,137123,300000]) {
    const s=song('synth',rate,tempo),c=s.tracks[0].clips[0];c.notes=[{id:'n',start_frame:1234,duration_frames:1,frequency_hz:443.123456,velocity:0.0001}];
    const before=structuredClone(s),events=decode(E.exportMidiClip(s,0,'phrase')),on=events[2],off=events[3];
    assert.equal(on.tick,Math.round(1234*960*tempo/(rate*60000)));assert.equal(off.tick,Math.max(on.tick+1,Math.round(1235*960*tempo/(rate*60000))));
    assert.equal(on.pitch,69);assert.equal(on.velocity,1);assert.deepEqual(s,before);
  }
});
test('same rounded pitch overlap, out-of-range pitch, missing/audio targets and invalid sessions reject',()=>{
  const s=song(),c=s.tracks[0].clips[0];c.notes[1].start_frame=1;c.notes[1].frequency_hz=E.midiToHz(60.1);
  const before=structuredClone(s);assert.throws(()=>E.exportMidiClip(s,0,'phrase'),/overlapping/);assert.deepEqual(s,before);
  c.notes=[{...c.notes[0],frequency_hz:1}];assert.throws(()=>E.exportMidiClip(s,0,'phrase'),/outside MIDI/);
  assert.throws(()=>E.exportMidiClip(song(),0,'missing'),/existing/);
  const audio=E.addAudioTrack(song(),'pcm');assert.throws(()=>E.exportMidiClip(audio,1,'phrase'),/sequenced sine/);
  s.sample_rate=NaN;assert.throws(()=>E.exportMidiClip(s,0,'phrase'));
});
test('polyphony and long multibyte deltas encode deterministically regardless of note storage order',()=>{
  const s=song(),c=s.tracks[0].clips[0];c.length_frames=48000*180;
  c.notes[1].start_frame=0;c.notes[1].frequency_hz=E.midiToHz(64);c.notes[0].duration_frames=48000*100;
  const first=E.exportMidiClip(s,0,'phrase');c.notes.reverse();assert.deepEqual(E.exportMidiClip(s,0,'phrase'),first);
  assert.equal(decode(first).at(-1).tick,345600);
});
test('actual app export downloads applied state only without requests/history or losing drafts',()=>{
  const source=fs.readFileSync(require.resolve('./app.js'),'utf8'),a=source.indexOf('  function exportSelectedMidi('),b=source.indexOf('  function timestamp()',a);
  for(const mode of ['ok','draft','take','busy','locked','history','unsupported','invalid']) {
    const applied=song(),before=structuredClone(applied),downloads=[],errors=[];
    const ctx={applied,SessionEditor:E,Blob,busy:mode==='busy',editLocked:()=>mode==='locked',historyAction:mode==='history',unsupportedSession:mode==='unsupported',
      noteRecording:mode==='take'?{pending:true}:null,studioHasDrafts:()=>mode==='draft',timestamp:()=> 'test',
      download:(...args)=>downloads.push(args),setNotice(){},arrangementView:{reportError:e=>errors.push(e)},announceError(){}};
    vm.createContext(ctx);vm.runInContext(source.slice(a,b),ctx);
    assert.equal(ctx.exportSelectedMidi({trackIndex:0,clipId:mode==='invalid'?'missing':'phrase'}),mode==='ok');
    assert.equal(downloads.length,mode==='ok'?1:0);assert.deepEqual(applied,before);
    if(mode==='ok'){assert.equal(downloads[0][0].type,'audio/midi');assert.equal(downloads[0][1],'daw-clip-test.mid');}
  }
});

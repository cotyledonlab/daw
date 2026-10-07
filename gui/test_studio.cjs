const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('./editor.js');
const Studio = require('./studio.js');
const contract = require('./studio_contract.json');
const History = require('./history.js');
const part = (role,operations)=>[{role,operations}];
test('musician relative transpose preserves microtiming, velocities and all other tracks',()=>{
  const song=E.createMusicalDemoSession(); song.tracks[0].clips[0].notes[0].frequency_hz=442.345; song.tracks[0].clips[0].notes[0].start_frame=7;
  const original=structuredClone(song), track=song.tracks[0], clip=track.clips[0];
  const next=Studio.apply(song,part('musician',[{op:'transpose',track_id:track.id,clip_id:clip.id,semitones:12}]),{track_id:track.id,clip_id:clip.id},contract,E);
  assert.deepEqual(song,original); assert.deepEqual(next.tracks.slice(1),song.tracks.slice(1));
  for(let i=0;i<clip.notes.length;i++){assert.equal(next.tracks[0].clips[0].notes[i].frequency_hz,clip.notes[i].frequency_hz*2);assert.equal(next.tracks[0].clips[0].notes[i].start_frame,clip.notes[i].start_frame);assert.equal(next.tracks[0].clips[0].notes[i].velocity,clip.notes[i].velocity);}
});
test('engineer relative dB and processing form one undoable batch',()=>{
  const song=E.editMixer(E.createMusicalDemoSession(),0,{gain:0.8}), track=song.tracks[0];
  const next=Studio.apply(song,part('engineer',[{op:'gainDb',track_id:track.id,db:-6},{op:'addEffect',track_id:track.id,id:'soft',effect:{kind:'lowpass',cutoff_hz:950,bypass:false}}]),null,contract,E);
  assert.equal(next.tracks[0].mixer.gain,0.8*10**(-6/20));assert.deepEqual(next.tracks[0].clips,track.clips);assert.deepEqual(next.tracks.slice(1),song.tracks.slice(1));
  const history=new History();history.commit(song,next);assert.deepEqual(history.undoTarget(next),song);assert.equal(history.undo.length,1);
});
test('bad final operation rolls back the entire local batch',()=>{
  const song=E.createMusicalDemoSession(), original=structuredClone(song);
  assert.throws(()=>Studio.apply(song,part('engineer',[{op:'gainDb',track_id:'lead',db:-3},{op:'mixer',track_id:'bass',patch:{gain:99}}]),null,contract,E));
  assert.deepEqual(song,original);
});
test('roles, selected scope, unknown fields and embedded runtime code are enforced',()=>{
  const song=E.createMusicalDemoSession();
  for(const [role,ops,scope] of [
    ['musician',[{op:'mixer',track_id:'lead',patch:{gain:0.5}}],null],
    ['engineer',[{op:'gainDb',track_id:'bass',db:-3}],{track_id:'lead'}],
    ['musician',[{op:'deleteClip',track_id:'lead',clip_id:song.tracks[0].clips[0].id}],{track_id:'lead',clip_id:'other'}],
    ['producer',[{op:'tempo',tempo_milli_bpm:90000,session:{}}],null],
    ['producer',[{op:'device',track_id:'lead',patch:{kind:'csound',program:'evil'}}],null],
    ['producer',[{op:'editNote',track_id:'lead',clip_id:song.tracks[0].clips[0].id,note_id:song.tracks[0].clips[0].notes[0].id,patch:{id:'changed'}}],null],
  ]) assert.throws(()=>Studio.apply(song,part(role,ops),scope,contract,E));
});
test('producer creates a complete phrase and tempo leaves all saved frames unchanged',()=>{
  const song=E.createArrangementSession();
  const next=Studio.apply(song,part('producer',[{op:'addTrack',id:'part',kind:'synth'},{op:'addClip',track_id:'part',clip:{id:'phrase',start_frame:1,length_frames:48000,notes:[]}},{op:'addNote',track_id:'part',clip_id:'phrase',note:{id:'a',start_frame:17,duration_frames:20000,frequency_hz:440.123,velocity:0.6}},{op:'tempo',tempo_milli_bpm:100000}]),null,contract,E);
  assert.equal(E.validate(next),null);assert.equal(next.tracks[0].clips[0].start_frame,1);assert.equal(next.tracks[0].clips[0].notes[0].start_frame,17);assert.equal(next.tracks[0].clips[0].notes[0].frequency_hz,440.123);
});

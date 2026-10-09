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
test('four internal tasks apply in order as one undoable producer interaction',()=>{
  const song=E.createMusicalDemoSession(), original=structuredClone(song);
  const parts=[{role:'producer',operations:[]},
    ...['lead','bass','drums'].map(track_id=>({role:'musician',operations:[{op:'device',track_id,patch:{gain:0.1}}]})),
    {role:'engineer',operations:[{op:'gainDb',track_id:'lead',db:-3}]}];
  const next=Studio.apply(song,parts,null,contract,E), history=new History();history.commit(song,next);
  assert.equal(history.undo.length,1);assert.deepEqual(history.undoTarget(next),original);
  assert.deepEqual(song,original); assert.equal(next.tracks[2].device.gain,0.1);
  parts[4].operations.push({op:'mixer',track_id:'bass',patch:{gain:99}});
  assert.throws(()=>Studio.apply(song,parts,null,contract,E));assert.deepEqual(song,original);
  assert.throws(()=>Studio.apply(song,[...parts,{role:'engineer',operations:[]}],null,contract,E));
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
test('copy notes propagates a changed groove while preserving positions, other tracks and Undo',()=>{
  const song=E.createMusicalDemoSession(), drums=song.tracks[2], source=drums.clips[0];
  drums.clips[1].notes[0].id='different-id';const original=structuredClone(song);
  const op={op:'copyNotes',track_id:drums.id,clip_id:source.id,target_clip_ids:drums.clips.slice(1).map(c=>c.id)};
  const next=Studio.apply(song,part('musician',[{op:'editNote',track_id:drums.id,clip_id:source.id,note_id:source.notes[0].id,patch:{velocity:0.731}},op]),null,contract,E);
  for(let i=1;i<drums.clips.length;i++){
    assert.deepEqual(next.tracks[2].clips[i].notes,next.tracks[2].clips[0].notes);
    assert.equal(next.tracks[2].clips[i].start_frame,drums.clips[i].start_frame);
    assert.equal(next.tracks[2].clips[i].length_frames,drums.clips[i].length_frames);
  }
  assert.deepEqual(next.tracks.slice(0,2),song.tracks.slice(0,2));assert.deepEqual(song,original);
  const history=new History();history.commit(song,next);assert.deepEqual(history.undoTarget(next),original);
  const different=structuredClone(song);different.tracks[2].clips[1].notes[0].frequency_hz+=0.125;
  assert.throws(()=>Studio.apply(different,part('musician',[op]),null,contract,E));
  assert.throws(()=>Studio.apply(song,part('musician',[op]),{track_id:drums.id,clip_id:source.id},contract,E));
  assert.throws(()=>Studio.apply(song,part('engineer',[op]),null,contract,E));
  assert.throws(()=>Studio.apply(song,part('musician',[{op:'editNote',track_id:drums.id,clip_id:drums.clips[1].id,note_id:'different-id',patch:{velocity:0.2}},op]),null,contract,E));
});
test('copy notes rejects the entire batch when an edited source no longer fits a target',()=>{
  let song=E.addNoteTrack(E.createArrangementSession(),'t','sine');
  for(const [id,length_frames] of [['source',4000],['target',800]]){
    song=E.addNoteClip(song,0,{id,start_frame:0,length_frames,notes:[]});
    song=E.addNote(song,0,id,{id:'n',start_frame:0,duration_frames:600,frequency_hz:440.123,velocity:0.7});
  }
  const original=structuredClone(song);
  assert.throws(()=>Studio.apply(song,part('musician',[
    {op:'editNote',track_id:'t',clip_id:'source',note_id:'n',patch:{duration_frames:1600}},
    {op:'copyNotes',track_id:'t',clip_id:'source',target_clip_ids:['target']}]),null,contract,E));
  assert.deepEqual(song,original);
});

test('complete note rewrites preserve clip placement and other data as one Undo entry',()=>{
  const song=E.createMusicalDemoSession(), original=structuredClone(song);
  const operations=song.tracks[0].clips.map(c=>({op:'setNotes',track_id:'lead',clip_id:c.id,notes:[{id:'new-hook',start_frame:1200,duration_frames:6000,frequency_hz:660.123,velocity:0.8}]}));
  const next=Studio.apply(song,part('musician',operations),{track_id:'lead'},contract,E);
  assert.deepEqual(next.tracks.slice(1),song.tracks.slice(1));assert.deepEqual(next.tracks[0].device,song.tracks[0].device);
  next.tracks[0].clips.forEach((c,i)=>{assert.equal(c.start_frame,song.tracks[0].clips[i].start_frame);assert.equal(c.length_frames,song.tracks[0].clips[i].length_frames);assert.deepEqual(c.notes,operations[i].notes);});
  const history=new History();history.commit(song,next);assert.deepEqual(history.undoTarget(next),original);assert.deepEqual(song,original);
  assert.throws(()=>Studio.apply(song,part('musician',operations),{track_id:'lead',clip_id:'phrase'},contract,E));
  for(const notes of ([{id:'bad'}],Array(129).fill(operations[0].notes[0]),[{...operations[0].notes[0],duration_frames:999999}], [{...operations[0].notes[0],frequency_hz:NaN}], [operations[0].notes[0],operations[0].notes[0]])) {
    assert.throws(()=>Studio.apply(song,part('musician',[operations[0],{...operations[1],notes}]),null,contract,E));assert.deepEqual(song,original);
  }
});

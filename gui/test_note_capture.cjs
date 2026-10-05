const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
const History = require('./history.js');
const NoteInput = require('./note_input.js');
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };

function setup() {
  let song = E.addNoteTrack(E.createArrangementSession(),'lead','synth');
  for (const id of ['phrase','other']) song=E.addNoteClip(song,0,{id,start_frame:id==='phrase'?0:96000,length_frames:96000,notes:[]});
  const nodes = new Map(), history = new History(), edits = [];
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id,{checked:false,disabled:false,hidden:false,value:'0.25',selectedOptions:[{textContent:'C3'}],textContent:'',classList:{add(){},remove(){}}});
    return nodes.get(id);
  };
  let selected='phrase',cursor=0,advances=0;
  const ctx = {SessionEditor:E,applied:song,draft:structuredClone(song),notePreviewAvailable:true,sessionRevision:'revision-1',unsupportedSession:false,
    noteRecording:null,recordingStarting:false,starting:false,busy:false,historyAction:false,stepCapturing:false,noteTargetKey:null,noteProjectGeneration:0,player:{context:null},
    dirty:false,locked:false,$,isDirty:()=>ctx.dirty,nativeLocked:()=>ctx.locked,
    noteInput:{enabled:false,resets:0,reset(){this.resets++;},setEnabled(v){this.enabled=v;},setVolume(v){this.volume=v;}},
    arrangementView:{getNoteTarget:()=>selected?{trackIndex:0,trackId:'lead',clipId:selected}:null,
      getStepTarget:()=>selected?{trackIndex:0,clipId:selected,start_frame:cursor,duration_frames:6000,key:`${selected}:${cursor}`} : null,
      acceptStepAdvance(target){if(target.key!==`${selected}:${cursor}`) return false; cursor+=target.duration_frames;advances++;return true;}}
  };
  ctx.editArrangement=async action=> {
    edits.push(action);
    const next=E.insertStepNote(ctx.applied,action.trackIndex,action.clipId,{id:`input-${edits.length}`,...action.note});
    history.commit(ctx.applied,next);ctx.applied=next;ctx.draft=structuredClone(next);ctx.sessionRevision=`revision-${edits.length+1}`;return true;
  };
  const text=fs.readFileSync(require.resolve('./app.js'),'utf8');
  const start=text.indexOf('  function noteInputTarget('),end=text.indexOf('  function announceError(',start);
  assert.ok(start>=0 && end>start,'app capture functions remain present');
  vm.createContext(ctx);vm.runInContext(text.slice(start,end),ctx);
  $('#note-input-enabled').checked=true;
  ctx.syncNoteInput();
  return {ctx,$,edits,history,get cursor(){return cursor;},get advances(){return advances;},select(id){selected=id;},setCursor(frame){cursor=frame;}};
}
const note = {frequency_hz:443.12345,velocity:0.723456789};
test('actual capture requires arming; checked accepted notes advance once with independent undo',async()=>{
  const s=setup(),before=structuredClone(s.ctx.applied);
  await s.ctx.captureStepNote(note);assert.equal(s.edits.length,0);assert.equal(s.cursor,0);
  s.$('#step-entry-enabled').checked=true;
  await s.ctx.captureStepNote(note);await s.ctx.captureStepNote(note);
  assert.equal(s.advances,2);assert.equal(s.cursor,12000);assert.equal(s.history.undo.length,2);
  assert.equal(s.ctx.applied.tracks[0].clips[0].notes[0].frequency_hz,note.frequency_hz);
  const previous=s.history.undoTarget(s.ctx.applied);s.history.acceptUndo(s.ctx.applied);assert.equal(previous.tracks[0].clips[0].notes.length,1);
  assert.deepEqual(s.history.undoTarget(previous),before);
  assert.match(s.$('#note-input-status').textContent,/Undo removes this note/);
});
test('failed checked replacement keeps cursor and project; concurrent presses cannot capture while apply is pending',async()=>{
  const s=setup(),before=structuredClone(s.ctx.applied);s.$('#step-entry-enabled').checked=true;
  let finish;
  s.ctx.editArrangement=async action=>{s.edits.push(action);s.ctx.busy=true;s.ctx.dirty=true;s.ctx.syncNoteInput();return new Promise(resolve=>{finish=resolve;});};
  const capture=s.ctx.captureStepNote(note);await flush();
  assert.equal(s.ctx.stepCapturing,true);assert.equal(s.ctx.noteInput.enabled,true,'own checked apply keeps preview cache/held-key lifecycle enabled');
  assert.equal(s.$('#note-preview-button').disabled,true);
  await s.ctx.captureStepNote(note);assert.equal(s.edits.length,1);
  s.ctx.busy=false;s.ctx.dirty=false;finish(false);await capture;
  assert.equal(s.cursor,0);assert.equal(s.advances,0);assert.deepEqual(s.ctx.applied,before);assert.equal(s.history.undo.length,0);
  assert.equal(s.ctx.stepCapturing,false);assert.match(s.$('#note-input-status').textContent,/rejected.*not advanced/);
});
test('actual target gates unsupported/audio/playback/dirty state and remains usable only through own apply',()=>{
  for (const [field,value] of [['noteRecording',{pending:{}}],['recordingStarting',true],['notePreviewAvailable',false],['applied',null],['sessionRevision',null],['unsupportedSession',true],['locked',true],['starting',true],['historyAction',true],['busy',true],['dirty',true]]) {
    const s=setup();s.ctx[field]=value;assert.equal(s.ctx.noteInputTarget(),null,field);s.ctx.syncNoteInput();assert.equal(s.ctx.noteInput.enabled,false,field);
  }
  const audio=setup();audio.ctx.applied.tracks[0].device.kind='audio';assert.equal(audio.ctx.noteInputTarget(),null);
  const browser=setup();browser.ctx.player.context={};assert.equal(browser.ctx.noteInputTarget(),null);
  const own=setup();own.ctx.stepCapturing=true;own.ctx.busy=true;own.ctx.dirty=true;assert.ok(own.ctx.noteInputTarget());
  own.ctx.locked=true;assert.equal(own.ctx.noteInputTarget(),null,'own capture cannot bypass native transport locking');
});
test('preview cache key ignores note edits/revision but changes for saved sound, project, and clip identity',()=>{
  const s=setup(),initial=s.ctx.noteInputTarget().key;
  s.ctx.applied.tracks[0].clips[0].notes.push({id:'new',start_frame:0,duration_frames:100,frequency_hz:440,velocity:0.8});s.ctx.sessionRevision='revision-new';
  assert.equal(s.ctx.noteInputTarget().key,initial);s.ctx.syncNoteInput();assert.equal(s.ctx.noteInput.resets,1);
  for (const change of [()=>s.ctx.applied.tracks[0].device.gain=0.4,()=>s.ctx.applied.tracks[0].mixer={gain:0.7},()=>s.ctx.applied.tracks[0].effects=[{id:'gain',kind:'gain',gain:0.5,bypass:false}],()=>s.ctx.applied.tracks[0].automation=[{effect_id:'gain',points:[{frame:0,value:0.4}]}],()=>s.ctx.noteProjectGeneration++,()=>s.select('other')]) {
    const old=s.ctx.noteInputTarget().key;change();assert.notEqual(s.ctx.noteInputTarget().key,old);s.ctx.syncNoteInput();
  }
});
test('actual NoteInput cancels stale preview before root capture can redirect to another selected clip',async()=>{
  const s=setup();s.$('#step-entry-enabled').checked=true;
  let resolvePreview;
  const context={currentTime:0,destination:{},resume:async()=>{},close:async()=>{},createGain:()=>({gain:{value:0,setTargetAtTime(){}},connect(){},disconnect(){}}),
    decodeAudioData:async()=>({}),createBufferSource:()=>({connect(){},start(){},stop(){},disconnect(){}})};
  const input=new NoteInput({createContext:()=>context,getTarget:s.ctx.noteInputTarget,requestPreview:()=>new Promise(resolve=>{resolvePreview=resolve;}),onNote:s.ctx.captureStepNote});
  s.ctx.noteInput=input;s.ctx.syncNoteInput();
  const playing=input.play(60);await flush();s.select('other');s.ctx.syncNoteInput();resolvePreview(new ArrayBuffer(8));
  assert.equal(await playing,false);await flush();assert.equal(s.edits.length,0);assert.equal(s.cursor,0);
  s.$('#step-entry-enabled').checked=false;
  input.options.requestPreview=async()=>new ArrayBuffer(8);
  assert.equal(await input.play(61),true,'disarmed step entry still auditions through actual NoteInput');
  await flush();assert.equal(s.edits.length,0);assert.equal(s.cursor,0);
  input.dispose();
});
test('slow actual preview cannot capture at a cursor moved after its press',async()=>{
  const s=setup();s.$('#step-entry-enabled').checked=true;
  let resolvePreview;
  const context={currentTime:0,destination:{},resume:async()=>{},close:async()=>{},createGain:()=>({gain:{value:0,setTargetAtTime(){}},connect(){},disconnect(){}}),
    decodeAudioData:async()=>({}),createBufferSource:()=>({connect(){},start(){},stop(){},disconnect(){}})};
  const input=new NoteInput({createContext:()=>context,getTarget:s.ctx.noteInputTarget,requestPreview:()=>new Promise(resolve=>{resolvePreview=resolve;}),onNote:s.ctx.captureStepNote});
  s.ctx.noteInput=input;s.ctx.syncNoteInput();
  const key=s.ctx.noteInputTarget().key,playing=input.play(60);await flush();
  s.setCursor(24000);s.ctx.syncNoteInput();assert.equal(s.ctx.noteInputTarget().key,key,'cursor changes preserve sound cache');
  resolvePreview(new ArrayBuffer(8));assert.equal(await playing,true);await flush();
  assert.equal(s.edits.length,0);assert.equal(s.cursor,24000);assert.equal(s.advances,0);
  assert.match(s.$('#note-input-status').textContent,/position changed during preview/);
  input.dispose();
});
test('arming during a slow actual audition does not retrospectively capture its press',async()=>{
  const s=setup();s.$('#step-entry-enabled').checked=false;
  let resolvePreview;
  const context={currentTime:0,destination:{},resume:async()=>{},close:async()=>{},createGain:()=>({gain:{value:0,setTargetAtTime(){}},connect(){},disconnect(){}}),
    decodeAudioData:async()=>({}),createBufferSource:()=>({connect(){},start(){},stop(){},disconnect(){}})};
  const input=new NoteInput({createContext:()=>context,getTarget:s.ctx.noteInputTarget,requestPreview:()=>new Promise(resolve=>{resolvePreview=resolve;}),onNote:s.ctx.captureStepNote});
  s.ctx.noteInput=input;s.ctx.syncNoteInput();
  const key=s.ctx.noteInputTarget().key,playing=input.play(60);await flush();
  s.$('#step-entry-enabled').checked=true;s.ctx.syncNoteInput();assert.equal(s.ctx.noteInputTarget().key,key,'arming preserves sound cache');
  resolvePreview(new ArrayBuffer(8));assert.equal(await playing,true);await flush();
  assert.equal(s.edits.length,0);assert.equal(s.cursor,0);assert.equal(s.advances,0);
  input.dispose();
});

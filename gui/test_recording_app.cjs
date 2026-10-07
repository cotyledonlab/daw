const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
const Recording = require('./note_recording.js');
const History = require('./history.js');
const source = fs.readFileSync(require.resolve('./app.js'), 'utf8');

function functions(first, next) {
  const start = source.indexOf(`  ${first}`), end = source.indexOf(`  ${next}`, start);
  assert.ok(start >= 0 && end > start, `${first} remains extractable`);
  return source.slice(start, end);
}

function setup() {
  let song = E.addNoteTrack(E.createArrangementSession(), 'lead', 'synth');
  for (const id of ['phrase', 'other']) song = E.addNoteClip(song, 0, {id, start_frame:0, length_frames:96000, notes:[]});
  const nodes = new Map(), calls = [], history = new History();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {checked:false, disabled:false, hidden:false, value:'0', textContent:'', selectedOptions:[{textContent:'C3'}]});
    return nodes.get(id);
  };
  let selected = 'phrase';
  const ctx = {SessionEditor:E, applied:song, draft:structuredClone(song), sessionRevision:'r1',
    noteRecording:new Recording({clock:()=>0}), recordingStarting:false, takeApplying:false,
    noteProjectGeneration:0, noteTargetKey:null, metronomeAvailable:true, nativeAvailable:true,
    checkedReplacementAvailable:true, notePreviewAvailable:true, stepCapturing:false,
    busy:false, nativeModeChange:false, nativePlayGeneration:0, playGeneration:0, starting:false,
    paused:false, metadataSuppressed:false, untilStoppedAvailable:true, untilStoppedDevices:['synth'],
    recoverButton:{}, outputMode:{value:'native'}, playButton:{}, playState:{}, nativeSnapshot:{state:'stopped'},
    performance:{now:()=>0}, $, editHistory:history, clone:structuredClone,
    nativeActive:()=>['playing','starting','paused'].includes(ctx.nativeSnapshot.state),
    nativeLocked:()=>ctx.nativeActive(), isDirty:()=>false,
    noteInputTarget:()=>ctx.nativeLocked()?null:{trackIndex:0,clipId:selected,key:selected,track_id:'lead'},
    player:{context:null,stop(){}}, applyDraft:async()=>true, hasSources:()=>false,
    requestAppliedEffectMetadata(){}, setNotice(message){calls.push(['notice',message]);},
    announceError(message){throw Error(message);}, noteInputError(error){throw error;},
    setBusy(value){ctx.busy=value;}, validateSession:()=>null,
    invalidateEffectMetadata(){}, rememberEffects(){}, configureSessionMode(){}, selectSampleRate(){},
    suggestArrangementDuration(){}, renderTracks(){}, arrangementView:{reportError(){},updateTransport(){}},
    request:async(path, options)=>{assert.equal(path,'/api/note/take');calls.push(['request',JSON.parse(options.body)]); return {json:async()=>({session:JSON.parse(options.body).session,revision:'r2'})};},
  };
  ctx.noteInput = {enabled:false, resets:0, reset(){this.resets++; ctx.onPerformance({cancelled:true});},
    setEnabled(value){this.enabled=value;if(!value)ctx.onPerformance({cancelled:true});},setVolume(){}};
  ctx.syncStatus = ()=>{ctx.syncNoteInput();ctx.syncRecordingControls();};
  ctx.nativeCommand = async command => {calls.push(['native',command]); ctx.nativeSnapshot={state:'playing',timeline_frame:0,sample_rate:48000};ctx.syncStatus();};
  ctx.stopNative = async()=>{ctx.nativeSnapshot={state:'stopped',timeline_frame:12000};await ctx.finishNoteTake(ctx.nativeSnapshot);};
  vm.createContext(ctx);
  vm.runInContext(functions('function syncNoteInput()', 'function resetNoteProject()') +
    functions('function beatControlsEligible()', 'function announceError(') +
    functions('async function toggleNative()', 'async function changeOutputMode()') +
    functions('function acceptImportedSession(', 'async function importAudioFile('), ctx);
  const callback = source.match(/onPerformance: (event => \{[^\n]+\})/);
  assert.ok(callback, 'actual performance callback remains present');
  vm.runInContext(`onPerformance = ${callback[1]}`,ctx);
  return {ctx,$,calls,history,select(id){selected=id;}};
}

test('actual recording start survives intentional input resets and native start sync', async()=>{
  const s=setup(); await s.ctx.startNoteRecording();
  assert.equal(s.ctx.noteRecording.active,true,s.ctx.noteRecording.message);
  assert.equal(s.ctx.recordingStarting,false);
  assert.equal(s.ctx.noteInput.enabled,true);
  assert.equal(s.ctx.noteTargetKey,'record:0:r1:phrase');
  const resets=s.ctx.noteInput.resets;
  s.select('other');s.ctx.syncStatus();
  assert.equal(s.ctx.noteInput.resets,resets,'selection cannot redirect the armed input');
  assert.equal(s.ctx.recordingInputTarget().key,'record:0:r1:phrase');
  assert.equal(s.calls.filter(c=>c[0]==='native').length,1);
});

async function captured(s) {
  await s.ctx.startNoteRecording();
  s.ctx.onPerformance({type:'on',key:'a',timestamp:0,frequency_hz:440,velocity:.5});
  s.ctx.onPerformance({type:'off',key:'a',timestamp:100});
}

test('actual Stop finishes take before disabling input and applies exactly one history entry',async()=>{
  const s=setup(),base=structuredClone(s.ctx.applied);await captured(s);
  await s.ctx.toggleNative();
  assert.equal(s.ctx.noteRecording.take,null);
  assert.equal(s.calls.filter(c=>c[0]==='request').length,1);
  assert.equal(s.history.undo.length,1);
  assert.deepEqual(s.history.undoTarget(s.ctx.applied),base);
  assert.equal(s.ctx.applied.tracks[0].clips[0].notes.length,1);
  assert.equal(s.ctx.applied.tracks[0].clips[1].notes.length,0);
  assert.match(s.$('#record-notes-status').textContent,/Undo removes the whole take/);
});

test('actual rejected validation and stale HTTP preserve pending take for retry without history',async()=>{
  for (const kind of ['validation','http']) {
    const s=setup(),base=structuredClone(s.ctx.applied);await captured(s);
    if(kind==='validation')s.ctx.validateSession=()=> 'invalid gate';
    else s.ctx.request=async(path)=>{assert.equal(path,'/api/note/take');throw Error('422 stale revision');};
    await s.ctx.toggleNative();
    assert.ok(s.ctx.noteRecording.pending,kind);
    assert.deepEqual(s.ctx.applied,base);
    assert.equal(s.history.undo.length,0);
    assert.equal(s.ctx.takeApplying,false);
    assert.equal(s.ctx.busy,false);
    assert.match(s.$('#record-notes-status').textContent,/Take kept for retry/);
    s.ctx.validateSession=()=>null;
    s.ctx.request=async(path)=>{assert.equal(path,'/api/note/take');return {json:async()=>({session:s.ctx.noteRecording.pending.draft,revision:'r2'})};};
    assert.equal(await s.ctx.applyNoteTake(),true);
    assert.equal(s.history.undo.length,1);
  }
});

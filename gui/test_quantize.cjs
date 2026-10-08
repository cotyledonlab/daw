const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const E = require('./editor.js');
const History = require('./history.js');
function song(rate=48000,tempo=120000,kind='synth') {
  let s=E.addNoteTrack(E.createArrangementSession(rate,tempo),'lead',kind);
  s=E.addNoteClip(s,0,{id:'phrase',start_frame:1234,length_frames:96000,notes:[
    {id:'a',start_frame:2999,duration_frames:1777,frequency_hz:kind==='drumkit'?E.midiToHz(36):443.123456789,velocity:0.723456789},
    {id:'b',start_frame:9001,duration_frames:7777,frequency_hz:kind==='drumkit'?E.midiToHz(38):220.123456789,velocity:0.612345678}]});
  s=E.duplicateClip(s,0,'phrase','other',100000);
  s=E.editMixer(s,0,{gain:0.823456789,pan:-0.312345678});
  s=E.addEffect(s,0,{kind:'gain',gain:0.83456789,bypass:false},'level');
  s=E.addGainAutomationPoint(s,0,'level',{frame:321,value:0.923456789});
  s=E.addAudioTrack(s,'pcm');
  return E.addAudioClip(s,1,{id:'take',start_frame:4321,length_frames:8888,source_path:'assets/take.wav',source_offset_frames:101,gain:0.621234567,fade_in_frames:99,fade_out_frames:207});
}
test('quantize moves only selected clip starts and preserves precise musical/audio data',()=>{
  for(const kind of ['sine','synth','drumkit','pd_instrument']) {
    const source=song(48000,120000,kind), before=structuredClone(source);
    const next=E.quantizeClip(source,0,'phrase',240);
    assert.deepEqual(next.tracks[0].clips[0].notes.map(n=>n.start_frame),[0,12000]);
    const expected=structuredClone(source);expected.tracks[0].clips[0].notes[0].start_frame=0;expected.tracks[0].clips[0].notes[1].start_frame=12000;
    assert.deepEqual(next,expected);assert.deepEqual(source,before);assert.equal(E.validate(next),null);
    assert.deepEqual(JSON.parse(JSON.stringify(next)),next);
  }
});
test('midpoints round later without double tick rounding at multiple rates and tempos',()=>{
  const source=song();
  for(const [frame,expected] of [[2999,0],[3000,6000],[3001,6000]]) {
    const s=E.editNote(source,0,'phrase','a',{start_frame:frame});
    assert.equal(E.quantizeClip(s,0,'phrase',240).tracks[0].clips[0].notes[0].start_frame,expected);
  }
  for(const [rate,tempo] of [[44100,137000],[48000,300000],[96000,20000]]) for(const grid of [240,480,960]) {
    let s=song(rate,tempo);s.tracks[0].clips[0].length_frames=rate*20;s.tracks[0].clips[1].start_frame=rate*30;
    const n=s.tracks[0].clips[0].notes[0];n.start_frame=E.ticksToFrames(s,grid*2)+1;
    assert.equal(E.quantizeClip(s,0,'phrase',grid).tracks[0].clips[0].notes[0].start_frame,E.ticksToFrames(s,grid*2));
  }
});
test('empty/aligned clips are no-ops; invalid grids, overflow and Pd overlap reject transactionally',()=>{
  const s=song(),before=structuredClone(s);
  for(const grid of [0,-1,120,NaN,Infinity,'240']) assert.throws(()=>E.quantizeClip(s,0,'phrase',grid),/grid/);
  assert.throws(()=>E.quantizeClip(s,1,'take',240),/note|sine/);
  assert.throws(()=>E.quantizeClip(s,0,'missing',240),/existing/);
  assert.deepEqual(s,before);
  const aligned=E.quantizeClip(s,0,'phrase',240);assert.equal(E.quantizeClip(aligned,0,'phrase',240),aligned);
  const empty=E.addNoteClip(s,0,{id:'empty',start_frame:0,length_frames:1,notes:[]});assert.equal(E.quantizeClip(empty,0,'empty',240),empty);
  const boundary=E.editNote(s,0,'phrase','b',{start_frame:93999,duration_frames:2001});
  assert.throws(()=>E.quantizeClip(boundary,0,'phrase',240),/past the clip end/);assert.equal(boundary.tracks[0].clips[0].notes[0].start_frame,2999);
  const pd=song(48000,120000,'pd_instrument');pd.tracks[0].clips[0].notes[0].duration_frames=3000;pd.tracks[0].clips[0].notes[1].start_frame=6000;
  assert.equal(E.validate(pd),null);assert.throws(()=>E.quantizeClip(pd,0,'phrase',960),/monophonic/);
  assert.equal(pd.tracks[0].clips[0].notes[0].start_frame,2999);
});
function app(source=song()) {
  let engine=structuredClone(source),revision='1';const history=new History(),calls=[],errors=[];
  const ctx={draft:structuredClone(source),applied:structuredClone(source),SessionEditor:E,clone:structuredClone,
    busy:false,historyAction:false,unsupportedSession:false,noteRecording:null,editLocked:()=>false,nativeLocked:()=>false,nativeActive:()=>false,
    studioHasDrafts:()=>false,validateSession:E.validate,editHistory:history,liveParameterQueue:Promise.resolve(),capabilitiesLoading:Promise.resolve(),bridgeDiscovered:true,checkedReplacementAvailable:true,sessionRevision:revision,
    document:{querySelectorAll:()=>[]},updateDraftFromControls(){},renderTracks(){},setNotice(message){calls.push(['notice',message]);},announceError(message){errors.push(message);},arrangementView:{reportError(message){errors.push(message?.message||message);}},
    setBusy(v){ctx.busy=v;},invalidateEffectMetadata(){},rememberEffects(){},configureSessionMode(){},selectSampleRate(){},syncStatus(){},requestAppliedEffectMetadata(){},
    inspectCurrentSession:async()=>{ctx.sessionRevision=revision;return {session:engine};},
    request:async(path,options)=>{
      calls.push(['request',path]);const body=JSON.parse(options.body);assert.equal(body.expected_revision,revision);
      if(ctx.reject) throw Error('stale or queue full');engine=body.session;revision=String(Number(revision)+1);return {json:async()=>engine};
    },
  };ctx.isDirty=()=>JSON.stringify(ctx.draft)!==JSON.stringify(ctx.applied);
  const text=fs.readFileSync(require.resolve('./app.js'),'utf8');
  const extract=(a,b)=>text.slice(text.indexOf(a),text.indexOf(b,text.indexOf(a)));
  vm.createContext(ctx);vm.runInContext(extract('  async function editArrangement(', '  async function importPdPreset(')+extract('  async function applyDraft(', '  function download('),ctx);
  return {ctx,calls,errors,history};
}
test('actual app quantize applies one checked edit and whole-clip undo/redo; aligned repeats make no requests',async()=>{
  for(const playing of [false,true]) {
    const s=app(),before=structuredClone(s.ctx.applied);s.ctx.nativeActive=()=>playing;s.ctx.nativeLocked=()=>playing;
    assert.equal(await s.ctx.editArrangement({type:'quantizeClip',trackIndex:0,clipId:'phrase',gridTicks:240}),true);
    assert.deepEqual(s.calls.filter(c=>c[0]==='request'),[['request',playing?'/api/session/live':'/api/session']]);assert.equal(s.history.undo.length,1);
    const after=structuredClone(s.ctx.applied);assert.deepEqual(s.history.undoTarget(after),before);assert.equal(s.history.acceptUndo(after),true);assert.deepEqual(s.history.redoTarget(before),after);
    await s.ctx.editArrangement({type:'quantizeClip',trackIndex:0,clipId:'phrase',gridTicks:240});assert.equal(s.calls.filter(c=>c[0]==='request').length,1);
  }
});
test('actual app quantize preserves drafts, pending takes, locks and rejected state/history',async()=>{
  for(const mode of ['draft','take','locked','busy','stale','boundary']) {
    const s=app();if(mode==='draft'){s.ctx.studioHasDrafts=()=>true;s.ctx.draft.tracks[0].device.gain=0.223456789;}
    if(mode==='take') s.ctx.noteRecording={pending:true};if(mode==='locked')s.ctx.editLocked=()=>true;if(mode==='busy')s.ctx.busy=true;if(mode==='stale')s.ctx.reject=true;
    if(mode==='boundary') {s.ctx.draft=E.editNote(s.ctx.draft,0,'phrase','b',{start_frame:93999,duration_frames:2001});s.ctx.applied=structuredClone(s.ctx.draft);}
    const before=structuredClone(s.ctx.draft),applied=structuredClone(s.ctx.applied);
    assert.equal(await s.ctx.editArrangement({type:'quantizeClip',trackIndex:0,clipId:'phrase',gridTicks:240}),false);
    assert.deepEqual(s.ctx.draft,before);assert.deepEqual(s.ctx.applied,applied);assert.equal(s.history.undo.length,0);
    assert.equal(s.calls.filter(c=>c[0]==='request').length,mode==='stale'?1:0);
  }
});

const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs');
const E=require('./editor.js'), History=require('./history.js');
const source=fs.readFileSync(require.resolve('./app.js'),'utf8');
function extract(first,next){const a=source.indexOf(`  ${first}`),b=source.indexOf(`  ${next}`,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
test('live gate unlocks built-in note/clip edits but preserves recording and unsupported locks',()=>{
  const ctx={nativeLocked:()=>true,liveArrangementEditsAvailable:true,nativeSnapshot:{state:'playing',live_arrangement_edits:true},noteRecording:null};
  vm.createContext(ctx);vm.runInContext(extract('function editLocked()', 'function liveParameterEligible'),ctx);
  assert.equal(ctx.editLocked(),false);ctx.nativeSnapshot.state='paused';assert.equal(ctx.editLocked(),false);
  ctx.noteRecording={active:true};assert.equal(ctx.editLocked(),true);ctx.noteRecording=null;
  ctx.nativeSnapshot.live_arrangement_edits=false;assert.equal(ctx.editLocked(),true);
});
test('actual Apply sends checked live replacement and commits one history snapshot',async()=>{
  const song=E.createMusicalDemoSession(), next=structuredClone(song), history=new History(), calls=[];
  next.tracks[0].device.gain=.1;
  const ctx={draft:next,applied:song,sessionRevision:'7',editHistory:history,nativeActive:()=>true,validateSession:()=>null,isDirty:()=>true,
    liveParameterQueue:Promise.resolve(),setBusy(){},setNotice(){},capabilitiesLoading:Promise.resolve(),bridgeDiscovered:true,checkedReplacementAvailable:true,
    request:async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return {json:async()=>next};},invalidateEffectMetadata(){},
    inspectCurrentSession:async()=>({revision:'8',session:next}),clone:structuredClone,rememberEffects(){},configureSessionMode(){},selectSampleRate(){},renderTracks(){},
    syncStatus(){},requestAppliedEffectMetadata(){},announceError:message=>assert.fail(message)};
  vm.createContext(ctx);vm.runInContext(extract('async function applyDraft(', 'function download('),ctx);
  assert.equal(await ctx.applyDraft({readControls:false}),true);
  assert.equal(calls[0][0],'/api/session/live');assert.equal(calls[0][1].expected_revision,'7');assert.equal(history.undo.length,1);assert.deepEqual(history.undoTarget(next),song);
});
test('actual live clip handler edits notes but rejects track deletion without changing drafts',async()=>{
  const ctx={draft:E.createMusicalDemoSession(),busy:false,editLocked:()=>false,nativeLocked:()=>true,noteRecording:null,historyAction:false,unsupportedSession:false,
    clone:structuredClone,SessionEditor:E,validateSession:()=>null,renderTracks(){},applyDraft:async()=>true};
  vm.createContext(ctx);vm.runInContext(extract('async function editArrangement(', 'async function importPdPreset('),ctx);
  const before=structuredClone(ctx.draft);
  assert.equal(await ctx.editArrangement({type:'editNote',trackIndex:0,clipId:'phrase',noteId:'n1',patch:{frequency_hz:300}}),true);
  assert.equal(ctx.draft.tracks[0].clips[0].notes[0].frequency_hz,300);
  const after=structuredClone(ctx.draft);assert.equal(await ctx.editArrangement({type:'deleteTrack',trackIndex:0}),false);assert.deepEqual(ctx.draft,after);
  assert.deepEqual(ctx.draft.tracks.slice(1),before.tracks.slice(1));
});

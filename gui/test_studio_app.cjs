const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const E = require('./editor.js');
const Studio = require('./studio.js');
const contract = require('./studio_contract.json');
const History = require('./history.js');
const source = fs.readFileSync(require.resolve('./app.js'),'utf8');
function extract(first,next){const a=source.indexOf(`  ${first}`),b=source.indexOf(`  ${next}`,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
function setup(){
  const song=E.createMusicalDemoSession(),nodes=new Map(),messages=[],history=new History();
  const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',disabled:false,textContent:'',checked:false,classList:{contains:()=>false}});return nodes.get(id);};
  $('#studio-prompt').value='Lower lead by 3 dB';$('#studio-role').value='engineer';
  const plan={revision:'7',parts:[{role:'engineer',reply:'Reduce lead.',operations:[{op:'gainDb',track_id:'lead',db:-3}]}]};
  const ctx={$,studioConfig:{available:true,operations:contract},studioPending:false,studioHistory:[],sessionRevision:'7',applied:song,draft:structuredClone(song),noteProjectGeneration:0,
    busy:false,nativeLocked:()=>false,player:{context:null},starting:false,noteRecording:null,historyAction:false,unsupportedSession:false,studioRecorder:null,studioMicStarting:false,studioTranscribing:false,
    SessionEditor:E,StudioActions:Studio,clone:structuredClone,noticeEl:{textContent:'',classList:{contains:()=>false}},
    studioScope:()=>null,studioHasDrafts:()=>false,studioMessage:(...args)=>messages.push(args),syncStatus(){},
    request:async()=>({json:async()=>plan}),
    applyDraft:async options=>{assert.equal(options.readControls,false);history.commit(ctx.applied,ctx.draft);ctx.applied=structuredClone(ctx.draft);ctx.sessionRevision='8';return true;},
    speakStudio:async()=>{},studioCommand:async()=>{},setNotice(){},};
  vm.createContext(ctx);vm.runInContext(extract('async function sendStudioPrompt()', 'async function toggleStudioMic()'),ctx);
  return {ctx,$,history,messages,song,plan};
}
test('actual prompt handler applies one checked batch with controls excluded and one Undo entry',async()=>{
  const s=setup();await s.ctx.sendStudioPrompt();assert.equal(s.history.undo.length,1);assert.deepEqual(s.history.undoTarget(s.ctx.applied),s.song);assert.equal(s.ctx.studioPending,false);assert.match(s.$('#studio-status').textContent,/one Undo/);
});
test('actual prompt handler preserves edits made while a provider was working',async()=>{
  const s=setup();s.ctx.request=async()=>{s.ctx.draft.tracks[0].device.gain=0.333;return {json:async()=>s.plan};};
  await s.ctx.sendStudioPrompt();assert.equal(s.ctx.draft.tracks[0].device.gain,0.333);assert.deepEqual(s.ctx.applied,s.song);assert.equal(s.history.undo.length,0);assert.match(s.$('#studio-status').textContent,/changed/);
});
test('actual prompt handler preserves typed drafts, active playback and rejected takes',async()=>{
  for(const state of ['drafts','playing','take']){
    const s=setup();if(state==='drafts')s.ctx.studioHasDrafts=()=>true;if(state==='playing')s.ctx.nativeLocked=()=>true;if(state==='take')s.ctx.noteRecording={pending:true};
    await s.ctx.sendStudioPrompt();assert.deepEqual(s.ctx.draft,s.song);assert.equal(s.history.undo.length,0);assert.match(s.$('#studio-status').textContent,/No studio edits applied/);
  }
});
test('actual prompt handler keeps local controls and history on rejected engine update',async()=>{
  const s=setup();s.ctx.applyDraft=async()=>false;await s.ctx.sendStudioPrompt();assert.equal(JSON.stringify(s.ctx.draft),JSON.stringify(s.song));assert.equal(s.history.undo.length,0);assert.equal(s.$('#studio-prompt').value,'Lower lead by 3 dB');
});
test('actual prompt handler rejects a stale response and never applies scoped command',async()=>{
  for(const kind of ['stale','command']){
    const s=setup();if(kind==='stale')s.plan.revision='6';else {s.plan.parts=[{role:'engineer',reply:'save',operations:[{op:'command',name:'save',args:{}}]}];}
    await s.ctx.sendStudioPrompt();assert.deepEqual(s.ctx.applied,s.song);assert.equal(s.history.undo.length,0);
  }
});

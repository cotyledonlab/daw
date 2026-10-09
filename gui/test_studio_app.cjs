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
  const ctx={Date,TextDecoder,setInterval:()=>1,clearInterval(){},$,studioConfig:{available:true,operations:contract},studioPending:false,studioHistory:[],sessionRevision:'7',applied:song,draft:structuredClone(song),noteProjectGeneration:0,
    busy:false,nativeLocked:()=>false,editLocked:()=>false,player:{context:null},starting:false,noteRecording:null,historyAction:false,unsupportedSession:false,studioRecorder:null,studioMicStarting:false,studioTranscribing:false,
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
test('one producer request streams four task summaries and commits only the final batch',async()=>{
  const s=setup();s.$('#studio-role').value='producer';s.$('#studio-prompt').value='French-house whole song';
  s.plan.parts=[{role:'producer',reply:'Filtered disco.',operations:[]},
    ...['lead','bass','drums'].map(track_id=>({role:'musician',reply:`Revoice ${track_id}.`,operations:[{op:'device',track_id,patch:{gain:0.1}}]})),
    {role:'engineer',reply:'Balance.',operations:[{op:'gainDb',track_id:'lead',db:-3}]}];
  let requests=0;s.ctx.request=async()=>{requests++;return streamResponse([
    ...s.plan.parts.map(part=>({type:'summary',role:part.role,message:part.reply})),{type:'result',plan:s.plan}]);};
  await s.ctx.sendStudioPrompt();assert.equal(requests,1);assert.equal(s.history.undo.length,1);
  assert.deepEqual(s.history.undoTarget(s.ctx.applied),s.song);assert.equal(s.ctx.applied.tracks[2].device.gain,0.1);
  assert.equal(s.messages.filter(m=>m[0]==='musician · proposal').length,3);assert.equal(s.ctx.studioHistory.length,2);
});
test('actual prompt handler preserves edits made while a provider was working',async()=>{
  const s=setup();s.ctx.request=async()=>{s.ctx.draft.tracks[0].device.gain=0.333;return {json:async()=>s.plan};};
  await s.ctx.sendStudioPrompt();assert.equal(s.ctx.draft.tracks[0].device.gain,0.333);assert.deepEqual(s.ctx.applied,s.song);assert.equal(s.history.undo.length,0);assert.match(s.$('#studio-status').textContent,/changed/);
});
test('actual prompt handler preserves typed drafts, active playback and rejected takes',async()=>{
  for(const state of ['drafts','playing','take']){
    const s=setup();if(state==='drafts')s.ctx.studioHasDrafts=()=>true;if(state==='playing'){s.ctx.nativeLocked=()=>true;s.ctx.editLocked=()=>true;}if(state==='take')s.ctx.noteRecording={pending:true};
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

test('actual prompt handler preserves the next direction typed during inference',async()=>{
  const s=setup();s.ctx.request=async()=>{s.$('#studio-prompt').value='Now add a counter melody';return {json:async()=>s.plan};};
  await s.ctx.sendStudioPrompt();assert.equal(s.$('#studio-prompt').value,'Now add a counter melody');assert.equal(s.history.undo.length,1);
});
test('actual microphone transcription preserves newer typing and releases microphone tracks',async()=>{
  const s=setup();let stopped=false;
  class Recorder {
    static isTypeSupported(){return true;}
    constructor(){this.handlers={};this.state='inactive';}
    addEventListener(name,callback){this.handlers[name]=callback;}
    start(){this.state='recording';this.handlers.dataavailable({data:new Blob(['audio'])});}
    stop(){this.state='inactive';return this.handlers.stop();}
  }
  Object.assign(s.ctx,{navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){stopped=true;}}]})}},MediaRecorder:Recorder,Blob,clearTimeout(){},setTimeout:()=>1,
    studioMicStream:null,studioMicTimer:null,studioAudioUrl:null});
  s.ctx.studioConfig.voice_available=true;s.$('#studio-audio').pause=()=>{};
  s.ctx.request=async()=>{s.$('#studio-prompt').value='A new typed direction';return {json:async()=>({text:'Lower the melody by 3 dB'})};};
  vm.runInContext(extract('async function toggleStudioMic()', "$('#studio-file-action').addEventListener"),s.ctx);
  await s.ctx.toggleStudioMic();await s.ctx.studioRecorder.stop();
  assert.equal(stopped,true);assert.equal(s.ctx.studioTranscribing,false);assert.equal(s.ctx.studioRecorder,null);assert.equal(s.$('#studio-prompt').value,'A new typed direction');assert.deepEqual(s.messages.at(-1),['Voice transcript','Lower the melody by 3 dB']);
});

test('actual prompt handler applies relative edits during supported native playback',async()=>{
  const s=setup();s.ctx.nativeLocked=()=>true;s.ctx.editLocked=()=>false;await s.ctx.sendStudioPrompt();
  assert.equal(s.history.undo.length,1);assert.notDeepEqual(s.ctx.applied,s.song);
});

function streamResponse(events, cut=17) {
  const wire = Buffer.from(events.map(e=>JSON.stringify(e)).join('\n')+'\n'); let offset=0;
  return {headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({
    async read(){ if(offset===wire.length)return {done:true};const value=wire.subarray(offset,offset+cut);offset+=value.length;return {value,done:false};},
    async cancel(){} })}};
}
test('streamed delegation snippets appear before completion and failed child preserves project',async()=>{
  const s=setup();s.ctx.request=async()=>streamResponse([
    {type:'summary',role:'producer',message:'I propose a quieter lead.'},
    {type:'delegation',role:'producer',message:'To engineer: lower lead'},
    {type:'error',error:'Engineer · model: Timed out. No studio edits applied.'}]);
  await s.ctx.sendStudioPrompt();assert.deepEqual(s.ctx.applied,s.song);assert.equal(s.history.undo.length,0);
  assert.equal(s.messages[1][1],'I propose a quieter lead.');assert.match(s.messages[2][1],/To engineer/);
  assert.match(s.$('#studio-status').textContent,/Timed out/);assert.equal(s.$('#studio-prompt').value,'Lower lead by 3 dB');
});
test('fragmented UTF-8 stream applies only a complete result and rejects dropped connections',async()=>{
  const s=setup();s.ctx.request=async()=>streamResponse([{type:'summary',role:'engineer',message:'Réduire 🎵'}, {type:'result',plan:s.plan}],1);
  await s.ctx.sendStudioPrompt();assert.equal(s.history.undo.length,1);assert.equal(s.messages[1][1],'Réduire 🎵');
  const dropped=setup();dropped.ctx.request=async()=>streamResponse([{type:'summary',role:'engineer',message:'Proposal'}]);
  await dropped.ctx.sendStudioPrompt();assert.equal(dropped.history.undo.length,0);assert.deepEqual(dropped.ctx.applied,dropped.song);
  assert.match(dropped.$('#studio-status').textContent,/before a complete result/);
});

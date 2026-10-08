const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const E=require('./editor.js'),M=require('./midi_file.js'),History=require('./history.js');
const end=[0,255,47,0];
function file(tracks,division=480,format=tracks.length===1?0:1){
  return new Uint8Array([77,84,104,100,0,0,0,6,0,format,0,tracks.length,division>>8,division&255,
    ...tracks.flatMap(body=>[77,84,114,107,(body.length>>>24)&255,(body.length>>>16)&255,(body.length>>>8)&255,body.length&255,...body])]);
}
const phrase=()=>file([[0,144,60,100,0x83,0x60,128,60,0,...end]]);
test('one-note import uses project tempo, adds a new lane at placement and preserves exact old state',()=>{
  let s=E.createMusicalDemoSession();s=E.addAudioTrack(s,'pcm');s=E.addAudioClip(s,s.tracks.length-1,{id:'take',start_frame:1234,length_frames:7777,source_path:'assets/take.wav',source_offset_frames:99,gain:0.723456789,fade_in_frames:20,fade_out_frames:70});
  const before=structuredClone(s),result=E.importMidiFile(s,phrase(),'Test.mid',4321),next=result.session;
  assert.equal(result.notes,1);assert.equal(result.ignored,0);assert.deepEqual(s,before);assert.deepEqual(next.tracks.slice(0,-1),s.tracks);
  const c=next.tracks.at(-1).clips[0];assert.equal(c.start_frame,4321);assert.equal(c.length_frames,24000);
  assert.deepEqual(c.notes,[{id:'n-1',start_frame:0,duration_frames:24000,frequency_hz:E.midiToHz(60),velocity:100/127}]);assert.equal(E.validate(next),null);
  const h=new History();h.commit(s,next);assert.deepEqual(h.undoTarget(next),s);assert.equal(h.acceptUndo(next),true);assert.deepEqual(h.redoTarget(s),next);
});
test('format 1 separates source tracks/channels, conductor metadata and drums, respects running status',()=>{
  const bytes=file([[0,255,81,3,7,161,32,...end],
    [0,144,60,64,0,145,64,100,0x83,0x60,144,60,0,0,145,64,0,...end],
    [0,153,36,127,0x81,0x70,36,0,...end]]);
  const parsed=M.parse(bytes);assert.equal(parsed.groups.length,3);assert.equal(parsed.division,480);
  const imported=E.importMidiFile(E.createArrangementSession(),bytes).session;assert.deepEqual(imported.tracks.map(t=>t.device.kind),['synth','synth','drumkit']);assert.equal(imported.tempo_milli_bpm,120000);
});
test('arbitrary PPQ/rate/tempo endpoints round once, source tempo/controllers never move old frames',()=>{
  for(const rate of [8000,44100,48000,192000])for(const tempo of [20000,137123,300000]) {
    const bytes=file([[0,255,81,3,15,66,64,0,192,10,0,176,7,100,1,144,60,1,1,128,60,0,...end]],32767);
    const s=E.createArrangementSession(rate,tempo),result=E.importMidiFile(s,bytes);assert.equal(result.ignored,2);
    const note=result.session.tracks[0].clips[0].notes[0],start=Math.round(rate*60000/(32767*tempo)),finish=Math.max(start+1,Math.round(2*rate*60000/(32767*tempo)));
    assert.equal(note.start_frame,start);assert.equal(note.duration_frames,finish-start);assert.equal(result.session.tempo_milli_bpm,tempo);
  }
});
test('export/import roundtrip preserves standard note gates and levels within MIDI precision',()=>{
  const s=E.createMusicalDemoSession();for(let i=0;i<s.tracks.length;i++) {
    const c=s.tracks[i].clips[0],bytes=E.exportMidiClip(s,i,c.id),next=E.importMidiFile(E.createArrangementSession(),bytes).session;
    const expected=c.notes.filter(n=>n.velocity>0).map(n=>({...n,id:null,velocity:Math.round(n.velocity*127)/127}));
    const actual=next.tracks[0].clips[0].notes.map(n=>({...n,id:null}));const order=(a,b)=>a.start_frame-b.start_frame||a.frequency_hz-b.frequency_hz;assert.deepEqual(actual.sort(order),expected.sort(order));
  }
});
test('malformed, truncated, ambiguous, unsupported and oversized files reject without mutation',()=>{
  const s=E.createArrangementSession(),before=structuredClone(s);
  const bad=[new Uint8Array(),new Uint8Array(M.limits.bytes+1),phrase().subarray(0,25),file([[0,60,90,...end]]),file([[0,144,60,90,...end]]),
    file([[0,128,60,0,...end]]),file([[0,144,60,100,0,144,60,100,...end]]),file([[0,144,60,90,0,128,60,0,...end]]),
    file([[128,128,128,128,0,144,60,90,...end]]),file([[0,144,128,90,...end]]),file([[0,255,47,1,0]]),file([[...end,0]]),
    file([[0,144,60,90,1,128,60,0]]),file([[...end]],0),file([[...end]],0xe728),file([[...end]],480,2)];
  for(const bytes of bad){assert.throws(()=>E.importMidiFile(s,bytes));assert.deepEqual(s,before);}
  assert.throws(()=>E.importMidiFile(s,phrase(),'name',-1),/placement/);
  assert.throws(()=>E.importMidiFile(s,file([[0,153,35,90,1,137,35,0,...end]])),/Drum/);
  const many=E.createArrangementSession();for(let i=0;i<64;i++)many.tracks.push({id:`t${i}`,mode:'sequenced',device:{kind:'sine',frequency_hz:440,gain:0.1},clips:[],effects:[]});
  assert.throws(()=>E.importMidiFile(many,phrase()),/64 tracks/);
});
function app(){
  let engine=E.createArrangementSession(),revision='1';const history=new History(),errors=[],calls=[];
  const ctx={draft:structuredClone(engine),applied:structuredClone(engine),SessionEditor:E,MidiFile:M,Uint8Array,clone:structuredClone,
    busy:false,historyAction:false,unsupportedSession:false,noteRecording:null,nativeLocked:()=>false,editLocked:()=>false,nativeActive:()=>false,studioHasDrafts:()=>false,
    sessionRevision:revision,$:()=>({value:'0'}),setBusy(v){ctx.busy=v;},setNotice(){},announceError:e=>errors.push(e),renderTracks(){},suggestArrangementDuration(){},arrangementView:{reportError(){}},
    validateSession:E.validate,liveParameterQueue:Promise.resolve(),capabilitiesLoading:Promise.resolve(),bridgeDiscovered:true,checkedReplacementAvailable:true,editHistory:history,
    invalidateEffectMetadata(){},rememberEffects(){},configureSessionMode(){},selectSampleRate(){},syncStatus(){},requestAppliedEffectMetadata(){},
    inspectCurrentSession:async()=>{ctx.sessionRevision=revision;return {session:engine};},
    request:async(path,options)=>{calls.push(path);if(ctx.reject)throw Error('stale');const body=JSON.parse(options.body);assert.equal(body.expected_revision,revision);engine=body.session;revision=String(Number(revision)+1);return {json:async()=>engine};}
  };ctx.isDirty=()=>JSON.stringify(ctx.draft)!==JSON.stringify(ctx.applied);
  const source=fs.readFileSync(require.resolve('./app.js'),'utf8'),extract=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
  vm.createContext(ctx);vm.runInContext(extract('  async function importMidiFile(', '  async function importAudioFile(')+extract('  async function applyDraft(', '  function download('),ctx);
  return {ctx,calls,history,errors};
}
test('actual importer applies one stopped checked undoable transaction; drafts/takes/locks/stale/read race retain state',async()=>{
  for(const mode of ['ok','draft','take','busy','native','locked','stale','race','bad']) {
    const a=app(),s=a.ctx;if(mode==='draft')s.studioHasDrafts=()=>true;if(mode==='take')s.noteRecording={pending:true};if(mode==='busy')s.busy=true;
    if(mode==='native')s.nativeLocked=()=>true;if(mode==='locked')s.editLocked=()=>true;if(mode==='stale')s.reject=true;
    const before=structuredClone(s.draft),applied=structuredClone(s.applied);
    const f={name:'test.mid',size:phrase().length,arrayBuffer:async()=>{if(mode==='race')s.sessionRevision='2';return mode==='bad'?new ArrayBuffer(5):phrase().buffer;}};
    assert.equal(await s.importMidiFile(f),mode==='ok');assert.equal(a.history.undo.length,mode==='ok'?1:0);
    assert.deepEqual(a.calls,mode==='ok'||mode==='stale'?['/api/session']:[]);
    if(mode!=='ok'){assert.deepEqual(s.draft,before);assert.deepEqual(s.applied,applied);}else assert.deepEqual(a.history.undoTarget(s.applied),before);
  }
});

test('parser bounds event, note and split-lane growth before session publication',()=>{
  const events=[];for(let i=0;i<M.limits.events;i++)events.push(0,255,1,0);
  assert.throws(()=>M.parse(file([[...events,...end]])),/event limit/);
  const notes=[];for(let i=0;i<=M.limits.notes;i++)notes.push(0,144,60,90,1,128,60,0);
  assert.throws(()=>M.parse(file([[...notes,...end]])),/note limit/);
  const tracks=Array.from({length:5},()=>{const data=[];for(let ch=0;ch<16;ch++)data.push(0,144|ch,60,90,1,128|ch,60,0);return [...data,...end];});
  assert.throws(()=>M.parse(file(tracks)),/64 lanes/);
});

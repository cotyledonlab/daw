const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('./app.js'),'utf8');
test('retained audio take stays bound to its original project after rejected Record',async()=>{
 const nodes=new Map(),imports=[],errors=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{disabled:false,hidden:true,checked:false,textContent:'',handlers:{},addEventListener(event,fn){this.handlers[event]=fn;}});return nodes.get(id);};
 const ctx={$,...{busy:false,audioCapture:null,audioTakeRevision:null,sessionRevision:'1',applied:{sample_rate:48000},audioProjectsAvailable:true,unsupportedSession:false,noteRecording:null},MediaRecorder:class{},window:{addEventListener(){}},nativeActive:()=>false,isDirty:()=>false,announceError:e=>errors.push(e),importAudioFile:async take=>{imports.push(take);return true;}};
 ctx.AudioCapture=class{constructor(options){this.options=options;this.state='stopped';this.take=null;}async start(){if(this.take)throw Error('Existing take');this.state='recording';}discard(){this.take=null;}};
 ctx.setBusy=value=>{ctx.busy=value;for(const node of nodes.values())node.disabled=value;ctx.syncAudioCaptureControls();};
 vm.createContext(ctx);const start=source.indexOf('  function syncAudioCaptureControls()'),end=source.indexOf('  async function addCsoundFile(',start);vm.runInContext(source.slice(start,end),ctx);
 await $('#record-audio-button').handlers.click();assert.equal(ctx.audioTakeRevision,'1');
 const take={name:'old.wav'};ctx.audioCapture.state='stopped';ctx.audioCapture.take=take;ctx.audioCapture.options.onReady();assert.equal($('#record-audio-button').disabled,true);
 ctx.setBusy(true);ctx.setBusy(false);assert.equal($('#record-audio-button').disabled,true);
 ctx.sessionRevision='2';await $('#record-audio-button').handlers.click();assert.equal(ctx.audioTakeRevision,'1');await $('#use-audio-take').handlers.click();assert.equal(imports.length,0);assert.equal(ctx.audioCapture.take,take);assert.match(errors.at(-1),/Project changed/);
});

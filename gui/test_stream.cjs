const test=require('node:test'),assert=require('node:assert/strict'),Stream=require('./stream.js');
function setup(){
 const calls=[],sources=[];let frame=0,id='1';
 const context={currentTime:0,state:'suspended',resume:async()=>{context.state='running';},suspend:async()=>{context.state='suspended';},close:async()=>{context.state='closed';},destination:{},createGain:()=>({gain:{value:0,setTargetAtTime(){}},connect(){}}),createBuffer:(channels,frames,rate)=>({getChannelData:()=>new Float32Array(frames)}),createBufferSource:()=>{const s={connect(){},disconnect(){},start(time){this.time=time;},stop(){this.stopped=true;}};sources.push(s);return s;}};
 const request=async(path,options)=>{const p=JSON.parse(options.body);calls.push(p);
 if(p.action==='read'){const start=frame;frame+=4096;return {headers:{get:()=>JSON.stringify({sample_rate:48000,start_frame:start})},arrayBuffer:async()=>new ArrayBuffer(4096*8)};}
 if(p.action==='seek')frame=p.frame;
 return {json:async()=>({stream_id:id,state:'playing',sample_rate:48000})};};
 const stream=new Stream(request,{createContext:()=>context,onError:error=>{throw error;}});
 return {stream,context,calls,sources};
}
test('bounded prebuffer, audible clock, pause/resume, seek and stop release every source',async()=>{
 const {stream,context,calls,sources}=setup();await stream.start('4',0.3);assert.equal(calls.filter(c=>c.action==='read').length,2);assert.equal(stream.snapshot().timeline_frame,0);assert.equal(stream.snapshot().count_in_remaining_frames,5760);
 context.currentTime=0.15;assert.equal(stream.snapshot().timeline_frame,1440);await stream.control('pause');assert.equal(context.state,'suspended');assert.equal(stream.state,'paused');const held=stream.snapshot().timeline_frame;
 await stream.control('seek',{frame:9600});assert.equal(stream.snapshot().timeline_frame,9600);assert.ok(sources.every(s=>s.stopped));assert.equal(stream.state,'paused');assert.ok(held>0);
 await stream.control('resume');await stream.pumping;const stopped=await stream.control('stop');assert.equal(stopped.state,'stopped');assert.equal(context.state,'closed');assert.equal(stream.sources.size,0);
});
test('replacement cancels audio promptly rather than keeping stale blocks playing',async()=>{
 const {stream,context}=setup();await stream.start('4',0.3);stream.onError=()=>{};stream.wire=async action=>action==='status'?{state:'stopped',stream_id:'2'}:{};const result=await stream.control('status');assert.equal(result.state,'error');assert.equal(context.state,'closed');assert.equal(stream.sources.size,0);
});
test('late network block after Stop never reaches the listening graph',async()=>{
 const {stream,sources}=setup();let release;const pending=new Promise(resolve=>release=resolve);const original=stream.wire.bind(stream);
 stream.wire=async(action,p)=>{if(action==='read'){await pending;}return original(action,p);};
 const starting=stream.start('1',0.3);await new Promise(resolve=>setImmediate(resolve));await stream.control('stop');release();await starting;assert.equal(sources.length,0);assert.equal(stream.state,'stopped');
});

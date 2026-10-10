/* One bounded server-rendered PCM stream. The browser owns the listening clock. */
class BrowserStream {
  constructor(request, {createContext = () => new AudioContext({latencyHint:'interactive'}), onError = () => {}} = {}) {
    this.request = request; this.createContext = createContext; this.onError = onError;
    this.state = 'stopped'; this.generation = 0; this.sources = new Set(); this.marks = [];
    this.volume = 0.25; this.underruns = 0; this.loop = null;
  }
  unlock() {
    if (!this.context) {
      this.context = this.createContext(); this.master = this.context.createGain();
      this.master.gain.value = this.volume; this.master.connect(this.context.destination);
    }
    return this.context.resume();
  }
  async wire(action, params = {}) {
    const response = await this.request('/api/stream', {method:'POST', body:JSON.stringify({action,...params}), signal:AbortSignal.timeout(5000)});
    return action === 'read' ? response : response.json();
  }
  snapshot() {
    const now = this.context?.currentTime || 0;
    let frame = this.lastFrame || 0;
    for (const mark of this.marks) {
      if (now < mark.time) break;
      frame = mark.start + Math.min(mark.frames,Math.max(0,Math.floor((now-mark.time)*this.rate)));
      if (this.loop && frame >= this.loop.end_frame) frame = this.loop.start_frame + (frame-this.loop.end_frame) % (this.loop.end_frame-this.loop.start_frame);
    }
    this.lastFrame = frame;
    return {state:this.state, timeline_frame:frame, sample_rate:this.rate, loop_region:this.loop,
      live_arrangement_edits:this.state === 'playing' || this.state === 'paused', source_mode:'prepared',
      count_in_remaining_frames:Math.max(0,Math.round(((this.marks[0]?.time||now)-now)*this.rate)),
      until_stopped:true, device:'Browser stream', buffered_seconds:Math.max(0,(this.nextTime||0)-now), underruns:this.underruns};
  }
  async start(revision, volume) {
    await this.unlock(); await this.pumping; this.volume = volume; this.master.gain.value = volume;
    let result;
    try {result = await this.wire('play', {expected_revision:revision});}
    catch(error) {await this.control('stop');throw error;}
    this.id = result.stream_id; this.rate = result.sample_rate; this.state = 'playing';
    this.lastFrame = 0; this.loop = null; this.underruns = 0; this.marks = [];
    this.nextTime = this.context.currentTime + 0.12;
    this.run(); await this.pumping; return this.snapshot();
  }
  run() {
    clearTimeout(this.timer);
    if (this.state !== 'playing' || this.pumping) return;
    const generation = this.generation;
    this.pumping = this.pump(generation).catch(error => {
      if (generation !== this.generation) return;
      void this.fail(error);
    }).finally(() => {
      this.pumping = null;
      if (generation === this.generation && this.state === 'playing') this.timer = setTimeout(()=>this.run(),20);
    });
  }
  async pump(generation) {
    // A slow connection must yield even when it cannot fill the target buffer.
    let blocks = 0;
    while (blocks++ < 3 && generation === this.generation && this.state === 'playing' && this.nextTime-this.context.currentTime < 0.25) {
      const response = await this.wire('read', {stream_id:this.id,frames:4096});
      const metadata = JSON.parse(response.headers.get('X-DAW-Block'));
      const bytes = await response.arrayBuffer();
      if (generation !== this.generation) return;
      if (metadata.sample_rate !== this.rate || bytes.byteLength !== 4096*8) throw new Error('Invalid PCM stream block.');
      const buffer = this.context.createBuffer(2,4096,this.rate), view = new DataView(bytes);
      for (let ch=0;ch<2;ch++) { const output=buffer.getChannelData(ch); for(let i=0;i<4096;i++) output[i]=view.getFloat32((i*2+ch)*4,true); }
      if (this.nextTime < this.context.currentTime) { this.underruns++; this.nextTime=this.context.currentTime+0.02; }
      const source=this.context.createBufferSource(); source.buffer=buffer; source.connect(this.master);
      this.sources.add(source); source.onended=()=>{source.disconnect();this.sources.delete(source);};
      this.marks.push({time:this.nextTime,start:metadata.start_frame,frames:4096});
      while(this.marks.length>1 && this.marks[1].time<=this.context.currentTime) this.marks.shift();
      source.start(this.nextTime); this.nextTime += 4096/this.rate;
    }
  }
  clearQueue() { for(const source of this.sources) {source.onended=null;source.stop();source.disconnect();} this.sources.clear();this.marks=[]; }
  async control(action,params={}) {
    if(action==='volume') {this.volume=params.volume;if(this.master)this.master.gain.setTargetAtTime(this.volume,this.context.currentTime,0.01);return this.snapshot();}
    if(action==='status') {
      if(this.state==='playing' && this.context?.state !== 'running') {await this.fail(new Error('Browser audio was suspended. Stop and restart playback.'));return this.snapshot();}
      if(this.id) {
        try {const remote=await this.wire('status',{stream_id:this.id});if(remote.state==='stopped'||remote.stream_id!==this.id) {await this.fail(new Error('Browser playback was stopped or replaced on the server.'));}}
        catch(error) {if(!error.message.includes('Browser stream expired or belongs to another playback'))throw error;await this.fail(error);}
      }
      return this.snapshot();
    }
    if(action==='stop') {
      const snapshot=this.snapshot(), id=this.id;
      this.generation++;this.state='stopped';clearTimeout(this.timer);this.clearQueue();this.id=null;
      if(this.context) {const context=this.context;this.context=null;await context.close();}
      if(id) await this.wire('stop',{stream_id:id});
      return {...snapshot,state:'stopped'};
    }
    if(!this.id) throw new Error('Start browser playback first.');
    if(action==='pause') {
      this.state='paused';clearTimeout(this.timer);await this.context.suspend();await this.pumping;
      await this.wire('pause',{stream_id:this.id});return this.snapshot();
    }
    if(action==='resume') {await this.wire('resume',{stream_id:this.id});await this.context.resume();this.state='playing';this.run();return this.snapshot();}
    if(action==='seek'||action==='loop') {
      const state=this.state;this.state='paused';clearTimeout(this.timer);await this.pumping;
      const frame=this.snapshot().timeline_frame;
      // Discard queued audio and restart from the actual listening position.
      try {
        if(action==='loop') {await this.wire('loop',{stream_id:this.id,...params});this.loop=params.region;await this.wire('seek',{stream_id:this.id,frame});}
        else await this.wire('seek',{stream_id:this.id,...params});
      } catch(error) {await this.fail(error);throw error;}
      this.clearQueue();this.lastFrame=action==='seek'?params.frame:frame;this.nextTime=this.context.currentTime+0.12;
      this.state=state;if(state==='playing')this.run();return this.snapshot();
    }
    throw new Error('Unknown browser playback action.');
  }
  async fail(error) {
    try {await this.control('stop');} catch (_) { /* local output is already stopped */ }
    this.state='error';this.onError(error);
  }
}
if(typeof module!=='undefined')module.exports=BrowserStream;

/* Local, bounded standalone audio takes; upload occurs only on Use take. */
class AudioCapture {
  constructor({onStatus=()=>{},onReady=()=>{},getUserMedia=c=>navigator.mediaDevices.getUserMedia(c),createContext=rate=>new AudioContext({sampleRate:rate}),Recorder=MediaRecorder}={}) {
    Object.assign(this,{onStatus,onReady,getUserMedia,createContext,Recorder});this.state='stopped';this.generation=0;this.take=null;
  }
  async start(rate,monitor=false) {
    if(this.state!=='stopped'||this.take)throw new Error('Use or discard the current audio take first.');
    const generation=++this.generation;this.state='starting';this.chunks=[];this.bytes=0;this.rate=rate;
    try {
      this.context=this.createContext(rate);await this.context.resume();
      const stream=await this.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
      if(generation!==this.generation){stream.getTracks().forEach(t=>t.stop());return;}
      this.stream=stream;
      if(monitor){this.source=this.context.createMediaStreamSource(stream);this.source.connect(this.context.destination);}
      this.recorder=new this.Recorder(stream);
      this.recorder.ondataavailable=event=>{if(generation!==this.generation||!event.data.size)return;this.bytes+=event.data.size;if(this.bytes>16*1024*1024){this.error=new Error('Audio take exceeded the capture limit.');this.stop();}else this.chunks.push(event.data);};
      this.recorder.onerror=event=>{this.error=new Error(event.error?.message||'Audio capture failed.');this.stop();};
      this.recorder.onstop=()=>{void this.finish(generation);};
      this.recorder.start(1000);this.state='recording';this.onStatus('Recording locally. Stop audio creates a take; Use take uploads it at Insert at.');
      this.timer=setTimeout(()=>this.stop(),120000);
    }catch(error){this.release();this.state='stopped';throw error;}
  }
  stop(){clearTimeout(this.timer);if(this.recorder?.state==='recording'){this.state='finishing';this.recorder.stop();}else if(this.state==='starting'){this.generation++;this.release();this.state='stopped';}}
  release(){this.source?.disconnect();this.source=null;this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;if(this.context){void this.context.close();this.context=null;}}
  async finish(generation){
    try{
      this.release();if(this.error)throw this.error;
      const bytes=await new Blob(this.chunks,{type:this.recorder.mimeType}).arrayBuffer();
      const context=new OfflineAudioContext(1,1,this.rate), decoded=await context.decodeAudioData(bytes);
      const frames=Math.min(Math.ceil(decoded.duration*this.rate),this.rate*120);
      if(frames<1)throw new Error('No audio was captured.');
      const resampler=new OfflineAudioContext(1,frames,this.rate),source=resampler.createBufferSource();source.buffer=decoded;source.connect(resampler.destination);source.start();
      const rendered=await resampler.startRendering();
      if(generation!==this.generation)return;
      this.take=new File([AudioCapture.wav(rendered.getChannelData(0),this.rate)],'recorded-audio.wav',{type:'audio/wav'});
      this.onStatus(`Audio take ready · ${(frames/this.rate).toFixed(1)} seconds. Use take uploads it; Download keeps a local copy.`);
    }catch(error){if(generation===this.generation)this.onStatus(`Recording failed: ${error.message}`);}
    finally{if(generation===this.generation){this.state='stopped';this.chunks=[];this.error=null;this.onReady();}}
  }
  discard(){this.generation++;clearTimeout(this.timer);if(this.recorder?.state==='recording')this.recorder.stop();this.release();this.take=null;this.chunks=[];this.state='stopped';this.error=null;this.onReady();}
  static wav(samples,rate){
    const bytes=new ArrayBuffer(44+samples.length*2),v=new DataView(bytes),text=(at,s)=>{for(let i=0;i<s.length;i++)v.setUint8(at+i,s.charCodeAt(i));};
    text(0,'RIFF');v.setUint32(4,bytes.byteLength-8,true);text(8,'WAVE');text(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,rate,true);v.setUint32(28,rate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,samples.length*2,true);
    for(let i=0;i<samples.length;i++){const x=Math.max(-1,Math.min(1,samples[i]));v.setInt16(44+i*2,Math.round(x*(x<0?32768:32767)),true);}return bytes;
  }
}
if(typeof module!=='undefined')module.exports=AudioCapture;

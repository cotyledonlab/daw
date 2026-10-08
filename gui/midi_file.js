/* Bounded Standard MIDI File note reader. No playback or session side effects. */
(function(root) {
  'use strict';
  const limits = {bytes:1024*1024, events:65536, notes:16384, tracks:64};
  function parse(input) {
    const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
    if (!bytes.length || bytes.length > limits.bytes) throw Error('MIDI files must be nonempty and at most 1 MiB.');
    const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
    let p=0, bound=bytes.length, events=0, noteCount=0, ignored=0;
    const need=n=>{if(n<0 || p+n>bound) throw Error('Truncated MIDI file.');};
    const byte=()=>{need(1);return bytes[p++];};
    const u16=()=>{need(2);const v=view.getUint16(p);p+=2;return v;};
    const u32=()=>{need(4);const v=view.getUint32(p);p+=4;return v;};
    const tag=()=>String.fromCharCode(byte(),byte(),byte(),byte());
    const vlq=()=>{let v=0;for(let i=0;i<4;i++){const b=byte();v=v*128+(b&127);if(!(b&128))return v;}throw Error('MIDI variable-length value exceeds four bytes.');};
    if(tag()!=='MThd' || u32()!==6) throw Error('Choose a standard MIDI file.');
    const format=u16(), tracks=u16(), division=u16();
    if(![0,1].includes(format) || !tracks || tracks>limits.tracks || (format===0&&tracks!==1)) throw Error('Import supports MIDI format 0/1 with at most 64 file tracks.');
    if(!division || division&0x8000) throw Error('Import requires beat-based MIDI timing (PPQ), not SMPTE.');
    const groups=[];
    for(let track=0;track<tracks;track++) {
      bound=bytes.length;
      if(tag()!=='MTrk') throw Error('Missing MIDI track chunk.');
      const size=u32();need(size);bound=p+size;
      let tick=0,running=0,ended=false;
      const channels=new Map(), held=new Map();
      while(p<bound) {
        if(++events>limits.events) throw Error('MIDI event limit exceeded.');
        tick+=vlq();if(!Number.isSafeInteger(tick)) throw Error('MIDI timing exceeds the safe range.');
        let status=byte(), first=null;
        if(status<128){if(!running)throw Error('MIDI running status has no channel message.');first=status;status=running;}
        else running=status<240?status:0;
        if(status===255) {
          const type=byte(),length=vlq();need(length);
          if(type===47){if(length || p!==bound)throw Error('Invalid MIDI end-of-track event.');ended=true;break;}
          p+=length;continue;
        }
        if(status===240 || status===247){const length=vlq();need(length);p+=length;ignored++;continue;}
        if(status<128 || status>=240)throw Error('Unsupported MIDI event status.');
        const kind=status>>4,channel=status&15;
        const a=first??byte(), b=[12,13].includes(kind)?null:byte();
        if(a>127 || (b!==null&&b>127))throw Error('Invalid MIDI channel data.');
        if(kind!==8&&kind!==9){ignored++;continue;}
        const key=channel*128+a;
        if(kind===9&&b>0) {
          if(held.has(key))throw Error('Overlapping MIDI note-ons of the same channel and pitch are ambiguous.');
          if(++noteCount>limits.notes)throw Error('MIDI note limit exceeded.');
          held.set(key,{pitch:a,velocity:b,start:tick});
        } else {
          const note=held.get(key);if(!note)throw Error('MIDI note-off has no matching note-on.');
          held.delete(key);if(tick<=note.start)throw Error('MIDI notes require positive gates.');
          if(!channels.has(channel))channels.set(channel,[]);
          channels.get(channel).push({...note,end:tick});
        }
      }
      if(!ended || held.size)throw Error('MIDI track is missing an end marker or has unfinished notes.');
      for(const [channel,notes] of channels)groups.push({track,channel,notes,end:tick});
    }
    if(p!==bytes.length)throw Error('Unexpected data after MIDI tracks.');
    if(!groups.length)throw Error('The MIDI file contains no notes.');
    if(groups.length>limits.tracks)throw Error('MIDI import would create more than 64 lanes.');
    return {division,groups,ignored};
  }
  const api={parse,limits};
  if(typeof module!=='undefined')module.exports=api;else root.MidiFile=api;
})(typeof window==='undefined'?globalThis:window);

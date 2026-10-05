const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('./editor.js');
const sample = overrides => ({kind:'audio',id:'audio-clip',start_frame:1234,length_frames:7777,source_path:'assets/recording.wav',source_offset_frames:99,gain:0.67,...overrides});
function session(version = 3) {
  let next = E.createArrangementSession(); next.schema_version = version;
  next = E.addAudioTrack(next,'audio');
  return E.addAudioClip(next,0,sample());
}

test('audio arrangements support represented formats alongside gain effects and built-in note tracks', () => {
  for (const version of [2,3,4,9]) {
    let next = session(version);
    assert.equal(E.supported(next),true); assert.equal(E.arrangement(next),true); assert.equal(E.validate(next),null);
    next = E.addNoteTrack(next,'notes');
    next = E.addNoteClip(next,1,{id:'note-clip',start_frame:0,length_frames:96000,notes:[{id:'note',start_frame:0,duration_frames:24000,frequency_hz:443.12345,velocity:0.75}]});
    assert.equal(E.validate(next),null);
    const roundtrip = JSON.parse(JSON.stringify(next)); assert.deepEqual(roundtrip,next);
    if (version !== 2) assert.equal(E.validate(E.addEffect(next,0,{kind:'gain',gain:0.8,bypass:false},'trim')),null);
  }
  const legacy = {schema_version:1,sample_rate:48000,tracks:[]};
  assert.equal(E.addAudioTrack(legacy,'audio').schema_version,3);
  assert.equal(legacy.schema_version,1);
});

test('audio gain/offset patches preserve exact untouched frames, duplicates clone independently, and clip operations are generic', () => {
  const original = session();
  const updated = E.editAudioClip(original,0,'audio-clip',{gain:0.25});
  assert.deepEqual(updated.tracks[0].clips[0],sample({gain:0.25}));
  assert.deepEqual(original.tracks[0].clips[0],sample());
  const offset = E.editAudioClip(updated,0,'audio-clip',{source_offset_frames:123,length_frames:6666});
  assert.equal(offset.tracks[0].clips[0].start_frame,1234);
  assert.equal(updated.tracks[0].clips[0].source_offset_frames,99);
  const duplicate = E.duplicateClip(original,0,'audio-clip','copy',9011);
  duplicate.tracks[0].clips[1].gain = 0;
  assert.equal(duplicate.tracks[0].clips[0].gain,0.67);
  assert.equal(original.tracks[0].clips.length,1);
  assert.equal(E.moveClip(original,0,'audio-clip',5678).tracks[0].clips[0].start_frame,5678);
  assert.equal(E.resizeClip(original,0,'audio-clip',123).tracks[0].clips[0].length_frames,123);
  assert.equal(E.deleteClip(original,0,'audio-clip').tracks[0].clips.length,0);
  assert.throws(() => E.addNote(original,0,'audio-clip',{}),/sequenced/);
});

test('audio syntactic path/range/gain rejection is transactional; actual file bounds remain Rust validation', () => {
  const original = session();
  for (const source_path of ['','/abs.wav','../file.wav','a/../b.wav','a/./b.wav','a//b.wav','C:file.wav','a\\b.wav','a\0b.wav','a/'.repeat(3000)]) {
    const candidate = structuredClone(original); candidate.tracks[0].clips[0].source_path = source_path;
    assert.match(E.validate(candidate),/relative paths/);
  }
  for (const patch of [{source_offset_frames:-1},{source_offset_frames:0.5},{source_offset_frames:Number.MAX_SAFE_INTEGER},{length_frames:0},{length_frames:Infinity},{gain:-0.01},{gain:1.01},{gain:NaN},{source_path:'another.wav'}]) {
    assert.throws(() => E.editAudioClip(original,0,'audio-clip',patch));
    assert.deepEqual(original.tracks[0].clips[0],sample());
  }
  const outOfAssetBounds = E.editAudioClip(original,0,'audio-clip',{source_offset_frames:4800000});
  assert.equal(E.validate(outOfAssetBounds),null);
  const invalidMode = structuredClone(original); invalidMode.tracks[0].mode='continuous'; invalidMode.tracks[0].clips=[];
  assert.equal(E.supported(invalidMode),false);
  const invalidEffect = structuredClone(original); invalidEffect.tracks[0].effects=[{kind:'vst3',id:'foreign'}];
  assert.equal(E.supported(invalidEffect),false);
});

test('audio clips share the 64 simultaneous voice cap with notes and continuous tracks, with half-open boundaries', () => {
  const source = session();
  source.tracks[0].clips = Array.from({length:64},(_,i)=>sample({id:`clip-${i}`,start_frame:0,length_frames:96000}));
  assert.equal(E.validate(source),null);
  source.tracks.push({id:'tone',mode:'continuous',device:{kind:'sine',frequency_hz:440,gain:0.1},clips:[],effects:[]});
  assert.match(E.validate(source),/64 voices/);
  source.tracks.pop();
  source.tracks[0].clips.push(sample({id:'next',start_frame:96000,length_frames:96000}));
  assert.equal(E.validate(source),null);
});

test('audio fades roundtrip across represented formats and preserve exact timing, source and gain', () => {
  for (const version of [2,3,4,9]) {
    const original=session(version);
    const faded=E.editAudioClip(original,0,'audio-clip',{fade_in_frames:17,fade_out_frames:29});
    assert.deepEqual(faded.tracks[0].clips[0],sample({fade_in_frames:17,fade_out_frames:29}));
    assert.deepEqual(original.tracks[0].clips[0],sample());
    const gain=E.editAudioClip(faded,0,'audio-clip',{gain:0.25});
    assert.deepEqual(gain.tracks[0].clips[0],sample({gain:0.25,fade_in_frames:17,fade_out_frames:29}));
    assert.deepEqual(JSON.parse(JSON.stringify(gain)),gain); assert.equal(E.validate(gain),null);
    const duplicated=E.duplicateClip(faded,0,'audio-clip','copy',9011);
    assert.equal(duplicated.tracks[0].clips[1].fade_in_frames,17);
    assert.equal(duplicated.tracks[0].clips[1].fade_out_frames,29);
    assert.equal(E.validate(E.editAudioClip(faded,0,'audio-clip',{fade_in_frames:0,fade_out_frames:7777})),null);
  }
});
test('audio fade invalid edits and shortening preserve project and undo history transactionally', () => {
  const History=require('./history.js'), history=new History(), original=session();
  const accepted=E.editAudioClip(original,0,'audio-clip',{fade_in_frames:4000,fade_out_frames:2000});
  history.commit(original,accepted);
  for (const patch of [{fade_in_frames:-1},{fade_out_frames:0.5},{fade_in_frames:NaN},{fade_out_frames:Infinity},{fade_in_frames:Number.MAX_SAFE_INTEGER+1},{fade_in_frames:7778},{fade_out_frames:4000},{length_frames:5999}]) {
    assert.throws(()=>E.editAudioClip(accepted,0,'audio-clip',patch));
    assert.deepEqual(history.undoTarget(accepted),original); assert.equal(history.undo.length,1);
  }
  assert.throws(()=>E.resizeClip(accepted,0,'audio-clip',5999));
  const resized=E.resizeClip(accepted,0,'audio-clip',6000);
  assert.equal(resized.tracks[0].clips[0].fade_in_frames,4000); assert.equal(resized.tracks[0].clips[0].fade_out_frames,2000);
  const short=E.editAudioClip(accepted,0,'audio-clip',{length_frames:100,fade_in_frames:1,fade_out_frames:99});
  assert.equal(E.validate(short),null);
  assert.equal(history.acceptUndo(accepted),true); assert.deepEqual(history.redoTarget(original),accepted); assert.equal(history.acceptRedo(original),true);
  for (const value of [null,'1',-1,0.5,7778]) {
    const invalid=structuredClone(original); invalid.tracks[0].clips[0].fade_in_frames=value;
    assert.ok(E.validate(invalid));
  }
});

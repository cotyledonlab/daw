/* Studio edits reuse the editor's validation and immutable session helpers. */
(function(root) {
  'use strict';
  const own = (object, keys) => object && typeof object === 'object' && !Array.isArray(object) && Object.keys(object).every(key => keys.includes(key));
  function patch(value, keys) {
    if (!own(value, keys) || !Object.keys(value).length) throw Error('Invalid studio patch.');
    return value;
  }
  function apply(session, parts, scope, contract, E) {
    let next = structuredClone(session), count = 0;
    if (!Array.isArray(parts) || parts.length > 5) throw Error('Invalid studio response.');
    for (const part of parts) {
      if (!['producer','engineer','musician'].includes(part.role) || !Array.isArray(part.operations)) throw Error('Unknown studio role.');
      for (const op of part.operations) {
        if (++count > 128) throw Error('Studio batch is too large.');
        const spec = contract[op?.op];
        if (!spec || !spec.roles.includes(part.role) || !own(op, ['op', ...Object.keys(spec.fields)]) || Object.keys(op).length !== Object.keys(spec.fields).length + 1) throw Error('Agent exceeded its role or returned an unknown operation.');
        if (op.op === 'command') throw Error('Commands cannot be applied as session edits.');
        if (scope && (op.track_id !== scope.track_id || (scope.clip_id && op.clip_id !== scope.clip_id))) throw Error('Agent exceeded the selected scope.');
        const i = next.tracks.findIndex(track => track.id === op.track_id);
        if ('track_id' in op && i < 0) throw Error('Agent referred to a missing track.');
        switch (op.op) {
          case 'addTrack':
            if (!['sine','synth','drumkit'].includes(op.kind)) throw Error('Choose a built-in note instrument.');
            next = E.addNoteTrack(next, op.id, op.kind); break;
          case 'deleteTrack': next.tracks.splice(i,1); break;
          case 'addClip':
            if (!own(op.clip,['id','start_frame','length_frames','notes']) || !Array.isArray(op.clip.notes) || op.clip.notes.length) throw Error('Add an empty clip, then its notes.');
            next = E.addNoteClip(next,i,op.clip); break;
          case 'moveClip': next = E.moveClip(next,i,op.clip_id,op.start_frame); break;
          case 'resizeClip': next = E.resizeClip(next,i,op.clip_id,op.length_frames); break;
          case 'duplicateClip': next = E.duplicateClip(next,i,op.clip_id,op.id,op.start_frame); break;
          case 'deleteClip': next = E.deleteClip(next,i,op.clip_id); break;
          case 'addNote':
            if (!own(op.note,['id','start_frame','duration_frames','frequency_hz','velocity'])) throw Error('Invalid studio note.');
            next = E.addNote(next,i,op.clip_id,op.note); break;
          case 'editNote': next = E.editNote(next,i,op.clip_id,op.note_id,patch(op.patch,['start_frame','duration_frames','frequency_hz','velocity'])); break;
          case 'deleteNote': next = E.deleteNote(next,i,op.clip_id,op.note_id); break;
          case 'transpose': {
            if (!Number.isFinite(op.semitones) || Math.abs(op.semitones) > 48) throw Error('Transpose must be within 48 semitones.');
            const clip = next.tracks[i].clips?.find(c=>c.id === op.clip_id);
            if (clip?.kind !== 'notes' || next.tracks[i].device.kind === 'drumkit') throw Error('Transpose needs a pitched note clip.');
            for (const note of clip.notes) next = E.editNote(next,i,clip.id,note.id,{frequency_hz: note.frequency_hz * 2 ** (op.semitones / 12)});
            break;
          }
          case 'setNotes': {
            const clip=next.tracks[i].clips?.find(c=>c.id===op.clip_id);
            if(clip?.kind!=='notes'||!Array.isArray(op.notes)||op.notes.length>128||op.notes.some(n=>!own(n,['id','start_frame','duration_frames','frequency_hz','velocity'])||Object.keys(n).length!==5)) throw Error('Rewrite needs an existing note clip and at most 128 complete notes.');
            clip.notes=structuredClone(op.notes);
            break;
          }
          case 'copyNotes': {
            const ids=op.target_clip_ids, source=next.tracks[i].clips?.find(c=>c.id===op.clip_id);
            if(!Array.isArray(ids)||ids.length<1||ids.length>64||ids.some(id=>typeof id!=='string')||new Set(ids).size!==ids.length||ids.includes(op.clip_id)||source?.kind!=='notes') throw Error('Copy notes needs distinct existing note clips.');
            const base=session.tracks.find(t=>t.id===op.track_id), original=base?.clips?.find(c=>c.id===op.clip_id);
            const pattern=clip=>JSON.stringify(clip.notes.map(n=>[n.start_frame,n.duration_frames,n.frequency_hz,n.velocity]));
            if(original?.kind!=='notes') throw Error('Copy notes needs an existing source pattern.');
            for(const id of ids){
              if(scope?.clip_id&&id!==scope.clip_id) throw Error('Agent exceeded the selected clip scope.');
              const target=next.tracks[i].clips?.find(c=>c.id===id), old=base.clips.find(c=>c.id===id);
              if(target?.kind!=='notes'||old?.kind!=='notes'||pattern(old)!==pattern(original)) throw Error('Copy notes only propagates originally identical patterns.');
              if(JSON.stringify(target.notes)!==JSON.stringify(old.notes)) throw Error('Copy notes would discard earlier target edits.');
              target.notes=structuredClone(source.notes);
            }
            break;
          }
          case 'mixer': next = E.editMixer(next,i,op.patch); break;
          case 'gainDb':
            if (!Number.isFinite(op.db) || Math.abs(op.db) > 60) throw Error('Relative gain must be within 60 dB.');
            next = E.editMixer(next,i,{gain:(next.tracks[i].mixer?.gain ?? 1) * 10 ** (op.db/20)}); break;
          case 'device': {
            const device = next.tracks[i].device;
            if (device.kind === 'pd_instrument') next = E.editPdInstrument(next,i,op.patch);
            else {
              const keys = {sine:['frequency_hz','gain'],synth:['waveform','gain','attack_ms','release_ms','cutoff_hz'],drumkit:['gain'],audio:['gain']}[device.kind];
              if (!keys) throw Error('Scripted/runtime program edits use the scripting interface.');
              Object.assign(device,structuredClone(patch(op.patch,keys)));
            }
            break;
          }
          case 'audioClip': next = E.editAudioClip(next,i,op.clip_id,op.patch); break;
          case 'addEffect':
            if (!['gain','lowpass','delay'].includes(op.effect?.kind)) throw Error('Choose a built-in effect.');
            next = E.addEffect(next,i,op.effect,op.id); break;
          case 'effect': next = E.editEffect(next,i,op.effect_id,op.patch); break;
          case 'removeEffect': next = E.removeEffect(next,i,op.effect_id); break;
          case 'addGainPoint': next = E.addGainAutomationPoint(next,i,op.effect_id,op.point); break;
          case 'editGainPoint': next = E.editGainAutomationPoint(next,i,op.effect_id,op.frame,op.point); break;
          case 'deleteGainPoint': next = E.deleteGainAutomationPoint(next,i,op.effect_id,op.frame); break;
          case 'tempo':
            if (next.schema_version === 1) { next.schema_version = 3; next.tracks.forEach(track=>Object.assign(track,{mode:'continuous',clips:[],effects:[]})); }
            next.tempo_milli_bpm = op.tempo_milli_bpm; break;
          default: throw Error('Unknown studio edit.');
        }
        const error = E.validate(next); if (error) throw Error(error);
      }
    }
    return next;
  }
  const api = {apply};
  if (typeof module !== 'undefined') module.exports = api;
  else root.StudioActions = api;
})(typeof window === 'undefined' ? globalThis : window);

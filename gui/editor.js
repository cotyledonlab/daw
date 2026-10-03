/* Browser editing scope; Rust remains authoritative for complete session validation. */
(function (root) {
  'use strict';
  function utf8Length(value) {
    if (typeof value !== 'string') return -1;
    for (let i = 0; i < value.length; i += 1) {
      const code = value.charCodeAt(i);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(i + 1);
        if (!(next >= 0xdc00 && next <= 0xdfff)) return -1;
        i += 1;
      } else if (code >= 0xdc00 && code <= 0xdfff) return -1;
    }
    return new TextEncoder().encode(value).length;
  }
  const validText = (value, maxBytes, allowEmpty = false) => {
    const bytes = utf8Length(value);
    return bytes >= (allowEmpty ? 0 : 1) && bytes <= maxBytes && !value.includes('\0');
  };
  const arrangement = session => [2, 3].includes(session?.schema_version) ||
    (session?.schema_version === 4 && session.tracks?.some(track => track?.mode === 'sequenced'));
  const supported = session => Boolean(session && Array.isArray(session.tracks) &&
    (session.schema_version === 1 || ([2, 3, 4, 6, 7].includes(session.schema_version) && session.tracks.every(track =>
      track && ([6, 7].includes(session.schema_version) ? ['sine', 'supercollider', ...(session.schema_version === 7 ? ['csound'] : [])].includes(track.device?.kind) : track.device?.kind === 'sine') &&
      Array.isArray(track.clips) &&
      ([6, 7].includes(session.schema_version) ? track.mode === 'continuous' && track.clips.length === 0 :
        ['continuous', 'sequenced'].includes(track.mode) && (track.mode !== 'continuous' || track.clips.length === 0) && track.clips.every(clip => clip?.kind === 'notes' && Array.isArray(clip.notes))) &&
      (![6, 7].includes(session.schema_version) || Array.isArray(track.effects)) &&
      ((!arrangement(session) && ![6, 7].includes(session.schema_version)) || ((track.effects === undefined || Array.isArray(track.effects)) && (track.effects || []).every(effect => effect?.kind === 'gain')))))));
  const sources = session => (session?.tracks || []).filter(track => ['supercollider', 'csound'].includes(track.device?.kind));
  function sourceControlsEditable(control, metadata, kind = 'supercollider') {
    if (kind === 'csound') return Boolean(Number.isFinite(control?.value) && Array.isArray(control.points) && control.points.length === 0);
    const native = metadata?.controls?.find(item => item.name === control.name);
    return Boolean(native && native.initialization_rate === false && native.default_values?.length === control.values?.length && Array.isArray(control.points) && control.points.length === 0);
  }
  const plugins = session => (session?.tracks || []).flatMap(track => track.effects || []).filter(effect => effect.kind === 'vst3');
  function validate(session) {
    if (!supported(session)) return 'This editor supports continuous sine sessions in formats 1 and 4, sine/SuperCollider sessions with gain effects in format 6, and sine/SuperCollider/Csound sessions with gain effects in format 7. Built-in sine note arrangements in formats 2, 3 and 4 are also supported; other clips require scripts.';
    if (!Number.isInteger(session.sample_rate) || session.sample_rate < 8000 || session.sample_rate > 192000) return 'Sample rate must be between 8,000 and 192,000 Hz.';
    if (session.tracks.length > 64) return 'A session can contain up to 64 tracks.';
    if (arrangement(session)) { const error = validateArrangement(session); if (error) return error; }
    const ids = new Set();
    for (const track of session.tracks) {
      if (!track || !validText(track.id, 128) || ids.has(track.id)) return 'Track IDs must be unique and no longer than 128 UTF-8 bytes.';
      ids.add(track.id);
      if (track.device?.kind === 'supercollider') {
        const source = track.device;
        if (typeof source.synth_name !== 'string' || !source.synth_name.length || typeof source.synthdef_hex !== 'string' || !source.synthdef_hex.length || source.synthdef_hex.length > 122880 || source.synthdef_hex.length % 2 || !/^[0-9a-f]+$/i.test(source.synthdef_hex)) return 'SuperCollider sources require a named, bounded hexadecimal program.';
        if (!Number.isInteger(source.duration_frames) || source.duration_frames <= 0) return 'Source duration must be a positive frame count.';
        if (!Number.isFinite(source.gain) || source.gain < 0 || source.gain > 1) return 'Track gain must be between 0 and 1.';
        if (!Array.isArray(source.controls) || source.controls.some(control => typeof control.name !== 'string' || !control.name.length || !Array.isArray(control.values) || !control.values.length || control.values.length > 256 || control.values.some(value => !Number.isFinite(value) || !Number.isFinite(Math.fround(value))) || !Array.isArray(control.points))) return 'Saved controls require named finite float32 arrays and automation points.';
        continue;
      }
      if (track.device?.kind === 'csound') {
        const source = track.device;
        const minimumDuration = Math.ceil(session.sample_rate / 1000);
        if (!validText(source.program, 61440) || !Number.isInteger(source.duration_frames) || source.duration_frames < minimumDuration || source.duration_frames > session.sample_rate * 10) return 'Csound sources require bounded UTF-8 program text and a duration from 1 ms through ten seconds.';
        if (!Number.isFinite(source.gain) || source.gain < 0 || source.gain > 1) return 'Track gain must be between 0 and 1.';
        if (!Array.isArray(source.controls) || source.controls.length > 64) return 'Csound sources can contain up to 64 saved controls.';
        const controlNames = new Set();
        for (const control of source.controls) {
          if (!control || !validText(control.name, 128) || controlNames.has(control.name) || !Number.isFinite(control.value) || !Array.isArray(control.points)) return 'Csound controls require unique bounded names, finite scalar values and point arrays.';
          controlNames.add(control.name);
        }
        continue;
      }
      if (track.device?.kind !== 'sine') return 'Only continuous sine tracks can be edited here.';
      if (!Number.isFinite(track.device.frequency_hz) || track.device.frequency_hz <= 0 || track.device.frequency_hz >= session.sample_rate / 2) return 'Frequency must be positive and below the session Nyquist limit.';
      if (!Number.isFinite(track.device.gain) || track.device.gain < 0 || track.device.gain > 1) return 'Track gain must be between 0 and 1.';
    }
    if (plugins(session).length && session.sample_rate !== 48000) return 'VST3 effects require a 48 kHz session.';
    return null;
  }
  const frame = value => Number.isSafeInteger(value) && value >= 0;
  const range = (start, length) => frame(start) && frame(length) && length > 0 && Number.isSafeInteger(start + length);
  function validateArrangement(session) {
    if (!Number.isInteger(session.tempo_milli_bpm) || session.tempo_milli_bpm < 20000 || session.tempo_milli_bpm > 300000) return 'Tempo must be between 20 and 300 BPM.';
    let clips = 0, notes = 0, continuous = 0;
    const events = [];
    for (const track of session.tracks) {
      if (track.mode === 'continuous') continuous += 1;
      if (session.schema_version === 2 && (track.effects !== undefined || track.automation !== undefined)) return 'Format 2 does not accept effects or automation.';
      if ([3, 4].includes(session.schema_version) && !Array.isArray(track.effects)) return 'Tracks require an effects array.';
      if ((track.effects || []).length > 16) return 'A track can contain up to 16 effects.';
      const effectIds = new Set();
      for (const effect of track.effects || []) {
        if (!validText(effect.id, 128) || effectIds.has(effect.id) || !Number.isFinite(effect.gain) || effect.gain < 0 || effect.gain > 4 || typeof effect.bypass !== 'boolean') return 'Gain effects require unique IDs, a gain from 0 to 4 and a bypass flag.';
        effectIds.add(effect.id);
      }
      const clipIds = new Set();
      clips += track.clips.length;
      for (const clip of track.clips) {
        if (!validText(clip.id, 128) || clipIds.has(clip.id)) return 'Clip IDs must be unique within a track and no longer than 128 UTF-8 bytes.';
        clipIds.add(clip.id);
        if (!range(clip.start_frame, clip.length_frames)) return 'Clips require a nonnegative safe frame position and positive length.';
        const noteIds = new Set();
        notes += clip.notes.length;
        for (const note of clip.notes) {
          if (!note || !validText(note.id, 128) || noteIds.has(note.id)) return 'Note IDs must be unique within a clip and no longer than 128 UTF-8 bytes.';
          noteIds.add(note.id);
          if (!range(note.start_frame, note.duration_frames) || note.start_frame + note.duration_frames > clip.length_frames) return 'Note gates must have positive duration and end within their clip.';
          if (!Number.isFinite(note.frequency_hz) || note.frequency_hz <= 0 || note.frequency_hz >= session.sample_rate / 2) return 'Note frequency must be positive and below Nyquist.';
          if (!Number.isFinite(note.velocity) || note.velocity < 0 || note.velocity > 1) return 'Note velocity must be between 0 and 1.';
          const off = clip.start_frame + note.start_frame + note.duration_frames;
          const releaseEnd = off + Math.floor((session.sample_rate + 100) / 200);
          if (!Number.isSafeInteger(releaseEnd)) return 'Note release exceeds the safe frame range.';
          events.push([clip.start_frame + note.start_frame, 1], [Math.min(releaseEnd, clip.start_frame + clip.length_frames), -1]);
        }
      }
    }
    if (clips > 1024 || notes > 16384) return 'A session can contain up to 1024 clips and 16384 notes.';
    events.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let voices = continuous;
    for (const [, delta] of events) { voices += delta; if (voices > 64) return 'Polyphony exceeds 64 voices.'; }
    return null;
  }
  function midiToHz(midi) {
    if (!Number.isFinite(midi)) throw new Error('MIDI pitch must be finite.');
    const hz = 440 * 2 ** ((midi - 69) / 12);
    if (!Number.isFinite(hz) || hz <= 0) throw new Error('MIDI pitch is outside the frequency range.');
    return hz;
  }
  function hzToMidi(hz) {
    if (!Number.isFinite(hz) || hz <= 0) throw new Error('Frequency must be finite and positive.');
    return 69 + 12 * Math.log2(hz / 440);
  }
  function conversionBase(session) {
    if (!Number.isInteger(session.sample_rate) || session.sample_rate < 8000 || session.sample_rate > 192000 ||
        !Number.isInteger(session.tempo_milli_bpm) || session.tempo_milli_bpm < 20000 || session.tempo_milli_bpm > 300000) throw new Error('Valid sample rate and tempo are required.');
  }
  function roundedRatio(n, d) {
    const result = Number((2n * n + d) / (2n * d));
    if (!frame(result)) throw new Error('Conversion exceeds the safe frame range.');
    return result;
  }
  function ticksToFrames(session, ticks) {
    conversionBase(session); if (!frame(ticks)) throw new Error('Ticks must be nonnegative safe integers.');
    return roundedRatio(BigInt(ticks) * BigInt(session.sample_rate) * 60000n, 960n * BigInt(session.tempo_milli_bpm));
  }
  function framesToTicks(session, frames) {
    conversionBase(session); if (!frame(frames)) throw new Error('Frames must be nonnegative safe integers.');
    return roundedRatio(BigInt(frames) * 960n * BigInt(session.tempo_milli_bpm), BigInt(session.sample_rate) * 60000n);
  }
  function snapFrame(session, frames, gridTicks = 240) {
    if (!frame(gridTicks) || !gridTicks) throw new Error('Grid ticks must be positive safe integers.');
    return ticksToFrames(session, Math.round(framesToTicks(session, frames) / gridTicks) * gridTicks);
  }
  function createArrangementSession(sampleRate = 48000, tempoMilliBpm = 120000) {
    const next = {schema_version: 3, sample_rate: sampleRate, tempo_milli_bpm: tempoMilliBpm, tracks: []};
    const error = validate(next); if (error) throw new Error(error);
    return next;
  }
  function mutateArrangement(session, mutate) {
    const error = validate(session); if (error) throw new Error(error);
    if (![1, 2, 3, 4].includes(session.schema_version)) throw new Error('Note editing requires a built-in sine arrangement.');
    const next = structuredClone(session);
    if (next.schema_version === 1) {
      next.schema_version = 3; next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    }
    mutate(next);
    const after = validate(next); if (after) throw new Error(after);
    return next;
  }
  function noteTrack(next, index) {
    const track = next.tracks[index];
    if (!track || track.device?.kind !== 'sine' || track.mode !== 'sequenced') throw new Error('Select a sequenced sine track.');
    return track;
  }
  function noteClip(next, index, id) {
    const clip = noteTrack(next, index).clips.find(item => item.id === id);
    if (!clip) throw new Error('Select an existing note clip.');
    return clip;
  }
  function addNoteTrack(session, id) {
    return mutateArrangement(session, next => {
      const track = {id, mode: 'sequenced', device: {kind: 'sine', frequency_hz: 440, gain: 0.15}, clips: []};
      if (next.schema_version !== 2) track.effects = [];
      next.tracks.push(track);
    });
  }
  function addNoteClip(session, index, clip) {
    return mutateArrangement(session, next => noteTrack(next, index).clips.push({kind: 'notes', ...structuredClone(clip), notes: structuredClone(clip.notes || [])}));
  }
  function moveClip(session, index, id, startFrame) {
    return mutateArrangement(session, next => { noteClip(next, index, id).start_frame = startFrame; });
  }
  function resizeClip(session, index, id, lengthFrames) {
    return mutateArrangement(session, next => { noteClip(next, index, id).length_frames = lengthFrames; });
  }
  function duplicateClip(session, index, id, newId, startFrame) {
    return mutateArrangement(session, next => {
      const clip = structuredClone(noteClip(next, index, id)); clip.id = newId; clip.start_frame = startFrame;
      noteTrack(next, index).clips.push(clip);
    });
  }
  function deleteClip(session, index, id) {
    return mutateArrangement(session, next => {
      noteClip(next, index, id);
      const track = noteTrack(next, index); track.clips = track.clips.filter(clip => clip.id !== id);
    });
  }
  function addNote(session, index, clipId, note) {
    return mutateArrangement(session, next => noteClip(next, index, clipId).notes.push(structuredClone(note)));
  }
  function editNote(session, index, clipId, noteId, patch) {
    return mutateArrangement(session, next => {
      const note = noteClip(next, index, clipId).notes.find(item => item.id === noteId);
      if (!note) throw new Error('Select an existing note.');
      Object.assign(note, structuredClone(patch));
    });
  }
  function deleteNote(session, index, clipId, noteId) {
    return mutateArrangement(session, next => {
      const clip = noteClip(next, index, clipId);
      if (!clip.notes.some(note => note.id === noteId)) throw new Error('Select an existing note.');
      clip.notes = clip.notes.filter(note => note.id !== noteId);
    });
  }
  function createDemoSession() {
    let next = addNoteTrack(createArrangementSession(), 'lead');
    next = addNoteTrack(next, 'bass');
    // Eight two-bar phrases make sixteen bars in 4/4, with a four-chord progression.
    const chords = [[60, 64, 67], [57, 60, 64], [53, 57, 60], [55, 59, 62]];
    const bassRoots = [36, 33, 29, 31];
    const phraseFrames = ticksToFrames(next, 8 * 960);
    for (let phrase = 0; phrase < 8; phrase += 1) {
      const chord = chords[phrase % chords.length];
      for (let track = 0; track < 2; track += 1) {
        const notes = Array.from({length: 8}, (_, beat) => {
          const pitch = track ? bassRoots[phrase % bassRoots.length] + (beat % 4 === 3 ? 12 : 0) :
            chord[(beat + Math.floor(phrase / 4)) % chord.length] + (beat === 7 ? 12 : 0);
          return {id: `n${beat + 1}`, start_frame: ticksToFrames(next, beat * 960),
            duration_frames: ticksToFrames(next, track ? 720 : (beat % 2 ? 480 : 660)),
            frequency_hz: midiToHz(pitch), velocity: track ? 0.65 : (beat % 4 === 0 ? 0.85 : 0.7)};
        });
        next = addNoteClip(next, track, {id: phrase ? `phrase-${phrase + 1}` : 'phrase',
          start_frame: phrase * phraseFrames, length_frames: phraseFrames, notes});
      }
    }
    return next;
  }
  function addEffect(session, index, effect, id) {
    const error = validate(session); if (error) throw new Error(error);
    if (arrangement(session) && effect?.kind !== 'gain') throw new Error('Note arrangement sessions support gain effects only.');
    if ([6, 7].includes(session.schema_version) && effect?.kind !== 'gain') throw new Error('SuperCollider/Csound GUI sessions support gain effects only.');
    const next = structuredClone(session);
    if (next.schema_version === 1) {
      next.schema_version = 4; next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    }
    if (next.schema_version === 2) { next.schema_version = 3; next.tracks.forEach(track => { track.effects = []; }); }
    const track = next.tracks[index]; if (!track) throw new Error('Select an existing track.');
    if ((track.effects || []).length >= 16) throw new Error('A track can contain up to 16 effects.');
    if (effect.kind === 'vst3' && (next.sample_rate !== 48000 || plugins(next).length >= 8)) throw new Error('Use 48 kHz and at most eight VST3 effects per session.');
    track.effects ||= []; track.effects.push({...structuredClone(effect), id});
    if (arrangement(next)) { const errorAfter = validate(next); if (errorAfter) throw new Error(errorAfter); }
    return next;
  }
  function addCsound(session, program, id, durationFrames) {
    const error = validate(session); if (error) throw new Error(error);
    if (![1, 4, 6, 7].includes(session.schema_version)) throw new Error('Adding a Csound source requires a supported format 1, 4, 6 or 7 session.');
    if (!validText(program, 61440)) throw new Error('Csound program must be nonempty UTF-8 text no longer than 60 KiB and contain no NUL.');
    if (!validText(id, 128)) throw new Error('Track IDs must be unique and no longer than 128 UTF-8 bytes.');
    if (session.tracks.some(track => track.id === id)) throw new Error('Track IDs must be unique.');
    if (session.tracks.some(track => (track.effects || []).some(effect => effect?.kind !== 'gain'))) throw new Error('Csound GUI sessions support gain effects only.');
    const minimumDuration = Math.ceil(session.sample_rate / 1000);
    if (!Number.isInteger(durationFrames) || durationFrames < minimumDuration || durationFrames > session.sample_rate * 10) throw new Error('Csound source duration must be from 1 ms through ten seconds.');
    const next = structuredClone(session);
    if (next.schema_version === 1) {
      next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    }
    next.schema_version = 7;
    next.tracks.push({
      id, mode: 'continuous', clips: [], effects: [],
      device: { kind: 'csound', program, duration_frames: durationFrames, gain: 0.5, controls: [] },
    });
    const errorAfter = validate(next); if (errorAfter) throw new Error(errorAfter);
    return next;
  }
  function addCsoundControl(session, index, name, value) {
    const error = validate(session); if (error) throw new Error(error);
    if (session.schema_version !== 7) throw new Error('Csound controls require a format 7 session.');
    if (!validText(name, 128) || !Number.isFinite(value)) throw new Error('Csound controls require a bounded UTF-8 name and finite scalar value.');
    const source = session.tracks[index]?.device;
    if (source?.kind !== 'csound') throw new Error('Select a Csound source.');
    if (source.controls.length >= 64) throw new Error('A Csound source can contain up to 64 controls.');
    if (source.controls.some(control => control.name === name)) throw new Error('Csound control names must be unique.');
    const next = structuredClone(session);
    next.tracks[index].device.controls.push({ name, value, points: [] });
    return next;
  }
  function removeEffect(session, index, id) {
    const next = structuredClone(session); const track = next.tracks[index];
    track.effects = track.effects.filter(effect => effect.id !== id);
    if (track.automation) track.automation = track.automation.filter(lane => lane.effect_id !== id);
    return next;
  }
  const api = {arrangement, createArrangementSession, createDemoSession, addNoteTrack, addNoteClip, moveClip, resizeClip, duplicateClip, deleteClip, addNote, editNote, deleteNote, midiToHz, hzToMidi, ticksToFrames, framesToTicks, snapFrame, supported, plugins, sources, sourceControlsEditable, validate, addEffect, addCsound, addCsoundControl, removeEffect};
  if (typeof module !== 'undefined') module.exports = api;
  else root.SessionEditor = api;
})(typeof window === 'undefined' ? globalThis : window);

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
  const PdInstrument = typeof module !== 'undefined' ? require('./pd_instrument.js') : root.PdInstrument;
  const noteKinds = ['sine', 'drumkit', 'synth', 'pd_instrument'];
  const arrangement = session => [2, 3, 9, 10, 11, 12].includes(session?.schema_version) ||
    (session?.schema_version === 4 && session.tracks?.some(track => track?.mode === 'sequenced'));
  const supported = session => Boolean(session && Array.isArray(session.tracks) &&
    (session.schema_version === 1 || ([2, 3, 4, 6, 7, 9, 10, 11, 12].includes(session.schema_version) && session.tracks.every(track => {
      if (!track || !Array.isArray(track.clips)) return false;
      if ([6, 7].includes(session.schema_version)) return ['sine', 'supercollider', ...(session.schema_version === 7 ? ['csound'] : [])].includes(track.device?.kind) && track.mode === 'continuous' && track.clips.length === 0 && Array.isArray(track.effects) && track.effects.every(effect => effect?.kind === 'gain');
      const kinds = [9, 10, 11, 12].includes(session.schema_version) ? [...noteKinds, 'audio'] : ['sine', 'audio'];
      if (track.device?.kind === 'pd_instrument' && session.schema_version !== 12) return false;
      if (!kinds.includes(track.device?.kind) || !['continuous', 'sequenced'].includes(track.mode)) return false;
      if (track.mode === 'continuous' && (track.device.kind !== 'sine' || track.clips.length)) return false;
      if (!track.clips.every(clip => track.device.kind === 'audio' ? clip?.kind === 'audio' : clip?.kind === 'notes' && Array.isArray(clip.notes))) return false;
      return !arrangement(session) || ((track.effects === undefined || Array.isArray(track.effects)) && (track.effects || []).every(effect => (effect?.kind === 'gain' || ([11, 12].includes(session.schema_version) && ['lowpass', 'delay'].includes(effect?.kind)))));
    }))));
  const sources = session => (session?.tracks || []).filter(track => ['supercollider', 'csound'].includes(track.device?.kind));
  function sourceControlsEditable(control, metadata, kind = 'supercollider') {
    if (kind === 'csound') return Boolean(Number.isFinite(control?.value) && Array.isArray(control.points) && control.points.length === 0);
    const native = metadata?.controls?.find(item => item.name === control.name);
    return Boolean(native && native.initialization_rate === false && native.default_values?.length === control.values?.length && Array.isArray(control.points) && control.points.length === 0);
  }
  const plugins = session => (session?.tracks || []).flatMap(track => track.effects || []).filter(effect => effect.kind === 'vst3');
  function validate(session) {
    if (!supported(session)) return 'This editor supports continuous sine sessions in formats 1 and 4, sine/SuperCollider sessions with gain effects in format 6, and sine/SuperCollider/Csound sessions with gain effects in format 7. Built-in note/audio arrangements in formats 2, 3, 4, 9, 10 and 11, and Pd note arrangements in format 12 are also supported; other clips require scripts.';
    if (!Number.isInteger(session.sample_rate) || session.sample_rate < 8000 || session.sample_rate > 192000) return 'Sample rate must be between 8,000 and 192,000 Hz.';
    if (session.tracks.some(track => track.device?.kind === 'pd_instrument') && session.sample_rate !== 48000) return 'Pd instruments require a 48 kHz session.';
    if (session.tracks.length > 64) return 'A session can contain up to 64 tracks.';
    const automationError = validateGainAutomation(session); if (automationError) return automationError;
    if (arrangement(session)) { const error = validateArrangement(session); if (error) return error; }
    const ids = new Set();
    for (const track of session.tracks) {
      if (!track || !validText(track.id, 128) || ids.has(track.id)) return 'Track IDs must be unique and no longer than 128 UTF-8 bytes.';
      ids.add(track.id);
      if (track.mixer !== undefined) {
        if (![10, 11, 12].includes(session.schema_version)) return 'Saved mixer controls require format 10, 11 or 12.';
        const m = track.mixer;
        if (!m || typeof m !== 'object' || Array.isArray(m) || Object.keys(m).some(key => !['gain','pan','mute','solo'].includes(key)) || !Number.isFinite(m.gain) || m.gain < 0 || m.gain > 2 || !Number.isFinite(m.pan) || m.pan < -1 || m.pan > 1 || typeof m.mute !== 'boolean' || typeof m.solo !== 'boolean') return 'Mixer requires gain 0–2, pan −1–1, and mute/solo flags.';
      }
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
      if (track.device?.kind === 'pd_instrument') {
        const error = PdInstrument.validateDevice(track.device); if (error) return error;
        if (track.mode !== 'sequenced') return 'Pd instruments require sequenced note clips.';
        continue;
      }
      if (track.device?.kind === 'audio') {
        if (!Number.isFinite(track.device.gain) || track.device.gain < 0 || track.device.gain > 1) return 'Track gain must be between 0 and 1.';
        continue;
      }
      if (track.device?.kind === 'drumkit') {
        if (track.device.kit_id !== 'factory-v1') return 'Unknown built-in drum kit.';
        if (!Number.isFinite(track.device.gain) || track.device.gain < 0 || track.device.gain > 1) return 'Track gain must be between 0 and 1.';
        continue;
      }
      if (track.device?.kind === 'synth') {
        const d = track.device;
        if (!['saw', 'square'].includes(d.waveform) || !Number.isFinite(d.attack_ms) || d.attack_ms < 1 || d.attack_ms > 2000 || !Number.isFinite(d.release_ms) || d.release_ms < 0 || d.release_ms > 2000 || !Number.isFinite(d.cutoff_hz) || d.cutoff_hz < 20 || d.cutoff_hz > 20000 || d.cutoff_hz >= session.sample_rate / 2) return 'Synth requires saw/square, 1–2000 ms attack, 0–2000 ms release and a cutoff from 20 Hz through the session limit.';
        if (!Number.isFinite(d.gain) || d.gain < 0 || d.gain > 1) return 'Track gain must be between 0 and 1.';
        continue;
      }
      if (track.device?.kind !== 'sine') return session.schema_version === 1 ? 'Only continuous sine tracks can be edited here.' : 'Unsupported note instrument.';
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
      if ([3, 4, 9, 10, 11, 12].includes(session.schema_version) && !Array.isArray(track.effects)) return 'Tracks require an effects array.';
      if ((track.effects || []).length > 16) return 'A track can contain up to 16 effects.';
      const effectIds = new Set();
      for (const effect of track.effects || []) {
        if (!validText(effect.id, 128) || effectIds.has(effect.id) || typeof effect.bypass !== 'boolean') return 'Effects require unique IDs and a bypass flag.';
        const error = validateBuiltinEffect(effect, session.sample_rate); if (error) return error;
        effectIds.add(effect.id);
      }
      const pdGates = [];
      const clipIds = new Set();
      clips += track.clips.length;
      for (const clip of track.clips) {
        if (!validText(clip.id, 128) || clipIds.has(clip.id)) return 'Clip IDs must be unique within a track and no longer than 128 UTF-8 bytes.';
        clipIds.add(clip.id);
        if (!range(clip.start_frame, clip.length_frames)) return 'Clips require a nonnegative safe frame position and positive length.';
        if (clip.kind === 'audio') {
          if (!validText(clip.source_path, 4096) || clip.source_path.includes('\\') || clip.source_path.includes(':') || clip.source_path.split('/').some(part => !part || part === '.' || part === '..')) return 'Audio source paths must be bounded relative paths with normal components.';
          if (!frame(clip.source_offset_frames) || !Number.isSafeInteger(clip.source_offset_frames + clip.length_frames)) return 'Audio source offset and length require a safe frame range.';
          if (!Number.isFinite(clip.gain) || clip.gain < 0 || clip.gain > 1) return 'Audio clip gain must be between 0 and 1.';
          const fadeIn = clip.fade_in_frames === undefined ? 0 : clip.fade_in_frames, fadeOut = clip.fade_out_frames === undefined ? 0 : clip.fade_out_frames;
          if (!frame(fadeIn) || !frame(fadeOut) || fadeIn > clip.length_frames || fadeOut > clip.length_frames || fadeIn + fadeOut > clip.length_frames) return 'Audio fades require nonnegative safe frame counts whose sum fits the clip length.';
          events.push([clip.start_frame, 1], [clip.start_frame + clip.length_frames, -1]);
          continue;
        }
        const noteIds = new Set();
        notes += clip.notes.length;
        for (const note of clip.notes) {
          if (!note || !validText(note.id, 128) || noteIds.has(note.id)) return 'Note IDs must be unique within a clip and no longer than 128 UTF-8 bytes.';
          noteIds.add(note.id);
          if (!range(note.start_frame, note.duration_frames) || note.start_frame + note.duration_frames > clip.length_frames) return 'Note gates must have positive duration and end within their clip.';
          if (!Number.isFinite(note.frequency_hz) || note.frequency_hz <= 0 || note.frequency_hz >= session.sample_rate / 2) return 'Note frequency must be positive and below Nyquist.';
          if (!Number.isFinite(note.velocity) || note.velocity < 0 || note.velocity > 1) return 'Note velocity must be between 0 and 1.';
          const off = clip.start_frame + note.start_frame + note.duration_frames;
          if (track.device.kind === 'pd_instrument') {
            if (note.duration_frames < 64) return 'Pd note gates must last at least 64 frames.';
            pdGates.push([clip.start_frame + note.start_frame, off]);
          }
          const d = track.device;
          let releaseEnd = track.device.kind === 'pd_instrument' ? off : off + Math.floor((session.sample_rate + 100) / 200);
          if (d.kind === 'synth') releaseEnd = off + Math.max(1, Math.round(d.release_ms * session.sample_rate / 1000));
          if (d.kind === 'drumkit') {
            const pad = [36, 38, 42].find(p => Math.abs(midiToHz(p) - note.frequency_hz) <= midiToHz(p) * 1e-6);
            if (pad === undefined) return 'Drum kit pitches: MIDI 36 kick, 38 snare, 42 closed hat.';
            releaseEnd = clip.start_frame + note.start_frame + Math.floor(session.sample_rate * ({36: 0.5, 38: 0.25, 42: 0.12}[pad]));
          }
          if (!Number.isSafeInteger(releaseEnd)) return 'Note release exceeds the safe frame range.';
          events.push([clip.start_frame + note.start_frame, 1], [Math.min(releaseEnd, clip.start_frame + clip.length_frames), -1]);
        }
      }
      pdGates.sort((a, b) => a[0] - b[0]);
      for (let i = 1; i < pdGates.length; i += 1) if (pdGates[i][0] < pdGates[i - 1][1]) return 'Pd instruments are monophonic: note gates cannot overlap across clips.';
    }
    if (clips > 1024 || notes > 16384) return 'A session can contain up to 1024 clips and 16384 notes.';
    events.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let voices = continuous;
    for (const [, delta] of events) { voices += delta; if (voices > 64) return 'Polyphony exceeds 64 voices.'; }
    return null;
  }
  // These are gain-effect lanes; SC/Csound source control points have separate bounds.
  const automationLimits = {maxLanesPerTrack: 16, maxPoints: 16384};
  function validateGainAutomation(session) {
    let count = 0;
    for (const track of session.tracks) {
      if (track.automation === undefined) continue;
      if ([1, 2].includes(session.schema_version)) return 'Formats 1 and 2 do not accept gain automation.';
      if (!Array.isArray(track.automation) || track.automation.length > automationLimits.maxLanesPerTrack) return 'A track can contain up to 16 gain automation lanes.';
      const targets = new Set();
      for (const lane of track.automation) {
        if (!lane || Object.keys(lane).some(key => !['effect_id', 'parameter', 'interpolation', 'points'].includes(key)) || lane.parameter !== 'gain' || lane.interpolation !== 'step') return 'Gain automation requires the gain parameter and step interpolation.';
        if (!track.effects?.some(effect => effect.kind === 'gain' && effect.id === lane.effect_id) || targets.has(lane.effect_id)) return 'Gain automation requires one lane per existing gain effect.';
        targets.add(lane.effect_id);
        if (!Array.isArray(lane.points) || !lane.points.length) return 'Gain automation lanes require at least one point.';
        count += lane.points.length;
        if (count > automationLimits.maxPoints) return 'A session can contain up to 16384 gain automation points.';
        let previous = -1;
        for (const point of lane.points) {
          if (!point || Object.keys(point).some(key => !['frame', 'value'].includes(key)) || !frame(point.frame) || point.frame <= previous) return 'Gain automation frames must be nonnegative safe integers in strictly increasing order.';
          if (!Number.isFinite(point.value) || point.value < 0 || point.value > 4) return 'Gain automation values must be from 0 to 4.';
          previous = point.frame;
        }
      }
    }
    return null;
  }
  function mutateGainAutomation(session, index, id, mutate) {
    const error = validate(session); if (error) throw new Error(error);
    const next = structuredClone(session), track = next.tracks[index];
    if (!track?.effects?.some(effect => effect.kind === 'gain' && effect.id === id)) throw new Error('Select an existing gain effect.');
    let lane = track.automation?.find(lane => lane.effect_id === id);
    if (!lane) lane = {effect_id: id, parameter: 'gain', interpolation: 'step', points: []};
    mutate(lane);
    lane.points.sort((a, b) => a.frame - b.frame);
    track.automation ||= [];
    if (!track.automation.includes(lane) && lane.points.length) track.automation.push(lane);
    if (!lane.points.length) track.automation = track.automation.filter(item => item !== lane);
    const after = validate(next); if (after) throw new Error(after);
    return next;
  }
  function addGainAutomationPoint(session, index, id, point) {
    return mutateGainAutomation(session, index, id, lane => lane.points.push(structuredClone(point)));
  }
  function editGainAutomationPoint(session, index, id, oldFrame, point) {
    return mutateGainAutomation(session, index, id, lane => {
      const position = lane.points.findIndex(item => item.frame === oldFrame);
      if (position < 0) throw new Error('Select an existing gain automation point.');
      lane.points[position] = structuredClone(point);
    });
  }
  function deleteGainAutomationPoint(session, index, id, oldFrame) {
    return mutateGainAutomation(session, index, id, lane => {
      const position = lane.points.findIndex(item => item.frame === oldFrame);
      if (position < 0) throw new Error('Select an existing gain automation point.');
      lane.points.splice(position, 1);
    });
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
    if (![1, 2, 3, 4, 9, 10, 11, 12].includes(session.schema_version)) throw new Error('Note editing requires a built-in sine arrangement.');
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
    if (!track || !noteKinds.includes(track.device?.kind) || track.mode !== 'sequenced') throw new Error('Select a sequenced sine track.');
    return track;
  }
  function arrangementClip(next, index, id) {
    const track = next.tracks[index];
    if (!track || track.mode !== 'sequenced') throw new Error('Select a sequenced track.');
    const clip = track.clips.find(item => item.id === id);
    if (!clip) throw new Error('Select an existing clip.');
    return clip;
  }
  function noteClip(next, index, id) {
    noteTrack(next, index);
    const clip = arrangementClip(next, index, id);
    if (clip.kind !== 'notes') throw new Error('Select an existing note clip.');
    return clip;
  }
  function addNoteTrack(session, id, kind = 'sine') {
    if (!noteKinds.includes(kind)) throw new Error('Choose a note instrument.');
    return mutateArrangement(session, next => {
      if (kind === 'pd_instrument') {
        if (next.schema_version === 2) next.tracks.forEach(track => { track.effects = []; });
        next.schema_version = 12;
      } else if (kind !== 'sine') {
        if (next.schema_version === 2) next.tracks.forEach(track => { track.effects = []; });
        if (![10, 11, 12].includes(next.schema_version)) next.schema_version = 9;
      }
      const device = kind === 'pd_instrument' ? PdInstrument.preset() : kind === 'sine' ? {kind, frequency_hz: 440, gain: 0.15} : kind === 'drumkit' ? {kind, kit_id: 'factory-v1', gain: 0.45} : {kind, waveform: 'saw', gain: 0.12, attack_ms: 8, release_ms: 90, cutoff_hz: Math.min(3000, next.sample_rate / 2)};
      const track = {id, mode: 'sequenced', device, clips: []};
      if (next.schema_version !== 2) track.effects = [];
      next.tracks.push(track);
    });
  }
  function addPdInstrument(session, id, device) {
    const error = PdInstrument.validateDevice(device); if (error) throw new Error(error);
    const next = addNoteTrack(session, id, 'pd_instrument');
    next.tracks.at(-1).device = structuredClone(device);
    return next;
  }
  function editPdInstrument(session, index, patch) {
    return mutateArrangement(session, next => {
      const device = noteTrack(next, index).device;
      if (device.kind !== 'pd_instrument') throw new Error('Select a Pd instrument.');
      if (!patch || !Object.keys(patch).length || Object.keys(patch).some(key => !['gain', 'controls'].includes(key))) throw new Error('Choose a saved Pd gain or control edit.');
      Object.assign(device, structuredClone(patch));
    });
  }
  function addNoteClip(session, index, clip) {
    return mutateArrangement(session, next => noteTrack(next, index).clips.push({kind: 'notes', ...structuredClone(clip), notes: structuredClone(clip.notes || [])}));
  }
  function addAudioTrack(session, id, gain = 0.8) {
    return mutateArrangement(session, next => {
      const track = {id, mode: 'sequenced', device: {kind: 'audio', gain}, clips: []};
      if (next.schema_version !== 2) track.effects = [];
      next.tracks.push(track);
    });
  }
  function addAudioClip(session, index, clip) {
    return mutateArrangement(session, next => {
      if (next.tracks[index]?.device?.kind !== 'audio') throw new Error('Select a sequenced audio track.');
      next.tracks[index].clips.push({...structuredClone(clip), kind: 'audio'});
    });
  }
  function editAudioClip(session, index, id, patch) {
    return mutateArrangement(session, next => {
      const clip = arrangementClip(next, index, id);
      if (clip.kind !== 'audio') throw new Error('Select an audio clip.');
      if (!patch || Object.keys(patch).some(key => !['source_offset_frames', 'length_frames', 'gain', 'fade_in_frames', 'fade_out_frames'].includes(key))) throw new Error('Audio edits support source offset, length, gain and fades.');
      Object.assign(clip, structuredClone(patch));
    });
  }
  function moveClip(session, index, id, startFrame) {
    return mutateArrangement(session, next => { arrangementClip(next, index, id).start_frame = startFrame; });
  }
  function resizeClip(session, index, id, lengthFrames) {
    return mutateArrangement(session, next => { arrangementClip(next, index, id).length_frames = lengthFrames; });
  }
  function duplicateClip(session, index, id, newId, startFrame) {
    return mutateArrangement(session, next => {
      const clip = structuredClone(arrangementClip(next, index, id)); clip.id = newId; clip.start_frame = startFrame;
      next.tracks[index].clips.push(clip);
    });
  }
  function deleteClip(session, index, id) {
    return mutateArrangement(session, next => {
      arrangementClip(next, index, id);
      const track = next.tracks[index]; track.clips = track.clips.filter(clip => clip.id !== id);
    });
  }
  function addNote(session, index, clipId, note) {
    return mutateArrangement(session, next => noteClip(next, index, clipId).notes.push(structuredClone(note)));
  }
  // Timing is resolved by the step cursor; insertion uses the same checked edit.
  function insertStepNote(session, index, clipId, note) {
    return addNote(session, index, clipId, note);
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
  function createMusicalDemoSession() {
    let next = createDemoSession();
    next.schema_version = 9;
    next.tracks[0].device = {kind: 'synth', waveform: 'saw', gain: 0.13, attack_ms: 8, release_ms: 90, cutoff_hz: 2800};
    next.tracks[1].device = {kind: 'synth', waveform: 'square', gain: 0.13, attack_ms: 4, release_ms: 60, cutoff_hz: 700};
    next = addNoteTrack(next, 'drums', 'drumkit');
    for (let bar = 0; bar < 16; bar++) {
      const hits = [[0,36],[2,36],[1,38],[3,38], ...Array.from({length:8},(_,i)=>[i/2,42])];
      next = addNoteClip(next, 2, {id: `beat-${bar+1}`, start_frame: ticksToFrames(next,bar*4*960), length_frames: ticksToFrames(next,4*960), notes: hits.map(([beat,pitch],i)=>({id:`hit-${i+1}`,start_frame:ticksToFrames(next,Math.round(beat*960)),duration_frames:ticksToFrames(next,120),frequency_hz:midiToHz(pitch),velocity:pitch === 42 ? (i%2 ? 0.48:0.62) : 0.85}))});
    }
    return next;
  }
  function validateBuiltinEffect(effect, sampleRate) {
    if (effect.kind === 'gain') return Number.isFinite(effect.gain) && effect.gain >= 0 && effect.gain <= 4 ? null : 'Gain must be from 0 to 4.';
    if (effect.kind === 'lowpass') return Number.isFinite(effect.cutoff_hz) && effect.cutoff_hz >= 20 && effect.cutoff_hz <= 20000 && effect.cutoff_hz < sampleRate / 2 ? null : 'Lowpass cutoff must be 20–20,000 Hz and below Nyquist.';
    if (effect.kind === 'delay') return Number.isFinite(effect.time_ms) && effect.time_ms >= 1 && effect.time_ms <= 2000 && Number.isFinite(effect.feedback) && effect.feedback >= 0 && effect.feedback <= 0.95 && Number.isFinite(effect.mix) && effect.mix >= 0 && effect.mix <= 1 ? null : 'Delay requires 1–2000 ms time, 0–0.95 feedback and 0–1 mix.';
    return 'Unsupported built-in effect.';
  }
  const effectPresets = {
    lowpass: {warm: {cutoff_hz: 2800}, dark: {cutoff_hz: 700}, open: {cutoff_hz: 12000}},
    delay: {slap: {time_ms: 90, feedback: 0.15, mix: 0.2}, echo: {time_ms: 375, feedback: 0.4, mix: 0.3}, spacious: {time_ms: 650, feedback: 0.6, mix: 0.4}},
  };
  function effectPreset(kind, name, sampleRate = 48000) {
    const settings = effectPresets[kind]?.[name];
    if (!settings) throw new Error('Choose an existing effect preset.');
    const next = {kind, ...structuredClone(settings), bypass: false};
    if (kind === 'lowpass') next.cutoff_hz = Math.min(next.cutoff_hz, sampleRate / 2 - 1);
    return next;
  }
  function editEffect(session, index, id, patch) {
    const error = validate(session); if (error) throw new Error(error);
    const next = structuredClone(session);
    const effect = next.tracks[index]?.effects?.find(effect => effect.id === id);
    const fields = {gain: ['gain', 'bypass'], lowpass: ['cutoff_hz', 'bypass'], delay: ['time_ms', 'feedback', 'mix', 'bypass']}[effect?.kind];
    if (!fields || !patch || Object.keys(patch).some(key => !fields.includes(key))) throw new Error('Choose a saved built-in effect control.');
    Object.assign(effect, structuredClone(patch));
    const after = validate(next); if (after) throw new Error(after);
    return next;
  }
  function addEffect(session, index, effect, id) {
    const error = validate(session); if (error) throw new Error(error);
    if (arrangement(session) && !['gain', 'lowpass', 'delay'].includes(effect?.kind)) throw new Error('Built-in arrangements support gain, lowpass and delay effects.');
    if (['lowpass', 'delay'].includes(effect?.kind) && !mixerSupported(session)) throw new Error('Built-in processing requires built-in instruments/audio and compatible effects.');
    if ([6, 7].includes(session.schema_version) && effect?.kind !== 'gain') throw new Error('SuperCollider/Csound GUI sessions support gain effects only.');
    const next = structuredClone(session);
    if (next.schema_version === 1) {
      next.schema_version = 4; next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    }
    if (next.schema_version === 2) { next.schema_version = 3; next.tracks.forEach(track => { track.effects = []; }); }
    if (['lowpass', 'delay'].includes(effect?.kind) && next.schema_version !== 12) next.schema_version = 11;
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
  const mixerSupported = session => supported(session) && [1, 2, 3, 4, 9, 10, 11, 12].includes(session.schema_version) && session.tracks.every(track => [...noteKinds, 'audio'].includes(track.device?.kind) && (track.effects || []).every(effect => ['gain', 'lowpass', 'delay'].includes(effect.kind)));
  function editMixer(session, index, patch) {
    if (!mixerSupported(session)) throw new Error('Mixer supports built-in instruments/audio and built-in effects. Existing session data is preserved.');
    const error = validate(session); if (error) throw new Error(error);
    if (!patch || !Object.keys(patch).length || Object.keys(patch).some(key => !['gain','pan','mute','solo'].includes(key))) throw new Error('Choose a mixer gain, pan, mute or solo edit.');
    const next = structuredClone(session);
    if (!next.tracks[index]) throw new Error('Select an existing track.');
    if (next.schema_version === 1) {
      next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    } else if (next.schema_version === 2) next.tracks.forEach(track => { track.effects = []; });
    if (![11, 12].includes(next.schema_version)) next.schema_version = 10;
    next.tracks[index].mixer = {...(next.tracks[index].mixer || {gain:1,pan:0,mute:false,solo:false}), ...structuredClone(patch)};
    const after = validate(next); if (after) throw new Error(after);
    return next;
  }
  function exportMaximum(session, limits = {}) {
    const bound = (value, fallback) => Number.isFinite(value) && value >= 0.001 ? value : fallback;
    const tracks = session?.tracks || [];
    if (tracks.some(track => (track.effects || []).some(effect => ['vst3','au'].includes(effect.kind)))) return bound(limits.plugin_max_seconds, 10);
    const builtin = tracks.every(track => ['sine','synth','drumkit','audio'].includes(track.device?.kind) && (track.effects || []).every(effect => ['gain', 'lowpass', 'delay'].includes(effect.kind)));
    return builtin ? bound(limits.builtin_max_seconds ?? limits.max_seconds, 60) : bound(limits.runtime_max_seconds, 60);
  }
  const api = {addPdInstrument, editPdInstrument, automationLimits, addGainAutomationPoint, editGainAutomationPoint, deleteGainAutomationPoint, effectPresets, effectPreset, editEffect, exportMaximum, mixerSupported, editMixer,addAudioTrack, addAudioClip, editAudioClip, noteKinds, createMusicalDemoSession, arrangement, createArrangementSession, createDemoSession, addNoteTrack, addNoteClip, moveClip, resizeClip, duplicateClip, deleteClip, addNote, editNote, deleteNote, midiToHz, hzToMidi, ticksToFrames, framesToTicks, snapFrame, supported, plugins, sources, sourceControlsEditable, validate, addEffect, addCsound, addCsoundControl, removeEffect};
  api.insertStepNote = insertStepNote;
  if (typeof module !== 'undefined') module.exports = api;
  else root.SessionEditor = api;
})(typeof window === 'undefined' ? globalThis : window);

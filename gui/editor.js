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
  const supported = session => session && Array.isArray(session.tracks) &&
    (session.schema_version === 1 || ([4, 6, 7].includes(session.schema_version) && session.tracks.every(track =>
      track && ([6, 7].includes(session.schema_version) ? ['sine', 'supercollider', ...(session.schema_version === 7 ? ['csound'] : [])].includes(track.device?.kind) : track.device?.kind === 'sine') &&
      track.mode === 'continuous' && Array.isArray(track.clips) && track.clips.length === 0 &&
      (![6, 7].includes(session.schema_version) || (Array.isArray(track.effects) && track.effects.every(effect => effect?.kind === 'gain'))))));
  const sources = session => (session?.tracks || []).filter(track => ['supercollider', 'csound'].includes(track.device?.kind));
  function sourceControlsEditable(control, metadata, kind = 'supercollider') {
    if (kind === 'csound') return Boolean(Number.isFinite(control?.value) && Array.isArray(control.points) && control.points.length === 0);
    const native = metadata?.controls?.find(item => item.name === control.name);
    return Boolean(native && native.initialization_rate === false && native.default_values?.length === control.values?.length && Array.isArray(control.points) && control.points.length === 0);
  }
  const plugins = session => (session?.tracks || []).flatMap(track => track.effects || []).filter(effect => effect.kind === 'vst3');
  function validate(session) {
    if (!supported(session)) return 'This editor supports continuous sine sessions in formats 1 and 4, sine/SuperCollider sessions with gain effects in format 6, and sine/SuperCollider/Csound sessions with gain effects in format 7. Notes, clips and other effects require scripts.';
    if (!Number.isInteger(session.sample_rate) || session.sample_rate < 8000 || session.sample_rate > 192000) return 'Sample rate must be between 8,000 and 192,000 Hz.';
    if (session.tracks.length > 64) return 'A session can contain up to 64 tracks.';
    const ids = new Set();
    for (const track of session.tracks) {
      if (!track || typeof track.id !== 'string' || !track.id.length || new TextEncoder().encode(track.id).length > 128 || ids.has(track.id)) return 'Track IDs must be unique and no longer than 128 UTF-8 bytes.';
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
  function addEffect(session, index, effect, id) {
    const error = validate(session); if (error) throw new Error(error);
    if ([6, 7].includes(session.schema_version) && effect?.kind !== 'gain') throw new Error('SuperCollider/Csound GUI sessions support gain effects only.');
    const next = structuredClone(session);
    if (next.schema_version === 1) {
      next.schema_version = 4; next.tempo_milli_bpm = 120000;
      next.tracks.forEach(track => { track.mode = 'continuous'; track.clips = []; track.effects = []; });
    }
    const track = next.tracks[index]; if (!track) throw new Error('Select an existing track.');
    if ((track.effects || []).length >= 16) throw new Error('A track can contain up to 16 effects.');
    if (effect.kind === 'vst3' && (next.sample_rate !== 48000 || plugins(next).length >= 8)) throw new Error('Use 48 kHz and at most eight VST3 effects per session.');
    track.effects ||= []; track.effects.push({...structuredClone(effect), id});
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
  const api = {supported, plugins, sources, sourceControlsEditable, validate, addEffect, addCsound, addCsoundControl, removeEffect};
  if (typeof module !== 'undefined') module.exports = api;
  else root.SessionEditor = api;
})(typeof window === 'undefined' ? globalThis : window);

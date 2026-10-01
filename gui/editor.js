/* Browser editing scope; Rust remains authoritative for complete session validation. */
(function (root) {
  'use strict';
  const supported = session => session && Array.isArray(session.tracks) &&
    (session.schema_version === 1 || ([4, 6].includes(session.schema_version) && session.tracks.every(track =>
      track && (session.schema_version === 6 ? ['sine', 'supercollider'].includes(track.device?.kind) : track.device?.kind === 'sine') &&
      track.mode === 'continuous' && Array.isArray(track.clips) && track.clips.length === 0 &&
      (session.schema_version !== 6 || (Array.isArray(track.effects) && track.effects.every(effect => effect?.kind === 'gain'))))));
  const sources = session => (session?.tracks || []).filter(track => track.device?.kind === 'supercollider');
  function sourceControlsEditable(control, metadata) {
    const native = metadata?.controls?.find(item => item.name === control.name);
    return Boolean(native && native.initialization_rate === false && native.default_values?.length === control.values?.length && Array.isArray(control.points) && control.points.length === 0);
  }
  const plugins = session => (session?.tracks || []).flatMap(track => track.effects || []).filter(effect => effect.kind === 'vst3');
  function validate(session) {
    if (!supported(session)) return 'This editor supports continuous sine sessions in formats 1 and 4, and sine/SuperCollider sessions with gain effects in format 6. Notes, clips and other effects require scripts.';
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
      if (track.device?.kind !== 'sine') return 'Only continuous sine tracks can be edited here.';
      if (!Number.isFinite(track.device.frequency_hz) || track.device.frequency_hz <= 0 || track.device.frequency_hz >= session.sample_rate / 2) return 'Frequency must be positive and below the session Nyquist limit.';
      if (!Number.isFinite(track.device.gain) || track.device.gain < 0 || track.device.gain > 1) return 'Track gain must be between 0 and 1.';
    }
    if (plugins(session).length && session.sample_rate !== 48000) return 'VST3 effects require a 48 kHz session.';
    return null;
  }
  function addEffect(session, index, effect, id) {
    const error = validate(session); if (error) throw new Error(error);
    if (session.schema_version === 6 && effect.kind !== 'gain') throw new Error('SuperCollider GUI sessions support gain effects only.');
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
  function removeEffect(session, index, id) {
    const next = structuredClone(session); const track = next.tracks[index];
    track.effects = track.effects.filter(effect => effect.id !== id);
    if (track.automation) track.automation = track.automation.filter(lane => lane.effect_id !== id);
    return next;
  }
  const api = {supported, plugins, sources, sourceControlsEditable, validate, addEffect, removeEffect};
  if (typeof module !== 'undefined') module.exports = api;
  else root.SessionEditor = api;
})(typeof window === 'undefined' ? globalThis : window);

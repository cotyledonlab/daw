/* Browser editing scope; Rust remains authoritative for complete session validation. */
(function (root) {
  'use strict';
  const supported = session => session && Array.isArray(session.tracks) &&
    (session.schema_version === 1 || (session.schema_version === 4 && session.tracks.every(track =>
      track && track.device?.kind === 'sine' && track.mode === 'continuous' && Array.isArray(track.clips) && track.clips.length === 0)));
  const plugins = session => (session?.tracks || []).flatMap(track => track.effects || []).filter(effect => effect.kind === 'vst3');
  function validate(session) {
    if (!supported(session)) return 'This editor supports continuous sine sessions in formats 1 and 4. Notes and audio clips require scripts.';
    if (!Number.isInteger(session.sample_rate) || session.sample_rate < 8000 || session.sample_rate > 192000) return 'Sample rate must be between 8,000 and 192,000 Hz.';
    if (session.tracks.length > 64) return 'A session can contain up to 64 tracks.';
    const ids = new Set();
    for (const track of session.tracks) {
      if (!track || typeof track.id !== 'string' || !track.id.length || new TextEncoder().encode(track.id).length > 128 || ids.has(track.id)) return 'Track IDs must be unique and no longer than 128 UTF-8 bytes.';
      ids.add(track.id);
      if (track.device?.kind !== 'sine') return 'Only continuous sine tracks can be edited here.';
      if (!Number.isFinite(track.device.frequency_hz) || track.device.frequency_hz <= 0 || track.device.frequency_hz >= session.sample_rate / 2) return 'Frequency must be positive and below the session Nyquist limit.';
      if (!Number.isFinite(track.device.gain) || track.device.gain < 0 || track.device.gain > 1) return 'Track gain must be between 0 and 1.';
    }
    if (plugins(session).length && session.sample_rate !== 48000) return 'VST3 effects require a 48 kHz session.';
    return null;
  }
  function addEffect(session, index, effect, id) {
    const error = validate(session); if (error) throw new Error(error);
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
  const api = {supported, plugins, validate, addEffect, removeEffect};
  if (typeof module !== 'undefined') module.exports = api;
  else root.SessionEditor = api;
})(typeof window === 'undefined' ? globalThis : window);

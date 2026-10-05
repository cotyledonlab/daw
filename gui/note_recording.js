/* A bounded overdub take. Captures never mutate the saved session. Clock units: ms. */
class NoteRecording {
  constructor(options = {}) {
    this.clock = options.clock || (() => performance.now());
    this.onStatus = options.onStatus || (() => {});
    this.maxEvents = options.maxEvents || 4096;
    this.maxHeld = options.maxHeld || 128;
    this.take = null;
    this.pending = null;
    this.message = '';
  }
  get active() { return !!this.take && !this.pending; }
  get count() { return this.take?.notes.length || 0; }
  status(message) { this.message = message; this.onStatus(message); }
  arm({session, revision, trackIndex, clipId}) {
    if (this.take) throw new Error('Finish or discard the current take first.');
    const track = session?.tracks?.[trackIndex];
    const clip = track?.clips?.find(c => c.id === clipId);
    if (!revision || !['sine', 'synth', 'drumkit', 'pd_instrument'].includes(track?.device?.kind) || !clip || clip.kind === 'audio' || !Array.isArray(clip.notes) || !Number.isSafeInteger(clip.length_frames) || clip.length_frames <= 0)
      throw new Error('Recording requires a saved note clip and revision.');
    this.take = {session: structuredClone(session), fingerprint: JSON.stringify(session), revision, trackIndex, clipId,
      clip: structuredClone(clip), notes: [], held: new Map(), events: 0, anchor: null, truncated: 0};
    this.status('Armed. Notes are buffered until Stop.');
    return true;
  }
  cancel(reason = 'Take discarded.') { this.take = null; this.pending = null; this.status(reason); }
  accept() { if (!this.pending) return false; this.take = null; this.pending = null; this.status('Recorded take applied.'); return true; }
  updateTransport(anchor) {
    if (!this.active) return false;
    const t = this.take;
    if (anchor?.loop?.enabled || anchor?.loop === true || anchor?.loop_enabled || anchor?.loop_region != null) { this.cancel('Recording requires loop playback to be disabled. Take discarded.'); return false; }
    if (!anchor || !Number.isSafeInteger(anchor.timeline_frame) || anchor.timeline_frame < 0 || anchor.sample_rate !== t.session.sample_rate ||
        !Number.isFinite(anchor.sample_rate) || anchor.sample_rate <= 0) { this.cancel('Invalid transport timing. Take discarded.'); return false; }
    if (anchor.state === 'paused' && t.anchor?.state === 'playing') {
      this.cancel('Playback paused during recording. Take discarded; record with uninterrupted playback.');
      return false;
    }
    const timestamp = Number.isFinite(anchor.timestamp) ? anchor.timestamp : this.clock();
    if (t.anchor && anchor.state === 'playing' && t.anchor.state === 'playing') {
      // Poll and callback timing can differ; a backwards timeline position is never a valid take.
      const expected = t.anchor.timeline_frame + Math.max(0, Math.round((timestamp - t.anchor.timestamp) * anchor.sample_rate / 1000) - (t.anchor.count_in_remaining_frames || 0));
      if (anchor.timeline_frame < t.anchor.timeline_frame || anchor.timeline_frame > expected + Math.round(anchor.sample_rate / 4) || timestamp < t.anchor.timestamp) {
        this.cancel('Transport position changed during recording. Take discarded.'); return false;
      }
    }
    t.anchor = {...anchor, timestamp};
    return true;
  }
  frame(timestamp) {
    const a = this.take?.anchor;
    if (!a || a.state !== 'playing' || !Number.isFinite(timestamp) || timestamp < a.timestamp) return null;
    const elapsed = Math.round((timestamp - a.timestamp) * a.sample_rate / 1000);
    const remaining = Math.max(0, a.count_in_remaining_frames || 0);
    if (elapsed < remaining) return null;
    return a.timeline_frame + elapsed - remaining;
  }
  capture(event) {
    if (!this.active || !event) return false;
    if (event.type === 'cancel' || event.cancelled) { this.cancel('Input focus lost or input disabled. Take discarded.'); return false; }
    const t = this.take, key = event.key || `${event.source}:${event.channel || 0}:${event.midi}`;
    if (!['on', 'off'].includes(event.type)) return false;
    if (event.type === 'on' && t.held.has(key)) return false;
    if (++t.events > this.maxEvents) { this.cancel('Recording event limit reached. Take discarded.'); return false; }
    if (event.type === 'on') {
      if (t.held.size >= this.maxHeld) { this.cancel('Too many held notes. Take discarded.'); return false; }
      const frame = this.frame(event.timestamp);
      // Keep an ignored onset until release, so a count-in key cannot become a later note.
      if (frame === null || frame < t.clip.start_frame || frame >= t.clip.start_frame + t.clip.length_frames) {
        t.held.set(key, null); return false;
      }
      if (!Number.isFinite(event.frequency_hz) || event.frequency_hz <= 0 || event.frequency_hz >= t.session.sample_rate / 2 ||
          !Number.isFinite(event.velocity) || event.velocity <= 0 || event.velocity > 1) return false;
      t.held.set(key, {frame, timestamp:event.timestamp, frequency_hz:event.frequency_hz, velocity:event.velocity});
      return true;
    }
    const note = t.held.get(key);
    t.held.delete(key);
    if (!note) return false;
    const end = this.frame(event.timestamp);
    return end !== null && this.complete(note, end);
  }
  complete(note, end) {
    const t = this.take, clipEnd = t.clip.start_frame + t.clip.length_frames;
    if (end > clipEnd) { end = clipEnd; t.truncated++; }
    if (end <= note.frame) return false;
    const duration = end - note.frame;
    if (t.session.tracks[t.trackIndex].device?.kind === 'pd_instrument' && duration < 64) {
      this.status('A gate shorter than 64 frames was ignored for this Pd instrument.'); return false;
    }
    t.notes.push({start_frame: note.frame - t.clip.start_frame, duration_frames: duration, frequency_hz: note.frequency_hz, velocity: note.velocity});
    this.status(`${t.notes.length} recorded note(s) buffered.${t.truncated ? ' New gates ending beyond the clip were shortened to its end.' : ''}`);
    return true;
  }
  finish({session, revision, transport} = {}) {
    if (!this.take) return null;
    const t = this.take;
    if (revision !== t.revision || JSON.stringify(session) !== t.fingerprint) { this.cancel('Saved session changed during recording. Take discarded.'); return null; }
    if (!transport || transport.state !== 'stopped') throw new Error('Stop native playback before applying the take.');
    if (this.pending) return structuredClone(this.pending);
    if (!Number.isSafeInteger(transport.timeline_frame) || transport.timeline_frame < (t.anchor?.timeline_frame || 0)) { this.cancel('Stop position unavailable. Take discarded.'); return null; }
    // Stop closes held gates at the confirmed stop frame, never at a later poll timestamp.
    for (const note of t.held.values()) if (note) this.complete(note, transport.timeline_frame);
    t.held.clear();
    if (!t.notes.length) { this.cancel('No notes recorded.'); return null; }
    const draft = structuredClone(t.session), clip = draft.tracks[t.trackIndex].clips.find(c => c.id === t.clipId);
    const ids = new Set(clip.notes.map(n => n.id));
    let index = 1;
    const notes = t.notes.map(note => { while (ids.has(`recorded-${index}`)) index++; const id = `recorded-${index++}`; ids.add(id); return {id, ...note}; });
    clip.notes.push(...notes);
    this.pending = {draft, revision:t.revision, trackIndex:t.trackIndex, clipId:t.clipId, notes, truncated:t.truncated};
    this.status(`${notes.length} recorded note(s) ready to apply.${t.truncated ? ' New gates shortened to clip end.' : ''}`);
    return structuredClone(this.pending);
  }
}
if (typeof window !== 'undefined') window.NoteRecording = NoteRecording;
if (typeof module !== 'undefined') module.exports = NoteRecording;

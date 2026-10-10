/* Opt-in note entry and actual-engine WAV audition. No browser synthesizer. */
class NoteInput {
  constructor(options = {}) {
    this.options = options;
    this.window = options.window || (typeof window !== 'undefined' ? window : null);
    this.document = options.document || (typeof document !== 'undefined' ? document : null);
    this.navigator = options.navigator || (typeof navigator !== 'undefined' ? navigator : null);
    this.createContext = options.createContext || (() => new AudioContext({ latencyHint: 'interactive' }));
    this.enabled = false;
    this.disposed = false;
    this.baseMidi = 48;
    this.volume = 0.25;
    this.epoch = 0;
    this.context = null;
    this.voices = new Set();
    this.pending = new Set();
    this.cache = new Map();
    this.held = new Set();
    this.performanceHeld = new Map();
    this.localMonitors = new Map();
    this.clock = options.clock || (() => performance.now());
    this.midiInputs = new Map();
    this.midiAccess = null;
    this.midiConnecting = null;
    this.keyDown = event => this.handleKey(event);
    this.keyUp = event => this.releasePerformance(event.code || event.key?.toLowerCase());
    this.blur = () => this.stop();
    this.visibility = () => { if (this.document.hidden) this.stop(); };
    this.window?.addEventListener('keydown', this.keyDown);
    this.window?.addEventListener('keyup', this.keyUp);
    this.window?.addEventListener('blur', this.blur);
    this.document?.addEventListener('visibilitychange', this.visibility);
  }

  setEnabled(value) {
    this.enabled = !!value && !this.disposed;
    if (!this.enabled) this.stop();
  }

  setBaseMidi(value) {
    if (Number.isInteger(value) && value >= 0 && value <= 115) this.baseMidi = value;
  }

  setVolume(value) {
    if (!Number.isFinite(value) || value < 0 || value > 1) return;
    this.volume = value;
    if (this.master) this.master.gain.setTargetAtTime(value, this.context.currentTime, 0.01);
  }

  editable(element) {
    return !!element && (element.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(element.tagName) ||
      !!element.closest?.('[contenteditable]:not([contenteditable="false"]),input,textarea,select'));
  }

  handleKey(event) {
    if (event.key === 'Escape') {
      // The transport owner closes gates at its confirmed Stop frame.
      if (!this.options.getPerformanceTarget?.()) this.stop();
      return;
    }
    if (!this.enabled || this.document?.hidden || event.repeat || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey ||
        this.editable(event.target) || this.editable(this.document?.activeElement)) return;
    const key = event.key.toLowerCase();
    const keys = ['a', 'w', 's', 'e', 'd', 'f', 't', 'g', 'y', 'h', 'u', 'j', 'k'];
    const offset = keys.indexOf(key);
    if (offset < 0) return;
    const target = this.options.getPerformanceTarget?.() || this.options.getTarget?.();
    if (!target) return;
    const midi = target.drum ? ({ a: 36, s: 38, d: 42 })[key] : this.baseMidi + offset;
    if (midi === undefined) return;
    const heldKey = event.code || key;
    if (this.held.has(heldKey)) return;
    this.held.add(heldKey);
    event.preventDefault?.();
    this.pressPerformance(midi, 0.8, 'keyboard', undefined, heldKey);
  }

  emitPerformance(note) {
    try { this.options.onPerformance?.(note); } catch (error) { this.report(error); }
  }

  pressPerformance(midi, velocity, source, channel, key) {
    if (!this.enabled || this.disposed || this.document?.hidden || this.performanceHeld.has(key) ||
        !Number.isInteger(midi) || midi < 0 || midi > 127 || !Number.isFinite(velocity) || velocity <= 0 || velocity > 1) return;
    if (!(this.options.getPerformanceTarget?.() || this.options.getTarget?.())) return;
    if (this.performanceHeld.size >= 128) { this.stop(); this.report(new Error('Too many held input notes.')); return; }
    const note = {midi, frequency_hz: 440 * 2 ** ((midi - 69) / 12), velocity, source, channel, key};
    this.performanceHeld.set(key, note);
    this.emitPerformance({...note, type: 'on', timestamp: this.clock()});
    if(this.options.getPerformanceTarget?.()?.localMonitor) {
      const context=this.context;
      if(context && context.state==='running') {
        const osc=context.createOscillator(),gain=context.createGain();
        osc.frequency.value=note.frequency_hz;gain.gain.value=0;gain.gain.setTargetAtTime(velocity*0.12,context.currentTime,0.005);
        osc.connect(gain);gain.connect(this.master);osc.start();this.localMonitors.set(key,{osc,gain});
      }
    }
    // During native recording getTarget is null: capture still runs without browser preview.
    if (this.options.getTarget?.()) void this.playNote(midi, velocity, source, channel);
  }

  releasePerformance(key, cancelled = false) {
    this.held.delete(key);
    const note = this.performanceHeld.get(key);
    if (!note) return;
    this.performanceHeld.delete(key);
    const monitor=this.localMonitors.get(key);
    if(monitor){this.localMonitors.delete(key);monitor.gain.gain.setTargetAtTime(0,this.context.currentTime,0.005);monitor.osc.stop(this.context.currentTime+0.04);monitor.osc.onended=()=>{monitor.osc.disconnect();monitor.gain.disconnect();};}
    this.emitPerformance({...note, type: 'off', timestamp: this.clock(), cancelled});
  }

  report(error) { this.options.onError?.(error); }

  // Resume runs synchronously in the key/Connect/click gesture, before fetching WAV data.
  resumeAudio() {
    if (!this.context) {
      this.context = this.createContext();
      this.master = this.context.createGain();
      this.master.gain.value = this.volume;
      this.master.connect(this.context.destination);
    }
    return this.context.resume();
  }

  unlock() {
    if (this.disposed) return Promise.resolve(false);
    try {
      return Promise.resolve(this.resumeAudio()).then(() => true).catch(error => { this.report(error); return false; });
    } catch (error) { this.report(error); return Promise.resolve(false); }
  }

  play(midi, velocity = 0.8, source = 'button') { return this.playNote(midi, velocity, source); }

  async playNote(midi, velocity = 0.8, source = 'keyboard', channel) {
    if (!this.enabled || this.disposed || this.document?.hidden || !Number.isInteger(midi) || midi < 0 || midi > 127 ||
        !Number.isFinite(velocity) || velocity <= 0 || velocity > 1) return false;
    let resume;
    try { resume = this.resumeAudio(); } catch (error) { this.report(error); return false; }
    // Capture one target for the entire audition; step entry follows successful playback.
    const entered = { midi, frequency_hz: 440 * 2 ** ((midi - 69) / 12), velocity, source, channel };
    Promise.resolve(resume).catch(() => {});
    const epoch = this.epoch;
    const context = this.context;
    let token;
    try {
      const target = this.options.getTarget?.();
      if (!target || !this.enabled || epoch !== this.epoch) return false;
      const key = String(target.key);
      const valid = () => this.enabled && !this.disposed && epoch === this.epoch && context === this.context &&
        !this.document?.hidden && String(this.options.getTarget?.()?.key) === key;
      const cacheKey = `${key}:${midi}:${velocity}`;
      const cached = this.cache.get(cacheKey);
      if (!cached && this.pending.size >= 3) return false;
      token = { controller: new AbortController() };
      if (!cached) this.pending.add(token);
      await resume;
      if (!valid()) return false;
      let buffer = cached;
      if (!buffer) {
        const bytes = await this.options.requestPreview({ ...entered, target, signal: token.controller.signal });
        if (!valid()) return false;
        buffer = await context.decodeAudioData(bytes.slice(0));
        if (!valid()) return false;
        this.cache.set(cacheKey, buffer);
        while (this.cache.size > 24) this.cache.delete(this.cache.keys().next().value);
      } else {
        this.cache.delete(cacheKey);
        this.cache.set(cacheKey, buffer);
      }
      if (!valid()) return false;
      while (this.voices.size >= 6) this.releaseVoice(this.voices.values().next().value, true);
      const voice = context.createBufferSource();
      voice.buffer = buffer;
      voice.connect(this.master);
      voice.onended = () => this.releaseVoice(voice);
      this.voices.add(voice);
      voice.start();
      Promise.resolve(this.options.onNote?.({ ...entered, target })).catch(error => this.report(error));
      return true;
    } catch (error) {
      if (epoch === this.epoch && error.name !== 'AbortError') this.report(error);
      return false;
    } finally {
      if (token) this.pending.delete(token);
      // A rejected resume must always be observed, even if note entry failed first.
      Promise.resolve(resume).catch(() => {});
    }
  }

  releaseVoice(voice, stop = false) {
    if (!this.voices.delete(voice)) return;
    voice.onended = null;
    if (stop) { try { voice.stop(); } catch (_) {} }
    voice.disconnect();
  }

  stop() {
    this.emitPerformance({type: 'cancel', timestamp: this.clock()});
    this.epoch += 1;
    for (const key of [...this.performanceHeld.keys()]) this.releasePerformance(key, true);
    this.held.clear();
    for (const token of this.pending) token.controller.abort();
    for (const voice of [...this.voices]) this.releaseVoice(voice, true);
  }

  reset() { this.stop(); this.cache.clear(); }

  midiStatus(status) { this.options.onMIDIStatus?.(status); return status; }

  connectMIDI() {
    if (this.disposed) return Promise.resolve('disconnected');
    if (!this.navigator?.requestMIDIAccess) return Promise.resolve(this.midiStatus('unsupported'));
    if (this.midiAccess) return Promise.resolve(this.midiStatus('connected'));
    if (this.midiConnecting) return this.midiConnecting;
    // Unlock browser audio from the explicit Connect gesture for subsequent MIDI messages.
    try { Promise.resolve(this.resumeAudio()).catch(error => this.report(error)); }
    catch (error) { this.report(error); }
    this.midiStatus('connecting');
    let accessRequest;
    try { accessRequest = this.navigator.requestMIDIAccess({ sysex: false }); }
    catch (error) { this.report(error); return Promise.resolve(this.midiStatus('unavailable')); }
    this.midiConnecting = Promise.resolve(accessRequest).then(access => {
      if (this.disposed) return 'disconnected';
      this.midiAccess = access;
      access.onstatechange = () => this.refreshMIDI();
      this.refreshMIDI();
      return this.midiStatus('connected');
    }).catch(error => {
      if (!this.disposed) { this.report(error); this.midiStatus('unavailable'); }
      return 'unavailable';
    }).finally(() => { this.midiConnecting = null; });
    return this.midiConnecting;
  }

  refreshMIDI() {
    const active = new Map();
    for (const input of this.midiAccess.inputs.values()) {
      if (input.state === 'disconnected') continue;
      active.set(input.id, input);
      if (this.midiInputs.get(input.id) === input) continue;
      input.onmidimessage = event => {
        const [status, midi, velocity] = event.data;
        const channel = status & 0x0f, key = `midi:${input.id}:${channel}:${midi}`;
        if ((status & 0xf0) === 0x90 && velocity > 0) this.pressPerformance(midi, velocity / 127, 'midi', channel, key);
        else if ((status & 0xf0) === 0x80 || ((status & 0xf0) === 0x90 && velocity === 0)) this.releasePerformance(key);
      };
    }
    for (const [id, input] of this.midiInputs) {
      if (active.get(id) !== input) { input.onmidimessage = null; this.stop(); }
    }
    this.midiInputs = active;
  }

  dispose() {
    this.setEnabled(false);
    this.disposed = true;
    this.window?.removeEventListener('keydown', this.keyDown);
    this.window?.removeEventListener('keyup', this.keyUp);
    this.window?.removeEventListener('blur', this.blur);
    this.document?.removeEventListener('visibilitychange', this.visibility);
    if (this.midiAccess) this.midiAccess.onstatechange = null;
    for (const input of this.midiInputs.values()) input.onmidimessage = null;
    this.midiInputs.clear();
    this.cache.clear();
    this.master?.disconnect();
    if (this.context) void this.context.close().catch(() => {});
    this.context = null;
    this.master = null;
  }
}

if (typeof window !== 'undefined') window.NoteInput = NoteInput;
if (typeof module !== 'undefined') module.exports = NoteInput;

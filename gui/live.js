/* Browser-only sine audition. Native/plugin audio remains owned by future engine work. */
class LivePlayer {
  constructor(createContext = () => new AudioContext({ latencyHint: 'interactive' })) {
    this.createContext = createContext;
    this.context = null;
    this.voices = new Map();
    this.volume = 0.25;
  }

  async start(session) {
    this.stop();
    const context = this.createContext();
    this.context = context;
    this.master = context.createGain();
    this.master.gain.value = this.volume;
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 256;
    this.samples = new Float32Array(this.analyser.fftSize);
    this.master.connect(this.analyser);
    this.analyser.connect(context.destination);
    try {
      // Called directly from the Play gesture, before any network await.
      await context.resume();
      if (this.context !== context) return false;
      this.update(session);
      return true;
    } catch (error) {
      if (this.context === context) this.stop();
      throw error;
    }
  }

  update(session) {
    if (!this.context) return;
    const limit = Math.min(session.sample_rate, this.context.sampleRate) / 2;
    for (const { device } of session.tracks) {
      if (device.kind !== 'sine' || !Number.isFinite(device.frequency_hz) ||
          device.frequency_hz <= 0 || device.frequency_hz >= limit ||
          !Number.isFinite(device.gain) || device.gain < 0 || device.gain > 1) {
        throw new Error(`Live frequency must be below ${limit} Hz and gain between 0 and 1.`);
      }
    }
    const now = this.context.currentTime;
    const ids = new Set(session.tracks.map(track => track.id));
    for (const [id, voice] of this.voices) {
      if (!ids.has(id)) {
        voice.osc.stop();
        voice.osc.disconnect();
        voice.gain.disconnect();
        this.voices.delete(id);
      }
    }
    // Normalize worst-case summed peaks. Monitor volume never changes WAV export.
    const divisor = Math.max(1, session.tracks.reduce((sum, track) => sum + track.device.gain, 0));
    for (const track of session.tracks) {
      let voice = this.voices.get(track.id);
      if (!voice) {
        const osc = this.context.createOscillator();
        const gain = this.context.createGain();
        osc.type = 'sine';
        osc.frequency.value = track.device.frequency_hz;
        gain.gain.value = 0;
        osc.connect(gain);
        gain.connect(this.master);
        osc.start();
        voice = { osc, gain };
        this.voices.set(track.id, voice);
      }
      voice.osc.frequency.setTargetAtTime(track.device.frequency_hz, now, 0.01);
      voice.gain.gain.setTargetAtTime(track.device.gain / divisor, now, 0.01);
    }
  }

  setVolume(value) {
    if (!Number.isFinite(value) || value < 0 || value > 1) return;
    this.volume = value;
    if (this.context) this.master.gain.setTargetAtTime(value, this.context.currentTime, 0.01);
  }

  level() {
    if (!this.context || this.context.state !== 'running') return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    return Math.sqrt(this.samples.reduce((sum, value) => sum + value * value, 0) / this.samples.length);
  }

  stop() {
    if (!this.context) return;
    const context = this.context;
    this.context = null;
    this.master.disconnect();
    this.analyser.disconnect();
    for (const voice of this.voices.values()) {
      voice.osc.stop();
      voice.osc.disconnect();
      voice.gain.disconnect();
    }
    this.voices.clear();
    void context.close().catch(() => {});
  }
}

if (typeof module !== 'undefined') module.exports = LivePlayer;

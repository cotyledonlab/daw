const assert = require('node:assert/strict');
const test = require('node:test');
const LivePlayer = require('./live.js');

class FakeParam {
  constructor(value = 0) {
    this.value = value;
    this.targets = [];
  }

  setTargetAtTime(value, time, constant) {
    this.targets.push({ value, time, constant });
  }
}

class FakeNode {
  constructor() {
    this.connections = [];
    this.disconnectCount = 0;
  }

  connect(destination) {
    this.connections.push(destination);
  }

  disconnect() {
    this.disconnectCount += 1;
  }
}

class FakeOscillator extends FakeNode {
  constructor() {
    super();
    this.type = null;
    this.frequency = new FakeParam();
    this.startCount = 0;
    this.stopCount = 0;
  }

  start() {
    this.startCount += 1;
  }

  stop() {
    this.stopCount += 1;
  }
}

class FakeGain extends FakeNode {
  constructor() {
    super();
    this.gain = new FakeParam();
  }
}

class FakeAnalyser extends FakeNode {
  constructor() {
    super();
    this.fftSize = 0;
  }

  getFloatTimeDomainData(samples) {
    samples.fill(0);
  }
}

class FakeAudioContext {
  constructor({ sampleRate = 48000, resume = () => Promise.resolve() } = {}) {
    this.sampleRate = sampleRate;
    this.currentTime = 3;
    this.state = 'running';
    this.destination = {};
    this.oscillators = [];
    this.gains = [];
    this.analysers = [];
    this.closeCount = 0;
    this.resume = resume;
  }

  createGain() {
    const gain = new FakeGain();
    this.gains.push(gain);
    return gain;
  }

  createAnalyser() {
    const analyser = new FakeAnalyser();
    this.analysers.push(analyser);
    return analyser;
  }

  createOscillator() {
    const oscillator = new FakeOscillator();
    this.oscillators.push(oscillator);
    return oscillator;
  }

  close() {
    this.closeCount += 1;
    return Promise.resolve();
  }
}

function track(id, frequency_hz, gain) {
  return { id, device: { kind: 'sine', frequency_hz, gain } };
}

function session(...tracks) {
  return { sample_rate: 48000, tracks };
}

test('start, update, repeated start, and stop keep one voice per track and release resources', async () => {
  const contexts = [];
  const player = new LivePlayer(() => {
    const context = new FakeAudioContext();
    contexts.push(context);
    return context;
  });

  assert.equal(await player.start(session(track('a', 440, 0.4))), true);
  const firstContext = contexts[0];
  const oldOscillator = firstContext.oscillators[0];
  player.update(session(track('a', 660, 0.6), track('b', 330, 0.2)));
  assert.equal(firstContext.oscillators.length, 2);
  assert.equal(oldOscillator.startCount, 1);

  assert.equal(await player.start(session(track('a', 220, 0.5))), true);
  const secondContext = contexts[1];
  assert.equal(firstContext.closeCount, 1);
  assert.equal(oldOscillator.stopCount, 1);
  assert.equal(oldOscillator.disconnectCount, 1);
  assert.equal(player.voices.size, 1);
  assert.equal(secondContext.oscillators.length, 1);

  player.stop();
  player.stop();
  assert.equal(player.context, null);
  assert.equal(player.voices.size, 0);
  assert.equal(secondContext.closeCount, 1);
  assert.equal(secondContext.oscillators[0].stopCount, 1);
  assert.equal(secondContext.oscillators[0].disconnectCount, 1);
  assert.equal(secondContext.gains[1].disconnectCount, 1);
});

test('normalizes summed voice gain and applies valid monitor volume changes', async () => {
  const context = new FakeAudioContext();
  const player = new LivePlayer(() => context);

  await player.start(session(track('a', 440, 0.7), track('b', 660, 0.7)));
  assert.equal(player.voices.get('a').gain.gain.targets.at(-1).value, 0.5);
  assert.equal(player.voices.get('b').gain.gain.targets.at(-1).value, 0.5);

  player.setVolume(0.8);
  assert.equal(player.volume, 0.8);
  assert.deepEqual(context.gains[0].gain.targets.at(-1), {
    value: 0.8,
    time: context.currentTime,
    constant: 0.01,
  });
  player.setVolume(1.1);
  assert.equal(player.volume, 0.8);
  assert.equal(context.gains[0].gain.targets.length, 1);
  player.stop();
});

test('rejects a frequency above the actual context Nyquist limit before changing voices', async () => {
  const context = new FakeAudioContext({ sampleRate: 48000 });
  const player = new LivePlayer(() => context);
  await player.start(session(track('a', 440, 0.4)));
  const voice = player.voices.get('a');
  const frequencyTargets = voice.osc.frequency.targets.length;
  const gainTargets = voice.gain.gain.targets.length;

  assert.throws(() => player.update({
    sample_rate: 96000,
    tracks: [track('a', 880, 0.9), track('b', 24000, 0.2)],
  }), /below 24000 Hz/);
  assert.equal(player.voices.size, 1);
  assert.equal(player.voices.get('a'), voice);
  assert.equal(voice.osc.frequency.targets.length, frequencyTargets);
  assert.equal(voice.gain.gain.targets.length, gainTargets);
  assert.equal(context.oscillators.length, 1);
  player.stop();
});

test('stopping while resume is pending prevents a late start from creating voices', async () => {
  let resolveResume;
  const context = new FakeAudioContext({
    resume: () => new Promise(resolve => { resolveResume = resolve; }),
  });
  const player = new LivePlayer(() => context);
  const starting = player.start(session(track('a', 440, 0.4)));

  player.stop();
  resolveResume();
  assert.equal(await starting, false);
  assert.equal(player.context, null);
  assert.equal(player.voices.size, 0);
  assert.equal(context.oscillators.length, 0);
  assert.equal(context.closeCount, 1);
});

test('a rejected resume tears down the context and propagates the failure', async () => {
  const failure = new Error('resume failed');
  const context = new FakeAudioContext({ resume: () => Promise.reject(failure) });
  const player = new LivePlayer(() => context);

  await assert.rejects(player.start(session(track('a', 440, 0.4))), error => error === failure);
  assert.equal(player.context, null);
  assert.equal(player.voices.size, 0);
  assert.equal(context.closeCount, 1);
  assert.equal(context.gains[0].disconnectCount, 1);
  assert.equal(context.analysers[0].disconnectCount, 1);
  assert.equal(context.oscillators.length, 0);
});

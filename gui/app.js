(() => {
  'use strict';

  const token = window.DAW_TOKEN;
  const DEFAULT_SESSION = { schema_version: 1, sample_rate: 48000, tracks: [] };
  let applied = null;
  let draft = structuredClone(DEFAULT_SESSION);
  let busy = false;
  let unsupportedSession = false;

  const $ = (selector) => document.querySelector(selector);
  const tracksEl = $('#tracks');
  const emptyEl = $('#empty-state');
  const noticeEl = $('#notice');
  const applyButton = $('#apply-button');
  const saveButton = $('#save-button');
  const loadButton = $('#load-button');
  const renderButton = $('#render-button');
  const rateSelect = $('#sample-rate');
  const durationInput = $('#duration');
  const fileInput = $('#session-file');
  const player = new LivePlayer();
  let starting = false;
  let playGeneration = 0;
  let nativeAvailable = false;
  let nativePluginsAvailable = false;
  let offlinePluginsAvailable = false;
  let effectCatalog = [];
  let nativeSnapshot = { state: 'stopped' };
  let nativeCommandGeneration = 0;
  let nativeCommandTail = Promise.resolve();
  let nativePlayGeneration = 0;
  let nativePollInFlight = false;
  let nativePollFailures = 0;
  let nativeCommandsPending = 0;
  let nativeModeChange = false;
  let nativeVolumeTimer = null;
  const outputMode = $('#output-mode');
  const playButton = $('#play-button');
  let paused = false;
  let holdTimer = null;
  let held = false;
  const playState = $('#play-state');

  function nativeActive() {
    return ['starting', 'playing', 'paused'].includes(nativeSnapshot.state);
  }

  function nativeLocked() {
    return nativeActive();
  }

  function nativeStatusText(snapshot) {
    if (snapshot.state === 'starting') return 'Starting native audio…';
    if (snapshot.state === 'playing') {
      const device = snapshot.device ? ` · ${snapshot.device}` : '';
      const rate = snapshot.sample_rate ? ` · ${(snapshot.sample_rate / 1000).toLocaleString()} kHz` : '';
      return `Playing${device}${rate}`;
    }
    if (snapshot.state === 'paused') return 'Paused';
    if (snapshot.state === 'error') return 'Native audio error';
    return 'Stopped';
  }

  function applyNativeSnapshot(snapshot) {
    if (!snapshot || !['stopped', 'starting', 'playing', 'paused', 'error'].includes(snapshot.state)) {
      throw new Error('The server returned an invalid native transport status.');
    }
    if (['starting', 'playing', 'paused'].includes(snapshot.state)) {
      if (player.context || starting) stopLive();
      outputMode.value = 'native';
    }
    nativeSnapshot = snapshot;
    if (outputMode.value === 'native') {
      playState.textContent = nativeStatusText(snapshot);
      $('#output-level').value = Number.isFinite(snapshot.level) ? Math.max(0, Math.min(1, snapshot.level)) : 0;
      if (snapshot.state === 'error') setNotice(snapshot.error || 'Native audio stopped with an error.', true);
    }
    syncStatus();
  }

  async function nativeCommand(payload) {
    const generation = ++nativeCommandGeneration;
    nativeCommandsPending += 1;
    const command = nativeCommandTail.then(async () => {
      const response = await request('/api/transport', { method: 'POST', body: JSON.stringify(payload) });
      return response.json();
    });
    nativeCommandTail = command.catch(() => {});
    try {
      const snapshot = await command;
      if (generation === nativeCommandGeneration) applyNativeSnapshot(snapshot);
      return generation === nativeCommandGeneration;
    } finally { nativeCommandsPending -= 1; }
  }

  async function stopNative() {
    if (!nativeAvailable) return false;
    nativePlayGeneration += 1;
    clearTimeout(nativeVolumeTimer);
    try {
      await nativeCommand({ action: 'stop' });
      return nativeSnapshot.state === 'stopped';
    } catch (error) {
      announceError(`Could not stop native audio. ${error.message}`);
      return false;
    }
  }

  async function loadCapabilities() {
    try {
      const response = await request('/api/capabilities');
      const capabilities = await response.json();
      nativeAvailable = capabilities.live_audio === true;
      nativePluginsAvailable = capabilities.plugin_hosting === true;
      offlinePluginsAvailable = capabilities.offline_vst3?.implemented === true;
      const option = outputMode.querySelector('option[value="native"]');
      option.disabled = !nativeAvailable;
      $('#native-build-hint').hidden = nativeAvailable;
      if (nativeAvailable) {
        const transportResponse = await request('/api/transport');
        applyNativeSnapshot(await transportResponse.json());
        if (nativeActive()) outputMode.value = 'native';
      }
      configureSessionMode();
      syncStatus();
    } catch (error) {
      nativeAvailable = false;
      outputMode.querySelector('option[value="native"]').disabled = true;
      $('#native-build-hint').hidden = false;
      setNotice(`Native output is unavailable. Build with the native-audio feature to enable it. ${error.message}`);
    }
  }

  function stopLive() {
    playGeneration += 1;
    starting = false;
    paused = false;
    player.stop();
    playState.textContent = 'Stopped';
    setNotice('Playback stopped.');
    $('#output-level').value = 0;
    syncStatus();
  }

  function updateLive() {
    if (!player.context || starting) return;
    if (draft.schema_version !== 1) { stopLive(); return; }
    try {
      const error = validateSession(draft);
      if (error) throw new Error(error);
      player.update(draft);
    } catch (error) {
      stopLive();
      setNotice(`Playback stopped. ${error.message}`, true);
    }
  }

  async function playLive() {
    if (draft.schema_version !== 1) { announceError('Effects require native audio output.'); return; }
    if (starting || (player.context && !paused) || busy) return;
    const error = validateSession(draft);
    if (error) { announceError(error); return; }
    if (!draft.tracks.length) { setNotice('Add a sine track, then press Play.'); return; }
    starting = true;
    const generation = ++playGeneration;
    playState.textContent = 'Starting…';
    syncStatus();
    const timeout = setTimeout(() => {
      if (starting && generation === playGeneration) {
        stopLive();
        setNotice('Audio did not start. Press Play again or try your system browser.', true);
      }
    }, 5000);
    try {
      const started = paused ? await player.resume() : await player.start(draft);
      if (!started || generation !== playGeneration) return;
      starting = false;
      paused = false;
      updateLive();
      syncStatus();
      if (player.context) {
        playState.textContent = `Playing · ${player.context.sampleRate / 1000} kHz`;
        setNotice('Playing live. Frequency and gain edits are heard immediately.');
      }
    } catch (error) {
      if (generation === playGeneration) {
        stopLive();
        setNotice(`Could not start audio. ${error.message}`, true);
      }
    } finally { clearTimeout(timeout); }
  }

  setInterval(() => {
    if (outputMode.value === 'browser') $('#output-level').value = player.level();
    if (player.context && !starting && !paused && player.context.state !== 'running') {
      stopLive();
      setNotice('Browser audio was interrupted. Press Play to resume.');
    }
  }, 100);

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function isDirty() {
    return !unsupportedSession && (!applied || JSON.stringify(draft) !== JSON.stringify(applied));
  }

  function setNotice(message, isError = false) {
    noticeEl.textContent = message;
    noticeEl.classList.toggle('error', isError);
    noticeEl.hidden = !message;
  }

  function setBusy(value) {
    busy = value;
    document.querySelectorAll('button, input, select').forEach((control) => {
      if (control === fileInput || control === playButton || control.id === 'monitor-volume' || control === outputMode) return;
      control.disabled = value;
    });
    syncStatus();
  }

  function syncStatus(error = false) {
    const saveState = $('#save-state');
    const text = $('#save-state-text');
    const dirty = isDirty();
    saveState.classList.toggle('dirty', dirty && !error);
    saveState.classList.toggle('error', error);
    text.textContent = unsupportedSession ? 'Timeline/effects · scripting only' : error ? 'Apply failed' : dirty ? 'Unapplied changes' : 'All changes applied';
    const locked = nativeLocked();
    applyButton.disabled = unsupportedSession || busy || locked || !dirty;
    saveButton.disabled = unsupportedSession || busy || locked;
    loadButton.disabled = unsupportedSession || busy || locked;
    fileInput.disabled = unsupportedSession || busy || locked;
    renderButton.disabled = unsupportedSession || busy || locked;
    outputMode.disabled = unsupportedSession || busy || nativeModeChange;
    playButton.disabled = nativeModeChange || (unsupportedSession && !nativeActive()) || (busy && !player.context && !starting && !nativeActive());
    const nativeSelected = outputMode.value === 'native';
    playButton.textContent = unsupportedSession && nativeActive() ? 'Stop' : nativeSelected
      ? nativeSnapshot.state === 'starting' ? 'Stop' : nativeSnapshot.state === 'playing' ? 'Pause' : 'Play'
      : starting ? 'Stop' : !player.context ? 'Play' : paused ? 'Play' : 'Pause';
    playButton.title = 'Click to play or pause. Hold to stop. Escape also stops.';
    const add = $('#add-track-button');
    const emptyAdd = $('#empty-add-button');
    if (add) add.disabled = unsupportedSession || busy || locked || draft.tracks.length >= 64;
    if (emptyAdd) emptyAdd.disabled = unsupportedSession || busy || locked || draft.tracks.length >= 64;
    tracksEl.querySelectorAll('button, input').forEach(control => { control.disabled = unsupportedSession || busy || locked; });
    rateSelect.disabled = unsupportedSession || busy || locked || SessionEditor.plugins(draft).length > 0;
    if (draft.schema_version === 4) {
      outputMode.querySelector('option[value="browser"]').disabled = true;
      playButton.disabled ||= !nativeAvailable || (SessionEditor.plugins(draft).length > 0 && !nativePluginsAvailable);
    }
    renderButton.disabled ||= SessionEditor.plugins(draft).length > 0 && !offlinePluginsAvailable;
    tracksEl.querySelectorAll('select').forEach(control => { control.disabled = unsupportedSession || busy || locked; });
    tracksEl.querySelectorAll('.add-vst3').forEach(control => { control.disabled ||= !effectCatalog.length || !offlinePluginsAvailable; });
  }

  function announceError(message) {
    setNotice(message, true);
    syncStatus(true);
  }

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (token) headers.set('X-DAW-Token', token);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch (error) {
      throw new Error('Could not reach the local DAW server. Check that the studio is running and try again.');
    }
    if (!response.ok) {
      let message = `The server returned an error (${response.status}).`;
      try {
        const body = await response.json();
        if (typeof body.error === 'string' && body.error.trim()) message = body.error;
      } catch (_) { /* Keep the status message for non-JSON responses. */ }
      throw new Error(message);
    }
    return response;
  }

  function validateSession(session) { return SessionEditor.validate(session); }

  function rememberEffects() {
    for (const effect of SessionEditor.plugins(applied)) {
      const index = effectCatalog.findIndex(item => item.bundle_path === effect.bundle_path && item.class_id === effect.class_id && item.id === effect.id);
      if (index >= 0) effectCatalog[index] = clone(effect);
      else { effectCatalog.push(clone(effect)); if (effectCatalog.length > 64) effectCatalog.shift(); }
    }
  }

  function configureSessionMode() {
    const effectsMode = draft.schema_version === 4;
    outputMode.querySelector('option[value="browser"]').disabled = effectsMode;
    if (effectsMode && nativeAvailable) outputMode.value = 'native';
    durationInput.max = SessionEditor.plugins(draft).length ? '10' : '60';
    rateSelect.disabled = effectsMode && SessionEditor.plugins(draft).length > 0;
    $('#effects-hint').textContent = effectsMode && SessionEditor.plugins(draft).length && !nativePluginsAvailable ? 'Live VST3 requires a vst3-live build. Offline rendering requires vst3-offline. Saved automation points are preserved.' : effectsMode ? 'Effects use native audio. Stop before editing. Saved automation is preserved; its points cannot be edited here.' : 'Effects use native playback. Add gain, or load a saved VST3 session to reuse its validated effects.';
  }

  function makeId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') return `tone-${window.crypto.randomUUID()}`;
    return `tone-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function selectSampleRate(rate) {
    const value = String(rate);
    if (![...rateSelect.options].some((option) => option.value === value)) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${(rate / 1000).toLocaleString(undefined, { maximumFractionDigits: 3 })} kHz`;
      rateSelect.add(option);
    }
    rateSelect.value = value;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeRange(min, max, step, value, label) {
    const input = element('input');
    input.type = 'range';
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    input.value = String(value);
    input.setAttribute('aria-label', label);
    return input;
  }

  function makeTrackCard(track, index) {
    const card = element('article', 'track-card');
    card.setAttribute('aria-label', `Sine track ${index + 1}`);

    const ident = element('div', 'track-ident');
    const icon = element('div', 'track-icon');
    icon.setAttribute('aria-hidden', 'true');
    const miniWave = element('span', 'wave-mini');
    for (let i = 0; i < 5; i++) miniWave.append(element('i'));
    icon.append(miniWave);
    const title = element('div', 'track-title');
    title.append(element('h2', '', `Sine ${String(index + 1).padStart(2, '0')}`), element('p', '', 'Sine oscillator'));
    ident.append(icon, title, element('span', 'track-index', String(index + 1).padStart(2, '0')));

    const frequency = element('div', 'frequency-control');
    frequency.dataset.field = 'frequency';
    const frequencyLabel = element('div', 'control-label');
    const frequencyLabelText = element('label', '', 'Frequency');
    const frequencyValue = element('output', 'control-value', `${formatFrequency(track.device.frequency_hz)} Hz`);
    frequencyLabel.append(frequencyLabelText, frequencyValue);
    const frequencyRow = element('div', 'control-row');
    const maxFrequency = Math.max(1, Math.floor(draft.sample_rate / 2) - 1);
    frequencyRow.append(element('span', 'range-end', '1'), makeRange(1, maxFrequency, 1, track.device.frequency_hz, `Sine ${index + 1} frequency in hertz`), element('span', 'range-end', `${maxFrequency}`));
    const frequencyRange = frequencyRow.querySelector('input');
    frequencyRange.addEventListener('input', () => {
      const value = Number(frequencyRange.value);
      draft.tracks[index].device.frequency_hz = value;
      frequencyValue.textContent = `${formatFrequency(value)} Hz`;
      const number = frequency.querySelector('.number-input');
      if (number && document.activeElement !== number) number.value = String(value);
      markEdited();
    });
    const frequencyNumber = element('input', 'number-input');
    frequencyNumber.type = 'number';
    frequencyNumber.min = '0.001';
    frequencyNumber.max = String(maxFrequency);
    frequencyNumber.step = 'any';
    frequencyNumber.value = String(track.device.frequency_hz);
    frequencyNumber.setAttribute('aria-label', `Sine ${index + 1} frequency in hertz`);
    frequencyNumber.addEventListener('input', () => {
      if (frequencyNumber.value === '') {
        frequencyNumber.setCustomValidity('Enter a frequency in hertz.');
        draft.tracks[index].device.frequency_hz = NaN;
        markEdited();
        return;
      }
      frequencyNumber.setCustomValidity('');
      const value = Number(frequencyNumber.value);
      draft.tracks[index].device.frequency_hz = value;
      frequencyRange.value = String(Math.max(1, Math.min(maxFrequency, value)));
      frequencyValue.textContent = `${formatFrequency(value)} Hz`;
      markEdited();
    });
    frequency.append(frequencyLabel, frequencyRow, frequencyNumber);

    const gain = element('div', 'gain-control');
    gain.dataset.field = 'gain';
    const gainLabel = element('div', 'control-label');
    gainLabel.append(element('label', '', 'Gain'), element('output', 'gain-output', formatGain(track.device.gain)));
    const gainRow = element('div', 'gain-row');
    const gainRange = makeRange(0, 1, 0.01, track.device.gain, `Sine ${index + 1} gain from 0 to 1`);
    gainRange.addEventListener('input', () => {
      const value = Number(gainRange.value);
      draft.tracks[index].device.gain = value;
      gainLabel.querySelector('output').textContent = formatGain(value);
      markEdited();
    });
    gainRow.append(element('span', 'range-end', '0'), gainRange, element('span', 'range-end', '1'));
    gain.append(gainLabel, gainRow);

    const remove = element('button', 'remove-button', '×');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remove sine track ${index + 1}`);
    remove.addEventListener('click', () => {
      draft.tracks.splice(index, 1);
      renderTracks();
      markEdited();
    });

    card.append(ident, frequency, gain, remove, makeEffectPanel(track, index));
    return card;
  }

  function makeEffectPanel(track, index) {
    const panel = element('section', 'effect-panel');
    panel.setAttribute('aria-label', `Sine ${index + 1} effects`);
    const heading = element('div', 'effect-heading');
    heading.append(element('h3', '', 'Effects'), element('span', 'output-hint', 'Applied in order'));
    panel.append(heading);
    for (const effect of track.effects || []) {
      const row = element('div', 'effect-row');
      const label = effect.kind === 'vst3' ? effect.bundle_path.split('/').pop().replace(/\.vst3$/, '') : 'Gain';
      row.append(element('strong', '', label));
      const bypassLabel = element('label', 'bypass-control', 'Bypass');
      const bypass = element('input'); bypass.type = 'checkbox'; bypass.checked = effect.bypass;
      bypass.setAttribute('aria-label', `${label} bypass on Sine ${index + 1}`);
      bypass.addEventListener('change', () => { effect.bypass = bypass.checked; markEdited(); });
      bypassLabel.prepend(bypass); row.append(bypassLabel);
      const remove = element('button', 'button button-quiet', 'Remove'); remove.type = 'button';
      remove.setAttribute('aria-label', `Remove ${label} from Sine ${index + 1}`);
      remove.addEventListener('click', () => { draft = SessionEditor.removeEffect(draft, index, effect.id); renderTracks(); markEdited(); });
      row.append(remove);
      const params = effect.kind === 'gain' ? [{id: 'gain', value: effect.gain, points: (track.automation || []).find(lane => lane.effect_id === effect.id)?.points || []}] : effect.parameters;
      for (const param of params) {
        const control = element('label', 'effect-parameter');
        control.append(element('span', '', effect.kind === 'gain' ? 'Gain' : `Parameter ${param.id}`));
        const value = element('output', '', Number(param.value).toFixed(3));
        const range = makeRange(0, effect.kind === 'gain' ? 4 : 1, 0.001, param.value, `${label} ${effect.kind === 'gain' ? 'gain' : `parameter ${param.id}`} on Sine ${index + 1}`);
        range.addEventListener('input', () => { const number = Number(range.value); if (effect.kind === 'gain') effect.gain = number; else param.value = number; value.textContent = number.toFixed(3); markEdited(); });
        control.append(range, value); row.append(control);
        if (param.points.length) row.append(element('p', 'output-hint', `${param.points.length} saved automation points override this base value during playback.`));
      }
      if (effect.kind === 'vst3') {
        const details = element('details', 'effect-identity');
        details.append(element('summary', '', 'Saved plugin identity'), element('p', '', `${effect.bundle_path} · ${effect.class_id}`));
        row.append(details);
      }
      panel.append(row);
    }
    const actions = element('div', 'effect-actions');
    const gain = element('button', 'button button-quiet', 'Add gain'); gain.type = 'button';
    const add = effect => {
      try {
        if (player.context || starting) stopLive();
        draft = SessionEditor.addEffect(draft, index, effect, makeId());
        configureSessionMode(); renderTracks(); markEdited();
      } catch (error) { announceError(error.message); }
    };
    gain.addEventListener('click', () => add({kind: 'gain', gain: 1, bypass: false}));
    const select = element('select', 'effect-select'); select.setAttribute('aria-label', `Saved VST3 effect for Sine ${index + 1}`);
    if (!effectCatalog.length) { const option = element('option', '', 'Load a saved VST3 session first'); option.value = ''; select.append(option); }
    effectCatalog.forEach((effect, i) => { const option = element('option', '', `${effect.bundle_path.split('/').pop().replace(/\.vst3$/, '')} · ${effect.id}`); option.value = String(i); select.append(option); });
    const vst = element('button', 'button button-quiet add-vst3', 'Add VST3'); vst.type = 'button';
    vst.addEventListener('click', () => { const effect = effectCatalog[Number(select.value)]; if (effect) add(effect); });
    actions.append(gain, select, vst); panel.append(actions);
    return panel;
  }

  function formatFrequency(value) {
    return Number.isInteger(value) ? String(value) : Number(value.toFixed(2)).toString();
  }

  function formatGain(value) {
    return Number(value).toFixed(2);
  }

  function renderTracks() {
    configureSessionMode();
    tracksEl.replaceChildren(...draft.tracks.map((track, index) => makeTrackCard(track, index)));
    const empty = draft.tracks.length === 0;
    emptyEl.hidden = !empty;
    tracksEl.hidden = empty;
    $('#track-count').textContent = `${draft.tracks.length} ${draft.tracks.length === 1 ? 'track' : 'tracks'}`;
    syncStatus();
  }

  function markEdited() {
    setNotice('');
    syncStatus();
    updateLive();
  }

  function addTrack() {
    if (busy || draft.tracks.length >= 64) return;
    const track = { id: makeId(), device: { kind: 'sine', frequency_hz: 440, gain: 0.15 } };
    if (draft.schema_version === 4) Object.assign(track, {mode: 'continuous', clips: [], effects: []});
    draft.tracks.push(track);
    renderTracks();
    markEdited();
  }

  function updateDraftFromControls() {
    const sampleRate = Number(rateSelect.value);
    draft.sample_rate = sampleRate;
    for (const input of document.querySelectorAll('.number-input')) {
      const card = input.closest('.track-card');
      const index = Array.from(tracksEl.children).indexOf(card);
      if (index >= 0 && input.value.trim()) draft.tracks[index].device.frequency_hz = Number(input.value);
    }
  }

  async function applyDraft() {
    if ([...document.querySelectorAll('.number-input')].some((input) => input.value.trim() === '')) {
      announceError('Enter a frequency for every track before applying changes.');
      return false;
    }
    updateDraftFromControls();
    const validation = validateSession(draft);
    if (validation) {
      announceError(validation);
      return false;
    }
    if (!isDirty()) return true;
    setBusy(true);
    setNotice('Applying session changes…');
    try {
      const response = await request('/api/session', { method: 'POST', body: JSON.stringify({ session: draft }) });
      const result = await response.json();
      const session = result.session || result;
      const error = validateSession(session);
      if (error) throw new Error(`The server returned an invalid session: ${error}`);
      applied = clone(session);
      draft = clone(session);
      rememberEffects();
      configureSessionMode();
      selectSampleRate(draft.sample_rate);
      renderTracks();
      setNotice('Session changes applied.');
      return true;
    } catch (error) {
      announceError(`Could not apply the session. ${error.message}`);
      return false;
    } finally {
      setBusy(false);
      syncStatus();
    }
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const anchor = element('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function timestamp() {
    return new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', 'Z');
  }

  async function saveSession() {
    setNotice('');
    const ok = await applyDraft();
    if (!ok) return;
    const blob = new Blob([`${JSON.stringify(applied, null, 2)}\n`], { type: 'application/json' });
    download(blob, `daw-session-${timestamp()}.json`);
    setNotice('Validated session downloaded as JSON.');
  }

  async function loadSession(file) {
    if (!file) return;
    if (isDirty() && !window.confirm('You have unapplied edits. Loading a session will discard them. Continue?')) {
      fileInput.value = '';
      return;
    }
    stopLive();
    setBusy(true);
    setNotice('Reading and validating session…');
    try {
      if (file.size > 1024 * 1024) throw new Error('Session files must be no larger than 1 MiB.');
      let parsed;
      try { parsed = JSON.parse(await file.text()); }
      catch (_) { throw new Error('The selected file is not valid JSON.'); }
      const validation = validateSession(parsed);
      if (validation) throw new Error(validation);
      const response = await request('/api/session', { method: 'POST', body: JSON.stringify({ session: parsed }) });
      const result = await response.json();
      const session = result.session || result;
      const serverValidation = validateSession(session);
      if (serverValidation) throw new Error(`The server returned an invalid session: ${serverValidation}`);
      applied = clone(session);
      draft = clone(session);
      rememberEffects();
      configureSessionMode();
      selectSampleRate(draft.sample_rate);
      renderTracks();
      setNotice(`Loaded ${file.name} and applied it to the session.`);
    } catch (error) {
      announceError(`Could not load session. ${error.message}`);
    } finally {
      fileInput.value = '';
      setBusy(false);
      syncStatus();
    }
  }

  async function renderAudio() {
    setNotice('');
    const seconds = Number(durationInput.value);
    const maxSeconds = SessionEditor.plugins(draft).length ? 10 : 60;
    if (!Number.isFinite(seconds) || seconds < 0.001 || seconds > maxSeconds) {
      durationInput.setCustomValidity(`Duration must be between 0.001 and ${maxSeconds} seconds.`);
      durationInput.reportValidity();
      announceError(`Duration must be between 0.001 and ${maxSeconds} seconds.`);
      return;
    }
    durationInput.setCustomValidity('');
    const ok = await applyDraft();
    if (!ok) return;
    setBusy(true);
    setNotice('Rendering WAV…');
    try {
      const response = await request('/api/render', { method: 'POST', body: JSON.stringify({ seconds }) });
      const blob = await response.blob();
      if (!blob.size) throw new Error('The server returned an empty WAV file.');
      const clipped = response.headers.get('X-Clipped-Frames');
      download(blob, `daw-render-${timestamp()}.wav`);
      setNotice(clipped && Number(clipped) > 0 ? `WAV rendered and downloaded. ${clipped} frames were clipped.` : 'WAV rendered and downloaded.');
    } catch (error) {
      announceError(`Could not render audio. ${error.message}`);
    } finally {
      setBusy(false);
      syncStatus();
    }
  }

  async function toggleNative() {
    if (!nativeAvailable || busy || nativeModeChange) return;
    if (nativeSnapshot.state === 'starting') {
      await stopNative();
      return;
    }
    if (nativeSnapshot.state === 'playing') {
      try { await nativeCommand({ action: 'pause' }); }
      catch (error) { announceError(`Could not pause native audio. ${error.message}`); }
      return;
    }
    if (nativeSnapshot.state === 'paused') {
      try { await nativeCommand({ action: 'resume' }); }
      catch (error) { announceError(`Could not resume native audio. ${error.message}`); }
      return;
    }

    const startGeneration = ++nativePlayGeneration;
    const appliedOk = await applyDraft();
    if (startGeneration !== nativePlayGeneration || outputMode.value !== 'native') return;
    if (!appliedOk) return;
    // Invalidate a pending browser start before releasing its stream.
    playGeneration += 1;
    starting = false;
    paused = false;
    player.stop();
    $('#output-level').value = 0;
    if (!draft.tracks.length) { setNotice('Add a sine track, then press Play.'); return; }
    nativeSnapshot = { state: 'starting' };
    playState.textContent = 'Starting native audio…';
    syncStatus();
    try {
      await nativeCommand({ action: 'play', seconds: 60, volume: Number($('#monitor-volume').value) });
    } catch (error) {
      nativeSnapshot = { state: 'error', error: error.message };
      applyNativeSnapshot(nativeSnapshot);
    }
  }

  async function changeOutputMode() {
    nativePlayGeneration += 1;
    if (outputMode.value === 'native') {
      if (!nativeAvailable) {
        outputMode.value = 'browser';
        return;
      }
      // Switching away from a running browser stream is safe and synchronous.
      if (player.context || starting) stopLive();
      applyNativeSnapshot(nativeSnapshot);
      syncStatus();
      return;
    }
    if (nativeAvailable) {
      clearTimeout(nativeVolumeTimer);
      nativeModeChange = true;
      syncStatus();
      const stopped = await stopNative();
      nativeModeChange = false;
      if (!stopped) {
        outputMode.value = 'native';
        syncStatus();
        return;
      }
    }
    playState.textContent = 'Stopped';
    $('#output-level').value = 0;
    syncStatus();
  }

  async function loadCurrentSession() {
    setBusy(true);
    setNotice('Connecting to the studio…');
    try {
      const response = await request('/api/session', { method: 'GET' });
      const result = await response.json();
      const session = result.session || result;
      if (session && !SessionEditor.supported(session)) {
        unsupportedSession = true;
        emptyEl.hidden = true;
        $('#track-count').textContent = `${session.tracks.length} ${session.tracks.length === 1 ? "track" : "tracks"} · read-only`;
        selectSampleRate(session.sample_rate);
        setNotice('This session uses a timeline or effect format. Use the scripting interface; this editor supports continuous sine sessions only.', true);
        return;
      }
      const validation = validateSession(session);
      if (validation) throw new Error(`The server returned an invalid session: ${validation}`);
      applied = clone(session);
      draft = clone(session);
      rememberEffects();
      configureSessionMode();
      selectSampleRate(draft.sample_rate);
      renderTracks();
      setNotice('Connected. Your session is ready to edit.');
    } catch (error) {
      announceError(`Could not load the current session. ${error.message}`);
    } finally {
      setBusy(false);
      syncStatus();
    }
  }

  $('#add-track-button').addEventListener('click', addTrack);
  async function toggleLive() {
    if (starting) { stopLive(); return; }
    if (!player.context || paused) { await playLive(); return; }
    const generation = ++playGeneration;
    starting = true;
    syncStatus();
    try {
      const retained = await player.pause();
      if (!retained || generation !== playGeneration) return;
      paused = true;
      starting = false;
      playState.textContent = 'Paused';
      setNotice('Playback paused. Press Play to resume, or hold the button to stop.');
      $('#output-level').value = 0;
      syncStatus();
    } catch (error) {
      if (generation === playGeneration) {
        stopLive();
        setNotice(`Could not pause audio. ${error.message}`, true);
      }
    }
  }
  playButton.addEventListener('click', () => {
    if (held) { held = false; return; }
    if (unsupportedSession) { if (nativeActive()) void stopNative(); return; }
    if (outputMode.value === 'native') void toggleNative();
    else void toggleLive();
  });
  playButton.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    held = false;
    playButton.setPointerCapture(event.pointerId);
    holdTimer = setTimeout(() => {
      held = true;
      if (outputMode.value === 'native') void stopNative();
      else stopLive();
    }, 600);
  });
  const cancelHold = () => { clearTimeout(holdTimer); holdTimer = null; };
  playButton.addEventListener('pointerup', cancelHold);
  playButton.addEventListener('pointercancel', cancelHold);
  playButton.addEventListener('lostpointercapture', cancelHold);
  $('#monitor-volume').addEventListener('input', event => {
    const volume = Number(event.target.value);
    player.setVolume(volume);
    if (outputMode.value === 'native' && nativeAvailable && nativeActive()) {
      clearTimeout(nativeVolumeTimer);
      nativeVolumeTimer = setTimeout(() => {
        void nativeCommand({ action: 'volume', volume }).catch(error => announceError(`Could not change native listening volume. ${error.message}`));
      }, 60);
    }
  });
  outputMode.addEventListener('change', () => { void changeOutputMode(); });
  window.addEventListener('pagehide', () => {
    stopLive();
    if (nativeAvailable && nativeActive()) {
      const headers = new Headers({ 'Content-Type': 'application/json' });
      if (token) headers.set('X-DAW-Token', token);
      void fetch('/api/transport', { method: 'POST', headers, body: JSON.stringify({ action: 'stop' }), keepalive: true }).catch(() => {});
    }
  });
  document.addEventListener('keydown', event => {
    if (event.code === 'Escape') {
      if (outputMode.value === 'native') void stopNative();
      else stopLive();
    }
  });
  $('#empty-add-button').addEventListener('click', addTrack);
  applyButton.addEventListener('click', () => { void applyDraft(); });
  saveButton.addEventListener('click', () => { void saveSession(); });
  loadButton.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { void loadSession(fileInput.files && fileInput.files[0]); });
  renderButton.addEventListener('click', () => { void renderAudio(); });
  rateSelect.addEventListener('change', () => {
    draft.sample_rate = Number(rateSelect.value);
    for (const input of document.querySelectorAll('.frequency-control input[type="range"]')) {
      const max = Math.max(1, Math.floor(draft.sample_rate / 2) - 1);
      input.max = String(max);
      const end = input.parentElement.lastElementChild;
      if (end) end.textContent = String(max);
    }
    for (const input of document.querySelectorAll('.number-input')) input.max = String(Math.floor(draft.sample_rate / 2) - 1);
    markEdited();
  });
  durationInput.addEventListener('input', () => durationInput.setCustomValidity(''));
  window.addEventListener('beforeunload', (event) => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = '';
  });

  renderTracks();
  void loadCurrentSession();
  void loadCapabilities();
  setInterval(async () => {
    if (!nativeAvailable || nativePollInFlight || nativeCommandsPending || nativeModeChange) return;
    nativePollInFlight = true;
    const generation = nativeCommandGeneration;
    try {
      const response = await request('/api/transport');
      const snapshot = await response.json();
      nativePollFailures = 0;
      if (generation === nativeCommandGeneration && !nativeCommandsPending && !nativeModeChange) applyNativeSnapshot(snapshot);
    } catch (_) {
      nativePollFailures += 1;
      if (nativePollFailures === 3 && nativeActive()) {
        setNotice('Native status is unavailable. Playback may still be active; its 60-second limit still applies. Try Stop or restart the studio.', true);
      }
    } finally { nativePollInFlight = false; }
  }, 300);
})();

(() => {
  'use strict';

  const token = window.DAW_TOKEN;
  const DEFAULT_SESSION = { schema_version: 1, sample_rate: 48000, tracks: [] };
  let applied = null;
  let draft = structuredClone(DEFAULT_SESSION);
  let studioConfig = null, studioPending = false, studioHistory = [], studioAudioUrl = null;
  let studioRecorder = null, studioMicStream = null, studioMicTimer = null, studioMicStarting = false, studioTranscribing = false;
  let busy = false;
  let unsupportedSession = false;
  const editHistory = new SessionHistory();
  let historyAction = false;
  let timelineBusy = false;
  let arrangementView = null;
  let mixerView = null;
  let automationViews = [];
  let automationDraftStates = new Map();
  let appliedGeneration = 0;
  let metadataRequestTail = Promise.resolve();
  let metadataCache = new Map();
  let metadataPending = new Set();
  let metadataSuppressed = false;
  let sessionRevision = null;
  let liveArrangementEditsAvailable = false;
  let liveParameterEditsAvailable = false;
  let liveSourceEditsAvailable = false;
  let csoundLiveEditsAvailable = false;
  let csoundLiveAvailable = false;
  let csoundBridgeAvailable = false;
  let liveSourcesAvailable = false;
  let checkedReplacementAvailable = false;
  let bridgeDiscovered = false;
  let capabilitiesLoading = Promise.resolve();
  let sourceBridgeAvailable = false;
  let audioProjectsAvailable = false;
  let renderLimits = {};
  let audioProjectByteLimit = 128 * 1024 * 1024;
  let untilStoppedDevices = [];
  let sourceMetadata = new Map();
  let sourceUpdateText = null;
  const liveParameterValues = new Map();
  let liveParameterTimer = null;
  let liveParameterQueue = Promise.resolve();
  let liveParameterBusy = false;
  let liveParameterGeneration = 0;
  let liveParameterAppliedRevision = null;
  let liveParameterAppliedFrame = null;
  let liveParameterPendingCount = 0;

  const $ = (selector) => document.querySelector(selector);
  const tracksEl = $('#tracks');
  const emptyEl = $('#empty-state');
  const noticeEl = $('#notice');
  const recoverButton = document.createElement('button');
  recoverButton.type = 'button'; recoverButton.className = 'button button-quiet';
  recoverButton.textContent = 'Reload engine state (discard local edits)'; recoverButton.hidden = true;
  noticeEl.after(recoverButton);
  recoverButton.addEventListener('click', () => { void loadCurrentSession(); });
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
  let browserStreaming = false;
  const streamPlayer = typeof BrowserStream === "undefined" ? null : new BrowserStream(request, {onError:error => { nativeSnapshot = {...streamPlayer.snapshot(), state:"error", error:error.message}; applyNativeSnapshot(nativeSnapshot); }});
  let nativePluginsAvailable = false;
  let untilStoppedAvailable = false;
  let processingAvailable = false;
  let pdInstrumentAvailable = false;
  let notePreviewAvailable = false;
  let noteInput = null;
  let noteProjectGeneration = 0;
  let noteTargetKey = null;
  let stepCapturing = false;
  let noteRecording = null;
  let recordingStarting = false;
  let takeApplying = false;
  let metronomeAvailable = false;
  let offlinePluginsAvailable = false;
  let parameterMetadataAvailable = false;
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

  function editLocked() {
    return nativeLocked() && !(liveArrangementEditsAvailable && nativeSnapshot.live_arrangement_edits === true &&
      ['playing', 'paused'].includes(nativeSnapshot.state) && !noteRecording?.active && !noteRecording?.pending);
  }

  function liveParameterEligible(trackId, effectId, parameterId) {
    if (!liveParameterEditsAvailable || !['playing', 'paused'].includes(nativeSnapshot.state)) return false;
    const track = applied?.tracks?.find(item => item.id === trackId);
    const effect = track?.effects?.find(item => item.id === effectId && item.kind === 'vst3');
    const parameter = effect?.parameters?.find(item => String(item.id) === String(parameterId));
    if (!parameter || parameter.points?.length || effect.bypass) return false;
    const metadata = metadataCache.get(JSON.stringify([trackId, effectId]));
    if (parameterMetadataAvailable) {
      if (!metadata || !Array.isArray(metadata.parameters)) return false;
      const info = metadata.parameters?.find(item => String(item.id) === String(parameterId));
      if (!info || info.automatable !== true || info.read_only === true) return false;
    }
    return true;
  }

  function sourceEligible(trackId, name) {
    const track = (nativeLocked() ? applied : draft)?.tracks?.find(item => item.id === trackId);
    const control = track?.device?.controls?.find(item => item.name === name);
    if (control && track.device.kind === 'csound' && !nativeLocked()) return Array.isArray(control.points) && control.points.length === 0;
    return control && SessionEditor.sourceControlsEditable(control, sourceMetadata.get(trackId), track.device.kind) &&
      (!nativeLocked() || ((track.device.kind === 'csound' ? csoundLiveEditsAvailable : liveSourceEditsAvailable) && nativeSnapshot.source_mode === 'live' && nativeSnapshot.state === 'playing'));
  }

  function canPlaySources() { return SessionEditor.sources(draft).every(track => track.device.kind === 'csound' ? csoundLiveAvailable : liveSourcesAvailable); }

  function hasSources() { return SessionEditor.sources(draft).length > 0; }
  function hasAudio() { return draft.tracks.some(track => track.device.kind === 'audio'); }

  async function inspectCurrentSession() {
    const response = await request('/api/session/inspect');
    const info = await response.json();
    if (typeof info.revision !== 'string' || !info.session) throw new Error('The server returned an invalid session revision.');
    sessionRevision = info.revision;
    return info;
  }

  function nativeStatusText(snapshot) {
    if (snapshot.state === 'starting') return browserStreaming ? 'Starting browser stream…' : 'Starting native audio…';
    if (snapshot.state === 'playing') {
      const device = snapshot.device ? ` · ${snapshot.device}` : '';
      const rate = snapshot.sample_rate ? ` · ${(snapshot.sample_rate / 1000).toLocaleString()} kHz` : '';
      return browserStreaming && snapshot.count_in_remaining_frames > 0 ? 'Buffering…' : snapshot.count_in_remaining_frames > 0 ? `Count-in · ${Math.ceil(snapshot.count_in_remaining_frames / snapshot.sample_rate * (applied?.tempo_milli_bpm || 120000) / 60000)} beats` : `Playing${device}${rate}`;
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
    if (noteRecording?.active && !recordingStarting) {
      if (browserStreaming && snapshot.underruns > 0) noteRecording.cancel('Listening stream underrun. Take discarded; try a more stable connection.');
      if (snapshot.state === 'error') noteRecording.cancel('Native playback failed. Take discarded.');
      else if (snapshot.state === 'stopped') void finishNoteTake(snapshot);
      else if (snapshot.sample_rate) noteRecording.updateTransport({...snapshot, timestamp: performance.now()});
    }
    mixerView?.updateMeters(snapshot.mixer_meters, snapshot.state);
    const sourceUpdate = snapshot.source_control_update;
    if (sourceUpdate) {
      const cancelled = snapshot.state === 'stopped' && (sourceUpdate.pending || (sourceUpdate.applied_revision && !sourceUpdate.callback_observed));
      const text = cancelled ? 'Playback stopped before pending source edits reached the callback. Accepted values remain saved.' : sourceUpdate.pending ? `Source control queued (${sourceUpdate.pending} pending).` : sourceUpdate.applied_revision && sourceUpdate.callback_observed ? `Source control observed by the callback at frame ${sourceUpdate.applied_frame}.` : sourceUpdate.applied_revision ? 'Source control acknowledged; waiting for the callback…' : null;
      if (text && text !== sourceUpdateText) setNotice(text);
      sourceUpdateText = text;
    }
    if (snapshot.error) {
      const message = typeof snapshot.error === 'string' ? snapshot.error : snapshot.error.message;
      setNotice(`Native playback failed. ${message}`, true);
    }
    const update = snapshot.plugin_parameter_update;
    if (update && Number.isInteger(update.pending)) {
      const changed = liveParameterAppliedRevision !== (update.applied_revision ?? null) || liveParameterPendingCount !== update.pending;
      liveParameterAppliedRevision = update.applied_revision ?? null;
      liveParameterAppliedFrame = update.applied_frame ?? null;
      liveParameterPendingCount = update.pending;
      if (changed && update.pending > 0) setNotice(`Plugin parameter update queued (${update.pending} pending).`);
      else if (changed && liveParameterAppliedRevision !== null) setNotice(`Plugin parameter applied at frame ${liveParameterAppliedFrame ?? 'unknown'}.`);
    }
    if (snapshot.state === 'stopped' || snapshot.state === 'error') {
      liveParameterGeneration += 1;
      liveParameterValues.clear();
      clearTimeout(liveParameterTimer);
      liveParameterTimer = null;
    }
    if (snapshot.state === 'stopped' || snapshot.state === 'error') requestAppliedEffectMetadata();
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
      requestAppliedEffectMetadata();
      if (nativeSnapshot.state === 'stopped') stopNoteInput();
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
      if (!capabilities || typeof capabilities !== 'object') throw new Error('Invalid server capabilities.');
      bridgeDiscovered = true;
      renderLimits = capabilities.render || {};
      processingAvailable = capabilities.supported_session_schema_versions?.includes(11) === true;
      pdInstrumentAvailable = capabilities.supported_session_schema_versions?.includes(12) === true;
      untilStoppedAvailable = capabilities.timeline_transport?.until_stopped === true;
      untilStoppedDevices = capabilities.timeline_transport?.until_stopped_devices || [];
      audioProjectsAvailable = capabilities.gui_bridge?.audio_projects === true;
      audioProjectByteLimit = capabilities.gui_bridge?.audio_project_limits?.project_bytes || audioProjectByteLimit;
      checkedReplacementAvailable = capabilities.gui_bridge?.checked_replacement === true;
      notePreviewAvailable = capabilities.note_preview?.implemented === true && capabilities.gui_bridge?.note_preview === true;
      metronomeAvailable = capabilities.timeline_transport?.metronome === true;
      sourceBridgeAvailable = capabilities.gui_bridge?.supercollider_sources === true;
      csoundBridgeAvailable = capabilities.gui_bridge?.csound_sources === true;
      csoundLiveAvailable = csoundBridgeAvailable && capabilities.csound_live_transport?.implemented === true;
      csoundLiveEditsAvailable = csoundBridgeAvailable && capabilities.csound_live_transport?.live_control_edits === true;
      browserStreaming = capabilities.live_audio !== true && capabilities.browser_stream?.implemented === true;
      nativeAvailable = capabilities.live_audio === true || browserStreaming;
      if (browserStreaming) {liveArrangementEditsAvailable = true; untilStoppedAvailable = true; untilStoppedDevices = ['sine','synth','drumkit','audio'];}
      liveArrangementEditsAvailable = browserStreaming || capabilities.live_arrangement_edits?.implemented === true;
      nativePluginsAvailable = capabilities.plugin_hosting === true;
      offlinePluginsAvailable = capabilities.offline_vst3?.implemented === true;
      parameterMetadataAvailable = capabilities.parameter_metadata?.implemented === true;
      liveSourcesAvailable = sourceBridgeAvailable && capabilities.supercollider_live_transport?.implemented === true && capabilities.supercollider_nrt?.configured === true;
      liveSourceEditsAvailable = capabilities.supercollider_live_transport?.live_control_edits === true;
      liveParameterEditsAvailable = capabilities.live_parameter_edits?.implemented === true && capabilities.live_parameter_edits?.automation_override === false;
      const option = outputMode.querySelector('option[value="native"]');
      option.disabled = !nativeAvailable;
      option.textContent = browserStreaming ? 'Browser stream' : 'Native audio';
      $('#native-build-hint').hidden = nativeAvailable;
      if (nativeAvailable) {
        const transportResponse = await request('/api/transport');
        applyNativeSnapshot(await transportResponse.json());
        if (nativeActive()) outputMode.value = 'native';
      }
      configureSessionMode();
      syncStatus();
      requestAppliedEffectMetadata();
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
    stopNoteInput();
    if (draft.schema_version !== 1) { announceError('Effects require native audio output.'); return; }
    if (starting || (player.context && !paused) || busy) return;
    const error = validateSession(draft);
    if (error) { announceError(error); return; }
    if (!draft.tracks.length) { setNotice('Add a note track or open the musical demo, then press Play.'); return; }
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
    recoverButton.hidden = !isError;
    recoverButton.disabled = busy || nativeLocked();
  }

  function setBusy(value) {
    busy = value;
    document.querySelectorAll('button, input, select').forEach((control) => {
      if (control === fileInput || control === playButton || control.id === 'monitor-volume' || control === outputMode) return;
      control.disabled = value;
    });
    syncStatus();
  }

  function invalidateEffectMetadata() {
    appliedGeneration += 1;
    metadataCache = new Map();
    metadataPending = new Set();
    sourceMetadata = new Map();
  }

  function requestAppliedEffectMetadata() {
    requestSourceMetadata();
    if (!parameterMetadataAvailable) return;
    const generation = appliedGeneration;
    const effects = [];
    for (const track of applied?.tracks || []) {
      for (const effect of track.effects || []) {
        if (effect.kind === 'vst3') effects.push({ track_id: track.id, effect_id: effect.id });
      }
    }
    for (const identity of effects) {
      const key = JSON.stringify([identity.track_id, identity.effect_id]);
      const pendingKey = `${generation}:${key}`;
      if (metadataCache.has(key) || metadataPending.has(pendingKey)) continue;
      metadataPending.add(pendingKey);
      const task = metadataRequestTail.then(async () => {
        if (generation !== appliedGeneration || metadataSuppressed || busy || starting || player.context || nativeActive()) return;
        try {
          const response = await request('/api/effect/inspect', { method: 'POST', body: JSON.stringify(identity) });
          const metadata = await response.json();
          if (generation !== appliedGeneration) return;
          metadataCache.set(key, metadata);
          updateEffectMetadata(identity, metadata);
        } catch (error) {
          if (generation === appliedGeneration) {
            metadataCache.set(key, { error: error.message });
            showMetadataError(identity, error.message);
          }
        }
      }).finally(() => metadataPending.delete(pendingKey));
      metadataRequestTail = task.catch(() => {});
    }
  }

  function requestSourceMetadata() {
    if (!sourceBridgeAvailable) return;
    const generation = appliedGeneration;
    for (const track of SessionEditor.sources(applied).filter(track => track.device.kind === 'supercollider')) {
      const pendingKey = `${generation}:source:${track.id}`;
      if (sourceMetadata.has(track.id) || metadataPending.has(pendingKey)) continue;
      metadataPending.add(pendingKey);
      const task = metadataRequestTail.then(async () => {
        if (generation !== appliedGeneration || busy || metadataSuppressed || nativeActive()) return;
        try {
          const response = await request('/api/source/inspect', {method: 'POST', body: JSON.stringify({synthdef_hex: track.device.synthdef_hex})});
          const metadata = await response.json();
          if (generation !== appliedGeneration) return;
          sourceMetadata.set(track.id, metadata);
        } catch (error) {
          if (generation !== appliedGeneration) return;
          sourceMetadata.set(track.id, {error: error.message});
        }
        const card = [...tracksEl.children].find(card => card.dataset.sourceTrackId === track.id);
        if (card) card.replaceWith(makeSourceCard(draft.tracks.find(item => item.id === track.id), draft.tracks.findIndex(item => item.id === track.id)));
        syncStatus();
      }).finally(() => metadataPending.delete(pendingKey));
      metadataRequestTail = task.catch(() => {});
    }
  }

  function findEffectRow(identity) {
    return [...tracksEl.querySelectorAll('.effect-row')].find(row =>
      row.dataset.trackId === identity.track_id && row.dataset.effectId === identity.effect_id);
  }

  function showMetadataError(identity, message) {
    const row = findEffectRow(identity);
    if (!row) return;
    let notice = row.querySelector('.metadata-error');
    if (!notice) { notice = element('p', 'output-hint metadata-error'); row.append(notice); }
    notice.textContent = `Parameter names unavailable. ${message}`;
  }

  function updateEffectMetadata(identity, metadata) {
    const row = findEffectRow(identity);
    if (!row || !Array.isArray(metadata.parameters)) return;
    row.querySelector('.metadata-error')?.remove();
    const byId = new Map(metadata.parameters.map(parameter => [String(parameter.id), parameter]));
    for (const control of row.querySelectorAll('[data-parameter-id]')) {
      const info = byId.get(control.dataset.parameterId);
      if (!info) continue;
      const label = control.querySelector('.parameter-name');
      const name = typeof info.name === 'string' && info.name ? info.name : `Parameter ${info.id}`;
      label.textContent = info.unit ? `${name} (${info.unit})` : name;
      const range = control.querySelector('input[type="range"]');
      range.setAttribute('aria-label', `Sine ${identity.track_id}, ${identity.effect_id}, ${name}${info.unit ? ` in ${info.unit}` : ''}`);
      control.dataset.metadataDisabled = info.automatable !== true || info.read_only === true ? 'true' : 'false';
      range.dataset.metadataDisabled = control.dataset.metadataDisabled;
      const live = liveParameterEligible(identity.track_id, identity.effect_id, control.dataset.parameterId);
      range.disabled = busy || (editLocked() && !live) || control.dataset.metadataDisabled === 'true';
    }
  }

  function syncStatus(error = false) {
    const saveState = $('#save-state');
    const text = $('#save-state-text');
    const dirty = isDirty();
    if ($('#studio-send')) $('#studio-send').disabled = busy || studioPending || !studioConfig?.available || Boolean(studioRecorder) || studioMicStarting || studioTranscribing;
    if ($('#studio-file-action')?.dataset.target) $('#studio-file-action').disabled = busy || Boolean($($('#studio-file-action').dataset.target)?.disabled);
    if ($('#studio-clear')) $('#studio-clear').disabled = studioPending;
    if ($('#studio-mic')) $('#studio-mic').disabled = studioPending || studioTranscribing || !studioConfig?.voice_available;
    if ($('#studio-spoken')) $('#studio-spoken').disabled = !studioConfig?.voice_available;
    saveState.classList.toggle('dirty', dirty && !error);
    saveState.classList.toggle('error', error);
    text.textContent = unsupportedSession ? 'Timeline/effects · scripting only' : error ? 'Apply failed' : dirty ? 'Device/effect drafts · Apply device changes' : 'Applied to engine · download to keep';
    const historyHelp = $('#history-help');
    if (historyHelp) historyHelp.textContent = dirty ? 'Undo/Redo paused: apply device/effect drafts first. Applied edits are in memory; download your project to keep them.' : 'Undo/Redo restores applied edits. Save session downloads the current project; it does not autosave.';
    const locked = editLocked() || Boolean(noteRecording?.pending) || recordingStarting;
    const structuralLocked = nativeLocked() || Boolean(noteRecording?.pending) || recordingStarting;
    applyButton.hidden = !dirty;
    saveState.title = text.textContent;
    applyButton.disabled = unsupportedSession || busy || locked || !dirty;
    saveButton.disabled = unsupportedSession || busy || locked;
    loadButton.disabled = unsupportedSession || busy || structuralLocked;
    fileInput.disabled = unsupportedSession || busy || structuralLocked;
    $('#import-midi-button').disabled = unsupportedSession || busy || structuralLocked || Boolean(player.context) || starting || hasSources() || SessionEditor.plugins(draft).length > 0 || draft.tracks.length >= 64;
    $('#midi-file').disabled = $('#import-midi-button').disabled;
    const audioImport = $('#import-audio-button');
    audioImport.disabled = unsupportedSession || busy || structuralLocked || !audioProjectsAvailable || hasSources() || SessionEditor.plugins(draft).length > 0 || draft.tracks.length >= 64;
    audioImport.title = audioProjectsAvailable ? 'Import WAV into a new audio lane at Insert at (beat).' : 'Restart the local server to enable WAV imports.';
    $('#audio-file').disabled = audioImport.disabled;
    saveButton.textContent = hasAudio() ? 'Save ZIP' : 'Save';
    saveButton.title = hasAudio() ? 'Download session and referenced WAV assets together.' : 'Download session JSON.';
    renderButton.disabled = unsupportedSession || busy || locked;
    outputMode.disabled = unsupportedSession || busy || nativeModeChange;
    playButton.disabled = nativeModeChange || (unsupportedSession && !nativeActive()) || (busy && !player.context && !starting && !nativeActive());
    const nativeSelected = outputMode.value === 'native';
    playButton.textContent = unsupportedSession && nativeActive() ? 'Stop' : nativeSelected
      ? nativeSnapshot.state === 'starting' ? 'Stop' : nativeSnapshot.state === 'playing' ? (nativeSnapshot.source_mode === 'live' ? 'Stop' : 'Pause') : 'Play'
      : starting ? 'Stop' : !player.context ? 'Play' : paused ? 'Play' : 'Pause';
    playButton.title = hasSources() ? 'Click to play or stop live sources. Escape also stops.' : 'Click to play or pause. Hold to stop. Escape also stops.';
    const add = $('#add-track-button');
    const emptyAdd = $('#empty-add-button');
    if (add) add.disabled = unsupportedSession || busy || structuralLocked || draft.tracks.length >= 64;
    recoverButton.disabled = busy || structuralLocked;
    if (emptyAdd) emptyAdd.disabled = unsupportedSession || busy || structuralLocked || hasSources() || SessionEditor.plugins(draft).length > 0 || draft.tracks.length >= 64;
    const addCsound = $('#add-csound-button');
    addCsound.disabled = unsupportedSession || busy || structuralLocked || !csoundBridgeAvailable || draft.tracks.length >= 64 || SessionEditor.plugins(draft).length > 0 || SessionEditor.arrangement(draft);
    addCsound.title = csoundBridgeAvailable ? 'Add a CSD program as a Csound track (format 7).' : 'Restart the local server to enable Csound imports.';
    mixerView?.setState({locked: unsupportedSession || busy || locked || Boolean(player.context) || starting});
    automationViews.forEach(({view}) => view.setState({locked: unsupportedSession || busy || locked || Boolean(player.context) || starting}));
    tracksEl.querySelectorAll('button, input').forEach(control => {
      const row = control.closest('.effect-row');
      const source = control.closest('[data-source-control]');
      const sourceEditable = source && sourceEligible(source.dataset.trackId, source.dataset.sourceControl);
      const live = control.type === 'range' && row && control.closest('[data-parameter-id]') &&
        liveParameterEligible(row.dataset.trackId, row.dataset.effectId, control.closest('[data-parameter-id]').dataset.parameterId);
      control.disabled = unsupportedSession || busy || (source ? !sourceEditable : locked && !live) || control.dataset.metadataDisabled === 'true' || (structuralLocked && control.dataset.structural === 'true');
    });
    rateSelect.disabled = unsupportedSession || busy || structuralLocked || SessionEditor.plugins(draft).length > 0 || hasSources() || SessionEditor.arrangement(draft);
    if (draft.schema_version !== 1) {
      outputMode.querySelector('option[value="browser"]').disabled = true;
      playButton.disabled ||= !nativeAvailable || (SessionEditor.plugins(draft).length > 0 && !nativePluginsAvailable);
    }
    if (hasSources()) playButton.disabled ||= !canPlaySources() || draft.sample_rate !== 48000;
    renderButton.disabled ||= SessionEditor.plugins(draft).length > 0 && !offlinePluginsAvailable;
    tracksEl.querySelectorAll('select').forEach(control => { control.disabled = unsupportedSession || busy || locked; });
    tracksEl.querySelectorAll('.add-processing').forEach(control => { control.disabled ||= !processingAvailable; });
    tracksEl.querySelectorAll('.add-vst3').forEach(control => { control.disabled ||= !effectCatalog.length || !offlinePluginsAvailable || SessionEditor.arrangement(draft); });
    for (const id of ['new-arrangement-button', 'demo-arrangement-button']) {
      const control = document.getElementById(id);
      if (control) control.disabled = busy || structuralLocked || unsupportedSession;
    }
    const noteTrack = $('#add-note-track-button');
    if (noteTrack) noteTrack.disabled = busy || structuralLocked || unsupportedSession || hasSources() || SessionEditor.plugins(draft).length > 0 || draft.tracks.length >= 64;
    if ($('#note-device')) {
      $('#note-device').disabled = noteTrack?.disabled === true;
      const pdOption = $('#note-device option[value="pd_instrument"]');
      if (pdOption) pdOption.disabled = !pdInstrumentAvailable || draft.sample_rate !== 48000;
    }
    $('#import-pd-button').disabled = noteTrack?.disabled === true || !pdInstrumentAvailable || draft.sample_rate !== 48000;
    $('#import-pd-button').title = draft.sample_rate !== 48000 ? 'Pd instruments require a 48 kHz session.' : 'Load a portable Pd Filtered Sine device JSON package.';
    $('#pd-file').disabled = $('#import-pd-button').disabled;
    if ($('#stop-edit-button')) { $('#stop-edit-button').hidden = !locked; $('#stop-edit-button').disabled = busy; }
    if ($('#undo-button')) {
      $('#undo-button').disabled = busy || locked || isDirty() || !editHistory.canUndo;
      $('#undo-button').title = dirty ? 'Apply or revert device/effect drafts to use Undo.' : 'Undo the last applied edit (⌘/Ctrl Z).';
    }
    if ($('#redo-button')) $('#redo-button').disabled = busy || locked || isDirty() || !editHistory.canRedo;
    if ($('#stop-button')) $('#stop-button').disabled = !nativeActive() && !player.context && !starting;
    arrangementView?.updateTransport({...nativeSnapshot, loop_region: nativeActive() ? nativeSnapshot.loop_region : null, locked: busy || locked, pending: timelineBusy || nativeSnapshot.timeline_command_pending === true,
      transportAvailable: nativeAvailable && ['playing', 'paused'].includes(nativeSnapshot.state) && nativeSnapshot.source_mode !== 'live'});
    syncNoteInput();
    syncRecordingControls();
  }

  function noteInputTarget() {
    if (!notePreviewAvailable || !applied || !sessionRevision || unsupportedSession || noteRecording?.pending || recordingStarting || nativeLocked() || player.context || starting || historyAction ||
        ((busy || isDirty()) && !stepCapturing)) return null;
    const selected = arrangementView?.getNoteTarget();
    const track = selected && applied.tracks[selected.trackIndex];
    if (!track || track.id !== selected.trackId || track.mode !== 'sequenced' || !['sine', 'synth', 'drumkit', 'pd_instrument'].includes(track.device.kind)) return null;
    let stepTarget = null;
    if ($('#step-entry-enabled').checked && !stepCapturing && !busy) {
      try { stepTarget = arrangementView.getStepTarget(); } catch (_) { /* The capture reports invalid cursor fields after audition. */ }
    }
    return {key: JSON.stringify([noteProjectGeneration, applied.sample_rate, track.id, selected.clipId, track.device, track.effects, track.automation, track.mixer]),
      track_id: track.id, expected_revision: sessionRevision, drum: track.device.kind === 'drumkit', trackIndex: selected.trackIndex, clipId: selected.clipId,
      stepArmed: $('#step-entry-enabled').checked, stepTarget};
  }

  function syncNoteInput() {
    if (!noteInput) return;
    const performanceTarget = typeof recordingInputTarget === 'function' ? recordingInputTarget() : null;
    const target = performanceTarget || noteInputTarget();
    const key = target?.key || null;
    if (key !== noteTargetKey) { noteInput.reset(); noteTargetKey = key; }
    $('#note-input').hidden = !SessionEditor.arrangement(draft);
    $('#note-input-enabled').disabled = !target;
    $('#step-entry-enabled').disabled = !target || Boolean(performanceTarget);
    arrangementView?.setStepEnabled?.($('#step-entry-enabled').checked);
    $('#note-input-octave').disabled = !target || target.drum;
    $('#note-preview-button').disabled = !target || stepCapturing || Boolean(performanceTarget);
    $('#connect-midi-button').disabled = !notePreviewAvailable || busy || nativeLocked();
    $('#note-preview-button').textContent = target?.drum ? 'Preview kick' : `Preview ${$('#note-input-octave').selectedOptions[0].textContent}`;
    noteInput.setEnabled(Boolean(target && $('#note-input-enabled').checked));
    if (noteInput.enabled) $('#stop-button').disabled = false;
    noteInput.setVolume(Number($('#monitor-volume').value));
    $('#note-input-help').textContent = !notePreviewAvailable ? 'Restart the local server with the current engine to enable note preview.' : !target
      ? 'Select a note clip and stop playback to play or enter notes. Apply pending changes first.'
      : `${target.track_id} · ${target.drum ? 'A / S / D: kick / snare / closed hat.' : 'A W S E D F T G Y H U J K: one chromatic octave.'} Each press previews a ¼-second gate through the saved instrument and effects. Step entry adds a note at Step position and advances after successful application. This uses browser listening audio while the arrangement is stopped.`;
  }

  function stopNoteInput() {
    $('#note-input-enabled').checked = false;
    noteInput?.setEnabled(false);
  }

  function resetNoteProject() {
    noteProjectGeneration += 1;
    stopNoteInput();
    noteInput?.reset();
    $('#step-entry-enabled').checked = false;
    $('#note-input-status').textContent = '';
    $('#note-input-status').classList.remove('error');
    if (!noteRecording?.take) $('#record-notes-status').textContent = '';
  }

  function noteInputError(error) {
    const status = $('#note-input-status');
    status.textContent = error?.message || String(error);
    status.classList.add('error');
  }

  async function captureStepNote(note) {
    if (!$('#step-entry-enabled').checked || (note.target && !note.target.stepArmed)) return;
    if (stepCapturing || busy) { noteInputError('The previous step note is still applying. Press the note again when it has finished.'); return; }
    const current = arrangementView?.getStepTarget();
    const target = note.target?.stepTarget || current;
    if (!target) return;
    if (!current || target.key !== current.key) { noteInputError('The step position changed during preview. Press the note again at the new position.'); return; }
    stepCapturing = true;
    try {
      const accepted = await editArrangement({type: 'addNote', trackIndex: target.trackIndex, clipId: target.clipId,
        note: {start_frame: target.start_frame, duration_frames: target.duration_frames, frequency_hz: note.frequency_hz, velocity: note.velocity}});
      if (accepted) {
        arrangementView.acceptStepAdvance(target);
        $('#note-input-status').classList.remove('error');
        $('#note-input-status').textContent = `Note entered in ${target.clipId}. Undo removes this note.`;
      } else noteInputError('Note entry was rejected. The step cursor has not advanced; see the arrangement error.');
    } finally { stepCapturing = false; syncNoteInput(); }
  }

  function beatControlsEligible() {
    return metronomeAvailable && nativeAvailable && SessionEditor.arrangement(draft) && draft.tracks.every(track =>
      ['sine', 'synth', 'drumkit', 'audio'].includes(track.device.kind) && (track.effects || []).every(effect => ['gain', 'lowpass', 'delay'].includes(effect.kind)));
  }

  function recordingInputTarget() {
    if (!noteRecording?.active || recordingStarting || !nativeActive()) return null;
    const take = noteRecording.take;
    const track = applied?.tracks[take.trackIndex];
    return track ? {key: `record:${noteProjectGeneration}:${take.revision}:${take.clipId}`, track_id: track.id,
      drum: track.device.kind === 'drumkit', localMonitor:browserStreaming} : null;
  }

  function syncRecordingControls() {
    const active = Boolean(noteRecording?.active), pending = Boolean(noteRecording?.pending);
    const eligible = beatControlsEligible();
    $('#beat-controls').hidden = !SessionEditor.arrangement(draft);
    $('#metronome-enabled').disabled = !eligible || nativeLocked() || busy || recordingStarting;
    $('#count-in-bars').disabled = $('#metronome-enabled').disabled;
    $('#beat-controls-help').textContent = eligible ? '4/4 click and count-in for native playback; listening only.' : 'Click/count-in currently require built-in instruments/audio and native output.';
    $('#record-notes-button').title = !noteInputTarget() ? 'Select a note clip and apply pending device changes to record.' : 'Record from song zero; Stop applies one take. Input is not monitored during playback.';
    $('#record-notes-button').disabled = !nativeAvailable || !checkedReplacementAvailable || !noteInputTarget() || nativeLocked() || busy || active || pending || recordingStarting;
    $('#apply-take-button').hidden = !pending;
    $('#apply-take-button').disabled = busy || nativeLocked() || takeApplying;
    $('#discard-take-button').hidden = !pending;
    $('#discard-take-button').disabled = busy || nativeLocked();
    if (active || pending || recordingStarting) outputMode.disabled = true;
    if (active && nativeActive()) {
      arrangementView?.updateTransport({...nativeSnapshot, transportAvailable: false, locked: true});
      playButton.textContent = 'Stop recording';
      $('#note-input-enabled').checked = true;
      $('#note-input-enabled').disabled = true;
      $('#note-input-help').textContent = `${applied.tracks[noteRecording.take.trackIndex].id} · recording held keyboard/MIDI gates into ${noteRecording.take.clipId}. Stop applies the overdub as one undoable take. ${browserStreaming ? 'Local sine tones monitor MIDI/keyboard input; saved instruments play on replay.' : 'Input is not monitored during playback.'} Loop/seek are disabled.`;
    }
    if (pending) { playButton.disabled = true; recoverButton.disabled = true; }
  }

  async function startNoteRecording() {
    const target = noteInputTarget();
    if (!target || busy || nativeLocked() || noteRecording?.take) return;
    try {
      noteInput.reset();
      if (browserStreaming) void noteInput.unlock();
      noteRecording.arm({session: applied, revision: sessionRevision, trackIndex: target.trackIndex, clipId: target.clipId});
      recordingStarting = true;
      $('#step-entry-enabled').checked = false;
      $('#note-input-enabled').checked = true;
      outputMode.value = 'native';
      await toggleNative();
      if (noteRecording.take) noteTargetKey = `record:${noteProjectGeneration}:${noteRecording.take.revision}:${noteRecording.take.clipId}`;
      recordingStarting = false;
      if (!nativeActive() || nativeSnapshot.state === 'error') noteRecording.cancel('Recording could not start. The project is unchanged.');
      else noteRecording.updateTransport({...nativeSnapshot, timestamp: performance.now()});
      syncStatus();
    } catch (error) {
      recordingStarting = false;
      noteRecording.cancel(error.message);
      syncStatus();
    }
  }

  async function finishNoteTake(snapshot) {
    if (!noteRecording?.active) return;
    try {
      noteRecording.finish({session: applied, revision: sessionRevision, transport: snapshot});
      stopNoteInput();
      if (noteRecording.pending) await applyNoteTake();
    } catch (error) { noteInputError(error); }
    syncStatus();
  }

  async function applyNoteTake() {
    if (!noteRecording?.pending || busy || nativeLocked() || takeApplying) return false;
    takeApplying = true;
    const take = noteRecording.pending;
    setBusy(true);
    try {
      if (take.revision !== sessionRevision || isDirty()) throw new Error('Project changed. Discard the take or reload the original project before retrying.');
      const validation = validateSession(take.draft);
      if (validation) throw new Error(validation);
      const response = await request('/api/note/take', {method: 'POST', body: JSON.stringify({session: take.draft, expected_revision: take.revision})});
      const result = await response.json();
      acceptImportedSession(result);
      const count = take.notes.length;
      noteRecording.accept();
      $('#record-notes-status').textContent = `${count} recorded notes applied. Undo removes the whole take.${take.truncated ? ' New gates ending beyond the clip were shortened to its end.' : ''}`;
      return true;
    } catch (error) {
      $('#record-notes-status').textContent = `Take kept for retry. ${error.message}`;
      setNotice(`Recorded take could not be applied. ${error.message}`, true);
      return false;
    } finally { takeApplying = false; setBusy(false); syncStatus(); }
  }

  function announceError(message) {
    setNotice(message, true);
    syncStatus(true);
  }

  async function request(path, options = {}) {
    if (browserStreaming && path.startsWith('/api/transport')) {
      const payload = options.body ? JSON.parse(options.body) : {action:'status'};
      const action = path.endsWith('/seek') ? 'seek' : path.endsWith('/loop') ? 'loop' : payload.action;
      const snapshot = action === 'play' ? await streamPlayer.start(sessionRevision, payload.volume) : await streamPlayer.control(action,payload);
      return {json:async()=>snapshot};
    }
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

  function scheduleLiveParameter(identity, value) {
    const key = JSON.stringify([identity.track_id, identity.effect_id, identity.parameter_id, identity.control_name]);
    liveParameterValues.set(key, { ...identity, value });
    clearTimeout(liveParameterTimer);
    liveParameterTimer = setTimeout(() => {
      liveParameterTimer = null;
      const generation = liveParameterGeneration;
      liveParameterQueue = liveParameterQueue.then(() => flushLiveParameters(generation)).catch(() => {});
    }, 75);
  }

  async function flushLiveParameters(generation) {
    if (liveParameterBusy || generation !== liveParameterGeneration) return;
    liveParameterBusy = true;
    try {
      while (liveParameterValues.size && generation === liveParameterGeneration && ['playing', 'paused'].includes(nativeSnapshot.state)) {
        const [key, change] = liveParameterValues.entries().next().value;
        liveParameterValues.delete(key);
        if (sessionRevision === null) throw new Error('The session revision is unavailable.');
        const source = change.control_name !== undefined;
        const payload = source ? {expected_revision: sessionRevision, track_id: change.track_id, control_name: change.control_name, values: change.values} : {expected_revision: sessionRevision, track_id: change.track_id, effect_id: change.effect_id, parameter_id: change.parameter_id, value: change.value};
        const response = await request(source ? '/api/source/control' : '/api/effect/parameter', {method: 'POST', body: JSON.stringify(payload)});
        const result = await response.json();
        if (typeof result.revision !== 'string' || !result.session || result.queued !== true) throw new Error('The server returned an invalid plugin parameter acceptance.');
        sessionRevision = result.revision;
        applied = clone(result.session);
        syncStatus();
      }
    } catch (error) {
      liveParameterValues.clear();
      await restoreAuthoritativeParameterValues();
      announceError(`Live parameter update failed. ${error.message}`);
    } finally {
      liveParameterBusy = false;
      syncStatus();
      if (liveParameterValues.size && !liveParameterTimer && generation === liveParameterGeneration) {
        liveParameterQueue = liveParameterQueue.then(() => flushLiveParameters(generation)).catch(() => {});
      }
    }
  }

  function parameterIn(session, identity) {
    const track = session?.tracks?.find(item => item.id === identity.track_id);
    const effect = track?.effects?.find(item => item.id === identity.effect_id);
    return effect?.parameters?.find(item => String(item.id) === String(identity.parameter_id));
  }

  function setDraftParameter(identity, value) {
    const parameter = parameterIn(draft, identity);
    if (parameter) parameter.value = value;
  }

  async function restoreAuthoritativeParameterValues() {
    try {
      const revision = await inspectCurrentSession();
      if (validateSession(revision.session)) return;
      applied = clone(revision.session);
      draft = clone(revision.session);
      renderTracks();
    } catch (_) { /* Keep the last accepted base if the bridge is unavailable. */ }
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
    const effectsMode = draft.schema_version !== 1;
    outputMode.querySelector('option[value="browser"]').disabled = effectsMode;
    if (effectsMode && nativeAvailable) outputMode.value = 'native';
    if (SessionEditor.arrangement(draft)) {
      $('#effects-hint').textContent = 'Note arrangement · native playback. Select a clip to edit notes. Note, clip and mix edits apply during built-in native playback; tempo changes the grid only.';
    }
    durationInput.max = String(SessionEditor.exportMaximum(draft, renderLimits));
    $('#render-limit').textContent = `Export up to ${durationInput.max} seconds for this project.`;
    rateSelect.disabled = effectsMode && SessionEditor.plugins(draft).length > 0;
    $('.live-help').textContent = hasSources() ? 'Click Play/Stop for live sources. Escape also stops. Playback ends at the longest saved source duration (up to ten seconds). Saved scalar/array controls without automation can change live; SC initialization-rate slots remain read-only. Listening volume affects playback only.' : 'Click Play/Pause. Hold the button or press Escape to stop. Browser output plays draft edits live. Native output applies the session and stops after 60 seconds, including time paused. Listening volume affects playback only.';
    $('#effects-hint').textContent = hasSources() ? (draft.sample_rate !== 48000 ? 'Live sources require a 48 kHz session/device. The saved rate is preserved; use scripts to change it. Save and render remain available.' : canPlaySources() ? 'Live native sources with gain effects. Programs, duration and automation are preserved. Declared saved controls without automation can change live; structural edits require stopped playback.' : 'Live sources require a native-audio build and their installed runtime/queue bridge. Loaded sources can still be saved and rendered when their runtime is available.') : effectsMode && SessionEditor.plugins(draft).length && !nativePluginsAvailable ? 'Live VST3 requires a vst3-live build. Offline rendering requires vst3-offline. Saved automation points are preserved.' : effectsMode ? (browserStreaming ? 'Server audio plays in this browser. Notes and mix edits reach playback after the listening buffer. Track/device changes require Stop.' : 'Effects use native audio. Built-in sound, effect, automation, mixer, note and clip edits apply during native playback. Track and foreign-runtime changes require Stop.') : 'Effects use native playback. Add gain, lowpass or delay, or load a saved VST3 session to reuse its validated effects.';
    if (SessionEditor.arrangement(draft)) $('.live-help').textContent = `${browserStreaming ? 'Browser streaming' : 'Native playback'} uses the applied arrangement. Seek and loop are available during playback or pause. Edit notes, clips and mix while built-in playback runs. ${untilStoppedAvailable ? `Built-in playback runs until Stop; WAV export supports up to ${durationInput.max} seconds.` : 'This engine limits playback to 60 seconds.'}`;
    if (SessionEditor.arrangement(draft)) $('#effects-hint').textContent = `${hasAudio() ? 'Arrangement' : 'Note arrangement'} · native playback. Select a clip to ${hasAudio() ? 'edit notes or trim audio' : 'edit notes'}. Note, clip and mix edits apply during built-in native playback; tempo changes the grid only.`;
    if (draft.tracks.some(track => track.device.kind === 'pd_instrument')) {
      $('#effects-hint').textContent += ' Pd notes are monophonic; cutoff and gain edits apply while stopped. Playback and export require libpd.';
      $('.live-help').textContent = `${browserStreaming ? 'Browser streaming' : 'Native playback'} uses the applied arrangement. Use Stop & edit to change notes and Pd controls. Pd notes are scheduled in 64-frame blocks. WAV export supports up to ${durationInput.max} seconds.`;
    }
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
    if (track.device.kind === 'pd_instrument') return makePdInstrumentCard(track, index);
    if (['synth', 'drumkit'].includes(track.device.kind)) return makeInstrumentCard(track, index);
    if (track.device.kind === 'audio') return makeAudioCard(track, index);
    card.setAttribute('aria-label', `Sine track ${index + 1}`);

    const ident = element('div', 'track-ident');
    const icon = element('div', 'track-icon');
    icon.setAttribute('aria-hidden', 'true');
    const miniWave = element('span', 'wave-mini');
    for (let i = 0; i < 5; i++) miniWave.append(element('i'));
    icon.append(miniWave);
    const title = element('div', 'track-title');
    title.append(element('h2', '', track.mode === 'sequenced' ? track.id : `Sine ${String(index + 1).padStart(2, '0')}`), element('p', '', track.mode === 'sequenced' ? 'Note instrument · sine' : 'Sine oscillator'));
    ident.append(icon, title, element('span', 'track-index', String(index + 1).padStart(2, '0')));

    const frequency = element('div', 'frequency-control');
    frequency.dataset.field = 'frequency';
    frequency.hidden = track.mode === 'sequenced';
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
    remove.type = 'button'; remove.dataset.structural = 'true';
    remove.setAttribute('aria-label', `Remove sine track ${index + 1}`);
    remove.addEventListener('click', () => {
      draft.tracks.splice(index, 1);
      renderTracks();
      markEdited();
    });

    card.append(ident, frequency, gain, remove, makeEffectPanel(track, index));
    return card;
  }

  function makeAudioCard(track, index) {
    const card = element('article', 'track-card instrument-card');
    card.setAttribute('aria-label', `${track.id} audio`);
    const ident = element('div', 'track-ident');
    const title = element('div', 'track-title');
    title.append(element('h2', '', track.id), element('p', '', 'PCM audio · select a clip to trim its source or change gain'));
    ident.append(element('span', 'track-icon', 'AU'), title);
    const controls = element('div', 'instrument-controls');
    const gain = element('label', 'instrument-control', 'Gain');
    const input = element('input', 'number-input');
    input.type = 'number'; input.min = '0'; input.max = '1'; input.step = '0.01'; input.value = String(track.device.gain);
    input.setAttribute('aria-label', `${track.id} Gain`);
    input.addEventListener('input', () => { draft.tracks[index].device.gain = input.value.trim() ? Number(input.value) : NaN; markEdited(); });
    gain.append(input); controls.append(gain);
    const remove = element('button', 'remove-button', '×'); remove.type = 'button'; remove.dataset.structural = 'true'; remove.setAttribute('aria-label', `Remove ${track.id}`);
    remove.addEventListener('click', () => { draft.tracks.splice(index, 1); renderTracks(); markEdited(); });
    card.append(ident, controls, remove, makeEffectPanel(track, index));
    return card;
  }

  function makePdInstrumentCard(track, index) {
    const d = track.device;
    const card = element('article', 'track-card instrument-card pd-instrument-card');
    card.setAttribute('aria-label', `${track.id} Pd instrument`);
    const ident = element('div', 'track-ident'), title = element('div', 'track-title');
    title.append(element('h2', '', track.id), element('p', '', 'Pd · Filtered Sine · monophonic'));
    ident.append(element('span', 'track-icon', 'PD'), title);
    const controls = element('div', 'instrument-controls');
    function numeric(label, value, min, max, edit) {
      const wrap = element('label', 'instrument-control', label), input = element('input', 'number-input');
      input.type = 'number'; input.min = String(min); input.max = String(max); input.step = 'any'; input.value = String(value);
      input.dataset.structural = 'true';
      input.setAttribute('aria-label', `${track.id} ${label}`);
      input.addEventListener('change', () => {
        const value = input.value.trim() ? Number(input.value) : NaN;
        void editArrangement({type: 'editPdInstrument', trackIndex: index, patch: edit(value)});
      });
      wrap.append(input); controls.append(wrap);
    }
    numeric('Gain', d.gain, 0, 1, value => ({gain: value}));
    for (const control of d.controls) numeric(`${control.name} (Hz)`, control.value, control.min, control.max, value => ({controls: d.controls.map(item => item.name === control.name ? {...item, value} : {...item})}));
    const save = element('button', 'button button-quiet', 'Save Pd preset'); save.type = 'button';
    save.addEventListener('click', () => download(new Blob([JSON.stringify(d, null, 2) + '\n'], {type: 'application/json'}), 'pd-filtered-sine.json'));
    controls.append(save);
    const remove = element('button', 'remove-button', '×'); remove.type = 'button'; remove.dataset.structural = 'true';
    remove.setAttribute('aria-label', `Remove ${track.id}`);
    remove.addEventListener('click', () => { void editArrangement({type: 'deleteTrack', trackIndex: index}); });
    const details = element('details', 'pd-program');
    details.append(element('summary', '', 'Embedded Pd program'));
    details.append(element('p', 'output-hint', 'The program and declared controls travel with this session. Change gain or cutoff while stopped; Undo restores the previous applied values. Requires the libpd runtime for playback and rendering.'));
    details.append(element('pre', '', d.program));
    card.append(ident, controls, remove, details, makeEffectPanel(track, index));
    return card;
  }

  function makeInstrumentCard(track, index) {
    const d = track.device, drum = d.kind === 'drumkit';
    const card = element('article', 'track-card instrument-card');
    card.setAttribute('aria-label', `${track.id} ${drum ? 'drum kit' : 'synth'}`);
    const ident = element('div', 'track-ident');
    const title = element('div', 'track-title');
    title.append(element('h2', '', track.id), element('p', '', drum ? 'Factory kit · 36 Kick / 38 Snare / 42 Hat' : 'Polyphonic synth'));
    ident.append(element('span', 'track-icon', drum ? 'DR' : 'SY'), title);
    const controls = element('div', 'instrument-controls');
    function numeric(key, label, min, max, step) {
      const wrap = element('label', 'instrument-control', label);
      const input = element('input', 'number-input');
      input.type = 'number'; input.min = min; input.max = max; input.step = step; input.value = String(d[key]);
      input.setAttribute('aria-label', `${track.id} ${label}`);
      input.addEventListener('input', () => { draft.tracks[index].device[key] = input.value.trim() ? Number(input.value) : NaN; markEdited(); });
      wrap.append(input); controls.append(wrap);
    }
    numeric('gain', 'Gain', 0, 1, 0.01);
    if (!drum) {
      const presetLabel = element('label', 'instrument-control', 'Preset');
      const preset = element('select');
      for (const [value, name] of [['custom','Custom'], ['bass','Bass'], ['lead','Lead']]) { const o = element('option','',name); o.value=value; preset.append(o); }
      preset.setAttribute('aria-label', `${track.id} synth preset`);
      preset.addEventListener('change', () => {
        if (preset.value === 'custom') return;
        const settings = preset.value === 'bass' ? {waveform:'square',gain:0.13,attack_ms:4,release_ms:60,cutoff_hz:700} : {waveform:'saw',gain:0.13,attack_ms:8,release_ms:90,cutoff_hz:Math.min(2800,draft.sample_rate/2-1)};
        Object.assign(draft.tracks[index].device,settings); markEdited(); renderTracks();
      });
      presetLabel.append(preset); controls.append(presetLabel);
      const waveformLabel = element('label', 'instrument-control', 'Waveform'), waveform = element('select');
      for (const kind of ['saw','square']) { const o = element('option','',kind); o.value=kind; waveform.append(o); }
      waveform.value=d.waveform; waveform.setAttribute('aria-label',`${track.id} waveform`);
      waveform.addEventListener('change',()=>{ draft.tracks[index].device.waveform=waveform.value; markEdited(); });
      waveformLabel.append(waveform); controls.append(waveformLabel);
      numeric('attack_ms', 'Attack (ms)', 1, 2000, 1);
      numeric('release_ms', 'Release (ms)', 0, 2000, 1);
      numeric('cutoff_hz', 'Cutoff (Hz)', 20, Math.min(20000,draft.sample_rate/2-1), 1);
    }
    const remove = element('button', 'remove-button', '×'); remove.type='button'; remove.dataset.structural = 'true'; remove.setAttribute('aria-label', `Remove ${track.id}`);
    remove.addEventListener('click',()=>{ draft.tracks.splice(index,1); renderTracks(); markEdited(); });
    card.append(ident, controls, remove, makeEffectPanel(track,index));
    return card;
  }

  function makeSourceCard(track, index) {
    const source = track.device;
    const csound = source.kind === 'csound';
    const kind = csound ? 'Csound' : 'SuperCollider';
    const card = element('article', 'track-card source-card');
    card.dataset.sourceTrackId = track.id;
    card.setAttribute('aria-label', `${kind} track ${index + 1}`);
    const ident = element('div', 'track-ident');
    const title = element('div', 'track-title');
    title.append(element('h2', '', `${kind} ${String(index + 1).padStart(2, '0')}`), element('p', '', track.id));
    ident.append(element('span', 'track-icon', csound ? 'CS' : 'SC'), title);
    const summary = element('p', 'source-summary', `${csound ? 'Embedded CSD' : source.synth_name} · ${(source.duration_frames / draft.sample_rate).toLocaleString()} sec · source gain ${source.gain}`);
    const remove = element('button', 'button button-quiet', 'Remove track');
    remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${track.id}`);
    remove.addEventListener('click', () => { draft.tracks.splice(index, 1); renderTracks(); markEdited(); });
    card.append(ident, summary, remove);
    const panel = element('section', 'source-controls');
    panel.setAttribute('aria-label', `Saved controls on ${track.id}`);
    panel.append(element('h3', '', 'Saved source controls'));
    const metadata = sourceMetadata.get(track.id);
    if (!csound && !metadata) panel.append(element('p', 'output-hint', sourceBridgeAvailable ? 'Inspecting control rates…' : 'Restart the local server to inspect source controls.'));
    if (!csound && metadata?.error) panel.append(element('p', 'metadata-error', `Controls are read-only. ${metadata.error}`));
    for (const control of source.controls) {
      const row = element('div', 'source-control');
      row.dataset.sourceControl = control.name;
      row.dataset.trackId = track.id;
      row.append(element('span', 'parameter-name', control.name));
      (csound ? [control.value] : control.values).forEach((value, slot) => {
        const label = element('label', 'source-slot');
        label.append(element('span', 'sr-only', `${track.id}, ${control.name}, value ${slot + 1}`));
        const input = element('input', 'number-input');
        input.type = 'number'; input.step = 'any'; input.value = String(value);
        input.setAttribute('aria-label', `${track.id}, ${control.name}, value ${slot + 1}`);
        input.addEventListener('input', () => {
          const number = input.value.trim() ? Number(input.value) : NaN;
          const target = draft.tracks.find(item => item.id === track.id).device.controls.find(item => item.name === control.name);
          if (csound) target.value = number; else target.values[slot] = number;
          if (!Number.isFinite(number) || (!csound && !Number.isFinite(Math.fround(number)))) {
            input.setCustomValidity(csound ? 'Enter a finite scalar value.' : 'Enter a finite float32 value.');
            announceError(csound ? 'Csound controls require finite scalar values.' : 'Source controls require finite float32 values.');
            syncStatus(); return;
          }
          input.setCustomValidity('');
          if (nativeLocked() && sourceEligible(track.id, control.name)) {
            scheduleLiveParameter({track_id: track.id, control_name: control.name, values: csound ? [target.value] : [...target.values]}, number);
            setNotice('Sending live source control…');
            syncStatus();
          } else markEdited();
        });
        label.append(input); row.append(label);
      });
      const native = metadata?.controls?.find(item => item.name === control.name);
      if (control.points.length) row.append(element('span', 'output-hint', `${control.points.length} saved automation points · read-only`));
      else if (native?.initialization_rate) row.append(element('span', 'output-hint', 'Initialization rate · read-only'));
      if (csound && !control.points.length) {
        const remove = element('button', 'button button-quiet', 'Remove control');
        remove.type = 'button'; remove.dataset.structural = 'true';
        remove.setAttribute('aria-label', `Remove ${track.id}, ${control.name}`);
        remove.addEventListener('click', () => {
          source.controls = source.controls.filter(item => item.name !== control.name);
          renderTracks(); markEdited();
        });
        row.append(remove);
      }
      panel.append(row);
    }
    if (!source.controls.length) panel.append(element('p', 'output-hint', 'No saved native controls. Program defaults are preserved.'));
    if (csound) {
      panel.append(element('p', 'output-hint', 'Scalar input channels are verified on Apply. Automated controls remain read-only.'));
      const settings = element('div', 'source-control');
      for (const [labelText, value, update, step] of [
        ['Duration (seconds)', source.duration_frames / draft.sample_rate, number => { source.duration_frames = Math.round(number * draft.sample_rate); }, 'any'],
        ['Source gain', source.gain, number => { source.gain = number; }, '0.01'],
      ]) {
        const label = element('label', 'source-slot', labelText);
        const input = element('input', 'number-input'); input.type = 'number'; input.step = step; input.value = String(value);
        input.setAttribute('aria-label', `${track.id}, ${labelText}`);
        input.addEventListener('input', () => { update(input.value.trim() ? Number(input.value) : NaN); markEdited(); });
        label.append(input); settings.append(label);
      }
      panel.append(settings);
      const add = element('div', 'source-control');
      const nameLabel = element('label', 'source-slot', 'Channel name');
      const name = element('input', 'number-input'); name.type = 'text'; name.setAttribute('aria-label', `${track.id}, new channel name`); nameLabel.append(name);
      const valueLabel = element('label', 'source-slot', 'Base value');
      const value = element('input', 'number-input'); value.type = 'number'; value.step = 'any'; value.value = '0'; value.setAttribute('aria-label', `${track.id}, new channel base value`); valueLabel.append(value);
      const button = element('button', 'button button-quiet', 'Add scalar control'); button.type = 'button';
      button.addEventListener('click', () => {
        try {
          draft = SessionEditor.addCsoundControl(draft, index, name.value, value.value.trim() ? Number(value.value) : NaN);
          renderTracks(); markEdited();
        } catch (error) { announceError(error.message); }
      });
      add.append(nameLabel, valueLabel, button); panel.append(add);
    }
    const details = element('details', 'effect-identity');
    details.append(element('summary', '', 'Saved program'));
    if (csound) details.append(element('pre', 'source-program', source.program));
    else details.append(element('p', '', `${source.synth_name} · ${source.synthdef_hex.length / 2} bytes · embedded SynthDef. Edit program, duration and automation through scripts.`));
    panel.append(details); card.append(panel, makeEffectPanel(track, index));
    return card;
  }

  function makeEffectPanel(track, index) {
    const panel = element('section', 'effect-panel');
    panel.setAttribute('aria-label', `Track ${index + 1} effects`);
    const heading = element('div', 'effect-heading');
    heading.append(element('h3', '', 'Effects'), element('span', 'output-hint', 'Applied in order'));
    panel.append(heading);
    for (const effect of track.effects || []) {
      const row = element('div', 'effect-row');
      row.dataset.trackId = track.id;
      row.dataset.effectId = effect.id;
      const label = effect.kind === 'vst3' ? effect.bundle_path.split('/').pop().replace(/\.vst3$/, '') : ({gain: 'Gain', lowpass: 'Lowpass', delay: 'Delay'}[effect.kind] || effect.kind);
      row.append(element('strong', '', label));
      const bypassLabel = element('label', 'bypass-control', 'Bypass');
      const bypass = element('input'); bypass.type = 'checkbox'; bypass.checked = effect.bypass;
      bypass.setAttribute('aria-label', `${label} bypass on track ${index + 1}`);
      bypass.addEventListener('change', () => { effect.bypass = bypass.checked; markEdited(); });
      bypassLabel.prepend(bypass); row.append(bypassLabel);
      const remove = element('button', 'button button-quiet', 'Remove'); remove.type = 'button';
      remove.setAttribute('aria-label', `Remove ${label} from track ${index + 1}`);
      remove.addEventListener('click', () => { draft = SessionEditor.removeEffect(draft, index, effect.id); renderTracks(); markEdited(); });
      row.append(remove);
      if (['lowpass', 'delay'].includes(effect.kind)) {
        const fields = effect.kind === 'lowpass'
          ? [['cutoff_hz', 'Cutoff (Hz)', 20, Math.min(20000, draft.sample_rate / 2 - 1), 1]]
          : [['time_ms', 'Time (ms)', 1, 2000, 1], ['feedback', 'Feedback', 0, 0.95, 0.01], ['mix', 'Wet mix', 0, 1, 0.01]];
        for (const [field, name, min, max, step] of fields) {
          const control = element('label', 'effect-parameter', name);
          const input = element('input'); input.type = 'number'; input.min = String(min); input.max = String(max); input.step = String(step); input.value = String(effect[field]);
          input.setAttribute('aria-label', `${label} ${name} on track ${index + 1}`);
          input.addEventListener('input', () => { effect[field] = input.value.trim() ? Number(input.value) : NaN; markEdited(); });
          control.append(input); row.append(control);
        }
        const preset = element('select', 'effect-select');
        preset.setAttribute('aria-label', `${label} preset on track ${index + 1}`);
        const prompt = element('option', '', 'Choose preset…'); prompt.value = ''; preset.append(prompt);
        for (const name of Object.keys(SessionEditor.effectPresets[effect.kind])) {
          const option = element('option', '', name[0].toUpperCase() + name.slice(1)); option.value = name; preset.append(option);
        }
        preset.addEventListener('change', () => {
          if (!preset.value) return;
          try {
            const {kind, bypass, ...settings} = SessionEditor.effectPreset(effect.kind, preset.value, draft.sample_rate);
            draft = SessionEditor.editEffect(draft, index, effect.id, settings);
            renderTracks(); markEdited();
          } catch (error) { announceError(error.message); }
        });
        row.append(preset); panel.append(row); continue;
      }
      const params = effect.kind === 'gain' ? [{id: 'gain', value: effect.gain, points: (track.automation || []).find(lane => lane.effect_id === effect.id)?.points || []}] : effect.parameters;
      for (const param of params) {
        const control = element('label', 'effect-parameter');
        if (effect.kind === 'vst3') {
          control.dataset.parameterId = String(param.id);
          const metadata = metadataCache.get(JSON.stringify([track.id, effect.id]));
          const info = metadata?.parameters?.find(item => String(item.id) === String(param.id));
          control.dataset.metadataDisabled = info && (info.automatable !== true || info.read_only === true) ? 'true' : 'false';
          const name = info?.name || `Parameter ${param.id}`;
          control.append(element('span', 'parameter-name', info?.unit ? `${name} (${info.unit})` : name));
        } else control.append(element('span', '', 'Gain'));
        const value = element('output', '', Number(param.value).toFixed(3));
        const metadata = effect.kind === 'vst3' ? metadataCache.get(JSON.stringify([track.id, effect.id]))?.parameters?.find(item => String(item.id) === String(param.id)) : null;
        const parameterName = metadata?.name || `Parameter ${param.id}`;
        const parameterUnit = metadata?.unit ? ` in ${metadata.unit}` : '';
        const range = makeRange(0, effect.kind === 'gain' ? 4 : 1, 0.001, param.value,
          effect.kind === 'gain' ? `Gain on track ${index + 1}` : `Sine ${track.id}, ${effect.id}, ${parameterName}${parameterUnit}`);
        if (effect.kind === 'vst3') {
          range.dataset.metadataDisabled = control.dataset.metadataDisabled;
          range.disabled = control.dataset.metadataDisabled === 'true';
        }
        range.addEventListener('input', () => {
          const number = Number(range.value);
          if (effect.kind === 'gain') { effect.gain = number; markEdited(); }
          else {
            const identity = { track_id: track.id, effect_id: effect.id, parameter_id: param.id };
            setDraftParameter(identity, number);
            if (liveParameterEligible(track.id, effect.id, param.id)) {
              setNotice(nativeSnapshot.state === 'paused' ? 'Parameter queued; it will reach the plugin when playback resumes.' : 'Sending live plugin parameter update…');
              scheduleLiveParameter(identity, number);
              syncStatus();
            } else markEdited();
          }
          value.textContent = number.toFixed(3);
        });
        control.append(range, value); row.append(control);
        if (param.points.length) row.append(element('p', 'output-hint', `${param.points.length} saved automation points override this base value; live editing is locked for this parameter.`));
      }
      if (effect.kind === 'gain' && draft.schema_version !== 1) {
        const holder = element('div', 'gain-automation');
        const key = JSON.stringify([track.id, effect.id]);
        holder.dataset.automationTrack = String(index);
        const view = AutomationView.create(holder, {editor: SessionEditor, trackIndex: index, effectId: effect.id,
          draftState: automationDraftStates.get(key), onEdit: applyAutomationEdit, onError: announceError});
        view.render(draft, {locked: busy || editLocked() || unsupportedSession || Boolean(player.context) || starting});
        automationViews.push({key, view});
        row.append(holder);
      }
      if (effect.kind === 'vst3') {
        const details = element('details', 'effect-identity');
        details.append(element('summary', '', 'Saved plugin identity'), element('p', '', `${effect.bundle_path} · ${effect.class_id}`));
        row.append(details);
        const failure = metadataCache.get(JSON.stringify([track.id, effect.id]))?.error;
        if (failure) row.append(element('p', 'output-hint metadata-error', `Parameter names unavailable. ${failure}`));
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
    actions.append(gain);
    if (SessionEditor.mixerSupported(draft)) {
      for (const [kind, name, preset] of [['lowpass', 'Add lowpass', 'warm'], ['delay', 'Add delay', 'echo']]) {
        const button = element('button', 'button button-quiet add-processing', name); button.type = 'button';
        button.title = 'Saved built-in processing requires an engine supporting format 11.';
        button.addEventListener('click', () => add(SessionEditor.effectPreset(kind, preset, draft.sample_rate)));
        actions.append(button);
      }
    }
    if (![6, 7].includes(draft.schema_version) && !SessionEditor.arrangement(draft)) actions.append(select, vst);
    panel.append(actions);
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
    automationDraftStates = new Map(automationViews.map(({key, view}) => [key, view.getDraftState()]));
    automationViews = [];
    mixerView?.render(draft, {locked: busy || editLocked() || unsupportedSession || Boolean(player.context) || starting});
    mixerView?.updateMeters(nativeSnapshot.mixer_meters, nativeSnapshot.state);
    tracksEl.replaceChildren(...draft.tracks.map((track, index) => (['supercollider', 'csound'].includes(track.device.kind) ? makeSourceCard(track, index) : makeTrackCard(track, index))));
    const empty = draft.tracks.length === 0;
    emptyEl.hidden = !empty;
    tracksEl.hidden = empty;
    $('#track-count').textContent = `${draft.tracks.length} ${draft.tracks.length === 1 ? 'track' : 'tracks'}`;
    $('#arrangement').hidden = !SessionEditor.arrangement(draft);
    $('#timeline-transport').hidden = $('#arrangement').hidden;
    arrangementView?.render(draft, {locked: busy || editLocked(), frame: nativeSnapshot.timeline_frame || 0, loop: nativeActive() ? nativeSnapshot.loop_region : null, transportAvailable: nativeAvailable && ['playing', 'paused'].includes(nativeSnapshot.state), pending: timelineBusy});
    syncStatus();
  }

  async function applyMixerEdit(next) {
    if (busy || editLocked() || noteRecording?.pending || historyAction || unsupportedSession) return false;
    if (player.context || starting) return false;
    const before = clone(draft);
    draft = next;
    if (await applyDraft()) return true;
    draft = before;
    renderTracks();
    return false;
  }

  async function applyAutomationEdit(next, focus) {
    if (busy || editLocked() || historyAction || unsupportedSession || player.context || starting) return false;
    const before = clone(draft);
    draft = next;
    if (await applyDraft()) {
      if (focus) {
        const holder = [...tracksEl.querySelectorAll('.gain-automation')].find(node => node.dataset.automationTrack === String(focus.trackIndex) &&
          node.closest('.effect-row')?.dataset.effectId === focus.effectId);
        const input = [...(holder?.querySelectorAll('input') || [])].find(node => node.dataset.automationKey === String(focus.frame) && node.dataset.automationField === focus.field);
        input?.focus();
      }
      return true;
    }
    draft = before;
    syncStatus();
    return false;
  }

  function markEdited() {
    setNotice('');
    syncStatus();
    updateLive();
  }

  function addTrack() {
    if (busy || nativeLocked() || draft.tracks.length >= 64) return;
    if (SessionEditor.arrangement(draft)) { void editArrangement({type: 'addTrack'}); return; }
    const track = { id: makeId(), device: { kind: 'sine', frequency_hz: 440, gain: 0.15 } };
    if ([4, 6, 7].includes(draft.schema_version)) Object.assign(track, {mode: 'continuous', clips: [], effects: []});
    draft.tracks.push(track);
    renderTracks();
    markEdited();
  }

  async function editArrangement(action) {
    if (busy || editLocked() || noteRecording?.pending || historyAction || unsupportedSession) return false;
    if (nativeLocked() && ['addTrack', 'deleteTrack', 'editPdInstrument'].includes(action.type)) return false;
    if (action.type === 'quantizeClip' && studioHasDrafts()) {
      const message = 'Apply or revert pending edits before quantizing; your drafts are preserved.';
      arrangementView?.reportError(message); announceError(message); return false;
    }
    const before = clone(draft);
    try {
      const index = action.trackIndex;
      const nextId = (prefix, items) => {
        const used = new Set((items || []).map(item => item.id));
        let number = 1;
        while (used.has(`${prefix}-${number}`)) number += 1;
        return `${prefix}-${number}`;
      };
      switch (action.type) {
        case 'addTrack': { const kind = action.device ? 'pd_instrument' : $('#note-device')?.value || 'synth'; const id = nextId(kind === 'pd_instrument' ? 'pd' : kind === 'drumkit' ? 'drums' : kind === 'synth' ? 'synth' : 'notes', draft.tracks); draft = action.device ? SessionEditor.addPdInstrument(draft, id, action.device) : SessionEditor.addNoteTrack(draft, id, kind); break; }
        case 'editPdInstrument': draft = SessionEditor.editPdInstrument(draft, index, action.patch); break;
        case 'deleteTrack': draft = clone(draft); draft.tracks.splice(index, 1); break;
        case 'addClip': draft = SessionEditor.addNoteClip(draft, index, {id: nextId('clip', draft.tracks[index]?.clips), start_frame: action.start_frame, length_frames: action.length_frames, notes: []}); break;
        case 'moveClip': draft = SessionEditor.moveClip(draft, index, action.clipId, action.start_frame); break;
        case 'resizeClip': draft = SessionEditor.resizeClip(draft, index, action.clipId, action.length_frames); break;
        case 'editAudioClip': draft = SessionEditor.editAudioClip(draft, index, action.clipId, action.patch); break;
        case 'duplicateClip': draft = SessionEditor.duplicateClip(draft, index, action.clipId, nextId('clip', draft.tracks[index]?.clips), action.start_frame); break;
        case 'deleteClip': draft = SessionEditor.deleteClip(draft, index, action.clipId); break;
        case 'addNote': draft = SessionEditor.addNote(draft, index, action.clipId, {...action.note, id: nextId('n', draft.tracks[index]?.clips.find(clip => clip.id === action.clipId)?.notes)}); break;
        case 'editNote': draft = SessionEditor.editNote(draft, index, action.clipId, action.noteId, action.patch); break;
        case 'quantizeClip': {
          const next = SessionEditor.quantizeClip(draft, index, action.clipId, action.gridTicks);
          if (next === draft) { setNotice('Clip notes are already aligned to this grid.'); return true; }
          draft = next; break;
        }
        case 'deleteNote': draft = SessionEditor.deleteNote(draft, index, action.clipId, action.noteId); break;
        case 'setTempo': draft = {...clone(draft), tempo_milli_bpm: action.tempo_milli_bpm}; break;
        default: throw new Error('Unknown arrangement edit.');
      }
      const error = validateSession(draft);
      if (error) throw new Error(error);
      renderTracks();
      if (!await applyDraft()) { const message = noticeEl.textContent; draft = before; renderTracks(); arrangementView?.reportError(message); return false; }
      return true;
    } catch (error) { draft = before; renderTracks(); arrangementView?.reportError(error); announceError(error.message); return false; }
  }

  async function importPdPreset(file) {
    if (!file || busy || nativeLocked() || unsupportedSession) return;
    try {
      if (file.size > PdInstrument.packageByteLimit) throw new Error('Pd preset packages must be JSON no larger than 128 KiB.');
      const device = PdInstrument.parsePackage(await file.text());
      await editArrangement({type: 'addTrack', device});
    } catch (error) { announceError(`Could not load the Pd preset. ${error.message}`); }
  }

  async function replaceWithArrangement(demo = false) {
    if (busy || nativeLocked() || unsupportedSession) return;
    if ((isDirty() || draft.tracks.length) && !window.confirm('Replace this session with a note arrangement? Save your current session first if needed.')) return;
    const before = clone(draft);
    resetNoteProject();
    stopLive();
    automationViews = []; automationDraftStates.clear();
    draft = demo ? SessionEditor.createMusicalDemoSession() : SessionEditor.createArrangementSession(draft.sample_rate);
    selectSampleRate(draft.sample_rate);
    renderTracks();
    if (!await applyDraft()) { draft = before; selectSampleRate(draft.sample_rate); renderTracks(); return; }
    const end = Math.max(draft.sample_rate, ...draft.tracks.flatMap(track => track.clips || []).map(clip => clip.start_frame + clip.length_frames));
    durationInput.value = String(Math.min(SessionEditor.exportMaximum(draft, renderLimits), end / draft.sample_rate));
    setNotice(demo ? 'Demo arrangement ready. Select a clip to edit notes, then play or export.' : 'Note arrangement ready. Add a note track and a clip to begin.');
  }

  async function traverseHistory(direction) {
    if (busy || editLocked() || noteRecording?.pending || isDirty() || historyAction) return;
    const target = direction === 'undo' ? editHistory.undoTarget(applied) : editHistory.redoTarget(applied);
    if (!target) return;
    historyAction = true;
    const before = clone(draft);
    const previousApplied = clone(applied);
    draft = target;
    selectSampleRate(draft.sample_rate);
    renderTracks();
    try {
      if (await applyDraft({recordHistory: false})) {
        if (direction === 'undo') editHistory.acceptUndo(previousApplied); else editHistory.acceptRedo(previousApplied);
        setNotice(direction === 'undo' ? 'Edit undone.' : 'Edit redone.');
      } else { draft = before; selectSampleRate(draft.sample_rate); renderTracks(); }
    } finally { historyAction = false; syncStatus(); }
  }

  async function timelineCommand(path, payload) {
    if (noteRecording?.active) { noteInputError('Stop recording before seeking or looping.'); return; }
    if (!nativeAvailable || !nativeActive() || timelineBusy || nativeSnapshot.source_mode === 'live') return;
    timelineBusy = true;
    const generation = ++nativeCommandGeneration;
    nativeCommandsPending += 1;
    syncStatus();
    const operation = nativeCommandTail.then(async () => {
      // Acknowledgment may arrive a callback later. Do not fill the one-slot handoff twice.
      const deadline = Date.now() + 3000;
      let snapshot = nativeSnapshot;
      while (snapshot.timeline_command_pending) {
        if (Date.now() > deadline) throw new Error('The timeline command was not acknowledged.');
        await new Promise(resolve => setTimeout(resolve, 30));
        snapshot = await (await request('/api/transport')).json();
        if (generation === nativeCommandGeneration) applyNativeSnapshot(snapshot);
      }
      if (!['playing', 'paused'].includes(snapshot.state)) throw new Error('Start native playback before seeking or looping.');
      const response = await request(path, {method: 'POST', body: JSON.stringify(payload)});
      const accepted = await response.json();
      if (generation === nativeCommandGeneration) applyNativeSnapshot(accepted);
    });
    nativeCommandTail = operation.catch(() => {});
    try { await operation; return true; }
    catch (error) { arrangementView?.reportError(error); announceError(error.message); return false; }
    finally { timelineBusy = false; nativeCommandsPending -= 1; syncStatus(); }
  }

  function acceptImportedSession(result, {resetHistory = false} = {}) {
    if (resetHistory) resetNoteProject();
    if (!result?.session || typeof result.revision !== 'string') throw new Error('The server returned an invalid project revision.');
    const validation = validateSession(result.session);
    if (validation) throw new Error(`The imported project cannot be edited: ${validation}`);
    if (resetHistory) { editHistory.reset(); automationViews = []; automationDraftStates.clear(); }
    else if (applied) editHistory.commit(applied, result.session);
    invalidateEffectMetadata();
    sessionRevision = result.revision;
    applied = clone(result.session); draft = clone(applied);
    unsupportedSession = false;
    rememberEffects(); configureSessionMode(); selectSampleRate(draft.sample_rate);
    suggestArrangementDuration(); arrangementView?.reportError(''); renderTracks();
  }

  async function importMidiFile(file) {
    if (!file || busy || nativeLocked() || editLocked() || player.context || starting || recordingStarting || noteRecording?.take || noteRecording?.pending || historyAction || unsupportedSession) return false;
    let before = null;
    try {
      if (studioHasDrafts()) throw new Error('Apply or revert pending edits before importing MIDI.');
      if (!file.size || file.size > MidiFile.limits.bytes) throw new Error('MIDI files must be nonempty and at most 1 MiB.');
      const beatText = $('#arrangement [data-field="insertBeat"]')?.value ?? '0';
      const beat = Number(beatText);
      if (!beatText.trim() || !Number.isFinite(beat) || beat < 0) throw new Error('Enter a nonnegative Insert at beat.');
      const start = SessionEditor.ticksToFrames({...draft,tempo_milli_bpm:draft.tempo_milli_bpm || 120000},Math.round(beat*960));
      const snapshot = JSON.stringify(draft), revision = sessionRevision;
      setBusy(true);
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (snapshot !== JSON.stringify(draft) || revision !== sessionRevision || studioHasDrafts() || nativeLocked() || editLocked() || player.context || starting || recordingStarting || noteRecording?.take || noteRecording?.pending)
        throw new Error('The project changed while reading MIDI. Import again when stopped.');
      const result = SessionEditor.importMidiFile(draft,bytes,file.name.replace(/\.[^.]+$/, ''),start);
      before = clone(draft); draft = result.session;
      if (!await applyDraft({readControls:false})) { draft = before; renderTracks(); return false; }
      suggestArrangementDuration(); arrangementView?.reportError('');
      arrangementView?.selectClip?.(before.tracks.length, 'midi-1');
      setNotice(`Imported ${result.notes} ${result.notes === 1 ? 'note' : 'notes'}.${result.ignored ? ' MIDI sounds and expression omitted.' : ''}`);
      return true;
    } catch (error) { if (before) { draft = before; renderTracks(); } announceError(`MIDI import failed. ${error.message}`); return false; }
    finally { setBusy(false); }
  }

  async function importAudioFile(file) {
    if (!file || busy || nativeLocked() || unsupportedSession) return;
    try {
      await capabilitiesLoading;
      if (!audioProjectsAvailable) throw new Error('Restart the local server to enable WAV imports.');
      if (file.size <= 0 || file.size > 32 * 1024 * 1024) throw new Error('WAV files must be nonempty and at most 32 MiB.');
      const beat = Number($('#arrangement [data-field="insertBeat"]')?.value || 0);
      if (!Number.isFinite(beat) || beat < 0) throw new Error('Insert at (beat) must be a nonnegative number.');
      const start_frame = SessionEditor.ticksToFrames({...draft, tempo_milli_bpm:draft.tempo_milli_bpm || 120000}, Math.round(beat * 960));
      if (!await applyDraft()) return;
      stopLive(); setBusy(true);
      setNotice(`Importing ${file.name}…`);
      const stem = file.name.replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9 _-]/g, '').trim().slice(0, 60) || 'audio';
      const ids = new Set(draft.tracks.map(track => track.id));
      let track_id = stem, suffix = 2;
      while (ids.has(track_id)) track_id = `${stem}-${suffix++}`;
      const response = await request('/api/audio/import', {method:'POST', body:file, headers:{'Content-Type':'audio/wav', 'X-DAW-Metadata':JSON.stringify({expected_revision:sessionRevision, track_id, clip_id:'take-1', start_frame})}});
      const result = await response.json();
      acceptImportedSession(result);
      setNotice(`Imported ${file.name} as ${track_id}. Select its clip to trim or change gain. Save project ZIP includes the audio.`);
      return true;
    } catch (error) { announceError(`Could not import WAV. ${error.message}`); }
    finally { $('#audio-file').value = ''; setBusy(false); requestAppliedEffectMetadata(); }
  }

  let audioCapture = null, audioTakeRevision = null;
  if (typeof AudioCapture !== 'undefined' && typeof MediaRecorder !== 'undefined') {
    const syncCapture = () => {
      const active = audioCapture.state !== 'stopped';
      $('#stop-audio-button').disabled = !active;
      $('#record-audio-button').disabled = active || Boolean(audioCapture.take);
      for (const id of ['use-audio-take','download-audio-take','discard-audio-take']) $(`#${id}`).hidden = !audioCapture.take;
      if (!active) setBusy(false);
    };
    audioCapture = new AudioCapture({onStatus:message=>{$('#audio-capture-status').textContent=message;},onReady:syncCapture});
    $('#record-audio-button').addEventListener('click', async () => {
      if (busy || nativeActive() || isDirty() || noteRecording?.take || !audioProjectsAvailable || unsupportedSession) {announceError('Stop playback and apply edits before recording audio.');return;}
      audioTakeRevision = sessionRevision; setBusy(true);
      try {const capture=audioCapture.start(applied.sample_rate,$('#monitor-audio-input').checked);syncCapture();await capture;syncCapture();}
      catch(error){$('#audio-capture-status').textContent=error.message;syncCapture();}
    });
    $('#stop-audio-button').addEventListener('click',()=>{audioCapture.stop();syncCapture();});
    $('#discard-audio-take').addEventListener('click',()=>{audioCapture.discard();$('#audio-capture-status').textContent='Audio take discarded.';});
    $('#download-audio-take').addEventListener('click',()=>{const url=URL.createObjectURL(audioCapture.take),a=document.createElement('a');a.href=url;a.download=audioCapture.take.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    $('#use-audio-take').addEventListener('click',async()=>{
      if(audioTakeRevision !== sessionRevision || isDirty()) {announceError('Project changed. Download this take before discarding it, or restore the recording project.');return;}
      if(await importAudioFile(audioCapture.take)) audioCapture.discard();
    });
    window.addEventListener('pagehide',()=>audioCapture.discard());
  } else {$('#record-audio-button').disabled=true;$('#audio-capture-status').textContent='Audio capture is unavailable in this browser.';}

  async function addCsoundFile(file) {
    if (!file || busy || nativeLocked()) return;
    if (file.size > 61440) { announceError('Csound programs must be at most 60 KiB.'); return; }
    setBusy(true);
    try {
      const program = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
      draft = SessionEditor.addCsound(draft, program, makeId(), draft.sample_rate);
      stopLive(); configureSessionMode(); renderTracks(); markEdited();
      setNotice('Csound track added as format 7. Add declared scalar controls, then Apply to validate the program.');
    } catch (error) { announceError(`Could not add Csound program. ${error.message}`); }
    finally { setBusy(false); }
  }

  function updateDraftFromControls() {
    const sampleRate = Number(rateSelect.value);
    draft.sample_rate = sampleRate;
    for (const input of document.querySelectorAll('.frequency-control .number-input')) {
      const card = input.closest('.track-card');
      const index = Array.from(tracksEl.children).indexOf(card);
      if (index >= 0 && input.value.trim()) draft.tracks[index].device.frequency_hz = Number(input.value);
    }
  }

  async function applyDraft({recordHistory = true, readControls = true} = {}) {
    if (readControls && [...document.querySelectorAll('.frequency-control .number-input')].some((input) => input.value.trim() === '')) {
      announceError('Enter a frequency for every track before applying changes.');
      return false;
    }
    if (readControls) updateDraftFromControls();
    const validation = validateSession(draft);
    if (validation) {
      announceError(validation);
      return false;
    }
    if (!isDirty()) return true;
    await liveParameterQueue;
    setBusy(true);
    setNotice('Applying session changes…');
    try {
      await capabilitiesLoading;
      if (!bridgeDiscovered) throw new Error('Server capabilities are unavailable. Reload before applying changes.');
      const response = await request(nativeActive() ? '/api/session/live' : '/api/session', { method: 'POST', body: JSON.stringify({session: draft, ...(checkedReplacementAvailable ? {expected_revision: sessionRevision} : {})}) });
      const result = await response.json();
      const session = result.session || result;
      const error = validateSession(session);
      if (error) throw new Error(`The server returned an invalid session: ${error}`);
      invalidateEffectMetadata();
      const inspected = await inspectCurrentSession();
      if (recordHistory && applied) editHistory.commit(applied, inspected.session || session);
      applied = clone(inspected.session || session);
      draft = clone(applied);
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
      requestAppliedEffectMetadata();
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

  function exportSelectedMidi({trackIndex, clipId}) {
    if (busy || editLocked() || noteRecording?.pending || historyAction || unsupportedSession) return false;
    try {
      if (studioHasDrafts()) throw new Error('Apply or revert pending edits before exporting MIDI; your drafts are preserved.');
      const bytes = SessionEditor.exportMidiClip(applied, trackIndex, clipId);
      download(new Blob([bytes], {type:'audio/midi'}), `daw-clip-${timestamp()}.mid`);
      setNotice('Clip MIDI downloaded.');
      return true;
    } catch (error) { arrangementView?.reportError(error); announceError(error.message); return false; }
  }

  function timestamp() {
    return new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', 'Z');
  }

  async function saveSession() {
    setNotice('');
    const ok = await applyDraft();
    if (!ok) return;
    if (hasAudio()) {
      if (!audioProjectsAvailable) { announceError('Restart the local server to save audio projects with their assets.'); return; }
      setBusy(true);
      setNotice('Packing session and audio assets…');
      try {
        const response = await request('/api/project');
        download(await response.blob(), `daw-project-${timestamp()}.zip`);
        setNotice('Project ZIP downloaded with session and audio assets.');
      } catch (error) { announceError(`Could not save project. ${error.message}`); }
      finally { setBusy(false); }
      return;
    }
    const blob = new Blob([`${JSON.stringify(applied, null, 2)}\n`], { type: 'application/json' });
    download(blob, `daw-session-${timestamp()}.json`);
    setNotice('Validated session downloaded as JSON.');
  }

  function suggestArrangementDuration() {
    if (!SessionEditor.arrangement(draft)) return;
    const end = Math.max(draft.sample_rate, ...draft.tracks.flatMap(track => track.clips || []).map(clip => clip.start_frame + clip.length_frames));
    durationInput.value = String(Math.min(SessionEditor.exportMaximum(draft, renderLimits), end / draft.sample_rate));
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
      const projectZip = /\.zip$/i.test(file.name) || file.type === 'application/zip';
      if (projectZip) {
        await capabilitiesLoading;
        if (!audioProjectsAvailable) throw new Error('Restart the local server to reopen project ZIPs.');
        if (file.size > audioProjectByteLimit) throw new Error(`Project ZIPs must be at most ${Math.floor(audioProjectByteLimit / (1024 * 1024))} MiB.`);
        await liveParameterQueue;
        const response = await request('/api/project', {method:'POST', body:file, headers:{'Content-Type':'application/zip', 'X-DAW-Metadata':JSON.stringify({expected_revision:sessionRevision})}});
        const result = await response.json();
        acceptImportedSession(result, {resetHistory:true});
        setNotice(`Loaded ${file.name} with its audio assets.`);
        return;
      }
      if (file.size > 1024 * 1024) throw new Error('Session files must be no larger than 1 MiB.');
      let parsed;
      try { parsed = JSON.parse(await file.text()); }
      catch (_) { throw new Error('The selected file is not valid JSON.'); }
      const validation = validateSession(parsed);
      if (validation) throw new Error(validation);
      await liveParameterQueue;
      await capabilitiesLoading;
      if (!bridgeDiscovered) throw new Error('Server capabilities are unavailable. Reload before loading a session.');
      const response = await request('/api/session', { method: 'POST', body: JSON.stringify({session: parsed, ...(checkedReplacementAvailable ? {expected_revision: sessionRevision} : {})}) });
      const result = await response.json();
      const session = result.session || result;
      const serverValidation = validateSession(session);
      if (serverValidation) throw new Error(`The server returned an invalid session: ${serverValidation}`);
      invalidateEffectMetadata();
      const inspected = await inspectCurrentSession();
      editHistory.reset();
      resetNoteProject();
      automationViews = []; automationDraftStates.clear();
      applied = clone(inspected.session || session);
      draft = clone(applied);
      rememberEffects();
      configureSessionMode();
      selectSampleRate(draft.sample_rate);
      suggestArrangementDuration();
      renderTracks();
      setNotice(`Loaded ${file.name} and applied it to the session.`);
    } catch (error) {
      announceError(`Could not load session. ${error.message}`);
    } finally {
      fileInput.value = '';
      setBusy(false);
      syncStatus();
      requestAppliedEffectMetadata();
    }
  }

  async function renderAudio() {
    setNotice('');
    const seconds = Number(durationInput.value);
    const maxSeconds = SessionEditor.exportMaximum(draft, renderLimits);
    if (!Number.isFinite(seconds) || seconds < 0.001 || seconds > maxSeconds) {
      durationInput.setCustomValidity(`Duration must be between 0.001 and ${maxSeconds} seconds.`);
      durationInput.reportValidity();
      announceError(`Duration must be between 0.001 and ${maxSeconds} seconds.`);
      return;
    }
    durationInput.setCustomValidity('');
    metadataSuppressed = true;
    const ok = await applyDraft();
    if (!ok) { metadataSuppressed = false; requestAppliedEffectMetadata(); return; }
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
      metadataSuppressed = false;
      requestAppliedEffectMetadata();
    }
  }

  async function toggleNative() {
    if (!nativeAvailable || busy || nativeModeChange) return;
    if (browserStreaming && !nativeActive()) void streamPlayer.unlock().catch(announceError);
    if (noteRecording?.active && nativeActive()) { await stopNative(); return; }
    if (noteRecording?.pending) return;
    if (!recordingStarting) stopNoteInput();
    if (nativeSnapshot.state === 'starting') {
      await stopNative();
      return;
    }
    if (nativeSnapshot.state === 'playing') {
      if (nativeSnapshot.source_mode === 'live') { await stopNative(); return; }
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
    metadataSuppressed = true;
    const appliedOk = await applyDraft();
    if (startGeneration !== nativePlayGeneration || outputMode.value !== 'native') { metadataSuppressed = false; requestAppliedEffectMetadata(); return; }
    if (!appliedOk) { metadataSuppressed = false; requestAppliedEffectMetadata(); return; }
    // Invalidate a pending browser start before releasing its stream.
    playGeneration += 1;
    starting = false;
    paused = false;
    player.stop();
    $('#output-level').value = 0;
    if (!draft.tracks.length) { metadataSuppressed = false; requestAppliedEffectMetadata(); setNotice('Add a note track or open the musical demo, then press Play.'); return; }
    nativeSnapshot = { state: 'starting' };
    if (hasSources()) setNotice('Starting live sources…');
    playState.textContent = browserStreaming ? 'Starting browser stream…' : 'Starting native audio…';
    syncStatus();
    try {
      const seconds = hasSources() ? Math.max(...SessionEditor.sources(draft).map(track => track.device.duration_frames)) / draft.sample_rate : 60;
      const untilStopped = untilStoppedAvailable && SessionEditor.arrangement(draft) && draft.tracks.every(track => untilStoppedDevices.includes(track.device.kind)) && !hasSources() && !SessionEditor.plugins(draft).length;
      await nativeCommand({ action: 'play', ...(untilStopped ? {until_stopped:true} : {seconds}), volume: Number($('#monitor-volume').value), ...(beatControlsEligible() ? {metronome: $('#metronome-enabled').checked, count_in_bars: Number($('#count-in-bars').value)} : {}), ...(hasSources() ? {source_mode: 'live'} : {}) });
      metadataSuppressed = false;
    } catch (error) {
      metadataSuppressed = false;
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
      const inspected = await inspectCurrentSession();
      const session = inspected.session;
      if (session && !SessionEditor.supported(session)) {
        invalidateEffectMetadata();
        unsupportedSession = true;
        mixerView?.render(session, {locked: true});
        emptyEl.hidden = true;
        $('#track-count').textContent = `${session.tracks.length} ${session.tracks.length === 1 ? "track" : "tracks"} · read-only`;
        selectSampleRate(session.sample_rate);
        setNotice('This session contains devices or clips this editor cannot yet edit. Use the scripting interface to preserve its complete state.', true);
        return;
      }
      const validation = validateSession(session);
      if (validation) throw new Error(`The server returned an invalid session: ${validation}`);
      invalidateEffectMetadata();
      editHistory.reset();
      resetNoteProject();
      automationViews = []; automationDraftStates.clear();
      applied = clone(session);
      draft = clone(session);
      rememberEffects();
      configureSessionMode();
      selectSampleRate(draft.sample_rate);
      suggestArrangementDuration();
      arrangementView?.reportError('');
      renderTracks();
      setNotice('Connected. Your session is ready to edit.');
    } catch (error) {
      announceError(`Could not load the current session. ${error.message}`);
    } finally {
      setBusy(false);
      syncStatus();
      requestAppliedEffectMetadata();
    }
  }

  mixerView = MixerView.create($('#mixer'), {editor: SessionEditor, onEdit: applyMixerEdit, onError: announceError});
  arrangementView = ArrangementView.create($('#arrangement'), {
    transportContainer: $('#timeline-transport'), stepContainer: $('#step-input-controls'),
    onSeek: frame => timelineCommand('/api/transport/seek', {frame}),
    onLoop: region => timelineCommand('/api/transport/loop', {region}),
    onEdit: action => editArrangement(action),
    onExportMidi: selection => exportSelectedMidi(selection),
    onSelectionChange: () => { syncNoteInput(); syncRecordingControls(); },
  });
  noteInput = new NoteInput({
    getTarget: noteInputTarget,
    getPerformanceTarget: recordingInputTarget,
    onPerformance: event => { if (!recordingStarting) noteRecording?.capture(event); },
    requestPreview: async note => {
      const response = await request('/api/note/preview', {method: 'POST', signal: note.signal, body: JSON.stringify({
        expected_revision: note.target.expected_revision, track_id: note.target.track_id, frequency_hz: note.frequency_hz, velocity: note.velocity})});
      return response.arrayBuffer();
    },
    onNote: async note => {
      $('#note-input-status').classList.remove('error');
      $('#note-input-status').textContent = `Previewing MIDI ${note.midi} · velocity ${note.velocity.toFixed(2)}.`;
      await captureStepNote(note);
    },
    onError: noteInputError,
    onMIDIStatus: status => {
      const messages = {connecting: 'Requesting MIDI access…', connected: 'MIDI connected. Enable Keys to preview or enter notes.',
        unsupported: 'Web MIDI is unavailable in this browser. Computer-keyboard notes still work.', unavailable: 'MIDI access was unavailable. Computer-keyboard notes still work.'};
      $('#midi-status').textContent = messages[status] || 'MIDI disconnected.';
      $('#connect-midi-button').textContent = status === 'connected' ? 'MIDI · on' : status === 'connecting' ? 'MIDI · …' : 'MIDI · off';
      $('#connect-midi-button').title = $('#midi-status').textContent;
    },
  });
  noteRecording = new NoteRecording({onStatus: message => { $('#record-notes-status').textContent = message; }});
  $('#record-notes-button').addEventListener('click', () => { void startNoteRecording(); });
  $('#apply-take-button').addEventListener('click', () => { void applyNoteTake(); });
  $('#discard-take-button').addEventListener('click', () => { noteRecording.cancel(); stopNoteInput(); syncStatus(); });
  $('#note-input-enabled').addEventListener('change', event => {
    syncNoteInput();
    if (event.target.checked) void noteInput.unlock();
  });
  $('#note-input-octave').addEventListener('change', event => { noteInput.setBaseMidi(Number(event.target.value)); syncNoteInput(); });
  $('#note-preview-button').addEventListener('click', () => {
    $('#note-input-enabled').checked = true;
    syncNoteInput();
    void noteInput.play(noteInputTarget()?.drum ? 36 : Number($('#note-input-octave').value));
  });
  $('#connect-midi-button').addEventListener('click', () => { void noteInput.connectMIDI(); });
  $('#step-entry-enabled').addEventListener('change', event => {
    if (event.target.checked) { $('#note-input-enabled').checked = true; syncNoteInput(); void noteInput.unlock(); }
  });
  $('#new-arrangement-button').addEventListener('click', () => { void replaceWithArrangement(); });
  $('#demo-arrangement-button').addEventListener('click', () => { void replaceWithArrangement(true); });
  $('#add-note-track-button').addEventListener('click', () => { void editArrangement({type: 'addTrack'}); });
  $('#undo-button').addEventListener('click', () => { void traverseHistory('undo'); });
  $('#redo-button').addEventListener('click', () => { void traverseHistory('redo'); });
  $('#stop-edit-button').addEventListener('click', async () => {
    if (await stopNative()) {
      arrangementView?.setState({locked: false});
      const target = $('#arrangement .piano-roll-svg [aria-pressed="true"]') || $('#arrangement .arrangement-svg [aria-pressed="true"]') || $('#arrangement [data-field="track"]');
      target?.focus();
      setNotice('Playback stopped. Edit the selected phrase, then press Play.');
    }
  });
  $('#stop-button').addEventListener('click', () => { if (nativeActive()) void stopNative(); else { stopNoteInput(); stopLive(); } });
  document.addEventListener('keydown', event => {
    if (event.target.closest('input, textarea, select') || busy || editLocked()) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault(); void traverseHistory(event.shiftKey ? 'redo' : 'undo');
    }
  });

  $('#add-track-button').addEventListener('click', addTrack);
  $('#add-csound-button').addEventListener('click', () => $('#csound-file').click());
  $('#import-pd-button').addEventListener('click', () => $('#pd-file').click());
  $('#pd-file').addEventListener('change', event => {
    const file = event.target.files[0]; event.target.value = ''; void importPdPreset(file);
  });
  $('#import-midi-button').addEventListener('click', () => $('#midi-file').click());
  $('#midi-file').addEventListener('change', event => { const file = event.target.files[0]; event.target.value = ''; void importMidiFile(file); });
  $('#import-audio-button').addEventListener('click', () => $('#audio-file').click());
  $('#audio-file').addEventListener('change', event => {
    const file = event.target.files[0]; event.target.value = ''; void importAudioFile(file);
  });
  $('#csound-file').addEventListener('change', event => {
    const file = event.target.files[0]; event.target.value = ''; void addCsoundFile(file);
  });
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
    noteInput.setVolume(volume);
    if (outputMode.value === 'native' && nativeAvailable && nativeActive()) {
      clearTimeout(nativeVolumeTimer);
      nativeVolumeTimer = setTimeout(() => {
        void nativeCommand({ action: 'volume', volume }).catch(error => announceError(`Could not change native listening volume. ${error.message}`));
      }, 60);
    }
  });
  outputMode.addEventListener('change', () => { void changeOutputMode(); });
  window.addEventListener('pagehide', () => {
    noteInput.dispose();
    stopLive();
    if (nativeAvailable && nativeActive()) {
      const headers = new Headers({ 'Content-Type': 'application/json' });
      if (token) headers.set('X-DAW-Token', token);
      void fetch(browserStreaming ? '/api/stream' : '/api/transport', { method: 'POST', headers, body: JSON.stringify(browserStreaming ? {action:'stop',stream_id:streamPlayer.id} : {action:'stop'}), keepalive: true }).catch(() => {});
    }
  });
  document.addEventListener('keydown', event => {
    if (event.code === 'Escape') {
      if (!noteRecording?.active) stopNoteInput();
      if (outputMode.value === 'native') void stopNative();
      else stopLive();
    }
  });
  $('#empty-add-button').addEventListener('click', () => { void editArrangement({type:'addTrack'}); });
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
    for (const input of document.querySelectorAll('.frequency-control .number-input')) input.max = String(Math.floor(draft.sample_rate / 2) - 1);
    markEdited();
  });
  durationInput.addEventListener('input', () => durationInput.setCustomValidity(''));
  window.addEventListener('beforeunload', (event) => {
    if (!isDirty() && !noteRecording?.active && !noteRecording?.pending) return;
    event.preventDefault();
    event.returnValue = '';
  });


  function studioMessage(role, text) {
    const log = $('#studio-conversation');
    log.append(element('p', 'studio-message', `${role}: ${text}`));
    while (log.children.length > 12) log.firstElementChild.remove();
    log.scrollTop = log.scrollHeight;
  }

  function studioHasDrafts() {
    return isDirty() || mixerView?.hasDrafts() || arrangementView?.hasDrafts() || automationViews.some(({view}) => view.getDraftState().drafts.length);
  }

  function studioScope() {
    const value = $('#studio-scope').value;
    if (value === 'session') return null;
    const selected = arrangementView?.getSelection();
    const track = selected && applied?.tracks[selected.trackIndex];
    if (!track) throw Error('Select a clip in the arrangement to scope this prompt.');
    return {track_id: track.id, ...(value === 'clip' ? {clip_id: selected.clipId} : {})};
  }

  async function studioCommand(op) {
    const args = op.args;
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('Invalid command arguments.');
    const allowed = {seek:['frame'],loop:['region'],monitor:['volume'],export:['seconds']}[op.name] || [];
    if (Object.keys(args).some(key=>!allowed.includes(key)) || Object.keys(args).length !== allowed.length) throw Error('Invalid command arguments.');
    if (op.name !== 'stop' && op.name !== 'discardTake' && (busy || historyAction)) throw Error('Wait for the current session operation.');
    if (!['stop','applyTake','discardTake'].includes(op.name) && noteRecording?.pending) throw Error('Apply or discard the pending take first.');
    if (!['stop','pause','monitor'].includes(op.name) && studioHasDrafts()) throw Error('Apply your typed drafts before running this command.');
    const click = id => { const button = $(id); if (!button || button.disabled) throw Error('This control is unavailable for the current session.'); button.click(); };
    switch (op.name) {
      case 'stop': if (nativeActive()) await stopNative(); else { stopNoteInput(); stopLive(); } break;
      case 'play':
        if (nativeSnapshot.state === 'playing' || (player.context && !paused)) break;
        if (outputMode.value === 'native') await toggleNative(); else await playLive(); break;
      case 'pause':
        if (nativeSnapshot.state === 'playing') await toggleNative(); else if (player.context && !paused) await toggleLive(); break;
      case 'undo': if ($('#undo-button').disabled) throw Error('Undo is unavailable.'); await traverseHistory('undo'); break;
      case 'redo': if ($('#redo-button').disabled) throw Error('Redo is unavailable.'); await traverseHistory('redo'); break;
      case 'save': if (saveButton.disabled) throw Error('Stop playback before saving.'); await saveSession(); break;
      case 'export':
        if (renderButton.disabled || !Number.isFinite(args.seconds) || args.seconds <= 0 || args.seconds > SessionEditor.exportMaximum(draft,renderLimits)) throw Error('Invalid or unavailable WAV export.');
        durationInput.value = String(args.seconds); await renderAudio(); break;
      case 'importWav': case 'loadProject': {
        const button = $('#studio-file-action'), target = op.name === 'importWav' ? '#import-audio-button' : '#load-button';
        if ($(target).disabled) throw Error('Stop playback before choosing a file.');
        button.dataset.target = target; button.textContent = op.name === 'importWav' ? 'Choose WAV for studio' : 'Choose project for studio'; button.hidden = false; button.disabled = false;
        return 'Click the studio file button to choose your local file.';
      }
      case 'newArrangement': if (nativeLocked()) throw Error('Stop playback first.'); await replaceWithArrangement(); break;
      case 'demo': if (nativeLocked()) throw Error('Stop playback first.'); await replaceWithArrangement(true); break;
      case 'preview': click('#note-preview-button'); break;
      case 'connectMidi': click('#connect-midi-button'); break;
      case 'recordNotes': if ($('#record-notes-button').disabled) throw Error('Select a clip and enable keyboard/MIDI input first.'); await startNoteRecording(); break;
      case 'applyTake': await applyNoteTake(); break;
      case 'discardTake': click('#discard-take-button'); break;
      case 'seek':
        if (!Number.isSafeInteger(args.frame) || args.frame < 0 || !nativeActive()) throw Error('Seek requires active native playback and a nonnegative frame.');
        await timelineCommand('/api/transport/seek',args); break;
      case 'loop':
        if (!nativeActive() || (args.region !== null && (!args.region || !Number.isSafeInteger(args.region.start_frame) || !Number.isSafeInteger(args.region.end_frame) || args.region.start_frame < 0 || args.region.end_frame <= args.region.start_frame))) throw Error('Loop requires active native playback and valid frames.');
        await timelineCommand('/api/transport/loop',args); break;
      case 'monitor':
        if (!Number.isFinite(args.volume) || args.volume < 0 || args.volume > 1) throw Error('Listening volume must be between 0 and 1.');
        $('#monitor-volume').value = String(args.volume); $('#monitor-volume').dispatchEvent(new Event('input')); break;
      default: throw Error('Unknown studio command.');
    }
  }

  async function speakStudio(text) {
    const response = await request('/api/studio/speak', {method:'POST', body:JSON.stringify({text:text.slice(0,4000)})});
    const audio = $('#studio-audio'); audio.pause();
    if (studioAudioUrl) URL.revokeObjectURL(studioAudioUrl);
    studioAudioUrl = URL.createObjectURL(await response.blob());
    audio.src = studioAudioUrl; audio.hidden = false;
    try { await audio.play(); } catch (_) { $('#studio-status').textContent += ' Press Play on the spoken reply.'; }
  }

  async function sendStudioPrompt() {
    if (studioPending || !studioConfig?.available) return;
    const submittedText = $('#studio-prompt').value, prompt = submittedText.trim();
    if (!prompt) return;
    studioPending = true; $('#studio-send').disabled = true; $('#studio-clear').disabled = true;
    $('#studio-status').textContent = 'Studio agents are working…';
    let resultText = '', progressText = 'Starting studio…';
    const progressStarted = Date.now();
    const progressTimer = setInterval(() => {
      $('#studio-status').textContent = `${progressText} · ${Math.floor((Date.now() - progressStarted) / 1000)}s elapsed`;
    }, 1000);
    try {
      if (!sessionRevision || busy || studioRecorder || studioMicStarting || studioTranscribing) throw Error('Wait for the current session or microphone operation.');
      const scope = studioScope(), revision = sessionRevision, before = JSON.stringify(draft), generation = noteProjectGeneration;
      studioMessage('You',prompt);
      const response = await request('/api/studio/prompt', {method:'POST',headers:{Accept:'application/x-ndjson'},body:JSON.stringify({prompt,role:$('#studio-role').value,scope,expected_revision:revision,history:studioHistory.slice(-6)})});
      let plan, streamed = false;
      if (response.headers?.get('Content-Type')?.includes('application/x-ndjson')) {
        streamed = true;
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let pending = '', bytes = 0, terminal = false;
        try {
          while (true) {
            const {value, done} = await reader.read();
            if (done) break;
            bytes += value.byteLength;
            if (bytes > 2 * 1024 * 1024) throw Error('Studio progress exceeded the size limit. No studio edits applied.');
            pending += decoder.decode(value, {stream:true});
            let newline;
            while ((newline = pending.indexOf('\n')) >= 0) {
              const event = JSON.parse(pending.slice(0, newline)); pending = pending.slice(newline + 1);
              if (terminal) throw Error('Invalid studio stream. No studio edits applied.');
              if (event.type === 'error') throw Error(event.error);
              if (event.type === 'result') { plan = event.plan; terminal = true; }
              else if (['progress', 'summary', 'delegation'].includes(event.type) && typeof event.message === 'string') {
                progressText = event.message;
                $('#studio-status').textContent = progressText;
                if (event.type !== 'progress') studioMessage(`${event.role} · proposal`, event.message);
              } else throw Error('Invalid studio progress. No studio edits applied.');
            }
          }
          if (!terminal || pending.trim()) throw Error('Studio connection ended before a complete result. No studio edits applied.');
        } finally { await reader.cancel(); }
      } else plan = await response.json();
      clearInterval(progressTimer);
      if (plan.revision !== revision || sessionRevision !== revision || before !== JSON.stringify(draft) || generation !== noteProjectGeneration || JSON.stringify(scope) !== JSON.stringify(studioScope())) throw Error('Project or selection changed while agents were working. No studio edits applied; retry your prompt.');
      if (!Array.isArray(plan.parts)) throw Error('Invalid studio response.');
      const operations = plan.parts.flatMap(part=>part.operations);
      if (!streamed) for (const part of plan.parts) studioMessage(part.role,part.reply);
      if (operations.some(op=>op.op === 'command')) {
        if (operations.length !== 1 || plan.parts.length !== 1 || plan.parts[0].role !== 'producer' || scope) throw Error('A producer command must run alone at whole-session scope.');
        setNotice('');
        const commandResult = await studioCommand(operations[0]);
        resultText = noticeEl.classList.contains('error') ? noticeEl.textContent : commandResult || 'Studio command dispatched to the existing control.';
      } else if (operations.length) {
        if (busy || editLocked() || player.context || starting || noteRecording?.pending || historyAction || unsupportedSession || studioHasDrafts()) throw Error('Stop playback and apply typed drafts or resolve the pending take first. No studio edits applied.');
        const next = StudioActions.apply(applied,plan.parts,scope,studioConfig.operations,SessionEditor);
        draft = next;
        // Leave existing controls intact until success; failed edits preserve typed fields.
        if (!await applyDraft({readControls:false})) { draft = JSON.parse(before); syncStatus(); throw Error(noticeEl.textContent || 'Studio edit failed.'); }
        resultText = `${operations.length} studio edits applied as one Undo entry. Download your project to keep them.`;
      } else resultText = 'Studio replied without changing the project.';
      const reply = plan.parts.map(part=>`${part.role}: ${part.reply}`).join('\n');
      studioHistory.push({role:'user',content:prompt},{role:'assistant',content:`${reply}\nOutcome: ${resultText}`.slice(0,4000)});
      studioHistory = studioHistory.slice(-6);
      if ($('#studio-prompt').value === submittedText) $('#studio-prompt').value = '';
      studioMessage('Studio',resultText);
      $('#studio-status').textContent = resultText;
      if ($('#studio-spoken').checked && studioConfig.voice_available) {
        try { await speakStudio(`${resultText} ${reply}`); } catch (error) { $('#studio-status').textContent = `${resultText} Voice reply failed: ${error.message}`; }
      }
    } catch (error) { studioMessage('Studio',error.message); $('#studio-status').textContent = error.message; }
    finally { clearInterval(progressTimer); studioPending = false; $('#studio-send').disabled = !studioConfig?.available; $('#studio-clear').disabled = false; }
  }

  async function toggleStudioMic() {
    if (studioRecorder) { studioRecorder.stop(); return; }
    if (studioMicStarting || studioPending || studioTranscribing) return;
    const originalPrompt = $('#studio-prompt').value;
    studioMicStarting = true;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw Error('Microphone capture is unavailable in this browser. Type your prompt instead.');
      $('#studio-audio').pause();
      studioMicStream = await navigator.mediaDevices.getUserMedia({audio:true});
      const mimeType = ['audio/webm','audio/mp4','audio/ogg'].find(type=>MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw Error('No supported microphone recording format.');
      const chunks = []; let bytes = 0, recordingFailed = false;
      const recorder = new MediaRecorder(studioMicStream,{mimeType}); studioRecorder = recorder;
      recorder.addEventListener('dataavailable', event=>{ bytes += event.data.size; if (bytes <= 4 * 1024 * 1024) chunks.push(event.data); else { recordingFailed = true; if (recorder.state !== 'inactive') recorder.stop(); } });
      recorder.addEventListener('error',()=>{recordingFailed=true; if (recorder.state !== 'inactive') recorder.stop();});
      recorder.addEventListener('stop',async()=> {
        clearTimeout(studioMicTimer); studioMicStream?.getTracks().forEach(track=>track.stop()); studioMicStream = null; studioRecorder = null;
        studioTranscribing = true;
        $('#studio-mic').textContent = 'Record voice prompt'; $('#studio-mic').disabled = true; $('#studio-send').disabled = true;
        try {
          if (recordingFailed) throw Error('Microphone recording failed or exceeded 4 MiB. Try a shorter prompt.');
          $('#studio-status').textContent = 'Transcribing your voice prompt…';
          const blob = new Blob(chunks,{type:mimeType});
          const response = await request('/api/studio/transcribe',{method:'POST',headers:{'Content-Type':mimeType},body:blob});
          const result = await response.json();
          if ($('#studio-prompt').value === originalPrompt) {
            $('#studio-prompt').value = result.text; $('#studio-status').textContent = 'Transcript ready. Review it, then Send to studio.';
          } else {
            studioMessage('Voice transcript',result.text); $('#studio-status').textContent = 'Transcript ready in the conversation. Your typed prompt was preserved.';
          }
        } catch(error) { $('#studio-status').textContent = error.message; }
        finally { studioTranscribing = false; $('#studio-mic').disabled = !studioConfig?.voice_available; $('#studio-send').disabled = !studioConfig?.available; }
      });
      recorder.start(250); $('#studio-mic').textContent = 'Stop voice recording';
      $('#studio-status').textContent = 'Recording microphone · stops after 30 seconds. Stop to transcribe.';
      studioMicTimer = setTimeout(()=>{if(recorder.state !== 'inactive') recorder.stop();},30000);
    } catch(error) { studioMicStream?.getTracks().forEach(track=>track.stop()); studioMicStream=null; studioRecorder=null; $('#studio-status').textContent=error.message; }
    finally { studioMicStarting = false; }
  }

  $('#studio-file-action').addEventListener('click',()=> { const button = $('#studio-file-action'), target = $(button.dataset.target); if (target && !target.disabled) { target.click(); button.hidden = true; } });
  $('#studio-send').addEventListener('click',()=>{void sendStudioPrompt();});
  $('#studio-prompt').addEventListener('keydown',event=>{if(event.key==='Enter' && (event.metaKey || event.ctrlKey)){event.preventDefault();void sendStudioPrompt();}});
  $('#studio-mic').addEventListener('click',()=>{void toggleStudioMic();});
  $('#studio-clear').addEventListener('click',()=>{studioHistory=[];$('#studio-conversation').replaceChildren();$('#studio-audio').pause();$('#studio-audio').hidden=true;});
  void (async()=>{
    try {
      studioConfig = await (await request('/api/studio')).json();
      $('#studio-send').disabled = !studioConfig.available; $('#studio-mic').disabled = !studioConfig.voice_available; $('#studio-spoken').disabled = !studioConfig.voice_available;
      $('#studio-status').textContent = studioConfig.available ? `OpenCode Zen · ${studioConfig.model}${studioConfig.voice_available ? ' · ElevenLabs key configured' : ' · set ELEVEN_API_KEY for voice'}` : 'Set OPENCODE_API_KEY in the server environment and restart to enable studio agents.';
    } catch(error) { $('#studio-status').textContent = `Studio unavailable: ${error.message}`; }
  })();

  renderTracks();
  void loadCurrentSession();
  capabilitiesLoading = loadCapabilities();
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
        setNotice(`Native status is unavailable. Playback may still be active. ${nativeSnapshot.until_stopped ? 'Until-stopped playback requires Stop or engine shutdown.' : 'Its finite playback deadline still applies.'} Try Stop or restart the studio.`, true);
      }
    } finally { nativePollInFlight = false; }
  }, 300);
})();

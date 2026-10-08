/* Frame-anchored arrangement authoring. The engine validates each edit before commit. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  function create(container, callbacks = {}) {
    let session = null, options = {}, selection = null, selectedNote = null;
    let spanFrames = 1, playhead = null, loopOverlay = null, timelineHeight = 1;
    let observedLoopKey = null, confirmedLoopKey = 'off';
    let noteBaseline = null, baselineNoteKey = null, audioBaseline = null, baselineAudioKey = null, localMessage = '';
    let gesture = null, rollLayout = null, suppressClick = false;
    let stepSelectionKey = null;
    const noteDevices = ['sine', 'drumkit', 'synth', 'pd_instrument'];
    container.innerHTML = `
      <div class="arrangement-heading"><div><p class="eyebrow">Arrange · 4 beats per bar</p><h2 class="arrangement-title">Note arrangement</h2></div><output class="position-readout" aria-label="Playhead position">Beat 0.00</output></div>
      <p class="arrangement-help">Drag clips to move; drag their right edge to resize. Click or draw in the piano roll to add notes. Positions are measured from zero. Tempo changes the grid; saved notes keep their timing.</p>
      <div class="arrangement-controls">
        <label>Tempo (BPM)<input data-field="tempo" type="number" min="20" max="300" step="0.001" value="120"></label><button data-action="tempo" class="button button-quiet">Set tempo</button>
        <label>Grid<select data-field="grid"><option value="240">¼ beat</option><option value="480">½ beat</option><option value="960">1 beat</option><option value="0">No snap</option></select></label>
        <label>Seek (beat)<input data-field="seek" type="number" min="0" step="0.25" value="0"></label><button data-action="seek" class="button button-quiet">Seek</button>
        <label>Loop start<input data-field="loopStart" type="number" min="0" step="0.25" value="0"></label><label>Loop end<input data-field="loopEnd" type="number" min="0" step="0.25" value="4"></label>
        <button data-action="loop" class="button button-quiet">Set loop</button><button data-action="clearLoop" class="button button-quiet">Clear loop</button>
      </div>
      <p class="arrangement-status" role="status" aria-live="polite"></p>
      <p class="loop-status" role="status" aria-live="polite">Loop off</p>
      <div class="timeline-scroll"><svg class="arrangement-svg" role="group" aria-label="Track lanes and beat ruler"></svg></div>
      <div class="arrangement-controls"><label>Selected clip<select data-field="selectedClip" aria-label="Select any clip, including overlapping copies"></select></label></div>
      <div class="arrangement-controls clip-creator"><label>Note track<select data-field="track"></select></label><label>Insert at (beat)<input data-field="insertBeat" type="number" min="0" step="0.25" value="0"></label><button data-action="addClip" class="button button-quiet">Add 4-beat clip</button></div>
      <section class="clip-editor" hidden><h3 class="clip-heading"></h3>
        <div class="arrangement-controls"><label>Clip start (beat)<input data-field="clipStart" type="number" min="0" step="0.25"></label><button data-action="moveClip" class="button button-quiet">Move clip</button><label>Clip length (beats)<input data-field="clipLength" type="number" min="0.001" step="0.25"></label><button data-action="resizeClip" class="button button-quiet">Resize clip</button><button data-action="duplicateClip" class="button button-quiet">Duplicate clip</button><button data-action="deleteClip" class="button button-quiet">Delete clip</button></div>
        <div class="timeline-scroll note-roll"><svg class="piano-roll-svg" role="group" aria-label="Piano roll; select a note to edit"></svg></div>
        <div class="arrangement-controls note-controls"><label>MIDI pitch<input data-field="pitch" type="number" min="0" max="127" step="1" value="60"></label><label>Note start (beat)<input data-field="noteStart" type="number" min="0" step="0.25" value="0"></label><label>Duration (beats)<input data-field="noteLength" type="number" min="0.001" step="0.25" value="0.5"></label><label>Velocity<input data-field="velocity" type="number" min="0" max="1" step="0.05" value="0.8"></label><button data-action="addNote" class="button button-primary">Add note</button><button data-action="editNote" class="button button-quiet">Update selected note</button><button data-action="deleteNote" class="button button-quiet">Delete selected note</button><button data-action="quantizeClip" class="button button-quiet" title="Move all note starts to the selected grid; preserve durations and pitch.">Quantize clip</button><button data-action="exportMidiClip" class="button button-quiet">Export clip MIDI</button></div>
        <p class="clip-edit-status note-edit-status" role="status" aria-live="polite" hidden></p>
        <div class="arrangement-controls step-controls"><label>Step position in clip (beat)<input data-field="stepBeat" type="number" min="0" step="0.25" value="0"></label><label>Gate (grid steps)<input data-field="stepGate" type="number" min="1" max="64" step="1" value="1"></label><output class="step-readout" aria-live="polite"></output></div>
        <div class="arrangement-controls audio-controls" hidden><label class="audio-source-info">Source<output class="audio-source-path"></output></label><label>Source offset (frames)<input data-field="audioOffset" type="number" min="0" step="1"></label><label>Clip gain<input data-field="audioGain" type="number" min="0" max="1" step="0.05"></label><label>Fade in (frames)<input data-field="audioFadeIn" type="number" min="0" step="1"></label><label>Fade out (frames)<input data-field="audioFadeOut" type="number" min="0" step="1"></label><button data-action="editAudioClip" class="button button-quiet">Update audio clip</button></div>
        <p class="clip-edit-status audio-edit-status" role="status" aria-live="polite" hidden></p>
        <p class="audio-fade-help arrangement-help" hidden>Linear fades reach silence at the clip edges. 0 turns a fade off; the two fades together must fit inside the clip.</p>
        <p class="quantize-help arrangement-help">Export clip MIDI downloads applied notes from clip zero at 960 ticks per beat. Pitches round to MIDI semitones and velocities to 1–127; silent notes are omitted. Sounds, effects and mix stay in the project. Quantize clip moves all note starts to the nearest Grid line from clip zero, with ties moved later. Durations, pitch and velocity stay exact. Choose a grid; No snap disables quantize. Notes must still fit inside the clip.</p>
        <p class="note-device-help arrangement-help"></p>
        <p class="note-pitch-detail arrangement-help" aria-live="polite"></p>
        <p class="note-help arrangement-help">C4 = MIDI 60. Notes must fit inside the clip. Duplicate places a copy directly after the original. Native seek and loops do not retrigger notes that started before the destination; WAV exports ignore the live loop.</p>
      </section>`;
    const find = selector => container.querySelector(selector);
    const field = name => find(`[data-field="${name}"]`);
    const value = name => {
      const text = field(name).value;
      if (!text.trim() || !Number.isFinite(Number(text))) throw new Error('Enter a number for every field used by this action.');
      return Number(text);
    };
    const status = find('.arrangement-status');
    const svg = find('.arrangement-svg'), roll = find('.piano-roll-svg');
    roll.setAttribute('tabindex','0');
    roll.setAttribute('data-focus-key','piano-roll');
    const beats = frame => frame * (session?.tempo_milli_bpm || 120000) / ((session?.sample_rate || 48000) * 60000);
    const frames = beat => {
      if (!Number.isFinite(beat) || beat < 0) throw new Error('Enter a nonnegative beat position.');
      let ticks = Math.round(beat * 960);
      const grid = value('grid');
      if (grid) ticks = Math.round(ticks / grid) * grid;
      if (window.SessionEditor?.ticksToFrames) return window.SessionEditor.ticksToFrames(session, ticks);
      const numerator = BigInt(ticks) * BigInt(session.sample_rate) * 60000n;
      const denominator = 960n * BigInt(session.tempo_milli_bpm || 120000);
      const frame = Number((2n * numerator + denominator) / (2n * denominator));
      if (!Number.isSafeInteger(frame)) throw new Error('Beat position exceeds the session range.');
      return frame;
    };
    const fmt = frame => Number(beats(frame).toFixed(5));
    const clip = () => selection && session?.tracks[selection.trackIndex]?.clips?.find(c => c.id === selection.clipId);
    function getNoteTarget() {
      const selected = clip(), track = selection && session?.tracks[selection.trackIndex];
      if (selected?.kind !== 'notes' || track?.mode !== 'sequenced' || !noteDevices.includes(track.device?.kind)) return null;
      return {...selection, trackId: track.id, device: track.device};
    }
    function stepTarget() {
      const target = getNoteTarget(); if (!target) return null;
      const selected = clip(), grid = value('grid') || 240, gate = value('stepGate'), beat = value('stepBeat');
      if (!Number.isInteger(gate) || gate < 1 || gate > 64) throw new Error('Step gate must be 1–64 grid steps.');
      if (beat < 0) throw new Error('Step position must be nonnegative.');
      const startTicks = Math.round(beat * 960 / grid) * grid, endTicks = startTicks + gate * grid;
      if (![startTicks,endTicks].every(Number.isSafeInteger)) throw new Error('Step position exceeds the session range.');
      // Round both absolute tick endpoints; rounding a duration independently drifts at fractional tempos.
      const convert = ticks => window.SessionEditor?.ticksToFrames ? window.SessionEditor.ticksToFrames(session,ticks) : Number((2n * BigInt(ticks) * BigInt(session.sample_rate) * 60000n + 960n * BigInt(session.tempo_milli_bpm || 120000)) / (1920n * BigInt(session.tempo_milli_bpm || 120000)));
      const start_frame = convert(startTicks), end = convert(endTicks);
      if (!Number.isSafeInteger(end) || end <= start_frame || end > selected.length_frames) throw new Error('Step note must fit inside the clip. Move the step position back to continue.');
      const key = JSON.stringify([selection,selected.start_frame,selected.length_frames,session.sample_rate,session.tempo_milli_bpm,startTicks,endTicks,grid]);
      return {trackIndex:target.trackIndex,clipId:target.clipId,start_frame,duration_frames:end-start_frame,key,nextBeat:endTicks/960};
    }
    function getStepTarget() {
      if (options.locked) throw new Error('Step entry is available only while editing is stopped.');
      return stepTarget();
    }
    function acceptStepAdvance(target) {
      let current; try { current = stepTarget(); } catch (_) { return false; }
      if (!current || target?.key !== current.key) return false;
      field('stepBeat').value = current.nextBeat;
      drawRoll(); return true;
    }
    function drawStepCursor(height, selected) {
      try {
        const target = stepTarget(); if (!target) return;
        const x = 60 + target.start_frame / selected.length_frames * 930;
        node(roll,'line',{x1:x,x2:x,y1:24,y2:height,stroke:'currentColor','stroke-width':2,'pointer-events':'none','aria-hidden':'true'});
        find('.step-readout').textContent = `Next: beat ${target.start_frame === 0 ? 0 : beats(target.start_frame).toFixed(3)} · gate ${beats(target.duration_frames).toFixed(3)} beats${value('grid') ? '' : ' · No snap uses ¼-beat steps'}`;
      } catch (error) { find('.step-readout').textContent = error.message; }
    }
    function checkAudioFades(selected, patch = {}) {
      const length = patch.length_frames ?? selected.length_frames;
      const fadeIn = patch.fade_in_frames ?? selected.fade_in_frames ?? 0;
      const fadeOut = patch.fade_out_frames ?? selected.fade_out_frames ?? 0;
      if (![fadeIn, fadeOut].every(frame => Number.isSafeInteger(frame) && frame >= 0)) throw new Error('Audio fades must be nonnegative safe frame counts.');
      if (fadeIn > length || fadeOut > length || fadeIn > length - fadeOut) throw new Error('Audio fades together must fit inside the clip length.');
    }
    const node = (parent, name, attrs, text) => {
      const el = document.createElementNS(NS, name);
      Object.entries(attrs || {}).forEach(([key, val]) => el.setAttribute(key, val));
      if (text != null) el.textContent = text;
      parent.append(el); return el;
    };
    function activate(el, fn) {
      el.setAttribute('tabindex', '0'); el.setAttribute('role', 'button');
      el.addEventListener('click', event => { if (suppressClick) { suppressClick = false; event.preventDefault(); return; } fn(); });
      el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } });
    }
    function showStatus() {
      for (const [selector, audio] of [['.note-edit-status',false],['.audio-edit-status',true]]) {
        const feedback = find(selector); feedback.textContent = localMessage; feedback.hidden = !localMessage || (clip()?.kind === 'audio') !== audio;
      }
      status.textContent = localMessage || (options.locked ? 'Editing is locked during playback or a session operation.' : 'Edits are checked and applied to the session before they appear here.');
      if (!localMessage && options.transportAvailable === false) status.textContent += ' Start native playback or pause it to enable seek and loop controls.';
    }
    function reportError(error) { localMessage = error?.message || String(error); showStatus(); }
    function rememberFocus() {
      const active = document.activeElement;
      if (!container.contains(active)) return null;
      return active?.getAttribute?.('data-focus-key');
    }
    function restoreFocus(key) {
      if (!key) return;
      [...container.querySelectorAll('[data-focus-key]')].find(el => el.getAttribute('data-focus-key') === key)?.focus();
    }
    function loadNoteFields(n) {
      field('pitch').value = Math.round(69 + 12 * Math.log2(n.frequency_hz / 440));
      field('noteStart').value = fmt(n.start_frame); field('noteLength').value = fmt(n.duration_frames); field('velocity').value = n.velocity;
      noteBaseline = Object.fromEntries(['pitch','noteStart','noteLength','velocity'].map(key => [key, String(field(key).value)]));
      baselineNoteKey = JSON.stringify([selection, session.tempo_milli_bpm, n]);
    }
    function selectClip(trackIndex, clipId) { selection = { trackIndex, clipId }; selectedNote = null; noteBaseline = null; baselineNoteKey = null; audioBaseline = null; baselineAudioKey = null;
      const drums = session.tracks[trackIndex].device?.kind === 'drumkit';
      field('pitch').value = drums ? 36 : 60;
      field('noteLength').value = drums ? 0.25 : 0.5; render(session, options); callbacks.onSelectionChange?.(getNoteTarget()); }
    function edit(type, extra = {}) {
      localMessage = ''; showStatus();
      const result = callbacks.onEdit?.({ type, ...selection, ...extra });
      if (result?.then) result.catch(reportError);
      return result;
    }
    function point(surface, event) {
      const p = surface.createSVGPoint(); p.x = event.clientX; p.y = event.clientY;
      return p.matrixTransform(surface.getScreenCTM().inverse());
    }
    function cancelGesture() {
      if (!gesture) return;
      const g = gesture; gesture = null;
      if (g.original) for (const [key, val] of Object.entries(g.original)) g.rect.setAttribute(key, val);
      else g.rect?.remove();
      if (g.group) g.group.setAttribute('transform','');
      if (g.handle && g.handleX != null) { g.handle.setAttribute('x',g.handleX); g.handle.setAttribute('y',g.handleY); }
      g.surface.releasePointerCapture?.(g.pointerId);
      suppressClick = false;
    }
    function beginGesture(surface, event, data) {
      if (options.locked || gesture || (event.button !== undefined && event.button !== 0)) return;
      event.preventDefault(); event.stopPropagation();
      const start = point(surface,event);
      gesture = {...data, surface, start, pointerId:event.pointerId, moved:false, action:null};
      if (data.handle) { gesture.handleX = data.handle.getAttribute('x'); gesture.handleY = data.handle.getAttribute('y'); }
      if (data.rect) gesture.original = Object.fromEntries(['x','y','width'].map(key=>[key,data.rect.getAttribute(key)]));
      surface.setPointerCapture?.(event.pointerId);
    }
    function previewGesture(event) {
      const g = gesture; if (!g || event.pointerId !== g.pointerId) return;
      try {
        const current = point(g.surface,event), dx = current.x - g.start.x, dy = current.y - g.start.y;
        g.moved = g.moved || Math.abs(dx) >= 3 || Math.abs(dy) >= 3;
        if (!g.moved && g.kind !== 'draw') return;
        g.action = null;
        const snap = frame => frames(beats(Math.max(0,frame)));
        if (g.kind === 'clip') {
          if (g.resize && Math.abs(dx) >= 3) {
            const length_frames = snap(g.saved.start_frame + g.saved.length_frames + dx / 840 * spanFrames) - g.saved.start_frame;
            if (length_frames <= 0) throw new Error('Clip length must stay positive.');
            if (g.saved.kind === 'audio') checkAudioFades(g.saved, {length_frames});
            if (length_frames !== g.saved.length_frames) g.action = {type:'resizeClip',trackIndex:g.trackIndex,clipId:g.saved.id,length_frames};
            g.rect.setAttribute('width',Math.max(5,length_frames/spanFrames*840));
            g.handle?.setAttribute('x',Number(g.original.x)+Math.max(5,length_frames/spanFrames*840)-5);
          } else if (Math.abs(dx) >= 3) {
            const start_frame = snap(g.saved.start_frame + dx / 840 * spanFrames);
            if (start_frame !== g.saved.start_frame) g.action = {type:'moveClip',trackIndex:g.trackIndex,clipId:g.saved.id,start_frame};
            g.group?.setAttribute('transform',`translate(${150+start_frame/spanFrames*840-Number(g.original.x)},0)`);
          }
        } else if (g.kind === 'note') {
          const patch = {};
          if (g.resize && Math.abs(dx) >= 3) {
            const duration_frames = snap(g.saved.start_frame + g.saved.duration_frames + dx / 930 * g.length) - g.saved.start_frame;
            if (duration_frames <= 0) throw new Error('Note gate must stay positive.');
            if (duration_frames !== g.saved.duration_frames) patch.duration_frames = duration_frames;
          } else if (!g.resize) {
            if (Math.abs(dx) >= 3) { const start_frame = snap(g.saved.start_frame + dx / 930 * g.length); if (start_frame !== g.saved.start_frame) patch.start_frame = start_frame; }
            const index = Math.max(0,Math.min(g.layout.rowPitches.length-1,g.rowIndex+Math.round(dy/g.layout.rowHeight)));
            if (index !== g.rowIndex) patch.frequency_hz = 440*Math.pow(2,(g.layout.rowPitches[index]-69)/12);
          }
          if (Object.keys(patch).length) g.action = {type:'editNote',...selection,noteId:g.saved.id,patch};
          g.rect.setAttribute('x',60+(patch.start_frame ?? g.saved.start_frame)/g.length*930);
          g.rect.setAttribute('width',Math.max(4,(patch.duration_frames ?? g.saved.duration_frames)/g.length*930));
          const index = patch.frequency_hz ? g.layout.rowPitches.indexOf(Math.round(69+12*Math.log2(patch.frequency_hz/440))) : g.rowIndex;
          g.rect.setAttribute('y',25+index*g.layout.rowHeight);
          g.handle?.setAttribute('x',60+((patch.start_frame ?? g.saved.start_frame)+(patch.duration_frames ?? g.saved.duration_frames))/g.length*930-4);
          g.handle?.setAttribute('y',25+index*g.layout.rowHeight);
        } else {
          const start_frame = g.note.start_frame;
          const end = snap((current.x-60)/930*g.length);
          const duration_frames = g.moved ? end-start_frame : g.note.duration_frames;
          if (duration_frames <= 0) throw new Error('Draw a positive note gate to the right.');
          g.action = {type:'addNote',...selection,note:{...g.note,duration_frames}};
          g.rect.setAttribute('width',Math.max(4,duration_frames/g.length*930));
        }
        g.error = null;
      } catch (error) { g.error = error; g.action = null; }
    }
    for (const surface of [svg,roll]) {
      surface.addEventListener('pointermove',previewGesture);
      surface.addEventListener('pointercancel',cancelGesture);
      // Captured pointer releases can dispatch their click to the SVG itself.
      surface.addEventListener('click',()=>{ suppressClick = false; });
      surface.addEventListener('pointerup',event=>{
        if (!gesture || event.pointerId !== gesture.pointerId) return;
        previewGesture(event);
        const g = gesture, action = g.action; cancelGesture();
        suppressClick = g.moved;
        if (g.error) { reportError(g.error); return; }
        if (action && !options.locked) edit(action.type,action);
        else if (!g.moved && g.kind === 'note') { drawRoll(); syncDisabled(); restoreFocus(`note:${g.saved.id}`); }
        else if (!g.moved && g.kind === 'clip') { selectClip(g.trackIndex,g.saved.id); restoreFocus(`clip:${g.trackIndex}:${g.saved.id}`); }
      });
    }
    roll.addEventListener('pointerdown',event=>{
      if (options.locked || !rollLayout || !clip() || event.target.closest('.roll-note')) return;
      try {
        const p = point(roll,event); if (p.x < 60 || p.x > 990) return;
        const layout = rollLayout, index = Math.floor((p.y-24)/layout.rowHeight);
        if (index < 0 || index >= layout.rowPitches.length) return;
        const selected = clip(), start_frame = frames(beats((p.x-60)/930*selected.length_frames));
        const duration_frames = Math.min(selected.length_frames-start_frame,Math.max(1,frames(value('noteLength'))));
        if (duration_frames <= 0) throw new Error('Choose a note position inside the clip.');
        const pitch = layout.rowPitches[index], note = {start_frame,duration_frames,frequency_hz:440*Math.pow(2,(pitch-69)/12),velocity:value('velocity')};
        roll.focus();
        beginGesture(roll,event,{kind:'draw',length:selected.length_frames,note});
        if (!gesture) return;
        gesture.rect = node(roll,'rect',{x:60+start_frame/selected.length_frames*930,y:25+index*layout.rowHeight,width:Math.max(4,duration_frames/selected.length_frames*930),height:layout.rowHeight-2,class:'gesture-preview'});
      } catch(error) { reportError(error); }
    });
    function drawRoll() {
      const focusKey = rememberFocus();
      roll.replaceChildren();
      rollLayout = null;
      const selected = clip(); field('selectedClip').value = selection ? `${selection.trackIndex}:${selection.clipId}` : ''; find('.clip-editor').hidden = !selected;
      const audio = selected?.kind === 'audio';
      for (const selector of ['.note-roll', '.note-controls', '.step-controls', '.note-device-help', '.note-pitch-detail', '.quantize-help', '.note-help']) find(selector).hidden = audio;
      find('.audio-controls').hidden = !audio;
      find('.audio-fade-help').hidden = !audio;
      find('.clip-creator').hidden = audio;
      if (!selected) return;
      find('.clip-heading').textContent = `${session.tracks[selection.trackIndex].id} / ${selected.id}`;
      find('.note-device-help').textContent = session.tracks[selection.trackIndex].device?.kind === 'drumkit'
        ? 'Factory drum pads: MIDI 36 = kick · 38 = snare · 42 = closed hat.' : session.tracks[selection.trackIndex].device?.kind === 'pd_instrument' ? 'Pd instrument · monophonic. Note gates cannot overlap, including across clips; gates last at least 64 frames.' : '';
      if (audio) {
        find('.audio-source-path').textContent = selected.source_path;
        const key = JSON.stringify([selection, session.tempo_milli_bpm, selected]);
        if (baselineAudioKey !== key) {
          field('clipStart').value = fmt(selected.start_frame); field('clipLength').value = fmt(selected.length_frames);
          field('audioOffset').value = selected.source_offset_frames; field('audioGain').value = selected.gain;
          field('audioFadeIn').value = selected.fade_in_frames ?? 0; field('audioFadeOut').value = selected.fade_out_frames ?? 0;
          audioBaseline = Object.fromEntries(['clipStart','clipLength','audioOffset','audioGain','audioFadeIn','audioFadeOut'].map(name => [name, String(field(name).value)]));
          baselineAudioKey = key;
        }
        return;
      }
      field('clipStart').value = fmt(selected.start_frame); field('clipLength').value = fmt(selected.length_frames);
      const stepKey = JSON.stringify([selection.trackIndex, selection.clipId]);
      if (stepSelectionKey !== stepKey) { field('stepBeat').value = 0; stepSelectionKey = stepKey; }
      const notes = selected.notes || [];
      const activeNote = notes.find(n => n.id === selectedNote);
      const drums = session.tracks[selection.trackIndex].device?.kind === 'drumkit';
      if (activeNote) {
        const midi = Math.round(69 + 12 * Math.log2(activeNote.frequency_hz / 440));
        const cents = 1200 * Math.log2(activeNote.frequency_hz / (440 * Math.pow(2, (midi - 69) / 12)));
        find('.note-pitch-detail').textContent = `Saved pitch: ${activeNote.frequency_hz} Hz · ${cents >= 0 ? '+' : ''}${cents.toFixed(2)} cents from MIDI ${midi}. Changing MIDI pitch sets an exact equal-tempered pitch.`;
      } else find('.note-pitch-detail').textContent = '';
      if (activeNote && baselineNoteKey !== JSON.stringify([selection, session.tempo_milli_bpm, activeNote])) loadNoteFields(activeNote);
      const pitches = notes.map(n => Math.round(69 + 12 * Math.log2(n.frequency_hz / 440)));
      const displayPitches = pitches.map(p => Math.max(0, Math.min(127, p)));
      const low = Math.min(48, ...displayPitches), high = Math.max(72, ...displayPitches);
      const rowHeight = drums ? 24 : 13;
      const rowPitches = drums ? [42, 38, 36] : Array.from({length: high - low + 1}, (_, i) => high - i);
      rollLayout = {rowPitches,rowHeight};
      const rowY = pitch => 24 + rowPitches.indexOf(pitch) * rowHeight;
      const height = rowPitches.length * rowHeight + 24;
      roll.setAttribute('viewBox', `0 0 1000 ${height}`);
      rowPitches.forEach(pitch => {
        const y = rowY(pitch);
        node(roll, 'rect', { x: 0, y, width: 1000, height: rowHeight, class: !drums && [1,3,6,8,10].includes(pitch % 12) ? 'roll-row roll-black' : 'roll-row' });
        if (drums) node(roll, 'text', {x: 4,y: y + 16,class: 'ruler-text'}, `${({36:'Kick',38:'Snare',42:'Hat'})[pitch]} ${pitch}`);
        else if (pitch % 12 === 0) node(roll, 'text', {x: 8,y: y + 10,class: 'ruler-text'}, `C${Math.floor(pitch / 12) - 1}`);
      });
      const beatLength = beats(selected.length_frames), step = Math.max(1, Math.ceil(beatLength / 64));
      for (let b = 0; b <= beatLength; b += step) {
        const x = 60 + b / beatLength * 930;
        node(roll, 'line', {x1:x,x2:x,y1:24,y2:height,class:'beat-grid'});
        node(roll, 'text', {x:x > 960 ? x - 3 : x + 3,y:16,class:'ruler-text','text-anchor':x > 960 ? 'end' : 'start'}, b);
      }
      notes.forEach((n, i) => {
        const group = node(roll, 'g', {class: `roll-note ${selectedNote === n.id ? 'selected' : ''}`, 'data-focus-key': `note:${n.id}`, 'aria-pressed': String(selectedNote === n.id), 'aria-label': `Note ${n.id}, MIDI ${pitches[i]}, beat ${fmt(n.start_frame)}`});
        const noteRect = node(group, 'rect', {x:60+n.start_frame/selected.length_frames*930,y:rowY(displayPitches[i])+1,width:Math.max(4,n.duration_frames/selected.length_frames*930),height:rowHeight-2,rx:2});
        const noteHandle = node(group,'rect',{x:60+(n.start_frame+n.duration_frames)/selected.length_frames*930-4,y:rowY(displayPitches[i])+1,width:8,height:rowHeight-2,class:'resize-handle','data-resize':'true'});
        group.addEventListener('pointerdown',event=>{
          if (options.locked) return;
          selectedNote = n.id; loadNoteFields(n); group.focus(); callbacks.onSelectionChange?.(getNoteTarget());
        beginGesture(roll,event,{kind:'note',saved:n,rect:noteRect,handle:noteHandle,resize:event.target.getAttribute('data-resize') === 'true',length:selected.length_frames,layout:rollLayout,rowIndex:rowPitches.indexOf(displayPitches[i])});
        });
        activate(group, () => {
          selectedNote = n.id; loadNoteFields(n); drawRoll(); syncDisabled();
          restoreFocus(`note:${n.id}`); callbacks.onSelectionChange?.(getNoteTarget());
        });
      });
      drawStepCursor(height, selected);
      restoreFocus(focusKey);
    }
    function syncDisabled() {
      container.querySelectorAll('button').forEach(button => {
        const action = button.dataset.action;
        const transport = ['seek','loop','clearLoop'].includes(action);
        button.disabled = transport ? options.transportAvailable === false || !!options.pending : !!options.locked;
        if (['editNote','deleteNote'].includes(action) && !selectedNote) button.disabled = true;
        if (action === 'exportMidiClip' && clip()?.kind !== 'notes') button.disabled = true;
        if (action === 'quantizeClip' && (clip()?.kind !== 'notes' || !clip()?.notes?.length || !Number(field('grid').value))) button.disabled = true;
        if (action === 'addClip' && !session?.tracks.some(t => t.mode === 'sequenced' && noteDevices.includes(t.device?.kind))) button.disabled = true;
      });
      container.querySelectorAll('input, select').forEach(input => { input.disabled = !!options.locked && !['seek','loopStart','loopEnd'].includes(input.dataset.field); });
    }
    function setState(state = {}) {
      const previousLocked = options.locked; options = {...options,...state}; if (options.locked && gesture) cancelGesture(); syncDisabled();
      if (previousLocked !== options.locked) showStatus();
    }
    function updateLoop(region, pending, syncFields = false) {
      options.loop = region;
      const key = region ? `${region.start_frame}:${region.end_frame}` : 'off';
      const loopPending = pending && key !== confirmedLoopKey;
      if (!pending) confirmedLoopKey = key;
      if (loopOverlay) {
        loopOverlay.setAttribute('class', loopPending ? 'loop-region loop-pending' : 'loop-region');
        loopOverlay.setAttribute('visibility', region ? 'visible' : 'hidden');
        if (region) {
          loopOverlay.setAttribute('x', 150 + region.start_frame / spanFrames * 840);
          loopOverlay.setAttribute('width', (region.end_frame - region.start_frame) / spanFrames * 840);
        }
      }
      find('.loop-status').textContent = region
        ? `Loop ${loopPending ? 'requested' : 'active'}: beat ${fmt(region.start_frame)}–${fmt(region.end_frame)}${loopPending ? ' · waiting for audio callback' : ''}`
        : (loopPending ? 'Loop off requested · waiting for audio callback' : 'Loop off');
      if (syncFields && key !== observedLoopKey && region) {
        field('loopStart').value = fmt(region.start_frame); field('loopEnd').value = fmt(region.end_frame);
      }
      observedLoopKey = key;
    }
    function updateTransport(transport = {}) {
      if ('locked' in transport || 'pending' in transport || 'transportAvailable' in transport) setState(transport);
      const frame = transport.timeline_frame ?? transport.position_frames ?? transport.frame ?? options.frame ?? 0;
      options.frame = Number(frame) || 0;
      if ('loop_region' in transport || 'loop' in transport) updateLoop(transport.loop_region ?? transport.loop ?? null, transport.timeline_command_pending === true, true);
      else if ('timeline_command_pending' in transport) updateLoop(options.loop || null, transport.timeline_command_pending === true);
      find('.position-readout').textContent = `Beat ${beats(options.frame).toFixed(2)} · frame ${options.frame}`;
      if (playhead) { const x = 150 + options.frame / spanFrames * 840; playhead.setAttribute('x1', x); playhead.setAttribute('x2', x); }
    }
    function render(next, state = {}) {
      if (gesture) cancelGesture();
      const focusKey = rememberFocus();
      session = next; options = { ...state }; if (!session) return;
      if (!clip()) { const hadSelection = !!selection; selection = null; selectedNote = null; stepSelectionKey = null; if (hadSelection) callbacks.onSelectionChange?.(null); }
      if (selectedNote && !clip()?.notes?.some(n => n.id === selectedNote)) selectedNote = null;
      field('tempo').value = (session.tempo_milli_bpm || 120000) / 1000;
      showStatus();
      find('.arrangement-title').textContent = session.tracks.some(track => track.device?.kind === 'audio') ? 'Arrangement' : 'Note arrangement';
      svg.replaceChildren();
      const end = Math.max(0, ...session.tracks.flatMap(t => (t.clips || []).map(c => c.start_frame + c.length_frames)));
      const totalBeats = Math.max(16, Math.ceil(beats(end) / 4) * 4);
      spanFrames = totalBeats * session.sample_rate * 60000 / (session.tempo_milli_bpm || 120000);
      const height = 36 + Math.max(1,session.tracks.length) * 64;
      svg.setAttribute('viewBox', `0 0 1000 ${height}`);
      const ruler = node(svg, 'rect', {x:150,y:0,width:840,height:32,class:'timeline-ruler'});
      ruler.addEventListener('click', event => {
        const point = svg.createSVGPoint(); point.x = event.clientX; point.y = event.clientY;
        const x = point.matrixTransform(svg.getScreenCTM().inverse()).x;
        if (options.transportAvailable !== false && !options.pending) callbacks.onSeek?.(frames(Math.max(0,(x - 150) / 840 * totalBeats)));
      });
      const step = totalBeats <= 16 ? 1 : Math.ceil(totalBeats / 16 / 4) * 4;
      for (let b = 0; b <= totalBeats; b += step) {
        const x = 150 + b / totalBeats * 840;
        node(svg, 'line', {x1:x,x2:x,y1:32,y2:height,class:'beat-grid'});
        // The endpoint grid line remains; a label here clips or overlaps its neighbour.
        if (x <= 960) node(svg, 'text', {x:x+4,y:21,class:'ruler-text'}, b % 4 === 0 ? `${Math.floor(b / 4) + 1} · ${b}` : String(b));
      }
      session.tracks.forEach((track, trackIndex) => {
        const y = 36 + trackIndex * 64;
        const clips = track.clips || [];
        const overlaps = clips.map(c => clips.filter(other => other !== c && c.start_frame < other.start_frame + other.length_frames && other.start_frame < c.start_frame + c.length_frames).length);
        const overlapCount = overlaps.filter(count => count > 0).length;
        node(svg, 'rect', {x:0,y,width:1000,height:64,class:'track-lane'});
        node(svg, 'text', {x:12,y:y+25,class:'lane-name'}, track.id);
        node(svg, 'text', {x:12,y:y+43,class:'ruler-text'}, track.device?.kind === 'audio' ? 'AUDIO' : track.mode === 'sequenced' ? `${track.device?.kind === 'pd_instrument' ? 'Pd instrument' : track.device?.kind || 'Instrument'} notes` : 'Continuous');
        if (overlapCount) node(svg,'text',{x:12,y:y+59,class:'overlap-label'},`${overlapCount} overlapping clips`);
        clips.forEach((c, clipIndex) => {
          const group = node(svg, 'g', {class:`timeline-clip ${selection?.trackIndex === trackIndex && selection?.clipId === c.id ? 'selected' : ''}`, 'data-focus-key':`clip:${trackIndex}:${c.id}`, 'aria-pressed':String(selection?.trackIndex === trackIndex && selection?.clipId === c.id), 'aria-label':`${track.id}, clip ${c.id}, beat ${fmt(c.start_frame)}, length ${fmt(c.length_frames)}`});
          if (overlaps[clipIndex]) {
            group.setAttribute('class', `${group.getAttribute('class')} overlapping`);
            const description = `Overlaps ${overlaps[clipIndex]} other clip${overlaps[clipIndex] === 1 ? '' : 's'}. Use Selected clip to reach each clip.`;
            group.setAttribute('aria-description', description);
            node(group,'title',{},description);
          }
          const x = 150+c.start_frame/spanFrames*840, width = Math.max(5,c.length_frames/spanFrames*840);
          const clipRect = node(group,'rect',{x,y:y+8,width,height:48,rx:5});
          const clipLabel = c.id;
          node(group,'text',{x:x+7,y:y+28,class:'clip-label'}, clipLabel.slice(0,Math.max(0,Math.floor(width/7)-2)));
          (c.notes || []).slice(0,128).forEach(n => node(group,'line',{x1:x+n.start_frame/spanFrames*840,x2:x+(n.start_frame+n.duration_frames)/spanFrames*840,y1:y+41,y2:y+41,class:'clip-note-preview'}));
          if (c.kind === 'audio') {
            const fadeAttrs = {stroke:'currentColor','stroke-width':1.5,'pointer-events':'none','aria-hidden':'true'};
            if (c.fade_in_frames > 0) node(group,'line',{...fadeAttrs,x1:x,y1:y+54,x2:x+width*c.fade_in_frames/c.length_frames,y2:y+10});
            if (c.fade_out_frames > 0) node(group,'line',{...fadeAttrs,x1:x+width*(1-c.fade_out_frames/c.length_frames),y1:y+10,x2:x+width,y2:y+54});
          }
          if (['notes', 'audio'].includes(c.kind)) {
            const clipHandle = node(group,'rect',{x:x+width-5,y:y+8,width:10,height:48,class:'resize-handle','data-resize':'true'});
            group.addEventListener('pointerdown',event=>{
              if (options.locked) return;
              if (selection?.trackIndex !== trackIndex || selection?.clipId !== c.id) { selection = {trackIndex,clipId:c.id}; selectedNote = null; drawRoll(); syncDisabled(); callbacks.onSelectionChange?.(getNoteTarget()); }
              group.focus();
              beginGesture(svg,event,{kind:'clip',saved:c,rect:clipRect,handle:clipHandle,group,resize:event.target.getAttribute('data-resize') === 'true',trackIndex});
            });
            activate(group, () => { selectClip(trackIndex,c.id); restoreFocus(`clip:${trackIndex}:${c.id}`); });
          }
        });
      });
      if (!session.tracks.length) node(svg,'text',{x:165,y:70,class:'ruler-text'},'Add a note track, then create a clip.');
      timelineHeight = height;
      loopOverlay = node(svg,'rect',{y:30,height:timelineHeight-30,class:'loop-region',visibility:'hidden'});
      updateLoop(options.loop || null, options.pending === true, true);
      playhead = node(svg,'line',{y1:0,y2:height,class:'playhead','aria-hidden':'true'});
      field('selectedClip').replaceChildren();
      const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Select a clip'; field('selectedClip').append(placeholder);
      session.tracks.forEach((track,index) => (track.clips || []).forEach(clip => {
        const option = document.createElement('option'); option.value = `${index}:${clip.id}`; option.textContent = `${track.id} / ${clip.id} · beat ${fmt(clip.start_frame)}`; field('selectedClip').append(option);
      }));
      const oldTrack = field('track').value; field('track').replaceChildren();
      session.tracks.forEach((track,index) => {
        if (track.mode !== 'sequenced' || !noteDevices.includes(track.device?.kind)) return;
        const option = document.createElement('option'); option.value = index; option.textContent = track.id; field('track').append(option);
      });
      if ([...field('track').options].some(o=>o.value===oldTrack)) field('track').value = oldTrack;
      drawRoll(); syncDisabled(); updateTransport({frame:options.frame}); restoreFocus(focusKey);
    }
    container.addEventListener('change', event => {
      if (event.target.dataset?.field === 'selectedClip') {
        const key = event.target.value, separator = key.indexOf(':');
        const index = Number(key.slice(0,separator)), id = key.slice(separator+1);
        if (separator >= 0 && session?.tracks[index]?.clips?.some(clip=>clip.id === id)) selectClip(index,id);
        return;
      }
      if (['stepBeat','stepGate','grid'].includes(event.target.dataset?.field)) { drawRoll(); syncDisabled(); }
    });
    container.addEventListener('click', event => {
      const button = event.target.closest('button[data-action]'); if (!button || button.disabled || !session) return;
      try {
        const action = button.dataset.action, selected = clip();
        if (action === 'tempo') return edit('setTempo',{tempo_milli_bpm:Math.round(value('tempo')*1000)});
        if (action === 'seek') return callbacks.onSeek?.(frames(value('seek')));
        if (action === 'loop') {
          const start_frame = frames(value('loopStart')), end_frame = frames(value('loopEnd'));
          if (end_frame <= start_frame) throw new Error('Loop end must be after its start.');
          return callbacks.onLoop?.({start_frame,end_frame});
        }
        if (action === 'clearLoop') return callbacks.onLoop?.(null);
        if (action === 'addClip') return edit('addClip',{trackIndex:value('track'),start_frame:frames(value('insertBeat')),length_frames:frames(4)});
        if (!selected) return;
        if (action === 'moveClip') {
          if (selected.kind === 'audio' && String(field('clipStart').value) === audioBaseline?.clipStart) return;
          return edit(action,{start_frame:frames(value('clipStart'))});
        }
        if (action === 'resizeClip') {
          if (selected.kind === 'audio' && String(field('clipLength').value) === audioBaseline?.clipLength) return;
          const length_frames = frames(value('clipLength'));
          if (selected.kind === 'audio') checkAudioFades(selected,{length_frames});
          return edit(action,{length_frames});
        }
        if (action === 'duplicateClip') return edit(action,{start_frame:selected.start_frame+selected.length_frames});
        if (action === 'deleteClip') return edit(action);
        if (action === 'editAudioClip' && selected.kind === 'audio') {
          const changed = name => String(field(name).value) !== audioBaseline?.[name];
          const patch = {};
          if (changed('clipLength')) {
            const length = frames(value('clipLength'));
            if (!Number.isSafeInteger(length) || length <= 0) throw new Error('Audio clip length must be a positive safe frame count.');
            patch.length_frames = length;
          }
          if (changed('audioOffset')) {
            const offset = value('audioOffset');
            if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('Source offset must be a nonnegative safe frame count.');
            patch.source_offset_frames = offset;
          }
          if (!Number.isSafeInteger((patch.source_offset_frames ?? selected.source_offset_frames) + (patch.length_frames ?? selected.length_frames))) throw new Error('Audio source offset and length require a safe frame range.');
          if (changed('audioGain')) {
            const gain = value('audioGain');
            if (gain < 0 || gain > 1) throw new Error('Audio clip gain must be between 0 and 1.');
            patch.gain = gain;
          }
          for (const [name, key] of [['audioFadeIn','fade_in_frames'],['audioFadeOut','fade_out_frames']]) if (changed(name)) patch[key] = value(name);
          checkAudioFades(selected,patch);
          if (Object.keys(patch).length) return edit(action,{patch});
          localMessage = 'Selected audio clip is unchanged.'; showStatus(); return;
        }
        if (selected.kind === 'audio') return;
        if (action === 'exportMidiClip') {
          if (hasDrafts()) throw new Error('Apply or revert typed note, clip and tempo edits before exporting MIDI.');
          return callbacks.onExportMidi?.({...selection});
        }
        if (action === 'quantizeClip') {
          if (hasDrafts()) throw new Error('Apply or revert typed note, clip and tempo edits before quantizing.');
          return edit(action,{gridTicks:value('grid')});
        }
        if (action === 'deleteNote') return edit(action,{noteId:selectedNote});
        if (action === 'editNote') {
          const saved = selected.notes.find(n => n.id === selectedNote);
          if (!saved || !noteBaseline) return;
          const changed = key => String(field(key).value) !== noteBaseline[key];
          const patch = {};
          if (changed('noteStart')) patch.start_frame = frames(value('noteStart'));
          if (changed('noteLength')) {
            const start = patch.start_frame ?? saved.start_frame;
            patch.duration_frames = frames(beats(start) + value('noteLength')) - start;
          }
          if (changed('pitch')) {
            const pitch = value('pitch');
            if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127) throw new Error('MIDI pitch must be an integer from 0 to 127.');
            patch.frequency_hz = 440 * Math.pow(2, (pitch - 69) / 12);
          }
          if (changed('velocity')) patch.velocity = value('velocity');
          if (Object.keys(patch).length) return edit(action,{noteId:selectedNote,patch});
          localMessage = 'Selected note is unchanged.'; showStatus(); return;
        }
        const start_frame = frames(value('noteStart')), end_frame = frames(value('noteStart')+value('noteLength'));
        const pitch = value('pitch');
        if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127) throw new Error('MIDI pitch must be an integer from 0 to 127.');
        const note = {start_frame,duration_frames:end_frame-start_frame,frequency_hz:440*Math.pow(2,(pitch-69)/12),velocity:value('velocity')};
        if (action === 'addNote') {
          if (note.duration_frames <= 0 || start_frame < 0 || end_frame > selected.length_frames) throw new Error('Note gates must have positive duration and end inside the clip.');
          return edit(action,{note});
        }
      } catch (error) { reportError(error); }
    });
    container.addEventListener('keydown', event => {
      if (event.key === 'Escape' && gesture) { event.preventDefault(); cancelGesture(); return; }
      if (options.locked || event.target.closest('input,select,button,textarea,[contenteditable]')) return;
      if (event.key === 'Delete' || event.key === 'Backspace') {
        if (selectedNote) { event.preventDefault(); edit('deleteNote',{noteId:selectedNote}); }
        else if (clip()) { event.preventDefault(); edit('deleteClip'); }
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd' && clip()) { event.preventDefault(); edit('duplicateClip',{start_frame:clip().start_frame+clip().length_frames}); }
    });
    function hasDrafts() {
      if (session && Number(field('tempo').value) !== (session.tempo_milli_bpm || 120000) / 1000) return true;
      const selected = clip();
      if (selected && ['clipStart','clipLength'].some((key, i) => String(field(key).value) !== String(fmt(i ? selected.length_frames : selected.start_frame)))) return true;
      const baseline = selected?.kind === 'audio' ? audioBaseline : noteBaseline;
      return Boolean(baseline && Object.entries(baseline).some(([key, text]) => String(field(key).value) !== text));
    }
    return {hasDrafts,render,updateTransport,setState,reportError,getNoteTarget,getStepTarget,acceptStepAdvance,getSelection:()=>selection && {...selection,noteId:selectedNote},clearSelection:()=>{selection=null;selectedNote=null;stepSelectionKey=null;callbacks.onSelectionChange?.(null);}};
  }
  window.ArrangementView = {create};
})();

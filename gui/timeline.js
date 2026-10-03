/* Frame-anchored arrangement authoring. The engine validates each edit before commit. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  function create(container, callbacks = {}) {
    let session = null, options = {}, selection = null, selectedNote = null;
    let spanFrames = 1, playhead = null, loopOverlay = null, timelineHeight = 1;
    let observedLoopKey = null, confirmedLoopKey = 'off';
    container.innerHTML = `
      <div class="arrangement-heading"><div><p class="eyebrow">Arrange · 4 beats per bar</p><h2>Note arrangement</h2></div><output class="position-readout" aria-label="Playhead position">Beat 0.00</output></div>
      <p class="arrangement-help">Select a clip, then edit notes below. Positions are measured from zero. Tempo changes the grid; saved notes keep their timing.</p>
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
      <div class="arrangement-controls clip-creator"><label>Note track<select data-field="track"></select></label><button data-action="addClip" class="button button-quiet">Add 4-beat clip</button></div>
      <section class="clip-editor" hidden><h3 class="clip-heading"></h3>
        <div class="arrangement-controls"><label>Clip start (beat)<input data-field="clipStart" type="number" min="0" step="0.25"></label><button data-action="moveClip" class="button button-quiet">Move clip</button><label>Clip length (beats)<input data-field="clipLength" type="number" min="0.001" step="0.25"></label><button data-action="resizeClip" class="button button-quiet">Resize clip</button><button data-action="duplicateClip" class="button button-quiet">Duplicate clip</button><button data-action="deleteClip" class="button button-quiet">Delete clip</button></div>
        <div class="timeline-scroll"><svg class="piano-roll-svg" role="group" aria-label="Piano roll; select a note to edit"></svg></div>
        <div class="arrangement-controls note-controls"><label>MIDI pitch<input data-field="pitch" type="number" min="0" max="127" step="1" value="60"></label><label>Note start (beat)<input data-field="noteStart" type="number" min="0" step="0.25" value="0"></label><label>Duration (beats)<input data-field="noteLength" type="number" min="0.001" step="0.25" value="0.5"></label><label>Velocity<input data-field="velocity" type="number" min="0" max="1" step="0.05" value="0.8"></label><button data-action="addNote" class="button button-primary">Add note</button><button data-action="editNote" class="button button-quiet">Update selected note</button><button data-action="deleteNote" class="button button-quiet">Delete selected note</button></div>
        <p class="arrangement-help">C4 = MIDI 60. Notes must fit inside the clip. Duplicate places a copy directly after the original. Native seek and loops do not retrigger notes that started before the destination; WAV exports ignore the live loop.</p>
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
    const node = (parent, name, attrs, text) => {
      const el = document.createElementNS(NS, name);
      Object.entries(attrs || {}).forEach(([key, val]) => el.setAttribute(key, val));
      if (text != null) el.textContent = text;
      parent.append(el); return el;
    };
    function activate(el, fn) {
      el.setAttribute('tabindex', '0'); el.setAttribute('role', 'button');
      el.addEventListener('click', fn);
      el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } });
    }
    function selectClip(trackIndex, clipId) { selection = { trackIndex, clipId }; selectedNote = null; render(session, options); }
    function edit(type, extra = {}) { callbacks.onEdit?.({ type, ...selection, ...extra }); }
    function drawRoll() {
      roll.replaceChildren();
      const selected = clip(); find('.clip-editor').hidden = !selected;
      if (!selected) return;
      find('.clip-heading').textContent = `${session.tracks[selection.trackIndex].id} / ${selected.id}`;
      field('clipStart').value = fmt(selected.start_frame); field('clipLength').value = fmt(selected.length_frames);
      const notes = selected.notes || [];
      const pitches = notes.map(n => Math.round(69 + 12 * Math.log2(n.frequency_hz / 440)));
      const displayPitches = pitches.map(p => Math.max(0, Math.min(127, p)));
      const low = Math.min(48, ...displayPitches), high = Math.max(72, ...displayPitches);
      const height = (high - low + 1) * 13 + 24;
      roll.setAttribute('viewBox', `0 0 1000 ${height}`);
      for (let pitch = low; pitch <= high; pitch++) {
        const y = 24 + (high - pitch) * 13;
        node(roll, 'rect', { x: 0, y, width: 1000, height: 13, class: [1,3,6,8,10].includes(pitch % 12) ? 'roll-row roll-black' : 'roll-row' });
        if (pitch % 12 === 0) node(roll, 'text', {x: 8,y: y + 10,class: 'ruler-text'}, `C${Math.floor(pitch / 12) - 1}`);
      }
      const beatLength = beats(selected.length_frames), step = Math.max(1, Math.ceil(beatLength / 64));
      for (let b = 0; b <= beatLength; b += step) {
        const x = 60 + b / beatLength * 930;
        node(roll, 'line', {x1:x,x2:x,y1:24,y2:height,class:'beat-grid'});
        node(roll, 'text', {x:x + 3,y:16,class:'ruler-text'}, b);
      }
      notes.forEach((n, i) => {
        const group = node(roll, 'g', {class: `roll-note ${selectedNote === n.id ? 'selected' : ''}`, 'aria-label': `Note ${n.id}, MIDI ${pitches[i]}, beat ${fmt(n.start_frame)}`});
        node(group, 'rect', {x:60+n.start_frame/selected.length_frames*930,y:25+(high-displayPitches[i])*13,width:Math.max(4,n.duration_frames/selected.length_frames*930),height:11,rx:2});
        activate(group, () => {
          selectedNote = n.id; field('pitch').value = pitches[i]; field('noteStart').value = fmt(n.start_frame);
          field('noteLength').value = fmt(n.duration_frames); field('velocity').value = n.velocity; drawRoll(); syncDisabled();
        });
      });
    }
    function syncDisabled() {
      container.querySelectorAll('button').forEach(button => {
        const action = button.dataset.action;
        const transport = ['seek','loop','clearLoop'].includes(action);
        button.disabled = transport ? options.transportAvailable === false || !!options.pending : !!options.locked;
        if (['editNote','deleteNote'].includes(action) && !selectedNote) button.disabled = true;
        if (action === 'addClip' && !session?.tracks.some(t => t.mode === 'sequenced' && t.device?.kind === 'sine')) button.disabled = true;
      });
      container.querySelectorAll('input, select').forEach(input => { input.disabled = !!options.locked && !['seek','loopStart','loopEnd'].includes(input.dataset.field); });
    }
    function setState(state = {}) {
      const previousLocked = options.locked; options = {...options,...state}; syncDisabled();
      if (previousLocked !== options.locked) {
        status.textContent = options.locked ? 'Editing is locked during playback or a session operation.' : 'Edits are checked and applied to the session before they appear here.';
        if (options.transportAvailable === false) status.textContent += ' Start native playback or pause it to enable seek and loop controls.';
      }
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
      session = next; options = { ...state }; if (!session) return;
      if (!clip()) { selection = null; selectedNote = null; }
      if (selectedNote && !clip()?.notes?.some(n => n.id === selectedNote)) selectedNote = null;
      field('tempo').value = (session.tempo_milli_bpm || 120000) / 1000;
      status.textContent = options.locked ? 'Editing is locked during playback or a session operation.' : 'Edits are checked and applied to the session before they appear here.';
      if (options.transportAvailable === false) status.textContent += ' Start native playback or pause it to enable seek and loop controls.';
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
        node(svg, 'text', {x:x+4,y:21,class:'ruler-text'}, b % 4 === 0 ? `${Math.floor(b / 4) + 1} · ${b}` : String(b));
      }
      session.tracks.forEach((track, trackIndex) => {
        const y = 36 + trackIndex * 64;
        node(svg, 'rect', {x:0,y,width:1000,height:64,class:'track-lane'});
        node(svg, 'text', {x:12,y:y+25,class:'lane-name'}, track.id);
        node(svg, 'text', {x:12,y:y+43,class:'ruler-text'}, track.mode === 'sequenced' ? 'Sine notes' : 'Continuous');
        (track.clips || []).forEach(c => {
          const group = node(svg, 'g', {class:`timeline-clip ${selection?.trackIndex === trackIndex && selection?.clipId === c.id ? 'selected' : ''}`, 'aria-label':`${track.id}, clip ${c.id}, beat ${fmt(c.start_frame)}, length ${fmt(c.length_frames)}`});
          const x = 150+c.start_frame/spanFrames*840, width = Math.max(5,c.length_frames/spanFrames*840);
          node(group,'rect',{x,y:y+8,width,height:48,rx:5});
          node(group,'text',{x:x+7,y:y+28,class:'clip-label'}, c.id.slice(0,Math.max(0,Math.floor(width/7)-2)));
          (c.notes || []).slice(0,128).forEach(n => node(group,'line',{x1:x+n.start_frame/spanFrames*840,x2:x+(n.start_frame+n.duration_frames)/spanFrames*840,y1:y+41,y2:y+41,class:'clip-note-preview'}));
          if (c.kind === 'notes') activate(group, () => selectClip(trackIndex,c.id));
        });
      });
      if (!session.tracks.length) node(svg,'text',{x:165,y:70,class:'ruler-text'},'Add a note track, then create a clip.');
      timelineHeight = height;
      loopOverlay = node(svg,'rect',{y:30,height:timelineHeight-30,class:'loop-region',visibility:'hidden'});
      updateLoop(options.loop || null, options.pending === true, true);
      playhead = node(svg,'line',{y1:0,y2:height,class:'playhead','aria-hidden':'true'});
      const oldTrack = field('track').value; field('track').replaceChildren();
      session.tracks.forEach((track,index) => {
        if (track.mode !== 'sequenced' || track.device?.kind !== 'sine') return;
        const option = document.createElement('option'); option.value = index; option.textContent = track.id; field('track').append(option);
      });
      if ([...field('track').options].some(o=>o.value===oldTrack)) field('track').value = oldTrack;
      drawRoll(); syncDisabled(); updateTransport({frame:options.frame});
    }
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
        if (action === 'addClip') return edit('addClip',{trackIndex:value('track'),start_frame:frames(value('seek')),length_frames:frames(4)});
        if (!selected) return;
        if (action === 'moveClip') return edit(action,{start_frame:frames(value('clipStart'))});
        if (action === 'resizeClip') return edit(action,{length_frames:frames(value('clipLength'))});
        if (action === 'duplicateClip') return edit(action,{start_frame:selected.start_frame+selected.length_frames});
        if (action === 'deleteClip') return edit(action);
        if (action === 'deleteNote') return edit(action,{noteId:selectedNote});
        const start_frame = frames(value('noteStart')), end_frame = frames(value('noteStart')+value('noteLength'));
        const pitch = value('pitch');
        if (!Number.isInteger(pitch) || pitch < 0 || pitch > 127) throw new Error('MIDI pitch must be an integer from 0 to 127.');
        const note = {start_frame,duration_frames:end_frame-start_frame,frequency_hz:440*Math.pow(2,(pitch-69)/12),velocity:value('velocity')};
        if (action === 'addNote') return edit(action,{note});
        if (action === 'editNote') return edit(action,{noteId:selectedNote,patch:note});
      } catch (error) { status.textContent = error.message; }
    });
    container.addEventListener('keydown', event => {
      if (options.locked || event.target.closest('input,select,button')) return;
      if (event.key === 'Delete' || event.key === 'Backspace') {
        if (selectedNote) { event.preventDefault(); edit('deleteNote',{noteId:selectedNote}); }
        else if (clip()) { event.preventDefault(); edit('deleteClip'); }
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd' && clip()) { event.preventDefault(); edit('duplicateClip',{start_frame:clip().start_frame+clip().length_frames}); }
    });
    return {render,updateTransport,setState,getSelection:()=>selection && {...selection,noteId:selectedNote},clearSelection:()=>{selection=null;selectedNote=null;}};
  }
  window.ArrangementView = {create};
})();

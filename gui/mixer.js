/* Saved mixer controls; meter updates never rebuild inputs or consume numeric drafts. */
(function (root) {
  'use strict';
  function create(container, {editor, onEdit, onError}) {
    const doc = container.ownerDocument;
    let session = null, locked = false, pending = false;
    let controls = [], numeric = [], meters = new Map(), master;
    const el = (tag, className, text) => {
      const node = doc.createElement(tag); node.className = className || '';
      if (text !== undefined) node.textContent = text;
      return node;
    };
    function meter(label) {
      const box = el('span', 'mixer-meter'); box.setAttribute('aria-label', label);
      const left = el('meter'), right = el('meter'), readout = el('span', 'mixer-peak');
      for (const bar of [left, right]) { bar.min = 0; bar.max = 1; bar.value = 0; }
      left.setAttribute('aria-label', `${label} left`); right.setAttribute('aria-label', `${label} right`);
      const bars = el('span', 'mixer-meter-bars'); bars.append(left, right);
      box.append(bars, readout);
      return {box, left, right, readout};
    }
    function updateMeter(target, value) {
      if (!target) return;
      const peaks = Array.isArray(value?.peak) && value.peak.length === 2 && value.peak.every(n => Number.isFinite(n) && n >= 0) ? value.peak : [0,0];
      target.left.value = Math.min(1, peaks[0]); target.right.value = Math.min(1, peaks[1]);
      const clipped = value?.clipped === true;
      target.box.className = `mixer-meter${clipped ? ' clipped' : ''}`;
      target.readout.textContent = `${peaks.map(n => n ? `${(20 * Math.log10(n)).toFixed(1)} dB` : '−∞ dB').join(' / ')}${clipped ? ' · CLIP' : ''}`;
    }
    function setState(state = {}) {
      locked = state.locked === true;
      for (const control of controls) control.disabled = locked || pending || !editor.mixerSupported(session);
    }
    async function edit(index, patch = {}) {
      if (locked || pending || !editor.mixerSupported(session)) return false;
      pending = true; setState({locked});
      try {
        let next = session;
        // Commit all visible number drafts together so a toggle/redraw cannot lose them.
        for (const row of numeric) {
          const changes = {};
          for (const key of ['gain','pan']) {
            const text = String(row[key].value).trim(), value = text ? Number(text) : NaN;
            const saved = session.tracks[row.index].mixer?.[key] ?? (key === 'gain' ? 1 : 0);
            if (value !== saved) changes[key] = value;
          }
          if (Object.keys(changes).length) next = editor.editMixer(next,row.index,changes);
        }
        if (Object.keys(patch).length) next = editor.editMixer(next,index,patch);
        if (next === session) return true;
        if (await onEdit(next) === false) return false;
        return true;
      } catch (error) { onError(error.message); return false; }
      finally { pending = false; setState({locked}); }
    }
    function render(next, state = {}) {
      session = next; controls = []; numeric = []; meters = new Map();
      container.replaceChildren(); container.hidden = !session?.tracks?.length;
      const header = el('div', 'mixer-heading');
      header.append(el('strong', '', 'Mixer'));
      header.title = 'Faders apply on release. Enter, blur or Apply all numbers commits numeric drafts across all strips. Save to keep the mix.';
      container.append(header);
      if (!editor.mixerSupported(session)) {
        container.append(el('p','output-hint','Mixer requires built-in instruments/audio with supported effects. This session is preserved.'));
        master = null; setState(state); updateMeters(null, 'stopped'); return;
      }
      const bank = el('div', 'mixer-bank'); container.append(bank);
      // Master is metering only: saved track levels determine the exported mix.
      const masterStrip = el('div', 'mixer-strip mixer-master');
      masterStrip.append(el('span','mixer-track','Master'),el('span','mixer-channel-type','PRE-MONITOR'));
      master = meter('Master peak'); masterStrip.append(master.box);
      masterStrip.append(el('span','mixer-master-note','Mix output'),el('span','mixer-master-note','Listening volume is separate'));
      bank.append(masterStrip);
      session.tracks.forEach((track, index) => {
        const row = el('div', 'mixer-strip');
        const name = el('span','mixer-track',track.id); name.title = track.id;
        row.append(name,el('span','mixer-channel-type',`CH ${String(index + 1).padStart(2, '0')} · ${track.device?.kind === 'pd_instrument' ? 'Pd instrument' : track.device?.kind || 'audio'}`));
        const numbers = {index}; numeric.push(numbers);
        const mix = track.mixer || {gain:1,pan:0,mute:false,solo:false};
        const exact = el('div','mixer-exact');
        for (const [key, min, max, title] of [['gain',0,2,'Gain'],['pan',-1,1,'Pan']]) {
          const label = el('label','',title), input = el('input','number-input');
          input.type = 'number'; input.min = min; input.max = max; input.step = '0.01'; input.value = mix[key];
          input.setAttribute('aria-label', `${track.id} mixer ${key}`);
          numbers[key] = input;
          input.addEventListener('change',()=>edit(index));
          input.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); return edit(index); }
          });
          label.append(input); exact.append(label); controls.push(input);
        }
        const pan = el('input','mixer-pan'); pan.type = 'range'; pan.min = -1; pan.max = 1; pan.step = '0.01'; pan.value = mix.pan;
        pan.setAttribute('aria-label', `${track.id} pan slider`);
        const panLabel = el('label','mixer-pan-control');
        const panScale = el('span','mixer-pan-scale'); panScale.append(el('span','','L'),el('span','','PAN'),el('span','','R'));
        panLabel.append(panScale,pan); row.append(panLabel);
        const toggles = el('div','mixer-toggles');
        const apply = el('button','button button-quiet mixer-apply','Apply all numbers'); apply.type = 'button';
        apply.title = 'Apply gain and pan number drafts across every mixer strip.';
        apply.setAttribute('aria-label', `${track.id} apply mixer values`);
        apply.addEventListener('click',()=>edit(index)); controls.push(apply);
        for (const key of ['mute','solo']) {
          const button = el('button', `button button-quiet mixer-toggle mixer-${key}${mix[key] ? ' active' : ''}`, key === 'mute' ? 'M' : 'S'); button.type = 'button';
          button.title = key === 'mute' ? 'Mute' : 'Solo';
          button.setAttribute('aria-label', `${track.id} ${key}`); button.setAttribute('aria-pressed', String(mix[key]));
          button.addEventListener('click', () => edit(index, {[key]: !(session.tracks[index].mixer?.[key] || false)}));
          controls.push(button); toggles.append(button);
        }
        const gain = el('input','mixer-fader'); gain.type = 'range'; gain.min = 0; gain.max = 2; gain.step = '0.01'; gain.value = mix.gain;
        gain.setAttribute('aria-label', `${track.id} gain fader`); gain.setAttribute('aria-orientation','vertical');
        for (const [key, slider] of [['gain',gain],['pan',pan]]) {
          slider.addEventListener('input',()=> {
            if (!locked && !pending) numbers[key].value = slider.value;
          });
          slider.addEventListener('change',()=> {
            if (locked || pending) return false;
            numbers[key].value = slider.value; return edit(index);
          });
          slider.addEventListener('keydown',event=> {
            if (event.key === 'Enter') { event.preventDefault(); return edit(index); }
          });
          numbers[key].addEventListener('input',()=> {
            const value = Number(numbers[key].value);
            if (String(numbers[key].value).trim() && Number.isFinite(value) && value >= Number(slider.min) && value <= Number(slider.max)) slider.value = value;
          });
          controls.push(slider);
        }
        const level = el('div','mixer-level');
        const scale = el('div','mixer-fader-scale'); scale.setAttribute('aria-hidden','true');
        scale.append(el('span','','2'),el('span','','1'),el('span','','0'));
        const trackMeter = meter(`${track.id} peak`); meters.set(track.id, trackMeter);
        level.append(scale,gain,trackMeter.box);
        row.append(toggles,level,exact,apply); bank.append(row);
      });
      setState(state); updateMeters(null, 'stopped');
    }
    function updateMeters(snapshot, state) {
      const values = state === 'playing' || state === 'paused' ? snapshot : null;
      updateMeter(master, values?.master);
      const byId = new Map((values?.tracks || []).map(track => [track.id, track]));
      for (const [id, target] of meters) updateMeter(target, byId.get(id));
    }
    function hasDrafts() {
      return numeric.some(row => ['gain','pan'].some(key => {
        const text = String(row[key].value).trim();
        return !text || Number(text) !== (session.tracks[row.index].mixer?.[key] ?? (key === 'gain' ? 1 : 0));
      }));
    }
    return {render, setState, updateMeters, hasDrafts};
  }
  const api = {create};
  if (typeof module !== 'undefined') module.exports = api;
  else root.MixerView = api;
})(typeof window === 'undefined' ? globalThis : window);

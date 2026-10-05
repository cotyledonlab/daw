/* Exact saved step automation for one gain effect. Edits use the checked session callback. */
(function (root) {
  'use strict';
  function create(container, {editor, onEdit, onError, trackIndex, effectId, draftState}) {
    const doc = container.ownerDocument;
    let session, locked = false, pending = false, controls = [], message = draftState?.message || '';
    const drafts = new Map(structuredClone(draftState?.drafts || []));
    const el = (tag, className, text) => {
      const node = doc.createElement(tag); node.className = className || '';
      if (text !== undefined) node.textContent = text;
      return node;
    };
    function setState(state = {}) {
      locked = state.locked === true;
      for (const control of controls) control.disabled = locked || pending;
    }
    async function edit(action, key, inputs) {
      if (locked || pending) return false;
      pending = true; setState({locked});
      const submittedDraft = drafts.has(key) ? structuredClone(drafts.get(key)) : undefined;
      let submitted = false;
      try {
        const point = {};
        if (action !== 'delete') for (const field of ['frame', 'value']) {
          const text = String(inputs[field].value).trim(); point[field] = text ? Number(text) : NaN;
        }
        const next = action === 'add' ? editor.addGainAutomationPoint(session, trackIndex, effectId, point) :
          action === 'update' ? editor.editGainAutomationPoint(session, trackIndex, effectId, key, point) :
            editor.deleteGainAutomationPoint(session, trackIndex, effectId, key);
        // The host may rebuild every effect view inside onEdit. Capture only other drafts.
        drafts.delete(key); message = ''; submitted = true;
        if (await onEdit(next, {trackIndex, effectId, frame: action === 'delete' ? 'new' : point.frame, field: 'frame'}) === false) throw new Error('Gain automation was not saved. Your draft is preserved.');
        session = next; drafts.delete(key); message = ''; render(session, {locked});
        const target = controls.find(node => node.dataset?.automationKey === String(action === 'delete' ? 'new' : point.frame));
        if (container.isConnected !== false) target?.focus?.();
        return true;
      } catch (error) {
        if (submitted && submittedDraft !== undefined) drafts.set(key, submittedDraft);
        message = error.message; onError?.(message);
        const status = container.children[container.children.length - 1];
        if (status) status.textContent = message;
        return false;
      } finally { pending = false; setState({locked}); }
    }
    function render(next, state = {}) {
      session = next; controls = []; container.replaceChildren();
      const effect = session.tracks[trackIndex]?.effects?.find(effect => effect.id === effectId && effect.kind === 'gain');
      if (!effect) { container.hidden = true; return; }
      container.hidden = false;
      container.append(el('p', 'automation-help', `Step automation · absolute song frames · gain 0–4. Base gain ${effect.gain} applies before the first point; each point holds until the next. Mixer gain is independent. Edits require stopped playback.`));
      const points = session.tracks[trackIndex].automation?.find(lane => lane.effect_id === effectId)?.points || [];
      const list = el('div', 'automation-points'); container.append(list);
      function row(point, key) {
        const node = el('div', 'automation-point'), inputs = {};
        for (const field of ['frame', 'value']) {
          const label = el('label', '', field === 'frame' ? 'Frame' : 'Gain'), input = el('input', 'number-input');
          input.type = 'number'; input.min = 0; input.max = field === 'frame' ? Number.MAX_SAFE_INTEGER : 4; input.step = field === 'frame' ? '1' : 'any';
          input.value = drafts.get(key)?.[field] ?? point[field];
          input.dataset ||= {}; input.dataset.automationKey = String(key); input.dataset.automationField = field; input.dataset.automationEffect = effectId;
          input.setAttribute('aria-label', `${effectId} ${key} automation ${field}`);
          input.addEventListener('input', () => drafts.set(key, {frame: inputs.frame.value, value: inputs.value.value}));
          input.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); return edit(key === 'new' ? 'add' : 'update', key, inputs); }
          });
          inputs[field] = input; controls.push(input); label.append(input); node.append(label);
        }
        for (const action of key === 'new' ? ['add'] : ['update', 'delete']) {
          const button = el('button', 'button button-quiet', action === 'add' ? 'Add point' : action === 'update' ? 'Update' : 'Delete'); button.type = 'button';
          button.setAttribute('aria-label', `${effectId} ${key} ${action} automation point`);
          button.addEventListener('click', () => edit(action, key, inputs)); controls.push(button); node.append(button);
        }
        list.append(node);
      }
      points.forEach(point => row(point, point.frame));
      row({frame: 0, value: effect.gain}, 'new');
      const status = el('p', 'automation-error', message); status.setAttribute('role', 'status'); container.append(status);
      setState(state);
    }
    function getDraftState() { return structuredClone({drafts: [...drafts], message}); }
    return {render, setState, getDraftState};
  }
  const api = {create};
  if (typeof module !== 'undefined') module.exports = api;
  else root.AutomationView = api;
})(typeof window === 'undefined' ? globalThis : window);

/* Portable factory Pd instrument packages. Programs stay embedded in saved sessions. */
(function (root) {
  'use strict';
  const program = "#N canvas 0 0 480 360 10;\n#X obj 20 20 r \\$0-frequency;\n#X obj 20 50 osc~;\n#X obj 160 20 r \\$0-cutoff;\n#X obj 20 90 lop~ 4000;\n#X obj 160 90 r \\$0-gate;\n#X obj 20 130 *~;\n#X obj 160 130 r \\$0-velocity;\n#X obj 20 170 *~;\n#X obj 20 210 dac~ 1 2;\n#X obj 300 20 r \\$0-reset;\n#X msg 300 50 0;\n#X msg 300 90 clear;\n#X connect 0 0 1 0;\n#X connect 1 0 3 0;\n#X connect 2 0 3 1;\n#X connect 3 0 5 0;\n#X connect 4 0 5 1;\n#X connect 5 0 7 0;\n#X connect 6 0 7 1;\n#X connect 7 0 8 0;\n#X connect 7 0 8 1;\n#X connect 9 0 10 0;\n#X connect 10 0 1 1;\n#X connect 10 0 5 1;\n#X connect 9 0 11 0;\n#X connect 11 0 3 0;\n";
  const control = {name: 'cutoff', type: 'float', default: 4000, min: 100, max: 12000, value: 4000};
  const packageByteLimit = 128 * 1024;
  function preset() { return {kind: 'pd_instrument', program, abstractions: [], gain: 0.2, controls: [structuredClone(control)]}; }
  function validateDevice(device) {
    if (!device || device.kind !== 'pd_instrument' || Object.keys(device).some(key => !['kind', 'program', 'abstractions', 'gain', 'controls'].includes(key))) return 'Choose a portable Pd instrument device.';
    if (typeof device.program !== 'string' || device.program.replace(/\r\n/g, '\n') !== program) return 'This MVP supports the embedded Pd Filtered Sine program. Other programs require scripting.';
    if (!Array.isArray(device.abstractions) || device.abstractions.length) return 'The Pd Filtered Sine preset has no external abstractions.';
    if (!Number.isFinite(device.gain) || device.gain < 0 || device.gain > 1) return 'Pd instrument gain must be between 0 and 1.';
    if (!Array.isArray(device.controls) || device.controls.length !== 1) return 'The Pd preset requires its declared cutoff control.';
    const saved = device.controls[0];
    if (!saved || Object.keys(saved).some(key => !Object.keys(control).includes(key)) || ['name', 'type', 'default', 'min', 'max'].some(key => saved[key] !== control[key]) || !Number.isFinite(saved.value) || saved.value < control.min || saved.value > control.max) return 'Pd cutoff requires float metadata and a value from 100 to 12,000 Hz.';
    return null;
  }
  function parsePackage(text) {
    if (typeof text !== 'string' || new TextEncoder().encode(text).length > packageByteLimit) throw new Error('Pd preset packages must be JSON no larger than 128 KiB.');
    let device;
    try { device = JSON.parse(text); } catch (_) { throw new Error('Pd preset package is not valid JSON.'); }
    const error = validateDevice(device); if (error) throw new Error(error);
    return structuredClone(device);
  }
  const api = {preset, validateDevice, parsePackage, packageByteLimit};
  if (typeof module !== 'undefined') module.exports = api;
  else root.PdInstrument = api;
})(typeof window === 'undefined' ? globalThis : window);

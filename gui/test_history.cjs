const assert = require('node:assert/strict');
const test = require('node:test');
const SessionHistory = require('./history.js');
const state = n => ({schema_version: 3, tracks: [{id: 'one', notes: [n]}]});

test('history peeks safely and advances only after successful engine application', () => {
  const history = new SessionHistory();
  const before = state(1), after = state(2);
  history.commit(before, after);
  const target = history.undoTarget(after);
  target.tracks[0].notes.push(99);
  assert.deepEqual(history.undoTarget(after), before);
  assert.equal(history.canRedo, false);
  // Engine rejection leaves the entry available for retry.
  assert.deepEqual(history.undoTarget(after), before);
  assert.equal(history.acceptUndo(after), true);
  assert.equal(history.canUndo, false);
  assert.deepEqual(history.redoTarget(before), after);
  assert.equal(history.acceptRedo(before), true);
  assert.equal(history.canRedo, false);
  assert.deepEqual(history.undoTarget(after), before);
});

test('commit stores independent snapshots, skips no-op and clears redo on a branch', () => {
  const history = new SessionHistory();
  const before = state(1), after = state(2);
  assert.equal(history.commit(before, structuredClone(before)), false);
  assert.equal(history.canUndo, false);
  history.commit(before, after);
  before.tracks[0].notes[0] = 99;
  after.tracks[0].notes[0] = 99;
  assert.deepEqual(history.undoTarget(state(2)), state(1));
  history.acceptUndo(state(2));
  assert.equal(history.commit(state(1), state(1)), false);
  assert.equal(history.canRedo, true);
  history.commit(state(1), state(3));
  assert.equal(history.canRedo, false);
  assert.deepEqual(history.undoTarget(state(3)), state(1));
  history.reset();
  assert.equal(history.canUndo, false);
  assert.equal(history.canRedo, false);
});

test('stale current state cannot consume undo or redo history', () => {
  const history = new SessionHistory();
  history.commit(state(1), state(2));
  assert.equal(history.undoTarget(state(99)), null);
  assert.equal(history.acceptUndo(state(99)), false);
  assert.equal(history.canUndo, true);
  history.acceptUndo(state(2));
  assert.equal(history.redoTarget(state(99)), null);
  assert.equal(history.acceptRedo(state(99)), false);
  assert.equal(history.canRedo, true);
});

test('history bounds entries and UTF-8 snapshot memory', () => {
  const history = new SessionHistory({limit: 2});
  history.commit(state(0), state(1));
  history.commit(state(1), state(2));
  history.commit(state(2), state(3));
  history.acceptUndo(state(3));
  history.acceptUndo(state(2));
  assert.equal(history.undoTarget(state(1)), null);
  const tiny = new SessionHistory({maxBytes: 4});
  tiny.commit({text: 'é'}, {text: 'ê'});
  assert.equal(tiny.canUndo, false);
  for (const options of [{limit: 0}, {maxBytes: 0}, {limit: 1.5}]) assert.throws(() => new SessionHistory(options));
});

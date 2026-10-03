/* Applied-session history: callers accept a transition only after the engine succeeds. */
(function (root) {
  'use strict';
  class SessionHistory {
    constructor({limit = 32, maxBytes = 8 * 1024 * 1024} = {}) {
      if (!Number.isSafeInteger(limit) || limit < 1 || !Number.isSafeInteger(maxBytes) || maxBytes < 1) throw new Error('History limits must be positive safe integers.');
      this.limit = limit;
      this.maxBytes = maxBytes;
      this.reset();
    }
    reset() { this.undo = []; this.redo = []; }
    get canUndo() { return this.undo.length > 0; }
    get canRedo() { return this.redo.length > 0; }
    commit(before, after) {
      const a = JSON.stringify(before), b = JSON.stringify(after);
      if (a === b) return false;
      const entry = {before: a, after: b, bytes: new TextEncoder().encode(a).length + new TextEncoder().encode(b).length};
      this.redo = [];
      this.undo.push(entry);
      this.trim();
      return true;
    }
    trim() {
      let bytes = [...this.undo, ...this.redo].reduce((sum, entry) => sum + entry.bytes, 0);
      while (this.undo.length > this.limit || bytes > this.maxBytes) {
        const removed = this.undo.shift();
        if (!removed) break;
        bytes -= removed.bytes;
      }
    }
    undoTarget(current) {
      const entry = this.undo.at(-1);
      return entry && JSON.stringify(current) === entry.after ? JSON.parse(entry.before) : null;
    }
    redoTarget(current) {
      const entry = this.redo.at(-1);
      return entry && JSON.stringify(current) === entry.before ? JSON.parse(entry.after) : null;
    }
    acceptUndo(current) {
      if (!this.undoTarget(current)) return false;
      this.redo.push(this.undo.pop());
      return true;
    }
    acceptRedo(current) {
      if (!this.redoTarget(current)) return false;
      this.undo.push(this.redo.pop());
      return true;
    }
  }
  if (typeof module !== 'undefined') module.exports = SessionHistory;
  else root.SessionHistory = SessionHistory;
})(typeof window === 'undefined' ? globalThis : window);

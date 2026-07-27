const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
function load(c, file) { vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), c, { filename: file }); }
function harness() {
  const ids = new Map();
  const document = { activeElement: null, getElementById(id) { return ids.get(id) || null; }, querySelector() { return null; }, addEventListener() {}, removeEventListener() {} };
  const revoked = [];
  const sandbox = { console, JSON, Math, Date, URL: { revokeObjectURL(url) { revoked.push(url); } }, state: { terminal_compose_height: 0, agent_message_history: {}, agents: {} }, document, window: { innerHeight: 900 }, setTimeout() { return 0; }, clearTimeout() {}, esc(v) { return String(v); } };
  sandbox.global = sandbox; sandbox.globalThis = sandbox;
  const c = vm.createContext(sandbox);
  load(c, 'static/js/terminal.js'); load(c, 'static/js/terminal/direct-messages.js'); load(c, 'static/js/terminal/composer.js'); load(c, 'static/js/terminal/composer-attachments.js');
  function input(id, value = '') {
    const listeners = Object.create(null);
    const el = { value, innerHTML: '', childNodes: null, dataset: { cellId: id }, selectionStart: value.length, selectionEnd: value.length, selectionDirection: 'none', style: { removeProperty() {}, setProperty() {} }, classList: { contains(name) { return name === 'terminal-compose-input'; }, toggle() {} }, getAttribute(name) { return name === 'contenteditable' ? 'true' : ''; }, focus() {}, contains() { return false; }, closest() { return null; }, addEventListener(type, listener) { (listeners[type] || (listeners[type] = [])).push(listener); }, dispatch(type, event) { (listeners[type] || []).forEach((listener) => listener(event || {})); } };
    ids.set(c._terminalComposeInputId(id), el); document.activeElement = el; return el;
  }
  return { c, input, revoked, get(expr) { return vm.runInContext(expr, c); } };
}
function type(h, input, value, kind = 'typing') { h.c._terminalComposeHistoryPrepare(input, kind); input.value = value; h.c.terminalComposeInput(input); }

test('semantic history coalesces typing, preserves multiline selections, and invalidates redo', () => {
  const h = harness(), input = h.input('a', '');
  h.c._terminalComposeHistoryState('a', input);
  type(h, input, 'one'); type(h, input, 'one\ntwo');
  assert.equal(h.get('_terminalComposeHistory.a.past.length'), 1);
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(input.value, '');
  assert.equal(h.c.terminalComposeUndoRedo(input, true), true);
  assert.equal(input.value, 'one\ntwo');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  type(h, input, 'replacement', 'paste');
  assert.equal(h.c.terminalComposeUndoRedo(input, true), false);
});

test('history is cell-local, explicit clear is undoable, and a send reset cannot resurrect a draft', () => {
  const h = harness(), a = h.input('a', ''), b = h.input('b', '');
  h.c._terminalComposeHistoryState('a', a); type(h, a, 'alpha');
  h.c._terminalComposeHistoryState('b', b); type(h, b, 'beta');
  h.c.terminalComposeClear('a');
  assert.equal(a.value, ''); assert.equal(b.value, 'beta');
  assert.equal(h.c.terminalComposeUndoRedo(a, false), true); assert.equal(a.value, 'alpha');
  h.c.terminalComposeClear('a'); h.c._terminalComposeHistoryReset('a', a);
  assert.equal(h.c.terminalComposeUndoRedo(a, false), false);
  assert.equal(h.c.terminalComposeUndoRedo(b, false), true); assert.equal(b.value, '');
});

test('attachment snapshots retain token ordering across undo/redo and release only after reset', () => {
  const h = harness(), input = h.input('a', 'left right');
  h.c._terminalComposeHistoryState('a', input);
  input.selectionStart = input.selectionEnd = 5;
  h.c._terminalComposeInsertAttachments(input, [{ path: '/safe/a.png', filename: 'a.png', preview_url: 'blob:a' }]);
  assert.equal(h.get('_terminalComposeAttachments.a.entries.length'), 1);
  assert.equal(h.get('_terminalComposeAttachments.a.entries[0].position'), 5);
  h.c._terminalComposeRemoveAttachment('a', '[ Image #1 ]');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(h.get('_terminalComposeAttachments.a.entries[0].token'), '[ Image #1 ]');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(h.get('_terminalComposeAttachments.a'), undefined);
  assert.equal(h.c.terminalComposeUndoRedo(input, true), true);
  assert.equal(h.get('_terminalComposeAttachments.a.entries[0].position'), 5);
  h.c.terminalComposeClear('a'); h.c._terminalComposeHistoryReset('a', input);
  assert.deepEqual(h.revoked, ['blob:a'], 'discarded preview URL is revoked exactly once');
});

test('Cmd/Ctrl Z is composer-scoped and composition does not steal native input', () => {
  const h = harness(), input = h.input('a', ''); h.c._terminalComposeHistoryState('a', input); type(h, input, 'x');
  const event = { key: 'z', metaKey: true, ctrlKey: false, altKey: false, isComposing: false, target: input, preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
  assert.equal(h.c.terminalComposeUndoRedoShortcut(event, input), true); assert.equal(input.value, ''); assert.equal(event.prevented, true);
  event.shiftKey = true; assert.equal(h.c.terminalComposeUndoRedoShortcut(event, input), true); assert.equal(input.value, 'x');
  event.metaKey = false; event.ctrlKey = true; event.shiftKey = false; assert.equal(h.c.terminalComposeUndoRedoShortcut(event, input), true); assert.equal(input.value, '');
  const composing = Object.assign({}, event, { isComposing: true }); assert.equal(h.c.terminalComposeUndoRedoShortcut(composing, input), false);
});

test('caret movement starts a fresh transaction without discarding older undo history', () => {
  const h = harness(), input = h.input('a', '');
  h.c._terminalComposeHistoryState('a', input);
  type(h, input, 'one');
  type(h, input, 'one two');
  input.selectionStart = input.selectionEnd = 1;
  type(h, input, 'oXne two');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(input.value, 'one two');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(input.value, '');
});

test('initial IME composition remains native and commits one undoable transaction', () => {
  const h = harness(), input = h.input('a', '');
  h.c._terminalComposeHistoryState('a', input);
  const event = {
    currentTarget: input, isComposing: true, inputType: 'insertCompositionText',
    preventDefault() { this.prevented = true; },
  };
  h.c.terminalComposeBeforeInput(event);
  assert.equal(event.prevented, undefined, 'composition beforeinput stays browser-native');
  input.value = 'あ';
  h.c.terminalComposeInput(input, { isComposing: true });
  assert.equal(h.c.terminalComposeUndoRedo(input, false), false,
    'intermediate composition state does not create a history entry');
  const finalBeforeInput = { currentTarget: input, isComposing: false, inputType: 'insertText',
    preventDefault() { this.prevented = true; } };
  h.c.terminalComposeBeforeInput(finalBeforeInput);
  assert.equal(finalBeforeInput.prevented, undefined,
    'the final native beforeinput remains unprevented and preserves the baseline');
  h.c.terminalComposeInput(input, { isComposing: false });
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(input.value, '');
});

test('compositionend commits Chrome composition-only lifecycle as one undoable transaction', () => {
  const h = harness(), input = h.input('a', '');
  h.c._terminalComposeHistoryState('a', input);
  const start = { currentTarget: input, isComposing: true, inputType: 'insertCompositionText',
    preventDefault() { this.prevented = true; } };
  h.c.terminalComposeBeforeInput(start);
  input.value = 'あ';
  h.c.terminalComposeInput(input, { isComposing: true });
  h.c._terminalComposeBindRichInput(input);
  h.c._terminalComposeBindRichInput(input);
  input.dispatch('compositionend', {});
  assert.equal(start.prevented, undefined, 'composition start remains native');
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(input.value, '');
});

test('attachment insertion survives rich DOM sync before undo and redo', () => {
  const h = harness(), input = h.input('a', 'left right');
  input.childNodes = [{ nodeType: 3, nodeValue: 'left right' }];
  input.selectionStart = input.selectionEnd = 5;
  h.c._terminalComposeHistoryState('a', input);
  h.c._terminalComposeInsertAttachments(input, [{ path: '/safe/a.png', filename: 'a.png', preview_url: 'blob:a' }]);
  assert.equal(h.get('_terminalComposeAttachments.a.entries.length'), 1);
  assert.equal(h.get('_terminalComposeHistory.a.past.length'), 1);
  assert.equal(h.c.terminalComposeUndoRedo(input, false), true);
  assert.equal(h.get('_terminalComposeAttachments.a'), undefined);
  assert.equal(h.c.terminalComposeUndoRedo(input, true), true);
  assert.equal(h.get('_terminalComposeAttachments.a.entries[0].token'), '[ Image #1 ]');
});

// Regression test for `_restoreSurfaceState` focus behavior.
//
// Bug: clicking into the empty inline "Add task" textarea on the board, then
// scrolling down, sent the page back to the top. The cause was the textarea
// scrolling out of view while WebSocket-driven re-renders kept calling
// `el.focus()` on the new (offscreen) input, and the browser's default
// scroll-into-view-on-focus behavior dragged the page back. With typed text
// the textarea autoresized taller and remained partially visible, so the
// browser didn't trigger the auto-scroll — hence the "only when empty"
// symptom. Fix: pass `{preventScroll: true}` so explicit scroll restoration
// (snapshot.scrolls and panel-level scroll bookkeeping) wins.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function makeFakeTextarea(id) {
  const focusCalls = [];
  return {
    id,
    value: '',
    selectionStart: 0,
    selectionEnd: 0,
    scrollTop: 0,
    scrollLeft: 0,
    focusCalls,
    focus(opts) {
      focusCalls.push(opts === undefined ? null : opts);
    },
  };
}

function makePanel(_textarea) {
  // _findSurfaceNode resolves '#id' selectors via the global document.getElementById,
  // which the sandbox below stubs. The panel just needs to claim it contains the
  // textarea so _surfaceContains succeeds during capture.
  return {
    contains(node) {
      return node === _textarea;
    },
    querySelector() { return null; },
  };
}

function buildSandbox(activeElement, lookup) {
  const sandbox = {
    console,
    document: {
      activeElement,
      getElementById(id) {
        const node = lookup[id];
        return node || null;
      },
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  return sandbox;
}

test('_restoreSurfaceState focuses the captured input with preventScroll', () => {
  const textarea = makeFakeTextarea('board-add-task-input');
  const sandbox = buildSandbox(textarea, { [textarea.id]: textarea });
  const panel = makePanel(textarea);

  const snapshot = sandbox._captureSurfaceState(panel);
  assert.ok(snapshot && snapshot.focus, 'snapshot should capture focus on textarea');
  assert.equal(snapshot.focus.key, '#board-add-task-input');

  // Simulate the DOM rebuild: focus is no longer on the textarea, but the same
  // id resolves to a *new* node with the same logical identity.
  sandbox.document.activeElement = null;
  textarea.focusCalls.length = 0;

  sandbox._restoreSurfaceState(panel, snapshot);

  assert.equal(textarea.focusCalls.length, 1,
    'restore should call focus exactly once on the resolved input');
  const opts = textarea.focusCalls[0];
  assert.ok(opts && opts.preventScroll === true,
    'focus must be invoked with {preventScroll: true} so scroll restoration is not'
    + ' overridden by the browser auto-scroll-into-view behavior'
    + ' (regression: empty inline-task textarea scroll-jumps to top)');
});

test('_restoreSurfaceState falls back to no-arg focus if focus(opts) throws', () => {
  const textarea = makeFakeTextarea('board-add-task-input');
  // Override focus to reject the options arg the first time, simulating an
  // older runtime that doesn't accept FocusOptions.
  textarea.focus = function(opts) {
    textarea.focusCalls.push(opts === undefined ? null : opts);
    if (opts && Object.prototype.hasOwnProperty.call(opts, 'preventScroll')) {
      throw new TypeError('options not supported');
    }
  };
  const sandbox = buildSandbox(textarea, { [textarea.id]: textarea });
  const panel = makePanel(textarea);

  const snapshot = sandbox._captureSurfaceState(panel);
  sandbox.document.activeElement = null;
  textarea.focusCalls.length = 0;

  sandbox._restoreSurfaceState(panel, snapshot);

  assert.equal(textarea.focusCalls.length, 2,
    'should retry focus() with no args when focus({preventScroll}) throws');
  assert.ok(textarea.focusCalls[0] && textarea.focusCalls[0].preventScroll === true,
    'first attempt should pass {preventScroll: true}');
  assert.equal(textarea.focusCalls[1], null,
    'fallback should call focus() with no arguments');
});

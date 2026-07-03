/* Terminal-flicker Phase 1 regression coverage.
 *
 * Two fixes, two behaviors under test:
 *
 *  1. static/js/ws.js — the post-keystroke delta-render flush is debounced.
 *     A typing-gated release (keyup / compositionend on a text-editing target)
 *     must NOT flush the pending delta batch on the next frame; it schedules a
 *     trailing idle flush (~250 ms) that re-arms on each keystroke so
 *     continuous typing coalesces to a single flush. A pointer release keeps
 *     the immediate next-frame flush.
 *
 *  2. static/js/terminal.js — `_terminalComposeAutoResize` is a no-op (zero
 *     `taskAutoResize` reflow, zero `style.height` writes) when the focused
 *     composer's height inputs (text + stored height) are unchanged since the
 *     last pass, so per-delta render passes stop resizing the stage and
 *     tripping the xterm ResizeObserver. A content change resizes again.
 */

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

/* ------------------------------------------------------------------ */
/* Fix 1 harness: ws.js with controllable rAF/setTimeout and captured  */
/* document/window listeners.                                          */
/* ------------------------------------------------------------------ */

function createWsGateHarness() {
  const rafQueue = [];
  const timers = new Map();
  let rafId = 0;
  let timerId = 0;
  const docListeners = Object.create(null);
  const winListeners = Object.create(null);

  function addListener(store, type, handler) {
    if (!store[type]) store[type] = [];
    store[type].push(handler);
  }

  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    state: {
      runtime: { embedded_terminal: false },
      agents: {},
      board_tasks: {},
      groups: { alpha: [] },
    },
    _activePanelApp: 'board',
    location: { host: 'localhost:18932' },
    WebSocket: function () {},
    document: {
      activeElement: null,
      getElementById() { return null; },
      addEventListener(type, handler) { addListener(docListeners, type, handler); },
    },
    window: {
      addEventListener(type, handler) { addListener(winListeners, type, handler); },
    },
    requestAnimationFrame(cb) {
      rafId += 1;
      rafQueue.push({ id: rafId, cb: cb });
      return rafId;
    },
    cancelAnimationFrame(id) {
      for (let i = 0; i < rafQueue.length; i++) {
        if (rafQueue[i].id === id) { rafQueue.splice(i, 1); return; }
      }
    },
    setTimeout(cb, delay) {
      timerId += 1;
      timers.set(timerId, { cb: cb, delay: delay });
      return timerId;
    },
    clearTimeout(id) { timers.delete(id); },
    _currentGroup() { return 'alpha'; },
    _focusedEngineerAgent() { return null; },
    _standalonePanelsEnabled() { return false; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');

  // Count real flushes without doing any DOM work.
  let flushCount = 0;
  sandbox._renderDeltaSurfaceInvalidations = function () { flushCount += 1; };

  function fire(store, type, ev) {
    const handlers = store[type] || [];
    handlers.forEach(function (h) { h(ev || {}); });
  }

  return {
    context,
    sandbox,
    getFlushCount() { return flushCount; },
    setPending() {
      vm.runInContext('_pendingDeltaSurfaceInvalidations = { main: true };', context);
    },
    pending() {
      return vm.runInContext('!!_pendingDeltaSurfaceInvalidations', context);
    },
    fireDoc(type, ev) { fire(docListeners, type, ev); },
    fireWin(type, ev) { fire(winListeners, type, ev); },
    pendingTimers() { return Array.from(timers.values()); },
    runTimers() {
      // Snapshot then run; cancelled timers are already gone from the map.
      const entries = Array.from(timers.entries());
      entries.forEach(function (pair) {
        if (timers.has(pair[0])) { timers.delete(pair[0]); pair[1].cb(); }
      });
    },
    runRaf() {
      const pending = rafQueue.splice(0, rafQueue.length);
      pending.forEach(function (entry) { entry.cb(); });
    },
    rafPending() { return rafQueue.length; },
  };
}

const TEXT_TARGET = { target: { tagName: 'TEXTAREA' } };

test('ws.js: typing-gated release defers to a trailing flush, not the next frame', () => {
  const h = createWsGateHarness();
  h.setPending();

  // keydown+keyup on a text-editing target = a typing interaction.
  h.fireDoc('keydown', TEXT_TARGET);
  h.fireDoc('keyup', {});

  // No immediate flush on this frame — a trailing timer is armed instead.
  h.runRaf();
  assert.equal(h.getFlushCount(), 0,
    'typing release must not flush on the next animation frame');
  const armed = h.pendingTimers();
  assert.equal(armed.length, 1, 'a single trailing-flush timer should be armed');
  assert.ok(armed[0].delay >= 200 && armed[0].delay <= 400,
    'trailing flush should fire on an idle delay (~250ms), got ' + armed[0].delay);
  assert.ok(h.pending(), 'batch must still be pending until the trailing timer fires');

  // When typing stops, the trailing timer flushes the batch exactly once.
  h.runTimers();
  assert.equal(h.getFlushCount(), 1, 'trailing timer flushes the coalesced batch');
  assert.ok(!h.pending(), 'pending batch is consumed by the trailing flush');
});

test('ws.js: pointer release still flushes on the next frame', () => {
  const h = createWsGateHarness();
  h.setPending();

  h.fireDoc('pointerdown', {});
  h.fireDoc('pointerup', {});

  // Pointer path uses the immediate next-frame flush, never the trailing timer.
  assert.equal(h.pendingTimers().length, 0,
    'pointer release must not arm the typing trailing timer');
  assert.ok(h.rafPending() >= 1, 'pointer release schedules a next-frame flush');
  assert.equal(h.getFlushCount(), 0, 'flush is deferred to the frame, not synchronous');

  h.runRaf();
  assert.equal(h.getFlushCount(), 1, 'pointer release flushes on the next frame');
});

test('ws.js: rapid successive keyups coalesce to a single trailing flush', () => {
  const h = createWsGateHarness();
  h.setPending();

  // Three keystrokes in quick succession; each keydown re-arms the timer.
  for (let i = 0; i < 3; i++) {
    h.fireDoc('keydown', TEXT_TARGET);
    h.fireDoc('keyup', {});
  }

  assert.equal(h.getFlushCount(), 0, 'no flush occurs while typing continues');
  assert.equal(h.pendingTimers().length, 1,
    'continuous typing keeps exactly one live trailing timer (prior ones cancelled)');

  h.runTimers();
  assert.equal(h.getFlushCount(), 1,
    'the whole typing burst produces exactly one trailing flush');
});

test('ws.js: window blur flushes promptly instead of leaking the trailing timer', () => {
  const h = createWsGateHarness();
  h.setPending();

  // Arm a trailing timer, then blur before it fires.
  h.fireDoc('keydown', TEXT_TARGET);
  h.fireDoc('keyup', {});
  assert.equal(h.pendingTimers().length, 1);

  h.fireWin('blur', {});
  assert.equal(h.pendingTimers().length, 0,
    'blur cancels the pending trailing timer so it cannot strand');
  assert.ok(h.rafPending() >= 1, 'blur falls back to a prompt next-frame flush');

  h.runRaf();
  assert.equal(h.getFlushCount(), 1, 'blur flushes the pending batch promptly');
});

/* ------------------------------------------------------------------ */
/* Fix 2 harness: terminal.js `_terminalComposeAutoResize` memoization. */
/* ------------------------------------------------------------------ */

function createTerminalComposeHarness() {
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    setTimeout() { return 0; },
    clearTimeout() {},
    state: { terminal_compose_height: 0 },
    document: {
      activeElement: null,
      addEventListener() {},
      getElementById() { return null; },
      querySelector() { return null; },
    },
    window: { addEventListener() {}, innerHeight: 900 },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/terminal.js');

  // Record every taskAutoResize call and the style.height writes it performs,
  // exactly as the real path does (auto -> scrollHeight px).
  const resizeCalls = [];
  sandbox.taskAutoResize = function (el) {
    resizeCalls.push(el);
    el.style.height = 'auto';
    el.style.height = (el.__scrollHeight || 0) + 'px';
  };

  function makeInput(cellId) {
    const heightWrites = [];
    const style = {
      _height: '',
      _maxHeight: '',
      setProperty() {},
      removeProperty() {},
    };
    Object.defineProperty(style, 'height', {
      get() { return style._height; },
      set(v) { style._height = v; heightWrites.push(v); },
    });
    Object.defineProperty(style, 'maxHeight', {
      get() { return style._maxHeight; },
      set(v) { style._maxHeight = v; },
    });
    return {
      dataset: { cellId: cellId },
      value: '',
      __scrollHeight: 24,
      style: style,
      heightWrites: heightWrites,
      getAttribute() { return null; },
      parentNode: null,
    };
  }

  return { context, sandbox, makeInput, resizeCalls };
}

test('terminal.js: autoresize skips style writes for an unchanged focused draft', () => {
  const h = createTerminalComposeHarness();
  const input = h.makeInput('cell-1');
  input.value = 'hello';
  h.sandbox.document.activeElement = input;

  // First pass runs the real resize and records the memo.
  h.sandbox._terminalComposeAutoResize(input);
  assert.equal(h.resizeCalls.length, 1, 'first pass resizes');
  assert.ok(input.heightWrites.length > 0, 'first pass writes style.height');

  const resizesAfterFirst = h.resizeCalls.length;
  const writesAfterFirst = input.heightWrites.length;

  // Repeated delta-driven passes with the same focused draft must be no-ops.
  h.sandbox._terminalComposeAutoResize(input);
  h.sandbox._terminalComposeAutoResize(input);
  h.sandbox._terminalComposeAutoResize(input);

  assert.equal(h.resizeCalls.length, resizesAfterFirst,
    'unchanged focused draft must not re-run taskAutoResize');
  assert.equal(input.heightWrites.length, writesAfterFirst,
    'unchanged focused draft must perform zero further style.height writes');
});

test('terminal.js: autoresize resizes again when the focused draft content changes', () => {
  const h = createTerminalComposeHarness();
  const input = h.makeInput('cell-2');
  input.value = 'a';
  h.sandbox.document.activeElement = input;

  h.sandbox._terminalComposeAutoResize(input);
  const resizesAfterFirst = h.resizeCalls.length;
  const writesAfterFirst = input.heightWrites.length;

  // Same content -> skipped.
  h.sandbox._terminalComposeAutoResize(input);
  assert.equal(h.resizeCalls.length, resizesAfterFirst, 'unchanged content skips');

  // Content grows -> must resize again (this is how the composer grows).
  input.value = 'a\nb\nc';
  input.__scrollHeight = 72;
  h.sandbox._terminalComposeAutoResize(input);

  assert.equal(h.resizeCalls.length, resizesAfterFirst + 1,
    'a content change re-runs taskAutoResize');
  assert.ok(input.heightWrites.length > writesAfterFirst,
    'a content change writes style.height again');
});

test('terminal.js: an unfocused composer still recomputes so it can re-clamp', () => {
  const h = createTerminalComposeHarness();
  const input = h.makeInput('cell-3');
  input.value = 'steady';
  // Not the active element: geometry (shell/window shrink) may have changed,
  // so the recompute must keep running to avoid a stale over-tall composer.
  h.sandbox.document.activeElement = null;

  h.sandbox._terminalComposeAutoResize(input);
  const afterFirst = h.resizeCalls.length;
  h.sandbox._terminalComposeAutoResize(input);

  assert.equal(h.resizeCalls.length, afterFirst + 1,
    'unfocused passes keep recomputing (no stale-clamp skip)');
});

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

function makeClassList(initial) {
  const classes = new Set(initial || []);
  return {
    add(cls) { classes.add(cls); },
    remove(cls) { classes.delete(cls); },
    contains(cls) { return classes.has(cls); },
    toggle(cls, force) {
      const shouldAdd = force === undefined ? !classes.has(cls) : !!force;
      if (shouldAdd) classes.add(cls);
      else classes.delete(cls);
      return shouldAdd;
    },
    values() { return Array.from(classes); },
  };
}

function makeSplitPart(id, opts) {
  const options = opts || {};
  return {
    id,
    classList: makeClassList(options.classes || []),
    style: {},
    attributes: {},
    children: options.children || [],
    scrollHeight: options.scrollHeight || 0,
    offsetHeight: options.offsetHeight || 0,
    clientHeight: options.clientHeight || 0,
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name] || null; },
    getBoundingClientRect() {
      return {
        top: 0,
        left: 0,
        width: options.width || 0,
        height: options.height || 0,
      };
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
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
      body: { tagName: 'BODY' },
      documentElement: { tagName: 'HTML' },
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
  loadScript(sandbox, 'static/js/grid/group-tabs.js');
  loadScript(sandbox, 'static/js/grid/agent-card.js');
  loadScript(sandbox, 'static/js/grid/terminal-row.js');
  loadScript(sandbox, 'static/js/grid/sections.js');
  loadScript(sandbox, 'static/js/agent-detail.js');
  loadScript(sandbox, 'static/js/agent-focus.js');
  loadScript(sandbox, 'static/js/grid/main.js');
  return sandbox;
}

function buildFocusSplitSandbox(options) {
  const opts = Object.assign({
    focusContentHeight: 260,
    splitHeight: 800,
    handleHeight: 10,
    viewportHeight: 900,
  }, options || {});
  const store = {};
  const tabsHost = makeSplitPart('agent-group-tabs-host');
  const grid = makeSplitPart('agent-grid-pane');
  const handle = makeSplitPart('agent-focus-resizer', { height: opts.handleHeight });
  const focus = makeSplitPart('agent-focus-panel', { height: opts.focusPanelHeight || 0 });
  const focusScroll = makeSplitPart('agent-focus-scroll', {
    scrollHeight: opts.focusContentHeight,
    offsetHeight: opts.focusContentHeight,
    clientHeight: opts.focusContentHeight,
  });
  const split = makeSplitPart('agent-split', {
    classes: ['agent-split'],
    height: opts.splitHeight,
  });
  const main = makeSplitPart('main');
  const byId = {
    main,
    'agent-group-tabs-host': tabsHost,
    'app-group-tabs-host': null,
    'agent-grid-pane': grid,
    'agent-focus-resizer': handle,
    'agent-focus-panel': focus,
    'agent-focus-scroll': focusScroll,
  };
  main.querySelector = function(selector) {
    if (selector === '[data-agent-split]') return split;
    if (selector === '[data-agent-grid-pane]') return grid;
    if (selector === '[data-agent-focus-resizer]') return handle;
    if (selector === '[data-agent-focus-panel]') return focus;
    if (selector === '[data-agent-focus-scroll]') return focusScroll;
    if (selector === '[data-agent-group-tabs-host]') return tabsHost;
    return null;
  };
  const body = {
    classList: makeClassList(),
    style: {},
  };
  const document = {
    activeElement: null,
    body,
    listeners: {},
    getElementById(id) {
      return Object.prototype.hasOwnProperty.call(byId, id) ? byId[id] : null;
    },
    querySelector() { return null; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    removeEventListener(type, handler) {
      if (this.listeners[type] === handler) delete this.listeners[type];
    },
  };
  main.ownerDocument = document;
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    location: { host: 'localhost:18932' },
    localStorage: {
      getItem(key) {
        return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
      },
      setItem(key, value) { store[key] = String(value); },
    },
    getComputedStyle() {
      return {
        paddingTop: '0px',
        paddingBottom: '0px',
        marginTop: '0px',
        marginBottom: '0px',
      };
    },
    window: { innerHeight: opts.viewportHeight },
    document,
    state: {
      runtime: { profile: 'focus-test', port: '18932' },
    },
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  loadScript(sandbox, 'static/js/grid/group-tabs.js');
  loadScript(sandbox, 'static/js/grid/agent-card.js');
  loadScript(sandbox, 'static/js/grid/terminal-row.js');
  loadScript(sandbox, 'static/js/grid/sections.js');
  loadScript(sandbox, 'static/js/agent-detail.js');
  loadScript(sandbox, 'static/js/agent-focus.js');
  loadScript(sandbox, 'static/js/grid/main.js');
  return {
    sandbox,
    store,
    parts: { main, split, tabsHost, grid, handle, focus, focusScroll, body },
  };
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
  sandbox.document.activeElement = sandbox.document.body;
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

test('terminal workspace restore does not refocus the composer when desktop focus is unknown', () => {
  const composer = makeFakeTextarea('terminal-compose-input-agent-1');
  const sandbox = buildSandbox(composer, { [composer.id]: composer });
  const workspace = makePanel(composer);
  sandbox._captureTerminalDirectMessagesState = function() { return null; };
  sandbox._restoreTerminalDirectMessagesState = function() {};
  loadScript(sandbox, 'static/js/terminal/composer.js');
  const snapshot = sandbox._captureTerminalWorkspaceState(workspace, { id: 'agent-1' });

  // WKWebView can lose sight of its native focus owner while document has no
  // active element. An automatic workspace redraw must treat that as unknown,
  // not as permission to restore the formerly focused DM composer.
  sandbox.document.activeElement = null;
  composer.focusCalls.length = 0;

  sandbox._restoreTerminalWorkspaceState(workspace, snapshot, { id: 'agent-1' });

  assert.equal(composer.focusCalls.length, 0,
    'an unknown desktop focus owner must not have focus taken by the DM composer');
});

test('_restoreSurfaceState does not refocus a composer after the operator moves to another control', () => {
  const composer = makeFakeTextarea('terminal-compose-input-agent-1');
  const operatorControl = { id: 'operator-settings-search', tagName: 'INPUT', isConnected: true };
  const sandbox = buildSandbox(composer, { [composer.id]: composer });
  const workspace = makePanel(composer);
  const snapshot = sandbox._captureSurfaceState(workspace);

  sandbox.document.activeElement = operatorControl;
  composer.focusCalls.length = 0;

  sandbox._restoreSurfaceState(workspace, snapshot);

  assert.equal(composer.focusCalls.length, 0,
    'a known operator-selected control must retain focus across the redraw');
});

test('_restoreSurfaceState restores focus when its captured composer remains the active owner', () => {
  const composer = makeFakeTextarea('terminal-compose-input-agent-1');
  const restoredComposer = makeFakeTextarea('terminal-compose-input-agent-1');
  const sandbox = buildSandbox(composer, { [composer.id]: composer });
  const workspace = makePanel(composer);
  const snapshot = sandbox._captureSurfaceState(workspace);

  // Some DOM hosts retain the pre-replacement focused node until the restore
  // completes. It is safe only because it is the exact captured owner.
  sandbox.document.getElementById = function(id) {
    return id === restoredComposer.id ? restoredComposer : null;
  };
  sandbox._restoreSurfaceState(workspace, snapshot);

  assert.equal(restoredComposer.focusCalls.length, 1,
    'the exact captured composer owner remains a legitimate restore state');
});

test('_restoreSurfaceState does not refocus a composer when a competing control is detached', () => {
  const composer = makeFakeTextarea('terminal-compose-input-agent-1');
  const staleOperatorControl = {
    id: 'operator-settings-search',
    tagName: 'INPUT',
    isConnected: false,
  };
  const sandbox = buildSandbox(composer, { [composer.id]: composer });
  const workspace = makePanel(composer);
  const snapshot = sandbox._captureSurfaceState(workspace);

  // A disconnected active element is not proof that the old composer still
  // owns focus: another surface can detach the operator's selected control.
  sandbox.document.activeElement = staleOperatorControl;
  composer.focusCalls.length = 0;

  sandbox._restoreSurfaceState(workspace, snapshot);

  assert.equal(composer.focusCalls.length, 0,
    'a detached competing focus owner must not license composer refocus');
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
  sandbox.document.activeElement = sandbox.document.body;
  textarea.focusCalls.length = 0;

  sandbox._restoreSurfaceState(panel, snapshot);

  assert.equal(textarea.focusCalls.length, 2,
    'should retry focus() with no args when focus({preventScroll}) throws');
  assert.ok(textarea.focusCalls[0] && textarea.focusCalls[0].preventScroll === true,
    'first attempt should pass {preventScroll: true}');
  assert.equal(textarea.focusCalls[1], null,
    'fallback should call focus() with no arguments');
});

test('_restoreSurfaceState keeps same-surface drafts and carets through composer, agent, events, and health refreshes', () => {
  const cases = [
    'terminal-compose-input-agent-1',
    'agent-panel-draft',
    'events-resolve-draft',
    'health-check-notes',
  ];

  cases.forEach((id) => {
    const active = makeFakeTextarea(id);
    active.value = 'draft for ' + id;
    active.selectionStart = 2;
    active.selectionEnd = 7;
    const restored = makeFakeTextarea(id);
    const sandbox = buildSandbox(active, { [id]: active });
    const panel = makePanel(active);
    const snapshot = sandbox._captureSurfaceState(panel);

    // A routine innerHTML refresh replaces the focused node and leaves the
    // document on its normal body fallback. This is the legitimate path that
    // must keep a streaming composer (and every other shared surface) editable.
    sandbox.document.activeElement = sandbox.document.body;
    sandbox.document.getElementById = function(key) {
      return key === id ? restored : null;
    };
    sandbox._restoreSurfaceState(panel, snapshot);

    assert.equal(restored.value, 'draft for ' + id, id + ' draft survives refresh');
    assert.equal(restored.selectionStart, 2, id + ' caret start survives refresh');
    assert.equal(restored.selectionEnd, 7, id + ' caret end survives refresh');
    assert.equal(restored.focusCalls.length, 1, id + ' regains focus after its own refresh');
  });
});

test('_restoreSurfaceState preserves textarea draft, caret, input scroll, and panel scroll positions', () => {
  const activeTextarea = makeFakeTextarea('agent-detail-draft');
  activeTextarea.value = 'draft reply';
  activeTextarea.selectionStart = 2;
  activeTextarea.selectionEnd = 7;
  activeTextarea.scrollTop = 33;
  activeTextarea.scrollLeft = 4;
  const restoredTextarea = makeFakeTextarea('agent-detail-draft');
  restoredTextarea.value = '';
  restoredTextarea.selectionStart = 0;
  restoredTextarea.selectionEnd = 0;
  restoredTextarea.scrollTop = 0;
  restoredTextarea.scrollLeft = 0;
  const log = { scrollTop: 81, scrollLeft: 5 };
  const panel = {
    scrollTop: 140,
    scrollLeft: 9,
    contains(node) { return node === activeTextarea; },
    querySelector(selector) {
      return selector === '.mcp-log' ? log : null;
    },
  };
  const sandbox = buildSandbox(activeTextarea, { [activeTextarea.id]: activeTextarea });

  const snapshot = sandbox._captureSurfaceState(panel, {
    scrollSelectors: [':root', '.mcp-log'],
  });

  // Simulate a repaint: the focused textarea resolves to a new node and the
  // panel/log scroll containers have drifted to the top.
  sandbox.document.activeElement = sandbox.document.body;
  sandbox.document.getElementById = function(id) {
    return id === restoredTextarea.id ? restoredTextarea : null;
  };
  panel.scrollTop = 0;
  panel.scrollLeft = 0;
  log.scrollTop = 0;
  log.scrollLeft = 0;

  sandbox._restoreSurfaceState(panel, snapshot, {
    scrollSelectors: [':root', '.mcp-log'],
  });

  assert.equal(restoredTextarea.value, 'draft reply');
  assert.equal(restoredTextarea.selectionStart, 2);
  assert.equal(restoredTextarea.selectionEnd, 7);
  assert.equal(restoredTextarea.scrollTop, 33);
  assert.equal(restoredTextarea.scrollLeft, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(restoredTextarea.focusCalls)), [
    { preventScroll: true },
  ]);
  assert.equal(panel.scrollTop, 140);
  assert.equal(panel.scrollLeft, 9);
  assert.equal(log.scrollTop, 81);
  assert.equal(log.scrollLeft, 5);
});

test('focus panel resizer click collapses and expands with persisted collapsed UI state', () => {
  const { sandbox, parts } = buildFocusSplitSandbox({ focusContentHeight: 260 });
  let prevented = 0;

  sandbox._agentFocusResizerClick({ preventDefault() { prevented += 1; } });

  assert.equal(prevented, 1);
  assert.equal(sandbox._agentFocusIsCollapsed(), true);
  assert.equal(parts.split.classList.contains('agent-split--focus-collapsed'), true);
  assert.equal(parts.handle.getAttribute('role'), 'button');
  assert.equal(parts.handle.getAttribute('aria-label'), 'Expand focus panel');
  assert.equal(parts.handle.getAttribute('aria-expanded'), 'false');
  assert.equal(parts.focus.style.height || '', '');

  sandbox._agentFocusResizerClick({ preventDefault() { prevented += 1; } });

  assert.equal(prevented, 2);
  assert.equal(sandbox._agentFocusIsCollapsed(), false);
  assert.equal(parts.split.classList.contains('agent-split--focus-collapsed'), false);
  assert.equal(parts.handle.getAttribute('role'), 'separator');
  assert.equal(parts.handle.getAttribute('aria-label'), 'Resize or collapse focus panel');
  assert.equal(parts.handle.getAttribute('aria-expanded'), 'true');
  assert.equal(sandbox._agentFocusMode(), 'auto');
  assert.equal(parts.focus.style.height, '260px');
  assert.equal(parts.focus.style.flexBasis, '260px');
});

test('focus panel auto-size clamps tall content to the viewport cap in auto mode', () => {
  const { sandbox, parts } = buildFocusSplitSandbox({
    focusContentHeight: 700,
    splitHeight: 800,
    handleHeight: 10,
    viewportHeight: 900,
  });

  sandbox._agentFocusApplyPersistedSplit();

  // min(content 700, viewport 45% cap 405, split available 590) => 405.
  assert.equal(parts.focus.style.height, '405px');
  assert.equal(parts.focus.style.flexBasis, '405px');
  assert.equal(parts.split.classList.contains('agent-split--focus-collapsed'), false);
});

test('focus panel resize fraction and collapsed state survive split rerender after extraction', () => {
  const { sandbox, parts } = buildFocusSplitSandbox({
    focusPanelHeight: 240,
    focusContentHeight: 260,
    splitHeight: 800,
    handleHeight: 10,
  });
  let prevented = false;

  sandbox._agentFocusResizeStart({
    button: 0,
    clientY: 300,
    preventDefault() { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(typeof sandbox.document.listeners.mousemove, 'function');
  assert.equal(typeof sandbox.document.listeners.mouseup, 'function');

  sandbox.document.listeners.mousemove({ clientY: 145 });

  assert.equal(parts.body.classList.contains('agent-focus-resizing'), true);
  assert.equal(parts.focus.style.height, '395px');
  assert.equal(parts.focus.style.flexBasis, '395px');
  assert.equal(sandbox.sendCalls.length, 0, 'dragging must not persist before mouseup');

  sandbox.document.listeners.mouseup({ clientY: 145 });

  assert.equal(parts.body.classList.contains('agent-focus-resizing'), false);
  assert.equal(sandbox._agentFocusMode(), 'manual');
  assert.equal(sandbox.sendCalls.length, 1);
  assert.equal(sandbox.sendCalls[0].cmd, 'ui_set_engineer_panel_split');
  assert.equal(sandbox.sendCalls[0].fraction, 0.5);
  assert.equal(sandbox.state.engineer_panel_split_fraction, 0.5);

  parts.focus.style.height = '';
  parts.focus.style.flexBasis = '';
  sandbox._agentFocusApplyPersistedSplit();

  assert.equal(sandbox._agentFocusMode(), 'manual');
  assert.equal(parts.focus.style.height, '400px');
  assert.equal(parts.focus.style.flexBasis, '400px');

  sandbox._agentFocusSetCollapsed(true);
  assert.equal(sandbox._agentFocusIsCollapsed(), true);
  parts.split.classList.remove('agent-split--focus-collapsed');
  parts.handle.attributes = {};

  sandbox._agentFocusApplyPersistedSplit();

  assert.equal(sandbox._agentFocusIsCollapsed(), true);
  assert.equal(parts.split.classList.contains('agent-split--focus-collapsed'), true);
  assert.equal(parts.handle.getAttribute('role'), 'button');
  assert.equal(parts.handle.getAttribute('aria-label'), 'Expand focus panel');
  assert.equal(parts.handle.getAttribute('aria-expanded'), 'false');
});

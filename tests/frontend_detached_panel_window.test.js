/* Detached panel-window behavior regression coverage.
 *
 * In the Tauri desktop shell a "detach panel" opens a SEPARATE full webview
 * instance of the same app with `?panel=X&window=Y`. CSS hides everything but
 * the one panel, but the JS delta pipeline used to run unconditionally in that
 * hidden instance and produced two user-visible bugs:
 *
 *   Bug 1 — a `ui_update standalone_panel_layout` delta (broadcast when the
 *   MAIN window resizes a panel) re-applied the main window's layout to the
 *   detached window's DOM, parking/hiding its own root (black window) and
 *   repointing `_activePanelApp` at the hidden main-window surface.
 *
 *   Bug 2 — the detached window ran the full grid/terminal render, opened its
 *   own PTY WebSocket for the shared session, fit()'d a zero-size xterm and
 *   sent `resize`/`focus` frames that clobbered the main window's terminal down
 *   to the 20-column clamp floor.
 *
 * These tests exercise the frontend guards that make a detached window inert
 * for everything except its own panel surface, and confirm the main (non-
 * detached) window is unchanged.
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
/* Minimal DOM fakes                                                    */
/* ------------------------------------------------------------------ */

class FakeClassList {
  constructor(initial = []) { this._items = new Set(initial); }
  add(...names) { for (const n of names) this._items.add(n); }
  remove(...names) { for (const n of names) this._items.delete(n); }
  contains(name) { return this._items.has(name); }
  toggle(name, force) {
    if (force === true) { this._items.add(name); return true; }
    if (force === false) { this._items.delete(name); return false; }
    if (this._items.has(name)) { this._items.delete(name); return false; }
    this._items.add(name); return true;
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.dataset = {};
    this.style = { setProperty() {}, removeProperty() {} };
    this.children = [];
    this.classList = new FakeClassList();
    this.parentNode = null;
    this.textContent = '';
    this._innerHTML = '';
    this._torqueLastHtml = undefined;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) { this._innerHTML = v; this.children = []; }
  appendChild(child) {
    if (child && child.parentNode) {
      child.parentNode.children = (child.parentNode.children || []).filter((c) => c !== child);
    }
    this.children.push(child);
    if (child) child.parentNode = this;
    return child;
  }
  setAttribute(k, v) { this.dataset[k] = v; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  focus() {}
}

/* ------------------------------------------------------------------ */
/* Bug 1 — ws.js: ui_update standalone_panel_layout                    */
/* ------------------------------------------------------------------ */

function createWsDeltaHarness(detached) {
  const applyLayoutCalls = [];
  const sandbox = {
    console, JSON, Math, Date,
    state: {},
    location: { host: 'localhost:18932' },
    WebSocket: function () {},
    document: {
      activeElement: null,
      getElementById() { return null; },
      addEventListener() {},
    },
    window: { addEventListener() {} },
    setTimeout() { return 0; },
    clearTimeout() {},
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');

  // Cross-file collaborators the ui_update branch consults, stubbed after load
  // so their sandbox-global bindings win over any (absent) originals.
  sandbox._detachedWindowActive = function () { return !!detached; };
  sandbox._standalonePanelsEnabled = function () { return false; };
  sandbox._standaloneVisiblePanelApps = function () { return []; };
  sandbox._syncVisibleStandalonePanelApps = function () {};
  sandbox._standalonePanelSetLayoutFromState = function (layout, opts) {
    applyLayoutCalls.push({ layout: layout, opts: opts });
  };

  return {
    sandbox,
    applyLayoutCalls,
    // ws.js declares its state as a lexical `let state`, so it is not a sandbox
    // global; read it back through the context.
    getStateLayout() {
      return JSON.parse(
        vm.runInContext('JSON.stringify(state.standalone_panel_layout || null)', context),
      );
    },
    applyLayoutDelta(value) {
      vm.runInContext(
        '_applyDelta([{ op: "ui_update", key: "standalone_panel_layout", value: ' +
          JSON.stringify(value) + ' }]);',
        context,
      );
    },
  };
}

test('ws.js: detached window keeps layout state in sync but never re-applies it to its DOM', () => {
  const h = createWsDeltaHarness(true);
  h.applyLayoutDelta({ bottom: { open: true, active: 'chat' } });

  assert.deepEqual(
    h.getStateLayout(),
    { bottom: { open: true, active: 'chat' } },
    'detached window must still patch state.standalone_panel_layout',
  );
  assert.equal(h.applyLayoutCalls.length, 0,
    'detached window must NOT call _standalonePanelSetLayoutFromState');
});

test('ws.js: non-detached window still applies the layout delta to its DOM', () => {
  const h = createWsDeltaHarness(false);
  h.applyLayoutDelta({ bottom: { open: true, active: 'chat' } });

  assert.deepEqual(
    h.getStateLayout(),
    { bottom: { open: true, active: 'chat' } },
    'main window patches state.standalone_panel_layout',
  );
  assert.equal(h.applyLayoutCalls.length, 1,
    'main window applies the layout via _standalonePanelSetLayoutFromState');
});

/* ------------------------------------------------------------------ */
/* Bug 1 — panel_manager.js belt-and-braces guards                     */
/* ------------------------------------------------------------------ */

function createPanelManagerHarness(detached) {
  const getByIdRequests = [];
  const elements = Object.create(null);
  function ensureEl(id) {
    if (!elements[id]) elements[id] = new FakeElement(id);
    return elements[id];
  }
  // The detached panel's own root (engineer panel maps to #panel-agent, but the
  // board panel root is #panel-board). Pre-create the board root so we can
  // assert it is never parked/hidden.
  const boardRoot = ensureEl('panel-board');

  const body = new FakeElement('body');
  const sandbox = {
    console, JSON, Math, Date,
    URLSearchParams,
    location: { search: detached ? '?panel=board&window=w1' : '' },
    state: { detached_panels: {} },
    _activePanelApp: 'board',
    setTimeout() { return 0; },
    clearTimeout() {},
    window: { innerHeight: 900, innerWidth: 1400, addEventListener() {} },
    document: {
      body: body,
      documentElement: new FakeElement('html'),
      getElementById(id) {
        getByIdRequests.push(id);
        return elements[id] || null;
      },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');

  return { context, sandbox, boardRoot, getByIdRequests, ensureEl };
}

test('panel_manager.js: detached mode is detected from ?panel/?window', () => {
  const h = createPanelManagerHarness(true);
  assert.equal(h.sandbox._detachedWindowActive(), true);
  assert.ok(h.sandbox.document.body.classList.contains('detached-window'),
    'detached body gets the detached-window class');
});

test('panel_manager.js: _standalonePanelSetLayout in detached mode preserves _activePanelApp and never re-places roots', () => {
  const h = createPanelManagerHarness(true);
  const mainLayout = {
    bottom: { open: true, tabs: ['chat'], active: 'chat' },
    right: { open: false, tabs: [], active: '' },
    floats: {},
    last_active: 'chat',
  };

  vm.runInContext(
    '_standalonePanelSetLayout(' + JSON.stringify(mainLayout) + ', { fromServer: true });',
    h.context,
  );

  assert.equal(h.sandbox._activePanelApp, 'board',
    'detached window must NOT repoint _activePanelApp at the main window active app');
  assert.ok(h.sandbox.state.standalone_panel_layout,
    'state.standalone_panel_layout is still updated');
  assert.ok(!h.getByIdRequests.includes('standalone-bottom-dock'),
    '_standaloneRenderPanelWorkspace must early-return before touching the dock DOM');
  assert.ok(!h.boardRoot.classList.contains('panel-hidden'),
    'the detached panel root must not be hidden');
  assert.equal(h.boardRoot.parentNode, null,
    'the detached panel root must not be reparented (parked) under #bottom-panel');
});

test('panel_manager.js: non-detached mode still recomputes _activePanelApp and renders the workspace', () => {
  const h = createPanelManagerHarness(false);
  const layout = {
    bottom: { open: true, tabs: ['chat'], active: 'chat' },
    right: { open: false, tabs: [], active: '' },
    floats: {},
    last_active: 'chat',
  };

  vm.runInContext(
    '_standalonePanelSetLayout(' + JSON.stringify(layout) + ', { fromServer: true });',
    h.context,
  );

  assert.equal(h.sandbox._activePanelApp, 'chat',
    'main window recomputes _activePanelApp from the applied layout');
  assert.ok(h.getByIdRequests.includes('standalone-bottom-dock'),
    '_standaloneRenderPanelWorkspace runs the placement pass in the main window');
});

/* ------------------------------------------------------------------ */
/* Bug 2 — render.js: renderInvalidatedSurfaces                        */
/* ------------------------------------------------------------------ */

function createRenderHarness(detached) {
  const renderCalls = [];
  const focusCalls = [];
  const surfaceCalls = [];
  const sandbox = {
    console, JSON, Math, Date,
    state: { engineer_settings: {} },
    document: {
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    window: {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  sandbox._detachedWindowActive = function () { return !!detached; };
  sandbox.render = function (opts) { renderCalls.push(opts); };
  sandbox.renderAgentFocusPanel = function () { focusCalls.push(true); };
  sandbox._currentPanelSurfaces = function () { return ['board']; };
  sandbox._renderSurface = function (s) { surfaceCalls.push(s); };
  sandbox._updateEngineerTaskbarBadge = function () {};
  sandbox.updateEventsAttentionBadge = function () {};

  return {
    context, sandbox, renderCalls, focusCalls, surfaceCalls,
    invalidate(flags) {
      vm.runInContext('renderInvalidatedSurfaces(' + JSON.stringify(flags) + ');', context);
    },
  };
}

test('render.js: detached window drops main/terminal/focus flags but still renders its panel surface', () => {
  const h = createRenderHarness(true);
  h.invalidate({ main: true, terminal: true, focus: true, board: true });

  assert.equal(h.renderCalls.length, 0,
    'detached window must never run the grid render / FLIP');
  assert.equal(h.focusCalls.length, 0,
    'detached window must never run the focus-panel refresh');
  assert.deepEqual(h.surfaceCalls, ['board'],
    'detached window still renders its own visible panel surface from deltas');
});

test('render.js: non-detached window runs the grid render and panel surfaces normally', () => {
  const h = createRenderHarness(false);
  h.invalidate({ main: true, board: true });

  assert.equal(h.renderCalls.length, 1,
    'main window runs render() when the main flag fires');
  assert.deepEqual(h.surfaceCalls, ['board'],
    'main window still renders invalidated panel surfaces');
});

/* ------------------------------------------------------------------ */
/* Bug 2 — terminal.js: renderTerminalWorkspace                        */
/* ------------------------------------------------------------------ */

function createTerminalHarness(detached) {
  const wsConstructions = [];
  const connectCalls = [];
  let embeddedModeChecks = 0;

  const root = new FakeElement('terminal-workspace');
  const stage = new FakeElement('stage');
  const dom = {
    topbar: new FakeElement('topbar'),
    tabs: new FakeElement('tabs'),
    stage: stage,
    directMessages: new FakeElement('dm'),
    compose: new FakeElement('compose'),
    statusbar: new FakeElement('statusbar'),
  };

  const sandbox = {
    console, JSON, Math, Date,
    state: {},
    setTimeout() { return 0; },
    clearTimeout() {},
    document: {
      activeElement: null,
      getElementById(id) { return id === 'terminal-workspace' ? root : null; },
      querySelector() { return null; },
      addEventListener() {},
    },
    window: { addEventListener() {}, innerHeight: 900 },
    WebSocket: function (url) { wsConstructions.push(url); this.readyState = 0; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/terminal.js');

  // Guard + collaborators.
  sandbox._detachedWindowActive = function () { return !!detached; };
  sandbox._terminalComposePersistFromDom = function () {};
  sandbox._disposeEmbeddedTerminal = function () {};
  sandbox.isEmbeddedTerminalMode = function () { embeddedModeChecks += 1; return true; };
  sandbox._connectEmbeddedTerminal = function (cell, surface) { connectCalls.push({ cell, surface }); };

  // Everything renderTerminalWorkspace touches on the embedded (non-detached)
  // path before reaching _connectEmbeddedTerminal, stubbed so we can assert the
  // connect is reached in the main window.
  const cell = { id: 'cell-1', name: 'Agent', session_id: 'sess-1', cell_type: 'worker' };
  sandbox._pruneEmbeddedTerminalSessions = function () {};
  sandbox._terminalCurrentGroupName = function () { return ''; };
  sandbox._terminalGroupCells = function () { return []; };
  sandbox._resolveTerminalWorkspaceCell = function () { return cell; };
  sandbox._terminalTargetAgent = function () { return null; };
  sandbox._terminalDisplayPath = function () { return ''; };
  sandbox._ensureTerminalWorkspaceDom = function () { return dom; };
  sandbox._captureTerminalWorkspaceState = function () { return null; };
  sandbox._terminalWorkspaceFocusedComposeHasDraft = function () { return false; };
  sandbox.esc = function (s) { return String(s == null ? '' : s); };
  sandbox._findEmbeddedTerminalEntryForCell = function () { return null; };
  sandbox._createEmbeddedTerminalSurface = function () { return new FakeElement('surface'); };
  sandbox._activateEmbeddedTerminalSurface = function () {};
  sandbox._renderTerminalDirectMessages = function () {};
  sandbox._renderTerminalCompose = function () {};
  sandbox._terminalStatusLabel = function () { return ''; };
  sandbox._restoreTerminalWorkspaceState = function () {};

  return {
    context, sandbox, root,
    wsConstructions, connectCalls,
    embeddedModeChecks() { return embeddedModeChecks; },
    render() { vm.runInContext('renderTerminalWorkspace();', context); },
  };
}

test('terminal.js: detached window opens no PTY socket and never connects the embedded terminal', () => {
  const h = createTerminalHarness(true);
  h.render();

  assert.equal(h.wsConstructions.length, 0,
    'detached window must not construct a terminal WebSocket');
  assert.equal(h.connectCalls.length, 0,
    'detached window must not reach _connectEmbeddedTerminal');
  assert.equal(h.embeddedModeChecks(), 0,
    'detached guard short-circuits before isEmbeddedTerminalMode is even evaluated');
  assert.equal(h.root.innerHTML, '',
    'detached window tears the workspace DOM down');
  assert.ok(!h.root.classList.contains('active'),
    'detached window deactivates the terminal workspace');
});

test('terminal.js: non-detached window still connects the embedded terminal', () => {
  const h = createTerminalHarness(false);
  h.render();

  assert.ok(h.embeddedModeChecks() >= 1,
    'main window flows past the detached guard into the normal render pipeline');
  assert.equal(h.connectCalls.length, 1,
    'main window reaches _connectEmbeddedTerminal for the active session');
});

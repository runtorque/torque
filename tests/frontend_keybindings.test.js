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

function keyEvent(key, opts = {}) {
  const event = {
    key,
    ctrlKey: !!opts.ctrl,
    metaKey: !!opts.meta,
    altKey: !!opts.alt,
    shiftKey: !!opts.shift,
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
  return event;
}

function createKeybindingContext(extra = {}) {
  const sandbox = Object.assign({
    console,
    JSON,
    state: { global_settings: { keybindings: {} } },
    moveFocusUp() {},
    moveFocusDown() {},
    moveFocusHorizontal() {},
    activateFocused() {},
    removeFocused() {},
    switchGroup() {},
    focusComposerForFocusedAgent() { return true; },
    openAddTerminalForFocused() {},
    openAddTaskForFocused() {},
    togglePanel() {},
  }, extra);
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/keybindings.js');
  return context;
}

test('_kbMatches is exact on modifiers and case-insensitive for letter keys', () => {
  const context = createKeybindingContext();
  const binding = { key: 't', ctrl: true, meta: false, alt: true, shift: false };

  assert.equal(context._kbMatches(keyEvent('T', { ctrl: true, alt: true }), binding), true);
  assert.equal(context._kbMatches(keyEvent('t', { ctrl: true, alt: true, shift: true }), binding), false);
  assert.equal(context._kbMatches(keyEvent('t', { ctrl: true }), binding), false);
  assert.equal(context._kbMatches(keyEvent('ArrowUp'), { key: 'ArrowUp', ctrl: false, meta: false, alt: false, shift: false }), true);
  assert.equal(context._kbMatches(keyEvent('ArrowUp', { meta: true }), { key: 'ArrowUp', ctrl: false, meta: false, alt: false, shift: false }), false);
});

test('effectiveKeybindings uses user overrides and discards old iTerm2-shaped entries', () => {
  const context = createKeybindingContext();
  context.state.global_settings.keybindings = {
    'terminal.create': { key: 'x', ctrl: true, meta: false, alt: false, shift: false },
    'task.create': { modifiers: ['command'], keycode: 'ANSI_N', character: 78 },
  };

  const effective = context.effectiveKeybindings();

  assert.deepEqual(JSON.parse(JSON.stringify(effective['terminal.create'])), {
    key: 'x', ctrl: true, meta: false, alt: false, shift: false,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(effective['task.create'])), {
    key: 'n', ctrl: false, meta: false, alt: false, shift: false,
  });
});

test('dispatchKeybindingEvent runs registry handlers and override beats default', () => {
  const calls = [];
  const context = createKeybindingContext({
    openAddTerminalForFocused() { calls.push('terminal'); },
    openAddTaskForFocused() { calls.push('task'); },
  });
  context.state.global_settings.keybindings = {
    'terminal.create': { key: 'x', ctrl: true, meta: false, alt: false, shift: false },
  };

  const defaultEvent = keyEvent('t');
  assert.equal(context.dispatchKeybindingEvent(defaultEvent), false);
  assert.equal(defaultEvent.defaultPrevented, false);
  assert.deepEqual(calls, []);

  const overrideEvent = keyEvent('x', { ctrl: true });
  assert.equal(context.dispatchKeybindingEvent(overrideEvent), true);
  assert.equal(overrideEvent.defaultPrevented, true);
  assert.deepEqual(calls, ['terminal']);
});

function createMainHarness() {
  const calls = [];
  const ids = new Map();
  function element(id = '') {
    return {
      id,
      tagName: 'DIV',
      value: '',
      type: 'text',
      classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
      style: {},
      dataset: {},
      addEventListener() {},
      removeEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      scrollIntoView() {},
      focus() { calls.push(['focus', id]); },
    };
  }
  const document = {
    activeElement: null,
    _modalOpen: false,
    _keydown: null,
    body: element('body'),
    addEventListener(type, handler) { if (type === 'keydown') this._keydown = handler; },
    removeEventListener() {},
    querySelector(selector) {
      if (selector === '.overlay.visible' && this._modalOpen) return element('modal');
      return null;
    },
    querySelectorAll() { return []; },
    getElementById(id) {
      if (!ids.has(id)) ids.set(id, element(id));
      return ids.get(id);
    },
  };
  const sandbox = {
    console,
    JSON,
    Date,
    Math,
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
    requestAnimationFrame(fn) { fn(); return 0; },
    window: {},
    document,
    state: {
      global_settings: { keybindings: {} },
      groups: { alpha: ['agent-1'] },
      agents: { 'agent-1': { id: 'agent-1', group: 'alpha', cell_type: 'agent' } },
    },
    selectedAgentId: 'agent-1',
    selectedTerminalId: 'agent-1',
    focusedItemId: 'agent-1',
    boardKeydown() { calls.push('board'); return false; },
    closeModals() { calls.push('closeModals'); },
    closeMenus() { calls.push('closeMenus'); },
    closeContextMenu() { calls.push('closeContextMenu'); },
    openCheatsheet() { calls.push('cheatsheet'); },
    openAddEngineerModal() { calls.push('engineer'); },
    openAddArchitectModal() { calls.push('architect'); },
    quickAddTerminal(group, agent) { calls.push(['terminal', group, agent]); },
    openAddTask(lane) { calls.push(['task', lane]); },
    connect() {},
    setupDrag() {},
    send() {},
    render() {},
  };
  sandbox.window = Object.assign(sandbox.window, sandbox);
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/keybindings.js');
  loadScript(context, 'static/js/main.js');
  return { context, document, calls };
}

test('main keydown preserves modal and input focus guards before registry dispatch', () => {
  const { document, calls } = createMainHarness();

  document.activeElement = { tagName: 'INPUT', type: 'text' };
  const inputEvent = keyEvent('t');
  document._keydown(inputEvent);
  assert.deepEqual(calls, []);
  assert.equal(inputEvent.defaultPrevented, false);

  document.activeElement = { tagName: 'DIV' };
  document._modalOpen = true;
  const modalEvent = keyEvent('t');
  document._keydown(modalEvent);
  assert.deepEqual(calls, []);
  assert.equal(modalEvent.defaultPrevented, false);

  const escapeEvent = keyEvent('Escape');
  document._keydown(escapeEvent);
  assert.deepEqual(calls, ['closeModals']);

  document._modalOpen = false;
  const normalEvent = keyEvent('t');
  document._keydown(normalEvent);
  assert.deepEqual(calls.slice(-1)[0], ['terminal', 'alpha', 'agent-1']);
  assert.equal(normalEvent.defaultPrevented, true);
});

function createModalHarness() {
  const listeners = [];
  const container = {
    _html: '',
    scrollTop: 0,
    get innerHTML() { return this._html; },
    set innerHTML(value) { this._html = String(value); },
  };
  const sandbox = {
    console,
    JSON,
    state: { global_settings: { keybindings: {} } },
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },
    document: {
      getElementById(id) { return id === 'gls-keybinding-list' ? container : null; },
      addEventListener(type, handler, capture) { listeners.push({ type, handler, capture }); },
      removeEventListener(type, handler) {
        const index = listeners.findIndex((item) => item.type === type && item.handler === handler);
        if (index >= 0) listeners.splice(index, 1);
      },
      createElement() { return {}; },
      querySelectorAll() { return []; },
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/keybindings.js');
  loadScript(context, 'static/js/modals.js');
  vm.runInContext('_glsDefaults = keybindingDefaults(); _glsKeybindings = {}; _glsPendingConflict = null; _glsCapturing = null;', context);
  return { context, container, listeners };
}

test('settings capture detects conflicts and confirm-reassign avoids double binding', () => {
  const { context, container, listeners } = createModalHarness();

  context._startCapture('terminal.create');
  assert.equal(listeners.length, 1);
  context._captureKeydown(keyEvent('n'));

  assert.match(container.innerHTML, /already assigned to/);
  assert.equal(vm.runInContext('_glsPendingConflict.conflictAction', context), 'task.create');
  assert.equal(vm.runInContext('_glsKeybindings["terminal.create"]', context), undefined);

  context._confirmKeybindingReassign();
  const overrides = JSON.parse(JSON.stringify(vm.runInContext('_glsKeybindings', context)));
  assert.deepEqual(overrides['terminal.create'], { key: 'n', ctrl: false, meta: false, alt: false, shift: false });
  assert.deepEqual(overrides['task.create'], { key: 't', ctrl: false, meta: false, alt: false, shift: false });
  assert.doesNotMatch(container.innerHTML, /already assigned to/);
});

test('settings keybinding rerender preserves capture row and scroll on global_settings_update', () => {
  const { context, container } = createModalHarness();
  container.scrollTop = 137;
  vm.runInContext('_glsCapturing = "terminal.create";', context);

  context._syncKeybindingSettingsFromGlobal({
    keybindings: { 'terminal.create': { key: 'x', ctrl: false, meta: false, alt: false, shift: false } },
  });

  assert.equal(container.scrollTop, 137);
  assert.match(container.innerHTML, /kb-capturing/);
  assert.match(container.innerHTML, /Press keys/);
  assert.equal(vm.runInContext('_glsCapturing', context), 'terminal.create');
});

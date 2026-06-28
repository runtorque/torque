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
    target: opts.target || null,
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

test('effectiveKeybindings uses user overrides and discards removed or old-shaped entries', () => {
  const context = createKeybindingContext();
  context.state.global_settings.keybindings = {
    'terminal.create': { key: 'x', ctrl: true, meta: false, alt: false, shift: false },
    'task.create': { modifiers: ['command'], keycode: 'ANSI_N', character: 78 },
    'panel.toggle': { key: 'b', ctrl: false, meta: false, alt: false, shift: false },
  };

  const effective = context.effectiveKeybindings();

  assert.equal(effective['terminal.create'], undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(effective['task.create'])), {
    key: 'n', ctrl: false, meta: false, alt: false, shift: false,
  });
  assert.deepEqual(JSON.parse(JSON.stringify(effective['panel.toggle'])), {
    key: 'b', ctrl: false, meta: false, alt: false, shift: false,
  });
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      context.sanitizeKeybindingOverrides(context.state.global_settings.keybindings),
      'terminal.create',
    ),
    false,
  );
});

test('dispatchKeybindingEvent runs registry handlers and override beats default', () => {
  const calls = [];
  const context = createKeybindingContext({
    openAddTaskForFocused() { calls.push('task'); },
  });
  context.state.global_settings.keybindings = {
    'task.create': { key: 'x', ctrl: true, meta: false, alt: false, shift: false },
  };

  const removedTerminalEvent = keyEvent('t');
  assert.equal(context.dispatchKeybindingEvent(removedTerminalEvent), false);
  assert.equal(removedTerminalEvent.defaultPrevented, false);
  assert.deepEqual(calls, []);

  const defaultEvent = keyEvent('n');
  assert.equal(context.dispatchKeybindingEvent(defaultEvent), false);
  assert.equal(defaultEvent.defaultPrevented, false);
  assert.deepEqual(calls, []);

  const overrideEvent = keyEvent('x', { ctrl: true });
  assert.equal(context.dispatchKeybindingEvent(overrideEvent), true);
  assert.equal(overrideEvent.defaultPrevented, true);
  assert.deepEqual(calls, ['task']);
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
    openAddTask(lane) { calls.push(['task', lane]); },
    removeAgent(id) { calls.push(['remove', id]); },
    _terminalComposeInputId(cellId) { return `terminal-compose-input-${cellId}`; },
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

  document.activeElement = { tagName: 'SELECT' };
  const selectEvent = keyEvent('t');
  document._keydown(selectEvent);
  assert.deepEqual(calls, []);
  assert.equal(selectEvent.defaultPrevented, false);

  document.activeElement = { tagName: 'TEXTAREA' };
  const textareaEvent = keyEvent('t');
  document._keydown(textareaEvent);
  assert.deepEqual(calls, []);
  assert.equal(textareaEvent.defaultPrevented, false);

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
  const removedTerminalEvent = keyEvent('t');
  document._keydown(removedTerminalEvent);
  assert.deepEqual(calls, ['closeModals']);
  assert.equal(removedTerminalEvent.defaultPrevented, false);

  const normalEvent = keyEvent('n');
  document._keydown(normalEvent);
  assert.deepEqual(calls.slice(-1)[0], ['task', '']);
  assert.equal(normalEvent.defaultPrevented, true);
});

test('main keydown composer.focus moves focus from non-input state and respects input guard', () => {
  const { document, calls } = createMainHarness();

  document.activeElement = { tagName: 'DIV' };
  const focusEvent = keyEvent('c');
  document._keydown(focusEvent);
  assert.deepEqual(calls, [['focus', 'terminal-compose-input-agent-1']]);
  assert.equal(focusEvent.defaultPrevented, true);

  [
    { tagName: 'INPUT', type: 'text' },
    { tagName: 'SELECT' },
    { tagName: 'TEXTAREA' },
  ].forEach((activeElement) => {
    calls.length = 0;
    document.activeElement = activeElement;
    const inputFocusEvent = keyEvent('c');
    document._keydown(inputFocusEvent);
    assert.deepEqual(calls, []);
    assert.equal(inputFocusEvent.defaultPrevented, false);
  });
});

test('main keydown suppresses global shortcuts from contenteditable composer', () => {
  const { context, document, calls } = createMainHarness();
  const composer = {
    tagName: 'DIV',
    isContentEditable: true,
    classList: { contains(name) { return name === 'terminal-compose-input'; } },
  };
  const composerChild = {
    tagName: 'SPAN',
    isContentEditable: false,
  };

  document.activeElement = composer;
  ['n', 'c', 'Delete', 'Backspace'].forEach((key) => {
    const event = keyEvent(key, { target: composer });
    document._keydown(event);
    assert.deepEqual(calls, []);
    assert.equal(event.defaultPrevented, false);
  });

  context._activePanelApp = 'board';
  const boardDeleteEvent = keyEvent('Delete', { target: composer });
  document._keydown(boardDeleteEvent);
  assert.deepEqual(calls, []);
  assert.equal(boardDeleteEvent.defaultPrevented, false);
  context._activePanelApp = '';

  const childTargetEvent = keyEvent('n', { target: composerChild });
  document._keydown(childTargetEvent);
  assert.deepEqual(calls, []);
  assert.equal(childTargetEvent.defaultPrevented, false);

  document.activeElement = document.body;
  const outsideTaskEvent = keyEvent('n', { target: document.body });
  document._keydown(outsideTaskEvent);
  assert.deepEqual(calls, [['task', '']]);
  assert.equal(outsideTaskEvent.defaultPrevented, true);

  calls.length = 0;
  const outsideDeleteEvent = keyEvent('Delete', { target: document.body });
  document._keydown(outsideDeleteEvent);
  assert.deepEqual(calls, [['remove', 'agent-1']]);
  assert.equal(outsideDeleteEvent.defaultPrevented, true);
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

  context._startCapture('panel.toggle');
  assert.equal(listeners.length, 1);
  context._captureKeydown(keyEvent('n'));

  assert.match(container.innerHTML, /already assigned to/);
  assert.equal(vm.runInContext('_glsPendingConflict.conflictAction', context), 'task.create');
  assert.equal(vm.runInContext('_glsKeybindings["panel.toggle"]', context), undefined);

  context._confirmKeybindingReassign();
  const overrides = JSON.parse(JSON.stringify(vm.runInContext('_glsKeybindings', context)));
  assert.deepEqual(overrides['panel.toggle'], { key: 'n', ctrl: false, meta: false, alt: false, shift: false });
  assert.deepEqual(overrides['task.create'], { key: 'k', ctrl: false, meta: false, alt: false, shift: false });
  assert.doesNotMatch(container.innerHTML, /already assigned to/);
});

test('settings keybinding rerender preserves capture row and scroll on global_settings_update', () => {
  const { context, container } = createModalHarness();
  container.scrollTop = 137;
  vm.runInContext('_glsCapturing = "panel.toggle";', context);

  context._syncKeybindingSettingsFromGlobal({
    keybindings: { 'panel.toggle': { key: 'x', ctrl: false, meta: false, alt: false, shift: false } },
  });

  assert.equal(container.scrollTop, 137);
  assert.match(container.innerHTML, /kb-capturing/);
  assert.match(container.innerHTML, /Press keys/);
  assert.equal(vm.runInContext('_glsCapturing', context), 'panel.toggle');
});

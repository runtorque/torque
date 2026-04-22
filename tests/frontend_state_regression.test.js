const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor(initial = []) {
    this._items = new Set(initial);
  }

  add(...names) {
    for (const name of names) this._items.add(name);
  }

  remove(...names) {
    for (const name of names) this._items.delete(name);
  }

  contains(name) {
    return this._items.has(name);
  }

  toggle(name, force) {
    if (force === true) {
      this._items.add(name);
      return true;
    }
    if (force === false) {
      this._items.delete(name);
      return false;
    }
    if (this._items.has(name)) {
      this._items.delete(name);
      return false;
    }
    this._items.add(name);
    return true;
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.dataset = {};
    this.attributes = {};
    this.style = {};
    this.children = [];
    this.classList = new FakeClassList();
    this.scrollTop = 0;
    this.scrollHeight = 600;
    this.clientHeight = 240;
    this.scrollLeft = 0;
    this.scrollWidth = 600;
    this.clientWidth = 240;
    this.offsetTop = 0;
    this.offsetHeight = 24;
    this.offsetLeft = 0;
    this.offsetWidth = 80;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.selectionDirection = 'none';
    this.focused = false;
    this.selected = false;
    this.listeners = {};
    this._innerHTML = '';
    this._querySelectorMap = new Map();
    this._querySelectorAllMap = new Map();
    this.parentNode = null;
    this._detachChildrenOnInnerHTMLClear = false;
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    if (this._detachChildrenOnInnerHTMLClear) {
      this.children.forEach((child) => {
        child.parentNode = null;
      });
    }
    this.children = [];
  }

  appendChild(child) {
    if (child.parentNode && Array.isArray(child.parentNode.children)) {
      child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
    }
    child.parentNode = this;
    this.children.push(child);
    if (child.selected) this.value = child.value;
    return child;
  }

  insertBefore(child, before) {
    if (!before) return this.appendChild(child);
    if (child.parentNode && Array.isArray(child.parentNode.children)) {
      child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
    }
    const idx = this.children.indexOf(before);
    child.parentNode = this;
    if (idx >= 0) this.children.splice(idx, 0, child);
    else this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  removeEventListener(type, handler) {
    if (this.listeners[type] === handler) delete this.listeners[type];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      this.dataset[key] = String(value);
    }
  }

  getAttribute(name) {
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
      if (Object.prototype.hasOwnProperty.call(this.dataset, key)) return this.dataset[key];
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }

  hasAttribute(name) {
    return this.getAttribute(name) !== null;
  }

  focus() {
    this.focused = true;
  }

  select() {
    this.selected = true;
  }

  remove() {
    this.removed = true;
    if (this.parentNode && Array.isArray(this.parentNode.children)) {
      this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    }
    this.parentNode = null;
  }

  contains(target) {
    if (target === this) return true;
    return this.children.some((child) => child === target || (child && child.contains && child.contains(target)));
  }

  closest(selector) {
    for (let node = this; node; node = node.parentNode) {
      if (node._matchesSimpleSelector && node._matchesSimpleSelector(selector)) return node;
    }
    return null;
  }

  _matchesSimpleSelector(selector) {
    if (!selector) return false;
    const classMatch = selector.match(/^\.([A-Za-z0-9_-]+)(.*)$/);
    if (classMatch) {
      if (!this.classList.contains(classMatch[1])) return false;
      const rest = classMatch[2] || '';
      if (!rest) return true;
      const attrMatch = rest.match(/^\[([^\]=]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
      if (!attrMatch) return false;
      const value = this.getAttribute(attrMatch[1]);
      if (value === null) return false;
      return typeof attrMatch[2] === 'undefined' || String(value) === attrMatch[2];
    }
    const attrMatch = selector.match(/^\[([^\]=]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
    if (attrMatch) {
      const value = this.getAttribute(attrMatch[1]);
      if (value === null) return false;
      return typeof attrMatch[2] === 'undefined' || String(value) === attrMatch[2];
    }
    return false;
  }

  get nextElementSibling() {
    if (!this.parentNode || !Array.isArray(this.parentNode.children)) return null;
    const index = this.parentNode.children.indexOf(this);
    if (index < 0) return null;
    return this.parentNode.children[index + 1] || null;
  }

  getBoundingClientRect() {
    return {
      top: 16,
      bottom: 40,
      left: 24,
      right: 184,
      width: 160,
      height: 24,
    };
  }

  scrollIntoView(options) {
    this.scrollIntoViewOptions = options;
  }

  querySelector(selector) {
    return this._querySelectorMap.get(selector) || null;
  }

  querySelectorAll(selector) {
    return this._querySelectorAllMap.get(selector) || [];
  }

  setQuerySelector(selector, value) {
    this._querySelectorMap.set(selector, value);
  }

  setQuerySelectorAll(selector, value) {
    this._querySelectorAllMap.set(selector, value);
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.selectorMap = new Map();
    this.selectorAllMap = new Map();
    this.body = new FakeElement('body');
    this.activeElement = null;
    this.listeners = {};
    this._restrictToAttachedIds = false;
  }

  register(id, element = new FakeElement(id)) {
    this.elements.set(id, element);
    return element;
  }

  getElementById(id) {
    const element = this.elements.get(id) || null;
    if (!element) return null;
    if (this._restrictToAttachedIds && !this.isAttached(element)) return null;
    return element;
  }

  createElement(tagName) {
    const el = new FakeElement();
    el.tagName = tagName.toUpperCase();
    return el;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  removeEventListener(type, handler) {
    if (this.listeners[type] === handler) delete this.listeners[type];
  }

  querySelector(selector) {
    if (selector === '.overlay.visible') {
      const overlays = this.querySelectorAll('.overlay');
      return overlays.find((element) => element.classList.contains('visible')) || null;
    }
    return this.selectorMap.get(selector) || null;
  }

  querySelectorAll(selector) {
    return this.selectorAllMap.get(selector) || [];
  }

  setSelector(selector, element) {
    this.selectorMap.set(selector, element);
  }

  setSelectorAll(selector, elements) {
    this.selectorAllMap.set(selector, elements);
  }

  isAttached(element) {
    for (let node = element; node; node = node.parentNode) {
      if (node === this.body) return true;
    }
    return false;
  }
}

const PANEL_ROOT_IDS = [
  'panel-board',
  'panel-actions',
  'panel-templates',
  'panel-context',
  'panel-events',
  'panel-agent',
];

function createSandbox(overrides = {}) {
  const document = new FakeDocument();
  const localStorageState = new Map();
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    document,
    window: { open() {} },
    location: { host: 'localhost:9000' },
    navigator: { clipboard: { writeText() {} } },
    localStorage: {
      getItem(key) {
        return localStorageState.has(key) ? localStorageState.get(key) : null;
      },
      setItem(key, value) {
        localStorageState.set(String(key), String(value));
      },
      removeItem(key) {
        localStorageState.delete(String(key));
      },
      clear() {
        localStorageState.clear();
      },
    },
    requestAnimationFrame(fn) { fn(); },
    setTimeout(fn) { fn(); return 1; },
    clearTimeout() {},
    fetch() { return Promise.resolve({ ok: true }); },
    CSS: { escape(value) { return String(value); } },
    state: {
      groups: {},
      agents: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: [],
    },
    selectedAgentId: '',
    sendCalls: [],
    esc(value) { return String(value); },
    formatCode(value) { return String(value); },
    isSystemLabel(label) { return String(label).startsWith('loom:'); },
    displayLabel(label) {
      const text = String(label);
      return text.startsWith('loom:') ? text.slice(5) : text;
    },
    labelColor() { return '#58a6ff'; },
    _schedFormatTime(value) { return value; },
    _currentGroup() { return 'alpha'; },
    agentStatusClass() { return ''; },
    focusAgent() {},
  };
  Object.assign(sandbox, overrides);
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document };
}

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function loadModalScripts(context) {
  [
    'static/js/modals.js',
    'static/js/modals/add-cell.js',
    'static/js/modals/task-artifacts.js',
    'static/js/modals/task-modal.js',
    'static/js/modals/action-picker.js',
  ].forEach((file) => loadScript(context, file));
}

function loadBoardScripts(context) {
  loadScript(context, 'static/js/board.js');
  loadScript(context, 'static/js/board/view-state.js');
  loadScript(context, 'static/js/board/card-rendering.js');
  loadScript(context, 'static/js/board/card-actions.js');
}

function createEmbeddedTerminalHarness(overrides = {}) {
  const sockets = [];
  const terminals = [];
  const loadRenderHelpers = !!overrides.loadRenderHelpers;
  const sandboxOverrides = Object.assign({}, overrides);
  delete sandboxOverrides.loadRenderHelpers;

  class FakeTerminal {
    constructor() {
      this.cols = 80;
      this.rows = 24;
      this.writes = [];
      this.resetCount = 0;
      terminals.push(this);
    }

    loadAddon(addon) {
      this.addon = addon;
    }

    open(surface) {
      this.surface = surface;
    }

    onData(handler) {
      this.onDataHandler = handler;
      return {
        dispose: () => {
          this.dataDisposed = true;
        },
      };
    }

    write(data) {
      this.writes.push(data);
    }

    reset() {
      this.resetCount += 1;
      this.writes = [];
    }

    dispose() {
      this.disposed = true;
    }

    focus() {
      this.focusCount = (this.focusCount || 0) + 1;
    }
  }

  class FakeFitAddon {
    fit() {
      this.fitCalls = (this.fitCalls || 0) + 1;
    }
  }

  class FakeResizeObserver {
    constructor(callback) {
      this.callback = callback;
    }

    observe(target) {
      this.target = target;
    }

    disconnect() {
      this.disconnected = true;
    }
  }

  function FakeWebSocket(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.sent = [];
    sockets.push(this);
  }
  FakeWebSocket.OPEN = 1;
  FakeWebSocket.prototype.send = function send(payload) {
    this.sent.push(JSON.parse(payload));
  };
  FakeWebSocket.prototype.close = function close() {
    this.closeCalled = true;
    this.readyState = 3;
  };

  const { sandbox, document } = createSandbox(Object.assign({
    Terminal: FakeTerminal,
    FitAddon: { FitAddon: FakeFitAddon },
    ResizeObserver: FakeResizeObserver,
    WebSocket: FakeWebSocket,
    location: { protocol: 'http:', host: 'localhost:9000' },
  }, sandboxOverrides));
  const workspace = document.register('terminal-workspace');
  const status = new FakeElement('terminal-statusbar');
  workspace.setQuerySelector('.terminal-statusbar', status);
  document.setSelector('#terminal-workspace .terminal-statusbar', status);
  const context = vm.createContext(sandbox);
  if (loadRenderHelpers) {
    loadScript(context, 'static/js/render.js');
    runInContext(context, `
      var _cachedAgentTemplates = [];
      var focusedItemId = null;
      var selectedTerminalId = null;
      getFilterByWindow = function() { return false; };
    `);
  }
  loadScript(context, 'static/js/terminal.js');
  return { context, sandbox, document, sockets, terminals, status };
}

function attachTerminalWorkspaceDom(document) {
  const workspace = document.getElementById('terminal-workspace');
  const shell = new FakeElement('terminal-shell');
  const topbar = new FakeElement('terminal-topbar');
  const tabs = new FakeElement('terminal-tabs');
  const stage = new FakeElement('terminal-stage');
  const compose = new FakeElement('terminal-compose-slot');
  const statusbar = new FakeElement('terminal-statusbar');
  const surface = new FakeElement('terminal-surface');
  shell.classList.add('terminal-shell');
  topbar.classList.add('terminal-topbar');
  tabs.classList.add('terminal-tabs');
  stage.classList.add('terminal-stage');
  compose.classList.add('terminal-compose-slot');
  statusbar.classList.add('terminal-statusbar');
  surface.classList.add('terminal-surface');
  workspace.appendChild(shell);
  shell.appendChild(topbar);
  shell.appendChild(tabs);
  shell.appendChild(stage);
  shell.appendChild(compose);
  shell.appendChild(statusbar);
  stage.appendChild(surface);
  workspace.setQuerySelector('.terminal-shell', shell);
  shell.setQuerySelector('.terminal-topbar', topbar);
  shell.setQuerySelector('.terminal-tabs', tabs);
  shell.setQuerySelector('.terminal-stage', stage);
  shell.setQuerySelector('.terminal-compose-slot', compose);
  shell.setQuerySelector('.terminal-statusbar', statusbar);
  stage.setQuerySelector('.terminal-surface', surface);
  document.setSelector('#terminal-workspace .terminal-statusbar', statusbar);
  return { workspace, shell, topbar, tabs, stage, compose, statusbar, surface };
}

function runInContext(context, code) {
  return vm.runInContext(code, context);
}

function jsonValue(context, expression) {
  return JSON.parse(runInContext(context, `JSON.stringify(${expression})`));
}

function attachStandalonePanelDom(document) {
  const get = (id) => document.elements.get(id) || null;
  const shell = get('standalone-sidebar-shell');
  const stack = get('standalone-main-stack');
  const bottomDock = get('standalone-bottom-dock');
  const rightRail = get('standalone-right-rail');
  const floatLayer = get('standalone-float-layer');
  const bottomHandle = get('standalone-bottom-resize-handle');
  const railHandle = get('standalone-rail-resize-handle');
  const bottomPanel = get('bottom-panel');
  const panelResizeHandle = get('panel-resize-handle');

  if (shell) document.body.appendChild(shell);
  if (stack && shell) shell.appendChild(stack);
  if (railHandle && shell) shell.appendChild(railHandle);
  if (rightRail && shell) shell.appendChild(rightRail);
  if (bottomHandle && shell) shell.appendChild(bottomHandle);
  if (bottomDock && shell) shell.appendChild(bottomDock);
  if (floatLayer) document.body.appendChild(floatLayer);
  if (bottomPanel) document.body.appendChild(bottomPanel);
  if (panelResizeHandle && bottomPanel) bottomPanel.appendChild(panelResizeHandle);
  PANEL_ROOT_IDS.forEach((id) => {
    const panelRoot = get(id);
    if (panelRoot && bottomPanel) bottomPanel.appendChild(panelRoot);
  });

  [bottomDock, rightRail, floatLayer].forEach((element) => {
    if (element) element._detachChildrenOnInnerHTMLClear = true;
  });
  document._restrictToAttachedIds = true;
}

function findChildByClassName(element, className) {
  if (!element || !Array.isArray(element.children)) return null;
  return element.children.find((child) => child.classList && child.classList.contains(className)) || null;
}

function attachedStandalonePanelIds(document) {
  return PANEL_ROOT_IDS.filter((id) => !!document.getElementById(id));
}

function standaloneZoneBodyPanelIds(document, zoneId) {
  const zone = document.getElementById(zoneId);
  const body = findChildByClassName(zone, 'standalone-panel-zone-body');
  if (!body || !Array.isArray(body.children)) return [];
  return body.children.map((child) => child.id).filter(Boolean);
}

function standaloneFloatPanelIds(document) {
  const layer = document.getElementById('standalone-float-layer');
  if (!layer || !Array.isArray(layer.children)) return [];
  const ids = [];
  layer.children.forEach((shell) => {
    const body = findChildByClassName(shell, 'standalone-float-body');
    if (!body || !Array.isArray(body.children)) return;
    body.children.forEach((child) => {
      if (child && child.id) ids.push(child.id);
    });
  });
  return ids;
}

function parkedStandalonePanelIds(document) {
  const bottomPanel = document.getElementById('bottom-panel');
  if (!bottomPanel || !Array.isArray(bottomPanel.children)) return [];
  return bottomPanel.children
    .map((child) => child.id)
    .filter((id) => PANEL_ROOT_IDS.includes(id));
}

function createBoardHarness(options = {}) {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);
  const boot = [
    `_renderBoardSelectionBar = function() { return ''; };`,
    `_boardScheduleCount = function() { return 0; };`,
    `boardUpdateScrollArrows = function() {};`,
    `boardAddTaskAutoResize = function() {};`,
  ];
  if (options.stubCards !== false) {
    boot.unshift(`_renderBoardCard = function(t) { return '<div class="board-card">' + t.id + '</div>'; };`);
  }
  runInContext(context, boot.join('\n'));
  return { context, document };
}

function createSelectionHarness() {
  const { sandbox, document } = createSandbox();
  sandbox.renderCalls = { main: 0, board: 0 };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/commands.js');
  runInContext(context, `
    render = function() { renderCalls.main++; };
    renderBoard = function() { renderCalls.board++; };
    _activePanelApp = 'board';
    _currentGroup = function() {
      if (selectedAgentId && state && state.agents && state.agents[selectedAgentId]) {
        return state.agents[selectedAgentId].group || '';
      }
      return '';
    };
  `);
  return { context, document };
}

function createEventsHarness(options = {}) {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/events.js');
  if (options.stubRenderers !== false) {
    runInContext(context, `
      _renderAttentionCard = function(item) { return '<div class="attention-item">' + item.id + '</div>'; };
      _renderEventEntry = function(evt) { return '<div class="event-entry">' + evt.kind + '</div>'; };
      _eventsOnScroll = function() {};
    `);
  }
  return { context, document };
}

function createContextHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/context.js');
  return { context, document };
}

function createWeaverHarness() {
  const { sandbox, document } = createSandbox({
    _cachedProviders: [],
    _esc(value) { return String(value); },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/agent_panel.js');
  return { context, document };
}

function createWeaverWsHarness() {
  const { sandbox, document } = createSandbox({
    _cachedProviders: [],
    _esc(value) { return String(value); },
  });
  document.register('main');
  document.register('bottom-panel');
  document.register('panel-agent');
  document.register('conn-dot');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/agent_panel.js');
  runInContext(context, `
    send = function(message) { sendCalls.push(message); };
    render = function() {};
    _activePanelApp = 'weaver';
    _panelStateRestored = true;
  `);
  return { context, document, sandbox };
}

function createAgentHistoryHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);
  loadScript(context, 'static/js/templates.js');
  runInContext(context, `
    _renderBoardSelectionBar = function() { return ''; };
    _boardScheduleCount = function() { return 0; };
    boardUpdateScrollArrows = function() {};
    boardAddTaskAutoResize = function() {};
  `);
  return { context, document };
}

function createWsRenderHarness() {
  const { sandbox, document } = createSandbox();
  sandbox._activePanelApp = '';
  sandbox.renderCalls = {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  };
  document.register('main');
  document.register('standalone-sidebar-shell');
  document.register('standalone-main-stack');
  document.register('standalone-bottom-dock');
  document.register('standalone-right-rail');
  document.register('standalone-float-layer');
  document.register('standalone-bottom-resize-handle');
  document.register('standalone-rail-resize-handle');
  document.register('bottom-panel');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/panel_manager.js');
  runInContext(context, `
    render = function() { renderCalls.main++; };
    renderBoard = function() { renderCalls.board++; };
    renderTemplatesPanel = function() { renderCalls.actions++; };
    renderContextPanel = function() { renderCalls.context++; };
    renderEvents = function() { renderCalls.events++; };
    renderAgentPanel = function() { renderCalls.weaver++; };
    renderAgentTemplatesPanel = function() { renderCalls.templates++; };
    updateEventsAttentionBadge = function() {};
    _expectedSeq = 1;
  `);
  return { context, document, sandbox };
}

function createTemplatesHarness() {
  const { sandbox, document } = createSandbox({
    _cachedProviders: [],
    _populateProviderSelect() {},
    _getProviderValue() { return ''; },
    _getProviderCommand() { return ''; },
    _envToText(value) { return value ? JSON.stringify(value) : ''; },
    _textToEnv() { return {}; },
    _tplAutoResize() {},
    toggleHint() {},
  });
  document.register('panel-templates');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/templates.js');
  return { context, document, sandbox };
}

function createMainHarness(overrides = {}) {
  const taskbarButtons = ['board', 'actions', 'templates', 'context', 'events', 'weaver'].map((app) => {
    const button = new FakeElement();
    button.dataset.app = app;
    return button;
  });
  const { sandbox, document } = createSandbox(Object.assign({
    renderBoard() {},
    tplEditorLoad() {},
    renderEvents() {},
    renderAgentPanel() {},
    agentTemplateEditorLoad() {},
    agentHistoryLoad() {},
    connect() {},
    setupDrag() {},
    closeModals() {},
    closeBroadcast() {},
    closeMenus() {},
    closeContextMenu() {},
    boardKeydown() { return false; },
    _boardSelectedCount() { return 0; },
    boardClearSelection() {},
    onAgentClick() {},
    removeAgent() {},
    quickAddAgent() {},
    openAddGroup() {},
    quickAddTerminal() {},
    openBroadcast() {},
    relaunchAgent() {},
    getFilterByWindow() { return false; },
  }, overrides));
  [
    'standalone-sidebar-shell',
    'standalone-main-stack',
    'standalone-bottom-dock',
    'standalone-right-rail',
    'standalone-float-layer',
    'standalone-bottom-resize-handle',
    'standalone-rail-resize-handle',
    'bottom-panel',
    'panel-resize-handle',
    'panel-board',
    'panel-actions',
    'panel-templates',
    'panel-context',
    'panel-events',
    'panel-agent',
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
    'engineer-name-input',
    'engineer-command-input',
    'gs-directory',
    'gs-agent-directory',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
    'gs-terminal-cmd-args',
    'gs-terminal-init-script',
    'gs-terminal-directory',
    'gs-weaver-boot-cmd',
    'gs-weaver-custom-instructions',
  ].forEach((id) => {
    document.register(id);
  });
  document.selectorMap.set('.overlay.visible', null);
  document.setSelector('.taskbar-app', taskbarButtons[0]);
  document.querySelectorAll = function(selector) {
    if (selector === '.taskbar-app') return taskbarButtons;
    if (selector === '.overlay') return [];
    return [];
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');
  loadScript(context, 'static/js/main.js');
  return { context, document, sandbox, taskbarButtons };
}

function createStandaloneWsSyncHarness() {
  const { context, document, sandbox } = createMainHarness({
    tplEditorLoad() {
      sandbox.tplEditorLoadCalls = (sandbox.tplEditorLoadCalls || 0) + 1;
    },
    agentTemplateEditorLoad() {
      sandbox.agentTemplateEditorLoadCalls = (sandbox.agentTemplateEditorLoadCalls || 0) + 1;
    },
    agentHistoryLoad() {
      sandbox.agentHistoryLoadCalls = (sandbox.agentHistoryLoadCalls || 0) + 1;
    },
  });
  sandbox.renderCalls = {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  };
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/ws.js');
  runInContext(context, `
    render = function() { renderCalls.main++; };
    renderBoard = function() { renderCalls.board++; };
    renderTemplatesPanel = function() { renderCalls.actions++; };
    renderContextPanel = function() { renderCalls.context++; };
    renderEvents = function() { renderCalls.events++; };
    renderAgentPanel = function() { renderCalls.weaver++; };
    renderAgentPanel = function() {};
    renderAgentTemplatesPanel = function() { renderCalls.templates++; };
    updateEventsAttentionBadge = function() {};
    _panelStateRestored = true;
    _expectedSeq = 1;
  `);
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  return { context, document, sandbox };
}

function createAttachedStandaloneWsSyncHarness() {
  const harness = createStandaloneWsSyncHarness();
  attachStandalonePanelDom(harness.document);
  return harness;
}

function createStandaloneRenderHarness() {
  const { sandbox, document } = createSandbox();
  sandbox.renderTerminalWorkspaceCalls = 0;
  sandbox.isEmbeddedTerminalMode = function() { return true; };
  sandbox.renderTerminalWorkspace = function() {
    sandbox.renderTerminalWorkspaceCalls++;
  };
  document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    var PROCESS_MAP = {};
    var focusedItemId = '';
  `);
  return { context, document, sandbox };
}

function createMainRenderHarness() {
  const { sandbox, document } = createSandbox();
  document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    var _cachedAgentTemplates = [];
    var focusedItemId = null;
    var selectedTerminalId = null;
    getFilterByWindow = function() { return false; };
  `);
  return { context, document, sandbox };
}

function createMainGridDragHarness() {
  const { sandbox, document } = createSandbox({
    renderTerminalWorkspace() {},
    updateEventsAttentionBadge() {},
    renderAgentPanel() {},
  });
  document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/commands.js');
  runInContext(context, `
    var _cachedAgentTemplates = [];
    var focusedItemId = null;
    var selectedTerminalId = null;
    var dragInProgress = false;
    getFilterByWindow = function() { return false; };
  `);
  return { context, document, sandbox };
}

function createDragDomElement({
  dragId = '',
  dragType = '',
  dragGroup = '',
  dropType = '',
  dropGroup = '',
  dropParent = '',
  groupName = '',
  classNames = [],
  rect = {},
  parent = null,
} = {}) {
  const element = new FakeElement(dragId || groupName || '');
  classNames.forEach((name) => element.classList.add(name));
  if (dragId) element.dataset.dragId = dragId;
  if (dragType) element.dataset.dragType = dragType;
  if (dragGroup) element.dataset.dragGroup = dragGroup;
  if (dropType) element.dataset.dropType = dropType;
  if (dropGroup) element.dataset.dropGroup = dropGroup;
  if (dropParent) element.dataset.dropParent = dropParent;
  if (groupName) element.dataset.groupName = groupName;
  const bounds = Object.assign({
    top: 20,
    bottom: 80,
    left: 20,
    right: 140,
    width: 120,
    height: 60,
  }, rect);
  element.getBoundingClientRect = () => bounds;
  if (parent) parent.appendChild(element);
  return element;
}

function fireGridDrag(main, dragElement, dropTarget, options = {}) {
  const transfer = {
    effectAllowed: '',
    dropEffect: '',
    data: {},
    setData(type, value) {
      this.data[type] = value;
    },
  };
  main.listeners.dragstart({ target: dragElement, dataTransfer: transfer });
  const dropEvent = {
    target: dropTarget,
    dataTransfer: transfer,
    clientX: typeof options.clientX === 'number' ? options.clientX : 0,
    clientY: typeof options.clientY === 'number' ? options.clientY : 0,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
  main.listeners.drop(dropEvent);
  return { transfer, dropEvent };
}

function applyMoveAgentForDragState(state, id, targetGroup, before = '') {
  const cell = state.agents[id];
  if (!cell || !state.groups[targetGroup]) return;
  if (cell.parent_id && state.children[cell.parent_id]) {
    state.children[cell.parent_id] = state.children[cell.parent_id].filter((childId) => childId !== id);
    cell.parent_id = '';
  }
  if (state.groups[cell.group]) {
    state.groups[cell.group] = state.groups[cell.group].filter((agentId) => agentId !== id);
  }
  const target = state.groups[targetGroup];
  const index = before ? target.indexOf(before) : -1;
  if (index >= 0) target.splice(index, 0, id);
  else target.push(id);
  cell.group = targetGroup;
}

function applyReparentTerminalForDragState(state, id, parentId = '') {
  const cell = state.agents[id];
  if (!cell || cell.cell_type !== 'terminal') return;
  const oldParent = cell.parent_id || '';
  if (oldParent && state.children[oldParent]) {
    state.children[oldParent] = state.children[oldParent].filter((childId) => childId !== id);
  }
  if (!oldParent && state.groups[cell.group]) {
    state.groups[cell.group] = state.groups[cell.group].filter((agentId) => agentId !== id);
  }
  if (parentId) {
    const parent = state.agents[parentId];
    if (!parent) return;
    cell.parent_id = parentId;
    cell.group = parent.group;
    if (!state.children[parentId]) state.children[parentId] = [];
    state.children[parentId].push(id);
  } else {
    cell.parent_id = '';
    if (state.groups[cell.group] && !state.groups[cell.group].includes(id)) {
      state.groups[cell.group].push(id);
    }
  }
}

function createMainNavigationHarness() {
  const { sandbox, document } = createSandbox({
    connect() {},
    setupDrag() {},
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
  });
  document.register('main');
  [
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
    'engineer-name-input',
    'engineer-command-input',
    'gs-directory',
    'gs-agent-directory',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
    'gs-terminal-cmd-args',
    'gs-terminal-init-script',
    'gs-terminal-directory',
    'gs-weaver-boot-cmd',
    'gs-weaver-custom-instructions',
  ].forEach((id) => document.register(id));
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    var _cachedAgentTemplates = [];
    var focusedItemId = null;
    var selectedTerminalId = null;
    getFilterByWindow = function() { return false; };
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
  `);
  loadScript(context, 'static/js/main.js');
  return { context, document, sandbox };
}

function createPanelHarness() {
  const { sandbox, document } = createSandbox({
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
    connect() {},
    setupDrag() {},
  });
  [
    'standalone-sidebar-shell',
    'standalone-main-stack',
    'standalone-bottom-dock',
    'standalone-right-rail',
    'standalone-float-layer',
    'standalone-bottom-resize-handle',
    'standalone-rail-resize-handle',
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
    'engineer-name-input',
    'engineer-command-input',
    'gs-directory',
    'gs-agent-directory',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
    'gs-terminal-cmd-args',
    'gs-terminal-init-script',
    'gs-terminal-directory',
    'gs-weaver-boot-cmd',
    'gs-weaver-custom-instructions',
  ].forEach((id) => document.register(id));
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');
  loadScript(context, 'static/js/main.js');
  return { context, document, sandbox };
}

function createWorkspaceResizeHarness() {
  const { sandbox, document } = createSandbox({
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
    connect() {},
    setupDrag() {},
  });
  [
    'workspace-shell',
    'workspace-resize-handle',
    'standalone-sidebar-shell',
    'standalone-main-stack',
    'standalone-bottom-dock',
    'standalone-right-rail',
    'standalone-float-layer',
    'standalone-bottom-resize-handle',
    'standalone-rail-resize-handle',
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
    'engineer-name-input',
    'engineer-command-input',
    'gs-directory',
    'gs-agent-directory',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
    'gs-terminal-cmd-args',
    'gs-terminal-init-script',
    'gs-terminal-directory',
    'gs-weaver-boot-cmd',
    'gs-weaver-custom-instructions',
  ].forEach((id) => document.register(id));
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');
  loadScript(context, 'static/js/main.js');
  return { context, document, sandbox };
}

function createPanelManagerHarness() {
  const { sandbox, document } = createSandbox({
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
    connect() {},
    setupDrag() {},
  });
  [
    'standalone-sidebar-shell',
    'standalone-main-stack',
    'standalone-bottom-dock',
    'standalone-right-rail',
    'standalone-float-layer',
    'standalone-bottom-resize-handle',
    'standalone-rail-resize-handle',
  ].forEach((id) => document.register(id));
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');
  return { context, document, sandbox };
}

function createModalHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModalScripts(context);
  runInContext(context, `
    _renderTaskAttachments = function() {};
    _renderTaskArtifacts = function() {};
    _renderTaskArtifactEditor = function() {};
    taskAutoResize = function() {};
  `);
  return { context, document, sandbox };
}

function registerEngineerModalDom(document) {
  const modal = document.register('modal-engineer');
  modal.classList.add('overlay');
  const summary = document.register('modal-engineer-summary');
  summary.classList.add('hidden');
  const nameInput = document.register('engineer-name-input');
  const commandInput = document.register('engineer-command-input');
  document.setSelectorAll('.overlay', [modal]);
  document.setSelectorAll('.hint-pop', []);
  return { modal, summary, nameInput, commandInput };
}

function registerArchitectModalDom(document) {
  const modal = document.register('modal-architect');
  modal.classList.add('overlay');
  const summary = document.register('modal-architect-summary');
  summary.classList.add('hidden');
  const nameInput = document.register('architect-name-input');
  const commandInput = document.register('architect-command-input');
  document.setSelectorAll('.overlay', [modal]);
  document.setSelectorAll('.hint-pop', []);
  return { modal, summary, nameInput, commandInput };
}

function registerAddCellModalElements(document) {
  [
    'modal-add',
    'modal-add-title',
    'modal-add-summary',
    'add-submit-btn',
    'add-name-input',
    'add-group-select',
    'add-template-row',
    'add-template-select',
    'add-advanced-details',
    'add-advanced-summary',
    'add-icon-row',
    'add-icon-picker',
    'add-provider-row',
    'add-provider-select',
    'add-cmd-row',
    'add-cmd-input',
    'add-model-row',
    'add-model-input',
    'add-reasoning-row',
    'add-reasoning-effort',
    'add-args-row',
    'add-args-input',
    'add-init-row',
    'add-init-input',
    'add-dir-select',
    'add-dir-input',
    'add-profile-select',
    'add-shell-select',
    'add-color-swatches',
    'add-env-vars',
    'add-wt-section',
    'add-wt-enabled',
    'add-wt-fields',
    'add-wt-base-dir',
    'add-wt-base-branch',
    'add-wt-name',
    'add-wt-auto-checkpoint',
    'add-wt-checkpoint-on-progress',
    'add-wt-squash',
  ].forEach((id) => {
    if (!document.getElementById(id)) document.register(id);
  });
  const modal = document.getElementById('modal-add');
  modal.classList.add('overlay');
  document.setSelectorAll('.overlay', [modal]);
  document.getElementById('add-cmd-row').setQuerySelector(
    'label',
    document.register('add-cmd-row-label'),
  );
  return modal;
}

function createDiffHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModalScripts(context);
  loadScript(context, 'static/js/diff.js');
  document.register('diff-view-root');
  return { context, document };
}

function createDiffKeyHarness() {
  const { sandbox, document } = createSandbox({
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
    connect() {},
    setupDrag() {},
  });
  const confirmOverlay = document.register('modal-confirm');
  confirmOverlay.classList.add('overlay');
  document.register('confirm-message');
  document.register('confirm-extras');
  document.register('confirm-yes-btn');
  document.register('diff-view-root');
  document.setSelectorAll('.overlay', [confirmOverlay]);
  [
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
    'engineer-name-input',
    'engineer-command-input',
    'gs-directory',
    'gs-agent-directory',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
    'gs-terminal-cmd-args',
    'gs-terminal-init-script',
    'gs-terminal-directory',
    'gs-weaver-boot-cmd',
    'gs-weaver-custom-instructions',
  ].forEach((id) => document.register(id));
  const context = vm.createContext(sandbox);
  loadModalScripts(context);
  loadScript(context, 'static/js/diff.js');
  loadScript(context, 'static/js/main.js');
  return { context, document, sandbox, confirmOverlay };
}

function createTaskHistoryHarness(options = {}) {
  const { sandbox, document } = createSandbox({
    window: {
      innerHeight: 900,
      addEventListener() {},
      open() {},
    },
    connect() {},
    setupDrag() {},
  });
  const overlay = document.register('modal-task-history');
  overlay.classList.add('overlay');
  document.register('task-history-root');
  document.setSelectorAll('.overlay', [overlay]);
  const context = vm.createContext(sandbox);
  loadModalScripts(context);
  loadScript(context, 'static/js/taskhistory.js');
  if (options.withMain) {
    [
      'add-name-input',
      'add-cmd-input',
      'add-dir-input',
      'add-args-input',
      'add-init-input',
      'engineer-name-input',
      'engineer-command-input',
      'gs-directory',
      'gs-agent-directory',
      'gs-terminal-prefix',
      'gs-terminal-boot-cmd',
      'gs-terminal-cmd-args',
      'gs-terminal-init-script',
      'gs-terminal-directory',
      'gs-weaver-boot-cmd',
      'gs-weaver-custom-instructions',
    ].forEach((id) => document.register(id));
    loadScript(context, 'static/js/main.js');
  }
  return { context, document, sandbox, overlay };
}

test('board visible tasks combine group, search, label, action, and agent filters', () => {
  const { context } = createBoardHarness();
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Deploy Worker', slug: 'deploy-worker' },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Deploy fix',
      description: 'Urgent ship blocker',
      slug: 'deploy-fix',
      labels: ['bug'],
      action_name: 'triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 3,
    },
    'task-2': {
      id: 'task-2',
      group: 'alpha',
      task: 'Deploy docs',
      description: 'Docs only',
      slug: 'deploy-docs',
      labels: ['docs'],
      action_name: 'triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 2,
    },
    'task-3': {
      id: 'task-3',
      group: 'beta',
      task: 'Deploy fix',
      description: 'Wrong group',
      slug: 'deploy-fix-beta',
      labels: ['bug'],
      action_name: 'triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 1,
    },
    'task-4': {
      id: 'task-4',
      group: 'alpha',
      task: 'Deploy fix',
      description: 'No assigned agent',
      slug: 'deploy-fix-unowned',
      labels: ['bug'],
      action_name: 'triage',
      agent_id: '',
      lane: 'Backlog',
      position: 0,
    },
    'task-5': {
      id: 'task-5',
      group: 'alpha',
      task: 'Deploy fix',
      description: 'Looks healthy',
      slug: 'deploy-fix-healthy',
      labels: ['bug'],
      action_name: 'triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 4,
      health_state: 'healthy',
    },
  };

  runInContext(context, `
    _boardSearchQuery = 'deploy';
    _boardFilterLabels = ['bug'];
    _boardFilterActions = ['triage'];
    _boardFilterAgents = ['agent-1'];
    _boardFilterHealth = ['stalled'];
    state.board_tasks['task-1'].health_state = 'stalled';
  `);

  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks()).sort()'), ['task-1']);
});

test('weaver task health summary prioritizes severe unhealthy tasks', () => {
  const { context } = createWeaverHarness();
  context.state.board_tasks = {
    healthy: {
      id: 'healthy',
      group: 'alpha',
      task: 'Healthy task',
      lane: 'Backlog',
      health_state: 'healthy',
    },
    stalled: {
      id: 'stalled',
      group: 'alpha',
      task: 'Stalled task',
      lane: 'In Progress',
      health_state: 'stalled',
      health_since: '2026-04-06T00:21:00+00:00',
    },
    blocked: {
      id: 'blocked',
      group: 'alpha',
      task: 'Blocked task',
      lane: 'In Progress',
      health_state: 'blocked',
      health_since: '2026-04-06T00:20:00+00:00',
    },
  };

  const summary = jsonValue(context, `_weaverTaskHealthSummary('alpha')`);

  assert.equal(summary.total, 2);
  assert.deepEqual(summary.counts, {
    blocked: 1,
    stalled: 1,
    thrashing: 0,
    'idle-risk': 0,
  });
  assert.deepEqual(summary.items.map((item) => item.id), ['blocked', 'stalled']);
});

test('weaver journal does not render dispatch overlap summaries', () => {
  const { context } = createWeaverHarness();
  context.state.dispatch_overlap_groups = {
    alpha: {
      counts: { notice: 1, warning: 1, conflict: 1 },
      total: 3,
      items: [
        { task_id: 'warn', title: 'Warning task', level: 'warning', summary: 'Same module' },
        { task_id: 'conflict', title: 'Conflict task', level: 'conflict', summary: 'Same branch' },
      ],
    },
  };

  const html = runInContext(context, `_agentPanelLegacyRenderJournal('alpha')`);

  assert.doesNotMatch(html, /Dispatch overlap/);
  assert.doesNotMatch(html, /Same branch/);
});

test('board search matches title, description, labels, action, and linked agent fields', () => {
  const { context } = createBoardHarness();
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alice Reviewer', slug: 'alice-reviewer' },
    'agent-2': { id: 'agent-2', name: 'Bob Builder', slug: 'bob-builder' },
  };
  context.state.board_tasks = {
    titleTask: {
      id: 'titleTask',
      group: 'alpha',
      task: 'Release notes',
      description: 'Prepare the changelog',
      labels: ['docs'],
      action_name: 'feature/docs',
      agent_id: 'agent-2',
      lane: 'Backlog',
      position: 4,
    },
    descriptionTask: {
      id: 'descriptionTask',
      group: 'alpha',
      task: 'Verify auth flow',
      description: 'Check production smoke coverage',
      labels: ['qa'],
      action_name: 'feature/test',
      agent_id: 'agent-2',
      lane: 'Backlog',
      position: 3,
    },
    labelTask: {
      id: 'labelTask',
      group: 'alpha',
      task: 'Update handbook',
      description: 'Internal docs refresh',
      labels: ['compliance'],
      action_name: 'feature/docs',
      agent_id: 'agent-2',
      lane: 'Backlog',
      position: 2,
    },
    actionTask: {
      id: 'actionTask',
      group: 'alpha',
      task: 'Review webhook retry logic',
      description: 'Needs a second pass',
      labels: ['review'],
      action_name: 'feature/review',
      agent_id: 'agent-2',
      lane: 'Backlog',
      position: 1,
    },
    agentTask: {
      id: 'agentTask',
      group: 'alpha',
      task: 'Triage flaky spec',
      description: 'Route to the reviewer',
      labels: ['triage'],
      action_name: 'feature/triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 0,
    },
  };

  runInContext(context, `
    _boardFilterLabels = [];
    _boardFilterActions = [];
    _boardFilterAgents = [];
    _boardSearchQuery = 'release';
  `);
  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks())'), ['titleTask']);

  runInContext(context, `_boardSearchQuery = 'smoke coverage';`);
  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks())'), ['descriptionTask']);

  runInContext(context, `_boardSearchQuery = 'compliance';`);
  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks())'), ['labelTask']);

  runInContext(context, `_boardSearchQuery = 'feature/review';`);
  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks())'), ['actionTask']);

  runInContext(context, `_boardSearchQuery = 'alice reviewer';`);
  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks())'), ['agentTask']);
});

test('actions editor save payload preserves auto_close_on_done', () => {
  const { sandbox, document } = createSandbox({
    _showToast() {},
    _cachedAgentTemplates: [],
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/actions.js');

  document.register('tpled-name').value = 'feature/review';
  document.register('tpled-desc').value = 'Review code';
  document.register('tpled-agent-template').value = '';
  document.register('tpled-agent-prefix').value = '';
  document.register('tpled-agent-color').value = '';
  document.register('tpled-agent-cmd').value = '';
  document.register('tpled-agent-dir').value = '';
  document.register('tpled-group').value = '';
  document.register('tpled-worktree').checked = false;
  document.register('tpled-auto-close-on-done').checked = true;
  document.register('tpled-disable-role-preamble').checked = true;
  document.register('tpled-prompt').value = '{{ TASK }}';
  document.register('tpled-labels').value = 'review';
  document.register('tpled-scope').value = 'project';
  const transitions = document.register('tpled-transitions');
  transitions.setQuerySelectorAll('.tpled-transition-entry', []);

  runInContext(context, `_tplEditorSelected = 'project:feature/review';`);
  runInContext(context, `_tplEditorScope = 'project';`);
  runInContext(context, `_tplEditorSaveInner();`);

  assert.equal(jsonValue(context, 'sendCalls.length'), 1);
  assert.equal(
    jsonValue(context, 'sendCalls[0].action.auto_close_on_done'),
    true,
  );
  assert.equal(
    jsonValue(context, 'sendCalls[0].action.disable_role_preamble'),
    true,
  );
  assert.deepEqual(jsonValue(context, 'sendCalls[0].action.transitions'), []);
});

test('actions panel ensure-loaded fetches once per group until refresh', () => {
  const { sandbox } = createSandbox({
    _cachedAgentTemplates: [],
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/actions.js');

  context.tplEditorEnsureLoaded();
  context.tplEditorEnsureLoaded();
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'list_actions', group: 'alpha' },
  ]);

  context.tplEditorReceiveList({
    type: 'actions',
    group: 'alpha',
    actions: [{ name: 'feature/review' }],
  });
  context.tplEditorEnsureLoaded();

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'list_actions', group: 'alpha' },
  ]);

  context.tplEditorLoad();
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'list_actions', group: 'alpha' },
    { cmd: 'list_actions', group: 'alpha' },
  ]);
});

test('board lane counts ignore subordinate tasks in the same lane', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Root task',
      lane: 'In Progress',
      position: 2,
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Sub task',
      lane: 'In Progress',
      parent_task_id: 'root',
      position: 1,
    },
    orphan: {
      id: 'orphan',
      group: 'alpha',
      task: 'Another root',
      lane: 'Backlog',
      position: 0,
    },
  };

  assert.equal(runInContext(context, `_boardLaneCount('In Progress')`), 1);
  assert.equal(runInContext(context, `_boardLaneCount('Backlog')`), 1);
});

test('renderBoard preserves inline task drafts and restores the saved lane when filters clear', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  const cards = document.register('board-cards');
  cards.scrollTop = 28;
  const draftInput = document.register('board-add-task-input');
  draftInput.value = 'Follow up with release notes';

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    root: { id: 'root', group: 'alpha', task: 'Task', lane: 'Backlog', position: 1 },
  };

  runInContext(context, `
    _boardAddingTask = true;
    _boardSelectedLane = 'Done';
    _boardPreFilterLane = 'Backlog';
    _boardSearchQuery = '';
    _boardFilterLabels = [];
    _boardFilterActions = [];
    _boardFilterAgents = [];
  `);

  context.renderBoard();

  assert.equal(runInContext(context, '_boardAddingTaskDraft'), 'Follow up with release notes');
  assert.equal(runInContext(context, '_boardSelectedLane'), 'Backlog');
  assert.equal(runInContext(context, '_boardPreFilterLane'), '');
  assert.equal(runInContext(context, '_boardCardsScrollTop'), 28);
});

test('board restores scroll state per lane and filter view', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  const cards = document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    backlogA: {
      id: 'backlogA',
      group: 'alpha',
      task: 'Fix bug',
      labels: ['bug'],
      lane: 'Backlog',
      position: 2,
    },
    backlogB: {
      id: 'backlogB',
      group: 'alpha',
      task: 'Ship docs',
      labels: ['docs'],
      lane: 'Backlog',
      position: 1,
    },
    doneTask: {
      id: 'doneTask',
      group: 'alpha',
      task: 'Closed item',
      lane: 'Done',
      position: 0,
    },
  };

  context.renderBoard();
  runInContext(context, `_boardRenderLimit = 150;`);
  cards.scrollTop = 120;
  cards.listeners.scroll();

  context.boardSelectLane('Done');
  assert.equal(cards.scrollTop, 0);
  assert.equal(runInContext(context, '_boardRenderLimit'), 50);

  cards.scrollTop = 34;
  cards.listeners.scroll();

  context.boardSelectLane('Backlog');
  assert.equal(cards.scrollTop, 120);
  assert.equal(runInContext(context, '_boardRenderLimit'), 150);

  context.boardUpdateSearch('bug');
  assert.equal(cards.scrollTop, 0);
  assert.equal(runInContext(context, '_boardRenderLimit'), 50);

  cards.scrollTop = 52;
  cards.listeners.scroll();

  context.boardClearFilters();
  assert.equal(cards.scrollTop, 120);
  assert.equal(runInContext(context, '_boardRenderLimit'), 150);
});

test('board restores the selected lane scroll when toggling schedules', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  const cards = document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    doneTask: {
      id: 'doneTask',
      group: 'alpha',
      task: 'Recently done',
      lane: 'Done',
      position: 1,
    },
  };
  context.state.schedules = {
    sched1: {
      id: 'sched1',
      name: 'Nightly docs',
      enabled: true,
      scheduled_at: '2026-04-07T00:00:00Z',
    },
  };

  runInContext(context, `_boardSelectedLane = 'Done';`);
  context.renderBoard();

  cards.scrollTop = 44;
  cards.listeners.scroll();

  context.boardToggleSchedules();
  assert.equal(runInContext(context, '_boardShowSchedules'), true);
  assert.equal(cards.scrollTop, 0);

  cards.scrollTop = 18;
  cards.listeners.scroll();

  context.boardToggleSchedules();
  assert.equal(runInContext(context, '_boardShowSchedules'), false);
  assert.equal(runInContext(context, '_boardSelectedLane'), 'Done');
  assert.equal(cards.scrollTop, 44);
});

test('renderBoard uses a wide multi-lane layout only for embedded wide panels', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'To Do', 'In Progress', 'Done'];
  context.state.board_tasks = {
    backlog: { id: 'backlog', group: 'alpha', task: 'Backlog task', lane: 'Backlog', position: 4 },
    todo: { id: 'todo', group: 'alpha', task: 'To Do task', lane: 'To Do', position: 3 },
    progress: { id: 'progress', group: 'alpha', task: 'Active task', lane: 'In Progress', position: 2 },
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };
  runInContext(context, `_boardSelectedLane = 'Backlog';`);

  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;
  context.renderBoard();

  assert.match(panel.innerHTML, /board-wide-grid/);
  assert.match(panel.innerHTML, /board-wide-add-task-wrap/);
  assert.equal((panel.innerHTML.match(/\+ Add task/g) || []).length, 1);
  assert.equal(
    panel.innerHTML.indexOf('board-wide-add-task-wrap') < panel.innerHTML.indexOf('board-wide-grid'),
    true,
  );
  assert.equal((panel.innerHTML.match(/data-board-lane-column="1"/g) || []).length, 4);
  assert.match(panel.innerHTML, /board-wide-lane-name">Backlog/);
  assert.match(panel.innerHTML, /board-wide-lane-name">Done/);

  panel.clientWidth = 820;
  context.renderBoard();
  assert.doesNotMatch(panel.innerHTML, /board-wide-grid/);
  assert.doesNotMatch(panel.innerHTML, /board-wide-add-task-wrap/);
  assert.match(panel.innerHTML, /\+ Add task/);

  document.body.classList.remove('runtime-embedded');
  panel.clientWidth = 1200;
  context.renderBoard();
  assert.doesNotMatch(panel.innerHTML, /board-wide-grid/);
});

test('renderBoard places recent, view, and schedules in the top toolbar for wide embedded layout', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'To Do', 'In Progress', 'Done'];
  context.state.board_tasks = {
    backlog: { id: 'backlog', group: 'alpha', task: 'Backlog task', lane: 'Backlog', position: 4 },
    todo: { id: 'todo', group: 'alpha', task: 'To Do task', lane: 'To Do', position: 3 },
    progress: { id: 'progress', group: 'alpha', task: 'Active task', lane: 'In Progress', position: 2 },
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };
  runInContext(context, `_boardSelectedLane = 'Backlog';`);

  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;
  context.renderBoard();

  assert.match(panel.innerHTML, /board-wide-grid/);
  assert.match(panel.innerHTML, /board-search-input-wrap/);
  assert.match(panel.innerHTML, /board-view-menu-wrap/);
  assert.match(panel.innerHTML, /board-schedules-toggle-wrap/);
  assert.match(panel.innerHTML, /onclick="boardApplyQuickView\('recent'\)">Recent/);
  assert.doesNotMatch(panel.innerHTML, /Recently Touched/);
  assert.equal(
    panel.innerHTML.indexOf('board-action-filter-wrap') < panel.innerHTML.indexOf("boardApplyQuickView('recent')"),
    true,
  );
  assert.match(panel.innerHTML, /boardToggleSchedules\(\)">Schedules/);
  assert.equal(
    panel.innerHTML.indexOf('board-view-menu-wrap') < panel.innerHTML.indexOf('board-schedules-toggle-wrap'),
    true,
  );
  assert.equal(
    panel.innerHTML.indexOf("boardApplyQuickView('recent')") < panel.innerHTML.indexOf('board-view-menu-wrap'),
    true,
  );
  assert.doesNotMatch(panel.innerHTML, /board-lane-bar/);
  assert.doesNotMatch(panel.innerHTML, /class="board-lane-tab board-lane-drop-target/);
  assert.doesNotMatch(panel.innerHTML, /id="board-scroll-left"/);
  assert.doesNotMatch(panel.innerHTML, /id="board-scroll-right"/);
  assert.doesNotMatch(panel.innerHTML, /board-lane-tab-schedules/);

  panel.clientWidth = 820;
  context.renderBoard();
  assert.doesNotMatch(panel.innerHTML, /board-wide-grid/);
  assert.match(panel.innerHTML, /board-schedules-toggle-wrap/);
  assert.match(panel.innerHTML, /class="board-lane-tab board-lane-drop-target/);
  assert.match(panel.innerHTML, /id="board-scroll-left"/);
  assert.match(panel.innerHTML, /id="board-scroll-right"/);
  assert.doesNotMatch(panel.innerHTML, /board-lane-tab-schedules/);
});

test('renderBoard restores per-lane body scrollTop across re-renders in wide layout', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    backlog: { id: 'backlog', group: 'alpha', task: 'Backlog task', lane: 'Backlog', position: 2 },
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };
  runInContext(context, `_boardSelectedLane = 'Backlog';`);

  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;

  // Mock per-lane body elements that the production render would create.
  const backlogBody = new FakeElement();
  backlogBody.dataset = { lane: 'Backlog' };
  backlogBody.scrollHeight = 800;
  backlogBody.clientHeight = 200;
  const doneBody = new FakeElement();
  doneBody.dataset = { lane: 'Done' };
  doneBody.scrollHeight = 800;
  doneBody.clientHeight = 200;
  panel.setQuerySelectorAll('.board-wide-lane-body[data-lane]', [backlogBody, doneBody]);

  context.renderBoard();

  // Operator scrolls the Backlog column 130px down.
  backlogBody.scrollTop = 130;
  backlogBody.listeners.scroll();
  assert.equal(
    runInContext(context, `_boardWideLaneScrollTops['Backlog']`),
    130,
  );

  // Re-render: a fresh body element appears (innerHTML rebuild). Per-lane
  // scrollTop must be restored on the new node.
  const backlogBody2 = new FakeElement();
  backlogBody2.dataset = { lane: 'Backlog' };
  backlogBody2.scrollHeight = 800;
  backlogBody2.clientHeight = 200;
  const doneBody2 = new FakeElement();
  doneBody2.dataset = { lane: 'Done' };
  doneBody2.scrollHeight = 800;
  doneBody2.clientHeight = 200;
  panel.setQuerySelectorAll('.board-wide-lane-body[data-lane]', [backlogBody2, doneBody2]);

  context.renderBoard();

  assert.equal(backlogBody2.scrollTop, 130);
  assert.equal(doneBody2.scrollTop, 0);
});

test('wide standalone lane columns keep their direct children from shrinking', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    css,
    /\.runtime-embedded #panel-board \.board-wide-lane-body > \*\s*\{[^}]*flex-shrink:\s*0;/,
  );
});

test('wide standalone board lanes keep card contrast with a lighter panel and visible borders', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    css,
    /\.runtime-embedded #panel-board \.board-wide-lane-body\s*\{[^}]*background:\s*linear-gradient\([^;]*var\(--bg-hover\)[^;]*var\(--bg-surface\)[^;]*;/s,
  );
  assert.match(
    css,
    /\.runtime-embedded #panel-board \.board-wide-lane-body \.board-card\s*\{[^}]*border-color:\s*color-mix\(/,
  );
});

test('wide embedded schedules view keeps the display-row toggle and does not restore lane tabs', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'To Do', 'In Progress', 'Done'];
  context.state.board_tasks = {
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };
  runInContext(context, `_boardSelectedLane = 'Done';`);

  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;
  context.boardToggleSchedules();

  assert.equal(runInContext(context, '_boardShowSchedules'), true);
  assert.match(panel.innerHTML, /board-schedules-toggle-wrap/);
  assert.match(panel.innerHTML, /board-filter-btn active" onclick="boardToggleSchedules\(\)">Schedules/);
  assert.match(panel.innerHTML, /board-view-menu-wrap/);
  assert.match(panel.innerHTML, /onclick="boardApplyQuickView\('recent'\)">Recent/);
  assert.doesNotMatch(panel.innerHTML, /board-lane-bar/);
  assert.doesNotMatch(panel.innerHTML, /class="board-lane-tab board-lane-drop-target/);
  assert.doesNotMatch(panel.innerHTML, /board-lane-tab-schedules/);
  assert.match(panel.innerHTML, /No schedules/);
});

test('board keeps scroll state when changing the selected lane in wide embedded mode', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  const cards = document.register('board-cards');
  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    backlog: { id: 'backlog', group: 'alpha', task: 'Backlog task', lane: 'Backlog', position: 2 },
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };

  runInContext(context, `_boardSelectedLane = 'Backlog';`);
  context.renderBoard();

  runInContext(context, `_boardRenderLimit = 150;`);
  cards.scrollTop = 77;
  cards.listeners.scroll();

  context.boardSelectLane('Done');

  assert.equal(runInContext(context, '_boardSelectedLane'), 'Done');
  assert.equal(cards.scrollTop, 77);
  assert.equal(runInContext(context, '_boardRenderLimit'), 150);
});

test('renderBoard switches to the first lane with matches when filters empty the current lane', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  context.state.board_tasks = {
    backlog: {
      id: 'backlog',
      group: 'alpha',
      task: 'Write docs',
      labels: ['docs'],
      lane: 'Backlog',
      position: 2,
    },
    progress: {
      id: 'progress',
      group: 'alpha',
      task: 'Fix websocket bug',
      labels: ['bug'],
      lane: 'In Progress',
      position: 1,
    },
  };

  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardFilterLabels = ['bug'];
    _boardSearchQuery = '';
    _boardFilterActions = [];
    _boardFilterAgents = [];
  `);

  context.renderBoard();

  assert.equal(runInContext(context, '_boardPreFilterLane'), 'Backlog');
  assert.equal(runInContext(context, '_boardSelectedLane'), 'In Progress');
  assert.equal(runInContext(context, `_boardLaneCount('Backlog')`), 0);
  assert.equal(runInContext(context, `_boardLaneCount('In Progress')`), 1);
});

test('board filters restore per group and persist updates independently', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  document.register('board-cards');

  context.activeGroup = 'alpha';
  runInContext(context, `
    _currentGroup = function() { return activeGroup; };
  `);

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  context.state.board_tasks = {
    alphaBug: {
      id: 'alphaBug',
      group: 'alpha',
      task: 'Deploy fix',
      labels: ['bug'],
      action_name: 'triage',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 2,
    },
    betaDocs: {
      id: 'betaDocs',
      group: 'beta',
      task: 'Write docs',
      labels: ['docs'],
      action_name: 'docs',
      agent_id: 'agent-2',
      lane: 'Backlog',
      position: 1,
    },
  };
  context.state.board_filters_by_group = {
    alpha: {
      search_query: 'deploy',
      quick_view: 'touched',
      filter_labels: ['bug'],
      filter_actions: ['triage'],
      filter_agents: ['agent-1'],
      pre_filter_lane: 'Backlog',
    },
  };

  context.renderBoard();

  assert.equal(runInContext(context, '_boardSearchQuery'), 'deploy');
  assert.equal(runInContext(context, '_boardQuickView'), 'touched');
  assert.deepEqual(jsonValue(context, '_boardFilterLabels'), ['bug']);
  assert.deepEqual(jsonValue(context, '_boardFilterActions'), ['triage']);
  assert.deepEqual(jsonValue(context, '_boardFilterAgents'), ['agent-1']);
  assert.equal(runInContext(context, '_boardPreFilterLane'), 'Backlog');

  context.activeGroup = 'beta';
  context.renderBoard();

  assert.equal(runInContext(context, '_boardSearchQuery'), '');
  assert.equal(runInContext(context, '_boardQuickView'), '');
  assert.deepEqual(jsonValue(context, '_boardFilterLabels'), []);
  assert.deepEqual(jsonValue(context, '_boardFilterActions'), []);
  assert.deepEqual(jsonValue(context, '_boardFilterAgents'), []);
  assert.equal(runInContext(context, '_boardPreFilterLane'), '');

  runInContext(context, `boardToggleLabel('docs');`);
  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.at(-1))), {
    cmd: 'board_set_filters',
    filters_by_group: {
      alpha: {
        search_query: 'deploy',
        quick_view: 'touched',
        filter_labels: ['bug'],
        filter_actions: ['triage'],
        filter_agents: ['agent-1'],
        filter_health: [],
        pre_filter_lane: 'Backlog',
      },
      beta: {
        search_query: '',
        quick_view: '',
        filter_labels: ['docs'],
        filter_actions: [],
        filter_agents: [],
        filter_health: [],
        pre_filter_lane: 'Backlog',
      },
    },
  });

  context.activeGroup = 'alpha';
  context.renderBoard();

  assert.equal(runInContext(context, '_boardSearchQuery'), 'deploy');
  assert.equal(runInContext(context, '_boardQuickView'), 'touched');
  assert.deepEqual(jsonValue(context, '_boardFilterLabels'), ['bug']);
});

test('board saved views persist per group and apply without extra hidden state', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.activeGroup = 'alpha';
  runInContext(context, `
    _currentGroup = function() { return activeGroup; };
  `);

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  context.state.board_saved_views_by_group = {
    alpha: [
      {
        name: 'Review Queue',
        search_query: 'review',
        quick_view: '',
        filter_labels: ['loom:blocked'],
        filter_actions: ['feature/review'],
        filter_agents: [],
        filter_health: [],
      },
    ],
  };
  context.state.board_tasks = {
    alphaTask: {
      id: 'alphaTask',
      group: 'alpha',
      task: 'Review auth flow',
      labels: ['loom:blocked'],
      action_name: 'feature/review',
      lane: 'Backlog',
      position: 2,
    },
    betaTask: {
      id: 'betaTask',
      group: 'beta',
      task: 'Docs follow-up',
      labels: ['docs'],
      action_name: 'feature/docs',
      lane: 'Backlog',
      position: 1,
    },
  };

  context.renderBoard();
  assert.match(panel.innerHTML, /Review Queue/);

  runInContext(context, `
    _boardSearchQuery = 'docs';
    _boardFilterLabels = ['docs'];
    _boardFilterActions = ['feature/docs'];
    _boardFilterAgents = [];
  `);
  context.boardSubmitSaveView('Docs Tasks');

  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.at(-1))), {
    cmd: 'board_set_saved_views',
    saved_views_by_group: {
      alpha: [
        {
          name: 'Review Queue',
          search_query: 'review',
          quick_view: '',
          filter_labels: ['loom:blocked'],
          filter_actions: ['feature/review'],
          filter_agents: [],
          filter_health: [],
        },
        {
          name: 'Docs Tasks',
          search_query: 'docs',
          quick_view: '',
          filter_labels: ['docs'],
          filter_actions: ['feature/docs'],
          filter_agents: [],
          filter_health: [],
        },
      ],
    },
  });

  context.boardApplySavedView('Review Queue');
  assert.equal(runInContext(context, '_boardSearchQuery'), 'review');
  assert.equal(runInContext(context, '_boardQuickView'), '');
  assert.deepEqual(jsonValue(context, '_boardFilterLabels'), ['loom:blocked']);
  assert.deepEqual(jsonValue(context, '_boardFilterActions'), ['feature/review']);
  assert.equal(
    context.sendCalls.at(-1).cmd,
    'board_set_filters',
  );

  context.boardDeleteSavedView('Docs Tasks');
  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.at(-1))), {
    cmd: 'board_set_saved_views',
    saved_views_by_group: {
      alpha: [
        {
          name: 'Review Queue',
          search_query: 'review',
          quick_view: '',
          filter_labels: ['loom:blocked'],
          filter_actions: ['feature/review'],
          filter_agents: [],
          filter_health: [],
        },
      ],
    },
  });

  context.activeGroup = 'beta';
  context.renderBoard();
  assert.equal(runInContext(context, '_boardCurrentGroupSavedViews().length'), 0);
});

test('boardSaveCurrentView opens an inline naming control and boardSubmitSaveView persists it', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    alphaTask: {
      id: 'alphaTask',
      group: 'alpha',
      task: 'Docs follow-up',
      labels: ['docs'],
      lane: 'Backlog',
      position: 1,
    },
  };

  runInContext(context, `
    _boardSearchQuery = 'docs';
    _boardFilterLabels = ['docs'];
  `);
  context.renderBoard();

  context.boardSaveCurrentView();
  assert.equal(runInContext(context, '_boardSavingView'), true);
  assert.match(panel.innerHTML, /board-save-view-input/);

  context.boardSubmitSaveView('Docs View');
  assert.equal(runInContext(context, '_boardSavingView'), false);
  assert.match(panel.innerHTML, /Docs View/);
});

test('board quick views keep recent visible while preserving touched-mode filtering', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.activeGroup = 'alpha';
  runInContext(context, `
    _currentGroup = function() { return activeGroup; };
  `);

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    recentNew: {
      id: 'recentNew',
      group: 'alpha',
      task: 'Investigate auth issue',
      lane: 'Backlog',
      position: 2,
      created_at: '2026-04-04T00:00:00Z',
      updated_at: '2026-04-01T00:00:00Z',
    },
    docsRecent: {
      id: 'docsRecent',
      group: 'alpha',
      task: 'Docs follow-up',
      lane: 'Backlog',
      position: 1,
      created_at: '2026-04-03T00:00:00Z',
      updated_at: '2026-04-04T00:00:00Z',
    },
    touchedDone: {
      id: 'touchedDone',
      group: 'alpha',
      task: 'Close review thread',
      lane: 'Done',
      position: 0,
      created_at: '2026-04-01T00:00:00Z',
      updated_at: '2026-04-05T00:00:00Z',
    },
  };

  context.renderBoard();
  assert.match(panel.innerHTML, /Recent/);
  assert.doesNotMatch(panel.innerHTML, /Recently Touched/);

  context.boardApplyQuickView('recent');
  assert.equal(runInContext(context, '_boardQuickView'), 'recent');
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.sendCalls.at(-1))),
    {
      cmd: 'board_set_filters',
      filters_by_group: {
        alpha: {
          search_query: '',
          quick_view: 'recent',
          filter_labels: [],
          filter_actions: [],
          filter_agents: [],
          filter_health: [],
          pre_filter_lane: 'Backlog',
        },
      },
    },
  );
  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['recentNew', 'docsRecent'],
  );

  context.boardApplyQuickView('touched');
  assert.equal(runInContext(context, '_boardQuickView'), 'touched');
  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['docsRecent', 'recentNew'],
  );
  assert.doesNotMatch(panel.innerHTML, /Recently Touched/);

  runInContext(context, `_boardSearchQuery = 'docs';`);
  assert.deepEqual(
    jsonValue(context, 'Object.keys(_boardVisibleTasks())'),
    ['docsRecent'],
  );
});

test('board lane sort modes persist per group and render stable visible ordering', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.activeGroup = 'alpha';
  runInContext(context, `
    _currentGroup = function() { return activeGroup; };
  `);

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_lane_sorts_by_group = {
    alpha: {
      Backlog: 'oldest',
    },
  };
  context.state.board_tasks = {
    manualTop: {
      id: 'manualTop',
      group: 'alpha',
      task: 'Manual top',
      lane: 'Backlog',
      position: 4,
      created_at: '2026-04-02T00:00:00Z',
    },
    newest: {
      id: 'newest',
      group: 'alpha',
      task: 'Newest item',
      lane: 'Backlog',
      position: 3,
      created_at: '2026-04-04T00:00:00Z',
      scheduled_at: '2026-04-10T00:00:00Z',
    },
    dueSoon: {
      id: 'dueSoon',
      group: 'alpha',
      task: 'Due first',
      lane: 'Backlog',
      position: 2,
      created_at: '2026-04-03T00:00:00Z',
      scheduled_at: '2026-04-07T00:00:00Z',
    },
    oldest: {
      id: 'oldest',
      group: 'alpha',
      task: 'Oldest item',
      lane: 'Backlog',
      position: 1,
      created_at: '2026-04-01T00:00:00Z',
    },
  };

  context.renderBoard();

  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['oldest', 'manualTop', 'dueSoon', 'newest'],
  );
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="oldest" selected>Oldest<\/option>/);

  context.activeGroup = 'beta';
  context.renderBoard();
  assert.equal(runInContext(context, `_boardLaneSortMode('Backlog')`), 'manual');
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="manual" selected>Manual<\/option>/);

  context.activeGroup = 'alpha';
  context.renderBoard();

  context.boardSetLaneSort('manual');
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.sendCalls.at(-1))),
    {
      cmd: 'board_set_lane_sorts',
      lane_sorts_by_group: {},
    },
  );
  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['manualTop', 'newest', 'dueSoon', 'oldest'],
  );

  context.boardSetLaneSort('newest');
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.sendCalls.at(-1))),
    {
      cmd: 'board_set_lane_sorts',
      lane_sorts_by_group: {
        alpha: {
          Backlog: 'newest',
        },
      },
    },
  );
  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['newest', 'dueSoon', 'manualTop', 'oldest'],
  );

  context.boardSetLaneSort('due');
  assert.deepEqual(
    jsonValue(context, `_boardTasksInLane('Backlog').map(function(t) { return t.id; })`),
    ['dueSoon', 'newest', 'manualTop', 'oldest'],
  );
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="due" selected>Due Soonest<\/option>/);
});

test('boardCardDrop maps manual drag ordering to the expected server position', () => {
  const { context } = createBoardHarness();
  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    top: { id: 'top', group: 'alpha', task: 'Top', lane: 'Backlog', position: 4 },
    upper: { id: 'upper', group: 'alpha', task: 'Upper', lane: 'Backlog', position: 3 },
    lower: { id: 'lower', group: 'alpha', task: 'Lower', lane: 'Backlog', position: 2 },
    bottom: { id: 'bottom', group: 'alpha', task: 'Bottom', lane: 'Backlog', position: 1 },
  };
  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardDragId = 'top';
  `);

  const card = new FakeElement();
  card.dataset.taskId = 'lower';
  card.classList.add('board-card');
  card.getBoundingClientRect = () => ({ top: 0, height: 100 });

  context.boardCardDrop({
    preventDefault() {},
    clientY: 80,
    target: card,
  });

  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.at(-1))), {
    cmd: 'board_reorder_task',
    id: 'top',
    position: 0,
  });
});

test('boardCardDrop moves tasks across lanes in the wide embedded layout', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board').clientWidth = 1200;
  document.body.classList.add('runtime-embedded');
  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    back: { id: 'back', group: 'alpha', task: 'Backlog task', lane: 'Backlog', position: 4 },
    done: { id: 'done', group: 'alpha', task: 'Done task', lane: 'Done', position: 1 },
  };
  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardDragId = 'back';
  `);

  const lane = new FakeElement();
  lane.classList.add('board-wide-lane');
  lane.dataset.lane = 'Done';

  const card = new FakeElement();
  card.dataset.taskId = 'done';
  card.classList.add('board-card');
  card.parentNode = lane;
  card.getBoundingClientRect = () => ({ top: 0, height: 100 });

  context.boardCardDrop({
    preventDefault() {},
    stopPropagation() {},
    clientY: 20,
    target: card,
  });

  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.at(-1))), {
    cmd: 'board_move_task',
    id: 'back',
    lane: 'Done',
    position: 1,
  });
});

test('board card density persists per group and keeps key signals rendered', () => {
  const { context, document } = createBoardHarness({ stubCards: false });
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.activeGroup = 'alpha';
  runInContext(context, `
    _currentGroup = function() { return activeGroup; };
  `);

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_card_density_by_group = {
    alpha: 'compact',
  };
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alice Reviewer', slug: 'alice-reviewer' },
  };
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Deploy fix',
      labels: ['bug'],
      action_name: 'feature/review',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 1,
      messages: [{ action: 'progress', message: 'Waiting on review' }],
    },
  };

  context.renderBoard();

  assert.match(panel.innerHTML, /board-density-compact/);
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="compact" selected>Compact<\/option>/);
  assert.match(panel.innerHTML, /board-card-meta/);
  assert.match(panel.innerHTML, /board-card-agent/);

  context.activeGroup = 'beta';
  context.renderBoard();
  assert.match(panel.innerHTML, /board-density-normal/);
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="normal" selected>Normal<\/option>/);

  context.activeGroup = 'alpha';
  context.renderBoard();
  context.boardSetCardDensity('detailed');

  assert.deepEqual(
    JSON.parse(JSON.stringify(context.sendCalls.at(-1))),
    {
      cmd: 'board_set_card_density',
      card_density_by_group: {
        alpha: 'detailed',
      },
    },
  );
  assert.match(panel.innerHTML, /board-density-detailed/);
  assert.match(runInContext(context, `_renderBoardDisplayControls()`), /<option value="detailed" selected>Detailed<\/option>/);
});

test('renderBoard relies on the filter toolbar and lane tabs instead of a duplicate lane header', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alice Reviewer', slug: 'alice-reviewer' },
  };
  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Deploy fix',
      labels: ['bug'],
      action_name: 'feature/review',
      agent_id: 'agent-1',
      lane: 'Backlog',
      position: 2,
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Deploy follow-up',
      labels: ['bug'],
      action_name: 'feature/review',
      agent_id: 'agent-1',
      parent_task_id: 'root',
      pipeline_depth: 1,
      lane: 'Backlog',
      position: 1,
    },
  };

  runInContext(context, `
    _boardSearchQuery = 'deploy';
    _boardFilterLabels = ['bug'];
    _boardFilterActions = ['feature/review'];
    _boardFilterAgents = ['agent-1'];
  `);

  context.renderBoard();

  assert.doesNotMatch(panel.innerHTML, /board-lane-header/);
  assert.match(panel.innerHTML, /Backlog/);
  assert.match(panel.innerHTML, /value="deploy"/);
  assert.match(panel.innerHTML, /bug &times;/);
  assert.match(panel.innerHTML, /feature\/review &times;/);
  assert.match(panel.innerHTML, /Alice Reviewer &times;/);
  assert.match(panel.innerHTML, /boardClearFilters\(\)">Clear/);
  assert.match(panel.innerHTML, /Save View/);
  assert.match(panel.innerHTML, /board-view-menu-wrap/);
  assert.match(panel.innerHTML, /View &#9662;/);
  assert.equal(panel.innerHTML.includes('Import external'), false);
  assert.equal(
    panel.innerHTML.indexOf('board-view-menu-wrap') < panel.innerHTML.indexOf('board-lane-bar'),
    true,
  );
});

test('_renderBoardCard hides redundant group chips and only shows execution badges on derived cards', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Research shared context bus',
      lane: 'Backlog',
      position: 2,
      status: 'Implementing',
      health_state: 'stalled',
      action_name: 'feature/research',
      labels: ['ready', 'memory'],
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Implement shared context memory v1',
      lane: 'In Progress',
      position: 1,
      parent_task_id: 'root',
      pipeline_depth: 1,
      status: 'Implementing',
      health_state: 'stalled',
    },
  };

  const html = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.root,
      { root: [state.board_tasks.child] },
      0
    )
  `);

  assert.equal((html.match(/board-card-group/g) || []).length, 0);
  assert.equal((html.match(/board-card-label board-card-status/g) || []).length, 1);
  assert.equal((html.match(/board-card-health-stalled/g) || []).length, 1);
});

test('_renderBoardCard caps subordinate indentation at depth 3', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Root task',
      lane: 'Backlog',
      position: 5,
    },
    child1: {
      id: 'child1',
      group: 'alpha',
      task: 'Depth 1 child',
      lane: 'Backlog',
      position: 4,
      parent_task_id: 'root',
      pipeline_depth: 1,
    },
    child2: {
      id: 'child2',
      group: 'alpha',
      task: 'Depth 2 child',
      lane: 'Backlog',
      position: 3,
      parent_task_id: 'child1',
      pipeline_depth: 2,
    },
    child3: {
      id: 'child3',
      group: 'alpha',
      task: 'Depth 3 child',
      lane: 'Backlog',
      position: 2,
      parent_task_id: 'child2',
      pipeline_depth: 3,
    },
    child4: {
      id: 'child4',
      group: 'alpha',
      task: 'Depth 4 child',
      lane: 'Backlog',
      position: 1,
      parent_task_id: 'child3',
      pipeline_depth: 4,
    },
  };

  const html = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.root,
      {
        root: [state.board_tasks.child1],
        child1: [state.board_tasks.child2],
        child2: [state.board_tasks.child3],
        child3: [state.board_tasks.child4],
      },
      0
    )
  `);

  assert.match(html, /data-task-id="child1"[^>]*data-indent-level="1"[^>]*--board-card-indent-level:1/);
  assert.match(html, /data-task-id="child2"[^>]*data-indent-level="2"[^>]*--board-card-indent-level:2/);
  assert.match(html, /data-task-id="child3"[^>]*data-indent-level="3"[^>]*--board-card-indent-level:3/);
  assert.match(html, /data-task-id="child4"[^>]*data-indent-level="3"[^>]*--board-card-indent-level:3/);
  assert.equal((html.match(/board-card-status-bar/g) || []).length, 5);
});

test('_boardLaneEntryText and refresh delay track the current lane transition age', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryText(
        {
          lane: 'In Progress',
          created_at: '2026-04-07T11:00:00Z',
          lane_entered_at: '2026-04-07T12:00:00Z'
        },
        Date.parse('2026-04-07T12:05:00Z')
      )`,
    ),
    'moved to In Progress · 5m ago',
  );
  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryText(
        {
          lane: 'In Progress',
          created_at: '2026-04-07T11:00:00Z',
          lane_entered_at: '2026-04-07T12:00:00Z'
        },
        Date.parse('2026-04-07T12:06:00Z')
      )`,
    ),
    'moved to In Progress · 6m ago',
  );
  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryText(
        {
          lane: 'Backlog',
          created_at: '2026-04-07T12:00:00Z',
          lane_entered_at: '2026-04-07T12:00:00Z'
        },
        Date.parse('2026-04-07T13:00:00Z')
      )`,
    ),
    'created · 1h ago',
  );
  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryNextRefreshDelay(
        { lane_entered_at: '2026-04-07T12:00:00Z' },
        Date.parse('2026-04-07T12:05:59Z')
      )`,
    ),
    1000,
  );
});

test('_renderBoardCard shows the task status bar above the card title', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  context.Date.now = () => Date.parse('2026-04-07T12:05:00Z');
  loadBoardScripts(context);

  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Keep the board readable',
      lane: 'In Progress',
      created_at: '2026-04-07T11:00:00Z',
      lane_entered_at: '2026-04-07T12:00:00Z',
      position: 1,
    },
  };

  const html = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.root,
      {},
      0
    )
  `);

  const statusIndex = html.indexOf('board-card-status-bar');
  const titleIndex = html.indexOf('board-card-title');
  assert.ok(statusIndex >= 0, 'status bar should render');
  assert.ok(titleIndex > statusIndex, 'status bar should render above the title');
  assert.match(html, /board-card-heading/);
  assert.match(html, /board-card-id/);
  assert.match(html, /root/);
  assert.match(html, /board-card-last-transition/);
  assert.match(html, /moved to In Progress · 5m ago/);
  assert.doesNotMatch(html, /board-card-heading-meta/);
});

test('board cards render created_by attribution badges and update across rerenders', () => {
  const { context, document } = createBoardHarness({ stubCards: false });
  const panel = document.register('panel-board');
  const cards = document.register('board-cards');
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
    },
  };
  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    userTask: {
      id: 'userTask',
      group: 'alpha',
      task: 'User filed task',
      lane: 'Backlog',
      position: 4,
      created_by: 'user',
    },
    architectTask: {
      id: 'architectTask',
      group: 'alpha',
      task: 'Architect filed task',
      lane: 'Backlog',
      position: 3,
      created_by: 'architect:arch-1',
    },
    engineerTask: {
      id: 'engineerTask',
      group: 'alpha',
      task: 'Engineer filed task',
      lane: 'Backlog',
      position: 2,
      created_by: 'engineer:eng-1',
    },
    systemTask: {
      id: 'systemTask',
      group: 'alpha',
      task: 'System fork',
      lane: 'Backlog',
      position: 1,
      created_by: 'system',
    },
  };

  context.renderBoard();

  assert.match(panel.innerHTML, /board-card-created-by-user/);
  assert.match(panel.innerHTML, /data-created-by="user"/);
  assert.match(panel.innerHTML, /board-card-created-by-architect/);
  assert.match(panel.innerHTML, /data-created-by="architect:arch-1"/);
  assert.match(panel.innerHTML, /Planner/);
  assert.match(panel.innerHTML, /board-card-created-by-engineer/);
  assert.match(panel.innerHTML, /data-created-by="engineer:eng-1"/);
  assert.match(panel.innerHTML, /Builder/);
  assert.match(panel.innerHTML, /board-card-created-by-system/);
  assert.match(panel.innerHTML, /data-created-by="system"/);

  cards.scrollTop = 72;
  context.state.board_tasks.userTask.created_by = 'engineer:eng-1';
  context.renderBoard();

  assert.match(panel.innerHTML, /User filed task[\s\S]*data-created-by="engineer:eng-1"/);
  assert.equal(cards.scrollTop, 72);
});

test('_boardTaskScheduleMeta distinguishes scheduled, due-soon, and overdue states', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  context.Date.now = () => Date.parse('2026-04-07T12:00:00Z');
  loadBoardScripts(context);
  context._schedFormatTime = (iso) => ({
    '2026-04-10T09:30:00Z': 'Apr 10 09:30',
    '2026-04-08T08:00:00Z': 'Apr 8 08:00',
    '2026-04-07T09:00:00Z': 'Apr 7 09:00',
  }[iso] || iso);

  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskScheduleMeta({
        lane: 'In Progress',
        scheduled_at: '2026-04-10T09:30:00Z'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-scheduled',
      label: 'Scheduled Apr 10 09:30',
    }),
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskScheduleMeta({
        lane: 'In Progress',
        scheduled_at: '2026-04-08T08:00:00Z'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-scheduled board-card-due-soon',
      label: 'Due Apr 8 08:00',
    }),
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskScheduleMeta({
        lane: 'In Progress',
        scheduled_at: '2026-04-07T09:00:00Z'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-scheduled board-card-overdue',
      label: 'Overdue Apr 7 09:00',
    }),
  );
});

test('_renderBoardCard shows overdue and due-soon chips with distinct classes', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  context.Date.now = () => Date.parse('2026-04-07T12:00:00Z');
  loadBoardScripts(context);
  context._schedFormatTime = (iso) => ({
    '2026-04-08T08:00:00Z': 'Apr 8 08:00',
    '2026-04-07T09:00:00Z': 'Apr 7 09:00',
  }[iso] || iso);

  const overdueHtml = runInContext(context, `
    _renderBoardCard({
      id: 'overdue',
      group: 'alpha',
      task: 'Follow up on rollout',
      lane: 'In Progress',
      scheduled_at: '2026-04-07T09:00:00Z'
    }, {}, 0)
  `);
  const dueSoonHtml = runInContext(context, `
    _renderBoardCard({
      id: 'soon',
      group: 'alpha',
      task: 'Draft notes for handoff',
      lane: 'In Progress',
      scheduled_at: '2026-04-08T08:00:00Z'
    }, {}, 0)
  `);

  assert.match(overdueHtml, /board-card-overdue/);
  assert.match(overdueHtml, /Overdue Apr 7 09:00/);
  assert.match(dueSoonHtml, /board-card-due-soon/);
  assert.match(dueSoonHtml, /Due Apr 8 08:00/);
});

test('_boardTaskDispatchEligibility only surfaces non-default dispatch states on cards', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  context.Date.now = () => Date.parse('2026-04-07T12:00:00Z');
  loadBoardScripts(context);
  context._schedFormatTime = (iso) => ({
    '2026-04-08T12:00:00Z': 'Apr 8 12:00',
  }[iso] || iso);

  context.state.board_tasks = {
    dep: { id: 'dep', task: 'Dependency', lane: 'To Do', group: 'alpha' },
  };
  runInContext(context, `
    _boardEligibilityActionsByGroup.alpha = [{ name: 'feature/impl' }];
    _boardEligibilityTemplatesByGroup.alpha = [{ name: 'worker' }];
  `);

  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDispatchEligibility({
        id: 'ready',
        lane: 'Backlog',
        group: 'alpha',
        depends_on: [],
        labels: []
      }))`,
    ),
    'null',
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDispatchEligibility({
        id: 'blocked',
        lane: 'Backlog',
        group: 'alpha',
        depends_on: ['dep'],
        labels: []
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-dispatch board-card-dispatch-blocked',
      label: 'Blocked by deps',
      title: 'Waiting on: Dependency',
    }),
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDispatchEligibility({
        id: 'scheduled',
        lane: 'Backlog',
        group: 'alpha',
        depends_on: [],
        labels: [],
        scheduled_at: '2026-04-08T12:00:00Z'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-dispatch board-card-dispatch-scheduled',
      label: 'Scheduled later',
      title: 'Dispatch window opens Apr 8 12:00',
    }),
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDispatchEligibility({
        id: 'missing',
        lane: 'Backlog',
        group: 'alpha',
        depends_on: [],
        labels: [],
        action_name: 'missing/action',
        agent_template: 'missing-template'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-dispatch board-card-dispatch-warning',
      label: 'Missing refs',
      title: 'Missing action "missing/action" and role "missing-template"',
    }),
  );
  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDispatchEligibility({
        id: 'queued',
        lane: 'To Do',
        group: 'alpha',
        agent_id: 'agent-1'
      }))`,
    ),
    JSON.stringify({
      className: 'board-card-dispatch board-card-dispatch-queued',
      label: 'Queued',
      title: 'Already queued for dispatch',
    }),
  );
});

test('renderBoard requests action and template refs for dispatch eligibility badges', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  context.state.board_lanes = ['Backlog'];
  context.state.groups = { alpha: [] };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Backlog',
      action_name: 'feature/impl',
      agent_template: 'worker',
    },
  };

  runInContext(context, `
    _activePanelApp = 'board';
    _boardSelectedLane = 'Backlog';
  `);

  context.renderBoard();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'list_actions', group: 'alpha' },
    { cmd: 'list_roles', group: 'alpha' },
  ]);
});

test('ws ignores unsolicited action lists instead of reopening task-from-action modal', () => {
  const { sandbox, document } = createSandbox({
    WebSocket: function FakeWebSocket() {
      this.readyState = 1;
      this.close = function() {};
    },
  });
  sandbox._showActionListCalls = 0;
  const actionModal = document.register('modal-action');
  document.register('conn-dot');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  runInContext(context, `
    _showActionList = function() { _showActionListCalls++; };
    connect();
  `);

  runInContext(context, `
    ws.onmessage({ data: JSON.stringify({
      seq: 1,
      type: 'actions',
      actions: [{ name: 'feature/implement' }]
    }) });
  `);

  assert.equal(jsonValue(context, '_showActionListCalls'), 0);
  assert.equal(actionModal.classList.contains('visible'), false);
});

test('sequence gaps trigger only one resync until a full state arrives', () => {
  const { context } = createWsRenderHarness();

  runInContext(context, `
    WebSocket = { OPEN: 1 };
    ws = {
      readyState: 1,
      send(payload) { sendCalls.push(JSON.parse(payload)); }
    };
    _expectedSeq = 2;
  `);

  context._handleDelta({ seq: 7, ops: [] });
  context._handleDelta({ seq: 8, ops: [] });

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'resync' },
  ]);
  assert.equal(jsonValue(context, '_resyncPending'), true);
  assert.equal(jsonValue(context, '_awaitingFullState'), true);

  runInContext(context, `
    _handleFullState({
      seq: 9,
      groups: {},
      agents: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: [],
    });
  `);

  assert.equal(jsonValue(context, '_resyncPending'), false);
  assert.equal(jsonValue(context, '_awaitingFullState'), false);

  context._handleDelta({ seq: 12, ops: [] });

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'resync' },
    { cmd: 'resync' },
  ]);
});

test('ws close clears pending resync guards', () => {
  const { sandbox, document } = createSandbox({
    WebSocket: function FakeWebSocket() {
      this.readyState = 1;
      this.close = function() {};
    },
  });
  document.register('conn-dot');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');

  runInContext(context, `
    _weaverResetSessionMapMetaCalls = [];
    _weaverResetSessionMapMeta = function(options) {
      _weaverResetSessionMapMetaCalls.push(options || {});
    };
    connect();
    _resyncPending = true;
    _awaitingFullState = true;
    ws.onclose();
  `);

  assert.equal(jsonValue(context, '_resyncPending'), false);
  assert.equal(jsonValue(context, '_awaitingFullState'), false);
  assert.deepEqual(jsonValue(context, '_weaverResetSessionMapMetaCalls'), [
    { clearStale: false },
  ]);
});

test('_renderBoardCard omits the default dispatch badge while keeping warning states', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);
  runInContext(context, `
    _boardEligibilityActionsByGroup.alpha = [{ name: 'feature/impl' }];
    _boardEligibilityTemplatesByGroup.alpha = [{ name: 'worker' }];
  `);

  const readyHtml = runInContext(context, `
    _renderBoardCard({
      id: 'ready',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Backlog',
      labels: []
    }, {}, 0)
  `);
  const missingHtml = runInContext(context, `
    _renderBoardCard({
      id: 'missing',
      group: 'alpha',
      task: 'Backfill docs',
      lane: 'Backlog',
      labels: [],
      action_name: 'missing/action',
      agent_template: 'missing-template'
    }, {}, 0)
  `);

  assert.doesNotMatch(readyHtml, /board-card-dispatch-/);
  assert.doesNotMatch(readyHtml, /Ready to dispatch/);
  assert.match(missingHtml, /board-card-dispatch-warning/);
  assert.match(missingHtml, />Missing refs</);
});

test('_boardTaskDependencyBadges exposes blocked and blocking counts compactly', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_tasks = {
    depA: { id: 'depA', task: 'API contract', lane: 'To Do', group: 'alpha' },
    depB: { id: 'depB', task: 'Schema migration', lane: 'Done', group: 'alpha' },
    blocked1: {
      id: 'blocked1',
      task: 'Rollout docs',
      lane: 'Backlog',
      group: 'alpha',
      depends_on: ['task-1'],
    },
    blocked2: {
      id: 'blocked2',
      task: 'Release notes',
      lane: 'In Progress',
      group: 'alpha',
      depends_on: ['task-1'],
    },
  };

  assert.equal(
    runInContext(
      context,
      `JSON.stringify(_boardTaskDependencyBadges({
        id: 'task-1',
        task: 'Ship release',
        lane: 'Backlog',
        group: 'alpha',
        depends_on: ['depA', 'depB']
      }))`,
    ),
    JSON.stringify([
      {
        className: 'board-card-dependency board-card-dependency-blocked',
        label: 'Blocked by 1',
        title: 'Waiting on: API contract',
        targetTaskId: 'depA',
      },
      {
        className: 'board-card-dependency board-card-dependency-blocking',
        label: 'Blocks 2',
        title: 'Blocking: Rollout docs, Release notes',
        targetTaskId: 'blocked1',
      },
    ]),
  );
});

test('_renderBoardCard shows inline dependency badges instead of the old generic deps pill', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_tasks = {
    dep: { id: 'dep', task: 'API contract', lane: 'To Do', group: 'alpha' },
    blocked: {
      id: 'blocked',
      task: 'Rollout docs',
      lane: 'Backlog',
      group: 'alpha',
      depends_on: ['task-1'],
    },
  };

  const html = runInContext(context, `
    _renderBoardCard({
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Backlog',
      depends_on: ['dep'],
      labels: []
    }, {}, 0)
  `);

  assert.match(html, /board-card-dependency-blocked/);
  assert.match(html, />Blocked by 1</);
  assert.match(html, /board-card-badge-jump/);
  assert.match(html, /boardJumpToTask\('dep'\)/);
  assert.match(html, /board-card-dependency-blocking/);
  assert.match(html, />Blocks 1</);
  assert.match(html, /boardJumpToTask\('blocked'\)/);
  assert.doesNotMatch(html, /&#x1F512; deps/);
});

test('boardCardMenu uses a dependency picker instead of inline blocker rows', () => {
  const { sandbox, document } = createSandbox();
  sandbox.window.innerWidth = 1200;
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  context.state.board_tasks = {
    depA: { id: 'depA', task: 'API contract', lane: 'To Do', group: 'alpha' },
    depB: { id: 'depB', task: 'Schema migration', lane: 'Done', group: 'alpha' },
    blocked1: {
      id: 'blocked1',
      task: 'Rollout docs',
      lane: 'Backlog',
      group: 'alpha',
      depends_on: ['task-1'],
    },
    'task-1': {
      id: 'task-1',
      task: 'Ship release',
      lane: 'Backlog',
      group: 'alpha',
      depends_on: ['depA', 'depB'],
    },
  };

  const menu = document.register('ctx-menu');
  context.boardCardMenu({
    preventDefault() {},
    clientX: 64,
    clientY: 32,
  }, 'task-1');

  assert.equal(menu.classList.contains('open'), true);
  assert.match(menu.innerHTML, /Jump to dependency\.\.\./);
  assert.doesNotMatch(menu.innerHTML, /Jump to blocker:/);
  assert.match(menu.innerHTML, /Jump to dependent: Rollout docs/);
  assert.match(menu.innerHTML, /boardJumpToTask\('blocked1'\)/);

  context.boardShowDependencyPicker('task-1');

  assert.match(menu.innerHTML, /◂ Back/);
  assert.match(menu.innerHTML, /Jump to dependency/);
  assert.match(menu.innerHTML, /API contract/);
  assert.match(menu.innerHTML, /Schema migration/);
  assert.match(menu.innerHTML, /boardJumpToTask\('depA'\)/);
  assert.match(menu.innerHTML, /boardJumpToTask\('depB'\)/);
  assert.match(menu.innerHTML, /ctx-button-wrap/);
});

test('boardCardMenu offers mark verified only for completed tasks awaiting verification', () => {
  const { context, document } = createBoardHarness();
  const menu = document.register('ctx-menu');
  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    pendingDone: {
      id: 'pendingDone',
      task: 'Ship release',
      lane: 'Done',
      group: 'alpha',
      verification_state: 'pending',
      verification_summary: {
        human_validation_pending: 'Confirm production login',
      },
    },
    pendingOpen: {
      id: 'pendingOpen',
      task: 'Deploy release',
      lane: 'In Progress',
      group: 'alpha',
      verification_state: 'pending',
    },
    passedDone: {
      id: 'passedDone',
      task: 'Verified release',
      lane: 'Done',
      group: 'alpha',
      verification_state: 'passed',
    },
  };

  context.boardCardMenu({
    preventDefault() {},
    clientX: 64,
    clientY: 32,
  }, 'pendingDone');
  assert.match(menu.innerHTML, /Mark verified/);

  context.boardCardMenu({
    preventDefault() {},
    clientX: 64,
    clientY: 32,
  }, 'pendingOpen');
  assert.doesNotMatch(menu.innerHTML, /Mark verified/);

  context.boardCardMenu({
    preventDefault() {},
    clientX: 64,
    clientY: 32,
  }, 'passedDone');
  assert.doesNotMatch(menu.innerHTML, /Mark verified/);
});

test('boardMarkTaskVerified uses the verification update flow', () => {
  const { context, document } = createBoardHarness();
  document.register('ctx-menu');
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Ship release',
      lane: 'Done',
      group: 'alpha',
      verification_state: 'pending',
      verification_summary: {
        human_validation_pending: 'Confirm production login',
      },
    },
  };

  context.boardMarkTaskVerified('task-1');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_verify_task',
    id: 'task-1',
    actor_name: 'Operator',
    verification_state: 'passed',
    manual_smoke_done: true,
    human_validation_pending: '',
    deploy_needed: false,
  }]);
});

test('renderBoardCard shows verification badges and preview text', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Deploy billing changes',
      lane: 'In Progress',
      position: 1,
      verification_mode: 'deploy',
      verification_state: 'failed',
      verification_notes: 'Smoke failed on login redirect',
      verification_summary: {
        tests_run: 'python3 -m unittest',
        human_validation_pending: 'Confirm billing dashboard loads',
      },
    },
  };

  const html = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.root,
      {},
      0
    )
  `);

  assert.match(html, /board-card-verification-failed/);
  assert.match(html, /Verify failed/);
  assert.match(html, /board-card-verification-mode/);
  assert.match(html, /Needs human validation: Confirm billing dashboard loads/);
});

test('renderBoardCard shows branch boundary review notes', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);

  context.state.board_tasks = {
    boundary: {
      id: 'boundary',
      group: 'alpha',
      task: 'Stable review point',
      lane: 'Done',
      position: 1,
      worktree_boundary: {
        repo_root: '/repo',
        branch: 'loom/worker',
        status: 'open',
        recorded_at: '2026-04-07T10:00:00+00:00',
      },
    },
    queued: {
      id: 'queued',
      group: 'alpha',
      task: 'Queued polish',
      lane: 'To Do',
      position: 2,
      resume_after_boundary_task_id: 'boundary',
    },
  };

  const boundaryHtml = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.boundary,
      {},
      0
    )
  `);
  const queuedHtml = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.queued,
      {},
      0
    )
  `);

  assert.match(boundaryHtml, /Safe review point/);
  assert.match(queuedHtml, /Queued after .*Stable review point/);
});

test('renderBoardCard does not render overlap badges or preview text', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.dispatch_overlap = {
    root: {
      level: 'conflict',
      summary: 'Shares the same worktree branch as another active agent.',
    },
  };
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Implement auth flow',
      lane: 'In Progress',
      position: 1,
    },
  };

  const html = runInContext(context, `
    _renderBoardCard(
      state.board_tasks.root,
      {},
      0
    )
  `);

  assert.doesNotMatch(html, /board-card-overlap-conflict/);
  assert.doesNotMatch(html, /Overlap conflict/);
  assert.doesNotMatch(html, /Shares the same worktree branch as another active agent/);
});

test('renderAgentDetails shows branch boundary status and queued follow-ups', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      worktree_path: '/repo/.loom/worktrees/worker',
      worktree_repo_root: '/repo',
      worktree_branch: 'loom/worker',
      mcp_messages: [],
    },
  };
  context.state.board_tasks = {
    boundary: {
      id: 'boundary',
      group: 'alpha',
      task: 'Stable review point',
      lane: 'Done',
      agent_id: 'agent-1',
      worktree_boundary: {
        repo_root: '/repo',
        branch: 'loom/worker',
        status: 'open',
        recorded_at: '2026-04-07T10:00:00+00:00',
      },
    },
    current: {
      id: 'current',
      group: 'alpha',
      task: 'Implement follow-up',
      lane: 'In Progress',
      agent_id: 'agent-1',
      resume_after_boundary_task_id: 'boundary',
    },
    queued: {
      id: 'queued',
      group: 'alpha',
      task: 'Queue release notes',
      lane: 'To Do',
      agent_id: 'agent-1',
      resume_after_boundary_task_id: 'boundary',
    },
  };

  const html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);

  assert.match(html, /Review point/);
  assert.match(html, /Stable review point/);
  assert.match(html, /Branch advanced/);
  assert.match(html, /Queued next/);
  assert.match(html, /Queue release notes/);
  assert.match(html, /Beyond boundary/);
  assert.match(html, /Implement follow-up/);
});

test('renderAgentDetails expands task details and preserves the expanded state across rerenders', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `render = function() {};`);

  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship the `hover` stability fix',
      description: 'Keep hover state stable while websocket updates stream in.\nDo not drop the expanded row.',
      lane: 'In Progress',
      agent_id: 'agent-1',
    },
  };

  let html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.doesNotMatch(html, /Keep hover state stable/);

  runInContext(context, `_toggleAgentDetailTask('agent-1')`);
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /detail-row-expanded/);
  assert.match(html, /Keep hover state stable while websocket updates stream in\.<br>Do not drop the expanded row\./);
  assert.match(html, /detail-link-arrow/);

  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /Keep hover state stable while websocket updates stream in\./);

  runInContext(context, `_toggleAgentDetailTask('agent-1')`);
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.doesNotMatch(html, /detail-row-expanded/);
  assert.doesNotMatch(html, /Keep hover state stable while websocket updates stream in\./);
});

test('agent detail inline description editing preserves draft state and save/cancel behavior across rerenders', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `render = function() {};`);

  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship inline edits',
      description: 'Keep the inline editor stable',
      lane: 'In Progress',
      agent_id: 'agent-1',
    },
  };

  runInContext(context, `_toggleAgentDetailTask('agent-1')`);
  let html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /detail-description-edit/);
  assert.match(html, /Keep the inline editor stable/);

  runInContext(context, `agentDetailEditDescription('agent-1', 'task-1')`);
  runInContext(context, `agentDetailDescriptionInput('agent-1', 'task-1', 'Draft text from inline editing')`);
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /detail-inline-description-input/);
  assert.match(html, /Draft text from inline editing/);

  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /Draft text from inline editing/);

  runInContext(context, `agentDetailCancelDescriptionEdit('agent-1', 'task-1')`);
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /Keep the inline editor stable/);
  assert.doesNotMatch(html, /Draft text from inline editing/);
  assert.equal(jsonValue(context, 'sendCalls.length'), 0);

  runInContext(context, `agentDetailEditDescription('agent-1', 'task-1')`);
  runInContext(context, `agentDetailDescriptionInput('agent-1', 'task-1', 'Saved from inline editing')`);
  runInContext(context, `agentDetailSaveDescription('agent-1', 'task-1')`);
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /Saved from inline editing/);
  assert.doesNotMatch(html, /detail-inline-description-input/);
  assert.equal(jsonValue(context, 'state.board_tasks["task-1"].description'), 'Saved from inline editing');
  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'board_update_task', id: 'task-1', description: 'Saved from inline editing' },
  ]);
});

test('renderAgentDetails expands one MCP message at a time and keeps the selection across rerenders', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `render = function() {};`);

  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      mcp_messages: [
        { action: 'progress', message: 'Expanded line one\nExpanded line two', timestamp: 1712345678 },
        { action: 'done', message: 'Collapsed summary', timestamp: 1712345679 },
      ],
    },
  };

  let html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.doesNotMatch(html, /mcp-entry-expanded/);

  const messageKey = runInContext(context, `_agentDetailMessageKey(state.agents["agent-1"].mcp_messages[0], 0)`);
  runInContext(context, `_toggleAgentDetailMessage('agent-1', ${JSON.stringify(messageKey)})`);

  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /mcp-entry-expanded/);
  assert.match(html, /Expanded line one<br>Expanded line two/);
  assert.doesNotMatch(html, /Collapsed summary<\/div>/);

  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /Expanded line one<br>Expanded line two/);
});

test('renderAgentDetails adds clickable diff, checkpoint, and preserved-merge affordances only when data exists', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      worktree_repo_root: '/repo',
      worktree_branch: 'loom/worker',
      worktree_merged: true,
      worktree_diff: { files: 2, insertions: 7, deletions: 3 },
      worktree_checkpoints: 4,
      mcp_messages: [],
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship review panel polish',
      lane: 'In Progress',
      agent_id: 'agent-1',
    },
    boundary: {
      id: 'boundary',
      group: 'alpha',
      task: 'Preserved merge boundary',
      lane: 'Done',
      agent_id: 'agent-1',
      worktree_boundary: {
        repo_root: '/repo',
        branch: 'loom/worker',
        status: 'merged',
        merged_at: '2026-04-10T10:00:00Z',
      },
      artifacts: [{
        id: 'artifact-1',
        type: 'diff',
        title: 'Pre-merge diff',
        filename: 'worker-pre-merge.patch',
        path: '/tmp/worker-pre-merge.patch',
        storage: { kind: 'path', path: '/tmp/worker-pre-merge.patch', content: '' },
        metadata: {
          preserved_on_merge: true,
          worktree_branch: 'loom/worker',
          boundary_recorded_at: '2026-04-10T09:58:00Z',
        },
      }],
    },
  };

  let html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.match(html, /detail-link-arrow/);
  assert.match(html, /showDiffView\('agent-1',true\)/);
  assert.match(html, /worktreeHistory\('agent-1'\)/);
  assert.match(html, /title="View preserved merge diff"/);
  assert.match(html, /data-task-id="boundary"/);
  assert.match(html, /data-artifact-id="artifact-1"/);
  assert.match(html, /data-artifact-filename="worker-pre-merge\.patch"/);
  assert.match(html, /data-artifact-path="\/tmp\/worker-pre-merge\.patch"/);
  assert.match(html, /openTaskArtifactById\(this\.dataset\.taskId,this\.dataset\.artifactId,this\.dataset\.artifactFilename,this\.dataset\.artifactPath\)/);

  context.state.board_tasks.boundary.artifacts = [];
  html = runInContext(context, `renderAgentDetails(state.agents["agent-1"])`);
  assert.doesNotMatch(html, /View preserved merge diff/);
  assert.match(html, /<span class="detail-wt-tag detail-wt-merged">merged<\/span>/);
});

test('renderAgentDetails shows architect pending hires and compact decisions', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      mcp_messages: [],
    },
  };
  context.state.pending_hires = {
    'hire-1': {
      id: 'hire-1',
      architect_id: 'arch-1',
      requested_name: 'Alice',
      requested_provider: 'codex',
      requested_command: 'codex --profile engineer',
      requested_directory: '/repo/services/api',
      created_at: 1712345678,
    },
    'hire-hidden': {
      id: 'hire-hidden',
      architect_id: 'arch-2',
      requested_name: 'Hidden engineer',
      created_at: 1712345678,
    },
  };
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Define the API cut line',
      rationale: 'Ship the API before the CLI.\nHold the dashboard until validation lands.',
      status: 'approved',
      linked_task_ids: ['task-1'],
      linked_engineer_ids: ['eng-1', 'eng-2'],
      updated_at: 1712345680,
    },
    'decision-hidden': {
      id: 'decision-hidden',
      architect_id: 'arch-2',
      title: 'Hidden decision',
      rationale: 'Do not show this row',
      status: 'proposed',
      updated_at: 1712345681,
    },
  };

  const html = runInContext(context, `renderAgentDetails(state.agents["arch-1"])`);

  assert.match(html, /Pending hires/);
  assert.match(html, /Alice/);
  assert.match(html, /codex --profile engineer/);
  assert.match(html, /approvePendingHire\("hire-1"\)/);
  assert.match(html, /rejectPendingHire\("hire-1"\)/);
  assert.doesNotMatch(html, /Hidden engineer/);

  assert.match(html, /Decisions/);
  assert.match(html, /detail-decisions-log/);
  assert.match(html, /Define the API cut line/);
  assert.doesNotMatch(html, /Ship the API before the CLI/);
  assert.doesNotMatch(html, /Linked to 1 task/);
  assert.doesNotMatch(html, /2 engineers/);
  assert.doesNotMatch(html, /approved/);
  assert.doesNotMatch(html, /Hidden decision/);
});

test('architect pending-hire actions send approve and reject commands', () => {
  const { sandbox } = createSandbox();
  sandbox.window.prompt = function(message, initialValue) {
    sandbox.promptArgs = [message, initialValue];
    return 'Needs a narrower scope';
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');

  runInContext(context, `approvePendingHire('hire-1')`);
  runInContext(context, `rejectPendingHire('hire-2')`);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'pending_hire_approve', id: 'hire-1' },
    { cmd: 'pending_hire_reject', id: 'hire-2', note: 'Needs a narrower scope' },
  ]);
  assert.deepEqual(sandbox.promptArgs, ['Optional rejection note', '']);

  sandbox.sendCalls.length = 0;
  sandbox.window.prompt = function() { return null; };
  runInContext(context, `rejectPendingHire('hire-3')`);
  assert.equal(sandbox.sendCalls.length, 0);
});

test('decision and pending-hire deltas invalidate the main surface', () => {
  const { sandbox } = createSandbox({
    renderInvalidatedSurfaces(flags) {
      sandbox.lastInvalidations = JSON.parse(JSON.stringify(flags));
    },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');

  runInContext(context, `_expectedSeq = 1;`);
  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'pending_hire_upsert', id: 'hire-1', architect_id: 'arch-1', requested_name: 'Alice' },
      { op: 'decision_upsert', id: 'decision-1', architect_id: 'arch-1', title: 'Define scope' },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.lastInvalidations)), {
    main: true,
    board: false,
    context: false,
    events: false,
    weaver: true,
    templates: false,
  });
  assert.deepEqual(jsonValue(context, `state.pending_hires["hire-1"]`), {
    id: 'hire-1',
    architect_id: 'arch-1',
    requested_name: 'Alice',
  });
  assert.deepEqual(jsonValue(context, `state.decisions["decision-1"]`), {
    id: 'decision-1',
    architect_id: 'arch-1',
    title: 'Define scope',
  });

  runInContext(context, `_expectedSeq = 2;`);
  context._handleDelta({
    seq: 2,
    ops: [
      { op: 'pending_hire_resolve', id: 'hire-1' },
      { op: 'decision_remove', id: 'decision-1' },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.lastInvalidations)), {
    main: true,
    board: false,
    context: false,
    events: false,
    weaver: true,
    templates: false,
  });
  assert.equal(
    jsonValue(context, `Object.prototype.hasOwnProperty.call(state.pending_hires || {}, "hire-1")`),
    false,
  );
  assert.equal(
    jsonValue(context, `Object.prototype.hasOwnProperty.call(state.decisions || {}, "decision-1")`),
    false,
  );
});

test('backlog dispatch note ignores overlap warnings for ready work', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    ready: {
      id: 'ready',
      group: 'alpha',
      task: 'Implement auth flow',
      lane: 'Backlog',
      position: 1,
    },
  };
  context.state.dispatch_overlap = {
    ready: {
      level: 'warning',
      summary: 'Shares a module with active work.',
    },
  };

  runInContext(context, `_boardSelectedLane = 'Backlog';`);
  const note = runInContext(context, `_boardBacklogDispatchNote([state.board_tasks.ready])`);

  assert.equal(note, null);
});

test('renderBoard does not surface recommended dispatch banners or markers', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'To Do', 'In Progress', 'Done'];
  context.state.agents = {
    'agent-idle': {
      id: 'agent-idle',
      name: 'Worker Two',
      current_task_id: '',
    },
  };
  context.state.board_tasks = {
    boundary: {
      id: 'boundary',
      group: 'alpha',
      task: 'Stable boundary',
      lane: 'Done',
      position: 2,
    },
    recommended: {
      id: 'recommended',
      group: 'alpha',
      task: 'Release notes follow-up',
      lane: 'To Do',
      agent_id: 'agent-idle',
      labels: ['priority:medium'],
      resume_after_boundary_task_id: 'boundary',
      position: 1,
    },
    other: {
      id: 'other',
      group: 'alpha',
      task: 'Refactor auth',
      lane: 'Backlog',
      labels: ['priority:high'],
      position: 0,
    },
  };

  runInContext(context, `_boardSelectedLane = 'To Do';`);
  context.renderBoard();

  assert.doesNotMatch(panel.innerHTML, /Recommended next dispatch: Release notes follow-up/);
  assert.doesNotMatch(panel.innerHTML, /Recommended next dispatch/);
});

test('renderBoard explains filtered empty states with a clear recovery action', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Ship docs',
      description: 'Publish the handbook',
      lane: 'Backlog',
      position: 1,
    },
  };

  runInContext(context, `
    _boardSearchQuery = 'no-such-task';
    _boardFilterLabels = [];
    _boardFilterActions = [];
    _boardFilterAgents = [];
  `);

  context.renderBoard();

  assert.match(panel.innerHTML, /No matching tasks/);
  assert.match(panel.innerHTML, /hide everything in Backlog/);
  assert.match(panel.innerHTML, /Clear Filters/);
});

test('renderBoard distinguishes empty, blocked, and no-ready backlog states', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'To Do', 'Done'];

  context.renderBoard();
  assert.match(panel.innerHTML, /No tasks in Backlog/);
  assert.match(panel.innerHTML, /Add a task here or move existing work into this lane/);
  assert.match(panel.innerHTML, /\+ Task/);

  context.state.board_tasks = {
    dep: {
      id: 'dep',
      group: 'alpha',
      task: 'Finish auth review',
      lane: 'To Do',
      position: 1,
    },
    blocked: {
      id: 'blocked',
      group: 'alpha',
      task: 'Dispatch fix',
      lane: 'Backlog',
      position: 0,
      depends_on: ['dep'],
    },
  };

  context.renderBoard();
  assert.match(panel.innerHTML, /Everything in Backlog is blocked/);
  assert.match(panel.innerHTML, /Resolve prerequisites to dispatch work/);

  const futureIso = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  context.state.board_tasks = {
    scheduled: {
      id: 'scheduled',
      group: 'alpha',
      task: 'Dispatch later',
      lane: 'Backlog',
      position: 0,
      scheduled_at: futureIso,
    },
  };

  context.renderBoard();
  assert.match(panel.innerHTML, /Nothing is ready to dispatch/);
  assert.match(panel.innerHTML, /scheduled for later/);
});

test('board visible tasks hide archived items until archived view is enabled', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    active: {
      id: 'active',
      group: 'alpha',
      task: 'Visible task',
      lane: 'Done',
      labels: ['bug'],
      position: 1,
    },
    archived: {
      id: 'archived',
      group: 'alpha',
      task: 'Hidden task',
      lane: 'Archived',
      archived_from_lane: 'Done',
      position: 0,
    },
  };

  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks()).sort()'), ['active']);

  runInContext(context, `_boardShowArchived = true;`);

  assert.deepEqual(jsonValue(context, 'Object.keys(_boardVisibleTasks()).sort()'), ['active', 'archived']);
});

test('boardAddTaskInput mirrors the current inline editor text into draft state', () => {
  const { context, document } = createBoardHarness();
  const input = document.register('board-add-task-input');
  document.register('board-add-label-dropdown');
  input.value = 'Follow up with design review';
  input.selectionStart = input.value.length;
  input.selectionEnd = input.value.length;

  context.boardAddTaskInput(input);

  assert.equal(runInContext(context, '_boardAddingTaskDraft'), 'Follow up with design review');
});

test('boardAddTaskKeydown moves Home and End to the current line boundaries', () => {
  const { context, document } = createBoardHarness();
  const input = document.register('board-add-task-input');
  document.register('board-add-label-dropdown');
  input.value = 'First line\nSecond line\nThird line';
  const secondLineStart = input.value.indexOf('Second');
  const secondLineEnd = input.value.indexOf('\n', secondLineStart);
  const midSecondLine = secondLineStart + 'Second'.length;
  input.selectionStart = midSecondLine;
  input.selectionEnd = midSecondLine;

  const homeEvent = {
    key: 'Home',
    target: input,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
    stopPropagation() {},
  };
  context.boardAddTaskKeydown(homeEvent);

  assert.equal(homeEvent.preventDefaultCalled, true);
  assert.equal(input.selectionStart, secondLineStart);
  assert.equal(input.selectionEnd, secondLineStart);

  input.selectionStart = midSecondLine;
  input.selectionEnd = midSecondLine;
  const endEvent = {
    key: 'End',
    target: input,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
    stopPropagation() {},
  };
  context.boardAddTaskKeydown(endEvent);

  assert.equal(endEvent.preventDefaultCalled, true);
  assert.equal(input.selectionStart, secondLineEnd);
  assert.equal(input.selectionEnd, secondLineEnd);
});

test('boardAddTaskKeydown updates inline label dropdown after moving the caret with Home', () => {
  const { context, document } = createBoardHarness();
  const input = document.register('board-add-task-input');
  const dropdown = document.register('board-add-label-dropdown');
  runInContext(context, `
    _getAllLabels = function() { return ['release']; };
  `);
  input.value = 'First line\nTask %rel';
  input.selectionStart = input.value.length;
  input.selectionEnd = input.value.length;

  context.boardAddTaskInput(input);
  assert.equal(dropdown.style.display, '');

  const homeEvent = {
    key: 'Home',
    target: input,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
    preventDefault() {},
    stopPropagation() {},
  };
  context.boardAddTaskKeydown(homeEvent);

  assert.equal(input.selectionStart, 'First line\n'.length);
  assert.equal(input.selectionEnd, 'First line\n'.length);
  assert.equal(dropdown.style.display, 'none');
});

test('boardDuplicateTask creates a fresh task from reusable fields only', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Ship release',
      description: 'Reuse the task details, not the workflow state',
      group: 'alpha',
      lane: 'Done',
      action_name: 'triage',
      action_vars: { OWNER: 'frontend' },
      agent_template: 'worker',
      agent_id: 'agent-1',
      labels: ['bug', 'loom:error'],
      depends_on: ['dep-1'],
      scheduled_at: '2099-01-01T12:00:00.000Z',
      parent_task_id: 'parent-1',
      pipeline_root_id: 'parent-1',
      pipeline_depth: 1,
      status: 'Blocked',
      attachments: [{ filename: 'evidence.png' }],
      artifacts: [{ type: 'log', title: 'build.log' }],
      external_id: 'EXT-123',
      external_url: 'https://example.test/task/EXT-123',
    },
  };

  context.boardDuplicateTask('task-1');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_add_task',
    task: 'Ship release',
    group: 'alpha',
    description: 'Reuse the task details, not the workflow state',
    action_name: 'triage',
    action_vars: { OWNER: 'frontend' },
    agent_template: 'worker',
    labels: ['bug'],
  }]);
});

test('boardCloneTask opens a fresh create modal with sanitized copied fields', () => {
  const { context } = createBoardHarness();
  runInContext(context, `
    _cloneModalConfig = null;
    _taskOpenModal = function(config) { _cloneModalConfig = config; };
  `);
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Ship release',
      description: 'Reuse the task details, not the workflow state',
      group: 'alpha',
      lane: 'Done',
      action_name: 'triage',
      action_vars: { OWNER: 'frontend' },
      agent_template: 'worker',
      agent_id: 'agent-1',
      labels: ['bug', 'loom:error'],
      depends_on: ['dep-1'],
      scheduled_at: '2099-01-01T12:00:00.000Z',
      parent_task_id: 'parent-1',
      pipeline_root_id: 'parent-1',
      pipeline_depth: 1,
      status: 'Blocked',
    },
  };

  context.boardCloneTask('task-1');

  assert.deepEqual(jsonValue(context, '_cloneModalConfig'), {
    draftScope: 'clone:task-1',
    editId: null,
    title: 'Clone Task',
    submitLabel: 'Create',
    task: 'Ship release',
    description: 'Reuse the task details, not the workflow state',
    labels: ['bug'],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: 'triage',
    agentTemplate: 'worker',
    actionVars: { OWNER: 'frontend' },
    group: 'alpha',
    lane: '',
    scheduledInput: '',
    selectTask: false,
  });
});

test('boardQuickSetPriority replaces only the priority label', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      labels: ['bug', 'loom:error', 'priority:low'],
    },
  };

  context.boardQuickSetPriority('task-1', 'high');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    labels: ['bug', 'loom:error', 'priority:high'],
  }]);
});

test('boardQuickAssignAgent updates the assigned agent directly from the card', () => {
  const { context } = createBoardHarness();

  context.boardQuickAssignAgent('task-1', 'agent-7');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    agent_id: 'agent-7',
  }]);
});

test('boardQuickSaveDue stores the inline due editor value as ISO time', () => {
  const { context } = createBoardHarness();
  runInContext(context, `_boardQuickDueDraft = '2099-01-08T09:45';`);

  context.boardQuickSaveDue('task-1');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    scheduled_at: new Date('2099-01-08T09:45').toISOString(),
  }]);
});

test('boardQuickAddLabel and boardQuickRemoveLabel preserve unrelated labels', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      labels: ['bug', 'loom:error', 'priority:medium'],
    },
  };

  context.boardQuickLabelInput('ops');
  context.boardQuickAddLabel('task-1');
  context.boardQuickRemoveLabel('task-1', 'bug');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_update_task',
      id: 'task-1',
      labels: ['bug', 'loom:error', 'priority:medium', 'ops'],
    },
    {
      cmd: 'board_update_task',
      id: 'task-1',
      labels: ['loom:error', 'priority:medium', 'ops'],
    },
  ]);
});

test('boardQuickAddLabel keeps the labels quick editor open and refocuses a cleared input', () => {
  const { context, document } = createBoardHarness({ stubCards: false });
  const panel = document.register('panel-board');
  document.register('board-cards');
  document.register('board-lane-tabs');
  const input = document.register('board-quick-label-input-task-1');
  input.value = 'ops';
  input.selectionStart = input.value.length;
  input.selectionEnd = input.value.length;
  panel.appendChild(input);
  document.activeElement = input;

  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Backlog',
      position: 1,
      labels: ['bug'],
    },
  };

  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardQuickEditTask = 'task-1';
    _boardQuickEditKind = 'labels';
    _boardQuickLabelDraft = 'ops';
  `);

  context.boardQuickAddLabel('task-1');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    labels: ['bug', 'ops'],
  }]);
  assert.equal(runInContext(context, '_boardQuickEditTask'), 'task-1');
  assert.equal(runInContext(context, '_boardQuickEditKind'), 'labels');
  assert.equal(runInContext(context, '_boardQuickLabelDraft'), '');
  assert.equal(input.focused, true);
  assert.equal(input.value, '');
  assert.equal(input.selectionStart, 0);
  assert.equal(input.selectionEnd, 0);
  assert.match(panel.innerHTML, /board-card-quick-editor/);
  assert.match(panel.innerHTML, /board-quick-label-input-task-1/);
  assert.match(panel.innerHTML, /Labels 2/);
  assert.match(panel.innerHTML, /board-card-quick-chip">ops/);
});

test('boardQuickLabelKeydown Enter keeps the labels quick editor open', () => {
  const { context } = createBoardHarness();
  let prevented = false;
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      labels: ['bug'],
    },
  };

  runInContext(context, `
    _boardQuickEditTask = 'task-1';
    _boardQuickEditKind = 'labels';
    _boardQuickLabelDraft = 'ops';
  `);

  context.boardQuickLabelKeydown({
    key: 'Enter',
    preventDefault() { prevented = true; },
  }, 'task-1');

  assert.equal(prevented, true);
  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    labels: ['bug', 'ops'],
  }]);
  assert.equal(runInContext(context, '_boardQuickEditTask'), 'task-1');
  assert.equal(runInContext(context, '_boardQuickEditKind'), 'labels');
});

test('boardQuickRemoveLabel keeps the labels quick editor open and refocuses the draft input', () => {
  const { context, document } = createBoardHarness({ stubCards: false });
  const panel = document.register('panel-board');
  document.register('board-cards');
  document.register('board-lane-tabs');
  const input = document.register('board-quick-label-input-task-1');
  input.value = 'ops';
  input.selectionStart = input.value.length;
  input.selectionEnd = input.value.length;
  panel.appendChild(input);
  document.activeElement = input;

  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Backlog',
      position: 1,
      labels: ['bug', 'docs'],
    },
  };

  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardQuickEditTask = 'task-1';
    _boardQuickEditKind = 'labels';
    _boardQuickLabelDraft = 'ops';
  `);

  context.boardQuickRemoveLabel('task-1', 'bug');

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_update_task',
    id: 'task-1',
    labels: ['docs'],
  }]);
  assert.equal(runInContext(context, '_boardQuickEditTask'), 'task-1');
  assert.equal(runInContext(context, '_boardQuickEditKind'), 'labels');
  assert.equal(runInContext(context, '_boardQuickLabelDraft'), 'ops');
  assert.equal(input.focused, true);
  assert.equal(input.value, 'ops');
  assert.equal(input.selectionStart, 3);
  assert.equal(input.selectionEnd, 3);
  assert.match(panel.innerHTML, /board-card-quick-editor/);
  assert.match(panel.innerHTML, /board-quick-label-input-task-1/);
  assert.match(panel.innerHTML, /Labels 1/);
  assert.match(panel.innerHTML, /board-card-quick-chip">docs/);
  assert.doesNotMatch(panel.innerHTML, /board-card-quick-chip">bug/);
});

test('_renderBoardCard includes compact quick-edit controls for focused root cards', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'worker', group: 'alpha', cell_type: 'agent' },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      labels: ['bug', 'priority:high'],
      agent_id: 'agent-1',
      scheduled_at: '2099-01-08T09:45:00.000Z',
    },
  };

  runInContext(context, `_boardFocusedTask = 'task-1';`);
  const html = runInContext(context, `_renderBoardCard(state.board_tasks["task-1"], {}, 0)`);

  assert.match(html, /board-card-quick-controls/);
  assert.match(html, /Labels/);
  assert.match(html, /worker/);
  assert.match(html, /Priority: High/);
});

test('boardToggleBatchEdit requests actions for single-group selections', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': { id: 'task-1', group: 'alpha', task: 'Ship release' },
    'task-2': { id: 'task-2', group: 'alpha', task: 'Write docs' },
  };

  runInContext(context, `
    _boardSelectedTasks = { 'task-1': true, 'task-2': true };
  `);

  context.boardToggleBatchEdit({ stopPropagation() {} });

  assert.equal(runInContext(context, '_boardBatchEditOpen'), true);
  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'list_actions', group: 'alpha' },
  ]);
});

test('boardApplyBatchEdit applies touched fields across all selected tasks', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship release',
      labels: ['bug', 'loom:error'],
    },
    'task-2': {
      id: 'task-2',
      group: 'alpha',
      task: 'Write docs',
      labels: ['docs', 'priority:low'],
    },
  };

  runInContext(context, `
    _boardSelectedTasks = { 'task-1': true, 'task-2': true };
    _boardBatchEditLabel = 'ops';
    _boardBatchEditAssignee = 'agent-9';
    _boardBatchEditDueMode = 'set';
    _boardBatchEditDue = '2099-01-10T09:30';
    _boardBatchEditAction = 'triage';
    _boardBatchEditPriority = 'high';
  `);

  context.boardApplyBatchEdit();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_update_task',
      id: 'task-1',
      labels: ['bug', 'loom:error', 'ops', 'priority:high'],
      agent_id: 'agent-9',
      action_name: 'triage',
      action_vars: {},
      scheduled_at: new Date('2099-01-10T09:30').toISOString(),
    },
    {
      cmd: 'board_update_task',
      id: 'task-2',
      labels: ['docs', 'ops', 'priority:high'],
      agent_id: 'agent-9',
      action_name: 'triage',
      action_vars: {},
      scheduled_at: new Date('2099-01-10T09:30').toISOString(),
    },
  ]);
  assert.equal(runInContext(context, '_boardSelectedCount()'), 0);
});

test('boardApplyBatchEdit skips group-scoped changes for mixed-group selections', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    'task-1': { id: 'task-1', group: 'alpha', task: 'Ship release', labels: ['bug'] },
    'task-2': { id: 'task-2', group: 'beta', task: 'Write docs', labels: ['docs'] },
  };

  runInContext(context, `
    _boardSelectedTasks = { 'task-1': true, 'task-2': true };
    _boardBatchEditLabel = 'ops';
    _boardBatchEditAssignee = 'agent-9';
    _boardBatchEditDueMode = 'set';
    _boardBatchEditDue = '2099-01-10T09:30';
    _boardBatchEditAction = 'triage';
    _boardBatchEditPriority = 'medium';
  `);

  context.boardApplyBatchEdit();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_update_task',
      id: 'task-1',
      labels: ['bug', 'ops', 'priority:medium'],
      scheduled_at: new Date('2099-01-10T09:30').toISOString(),
    },
    {
      cmd: 'board_update_task',
      id: 'task-2',
      labels: ['docs', 'ops', 'priority:medium'],
      scheduled_at: new Date('2099-01-10T09:30').toISOString(),
    },
  ]);
});

test('boardBulkArchiveSelected archives selected completed tasks and their descendants', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Done',
      labels: ['bug'],
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Verify release',
      lane: 'Done',
      parent_task_id: 'root',
      labels: ['loom:error'],
      archived_from_lane: '',
    },
    active: {
      id: 'active',
      group: 'alpha',
      task: 'Still open',
      lane: 'In Progress',
      labels: ['docs'],
    },
  };

  runInContext(context, `
    _boardSelectedTasks = { root: true, active: true };
  `);

  context.boardBulkArchiveSelected();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_archive_task',
      id: 'root',
    },
    {
      cmd: 'board_archive_task',
      id: 'child',
    },
  ]);
  assert.equal(runInContext(context, '_boardSelectedCount()'), 0);
});

test('boardBulkRestoreSelected restores archived tasks and descendants', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    root: {
      id: 'root',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Archived',
      archived_from_lane: 'Done',
      labels: ['bug'],
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Verify release',
      lane: 'Archived',
      archived_from_lane: 'Done',
      parent_task_id: 'root',
      labels: ['loom:error'],
    },
  };

  runInContext(context, `
    _boardSelectedTasks = { root: true };
  `);

  context.boardBulkRestoreSelected();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_unarchive_task',
      id: 'root',
    },
    {
      cmd: 'board_unarchive_task',
      id: 'child',
    },
  ]);
  assert.equal(runInContext(context, '_boardSelectedCount()'), 0);
});

test('_renderBoardSelectionBar explains mixed-group limits when batch edit is open', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);
  context.state.board_tasks = {
    'task-1': { id: 'task-1', group: 'alpha', task: 'Ship release', lane: 'Backlog', position: 1 },
    'task-2': { id: 'task-2', group: 'beta', task: 'Write docs', lane: 'Backlog', position: 0 },
  };

  runInContext(context, `
    _boardSelectedTasks = { 'task-1': true, 'task-2': true };
    _boardBatchEditOpen = true;
  `);

  const html = runInContext(context, `_renderBoardSelectionBar()`);

  assert.match(html, /Action and assignee edits require all selected tasks to be in the same group/);
});

test('_renderBoardSelectionBar shows archive and restore actions for eligible selections', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);
  context.state.board_tasks = {
    done: {
      id: 'done',
      group: 'alpha',
      task: 'Ship release',
      lane: 'Done',
      labels: ['bug'],
      position: 1,
    },
    archived: {
      id: 'archived',
      group: 'alpha',
      task: 'Old release',
      lane: 'Archived',
      archived_from_lane: 'Done',
      labels: [],
      position: 0,
    },
  };

  runInContext(context, `
    _boardSelectedTasks = { done: true, archived: true };
  `);

  const html = runInContext(context, `_renderBoardSelectionBar()`);

  assert.match(html, /Archive completed \(1\)/);
  assert.match(html, /Restore \(1\)/);
});

test('renderBoard surfaces a stale completed-task archive suggestion on the Done lane', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Done'];
  context.state.board_tasks = {
    stale: {
      id: 'stale',
      group: 'alpha',
      task: 'Old completed task',
      lane: 'Done',
      labels: ['bug'],
      updated_at: '2000-01-01T00:00:00Z',
      position: 0,
    },
  };

  runInContext(context, `_boardSelectedLane = 'Done';`);
  context.renderBoard();

  assert.match(panel.innerHTML, /Archive stale/);
  assert.match(panel.innerHTML, /inactive for 7\+ days/);
});

test('boardArchiveSuggestedDone archives only stale completed tasks', () => {
  const { context } = createBoardHarness();
  context.state.board_tasks = {
    stale: {
      id: 'stale',
      group: 'alpha',
      task: 'Old completed task',
      lane: 'Done',
      labels: ['bug'],
      updated_at: '2000-01-01T00:00:00Z',
    },
    fresh: {
      id: 'fresh',
      group: 'alpha',
      task: 'Recent completed task',
      lane: 'Done',
      labels: ['docs'],
      updated_at: new Date().toISOString(),
    },
    active: {
      id: 'active',
      group: 'alpha',
      task: 'Still active',
      lane: 'In Progress',
      labels: ['ops'],
      updated_at: '2000-01-01T00:00:00Z',
    },
  };

  context.boardArchiveSuggestedDone();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_archive_task',
      id: 'stale',
    },
  ]);
});

test('events attention items include active asks and blocked agents for the current group', () => {
  const { context } = createEventsHarness();
  context.state.board_tasks = {
    parent: {
      id: 'parent',
      group: 'alpha',
      task: 'Root pipeline task',
      agent_id: 'agent-1',
    },
    ask: {
      id: 'ask',
      group: 'alpha',
      task: 'Need approval',
      description: 'Choose merge strategy',
      labels: ['loom:human'],
      parent_task_id: 'parent',
      lane: 'Backlog',
      created_at: '2099-01-02T00:00:00Z',
    },
    doneAsk: {
      id: 'doneAsk',
      group: 'alpha',
      task: 'Already resolved',
      labels: ['loom:human'],
      lane: 'Done',
      created_at: '2099-01-01T00:00:00Z',
    },
  };
  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'worker',
      slug: 'worker',
      group: 'alpha',
      needs_attention: true,
      activity_detail: 'Blocked on review',
      last_event_at: 100,
      cell_type: 'agent',
    },
    terminal: {
      id: 'terminal',
      name: 'shell',
      group: 'alpha',
      needs_attention: true,
      activity_detail: 'Ignore terminals',
      last_event_at: 200,
      cell_type: 'terminal',
    },
    other: {
      id: 'other',
      name: 'other',
      group: 'beta',
      needs_attention: true,
      activity_detail: 'Wrong group',
      last_event_at: 300,
      cell_type: 'agent',
    },
  };

  const items = jsonValue(context, '_eventsGetAttentionItems()');

  assert.deepEqual(items.map((item) => item.id), ['ask', 'agent-1']);
  assert.equal(items[0].parent_agent_id, 'agent-1');
  assert.equal(items[1].type, 'blocked');
});

test('event filtering respects the selected kind group and search query', () => {
  const { context } = createEventsHarness();
  runInContext(context, `
    _eventsKindFilter = 'errors';
    _eventsSearchQuery = 'stuck';
  `);

  assert.equal(
    runInContext(context, `_eventsMatchesFilters({ kind: 'agent_error', message: 'Agent is stuck', agent_name: 'worker' })`),
    true,
  );
  assert.equal(
    runInContext(context, `_eventsMatchesFilters({ kind: 'task_completed', message: 'Agent is stuck', agent_name: 'worker' })`),
    false,
  );
  assert.equal(
    runInContext(context, `_eventsMatchesFilters({ kind: 'agent_error', message: 'All clear', agent_name: 'worker' })`),
    false,
  );
});

test('renderEvents preserves inline resolve drafts across panel rerenders', () => {
  const { context, document } = createEventsHarness();
  const panel = document.register('panel-events');
  const log = new FakeElement('events-log');
  log.scrollTop = 41;
  const textarea = new FakeElement('events-resolve-ask-1');
  textarea.value = 'Please re-run the failing test';
  panel.setQuerySelector('.events-log', log);
  panel.setQuerySelectorAll('.events-resolve-textarea', [textarea]);
  panel.setQuerySelector('.events-search-input', new FakeElement('events-search-input'));

  context.state.panel_events = [];

  context.renderEvents();

  assert.deepEqual(jsonValue(context, '_eventsResolveDrafts'), {
    'ask-1': 'Please re-run the failing test',
  });
  assert.equal(runInContext(context, '_eventsScrollTop'), 41);
});

test('renderBoard restores focused input value and caret across rerenders', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');
  document.register('board-lane-tabs');
  const input = document.register('board-search-input');
  input.value = 'deploy auth';
  input.selectionStart = 6;
  input.selectionEnd = 11;
  panel.appendChild(input);
  document.activeElement = input;

  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    task: { id: 'task', group: 'alpha', task: 'Deploy auth flow', lane: 'Backlog', position: 1 },
  };

  runInContext(context, `_boardSearchQuery = '';`);
  context.renderBoard();

  assert.equal(input.focused, true);
  assert.equal(input.value, 'deploy auth');
  assert.equal(input.selectionStart, 6);
  assert.equal(input.selectionEnd, 11);
});

test('renderBoard restores lane and card scroll before deferred layout work runs', () => {
  const rafQueue = [];
  const { sandbox, document } = createSandbox({
    requestAnimationFrame(fn) {
      rafQueue.push(fn);
      return rafQueue.length;
    },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);
  runInContext(context, `
    _renderBoardSelectionBar = function() { return ''; };
    _renderBoardCard = function(t) { return '<div class="board-card">' + t.id + '</div>'; };
    _boardScheduleCount = function() { return 0; };
    boardUpdateScrollArrows = function() {};
    boardAddTaskAutoResize = function() {};
  `);

  document.register('panel-board');
  const cards = document.register('board-cards');
  const tabs = document.register('board-lane-tabs');
  tabs.setQuerySelector('.board-lane-tab.active', null);
  cards.scrollTop = 81;

  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    task: { id: 'task', group: 'alpha', task: 'Deploy auth flow', lane: 'Backlog', position: 1 },
  };
  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardScrollLeft = 36;
  `);

  context.renderBoard();

  assert.equal(cards.scrollTop, 81);
  assert.equal(tabs.scrollLeft, 36);
  assert.equal(rafQueue.length > 0, true);
});

test('renderBoard preserves hovered-card chrome across rerenders', () => {
  const { context, document } = createBoardHarness({ stubCards: false });
  const panel = document.register('panel-board');
  document.register('board-cards');
  document.register('board-lane-tabs');

  context.state.board_lanes = ['Backlog'];
  context.state.board_tasks = {
    task: { id: 'task', group: 'alpha', task: 'Deploy auth flow', lane: 'Backlog', position: 1 },
  };
  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    boardCardMouseEnter('task');
  `);

  context.renderBoard();

  assert.match(panel.innerHTML, /board-card-hovered/);
});

test('renderBoard keeps the inline add-task composer expanded across rerenders', () => {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);
  runInContext(context, `
    _renderBoardSelectionBar = function() { return ''; };
    _renderBoardCard = function(t) { return '<div class="board-card">' + t.id + '</div>'; };
    _boardScheduleCount = function() { return 0; };
    boardUpdateScrollArrows = function() {};
  `);

  const panel = document.register('panel-board');
  document.register('board-cards');
  const input = document.register('board-add-task-input');
  input.value = 'Whenever the user is writing a task in-line and it wraps onto a second line';
  input.scrollHeight = 72;
  input.style.height = '18px';
  input.selectionStart = input.value.length;
  input.selectionEnd = input.value.length;
  panel.appendChild(input);
  document.activeElement = input;

  context.state.board_lanes = ['Backlog'];
  runInContext(context, `
    _boardSelectedLane = 'Backlog';
    _boardAddingTask = true;
    _boardAddingTaskDraft = '';
  `);

  context.renderBoard();

  assert.equal(runInContext(context, '_boardAddingTaskDraft'), input.value);
  assert.equal(input.focused, true);
  assert.equal(input.selectionStart, input.value.length);
  assert.equal(input.selectionEnd, input.value.length);
  assert.equal(input.style.height, '72px');
});

test('renderBoard shows the inline add-task lane picker with Backlog as the default', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  runInContext(context, `
    _boardSelectedLane = 'Done';
    _boardAddingTask = true;
    _boardAddingTaskLane = '';
  `);

  context.renderBoard();

  assert.match(panel.innerHTML, /boardToggleLaneDropdown\(\)/);
  assert.match(panel.innerHTML, />Backlog &#9662;<\/button>/);
});

test('renderBoard keeps the inline add-task composer in the shared wide strip', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');

  context.state.board_lanes = ['Backlog', 'Done'];
  runInContext(context, `
    _boardSelectedLane = 'Done';
    _boardAddingTask = true;
    _boardAddingTaskLane = '';
  `);

  document.body.classList.add('runtime-embedded');
  panel.clientWidth = 1200;
  context.renderBoard();

  assert.match(panel.innerHTML, /board-wide-add-task-wrap/);
  assert.equal((panel.innerHTML.match(/board-add-task-active/g) || []).length, 1);
  assert.match(panel.innerHTML, /board-add-task-input/);
  assert.match(panel.innerHTML, /boardToggleLaneDropdown\(\)/);
  assert.match(panel.innerHTML, />Backlog &#9662;<\/button>/);
  assert.equal(
    panel.innerHTML.indexOf('board-wide-add-task-wrap') < panel.innerHTML.indexOf('board-wide-grid'),
    true,
  );
});

test('boardStartAddTaskForLane defaults inline task placement to Backlog', () => {
  const { context } = createBoardHarness();
  context.state.board_lanes = ['Backlog', 'Done'];

  runInContext(context, `
    _boardSelectedLane = 'Done';
    _generateDraftId = function() { return 'draft-1'; };
  `);
  context.boardStartAddTaskForLane('Done');

  assert.equal(runInContext(context, '_boardAddingTaskLane'), 'Backlog');
  assert.equal(runInContext(context, '_boardSelectedLane'), 'Done');
});

test('boardSubmitAddTask sends the selected inline lane instead of the viewed lane', () => {
  const { context, document } = createBoardHarness();
  const input = document.register('board-add-task-input');
  input.value = 'Ship docs';

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  runInContext(context, `
    _boardAddingTask = true;
    _boardSelectedLane = 'Done';
    _boardAddingTaskLane = 'In Progress';
  `);

  context.boardSubmitAddTask();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [{
    cmd: 'board_add_task',
    task: 'Ship docs',
    group: 'alpha',
    lane: 'In Progress',
  }]);
  assert.equal(runInContext(context, '_boardAddingTaskLane'), '');
});

test('boardSubmitAddTask clears the inline textarea and keeps the form open for rapid-fire entry', () => {
  const { context, document } = createBoardHarness();
  const panel = document.register('panel-board');
  document.register('board-cards');
  const input = document.register('board-add-task-input');
  input.value = 'foo';
  document.activeElement = input;

  context.state.board_lanes = ['Backlog', 'In Progress', 'Done'];
  runInContext(context, `
    _boardAddingTask = true;
    _boardAddingTaskDraft = 'foo';
    _boardAddingTaskAction = 'oneshot/fix';
    _boardAddingTaskAgent = 'agent-1';
    _boardInlineDraftId = 'draft-1';
    _boardInlineAttachments = [{ url: '/u/1', filename: 'a.png' }];
    _boardSelectedLane = 'Backlog';
    _boardAddingTaskLane = 'Backlog';
  `);

  context.boardSubmitAddTask();

  // Live textarea value cleared synchronously (before the upcoming render),
  // so a blur dispatched during innerHTML replacement cannot reseed the
  // draft from the pre-submit text.
  assert.equal(input.value, '', 'textarea value should be cleared in the DOM');
  assert.equal(runInContext(context, '_boardAddingTaskDraft'), '');
  assert.equal(runInContext(context, '_boardAddingTaskAction'), '');
  assert.equal(runInContext(context, '_boardAddingTaskAgent'), '');
  assert.equal(runInContext(context, '_boardInlineDraftId'), '');
  assert.deepEqual(jsonValue(context, '_boardInlineAttachments'), []);

  // Form stays open so the operator can rapid-fire submit consecutive tasks,
  // and the textarea is re-focused by the render that runs inside submit.
  assert.equal(runInContext(context, '_boardAddingTask'), true);
  assert.equal(input.focused, true, 'textarea should be re-focused after submit');

  // The rendered HTML should contain a fresh, empty textarea — not the
  // just-submitted "foo" text.
  assert.match(panel.innerHTML, /board-add-task-input/);
  assert.doesNotMatch(panel.innerHTML, />foo</);
});

test('boardCancelAddTask keeps the form open when a re-render restored focus to the textarea', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  document.register('board-cards');
  const input = document.register('board-add-task-input');
  input.value = '';
  // Simulate post-render state: the old textarea blurred and the surface-state
  // restore re-focused the (new) textarea synchronously before this timer runs.
  document.activeElement = input;

  context.state.board_lanes = ['Backlog', 'Done'];
  runInContext(context, `
    _boardAddingTask = true;
    _boardAddingTaskDraft = '';
    _boardInlineAttachments = [];
  `);

  context.boardCancelAddTask();

  assert.equal(runInContext(context, '_boardAddingTask'), true,
    'form must stay open when focus was restored by a re-render');
});

test('boardCancelAddTask closes the form when focus moved outside the inline textarea', () => {
  const { context, document } = createBoardHarness();
  document.register('panel-board');
  document.register('board-cards');
  const input = document.register('board-add-task-input');
  input.value = '';
  // Simulate the user clicking elsewhere: focus is NOT on the textarea.
  document.activeElement = new FakeElement('some-other-button');

  context.state.board_lanes = ['Backlog', 'Done'];
  runInContext(context, `
    _boardAddingTask = true;
    _boardAddingTaskDraft = '';
    _boardInlineAttachments = [];
  `);

  context.boardCancelAddTask();

  assert.equal(runInContext(context, '_boardAddingTask'), false,
    'form must close when the user moved focus outside the inline textarea');
});

test('agent history task links open the board and focus the selected task', () => {
  const { context, document } = createAgentHistoryHarness();
  document.register('panel-board');
  document.register('board-cards');
  document.register('panel-templates');
  const detail = document.register('ah-detail-agent-1');
  const focusedCard = new FakeElement('focused-card');
  document.setSelector('.board-card.focused', focusedCard);

  context.state.board_lanes = ['Backlog', 'Done'];
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Review agent output',
      lane: 'Done',
      labels: ['loom:archived'],
      position: 1,
    },
  };
  runInContext(context, `
    _agentHistoryDetail = {
      record: { id: 'agent-1', template: '', worktree_branch: '', created_at: 1 },
      tasks: [{ task_id: 'task-1', task_title: 'Review agent output', outcome: 'done', started_at: 1 }],
      messages: [],
    };
  `);
  context.togglePanel = function(appName) {
    context._activePanelApp = appName;
    context.toggledPanel = appName;
    context.renderBoard();
  };

  runInContext(context, `
    _activePanelApp = 'templates';
    _boardShowSchedules = true;
    _boardSearchQuery = 'worker';
    _boardQuickView = 'recent';
    _boardFilterLabels = ['ops'];
    _boardFilterActions = ['review'];
    _boardFilterAgents = ['agent-1'];
    _boardFilterHealth = ['blocked'];
    _boardShowArchived = false;
  `);

  context.renderAgentHistoryExpanded();
  assert.match(detail.innerHTML, /agentHistoryOpenTask\('task-1'\)/);

  context.agentHistoryOpenTask('task-1');

  assert.equal(context.toggledPanel, 'board');
  assert.equal(runInContext(context, '_boardSelectedLane'), 'Done');
  assert.equal(runInContext(context, '_boardFocusedTask'), 'task-1');
  assert.equal(runInContext(context, '_boardLastSelectedTask'), 'task-1');
  assert.equal(runInContext(context, '_boardShowSchedules'), false);
  assert.equal(runInContext(context, '_boardSearchQuery'), '');
  assert.equal(runInContext(context, '_boardQuickView'), '');
  assert.deepEqual(jsonValue(context, '_boardFilterLabels'), []);
  assert.deepEqual(jsonValue(context, '_boardFilterActions'), []);
  assert.deepEqual(jsonValue(context, '_boardFilterAgents'), []);
  assert.deepEqual(jsonValue(context, '_boardFilterHealth'), []);
  assert.equal(runInContext(context, '_boardShowArchived'), true);
  assert.equal(focusedCard.scrollIntoViewOptions.block, 'nearest');
});

test('agent history renders answered outcomes for weaver follow-up tasks', () => {
  const { context, document } = createAgentHistoryHarness();
  const detail = document.register('ah-detail-agent-1');

  runInContext(context, `
    _agentHistoryDetail = {
      record: { id: 'agent-1', template: '', worktree_branch: '', created_at: 1 },
      tasks: [{
        task_id: 'task-1',
        task_title: 'Weaver: Need rebase status',
        outcome: 'answered',
        started_at: 1,
        completed_at: 2
      }],
      messages: [],
    };
  `);

  context.renderAgentHistoryExpanded();

  assert.match(detail.innerHTML, /ah-outcome-answered/);
  assert.match(detail.innerHTML, /answered/);
});

test('renderEvents restores focused search input value and caret across rerenders', () => {
  const { context, document } = createEventsHarness();
  const panel = document.register('panel-events');
  panel.setQuerySelector('.events-log', null);
  panel.setQuerySelector('.events-attention', null);
  panel.setQuerySelectorAll('.events-resolve-textarea', []);
  const input = new FakeElement();
  input.classList.add('events-search-input');
  input.value = 'stuck';
  input.selectionStart = 2;
  input.selectionEnd = 5;
  panel.appendChild(input);
  panel.setQuerySelector('.events-search-input', input);
  document.activeElement = input;

  context.state.panel_events = [];

  runInContext(context, `_eventsSearchQuery = '';`);
  context.renderEvents();

  assert.equal(input.focused, true);
  assert.equal(input.value, 'stuck');
  assert.equal(input.selectionStart, 2);
  assert.equal(input.selectionEnd, 5);
});

test('events scroll anchors keep the same log entry visible when newer events arrive', () => {
  const { context, document } = createEventsHarness({ stubRenderers: false });
  const panel = document.register('panel-events');
  const log = new FakeElement('events-log');
  const search = new FakeElement();
  search.classList.add('events-search-input');
  panel.setQuerySelector('.events-log', log);
  panel.setQuerySelector('.events-attention', null);
  panel.setQuerySelector('.events-search-input', search);
  panel.setQuerySelectorAll('.events-resolve-textarea', []);

  let renderPhase = 'before';
  const oldEntry = new FakeElement();
  oldEntry.dataset.eventId = '100';
  oldEntry.offsetTop = 100;
  oldEntry.offsetHeight = 24;
  const newEntry = new FakeElement();
  newEntry.dataset.eventId = '101';
  newEntry.offsetTop = 0;
  newEntry.offsetHeight = 24;
  const anchoredEntry = new FakeElement();
  anchoredEntry.dataset.eventId = '100';
  anchoredEntry.offsetTop = 124;
  anchoredEntry.offsetHeight = 24;
  log.scrollTop = 110;
  log.querySelectorAll = function(selector) {
    if (selector !== '.events-entry') return [];
    return renderPhase === 'before' ? [oldEntry] : [newEntry, anchoredEntry];
  };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() { return this._innerHTML; },
    set(value) {
      this._innerHTML = value;
      this.children = [];
      renderPhase = 'after';
    },
  });

  context.state.panel_events = [
    { id: 100, kind: 'agent_progress', message: 'Still working', group: 'alpha', timestamp: 1 },
    { id: 101, kind: 'agent_progress', message: 'More work', group: 'alpha', timestamp: 2 },
  ];

  context.renderEvents();

  assert.equal(log.scrollTop, 134);
});

test('events scroll anchors preserve the attention list while asks are visible', () => {
  const { context, document } = createEventsHarness({ stubRenderers: false });
  const panel = document.register('panel-events');
  const attention = new FakeElement('events-attention');
  const log = new FakeElement('events-log');
  const search = new FakeElement();
  search.classList.add('events-search-input');
  panel.setQuerySelector('.events-attention', attention);
  panel.setQuerySelector('.events-log', log);
  panel.setQuerySelector('.events-search-input', search);
  panel.setQuerySelectorAll('.events-resolve-textarea', []);

  let renderPhase = 'before';
  const oldCard = new FakeElement();
  oldCard.dataset.itemId = 'ask-1';
  oldCard.offsetTop = 60;
  oldCard.offsetHeight = 80;
  const newCard = new FakeElement();
  newCard.dataset.itemId = 'ask-0';
  newCard.offsetTop = 0;
  newCard.offsetHeight = 80;
  const anchoredCard = new FakeElement();
  anchoredCard.dataset.itemId = 'ask-1';
  anchoredCard.offsetTop = 80;
  anchoredCard.offsetHeight = 80;
  attention.scrollTop = 70;
  attention.querySelectorAll = function(selector) {
    if (selector !== '.events-attention-card') return [];
    return renderPhase === 'before' ? [oldCard] : [newCard, anchoredCard];
  };
  log.querySelectorAll = function() { return []; };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() { return this._innerHTML; },
    set(value) {
      this._innerHTML = value;
      this.children = [];
      renderPhase = 'after';
    },
  });

  context.state.board_tasks = {
    'ask-0': {
      id: 'ask-0',
      group: 'alpha',
      task: 'Newest question',
      lane: 'Backlog',
      labels: ['loom:human'],
      created_at: '2026-04-10T10:05:00+00:00',
    },
    'ask-1': {
      id: 'ask-1',
      group: 'alpha',
      task: 'Original question',
      lane: 'Backlog',
      labels: ['loom:human'],
      created_at: '2026-04-10T10:00:00+00:00',
    },
  };
  context.state.panel_events = [];

  context.renderEvents();

  assert.equal(attention.scrollTop, 90);
});

test('events entries preserve expansion state by event id across rerenders', () => {
  const { context } = createEventsHarness({ stubRenderers: false });

  runInContext(context, `_eventsExpandedEntries = { '7': true };`);
  const html = context._renderEventEntry({
    id: 7,
    kind: 'agent_error',
    message: 'Boom',
    timestamp: 1,
  }, 12);

  assert.match(html, /events-entry .*expanded/);
  assert.match(html, /data-event-id="7"/);
  assert.match(html, /eventsToggleEntry\('7'\)/);
});

test('context panel requests scoped memory and renders provenance details', () => {
  const { context, document } = createContextHarness();
  const panel = document.register('panel-context');
  panel.clientWidth = 1100;
  const list = new FakeElement('context-list');
  panel.setQuerySelector('#context-list', list);

  context.selectedAgentId = 'agent-1';
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Worker', group: 'alpha', cell_type: 'agent' },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Ship the context browser',
      group: 'alpha',
      lane: 'In Progress',
      agent_id: 'agent-1',
      pipeline_root_id: 'root-1',
    },
    'root-1': {
      id: 'root-1',
      task: 'Release memory tools',
      group: 'alpha',
      lane: 'In Progress',
    },
  };
  runInContext(context, `
    _contextEntries = [{
      id: 'mem-1',
      title: 'Keep pinned entries visible',
      content: 'Pin major decisions so orchestrators can see them first.',
      entry_type: 'decision',
      pinned: true,
      scope_kind: 'task',
      scope_ref: 'task-1',
      source_kind: 'agent',
      source_id: 'agent-1',
      source_name: 'Worker',
      created_at: 100,
      updated_at: 120,
      links: [
        { target_kind: 'task', target_ref: 'task-1' },
        { target_kind: 'agent', target_ref: 'agent-1' },
      ],
    }];
    _contextSelectedId = 'mem-1';
    _contextLastQueryKey = JSON.stringify(_contextBuildListQuery());
  `);

  context.renderContextPanel();

  assert.match(panel.innerHTML, /Context/);
  assert.match(panel.innerHTML, /btn-primary btn-sm" onclick="contextOpenCreate\(\)">New Note/);
  assert.doesNotMatch(panel.innerHTML, />Refresh</);
  assert.doesNotMatch(panel.innerHTML, />Promote Task</);
  assert.match(panel.innerHTML, /context-splitter/);
  assert.match(panel.innerHTML, /Keep pinned entries visible/);
  assert.match(panel.innerHTML, /Pinned/);
  assert.match(panel.innerHTML, /Task: Ship the context browser/);
  assert.match(panel.innerHTML, /Source: Worker/);
  assert.match(panel.innerHTML, /agent-1/);
  assert.match(panel.innerHTML, /task: Ship the context browser/);
});

test('context panel renders compacted summaries as read-only entries', () => {
  const { context, document } = createContextHarness();
  const panel = document.register('panel-context');
  panel.clientWidth = 1100;
  const list = new FakeElement('context-list');
  panel.setQuerySelector('#context-list', list);

  runInContext(context, `
    _contextEntries = [{
      id: 'summary:mem-1,mem-2',
      title: '2 older transient entries',
      content: 'Covers 2 older transient entries (note×2).',
      entry_type: 'summary',
      retention_kind: 'summary',
      synthetic: true,
      pinned: false,
      scope_kind: 'group',
      scope_ref: 'alpha',
      source_kind: 'system',
      source_name: 'Loom',
      created_at: 100,
      updated_at: 120,
      links: [],
    }];
    _contextSelectedId = 'summary:mem-1,mem-2';
    _contextLastQueryKey = JSON.stringify(_contextBuildListQuery());
  `);

  context.renderContextPanel();

  assert.match(panel.innerHTML, /Summary/);
  assert.doesNotMatch(panel.innerHTML, /contextTogglePin/);
  assert.doesNotMatch(panel.innerHTML, /contextEditEntry/);
});

test('context panel opens a new linked manual note for the current task', () => {
  const { context, document } = createContextHarness();
  document.register('panel-context');
  context.selectedAgentId = 'agent-1';
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Worker', group: 'alpha', cell_type: 'agent' },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Capture orchestration guidance',
      description: 'Document the operating constraints for memory notes.',
      group: 'alpha',
      lane: 'In Progress',
      agent_id: 'agent-1',
      pipeline_root_id: 'root-1',
    },
  };

  context.contextOpenCreate();
  runInContext(context, `
    contextUpdateEditor('title', 'Capture orchestration guidance');
    contextUpdateEditor('content', 'Document the operating constraints for memory notes.');
  `);
  context.contextSaveEditor();

  assert.deepEqual(jsonValue(context, 'sendCalls[sendCalls.length - 1]'), {
    cmd: 'memory_publish',
    title: 'Capture orchestration guidance',
    content: 'Document the operating constraints for memory notes.',
    entry_type: 'note',
    scope_kind: 'task',
    scope_ref: 'task-1',
    pinned: false,
    source_kind: 'manual',
    link_targets: [
      { target_kind: 'task', target_ref: 'task-1' },
      { target_kind: 'agent', target_ref: 'agent-1' },
    ],
  });
});

test('context panel uses a compact master-detail flow on narrow panels', () => {
  const { context, document } = createContextHarness();
  const panel = document.register('panel-context');
  panel.clientWidth = 420;

  runInContext(context, `
    _contextEntries = [{
      id: 'mem-1',
      title: 'Keep the detail pane readable',
      content: 'Use a separate detail view on narrow widths.',
      entry_type: 'note',
      pinned: false,
      scope_kind: 'group',
      scope_ref: 'alpha',
      source_kind: 'manual',
      source_id: 'note-1',
      created_at: 100,
      updated_at: 120,
      links: [],
    }];
    _contextSelectedId = 'mem-1';
    _contextLastQueryKey = JSON.stringify(_contextBuildListQuery());
    _contextCompactDetailOpen = false;
  `);

  context.renderContextPanel();
  assert.match(panel.innerHTML, /context-list-compact/);
  assert.doesNotMatch(panel.innerHTML, /Back to List/);

  context.contextSelectEntry('mem-1');
  assert.match(panel.innerHTML, /context-detail-compact/);
  assert.match(panel.innerHTML, /Back to List/);
  assert.match(panel.innerHTML, /Keep the detail pane readable/);

  context.contextShowList();
  assert.match(panel.innerHTML, /context-list-compact/);
});

test('agent clicks rescope the board to the clicked agent group immediately', () => {
  const { context } = createSelectionHarness();

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alpha', group: 'alpha', cell_type: 'agent' },
    'agent-2': { id: 'agent-2', name: 'Beta', group: 'beta', cell_type: 'agent' },
    'term-2': { id: 'term-2', name: 'Beta term', group: 'beta', cell_type: 'terminal', parent_id: 'agent-2' },
  };
  runInContext(context, `
    selectedAgentId = 'agent-1';
    focusedItemId = 'agent-1';
  `);

  context.onAgentClick('agent-2');
  assert.equal(jsonValue(context, 'selectedAgentId'), 'agent-2');
  assert.equal(jsonValue(context, '_currentGroup()'), 'beta');
  assert.equal(jsonValue(context, 'renderCalls.board'), 1);
  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'select_agent',
    id: 'agent-2',
  });

  context.focusAgent('term-2');
  assert.equal(jsonValue(context, 'selectedAgentId'), 'agent-2');
  assert.equal(jsonValue(context, '_currentGroup()'), 'beta');
  assert.equal(jsonValue(context, 'renderCalls.board'), 1);
});

test('agent clicks still focus immediately when focus-on-click is enabled', () => {
  const { context } = createSelectionHarness();

  context.state.global_settings = { focus_on_click: true };
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alpha', group: 'alpha', cell_type: 'agent' },
    'agent-2': { id: 'agent-2', name: 'Beta', group: 'beta', cell_type: 'agent' },
  };

  context.onAgentClick('agent-2');

  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls)), [
    { cmd: 'select_agent', id: 'agent-2' },
    { cmd: 'focus_agent', id: 'agent-2' },
  ]);
});

test('engineer context menu exposes authorized dismiss and rehire controls', async () => {
  const { context, document } = createSelectionHarness();
  const menu = document.register('ctx-menu');
  context.showConfirm = function() { return Promise.resolve(true); };
  context.state.active_session_id = 'sess-arch-a';
  context.state.group_settings = { alpha: {} };
  context.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Planner A',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      session_id: 'sess-arch-a',
    },
    'arch-b': {
      id: 'arch-b',
      name: 'Planner B',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      session_id: 'sess-arch-b',
    },
    'eng-a': {
      id: 'eng-a',
      name: 'Builder A',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-a',
      status: 'running',
    },
    'eng-b': {
      id: 'eng-b',
      name: 'Builder B',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-b',
      status: 'running',
    },
    'worker-a': {
      id: 'worker-a',
      name: 'Worker A',
      kind: 'worker',
      group: 'alpha',
      cell_type: 'agent',
      owner_engineer_id: 'eng-a',
      status: 'running',
    },
  };

  const evt = {
    preventDefault() {},
    stopPropagation() {},
    clientX: 12,
    clientY: 24,
  };

  context.onCellContextMenu(evt, 'eng-a');
  assert.match(menu.innerHTML, /Dismiss/);
  assert.doesNotMatch(menu.innerHTML, /Rehire/);

  context.onCellContextMenu(evt, 'eng-b');
  assert.doesNotMatch(menu.innerHTML, /Dismiss/);
  assert.doesNotMatch(menu.innerHTML, /Rehire/);

  context.state.agents['eng-a'].dismissed_at = 456;
  context.state.agents['eng-a'].status = 'stopped';
  context.onCellContextMenu(evt, 'eng-a');
  assert.match(menu.innerHTML, /Rehire/);
  assert.doesNotMatch(menu.innerHTML, /Dismiss/);
  assert.doesNotMatch(menu.innerHTML, /Relaunch/);
  assert.doesNotMatch(menu.innerHTML, /Restart/);

  context.state.active_session_id = '';
  context.onCellContextMenu(evt, 'eng-b');
  assert.match(menu.innerHTML, /Dismiss/);

  context.state.active_session_id = 'sess-arch-a';
  context.state.agents['eng-a'].dismissed_at = 0;
  context.state.agents['eng-a'].status = 'running';
  await context.dismissEngineer('eng-a');
  context.state.agents['eng-a'].dismissed_at = 456;
  context.rehireEngineer('eng-a');

  assert.deepEqual(JSON.parse(JSON.stringify(context.sendCalls.slice(-2))), [
    {
      cmd: 'architect_engineer_dismiss',
      engineer_id: 'eng-a',
      architect_id: 'arch-a',
      reason: 'Dismissed from UI',
    },
    {
      cmd: 'architect_engineer_rehire',
      engineer_id: 'eng-a',
      architect_id: 'arch-a',
    },
  ]);
});

test('embedded terminal selection clears stale agent selection for standalone terminals', () => {
  const { context } = createSelectionHarness();
  context.isEmbeddedTerminalMode = function() { return true; };

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alpha', group: 'alpha', cell_type: 'agent' },
    'term-root': { id: 'term-root', name: 'Shell Root', group: 'alpha', cell_type: 'terminal' },
  };
  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'agent-1';
    focusedItemId = 'agent-1';
  `);

  context.focusAgent('term-root');

  assert.equal(jsonValue(context, 'selectedAgentId'), '');
  assert.equal(jsonValue(context, 'selectedTerminalId'), 'term-root');
  assert.equal(jsonValue(context, 'focusedItemId'), 'term-root');
});

test('embedded terminal selection returns keyboard focus to the terminal workspace', () => {
  const { context } = createSelectionHarness();
  context.isEmbeddedTerminalMode = function() { return true; };
  context.renderTerminalWorkspace = function() {};
  context.focusEmbeddedTerminalWorkspaceCalls = 0;
  context.focusEmbeddedTerminalWorkspace = function(force) {
    context.focusEmbeddedTerminalWorkspaceCalls += force ? 1 : 0;
    return true;
  };

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alpha', group: 'alpha', cell_type: 'agent', session_id: 'sess-agent' },
    'term-root': { id: 'term-root', name: 'Shell Root', group: 'alpha', cell_type: 'terminal', session_id: 'sess-root' },
  };

  context.focusAgent('term-root');

  assert.equal(context.focusEmbeddedTerminalWorkspaceCalls, 1);
});

test('classic terminal selection keeps the current agent context on the toolbelt path', () => {
  const { context } = createSelectionHarness();
  context.focusEmbeddedTerminalWorkspaceCalls = 0;
  context.focusEmbeddedTerminalWorkspace = function() {
    context.focusEmbeddedTerminalWorkspaceCalls += 1;
  };

  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Alpha', group: 'alpha', cell_type: 'agent' },
    'term-root': { id: 'term-root', name: 'Shell Root', group: 'alpha', cell_type: 'terminal' },
  };
  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'agent-1';
    focusedItemId = 'agent-1';
  `);

  context.focusAgent('term-root');

  assert.equal(jsonValue(context, 'selectedAgentId'), 'agent-1');
  assert.equal(jsonValue(context, 'selectedTerminalId'), 'term-root');
  assert.equal(jsonValue(context, 'focusedItemId'), 'term-root');
  assert.equal(jsonValue(context, 'renderCalls.main'), 0);
  assert.equal(context.focusEmbeddedTerminalWorkspaceCalls, 0);
});

test('clicking empty main-grid space clears focusedItemId and re-renders the agent panel surface', () => {
  const { sandbox, document } = createSandbox();
  sandbox.renderCalls = { main: 0, agent: 0 };
  const main = document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');
  runInContext(context, `
    var dragInProgress = false;
    var focusedItemId = 'agent-1';
    render = function() {
      renderCalls.main++;
      if (typeof renderAgentPanel === 'function') renderAgentPanel();
    };
    renderAgentPanel = function() { renderCalls.agent++; };
  `);

  context.setupDrag();
  main.listeners.click({
    target: {
      closest() { return null; },
    },
  });

  assert.equal(jsonValue(context, 'focusedItemId'), null);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    agent: 1,
  });
});

test('terminal workspace stays inert when embedded runtime is disabled', () => {
  const { context, document, sockets } = createEmbeddedTerminalHarness();
  const workspace = document.getElementById('terminal-workspace');

  runInContext(context, `
    state.runtime = { embedded_terminal: false };
    window.__disposeFlags = {};
    document.getElementById('terminal-workspace').innerHTML = '<div class="terminal-shell">stale</div>';
    document.getElementById('terminal-workspace').classList.add('active');
    _embeddedTerminal = {
      dispose: function() { window.__disposeFlags.terminalDisposed = true; }
    };
    _embeddedTerminalFit = { active: true };
    _embeddedTerminalDataHandler = {
      dispose: function() { window.__disposeFlags.dataDisposed = true; }
    };
    _embeddedTerminalWs = {
      close: function() { window.__disposeFlags.wsClosed = true; }
    };
    _embeddedTerminalResizeObserver = {
      disconnect: function() { window.__disposeFlags.observerDisconnected = true; }
    };
    _embeddedTerminalSessionKey = 'term-1:session-old';
  `);

  context.renderTerminalWorkspace();

  assert.equal(workspace.innerHTML, '');
  assert.equal(workspace.classList.contains('active'), false);
  assert.deepEqual(jsonValue(context, 'window.__disposeFlags'), {
    observerDisconnected: true,
    wsClosed: true,
    dataDisposed: true,
    terminalDisposed: true,
  });
  assert.equal(sockets.length, 0);
  assert.equal(jsonValue(context, '!!_embeddedTerminal'), false);
  assert.equal(jsonValue(context, '!!_embeddedTerminalWs'), false);
  assert.equal(jsonValue(context, '_embeddedTerminalSessionKey'), '');
});

test('embedded terminal compose renders only for standalone runtime and preserves drafts across rerenders', () => {
  const { context, document, sandbox } = createEmbeddedTerminalHarness({
    loadRenderHelpers: true,
  });
  const dom = attachTerminalWorkspaceDom(document);
  document.body.classList.add('runtime-embedded');
  sandbox.state.runtime = { embedded_terminal: true };
  sandbox.state.groups = { alpha: ['agent-1'] };
  sandbox.state.group_settings = { alpha: {} };
  sandbox.state.children = { 'agent-1': [] };
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Builder',
      group: 'alpha',
      cell_type: 'agent',
      session_id: 'sess-1',
      status: 'running',
      current_path: '/repo',
    },
  };

  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'agent-1';
    _embeddedTerminalSessionKey = 'agent-1:sess-1';
    renderTerminalWorkspace();
  `);

  assert.match(dom.compose.innerHTML, /class="terminal-compose"/);
  assert.match(dom.compose.innerHTML, /Send a message to Builder/);

  const input = document.register('terminal-compose-input-agent-1');
  input.classList.add('terminal-compose-input');
  input.dataset.cellId = 'agent-1';
  input.value = 'Draft survives a digest update';
  input.selectionStart = 6;
  input.selectionEnd = 14;
  const button = document.register('terminal-compose-submit-agent-1');
  button.classList.add('terminal-compose-submit');
  dom.compose.appendChild(input);
  dom.compose.appendChild(button);
  dom.compose.setQuerySelector('.terminal-compose-input', input);
  dom.compose.setQuerySelector('.terminal-compose-submit', button);
  dom.workspace.setQuerySelector('.terminal-compose-input', input);
  document.activeElement = input;

  runInContext(context, `
    terminalComposeInput(document.getElementById('terminal-compose-input-agent-1'));
    state.agents['agent-1'].activity_detail = 'Digest arrived';
    renderTerminalWorkspace();
  `);

  assert.equal(input.focused, true);
  assert.equal(input.value, 'Draft survives a digest update');
  assert.equal(input.selectionStart, 6);
  assert.equal(input.selectionEnd, 14);
  assert.equal(button.disabled, false);
  assert.equal(
    jsonValue(context, `_terminalComposeDrafts['agent-1']`),
    'Draft survives a digest update',
  );

  runInContext(context, `
    state.runtime = { embedded_terminal: false };
    document.getElementById('terminal-workspace').innerHTML = '<form class="terminal-compose">stale</form>';
    renderTerminalWorkspace();
  `);

  assert.equal(dom.workspace.innerHTML, '');
  assert.doesNotMatch(dom.workspace.innerHTML, /terminal-compose/);
});

test('embedded terminal compose submits on Enter, allows Shift+Enter, and clears on Escape', () => {
  const { context, document, sandbox } = createEmbeddedTerminalHarness({
    loadRenderHelpers: true,
  });
  const dom = attachTerminalWorkspaceDom(document);
  document.body.classList.add('runtime-embedded');
  sandbox.state.runtime = { embedded_terminal: true };
  sandbox.state.groups = { alpha: ['agent-1'] };
  sandbox.state.group_settings = { alpha: {} };
  sandbox.state.children = { 'agent-1': [] };
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Builder',
      group: 'alpha',
      cell_type: 'agent',
      session_id: 'sess-1',
      status: 'running',
    },
  };
  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'agent-1';
    _embeddedTerminalSessionKey = 'agent-1:sess-1';
    renderTerminalWorkspace();
  `);

  const input = document.register('terminal-compose-input-agent-1');
  input.classList.add('terminal-compose-input');
  input.dataset.cellId = 'agent-1';
  const button = document.register('terminal-compose-submit-agent-1');
  button.classList.add('terminal-compose-submit');
  dom.compose.appendChild(input);
  dom.compose.appendChild(button);
  dom.compose.setQuerySelector('.terminal-compose-input', input);
  dom.compose.setQuerySelector('.terminal-compose-submit', button);
  dom.workspace.setQuerySelector('.terminal-compose-input', input);

  input.value = 'hello agent';
  runInContext(context, `terminalComposeInput(document.getElementById('terminal-compose-input-agent-1'));`);
  const enterEvt = {
    key: 'Enter',
    shiftKey: false,
    preventDefaultCalled: false,
    stopPropagationCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
    stopPropagation() { this.stopPropagationCalled = true; },
  };
  context.__enterEvt = enterEvt;
  runInContext(context, `terminalComposeKeydown(__enterEvt, 'agent-1');`);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [{
    cmd: 'send_user_message',
    cell_id: 'agent-1',
    text: 'hello agent',
  }]);
  assert.equal(enterEvt.preventDefaultCalled, true);
  assert.equal(enterEvt.stopPropagationCalled, true);
  assert.equal(input.value, '');
  assert.equal(button.disabled, true);

  input.value = 'line one';
  runInContext(context, `terminalComposeInput(document.getElementById('terminal-compose-input-agent-1'));`);
  const shiftEnterEvt = {
    key: 'Enter',
    shiftKey: true,
    preventDefaultCalled: false,
    stopPropagationCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
    stopPropagation() { this.stopPropagationCalled = true; },
  };
  context.__shiftEnterEvt = shiftEnterEvt;
  runInContext(context, `terminalComposeKeydown(__shiftEnterEvt, 'agent-1');`);

  assert.equal(sandbox.sendCalls.length, 1);
  assert.equal(shiftEnterEvt.preventDefaultCalled, false);
  assert.equal(input.value, 'line one');

  const escapeEvt = {
    key: 'Escape',
    shiftKey: false,
    preventDefaultCalled: false,
    stopPropagationCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
    stopPropagation() { this.stopPropagationCalled = true; },
  };
  context.__escapeEvt = escapeEvt;
  runInContext(context, `terminalComposeKeydown(__escapeEvt, 'agent-1');`);

  assert.equal(escapeEvt.preventDefaultCalled, true);
  assert.equal(escapeEvt.stopPropagationCalled, true);
  assert.equal(input.value, '');
  assert.equal(button.disabled, true);
});

test('embedded terminal ignores stale websocket output after a relaunch session swap', () => {
  const { context, sockets, terminals } = createEmbeddedTerminalHarness();
  const firstSurface = new FakeElement('surface-1');
  const secondSurface = new FakeElement('surface-2');
  context.__surface1 = firstSurface;
  context.__surface2 = secondSurface;

  runInContext(
    context,
    `_connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-old' }, __surface1);`,
  );
  const oldSocket = sockets[0];

  runInContext(
    context,
    `_connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-new' }, __surface2);`,
  );
  const currentSocket = sockets[1];
  const currentTerminal = terminals[1];

  assert.equal(oldSocket.closeCalled, true);
  assert.equal(oldSocket.onmessage, null);

  currentSocket.onmessage({
    data: JSON.stringify({
      type: 'snapshot',
      cell_id: 'term-1',
      session_id: 'session-new',
      data: 'clean prompt',
    }),
  });
  assert.deepEqual(currentTerminal.writes, ['clean prompt']);

  currentSocket.onmessage({
    data: JSON.stringify({
      type: 'output',
      cell_id: 'term-1',
      session_id: 'session-old',
      data: 'autoload -Uz add-zsh-hook',
    }),
  });
  assert.deepEqual(currentTerminal.writes, ['clean prompt']);

  currentSocket.onmessage({
    data: JSON.stringify({
      type: 'output',
      cell_id: 'term-1',
      session_id: 'session-new',
      data: '\nready',
    }),
  });
  assert.deepEqual(currentTerminal.writes, ['clean prompt', '\nready']);
});

test('embedded terminal auto-focuses new sessions when standalone mode is active', () => {
  const { context, sockets, terminals } = createEmbeddedTerminalHarness();
  const surface = new FakeElement('surface');
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  sockets[0].onopen();

  assert.equal(terminals[0].focusCount, 1);
});

test('embedded terminal dragover accepts file drags before the browser exposes dropped files', () => {
  const { context, status } = createEmbeddedTerminalHarness();
  const surface = new FakeElement('surface');
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  const dragOverEvent = {
    dataTransfer: {
      types: ['Files'],
      items: [{ kind: 'file', type: 'image/png' }],
      files: [],
    },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  surface.listeners.dragover(dragOverEvent);

  assert.equal(dragOverEvent.preventDefaultCalled, true);
  assert.equal(surface.classList.contains('terminal-drop-target'), true);
  assert.equal(status.classList.contains('terminal-drop-target'), true);
});

test('embedded terminal image drops upload files and paste quoted paths into the CLI buffer', async () => {
  const uploads = [];
  const uploadResponses = [
    { ok: true, data: [{ path: '/tmp/with space/first.png' }] },
    { ok: true, data: [{ path: "/tmp/O'Reilly/second.jpg" }] },
  ];

  class FakeFormData {
    constructor() {
      this.entries = [];
    }

    append(name, value) {
      this.entries.push([name, value]);
    }
  }

  const { context, sockets, terminals, status } = createEmbeddedTerminalHarness({
    FormData: FakeFormData,
    fetch(url, options) {
      uploads.push({ url, entries: options.body.entries });
      return Promise.resolve({
        json() {
          return Promise.resolve(uploadResponses.shift());
        },
      });
    },
  });
  const surface = new FakeElement('surface');
  const imageOne = { type: 'image/png', name: 'first.png' };
  const nonImage = { type: 'application/pdf', name: 'skip.pdf' };
  const imageTwo = { type: 'image/jpeg', name: 'second.jpg' };
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  const dragOverEvent = {
    dataTransfer: {
      types: ['Files'],
      items: [{ kind: 'file', type: 'image/png' }],
      files: [],
    },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  surface.listeners.dragover(dragOverEvent);
  assert.equal(dragOverEvent.preventDefaultCalled, true);
  assert.equal(surface.classList.contains('terminal-drop-target'), true);
  assert.equal(status.classList.contains('terminal-drop-target'), true);

  const dropEvent = {
    dataTransfer: { files: [imageOne, nonImage, imageTwo] },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  await surface.listeners.drop(dropEvent);

  assert.equal(dropEvent.preventDefaultCalled, true);
  assert.equal(surface.classList.contains('terminal-drop-target'), false);
  assert.equal(status.classList.contains('terminal-drop-target'), false);
  assert.equal(uploads.length, 2);
  assert.deepEqual(uploads.map((upload) => upload.url), ['/api/upload', '/api/upload']);
  assert.deepEqual(uploads[0].entries, [
    ['task_id', 'terminal-drop-term-1-session-1'],
    ['file', imageOne],
  ]);
  assert.deepEqual(uploads[1].entries, [
    ['task_id', 'terminal-drop-term-1-session-1'],
    ['file', imageTwo],
  ]);
  assert.deepEqual(sockets[0].sent[sockets[0].sent.length - 1], {
    type: 'input',
    data: "'/tmp/with space/first.png' '/tmp/O'\"'\"'Reilly/second.jpg' ",
  });
  assert.equal(terminals[0].focusCount, 1);
});

test('embedded terminal ignores non-image file drops', async () => {
  let fetchCalls = 0;
  const { context, sockets, status } = createEmbeddedTerminalHarness({
    FormData: function FakeFormData() {},
    fetch() {
      fetchCalls += 1;
      return Promise.resolve({
        json() {
          return Promise.resolve({ ok: true, data: [] });
        },
      });
    },
  });
  const surface = new FakeElement('surface');
  const pdf = { type: 'application/pdf', name: 'notes.pdf' };
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  const initialSentCount = sockets[0].sent.length;
  const dragOverEvent = {
    dataTransfer: {
      types: ['Files'],
      items: [{ kind: 'file', type: 'application/pdf' }],
      files: [],
    },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  surface.listeners.dragover(dragOverEvent);
  assert.equal(dragOverEvent.preventDefaultCalled, true);
  assert.equal(surface.classList.contains('terminal-drop-target'), false);
  assert.equal(status.classList.contains('terminal-drop-target'), false);

  const dropEvent = {
    dataTransfer: { files: [pdf] },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  await surface.listeners.drop(dropEvent);

  assert.equal(dropEvent.preventDefaultCalled, true);
  assert.equal(fetchCalls, 0);
  assert.equal(sockets[0].sent.length, initialSentCount);
});

test('embedded terminal ignores non-file drags', () => {
  const { context, status } = createEmbeddedTerminalHarness();
  const surface = new FakeElement('surface');
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  const dragOverEvent = {
    dataTransfer: {
      types: ['text/plain'],
      items: [{ kind: 'string', type: 'text/plain' }],
      files: [],
    },
    preventDefaultCalled: false,
    preventDefault() { this.preventDefaultCalled = true; },
  };
  surface.listeners.dragover(dragOverEvent);

  assert.equal(dragOverEvent.preventDefaultCalled, false);
  assert.equal(surface.classList.contains('terminal-drop-target'), false);
  assert.equal(status.classList.contains('terminal-drop-target'), false);
});

test('embedded terminal removes drop listeners when disposed', () => {
  const { context } = createEmbeddedTerminalHarness();
  const surface = new FakeElement('surface');
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  assert.equal(typeof surface.listeners.dragenter, 'function');
  assert.equal(typeof surface.listeners.dragover, 'function');
  assert.equal(typeof surface.listeners.dragleave, 'function');
  assert.equal(typeof surface.listeners.drop, 'function');

  runInContext(context, `_disposeEmbeddedTerminal();`);

  assert.equal(surface.listeners.dragenter, undefined);
  assert.equal(surface.listeners.dragover, undefined);
  assert.equal(surface.listeners.dragleave, undefined);
  assert.equal(surface.listeners.drop, undefined);
});

test('embedded terminal does not steal focus from an active editor input', () => {
  const { context, document, sockets, terminals } = createEmbeddedTerminalHarness();
  const surface = new FakeElement('surface');
  const boardSearch = new FakeElement('board-search-input');
  boardSearch.tagName = 'INPUT';
  document.activeElement = boardSearch;
  context.__surface = surface;

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    _connectEmbeddedTerminal({ id: 'term-1', session_id: 'session-1' }, __surface);
  `);

  sockets[0].onopen();

  assert.equal(terminals[0].focusCount || 0, 0);
});

test('ws invalidation rerenders the context panel for task updates', () => {
  const { context } = createWsRenderHarness();
  runInContext(context, `_activePanelApp = 'context';`);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_upsert',
      id: 'task-1',
      task: 'Sync memory',
      group: 'alpha',
      lane: 'In Progress',
    }],
  });

  assert.equal(jsonValue(context, 'renderCalls.context'), 1);
});

test('ws invalidation skips rerendering the active board for off-group task updates', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'board';

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_upsert',
      id: 'task-1',
      task: 'Sync memory',
      group: 'beta',
      lane: 'In Progress',
    }],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('ws invalidation skips rerendering the active events panel for off-group task updates', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'events';

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_upsert',
      id: 'task-1',
      task: 'Sync memory',
      group: 'beta',
      lane: 'In Progress',
    }],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('ws invalidation skips rerendering the active board for off-group task removals', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'board';
  runInContext(context, `
    state.board_tasks = {
      'task-1': {
        id: 'task-1',
        task: 'Sync memory',
        group: 'beta',
        lane: 'In Progress',
      },
    };
  `);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_remove',
      id: 'task-1',
    }],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('ws invalidation skips rerendering the active events panel for off-group task removals', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'events';
  runInContext(context, `
    state.board_tasks = {
      'task-1': {
        id: 'task-1',
        task: 'Sync memory',
        group: 'beta',
        lane: 'In Progress',
      },
    };
  `);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_remove',
      id: 'task-1',
    }],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('task completion deltas trigger the done flourish only for live lane transitions', () => {
  const { context } = createWsRenderHarness();
  runInContext(context, `
    doneFlourishCalls = [];
    _startAgentDoneFlourish = function(agentId, label) {
      doneFlourishCalls.push({ agentId: agentId, label: label });
    };
    state.board_tasks = {
      'task-1': {
        id: 'task-1',
        task: 'Ship polish',
        group: 'alpha',
        agent_id: 'agent-1',
        lane: 'In Progress',
      },
    };
  `);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'task_upsert',
      id: 'task-1',
      task: 'Ship polish',
      group: 'alpha',
      agent_id: 'agent-1',
      lane: 'Done',
    }],
  });
  context._handleDelta({
    seq: 2,
    ops: [{
      op: 'task_upsert',
      id: 'task-1',
      task: 'Ship polish',
      group: 'alpha',
      agent_id: 'agent-1',
      lane: 'Done',
      status: 'reported',
    }],
  });
  context._handleDelta({
    seq: 3,
    ops: [{
      op: 'task_upsert',
      id: 'task-2',
      task: 'Already done',
      group: 'alpha',
      agent_id: 'agent-2',
      lane: 'Done',
    }],
  });

  assert.deepEqual(jsonValue(context, 'doneFlourishCalls'), [
    { agentId: 'agent-1', label: 'Done' },
  ]);
});

test('ws invalidation skips rerendering the active context panel for off-group agent removals', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'context';
  runInContext(context, `
    state.agents = {
      'agent-1': {
        id: 'agent-1',
        name: 'Beta agent',
        group: 'beta',
      },
    };
  `);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'agent_remove',
      id: 'agent-1',
    }],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('renderAgentPanel preserves focused reply draft across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  const input = document.register('weaver-reply-input');
  input.value = 'Proceed with merge';
  input.selectionStart = 8;
  input.selectionEnd = 13;
  panel.appendChild(input);
  document.activeElement = input;

  context.state.weaver_settings = {
    alpha: { pending_question: 'Merge this branch?' },
  };

  runInContext(context, `_weaverReplyDraft = '';`);
  context.renderAgentPanel();

  assert.equal(input.focused, true);
  assert.equal(input.value, 'Proceed with merge');
  assert.equal(input.selectionStart, 8);
  assert.equal(input.selectionEnd, 13);
});

test('renderAgentPanel preserves focused architect decision title editor across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  const content = document.createElement('div');
  content.classList.add('agent-panel-content');
  panel.setQuerySelector('.agent-panel-content', content);

  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Productmind',
      slug: 'productmind',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      created_at: 10,
    },
  };
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Initial title',
      rationale: 'Initial rationale',
      status: 'proposed',
      updated_at: 20,
    },
  };

  runInContext(context, `
    _weaverArchitectExpanded['arch-1'] = true;
    weaverStartDecisionEdit('decision-1');
    weaverDecisionDraftInput('decision-1', 'title', 'Updated direction');
  `);

  const oldInput = document.createElement('input');
  oldInput.dataset.focusKey = 'weaver-decision-title:decision-1';
  oldInput.value = 'Updated direction';
  oldInput.selectionStart = 7;
  oldInput.selectionEnd = 16;
  panel.appendChild(oldInput);
  document.activeElement = oldInput;

  const restoredInput = document.createElement('input');
  restoredInput.dataset.focusKey = 'weaver-decision-title:decision-1';
  panel.setQuerySelector('[data-focus-key="weaver-decision-title:decision-1"]', restoredInput);

  context.renderAgentPanel();

  assert.equal(restoredInput.focused, true);
  assert.equal(restoredInput.value, 'Updated direction');
  assert.equal(restoredInput.selectionStart, 7);
  assert.equal(restoredInput.selectionEnd, 16);
  assert.equal(
    jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].draft.title`),
    'Updated direction',
  );
});

test('renderAgentPanel preserves focused architect decision rationale editor across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  const content = document.createElement('div');
  content.classList.add('agent-panel-content');
  panel.setQuerySelector('.agent-panel-content', content);

  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Productmind',
      slug: 'productmind',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
      created_at: 10,
    },
  };
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Initial title',
      rationale: 'Initial rationale',
      status: 'proposed',
      updated_at: 20,
    },
  };

  runInContext(context, `
    _weaverArchitectExpanded['arch-1'] = true;
    weaverStartDecisionEdit('decision-1');
    weaverDecisionDraftInput('decision-1', 'rationale', 'Keep the UI stable across refreshes');
  `);

  const oldTextarea = document.createElement('textarea');
  oldTextarea.dataset.focusKey = 'weaver-decision-rationale:decision-1';
  oldTextarea.value = 'Keep the UI stable across refreshes';
  oldTextarea.selectionStart = 9;
  oldTextarea.selectionEnd = 18;
  panel.appendChild(oldTextarea);
  document.activeElement = oldTextarea;

  const restoredTextarea = document.createElement('textarea');
  restoredTextarea.dataset.focusKey = 'weaver-decision-rationale:decision-1';
  panel.setQuerySelector('[data-focus-key="weaver-decision-rationale:decision-1"]', restoredTextarea);

  context.renderAgentPanel();

  assert.equal(restoredTextarea.focused, true);
  assert.equal(restoredTextarea.value, 'Keep the UI stable across refreshes');
  assert.equal(restoredTextarea.selectionStart, 9);
  assert.equal(restoredTextarea.selectionEnd, 18);
  assert.equal(
    jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].draft.rationale`),
    'Keep the UI stable across refreshes',
  );
});

test('renderAgentPanel preserves expanded architect decision state and scroll across decision WS deltas', () => {
  const { context, document } = createWeaverWsHarness();
  const panel = document.getElementById('panel-agent');
  const content = document.createElement('div');
  content.classList.add('agent-panel-content');
  panel.setQuerySelector('.agent-panel-content', content);

  runInContext(context, `
    state.groups = { alpha: ['arch-1'] };
    state.agents = {
      'arch-1': {
        id: 'arch-1',
        name: 'Productmind',
        slug: 'productmind',
        kind: 'architect',
        group: 'alpha',
        cell_type: 'agent',
        status: 'running',
        created_at: 10,
      },
      'eng-1': {
        id: 'eng-1',
        name: 'Engineer One',
        kind: 'engineer',
        group: 'alpha',
        hired_by_architect_id: 'arch-1',
        cell_type: 'agent',
        created_at: 11,
      },
    };
    state.board_tasks = {
      'LOOM:200': { id: 'LOOM:200', task: 'Implement decision UI', group: 'alpha', lane: 'In Progress' },
    };
    state.decisions = {
      'decision-1': {
        id: 'decision-1',
        architect_id: 'arch-1',
        title: 'Initial title',
        rationale: 'Initial rationale',
        status: 'proposed',
        updated_at: 20,
      },
    };
    focusedItemId = 'arch-1';
    _expectedSeq = 1;
    renderAgentPanel();
    weaverToggleDecision('decision-1');
  `);
  content.scrollTop = 144;

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'decision_upsert',
        id: 'decision-1',
        architect_id: 'arch-1',
        title: 'Define scope first',
        rationale: 'Updated rationale from server',
        status: 'accepted',
        linked_task_ids: ['LOOM:200'],
        linked_engineer_ids: ['eng-1'],
        supersedes: 'decision-0',
        updated_at: 30,
      },
      {
        op: 'decision_upsert',
        id: 'decision-2',
        architect_id: 'arch-1',
        title: 'Revision',
        rationale: 'Supersedes the first decision',
        status: 'revised',
        supersedes: 'decision-1',
        updated_at: 31,
      },
    ],
  });

  assert.equal(content.scrollTop, 144);
  assert.equal(jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].expanded`), true);
  assert.match(panel.innerHTML, /Updated rationale from server/);
  assert.match(panel.innerHTML, /Linked tasks/);
  assert.match(panel.innerHTML, /LOOM:200/);
  assert.match(panel.innerHTML, /Linked engineers/);
  assert.match(panel.innerHTML, /eng-1/);
  assert.match(panel.innerHTML, /Supersedes/);
  assert.match(panel.innerHTML, /decision-0/);
  assert.match(panel.innerHTML, /Superseded by/);
  assert.match(panel.innerHTML, /decision-2/);
});

test('renderAgentPanel preserves focused architect decision edit draft across decision WS deltas', () => {
  const { context, document, sandbox } = createWeaverWsHarness();
  const panel = document.getElementById('panel-agent');
  const content = document.createElement('div');
  content.classList.add('agent-panel-content');
  panel.setQuerySelector('.agent-panel-content', content);

  runInContext(context, `
    state.groups = { alpha: ['arch-1'] };
    state.agents = {
      'arch-1': {
        id: 'arch-1',
        name: 'Productmind',
        slug: 'productmind',
        kind: 'architect',
        group: 'alpha',
        cell_type: 'agent',
        status: 'running',
        created_at: 10,
      },
    };
    state.decisions = {
      'decision-1': {
        id: 'decision-1',
        architect_id: 'arch-1',
        title: 'Initial title',
        rationale: 'Initial rationale',
        status: 'proposed',
        updated_at: 20,
      },
    };
    focusedItemId = 'arch-1';
    _expectedSeq = 1;
    renderAgentPanel();
    weaverStartDecisionEdit('decision-1');
    weaverDecisionDraftInput('decision-1', 'title', 'Draft title');
    weaverDecisionDraftInput('decision-1', 'rationale', 'Draft rationale');
    weaverDecisionDraftInput('decision-1', 'status', 'accepted');
  `);
  content.scrollTop = 96;

  const oldInput = document.createElement('input');
  oldInput.dataset.focusKey = 'weaver-decision-title:decision-1';
  oldInput.value = 'Draft title';
  oldInput.selectionStart = 2;
  oldInput.selectionEnd = 7;
  panel.appendChild(oldInput);
  document.activeElement = oldInput;

  const restoredInput = document.createElement('input');
  restoredInput.dataset.focusKey = 'weaver-decision-title:decision-1';
  panel.setQuerySelector('[data-focus-key="weaver-decision-title:decision-1"]', restoredInput);

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'decision_upsert',
        id: 'decision-1',
        architect_id: 'arch-1',
        title: 'Server title',
        rationale: 'Server rationale',
        status: 'rejected',
        updated_at: 30,
      },
    ],
  });

  assert.equal(content.scrollTop, 96);
  assert.equal(restoredInput.focused, true);
  assert.equal(restoredInput.value, 'Draft title');
  assert.equal(restoredInput.selectionStart, 2);
  assert.equal(restoredInput.selectionEnd, 7);
  assert.equal(jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].editing`), true);
  assert.equal(jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].draft.title`), 'Draft title');
  assert.equal(jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].draft.rationale`), 'Draft rationale');
  assert.equal(jsonValue(context, `_weaverArchitectDecisionUi['decision-1'].draft.status`), 'accepted');

  runInContext(context, `weaverSaveDecisionEdit('arch-1', 'decision-1')`);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'architect_decision_update',
      architect_id: 'arch-1',
      id: 'decision-1',
      title: 'Draft title',
      rationale: 'Draft rationale',
      status: 'accepted',
    },
  ]);
});

test('renderAgentPanel preserves the selected Events tab across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  panel.querySelector = function() { return null; };
  context.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer One',
      group: 'alpha',
      kind: 'engineer',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'eng-1';
  context.state.agent_digest_settings = {
    'eng-1': { agent_id: 'eng-1', paused: false },
  };
  context.state.digest_buffer_stats = {
    'eng-1': {
      agent_id: 'eng-1',
      group: 'alpha',
      buffered_events: 1,
      next_push_in: 30,
      queued_events: [
        { id: 3, kind: 'task_completed', message: 'Queued event', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  context.state.digest_sent_events = {
    'eng-1': [
      { id: 2, kind: 'task_completed', message: 'Sent event', timestamp: 5, delivered_at: 8 },
    ],
  };

  context.renderAgentPanel();
  runInContext(context, `agentPanelSelectTab('events')`);

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Queued for next digest/);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Already sent to Engineer One/);
});

test('renderAgentPanel shows workflow breach events in engineer cell history', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  panel.querySelector = function() { return null; };
  context.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer One',
      group: 'alpha',
      kind: 'engineer',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'eng-1';
  context.state.agent_digest_settings = {
    'eng-1': { agent_id: 'eng-1', paused: false },
  };
  context.state.panel_events = [
    {
      id: 42,
      kind: 'workflow_breach',
      cell_id: 'eng-1',
      group: 'alpha',
      message: 'escape_clause_skip: Review gate caught direct done (worker=worker-1)',
      task_id: 'task-1',
      timestamp: 10,
    },
  ];
  runInContext(context, `_agentPanelLastSelectedTabByKind.engineer = 'events';`);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /workflow breach/);
  assert.match(panel.innerHTML, /escape_clause_skip: Review gate caught direct done/);
  assert.match(panel.innerHTML, /task-1/);
});

test('renderAgentPanel preserves the selected architect Events tab across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  panel.querySelector = function() { return null; };
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Architect One',
      group: 'alpha',
      kind: 'architect',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'arch-1';
  context.state.agent_digest_settings = {
    'arch-1': { agent_id: 'arch-1', paused: false, architect_digest: true },
  };
  context.state.digest_buffer_stats = {
    'arch-1': {
      agent_id: 'arch-1',
      group: 'alpha',
      buffered_events: 1,
      next_push_in: 300,
      queued_events: [
        { id: 3, kind: 'task_completed', message: 'Queued architect event', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  context.state.digest_sent_events = {
    'arch-1': [
      { id: 2, kind: 'task_completed', message: 'Sent architect event', timestamp: 5, delivered_at: 8 },
    ],
  };

  context.renderAgentPanel();
  runInContext(context, `agentPanelSelectTab('events')`);

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Queued architect event/);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Already sent to Architect One/);
});

test('renderAgentPanel preserves the selected Worklog tab across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  panel.querySelector = function() { return null; };
  context.state.weaver_worklog = {
    alpha: [
      {
        id: 3,
        task_id: 'LOOM:3',
        task_title: 'Add Worklog tab',
        agent_id: 'agent-1',
        agent_name: 'Worker',
        agent_owned: true,
        started_at: 10,
      },
    ],
  };
  context.state.board_tasks = {
    'LOOM:3': {
      id: 'LOOM:3',
      task: 'Add Worklog tab',
      group: 'alpha',
      lane: 'Review',
      status: 'Awaiting approval',
      agent_id: 'agent-1',
    },
  };
  context.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Engineer One', group: 'alpha', kind: 'engineer', cell_type: 'agent' },
  };
  context.focusedItemId = 'agent-1';

  context.renderAgentPanel();
  runInContext(context, `agentPanelSelectTab('worklog')`);

  assert.match(panel.innerHTML, /id="agent-panel-tab-worklog" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Dispatched tasks/);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-worklog" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Awaiting approval/);
});

test('renderAgentPanel keeps the same Events anchor visible when new digest rows are inserted above', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  const oldContent = new FakeElement('weaver-content-old');
  const newContent = new FakeElement('weaver-content-new');
  let currentContent = oldContent;

  function makeAnchor(key, top, bottom) {
    const el = new FakeElement();
    el.setAttribute('data-weaver-anchor', key);
    el.getBoundingClientRect = function() {
      return { top, bottom, left: 0, right: 200, width: 200, height: bottom - top };
    };
    return el;
  }

  oldContent.scrollTop = 100;
  oldContent.getBoundingClientRect = function() {
    return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
  };
  oldContent.querySelectorAll = function(selector) {
    if (selector === '[data-weaver-anchor]') {
      return [
        makeAnchor('sent-2-8', 20, 40),
        makeAnchor('sent-1-6', 60, 80),
      ];
    }
    return [];
  };

  newContent.scrollTop = 0;
  newContent.getBoundingClientRect = function() {
    return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
  };
  newContent.querySelectorAll = function(selector) {
    if (selector === '[data-weaver-anchor]') {
      return [
        makeAnchor('sent-3-10', 10, 30),
        makeAnchor('sent-2-8', 40, 60),
        makeAnchor('sent-1-6', 80, 100),
      ];
    }
    return [];
  };

  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-content') return currentContent;
    return null;
  };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      currentContent = newContent;
    },
  });

  context.state.agent_digest_settings = {
    'eng-1': { agent_id: 'eng-1', paused: false },
  };
  context.state.digest_buffer_stats = {
    'eng-1': {
      agent_id: 'eng-1',
      group: 'alpha',
      buffered_events: 0,
      next_push_in: 0,
      queued_events: [],
      manual_flush_requested: false,
    },
  };
  context.state.digest_sent_events = {
    'eng-1': [
      { id: 2, kind: 'task_completed', message: 'Older digest', timestamp: 5, delivered_at: 8 },
      { id: 1, kind: 'task_completed', message: 'Oldest digest', timestamp: 4, delivered_at: 6 },
    ],
  };
  runInContext(context, `_weaverActiveTabByGroup.alpha = 'events';`);

  context.renderAgentPanel();

  assert.equal(newContent.scrollTop, 120);
});

test('renderAgentPanel keeps the same Messages anchor visible when new message cards are inserted above', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  const oldList = new FakeElement('messages-old');
  const newList = new FakeElement('messages-new');
  let currentList = oldList;

  function makeAnchor(key, top, bottom) {
    const el = new FakeElement();
    el.setAttribute('data-agent-panel-anchor', key);
    el.getBoundingClientRect = function() {
      return { top, bottom, left: 0, right: 240, width: 240, height: bottom - top };
    };
    return el;
  }

  oldList.scrollTop = 100;
  oldList.getBoundingClientRect = function() {
    return { top: 0, bottom: 160, left: 0, right: 240, width: 240, height: 160 };
  };
  oldList.querySelectorAll = function(selector) {
    if (selector === '[data-agent-panel-anchor]') {
      return [
        makeAnchor('message-msg-2', 20, 50),
        makeAnchor('message-msg-1', 70, 100),
      ];
    }
    return [];
  };

  newList.scrollTop = 0;
  newList.getBoundingClientRect = function() {
    return { top: 0, bottom: 160, left: 0, right: 240, width: 240, height: 160 };
  };
  newList.querySelectorAll = function(selector) {
    if (selector === '[data-agent-panel-anchor]') {
      return [
        makeAnchor('message-msg-3', 10, 40),
        makeAnchor('message-msg-2', 50, 80),
        makeAnchor('message-msg-1', 100, 130),
      ];
    }
    return [];
  };

  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-message-list') return currentList;
    return null;
  };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      currentList = newList;
    },
  });

  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Architect One',
      group: 'alpha',
      kind: 'architect',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'msg-3', action: 'engineer_message_architect', message: 'Newest message', timestamp: 30, sender_kind: 'engineer', direction: 'received' },
        { id: 'msg-2', action: 'architect_reply', message: 'Anchored message', timestamp: 20, sender_kind: 'architect', direction: 'sent' },
        { id: 'msg-1', action: 'engineer_message_architect', message: 'Oldest message', timestamp: 10, sender_kind: 'engineer', direction: 'received' },
      ],
    },
  };
  context.focusedItemId = 'arch-1';
  runInContext(context, `_agentPanelLastSelectedTabByKind.architect = 'messages';`);

  context.renderAgentPanel();

  assert.equal(newList.scrollTop, 130);
});

test('agent Messages tab owns a full-height scroll region and preserves full body wrapping', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    css,
    /\.agent-panel-panel\[data-agent-panel-tab="messages"\] \.agent-panel-content\s*\{[^}]*display:\s*flex;[^}]*overflow:\s*hidden;[^}]*padding:\s*0;/s,
  );
  assert.match(
    css,
    /\.agent-panel-messages-tab\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*height:\s*100%;/s,
  );
  assert.match(
    css,
    /\.agent-panel-message-list\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/s,
  );
  assert.match(
    css,
    /\.agent-panel-message-body\s*\{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/s,
  );
});

test('renderAgentCell shows per-engineer and architect digest pause controls with state-driven classes', () => {
  const { context } = createWeaverHarness();
  context.state.group_settings = {
    alpha: { weaver_agent_id: 'weaver-1' },
  };
  context.state.children = {};
  context.state.weaver_settings = {
    alpha: { paused: false },
  };
  context.state.agents = {
    'weaver-1': { id: 'weaver-1', name: 'Weaver', group: 'alpha', cell_type: 'agent' },
    'eng-1': { id: 'eng-1', name: 'Engineer', group: 'alpha', kind: 'engineer', cell_type: 'agent' },
    'arch-1': { id: 'arch-1', name: 'Architect', group: 'alpha', kind: 'architect', cell_type: 'agent' },
  };
  context.state.agent_digest_settings = {
    'eng-1': { agent_id: 'eng-1', paused: false },
    'arch-1': { agent_id: 'arch-1', paused: false, architect_digest: true },
  };
  runInContext(context, `focusedItemId = ''; selectedAgentId = 'weaver-1';`);

  const runningHtml = context.renderAgentCell({
    id: 'weaver-1',
    name: 'Weaver',
    icon: '🧶',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  });
  const engineerNamedWeaverHtml = context.renderAgentCell({
    id: 'eng-weaver',
    name: 'Weaver',
    icon: '🛠️',
    group: 'alpha',
    kind: 'engineer',
    cell_type: 'agent',
    status: 'running',
  });
  const workerHtml = context.renderAgentCell({
    id: 'agent-1',
    name: 'Worker',
    icon: '🤖',
    group: 'alpha',
    kind: 'worker',
    cell_type: 'agent',
    status: 'running',
  });
  const engineerHtml = context.renderAgentCell({
    id: 'eng-1',
    name: 'Engineer',
    icon: '🛠️',
    group: 'alpha',
    kind: 'engineer',
    cell_type: 'agent',
    status: 'running',
  });
  const architectHtml = context.renderAgentCell({
    id: 'arch-1',
    name: 'Architect',
    icon: '🏛️',
    group: 'alpha',
    kind: 'architect',
    cell_type: 'agent',
    status: 'running',
  });

  assert.match(runningHtml, /^<div class="cell selected"/);
  assert.doesNotMatch(runningHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(runningHtml, /class="cell-header-controls">/);
  assert.match(runningHtml, /class="cell-weaver-toggle running"/);
  assert.match(runningHtml, /Pause Weaver event delivery/);
  assert.match(engineerNamedWeaverHtml, /^<div class="cell engineer"/);
  assert.doesNotMatch(engineerNamedWeaverHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.doesNotMatch(workerHtml, /cell-weaver-toggle/);
  assert.match(workerHtml, /class="cell-worker-badge">worker<\/div>/);
  assert.doesNotMatch(workerHtml, /cell-engineer-badge|cell-architect-badge/);
  assert.match(engineerHtml, /^<div class="cell engineer"/);
  assert.doesNotMatch(engineerHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(engineerHtml, /class="cell-engineer-badge">engineer<\/div>/);
  assert.match(engineerHtml, /class="cell-weaver-toggle running"/);
  assert.match(engineerHtml, /toggleDigestPauseForAgent\(decodeURIComponent\('eng-1'\)\)/);
  assert.match(engineerHtml, /Pause event delivery/);
  assert.doesNotMatch(engineerHtml, /Pause Weaver event delivery/);
  assert.match(architectHtml, /^<div class="cell architect"/);
  assert.doesNotMatch(architectHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(architectHtml, /class="cell-architect-badge">architect<\/div>/);
  assert.match(architectHtml, /class="cell-weaver-toggle running"/);
  assert.match(architectHtml, /toggleDigestPauseForAgent\(decodeURIComponent\('arch-1'\)\)/);
  assert.match(architectHtml, /Pause event delivery/);
  assert.doesNotMatch(architectHtml, /Pause Weaver event delivery/);

  runInContext(context, `toggleDigestPauseForAgent('eng-1')`);
  runInContext(context, `toggleDigestPauseForAgent('arch-1')`);
  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'digest_pause', agent_id: 'eng-1' },
    { cmd: 'digest_pause', agent_id: 'arch-1' },
  ]);

  context.state.weaver_settings.alpha.paused = true;
  const pausedHtml = context.renderAgentCell({
    id: 'weaver-1',
    name: 'Weaver',
    icon: '🧶',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  });

  assert.match(pausedHtml, /^<div class="cell selected"/);
  assert.doesNotMatch(pausedHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(pausedHtml, /class="cell-weaver-toggle paused"/);
  assert.match(pausedHtml, /Resume Weaver event delivery/);

  context.state.agent_digest_settings['eng-1'].paused = true;
  context.state.agent_digest_settings['arch-1'].paused = true;
  const pausedEngineerHtml = context.renderAgentCell({
    id: 'eng-1',
    name: 'Engineer',
    icon: '🛠️',
    group: 'alpha',
    kind: 'engineer',
    cell_type: 'agent',
    status: 'running',
  });
  const pausedArchitectHtml = context.renderAgentCell({
    id: 'arch-1',
    name: 'Architect',
    icon: '🏛️',
    group: 'alpha',
    kind: 'architect',
    cell_type: 'agent',
    status: 'running',
  });
  assert.match(pausedEngineerHtml, /^<div class="cell engineer"/);
  assert.doesNotMatch(pausedEngineerHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(pausedEngineerHtml, /class="cell-weaver-toggle paused"/);
  assert.match(pausedEngineerHtml, /Resume event delivery/);
  assert.match(pausedArchitectHtml, /^<div class="cell architect"/);
  assert.doesNotMatch(pausedArchitectHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(pausedArchitectHtml, /class="cell-weaver-toggle paused"/);
  assert.match(pausedArchitectHtml, /Resume event delivery/);

  context.state.weaver_settings.alpha = {
    paused: false,
    pending_question: 'Need input',
  };
  const askingHtml = context.renderAgentCell({
    id: 'weaver-1',
    name: 'Weaver',
    icon: '🧶',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  });

  assert.match(askingHtml, /^<div class="cell selected"/);
  assert.doesNotMatch(askingHtml, /^<div class="[^"]*\bweaver(?:\b|-)/);
  assert.match(askingHtml, /\? awaiting input/);
});

test('renderAgentCell keeps done flourish timing stable across rerenders', () => {
  let fakeNow = 1000;
  const timers = [];
  const { sandbox } = createSandbox({
    Date: {
      now() {
        return fakeNow;
      },
    },
    setTimeout(fn, delay) {
      timers.push({ fn, delay, cleared: false });
      return timers.length;
    },
    clearTimeout(id) {
      if (timers[id - 1]) timers[id - 1].cleared = true;
    },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    renderCalls = 0;
    render = function() { renderCalls++; };
    focusedItemId = '';
    selectedAgentId = '';
    state.children = {};
    state.group_settings = {};
    state.weaver_settings = {};
    _startAgentDoneFlourish('agent-1', 'Done');
  `);

  const agent = {
    id: 'agent-1',
    name: 'Worker',
    icon: '🤖',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  };

  let html = context.renderAgentCell(agent);
  assert.match(html, /cell-status-done-flourish/);
  assert.match(html, /cell-status-flourish-label">Done</);
  assert.equal(timers[0].delay, 3400);

  fakeNow += 1200;
  html = context.renderAgentCell(agent);
  assert.match(html, /--cell-status-done-delay:-1200ms/);

  fakeNow += 2200;
  timers[0].fn();
  html = context.renderAgentCell(agent);
  assert.doesNotMatch(html, /cell-status-done-flourish/);
  assert.equal(jsonValue(context, 'renderCalls'), 1);
});

test('weaver agent card toggle shares the close control reveal affordances and icon-only default chrome', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /\.cell-header-controls\s*\{[^}]*position:\s*absolute;[^}]*display:\s*flex;[^}]*align-items:\s*center;/);
  assert.match(css, /\.cell-weaver-toggle\s*\{[^}]*opacity:\s*0;[^}]*pointer-events:\s*none;/);
  assert.match(css, /\.cell-weaver-toggle\s*\{[^}]*border:\s*1px solid transparent;[^}]*background:\s*transparent;/);
  assert.match(css, /\.cell:hover \.cell-close,\s*\.cell:hover \.cell-weaver-toggle,\s*\.cell:hover \.cell-relaunch,\s*\.cell.focused \.cell-close,\s*\.cell.focused \.cell-weaver-toggle,\s*\.cell.focused \.cell-relaunch,\s*\.cell:focus-within \.cell-close,\s*\.cell:focus-within \.cell-weaver-toggle,\s*\.cell:focus-within \.cell-relaunch\s*\{[^}]*opacity:\s*1;[^}]*pointer-events:\s*auto;/);
  assert.match(css, /\.cell-weaver-toggle:hover,\s*\.cell-weaver-toggle:focus-visible,\s*\.cell-weaver-toggle:active\s*\{/);
  assert.match(css, /\.cell-weaver-toggle:hover,\s*\.cell-weaver-toggle:focus-visible,\s*\.cell-weaver-toggle:active\s*\{[^}]*background:/);
  assert.match(css, /\.cell-weaver-toggle:hover,\s*\.cell-weaver-toggle:focus-visible,\s*\.cell-weaver-toggle:active\s*\{[^}]*border-color:/);
  assert.match(css, /\.cell-weaver-toggle:hover,\s*\.cell-weaver-toggle:focus-visible,\s*\.cell-weaver-toggle:active\s*\{[^}]*outline:\s*none;/);
});

test('agent role badges render in the bottom-right opposite the agent type label', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const typeRule = css.match(/\.cell-type\s*\{[^}]*\}/)[0];
  const engineerRule = css.match(/\.cell-engineer-badge\s*\{[^}]*\}/)[0];
  const architectRule = css.match(/\.cell-architect-badge\s*\{[^}]*\}/)[0];
  const workerRule = css.match(/\.cell-worker-badge\s*\{[^}]*\}/)[0];

  assert.match(typeRule, /bottom:\s*2px;/);
  assert.match(typeRule, /left:\s*3px;/);
  assert.match(engineerRule, /bottom:\s*2px;/);
  assert.match(engineerRule, /right:\s*3px;/);
  assert.doesNotMatch(engineerRule, /left:\s*3px;/);
  assert.match(architectRule, /bottom:\s*2px;/);
  assert.match(architectRule, /right:\s*3px;/);
  assert.doesNotMatch(architectRule, /left:\s*3px;/);
  assert.match(workerRule, /bottom:\s*2px;/);
  assert.match(workerRule, /right:\s*3px;/);
  assert.doesNotMatch(workerRule, /left:\s*3px;/);
  assert.match(workerRule, /color:\s*var\(--green\);/);
});

test('agent cards do not define legacy weaver edge chrome', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /\.cell\.engineer\s*\{[^}]*border-left:\s*3px solid rgba\(88, 166, 255, 0\.72\);/);
  assert.doesNotMatch(css, /\.cell\.weaver(?:[\s.#:{]|$)/);
  assert.doesNotMatch(css, /\.cell\.weaver-asking(?:[\s.#:{]|$)/);
  assert.doesNotMatch(css, /--weaver-(?:chrome|edge-shadow|active-glow)/);
  assert.doesNotMatch(css, /weaver-pulse/);
  assert.match(css, /\.cell-weaver-ask\s*\{/);
});

test('renderAgentPanel shows branch review-point summary in Session Map view', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');

  context.state.agents = {
    'weaver-1': {
      id: 'weaver-1',
      name: 'Weaver',
      group: 'alpha',
      cell_type: 'agent',
      status: 'running',
    },
    'agent-1': {
      id: 'agent-1',
      name: 'Engineer One',
      group: 'alpha',
      kind: 'engineer',
      cell_type: 'agent',
      status: 'running',
      worktree_path: '/repo/.loom/worktrees/worker',
      worktree_repo_root: '/repo',
      worktree_branch: 'loom/worker',
    },
  };
  context.state.group_settings = {
    alpha: {
      weaver_agent_id: 'weaver-1',
    },
  };
  context.state.weaver_session_maps = {
    alpha: {
      branch_boundaries: {
        items: [
          {
            branch: 'loom/worker',
            latest_boundary_task: 'Stable review point',
            partial_review_safe: false,
            foreground_task_title: 'Implement follow-up',
            queued_followups: [{ title: 'Queue release notes' }],
          },
        ],
      },
    },
  };
  context._weaverJournalSubviewByGroup.alpha = 'session_map';
  context.focusedItemId = 'agent-1';

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Branch review points/);
  assert.match(panel.innerHTML, /Stable review point/);
  assert.match(panel.innerHTML, /Branch advanced/);
  assert.match(panel.innerHTML, /Current: Implement follow-up/);
  assert.match(panel.innerHTML, /Queued next: Queue release notes/);
});

test('task deltas do not rerender the templates panel when it is active', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'templates';

  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'task_upsert', id: 'task-1', group: 'alpha', task: 'Ship docs', lane: 'Backlog', position: 1 },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('weaver settings deltas rerender the main grid for card pause state updates', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'board';

  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'weaver_settings_update', group: 'alpha', paused: true },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('agent digest deltas rerender the main grid for engineer card pause state updates', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'board';

  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'agent_digest_update', agent_id: 'eng-1', group: 'alpha', paused: true },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('weaver sent-event deltas rerender only the active agent panel surface', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'weaver';

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'weaver_sent_events',
        group: 'alpha',
        events: [
          { id: 11, kind: 'task_completed', message: 'Merged cleanly', timestamp: 1, delivered_at: 2 },
        ],
      },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 1,
    templates: 0,
  });
  assert.deepEqual(jsonValue(context, 'state.weaver_sent_events.alpha'), [
    { id: 11, kind: 'task_completed', message: 'Merged cleanly', timestamp: 1, delivered_at: 2 },
  ]);
});

test('renderActivePanel routes the agent slot through renderAgentPanel only', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'weaver';
  sandbox.renderCalls.agent = 0;
  runInContext(context, `
    renderAgentPanel = function() { renderCalls.agent++; };
  `);

  context.renderActivePanel();

  assert.equal(sandbox.renderCalls.agent, 1);
});

test('togglePanel renders the agent slot through renderAgentPanel only', () => {
  const { context, sandbox } = createMainHarness({
    renderAgentPanel() {
      sandbox.agentPanelCalls = (sandbox.agentPanelCalls || 0) + 1;
    },
  });

  context.togglePanel('weaver');

  assert.equal(sandbox.agentPanelCalls, 1);
});

test('weaver worklog deltas rerender only the active agent panel surface', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'weaver';

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'weaver_worklog_append',
        group: 'alpha',
        entry: {
          id: 7,
          task_id: 'LOOM:7',
          task_title: 'Review Worklog tab',
          agent_id: 'agent-7',
          agent_name: 'Worker Seven',
          agent_owned: true,
          started_at: 3,
        },
      },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 1,
    templates: 0,
  });
  assert.deepEqual(jsonValue(context, 'state.weaver_worklog.alpha'), [
    {
      id: 7,
      task_id: 'LOOM:7',
      task_title: 'Review Worklog tab',
      agent_id: 'agent-7',
      agent_name: 'Worker Seven',
      agent_owned: true,
      started_at: 3,
    },
  ]);
});

test('weaver stream deltas rerender only the active agent panel surface', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'weaver';

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'weaver_streams',
        group: 'alpha',
        streams: {
          count: 1,
          by_state: { awaiting_human_validation: 1 },
          items: [
            {
              stream_id: 'stream-events',
              branch: 'loom/events-panel',
              state: 'awaiting_human_validation',
              recommended_next_action: 'run_manual_validation',
            },
          ],
        },
      },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 1,
    templates: 0,
  });
  assert.deepEqual(jsonValue(context, 'state.weaver_streams.alpha'), {
    count: 1,
    by_state: { awaiting_human_validation: 1 },
    items: [
      {
        stream_id: 'stream-events',
        branch: 'loom/events-panel',
        state: 'awaiting_human_validation',
        recommended_next_action: 'run_manual_validation',
      },
    ],
  });
});

test('weaver Session Map responses rerender only the active agent panel surface', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'weaver';
  runInContext(context, `
    _currentGroup = function() { return 'alpha'; };
  `);

  context._handleWeaverSessionMapMessage({
    type: 'weaver_session_map',
    group: 'alpha',
    session_map: {
      group: 'alpha',
      overview: { tasks_total: 3 },
      streams: { items: [] },
    },
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 1,
    templates: 0,
  });
  assert.deepEqual(jsonValue(context, 'state.weaver_session_maps.alpha'), {
    group: 'alpha',
    overview: { tasks_total: 3 },
    streams: { items: [] },
  });
});

test('full state re-requests an open Session Map after reconnect clears cached data', () => {
  const { context, sandbox } = createWeaverWsHarness();

  runInContext(context, `
    state.groups = { alpha: ['weaver-1'] };
    state.agents = {
      'weaver-1': {
        id: 'weaver-1',
        group: 'alpha',
        name: 'Weaver',
        cell_type: 'agent',
        status: 'running'
      }
    };
    state.group_settings = {
      alpha: { weaver_agent_id: 'weaver-1' }
    };
  `);

  context.weaverOpenSessionMap('alpha');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'weaver_session_map_read', group: 'alpha' },
  ]);
  assert.deepEqual(jsonValue(context, '_weaverSessionMapMetaByGroup.alpha'), {
    loading: true,
    stale: false,
  });

  sandbox.sendCalls.length = 0;
  context._handleFullState({
    type: 'state',
    seq: 1,
    groups: { alpha: ['weaver-1'] },
    agents: {
      'weaver-1': {
        id: 'weaver-1',
        group: 'alpha',
        name: 'Weaver',
        cell_type: 'agent',
        status: 'running',
      },
    },
    group_settings: {
      alpha: { weaver_agent_id: 'weaver-1' },
    },
    board_tasks: {},
    board_lanes: [],
    panel_events: [],
    weaver_buffer_stats: {},
    weaver_sent_events: {},
    weaver_worklog: {},
    weaver_streams: {},
    weaver_session_maps: {},
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'weaver_session_map_read', group: 'alpha' },
  ]);
  assert.deepEqual(jsonValue(context, '_weaverSessionMapMetaByGroup.alpha'), {
    loading: true,
    stale: false,
  });
});

test('task deltas mark the current group Session Map stale', () => {
  const { context } = createWsRenderHarness();
  runInContext(context, `
    _weaverMarkedGroups = [];
    _weaverMarkSessionMapStale = function(groups) {
      _weaverMarkedGroups = groups.slice();
    };
  `);

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'task_upsert',
        id: 'LOOM:1',
        group: 'alpha',
        task: 'Refresh Session Map',
        lane: 'Backlog',
      },
    ],
  });

  assert.deepEqual(jsonValue(context, '_weaverMarkedGroups'), ['alpha']);
});

test('full state hydrates weaver streams for the journal tab', () => {
  const { context } = createWsRenderHarness();

  runInContext(context, `
    _handleFullState({
      seq: 1,
      groups: { alpha: [] },
      agents: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: [],
      weaver_streams: {
        alpha: {
          count: 1,
          by_state: { ready_to_merge: 1 },
          items: [
            {
              stream_id: 'stream:/repo::loom/events-panel',
              branch: 'loom/events-panel',
              state: 'ready_to_merge',
              foreground_task_title: 'Ship the Events panel',
            },
          ],
        },
      },
    });
  `);

  assert.deepEqual(jsonValue(context, 'state.weaver_streams.alpha'), {
    count: 1,
    by_state: { ready_to_merge: 1 },
    items: [
      {
        stream_id: 'stream:/repo::loom/events-panel',
        branch: 'loom/events-panel',
        state: 'ready_to_merge',
        foreground_task_title: 'Ship the Events panel',
      },
    ],
  });
});

test('agents panel defaults to history view', () => {
  const { context, document } = createTemplatesHarness();
  const panel = document.getElementById('panel-templates');

  assert.equal(jsonValue(context, '_agentsPanelView'), 'history');
  assert.equal(jsonValue(context, '_agentHistoryFilter'), 'merged');

  context.renderAgentTemplatesPanel();

  assert.match(panel.innerHTML, /Role Library/);
  assert.match(panel.innerHTML, /Live agents stay in the left column/);
  assert.match(panel.innerHTML, /History<\/button>/);
  assert.match(panel.innerHTML, /tpled-view-btn active[^"]*" onclick="agentsPanelSwitchView\('history'\)"/);
  assert.match(panel.innerHTML, /agent-history-container/);
});

test('roles panel renders role fields and saves via save_role with blank priorities discarded', () => {
  const { context, document, sandbox } = createTemplatesHarness();
  const panel = document.getElementById('panel-templates');
  const editor = document.register('agent-tpl-editor');

  runInContext(context, `
    _agentsPanelView = 'templates';
    _agentTplData = {
      name: 'reviewer',
      display_name: 'Reviewer',
      preamble: 'Be careful.',
      priorities: ['ship small', 'test first'],
      env_vars: {},
      terminals: [],
    };
    _agentTplNew = true;
    _agentTplScope = 'project';
    renderAgentTemplatesPanel();
  `);

  assert.match(panel.innerHTML, /Roles<\/button>/);
  assert.match(editor.innerHTML, /Preamble \(behavior\)/);
  assert.match(editor.innerHTML, /Behavior guidance for workers with this role/);
  assert.match(editor.innerHTML, /Priorities/);
  assert.match(editor.innerHTML, /Project \(\.loom\/roles\/\)/);
  assert.match(editor.innerHTML, /User \(~\/\.loom\/roles\/\)/);

  document.register('agent-template-name').value = 'reviewer';
  document.register('agent-template-display').value = 'Reviewer';
  document.register('agent-template-desc').value = 'Reviews carefully';
  document.register('agent-template-provider').value = '';
  document.register('agent-template-command').value = '';
  document.register('agent-template-model').value = '';
  document.register('agent-template-permissions').value = '';
  document.register('agent-template-max-turns').value = '0';
  document.register('agent-template-preamble').value = 'Be careful.';
  document.register('agent-template-system-prompt').value = '';
  document.register('agent-template-initial-prompt').value = '';
  document.register('agent-template-session-resume').checked = true;
  document.register('agent-template-idle-timeout').value = '0';
  document.register('agent-template-color').value = '';
  document.register('agent-template-icon').value = '';
  document.register('agent-template-worktree').checked = false;
  document.register('agent-template-worktree-base').value = '';
  document.register('agent-template-auto-checkpoint').checked = false;
  document.register('agent-template-merge-squash').checked = true;
  document.register('agent-template-scope').value = 'project';
  document.register('agent-template-env').value = '';

  const priorityOne = new FakeElement();
  priorityOne.setQuerySelector('.agent-template-priority-input', { value: 'ship small' });
  const priorityBlank = new FakeElement();
  priorityBlank.setQuerySelector('.agent-template-priority-input', { value: '   ' });
  const priorityTwo = new FakeElement();
  priorityTwo.setQuerySelector('.agent-template-priority-input', { value: 'test first' });
  document.setSelectorAll('#agent-template-priorities .tpled-priority-row', [
    priorityOne, priorityBlank, priorityTwo,
  ]);
  document.setSelectorAll('#agent-template-terminals .tpled-transition-entry', []);

  runInContext(context, `
    _agentTplSelected = 'project:reviewer';
    _agentTplScope = 'project';
    agentTemplateSave();
  `);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'save_role',
      name: 'reviewer',
      role: {
        name: 'reviewer',
        display_name: 'Reviewer',
        description: 'Reviews carefully',
        provider: '',
        command: '',
        model: '',
        permissions: '',
        max_turns: 0,
        preamble: 'Be careful.',
        priorities: ['ship small', 'test first'],
        system_prompt: '',
        initial_prompt: '',
        session_resume: true,
        idle_timeout: 0,
        tab_color: '',
        icon: '',
        worktree: false,
        worktree_base_branch: '',
        worktree_auto_checkpoint: false,
        worktree_merge_squash: true,
        env_vars: {},
        terminals: [],
      },
      scope: 'project',
      group: 'alpha',
    },
  ]);
});

test('task modal and add-agent labels rename template UI to role UI', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /<label>Role<\/label>\s*<select id="add-template-select"/);
  assert.match(html, /<label>Default role<\/label>\s*<select id="gs-default-agent-template"/);
  assert.match(html, /<label>Role[\s\S]*Optional role used when this task creates a new agent during dispatch\./);
});

test('standalone panel titles use current operator-facing names', () => {
  const { context } = createPanelHarness();
  assert.equal(jsonValue(context, `_standalonePanelTitle('templates')`), 'Library');
  assert.equal(jsonValue(context, `_standalonePanelTitle('weaver')`), 'Agent');
});

test('taskbar labels the selected-agent panel as Agent', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /<button class="taskbar-app" data-app="weaver" onclick="togglePanel\('weaver'\)">&#x2696; Agent<\/button>/);
  assert.doesNotMatch(html, /&#x2696; Architects<\/button>/);
});

test('renderAgentPanel shows the focused-agent empty state when nothing is focused', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-agent');
  panel.querySelector = function() { return null; };

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Select an agent from the grid to see its context\./);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-tab-/);
});

test('opening agents panel requests history when history is the active view', () => {
  const { context, sandbox, taskbarButtons } = createMainHarness({
    _agentsPanelView: 'history',
  });
  sandbox.historyLoads = 0;
  sandbox.templateLoads = 0;
  context.agentHistoryLoad = function() { sandbox.historyLoads++; };
  context.agentTemplateEditorLoad = function() { sandbox.templateLoads++; };

  context.togglePanel('templates');

  assert.equal(sandbox._activePanelApp, 'templates');
  assert.equal(sandbox.historyLoads, 1);
  assert.equal(sandbox.templateLoads, 0);
  assert.equal(taskbarButtons[2].classList.contains('active'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'board_set_panel', active: 'templates' },
  ]);
});

test('switching Library from History to Roles requests roles once', () => {
  const { context, sandbox } = createTemplatesHarness();

  context.renderAgentTemplatesPanel();
  context.agentsPanelSwitchView('templates');
  context.agentsPanelSwitchView('templates');

  assert.equal(jsonValue(context, '_agentsPanelView'), 'templates');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'get_config', group: 'alpha' },
    { cmd: 'list_roles', group: 'alpha' },
  ]);
});

test('event deltas rerender only the active events panel', () => {
  const { context, sandbox } = createWsRenderHarness();
  sandbox._activePanelApp = 'board';

  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'event_append', id: 7, kind: 'agent_progress', message: 'Still working', group: 'alpha', timestamp: 1 },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });

  sandbox._activePanelApp = 'events';
  runInContext(context, `_expectedSeq = 2;`);
  context._handleDelta({
    seq: 2,
    ops: [
      { op: 'event_append', id: 8, kind: 'agent_progress', message: 'More work', group: 'alpha', timestamp: 2 },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 0,
    actions: 0,
    context: 0,
    events: 1,
    weaver: 0,
    templates: 0,
  });
});

test('eventsDismiss clears drafts and hides dismissed attention items from the badge', () => {
  const { context, document } = createEventsHarness();
  const panel = document.register('panel-events');
  panel.setQuerySelector('.events-log', null);
  panel.setQuerySelectorAll('.events-resolve-textarea', []);
  panel.setQuerySelector('.events-search-input', null);
  const badge = new FakeElement('events-badge');
  badge.classList.add('panel-attention');
  document.setSelector('.taskbar-app[data-app="events"]', badge);

  context.state.board_tasks = {
    ask: {
      id: 'ask',
      group: 'alpha',
      task: 'Need input',
      labels: ['loom:human'],
      lane: 'Backlog',
      created_at: '2099-01-01T00:00:00Z',
    },
  };

  runInContext(context, `_eventsResolveDrafts = { ask: 'draft reply' };`);
  context.eventsDismiss('ask');

  assert.deepEqual(jsonValue(context, '_eventsResolveDrafts'), {});
  assert.equal(runInContext(context, `_eventsDismissedIds.has('ask')`), true);
  assert.equal(jsonValue(context, `state.events_dismissed_attention.ask`), 4070908800);
  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'events_dismiss', id: 'ask', timestamp: 4070908800 },
  ]);
  assert.equal(badge.classList.contains('panel-attention'), false);
});

test('persisted dismissed attention stays hidden until a newer timestamp appears', () => {
  const { context, document } = createEventsHarness();
  const panel = document.register('panel-events');
  panel.setQuerySelector('.events-log', null);
  panel.setQuerySelectorAll('.events-resolve-textarea', []);
  panel.setQuerySelector('.events-search-input', null);
  const badge = new FakeElement('events-badge');
  document.setSelector('.taskbar-app[data-app="events"]', badge);

  context.state.events_dismissed_attention = { 'agent-1': 100 };
  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      needs_attention: true,
      error_message: 'Still blocked',
      last_event_at: 100,
    },
  };

  context.updateEventsAttentionBadge();
  assert.equal(badge.classList.contains('panel-attention'), false);

  context.state.agents['agent-1'].last_event_at = 101;
  context.updateEventsAttentionBadge();
  assert.equal(badge.classList.contains('panel-attention'), true);
});

test('openEditTask populates modal state from the task and preserves editable versus system labels', () => {
  const { context, document } = createModalHarness();
  loadScript(context, 'static/js/render.js');
  const futureIso = '2099-01-04T12:30:00.000Z';

  document.register('task-modal-title');
  document.register('task-submit-btn');
  document.register('task-created-by-attribution');
  document.register('task-task-input');
  document.register('task-description-input');
  document.register('task-labels-input');
  document.register('task-labels-chips');
  document.register('task-deps-input');
  document.register('task-deps-dropdown');
  document.register('task-deps-chips');
  document.register('task-action-vars');
  document.register('task-scheduled-input');
  document.register('task-group-select');
  document.register('task-template-select');
  document.register('task-action-select');
  document.register('task-external-provider-input');
  document.register('task-external-id-input');
  document.register('task-external-url-input');
  document.register('task-verification-mode-input');
  document.register('task-verification-state-input');
  document.register('task-verification-tests-input');
  document.register('task-verification-smoke-input').checked = false;
  document.register('task-verification-deploy-needed-input').checked = false;
  document.register('task-verification-deploy-attempted-input').checked = false;
  document.register('task-verification-human-input');
  document.register('task-verification-notes-input');
  const modal = document.register('modal-task');

  context.state.groups = { alpha: [], beta: [] };
  context.state.agents = {
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'beta' },
  };
  context.state.board_tasks = {
    dep: {
      id: 'dep',
      task: 'Dependency',
      lane: 'Backlog',
    },
    'task-1': {
      id: 'task-1',
      task: 'Fix flakey UI state',
      description: 'Preserve modal data after rerenders',
      labels: ['bug', 'loom:blocked'],
      depends_on: ['dep'],
      attachments: [{ filename: 'screenshot.png' }],
      action_name: 'triage',
      agent_template: 'worker',
      action_vars: { OWNER: 'frontend' },
      group: 'beta',
      scheduled_at: futureIso,
      provider: 'github',
      external_id: 'openai/example#42',
      external_url: 'https://github.com/openai/example/issues/42',
      verification_mode: 'deploy',
      verification_state: 'pending',
      verification_notes: 'Smoke after deploy',
      verification_summary: {
        tests_run: 'npm test -- modal',
        manual_smoke_done: true,
        deploy_needed: true,
        deploy_attempted: false,
        human_validation_pending: 'Confirm the modal in production',
      },
      created_by: 'architect:arch-1',
    },
  };

  context.openEditTask('task-1');

  assert.equal(document.getElementById('task-modal-title').textContent, 'Edit Task');
  assert.equal(document.getElementById('task-submit-btn').textContent, 'Save');
  assert.match(document.getElementById('task-created-by-attribution').innerHTML, /Created by architect Planner \(arch-1\)/);
  assert.match(document.getElementById('task-created-by-attribution').innerHTML, /architect:arch-1/);
  assert.equal(document.getElementById('task-task-input').value, 'Fix flakey UI state');
  assert.equal(document.getElementById('task-description-input').value, 'Preserve modal data after rerenders');
  assert.equal(
    document.getElementById('task-scheduled-input').value,
    new Date(futureIso).toISOString().slice(0, 16),
  );
  assert.deepEqual(jsonValue(context, '_taskLabels'), ['bug']);
  assert.deepEqual(jsonValue(context, '_taskSystemLabels'), ['loom:blocked']);
  assert.deepEqual(jsonValue(context, '_taskDeps'), ['dep']);
  assert.equal(document.getElementById('task-group-select').value, 'beta');
  assert.equal(document.getElementById('task-external-provider-input').value, 'github');
  assert.equal(document.getElementById('task-external-id-input').value, 'openai/example#42');
  assert.equal(
    document.getElementById('task-external-url-input').value,
    'https://github.com/openai/example/issues/42',
  );
  assert.equal(document.getElementById('task-verification-mode-input').value, 'deploy');
  assert.equal(document.getElementById('task-verification-state-input').value, 'pending');
  assert.equal(
    document.getElementById('task-verification-tests-input').value,
    'npm test -- modal',
  );
  assert.equal(document.getElementById('task-verification-smoke-input').checked, true);
  assert.equal(document.getElementById('task-verification-deploy-needed-input').checked, true);
  assert.equal(document.getElementById('task-verification-deploy-attempted-input').checked, false);
  assert.equal(
    document.getElementById('task-verification-human-input').value,
    'Confirm the modal in production',
  );
  assert.equal(document.getElementById('task-verification-notes-input').value, 'Smoke after deploy');
  assert.equal(modal.classList.contains('visible'), true);
  assert.equal(document.getElementById('task-task-input').focused, true);
  assert.equal(document.getElementById('task-task-input').selected, true);
  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    { cmd: 'list_actions', group: 'beta' },
    { cmd: 'list_roles', group: 'beta' },
  ]);
});

test('previewTaskPrompt includes the currently selected role in the preview request', () => {
  const { context, document } = createModalHarness();

  document.register('task-task-input').value = 'Review role preview';
  document.register('task-description-input').value = 'Make sure the preamble shows up';
  document.register('task-group-select').value = 'beta';

  runInContext(context, `
    _taskSelectedAction = 'feature/review';
    _taskSelectedTemplate = 'worker/reviewer';
    _taskAttachments = [];
    _taskArtifacts = [];
    _taskEditId = 'task-1';
    previewTaskPrompt();
  `);

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'preview_prompt',
      task: 'Review role preview',
      description: 'Make sure the preamble shows up',
      action_name: 'feature/review',
      action_vars: {},
      agent_template: 'worker/reviewer',
      group: 'beta',
      attachments: [],
      artifacts: [],
      id: 'task-1',
    },
  ]);
});

test('openEditTask clears past scheduled times instead of showing stale dispatch state', () => {
  const { context, document } = createModalHarness();

  document.register('task-modal-title');
  document.register('task-submit-btn');
  document.register('task-task-input');
  document.register('task-description-input');
  document.register('task-labels-input');
  document.register('task-labels-chips');
  document.register('task-deps-input');
  document.register('task-deps-dropdown');
  document.register('task-deps-chips');
  document.register('task-action-vars');
  document.register('task-scheduled-input');
  document.register('task-group-select');
  document.register('task-template-select');
  document.register('task-action-select');
  document.register('modal-task');

  context.state.groups = { alpha: [] };
  context.state.board_tasks = {
    'task-2': {
      id: 'task-2',
      task: 'Old task',
      group: 'alpha',
      scheduled_at: '2000-01-01T00:00:00.000Z',
    },
  };

  context.openEditTask('task-2');

  assert.equal(document.getElementById('task-scheduled-input').value, '');
});

test('openEditTask resets task modal body scroll to the top', () => {
  const { context, document } = createModalHarness();

  document.register('task-modal-title');
  document.register('task-submit-btn');
  document.register('task-task-input');
  document.register('task-description-input');
  document.register('task-labels-input');
  document.register('task-labels-chips');
  document.register('task-deps-input');
  document.register('task-deps-dropdown');
  document.register('task-deps-chips');
  document.register('task-action-vars');
  document.register('task-scheduled-input');
  document.register('task-group-select');
  document.register('task-template-select');
  document.register('task-action-select');
  document.register('modal-task');
  const taskModalBody = document.register('task-modal-body');
  taskModalBody.scrollTop = 240;

  context.state.groups = { alpha: [] };
  context.state.board_tasks = {
    'task-2': {
      id: 'task-2',
      task: 'Old task',
      group: 'alpha',
    },
  };

  context.openEditTask('task-2');

  assert.equal(taskModalBody.scrollTop, 0);
});

test('standalone quickAddAgent opens the streamlined add-agent modal instead of instant-creating', () => {
  const { context } = createSelectionHarness();
  context.isEmbeddedTerminalMode = function() { return true; };
  context.openAddAgentCalls = [];
  context.openAddAgent = function(group, templateName) {
    context.openAddAgentCalls.push({ group, templateName: templateName || '' });
  };

  context.quickAddAgent('alpha');

  assert.deepEqual(context.openAddAgentCalls, [{ group: 'alpha', templateName: '' }]);
  assert.equal(context.sendCalls.length, 0);
});

test('classic quickAddAgent keeps the existing instant-create path', () => {
  const { context } = createSelectionHarness();

  context.quickAddAgent('alpha');

  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'add_agent',
    name: 'Agent 1',
    group: 'alpha',
  });
});

test('standalone template creation opens the streamlined add-agent modal with the template selected', () => {
  const { context } = createSelectionHarness();
  context.isEmbeddedTerminalMode = function() { return true; };
  context.openAddAgentCalls = [];
  context.openAddAgent = function(group, templateName) {
    context.openAddAgentCalls.push({ group, templateName: templateName || '' });
  };

  context.newAgentFromTemplate('alpha', 'worker/reviewer');

  assert.deepEqual(context.openAddAgentCalls, [{
    group: 'alpha',
    templateName: 'worker/reviewer',
  }]);
  assert.equal(context.sendCalls.length, 0);
});

test('submitAdd includes worktree_name for custom agent worktrees', () => {
  const { context, document } = createModalHarness();

  document.register('add-name-input').value = 'Worker';
  document.register('add-group-select').value = 'alpha';
  document.register('add-cmd-input').value = '';
  document.register('add-profile-select').value = 'Default';
  document.register('add-dir-select').value = '/repo';
  document.register('add-dir-input').value = '';
  document.register('add-shell-select').value = '';
  document.register('add-env-vars').value = '';
  document.register('add-template-select').value = '';
  document.register('add-provider-select').value = '';
  document.register('add-model-input').value = 'gpt-5';
  document.register('add-reasoning-effort').value = 'high';
  document.register('add-wt-enabled').checked = true;
  document.register('add-wt-base-dir').value = '.loom/worktrees';
  document.register('add-wt-base-branch').value = 'main';
  document.register('add-wt-name').value = 'Feature API / v2';
  document.register('add-wt-auto-checkpoint').checked = false;
  document.register('add-wt-checkpoint-on-progress').checked = false;
  document.register('add-wt-squash').checked = true;

  context.submitAdd();

  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'add_agent',
    name: 'Worker',
    group: 'alpha',
    profile: 'Default',
    directory: '/repo',
    model: 'gpt-5',
    reasoning_effort: 'high',
    worktree: true,
    worktree_base_dir: '.loom/worktrees',
    worktree_base_branch: 'main',
    worktree_name: 'Feature API / v2',
    worktree_auto_checkpoint: false,
    checkpoint_on_progress: false,
    worktree_merge_squash: true,
  });
});

test('submitAdd omits worktree_name when custom worktree naming is blank or disabled', () => {
  const { context, document } = createModalHarness();

  document.register('add-name-input').value = 'Worker';
  document.register('add-group-select').value = 'alpha';
  document.register('add-cmd-input').value = '';
  document.register('add-profile-select').value = 'Default';
  document.register('add-dir-select').value = '/repo';
  document.register('add-dir-input').value = '';
  document.register('add-shell-select').value = '';
  document.register('add-env-vars').value = '';
  document.register('add-template-select').value = '';
  document.register('add-provider-select').value = '';
  document.register('add-model-input').value = '';
  document.register('add-reasoning-effort').value = '';
  document.register('add-wt-enabled').checked = true;
  document.register('add-wt-base-dir').value = '.loom/worktrees';
  document.register('add-wt-base-branch').value = 'main';
  document.register('add-wt-name').value = '   ';
  document.register('add-wt-auto-checkpoint').checked = false;
  document.register('add-wt-checkpoint-on-progress').checked = false;
  document.register('add-wt-squash').checked = true;

  context.submitAdd();

  assert.equal(
    Object.prototype.hasOwnProperty.call(jsonValue(context, 'sendCalls[0]'), 'worktree_name'),
    false,
  );

  context.sendCalls.length = 0;
  document.getElementById('add-wt-enabled').checked = false;
  document.getElementById('add-wt-name').value = 'Feature API / v2';

  context.submitAdd();

  assert.equal(
    Object.prototype.hasOwnProperty.call(jsonValue(context, 'sendCalls[0]'), 'worktree_name'),
    false,
  );
  assert.equal(jsonValue(context, 'sendCalls[0].worktree'), false);
});

test('rendered add-agent templates apply model and reasoning effort overrides', () => {
  const { context, document } = createModalHarness();
  [
    'add-provider-select',
    'add-cmd-input',
    'add-cmd-row',
    'add-model-row',
    'add-reasoning-row',
    'add-model-input',
    'add-reasoning-effort',
    'add-shell-select',
    'add-env-vars',
    'add-wt-enabled',
    'add-wt-fields',
    'add-wt-base-dir',
    'add-wt-base-branch',
    'add-wt-auto-checkpoint',
    'add-wt-checkpoint-on-progress',
    'add-wt-squash',
    'add-profile-select',
    'add-dir-select',
    'add-dir-input',
    'add-name-input',
  ].forEach((id) => document.register(id));
  document.getElementById('add-cmd-row').setQuerySelector('label', document.register('add-cmd-row-label'));
  runInContext(context, `
    _cachedAgentTemplates = [];
    _cachedProviders = [{
      name: 'codex',
      display_name: 'Codex',
      command: 'codex',
      reasoning_efforts: ['low', 'medium', 'high'],
    }];
    addCellMode = 'agent';
  `);
  context._applyRenderedAddTemplate({
    provider: 'codex',
    command: 'codex --full-auto',
    model: 'gpt-5',
    reasoning_effort: 'high',
    shell: 'zsh',
    env_vars: {},
    worktree: false,
  }, 'worker/reviewer');

  assert.equal(document.getElementById('add-provider-select').value, 'codex');
  assert.equal(document.getElementById('add-cmd-input').value, 'codex --full-auto');
  assert.equal(document.getElementById('add-model-input').value, 'gpt-5');
  assert.equal(document.getElementById('add-reasoning-effort').value, 'high');
});

test('standalone submitGroup immediately continues into add-agent setup', () => {
  const { context, document } = createModalHarness();
  document.register('group-name-input').value = 'Demo';
  document.register('modal-group');
  const summary = document.register('modal-group-summary');
  summary.classList.add('hidden');
  context.state.runtime = { embedded_terminal: true };
  context.openAddAgentCalls = [];
  context.openAddAgent = function(group) {
    context.openAddAgentCalls.push(group);
  };

  context.submitGroup();

  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'add_group',
    group: 'Demo',
  });
  assert.deepEqual(context.openAddAgentCalls, ['Demo']);
});

test('classic submitGroup keeps the existing single-step flow', () => {
  const { context, document } = createModalHarness();
  document.register('group-name-input').value = 'Demo';
  document.register('modal-group');
  document.register('modal-group-summary');
  context.openAddAgentCalls = [];
  context.openAddAgent = function(group) {
    context.openAddAgentCalls.push(group);
  };

  context.submitGroup();

  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'add_group',
    group: 'Demo',
  });
  assert.deepEqual(context.openAddAgentCalls, []);
});

test('compact standalone add-agent flow keeps advanced options collapsed by default', () => {
  const { context, document } = createModalHarness();
  const details = document.register('add-advanced-details');
  const summary = document.register('add-advanced-summary');

  context.state.runtime = { embedded_terminal: true };
  runInContext(context, `_pendingModal = { advanced: false };`);
  context._setAddAdvancedState('agent');

  assert.equal(details.open, false);
  assert.equal(summary.textContent, 'Advanced options');

  runInContext(context, `_pendingModal = { advanced: true };`);
  context._setAddAdvancedState('agent');
  assert.equal(details.open, true);

  context.state.runtime = { embedded_terminal: false };
  runInContext(context, `_pendingModal = { advanced: false };`);
  context._setAddAdvancedState('agent');
  assert.equal(details.open, true);

  context.state.runtime = { embedded_terminal: true };
  runInContext(context, `_pendingModal = { advanced: false };`);
  context._setAddAdvancedState('terminal');
  assert.equal(details.open, true);
});

test('task modal keeps a scrollable body separate from its footer actions', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(html, /<div id="task-modal-body" class="task-modal-body">[\s\S]*<div class="modal-actions">/);
  assert.match(css, /#modal-task \.modal\s*\{[^}]*overflow:\s*hidden;/);
  assert.match(css, /\.task-modal-body\s*\{[^}]*flex:\s*1;[^}]*overflow-y:\s*auto;/);
});

test('task modal prioritizes labels, dependencies, and schedule before lower-frequency sections', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const descriptionIndex = html.indexOf('<label>Description</label>');
  const labelsIndex = html.indexOf('<label>Labels');
  const depsIndex = html.indexOf('<label>Dependencies');
  const scheduleIndex = html.indexOf('<label>Schedule dispatch');
  const imagesIndex = html.indexOf('<label>Images');
  const externalIndex = html.indexOf('<summary>External');
  const verificationIndex = html.indexOf('<summary>Verification');

  assert.notEqual(descriptionIndex, -1);
  assert.notEqual(labelsIndex, -1);
  assert.notEqual(depsIndex, -1);
  assert.notEqual(scheduleIndex, -1);
  assert.notEqual(imagesIndex, -1);
  assert.notEqual(externalIndex, -1);
  assert.notEqual(verificationIndex, -1);
  assert.ok(descriptionIndex < labelsIndex);
  assert.ok(labelsIndex < depsIndex);
  assert.ok(depsIndex < scheduleIndex);
  assert.ok(scheduleIndex < imagesIndex);
  assert.ok(externalIndex < verificationIndex);
});

test('task modal keeps external and verification collapsed by default with responsive section styles', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(html, /<details class="task-modal-section">\s*<summary>External/);
  assert.match(html, /<details class="task-modal-section">\s*<summary>Verification/);
  assert.equal(html.includes('<details class="task-modal-section" open>'), false);
  assert.match(css, /\.task-modal-section\s*\{/);
  assert.match(css, /\.task-modal-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(120px,\s*1fr\)\);/);
  assert.match(css, /\.task-modal-check-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(160px,\s*1fr\)\);/);
});

test('weaver group settings keep provider first and digest details collapsed by default', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  const providerIndex = html.indexOf('<details id="gs-weaver-provider-section" class="task-modal-section" open>');
  const autonomyIndex = html.indexOf('<details id="gs-weaver-autonomy-section" class="task-modal-section" open>');
  const digestIndex = html.indexOf('<details id="gs-weaver-digest-section" class="task-modal-section">');

  assert.notEqual(providerIndex, -1);
  assert.notEqual(autonomyIndex, -1);
  assert.notEqual(digestIndex, -1);
  assert.ok(providerIndex < autonomyIndex);
  assert.ok(autonomyIndex < digestIndex);
  assert.match(html, /<details id="gs-weaver-provider-section" class="task-modal-section" open>\s*<summary>Provider<\/summary>/);
  assert.match(html, /<details id="gs-weaver-autonomy-section" class="task-modal-section" open>\s*<summary>Autonomy mode/);
  assert.match(html, /<details id="gs-weaver-digest-section" class="task-modal-section">\s*<summary>Digest details<\/summary>/);
  assert.equal(html.includes('<details id="gs-weaver-digest-section" class="task-modal-section" open>'), false);
  assert.match(css, /\.task-modal-section\[open\] summary\s*\{[^}]*border-bottom:\s*1px solid var\(--border\);/);
  assert.match(css, /\.task-modal-section-intro\s*\{[^}]*margin-bottom:\s*8px;/);
});

test('context menu constrains width and clamps wrapped task-title rows', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /#ctx-menu\s*\{[^}]*max-width:\s*min\(320px,\s*calc\(100vw - 8px\)\);/);
  assert.match(css, /#ctx-menu button\.ctx-button-wrap\s*\{[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere;[^}]*-webkit-line-clamp:\s*2;/);
});

test('inline add-task toolbar wraps buttons instead of overflowing narrow card widths', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  // Parent toolbar row must allow wrapping so the right-side group can
  // drop onto a second line when the card is narrow (prevents Submit from
  // clipping off the card edge).
  assert.match(css, /\.board-add-toolbar\s*\{[^}]*flex-wrap:\s*wrap;[^}]*row-gap:\s*4px;/);
  // The right-side group (agent / action / lane / submit) must also wrap
  // internally — the four pill buttons can themselves outgrow the row.
  assert.match(css, /\.board-add-toolbar-right\s*\{[^}]*flex-wrap:\s*wrap;[^}]*row-gap:\s*4px;/);
});

test('submitTask includes structured artifacts alongside attachments when editing a task', () => {
  const { context, document } = createModalHarness();

  document.register('task-task-input').value = 'Investigate flaky review run';
  document.register('task-group-select').value = 'alpha';
  document.register('task-description-input').value = 'Capture the failing logs';
  document.register('task-template-select').value = '';
  document.register('task-labels-input').value = '';
  document.register('task-scheduled-input').value = '';
  document.register('modal-task').dataset.lane = 'Backlog';

  runInContext(context, `
    _taskEditId = 'task-9';
    _taskSelectedAction = 'feature/review';
    _taskSelectedTemplate = '';
    _taskAttachments = [{ filename: 'screenshot.png', path: '/tmp/screenshot.png', mime_type: 'image/png' }];
    _taskArtifacts = [{
      type: 'log',
      title: 'pytest.log',
      filename: 'pytest.log',
      path: '/tmp/pytest.log',
      summary: 'Last failing run',
      content: 'E assert 1 == 2',
      prompt: { mode: 'summary' },
      storage: { kind: 'path', path: '/tmp/pytest.log', content: 'E assert 1 == 2' },
      lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
    }];
    _taskOriginalAttachments = [];
    _taskOriginalArtifacts = [];
  `);

  context.submitTask();

  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'board_update_task',
    id: 'task-9',
    task: 'Investigate flaky review run',
    group: 'alpha',
    description: 'Capture the failing logs',
    action_name: 'feature/review',
    agent_template: '',
    action_vars: {},
    labels: [],
    scheduled_at: '',
    depends_on: [],
    attachments: [{ filename: 'screenshot.png', path: '/tmp/screenshot.png', mime_type: 'image/png' }],
    provider: '',
    external_id: '',
    external_url: '',
    artifacts: [{
      type: 'log',
      title: 'pytest.log',
      filename: 'pytest.log',
      path: '/tmp/pytest.log',
      summary: 'Last failing run',
      content: 'E assert 1 == 2',
      prompt: { mode: 'summary' },
      storage: { kind: 'path', path: '/tmp/pytest.log', content: 'E assert 1 == 2' },
      lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
    }],
  });
});

test('openTaskArtifactById opens a preserved artifact directly when a file URL is available', () => {
  const opened = [];
  const { sandbox } = createSandbox({
    window: {
      open(url) { opened.push(url); },
    },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals/task-artifacts.js');

  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Review merge diff',
      artifacts: [{
        id: 'artifact-1',
        type: 'diff',
        filename: 'worker-pre-merge.patch',
        path: '/tmp/worker-pre-merge.patch',
        storage: { kind: 'path', path: '/tmp/worker-pre-merge.patch', content: '' },
        prompt: { mode: 'summary' },
      }],
    },
  };

  assert.equal(runInContext(context, `openTaskArtifactById('task-1', 'artifact-1')`), true);
  assert.deepEqual(opened, ['/attachments/task-1/worker-pre-merge.patch']);
});

test('openTaskArtifactById prefers filename and path when artifact ids are duplicated', () => {
  const opened = [];
  const { sandbox } = createSandbox({
    window: {
      open(url) { opened.push(url); },
    },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals/task-artifacts.js');

  context.state.board_tasks = {
    boundary: {
      id: 'boundary',
      task: 'Boundary task',
      artifacts: [
        {
          id: 'artifact-1',
          type: 'log',
          filename: 'notes.txt',
          path: '/tmp/notes.txt',
          storage: { kind: 'path', path: '/tmp/notes.txt', content: '' },
          prompt: { mode: 'summary' },
        },
        {
          id: 'artifact-1',
          type: 'diff',
          filename: 'worker-pre-merge.patch',
          path: '/tmp/worker-pre-merge.patch',
          storage: { kind: 'path', path: '/tmp/worker-pre-merge.patch', content: '' },
          prompt: { mode: 'summary' },
        },
      ],
    },
  };

  assert.equal(
    runInContext(
      context,
      `openTaskArtifactById('boundary', 'artifact-1', 'worker-pre-merge.patch', '/tmp/worker-pre-merge.patch')`
    ),
    true
  );
  assert.deepEqual(opened, ['/attachments/boundary/worker-pre-merge.patch']);
});

test('diff review surfaces related task artifacts next to the synthesized diff artifact', () => {
  const { context } = createDiffHarness();
  context.state.agents = {
    'agent-1': {
      id: 'agent-1',
      current_task_id: 'task-1',
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Review auth patch',
      agent_id: 'agent-1',
      lane: 'In Progress',
      updated_at: '2099-01-01T00:00:00Z',
      artifacts: [{
        type: 'test_report',
        title: 'pytest report',
        content: '2 failed, 18 passed',
        prompt: { mode: 'summary' },
      }],
    },
  };

  runInContext(context, `
    _diffViewAgentId = 'agent-1';
    _diffViewData = {
      stats: { files: 1, insertions: 4, deletions: 2 },
      files: [{ path: 'auth.py', status: 'modified' }],
    };
  `);

  assert.deepEqual(jsonValue(context, `_diffRelatedArtifacts().map(function(a) {
    return { title: a.title, type: a.type, taskLabel: a.taskLabel || '' };
  })`), [
    { title: 'Worktree diff', type: 'diff', taskLabel: '' },
    { title: 'pytest report', type: 'test_report', taskLabel: 'Review auth patch' },
  ]);
});

test('diff review bulk collapse controls stay stable across refreshes', () => {
  const { context, document } = createDiffHarness();
  const root = document.getElementById('diff-view-root');

  runInContext(context, `
    _diffViewOpen = true;
    _diffViewData = {
      agent_name: 'Worker',
      branch: 'loom/worker',
      base_branch: 'main',
      stats: { files: 2, insertions: 4, deletions: 1 },
      files: [
        {
          path: 'src/a.js',
          status: 'modified',
          insertions: 2,
          deletions: 1,
          hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'a' }] }],
        },
        {
          path: 'src/b.js',
          status: 'modified',
          insertions: 2,
          deletions: 0,
          hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'b' }] }],
        },
      ],
    };
    renderDiffView();
  `);

  assert.match(root.innerHTML, /Collapse all/);
  assert.match(root.innerHTML, /Expand all/);
  assert.match(root.innerHTML, /0 of 2 collapsed/);

  runInContext(context, `diffSetAllFilesCollapsed(true)`);

  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/a.js')`), true);
  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/b.js')`), true);
  assert.match(root.innerHTML, /2 of 2 collapsed/);

  runInContext(context, `
    _diffViewData = {
      agent_name: 'Worker',
      branch: 'loom/worker',
      base_branch: 'main',
      stats: { files: 3, insertions: 5, deletions: 1 },
      files: [
        {
          path: 'src/a.js',
          status: 'modified',
          insertions: 2,
          deletions: 1,
          hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'a' }] }],
        },
        {
          path: 'src/b.js',
          status: 'modified',
          insertions: 2,
          deletions: 0,
          hunks: [{ header: '@@ -1 +1 @@', lines: [{ type: 'add', text: 'b' }] }],
        },
        {
          path: 'src/c.js',
          status: 'added',
          insertions: 1,
          deletions: 0,
          hunks: [{ header: '@@ -0,0 +1 @@', lines: [{ type: 'add', text: 'c' }] }],
        },
      ],
    };
    renderDiffView();
  `);

  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/c.js')`), true);
  assert.match(root.innerHTML, /3 of 3 collapsed/);

  runInContext(context, `toggleDiffFile('src/a.js')`);

  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/a.js')`), false);
  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/b.js')`), true);
  assert.match(root.innerHTML, /2 of 3 collapsed/);

  runInContext(context, `diffSetAllFilesCollapsed(false)`);

  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/b.js')`), false);
  assert.equal(jsonValue(context, `_isDiffFileCollapsed('src/c.js')`), false);
  assert.match(root.innerHTML, /0 of 3 collapsed/);
});

test('diff review surfaces stale-base warning before merge controls', () => {
  const { context, document } = createDiffHarness();
  const root = document.getElementById('diff-view-root');

  runInContext(context, `
    _diffViewOpen = true;
    _diffViewAgentId = 'agent-1';
    _diffViewData = {
      agent_name: 'Worker',
      branch: 'loom/worker',
      base_branch: 'main',
      stats: { files: 1, insertions: 1, deletions: 0 },
      files: [{ path: 'src/a.js', status: 'modified', hunks: [] }],
      stale_base_warning: '⚠ STALE BASE: loom/worker forks from 11111111 (old).',
      stale_base: { stale: true },
    };
    _diffMergeCheck = {
      clean: false,
      conflicts: [],
      stale_base_warning: '⚠ STALE BASE: loom/worker forks from 11111111 (old).',
      stale_base: { stale: true },
    };
    renderDiffView();
  `);

  assert.match(root.innerHTML, /diff-stale-base-warning/);
  assert.match(root.innerHTML, /STALE BASE/);
  assert.match(root.innerHTML, /Rebase onto Main/);
});

test('diff review overlay hides the workspace shell so standalone merge review uses the full viewport', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /body\.diff-view-open #workspace-shell,\s*body\.diff-view-open main,\s*body\.diff-view-open #standalone-bottom-dock,\s*body\.diff-view-open #standalone-right-rail,\s*body\.diff-view-open #standalone-float-layer,\s*body\.diff-view-open #bottom-panel,\s*body\.diff-view-open #taskbar,\s*body\.diff-view-open #broadcast,\s*body\.diff-view-open #ctx-menu\s*\{[^}]*display:\s*none\s*!important;/);
});

test('Escape closes the read-only diff viewer through the shared key handler', () => {
  const { context, document } = createDiffKeyHarness();

  runInContext(context, `
    _diffViewOpen = true;
    _diffReadOnly = true;
    _diffViewAgentId = 'agent-1';
    _diffViewData = {
      agent_name: 'Worker',
      branch: 'loom/worker',
      base_branch: 'main',
      stats: { files: 1, insertions: 2, deletions: 1 },
      files: [],
    };
    renderDiffView();
  `);

  let prevented = false;
  document.listeners.keydown({
    key: 'Escape',
    preventDefault() { prevented = true; },
    shiftKey: false,
  });

  assert.equal(prevented, true);
  assert.equal(jsonValue(context, `_diffViewOpen`), false);
  assert.equal(document.getElementById('diff-view-root').innerHTML, '');
  assert.equal(document.body.classList.contains('diff-view-open'), false);
});

test('Escape prefers the active overlay over the underlying read-only diff viewer', () => {
  const { context, document, confirmOverlay } = createDiffKeyHarness();

  runInContext(context, `
    _diffViewOpen = true;
    _diffReadOnly = true;
    _diffViewAgentId = 'agent-1';
    _diffViewData = {
      agent_name: 'Worker',
      branch: 'loom/worker',
      base_branch: 'main',
      stats: { files: 1, insertions: 2, deletions: 1 },
      files: [],
    };
    renderDiffView();
  `);
  context.showConfirm('Close this modal first?');

  document.listeners.keydown({
    key: 'Escape',
    preventDefault() {},
    shiftKey: false,
  });

  assert.equal(confirmOverlay.classList.contains('visible'), false);
  assert.equal(jsonValue(context, `_diffViewOpen`), true);
  assert.notEqual(document.getElementById('diff-view-root').innerHTML, '');
  assert.equal(document.body.classList.contains('diff-view-open'), true);
});

test('task history opens in a visible modal overlay without taking over the whole body layout', () => {
  const { context, document, sandbox, overlay } = createTaskHistoryHarness();

  context.showTaskHistory('agent-1');

  assert.equal(jsonValue(context, `_taskHistoryOpen`), true);
  assert.equal(overlay.classList.contains('visible'), true);
  assert.equal(document.body.classList.contains('task-history-open'), false);
  assert.match(document.getElementById('task-history-root').innerHTML, /Close/);
  assert.deepEqual(jsonValue(context, `sendCalls`), [
    { cmd: 'get_agent_history_detail', agent_id: 'agent-1' },
  ]);
});

test('closeModals clears task history modal state and content', () => {
  const { context, document, overlay } = createTaskHistoryHarness();

  context.showTaskHistory('agent-1');
  context.closeModals();

  assert.equal(jsonValue(context, `_taskHistoryOpen`), false);
  assert.equal(overlay.classList.contains('visible'), false);
  assert.equal(document.getElementById('task-history-root').innerHTML, '');
});

test('Escape closes task history through the shared overlay key handler', () => {
  const { context, document, overlay } = createTaskHistoryHarness({ withMain: true });

  context.showTaskHistory('agent-1');
  document.listeners.keydown({
    key: 'Escape',
    preventDefault() {},
    shiftKey: false,
  });

  assert.equal(jsonValue(context, `_taskHistoryOpen`), false);
  assert.equal(overlay.classList.contains('visible'), false);
  assert.equal(document.getElementById('task-history-root').innerHTML, '');
});

test('Cmd+Option+E opens the add engineer modal through the shared key handler', () => {
  const { document, sandbox } = createMainHarness({
    openAddEngineerModal() {
      sandbox.addEngineerModalCalls = (sandbox.addEngineerModalCalls || 0) + 1;
    },
  });
  let prevented = false;

  document.listeners.keydown({
    key: 'E',
    metaKey: true,
    altKey: true,
    ctrlKey: false,
    shiftKey: false,
    preventDefault() { prevented = true; },
  });

  assert.equal(prevented, true);
  assert.equal(sandbox.addEngineerModalCalls, 1);
});

test('Cmd+Option+P opens the add architect modal through the shared key handler', () => {
  const { document, sandbox } = createMainHarness({
    openAddArchitectModal(group) {
      sandbox.addArchitectModalCalls = (sandbox.addArchitectModalCalls || []);
      sandbox.addArchitectModalCalls.push(group);
    },
    _agentPanelCurrentGroup() {
      return 'loom';
    },
  });
  let prevented = false;

  document.listeners.keydown({
    key: 'P',
    metaKey: true,
    altKey: true,
    ctrlKey: false,
    shiftKey: false,
    preventDefault() { prevented = true; },
  });

  assert.equal(prevented, true);
  assert.deepEqual(sandbox.addArchitectModalCalls, ['loom']);
});

test('full state toggles embedded runtime body class', () => {
  const { context, document } = createWsRenderHarness();

  runInContext(context, `
    _handleFullState({
      seq: 7,
      groups: {},
      agents: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: [],
      runtime: { embedded_terminal: true },
    });
  `);

  assert.equal(document.body.classList.contains('runtime-embedded'), true);

  runInContext(context, `
    _handleFullState({
      seq: 8,
      groups: {},
      agents: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: [],
      runtime: { embedded_terminal: false },
    });
  `);

  assert.equal(document.body.classList.contains('runtime-embedded'), false);
});

test('embedded runtime reuses the shared group, cell, and terminal UI', () => {
  const { context, document, sandbox } = createStandaloneRenderHarness();
  const main = document.getElementById('main');

  sandbox._cachedAgentTemplates = [{ name: 'fixer', display_name: 'Fixer', shadowed: false }];
  sandbox.state.groups = { alpha: ['agent-1', 'term-root'] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = { 'agent-1': ['term-child'] };
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Runner',
      group: 'alpha',
      cell_type: 'agent',
      icon: 'A',
      command: 'codex',
      status: 'idle',
      session_id: 'sess-1',
      activity_detail: 'Reviewing patch',
    },
    'term-child': {
      id: 'term-child',
      name: 'Shell Child',
      group: 'alpha',
      cell_type: 'terminal',
      parent_id: 'agent-1',
      current_process: 'zsh',
      current_path: '/tmp/child',
      status: 'idle',
      session_id: 'sess-2',
    },
    'term-root': {
      id: 'term-root',
      name: 'Shell Root',
      group: 'alpha',
      cell_type: 'terminal',
      current_process: 'bash',
      current_path: '/tmp/root',
      status: 'stopped',
      session_id: '',
    },
  };

  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'term-child';
    render();
  `);

  assert.match(main.innerHTML, /class="group/);
  assert.doesNotMatch(main.innerHTML, /sidebar-group/);
  assert.match(main.innerHTML, /Runner/);
  assert.match(main.innerHTML, /Reviewing patch/);
  assert.match(main.innerHTML, /Runner terminals/);
  assert.match(main.innerHTML, /Shell Child/);
  assert.match(main.innerHTML, /Shell Root/);
  assert.match(main.innerHTML, /class="cell[^"]*selected/);
  assert.match(main.innerHTML, /class="term-row/);
  assert.match(main.innerHTML, /ghost-card ghost-card--worker[\s\S]*openAddWorkerModal\('alpha'\)/);
  assert.match(main.innerHTML, /ghost-card ghost-card--engineer[\s\S]*openAddEngineerForSection\(&quot;alpha&quot;,&quot;&quot;\)/);
  assert.match(main.innerHTML, /ghost-card ghost-card--architect[\s\S]*openAddArchitectForGroup\(&quot;alpha&quot;\)/);
  assert.doesNotMatch(main.innerHTML, /quickAddAgent\('alpha'\)/);
  assert.doesNotMatch(main.innerHTML, /openAddAgentAdvanced\('alpha'\)/);
  assert.match(main.innerHTML, /quickAddTerminal\('alpha','agent-1'\)/);
  assert.match(main.innerHTML, /openAddTerminal\('alpha','agent-1'\)/);
  assert.equal(sandbox.renderTerminalWorkspaceCalls, 1);
});

test('embedded runtime hides stale agent details when a standalone terminal is selected', () => {
  const { context, document, sandbox } = createStandaloneRenderHarness();
  const main = document.getElementById('main');

  sandbox._cachedAgentTemplates = [];
  sandbox.state.groups = { alpha: ['agent-1', 'term-root'] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Runner',
      group: 'alpha',
      cell_type: 'agent',
      icon: 'A',
      status: 'idle',
      session_id: 'sess-1',
    },
    'term-root': {
      id: 'term-root',
      name: 'Shell Root',
      group: 'alpha',
      cell_type: 'terminal',
      current_process: 'bash',
      current_path: '/tmp/root',
      status: 'idle',
      session_id: 'sess-2',
    },
  };

  runInContext(context, `
    selectedAgentId = 'agent-1';
    selectedTerminalId = 'term-root';
    render();
  `);

  assert.doesNotMatch(main.innerHTML, /Runner terminals/);
  assert.doesNotMatch(main.innerHTML, /class="agent-details"/);
  assert.match(main.innerHTML, /Shell Root/);
});

test('main agent grid omits the retired legacy creation dropdown', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { alpha: [] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render();`);

  assert.doesNotMatch(main.innerHTML, /cell cell-add/);
  assert.doesNotMatch(main.innerHTML, /cell-add-drop/);
  assert.doesNotMatch(main.innerHTML, /agent-grid-global-add-row/);
  assert.doesNotMatch(main.innerHTML, /agent-add-menu/);
  assert.match(main.innerHTML, /ghost-card ghost-card--worker[\s\S]*\+ New Worker/);
  assert.match(main.innerHTML, /ghost-card ghost-card--engineer[\s\S]*\+ New Engineer/);
  assert.match(main.innerHTML, /ghost-card ghost-card--architect[\s\S]*\+ New Architect/);
});

test('standalone sidebar formats repo and home paths compactly', () => {
  const { context, document, sandbox } = createStandaloneRenderHarness();
  const main = document.getElementById('main');

  sandbox._cachedAgentTemplates = [];
  sandbox.state.runtime = { home_directory: '/Users/aleks' };
  sandbox.state.groups = { alpha: ['agent-1', 'term-1'] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Runner',
      group: 'alpha',
      cell_type: 'agent',
      icon: 'A',
      command: 'codex',
      status: 'idle',
      session_id: 'sess-1',
      directory: '/Users/aleks/dev/personal/gh/iterm2-loom',
      current_path: '/Users/aleks/dev/personal/gh/iterm2-loom/docs',
      git_root: '/Users/aleks/dev/personal/gh/iterm2-loom',
    },
    'term-1': {
      id: 'term-1',
      name: 'Shell Root',
      group: 'alpha',
      cell_type: 'terminal',
      current_process: 'bash',
      current_path: '/Users/aleks/dev/personal/scratch',
      status: 'idle',
      session_id: 'sess-2',
    },
  };

  runInContext(context, `
    var selectedTerminalId = '';
    render();
  `);

  assert.match(main.innerHTML, /iterm2-loom\/docs/);
  assert.doesNotMatch(main.innerHTML, /\/Users\/aleks\/dev\/personal\/gh\/iterm2-loom\/docs/);
  assert.match(main.innerHTML, /~\/dev\/personal\/scratch/);
  assert.equal(
    jsonValue(
      context,
      `_formatDisplayPath('/Users/aleks/dev/personal/gh/iterm2-loom/docs', '/Users/aleks/dev/personal/gh/iterm2-loom')`
    ),
    'iterm2-loom/docs'
  );
});

test('standalone runtime metadata does not change embedded-runtime detection', () => {
  const { context, sandbox } = createStandaloneRenderHarness();

  sandbox.state.runtime = {
    embedded_terminal: true,
    profile: 'desktop',
    data_dir: '/Users/aleks/.loom/profiles/desktop',
    port: 18933,
    home_directory: '/Users/aleks',
  };

  assert.equal(jsonValue(context, `_embeddedRuntimeEnabled()`), true);
});


test('architect section + New Engineer ghost opens a scoped modal and submits architect hire context', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const modalDom = registerEngineerModalDom(document);
  loadModalScripts(context);
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['arch-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Productmind',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'P',
      status: 'running',
      created_at: 1,
    },
  };

  runInContext(context, `render();`);

  assert.match(main.innerHTML, /data-agent-section="architect:arch-a"/);
  assert.match(main.innerHTML, /agent-section-architect-column[\s\S]*Productmind/);
  assert.match(main.innerHTML, /agent-section-body[\s\S]*class="ghost-card ghost-card--engineer"/);
  assert.match(main.innerHTML, /class="ghost-card ghost-card--engineer"/);
  assert.match(main.innerHTML, /data-hired-by-architect-id="arch-a"/);
  assert.match(main.innerHTML, /openAddEngineerForSection\(&quot;loom&quot;,&quot;arch-a&quot;\)/);
  assert.doesNotMatch(main.innerHTML, /architect-header-row|agent-section-header-row/);

  const click = main.innerHTML.match(/onclick="([^"]*openAddEngineerForSection\(&quot;loom&quot;,&quot;arch-a&quot;\)[^"]*)"/);
  assert.ok(click, 'new-engineer ghost should have a scoped click handler');
  runInContext(context, `
    var event = { stopPropagation() {} };
    ${click[1].replace(/&quot;/g, '"')};
  `);
  assert.equal(modalDom.modal.classList.contains('visible'), true);
  assert.equal(modalDom.summary.textContent, 'Create a persistent engineer session hired by Productmind in loom.');
  modalDom.nameInput.value = 'Casey';
  modalDom.commandInput.value = 'codex --fast';

  runInContext(context, `submitAddEngineer();`);

  assert.deepEqual(jsonValue(context, `sendCalls`), [{
    cmd: 'add_engineer',
    name: 'Casey',
    group: 'loom',
    hired_by_architect_id: 'arch-a',
    command: 'codex --fast',
  }]);
});

test('user section + New Engineer ghost submits a user-hired engineer without architect context', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const modalDom = registerEngineerModalDom(document);
  loadModalScripts(context);
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: [] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render();`);

  assert.match(main.innerHTML, /data-agent-section="user"/);
  assert.match(main.innerHTML, /agent-section-architect-column[\s\S]*agent-section-user-card[\s\S]*User/);
  assert.match(main.innerHTML, /agent-section-body[\s\S]*class="ghost-card ghost-card--engineer"/);
  assert.match(main.innerHTML, /class="ghost-card ghost-card--engineer"/);
  assert.match(main.innerHTML, /data-hired-by-architect-id=""/);
  assert.match(main.innerHTML, /openAddEngineerForSection\(&quot;loom&quot;,&quot;&quot;\)/);
  assert.doesNotMatch(main.innerHTML, /architect-header-row|agent-section-header-row/);

  runInContext(context, `openAddEngineerForSection('loom', '');`);
  assert.equal(modalDom.summary.textContent, 'Create a persistent user-hired engineer session in loom.');
  modalDom.nameInput.value = 'User Engineer';

  runInContext(context, `submitAddEngineer();`);

  const sendCalls = jsonValue(context, `sendCalls`);
  assert.deepEqual(sendCalls, [{
    cmd: 'add_engineer',
    name: 'User Engineer',
    group: 'loom',
  }]);
  assert.equal(Object.prototype.hasOwnProperty.call(sendCalls[0], 'hired_by_architect_id'), false);
});

test('user section renders + New Architect ghost in the architect column below User only', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['arch-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      created_at: 1,
    },
  };

  runInContext(context, `render();`);

  assert.equal((main.innerHTML.match(/ghost-card ghost-card--architect/g) || []).length, 1);
  assert.match(
    main.innerHTML,
    /data-agent-section="user"[\s\S]*agent-section-architect-column[\s\S]*agent-section-user-card[\s\S]*\+ New Architect[\s\S]*agent-section-body/
  );
  assert.match(
    main.innerHTML,
    /data-agent-section="user"[\s\S]*\+ New Architect[\s\S]*agent-section-divider[\s\S]*data-agent-section="architect:arch-a"/
  );
  assert.doesNotMatch(main.innerHTML, /agent-grid-new-architect-row/);
  assert.doesNotMatch(main.innerHTML, /data-agent-section="architect:arch-a"[\s\S]*\+ New Architect/);
});

test('user-section + New Architect ghost opens the group-scoped architect modal', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const modalDom = registerArchitectModalDom(document);
  loadModalScripts(context);
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['arch-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      created_at: 1,
    },
  };

  runInContext(context, `render();`);

  assert.match(main.innerHTML, /class="ghost-card ghost-card--architect"/);
  assert.match(main.innerHTML, /openAddArchitectForGroup\(&quot;loom&quot;\)/);
  assert.match(main.innerHTML, /agent-section-user-card[\s\S]*\+ New Architect[\s\S]*Architect A/);
  assert.doesNotMatch(main.innerHTML, /agent-grid-new-architect-row/);
  assert.doesNotMatch(main.innerHTML, /cell cell-add/);

  const click = main.innerHTML.match(/onclick="([^"]*openAddArchitectForGroup\(&quot;loom&quot;\)[^"]*)"/);
  assert.ok(click, 'new-architect ghost should have a click handler');
  runInContext(context, `
    var event = { stopPropagation() {} };
    ${click[1].replace(/&quot;/g, '"')};
  `);
  assert.equal(modalDom.modal.classList.contains('visible'), true);
  assert.equal(modalDom.summary.textContent, 'Create a persistent architect session for loom.');
  modalDom.nameInput.value = 'Second Architect';
  modalDom.commandInput.value = 'codex architect';

  runInContext(context, `submitAddArchitect();`);

  assert.deepEqual(jsonValue(context, `sendCalls`), [{
    cmd: 'add_architect',
    name: 'Second Architect',
    group: 'loom',
    command: 'codex architect',
  }]);
});

test('hierarchical creation controls render shared quarter-height ghost-card classes and labels', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: [] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render();`);

  assert.match(main.innerHTML, /class="ghost-card ghost-card--worker loose-workers-new-worker-btn"[\s\S]*\+ New Worker/);
  assert.match(main.innerHTML, /class="ghost-card ghost-card--engineer"[\s\S]*\+ New Engineer/);
  assert.match(main.innerHTML, /class="ghost-card ghost-card--architect"[\s\S]*\+ New Architect/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(css, /--agent-grid-card-height:\s*64px;/);
  assert.match(css, /--agent-ghost-card-height:\s*calc\(var\(--agent-grid-card-height\) \/ 4\);/);
  assert.match(css, /\.ghost-card\s*\{[\s\S]*height:\s*var\(--agent-ghost-card-height\)/);
  assert.match(css, /\.ghost-card--architect\s*\{[\s\S]*width:\s*var\(--agent-architect-column-width\)/);
  assert.match(css, /\.ghost-card--engineer\s*\{[\s\S]*width:\s*var\(--agent-engineer-column-width\)/);
  assert.match(css, /\.ghost-card--worker\s*\{[\s\S]*flex:\s*0 1 var\(--agent-grid-card-basis\)/);
  assert.match(css, /body\.runtime-embedded \.agent-grid\s*\{[\s\S]*--agent-grid-card-height:\s*80px;/);
});

test('classic runtime keeps the shared left rail filtered to the current window', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.current_window_id = 'window-a';
  sandbox.state.groups = { alpha: ['agent-a', 'agent-b', 'term-b'] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'agent-a': {
      id: 'agent-a',
      name: 'Worker A',
      icon: 'A',
      group: 'alpha',
      cell_type: 'agent',
      window_id: 'window-a',
      status: 'running',
      session_id: 'sess-a',
    },
    'agent-b': {
      id: 'agent-b',
      name: 'Worker B',
      icon: 'B',
      group: 'alpha',
      cell_type: 'agent',
      window_id: 'window-b',
      status: 'running',
      session_id: 'sess-b',
    },
    'term-b': {
      id: 'term-b',
      name: 'Shell B',
      group: 'alpha',
      cell_type: 'terminal',
      window_id: 'window-b',
      status: 'idle',
      current_process: 'zsh',
    },
  };
  runInContext(context, `
    getFilterByWindow = function() { return true; };
    render();
  `);

  assert.match(main.innerHTML, /Worker A/);
  assert.doesNotMatch(main.innerHTML, /Worker B/);
  assert.doesNotMatch(main.innerHTML, /Shell B/);
  assert.deepEqual(jsonValue(context, `window._navAgents`), ['agent-a']);
});

test('main render groups user loose workers before engineer rows and exposes row primitives', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = {
    loom: ['eng-a', 'worker-a', 'eng-b', 'worker-b', 'agent-user'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: 'eng-a' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-a': {
      id: 'eng-a',
      name: 'Alice',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'sess-eng-a',
      created_at: 10,
    },
    'worker-a': {
      id: 'worker-a',
      name: 'Worker A',
      kind: 'worker',
      owner_engineer_id: 'eng-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '1',
      status: 'running',
      session_id: 'sess-worker-a',
      created_at: 11,
    },
    'eng-b': {
      id: 'eng-b',
      name: 'Bob',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
      session_id: 'sess-eng-b',
      created_at: 20,
    },
    'worker-b': {
      id: 'worker-b',
      name: 'Worker B',
      kind: 'worker',
      owner_engineer_id: 'eng-b',
      group: 'loom',
      cell_type: 'agent',
      icon: '3',
      status: 'running',
      session_id: 'sess-worker-b',
      created_at: 21,
    },
    'agent-user': {
      id: 'agent-user',
      name: 'User Worker',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'U',
      status: 'running',
      session_id: 'sess-user',
      created_at: 30,
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'agent-user',
    'eng-a',
    'worker-a',
    'eng-b',
    'worker-b',
  ]);
  assert.match(main.innerHTML, /User Worker[\s\S]*Alice[\s\S]*Worker A[\s\S]*Bob[\s\S]*Worker B/);
  assert.match(main.innerHTML, /agent-section-architect-column/);
  assert.match(main.innerHTML, /agent-section-body/);
  assert.doesNotMatch(main.innerHTML, /architect-header-row|agent-section-header-row/);
  assert.match(main.innerHTML, /loose-workers-strip/);
  assert.match(main.innerHTML, /engineer-row/);
  assert.match(main.innerHTML, /cell-engineer-badge/);
  assert.doesNotMatch(
    main.innerHTML,
    new RegExp('engineer-' + 'owned-worker|architect-' + 'owned-engineer|architect-' + 'owned-worker'),
  );
});

test('main render falls back to state.groups ordering within hierarchy buckets without creation timestamps', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = {
    loom: ['worker-a-2', 'eng-b', 'worker-b-2', 'eng-a', 'worker-b-1', 'agent-user', 'worker-a-1'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: 'eng-a' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-a': {
      id: 'eng-a',
      name: 'Alice',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'sess-eng-a',
    },
    'eng-b': {
      id: 'eng-b',
      name: 'Bob',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'B',
      status: 'running',
      session_id: 'sess-eng-b',
    },
    'worker-a-1': {
      id: 'worker-a-1',
      name: 'Worker A1',
      kind: 'worker',
      owner_engineer_id: 'eng-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '1',
      status: 'running',
      session_id: 'sess-worker-a-1',
    },
    'worker-a-2': {
      id: 'worker-a-2',
      name: 'Worker A2',
      kind: 'worker',
      owner_engineer_id: 'eng-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
      session_id: 'sess-worker-a-2',
    },
    'worker-b-1': {
      id: 'worker-b-1',
      name: 'Worker B1',
      kind: 'worker',
      owner_engineer_id: 'eng-b',
      group: 'loom',
      cell_type: 'agent',
      icon: '3',
      status: 'running',
      session_id: 'sess-worker-b-1',
    },
    'worker-b-2': {
      id: 'worker-b-2',
      name: 'Worker B2',
      kind: 'worker',
      owner_engineer_id: 'eng-b',
      group: 'loom',
      cell_type: 'agent',
      icon: '4',
      status: 'running',
      session_id: 'sess-worker-b-2',
    },
    'agent-user': {
      id: 'agent-user',
      name: 'User Worker',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'U',
      status: 'running',
      session_id: 'sess-user',
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'agent-user',
    'eng-b',
    'worker-b-2',
    'worker-b-1',
    'eng-a',
    'worker-a-2',
    'worker-a-1',
  ]);
  assert.match(main.innerHTML, /User Worker[\s\S]*Bob[\s\S]*Worker B2[\s\S]*Worker B1[\s\S]*Alice[\s\S]*Worker A2[\s\S]*Worker A1/);
});

test('main render orders sections user-first then architect creation order', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = {
    loom: ['worker-hired-a', 'arch-a', 'eng-hired-a', 'arch-b', 'eng-hired-b', 'worker-user', 'eng-user', 'worker-user-owned'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: '' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'sess-arch-a',
      created_at: 30,
    },
    'arch-b': {
      id: 'arch-b',
      name: 'Architect B',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'B',
      status: 'running',
      session_id: 'sess-arch-b',
      created_at: 20,
    },
    'eng-hired-a': {
      id: 'eng-hired-a',
      name: 'Alice A',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
      session_id: 'sess-eng-hired-a',
      created_at: 31,
    },
    'worker-hired-a': {
      id: 'worker-hired-a',
      name: 'Worker A',
      kind: 'worker',
      owner_engineer_id: 'eng-hired-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
      session_id: 'sess-worker-hired-a',
      created_at: 32,
    },
    'eng-hired-b': {
      id: 'eng-hired-b',
      name: 'Alice B',
      kind: 'engineer',
      hired_by_architect_id: 'arch-b',
      group: 'loom',
      cell_type: 'agent',
      icon: 'F',
      status: 'running',
      session_id: 'sess-eng-hired-b',
      created_at: 21,
    },
    'eng-user': {
      id: 'eng-user',
      name: 'Bob',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'U',
      status: 'running',
      session_id: 'sess-eng-user',
      created_at: 10,
    },
    'worker-user-owned': {
      id: 'worker-user-owned',
      name: 'Worker B',
      kind: 'worker',
      owner_engineer_id: 'eng-user',
      group: 'loom',
      cell_type: 'agent',
      icon: 'V',
      status: 'running',
      session_id: 'sess-worker-user',
      created_at: 11,
    },
    'worker-user': {
      id: 'worker-user',
      name: 'Loose Worker',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'O',
      status: 'running',
      session_id: 'sess-loose',
      created_at: 5,
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'worker-user',
    'eng-user',
    'worker-user-owned',
    'arch-b',
    'eng-hired-b',
    'arch-a',
    'eng-hired-a',
    'worker-hired-a',
  ]);
  assert.match(main.innerHTML, /Loose Worker[\s\S]*Bob[\s\S]*Worker B[\s\S]*Architect B[\s\S]*Alice B[\s\S]*Architect A[\s\S]*Alice A[\s\S]*Worker A/);
  assert.match(main.innerHTML, /cell-architect-badge/);
  assert.match(main.innerHTML, /agent-section-divider/);
});

test('main render uses containment primitives and retires cell hierarchy indentation classes', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = {
    loom: ['arch-a', 'eng-hired', 'worker-hired', 'orphan'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: '' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
    },
    'eng-hired': {
      id: 'eng-hired',
      name: 'Alice',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
    },
    'worker-hired': {
      id: 'worker-hired',
      name: 'Worker A',
      kind: 'worker',
      owner_engineer_id: 'eng-hired',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
    },
    orphan: {
      id: 'orphan',
      name: 'Orphan Worker',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'O',
      status: 'running',
    },
  };

  runInContext(context, `render();`);

  const hiredWorker = main.innerHTML.match(/<div class="([^"]*)" draggable="true" data-drag-id="worker-hired"/);
  const orphanWorker = main.innerHTML.match(/<div class="([^"]*)" draggable="true" data-drag-id="orphan"/);
  assert.ok(hiredWorker, 'architect-owned worker cell should render');
  assert.ok(orphanWorker, 'orphan worker cell should render');
  assert.match(hiredWorker[1], /\bworker\b/);
  assert.match(orphanWorker[1], /\bworker\b/);
  assert.doesNotMatch(
    hiredWorker[1],
    new RegExp('\\barchitect-' + 'owned-worker\\b|\\bengineer-' + 'owned-worker\\b'),
  );
  assert.doesNotMatch(
    orphanWorker[1],
    new RegExp('\\barchitect-' + 'owned-worker\\b|\\bengineer-' + 'owned-worker\\b'),
  );
  assert.match(main.innerHTML, /agent-section-architect-column/);
  assert.match(main.innerHTML, /agent-section-body/);
  assert.doesNotMatch(main.innerHTML, /architect-header-row|agent-section-header-row/);
  assert.match(main.innerHTML, /loose-workers-strip/);
  assert.match(main.innerHTML, /engineer-row/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.doesNotMatch(
    css,
    new RegExp('\\.cell\\.architect-' + 'owned-engineer|\\.cell\\.architect-' + 'owned-worker|\\.cell\\.engineer-' + 'owned-worker'),
  );
  assert.doesNotMatch(css, /agent-section-header-row|architect-header-row/);
});

test('main render anchors each architect once in a fixed left column with engineer rows on the right', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = {
    loom: ['arch-a', 'eng-a1', 'worker-a1', 'eng-a2', 'worker-a2', 'eng-a3'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: '' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Productmind',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'P',
      status: 'running',
      created_at: 1,
    },
    'eng-a1': {
      id: 'eng-a1',
      name: 'Engineer One',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '1',
      status: 'running',
      created_at: 2,
    },
    'worker-a1': {
      id: 'worker-a1',
      name: 'Worker One',
      kind: 'worker',
      owner_engineer_id: 'eng-a1',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
      created_at: 3,
    },
    'eng-a2': {
      id: 'eng-a2',
      name: 'Engineer Two',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
      created_at: 4,
    },
    'worker-a2': {
      id: 'worker-a2',
      name: 'Worker Two',
      kind: 'worker',
      owner_engineer_id: 'eng-a2',
      group: 'loom',
      cell_type: 'agent',
      icon: 'V',
      status: 'running',
      created_at: 5,
    },
    'eng-a3': {
      id: 'eng-a3',
      name: 'Engineer Three',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: '3',
      status: 'running',
      created_at: 6,
    },
  };

  runInContext(context, `render();`);

  const start = main.innerHTML.indexOf('data-agent-section="architect:arch-a"');
  const end = main.innerHTML.indexOf('</section>', start);
  const sectionHtml = main.innerHTML.slice(start, end);
  assert.ok(start >= 0 && end > start, 'architect section should render');
  assert.equal((sectionHtml.match(/data-drag-id="arch-a"/g) || []).length, 1);
  assert.equal((sectionHtml.match(/class="engineer-row agent-grid-engineer-row"/g) || []).length, 3);
  assert.ok(sectionHtml.indexOf('agent-section-architect-column') < sectionHtml.indexOf('agent-section-body'));
  assert.ok(sectionHtml.indexOf('Productmind') < sectionHtml.indexOf('Engineer One'));
  assert.match(sectionHtml, /agent-section-body[\s\S]*Engineer One[\s\S]*Engineer Two[\s\S]*Engineer Three[\s\S]*\+ New Engineer/);
  assert.doesNotMatch(sectionHtml, /architect-header-row|agent-section-header-row/);
});

test('main render keeps the User loose-workers strip inside the right column above engineer rows', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['loose-a', 'eng-user', 'worker-user'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false, weaver_agent_id: '' } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'loose-a': {
      id: 'loose-a',
      name: 'Loose A',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'L',
      status: 'running',
      created_at: 1,
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
      created_at: 2,
    },
    'worker-user': {
      id: 'worker-user',
      name: 'User Worker',
      kind: 'worker',
      owner_engineer_id: 'eng-user',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
      created_at: 3,
    },
  };

  runInContext(context, `render();`);

  const start = main.innerHTML.indexOf('data-agent-section="user"');
  const end = main.innerHTML.indexOf('</section>', start);
  const sectionHtml = main.innerHTML.slice(start, end);
  const bodyStart = sectionHtml.indexOf('agent-section-body');
  assert.ok(bodyStart >= 0, 'user section body should render');
  assert.ok(sectionHtml.indexOf('agent-section-user-card') < bodyStart);
  assert.ok(sectionHtml.indexOf('loose-workers-strip') > bodyStart);
  assert.ok(sectionHtml.indexOf('loose-workers-strip') < sectionHtml.indexOf('data-engineer-id="eng-user"'));
  assert.match(sectionHtml, /loose-workers-strip[\s\S]*Loose A[\s\S]*\+ New Worker/);
  assert.match(sectionHtml, /data-engineer-id="eng-user"[\s\S]*User Engineer[\s\S]*User Worker/);
});

test('main render keeps wrapped workers inside their engineer row and fixes architect column width via CSS', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  const ids = ['eng-a'];
  const agents = {
    'eng-a': {
      id: 'eng-a',
      name: 'Engineer A',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
      created_at: 1,
    },
    'eng-b': {
      id: 'eng-b',
      name: 'Engineer B',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'B',
      status: 'running',
      created_at: 20,
    },
  };
  for (let i = 1; i <= 7; i++) {
    const id = 'worker-' + i;
    ids.push(id);
    agents[id] = {
      id,
      name: 'Worker ' + i,
      kind: 'worker',
      owner_engineer_id: 'eng-a',
      group: 'loom',
      cell_type: 'agent',
      icon: String(i),
      status: 'running',
      created_at: i + 1,
    };
  }
  ids.push('eng-b');

  sandbox.state.groups = { loom: ids };
  sandbox.state.group_settings = { loom: { collapsed_default: false, weaver_agent_id: '' } };
  sandbox.state.children = {};
  sandbox.state.agents = agents;

  runInContext(context, `render();`);

  const firstRowStart = main.innerHTML.indexOf('data-engineer-id="eng-a"');
  const secondRowStart = main.innerHTML.indexOf('data-engineer-id="eng-b"');
  const firstRowHtml = main.innerHTML.slice(firstRowStart, secondRowStart);
  assert.ok(firstRowStart >= 0 && secondRowStart > firstRowStart, 'two engineer rows should render');
  assert.match(firstRowHtml, /engineer-row-workers/);
  for (let i = 1; i <= 7; i++) assert.match(firstRowHtml, new RegExp('Worker ' + i));
  assert.doesNotMatch(firstRowHtml, /Engineer B/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(css, /--agent-architect-column-width:\s*96px/);
  assert.match(css, /\.agent-section\s*\{[^}]*grid-template-columns:\s*var\(--agent-architect-column-width\)\s+minmax\(0,\s*1fr\)/s);
  assert.match(css, /\.agent-grid \.engineer-row\s*\{[^}]*display:\s*flex/s);
  assert.match(css, /\.engineer-row-workers,\s*\.loose-workers-strip\s*\{[^}]*flex-wrap:\s*wrap/s);
});

test('main hierarchy rerender preserves surface state when a worker moves from loose strip to engineer row', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');
  let renderCount = 0;

  function installFocusedInput() {
    const input = new FakeElement('hierarchy-filter');
    input.value = '';
    input.selectionStart = 0;
    input.selectionEnd = 0;
    input.parentNode = main;
    main.children = [input];
    document.register('hierarchy-filter', input);
    return input;
  }

  Object.defineProperty(main, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      renderCount += 1;
      this.scrollTop = 0;
      installFocusedInput();
    },
  });

  sandbox.state.groups = {
    loom: ['arch-a', 'eng-hired', 'worker-hired'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: '' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
    },
    'eng-hired': {
      id: 'eng-hired',
      name: 'Alice',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
    },
    'worker-hired': {
      id: 'worker-hired',
      name: 'Worker A',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
    },
  };

  let input = installFocusedInput();
  input.value = 'planner';
  input.selectionStart = 2;
  input.selectionEnd = 5;
  document.activeElement = input;
  main.scrollTop = 73;

  runInContext(context, `render();`);

  input = document.getElementById('hierarchy-filter');
  assert.equal(renderCount, 1);
  assert.equal(main.scrollTop, 73);
  assert.equal(input.value, 'planner');
  assert.equal(input.selectionStart, 2);
  assert.equal(input.selectionEnd, 5);
  assert.equal(input.focused, true);
  let workerMatch = main.innerHTML.match(/<div class="([^"]*)" draggable="true" data-drag-id="worker-hired"/);
  assert.ok(workerMatch, 'worker cell should render before ownership changes');
  assert.doesNotMatch(
    workerMatch[1],
    new RegExp('\\barchitect-' + 'owned-worker\\b|\\bengineer-' + 'owned-worker\\b'),
  );
  assert.match(main.innerHTML, /loose-workers-strip[\s\S]*Worker A[\s\S]*Alice/);

  document.activeElement = input;
  input.value = 'planner';
  input.selectionStart = 3;
  input.selectionEnd = 7;
  main.scrollTop = 91;
  sandbox.state.agents['worker-hired'].owner_engineer_id = 'eng-hired';

  runInContext(context, `render();`);

  input = document.getElementById('hierarchy-filter');
  assert.equal(renderCount, 2);
  assert.equal(main.scrollTop, 91);
  assert.equal(input.value, 'planner');
  assert.equal(input.selectionStart, 3);
  assert.equal(input.selectionEnd, 7);
  assert.equal(input.focused, true);
  workerMatch = main.innerHTML.match(/<div class="([^"]*)" draggable="true" data-drag-id="worker-hired"/);
  assert.ok(workerMatch, 'worker cell should render after ownership changes');
  assert.doesNotMatch(
    workerMatch[1],
    new RegExp('\\barchitect-' + 'owned-worker\\b|\\bengineer-' + 'owned-worker\\b'),
  );
  assert.match(main.innerHTML, /Alice[\s\S]*Worker A/);
});

test('main render keeps dismissed engineers in-place and preserves scroll across rehire transitions', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');
  let renderCount = 0;
  Object.defineProperty(main, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      renderCount += 1;
      this.scrollTop = 0;
    },
  });

  sandbox.state.groups = {
    loom: ['eng-dismissed', 'worker-dismissed', 'eng-active', 'worker-active'],
  };
  sandbox.state.group_settings = {
    loom: { collapsed_default: false, weaver_agent_id: '' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-dismissed': {
      id: 'eng-dismissed',
      name: 'Paused Engineer',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'D',
      status: 'stopped',
      dismissed_at: 123,
    },
    'worker-dismissed': {
      id: 'worker-dismissed',
      name: 'Paused Worker',
      kind: 'worker',
      owner_engineer_id: 'eng-dismissed',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'stopped',
    },
    'eng-active': {
      id: 'eng-active',
      name: 'Active Engineer',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'sess-active',
    },
    'worker-active': {
      id: 'worker-active',
      name: 'Active Worker',
      kind: 'worker',
      owner_engineer_id: 'eng-active',
      group: 'loom',
      cell_type: 'agent',
      icon: 'B',
      status: 'running',
      session_id: 'sess-worker-active',
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'eng-dismissed',
    'worker-dismissed',
    'eng-active',
    'worker-active',
  ]);
  assert.match(main.innerHTML, /Paused Engineer[\s\S]*Paused Worker[\s\S]*Active Engineer[\s\S]*Active Worker/);
  assert.match(main.innerHTML, /cell[^"]*engineer[^"]*dismissed/);
  assert.match(main.innerHTML, /data-dismissed-at="123"/);
  assert.match(main.innerHTML, /cell-dismissed-badge/);
  assert.match(main.innerHTML, /rehireEngineer\('eng-dismissed'\)/);

  main.scrollTop = 141;
  sandbox.state.agents['eng-dismissed'].dismissed_at = 0;
  sandbox.state.agents['eng-dismissed'].status = 'running';
  sandbox.state.agents['eng-dismissed'].session_id = 'sess-dismissed-rehired';

  runInContext(context, `render();`);

  assert.equal(renderCount, 2);
  assert.equal(main.scrollTop, 141);
  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'eng-dismissed',
    'worker-dismissed',
    'eng-active',
    'worker-active',
  ]);
  assert.doesNotMatch(main.innerHTML, /cell-dismissed-badge/);
  assert.doesNotMatch(main.innerHTML, /rehireEngineer\('eng-dismissed'\)/);
});

test('main render restores scroll when a section rerenders with a new engineer row', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');
  let renderCount = 0;
  Object.defineProperty(main, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      renderCount += 1;
      this.scrollTop = 0;
    },
  });

  sandbox.state.groups = { loom: ['arch-a', 'eng-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'R',
      status: 'running',
      session_id: 'sess-arch-a',
      created_at: 5,
    },
    'eng-a': {
      id: 'eng-a',
      name: 'Alice',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'sess-eng-a',
      created_at: 10,
    },
  };

  runInContext(context, `render();`);
  main.scrollTop = 135;

  sandbox.state.agents['eng-b'] = {
    id: 'eng-b',
    name: 'Bob',
    kind: 'engineer',
    hired_by_architect_id: 'arch-a',
    group: 'loom',
    cell_type: 'agent',
    icon: 'B',
    status: 'running',
    session_id: 'sess-eng-b',
    created_at: 11,
  };
  sandbox.state.groups.loom = ['arch-a', 'eng-a', 'eng-b'];

  runInContext(context, `render();`);

  assert.equal(renderCount, 2);
  assert.equal(main.scrollTop, 135);
  assert.deepEqual(jsonValue(context, `window._navAgents`), ['arch-a', 'eng-a', 'eng-b']);
  assert.match(main.innerHTML, /Architect A[\s\S]*Alice[\s\S]*Bob/);
});

test('main hierarchy row shapes preserve focused controls across rerenders', () => {
  const cases = [
    { label: 'architect column', focusKey: 'agent-close:arch-1' },
    { label: 'engineer row', focusKey: 'agent-close:eng-1' },
    { label: 'loose-workers strip', focusKey: 'agent-close:loose-1' },
  ];

  for (const item of cases) {
    const { context, document, sandbox } = createMainRenderHarness();
    const main = document.getElementById('main');
    let currentFocusTarget = null;
    let renderCount = 0;

    function installFocusTarget() {
      const target = new FakeElement('focused-grid-control');
      target.dataset.focusKey = item.focusKey;
      target.value = '';
      target.selectionStart = 0;
      target.selectionEnd = 0;
      target.parentNode = main;
      main.children = [target];
      main.setQuerySelector('[data-focus-key="' + item.focusKey + '"]', target);
      document.register('focused-grid-control', target);
      currentFocusTarget = target;
      return target;
    }

    Object.defineProperty(main, 'innerHTML', {
      configurable: true,
      get() {
        return this._innerHTML || '';
      },
      set(value) {
        this._innerHTML = value;
        renderCount += 1;
        installFocusTarget();
      },
    });

    sandbox.state.groups = { loom: ['arch-1', 'eng-1', 'loose-1'] };
    sandbox.state.group_settings = { loom: { collapsed_default: false } };
    sandbox.state.children = {};
    sandbox.state.agents = {
      'arch-1': {
        id: 'arch-1',
        name: 'Architect',
        kind: 'architect',
        group: 'loom',
        cell_type: 'agent',
        icon: 'A',
        status: 'running',
        created_at: 1,
      },
      'eng-1': {
        id: 'eng-1',
        name: 'Engineer',
        kind: 'engineer',
        hired_by_architect_id: 'arch-1',
        group: 'loom',
        cell_type: 'agent',
        icon: 'E',
        status: 'running',
        created_at: 2,
      },
      'loose-1': {
        id: 'loose-1',
        name: 'Loose Worker',
        kind: 'worker',
        group: 'loom',
        cell_type: 'agent',
        icon: 'W',
        status: 'running',
        created_at: 3,
      },
    };

    let target = installFocusTarget();
    target.value = item.label;
    target.selectionStart = 1;
    target.selectionEnd = 4;
    document.activeElement = target;

    runInContext(context, `render();`);

    assert.match(main.innerHTML, new RegExp('data-focus-key="' + item.focusKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"'));
    assert.equal(currentFocusTarget.focused, true, item.label + ' focus should be restored on initial render');
    assert.equal(currentFocusTarget.value, item.label);
    assert.equal(currentFocusTarget.selectionStart, 1);
    assert.equal(currentFocusTarget.selectionEnd, 4);

    document.activeElement = currentFocusTarget;
    currentFocusTarget.value = item.label + ' draft';
    currentFocusTarget.selectionStart = 2;
    currentFocusTarget.selectionEnd = 6;
    sandbox.state.agents['eng-2'] = {
      id: 'eng-2',
      name: 'Engineer 2',
      kind: 'engineer',
      hired_by_architect_id: 'arch-1',
      group: 'loom',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
      created_at: 4,
    };
    sandbox.state.groups.loom = ['arch-1', 'eng-1', 'loose-1', 'eng-2'];

    runInContext(context, `render();`);

    assert.equal(renderCount, 2);
    assert.equal(currentFocusTarget.focused, true, item.label + ' focus should survive rerender');
    assert.equal(currentFocusTarget.value, item.label + ' draft');
    assert.equal(currentFocusTarget.selectionStart, 2);
    assert.equal(currentFocusTarget.selectionEnd, 6);
  }
});

test('add engineer modal draft survives agent grid rerender during websocket delta', () => {
  const { sandbox, document } = createSandbox();
  document.register('main');
  const modalDom = registerEngineerModalDom(document);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  loadModalScripts(context);
  runInContext(context, `
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
    getFilterByWindow = function() { return false; };
  `);

  runInContext(context, `
    _handleFullState({
      seq: 1,
      groups: { loom: ['arch-a'] },
      group_settings: { loom: { collapsed_default: false } },
      agents: {
        'arch-a': {
          id: 'arch-a',
          name: 'Architect A',
          kind: 'architect',
          group: 'loom',
          cell_type: 'agent',
          icon: 'A',
          status: 'running',
          created_at: 1,
        },
      },
      children: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: []
    });
    openAddEngineerForSection('loom', 'arch-a');
  `);

  modalDom.nameInput.value = 'Draft Engineer';
  modalDom.nameInput.selectionStart = 6;
  modalDom.nameInput.selectionEnd = 14;
  modalDom.commandInput.value = 'codex --model gpt-5.4';
  document.activeElement = modalDom.nameInput;

  runInContext(context, `
    _handleDelta({
      seq: 2,
      ops: [
        { op: 'agent_upsert', id: 'eng-a', name: 'Engineer A', kind: 'engineer', hired_by_architect_id: 'arch-a', group: 'loom', cell_type: 'agent', icon: 'E', status: 'running', created_at: 2 },
        { op: 'group_update', name: 'loom', agents: ['arch-a', 'eng-a'] }
      ]
    });
  `);

  assert.equal(modalDom.modal.classList.contains('visible'), true);
  assert.equal(modalDom.nameInput.value, 'Draft Engineer');
  assert.equal(modalDom.commandInput.value, 'codex --model gpt-5.4');
  assert.equal(modalDom.nameInput.selectionStart, 6);
  assert.equal(modalDom.nameInput.selectionEnd, 14);
  assert.equal(document.activeElement, modalDom.nameInput);
  assert.match(document.getElementById('main').innerHTML, /Architect A[\s\S]*Engineer A/);
  assert.deepEqual(jsonValue(context, `sendCalls`), []);
});

test('main hierarchy preserves loose-workers strip scroll and focus across websocket delta rerenders', () => {
  const { sandbox, document } = createSandbox();
  const main = document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    _cachedAgentTemplates = [];
    selectedTerminalId = null;
    getFilterByWindow = function() { return false; };
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
  `);

  let currentStrip = null;
  let currentButton = null;
  let renderCount = 0;
  function installLooseStripSurface() {
    currentStrip = new FakeElement('');
    currentButton = new FakeElement('');
    currentButton.dataset.focusKey = 'loose-new-worker:loom:user';
    currentButton.value = '';
    currentButton.selectionStart = 0;
    currentButton.selectionEnd = 0;
    currentStrip.parentNode = main;
    currentButton.parentNode = main;
    main.children = [currentStrip, currentButton];
    main.setQuerySelector('.loose-workers-strip', currentStrip);
    main.setQuerySelector('[data-focus-key=\"loose-new-worker:loom:user\"]', currentButton);
  }

  Object.defineProperty(main, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      renderCount += 1;
      installLooseStripSurface();
    },
  });

  runInContext(context, `
    _handleFullState({
      seq: 1,
      groups: { loom: ['loose-1'] },
      group_settings: { loom: { collapsed_default: false } },
      agents: {
        'loose-1': {
          id: 'loose-1',
          name: 'Loose One',
          kind: 'worker',
          group: 'loom',
          cell_type: 'agent',
          icon: '1',
          status: 'running',
          created_at: 1
        }
      },
      children: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: []
    });
  `);

  currentStrip.scrollTop = 17;
  currentStrip.scrollLeft = 43;
  currentButton.value = 'draft';
  currentButton.selectionStart = 1;
  currentButton.selectionEnd = 4;
  document.activeElement = currentButton;

  runInContext(context, `
    _handleDelta({
      seq: 2,
      ops: [
        {
          op: 'agent_upsert',
          id: 'loose-2',
          name: 'Loose Two',
          kind: 'worker',
          group: 'loom',
          cell_type: 'agent',
          icon: '2',
          status: 'running',
          created_at: 2
        },
        { op: 'group_update', name: 'loom', agents: ['loose-1', 'loose-2'] }
      ]
    });
  `);

  assert.equal(renderCount, 2);
  assert.equal(currentStrip.scrollTop, 17);
  assert.equal(currentStrip.scrollLeft, 43);
  assert.equal(currentButton.focused, true);
  assert.equal(currentButton.value, 'draft');
  assert.equal(currentButton.selectionStart, 1);
  assert.equal(currentButton.selectionEnd, 4);
  assert.match(main.innerHTML, /Loose One[\s\S]*Loose Two[\s\S]*\+ New Worker/);
});

test('main hierarchy preserves focused ghost-card state across websocket delta rerenders', () => {
  const cases = [
    { label: 'worker ghost', focusKey: 'loose-new-worker:loom:user' },
    { label: 'engineer ghost', focusKey: 'section-new-engineer:loom:architect:arch-a' },
    { label: 'architect ghost', focusKey: 'agent-new-architect:loom' },
  ];

  for (const item of cases) {
    const { sandbox, document } = createSandbox();
    const main = document.register('main');
    const context = vm.createContext(sandbox);
    loadScript(context, 'static/js/ws.js');
    loadScript(context, 'static/js/render.js');
    runInContext(context, `
      _cachedAgentTemplates = [];
      selectedTerminalId = null;
      getFilterByWindow = function() { return false; };
      renderTerminalWorkspace = function() {};
      updateEventsAttentionBadge = function() {};
    `);

    let currentGhost = null;
    let renderCount = 0;
    function installGhostSurface() {
      currentGhost = new FakeElement('');
      currentGhost.dataset.focusKey = item.focusKey;
      currentGhost.value = '';
      currentGhost.selectionStart = 0;
      currentGhost.selectionEnd = 0;
      currentGhost.parentNode = main;
      main.children = [currentGhost];
      main.setQuerySelector('[data-focus-key="' + item.focusKey + '"]', currentGhost);
    }

    Object.defineProperty(main, 'innerHTML', {
      configurable: true,
      get() {
        return this._innerHTML || '';
      },
      set(value) {
        this._innerHTML = value;
        renderCount += 1;
        installGhostSurface();
      },
    });

    runInContext(context, `
      _handleFullState({
        seq: 1,
        groups: { loom: ['arch-a'] },
        group_settings: { loom: { collapsed_default: false } },
        agents: {
          'arch-a': {
            id: 'arch-a',
            name: 'Architect A',
            kind: 'architect',
            group: 'loom',
            cell_type: 'agent',
            icon: 'A',
            status: 'running',
            created_at: 1
          }
        },
        children: {},
        board_lanes: [],
        board_tasks: {},
        panel_events: []
      });
    `);

    currentGhost.value = item.label + ' draft';
    currentGhost.selectionStart = 1;
    currentGhost.selectionEnd = 5;
    document.activeElement = currentGhost;

    runInContext(context, `
      _handleDelta({
        seq: 2,
        ops: [
          {
            op: 'agent_upsert',
            id: 'eng-a',
            name: 'Engineer A',
            kind: 'engineer',
            hired_by_architect_id: 'arch-a',
            group: 'loom',
            cell_type: 'agent',
            icon: 'E',
            status: 'running',
            created_at: 2
          },
          { op: 'group_update', name: 'loom', agents: ['arch-a', 'eng-a'] }
        ]
      });
    `);

    assert.equal(renderCount, 2, item.label);
    assert.equal(currentGhost.focused, true, item.label + ' focus should survive rerender');
    assert.equal(currentGhost.value, item.label + ' draft');
    assert.equal(currentGhost.selectionStart, 1);
    assert.equal(currentGhost.selectionEnd, 5);
    assert.match(main.innerHTML, new RegExp('data-focus-key="' + item.focusKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '"'));
  }
});

test('main hierarchy ordering is stable when multiple agent deltas arrive in one websocket message', () => {
  const { sandbox, document } = createSandbox();
  document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    _cachedAgentTemplates = [];
    selectedTerminalId = null;
    getFilterByWindow = function() { return false; };
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
  `);

  runInContext(context, `
    _handleFullState({
      seq: 1,
      groups: { loom: [] },
      group_settings: { loom: { collapsed_default: false } },
      agents: {},
      children: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: []
    });
    _handleDelta({
      seq: 2,
      ops: [
        { op: 'agent_upsert', id: 'arch-a', name: 'Architect A', kind: 'architect', group: 'loom', cell_type: 'agent', icon: 'A', status: 'running', created_at: 30 },
        { op: 'agent_upsert', id: 'eng-user', name: 'User Engineer', kind: 'engineer', group: 'loom', cell_type: 'agent', icon: 'U', status: 'running', created_at: 2 },
        { op: 'agent_upsert', id: 'arch-b', name: 'Architect B', kind: 'architect', group: 'loom', cell_type: 'agent', icon: 'B', status: 'running', created_at: 20 },
        { op: 'agent_upsert', id: 'worker-user', name: 'User Worker', kind: 'worker', owner_engineer_id: 'eng-user', group: 'loom', cell_type: 'agent', icon: 'w', status: 'running', created_at: 3 },
        { op: 'agent_upsert', id: 'eng-a', name: 'Engineer A', kind: 'engineer', hired_by_architect_id: 'arch-a', group: 'loom', cell_type: 'agent', icon: 'E', status: 'running', created_at: 31 },
        { op: 'agent_upsert', id: 'eng-b', name: 'Engineer B', kind: 'engineer', hired_by_architect_id: 'arch-b', group: 'loom', cell_type: 'agent', icon: 'F', status: 'running', created_at: 21 },
        { op: 'agent_upsert', id: 'loose', name: 'Loose', kind: 'worker', group: 'loom', cell_type: 'agent', icon: 'L', status: 'running', created_at: 1 },
        { op: 'group_update', name: 'loom', agents: ['eng-a', 'arch-a', 'worker-user', 'arch-b', 'loose', 'eng-b', 'eng-user'] }
      ]
    });
  `);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'loose',
    'eng-user',
    'worker-user',
    'arch-b',
    'eng-b',
    'arch-a',
    'eng-a',
  ]);
  assert.match(document.getElementById('main').innerHTML, /Loose[\s\S]*User Engineer[\s\S]*User Worker[\s\S]*Architect B[\s\S]*Engineer B[\s\S]*Architect A[\s\S]*Engineer A/);
});

test('main hierarchy loose-workers strip orders detached workers by creation time with new-worker button at the end', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['loose-b', 'loose-a', 'loose-c'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'loose-a': {
      id: 'loose-a',
      name: 'Worker A',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      created_at: 10,
    },
    'loose-b': {
      id: 'loose-b',
      name: 'Worker B',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'B',
      status: 'running',
      created_at: 30,
    },
    'loose-c': {
      id: 'loose-c',
      name: 'Worker C',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'C',
      status: 'running',
      created_at: 20,
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'loose-a',
    'loose-c',
    'loose-b',
  ]);
  assert.match(main.innerHTML, /Worker A[\s\S]*Worker C[\s\S]*Worker B[\s\S]*\+ New Worker/);
  assert.ok(
    main.innerHTML.indexOf('data-drag-id="loose-b"') <
      main.innerHTML.indexOf('+ New Worker'),
    'new-worker button should render after the last detached worker card',
  );
});

test('main grid drag reorders agents within a hierarchical row without changing row ownership', () => {
  const { context, document, sandbox } = createMainGridDragHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['eng-1', 'worker-1', 'worker-2'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Worker 1',
      kind: 'worker',
      owner_engineer_id: 'eng-1',
      group: 'loom',
      cell_type: 'agent',
      icon: '1',
      status: 'running',
    },
    'worker-2': {
      id: 'worker-2',
      name: 'Worker 2',
      kind: 'worker',
      owner_engineer_id: 'eng-1',
      group: 'loom',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
    },
  };

  runInContext(context, `render(); setupDrag();`);
  assert.deepEqual(
    jsonValue(context, `window._navGridRows.find(function(row) { return row.rowKey === 'user:engineer:eng-1'; }).items.map(function(item) { return item.id; })`),
    ['eng-1', 'worker-1', 'worker-2'],
  );

  const row = createDragDomElement({
    classNames: ['engineer-row', 'agent-grid-engineer-row'],
    parent: main,
  });
  const worker1 = createDragDomElement({
    dragId: 'worker-1',
    dragType: 'agent',
    dragGroup: 'loom',
    rect: { left: 180, right: 300, width: 120 },
    parent: row,
  });
  const worker2 = createDragDomElement({
    dragId: 'worker-2',
    dragType: 'agent',
    dragGroup: 'loom',
    rect: { left: 320, right: 440, width: 120 },
    parent: row,
  });

  const { dropEvent } = fireGridDrag(main, worker2, worker1, { clientX: 190, clientY: 40 });

  assert.equal(dropEvent.defaultPrevented, true);
  assert.deepEqual(jsonValue(context, `sendCalls[0]`), {
    cmd: 'move_agent',
    id: 'worker-2',
    target_group: 'loom',
    before: 'worker-1',
  });

  applyMoveAgentForDragState(sandbox.state, 'worker-2', 'loom', 'worker-1');
  runInContext(context, `render();`);

  assert.deepEqual(
    jsonValue(context, `window._navGridRows.find(function(row) { return row.rowKey === 'user:engineer:eng-1'; }).items.map(function(item) { return item.id; })`),
    ['eng-1', 'worker-2', 'worker-1'],
  );
  assert.deepEqual(jsonValue(context, `({
    worker2Section: window._navGridItemMeta['worker-2'].sectionKey,
    worker2Row: window._navGridItemMeta['worker-2'].rowKey,
    worker2Owner: state.agents['worker-2'].owner_engineer_id
  })`), {
    worker2Section: 'user',
    worker2Row: 'user:engineer:eng-1',
    worker2Owner: 'eng-1',
  });
});

test('terminal drag moves between an agent drawer and standalone while preserving session and worktree fields', () => {
  const { context, document, sandbox } = createMainGridDragHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['agent-1', 'term-standalone'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = { 'agent-1': ['term-child'] };
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Agent',
      kind: 'worker',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
      session_id: 'agent-session',
    },
    'term-child': {
      id: 'term-child',
      name: 'Child Terminal',
      kind: 'terminal',
      group: 'loom',
      cell_type: 'terminal',
      parent_id: 'agent-1',
      status: 'running',
      session_id: 'child-session',
      current_process: 'zsh',
      worktree_path: '/repo/.loom/worktrees/term-child',
      worktree_branch: 'loom/term-child',
      current_path: '/repo',
    },
    'term-standalone': {
      id: 'term-standalone',
      name: 'Standalone Terminal',
      kind: 'terminal',
      group: 'loom',
      cell_type: 'terminal',
      status: 'running',
      session_id: 'standalone-session',
      current_process: 'zsh',
    },
  };

  runInContext(context, `selectedAgentId = 'agent-1'; selectedTerminalId = 'agent-1'; render(); setupDrag();`);

  const childTerminal = createDragDomElement({
    dragId: 'term-child',
    dragType: 'terminal',
    dragGroup: 'loom',
    parent: main,
  });
  const standaloneList = createDragDomElement({
    dropType: 'terminal',
    dropGroup: 'loom',
    classNames: ['term-list'],
    parent: main,
  });

  fireGridDrag(main, childTerminal, standaloneList, { clientX: 40, clientY: 40 });

  assert.deepEqual(jsonValue(context, `sendCalls[0]`), {
    cmd: 'move_agent',
    id: 'term-child',
    target_group: 'loom',
    before: '',
  });

  applyMoveAgentForDragState(sandbox.state, 'term-child', 'loom', '');
  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `({
    parent: state.agents['term-child'].parent_id,
    group: state.agents['term-child'].group,
    groups: state.groups.loom,
    children: state.children['agent-1'],
    session: state.agents['term-child'].session_id,
    worktreePath: state.agents['term-child'].worktree_path,
    worktreeBranch: state.agents['term-child'].worktree_branch
  })`), {
    parent: '',
    group: 'loom',
    groups: ['agent-1', 'term-standalone', 'term-child'],
    children: [],
    session: 'child-session',
    worktreePath: '/repo/.loom/worktrees/term-child',
    worktreeBranch: 'loom/term-child',
  });

  const detachedTerminal = createDragDomElement({
    dragId: 'term-child',
    dragType: 'terminal',
    dragGroup: 'loom',
    parent: main,
  });
  const parentDrawerList = createDragDomElement({
    dropType: 'terminal',
    dropGroup: 'loom',
    dropParent: 'agent-1',
    classNames: ['term-list'],
    parent: main,
  });

  fireGridDrag(main, detachedTerminal, parentDrawerList, { clientX: 40, clientY: 40 });

  assert.deepEqual(jsonValue(context, `sendCalls[1]`), {
    cmd: 'reparent_terminal',
    id: 'term-child',
    parent_id: 'agent-1',
  });

  applyReparentTerminalForDragState(sandbox.state, 'term-child', 'agent-1');
  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `({
    parent: state.agents['term-child'].parent_id,
    group: state.agents['term-child'].group,
    groups: state.groups.loom,
    children: state.children['agent-1'],
    navItems: window._navItems,
    session: state.agents['term-child'].session_id,
    worktreePath: state.agents['term-child'].worktree_path,
    worktreeBranch: state.agents['term-child'].worktree_branch
  })`), {
    parent: 'agent-1',
    group: 'loom',
    groups: ['agent-1', 'term-standalone'],
    children: ['term-child'],
    navItems: ['agent-1', 'term-child', 'term-standalone'],
    session: 'child-session',
    worktreePath: '/repo/.loom/worktrees/term-child',
    worktreeBranch: 'loom/term-child',
  });
});

test('agent drag across an architect section boundary preserves ownership instead of reparenting', () => {
  const { context, document, sandbox } = createMainGridDragHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: ['eng-user', 'worker-user', 'arch-a', 'eng-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'loom',
      cell_type: 'agent',
      icon: 'U',
      status: 'running',
    },
    'worker-user': {
      id: 'worker-user',
      name: 'User Worker',
      kind: 'worker',
      owner_engineer_id: 'eng-user',
      group: 'loom',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
    },
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      icon: 'A',
      status: 'running',
    },
    'eng-a': {
      id: 'eng-a',
      name: 'Architect Engineer',
      kind: 'engineer',
      hired_by_architect_id: 'arch-a',
      group: 'loom',
      cell_type: 'agent',
      icon: 'E',
      status: 'running',
    },
  };

  runInContext(context, `render(); setupDrag();`);

  const userSection = createDragDomElement({
    classNames: ['agent-section', 'agent-section-user'],
    parent: main,
  });
  userSection.dataset.agentSection = 'user';
  const architectSection = createDragDomElement({
    classNames: ['agent-section', 'agent-section-architect'],
    parent: main,
  });
  architectSection.dataset.agentSection = 'architect:arch-a';
  const workerUser = createDragDomElement({
    dragId: 'worker-user',
    dragType: 'agent',
    dragGroup: 'loom',
    rect: { left: 180, right: 300, width: 120 },
    parent: userSection,
  });
  const architectEngineer = createDragDomElement({
    dragId: 'eng-a',
    dragType: 'agent',
    dragGroup: 'loom',
    rect: { left: 180, right: 300, width: 120 },
    parent: architectSection,
  });

  fireGridDrag(main, workerUser, architectEngineer, { clientX: 190, clientY: 40 });

  assert.deepEqual(jsonValue(context, `sendCalls[0]`), {
    cmd: 'move_agent',
    id: 'worker-user',
    target_group: 'loom',
    before: 'eng-a',
  });
  assert.equal(jsonValue(context, `sendCalls.length`), 1);

  applyMoveAgentForDragState(sandbox.state, 'worker-user', 'loom', 'eng-a');
  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `({
    workerSection: window._navGridItemMeta['worker-user'].sectionKey,
    workerRow: window._navGridItemMeta['worker-user'].rowKey,
    workerOwner: state.agents['worker-user'].owner_engineer_id,
    engineerArchitect: state.agents['eng-a'].hired_by_architect_id,
    navAgents: window._navAgents
  })`), {
    workerSection: 'user',
    workerRow: 'user:engineer:eng-user',
    workerOwner: 'eng-user',
    engineerArchitect: 'arch-a',
    navAgents: ['eng-user', 'worker-user', 'arch-a', 'eng-a'],
  });
});

test('group header drag still maps hierarchical grid group reordering to move_group', () => {
  const { context, document, sandbox } = createMainGridDragHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { alpha: [], beta: [] };
  sandbox.state.group_settings = {
    alpha: { collapsed_default: false },
    beta: { collapsed_default: false },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render(); setupDrag();`);

  const alphaGroup = createDragDomElement({
    groupName: 'alpha',
    classNames: ['group'],
    rect: { top: 10, bottom: 110, height: 100 },
    parent: main,
  });
  const betaGroup = createDragDomElement({
    groupName: 'beta',
    classNames: ['group'],
    rect: { top: 130, bottom: 230, height: 100 },
    parent: main,
  });
  const betaHeader = createDragDomElement({
    dragId: 'beta',
    dragType: 'group',
    classNames: ['group-hdr'],
    parent: betaGroup,
  });

  fireGridDrag(main, betaHeader, alphaGroup, { clientX: 20, clientY: 20 });

  assert.deepEqual(jsonValue(context, `sendCalls[0]`), {
    cmd: 'move_group',
    group: 'beta',
    before: 'alpha',
  });
});

test('main grid keyboard navigation traverses logical rows across sections', () => {
  const { context, sandbox } = createMainNavigationHarness();

  sandbox.state.groups = {
    loom: [
      'loose-1',
      'loose-2',
      'eng-user-a',
      'worker-user-a1',
      'worker-user-a2',
      'eng-user-b',
      'worker-user-b1',
      'arch-a',
      'eng-a',
      'worker-a1',
      'worker-a2',
      'arch-b',
      'eng-b',
      'worker-b1',
    ],
  };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'loose-1': { id: 'loose-1', name: 'Loose 1', kind: 'worker', group: 'loom', cell_type: 'agent', status: 'running', created_at: 1 },
    'loose-2': { id: 'loose-2', name: 'Loose 2', kind: 'worker', group: 'loom', cell_type: 'agent', status: 'running', created_at: 2 },
    'eng-user-a': { id: 'eng-user-a', name: 'User A', kind: 'engineer', group: 'loom', cell_type: 'agent', status: 'running', created_at: 10 },
    'worker-user-a1': { id: 'worker-user-a1', name: 'A1', kind: 'worker', owner_engineer_id: 'eng-user-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 11 },
    'worker-user-a2': { id: 'worker-user-a2', name: 'A2', kind: 'worker', owner_engineer_id: 'eng-user-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 12 },
    'eng-user-b': { id: 'eng-user-b', name: 'User B', kind: 'engineer', group: 'loom', cell_type: 'agent', status: 'running', created_at: 20 },
    'worker-user-b1': { id: 'worker-user-b1', name: 'B1', kind: 'worker', owner_engineer_id: 'eng-user-b', group: 'loom', cell_type: 'agent', status: 'running', created_at: 21 },
    'arch-a': { id: 'arch-a', name: 'Architect A', kind: 'architect', group: 'loom', cell_type: 'agent', status: 'running', created_at: 30 },
    'eng-a': { id: 'eng-a', name: 'Eng A', kind: 'engineer', hired_by_architect_id: 'arch-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 31 },
    'worker-a1': { id: 'worker-a1', name: 'WA1', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 32 },
    'worker-a2': { id: 'worker-a2', name: 'WA2', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 33 },
    'arch-b': { id: 'arch-b', name: 'Architect B', kind: 'architect', group: 'loom', cell_type: 'agent', status: 'running', created_at: 40 },
    'eng-b': { id: 'eng-b', name: 'Eng B', kind: 'engineer', hired_by_architect_id: 'arch-b', group: 'loom', cell_type: 'agent', status: 'running', created_at: 41 },
    'worker-b1': { id: 'worker-b1', name: 'WB1', kind: 'worker', owner_engineer_id: 'eng-b', group: 'loom', cell_type: 'agent', status: 'running', created_at: 42 },
  };

  runInContext(context, `focusedItemId = 'eng-user-a'; render();`);

  assert.deepEqual(jsonValue(context, `window._navGridRows.map(function(row) { return { type: row.rowType, items: row.items.map(function(item) { return item.id; }) }; })`), [
    { type: 'architect-creation-row', items: ['grid-control:agent-new-architect:loom'] },
    { type: 'loose-workers-strip', items: ['loose-1', 'loose-2', 'grid-control:loose-new-worker:loom:user'] },
    { type: 'engineer-row', items: ['eng-user-a', 'worker-user-a1', 'worker-user-a2'] },
    { type: 'engineer-row', items: ['eng-user-b', 'worker-user-b1'] },
    { type: 'section-creation-row', items: ['grid-control:section-new-engineer:loom:user'] },
    { type: 'engineer-row', items: ['arch-a', 'eng-a', 'worker-a1', 'worker-a2'] },
    { type: 'section-creation-row', items: ['grid-control:section-new-engineer:loom:architect:arch-a'] },
    { type: 'engineer-row', items: ['arch-b', 'eng-b', 'worker-b1'] },
    { type: 'section-creation-row', items: ['grid-control:section-new-engineer:loom:architect:arch-b'] },
  ]);

  runInContext(context, `moveFocusHorizontal(1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-user-a1');
  runInContext(context, `moveFocusHorizontal(1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-user-a2');
  runInContext(context, `moveFocusDown();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-user-b1');
  runInContext(context, `moveFocusDown();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:section-new-engineer:loom:user');
  runInContext(context, `moveFocusDown();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-a1');
  runInContext(context, `moveFocusDown();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:section-new-engineer:loom:architect:arch-a');
  runInContext(context, `moveFocusDown();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-b1');

  runInContext(context, `focusedItemId = 'eng-a'; render();`);
  runInContext(context, `moveFocusHorizontal(-1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'arch-a');
  runInContext(context, `moveFocusHorizontal(1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'eng-a');

  runInContext(context, `focusedItemId = 'loose-1'; render();`);
  runInContext(context, `moveFocusUp();`);
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:agent-new-architect:loom');
  runInContext(context, `focusedItemId = 'loose-1'; render();`);
  runInContext(context, `moveFocusHorizontal(1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'loose-2');
  runInContext(context, `moveFocusHorizontal(1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:loose-new-worker:loom:user');
  runInContext(context, `moveFocusHorizontal(-1);`);
  assert.equal(jsonValue(context, `focusedItemId`), 'loose-2');
});

test('main grid keyboard navigation keeps wrapped worker rows in logical order', () => {
  const { context, document, sandbox } = createMainNavigationHarness();
  const focusedMarker = new FakeElement('focused-marker');
  document.setSelector('.focused', focusedMarker);

  sandbox.state.groups = {
    loom: ['eng-a', 'worker-1', 'worker-2', 'worker-3', 'worker-4', 'worker-5'],
  };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'eng-a': { id: 'eng-a', name: 'Engineer', kind: 'engineer', group: 'loom', cell_type: 'agent', status: 'running', created_at: 1 },
    'worker-1': { id: 'worker-1', name: 'Worker 1', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 2 },
    'worker-2': { id: 'worker-2', name: 'Worker 2', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 3 },
    'worker-3': { id: 'worker-3', name: 'Worker 3', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 4 },
    'worker-4': { id: 'worker-4', name: 'Worker 4', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 5 },
    'worker-5': { id: 'worker-5', name: 'Worker 5', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 6 },
  };

  runInContext(context, `focusedItemId = 'eng-a'; render();`);

  assert.deepEqual(jsonValue(context, `window._navGridRows.find(function(row) { return row.rowKey === 'user:engineer:eng-a'; }).items.map(function(item) { return item.id; })`), [
    'eng-a',
    'worker-1',
    'worker-2',
    'worker-3',
    'worker-4',
    'worker-5',
  ]);

  for (const expected of ['worker-1', 'worker-2', 'worker-3', 'worker-4', 'worker-5']) {
    runInContext(context, `moveFocusHorizontal(1);`);
    assert.equal(jsonValue(context, `focusedItemId`), expected);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(focusedMarker.scrollIntoViewOptions)), {
    block: 'nearest',
    inline: 'nearest',
  });
});

test('main grid focus falls forward across websocket delta removals', () => {
  const { sandbox, document } = createSandbox();
  document.register('main');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    _cachedAgentTemplates = [];
    selectedTerminalId = null;
    getFilterByWindow = function() { return false; };
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
  `);

  runInContext(context, `
    _handleFullState({
      seq: 1,
      groups: { loom: ['loose-1', 'eng-a', 'worker-a1', 'worker-a2', 'eng-b'] },
      group_settings: { loom: { collapsed_default: false } },
      agents: {
        'loose-1': { id: 'loose-1', name: 'Loose', kind: 'worker', group: 'loom', cell_type: 'agent', status: 'running', created_at: 1 },
        'eng-a': { id: 'eng-a', name: 'Engineer A', kind: 'engineer', group: 'loom', cell_type: 'agent', status: 'running', created_at: 10 },
        'worker-a1': { id: 'worker-a1', name: 'Worker A1', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 11 },
        'worker-a2': { id: 'worker-a2', name: 'Worker A2', kind: 'worker', owner_engineer_id: 'eng-a', group: 'loom', cell_type: 'agent', status: 'running', created_at: 12 },
        'eng-b': { id: 'eng-b', name: 'Engineer B', kind: 'engineer', group: 'loom', cell_type: 'agent', status: 'running', created_at: 20 }
      },
      children: {},
      board_lanes: [],
      board_tasks: {},
      panel_events: []
    });
    focusedItemId = 'worker-a1';
    render();
    _handleDelta({
      seq: 2,
      ops: [
        { op: 'agent_remove', id: 'worker-a1' },
        { op: 'group_update', name: 'loom', agents: ['loose-1', 'eng-a', 'worker-a2', 'eng-b'] }
      ]
    });
  `);
  assert.equal(jsonValue(context, `focusedItemId`), 'worker-a2');

  runInContext(context, `
    _handleDelta({
      seq: 3,
      ops: [
        { op: 'agent_remove', id: 'worker-a2' },
        { op: 'group_update', name: 'loom', agents: ['loose-1', 'eng-a', 'eng-b'] }
      ]
    });
  `);
  assert.equal(jsonValue(context, `focusedItemId`), 'eng-a');

  runInContext(context, `
    _handleDelta({
      seq: 4,
      ops: [
        { op: 'agent_remove', id: 'eng-a' },
        { op: 'group_update', name: 'loom', agents: ['loose-1', 'eng-b'] }
      ]
    });
  `);
  assert.equal(jsonValue(context, `focusedItemId`), 'eng-b');

  runInContext(context, `
    _handleDelta({
      seq: 5,
      ops: [
        { op: 'agent_remove', id: 'eng-b' },
        { op: 'group_update', name: 'loom', agents: ['loose-1'] }
      ]
    });
  `);
  assert.equal(jsonValue(context, `focusedItemId`), 'loose-1');
});

test('main grid Tab cycles through creation buttons in visual order', () => {
  const { context, document, sandbox } = createMainNavigationHarness();

  sandbox.state.groups = { loom: ['arch-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      status: 'running',
      created_at: 1,
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navCreationControls.map(function(item) { return item.controlType + ':' + item.sectionKey; })`), [
    'agent-new-architect:user',
    'loose-new-worker:user',
    'section-new-engineer:user',
    'section-new-engineer:architect:arch-a',
  ]);

  function pressTab(shiftKey = false) {
    const event = {
      key: 'Tab',
      shiftKey,
      preventDefault() { this.defaultPrevented = true; },
    };
    document.listeners.keydown(event);
    assert.equal(event.defaultPrevented, true);
  }

  pressTab();
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:agent-new-architect:loom');
  pressTab();
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:loose-new-worker:loom:user');
  pressTab();
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:section-new-engineer:loom:user');
  pressTab();
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:section-new-engineer:loom:architect:arch-a');
  pressTab();
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:agent-new-architect:loom');
  pressTab(true);
  assert.equal(jsonValue(context, `focusedItemId`), 'grid-control:section-new-engineer:loom:architect:arch-a');
});

test('main grid Enter activates focused creation ghost cards with scoped context', () => {
  const { context, document, sandbox } = createMainNavigationHarness();

  sandbox.state.groups = { loom: ['arch-a'] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'arch-a': {
      id: 'arch-a',
      name: 'Architect A',
      kind: 'architect',
      group: 'loom',
      cell_type: 'agent',
      status: 'running',
      created_at: 1,
    },
  };

  runInContext(context, `
    var engineerGhostCalls = [];
    var workerGhostCalls = [];
    var architectGhostCalls = [];
    openAddEngineerForSection = function(group, architectId) {
      engineerGhostCalls.push({ group: group, architectId: architectId || '' });
    };
    openAddWorkerModal = function(group) {
      workerGhostCalls.push({ group: group });
    };
    openAddArchitectForGroup = function(group) {
      architectGhostCalls.push({ group: group });
    };
    render();
  `);

  function pressEnter(focusedId) {
    runInContext(context, `focusedItemId = ${JSON.stringify(focusedId)}; render();`);
    const event = {
      key: 'Enter',
      preventDefault() { this.defaultPrevented = true; },
    };
    document.listeners.keydown(event);
    assert.equal(event.defaultPrevented, true);
  }

  pressEnter('grid-control:loose-new-worker:loom:user');
  pressEnter('grid-control:section-new-engineer:loom:user');
  pressEnter('grid-control:section-new-engineer:loom:architect:arch-a');
  pressEnter('grid-control:agent-new-architect:loom');

  assert.deepEqual(jsonValue(context, `workerGhostCalls`), [{ group: 'loom' }]);
  assert.deepEqual(jsonValue(context, `engineerGhostCalls`), [
    { group: 'loom', architectId: '' },
    { group: 'loom', architectId: 'arch-a' },
  ]);
  assert.deepEqual(jsonValue(context, `architectGhostCalls`), [{ group: 'loom' }]);
});

test('main hierarchy always renders the synthetic User loose-workers strip when empty', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { loom: [] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render();`);

  assert.match(main.innerHTML, /agent-section agent-section-user/);
  assert.match(main.innerHTML, /agent-section-user-card[\s\S]*User/);
  const looseStrip = main.innerHTML.match(/<div class="loose-workers-strip"[\s\S]*?<\/div>/);
  assert.ok(looseStrip, 'empty user section should still include the loose-workers strip');
  assert.match(looseStrip[0], /ghost-card ghost-card--worker loose-workers-new-worker-btn/);
  assert.match(looseStrip[0], /openAddWorkerModal\('loom'\)/);
  assert.doesNotMatch(looseStrip[0], /\bdisabled\b/);
  assert.doesNotMatch(looseStrip[0], /data-drag-id=/);
});

test('loose-workers + New Worker opens creation modal and submitted worker appears in the strip', () => {
  const { sandbox, document } = createSandbox();
  const main = document.register('main');
  const modal = registerAddCellModalElements(document);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  loadModalScripts(context);
  runInContext(context, `
    _cachedAgentTemplates = [];
    _cachedProviders = [];
    selectedTerminalId = null;
    focusedItemId = null;
    getFilterByWindow = function() { return false; };
    renderTerminalWorkspace = function() {};
    updateEventsAttentionBadge = function() {};
  `);

  sandbox.state.groups = { loom: [] };
  sandbox.state.group_settings = { loom: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {};

  runInContext(context, `render();`);
  const click = main.innerHTML.match(/onclick="([^"]*openAddWorkerModal\('loom'\)[^"]*)"/);
  assert.ok(click, 'new-worker button should have a click handler');

  runInContext(context, `
    var event = { stopPropagation() {} };
    ${click[1]};
  `);
  assert.deepEqual(jsonValue(context, 'sendCalls[0]'), {
    cmd: 'get_config',
    group: 'loom',
  });

  runInContext(context, `
    _showAddModal('worker', 'loom', {
      current_path: '/repo',
      profiles: ['Default'],
      current_profile: 'Default',
      group_cells: [],
      group_settings: {},
      resolved_agent_defaults: { worktree: false }
    });
  `);

  assert.equal(modal.classList.contains('visible'), true);
  assert.equal(document.getElementById('modal-add-title').textContent, 'New Detached Worker');
  assert.equal(document.getElementById('add-submit-btn').textContent, 'Create Worker');
  assert.match(
    document.getElementById('modal-add-summary').textContent,
    /user-owned detached worker/,
  );

  document.getElementById('add-name-input').value = 'Detached Worker';
  document.getElementById('add-dir-select').value = '';
  document.getElementById('add-profile-select').value = 'Default';
  document.getElementById('add-shell-select').value = '';
  document.getElementById('add-env-vars').value = '';
  document.getElementById('add-template-select').value = '';
  document.getElementById('add-provider-select').value = '';
  document.getElementById('add-cmd-input').value = '';
  document.getElementById('add-model-input').value = '';
  document.getElementById('add-reasoning-effort').value = '';
  document.getElementById('add-wt-enabled').checked = false;

  context.submitAdd();

  assert.deepEqual(jsonValue(context, 'sendCalls[1]'), {
    cmd: 'add_worker',
    name: 'Detached Worker',
    group: 'loom',
    profile: 'Default',
    worktree: false,
  });

  sandbox.state.agents['worker-new'] = {
    id: 'worker-new',
    name: 'Detached Worker',
    kind: 'worker',
    group: 'loom',
    cell_type: 'agent',
    icon: 'W',
    status: 'running',
    created_at: 1,
  };
  sandbox.state.groups.loom = ['worker-new'];

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), ['worker-new']);
  assert.match(main.innerHTML, /Detached Worker[\s\S]*\+ New Worker/);
});

test('main render restores the inline task description editor caret across rerenders', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { alpha: ['agent-1'] };
  sandbox.state.group_settings = { alpha: { collapsed_default: false } };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker',
      group: 'alpha',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
      session_id: 'sess-1',
      mcp_messages: [],
    },
  };
  sandbox.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      group: 'alpha',
      task: 'Ship inline edits',
      description: 'Keep this draft steady',
      lane: 'In Progress',
      agent_id: 'agent-1',
    },
  };

  runInContext(context, `
    selectedAgentId = 'agent-1';
    _toggleAgentDetailTask('agent-1');
    agentDetailEditDescription('agent-1', 'task-1');
  `);

  const input = document.register('detail-description-input');
  input.value = 'Keep this draft steady across websocket refreshes';
  input.selectionStart = 17;
  input.selectionEnd = 22;
  main.appendChild(input);
  document.activeElement = input;

  runInContext(context, `
    agentDetailDescriptionInput('agent-1', 'task-1', 'Keep this draft steady across websocket refreshes');
    render();
  `);

  assert.equal(input.focused, true);
  assert.equal(input.value, 'Keep this draft steady across websocket refreshes');
  assert.equal(input.selectionStart, 17);
  assert.equal(input.selectionEnd, 22);
  assert.equal(
    jsonValue(context, `_agentDetailState('agent-1').description_editor.draft`),
    'Keep this draft steady across websocket refreshes',
  );
});

test('panel resize bounds stay narrow by default and expand in embedded runtime', () => {
  const { context, document } = createPanelHarness();

  assert.deepEqual(jsonValue(context, `_panelResizeBounds()`), {
    min: 80,
    max: 820,
  });
  assert.equal(jsonValue(context, `_normalizePanelHeight(120)`), 120);

  document.body.classList.add('runtime-embedded');

  assert.deepEqual(jsonValue(context, `_panelResizeBounds()`), {
    min: 180,
    max: 740,
  });
  assert.equal(jsonValue(context, `_normalizePanelHeight(120)`), 180);
  assert.equal(jsonValue(context, `_normalizePanelHeight(1200)`), 740);
});

test('standalone shell owns the full-width bottom dock rows and drag selector', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    css,
    /body\.runtime-embedded #workspace-shell\s*\{[^}]*grid-template-columns:\s*max\(var\(--standalone-sidebar-width\),\s*calc\(240px\s*\+\s*8px\s*\+\s*var\(--standalone-right-rail-width,\s*0px\)\)\)\s+8px\s+minmax\(0,\s*1fr\);/s,
  );
  assert.match(
    html,
    /<div id="standalone-sidebar-shell">\s*<div id="standalone-main-stack">\s*<main id="main"><\/main>\s*<\/div>\s*<div id="standalone-rail-resize-handle"[^>]*><\/div>\s*<aside id="standalone-right-rail"><\/aside>\s*<div id="standalone-bottom-resize-handle"[^>]*><\/div>\s*<section id="standalone-bottom-dock"><\/section>\s*<\/div>/s,
  );
  assert.match(
    css,
    /body\.runtime-embedded #standalone-sidebar-shell\s*\{[^}]*grid-template-columns:\s*minmax\(240px,\s*1fr\)\s+8px\s+var\(--standalone-right-rail-width,\s*320px\);[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)\s+8px\s+var\(--standalone-bottom-height,\s*280px\);/s,
  );
  assert.match(
    css,
    /body\.runtime-embedded #standalone-bottom-resize-handle\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;[^}]*grid-row:\s*2;/s,
  );
  assert.match(
    css,
    /body\.runtime-embedded #standalone-bottom-dock\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;[^}]*grid-row:\s*3;/s,
  );
  assert.match(
    css,
    /body\.runtime-embedded\.standalone-panel-dragging #standalone-bottom-dock,\s*body\.runtime-embedded\.standalone-panel-dragging #standalone-right-rail\s*\{/s,
  );
});

test('standalone keeps the legacy bottom panel parking host fully collapsed', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    css,
    /body\.runtime-embedded #bottom-panel\.collapsed\s*\{[^}]*height:\s*0;[^}]*border-top:\s*none;[^}]*box-shadow:\s*none;/s,
  );
});

test('standalone layout restore migrates legacy panel state into bottom and right docks', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  runInContext(context, `
    state = {
      panel_active: 'events',
      board_panel_height: 310,
      standalone_panel_layout: {},
    };
    _restoreStandalonePanelState();
  `);

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.size`), 310);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.active`), 'board');
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.active`), 'events');
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.open`), true);
});

test('standalone bottom resize uses shell bounds and updates the shell height var', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  const shell = document.getElementById('standalone-sidebar-shell');
  const stack = document.getElementById('standalone-main-stack');
  shell.getBoundingClientRect = () => ({ top: 20, bottom: 720, left: 0, right: 900, width: 900, height: 700 });
  stack.getBoundingClientRect = () => ({ top: 20, bottom: 360, left: 0, right: 540, width: 540, height: 340 });

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
    standalonePanelResizeBottom(320);
  `);

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.size`), 400);
  assert.equal(document.getElementById('standalone-sidebar-shell').style['--standalone-bottom-height'], '400px');
  assert.equal(document.getElementById('standalone-main-stack').style['--standalone-bottom-height'], undefined);
});

test('standalone right resize preserves the main stack minimum width', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  const shell = document.getElementById('standalone-sidebar-shell');
  shell.clientWidth = 900;
  shell.getBoundingClientRect = () => ({ top: 20, bottom: 720, left: 0, right: 900, width: 900, height: 700 });

  runInContext(context, `
    _workspaceSidebarWidth = 568;
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 280, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
    standalonePanelResizeRight(0);
  `);

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.size`), 320);
  assert.equal(document.getElementById('standalone-sidebar-shell').style['--standalone-right-rail-width'], '320px');
});

test('standalone outer resize shrinks the right rail when the main stack is already at its minimum', () => {
  const { context, document } = createWorkspaceResizeHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  const workspaceShell = document.getElementById('workspace-shell');
  workspaceShell.getBoundingClientRect = () => ({ top: 0, bottom: 720, left: 0, right: 1400, width: 1400, height: 720 });

  runInContext(context, `
    _workspaceSidebarWidth = 568;
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  document.getElementById('workspace-resize-handle').listeners.mousedown({
    preventDefault() {},
    clientX: 572,
    clientY: 100,
  });
  document.listeners.mousemove({
    clientX: 520,
    clientY: 100,
  });

  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 520);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.size`), 272);
  assert.equal(document.body.style['--standalone-sidebar-width'], '520px');
  assert.equal(document.getElementById('standalone-sidebar-shell').style['--standalone-right-rail-width'], '272px');

  document.listeners.mousemove({
    clientX: 440,
    clientY: 100,
  });

  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 488);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.size`), 240);
  assert.equal(document.body.style['--standalone-sidebar-width'], '488px');
  assert.equal(document.getElementById('standalone-sidebar-shell').style['--standalone-right-rail-width'], '240px');
});

test('standalone rail drag shrinks immediately from the grabbed edge at the min boundary', () => {
  const { context, document } = createPanelManagerHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  const shell = document.getElementById('standalone-sidebar-shell');
  const handle = document.getElementById('standalone-rail-resize-handle');
  shell.clientWidth = 568;
  shell.getBoundingClientRect = () => ({ top: 20, bottom: 720, left: 0, right: 568, width: 568, height: 700 });
  handle.getBoundingClientRect = () => ({ top: 20, bottom: 720, left: 240, right: 248, width: 8, height: 700 });

  runInContext(context, `
    _workspaceSidebarWidth = 568;
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  handle.listeners.mousedown({
    preventDefault() {},
    clientX: 240,
    clientY: 60,
  });
  document.listeners.mousemove({
    clientX: 245,
    clientY: 60,
  });

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.size`), 315);
});

test('standalone first full-state restore persists responsive defaults once', () => {
  const { context } = createStandaloneWsSyncHarness();
  context.window.innerWidth = 1400;
  context.window.innerHeight = 900;
  runInContext(context, `
    _panelStateRestored = false;
    sendCalls = [];
    send = function(message) { sendCalls.push(message); };
  `);

  context._handleFullState({
    seq: 1,
    runtime: { embedded_terminal: true },
    agents: {},
    groups: {},
    children: {},
    board_tasks: {},
    panel_events: [],
    active_session_id: null,
    standalone_panel_layout: {},
  });

  assert.equal(context.localStorage.getItem('loom.ide.sidebar_width'), '784');
  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 784);
  assert.equal(jsonValue(context, `sendCalls.length`), 1);
  assert.deepEqual(jsonValue(context, `sendCalls[0]`), {
    cmd: 'standalone_set_panel_layout',
    layout: {
      version: 1,
      bottom: { open: true, size: 306, tabs: ['board'], active: 'board' },
      right: {
        open: true,
        size: 298,
        tabs: ['actions', 'templates', 'context', 'events'],
        active: 'context',
      },
      floats: {},
      last_active: 'board',
    },
  });
});

test('standalone startup preserves persisted layouts by widening the sidebar when needed', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.window.innerWidth = 1400;

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      panel_active: 'weaver',
      board_panel_height: 420,
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board', 'weaver'], active: 'weaver' },
        right: { open: true, size: 320, tabs: ['actions', 'events'], active: 'events' },
        floats: {},
        last_active: 'events',
      },
    };
    _restorePanelState();
  `);

  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 568);
  assert.equal(context.localStorage.getItem('loom.ide.sidebar_width'), null);
  assert.equal(jsonValue(context, `sendCalls.length`), 0);
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout()`), {
    version: 1,
    bottom: { open: true, size: 280, tabs: ['board', 'weaver'], active: 'weaver' },
    right: { open: true, size: 320, tabs: ['actions', 'events'], active: 'events' },
    floats: {},
    last_active: 'events',
  });
});

test('standalone startup widens a saved sidebar width that cannot fit the open right rail', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.window.innerWidth = 1400;
  context.localStorage.setItem('loom.ide.sidebar_width', '340');

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
        right: { open: true, size: 320, tabs: ['context'], active: 'context' },
        floats: {},
        last_active: 'context',
      },
    };
    _restorePanelState();
  `);

  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 568);
  assert.equal(context.localStorage.getItem('loom.ide.sidebar_width'), '568');
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.size`), 320);
});

test('standalone startup preserves a saved sidebar width', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.localStorage.setItem('loom.ide.sidebar_width', '612');

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
        right: { open: true, size: 320, tabs: ['context'], active: 'context' },
        floats: {},
        last_active: 'context',
      },
    };
    _restorePanelState();
  `);

  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 612);
  assert.equal(document.body.style['--standalone-sidebar-width'], '612px');
  assert.equal(jsonValue(context, `sendCalls.length`), 0);
});

test('restoreStandaloneLayoutDefaults resets width and ignores legacy state', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.window.innerWidth = 1400;
  context.window.innerHeight = 900;
  context.localStorage.setItem('loom.ide.sidebar_width', '612');

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      panel_active: 'weaver',
      board_panel_height: 420,
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board', 'weaver'], active: 'weaver' },
        right: { open: true, size: 320, tabs: ['actions', 'events'], active: 'events' },
        floats: {
          context: { x: 50, y: 60, width: 400, height: 300, z: 2 },
        },
        last_active: 'weaver',
      },
    };
    _restorePanelState();
    sendCalls = [];
    restoreStandaloneLayoutDefaults();
  `);

  assert.equal(context.localStorage.getItem('loom.ide.sidebar_width'), '784');
  assert.equal(jsonValue(context, `_workspaceSidebarWidth`), 784);
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout()`), {
    version: 1,
    bottom: { open: true, size: 306, tabs: ['board'], active: 'board' },
    right: {
      open: true,
      size: 298,
      tabs: ['actions', 'templates', 'context', 'events'],
      active: 'context',
    },
    floats: {},
    last_active: 'board',
  });
  assert.deepEqual(jsonValue(context, `sendCalls`), [{
    cmd: 'standalone_set_panel_layout',
    layout: {
      version: 1,
      bottom: { open: true, size: 306, tabs: ['board'], active: 'board' },
      right: {
        open: true,
        size: 298,
        tabs: ['actions', 'templates', 'context', 'events'],
        active: 'context',
      },
      floats: {},
      last_active: 'board',
    },
  }]);
});

test('standalone startup restore keeps panel roots attached across the first double render', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();
  runInContext(context, `_panelStateRestored = false;`);

  context._handleFullState({
    seq: 1,
    runtime: { embedded_terminal: true },
    agents: {},
    groups: {},
    children: {},
    board_tasks: {},
    panel_events: [],
    active_session_id: null,
    standalone_panel_layout: {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board', 'weaver'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions', 'templates', 'context', 'events'], active: 'context' },
      floats: {},
      last_active: 'context',
    },
  });

  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-board', 'panel-agent']);
  assert.deepEqual(
    standaloneZoneBodyPanelIds(document, 'standalone-right-rail'),
    ['panel-actions', 'panel-templates', 'panel-context', 'panel-events']
  );
});

test('standalone task deltas rerender every visible docked surface', () => {
  const { context, sandbox } = createWsRenderHarness();

  runInContext(context, `
    isEmbeddedTerminalMode = function() { return true; };
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context', 'events'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  context._handleDelta({
    seq: 1,
    ops: [
      { op: 'task_upsert', id: 'task-1', group: 'alpha', task: 'Ship docs', lane: 'Backlog', position: 1 },
    ],
  });

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 1,
    actions: 0,
    context: 1,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('standalone render tracks Actions as a visible surface', () => {
  const { context, sandbox } = createWsRenderHarness();

  runInContext(context, `
    isEmbeddedTerminalMode = function() { return true; };
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions'], active: 'actions' },
      floats: {},
      last_active: 'actions',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  assert.deepEqual(jsonValue(context, '_currentPanelSurfaces()'), ['board', 'actions']);

  context.renderActivePanel();

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 1,
    actions: 1,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('standalone full-state resync loads and rerenders newly visible Actions', () => {
  const { context, sandbox } = createStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: false, size: 320, tabs: ['actions'], active: 'actions' },
      floats: {},
      last_active: 'board',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
    tplEditorLoadCalls = 0;
  `);
  sandbox.renderCalls = { main: 0, board: 0, actions: 0, context: 0, events: 0, weaver: 0, templates: 0 };

  context._handleFullState({
    seq: 1,
    runtime: { embedded_terminal: true },
    agents: {},
    groups: {},
    children: {},
    board_tasks: {},
    panel_events: [],
    active_session_id: null,
    standalone_panel_layout: {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions'], active: 'actions' },
      floats: {},
      last_active: 'actions',
    },
  });

  assert.equal(sandbox.tplEditorLoadCalls, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 1,
    board: 1,
    actions: 1,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('standalone full-state rerender keeps panel roots attached', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  context._handleFullState({
    seq: 1,
    runtime: { embedded_terminal: true },
    agents: {},
    groups: {},
    children: {},
    board_tasks: {},
    panel_events: [],
    active_session_id: null,
    standalone_panel_layout: {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'actions' },
      floats: {},
      last_active: 'actions',
    },
  });

  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-right-rail'), ['panel-actions', 'panel-context']);
  assert.deepEqual(parkedStandalonePanelIds(document), ['panel-templates', 'panel-events', 'panel-agent']);
});

test('standalone layout ui_update loads and rerenders newly visible Actions', () => {
  const { context, sandbox } = createStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
    tplEditorLoadCalls = 0;
  `);
  sandbox.renderCalls = { main: 0, board: 0, actions: 0, context: 0, events: 0, weaver: 0, templates: 0 };

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'ui_update',
        key: 'standalone_panel_layout',
        value: {
          version: 1,
          bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
          right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'actions' },
          floats: {},
          last_active: 'actions',
        },
      },
    ],
  });

  assert.equal(sandbox.tplEditorLoadCalls, 1);
  assert.deepEqual(jsonValue(context, '_currentPanelSurfaces()'), ['board', 'actions']);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.renderCalls)), {
    main: 0,
    board: 1,
    actions: 1,
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('standalone layout ui_update keeps panel roots attached across rerenders', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'ui_update',
        key: 'standalone_panel_layout',
        value: {
          version: 1,
          bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
          right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'actions' },
          floats: {},
          last_active: 'actions',
        },
      },
    ],
  });

  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-right-rail'), ['panel-actions', 'panel-context']);
  assert.deepEqual(parkedStandalonePanelIds(document), ['panel-templates', 'panel-events', 'panel-agent']);
});

test('standalone bottom-dock drop moves a panel to the shell-owned dock and preserves roots across rerenders', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context', 'actions'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  context.standalonePanelDragStart({
    dataTransfer: {
      setData() {},
      effectAllowed: '',
    },
  }, 'context');
  context.standalonePanelZoneDrop({
    preventDefault() {},
    currentTarget: document.getElementById('standalone-bottom-dock'),
    dataTransfer: {
      getData() { return 'context'; },
    },
  }, 'bottom');

  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().bottom.tabs`), ['board', 'context']);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.active`), 'context');
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().right.tabs`), ['actions']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-board', 'panel-context']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-right-rail'), ['panel-actions']);
  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);

  const droppedLayout = jsonValue(context, `_standalonePanelCurrentLayout()`);
  context._handleDelta({
    seq: 1,
    ops: [
      {
        op: 'ui_update',
        key: 'standalone_panel_layout',
        value: droppedLayout,
      },
    ],
  });

  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-board', 'panel-context']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-right-rail'), ['panel-actions']);
});

test('standalone drag temporarily reveals an empty bottom dock and allows redocking', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();
  const shell = document.getElementById('standalone-sidebar-shell');
  const bottomDock = document.getElementById('standalone-bottom-dock');

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: [], active: '' },
      right: { open: true, size: 320, tabs: ['board', 'context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  assert.equal(shell.style['--standalone-bottom-height'], '0px');
  assert.equal(bottomDock.classList.contains('collapsed'), true);

  context.standalonePanelDragStart(null, 'context');

  assert.equal(shell.style['--standalone-bottom-height'], '72px');
  assert.equal(bottomDock.classList.contains('collapsed'), false);
  assert.equal(
    !!findChildByClassName(findChildByClassName(bottomDock, 'standalone-panel-zone-body'), 'standalone-panel-empty-drop'),
    true,
  );

  context.standalonePanelZoneDrop({
    preventDefault() {},
    currentTarget: bottomDock,
    dataTransfer: {
      getData() { return 'context'; },
    },
  }, 'bottom');

  assert.equal(shell.style['--standalone-bottom-height'], '280px');
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().bottom.tabs`), ['context']);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.active`), 'context');
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().right.tabs`), ['board']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-context']);
});

test('standalone pointer drag moves a panel without native drag events', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();
  const bottomDock = document.getElementById('standalone-bottom-dock');

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context', 'actions'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  document.elementFromPoint = function() {
    return bottomDock;
  };

  context.standalonePanelPointerDragStart({
    button: 0,
    clientX: 720,
    clientY: 120,
  }, 'context');
  document.listeners.mousemove({
    clientX: 220,
    clientY: 520,
    preventDefault() {},
  });
  document.listeners.mouseup({
    clientX: 220,
    clientY: 520,
    preventDefault() {},
  });

  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().bottom.tabs`), ['board', 'context']);
  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.active`), 'context');
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().right.tabs`), ['actions']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-board', 'panel-context']);
  assert.equal(document.body.classList.contains('standalone-panel-dragging'), false);
});

test('standalone pointer drag ignores releases outside dock targets', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();

  runInContext(context, `
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context', 'actions'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  document.elementFromPoint = function() {
    return document.getElementById('standalone-main-stack');
  };

  context.standalonePanelPointerDragStart({
    button: 0,
    clientX: 720,
    clientY: 120,
  }, 'context');
  document.listeners.mousemove({
    clientX: 420,
    clientY: 240,
    preventDefault() {},
  });
  document.listeners.mouseup({
    clientX: 420,
    clientY: 240,
    preventDefault() {},
  });

  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().bottom.tabs`), ['board']);
  assert.deepEqual(jsonValue(context, `_standalonePanelCurrentLayout().right.tabs`), ['context', 'actions']);
  assert.equal(jsonValue(context, `Object.keys(_standalonePanelCurrentLayout().floats).length`), 0);
  assert.equal(document.body.classList.contains('standalone-panel-dragging'), false);
});

test('standalone persisted float restore keeps floated panel roots attached', () => {
  const { context, document } = createAttachedStandaloneWsSyncHarness();
  runInContext(context, `_panelStateRestored = false;`);

  context._handleFullState({
    seq: 1,
    runtime: { embedded_terminal: true },
    agents: {},
    groups: {},
    children: {},
    board_tasks: {},
    panel_events: [],
    active_session_id: null,
    standalone_panel_layout: {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {
        actions: { x: 48, y: 72, width: 420, height: 320, z: 2 },
      },
      last_active: 'actions',
    },
  });

  assert.deepEqual(attachedStandalonePanelIds(document), PANEL_ROOT_IDS);
  assert.deepEqual(standaloneFloatPanelIds(document), ['panel-actions']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-bottom-dock'), ['panel-board']);
  assert.deepEqual(standaloneZoneBodyPanelIds(document, 'standalone-right-rail'), ['panel-context']);
});

test('opening Actions in standalone loads the actions editor', () => {
  const { context, document, sandbox } = createMainHarness({
    tplEditorLoad() {
      sandbox.tplEditorLoadCalls = (sandbox.tplEditorLoadCalls || 0) + 1;
    },
    renderActivePanel() {
      sandbox.renderActivePanelCalls = (sandbox.renderActivePanelCalls || 0) + 1;
    },
  });
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
        right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'context' },
        floats: {},
        last_active: 'context',
      },
    };
    _restoreStandalonePanelState();
  `);

  context.togglePanel('actions');

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.active`), 'actions');
  assert.equal(jsonValue(context, `_standalonePanelSurfaceVisible('actions')`), true);
  assert.equal(sandbox.tplEditorLoadCalls, 1);
  assert.equal(sandbox.renderActivePanelCalls, 1);
});

test('selecting an existing standalone Actions tab loads and renders the actions editor', () => {
  const { context, document, sandbox } = createMainHarness({
    tplEditorLoad() {
      sandbox.tplEditorLoadCalls = (sandbox.tplEditorLoadCalls || 0) + 1;
    },
    renderActivePanel() {
      sandbox.renderActivePanelCalls = (sandbox.renderActivePanelCalls || 0) + 1;
    },
  });
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };

  runInContext(context, `
    state = {
      runtime: { embedded_terminal: true },
      standalone_panel_layout: {
        version: 1,
        bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
        right: { open: true, size: 320, tabs: ['actions', 'context'], active: 'context' },
        floats: {},
        last_active: 'context',
      },
    };
    _restoreStandalonePanelState();
  `);

  runInContext(context, `_standaloneSelectPanel('actions')`);

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().right.active`), 'actions');
  assert.equal(jsonValue(context, `_standalonePanelSurfaceVisible('actions')`), true);
  assert.equal(sandbox.tplEditorLoadCalls, 1);
  assert.equal(sandbox.renderActivePanelCalls, 1);
});

test('visible standalone context rerenders even when it is not last active', () => {
  const { context, document } = createMainHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  loadScript(context, 'static/js/context.js');

  runInContext(context, `
    var renderContextPanelCalls = 0;
    renderContextPanel = function() { renderContextPanelCalls++; };
    state.runtime = { embedded_terminal: true };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'board',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  context.handleContextEntries({
    entries: [
      { id: 'entry-1', entry_type: 'note', content: 'Remember the fix' },
    ],
  });

  assert.equal(jsonValue(context, 'renderContextPanelCalls'), 1);
});

test('board navigation does not collapse an already visible standalone board', () => {
  const { context, document, sandbox } = createMainHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  loadScript(context, 'static/js/render.js');
  loadBoardScripts(context);

  runInContext(context, `
    var renderBoardCalls = 0;
    renderBoard = function() { renderBoardCalls++; };
    _renderBoardSelectionBar = function() { return ''; };
    _boardScheduleCount = function() { return 0; };
    boardUpdateScrollArrows = function() {};
    boardAddTaskAutoResize = function() {};
    state.runtime = { embedded_terminal: true };
    state.board_tasks = {
      'task-1': {
        id: 'task-1',
        group: 'alpha',
        task: 'Ship docs',
        lane: 'Backlog',
        position: 1,
      },
    };
    state.standalone_panel_layout = {
      version: 1,
      bottom: { open: true, size: 280, tabs: ['board'], active: 'board' },
      right: { open: true, size: 320, tabs: ['context'], active: 'context' },
      floats: {},
      last_active: 'context',
    };
    _standalonePanelSetLayoutFromState(state.standalone_panel_layout, { fromServer: true });
  `);

  const originalTogglePanel = context.togglePanel;
  sandbox.togglePanelCalls = [];
  context.togglePanel = function(appName) {
    sandbox.togglePanelCalls.push(appName);
    return originalTogglePanel.call(this, appName);
  };

  context.boardNavigateToTask('task-1');

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.open`), true);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.togglePanelCalls)), []);
  assert.equal(jsonValue(context, 'renderBoardCalls'), 1);
});

test('collapsing the embedded board returns keyboard focus to the terminal workspace', () => {
  const { context, document } = createPanelHarness();
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.focusEmbeddedTerminalWorkspaceCalls = 0;
  context.focusEmbeddedTerminalWorkspace = function(force) {
    context.focusEmbeddedTerminalWorkspaceCalls += force ? 1 : 0;
    return true;
  };
  runInContext(context, `
    state = { panel_active: 'board', board_panel_height: 280, standalone_panel_layout: {} };
    _restoreStandalonePanelState();
  `);

  context.togglePanel('board');

  assert.equal(jsonValue(context, `_standalonePanelCurrentLayout().bottom.open`), false);
  assert.equal(context.focusEmbeddedTerminalWorkspaceCalls, 1);
});

test('collapsing the classic board does not refocus the embedded terminal workspace', () => {
  const { context, document } = createPanelHarness();
  const panel = document.register('bottom-panel');
  panel.classList.remove('collapsed');
  context.isEmbeddedTerminalMode = function() { return false; };
  context.focusEmbeddedTerminalWorkspaceCalls = 0;
  context.focusEmbeddedTerminalWorkspace = function() {
    context.focusEmbeddedTerminalWorkspaceCalls += 1;
  };
  runInContext(context, `_activePanelApp = 'board';`);

  context.togglePanel('board');

  assert.equal(panel.classList.contains('collapsed'), true);
  assert.equal(context.focusEmbeddedTerminalWorkspaceCalls, 0);
});

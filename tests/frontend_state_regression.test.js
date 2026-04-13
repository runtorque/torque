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
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    this.children = [];
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (child.selected) this.value = child.value;
    return child;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
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
    return this.children.includes(target);
  }

  closest(selector) {
    if (!selector || selector.charAt(0) !== '.') return null;
    return this.classList.contains(selector.slice(1)) ? this : null;
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
  }

  register(id, element = new FakeElement(id)) {
    this.elements.set(id, element);
    return element;
  }

  getElementById(id) {
    return this.elements.get(id) || null;
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
}

function createSandbox(overrides = {}) {
  const document = new FakeDocument();
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    document,
    window: { open() {} },
    location: { host: 'localhost:9000' },
    navigator: { clipboard: { writeText() {} } },
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

function createEmbeddedTerminalHarness() {
  const sockets = [];
  const terminals = [];

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

  const { sandbox, document } = createSandbox({
    Terminal: FakeTerminal,
    FitAddon: { FitAddon: FakeFitAddon },
    ResizeObserver: FakeResizeObserver,
    WebSocket: FakeWebSocket,
    location: { protocol: 'http:', host: 'localhost:9000' },
  });
  const workspace = document.register('terminal-workspace');
  const status = new FakeElement('terminal-statusbar');
  workspace.setQuerySelector('.terminal-statusbar', status);
  document.setSelector('#terminal-workspace .terminal-statusbar', status);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/terminal.js');
  return { context, sandbox, document, sockets, terminals, status };
}

function runInContext(context, code) {
  return vm.runInContext(code, context);
}

function jsonValue(context, expression) {
  return JSON.parse(runInContext(context, `JSON.stringify(${expression})`));
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
  loadScript(context, 'static/js/weaver.js');
  return { context, document };
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
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  };
  document.register('main');
  document.register('bottom-panel');
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/render.js');
  runInContext(context, `
    render = function() { renderCalls.main++; };
    renderBoard = function() { renderCalls.board++; };
    renderContextPanel = function() { renderCalls.context++; };
    renderEvents = function() { renderCalls.events++; };
    renderWeaverPanel = function() { renderCalls.weaver++; };
    renderAgentTemplatesPanel = function() { renderCalls.templates++; };
    updateEventsAttentionBadge = function() {};
    _expectedSeq = 1;
  `);
  return { context, document, sandbox };
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
    'add-name-input',
    'add-cmd-input',
    'add-dir-input',
    'add-args-input',
    'add-init-input',
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
  loadScript(context, 'static/js/main.js');
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
  return { context, document };
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

  const html = runInContext(context, `_weaverRenderJournal('alpha')`);

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
  assert.equal((panel.innerHTML.match(/data-board-lane-column="1"/g) || []).length, 4);
  assert.match(panel.innerHTML, /board-wide-lane-name">Backlog/);
  assert.match(panel.innerHTML, /board-wide-lane-name">Done/);

  panel.clientWidth = 820;
  context.renderBoard();
  assert.doesNotMatch(panel.innerHTML, /board-wide-grid/);

  document.body.classList.remove('runtime-embedded');
  panel.clientWidth = 1200;
  context.renderBoard();
  assert.doesNotMatch(panel.innerHTML, /board-wide-grid/);
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

test('board quick views expose recent and recently touched tasks through the existing filter model', () => {
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
  assert.match(panel.innerHTML, /Recently Touched/);

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
  assert.match(panel.innerHTML, /Recently Touched/);

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
  assert.equal((html.match(/board-card-status/g) || []).length, 1);
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
});

test('_boardLaneEntryText and refresh delay track the current lane age', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardScripts(context);

  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryText(
        { lane_entered_at: '2026-04-07T12:00:00Z' },
        Date.parse('2026-04-07T12:05:00Z')
      )`,
    ),
    '[5m ago]',
  );
  assert.equal(
    runInContext(
      context,
      `_boardLaneEntryText(
        { lane_entered_at: '2026-04-07T12:00:00Z' },
        Date.parse('2026-04-07T12:06:00Z')
      )`,
    ),
    '[6m ago]',
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

test('_renderBoardCard shows the lane-entry timestamp badge in the card header', () => {
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

  assert.match(html, /board-card-heading/);
  assert.match(html, /board-card-task-id/);
  assert.match(html, /root/);
  assert.match(html, /board-card-lane-entered/);
  assert.match(html, /\[5m ago\]/);
  assert.match(html, /board-card-heading-meta/);
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
      title: 'Missing action "missing/action" and template "missing-template"',
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
    { cmd: 'list_templates', group: 'alpha' },
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
    connect();
    _resyncPending = true;
    _awaitingFullState = true;
    ws.onclose();
  `);

  assert.equal(jsonValue(context, '_resyncPending'), false);
  assert.equal(jsonValue(context, '_awaitingFullState'), false);
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

  context.focusAgent('term-2');
  assert.equal(jsonValue(context, 'selectedAgentId'), 'agent-2');
  assert.equal(jsonValue(context, '_currentGroup()'), 'beta');
  assert.equal(jsonValue(context, 'renderCalls.board'), 1);
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
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('renderWeaverPanel preserves focused reply draft across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-weaver');
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
  context.renderWeaverPanel();

  assert.equal(input.focused, true);
  assert.equal(input.value, 'Proceed with merge');
  assert.equal(input.selectionStart, 8);
  assert.equal(input.selectionEnd, 13);
});

test('renderWeaverPanel preserves the selected Events tab across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-weaver');
  panel.querySelector = function() { return null; };
  context.state.weaver_buffer_stats = {
    alpha: {
      buffered_events: 1,
      next_push_in: 30,
      queued_events: [
        { id: 3, kind: 'task_completed', message: 'Queued event', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  context.state.weaver_sent_events = {
    alpha: [
      { id: 2, kind: 'task_completed', message: 'Sent event', timestamp: 5, delivered_at: 8 },
    ],
  };

  context.renderWeaverPanel();
  runInContext(context, `weaverSelectTab('events')`);

  assert.match(panel.innerHTML, /id="weaver-tab-events" class="weaver-tab active"/);
  assert.match(panel.innerHTML, /Queued for next digest/);

  context.renderWeaverPanel();

  assert.match(panel.innerHTML, /id="weaver-tab-events" class="weaver-tab active"/);
  assert.match(panel.innerHTML, /Already sent to Weaver/);
});

test('renderWeaverPanel preserves the selected Worklog tab across rerenders', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-weaver');
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
    'agent-1': { id: 'agent-1', name: 'Worker', group: 'alpha', cell_type: 'agent' },
  };

  context.renderWeaverPanel();
  runInContext(context, `weaverSelectTab('worklog')`);

  assert.match(panel.innerHTML, /id="weaver-tab-worklog" class="weaver-tab active"/);
  assert.match(panel.innerHTML, /Dispatched tasks/);

  context.renderWeaverPanel();

  assert.match(panel.innerHTML, /id="weaver-tab-worklog" class="weaver-tab active"/);
  assert.match(panel.innerHTML, /Awaiting approval/);
});

test('renderWeaverPanel keeps the same Events anchor visible when new digest rows are inserted above', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-weaver');
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
    if (selector === '.weaver-content') return currentContent;
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

  context.state.weaver_buffer_stats = {
    alpha: {
      buffered_events: 0,
      next_push_in: 0,
      queued_events: [],
      manual_flush_requested: false,
    },
  };
  context.state.weaver_sent_events = {
    alpha: [
      { id: 2, kind: 'task_completed', message: 'Older digest', timestamp: 5, delivered_at: 8 },
      { id: 1, kind: 'task_completed', message: 'Oldest digest', timestamp: 4, delivered_at: 6 },
    ],
  };
  runInContext(context, `_weaverActiveTabByGroup.alpha = 'events';`);

  context.renderWeaverPanel();

  assert.equal(newContent.scrollTop, 120);
});

test('renderAgentCell shows a weaver-only pause control with state-driven classes', () => {
  const { context } = createWeaverHarness();
  context.state.group_settings = {
    alpha: { weaver_agent_id: 'weaver-1' },
  };
  context.state.children = {};
  context.state.weaver_settings = {
    alpha: { paused: false },
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
  const workerHtml = context.renderAgentCell({
    id: 'agent-1',
    name: 'Worker',
    icon: '🤖',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  });

  assert.match(runningHtml, /class="cell selected weaver weaver-running"/);
  assert.match(runningHtml, /class="cell-header-controls">/);
  assert.match(runningHtml, /class="cell-weaver-toggle running"/);
  assert.match(runningHtml, /Pause Weaver event delivery/);
  assert.doesNotMatch(workerHtml, /cell-weaver-toggle/);

  context.state.weaver_settings.alpha.paused = true;
  const pausedHtml = context.renderAgentCell({
    id: 'weaver-1',
    name: 'Weaver',
    icon: '🧶',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  });

  assert.match(pausedHtml, /class="cell selected weaver weaver-paused"/);
  assert.match(pausedHtml, /class="cell-weaver-toggle paused"/);
  assert.match(pausedHtml, /Resume Weaver event delivery/);

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

  assert.match(askingHtml, /class="cell selected weaver weaver-asking weaver-running"/);
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

test('selected weaver cards keep selection chrome aligned with running and paused status colors', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /\.cell\.weaver\s*\{[^}]*--weaver-chrome:\s*transparent;[^}]*--weaver-edge-shadow:\s*none;[^}]*border-left-color:\s*var\(--weaver-chrome\);[^}]*box-shadow:\s*var\(--weaver-edge-shadow\);/);
  assert.match(css, /\.cell\.weaver\.weaver-running\s*\{[^}]*--weaver-chrome:\s*var\(--green\);/);
  assert.match(css, /\.cell\.weaver\.weaver-paused\s*\{[^}]*--weaver-chrome:\s*var\(--amber\);/);
  assert.match(css, /\.cell\.weaver\.selected\s*\{[^}]*border-color:\s*var\(--weaver-chrome\);/);
  assert.match(css, /\.cell\.weaver\.focused\s*\{[^}]*outline-color:\s*var\(--weaver-chrome\);/);
  assert.match(css, /\.cell\.weaver\.selected\.active\s*\{[^}]*box-shadow:\s*var\(--weaver-active-glow\),\s*var\(--weaver-edge-shadow\);/);
  assert.match(css, /\.cell\.weaver-asking\s*\{[^}]*--weaver-chrome:\s*var\(--amber\);[^}]*animation:\s*weaver-pulse 2s ease-in-out infinite;/);
  assert.match(css, /@keyframes weaver-pulse\s*\{\s*0%,\s*100%\s*\{\s*box-shadow:\s*var\(--weaver-edge-shadow\);[^}]*\}\s*50%\s*\{\s*box-shadow:\s*var\(--weaver-edge-shadow\),\s*0 0 8px rgba\(210, 153, 34, 0\.3\);/);
});

test('renderWeaverPanel shows branch review-point summary in Session Map view', () => {
  const { context, document } = createWeaverHarness();
  const panel = document.register('panel-weaver');

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
      name: 'Worker',
      group: 'alpha',
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

  context.renderWeaverPanel();

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
    context: 0,
    events: 0,
    weaver: 0,
    templates: 0,
  });
});

test('weaver sent-event deltas rerender only the active Weaver panel', () => {
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
    context: 0,
    events: 0,
    weaver: 1,
    templates: 0,
  });
  assert.deepEqual(jsonValue(context, 'state.weaver_sent_events.alpha'), [
    { id: 11, kind: 'task_completed', message: 'Merged cleanly', timestamp: 1, delivered_at: 2 },
  ]);
});

test('weaver worklog deltas rerender only the active Weaver panel', () => {
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

test('weaver stream deltas rerender only the active Weaver panel', () => {
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

test('weaver Session Map responses rerender only the active Weaver panel', () => {
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
  const futureIso = '2099-01-04T12:30:00.000Z';

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
    },
  };

  context.openEditTask('task-1');

  assert.equal(document.getElementById('task-modal-title').textContent, 'Edit Task');
  assert.equal(document.getElementById('task-submit-btn').textContent, 'Save');
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
    { cmd: 'list_templates', group: 'beta' },
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

test('diff review overlay hides the workspace shell so standalone merge review uses the full viewport', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /body\.diff-view-open #workspace-shell,\s*body\.diff-view-open main,\s*body\.diff-view-open #bottom-panel,\s*body\.diff-view-open #taskbar,\s*body\.diff-view-open #broadcast,\s*body\.diff-view-open #ctx-menu\s*\{[^}]*display:\s*none\s*!important;/);
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
  assert.match(main.innerHTML, /newWeaver\('alpha'\)/);
  assert.match(main.innerHTML, /quickAddAgent\('alpha'\)/);
  assert.match(main.innerHTML, /openAddAgentAdvanced\('alpha'\)/);
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

test('agent create menu action stays standalone-only for the advanced modal path', () => {
  const classic = createMainRenderHarness();
  assert.deepEqual(jsonValue(classic.context, `_agentCreateMenuAction('alpha')`), {
    label: 'Custom…',
    action: "openAddAgent('alpha')",
  });

  const embedded = createStandaloneRenderHarness();
  assert.deepEqual(jsonValue(embedded.context, `_agentCreateMenuAction('alpha')`), {
    label: 'Advanced…',
    action: "openAddAgentAdvanced('alpha')",
  });
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

test('desktop runtime metadata does not change standalone embedded-runtime detection', () => {
  const { context, sandbox } = createStandaloneRenderHarness();

  sandbox.state.runtime = {
    embedded_terminal: true,
    desktop_shell: 'pywebview',
    profile: 'desktop',
    data_dir: '/Users/aleks/.loom/profiles/desktop',
    port: 18933,
    home_directory: '/Users/aleks',
  };

  assert.equal(jsonValue(context, `_embeddedRuntimeEnabled()`), true);
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

test('main render pins the weaver first in the visible and navigable agent order', () => {
  const { context, document, sandbox } = createMainRenderHarness();
  const main = document.getElementById('main');

  sandbox.state.groups = { alpha: ['agent-1', 'weaver-1', 'agent-2'] };
  sandbox.state.group_settings = {
    alpha: { collapsed_default: false, weaver_agent_id: 'weaver-1' },
  };
  sandbox.state.children = {};
  sandbox.state.agents = {
    'agent-1': {
      id: 'agent-1',
      name: 'Worker One',
      group: 'alpha',
      cell_type: 'agent',
      icon: '1',
      status: 'running',
      session_id: 'sess-1',
    },
    'weaver-1': {
      id: 'weaver-1',
      name: 'Weaver Prime',
      group: 'alpha',
      cell_type: 'agent',
      icon: 'W',
      status: 'running',
      session_id: 'sess-weaver',
    },
    'agent-2': {
      id: 'agent-2',
      name: 'Worker Two',
      group: 'alpha',
      cell_type: 'agent',
      icon: '2',
      status: 'running',
      session_id: 'sess-2',
    },
  };

  runInContext(context, `render();`);

  assert.deepEqual(jsonValue(context, `window._navAgents`), [
    'weaver-1',
    'agent-1',
    'agent-2',
  ]);
  assert.match(main.innerHTML, /Weaver Prime[\s\S]*Worker One[\s\S]*Worker Two/);
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

test('collapsing the embedded board returns keyboard focus to the terminal workspace', () => {
  const { context, document } = createPanelHarness();
  const panel = document.register('bottom-panel');
  panel.classList.remove('collapsed');
  document.body.classList.add('runtime-embedded');
  context.isEmbeddedTerminalMode = function() { return true; };
  context.focusEmbeddedTerminalWorkspaceCalls = 0;
  context.focusEmbeddedTerminalWorkspace = function(force) {
    context.focusEmbeddedTerminalWorkspaceCalls += force ? 1 : 0;
    return true;
  };
  runInContext(context, `_activePanelApp = 'board';`);

  context.togglePanel('board');

  assert.equal(panel.classList.contains('collapsed'), true);
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

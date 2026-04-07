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
    this.style = {};
    this.children = [];
    this.classList = new FakeClassList();
    this.scrollTop = 0;
    this.scrollHeight = 600;
    this.clientHeight = 240;
    this.scrollLeft = 0;
    this.scrollWidth = 600;
    this.clientWidth = 240;
    this.offsetLeft = 0;
    this.offsetWidth = 80;
    this.selectionStart = 0;
    this.selectionEnd = 0;
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
    this.body = new FakeElement('body');
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
    return this.selectorMap.get(selector) || null;
  }

  querySelectorAll() {
    return [];
  }

  setSelector(selector, element) {
    this.selectorMap.set(selector, element);
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

function runInContext(context, code) {
  return vm.runInContext(code, context);
}

function jsonValue(context, expression) {
  return JSON.parse(runInContext(context, `JSON.stringify(${expression})`));
}

function createBoardHarness(options = {}) {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board.js');
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

function createEventsHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/events.js');
  runInContext(context, `
    _renderAttentionCard = function(item) { return '<div class="attention-item">' + item.id + '</div>'; };
    _renderEventEntry = function(evt) { return '<div class="event-entry">' + evt.kind + '</div>'; };
    _eventsOnScroll = function() {};
  `);
  return { context, document };
}

function createWeaverHarness() {
  const { sandbox, document } = createSandbox({
    _cachedProviders: [],
    _esc(value) { return String(value); },
  });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/weaver.js');
  return { context, document };
}

function createModalHarness() {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
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
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/diff.js');
  document.register('diff-view-root');
  return { context, document };
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

test('weaver overlap summary prioritizes conflicts before warnings', () => {
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

  const summary = jsonValue(context, `_weaverOverlapSummary('alpha')`);

  assert.equal(summary.total, 3);
  assert.deepEqual(summary.items.map((item) => item.task_id), ['conflict', 'warn']);
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
  loadScript(context, 'static/js/board.js');

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

test('renderBoardCard shows verification badges and preview text', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board.js');

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

test('renderBoardCard shows overlap badges and preview text', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board.js');

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

  assert.match(html, /board-card-overlap-conflict/);
  assert.match(html, /Overlap conflict/);
  assert.match(html, /Shares the same worktree branch as another active agent/);
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
      lane: 'Done',
      labels: ['loom:archived'],
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
      labels: ['loom:error', 'priority:medium'],
    },
  ]);
});

test('_renderBoardCard includes compact quick-edit controls for focused root cards', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board.js');

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
      cmd: 'board_update_task',
      id: 'root',
      labels: ['bug', 'loom:archived'],
    },
    {
      cmd: 'board_update_task',
      id: 'child',
      labels: ['loom:error', 'loom:archived'],
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
      lane: 'Done',
      labels: ['bug', 'loom:archived'],
    },
    child: {
      id: 'child',
      group: 'alpha',
      task: 'Verify release',
      lane: 'Done',
      parent_task_id: 'root',
      labels: ['loom:error', 'loom:archived'],
    },
  };

  runInContext(context, `
    _boardSelectedTasks = { root: true };
  `);

  context.boardBulkRestoreSelected();

  assert.deepEqual(jsonValue(context, 'sendCalls'), [
    {
      cmd: 'board_update_task',
      id: 'root',
      labels: ['bug'],
    },
    {
      cmd: 'board_update_task',
      id: 'child',
      labels: ['loom:error'],
    },
  ]);
  assert.equal(runInContext(context, '_boardSelectedCount()'), 0);
});

test('_renderBoardSelectionBar explains mixed-group limits when batch edit is open', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board.js');
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
  loadScript(context, 'static/js/board.js');
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
      lane: 'Done',
      labels: ['loom:archived'],
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
      cmd: 'board_update_task',
      id: 'stale',
      labels: ['bug', 'loom:archived'],
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
  assert.equal(badge.classList.contains('panel-attention'), false);
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

test('task modal keeps a scrollable body separate from its footer actions', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(html, /<div id="task-modal-body" class="task-modal-body">[\s\S]*<div class="modal-actions">/);
  assert.match(css, /#modal-task \.modal\s*\{[^}]*overflow:\s*hidden;/);
  assert.match(css, /\.task-modal-body\s*\{[^}]*flex:\s*1;[^}]*overflow-y:\s*auto;/);
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

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
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = value;
    this.children = [];
  }

  appendChild(child) {
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
    taskAutoResize = function() {};
  `);
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
  context.window.prompt = () => 'Docs Tasks';
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
  context.boardSaveCurrentView();

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
  assert.match(panel.innerHTML, /<option value="oldest" selected>Oldest<\/option>/);

  context.activeGroup = 'beta';
  context.renderBoard();
  assert.equal(runInContext(context, `_boardLaneSortMode('Backlog')`), 'manual');
  assert.match(panel.innerHTML, /<option value="manual" selected>Manual<\/option>/);

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
  assert.match(panel.innerHTML, /<option value="due" selected>Due Soonest<\/option>/);
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
  assert.match(panel.innerHTML, /<option value="compact" selected>Compact<\/option>/);
  assert.match(panel.innerHTML, /board-card-meta/);
  assert.match(panel.innerHTML, /board-card-agent/);

  context.activeGroup = 'beta';
  context.renderBoard();
  assert.match(panel.innerHTML, /board-density-normal/);
  assert.match(panel.innerHTML, /<option value="normal" selected>Normal<\/option>/);

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
  assert.match(panel.innerHTML, /<option value="detailed" selected>Detailed<\/option>/);
});

test('renderBoard shows a compact sticky lane header with counts, filter summary, and quick actions', () => {
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

  assert.match(panel.innerHTML, /board-lane-header/);
  assert.match(panel.innerHTML, /Backlog/);
  assert.match(panel.innerHTML, /1 root · 2 total/);
  assert.match(panel.innerHTML, /Search "deploy"/);
  assert.match(panel.innerHTML, /Labels bug/);
  assert.match(panel.innerHTML, /Actions feature\/review/);
  assert.match(panel.innerHTML, /Agents Alice Reviewer/);
  assert.match(panel.innerHTML, /\+ Task/);
  assert.match(panel.innerHTML, /Clear Filters/);
  assert.match(panel.innerHTML, /Save View/);
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

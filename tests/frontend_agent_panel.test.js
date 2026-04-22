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

function createHarness() {
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  const captureCalls = [];
  const restoreCalls = [];
  const intervalCalls = [];
  const clearIntervalCalls = [];
  const sendCalls = [];
  const sandbox = {
    console,
    Date,
    state: {
      agents: {},
      board_tasks: {},
      groups: { alpha: [] },
      weaver_settings: {},
      weaver_buffer_stats: {},
      weaver_sent_events: {},
      weaver_worklog: {},
      weaver_journal: {},
      weaver_session_maps: {},
      agent_digest_settings: {},
      digest_buffer_stats: {},
      digest_sent_events: {},
      panel_events: [],
      decisions: {},
      group_settings: {},
    },
    focusedItemId: '',
    document: {
      activeElement: null,
      getElementById(id) {
        return id === 'panel-agent' ? panel : null;
      },
    },
    _captureSurfaceState(root, opts) {
      captureCalls.push({ root, opts });
      const snapshot = { focus: null, scrolls: [] };
      if (opts && typeof opts.capture === 'function') opts.capture(snapshot, root);
      return snapshot;
    },
    _restoreSurfaceState(root, snapshot, opts) {
      restoreCalls.push({ root, snapshot, opts });
      if (opts && typeof opts.restore === 'function') opts.restore(root, snapshot);
    },
    _weaverStopEventsCountdownTimer() {},
    _currentGroup() { return 'alpha'; },
    setInterval(fn, ms) { intervalCalls.push({ fn, ms }); return intervalCalls.length; },
    clearInterval(id) { clearIntervalCalls.push(id); },
    send(message) { sendCalls.push(message); },
    window: { prompt() { return null; } },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/agent_panel.js');
  loadScript(context, 'static/js/agent_panel.js');
  return {
    context,
    panel,
    captureCalls,
    restoreCalls,
    intervalCalls,
    clearIntervalCalls,
    sendCalls,
  };
}

function setFocusedAgent(context, agent) {
  context.state.agents = {};
  context.focusedItemId = '';
  if (agent) {
    context.state.agents[agent.id] = agent;
    context.focusedItemId = agent.id;
  }
}

test('renderAgentPanel shows an empty state when no grid item is focused', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Select an agent from the grid to see its context\./);
  assert.equal(captureCalls.length, 1);
  assert.equal(restoreCalls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(captureCalls[0].opts.scrollSelectors)), ['.agent-panel-content']);
});

test('renderAgentPanel renders architect, engineer, worker, and terminal panels', () => {
  const { context, panel } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.match(panel.innerHTML, /Decisions/);
  assert.match(panel.innerHTML, /Hired engineers/);
  assert.match(panel.innerHTML, /Messages/);
  assert.match(panel.innerHTML, /Events/);

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Engineer: Builder · Group: alpha/);
  assert.match(panel.innerHTML, /Journal/);
  assert.match(panel.innerHTML, /Events/);
  assert.match(panel.innerHTML, /Worklog/);

  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.match(panel.innerHTML, /Per-worker event stream and task history\./);

  setFocusedAgent(context, {
    id: 'term-1',
    name: 'Shell Root',
    cell_type: 'terminal',
    current_branch: 'loom/feature-x',
    current_process: 'pytest',
    current_path: '/tmp/loom',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Terminal: Shell Root/);
  assert.match(panel.innerHTML, /Open the terminal drawer to interact with this session\./);
  assert.match(panel.innerHTML, /pytest/);
});

test('architect roster renders worker kind badges with worker-specific class', () => {
  const { context, panel } = createHarness();
  context._esc = function(value) { return String(value); };
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Worker Bee',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-1',
      cell_type: 'agent',
    },
  };
  context.state.groups.alpha = ['arch-1', 'eng-1', 'worker-1'];
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('hired_engineers');

  assert.match(panel.innerHTML, /class="engineer-row-kind engineer-row-kind-engineer">engineer<\/span>/);
  assert.match(panel.innerHTML, /class="engineer-row-kind engineer-row-kind-worker">worker<\/span>/);
});

test('agent panel roster marks architect-owned and user-owned hierarchy rows distinctly', () => {
  const { context, panel } = createHarness();
  context._esc = function(value) { return String(value); };
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-arch': {
      id: 'eng-arch',
      name: 'Architect Engineer',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-arch': {
      id: 'worker-arch',
      name: 'Architect Worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-arch',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
    'worker-user': {
      id: 'worker-user',
      name: 'User Worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-user',
      cell_type: 'agent',
    },
  };
  context.state.groups.alpha = ['arch-1', 'eng-arch', 'worker-arch', 'eng-user', 'worker-user'];
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('hired_engineers');

  assert.match(panel.innerHTML, /architect-roster-level-1 architect-owned-engineer-row/);
  assert.match(panel.innerHTML, /architect-roster-level-2 architect-owned-worker-row/);

  const userRosterHtml = vm.runInContext(`_agentPanelLegacyRenderEngineerRoster('alpha')`, context);
  assert.match(userRosterHtml, /engineer-row-virtual-parent/);
  assert.match(userRosterHtml, /engineer-roster-level-1 user-owned-engineer-row/);
  assert.match(userRosterHtml, /engineer-roster-level-2 engineer-owned-worker-row/);
  assert.doesNotMatch(userRosterHtml, /architect-owned-engineer-row|architect-owned-worker-row/);
});

test('agentPanelSelectTab remembers the last selected tab per kind', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.agentPanelSelectTab('worklog');

  assert.equal(vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer`, context), 'worklog');
  assert.match(panel.innerHTML, /data-agent-panel-tab="worklog"/);
  assert.match(panel.innerHTML, /id="agent-panel-tab-worklog" class="agent-panel-tab active"/);
});

test('_resolveFocusedAgent returns null when focusedItemId does not resolve', () => {
  const { context } = createHarness();
  context.focusedItemId = 'missing-agent';

  assert.equal(vm.runInContext(`_resolveFocusedAgent()`, context), null);
});

test('engineer panel renders journal, events, and worklog from the focused engineer group', () => {
  const { context, panel } = createHarness();
  context.state.group_settings = { alpha: { weaver_agent_id: 'weaver-1' } };
  context.state.agents['weaver-1'] = { id: 'weaver-1', name: 'Weaver', group: 'alpha' };
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  context.state.weaver_journal.alpha = [
    { id: 2, type: 'decision', entry: 'Approved the refactor', timestamp: 50 },
  ];
  context.state.digest_buffer_stats['eng-1'] = {
    agent_id: 'eng-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 60,
    queued_events: [
      { id: 3, kind: 'task_completed', message: 'Queued digest item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['eng-1'] = [
    { id: 2, kind: 'task_completed', message: 'Delivered digest item', timestamp: 35, delivered_at: 38 },
  ];
  context.state.weaver_worklog.alpha = [
    {
      id: 9,
      task_id: 'LOOM:9',
      task_title: 'Ship engineer panel',
      agent_id: 'eng-1',
      agent_name: 'Builder',
      started_at: 30,
    },
  ];
  context.state.board_tasks['LOOM:9'] = {
    id: 'LOOM:9',
    task: 'Ship engineer panel',
    lane: 'Review',
    status: 'In progress',
    group: 'alpha',
    agent_id: 'eng-1',
  };

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Approved the refactor/);
  assert.match(panel.innerHTML, /Group: alpha \(group-wide\)/);

  context.agentPanelSelectTab('events');
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn"/);
  assert.match(panel.innerHTML, /Queued digest item/);
  assert.match(panel.innerHTML, /Already sent to Builder/);
  assert.match(panel.innerHTML, /Delivered digest item/);

  context.agentPanelSelectTab('worklog');
  assert.match(panel.innerHTML, /Ship engineer panel/);
  assert.match(panel.innerHTML, /Review/);
});

test('worker panel filters per-cell events and task history to the focused worker', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.state.panel_events = [
    { id: 2, cell_id: 'worker-2', kind: 'task_completed', message: 'Ignore me', timestamp: 20 },
    { id: 3, cell_id: 'worker-1', kind: 'task_completed', message: 'Finished compile step', timestamp: 30, task_id: 'LOOM:3' },
  ];
  context.state.board_tasks = {
    'LOOM:2': { id: 'LOOM:2', task: 'Ignore task', agent_id: 'worker-2', lane: 'Done', status: 'done', created_at: 15 },
    'LOOM:3': { id: 'LOOM:3', task: 'Compile feature branch', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 25 },
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Finished compile step/);
  assert.doesNotMatch(panel.innerHTML, /Ignore me/);

  context.agentPanelSelectTab('worklog');
  assert.match(panel.innerHTML, /Compile feature branch/);
  assert.doesNotMatch(panel.innerHTML, /Ignore task/);
});

test('focused engineer events tab renders server-merged cell events', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);

  context.renderAgentPanel();

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), {
    cmd: 'get_cell_events',
    cell_id: 'eng-1',
    limit: 200,
  });
  assert.match(panel.innerHTML, /Loading cell events/);

  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'eng-1',
    events: [
      {
        id: 9,
        cell_id: 'eng-1',
        kind: 'task_dispatched',
        message: 'Persisted dispatch survived restart',
        timestamp: 20,
        source: 'panel_events',
      },
      {
        id: 'live:25',
        cell_id: 'eng-1',
        kind: 'tool_start',
        message: 'Live tool call',
        timestamp: 25,
        source: 'event_log',
      },
    ],
  });

  assert.match(panel.innerHTML, /Cell events/);
  assert.match(panel.innerHTML, /Persisted dispatch survived restart/);
  assert.match(panel.innerHTML, /Live tool call/);
});

test('architect panel filters decisions, hired engineers, and messages to the focused architect', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [{ action: 'architect_reply', message: 'Need a follow-up worker', timestamp: 60 }],
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-1',
    },
    'eng-2': {
      id: 'eng-2',
      name: 'Other Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-2',
    },
  };
  context.focusedItemId = 'arch-1';
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Adopt the new layout',
      status: 'accepted',
      updated_at: 20,
    },
    'decision-2': {
      id: 'decision-2',
      architect_id: 'arch-2',
      title: 'Do not show me',
      status: 'accepted',
      updated_at: 10,
    },
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Adopt the new layout/);
  assert.doesNotMatch(panel.innerHTML, /Do not show me/);

  context.agentPanelSelectTab('hired_engineers');
  assert.match(panel.innerHTML, /Builder/);
  assert.doesNotMatch(panel.innerHTML, /Other Engineer/);

  context.agentPanelSelectTab('messages');
  assert.match(panel.innerHTML, /Need a follow-up worker/);
  assert.match(panel.innerHTML, /Reply composer lands in a later task\./);
});

test('focused architect decision interactions rerender the agent panel instead of the legacy weaver surface', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.decisions = {
    d1: {
      id: 'd1',
      architect_id: 'arch-1',
      title: 'Keep the focused agent surface stable',
      status: 'proposed',
      updated_at: 40,
    },
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);

  context.weaverToggleDecision('d1');

  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.match(panel.innerHTML, /architect-decision-body/);
  assert.equal(vm.runInContext(`_weaverDecisionUiState('d1').expanded`, context), true);
});

test('focused engineer events tab starts the live countdown timer', () => {
  const { context, panel, intervalCalls } = createHarness();
  const countdownEl = { textContent: '' };
  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-events-countdown'
        && /agent-panel-events-countdown/.test(this.innerHTML || '')) {
      return countdownEl;
    }
    return null;
  };

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  context.state.digest_buffer_stats['eng-1'] = {
    agent_id: 'eng-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 45,
    queued_events: [
      { id: 7, kind: 'task_completed', message: 'Queued for digest', timestamp: 30 },
    ],
    manual_flush_requested: false,
  };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Send queued now/);
  assert.match(countdownEl.textContent, /Next eligible send in/);
  assert.equal(intervalCalls.length, 1);
  assert.equal(intervalCalls[0].ms, 1000);
});

test('focused engineer pause toggle sends per-agent digest pause and resume commands', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn"/);

  vm.runInContext(`agentPanelTogglePauseForAgent('eng-1')`, context);
  context.state.agent_digest_settings['eng-1'].paused = true;
  context.renderAgentPanel();
  vm.runInContext(`agentPanelTogglePauseForAgent('eng-1')`, context);

  const digestCalls = sendCalls.filter((call) => call.cmd && call.cmd.startsWith('digest_'));
  assert.deepEqual(JSON.parse(JSON.stringify(digestCalls)), [
    { cmd: 'digest_pause', agent_id: 'eng-1' },
    { cmd: 'digest_resume', agent_id: 'eng-1' },
  ]);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn paused"/);
});

test('focused architect Events tab renders digest data and pause control', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['arch-1'] = {
    agent_id: 'arch-1',
    paused: false,
    architect_digest: true,
  };
  context.state.digest_buffer_stats['arch-1'] = {
    agent_id: 'arch-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 300,
    queued_events: [
      { id: 11, kind: 'task_completed', message: 'Architect queued item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['arch-1'] = [
    { id: 10, kind: 'task_completed', message: 'Architect delivered item', timestamp: 35, delivered_at: 38 },
  ];
  vm.runInContext(`_agentPanelLastSelectedTabByKind.architect = 'events';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn"/);
  assert.match(panel.innerHTML, /Architect queued item/);
  assert.match(panel.innerHTML, /Already sent to Planner/);
  assert.match(panel.innerHTML, /Architect delivered item/);

  vm.runInContext(`agentPanelTogglePauseForAgent('arch-1')`, context);
  context.state.agent_digest_settings['arch-1'].paused = true;
  context.renderAgentPanel();
  vm.runInContext(`agentPanelTogglePauseForAgent('arch-1')`, context);

  const digestCalls = sendCalls.filter((call) => call.cmd && call.cmd.startsWith('digest_'));
  assert.deepEqual(JSON.parse(JSON.stringify(digestCalls)), [
    { cmd: 'digest_pause', agent_id: 'arch-1' },
    { cmd: 'digest_resume', agent_id: 'arch-1' },
  ]);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn paused"/);
});

test('worker worklog updates after task changes and preserves the current anchor across rerenders', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.agentPanelSelectTab('worklog');

  const oldContent = {
    scrollTop: 100,
    getBoundingClientRect() {
      return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
    },
    querySelectorAll(selector) {
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-weaver-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-LOOM:2' },
          getBoundingClientRect() {
            return { top: 20, bottom: 40, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-LOOM:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-LOOM:1' },
          getBoundingClientRect() {
            return { top: 60, bottom: 80, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-LOOM:1' : '';
          },
        },
      ];
    },
  };
  const newContent = {
    scrollTop: 0,
    getBoundingClientRect() {
      return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
    },
    querySelectorAll(selector) {
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-weaver-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-LOOM:3' },
          getBoundingClientRect() {
            return { top: 10, bottom: 30, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-LOOM:3' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-LOOM:2' },
          getBoundingClientRect() {
            return { top: 40, bottom: 60, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-LOOM:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-LOOM:1' },
          getBoundingClientRect() {
            return { top: 80, bottom: 100, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-LOOM:1' : '';
          },
        },
      ];
    },
  };
  let currentContent = oldContent;
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

  context.state.board_tasks = {
    'LOOM:2': { id: 'LOOM:2', task: 'Second task', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 20 },
    'LOOM:1': { id: 'LOOM:1', task: 'First task', agent_id: 'worker-1', lane: 'Todo', status: 'queued', created_at: 10 },
  };

  context.renderAgentPanel();
  assert.equal(newContent.scrollTop, 20);
  assert.match(panel.innerHTML, /Second task/);

  context.state.board_tasks['LOOM:3'] = {
    id: 'LOOM:3',
    task: 'Newest task',
    agent_id: 'worker-1',
    lane: 'Review',
    status: 'waiting',
    created_at: 30,
  };
  currentContent = oldContent;
  oldContent.scrollTop = 100;
  newContent.scrollTop = 0;
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Newest task/);
  assert.equal(newContent.scrollTop, 20);
});

test('_weaverReceiveSessionMap keeps the focused-agent panel rendered when agent panel support is loaded', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);

  context._weaverReceiveSessionMap({
    group: 'alpha',
    session_map: { group: 'alpha', streams: { items: [] } },
  });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
});

test('_weaverResetSessionMapMeta keeps the focused-agent panel rendered during reconnect resets', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  vm.runInContext(`
    _weaverSessionMapMetaByGroup.alpha = { loading: true, stale: false };
  `, context);

  context._weaverResetSessionMapMeta({ clearStale: false });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.equal(vm.runInContext(`_weaverSessionMapMetaByGroup.alpha.loading`, context), false);
});

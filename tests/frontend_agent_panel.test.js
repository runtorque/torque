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
      engineer_settings: {},
      engineer_buffer_stats: {},
      engineer_sent_events: {},
      engineer_worklog: {},
      engineer_journal: {},
      engineer_session_maps: {},
      agent_digest_settings: {},
      digest_buffer_stats: {},
      digest_sent_events: {},
      panel_events: [],
      decisions: {},
      group_settings: {},
      global_settings: {
        mcp_call_log_args_capture: 'metadata',
      },
      mcp_calls: {},
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
    _engineerStopEventsCountdownTimer() {},
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
  assert.deepEqual(JSON.parse(JSON.stringify(captureCalls[0].opts.scrollSelectors)), [
    '.agent-panel-content',
    '.agent-panel-message-list',
  ]);
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
  assert.match(panel.innerHTML, /Queued/);
  assert.match(panel.innerHTML, /Completed/);

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
    current_branch: 'torque/feature-x',
    current_process: 'pytest',
    current_path: '/tmp/torque',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Terminal: Shell Root/);
  assert.match(panel.innerHTML, /Open the terminal drawer to interact with this session\./);
  assert.match(panel.innerHTML, /pytest/);
});

test('agent panel folds MCP into Events subtabs for standalone and toolbelt modes', () => {
  const { context, panel } = createHarness();
  const expectedSpecs = {
    architect: ['decisions', 'journal', 'hired_engineers', 'messages', 'events'],
    engineer: ['journal', 'events', 'queued', 'worklog'],
    worker: ['events', 'messages', 'worklog'],
  };
  const agents = {
    architect: { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
    engineer: { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    worker: { id: 'worker-1', name: 'Worker Bee', kind: 'worker', group: 'alpha', cell_type: 'agent' },
  };
  const runtimes = [
    { mode: 'standalone', embedded_terminal: true },
    { mode: 'toolbelt', embedded_terminal: false },
  ];

  for (const runtime of runtimes) {
    context.state.runtime = runtime;
    for (const kind of Object.keys(expectedSpecs)) {
      assert.deepEqual(
        JSON.parse(JSON.stringify(vm.runInContext(
          `_agentPanelTabSpec('${kind}').map(function(tab) { return tab.key; })`,
          context
        ))),
        expectedSpecs[kind],
        kind + ' tab spec in ' + runtime.mode
      );
      context.state.agents = { [agents[kind].id]: agents[kind] };
      context.focusedItemId = agents[kind].id;
      vm.runInContext(`_agentPanelLastSelectedTabByKind.${kind} = 'events';`, context);
      context.renderAgentPanel();
      assert.doesNotMatch(panel.innerHTML, /id="agent-panel-tab-mcp"/,
        kind + ' must not expose a top-level MCP tab in ' + runtime.mode);
      assert.match(
        panel.innerHTML,
        /data-agent-panel-events-inner-tab="inbox"[\s\S]*data-agent-panel-events-inner-tab="lifecycle"[\s\S]*data-agent-panel-events-inner-tab="mcp"/,
        kind + ' Events subtabs must be inbox, lifecycle, mcp in ' + runtime.mode
      );
    }
  }

  const source = fs.readFileSync(path.join(repoRoot, 'static/js/agent_panel.js'), 'utf8');
  assert.equal(source.includes("activeTab === 'mcp'"), false);
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

  assert.match(panel.innerHTML, /engineers-roster-list agent-panel-hierarchy-list agent-panel-hierarchy-list-architect/);
  assert.match(panel.innerHTML, /agent-panel-hierarchy-branch agent-panel-hierarchy-branch-architect has-workers/);
  assert.match(panel.innerHTML, /agent-panel-hierarchy-children/);
  assert.match(panel.innerHTML, /architect-roster-level-1 architect-section-engineer-row/);
  assert.match(panel.innerHTML, /architect-roster-level-2 architect-section-worker-row/);

  const userRosterHtml = vm.runInContext(`_agentPanelLegacyRenderEngineerRoster('alpha')`, context);
  assert.match(userRosterHtml, /engineers-roster-list agent-panel-hierarchy-list agent-panel-hierarchy-list-user agent-panel-hierarchy-list-rooted/);
  assert.match(userRosterHtml, /engineer-row-virtual-parent/);
  assert.match(userRosterHtml, /agent-panel-hierarchy-branch agent-panel-hierarchy-branch-user has-workers/);
  assert.match(userRosterHtml, /engineer-roster-level-1 user-section-engineer-row/);
  assert.match(userRosterHtml, /engineer-roster-level-2 user-section-worker-row/);
  assert.doesNotMatch(userRosterHtml, /architect-section-engineer-row|architect-section-worker-row/);
});

test('worker panel header shows clickable upward architect and engineer parent chain', () => {
  const { context, panel, sendCalls, restoreCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Panelsmith',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Hierarchy worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-1',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'worker-1';

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /agent-panel-header-breadcrumb/);
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect"/);
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-worker current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer[\s\S]*ENGINEER[\s\S]*Panelsmith[\s\S]*WORKER[\s\S]*Hierarchy worker/);

  const restoreCountBeforeClick = restoreCalls.length;
  vm.runInContext(`agentPanelFocusHierarchyTarget("eng-1", "engineer", "alpha")`, context);

  assert.equal(context.focusedItemId, 'eng-1');
  assert.match(panel.innerHTML, /Engineer: Panelsmith · Group: alpha/);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'focus_agent',
    id: 'eng-1',
  });
  assert.ok(restoreCalls.length > restoreCountBeforeClick, 'breadcrumb focus rerender restores panel state');
});

test('engineer panel header shows architect or User parent chain with current self chip', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-hired': {
      id: 'eng-hired',
      name: 'Hired Engineer',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };

  context.focusedItemId = 'eng-hired';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer[\s\S]*ENGINEER[\s\S]*Hired Engineer/);

  context.focusedItemId = 'eng-user';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-user"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer current"/);
  assert.match(panel.innerHTML, /USER[\s\S]*User[\s\S]*ENGINEER[\s\S]*User Engineer/);
});

test('architect and user panel headers show only their current hierarchy chip', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };

  context.focusedItemId = 'arch-1';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer/);
  assert.doesNotMatch(panel.innerHTML, /agentPanelFocusHierarchyTarget/);

  vm.runInContext(`agentPanelFocusHierarchyTarget("", "user", "alpha")`, context);
  assert.equal(context.focusedItemId, 'principal:alpha:user');
  assert.match(panel.innerHTML, /User · Group: alpha/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-user current"/);
  assert.match(panel.innerHTML, /USER[\s\S]*User/);
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

test('_resolveFocusedAgent resolves architect principal nav ids to the architect agent', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:arch-a';
  context.window._navGridItemMeta = {
    'principal:alpha:arch-a': { type: 'principal', principalId: 'arch-a', group: 'alpha' },
  };

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'architect principal nav id should resolve to the architect agent');
  assert.equal(agent.id, 'arch-a');
});

test('_resolveFocusedAgent resolves architect principal nav ids by string fallback when nav meta is missing', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:arch-a';

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'string fallback should resolve architect principal nav id');
  assert.equal(agent.id, 'arch-a');
});

test('_resolveFocusedAgent resolves the user principal nav id to a virtual user principal', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:user';
  context.window._navGridItemMeta = {
    'principal:alpha:user': { type: 'principal', principalId: '', group: 'alpha' },
  };

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'user principal nav id should resolve to a virtual user panel target');
  assert.equal(agent.kind, 'user');
  assert.equal(agent.group, 'alpha');
  assert.equal(agent.id, 'principal:alpha:user');
});

test('engineer panel renders journal for the focused engineer only', () => {
  const { context, panel } = createHarness();
  context.state.group_settings = { alpha: { engineer_agent_id: 'engineer-1' } };
  context.state.agents['engineer-1'] = { id: 'engineer-1', name: 'Engineer', group: 'alpha' };
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  context.state.engineer_journal['eng-1'] = [
    { id: 2, type: 'decision', entry: 'Approved the refactor', timestamp: 50, author_cell_id: 'eng-1' },
  ];
  context.state.engineer_journal['eng-2'] = [
    { id: 3, type: 'decision', entry: 'Other engineer note', timestamp: 51, author_cell_id: 'eng-2' },
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
  context.state.engineer_worklog.alpha = [
    {
      id: 9,
      task_id: 'TORQUE:9',
      task_title: 'Ship engineer panel',
      agent_id: 'eng-1',
      agent_name: 'Builder',
      started_at: 30,
    },
  ];
  context.state.board_tasks['TORQUE:9'] = {
    id: 'TORQUE:9',
    task: 'Ship engineer panel',
    lane: 'Review',
    status: 'In progress',
    group: 'alpha',
    agent_id: 'eng-1',
  };
  context.state.board_tasks['TORQUE:10'] = {
    id: 'TORQUE:10',
    task: 'Queued backlog item',
    lane: 'Backlog',
    status: 'Waiting',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    position: 1,
  };
  context.state.board_tasks['TORQUE:11'] = {
    id: 'TORQUE:11',
    task: 'Queued in-progress item',
    lane: 'In Progress',
    status: 'Running',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    position: 2,
  };
  context.state.board_tasks['TORQUE:12'] = {
    id: 'TORQUE:12',
    task: 'Finished item should be completed only',
    lane: 'Done',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
  };
  context.state.board_tasks['TORQUE:13'] = {
    id: 'TORQUE:13',
    task: 'Other engineer item',
    lane: 'Backlog',
    group: 'alpha',
    assigned_engineer_id: 'eng-2',
  };
  context.state.board_tasks['TORQUE:14'] = {
    id: 'TORQUE:14',
    task: 'Engineer message followup',
    lane: 'To Do',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    labels: ['torque:engineer-message'],
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
  assert.doesNotMatch(panel.innerHTML, /Other engineer note/);
  assert.doesNotMatch(panel.innerHTML, /Group: alpha \(group-wide\)/);

  context.agentPanelSelectTab('events');
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn"/);
  assert.match(panel.innerHTML, /Queued digest item/);
  assert.match(panel.innerHTML, /Already digested to Builder/);
  assert.match(panel.innerHTML, /Delivered digest item/);

  context.agentPanelSelectTab('queued');
  assert.match(panel.innerHTML, /id="agent-panel-tab-queued" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Queued tasks/);
  assert.match(panel.innerHTML, /Queued backlog item/);
  assert.match(panel.innerHTML, /Queued in-progress item/);
  assert.doesNotMatch(panel.innerHTML, /Finished item should be completed only/);
  assert.doesNotMatch(panel.innerHTML, /Other engineer item/);
  assert.doesNotMatch(panel.innerHTML, /Engineer message followup/);

  context.agentPanelSelectTab('worklog');
  assert.match(panel.innerHTML, /Completed tasks/);
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
    { id: 3, cell_id: 'worker-1', kind: 'task_completed', message: 'Finished compile step', timestamp: 30, task_id: 'TORQUE:3' },
  ];
  context.state.board_tasks = {
    'TORQUE:2': { id: 'TORQUE:2', task: 'Ignore task', agent_id: 'worker-2', lane: 'Done', status: 'done', created_at: 15 },
    'TORQUE:3': { id: 'TORQUE:3', task: 'Compile feature branch', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 25 },
  };
  vm.runInContext(`_agentPanelEventsInnerTabByAgentId['worker-1'] = 'lifecycle';`, context);

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
  vm.runInContext(`_agentPanelEventsInnerTabByAgentId['eng-1'] = 'lifecycle';`, context);

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

test('architect Journal header keeps entry and decision counts grouped', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.architect_journals = {
    'arch-1': [
      { id: 'j-2', architect_id: 'arch-1', type: 'observation', entry: 'Second journal entry', timestamp: 20 },
      { id: 'j-1', architect_id: 'arch-1', type: 'observation', entry: 'First journal entry', timestamp: 10 },
    ],
  };
  context.state.decisions = {
    'decision-1': { id: 'decision-1', architect_id: 'arch-1', title: 'Adopt layout', status: 'accepted' },
  };
  context.focusedItemId = 'arch-1';
  vm.runInContext(`_agentPanelLastSelectedTabByKind.architect = 'journal';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /data-agent-panel-kind="architect" data-agent-panel-tab="journal"/);
  assert.match(
    panel.innerHTML,
    /<span class="agent-panel-worklog-title">Journal<\/span><span class="agent-panel-worklog-count" data-agent-panel-journal-count>2<\/span><span class="agent-panel-worklog-note"> · 1 decision<\/span>/
  );

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(
    css,
    /\.agent-panel-panel\[data-agent-panel-kind="architect"\]\[data-agent-panel-tab="journal"\] \.agent-panel-worklog-header\s*\{[^}]*justify-content:\s*flex-start;/
  );
  assert.match(css, /\.agent-panel-worklog-header\s*\{[^}]*justify-content:\s*space-between;/);
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
  assert.doesNotMatch(panel.innerHTML, /agent-panel-message-note/);
});

test('architect Messages tab renders full-height message cards instead of the compact MCP preview', () => {
  const { context, panel } = createHarness();
  const longBody = 'Line one with enough detail to be useful to the operator.\n'
    + 'Line two remains visible instead of being collapsed into a preview snippet.';
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        {
          id: 'msg-in',
          action: 'engineer_message_architect',
          message: longBody,
          timestamp: 1712345678,
          sender_kind: 'engineer',
          direction: 'received',
        },
        {
          id: 'msg-out',
          action: 'architect_reply',
          message: 'Thanks, keep going with the full plan.',
          timestamp: 1712345688,
          sender_kind: 'architect',
          direction: 'sent',
        },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-messages-tab/);
  assert.match(panel.innerHTML, /agent-panel-message-list/);
  assert.match(panel.innerHTML, /agent-panel-message-card/);
  assert.match(panel.innerHTML, /Engineer/);
  assert.match(panel.innerHTML, /In/);
  assert.match(panel.innerHTML, /Architect/);
  assert.match(panel.innerHTML, /Out/);
  assert.match(panel.innerHTML, /2024-04-05 19:34:38 UTC/);
  assert.ok(panel.innerHTML.includes(longBody));
  assert.doesNotMatch(panel.innerHTML, /class="mcp-log"/);
  assert.doesNotMatch(panel.innerHTML, /class="mcp-text"/);
});

test('architect Messages tab labels outgoing, incoming, and three-engineer messages', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent',
      mcp_messages: [
        { id: 'msg-c', action: 'engineer_message_architect', message: 'Need a decision.', timestamp: 300,
          sender_id: 'eng-c', sender_kind: 'engineer', peer_id: 'eng-c', peer_kind: 'engineer', direction: 'received' },
        { id: 'msg-b', action: 'architect_message', message: 'Please verify.', timestamp: 200,
          sender_id: 'arch-1', sender_kind: 'architect', peer_id: 'eng-b', peer_kind: 'engineer', direction: 'sent' },
        { id: 'msg-a', action: 'architect_message', message: 'Please build.', timestamp: 100,
          sender_id: 'arch-1', sender_kind: 'architect', peer_id: 'eng-a', peer_kind: 'engineer', direction: 'sent' },
      ] },
    'eng-a': { id: 'eng-a', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'eng-b': { id: 'eng-b', name: 'Verifier', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'eng-c': { id: 'eng-c', name: 'Debugger', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-message-attribution-in/);
  assert.match(panel.innerHTML, /agent-panel-message-attribution-out/);
  assert.match(panel.innerHTML, /From:<\/span><span class="agent-panel-message-attribution-name">Debugger/);
  assert.match(panel.innerHTML, /To:<\/span><span class="agent-panel-message-attribution-name">Verifier/);
  assert.match(panel.innerHTML, /To:<\/span><span class="agent-panel-message-attribution-name">Builder/);
});

test('architect Messages tab renders peer-message affordances and context refs', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        {
          id: 'peer-msg-1',
          action: 'architect_peer_message',
          message: 'Can you sanity-check the rollout?',
          timestamp: 1712345688,
          sender_id: 'arch-2',
          sender_kind: 'architect',
          recipient_id: 'arch-1',
          recipient_kind: 'architect',
          peer_id: 'arch-2',
          peer_kind: 'architect',
          direction: 'received',
          ack_required: true,
          context_task_ids: ['TORQUE:101'],
          context_engineer_ids: ['eng-1'],
          context_decision_ids: ['decision-1'],
          context_summary: 'API ownership is ambiguous.',
        },
      ],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.board_tasks = {
    'TORQUE:101': {
      id: 'TORQUE:101',
      task: 'Roll out peer messaging',
      group: 'alpha',
    },
  };
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Use direct peer messages',
      status: 'accepted',
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /Peer Architect · alpha/);
  assert.match(panel.innerHTML, /Ack required/);
  assert.match(panel.innerHTML, /API ownership is ambiguous\./);
  assert.match(panel.innerHTML, /TORQUE:101 · Roll out peer messaging/);
  assert.match(panel.innerHTML, /Builder/);
  assert.match(panel.innerHTML, /decision-1 · Use direct peer messages/);
});

test('architect Messages compose sends peer message with ack-required and context attachments', () => {
  const { context, panel, sendCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  assert.match(panel.innerHTML, /Send peer message/);

  context.agentPanelPeerComposeInput('arch-1', 'peer_id', 'arch-2');
  context.agentPanelPeerComposeInput('arch-1', 'message', 'Please review the API boundary.');
  context.agentPanelPeerComposeToggle('arch-1', true);
  context.agentPanelPeerComposeInput('arch-1', 'context_task_ids', 'TORQUE:1, TORQUE:2');
  context.agentPanelPeerComposeInput('arch-1', 'context_engineer_ids', 'eng-1');
  context.agentPanelPeerComposeInput('arch-1', 'context_decision_ids', 'decision-1');
  context.agentPanelPeerComposeInput('arch-1', 'context_summary', 'Ownership is ambiguous.');
  context.agentPanelPeerComposeSubmit({
    preventDefault() {},
    stopPropagation() {},
  }, 'arch-1');

  const peerMessageCalls = sendCalls.filter((call) => call.cmd === 'architect_peer_message');
  assert.equal(peerMessageCalls.length, 1);
  const sent = JSON.parse(JSON.stringify(peerMessageCalls[0]));
  assert.equal(sent.sender_architect_id, 'arch-1');
  assert.equal(sent.architect_id, 'arch-2');
  assert.equal(sent.message, 'Please review the API boundary.');
  assert.equal(sent.ack_required, true);
  assert.deepEqual(sent.context_task_ids, ['TORQUE:1', 'TORQUE:2']);
  assert.deepEqual(sent.context_engineer_ids, ['eng-1']);
  assert.deepEqual(sent.context_decision_ids, ['decision-1']);
  assert.equal(sent.context_summary, 'Ownership is ambiguous.');
  assert.ok(/^ui-peer-arch-1-/.test(sent.idempotency_key));
});

test('architect Messages compose draft survives peer-message rerenders', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  context.agentPanelPeerComposeInput('arch-1', 'peer_id', 'arch-2');
  context.agentPanelPeerComposeInput('arch-1', 'message', 'Draft survives deltas');
  context.agentPanelPeerComposeToggle('arch-1', true);

  context.state.agents['arch-1'].mcp_messages = [
    {
      id: 'peer-msg-new',
      action: 'architect_peer_message',
      message: 'Fresh peer update',
      timestamp: 200,
      peer_id: 'arch-2',
      direction: 'received',
    },
  ];
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Draft survives deltas/);
  assert.match(panel.innerHTML, /value="arch-2" selected/);
  assert.match(panel.innerHTML, /agent-panel-peer-ack-arch-1" type="checkbox" checked/);
  assert.match(panel.innerHTML, /Fresh peer update/);
});

test('worker Messages tab renders inline task thread entries', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Worker',
      kind: 'worker',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Parent task',
      group: 'alpha',
      agent_id: 'worker-1',
      messages_thread: [
        {
          timestamp: 1712345600,
          sender_agent_id: 'eng-1',
          recipient_agent_id: 'worker-1',
          content: 'Use the smaller repro before continuing.',
          reply_required: false,
        },
      ],
    },
  };
  context.focusedItemId = 'worker-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-messages-tab/);
  assert.match(panel.innerHTML, /Use the smaller repro before continuing\./);
  assert.match(panel.innerHTML, /Engineer/);
  assert.match(panel.innerHTML, /In/);
  assert.match(panel.innerHTML, /engineer message/);
  assert.match(panel.innerHTML, /Inline Engineer messages stored/);
});

test('architect Messages tab renders newest-first regardless of source array order', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-oldest', action: 'architect_message', message: 'OLDEST-BODY', timestamp: 100 },
        { id: 'm-middle', action: 'architect_message', message: 'MIDDLE-BODY', timestamp: 200 },
        { id: 'm-newest', action: 'architect_message', message: 'NEWEST-BODY', timestamp: 300 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  const html = panel.innerHTML;
  const iOldest = html.indexOf('OLDEST-BODY');
  const iMiddle = html.indexOf('MIDDLE-BODY');
  const iNewest = html.indexOf('NEWEST-BODY');
  assert.ok(iNewest >= 0 && iMiddle >= 0 && iOldest >= 0, 'all three messages should render');
  assert.ok(iNewest < iMiddle, 'newest message card must appear before middle card in DOM order');
  assert.ok(iMiddle < iOldest, 'middle message card must appear before oldest card in DOM order');
});

test('architect Messages tab keeps newest-first when the source array is already newest-first (server insert-at-0 shape)', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-newest', action: 'architect_message', message: 'NEWEST-BODY', timestamp: 300 },
        { id: 'm-middle', action: 'architect_message', message: 'MIDDLE-BODY', timestamp: 200 },
        { id: 'm-oldest', action: 'architect_message', message: 'OLDEST-BODY', timestamp: 100 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  const html = panel.innerHTML;
  const iOldest = html.indexOf('OLDEST-BODY');
  const iMiddle = html.indexOf('MIDDLE-BODY');
  const iNewest = html.indexOf('NEWEST-BODY');
  assert.ok(iNewest < iMiddle && iMiddle < iOldest,
    'newest-first ordering must be stable regardless of source array order');
});

test('architect Messages tab promotes a newly arrived message to the top on rerender without disturbing existing ordering', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-old1', action: 'architect_message', message: 'OLD-ONE-BODY', timestamp: 100 },
        { id: 'm-old2', action: 'architect_message', message: 'OLD-TWO-BODY', timestamp: 200 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  const firstHtml = panel.innerHTML;
  assert.ok(firstHtml.indexOf('OLD-TWO-BODY') < firstHtml.indexOf('OLD-ONE-BODY'),
    'baseline: newer OLD-TWO-BODY must render before older OLD-ONE-BODY');

  // Simulate a WS delta that prepends a new message (server inserts at index 0 with newer timestamp).
  context.state.agents['arch-1'].mcp_messages = [
    { id: 'm-brand-new', action: 'architect_message', message: 'BRAND-NEW-BODY', timestamp: 500 },
    { id: 'm-old1', action: 'architect_message', message: 'OLD-ONE-BODY', timestamp: 100 },
    { id: 'm-old2', action: 'architect_message', message: 'OLD-TWO-BODY', timestamp: 200 },
  ];
  context.renderAgentPanel();

  const secondHtml = panel.innerHTML;
  const iNew = secondHtml.indexOf('BRAND-NEW-BODY');
  const iOld2 = secondHtml.indexOf('OLD-TWO-BODY');
  const iOld1 = secondHtml.indexOf('OLD-ONE-BODY');
  assert.ok(iNew >= 0 && iOld2 >= 0 && iOld1 >= 0, 'all three messages render after rerender');
  assert.ok(iNew < iOld2 && iOld2 < iOld1,
    'brand-new message must take the top slot; pre-existing ordering must remain newest-first below it');
});

test('architect Messages tab rerender routes through capture/restore so scroll + focus survive delta updates', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-a', action: 'architect_message', message: 'first', timestamp: 100 },
        { id: 'm-b', action: 'architect_message', message: 'second', timestamp: 200 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  const captureBefore = captureCalls.length;
  const restoreBefore = restoreCalls.length;

  // Rerender via state mutation (simulate WS delta)
  context.state.agents['arch-1'].mcp_messages = [
    { id: 'm-c', action: 'architect_message', message: 'third', timestamp: 300 },
    { id: 'm-a', action: 'architect_message', message: 'first', timestamp: 100 },
    { id: 'm-b', action: 'architect_message', message: 'second', timestamp: 200 },
  ];
  context.renderAgentPanel();

  assert.ok(captureCalls.length > captureBefore, 'rerender must invoke _captureSurfaceState to snapshot scroll/focus');
  assert.ok(restoreCalls.length > restoreBefore, 'rerender must invoke _restoreSurfaceState to reapply snapshot');
  const latestCapture = captureCalls[captureCalls.length - 1];
  const scrollSelectors = JSON.parse(JSON.stringify(latestCapture.opts.scrollSelectors));
  assert.ok(scrollSelectors.indexOf('.agent-panel-message-list') >= 0,
    'capture must include .agent-panel-message-list so message-list scroll is anchored across rerenders');
});

test('focused architect decision interactions rerender the agent panel instead of the legacy engineer surface', () => {
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

  context.engineerToggleDecision('d1');

  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.match(panel.innerHTML, /architect-decision-body/);
  assert.equal(vm.runInContext(`_engineerDecisionUiState('d1').expanded`, context), true);
});

test('focused architect decision rows render parseable click handlers and expanded details', () => {
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
      rationale: 'Show the operator why this direction was chosen.',
      status: 'accepted',
      linked_task_ids: ['TORQUE:101'],
      linked_engineer_ids: ['eng-1'],
      supersedes: 'd0',
      updated_at: 40,
    },
    d2: {
      id: 'd2',
      architect_id: 'arch-1',
      title: 'Follow-up decision',
      status: 'revised',
      supersedes: 'd1',
      updated_at: 41,
    },
  };

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /role="button"/);
  assert.match(panel.innerHTML, /tabindex="0"/);
  assert.match(panel.innerHTML, /onkeydown="if\(event\.key===&#39;Enter&#39;\|\|event\.key===&#39; &#39;\)\{event\.preventDefault\(\);engineerToggleDecision\(&quot;d1&quot;\)\}"/);
  assert.match(panel.innerHTML, /onclick="engineerToggleDecision\(&quot;d1&quot;\)"/);
  assert.match(panel.innerHTML, /onclick="event\.stopPropagation\(\);engineerStartDecisionEdit\(&quot;d1&quot;\)"/);
  assert.doesNotMatch(panel.innerHTML, /onclick="engineerToggleDecision\("/);
  assert.doesNotMatch(panel.innerHTML, /engineerStartDecisionEdit\("/);

  context.engineerToggleDecision('d1');

  assert.match(panel.innerHTML, /Show the operator why this direction was chosen\./);
  assert.match(panel.innerHTML, /Status/);
  assert.match(panel.innerHTML, /accepted/);
  assert.match(panel.innerHTML, /Linked tasks/);
  assert.match(panel.innerHTML, /TORQUE:101/);
  assert.match(panel.innerHTML, /Linked engineers/);
  assert.match(panel.innerHTML, /eng-1/);
  assert.match(panel.innerHTML, /Supersedes/);
  assert.match(panel.innerHTML, /d0/);
  assert.match(panel.innerHTML, /Superseded by/);
  assert.match(panel.innerHTML, /d2/);
});

test('focused architect decision rows clamp titles and use compact secondary metadata/actions', () => {
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
      title: 'A very long accepted decision title that should ellipsize before it can push buttons',
      status: 'accepted',
      updated_at: 40,
    },
    d2: {
      id: 'd2',
      architect_id: 'arch-1',
      title: 'Short accepted decision',
      status: 'accepted',
      updated_at: 41,
    },
  };

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /architect-decision-toggle/);
  assert.match(panel.innerHTML, /architect-decision-title/);
  assert.match(panel.innerHTML, /architect-decision-summary-row/);
  assert.match(panel.innerHTML, /detail-section-card-actions/);
  assert.match(panel.innerHTML, /architect-decision-action-btn/);
  assert.match(panel.innerHTML, /Edit/);
  assert.match(panel.innerHTML, /Archive/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(
    css,
    /\.architect-decision-toggle\s*\{[^}]*flex:\s*1 1 auto;/
  );
  assert.match(
    css,
    /\.architect-decision-title\s*\{[^}]*-webkit-line-clamp:\s*2;/
  );
  assert.match(
    css,
    /\.architect-decision-summary-row\s*\{[^}]*display:\s*flex;/
  );
  assert.match(
    css,
    /\.architect-decision-action-btn\s*\{[^}]*font-size:\s*8px;/
  );
});

test('focused architect decision edit sends the existing update command', () => {
  const { context, sendCalls } = createHarness();
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
      title: 'Initial title',
      rationale: 'Initial rationale',
      status: 'proposed',
      updated_at: 40,
    },
  };

  context.renderAgentPanel();
  context.engineerStartDecisionEdit('d1');
  context.engineerDecisionDraftInput('d1', 'title', 'Updated title');
  context.engineerDecisionDraftInput('d1', 'rationale', 'Updated rationale');
  context.engineerDecisionDraftInput('d1', 'status', 'accepted');
  context.engineerSaveDecisionEdit('arch-1', 'd1');

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    {
      cmd: 'architect_decision_update',
      architect_id: 'arch-1',
      id: 'd1',
      title: 'Updated title',
      rationale: 'Updated rationale',
      status: 'accepted',
    },
  ]);
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

test('agent Events MCP subtab fetches calls, filters, and expands redacted details', () => {
  const { context, panel, sendCalls } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-mcp',
    name: 'Worker MCP',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.global_settings.mcp_call_log_args_capture = 'metadata';
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-mcp'] = 'mcp';
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.doesNotMatch(panel.innerHTML, /id="agent-panel-tab-mcp"/);
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-mcp-banner/);
  assert.ok(sendCalls.some((call) => call.cmd === 'mcp_calls'
    && call.cell_id === 'worker-mcp'
    && call.limit === 50
    && call.hook_event_name === 'PostToolUse'));

  context.agentPanelReceiveMcpCalls({
    type: 'mcp_calls',
    cell_id: 'worker-mcp',
    calls: [
      {
        cursor: 10,
        cell_id: 'worker-mcp',
        tool_name: 'mcp__torque__torque_progress',
        hook_event_name: 'PostToolUse',
        appended_at: now,
        success: true,
        duration_ms: 12,
        args: { redacted: true, arg_keys: ['message'], byte_size: 42 },
        args_redacted: true,
        result: { redacted: true, byte_size: 2 },
        result_redacted: true,
      },
    ],
  });
  assert.match(panel.innerHTML, /mcp__torque__torque_progress/);
  assert.match(panel.innerHTML, /redacted keys: message/);

  context.agentPanelToggleMcpCall('worker-mcp', '10');
  assert.match(panel.innerHTML, /Args redacted at ingest\./);
  assert.match(panel.innerHTML, /Result redacted at ingest\./);

  const beforeFilterCalls = sendCalls.length;
  context.agentPanelMcpFilterChange('tool', 'progress');
  assert.ok(sendCalls.length > beforeFilterCalls);
  assert.equal(sendCalls[sendCalls.length - 1].tool_name_pattern, '*progress*');
});

test('agent Events MCP subtab hides settings banner in full capture mode and renders full args', () => {
  const { context, panel } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-full',
    name: 'Worker Full',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.global_settings.mcp_call_log_args_capture = 'full';
  context.state.mcp_calls['worker-full'] = [
    {
      cursor: 1,
      cell_id: 'worker-full',
      tool_name: 'mcp__torque__torque_done',
      hook_event_name: 'PostToolUse',
      appended_at: now,
      success: true,
      args: { message: 'shipped' },
      args_redacted: false,
      result: { ok: true },
      result_redacted: false,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-full'] = 'mcp';
  `, context);

  context.renderAgentPanel();
  context.agentPanelToggleMcpCall('worker-full', '1');

  assert.doesNotMatch(panel.innerHTML, /Args redacted by default/);
  assert.match(panel.innerHTML, /&quot;message&quot;: &quot;shipped&quot;/);
  assert.doesNotMatch(panel.innerHTML, /Args redacted at ingest\./);
});

test('agent Events MCP subtab live update prepends without losing expanded row across rerender', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-live',
    name: 'Worker Live',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.mcp_calls['worker-live'] = [
    {
      cursor: 1,
      cell_id: 'worker-live',
      tool_name: 'mcp__torque__old',
      hook_event_name: 'PostToolUse',
      appended_at: now - 10,
      success: true,
      args: { old: true },
      args_redacted: false,
      result: {},
      result_redacted: false,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-live'] = 'mcp';
  `, context);
  context.renderAgentPanel();
  context.agentPanelToggleMcpCall('worker-live', '1');
  const captureBefore = captureCalls.length;
  const restoreBefore = restoreCalls.length;

  context.agentPanelReceiveMcpCallAppend({
    cursor: 2,
    cell_id: 'worker-live',
    tool_name: 'mcp__torque__pre',
    hook_event_name: 'PreToolUse',
    appended_at: now,
    success: true,
    args: { task_id: 'TORQUE:1' },
    args_redacted: false,
    result: null,
    result_redacted: true,
  });

  assert.doesNotMatch(panel.innerHTML, /mcp__torque__pre/);
  assert.equal(context.state.mcp_calls['worker-live'].some((call) => call.tool_name === 'mcp__torque__pre'), false);

  context.agentPanelReceiveMcpCallAppend({
    cursor: 3,
    cell_id: 'worker-live',
    tool_name: 'mcp__torque__new',
    hook_event_name: 'PostToolUse',
    appended_at: now,
    success: false,
    error: 'boom',
    args: { redacted: true, arg_keys: ['task_id'] },
    args_redacted: true,
    result: null,
    result_redacted: true,
  });

  const html = panel.innerHTML;
  assert.ok(html.indexOf('mcp__torque__new') < html.indexOf('mcp__torque__old'));
  assert.match(html, /&quot;old&quot;: true/);
  assert.ok(captureCalls.length > captureBefore);
  assert.ok(restoreCalls.length > restoreBefore);
  const latestCapture = captureCalls[captureCalls.length - 1];
  const scrollSelectors = JSON.parse(JSON.stringify(latestCapture.opts.scrollSelectors));
  assert.ok(scrollSelectors.indexOf('.agent-panel-mcp-list') >= 0,
    'Events.MCP subtab must register its list for rerender scroll preservation');
});

test('agentPanelSelectTab mcp redirects to Events MCP subtab and subtab state is per-agent', () => {
  const { context, panel } = createHarness();
  const engA = {
    id: 'eng-a',
    name: 'Engineer A',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  };
  const engB = {
    id: 'eng-b',
    name: 'Engineer B',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  };
  context.state.agents = { 'eng-a': engA, 'eng-b': engB };

  context.focusedItemId = 'eng-a';
  context.renderAgentPanel();
  context.agentPanelSelectTab('mcp');
  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);

  context.focusedItemId = 'eng-b';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-inbox" class="agent-panel-events-subtab active"/);
  assert.doesNotMatch(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);

  context.agentPanelSelectEventsInnerTab('lifecycle');
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-lifecycle" class="agent-panel-events-subtab active"/);

  context.focusedItemId = 'eng-a';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);
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
  assert.match(panel.innerHTML, /Already digested to Planner/);
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
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-engineer-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:2' },
          getBoundingClientRect() {
            return { top: 20, bottom: 40, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:1' },
          getBoundingClientRect() {
            return { top: 60, bottom: 80, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:1' : '';
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
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-engineer-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:3' },
          getBoundingClientRect() {
            return { top: 10, bottom: 30, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:3' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:2' },
          getBoundingClientRect() {
            return { top: 40, bottom: 60, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:1' },
          getBoundingClientRect() {
            return { top: 80, bottom: 100, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:1' : '';
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
    'TORQUE:2': { id: 'TORQUE:2', task: 'Second task', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 20 },
    'TORQUE:1': { id: 'TORQUE:1', task: 'First task', agent_id: 'worker-1', lane: 'Todo', status: 'queued', created_at: 10 },
  };

  context.renderAgentPanel();
  assert.equal(newContent.scrollTop, 20);
  assert.match(panel.innerHTML, /Second task/);

  context.state.board_tasks['TORQUE:3'] = {
    id: 'TORQUE:3',
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

test('_engineerReceiveSessionMap keeps the focused-agent panel rendered when agent panel support is loaded', () => {
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

  context._engineerReceiveSessionMap({
    group: 'alpha',
    session_map: { group: 'alpha', streams: { items: [] } },
  });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
});

function _eventsTabHarness(kind, agentId, agentName) {
  const harness = createHarness();
  setFocusedAgent(harness.context, {
    id: agentId,
    name: agentName,
    kind: kind,
    group: 'alpha',
    cell_type: 'agent',
  });
  harness.context.state.agent_digest_settings[agentId] = {
    agent_id: agentId,
    paused: false,
  };
  vm.runInContext(
    `_agentPanelLastSelectedTabByKind.${kind} = 'events';`,
    harness.context
  );
  vm.runInContext(`_agentPanelSectionPagers = {};`, harness.context);
  return harness;
}

test('engineer Events tab splits digest inbox from Cell events lifecycle', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-order', 'Builder');
  context.state.digest_buffer_stats['eng-order'] = {
    agent_id: 'eng-order',
    group: 'alpha',
    buffered_events: 1,
    queued_events: [
      { id: 1, kind: 'task_completed', message: 'Queued digest item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'eng-order',
    events: [
      { id: 100, cell_id: 'eng-order', kind: 'status_changed',
        message: 'Cell event row', timestamp: 20, source: 'panel_events' },
    ],
  });

  let html = panel.innerHTML;
  assert.match(html, /agent-panel-events-subtabs/);
  assert.match(html, /data-agent-panel-events-inner-tab="inbox"[^>]*aria-selected="true"/);
  assert.match(html, /Send queued now/);
  assert.match(html, /Queued for next digest/);
  assert.match(html, /Already digested to Builder/);
  assert.doesNotMatch(html, /Cell events/);

  vm.runInContext(`agentPanelSelectEventsInnerTab('lifecycle')`, context);
  html = panel.innerHTML;
  assert.match(html, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(html, /Cell events/);
  assert.match(html, /Cell event row/);
  assert.doesNotMatch(html, /Send queued now/);
  assert.doesNotMatch(html, /Queued for next digest/);
});

test('architect Events tab splits digest inbox from Cell events lifecycle', () => {
  const { context, panel } = _eventsTabHarness('architect', 'arch-order', 'Planner');
  context.state.digest_buffer_stats['arch-order'] = {
    agent_id: 'arch-order',
    group: 'alpha',
    buffered_events: 2,
    queued_events: [
      { id: 1, kind: 'task_completed', message: 'A queued item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['arch-order'] = [
    { id: 9, kind: 'task_completed', message: 'A sent item',
      timestamp: 30, delivered_at: 35 },
  ];
  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'arch-order',
    events: [
      { id: 200, cell_id: 'arch-order', kind: 'status_changed',
        message: 'Arch cell event', timestamp: 25, source: 'panel_events' },
    ],
  });

  let html = panel.innerHTML;
  assert.match(html, /agent-panel-events-subtabs/);
  assert.match(html, /data-agent-panel-events-inner-tab="inbox"[^>]*aria-selected="true"/);
  assert.match(html, /Send queued now/);
  assert.match(html, /Queued for next digest/);
  assert.match(html, /Already digested to Planner/);
  assert.doesNotMatch(html, /Cell events/);

  vm.runInContext(`agentPanelSelectEventsInnerTab('lifecycle')`, context);
  html = panel.innerHTML;
  assert.match(html, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(html, /Cell events/);
  assert.match(html, /Arch cell event/);
  assert.doesNotMatch(html, /Send queued now/);
});

test('worker Events tab exposes inbox, lifecycle, and MCP subtabs with lifecycle event list', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-events',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.panel_events = [
    {
      id: 1,
      cell_id: 'worker-events',
      kind: 'agent_started',
      message: 'Worker booted',
      timestamp: 10,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-events'] = 'lifecycle';
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /agent-panel-events-subtabs/);
  assert.match(panel.innerHTML, /data-agent-panel-events-inner-tab="inbox"[\s\S]*data-agent-panel-events-inner-tab="lifecycle"[\s\S]*data-agent-panel-events-inner-tab="mcp"/);
  assert.match(panel.innerHTML, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(panel.innerHTML, /Worker events/);
  assert.match(panel.innerHTML, /Worker booted/);
});

test('Already sent section caps at 20 events and exposes a Load more button', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-cap', 'Builder');
  const sent = [];
  for (let i = 0; i < 30; i++) {
    sent.push({
      id: 100 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-cap'] = sent;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/agent-panel-event-item-sent/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 sent items render initially');
  assert.match(html, /Already digested to Builder/);
  assert.match(html, /data-agent-panel-section="sent"/);
  assert.match(html, /Load 10 older events/);
  assert.match(html, /20 \/ 30/);
});

test('Queued section caps at 20 events and exposes a Load more button', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-qcap', 'Builder');
  const queued = [];
  for (let i = 0; i < 25; i++) {
    queued.push({
      id: 500 + i,
      kind: 'task_completed',
      message: 'Queued event ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.digest_buffer_stats['eng-qcap'] = {
    agent_id: 'eng-qcap',
    group: 'alpha',
    buffered_events: queued.length,
    queued_events: queued,
    manual_flush_requested: false,
  };

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/agent-panel-event-item-queued/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 queued items render initially');
  assert.match(html, /Queued for next digest/);
  assert.match(html, /data-agent-panel-section="queued"/);
  assert.match(html, /Load 5 older events/);
});

test('agentPanelLoadMoreSection expands the sent pager and preserves expansion across rerender', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-expand', 'Builder');
  const sent = [];
  for (let i = 0; i < 45; i++) {
    sent.push({
      id: 600 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-expand'] = sent;

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.match(html, /Load 20 older events/);
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    20
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'sent', 'eng-expand');`,
    context
  );

  html = panel.innerHTML;
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    40,
    'Load more extends the sent window to 40'
  );
  assert.match(html, /40 \/ 45/);

  // Simulate a WebSocket delta rerender — expansion must survive.
  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    40,
    'Expansion is preserved across rerender'
  );
});

test('sent section pager grows to anchor around newly delivered events so prior rows remain rendered', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-anchor', 'Builder');
  const sent = [];
  for (let i = 0; i < 25; i++) {
    sent.push({
      id: 800 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-anchor'] = sent.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(html.indexOf('Sent event 5') >= 0,
    'Initial render shows mid-range event');

  // New deliveries land at the top (newest-first). The pager should grow so
  // the previously visible "Sent event 5" row stays in the DOM for the shared
  // anchor-restore helper to lock onto.
  const fresh = [
    { id: 900, kind: 'task_completed', message: 'Fresh delivery 1',
      timestamp: 9000, delivered_at: 9001 },
    { id: 901, kind: 'task_completed', message: 'Fresh delivery 2',
      timestamp: 9002, delivered_at: 9003 },
  ];
  context.state.digest_sent_events['eng-anchor'] = fresh.concat(sent);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(html.indexOf('Fresh delivery 1') >= 0,
    'New top rows render');
  assert.ok(html.indexOf('Sent event 5') >= 0,
    'Previously visible row stays rendered after top-insert');
});

function _engineerPanelHarness(tab, agentId, agentName) {
  const harness = createHarness();
  harness.context.state.group_settings = {
    alpha: { engineer_agent_id: 'engineer-1' },
  };
  harness.context.state.agents['engineer-1'] = {
    id: 'engineer-1',
    name: 'Engineer',
    group: 'alpha',
  };
  setFocusedAgent(harness.context, {
    id: agentId,
    name: agentName,
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(
    `_agentPanelLastSelectedTabByKind.engineer = '${tab}';`,
    harness.context
  );
  vm.runInContext(`_agentPanelSectionPagers = {};`, harness.context);
  return harness;
}

test('engineer journal caps at 20 entries and exposes a Load more button', () => {
  const { context, panel } = _engineerPanelHarness('journal', 'eng-journal-cap', 'Builder');
  const entries = [];
  for (let i = 0; i < 25; i++) {
    entries.push({
      id: 1000 + i,
      type: 'observation',
      entry: 'Journal entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-cap'] = entries;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/data-engineer-anchor="journal-/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 journal entries render initially');
  assert.match(html, /Load 5 older entries/);
  assert.match(html, /data-agent-panel-section="journal"/);
  assert.match(html, /data-agent-panel-section-agent="eng-journal-cap"/);
});

test('engineer journal Load more appends 20 more entries without a re-fetch', () => {
  const { context, panel, sendCalls } = _engineerPanelHarness('journal', 'eng-journal-more', 'Builder');
  const entries = [];
  for (let i = 0; i < 45; i++) {
    entries.push({
      id: 1000 + i,
      type: 'observation',
      entry: 'Journal entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-more'] = entries;

  context.renderAgentPanel();
  const sendsBefore = sendCalls.length;
  assert.equal(
    (panel.innerHTML.match(/data-engineer-anchor="journal-/g) || []).length,
    20,
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'journal', 'eng-journal-more');`,
    context,
  );

  const html = panel.innerHTML;
  assert.equal(
    (html.match(/data-engineer-anchor="journal-/g) || []).length,
    40,
    'Load more extends the journal window to 40',
  );
  assert.match(html, /Load 5 older entries/);
  assert.equal(
    sendCalls.length,
    sendsBefore,
    'Load more must not trigger a server fetch — windowing is frontend-only',
  );
});

test('engineer journal pager grows to anchor around newly arriving entries', () => {
  const { context, panel } = _engineerPanelHarness('journal', 'eng-journal-anchor', 'Builder');
  const existing = [];
  for (let i = 0; i < 25; i++) {
    existing.push({
      id: 3000 + i,
      type: 'observation',
      entry: 'Existing entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-anchor'] = existing.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(
    html.indexOf('Existing entry 5') >= 0,
    'Initial render shows mid-range anchor-candidate entry',
  );

  // WS delta: two fresh journal entries arrive while the user is scrolled
  // into older history. The pager should grow so the row the anchor helper
  // was about to lock onto stays in the DOM.
  const fresh = [
    { id: 3100, type: 'decision', entry: 'Fresh decision', timestamp: 9000 },
    { id: 3101, type: 'plan', entry: 'Fresh plan', timestamp: 9001 },
  ];
  context.state.engineer_journal['eng-journal-anchor'] = fresh.concat(existing);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(html.indexOf('Fresh decision') >= 0, 'Newest entries render at the top');
  assert.ok(
    html.indexOf('Existing entry 5') >= 0,
    'Prior anchor row stays rendered after WS delta so anchor-restore can lock onto it',
  );
});

test('engineer worklog caps at 20 dispatched tasks and exposes a Load more button', () => {
  const { context, panel } = _engineerPanelHarness('worklog', 'eng-wkl-cap', 'Builder');
  const entries = [];
  for (let i = 0; i < 25; i++) {
    entries.push({
      id: 2000 + i,
      task_id: 'TORQUE:' + (2000 + i),
      task_title: 'Dispatched task ' + i,
      agent_id: 'eng-wkl-cap',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = entries;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/data-engineer-anchor="worklog-/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 worklog entries render initially');
  assert.match(html, /Load 5 older tasks/);
  assert.match(html, /data-agent-panel-section="worklog-all"/);
  assert.match(html, /data-agent-panel-section-agent="alpha"/);
});

test('engineer worklog Load more appends 20 more tasks without a re-fetch', () => {
  const { context, panel, sendCalls } = _engineerPanelHarness('worklog', 'eng-wkl-more', 'Builder');
  const entries = [];
  for (let i = 0; i < 45; i++) {
    entries.push({
      id: 2000 + i,
      task_id: 'TORQUE:' + (2000 + i),
      task_title: 'Dispatched task ' + i,
      agent_id: 'eng-wkl-more',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = entries;

  context.renderAgentPanel();
  const sendsBefore = sendCalls.length;
  assert.equal(
    (panel.innerHTML.match(/data-engineer-anchor="worklog-/g) || []).length,
    20,
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'worklog-all', 'alpha');`,
    context,
  );

  const html = panel.innerHTML;
  assert.equal(
    (html.match(/data-engineer-anchor="worklog-/g) || []).length,
    40,
  );
  assert.match(html, /Load 5 older tasks/);
  assert.equal(
    sendCalls.length,
    sendsBefore,
    'Worklog pager must not issue a server fetch on Load more',
  );
});

test('engineer worklog pager grows to anchor around newly dispatched tasks', () => {
  const { context, panel } = _engineerPanelHarness('worklog', 'eng-wkl-anchor', 'Builder');
  const existing = [];
  for (let i = 0; i < 25; i++) {
    existing.push({
      id: 4000 + i,
      task_id: 'TORQUE:' + (4000 + i),
      task_title: 'Existing task ' + i,
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = existing.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(
    html.indexOf('Existing task 5') >= 0,
    'Initial render shows mid-range anchor-candidate task',
  );

  // WS delta: two fresh dispatched tasks land at the top while the user is
  // scrolled into older history. The pager should grow so the previously
  // visible row stays rendered for anchor-restore.
  const fresh = [
    {
      id: 4100,
      task_id: 'TORQUE:4100',
      task_title: 'Fresh dispatched task 1',
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 9000,
    },
    {
      id: 4101,
      task_id: 'TORQUE:4101',
      task_title: 'Fresh dispatched task 2',
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 9001,
    },
  ];
  context.state.engineer_worklog.alpha = fresh.concat(existing);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(
    html.indexOf('Fresh dispatched task 1') >= 0,
    'Newest tasks render at the top',
  );
  assert.ok(
    html.indexOf('Existing task 5') >= 0,
    'Prior anchor row stays rendered after WS delta so anchor-restore can lock onto it',
  );
});

test('_engineerResetSessionMapMeta keeps the focused-agent panel rendered during reconnect resets', () => {
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
    _engineerSessionMapMetaByGroup.alpha = { loading: true, stale: false };
  `, context);

  context._engineerResetSessionMapMeta({ clearStale: false });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.equal(vm.runInContext(`_engineerSessionMapMetaByGroup.alpha.loading`, context), false);
});

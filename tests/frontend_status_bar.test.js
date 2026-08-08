const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const { appStylesheetSource } = require('./frontend_stylesheet_loader');

class FakeClassList {
  constructor() { this._set = new Set(); }
  add(...names) { names.forEach((name) => this._set.add(name)); }
  remove(...names) { names.forEach((name) => this._set.delete(name)); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const next = force === undefined ? !this._set.has(name) : !!force;
    if (next) this._set.add(name);
    else this._set.delete(name);
    return next;
  }
  values() { return Array.from(this._set); }
}

class FakeElement {
  constructor(id, ownerDocument) {
    this.id = id || '';
    this.ownerDocument = ownerDocument || null;
    this.children = [];
    this.parentNode = null;
    this.textContent = '';
    this.title = '';
    this.hidden = false;
    this.attributes = {};
    this.classList = new FakeClassList();
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((c) => c !== child);
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.body = this.register('body');
    this.hidden = false;
    this._listeners = new Map();
  }
  register(id) {
    const el = new FakeElement(id, this);
    this.elements.set(id, el);
    return el;
  }
  ensure(id) {
    if (!this.elements.has(id)) this.register(id);
    return this.elements.get(id);
  }
  getElementById(id) { return this.elements.get(id) || null; }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const listeners = this._listeners.get(type) || [];
    this._listeners.set(type, listeners.filter((listener) => listener !== fn));
  }
  dispatchEvent(event) {
    const type = typeof event === 'string' ? event : event.type;
    const listeners = (this._listeners.get(type) || []).slice();
    listeners.forEach((listener) => listener.call(this, event));
  }
  listenerCount(type) {
    return (this._listeners.get(type) || []).length;
  }
}

function loadStatusBar(context) {
  const filename = path.join(repoRoot, 'static/js/status_bar.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function createSandbox() {
  const document = new FakeDocument();
  [
    'statusbar-info',
    'statusbar-panel-buttons',
    'daemon-status-indicator',
    'taskbar-conn-dot',
    'taskbar-daemon-label',
    'supervisor-status-indicator',
    'taskbar-supervisor-dot',
    'taskbar-supervisor-label',
    'statusbar-claude-usage',
    'statusbar-codex-usage',
    'statusbar-deploy',
    'statusbar-metrics',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ].forEach((id) => document.ensure(id));
  const panelbar = document.register('panelbar');
  const statusInfo = document.getElementById('statusbar-info');
  const daemon = document.getElementById('daemon-status-indicator');
  daemon.appendChild(document.getElementById('taskbar-conn-dot'));
  daemon.appendChild(document.getElementById('taskbar-daemon-label'));
  statusInfo.appendChild(daemon);
  const supervisor = document.getElementById('supervisor-status-indicator');
  supervisor.appendChild(document.getElementById('taskbar-supervisor-dot'));
  supervisor.appendChild(document.getElementById('taskbar-supervisor-label'));
  statusInfo.appendChild(supervisor);
  [
    'statusbar-claude-usage',
    'statusbar-codex-usage',
    'statusbar-deploy',
    'statusbar-metrics',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ].forEach((id) => statusInfo.appendChild(document.getElementById(id)));
  const panelButtons = document.getElementById('statusbar-panel-buttons');
  const board = document.register('panel-board-button');
  const chat = document.register('panel-chat-button');
  panelbar.appendChild(panelButtons);
  panelButtons.appendChild(board);
  panelButtons.appendChild(chat);
  const spacer = document.register('taskbar-spacer');
  panelbar.appendChild(spacer);
  panelbar.appendChild(statusInfo);
  document.body.appendChild(panelbar);

  const sendCalls = [];
  const timers = [];
  const sandbox = {
    console,
    document,
    state: {
      active_group: 'Torque',
      groups: { Torque: [], Other: [] },
      agents: {},
      board_tasks: {},
      global_settings: {},
    },
    Date,
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay, active: true });
      return id;
    },
    clearTimeout(id) {
      const timer = timers.find((entry) => entry.id === id);
      if (timer) timer.active = false;
      timers.push({ cleared: id });
    },
    send(message) { sendCalls.push(message); },
    togglePanel(app) { sendCalls.push({ togglePanel: app }); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document, sendCalls, timers, statusInfo, panelbar, panelButtons, spacer, board, chat };
}

function jsonValue(context, expression) {
  return JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));
}

function liveTimers(timers, delay) {
  return timers.filter((timer) => timer.active && timer.delay === delay);
}

function runTimer(timer) {
  timer.active = false;
  timer.fn();
}

test('status chips live right-aligned inside the bottom panelbar without a right rail', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();
  const panelbarStart = html.indexOf('<div id="panelbar">');
  const panelbarEnd = html.indexOf('<!-- Context menu', panelbarStart);
  assert.ok(panelbarStart >= 0 && panelbarEnd > panelbarStart, '#panelbar markup exists');
  const panelbarHtml = html.slice(panelbarStart, panelbarEnd);

  assert.equal(html.includes('<div id="taskbar">'), false);
  assert.ok(
    panelbarHtml.indexOf('id="statusbar-panel-buttons"') < panelbarHtml.indexOf('class="taskbar-spacer"'),
    'panel buttons stay on the left of the bottom bar',
  );
  assert.ok(
    panelbarHtml.indexOf('class="taskbar-spacer"') < panelbarHtml.indexOf('id="statusbar-info"'),
    'spacer pushes status chips to the right edge',
  );
  assert.match(panelbarHtml, /id="statusbar-info"[\s\S]*id="statusbar-claude-usage"[\s\S]*id="statusbar-attention"/);

  assert.doesNotMatch(css, /--statusbar-rail-width/);
  assert.match(
    css,
    /body\.runtime-embedded #workspace-shell\s*\{[^}]*grid-template-columns:\s*max\(var\(--standalone-sidebar-width\),\s*calc\(var\(--standalone-main-stack-min-width\)\s*\+\s*8px\s*\+\s*var\(--standalone-right-rail-width,\s*var\(--standalone-right-rail-min-width\)\)\)\)\s+8px\s+minmax\(0,\s*1fr\);/s,
  );
  assert.match(css, /#panelbar\s*\{[^}]*flex:\s*0 0 24px;[^}]*height:\s*24px;/s);
  assert.match(css, /body\.runtime-embedded #panelbar\s*\{[^}]*flex-basis:\s*24px;[^}]*height:\s*24px;/s);
  assert.match(css, /\.statusbar-info\s*\{[^}]*flex-direction:\s*row;[^}]*justify-content:\s*flex-end;[^}]*margin-left:\s*auto;/s);
  assert.match(css, /\.taskbar-spacer\s*\{[^}]*flex:\s*1 1 auto;/s);
  assert.match(panelbarHtml, /id="daemon-status-indicator"[\s\S]*hidden/);
  assert.match(panelbarHtml, /id="supervisor-status-indicator"[\s\S]*id="taskbar-supervisor-dot"[\s\S]*hidden/);
  assert.match(panelbarHtml, /id="statusbar-metrics"[\s\S]*hidden/);
  assert.match(panelbarHtml, /id="statusbar-tasks"[\s\S]*onclick="statusBarOpenTasks\(\)"/);
  assert.match(panelbarHtml, /id="statusbar-attention"[\s\S]*onclick="statusBarOpenAttention\(\)"/);
});

test('status bar visibility defaults are lean and each toggle gates its chip', () => {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  sandbox.state.agents = {
    claude1: {
      id: 'claude1',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'claude-code',
      provider_usage: {
        five_hour: { available: true, used_percentage: 10 },
      },
    },
    codex1: {
      id: 'codex1',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'codex',
      provider_usage: {
        five_hour: { available: true, used_percentage: 20 },
      },
    },
    a1: { id: 'a1', group: 'Torque', cell_type: 'agent', status: 'running' },
  };
  sandbox.state.board_tasks = {
    task1: { id: 'task1', group: 'Torque', lane: 'In Progress', agent_id: 'a1' },
    task2: { id: 'task2', group: 'Torque', lane: 'To Do', labels: ['torque:human'] },
  };
  vm.runInContext(`statusBarReceiveDeployState({
    group: 'Torque',
    pending_deploy: { count: 1, torque_task_ids: ['TORQUE:1'] },
  });`, context);

  assert.equal(document.getElementById('daemon-status-indicator').hidden, true);
  assert.equal(document.getElementById('supervisor-status-indicator').hidden, true);
  assert.equal(document.getElementById('statusbar-claude-usage').hidden, true);
  assert.equal(document.getElementById('statusbar-codex-usage').hidden, true);
  assert.equal(document.getElementById('statusbar-deploy').hidden, false);
  assert.equal(document.getElementById('statusbar-metrics').hidden, true);
  assert.equal(document.getElementById('statusbar-workload').hidden, true);
  assert.equal(document.getElementById('statusbar-tasks').hidden, false);
  assert.equal(document.getElementById('statusbar-attention').hidden, false);

  sandbox.state.global_settings.status_bar_visibility = {
    daemon_status: true,
    claude_usage: true,
    codex_usage: true,
    deploy: false,
    health: true,
    workload: true,
    tasks: false,
    attention: false,
  };
  vm.runInContext('refreshStatusBar();', context);

  assert.equal(document.getElementById('daemon-status-indicator').hidden, false);
  assert.equal(document.getElementById('supervisor-status-indicator').hidden, false);
  assert.equal(document.getElementById('statusbar-claude-usage').hidden, false);
  assert.equal(document.getElementById('statusbar-codex-usage').hidden, false);
  assert.equal(document.getElementById('statusbar-deploy').hidden, true);
  assert.equal(document.getElementById('statusbar-metrics').hidden, false);
  assert.equal(document.getElementById('statusbar-workload').hidden, false);
  assert.equal(document.getElementById('statusbar-tasks').hidden, true);
  assert.equal(document.getElementById('statusbar-attention').hidden, true);
});


test('supervisor status indicator maps health states to neutral/warn/danger styles', () => {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  sandbox.state.global_settings.status_bar_visibility = { daemon_status: true };

  const cases = [
    ['up', 'normal', 'green', 'Supervisor'],
    ['degraded', 'warn', 'amber', 'Supervisor degraded'],
    ['restarting', 'warn', 'amber', 'Supervisor restarting'],
    ['down', 'danger', 'red', 'Supervisor down'],
    ['unavailable', 'muted', 'grey', 'Supervisor unavailable'],
    ['na_profile', 'muted', 'grey', 'Supervisor n/a'],
  ];
  for (const [stateName, level, dotColor, labelText] of cases) {
    sandbox.state.runtime = {
      supervisor: {
        state: stateName,
        supervisor_pid: 4321,
        uptime: 3661,
        connected: stateName === 'up',
        last_op_latency_ms: 7.4,
        reconnect_count: 2,
        session_count: 3,
      },
    };
    vm.runInContext('refreshStatusBar();', context);
    const root = document.getElementById('supervisor-status-indicator');
    const label = document.getElementById('taskbar-supervisor-label');
    const dot = document.getElementById('taskbar-supervisor-dot');
    assert.equal(root.hidden, false);
    assert.equal(root.getAttribute('data-supervisor-status'), stateName);
    assert.equal(root.getAttribute('data-statusbar-level'), level);
    assert.equal(label.textContent, labelText);
    assert.equal(dot.classList.contains(`relay-status-dot--${dotColor}`), true);
    assert.match(root.title, /PID: 4321/);
    assert.match(root.title, /Latency: 7\.4ms/);
    assert.match(root.title, /Sessions: 3/);
  }
});

test('status bar tasks and attention chips navigate through existing panel toggles', () => {
  const { sandbox, sendCalls } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  vm.runInContext('statusBarOpenTasks(); statusBarOpenAttention();', context);

  assert.deepEqual(sendCalls.filter((call) => call.togglePanel).map((call) => call.togglePanel), [
    'board',
    'events',
  ]);
});

test('Claude usage view is unknown when no Claude agent has available provider_usage', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  assert.deepEqual(jsonValue(context, `_statusBarClaudeUsageView()`), {
    state: 'unknown',
    label: 'Claude —',
    level: 'unknown',
    title: 'Claude 5h and weekly usage limits are unavailable until a Claude agent reports available provider_usage.',
    nextResetAt: 0,
  });

  sandbox.state.agents = {
    claude1: {
      id: 'claude1',
      name: 'Claude unavailable',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'claude-code',
      provider_usage: {
        five_hour: { available: false, used_percentage: null, resets_at: null },
        seven_day: { available: false, used_percentage: null, resets_at: null },
      },
    },
  };
  vm.runInContext('refreshStatusBar();', context);
  assert.equal(sandbox.document.getElementById('statusbar-claude-usage').textContent, 'Claude —');
  assert.equal(
    sandbox.document.getElementById('statusbar-claude-usage').classList.contains('statusbar-segment--unknown'),
    true,
  );
});

test('Claude usage view reads per-agent provider_usage in the shipped TORQUE:700 shape', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  const resetsAt = new Date(Date.now() + 90 * 60 * 1000).toISOString();
  sandbox.state.agents = {
    claude1: {
      id: 'claude1',
      name: 'Claude Worker',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'claude-code',
      last_heartbeat_at: 10,
      provider_usage: {
        five_hour: { available: true, used_percentage: 72, resets_at: resetsAt },
        seven_day: { available: true, used_percentage: 41, resets_at: new Date(Date.now() + 86400 * 1000).toISOString() },
      },
    },
  };

  const view = jsonValue(context, `_statusBarClaudeUsageView()`);
  assert.equal(view.label, 'Claude 5h 72% · 7d 41%');
  assert.equal(view.level, 'warn');
  assert.match(view.title, /5h: used 72%/);
  assert.match(view.title, /remaining 28%/);
  assert.match(view.title, /7d: used 41%/);
  assert.match(view.title, /Source agent: Claude Worker/);

  vm.runInContext(`refreshStatusBar();`, context);
  assert.equal(sandbox.document.getElementById('statusbar-claude-usage').textContent, 'Claude 5h 72% · 7d 41%');
  assert.equal(
    sandbox.document.getElementById('statusbar-claude-usage').classList.contains('statusbar-segment--warning'),
    true,
  );
});

test('Codex usage view reads codex provider_usage and paints its own chip', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  const resetsAt = new Date(Date.now() + 90 * 60 * 1000).toISOString();
  sandbox.state.agents = {
    codex1: {
      id: 'codex1',
      name: 'Codex Worker',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'codex',
      last_heartbeat_at: 10,
      provider_usage: {
        five_hour: { available: true, used_percentage: 64, resets_at: resetsAt },
        seven_day: { available: true, used_percentage: 34, resets_at: new Date(Date.now() + 86400 * 1000).toISOString() },
      },
    },
  };

  const view = jsonValue(context, `_statusBarCodexUsageView()`);
  assert.equal(view.label, 'Codex 5h 64% · 7d 34%');
  assert.equal(view.level, 'normal');
  assert.match(view.title, /Codex account usage limits/);
  assert.match(view.title, /Source agent: Codex Worker/);

  vm.runInContext(`refreshStatusBar();`, context);
  assert.equal(sandbox.document.getElementById('statusbar-codex-usage').textContent, 'Codex 5h 64% · 7d 34%');
  assert.equal(
    sandbox.document.getElementById('statusbar-codex-usage').classList.contains('statusbar-segment--normal'),
    true,
  );
  assert.equal(sandbox.document.getElementById('statusbar-claude-usage').textContent, 'Claude —');
});

test('Claude usage view chooses one available Claude agent and does not sum account quotas', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  const resetsAt = new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString();
  sandbox.state.agents = {
    claude1: {
      id: 'claude1',
      name: 'Claude first',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'claude-code',
      last_heartbeat_at: 10,
      provider_usage: {
        five_hour: { available: true, used_percentage: 40, resets_at: resetsAt },
        seven_day: { available: true, used_percentage: 30, resets_at: resetsAt },
      },
    },
    claude2: {
      id: 'claude2',
      name: 'Claude freshest',
      group: 'Torque',
      cell_type: 'agent',
      agent_type: 'claude-code',
      last_heartbeat_at: 20,
      provider_usage: {
        five_hour: { available: true, used_percentage: 40, resets_at: resetsAt },
        seven_day: { available: true, used_percentage: 30, resets_at: resetsAt },
      },
    },
  };

  const view = jsonValue(context, `_statusBarClaudeUsageView()`);
  assert.equal(view.label, 'Claude 5h 40% · 7d 30%');
  assert.doesNotMatch(view.label, /80%/);
  assert.doesNotMatch(view.label, /60%/);
  assert.match(view.title, /Source agent: Claude freshest/);
  assert.match(view.title, /not summed/);
});

test('status bar count helpers scope agents, active tasks, and attention to the active group', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  sandbox.state.agents = {
    a1: { id: 'a1', group: 'Torque', cell_type: 'agent', status: 'running' },
    a2: { id: 'a2', group: 'Torque', cell_type: 'agent', status: 'idle', needs_attention: true },
    a3: { id: 'a3', group: 'Torque', cell_type: 'agent', status: 'error' },
    t1: { id: 't1', group: 'Torque', cell_type: 'terminal', status: 'running' },
    b1: { id: 'b1', group: 'Other', cell_type: 'agent', status: 'running' },
  };
  sandbox.state.board_tasks = {
    task1: { id: 'task1', group: 'Torque', lane: 'In Progress', agent_id: 'a1' },
    task2: { id: 'task2', group: 'Torque', lane: 'To Do', labels: ['torque:human'] },
    task3: { id: 'task3', group: 'Torque', lane: 'Done', agent_id: 'a2' },
    task4: { id: 'task4', group: 'Other', lane: 'In Progress', agent_id: 'b1' },
  };

  const agents = jsonValue(context, `_statusBarAgentCounts('Torque')`);
  assert.equal(agents.total, 3);
  assert.equal(agents.running, 1);
  assert.equal(agents.idle, 1);
  assert.equal(agents.error, 1);
  assert.equal(jsonValue(context, `_statusBarActiveTaskCount('Torque').count`), 1);
  assert.equal(jsonValue(context, `_statusBarAttentionCount('Torque').count`), 2);
});

test('deploy view hides zero-pending state and highlights pending deploys', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  assert.deepEqual(jsonValue(context, `_statusBarDeployView({ pending_deploy: { count: 0, torque_task_ids: [] } })`), {
    visible: false,
    label: 'Deploy 0',
    level: 'muted',
    title: 'No merged-but-not-deployed Torque tasks for this daemon boot.',
  });

  const view = jsonValue(context, `_statusBarDeployView({ pending_deploy: { count: 2, torque_task_ids: ['TORQUE:1', 'TORQUE:2'] }, daemon_uptime_seconds: 3661 })`);
  assert.equal(view.visible, true);
  assert.equal(view.label, 'Deploy +2');
  assert.equal(view.level, 'warn');
  assert.match(view.title, /TORQUE:1, TORQUE:2/);
});

test('refreshStatusBar updates static nodes without wiping panel-button nodes or focus', () => {
  const { sandbox, document, statusInfo, panelbar, panelButtons, board, chat } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  board.focus();
  sandbox.state.agents = {
    a1: { id: 'a1', group: 'Torque', cell_type: 'agent', status: 'running' },
  };
  // Hidden chips now skip their full agent/task scans (and their label
  // writes) entirely; enable the workload chip so the text assertion
  // below exercises the visible path.
  sandbox.state.global_settings = {
    status_bar_visibility: { workload: true },
  };

  vm.runInContext('refreshStatusBar();', context);
  const beforeStatusChildren = statusInfo.children.slice();
  const beforeChildren = panelButtons.children.slice();
  assert.equal(document.activeElement, board);
  assert.deepEqual(beforeStatusChildren.map((child) => child.id), [
    'daemon-status-indicator',
    'supervisor-status-indicator',
    'statusbar-claude-usage',
    'statusbar-codex-usage',
    'statusbar-deploy',
    'statusbar-metrics',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ]);
  assert.deepEqual(beforeChildren, [board, chat]);

  sandbox.state.agents.a2 = { id: 'a2', group: 'Torque', cell_type: 'agent', status: 'idle' };
  vm.runInContext('refreshStatusBar();', context);

  assert.equal(document.getElementById('statusbar-info'), statusInfo);
  assert.equal(statusInfo.parentNode, panelbar);
  assert.deepEqual(statusInfo.children, beforeStatusChildren);
  assert.equal(document.getElementById('statusbar-panel-buttons'), panelButtons);
  assert.equal(panelButtons.parentNode, panelbar);
  assert.deepEqual(panelButtons.children, beforeChildren);
  assert.equal(document.activeElement, board);
  assert.match(document.getElementById('statusbar-workload').textContent, /Agents 1 run \/ 1 idle/);
});

test('statusBarRequestDeployState polls explicit get_deploy_state per active group', () => {
  const { sandbox, sendCalls } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  const sent = vm.runInContext('statusBarRequestDeployState({ force: true })', context);
  assert.equal(sent, true);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), { cmd: 'get_deploy_state', group: 'Torque' });
});

test('deploy-state interval polls while visible via statusBarRequestDeployState', () => {
  const { sandbox, sendCalls, timers } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  let deployTimers = liveTimers(timers, 90000);
  assert.equal(deployTimers.length, 1);
  assert.equal(jsonValue(context, '_statusBarDeployPollTimer > 0'), true);

  runTimer(deployTimers[0]);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), { cmd: 'get_deploy_state', group: 'Torque' });
  deployTimers = liveTimers(timers, 90000);
  assert.equal(deployTimers.length, 1);
});

test('deploy-state interval pauses while document is hidden', () => {
  const { sandbox, document, sendCalls, timers } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  const initialTimer = liveTimers(timers, 90000)[0];

  document.hidden = true;
  document.dispatchEvent({ type: 'visibilitychange' });

  assert.equal(liveTimers(timers, 90000).length, 0);
  assert.equal(jsonValue(context, '_statusBarDeployPollTimer'), 0);

  runTimer(initialTimer);
  assert.equal(sendCalls.length, 0);
  assert.equal(liveTimers(timers, 90000).length, 0);
});

test('deploy-state interval resumes with immediate refresh when document becomes visible', () => {
  const { sandbox, document, sendCalls, timers } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  document.hidden = true;
  document.dispatchEvent({ type: 'visibilitychange' });
  assert.equal(liveTimers(timers, 90000).length, 0);

  document.hidden = false;
  document.dispatchEvent({ type: 'visibilitychange' });

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), { cmd: 'get_deploy_state', group: 'Torque' });
  assert.equal(liveTimers(timers, 90000).length, 1);
});

test('deploy-state polling start is idempotent and teardown clears the one timer', () => {
  const { sandbox, document, timers } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);

  assert.equal(liveTimers(timers, 90000).length, 1);
  assert.equal(document.listenerCount('visibilitychange'), 1);

  vm.runInContext('statusBarStartDeployPolling(); statusBarStartDeployPolling();', context);

  assert.equal(liveTimers(timers, 90000).length, 1);
  assert.equal(document.listenerCount('visibilitychange'), 1);

  vm.runInContext('statusBarStopDeployPolling();', context);

  assert.equal(liveTimers(timers, 90000).length, 0);
});

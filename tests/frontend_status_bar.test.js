const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

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
    'statusbar-claude-usage',
    'statusbar-deploy',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ].forEach((id) => document.ensure(id));
  const panelButtons = document.getElementById('statusbar-panel-buttons');
  const board = document.register('panel-board-button');
  const chat = document.register('panel-chat-button');
  panelButtons.appendChild(board);
  panelButtons.appendChild(chat);

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
    },
    Date,
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout(id) { timers.push({ cleared: id }); },
    send(message) { sendCalls.push(message); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document, sendCalls, timers, panelButtons, board, chat };
}

function jsonValue(context, expression) {
  return JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));
}

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
    sandbox.document.getElementById('statusbar-claude-usage').classList.contains('statusbar-chip--unknown'),
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
    sandbox.document.getElementById('statusbar-claude-usage').classList.contains('statusbar-chip--warn'),
    true,
  );
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
  const { sandbox, document, panelButtons, board, chat } = createSandbox();
  const context = vm.createContext(sandbox);
  loadStatusBar(context);
  board.focus();
  sandbox.state.agents = {
    a1: { id: 'a1', group: 'Torque', cell_type: 'agent', status: 'running' },
  };

  vm.runInContext('refreshStatusBar();', context);
  const beforeChildren = panelButtons.children.slice();
  assert.equal(document.activeElement, board);
  assert.deepEqual(beforeChildren, [board, chat]);

  sandbox.state.agents.a2 = { id: 'a2', group: 'Torque', cell_type: 'agent', status: 'idle' };
  vm.runInContext('refreshStatusBar();', context);

  assert.equal(document.getElementById('statusbar-panel-buttons'), panelButtons);
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

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    Date: { now: () => Date.now() },
    state: {
      groups: { torque: [] },
      group_settings: { torque: {} },
      agents: {},
      board_tasks: {},
      children: {},
      engineer_settings: {},
      engineer_journal: {},
      engineer_sent_events: {},
      engineer_buffer_stats: {},
      engineer_worklog: {},
    },
    focusedItemId: '',
    document: {
      activeElement: null,
      getElementById() { return null; },
    },
    window: {
      prompt() { return null; },
    },
    _cachedProviders: [],
    _esc(value) { return String(value); },
    _currentGroup() { return 'torque'; },
    _captureSurfaceState() { return null; },
    _restoreSurfaceState() {},
    setInterval() { return 1; },
    clearInterval() {},
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function createHarness() {
  const sandbox = createSandbox();
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-agent' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/agent_panel.js');
  return { context, panel, sandbox };
}

function engineer(id, name, createdAt) {
  return {
    id,
    name,
    slug: id,
    kind: 'engineer',
    group: 'torque',
    cell_type: 'agent',
    status: 'running',
    created_at: createdAt,
  };
}

test('focused engineer panel shows journal/events/worklog tabs with the engineer header', () => {
  const { context, panel, sandbox } = createHarness();
  sandbox.state.agents['eng-alice'] = engineer('eng-alice', 'Alice', 10);
  sandbox.focusedItemId = 'eng-alice';

  vm.runInContext('renderAgentPanel()', context);

  assert.match(panel.innerHTML, /Engineer: Alice · Group: torque/);
  assert.match(panel.innerHTML, />Journal</);
  assert.match(panel.innerHTML, />Events</);
  assert.match(panel.innerHTML, />Worklog</);
  assert.doesNotMatch(panel.innerHTML, /Group: torque \(group-wide\)/);
});

test('focused engineer worklog tab renders the group worklog entries', () => {
  const { context, panel, sandbox } = createHarness();
  sandbox.state.agents['eng-alice'] = engineer('eng-alice', 'Alice', 10);
  sandbox.state.agents['worker-1'] = { id: 'worker-1', name: 'Worker One' };
  sandbox.state.engineer_worklog.torque = [
    {
      id: 4,
      task_id: 'TORQUE:9',
      task_title: 'Add Worklog tab',
      agent_id: 'worker-1',
      agent_name: 'Worker One',
      agent_slug: 'worker-one',
      agent_owned: true,
      started_at: 12,
    },
  ];
  sandbox.state.board_tasks = {
    'TORQUE:9': {
      id: 'TORQUE:9',
      task: 'Add Worklog tab',
      lane: 'In Progress',
      status: 'In review',
      agent_id: 'worker-1',
    },
  };
  sandbox.focusedItemId = 'eng-alice';

  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'worklog'; renderAgentPanel()`, context);

  assert.match(panel.innerHTML, /Engineer: Alice · Group: torque/);
  assert.match(panel.innerHTML, /Dispatched tasks/);
  assert.match(panel.innerHTML, /Add Worklog tab/);
  assert.match(panel.innerHTML, /In Progress/);
  assert.match(panel.innerHTML, /In review/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    AGENT_ICONS: ['🤖'],
    PROCESS_MAP: {},
    state: {
      group_settings: {},
      agents: {},
    },
    sendCalls: [],
    modalCalls: [],
    contextMenuCalls: [],
    esc(value) { return String(value); },
    _cachedAgentTemplates: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.openEngineerLaunchDialog = function(group, agentId) {
    sandbox.modalCalls.push({ group, agentId: agentId || '' });
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

test('relaunchAgent routes stopped designated engineers through the launch dialog', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { engineer_agent_id: 'engineer-1' };
  sandbox.state.agents['engineer-1'] = {
    id: 'engineer-1',
    group: 'alpha',
    status: 'stopped',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`relaunchAgent('engineer-1')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), [
    { group: 'alpha', agentId: 'engineer-1' },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('relaunchAgent keeps normal agents on the plain relaunch path', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { engineer_agent_id: 'engineer-1' };
  sandbox.state.agents['agent-1'] = {
    id: 'agent-1',
    group: 'alpha',
    status: 'stopped',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');

  vm.runInContext(`relaunchAgent('agent-1')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'relaunch_agent', id: 'agent-1' },
  ]);
});

test('designated engineer context menu shows Restart Engineer and routes through the launch dialog', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { engineer_agent_id: 'engineer-1' };
  sandbox.state.agents['engineer-1'] = {
    id: 'engineer-1',
    group: 'alpha',
    status: 'stopped',
    cell_type: 'agent',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');
  sandbox.showContextMenu = function(x, y, items) {
    sandbox.contextMenuCalls.push({ x, y, items });
  };

  const event = {
    clientX: 40,
    clientY: 80,
    preventDefault() {},
    stopPropagation() {},
  };
  sandbox.__event = event;
  vm.runInContext(`onCellContextMenu(__event, 'engineer-1')`, context);

  assert.equal(sandbox.contextMenuCalls.length, 1);
  const items = JSON.parse(JSON.stringify(sandbox.contextMenuCalls[0].items));
  assert.equal(items.some((item) => item.label === 'Restart Engineer…'), true);
  assert.equal(items.some((item) => item.label === 'Relaunch'), false);

  const restartItem = sandbox.contextMenuCalls[0].items.find((item) => item.label === 'Restart Engineer…');
  assert.ok(restartItem);
  vm.runInContext(restartItem.action, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.modalCalls)), [
    { group: 'alpha', agentId: 'engineer-1' },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('normal agent context menu keeps the existing relaunch action', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings.alpha = { engineer_agent_id: 'engineer-1' };
  sandbox.state.agents['agent-1'] = {
    id: 'agent-1',
    group: 'alpha',
    status: 'stopped',
    cell_type: 'agent',
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/commands.js');
  sandbox.showContextMenu = function(x, y, items) {
    sandbox.contextMenuCalls.push({ x, y, items });
  };

  const event = {
    clientX: 10,
    clientY: 20,
    preventDefault() {},
    stopPropagation() {},
  };
  sandbox.__event = event;
  vm.runInContext(`onCellContextMenu(__event, 'agent-1')`, context);

  assert.equal(sandbox.contextMenuCalls.length, 1);
  const items = JSON.parse(JSON.stringify(sandbox.contextMenuCalls[0].items));
  assert.equal(items.some((item) => item.label === 'Relaunch'), true);
  assert.equal(items.some((item) => item.label === 'Restart…'), true);
  assert.equal(items.some((item) => item.label === 'Restart Engineer…'), false);
});

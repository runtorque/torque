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

function createSandbox() {
  const sandbox = {
    console,
    state: {
      active_session_id: '',
      agents: {},
      group_settings: {},
      engineer_settings: {},
      agent_digest_settings: {},
    },
    contextMenuCalls: [],
    modalCalls: [],
    sendCalls: [],
    esc(value) { return String(value); },
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

function createContext() {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  // Match webview load order: canvas is declared before command helpers.
  loadScript(context, 'static/js/canvas.js');
  loadScript(context, 'static/js/commands.js');
  context.showContextMenu = function(x, y, items) {
    context.contextMenuCalls.push({ x, y, items });
  };
  return context;
}

function showCanvasMenu(context, kind, id) {
  vm.runInContext(
    `_canvasShowCardMenu(12, 34, ${JSON.stringify(kind)}, ${JSON.stringify(id)}, 'alpha')`,
    context
  );
  assert.equal(context.contextMenuCalls.length, 1);
  return context.contextMenuCalls[0].items;
}

function labels(items) {
  return items.filter((item) => item && item.label).map((item) => item.label);
}

test('canvas card menu reuses grid worktree, task, context, and edit actions', () => {
  const context = createContext();
  context.state.group_settings.alpha = { git_worktree: true };
  context.state.agents['worker-1'] = {
    id: 'worker-1',
    name: 'Worker One',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    worktree_path: '/tmp/worker-1',
    session_id: 'sess-worker',
    agent_type: 'codex',
  };

  const items = showCanvasMenu(context, 'worker', 'worker-1');
  const itemLabels = labels(items);

  assert.deepEqual(JSON.parse(JSON.stringify(itemLabels)), [
    'Edit…',
    'Focus',
    'Restart…',
    'Worktree',
    'View Tasks',
    'Clear context',
    'Copy ID: worker-1…',
    'Delete',
  ]);
  assert.equal(items.find((item) => item.label === 'Worktree').submenu, "_showWorktreeSubmenu('worker-1')");
  assert.equal(itemLabels.includes('Rename…'), false);
  assert.equal(itemLabels.includes('Remove'), false);
});

test('canvas card menu exposes designated engineer relaunch and lifecycle controls', () => {
  const context = createContext();
  context.state.group_settings.alpha = {
    engineer_agent_id: 'engineer-1',
    git_worktree: true,
  };
  context.state.agents['engineer-1'] = {
    id: 'engineer-1',
    name: 'Engineer One',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
  };

  const items = showCanvasMenu(context, 'engineer', 'engineer-1');
  const itemLabels = labels(items);

  assert.equal(itemLabels.includes('Restart Engineer…'), true);
  assert.equal(itemLabels.includes('Relaunch'), false);
  assert.equal(itemLabels.includes('Restart…'), true);
  assert.equal(itemLabels.includes('Pause event delivery'), true);
  assert.equal(itemLabels.includes('Dismiss…'), true);
  assert.equal(itemLabels.includes('Create Worktree'), true);
  assert.equal(itemLabels.includes('View Tasks'), true);

  const restartItem = items.find((item) => item.label === 'Restart Engineer…');
  vm.runInContext(restartItem.action, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.modalCalls)), [
    { group: 'alpha', agentId: 'engineer-1' },
  ]);
});

test('canvas menu gates architect-only actions by cell.kind from state', () => {
  const context = createContext();
  context.state.group_settings.alpha = {};
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Architect One',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  };

  const items = showCanvasMenu(context, 'worker', 'arch-1');
  const itemLabels = labels(items);

  assert.equal(itemLabels[0], '+ Engineer here');
  assert.equal(itemLabels.includes('Pause event delivery'), true);
  assert.equal(itemLabels.includes('Dismiss…'), true);
});

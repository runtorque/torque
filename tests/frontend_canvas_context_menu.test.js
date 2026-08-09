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
    clipboardCalls: [],
    modalCalls: [],
    sendCalls: [],
    esc(value) { return String(value); },
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.navigator = {
    clipboard: {
      writeText(value) {
        sandbox.clipboardCalls.push(value);
        return Promise.resolve();
      },
    },
  };
  sandbox.closeContextMenu = function() {};
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
  context.closeContextMenu = function() {};
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

test('canvas empty menu actions invoke the create handlers for the canvas group', () => {
  const context = createContext();
  vm.runInContext(`
    var createCalls = [];
    openAddArchitectForGroup = function(group) { createCalls.push({ type: 'architect', group: group }); };
    openAddEngineerForSection = function(group, architectId) { createCalls.push({ type: 'engineer', group: group, architectId: architectId || '' }); };
    openAddWorkerForSection = function(group) { createCalls.push({ type: 'worker', group: group }); };
  `, context);

  vm.runInContext("_canvasShowEmptyMenu(56, 78, 'alpha')", context);

  assert.equal(context.contextMenuCalls.length, 1);
  assert.equal(context.contextMenuCalls[0].x, 56);
  assert.equal(context.contextMenuCalls[0].y, 78);
  assert.deepEqual(JSON.parse(JSON.stringify(labels(context.contextMenuCalls[0].items))), [
    'New architect',
    'New engineer',
    'New worker',
  ]);

  for (const item of context.contextMenuCalls[0].items) {
    vm.runInContext(item.action, context);
  }
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(createCalls)', context)), [
    { type: 'architect', group: 'alpha' },
    { type: 'engineer', group: 'alpha', architectId: '' },
    { type: 'worker', group: 'alpha' },
  ]);
});

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

  const standardItems = context._cellContextMenuItems('worker-1');
  assert.deepEqual(JSON.parse(JSON.stringify(labels(standardItems))), [
    'Edit…',
    'Restart…',
    'Worktree',
    'View Tasks',
    'Clear context',
    'Copy ID: worker-1…',
    'Copy Name',
    'Delete',
  ]);

  const items = showCanvasMenu(context, 'worker', 'worker-1');
  const itemLabels = labels(items);

  assert.deepEqual(JSON.parse(JSON.stringify(itemLabels)), [
    'Edit…',
    'Restart…',
    'Worktree',
    'View Tasks',
    'Clear context',
    'Copy ID: worker-1…',
    'Copy Name',
    'Delete',
  ]);
  assert.equal(items.find((item) => item.label === 'Worktree').submenu, "_showWorktreeSubmenu('worker-1')");
  assert.equal(itemLabels.includes('Rename…'), false);
  assert.equal(itemLabels.includes('Remove'), false);
  assert.equal(itemLabels.includes('Focus'), false);
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

  assert.deepEqual(JSON.parse(JSON.stringify(labels(context._cellContextMenuItems('engineer-1')))), [
    'Edit…',
    'Settings…',
    'Restart Engineer…',
    'Restart…',
    'View Tasks',
    'Copy ID: engineer…',
    'Copy Name',
    'Dismiss…',
    'Delete',
  ]);
  const items = showCanvasMenu(context, 'engineer', 'engineer-1');
  const itemLabels = labels(items);

  assert.deepEqual(JSON.parse(JSON.stringify(itemLabels)), [
    'Edit…',
    'Pause event delivery',
    'Settings…',
    'Restart Engineer…',
    'Restart…',
    'View Tasks',
    'Copy ID: engineer…',
    'Copy Name',
    'Dismiss…',
    'Delete',
  ]);
  assert.equal(itemLabels.includes('Relaunch'), false);
  assert.equal(itemLabels.includes('Create Worktree'), false);
  assert.equal(itemLabels.indexOf('Dismiss…'), itemLabels.indexOf('Delete') - 1);

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
  context.createCalls = [];
  context.openAddEngineerForSection = function(group, architectId) {
    context.createCalls.push({ group, architectId: architectId || '' });
  };

  const items = showCanvasMenu(context, 'worker', 'arch-1');
  const itemLabels = labels(items);

  assert.equal(itemLabels.includes('New engineer'), true);
  assert.equal(itemLabels.includes('Pause event delivery'), true);
  assert.equal(itemLabels.includes('Dismiss…'), true);
  assert.equal(itemLabels.includes('Create Worktree'), false);
  assert.equal(itemLabels.includes('Focus'), false);
  assert.equal(itemLabels.indexOf('Dismiss…'), itemLabels.indexOf('Delete') - 1);
  const newEngineerItem = items.find((item) => item.label === 'New engineer');
  assert.equal(newEngineerItem.action, 'openAddEngineerForSection("alpha", "arch-1")');
  vm.runInContext(newEngineerItem.action, context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.createCalls)), [
    { group: 'alpha', architectId: 'arch-1' },
  ]);
});

test('standard menu copies the exact current name and keeps worker worktree creation', () => {
  const context = createContext();
  context.state.group_settings.alpha = { git_worktree: true };
  context.state.agents['worker-copy'] = {
    id: 'worker-copy',
    name: 'Original',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
  };

  const items = context._cellContextMenuItems('worker-copy');
  const itemLabels = labels(items);
  assert.deepEqual(JSON.parse(JSON.stringify(itemLabels)), [
    'Edit…',
    'Restart…',
    'Create Worktree',
    'View Tasks',
    'Copy ID: worker-c…',
    'Copy Name',
    'Delete',
  ]);
  assert.equal(itemLabels.includes('Focus'), false);
  assert.equal(itemLabels.indexOf('Copy Name'), itemLabels.indexOf('Copy ID: worker-c…') + 1);

  context.state.agents['worker-copy'].name = `Current <Worker> & "exact"`;
  const copyNameItem = items.find((item) => item.label === 'Copy Name');
  vm.runInContext(copyNameItem.action, context);
  assert.deepEqual(context.clipboardCalls, [`Current <Worker> & "exact"`]);
});

test('terminal menu has copy actions without focus and canvas fallback keeps the same tail order', () => {
  const context = createContext();
  context.state.agents['terminal-1'] = {
    id: 'terminal-1',
    name: 'Shell One',
    kind: 'terminal',
    group: 'alpha',
    cell_type: 'terminal',
    status: 'running',
  };

  assert.deepEqual(JSON.parse(JSON.stringify(labels(context._cellContextMenuItems('terminal-1')))), [
    'Edit…',
    'Copy ID: terminal…',
    'Copy Name',
    'Delete',
  ]);
  const fallbackItems = context._canvasFallbackCardMenuItems('terminal-1');
  assert.deepEqual(JSON.parse(JSON.stringify(labels(fallbackItems))), [
    'Edit…',
    'Copy ID: terminal…',
    'Copy Name',
    'Delete',
  ]);
  context.state.agents['terminal-1'].name = 'Exact current shell name';
  vm.runInContext(fallbackItems.find((item) => item.label === 'Copy Name').action, context);
  assert.deepEqual(context.clipboardCalls, ['Exact current shell name']);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    state: {
      agents: {
        'agent-1': {
          id: 'agent-1',
          name: 'Worker',
          group: 'alpha',
          worktree_base_branch: 'main',
          worktree_merge_squash: true,
        },
      },
      group_settings: {
        alpha: {
          worktree_merge_cleanup: 'close_remove',
          worktree_merge_preserve_diff: true,
        },
      },
    },
    document: {
      body: { appendChild() {} },
      createElement() {
        return {
          className: '',
          textContent: '',
          classList: { add() {}, remove() {} },
          remove() {},
        };
      },
    },
    window: { open() {} },
    requestAnimationFrame(fn) { fn(); },
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
    sendCalls: [],
    confirmCalls: [],
    selectedAgentId: null,
    selectedTerminalId: null,
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.showConfirm = function(message, opts) {
    opts = opts || {};
    sandbox.confirmCalls.push({ message, opts });
    if (!opts.checkboxes) return Promise.resolve(true);
    return Promise.resolve(
      Object.fromEntries(
        opts.checkboxes.map((checkbox) => [checkbox.key, !!checkbox.checked])
      )
    );
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadCommands(context) {
  const filename = path.join(repoRoot, 'static/js/commands.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

test('confirmWorktreeMerge preselects cleanup flags from group defaults', async () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadCommands(context);

  const merged = await vm.runInContext(`_confirmWorktreeMerge('agent-1', '')`, context);

  assert.equal(merged, true);
  assert.equal(sandbox.confirmCalls.length, 1);
  assert.match(
    sandbox.confirmCalls[0].message,
    /^Create PR and squash-merge "Worker" into main\?/
  );
  assert.match(
    sandbox.confirmCalls[0].message,
    /Cleanup options run only after the PR merges, not when the PR is created\./
  );
  assert.equal(sandbox.confirmCalls[0].opts.label, 'Create PR + Merge');
  const checkboxMap = Object.fromEntries(
    sandbox.confirmCalls[0].opts.checkboxes.map((checkbox) => [checkbox.key, checkbox])
  );
  assert.equal(checkboxMap.close_agent_on_merge.checked, true);
  assert.equal(checkboxMap.remove_worktree_on_merge.checked, true);
  assert.equal(checkboxMap.preserve_merge_diff.checked, true);
  assert.equal(checkboxMap.force_direct.checked, false);
  assert.match(checkboxMap.close_agent_on_merge.label, /after PR merge/);
  assert.match(checkboxMap.remove_worktree_on_merge.label, /after PR merge/);
  assert.match(checkboxMap.force_direct.label, /force direct local merge/);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'worktree_merge',
      id: 'agent-1',
      message: '',
      close_agent_on_merge: true,
      remove_worktree_on_merge: true,
      preserve_merge_diff: true,
      clear_context: false,
    },
  ]);
});

test('confirmWorktreeMerge sends force_direct only when the advanced escape is selected', async () => {
  const sandbox = createSandbox();
  sandbox.showConfirm = function(message, opts) {
    sandbox.confirmCalls.push({ message, opts });
    return Promise.resolve({
      close_agent_on_merge: false,
      remove_worktree_on_merge: true,
      preserve_merge_diff: false,
      clear_context: false,
      force_direct: true,
    });
  };
  const context = vm.createContext(sandbox);
  loadCommands(context);

  const merged = await vm.runInContext(`_confirmWorktreeMerge('agent-1', 'Ship it')`, context);

  assert.equal(merged, true);
  assert.equal(sandbox.confirmCalls.length, 1);
  assert.match(sandbox.confirmCalls[0].message, /^Create PR and squash-merge "Worker" into main\?/);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'worktree_merge',
      id: 'agent-1',
      message: 'Ship it',
      close_agent_on_merge: false,
      remove_worktree_on_merge: true,
      preserve_merge_diff: false,
      clear_context: false,
      force_direct: true,
    },
  ]);
});

test('removeAgent confirmation copy uses Delete and explains consequences per kind', async () => {
  const sandbox = createSandbox();
  sandbox._architectDecisionsForAgent = function() {
    return [{ id: 'dec-1', architect_id: 'arch-1' }];
  };
  const context = vm.createContext(sandbox);
  loadCommands(context);

  async function confirmFor(id, agents, children = {}) {
    sandbox.state.agents = agents;
    sandbox.state.children = children;
    sandbox.confirmCalls.length = 0;
    sandbox.sendCalls.length = 0;
    await vm.runInContext(`removeAgent(${JSON.stringify(id)})`, context);
    assert.equal(sandbox.confirmCalls.length, 1);
    assert.equal(sandbox.confirmCalls[0].opts.label, 'Delete');
    return sandbox.confirmCalls[0].message;
  }

  let message = await confirmFor('eng-1', {
    'eng-1': { id: 'eng-1', name: 'Engineer', cell_type: 'agent', kind: 'engineer' },
  });
  assert.match(message, /^Delete "Engineer"\\?/);
  assert.match(message, /Owned workers and assigned tasks will be transferred to the user/);
  assert.match(message, /permanent deletion in 7 days/);
  assert.match(message, /Recently deleted/);

  message = await confirmFor('arch-1', {
    'arch-1': { id: 'arch-1', name: 'Architect', cell_type: 'agent', kind: 'architect' },
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer',
      cell_type: 'agent',
      kind: 'engineer',
      hired_by_architect_id: 'arch-1',
    },
  });
  assert.match(message, /^Delete "Architect"\\?/);
  assert.match(message, /1 hired engineer will be transferred to the user/);
  assert.match(message, /1 decision will be archived/);
  assert.match(message, /Recently deleted/);

  message = await confirmFor('worker-1', {
    'worker-1': {
      id: 'worker-1',
      name: 'Worker',
      cell_type: 'agent',
      kind: 'worker',
      worktree_path: '/tmp/shared',
    },
    'peer-1': {
      id: 'peer-1',
      name: 'Peer',
      cell_type: 'agent',
      kind: 'worker',
      worktree_path: '/tmp/shared',
    },
  });
  assert.match(message, /^Delete "Worker"\\?/);
  assert.match(message, /worktree is shared with "Peer" and will be kept/);
  assert.match(message, /Recently deleted/);

  message = await confirmFor('worker-1', {
    'worker-1': {
      id: 'worker-1',
      name: 'Worker',
      cell_type: 'agent',
      kind: 'worker',
      worktree_path: '/tmp/exclusive',
      worktree_checkpoints: 1,
      worktree_dirty: true,
    },
  });
  assert.match(message, /worktree has unmerged commits and uncommitted changes/);
  assert.match(message, /if not restored, all changes will be lost/);

  message = await confirmFor('worker-1', {
    'worker-1': {
      id: 'worker-1',
      name: 'Worker',
      cell_type: 'agent',
      kind: 'worker',
      worktree_path: '/tmp/clean',
    },
  });
  assert.match(message, /worktree will be preserved during the 7-day restore window, then permanently removed/);

  message = await confirmFor('term-1', {
    'term-1': { id: 'term-1', name: 'Logs', cell_type: 'terminal' },
  });
  assert.equal(message, 'Delete "Logs"? This terminal will close.');
  assert.doesNotMatch(message, /Recently deleted/);
});

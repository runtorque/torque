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
        alpha: { worktree_merge_cleanup: 'close_remove' },
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
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.showConfirm = function(message, opts) {
    sandbox.confirmCalls.push({ message, opts });
    return Promise.resolve({
      close_agent_on_merge: !!opts.checkboxes[0].checked,
      remove_worktree_on_merge: !!opts.checkboxes[1].checked,
      clear_context: false,
    });
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
  assert.equal(sandbox.confirmCalls[0].opts.checkboxes[0].checked, true);
  assert.equal(sandbox.confirmCalls[0].opts.checkboxes[1].checked, true);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'worktree_merge',
      id: 'agent-1',
      message: '',
      close_agent_on_merge: true,
      remove_worktree_on_merge: true,
      clear_context: false,
    },
  ]);
});

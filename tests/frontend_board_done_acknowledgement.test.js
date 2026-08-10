const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createContext({ accepted }) {
  const sent = [];
  const confirmations = [];
  const toasts = [];
  const sandbox = {
    console,
    Promise,
    showConfirm(message, options) {
      confirmations.push({ message, options });
      return Promise.resolve(accepted);
    },
    send(command) {
      sent.push(command);
      return true;
    },
    _showToast(message, level) {
      toasts.push({ message, level });
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(
    fs.readFileSync(
      path.join(repoRoot, 'static/js/board/selection.js'), 'utf8'
    ),
    context,
    { filename: 'static/js/board/selection.js' },
  );
  return { context, confirmations, sent, toasts };
}

test('Done acknowledgement prompt resends the move with explicit acknowledgement', async () => {
  const { context, confirmations, sent } = createContext({ accepted: true });
  const handled = context.handleBoardMoveAcknowledgementResponse({
    type: 'task_move_acknowledgement_required',
    task_id: 'TORQUE:1608',
    new_lane: 'Done',
    clear_status: true,
    position: 3,
    message: 'Closing will leave branch torque/forge/conflict at 45ff0a96.',
  });
  await Promise.resolve();

  assert.equal(handled, true);
  assert.equal(confirmations.length, 1);
  assert.match(confirmations[0].message, /torque\/forge\/conflict/);
  assert.equal(confirmations[0].options.label, 'Close anyway');
  assert.equal(confirmations[0].options.variant, 'btn-warning');
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [{
    cmd: 'board_move_task',
    id: 'TORQUE:1608',
    lane: 'Done',
    acknowledge_unmerged: true,
    clear_status: true,
    position: 3,
  }]);
});

test('unarchive acknowledgement resends Restore with explicit acknowledgement', async () => {
  const { context, sent } = createContext({ accepted: true });
  assert.equal(context.handleBoardMoveAcknowledgementResponse({
    type: 'task_move_acknowledgement_required',
    command: 'board_unarchive_task',
    task_id: 'TORQUE:1620',
    new_lane: 'Done',
    message: 'Restoring will leave branch torque/worker/unmerged.',
  }), true);
  await Promise.resolve();

  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [{
    cmd: 'board_unarchive_task',
    id: 'TORQUE:1620',
    lane: 'Done',
    acknowledge_unmerged: true,
    clear_status: false,
  }]);
});

test('successful Restore renders advisory with every blocking task id', () => {
  const { context, toasts } = createContext({ accepted: false });
  assert.equal(context.handleBoardDoneAdvisoryResponse({
    type: 'task_unarchived',
    task_id: 'TORQUE:1620',
    advisory: {
      missing_gates: ['code_boundary_not_durably_merged'],
      code_boundary: {
        blocking: [{ task_id: 'TORQUE:1620:1' }, { task_id: 'TORQUE:1620:3' }],
      },
    },
  }), true);

  assert.deepEqual(toasts, [{
    message: 'Restored to Done with a finalization advisory. Blocking tasks: TORQUE:1620:1, TORQUE:1620:3. Missing gates: code_boundary_not_durably_merged.',
    level: 'warning',
  }]);
});

test('typed finalization refusal renders structured blocking task ids', () => {
  const { context, toasts } = createContext({ accepted: false });
  assert.equal(context.handleBoardDoneAdvisoryResponse({
    type: 'finalization_blocked',
    task_id: 'TORQUE:1620',
    finalization: {
      missing_gates: ['review'],
      code_boundary: { blocking: [{ task_id: 'TORQUE:1620:2' }] },
    },
  }), true);

  assert.match(toasts[0].message, /finalization is blocked/);
  assert.match(toasts[0].message, /TORQUE:1620:2/);
  assert.equal(toasts[0].level, 'warning');
});

test('cancelling Done acknowledgement leaves the task untouched', async () => {
  const { context, sent } = createContext({ accepted: false });
  assert.equal(context.handleBoardMoveAcknowledgementResponse({
    type: 'task_move_acknowledgement_required',
    task_id: 'TORQUE:1608',
    new_lane: 'Done',
    message: 'Closing will leave unmerged code.',
  }), true);
  await Promise.resolve();
  assert.deepEqual(sent, []);
});

test('boundary-less card confirmation resends the acknowledged Done move', async () => {
  const { context, confirmations, sent } = createContext({ accepted: true });
  assert.equal(context.handleBoardMoveAcknowledgementResponse({
    type: 'task_move_acknowledgement_required',
    task_id: 'TORQUE:1608',
    new_lane: 'Done',
    message: 'Closing this task may leave unmerged code, but no branch or commit reference was recorded.',
    acknowledgement: {
      reason: 'missing_merge_sha',
      branch: '',
      commit_sha: '',
      merge_commit_sha: '',
    },
  }), true);
  await Promise.resolve();

  assert.equal(confirmations.length, 1);
  assert.match(confirmations[0].message, /no branch or commit reference/);
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [{
    cmd: 'board_move_task',
    id: 'TORQUE:1608',
    lane: 'Done',
    acknowledge_unmerged: true,
    clear_status: false,
  }]);
});

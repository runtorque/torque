const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createContext({ accepted }) {
  const sent = [];
  const confirmations = [];
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
  return { context, confirmations, sent };
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

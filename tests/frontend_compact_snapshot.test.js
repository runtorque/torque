/* Compact-snapshot consumer (compact-v1) frontend tests.
 *
 * Exercises the localStorage opt-in, WS URL annotation, lazy-load command
 * callers, response merging into shared `state`, and rerender guardrails
 * (preserve scroll/focus/inline-drafts across a lazy-load round-trip). */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

/* vm-context objects have a different Object.prototype than the test module,
 * so assert.deepStrictEqual rejects structurally-equal values. Normalize via
 * JSON for comparisons. */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}
function assertPlainEqual(actual, expected, message) {
  assert.deepEqual(plain(actual), expected, message);
}

function createCompactContext(opts) {
  var flag = (opts && opts.flag) || null;
  var localStorageState = new Map();
  if (flag) localStorageState.set('torque:snapshot_protocol', flag);
  var sandbox = {
    console,
    Date,
    JSON,
    Math,
    Object,
    state: {},
    localStorage: {
      getItem(key) {
        return localStorageState.has(key) ? localStorageState.get(key) : null;
      },
      setItem(key, value) {
        localStorageState.set(String(key), String(value));
      },
      removeItem(key) { localStorageState.delete(String(key)); },
      clear() { localStorageState.clear(); },
    },
    sendCalls: [],
    renderCalls: 0,
    renderActivePanelCalls: 0,
  };
  sandbox.send = function(payload) { sandbox.sendCalls.push(payload); };
  sandbox.render = function() { sandbox.renderCalls += 1; };
  sandbox.renderActivePanel = function() { sandbox.renderActivePanelCalls += 1; };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  var context = vm.createContext(sandbox);
  var source = fs.readFileSync(
    path.join(repoRoot, 'static/js/compact.js'), 'utf8');
  vm.runInContext(source, context, { filename: 'compact.js' });
  return { context, sandbox };
}

function run(context, code) {
  return vm.runInContext(code, context);
}

test('_compactPrepareWSUrl appends ?compact=1 by default (unset flag opts in)', () => {
  const { context } = createCompactContext();
  assert.equal(
    run(context, `_compactPrepareWSUrl('ws://localhost:18932/ws')`),
    'ws://localhost:18932/ws?compact=1');
  assert.equal(run(context, `_compactFlagEnabled()`), true);
});

test('_compactPrepareWSUrl is a no-op when explicit legacy opt-out is set', () => {
  for (const sentinel of ['legacy', 'off', '0', 'false']) {
    const { context } = createCompactContext({ flag: sentinel });
    assert.equal(
      run(context, `_compactPrepareWSUrl('ws://localhost:18932/ws')`),
      'ws://localhost:18932/ws',
      'sentinel ' + sentinel + ' should disable compact');
    assert.equal(run(context, `_compactFlagEnabled()`), false);
  }
});

test('_compactPrepareWSUrl still honours explicit compact-v1 opt-in', () => {
  const { context } = createCompactContext({ flag: 'compact-v1' });
  assert.equal(
    run(context, `_compactPrepareWSUrl('ws://localhost:18932/ws')`),
    'ws://localhost:18932/ws?compact=1');
});

test('_compactPrepareWSUrl preserves existing query parameters', () => {
  const { context } = createCompactContext({ flag: '1' });
  assert.equal(
    run(context, `_compactPrepareWSUrl('ws://localhost:18932/ws?foo=bar')`),
    'ws://localhost:18932/ws?foo=bar&compact=1');
});

test('_compactModeActive reflects the snapshot_protocol on state', () => {
  const { context, sandbox } = createCompactContext();
  assert.equal(run(context, `_compactModeActive()`), false);
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  assert.equal(run(context, `_compactModeActive()`), true);
});

test('_compactInitDeferredMaps seeds empty maps on compact state', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assertPlainEqual(sandbox.state.decisions, {});
  assertPlainEqual(sandbox.state.pending_hires, {});
  assertPlainEqual(sandbox.state.engineer_journal, {});
  assertPlainEqual(sandbox.state.engineer_worklog, {});
  assertPlainEqual(sandbox.state.engineer_streams, {});
});

test('_compactInitDeferredMaps is a no-op when snapshot is legacy', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { decisions: null };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(sandbox.state.decisions, null);
});

test('lazyLoadDecisions sends decisions_snapshot and dedups in-flight calls', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(run(context, `lazyLoadDecisions()`), true);
  assert.equal(run(context, `lazyLoadDecisions()`), false);
  assertPlainEqual(sandbox.sendCalls, [{ cmd: 'decisions_snapshot' }]);
});

test('lazyLoadPendingHires defaults status_filter to pending', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(run(context, `lazyLoadPendingHires()`), true);
  assertPlainEqual(sandbox.sendCalls, [
    { cmd: 'pending_hires_snapshot', status_filter: 'pending' },
  ]);
});

test('lazyLoadArchivedTasks is a no-op outside compact mode', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = {};
  assert.equal(run(context, `lazyLoadArchivedTasks('alpha')`), false);
  assertPlainEqual(sandbox.sendCalls, []);
});

test('lazyLoadArchivedTasks sends once per group', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(run(context, `lazyLoadArchivedTasks('alpha')`), true);
  assert.equal(run(context, `lazyLoadArchivedTasks('alpha')`), false);
  assert.equal(run(context, `lazyLoadArchivedTasks('beta')`), true);
  assertPlainEqual(sandbox.sendCalls, [
    { cmd: 'archived_tasks', group: 'alpha' },
    { cmd: 'archived_tasks', group: 'beta' },
  ]);
});

test('lazyLoadEngineerJournal requires a group and dedups per group', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(run(context, `lazyLoadEngineerJournal('')`), false);
  assert.equal(run(context, `lazyLoadEngineerJournal('alpha')`), true);
  assert.equal(run(context, `lazyLoadEngineerJournal('alpha')`), false);
  assertPlainEqual(sandbox.sendCalls, [
    { cmd: 'engineer_journal_snapshot', group: 'alpha' },
  ]);
});

test('ensureTaskDetail invokes cb immediately in legacy mode', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { board_tasks: { 't-1': { id: 't-1', task: 'hi', description: 'done' } } };
  let received = null;
  run(context, `function _cb(task) { received = task; }`);
  sandbox.received = null;
  run(context, `ensureTaskDetail('t-1', function(task){ received = task; })`);
  assert.equal(sandbox.received.id, 't-1');
  assertPlainEqual(sandbox.sendCalls, []);
});

test('ensureTaskDetail fetches when compact task is missing heavy fields', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: { 't-9': { id: 't-9', task: 'short', lane: 'To Do' } },
  };
  run(context, `_compactInitDeferredMaps()`);
  sandbox.received = null;
  run(context, `ensureTaskDetail('t-9', function(task){ received = task; })`);
  assertPlainEqual(sandbox.sendCalls, [{ cmd: 'task_detail', id: 't-9' }]);
  // Second call while in-flight stacks the callback without sending again.
  run(context, `ensureTaskDetail('t-9', function(task){ received = task; })`);
  assert.equal(sandbox.sendCalls.length, 1);

  // Simulate server response with full detail.
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-9',
    task: { id: 't-9', task: 'short', lane: 'To Do',
            description: 'expanded', messages: [{message: 'hi'}] }
  })`);
  assert.equal(sandbox.state.board_tasks['t-9'].description, 'expanded');
  assertPlainEqual(sandbox.state.board_tasks['t-9'].messages, [{ message: 'hi' }]);
  assert.equal(sandbox.received.id, 't-9');
});

test('_compactHandleLazyResponse merges decisions and pending_hires snapshots', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactHandleLazyResponse({
    type: 'decisions_snapshot',
    decisions: { 'd-1': { id: 'd-1' }, 'd-2': { id: 'd-2' } }
  })`);
  run(context, `_compactHandleLazyResponse({
    type: 'pending_hires_snapshot',
    pending_hires: { 'h-1': { id: 'h-1' } }
  })`);
  assert.deepEqual(Object.keys(sandbox.state.decisions).sort(), ['d-1', 'd-2']);
  assert.deepEqual(Object.keys(sandbox.state.pending_hires), ['h-1']);
});

test('_compactHandleLazyResponse merges archived_tasks and engineer_journal_snapshot', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactHandleLazyResponse({
    type: 'archived_tasks',
    group: 'alpha',
    board_tasks: { 't-arch': { id: 't-arch', task: 'old', description: 'hist' } }
  })`);
  assert.equal(sandbox.state.board_tasks['t-arch'].description, 'hist');

  run(context, `_compactHandleLazyResponse({
    type: 'engineer_journal_snapshot',
    group: 'alpha',
    engineer_journal: { 'eng-a': [{ id: 1, entry: 'j1', author_cell_id: 'eng-a' }] },
    engineer_worklog: { alpha: [{ id: 2, entry: 'w1' }] },
    engineer_streams: { alpha: { count: 3, items: [] } }
  })`);
  assertPlainEqual(sandbox.state.engineer_journal['eng-a'],
    [{ id: 1, entry: 'j1', author_cell_id: 'eng-a' }]);
  assertPlainEqual(sandbox.state.engineer_worklog.alpha,
    [{ id: 2, entry: 'w1' }]);
  assertPlainEqual(sandbox.state.engineer_streams.alpha,
    { count: 3, items: [] });
});

test('_compactHandleLazyResponse returns false for unrelated message types', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(
    run(context, `_compactHandleLazyResponse({ type: 'delta', seq: 1 })`),
    false);
  assert.equal(run(context, `_compactHandleLazyResponse(null)`), false);
});

test('_compactAutoHydrateOnConnect fires decisions + pending_hires once', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactAutoHydrateOnConnect()`);
  assert.deepEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['decisions_snapshot', 'pending_hires_snapshot']);

  // Simulating both responses clears in-flight state; re-hydrating should
  // not re-send because the fetched flags latch.
  run(context, `_compactHandleLazyResponse({
    type: 'decisions_snapshot', decisions: {}
  })`);
  run(context, `_compactHandleLazyResponse({
    type: 'pending_hires_snapshot', pending_hires: {}
  })`);
  sandbox.sendCalls.length = 0;
  run(context, `_compactAutoHydrateOnConnect()`);
  assertPlainEqual(sandbox.sendCalls, []);
});

test('a resync re-init clears latched flags so a fresh auto-hydrate runs', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactAutoHydrateOnConnect()`);
  run(context, `_compactHandleLazyResponse({
    type: 'decisions_snapshot', decisions: {}
  })`);
  run(context, `_compactHandleLazyResponse({
    type: 'pending_hires_snapshot', pending_hires: {}
  })`);
  sandbox.sendCalls.length = 0;

  // Simulate a resync: new state frame → init clears flags.
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactAutoHydrateOnConnect()`);
  assert.deepEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['decisions_snapshot', 'pending_hires_snapshot']);
});

/* -- Rerender guardrail: lazy-load must not blow away local UI state ----- */

test('task_detail merge preserves per-task fields the server did not send', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      't-live': {
        id: 't-live', task: 'live', lane: 'To Do', position: 4,
        lane_entered_at: '2026-04-22T00:00:00+00:00',
        // Delta ops could arrive mid-flight carrying non-compact fields
        // (e.g. a task_upsert whose source sets status). The merge must not
        // drop those in favour of the task_detail response.
        status: 'on-review',
      },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-live',
    task: { id: 't-live', task: 'live', description: 'full' }
  })`);
  var t = sandbox.state.board_tasks['t-live'];
  assert.equal(t.description, 'full');
  assert.equal(t.status, 'on-review');
  assert.equal(t.position, 4);
  assert.equal(t.lane_entered_at, '2026-04-22T00:00:00+00:00');
});

test('_compactTaskHasFullDetail only trusts the fully-loaded registry', () => {
  // Guards against a partially enriched card (e.g. a delta that added one
  // heavy field) short-circuiting a needed task_detail fetch.
  const { context, sandbox } = createCompactContext();
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      't-partial': {
        id: 't-partial', task: 'x', lane: 'To Do',
        action_vars: { foo: 'bar' },  // enriched by a delta, not by task_detail
      },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(
    run(context, `_compactTaskHasFullDetail(state.board_tasks['t-partial'])`),
    false);

  // A real task_detail response latches the registry.
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-partial',
    task: { id: 't-partial', description: 'full' }
  })`);
  assert.equal(
    run(context, `_compactTaskHasFullDetail(state.board_tasks['t-partial'])`),
    true);
});

/* -- Board duplicate / clone / artifact consumers hydrate first ---------- */

function createBoardConsumerContext() {
  const { context, sandbox } = createCompactContext({ flag: 'compact-v1' });
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      't-compact': {
        id: 't-compact', task: 'Short card', slug: 'short-card',
        group: 'alpha', lane: 'To Do', position: 2,
        action_name: 'feature/implement', labels: ['perf'],
      },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  // Stub out dependencies so we only exercise the hydrate-before-act path.
  run(context, `
    _closeCtxMenu = function() {};
    _boardTasks = function() { return state.board_tasks; };
    _currentGroup = function() { return 'alpha'; };
    isSystemLabel = function(l) { return String(l).indexOf('torque:') === 0; };
    _boardTaskCloneFields = function(task) {
      return {
        task: task.task || '',
        description: task.description || '',
        group: task.group || 'alpha',
        action_name: task.action_name || '',
        action_vars: Object.assign({}, task.action_vars || {}),
        agent_template: task.agent_template || '',
        labels: (task.labels || []).filter(function(l) { return !isSystemLabel(l); }),
      };
    };
    _taskOpenModalCalls = [];
    _taskOpenModal = function(cfg) { _taskOpenModalCalls.push(cfg); };
  `);
  return { context, sandbox };
}

test('boardDuplicateTask hydrates the full task before cloning', () => {
  const { context, sandbox } = createBoardConsumerContext();
  const boardSource = fs.readFileSync(
    path.join(repoRoot, 'static/js/board.js'), 'utf8');
  // Only load the helpers we need — the full board.js pulls in too many
  // DOM dependencies. We extract just boardDuplicateTask + boardCloneTask.
  const duplicateFn = boardSource.match(
    /function boardDuplicateTask\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  const cloneFn = boardSource.match(
    /function boardCloneTask\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  vm.runInContext(duplicateFn + '\n' + cloneFn, context);

  run(context, `boardDuplicateTask('t-compact')`);
  // First call fires task_detail and bails out — no board_add_task yet.
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);

  // Server responds with heavy fields; the callback re-enters and
  // now emits board_add_task carrying the hydrated action_vars/description.
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-compact',
    task: {
      id: 't-compact', task: 'Short card', group: 'alpha',
      description: 'details', action_name: 'feature/implement',
      action_vars: { foo: 'bar' }, agent_template: 'impl',
      labels: ['perf']
    }
  })`);
  const addTask = sandbox.sendCalls.find(function(c) {
    return c.cmd === 'board_add_task';
  });
  assert.ok(addTask, 'board_add_task should have been sent after hydration');
  assert.equal(addTask.description, 'details');
  assert.equal(addTask.action_name, 'feature/implement');
  assert.equal(addTask.agent_template, 'impl');
  assertPlainEqual(addTask.action_vars, { foo: 'bar' });
  assertPlainEqual(addTask.labels, ['perf']);
});

test('boardCloneTask hydrates before opening the clone modal', () => {
  const { context, sandbox } = createBoardConsumerContext();
  const boardSource = fs.readFileSync(
    path.join(repoRoot, 'static/js/board.js'), 'utf8');
  const duplicateFn = boardSource.match(
    /function boardDuplicateTask\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  const cloneFn = boardSource.match(
    /function boardCloneTask\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  vm.runInContext(duplicateFn + '\n' + cloneFn, context);

  run(context, `boardCloneTask('t-compact')`);
  assert.equal(run(context, `_taskOpenModalCalls.length`), 0);
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);

  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-compact',
    task: {
      id: 't-compact', task: 'Short card', group: 'alpha',
      description: 'details', action_name: 'feature/implement',
      action_vars: { foo: 'bar' }, agent_template: 'impl',
      labels: ['perf']
    }
  })`);
  assert.equal(run(context, `_taskOpenModalCalls.length`), 1);
  const cfg = plain(run(context, `_taskOpenModalCalls[0]`));
  assert.equal(cfg.description, 'details');
  assert.equal(cfg.agentTemplate, 'impl');
  assertPlainEqual(cfg.actionVars, { foo: 'bar' });
});

test('openTaskArtifactBrowser hydrates when the local card is compact', () => {
  const { context, sandbox } = createBoardConsumerContext();
  const calls = { renderArtifact: [], modalVisible: false };
  // Stub DOM bits and artifact helpers so we can tell whether the render
  // path was entered with compact vs hydrated data.
  run(context, `
    _taskArtifactsCombined = function(task) {
      return (task.attachments || []).concat(task.artifacts || []);
    };
    _renderArtifactCollection = function(list, opts) {
      return 'artifacts=' + list.length;
    };
    document = {
      _els: {},
      getElementById: function(id) {
        if (!this._els[id]) {
          this._els[id] = { textContent: '', innerHTML: '',
            classList: { add: function() {} } };
        }
        return this._els[id];
      },
    };
  `);
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/modals/task-artifacts.js'), 'utf8');
  const fn = source.match(
    /function openTaskArtifactBrowser\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  vm.runInContext(fn, context);

  run(context, `openTaskArtifactBrowser('t-compact')`);
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);
  // Compact-card render path should not run before the full task arrives.
  assert.equal(run(context, `
    (document._els['task-artifacts-modal-content']
      && document._els['task-artifacts-modal-content'].innerHTML) || ''
  `), '');

  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-compact',
    task: {
      id: 't-compact', task: 'Short card',
      artifacts: [{ id: 'a1', filename: 'out.log' }]
    }
  })`);
  // Re-entry rendered the hydrated artifacts.
  assert.equal(
    run(context, `document._els['task-artifacts-modal-content'].innerHTML`),
    'artifacts=1');
});

/* -- Agent-detail task panel hydrates before read/edit ------------------ */

function createAgentDetailContext() {
  const { context, sandbox } = createCompactContext({ flag: 'compact-v1' });
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      't-card': {
        id: 't-card', task: 'card', slug: 'card',
        group: 'alpha', lane: 'To Do', position: 1,
        action_name: 'feature/implement', agent_id: 'agent-1',
      },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  sandbox.renderCalls = 0;
  run(context, `
    _agentDetailUiState = {};
    render = function() { renderCalls++; };
    _agentDetailState = function(agentId) {
      if (!_agentDetailUiState[agentId]) {
        _agentDetailUiState[agentId] = { task_expanded: false, description_editor: null };
      }
      return _agentDetailUiState[agentId];
    };
    _agentDetailDescriptionState = function(agentId, task) {
      var s = _agentDetailState(agentId);
      var tid = String((task && task.id) || '');
      if (!s.description_editor || s.description_editor.task_id !== tid) {
        s.description_editor = { task_id: tid, open: false,
          draft: String((task && task.description) || '') };
      } else if (!s.description_editor.open) {
        s.description_editor.draft = String((task && task.description) || '');
      }
      return s.description_editor;
    };
    _getAgentTask = function(agentId) {
      for (var id in state.board_tasks) {
        if (state.board_tasks[id].agent_id === agentId) return state.board_tasks[id];
      }
      return null;
    };
    document = {
      getElementById: function() { return null; },
    };
    requestAnimationFrame = function(fn) {};
  `);
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/render.js'), 'utf8');
  const fns = [
    'function _toggleAgentDetailTask',
    'function agentDetailEditDescription',
    'function agentDetailSaveDescription',
  ];
  fns.forEach(function(sig) {
    const re = new RegExp(sig.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '\\([\\s\\S]*?\\n\\}', 'm');
    const m = source.match(re);
    if (!m) throw new Error('missing ' + sig);
    vm.runInContext(m[0], context);
  });
  return { context, sandbox };
}

test('_toggleAgentDetailTask hydrates when expanding a compact task', () => {
  const { context, sandbox } = createAgentDetailContext();
  run(context, `_toggleAgentDetailTask('agent-1')`);
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);

  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-card',
    task: { id: 't-card', description: 'real body' }
  })`);
  assert.equal(sandbox.state.board_tasks['t-card'].description, 'real body');
  // Collapsing should not re-fetch.
  sandbox.sendCalls.length = 0;
  run(context, `_toggleAgentDetailTask('agent-1')`);
  assertPlainEqual(sandbox.sendCalls, []);
});

test('agentDetailEditDescription hydrates before opening the editor', () => {
  const { context, sandbox } = createAgentDetailContext();
  run(context, `agentDetailEditDescription('agent-1', 't-card')`);
  // Editor did NOT open yet (would have been true after draft seeding).
  assert.equal(run(context, `
    (function(){
      var e = _agentDetailState('agent-1').description_editor;
      return e ? !!e.open : false;
    })()
  `), false);
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);

  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-card',
    task: { id: 't-card', description: 'existing body' }
  })`);
  // Re-entry now seeds the editor from the real description.
  const editor = plain(run(context,
    `_agentDetailState('agent-1').description_editor`));
  assert.equal(editor.open, true);
  assert.equal(editor.draft, 'existing body');
});

test('agentDetailSaveDescription hydrates before issuing board_update_task', () => {
  const { context, sandbox } = createAgentDetailContext();
  // Simulate a pre-hydrate editor pointed at the compact card.
  run(context, `
    var editor = _agentDetailDescriptionState('agent-1', state.board_tasks['t-card']);
    editor.open = true;
    editor.draft = 'user typed note';
  `);

  run(context, `agentDetailSaveDescription('agent-1', 't-card')`);
  // No destructive update yet — only a hydrate request.
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.cmd; }),
    ['task_detail']);

  // Server delivers the real existing description.
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-card',
    task: { id: 't-card', description: 'server original' }
  })`);
  // The re-entry issues the update carrying the user's draft (not "").
  const update = sandbox.sendCalls.find(function(c) {
    return c.cmd === 'board_update_task';
  });
  assert.ok(update, 'expected board_update_task after hydration');
  assert.equal(update.id, 't-card');
  assert.equal(update.description, 'user typed note');
});

test('agentDetailSaveDescription skips the update when draft matches hydrated text', () => {
  const { context, sandbox } = createAgentDetailContext();
  run(context, `
    var editor = _agentDetailDescriptionState('agent-1', state.board_tasks['t-card']);
    editor.open = true;
    editor.draft = 'server original';
  `);
  run(context, `agentDetailSaveDescription('agent-1', 't-card')`);
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 't-card',
    task: { id: 't-card', description: 'server original' }
  })`);
  const update = sandbox.sendCalls.find(function(c) {
    return c.cmd === 'board_update_task';
  });
  assert.equal(update, undefined,
    'must not overwrite when the hydrated description already matches');
});

test('_compactHydrateTasksMatching fetches every non-loaded match and dedups', () => {
  const { context, sandbox } = createCompactContext({ flag: 'compact-v1' });
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      a: { id: 'a', agent_id: 'agent-1', lane: 'In Progress' },
      b: { id: 'b', agent_id: 'agent-1', lane: 'To Do' },
      c: { id: 'c', agent_id: 'agent-2', lane: 'To Do' },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  const fired = run(context, `
    _compactHydrateTasksMatching(function(t) { return t.agent_id === 'agent-1'; })
  `);
  assert.equal(fired, 2);
  assertPlainEqual(
    sandbox.sendCalls.map(function(c) { return c.id; }),
    ['a', 'b']);

  // Second call is a no-op: both are in-flight already.
  sandbox.sendCalls.length = 0;
  run(context, `
    _compactHydrateTasksMatching(function(t) { return t.agent_id === 'agent-1'; })
  `);
  assertPlainEqual(sandbox.sendCalls, []);

  // After one resolves, the registry latches; a third call skips it.
  run(context, `_compactHandleLazyResponse({
    type: 'task_detail', id: 'a', task: { id: 'a', description: 'done' }
  })`);
  sandbox.sendCalls.length = 0;
  run(context, `
    _compactHydrateTasksMatching(function(t) { return t.agent_id === 'agent-1'; })
  `);
  assertPlainEqual(sandbox.sendCalls, []);
});

test('_compactHydrateTasksMatching is a no-op outside compact mode', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = {
    board_tasks: { a: { id: 'a' } },
  };
  assert.equal(
    run(context, `_compactHydrateTasksMatching(function() { return true; })`),
    0);
  assertPlainEqual(sandbox.sendCalls, []);
});

test('archived_tasks merge layers over local compact summaries without loss', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = {
    snapshot_protocol: 'compact-v1',
    board_tasks: {
      't-arch': {
        id: 't-arch', task: 'old', lane: 'Archived', position: 9,
        status: 'Done',
      },
    },
  };
  run(context, `_compactInitDeferredMaps()`);
  run(context, `_compactHandleLazyResponse({
    type: 'archived_tasks',
    group: 'alpha',
    board_tasks: {
      't-arch': { id: 't-arch', task: 'old', description: 'archive hist',
                  messages: [{message: 'closed'}] }
    }
  })`);
  var t = sandbox.state.board_tasks['t-arch'];
  assert.equal(t.description, 'archive hist');
  // Local compact fields survive the merge.
  assert.equal(t.status, 'Done');
  assert.equal(t.position, 9);
  assert.equal(t.lane, 'Archived');
});

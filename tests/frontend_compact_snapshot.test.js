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
  if (flag) localStorageState.set('loom:snapshot_protocol', flag);
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

test('_compactPrepareWSUrl is a no-op when the feature flag is unset', () => {
  const { context } = createCompactContext();
  assert.equal(
    run(context, `_compactPrepareWSUrl('ws://localhost:18932/ws')`),
    'ws://localhost:18932/ws');
  assert.equal(run(context, `_compactFlagEnabled()`), false);
});

test('_compactPrepareWSUrl appends ?compact=1 when flag is compact-v1', () => {
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
  assertPlainEqual(sandbox.state.weaver_journal, {});
  assertPlainEqual(sandbox.state.weaver_worklog, {});
  assertPlainEqual(sandbox.state.weaver_streams, {});
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

test('lazyLoadWeaverJournal requires a group and dedups per group', () => {
  const { context, sandbox } = createCompactContext();
  sandbox.state = { snapshot_protocol: 'compact-v1' };
  run(context, `_compactInitDeferredMaps()`);
  assert.equal(run(context, `lazyLoadWeaverJournal('')`), false);
  assert.equal(run(context, `lazyLoadWeaverJournal('alpha')`), true);
  assert.equal(run(context, `lazyLoadWeaverJournal('alpha')`), false);
  assertPlainEqual(sandbox.sendCalls, [
    { cmd: 'weaver_journal_snapshot', group: 'alpha' },
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

test('_compactHandleLazyResponse merges archived_tasks and weaver_journal_snapshot', () => {
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
    type: 'weaver_journal_snapshot',
    group: 'alpha',
    weaver_journal: { alpha: [{ id: 1, entry: 'j1' }] },
    weaver_worklog: { alpha: [{ id: 2, entry: 'w1' }] },
    weaver_streams: { alpha: { count: 3, items: [] } }
  })`);
  assertPlainEqual(sandbox.state.weaver_journal.alpha,
    [{ id: 1, entry: 'j1' }]);
  assertPlainEqual(sandbox.state.weaver_worklog.alpha,
    [{ id: 2, entry: 'w1' }]);
  assertPlainEqual(sandbox.state.weaver_streams.alpha,
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

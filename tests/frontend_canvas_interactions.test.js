/*
 * Agent-canvas interaction + tree-model regression coverage.
 *
 * Companion to frontend_canvas_context_menu.test.js, which covers only the
 * context-menu rendering path. This file covers the two other load-bearing
 * pieces of static/js/canvas.js that were previously untested:
 *
 *   1. _canvasBuildTrees(groupName) — the pure/deterministic tree model
 *      that drives the entire canvas hierarchy (architect -> engineer ->
 *      worker, plus loose engineers and loose workers).
 *   2. _canvasAttachInteractions(groupName) — click/dblclick/contextmenu
 *      routing dispatched off [data-canvas-card-id] cards.
 *
 * Harness/setup mirrors frontend_canvas_context_menu.test.js (same vm
 * sandbox + loadScript bootstrap). canvas.js is the only production module
 * required: the tree model is pure over `state`, and the interaction
 * handlers route to functions we stub (onAgentClick / focusAgent /
 * _canvasShowCardMenu / _canvasShowEmptyMenu).
 */

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
      groups: {},
      group_settings: {},
      engineer_settings: {},
      agent_digest_settings: {},
    },
    esc(value) { return String(value); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function createContext() {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  // Match webview load order: canvas is declared before command helpers.
  // For these tests only canvas.js is needed; the interaction handlers
  // route to functions we stub per-test.
  loadScript(context, 'static/js/canvas.js');
  return context;
}

/* -- Agent factories (mirror frontend_stratified_grid.test.js shapes) --- */

function architect(id, name, createdAt) {
  return {
    id, name, slug: id, kind: 'architect', group: 'alpha',
    cell_type: 'agent', status: 'running', created_at: createdAt || 1,
  };
}

function engineer(id, name, hiredBy, createdAt) {
  return {
    id, name, slug: id, kind: 'engineer',
    hired_by_architect_id: hiredBy || '', group: 'alpha',
    cell_type: 'agent', status: 'running', created_at: createdAt || 1,
  };
}

function worker(id, name, ownerEngineer, createdAt) {
  const w = {
    id, name, slug: id, kind: 'worker', group: 'alpha',
    cell_type: 'agent', status: 'running', created_at: createdAt || 1,
  };
  if (ownerEngineer !== undefined) w.owner_engineer_id = ownerEngineer;
  return w;
}

function seed(context, cells) {
  for (const cell of cells) {
    context.state.agents[cell.id] = cell;
  }
}

function buildTrees(context, groupName) {
  // Round-trip through JSON so the returned model lives in the test realm
  // (cross-realm arrays/objects from the vm fail assert/strict deepEqual's
  // prototype/reference checks).
  const json = vm.runInContext(
    `JSON.stringify(_canvasBuildTrees(${JSON.stringify(groupName)}))`,
    context,
  );
  return JSON.parse(json);
}

// Count how many times a given card id appears anywhere in the assembled
// model (architect spines, hired-engineer rows + their workers, loose
// engineers + their workers, and the loose-workers bar). Used as a
// belt-and-suspenders dedupe check — every agent should appear exactly once.
function countCardOccurrences(model, id) {
  let n = 0;
  for (const tree of model.trees) {
    if (tree.architect && tree.architect.id === id) n++;
    for (const row of tree.engineers) {
      if (row.engineer && row.engineer.id === id) n++;
      for (const w of row.workers) if (w.id === id) n++;
    }
  }
  for (const row of model.standalone.engineers) {
    if (row.engineer && row.engineer.id === id) n++;
    for (const w of row.workers) if (w.id === id) n++;
  }
  for (const w of model.standalone.workers) if (w.id === id) n++;
  return n;
}

/* -- Tree model: _canvasBuildTrees -------------------------------------- */

test('canvas tree model nests hired engineers and their owned workers under each architect', () => {
  const context = createContext();
  seed(context, [
    architect('arch-1', 'Architect One', 1),
    engineer('eng-1', 'Engineer One', 'arch-1', 2),
    worker('worker-1', 'Worker One', 'eng-1', 3),
    worker('worker-2', 'Worker Two', 'eng-1', 4),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.equal(model.trees.length, 1);
  const tree = model.trees[0];
  assert.equal(tree.architect.id, 'arch-1');
  assert.equal(tree.engineers.length, 1);
  assert.equal(tree.engineers[0].engineer.id, 'eng-1');
  assert.deepEqual(
    tree.engineers[0].workers.map((w) => w.id),
    ['worker-1', 'worker-2'],
  );
  assert.equal(model.standalone.engineers.length, 0);
  assert.equal(model.standalone.workers.length, 0);
});

test('canvas tree model classifies a worker by owner_engineer_id OR created_by_engineer_id', () => {
  const context = createContext();
  const legacyWorker = worker('worker-legacy', 'Legacy Worker', undefined, 3);
  // No owner_engineer_id; only the legacy created_by_engineer_id link.
  legacyWorker.created_by_engineer_id = 'eng-1';
  seed(context, [
    architect('arch-1', 'Architect One', 1),
    engineer('eng-1', 'Engineer One', 'arch-1', 2),
    legacyWorker,
  ]);

  const model = buildTrees(context, 'alpha');

  assert.deepEqual(
    model.trees[0].engineers[0].workers.map((w) => w.id),
    ['worker-legacy'],
  );
  assert.equal(model.standalone.workers.length, 0);
});

test('canvas tree model places a loose engineer (no architect) and its workers in standalone.engineers', () => {
  const context = createContext();
  seed(context, [
    engineer('eng-loose', 'Loose Engineer', '', 1),
    worker('worker-loose', 'Loose-engineer Worker', 'eng-loose', 2),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.equal(model.trees.length, 0);
  assert.equal(model.standalone.engineers.length, 1);
  assert.equal(model.standalone.engineers[0].engineer.id, 'eng-loose');
  assert.deepEqual(
    model.standalone.engineers[0].workers.map((w) => w.id),
    ['worker-loose'],
  );
  assert.equal(model.standalone.workers.length, 0);
});

test('canvas tree model renders a present-loose-engineer worker exactly once even when the worker is iterated before its engineer', () => {
  // `all` follows state.agents insertion order, which is NOT guaranteed to
  // be engineer-first across snapshot/delta/resync. Seed the worker BEFORE
  // its loose (no-architect) engineer: the worker is iterated first in the
  // standalone loop and must NOT be pushed to standalone.workers, because
  // the engineer attaches it under standalone.engineers[].workers. A double
  // push would render the same data-canvas-card-id twice.
  const context = createContext();
  seed(context, [
    worker('worker-before', 'Worker Before Engineer', 'eng-loose', 1),
    engineer('eng-loose', 'Loose Engineer', '', 2),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.equal(model.standalone.engineers.length, 1);
  assert.equal(model.standalone.engineers[0].engineer.id, 'eng-loose');
  assert.deepEqual(
    model.standalone.engineers[0].workers.map((w) => w.id),
    ['worker-before'],
  );
  // Must appear under the engineer only — never duplicated in the bar.
  assert.equal(model.standalone.workers.length, 0);
  // Belt-and-suspenders: the worker id occurs exactly once across the whole
  // model (a double-add would produce a duplicate data-canvas-card-id).
  assert.equal(countCardOccurrences(model, 'worker-before'), 1);
});

test('canvas tree model surfaces orphaned (owner-less) workers in standalone.workers', () => {
  const context = createContext();
  seed(context, [
    worker('worker-orphan-a', 'Orphan A', '', 1),
    worker('worker-orphan-b', 'Orphan B', undefined, 2),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.equal(model.trees.length, 0);
  assert.equal(model.standalone.engineers.length, 0);
  assert.deepEqual(
    model.standalone.workers.map((w) => w.id),
    ['worker-orphan-a', 'worker-orphan-b'],
  );
});

test('canvas tree model surfaces a worker whose owning engineer is absent in the loose-workers bar (not dropped)', () => {
  // owner_engineer_id points at an engineer that is NOT in the group/tree
  // (e.g. tombstoned or out-of-group). The worker itself exists, so it must
  // never silently vanish — it falls through to the loose-workers bar, the
  // same surface used for genuinely owner-less workers.
  const context = createContext();
  const tombstonedOwner = engineer('eng-gone', 'Tombstoned Engineer', 'arch-1', 2);
  tombstonedOwner.deleted_at = 1700000000;
  seed(context, [
    architect('arch-1', 'Architect One', 1),
    tombstonedOwner,
    worker('worker-orphaned-owner', 'Orphaned-owner Worker', 'eng-gone', 3),
  ]);

  const model = buildTrees(context, 'alpha');

  // The tombstoned owner engineer is excluded from the tree...
  assert.equal(model.trees.length, 1);
  assert.equal(model.trees[0].engineers.length, 0);
  assert.equal(model.standalone.engineers.length, 0);
  // ...but its orphaned-owner worker still appears in the loose-workers bar
  // exactly once (not dropped, not duplicated).
  assert.deepEqual(
    model.standalone.workers.map((w) => w.id),
    ['worker-orphaned-owner'],
  );
  assert.equal(countCardOccurrences(model, 'worker-orphaned-owner'), 1);
});

test('canvas tree model excludes tombstoned agents from trees and standalone buckets', () => {
  const context = createContext();
  const deadArch = architect('arch-dead', 'Dead Architect', 1);
  deadArch.deleted_at = 1700000000;
  const deadWorker = worker('worker-dead', 'Dead Worker', '', 5);
  deadWorker.deleted_at = 1700000001;
  seed(context, [
    deadArch,
    architect('arch-live', 'Live Architect', 2),
    engineer('eng-1', 'Engineer One', 'arch-live', 3),
    deadWorker,
    worker('worker-live', 'Live Worker', '', 6),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.deepEqual(model.trees.map((t) => t.architect.id), ['arch-live']);
  assert.deepEqual(model.standalone.workers.map((w) => w.id), ['worker-live']);
  // The tombstoned architect and worker must not appear anywhere.
  const allIds = [
    ...model.trees.map((t) => t.architect.id),
    ...model.standalone.engineers.map((r) => r.engineer.id),
    ...model.standalone.workers.map((w) => w.id),
  ];
  assert.equal(allIds.includes('arch-dead'), false);
  assert.equal(allIds.includes('worker-dead'), false);
});

test('canvas tree model excludes non-agent cells (e.g. terminals) and out-of-group agents', () => {
  const context = createContext();
  const terminalCell = {
    id: 'term-1', name: 'Terminal', kind: 'terminal',
    group: 'alpha', cell_type: 'terminal', status: 'running', created_at: 1,
  };
  const otherGroupWorker = worker('worker-other', 'Other Group', '', 2);
  otherGroupWorker.group = 'beta';
  seed(context, [
    terminalCell,
    otherGroupWorker,
    worker('worker-alpha', 'Alpha Worker', '', 3),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.deepEqual(model.standalone.workers.map((w) => w.id), ['worker-alpha']);
  assert.equal(model.trees.length, 0);
  assert.equal(model.standalone.engineers.length, 0);
});

test('canvas tree model orders architects, engineers, and workers by created_at then id', () => {
  const context = createContext();
  seed(context, [
    architect('arch-late', 'Late Architect', 20),
    architect('arch-early', 'Early Architect', 10),
    engineer('eng-b', 'Engineer B', 'arch-early', 5),
    engineer('eng-a', 'Engineer A', 'arch-early', 5), // tie on created_at -> id order
    worker('worker-z', 'Worker Z', 'eng-a', 8),
    worker('worker-y', 'Worker Y', 'eng-a', 7),
  ]);

  const model = buildTrees(context, 'alpha');

  assert.deepEqual(
    model.trees.map((t) => t.architect.id),
    ['arch-early', 'arch-late'],
  );
  // eng-a and eng-b tie on created_at (5); id ordering breaks the tie.
  assert.deepEqual(
    model.trees[0].engineers.map((r) => r.engineer.id),
    ['eng-a', 'eng-b'],
  );
  const engA = model.trees[0].engineers.find((r) => r.engineer.id === 'eng-a');
  assert.deepEqual(engA.workers.map((w) => w.id), ['worker-y', 'worker-z']);
});

test('canvas tree model returns empty buckets when state has no agents', () => {
  const context = createContext();
  const model = buildTrees(context, 'alpha');
  assert.deepEqual(model.trees, []);
  assert.deepEqual(model.standalone.engineers, []);
  assert.deepEqual(model.standalone.workers, []);
});

/* -- Interaction routing: _canvasAttachInteractions --------------------- */

/* Minimal synthetic-event harness. _canvasAttachInteractions registers
 * contextmenu/click/dblclick listeners on the `.agent-canvas` root and
 * dispatches off e.target.closest('[data-canvas-card-id]'). We capture the
 * registered handlers and fire hand-built events at them. */
function attachInteractionHarness(context, opts) {
  const options = opts || {};
  const handlers = {};
  const root = {
    addEventListener(type, fn) { handlers[type] = fn; },
  };
  context.document = {
    querySelector(sel) { return sel === '.agent-canvas' ? root : null; },
  };

  const calls = { onAgentClick: [], focusAgent: [], cardMenu: [], emptyMenu: [] };
  if (options.withOnAgentClick !== false) {
    context.onAgentClick = function(id) { calls.onAgentClick.push(id); };
  } else {
    context.onAgentClick = undefined;
  }
  context.focusAgent = function(id) { calls.focusAgent.push(id); };
  // Stub the menu entry points: their content is covered by
  // frontend_canvas_context_menu.test.js; here we only assert routing.
  context._canvasShowCardMenu = function(x, y, kind, id, groupName) {
    calls.cardMenu.push({ x, y, kind, id, groupName });
  };
  context._canvasShowEmptyMenu = function(x, y, groupName) {
    calls.emptyMenu.push({ x, y, groupName });
  };

  vm.runInContext("_canvasAttachInteractions('alpha')", context);
  return { handlers, calls };
}

function cardTarget(id, kind) {
  const card = {
    getAttribute(name) {
      if (name === 'data-canvas-card-id') return id;
      if (name === 'data-canvas-card-kind') return kind || '';
      return null;
    },
  };
  // closest('[data-canvas-card-id]') and the contextmenu selector both
  // contain "data-canvas-card-id"; either resolves to this card.
  return {
    closest(sel) {
      if (String(sel).includes('data-canvas-card')) return card;
      return null;
    },
  };
}

function nonCardTarget() {
  return { closest() { return null; } };
}

function fireEvent(handler, target, extra) {
  const event = Object.assign({
    target,
    clientX: 12,
    clientY: 34,
    preventDefault() { event._prevented = true; },
  }, extra || {});
  handler(event);
  return event;
}

test('canvas click on a card routes to onAgentClick with the card id', () => {
  const context = createContext();
  const { handlers, calls } = attachInteractionHarness(context);

  fireEvent(handlers.click, cardTarget('worker-1', 'worker'));

  assert.deepEqual(calls.onAgentClick, ['worker-1']);
  assert.deepEqual(calls.focusAgent, []);
});

test('canvas click falls back to focusAgent when onAgentClick is absent', () => {
  const context = createContext();
  const { handlers, calls } = attachInteractionHarness(context, {
    withOnAgentClick: false,
  });

  fireEvent(handlers.click, cardTarget('eng-1', 'engineer'));

  assert.deepEqual(calls.onAgentClick, []);
  assert.deepEqual(calls.focusAgent, ['eng-1']);
});

test('canvas dblclick on a card routes to focusAgent', () => {
  const context = createContext();
  const { handlers, calls } = attachInteractionHarness(context);

  fireEvent(handlers.dblclick, cardTarget('arch-1', 'architect'));

  assert.deepEqual(calls.focusAgent, ['arch-1']);
  assert.deepEqual(calls.onAgentClick, []);
});

test('canvas click and dblclick on a non-card target are no-ops', () => {
  const context = createContext();
  const { handlers, calls } = attachInteractionHarness(context);

  fireEvent(handlers.click, nonCardTarget());
  fireEvent(handlers.dblclick, nonCardTarget());

  assert.deepEqual(calls.onAgentClick, []);
  assert.deepEqual(calls.focusAgent, []);
});

test('canvas contextmenu routes cards to the card menu and empty space to the empty menu', () => {
  const context = createContext();
  const { handlers, calls } = attachInteractionHarness(context);

  const cardEvent = fireEvent(handlers.contextmenu, cardTarget('worker-1', 'worker'));
  assert.equal(cardEvent._prevented, true);
  assert.deepEqual(calls.cardMenu, [
    { x: 12, y: 34, kind: 'worker', id: 'worker-1', groupName: 'alpha' },
  ]);
  assert.deepEqual(calls.emptyMenu, []);

  const emptyEvent = fireEvent(handlers.contextmenu, nonCardTarget());
  assert.equal(emptyEvent._prevented, true);
  assert.deepEqual(calls.emptyMenu, [{ x: 12, y: 34, groupName: 'alpha' }]);
  assert.equal(calls.cardMenu.length, 1);
});

test('canvas attach is a no-op when there is no .agent-canvas root', () => {
  const context = createContext();
  context.document = { querySelector() { return null; } };
  context.onAgentClick = function() { throw new Error('should not be called'); };
  context.focusAgent = function() { throw new Error('should not be called'); };
  // Must not throw despite the absent root.
  vm.runInContext("_canvasAttachInteractions('alpha')", context);
});

/* -- View-mode persistence (smoke only) --------------------------------- */

test('toggleAgentCanvasView flips persisted grid <-> canvas mode (smoke)', () => {
  const context = createContext();
  const store = {};
  context.localStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem(k, v) { store[k] = String(v); },
  };
  context.document = { querySelectorAll() { return []; } };
  context.render = function() {};

  assert.equal(vm.runInContext('_torqueAgentViewMode()', context), 'grid');
  vm.runInContext('toggleAgentCanvasView()', context);
  assert.equal(vm.runInContext('_torqueAgentViewMode()', context), 'canvas');
  vm.runInContext('toggleAgentCanvasView()', context);
  assert.equal(vm.runInContext('_torqueAgentViewMode()', context), 'grid');
});

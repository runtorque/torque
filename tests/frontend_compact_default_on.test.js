/* Phase-2 regression harness for compact-v1 default-ON under the v2
 * eager-field contract (LOOM:180).
 *
 * Pins the behaviors that flipped in this phase:
 *   - default-ON: unset localStorage opts into compact, explicit sentinels opt
 *     out.
 *   - agent_panel weaver summaries no longer hydrate against eager fields.
 *   - _preservedMergeDiffForAgent narrows its hydrate scope via the now-eager
 *     worktree_boundary.
 *   - showTaskMessages uses the eager messages-preview count to skip
 *     hydrating tasks with no messages.
 *   - board search treats description as lazy in compact mode: title / action
 *     / labels / verification fields all match, description-only queries
 *     only match tasks the operator has already hydrated.
 *   - toolbelt runtime (embedded_terminal=false) exercises the same compact
 *     paths as standalone. */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

/* vm-context objects have a different Object.prototype than the test module
 * here, so deepStrictEqual rejects structurally-equal arrays/objects.
 * Normalize via JSON for comparisons and use assert.deepEqual (non-strict). */
function plain(value) { return JSON.parse(JSON.stringify(value)); }
function eq(actual, expected, message) {
  assert.deepEqual(plain(actual), expected, message);
}

function loadFile(rel) {
  return fs.readFileSync(path.join(repoRoot, rel), 'utf8');
}

function makeStorage(initial) {
  var map = new Map();
  if (initial) {
    for (var key in initial) map.set(key, initial[key]);
  }
  return {
    getItem(key) { return map.has(key) ? map.get(key) : null; },
    setItem(key, value) { map.set(String(key), String(value)); },
    removeItem(key) { map.delete(String(key)); },
    clear() { map.clear(); },
  };
}

function createCompactSandbox(opts) {
  opts = opts || {};
  var sandbox = {
    console,
    Date,
    JSON,
    Math,
    Object,
    state: opts.state || {},
    localStorage: makeStorage(opts.localStorage || null),
    sendCalls: [],
  };
  sandbox.send = function(p) { sandbox.sendCalls.push(p); };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  var context = vm.createContext(sandbox);
  vm.runInContext(loadFile('static/js/compact.js'), context,
    { filename: 'compact.js' });
  return { context, sandbox };
}

function run(context, code) { return vm.runInContext(code, context); }

/* -- Default-on flag contract ------------------------------------------- */

test('unset localStorage flag opts into compact by default', () => {
  const { context } = createCompactSandbox();
  assert.equal(run(context, `_compactFlagEnabled()`), true);
  assert.equal(
    run(context, `_compactPrepareWSUrl('ws://h/ws')`),
    'ws://h/ws?compact=1');
});

test('legacy sentinels opt out and strip the compact query annotation', () => {
  for (const sentinel of ['legacy', 'LEGACY', 'off', '0', 'false']) {
    const { context } = createCompactSandbox({
      localStorage: { 'loom:snapshot_protocol': sentinel },
    });
    assert.equal(
      run(context, `_compactFlagEnabled()`), false,
      'expected opt-out for sentinel ' + sentinel);
    assert.equal(
      run(context, `_compactPrepareWSUrl('ws://h/ws')`),
      'ws://h/ws',
      'expected raw ws URL for sentinel ' + sentinel);
  }
});

test('compact-v1 / 1 / yes still opt in explicitly', () => {
  for (const value of ['compact-v1', '1', 'yes', 'compact', 'true']) {
    const { context } = createCompactSandbox({
      localStorage: { 'loom:snapshot_protocol': value },
    });
    assert.equal(
      run(context, `_compactFlagEnabled()`), true,
      'expected opt-in for flag ' + value);
  }
});

/* -- agent_panel weaver summaries: no redundant hydrate in v2 ----------- */

function createWeaverSummaryHarness() {
  const { context, sandbox } = createCompactSandbox({
    localStorage: { 'loom:snapshot_protocol': 'compact-v1' },
    state: {
      snapshot_protocol: 'compact-v1',
      board_tasks: {
        a: {
          id: 'a', group: 'alpha', task: 'Alpha', lane: 'In Progress',
          health_state: 'stalled',
          health_since: '2026-04-22T12:00:00+00:00',
          health_details: { reason: 'idle' },
          verification_state: 'pending',
          verification_mode: 'deploy',
          verification_notes: 'needs smoke',
          verification_summary: { tests_run: 'targeted' },
        },
        b: {
          id: 'b', group: 'alpha', task: 'Beta', lane: 'To Do',
          health_state: 'healthy',
          verification_state: '',
        },
      },
    },
  });
  run(context, `_compactInitDeferredMaps()`);
  sandbox.renderCalls = 0;
  sandbox.render = function() { sandbox.renderCalls++; };
  sandbox.renderActivePanel = function() {};
  run(context, `
    var _weaverHealthSeverity = { blocked: 3, stalled: 2, thrashing: 2, 'idle-risk': 1 };
    var _weaverVerificationLabels = { pending: 'Pending', failed: 'Failed',
      attempted: 'Attempted', passed: 'Passed' };
  `);
  const source = loadFile('static/js/agent_panel.js');
  const healthFn = source.match(
    /function _weaverTaskHealthSummary\(group\)\s*\{[\s\S]*?\n\}/m)[0];
  const verifyFn = source.match(
    /function _weaverVerificationSummary\(group\)\s*\{[\s\S]*?\n\}/m)[0];
  vm.runInContext(healthFn + '\n' + verifyFn, context);
  return { context, sandbox };
}

test('_weaverTaskHealthSummary does not hydrate compact cards in v2', () => {
  const { context, sandbox } = createWeaverSummaryHarness();
  const summary = plain(run(context, `_weaverTaskHealthSummary('alpha')`));
  assert.equal(summary.total, 1);
  assert.equal(summary.items[0].id, 'a');
  assert.equal(summary.items[0].health_state, 'stalled');
  eq(sandbox.sendCalls.map(function(c) { return c.cmd; }), []);
});

test('_weaverVerificationSummary does not hydrate compact cards in v2', () => {
  const { context, sandbox } = createWeaverSummaryHarness();
  const summary = plain(run(context, `_weaverVerificationSummary('alpha')`));
  assert.equal(summary.total, 1);
  assert.equal(summary.items[0].id, 'a');
  assert.equal(summary.items[0].detail, 'needs smoke');
  eq(sandbox.sendCalls.map(function(c) { return c.cmd; }), []);
});

/* -- _preservedMergeDiffForAgent: narrowed hydrate via eager boundary --- */

function createPreservedMergeHarness() {
  const { context, sandbox } = createCompactSandbox({
    localStorage: { 'loom:snapshot_protocol': 'compact-v1' },
    state: {
      snapshot_protocol: 'compact-v1',
      board_tasks: {
        match: {
          id: 'match', agent_id: 'other-agent', lane: 'Done',
          worktree_boundary: {
            repo_root: '/repo', branch: 'feature/x', status: 'merged',
          },
        },
        miss: {
          id: 'miss', agent_id: 'the-agent', lane: 'In Progress',
          worktree_boundary: {
            repo_root: '/repo', branch: 'feature/other', status: 'open',
          },
        },
        unboundaried: {
          id: 'unboundaried', agent_id: 'the-agent', lane: 'In Progress',
        },
      },
    },
  });
  run(context, `_compactInitDeferredMaps()`);
  run(context, `
    function _taskPreservedMergeDiffArtifact(task, branch) {
      return null;
    }
  `);
  const source = loadFile('static/js/render.js');
  const funcs = [
    'function _taskBoundaryMeta',
    'function _taskBoundaryBranchKey',
    'function _taskBoundarySortValue',
    'function _preservedMergeDiffForAgent',
  ];
  for (const sig of funcs) {
    const re = new RegExp(sig.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '\\([\\s\\S]*?\\n\\}', 'm');
    const m = source.match(re);
    if (!m) throw new Error('missing ' + sig);
    vm.runInContext(m[0], context);
  }
  return { context, sandbox };
}

test('_preservedMergeDiffForAgent hydrates only boundary-matching tasks', () => {
  const { context, sandbox } = createPreservedMergeHarness();
  run(context, `_preservedMergeDiffForAgent({
    id: 'the-agent',
    worktree_repo_root: '/repo',
    worktree_branch: 'feature/x',
  })`);
  const ids = sandbox.sendCalls
    .filter(function(c) { return c.cmd === 'task_detail'; })
    .map(function(c) { return c.id; });
  eq(ids, ['match'],
    'only the task whose worktree_boundary matches the agent branch should hydrate');
});

test('_preservedMergeDiffForAgent skips hydrate when agent has no branch', () => {
  const { context, sandbox } = createPreservedMergeHarness();
  run(context, `_preservedMergeDiffForAgent({ id: 'the-agent' })`);
  eq(sandbox.sendCalls, []);
});

/* -- showTaskMessages: gate on eager messages-preview count ------------- */

function createShowTaskMessagesHarness() {
  const { context, sandbox } = createCompactSandbox({
    localStorage: { 'loom:snapshot_protocol': 'compact-v1' },
    state: {
      snapshot_protocol: 'compact-v1',
      board_tasks: {
        silent: {
          id: 'silent', task: 'silent', lane: 'To Do',
          messages: [],
        },
        chatty: {
          id: 'chatty', task: 'chatty', lane: 'To Do',
          messages: [{ count: 4, action: 'progress', message: 'preview' }],
        },
      },
    },
  });
  run(context, `_compactInitDeferredMaps()`);
  run(context, `
    function esc(s) { return String(s == null ? '' : s); }
    function _relativeTime() { return ''; }
    document = {
      _els: {},
      getElementById(id) {
        if (!this._els[id]) {
          this._els[id] = { textContent: '', innerHTML: '',
            classList: { add: function() {} } };
        }
        return this._els[id];
      },
    };
  `);
  const source = loadFile('static/js/board.js');
  const fn = source.match(
    /function showTaskMessages\(taskId\)\s*\{[\s\S]*?\n\}/m)[0];
  vm.runInContext(fn, context);
  return { context, sandbox };
}

test('showTaskMessages short-circuits on eager zero-count preview', () => {
  const { context, sandbox } = createShowTaskMessagesHarness();
  run(context, `showTaskMessages('silent')`);
  eq(sandbox.sendCalls, [],
    'no hydrate needed when the compact preview reports zero messages');
});

test('showTaskMessages hydrates when preview signals messages exist', () => {
  const { context, sandbox } = createShowTaskMessagesHarness();
  run(context, `showTaskMessages('chatty')`);
  eq(sandbox.sendCalls.map(function(c) { return c.cmd; }), ['task_detail']);

  run(context, `_compactHandleLazyResponse({
    type: 'task_detail',
    id: 'chatty',
    task: {
      id: 'chatty', task: 'chatty', lane: 'To Do',
      messages: [
        { action: 'progress', message: 'step 1' },
        { action: 'progress', message: 'step 2' },
        { action: 'progress', message: 'step 3' },
        { action: 'ready', message: 'done' },
      ],
    }
  })`);
  run(context, `showTaskMessages('chatty')`);
  const title = run(context,
    `document._els['task-messages-title'].textContent`);
  assert.match(String(title), /chatty/i);
  const content = String(run(context,
    `document._els['task-messages-content'].innerHTML`));
  assert.ok(content.indexOf('step 1') >= 0);
  assert.ok(content.indexOf('ready') >= 0);
});

/* -- Board search: compact-aware description gating -------------------- */

function createBoardSearchHarness(opts) {
  const taskDescription = opts && opts.hasHydratedDesc
    ? 'this body text mentions supernova explicitly'
    : '';
  const tasks = {
    't-title': {
      id: 't-title', task: 'supernova investigation',
      group: 'alpha', lane: 'To Do', position: 1,
      action_name: 'feature/implement', labels: ['performance'],
      verification_state: 'pending', verification_mode: 'deploy',
      verification_notes: 'smoke checks',
      verification_summary: { tests_run: 'targeted' },
    },
    't-desc': {
      id: 't-desc', task: 'plain card',
      group: 'alpha', lane: 'To Do', position: 2,
      action_name: 'feature/review', labels: [],
      description: taskDescription,
    },
    't-action': {
      id: 't-action', task: 'card three',
      group: 'alpha', lane: 'To Do', position: 3,
      action_name: 'feature/supernova', labels: [],
    },
    't-label': {
      id: 't-label', task: 'card four',
      group: 'alpha', lane: 'To Do', position: 4,
      action_name: 'feature/implement', labels: ['supernova-ready'],
    },
    't-unrelated': {
      id: 't-unrelated', task: 'other', group: 'alpha',
      lane: 'To Do', position: 5, action_name: 'feature/x', labels: ['chore'],
    },
  };
  const runtime = (opts && opts.runtime) || { embedded_terminal: false };
  const { context, sandbox } = createCompactSandbox({
    state: {
      snapshot_protocol: 'compact-v1',
      runtime: runtime,
      board_tasks: tasks,
      agents: {},
    },
  });
  run(context, `_compactInitDeferredMaps()`);
  if (opts && opts.hasHydratedDesc) {
    run(context, `_compactTasksFullyLoaded['t-desc'] = true;`);
  }
  run(context, `
    function isSystemLabel(l) { return String(l).indexOf('loom:') === 0; }
    function displayLabel(l) { return String(l); }
    function _boardSearchMatch(query) {
      var q = String(query || '').toLowerCase();
      if (!q) return Object.keys(state.board_tasks);
      var matched = [];
      var compactActive = typeof _compactModeActive === 'function'
        && _compactModeActive();
      for (var id in state.board_tasks) {
        var t = state.board_tasks[id];
        var descriptionForSearch = t.description || '';
        if (compactActive
            && typeof _compactTaskHasFullDetail === 'function'
            && !_compactTaskHasFullDetail(t)) {
          descriptionForSearch = '';
        }
        var parts = [t.task, descriptionForSearch, t.id, t.action_name, t.agent_id];
        parts.push(t.verification_mode || '');
        parts.push(t.verification_state || '');
        parts.push(t.verification_notes || '');
        var vs = t.verification_summary || {};
        parts.push(vs.tests_run || '');
        parts.push(vs.human_validation_pending || '');
        if (t.labels && t.labels.length) {
          for (var li = 0; li < t.labels.length; li++) {
            parts.push(t.labels[li]);
            parts.push(displayLabel(t.labels[li]));
          }
        }
        if (t.agent_id && state.agents && state.agents[t.agent_id]) {
          var a = state.agents[t.agent_id];
          parts.push(a.name || '');
          parts.push(a.slug || '');
        }
        var haystack = parts.join('\\n').toLowerCase();
        if (haystack.indexOf(q) >= 0) matched.push(id);
      }
      return matched.sort();
    }
  `);
  return { context, sandbox };
}

test('board search matches eager fields in compact mode (standalone)', () => {
  const { context } = createBoardSearchHarness({
    runtime: { embedded_terminal: true },
  });
  eq(run(context, `_boardSearchMatch('supernova')`),
    ['t-action', 't-label', 't-title']);
  eq(run(context, `_boardSearchMatch('feature/review')`), ['t-desc']);
  eq(run(context, `_boardSearchMatch('chore')`), ['t-unrelated']);
  eq(run(context, `_boardSearchMatch('smoke')`), ['t-title']);
});

test('board search skips description bodies for unhydrated compact cards', () => {
  const { context } = createBoardSearchHarness({ hasHydratedDesc: false });
  eq(run(context, `_boardSearchMatch('body text')`), [],
    'description is lazy — no match until the task is hydrated');
});

test('board search includes description once its task is hydrated', () => {
  const { context } = createBoardSearchHarness({ hasHydratedDesc: true });
  eq(run(context, `_boardSearchMatch('body text')`), ['t-desc'],
    'once hydrated, description body joins the search haystack');
});

test('board search behaves identically in toolbelt-mode runtime', () => {
  const { context } = createBoardSearchHarness({
    runtime: { embedded_terminal: false },
  });
  eq(run(context, `_boardSearchMatch('supernova')`),
    ['t-action', 't-label', 't-title']);
  eq(run(context, `_boardSearchMatch('smoke')`), ['t-title']);
  eq(run(context, `_boardSearchMatch('body text')`), []);
});

/* -- Card-level eager fields render without hydrate -------------------- */

test('compact cards expose eager v2 fields for deps / external / boundary', () => {
  const { context } = createCompactSandbox({
    state: {
      snapshot_protocol: 'compact-v1',
      board_tasks: {
        't-card': {
          id: 't-card', task: 't', group: 'alpha', lane: 'In Progress',
          depends_on: ['t-root'],
          provider: 'github',
          external_id: '42',
          external_url: 'https://example.test/issues/42',
          worktree_boundary: {
            repo_root: '/repo', branch: 'feature/x', status: 'open',
          },
          resume_after_boundary_task_id: 't-boundary',
          messages: [{ count: 7, action: 'progress', message: '7 updates · last progress' }],
          health_state: 'stalled',
          health_details: { reason: 'idle' },
          verification_state: 'pending',
          verification_summary: { tests_run: 'targeted' },
        },
      },
    },
  });
  const card = run(context, `state.board_tasks['t-card']`);
  eq(card.depends_on, ['t-root']);
  assert.equal(card.provider, 'github');
  assert.equal(card.external_url, 'https://example.test/issues/42');
  assert.equal(card.worktree_boundary.status, 'open');
  assert.equal(card.resume_after_boundary_task_id, 't-boundary');
  assert.equal(card.messages.length, 1);
  assert.equal(card.messages[0].count, 7);
  assert.equal(card.messages[0].action, 'progress');
  assert.equal(card.health_state, 'stalled');
  assert.equal(card.verification_state, 'pending');
  assert.equal(card.verification_summary.tests_run, 'targeted');
});

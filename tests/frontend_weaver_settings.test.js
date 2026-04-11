const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const intervalCalls = [];
  const clearedIntervals = [];
  const sandbox = {
    console,
    Date: { now: () => Date.now() },
    state: { weaver_settings: {} },
    document: {
      activeElement: null,
      getElementById() { return null; },
    },
    window: {},
    _cachedProviders: [],
    _esc(value) { return String(value); },
    _currentGroup() { return 'alpha'; },
    _captureSurfaceState() { return null; },
    _restoreSurfaceState() {},
    setInterval(fn, delay) {
      const id = intervalCalls.length + 1;
      intervalCalls.push({ id, fn, delay });
      return id;
    },
    clearInterval(id) {
      clearedIntervals.push(id);
    },
    intervalCalls,
    clearedIntervals,
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadWeaver(context) {
  const filename = path.join(repoRoot, 'static/js/weaver.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

test('renderWeaverPanel shows Journal and Events tabs without settings tabs', () => {
  const sandbox = createSandbox();
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-weaver' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext('renderWeaverPanel()', context);

  assert.match(panel.innerHTML, /Weaver — alpha/);
  assert.match(panel.innerHTML, />Journal</);
  assert.match(panel.innerHTML, />Events</);
  assert.match(panel.innerHTML, />Worklog</);
  assert.match(panel.innerHTML, /weaver-tabs/);
  assert.doesNotMatch(panel.innerHTML, />Settings</);
});

test('weaver Events tab renders queued and sent digest sections', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_buffer_stats = {
    alpha: {
      buffered_events: 1,
      next_push_in: 45,
      queued_events: [
        { id: 7, kind: 'task_completed', agent_name: 'Worker', message: 'Waiting for review', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  sandbox.state.weaver_sent_events = {
    alpha: [
      { id: 5, kind: 'task_completed', agent_name: 'Worker', message: 'Merged cleanly', timestamp: 5, delivered_at: 12 },
    ],
  };
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-weaver' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverSelectTab('events')`, context);

  assert.match(panel.innerHTML, /Queued for next digest/);
  assert.match(panel.innerHTML, /Already sent to Weaver/);
  assert.match(panel.innerHTML, /Waiting for review/);
  assert.match(panel.innerHTML, /Merged cleanly/);
  assert.match(panel.innerHTML, /Send queued now/);
});

test('weaver Events countdown updates in place without rerendering the whole panel', () => {
  const sandbox = createSandbox();
  let nowMs = 1_000_000;
  sandbox.Date.now = () => nowMs;
  sandbox.state.weaver_buffer_stats = {
    alpha: {
      buffered_events: 1,
      next_push_in: 54,
      next_push_at: 1054,
      queued_events: [
        { id: 7, kind: 'task_completed', message: 'Queued event', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  const countdown = { textContent: '' };
  let renderCount = 0;
  const panel = {
    querySelector(selector) {
      if (selector === '.weaver-events-countdown') return countdown;
      return null;
    },
  };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      renderCount += 1;
      this._innerHTML = value;
    },
  });
  sandbox.document.getElementById = function(id) {
    return id === 'panel-weaver' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`_weaverActiveTabByGroup.alpha = 'events'; renderWeaverPanel()`, context);

  assert.equal(renderCount, 1);
  assert.equal(sandbox.intervalCalls.length, 1);
  assert.equal(sandbox.intervalCalls[0].delay, 1000);
  assert.equal(countdown.textContent, 'Next eligible send in 54s.');

  nowMs += 4000;
  sandbox.intervalCalls[0].fn();

  assert.equal(renderCount, 1);
  assert.equal(countdown.textContent, 'Next eligible send in 50s.');

  nowMs = 1_055_000;
  sandbox.intervalCalls[0].fn();

  assert.equal(renderCount, 1);
  assert.equal(countdown.textContent, 'Eligible to send now.');
  assert.deepEqual(sandbox.clearedIntervals, [1]);
});

test('weaver Events tab disables send-now while paused', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_settings.alpha = { paused: true };
  sandbox.state.weaver_buffer_stats = {
    alpha: {
      buffered_events: 1,
      next_push_in: 0,
      queued_events: [
        { id: 9, kind: 'task_completed', message: 'Blocked by pause', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(
    `_weaverRenderEvents("alpha", state.weaver_settings.alpha, null, state.weaver_buffer_stats.alpha)`,
    context,
  );

  assert.match(html, /Delivery is paused/);
  assert.match(html, /Send queued now/);
  assert.match(html, /disabled/);
});

test('weaver Worklog tab renders dispatched tasks with live lane and status', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_worklog = {
    alpha: [
      {
        id: 4,
        task_id: 'LOOM:9',
        task_title: 'Add Worklog tab',
        agent_id: 'agent-1',
        agent_name: 'Worker One',
        agent_slug: 'worker-one',
        agent_owned: true,
        started_at: 12,
      },
    ],
  };
  sandbox.state.board_tasks = {
    'LOOM:9': {
      id: 'LOOM:9',
      task: 'Add Worklog tab',
      lane: 'In Progress',
      status: 'In review',
      agent_id: 'agent-1',
    },
  };
  sandbox.state.agents = {
    'agent-1': { id: 'agent-1', name: 'Worker One' },
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(
    `_weaverRenderWorklog("alpha", {})`,
    context,
  );

  assert.match(html, /Dispatched tasks/);
  assert.match(html, /Add Worklog tab/);
  assert.match(html, /In Progress/);
  assert.match(html, /In review/);
  assert.match(html, /Worker One/);
});

test('weaver Worklog tab hides non-owned rows when restrict_to_created_agents is enabled', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_worklog = {
    alpha: [
      {
        id: 2,
        task_id: 'LOOM:2',
        task_title: 'Owned task',
        agent_id: 'agent-owned',
        agent_name: 'Owned Worker',
        agent_owned: true,
        started_at: 20,
      },
      {
        id: 1,
        task_id: 'LOOM:1',
        task_title: 'Legacy task',
        agent_id: 'agent-legacy',
        agent_name: 'Legacy Worker',
        agent_owned: false,
        started_at: 10,
      },
    ],
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(
    `_weaverRenderWorklog("alpha", { restrict_to_created_agents: true })`,
    context,
  );

  assert.match(html, /Owned task/);
  assert.doesNotMatch(html, /Legacy task/);
  assert.match(html, /Weaver-created agents/);
});

test('weaver journal distinguishes blocking asks from non-blocking notes', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_settings.alpha = {
    pending_question: 'Need approval to merge?',
    pending_note: 'FYI: branch is ready for review',
    pending_note_kind: 'question',
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(`_weaverRenderJournal("alpha")`, context);

  assert.match(html, /class="weaver-ask-banner"/);
  assert.match(html, /Weaver is asking:/);
  assert.match(html, /class="weaver-note-banner"/);
  assert.match(html, /Weaver asks \(non-blocking\):/);
  assert.match(html, /weaverDismissNote/);
});

test('weaver journal renders open streams with product, workflow, and context summaries', () => {
  const sandbox = createSandbox();
  sandbox.state.board_tasks = {
    'LOOM:333': { id: 'LOOM:333', task: 'Add Events tab' },
    'LOOM:342': { id: 'LOOM:342', task: 'Keep Weaver Events next-dispatch timing accurate' },
    'LOOM:333:4': { id: 'LOOM:333:4', task: 'Review Events stream' },
    'LOOM:342:1': { id: 'LOOM:342:1', task: 'Validate stream after smoke testing' },
  };
  sandbox.state.weaver_streams = {
    alpha: {
      count: 1,
      by_state: { awaiting_human_validation: 1 },
      items: [
        {
          stream_id: 'stream:/repo::loom/events-panel',
          branch: 'loom/events-panel',
          foreground_task_title: 'Keep Weaver Events next-dispatch timing accurate',
          state: 'awaiting_human_validation',
          code_state: 'reviewed_clean',
          validation_state: 'pending_human_validation',
          merge_state: 'not_ready',
          gate_reason: 'Run manual smoke',
          recommended_next_action: 'merge_after_validation',
          latest_reviewed_commit_sha: 'rev4561234567',
          product_task_ids: ['LOOM:333', 'LOOM:342'],
          workflow_task_ids: ['LOOM:333:4', 'LOOM:342:1'],
          recent_visibility_items: [
            {
              kind: 'agent_reply',
              task_id: 'LOOM:9',
              task_title: 'Weaver: reprioritize blocker fix',
              summary: 'Will handle blocker first',
              timestamp: '2026-04-07T11:12:00+00:00',
            },
            {
              kind: 'weaver_message',
              task_id: 'LOOM:9',
              task_title: 'Weaver: reprioritize blocker fix',
              summary: 'Reprioritized blocker fix before queued work',
              timestamp: '2026-04-07T11:11:00+00:00',
            },
          ],
        },
      ],
    },
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(`_weaverRenderJournal("alpha")`, context);

  assert.match(html, /Open Streams/);
  assert.match(html, /Keep Weaver Events next-dispatch timing accurate/);
  assert.match(html, /events-panel/);
  assert.match(html, /Awaiting validation/);
  assert.match(html, /Not ready to merge/);
  assert.match(html, /Run manual smoke/);
  assert.match(html, /Merge after validation/);
  assert.match(html, /rev4561/);
  assert.match(html, /Add Events tab/);
  assert.match(html, /Keep Weaver Events next-dispatch timing accurate/);
  assert.match(html, /2 workflow tasks/);
  assert.match(html, /Reprioritized blocker fix before queued work/);
  assert.match(html, /weaver-stream-section-label-product/);
  assert.match(html, /weaver-stream-section-label-workflow/);
  assert.match(html, /weaver-stream-section-label-context/);
});

test('weaver journal makes validation-pending streams read as awaiting validation', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_streams = {
    alpha: {
      group: 'alpha',
      count: 1,
      streams: [
        {
          branch: 'loom/modal-polish',
          foreground_task_title: 'Polish modal',
          validation_state: 'pending_human_validation',
          recommended_next_action: 'run_manual_validation',
        },
      ],
    },
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(`_weaverRenderJournal("alpha")`, context);

  assert.match(html, /Polish modal/);
  assert.match(html, /modal-polish/);
  assert.match(html, /Awaiting validation/);
  assert.match(html, /Run manual validation/);
  assert.doesNotMatch(html, />Idle</);
});

test('weaverDismissNote clears the non-blocking banner without resuming', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverDismissNote()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'weaver_dismiss_note',
      group: 'alpha',
    },
  ]);
});

test('weaverTogglePauseForGroup reuses the normal pause and resume commands', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_settings.alpha = { paused: false };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverTogglePauseForGroup('alpha')`, context);
  sandbox.state.weaver_settings.alpha.paused = true;
  vm.runInContext(`weaverTogglePauseForGroup('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'weaver_pause', group: 'alpha' },
    { cmd: 'weaver_resume', group: 'alpha' },
  ]);
});

test('weaverSendNow uses the explicit flush command', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverSendNow()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'weaver_flush_now', group: 'alpha' },
  ]);
});

test('renderWeaverPanel uses the focused group in multi-project workspaces', () => {
  const sandbox = createSandbox();
  sandbox.document.getElementById = function(id) {
    if (id !== 'panel-weaver') return null;
    return {
      innerHTML: '',
      querySelector() { return null; },
    };
  };
  sandbox.state.groups = { alpha: [], beta: [] };
  sandbox.state.group_settings = {
    alpha: { weaver_agent_id: 'weaver-alpha' },
    beta: {},
  };
  sandbox.state.agents = {
    'stale-selected': { id: 'stale-selected', group: 'alpha' },
  };
  sandbox.selectedAgentId = 'stale-selected';
  sandbox._focusedGroup = function() { return 'beta'; };
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-weaver' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext('renderWeaverPanel()', context);

  assert.match(panel.innerHTML, /Weaver — beta/);
  assert.match(panel.innerHTML, /No journal entries yet/);
});

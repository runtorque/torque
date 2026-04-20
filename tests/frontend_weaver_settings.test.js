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

test('renderWeaverPanel shows Journal and Events tabs without settings tabs when a Weaver is configured', () => {
  const sandbox = createSandbox();
  sandbox.state.group_settings = {
    alpha: { weaver_agent_id: 'weaver-alpha' },
  };
  sandbox.state.agents = {
    'weaver-alpha': { id: 'weaver-alpha', group: 'alpha', name: 'Weaver' },
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

  vm.runInContext('renderWeaverPanel()', context);

  assert.match(panel.innerHTML, /Engineers — alpha/);
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

test('weaver journal keeps chronology separate and shows a Session Map button', () => {
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

  assert.match(html, />Session Map</);
  assert.match(html, /No journal entries yet/);
  assert.doesNotMatch(html, /Open Streams/);
  assert.doesNotMatch(html, /Awaiting validation/);
  assert.doesNotMatch(html, /weaver-stream-section-label-product/);
});

test('weaver session map renders deterministic stream and recovery sections', () => {
  const sandbox = createSandbox();
  sandbox.state.weaver_session_maps = {
    alpha: {
      group: 'alpha',
      overview: {
        tasks_total: 4,
        active_stream_count: 1,
        active_agent_count: 1,
        pending_ask_count: 1,
        human_gate_count: 1,
        queued_follow_up_count: 2,
      },
      streams: {
        items: [
          {
            stream_id: 'stream:/repo::loom/modal-polish',
            branch: 'loom/modal-polish',
            foreground_task_title: 'Polish modal',
            state: 'awaiting_human_validation',
            validation_state: 'pending_human_validation',
            merge_state: 'not_ready',
            gate_reason: 'Run manual smoke',
            recommended_next_action: 'run_manual_validation',
            latest_reviewed_commit_sha: 'abc1234567',
            product_task_ids: ['LOOM:1'],
            workflow_task_ids: ['LOOM:1:1'],
            recent_visibility_items: [
              { kind: 'weaver_message', summary: 'Paused queued work until validation clears' },
            ],
          },
        ],
      },
      asks: {
        items: [{ id: 'ASK:1', title: 'Approve release plan', parent_task_id: 'LOOM:1' }],
      },
      human_gates: {
        items: [{ stream_title: 'Polish modal', branch: 'loom/modal-polish', gate_reason: 'Run manual smoke' }],
      },
      task_health: {
        items: [{ id: 'LOOM:9', title: 'Investigate flaky smoke', health_state: 'blocked', via: '' }],
      },
      verification: {
        items: [{ id: 'LOOM:1', title: 'Polish modal', verification_state: 'pending', verification_mode: 'deploy', detail: 'Run manual smoke' }],
      },
      branch_boundaries: {
        items: [{
          latest_boundary_task: 'Review modal polish',
          branch: 'loom/modal-polish',
          partial_review_safe: true,
          foreground_task_title: 'Polish modal',
          queued_followups: [{ title: 'Ship follow-up copy tweak' }],
          started_followups: [],
        }],
      },
      agents: {
        items: [{ id: 'agent-1', name: 'Worker One', status: 'running', current_task: 'Polish modal', activity_detail: 'Waiting on smoke results' }],
      },
      queued_follow_up: {
        items: [
          { source: 'stream_queue', task_id: 'LOOM:2', task_title: 'Ship follow-up copy tweak', queue_state: 'paused_by_validation', branch: 'loom/modal-polish', gate_reason: 'Run manual smoke' },
          { source: 'dispatch_queue', task_id: 'LOOM:3', task_title: 'Prepare release notes', target_agent_name: 'Worker One' },
        ],
      },
      journal: {
        items: [
          { id: 9, type: 'checkpoint', timestamp: 100, entry: 'Checkpoint: validation pending before merge.' },
          { id: 8, type: 'decision', timestamp: 90, entry: 'Decision: pause queued work until smoke passes.' },
        ],
      },
      hints: {
        items: [{ kind: 'ready_to_merge', message: '1 stream will be ready to merge once validation clears.' }],
      },
    },
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);
  vm.runInContext(`_weaverJournalSubviewByGroup.alpha = 'session_map'`, context);

  const html = vm.runInContext(`_weaverRenderJournal("alpha")`, context);

  assert.match(html, /Polish modal/);
  assert.match(html, /modal-polish/);
  assert.match(html, /Awaiting validation/);
  assert.match(html, /Run manual validation/);
  assert.match(html, /Pending asks/);
  assert.match(html, /Approve release plan/);
  assert.match(html, /Human gates/);
  assert.match(html, /Task health/);
  assert.match(html, /Verification/);
  assert.match(html, /Branch review points/);
  assert.match(html, /Active agents/);
  assert.match(html, /Queued follow-up/);
  assert.match(html, /Recent decisions, plans, and checkpoints/);
  assert.match(html, /Back to Journal/);
});

test('weaverOpenSessionMap requests the on-demand Session Map payload', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverOpenSessionMap('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'weaver_session_map_read', group: 'alpha' },
  ]);
  assert.equal(vm.runInContext(`_weaverJournalSubviewByGroup.alpha`, context), 'session_map');
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

  assert.match(panel.innerHTML, /Engineers — beta/);
  assert.match(panel.innerHTML, /No Weaver configured for beta/);
  assert.doesNotMatch(panel.innerHTML, /Engineers — alpha/);
});

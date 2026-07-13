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
    state: { engineer_settings: {} },
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

function loadEngineer(context) {
  [
    'static/js/agent_panel.js',
    'static/js/agent-panel/virtual-lists.js',
    'static/js/agent-panel/events.js',
    'static/js/agent-panel/architect.js',
    'static/js/agent-panel/engineer.js',
    'static/js/agent-panel/worker.js',
    'static/js/agent-panel/hierarchy.js',
    'static/js/agent-panel/legacy-engineer.js',
    'static/js/agent-panel/classes.js',
  ].forEach(function(relPath) {
    const filename = path.join(repoRoot, relPath);
    const source = fs.readFileSync(filename, 'utf8');
    vm.runInContext(source, context, { filename });
  });
}

test('focused engineer panel shows Journal, Events, Queued, and Completed tabs without settings tabs', () => {
  const sandbox = createSandbox();
  sandbox.focusedItemId = 'eng-alpha';
  sandbox.state.agents = {
    'eng-alpha': { id: 'eng-alpha', group: 'alpha', name: 'Builder', kind: 'engineer', cell_type: 'agent' },
  };
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-agent' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext('renderAgentPanel()', context);

  assert.match(panel.innerHTML, /Engineer: Builder · Group: alpha/);
  assert.match(panel.innerHTML, />Journal</);
  assert.match(panel.innerHTML, />Events</);
  assert.match(panel.innerHTML, />Queued</);
  assert.match(panel.innerHTML, />Completed</);
  assert.match(panel.innerHTML, /agent-panel-tabs/);
  assert.doesNotMatch(panel.innerHTML, />Settings</);
});

test('engineer Events tab renders queued and sent digest sections', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_buffer_stats = {
    alpha: {
      buffered_events: 1,
      next_push_in: 45,
      queued_events: [
        { id: 7, kind: 'task_completed', agent_name: 'Worker', message: 'Waiting for review', timestamp: 10 },
      ],
      manual_flush_requested: false,
    },
  };
  sandbox.state.engineer_sent_events = {
    alpha: [
      { id: 5, kind: 'task_completed', agent_name: 'Worker', message: 'Merged cleanly', timestamp: 5, delivered_at: 12 },
    ],
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  const html = vm.runInContext(`_agentPanelLegacyRenderEvents('alpha', state.engineer_settings.alpha || {}, null, state.engineer_buffer_stats.alpha)`, context);

  assert.match(html, /Queued for next digest/);
  assert.match(html, /Already sent to Engineer/);
  assert.match(html, /Waiting for review/);
  assert.match(html, /Merged cleanly/);
  assert.match(html, /Send queued now/);
});

test('focused engineer Events countdown updates in place without rerendering the whole panel', () => {
  const sandbox = createSandbox();
  let nowMs = 1_000_000;
  sandbox.Date.now = () => nowMs;
  sandbox.focusedItemId = 'eng-alpha';
  sandbox.state.agents = {
    'eng-alpha': { id: 'eng-alpha', group: 'alpha', name: 'Builder', kind: 'engineer', cell_type: 'agent' },
  };
  sandbox.state.agent_digest_settings = {
    'eng-alpha': { agent_id: 'eng-alpha', paused: false },
  };
  sandbox.state.digest_buffer_stats = {
    'eng-alpha': {
      agent_id: 'eng-alpha',
      group: 'alpha',
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
      if (selector === '.agent-panel-events-countdown') return countdown;
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
    return id === 'panel-agent' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events'; renderAgentPanel()`, context);

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

test('engineer Events tab disables send-now while paused', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_settings.alpha = { paused: true };
  sandbox.state.engineer_buffer_stats = {
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
  loadEngineer(context);

  const html = vm.runInContext(
    `_agentPanelLegacyRenderEvents("alpha", state.engineer_settings.alpha, null, state.engineer_buffer_stats.alpha)`,
    context,
  );

  assert.match(html, /Delivery is paused/);
  assert.match(html, /Send queued now/);
  assert.match(html, /disabled/);
});

test('engineer Completed tab renders worklog tasks with live lane and status', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_worklog = {
    alpha: [
      {
        id: 4,
        task_id: 'TORQUE:9',
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
    'TORQUE:9': {
      id: 'TORQUE:9',
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
  loadEngineer(context);

  const html = vm.runInContext(
    `_agentPanelLegacyRenderWorklog("alpha", {})`,
    context,
  );

  assert.match(html, /Completed tasks/);
  assert.match(html, /Add Worklog tab/);
  assert.match(html, /In Progress/);
  assert.match(html, /In review/);
  assert.match(html, /Worker One/);
});

test('engineer Completed tab hides non-owned rows when restrict_to_created_agents is enabled', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_worklog = {
    alpha: [
      {
        id: 2,
        task_id: 'TORQUE:2',
        task_title: 'Owned task',
        agent_id: 'agent-owned',
        agent_name: 'Owned Worker',
        agent_owned: true,
        started_at: 20,
      },
      {
        id: 1,
        task_id: 'TORQUE:1',
        task_title: 'Legacy task',
        agent_id: 'agent-legacy',
        agent_name: 'Legacy Worker',
        agent_owned: false,
        started_at: 10,
      },
    ],
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  const html = vm.runInContext(
    `_agentPanelLegacyRenderWorklog("alpha", { restrict_to_created_agents: true })`,
    context,
  );

  assert.match(html, /Owned task/);
  assert.doesNotMatch(html, /Legacy task/);
  assert.match(html, /Engineer-created agents/);
});

test('engineer journal distinguishes blocking asks from non-blocking notes', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_settings.alpha = {
    pending_question: 'Need approval to merge?',
    pending_note: 'FYI: branch is ready for review',
    pending_note_kind: 'question',
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  const html = vm.runInContext(`_agentPanelLegacyRenderJournal("alpha")`, context);

  assert.match(html, /class="agent-panel-ask-banner"/);
  assert.match(html, /Engineer is asking:/);
  assert.match(html, /class="agent-panel-note-banner"/);
  assert.match(html, /Engineer asks \(non-blocking\):/);
  assert.match(html, /engineerDismissNote/);
});

test('engineer journal keeps chronology separate and shows a Session Map button', () => {
  const sandbox = createSandbox();
  sandbox.state.board_tasks = {
    'TORQUE:333': { id: 'TORQUE:333', task: 'Add Events tab' },
    'TORQUE:342': { id: 'TORQUE:342', task: 'Keep Engineer Events next-dispatch timing accurate' },
    'TORQUE:333:4': { id: 'TORQUE:333:4', task: 'Review Events stream' },
    'TORQUE:342:1': { id: 'TORQUE:342:1', task: 'Validate stream after smoke testing' },
  };
  sandbox.state.engineer_streams = {
    alpha: {
      count: 1,
      by_state: { awaiting_human_validation: 1 },
      items: [
        {
          stream_id: 'stream:/repo::torque/events-panel',
          branch: 'torque/events-panel',
          foreground_task_title: 'Keep Engineer Events next-dispatch timing accurate',
          state: 'awaiting_human_validation',
          code_state: 'reviewed_clean',
          validation_state: 'pending_human_validation',
          merge_state: 'not_ready',
          gate_reason: 'Run manual smoke',
          recommended_next_action: 'merge_after_validation',
          latest_reviewed_commit_sha: 'rev4561234567',
          product_task_ids: ['TORQUE:333', 'TORQUE:342'],
          workflow_task_ids: ['TORQUE:333:4', 'TORQUE:342:1'],
          recent_visibility_items: [
            {
              kind: 'agent_reply',
              task_id: 'TORQUE:9',
              task_title: 'Engineer: reprioritize blocker fix',
              summary: 'Will handle blocker first',
              timestamp: '2026-04-07T11:12:00+00:00',
            },
            {
              kind: 'engineer_message',
              task_id: 'TORQUE:9',
              task_title: 'Engineer: reprioritize blocker fix',
              summary: 'Reprioritized blocker fix before queued work',
              timestamp: '2026-04-07T11:11:00+00:00',
            },
          ],
        },
      ],
    },
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  const html = vm.runInContext(`_agentPanelLegacyRenderJournal("alpha")`, context);

  assert.match(html, />Session Map</);
  assert.match(html, /No journal entries yet/);
  assert.doesNotMatch(html, /Open Streams/);
  assert.doesNotMatch(html, /Awaiting validation/);
  assert.doesNotMatch(html, /agent-panel-stream-section-label-product/);
});

test('engineer session map renders deterministic stream and recovery sections', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_session_maps = {
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
            stream_id: 'stream:/repo::torque/modal-polish',
            branch: 'torque/modal-polish',
            foreground_task_title: 'Polish modal',
            state: 'awaiting_human_validation',
            validation_state: 'pending_human_validation',
            merge_state: 'not_ready',
            gate_reason: 'Run manual smoke',
            recommended_next_action: 'run_manual_validation',
            latest_reviewed_commit_sha: 'abc1234567',
            pr_url: 'https://github.com/acme/repo/pull/42',
            pr_state: 'auto_merge_enabled',
            pr: {
              url: 'https://github.com/acme/repo/pull/42',
              number: 42,
              state: 'auto_merge_enabled',
            },
            product_task_ids: ['TORQUE:1'],
            workflow_task_ids: ['TORQUE:1:1'],
            recent_visibility_items: [
              { kind: 'engineer_message', summary: 'Paused queued work until validation clears' },
            ],
          },
        ],
      },
      asks: {
        items: [{ id: 'ASK:1', title: 'Approve release plan', parent_task_id: 'TORQUE:1' }],
      },
      human_gates: {
        items: [{ stream_title: 'Polish modal', branch: 'torque/modal-polish', gate_reason: 'Run manual smoke' }],
      },
      task_health: {
        items: [{ id: 'TORQUE:9', title: 'Investigate flaky smoke', health_state: 'blocked', via: '' }],
      },
      verification: {
        items: [{ id: 'TORQUE:1', title: 'Polish modal', verification_state: 'pending', verification_mode: 'deploy', detail: 'Run manual smoke' }],
      },
      branch_boundaries: {
        items: [{
          latest_boundary_task: 'Review modal polish',
          branch: 'torque/modal-polish',
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
          { source: 'stream_queue', task_id: 'TORQUE:2', task_title: 'Ship follow-up copy tweak', queue_state: 'paused_by_validation', branch: 'torque/modal-polish', gate_reason: 'Run manual smoke' },
          { source: 'dispatch_queue', task_id: 'TORQUE:3', task_title: 'Prepare release notes', target_agent_name: 'Worker One' },
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
  loadEngineer(context);
  vm.runInContext(`_engineerJournalSubviewByGroup.alpha = 'session_map'`, context);

  const html = vm.runInContext(`_agentPanelLegacyRenderJournal("alpha")`, context);

  assert.match(html, /Polish modal/);
  assert.match(html, /modal-polish/);
  assert.match(html, /Awaiting validation/);
  assert.match(html, /https:\/\/github\.com\/acme\/repo\/pull\/42/);
  assert.match(html, /#42/);
  assert.match(html, /Auto-merge pending/);
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

test('engineerOpenSessionMap requests the on-demand Session Map payload', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerOpenSessionMap('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_session_map_read', group: 'alpha' },
  ]);
  assert.equal(vm.runInContext(`_engineerJournalSubviewByGroup.alpha`, context), 'session_map');
});

test('focused engineer Session Map requests are scoped by engineer id', () => {
  const sandbox = createSandbox();
  sandbox.focusedItemId = 'eng-alpha';
  sandbox.state.agents = {
    'eng-alpha': {
      id: 'eng-alpha',
      group: 'alpha',
      name: 'Builder',
      kind: 'engineer',
      cell_type: 'agent',
    },
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerOpenSessionMap('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_session_map_read', group: 'alpha', engineer_id: 'eng-alpha' },
  ]);
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      vm.runInContext(`_engineerSessionMapMetaByGroup['alpha::eng-alpha']`, context)
    )),
    { loading: true, stale: false },
  );
});

test('group-level Session Map reads default-engineer scoped responses', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_session_maps = {};
  sandbox.state.group_settings = {
    alpha: { engineer_agent_id: 'eng-alpha' },
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerOpenSessionMap('alpha')`, context);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_session_map_read', group: 'alpha' },
  ]);
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      vm.runInContext(`_engineerSessionMapMetaByGroup.alpha`, context)
    )),
    { loading: true, stale: false },
  );

  vm.runInContext(`
    state.engineer_session_maps['alpha::eng-alpha'] = {
      group: 'alpha',
      overview: { tasks_total: 7 },
      streams: { items: [] }
    };
    _engineerReceiveSessionMap({
      type: 'engineer_session_map',
      group: 'alpha',
      engineer_id: 'eng-alpha',
      session_map: state.engineer_session_maps['alpha::eng-alpha']
    });
  `, context);

  assert.deepEqual(
    JSON.parse(JSON.stringify(vm.runInContext(`_engineerSessionMapData('alpha')`, context))),
    {
      group: 'alpha',
      overview: { tasks_total: 7 },
      streams: { items: [] },
    },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      vm.runInContext(`_engineerSessionMapMetaByGroup.alpha`, context)
    )),
    { loading: false, stale: false },
  );
  assert.equal(
    vm.runInContext(`_engineerSessionMapStatus('alpha', 'session_map')`, context),
    '',
  );
});

test('group-level Session Map ignores non-default scoped responses', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_session_maps = {
    'alpha::eng-peer': { group: 'alpha', overview: { tasks_total: 99 } },
  };
  sandbox.state.group_settings = {
    alpha: { engineer_agent_id: 'eng-alpha' },
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  assert.equal(vm.runInContext(`_engineerSessionMapData('alpha')`, context), null);
});

test('engineerDismissNote clears the non-blocking banner without resuming', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerDismissNote()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'engineer_dismiss_note',
      group: 'alpha',
    },
  ]);
});

test('engineerTogglePauseForGroup reuses the normal pause and resume commands', () => {
  const sandbox = createSandbox();
  sandbox.state.engineer_settings.alpha = { paused: false };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerTogglePauseForGroup('alpha')`, context);
  sandbox.state.engineer_settings.alpha.paused = true;
  vm.runInContext(`engineerTogglePauseForGroup('alpha')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_pause', group: 'alpha' },
    { cmd: 'engineer_resume', group: 'alpha' },
  ]);
});

test('engineerSendNow uses the explicit flush command', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext(`engineerSendNow()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_flush_now', group: 'alpha' },
  ]);
});

test('focused engineer utilities use the focused agent group in multi-project workspaces', () => {
  const sandbox = createSandbox();
  sandbox.state.groups = { alpha: [], beta: [] };
  sandbox.state.agents = {
    'eng-beta': { id: 'eng-beta', group: 'beta', name: 'Builder', kind: 'engineer', cell_type: 'agent' },
  };
  sandbox.focusedItemId = 'eng-beta';
  sandbox._focusedGroup = function() { return 'alpha'; };
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-agent' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadEngineer(context);

  vm.runInContext('renderAgentPanel()', context);
  vm.runInContext('engineerSendNow()', context);

  assert.match(panel.innerHTML, /Engineer: Builder · Group: beta/);
  assert.equal(vm.runInContext(`_agentPanelCurrentGroup()`, context), 'beta');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'engineer_flush_now', group: 'beta' },
  ]);
});

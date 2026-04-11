const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
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

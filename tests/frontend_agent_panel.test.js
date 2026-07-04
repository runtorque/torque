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

function createHarness() {
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  function makeClassList() {
    const values = new Set();
    return {
      add(name) { values.add(name); },
      remove(name) { values.delete(name); },
      contains(name) { return values.has(name); },
      toString() { return Array.from(values).join(' '); },
    };
  }
  const classModal = {
    innerHTML: '',
    textContent: '',
    classList: makeClassList(),
    querySelector() { return null; },
  };
  const classModalBody = {
    innerHTML: '',
    textContent: '',
    classList: makeClassList(),
    querySelector() { return null; },
  };
  const classModalSummary = {
    innerHTML: '',
    textContent: '',
    classList: makeClassList(),
    querySelector() { return null; },
  };
  const captureCalls = [];
  const restoreCalls = [];
  const intervalCalls = [];
  const clearIntervalCalls = [];
  const sendCalls = [];
  const sandbox = {
    console,
    Date,
    state: {
      agents: {},
      board_tasks: {},
      groups: { alpha: [] },
      engineer_settings: {},
      engineer_buffer_stats: {},
      engineer_sent_events: {},
      engineer_worklog: {},
      engineer_journal: {},
      engineer_session_maps: {},
      agent_digest_settings: {},
      digest_buffer_stats: {},
      digest_sent_events: {},
      panel_events: [],
      decisions: {},
      group_settings: {},
      global_settings: {
        mcp_call_log_args_capture: 'metadata',
      },
      mcp_calls: {},
    },
    focusedItemId: '',
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'panel-agent') return panel;
        if (id === 'modal-agent-class') return classModal;
        if (id === 'agent-class-modal-body') return classModalBody;
        if (id === 'agent-class-modal-summary') return classModalSummary;
        return null;
      },
    },
    _captureSurfaceState(root, opts) {
      captureCalls.push({ root, opts });
      const snapshot = { focus: null, scrolls: [] };
      if (opts && typeof opts.capture === 'function') opts.capture(snapshot, root);
      return snapshot;
    },
    _restoreSurfaceState(root, snapshot, opts) {
      restoreCalls.push({ root, snapshot, opts });
      if (opts && typeof opts.restore === 'function') opts.restore(root, snapshot);
    },
    _engineerStopEventsCountdownTimer() {},
    _currentGroup() { return 'alpha'; },
    setInterval(fn, ms) { intervalCalls.push({ fn, ms }); return intervalCalls.length; },
    clearInterval(id) { clearIntervalCalls.push(id); },
    send(message) { sendCalls.push(message); },
    window: { prompt() { return null; } },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/behavior_overlay.js');
  loadScript(context, 'static/js/agent_panel.js');
  return {
    context,
    panel,
    classModal,
    classModalBody,
    classModalSummary,
    captureCalls,
    restoreCalls,
    intervalCalls,
    clearIntervalCalls,
    sendCalls,
  };
}

function setFocusedAgent(context, agent) {
  context.state.agents = {};
  context.focusedItemId = '';
  if (agent) {
    context.state.agents[agent.id] = agent;
    context.focusedItemId = agent.id;
  }
}
function countText(haystack, needle) {
  return String(haystack || '').split(needle).length - 1;
}

test('renderAgentPanel shows an empty state when no grid item is focused', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Select an agent from the grid to see its context\./);
  assert.equal(captureCalls.length, 1);
  assert.equal(restoreCalls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(captureCalls[0].opts.scrollSelectors)), [
    '.agent-panel-content',
    '.agent-panel-message-list',
  ]);
});

test('renderAgentPanel renders architect, engineer, worker, and terminal panels', () => {
  const { context, panel } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.match(panel.innerHTML, /Decisions/);
  assert.doesNotMatch(panel.innerHTML, /id="agent-panel-tab-hired_engineers"/);
  assert.match(panel.innerHTML, /Messages/);
  assert.match(panel.innerHTML, /Events/);

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Engineer: Builder · Group: alpha/);
  assert.match(panel.innerHTML, /Journal/);
  assert.match(panel.innerHTML, /Events/);
  assert.match(panel.innerHTML, /Queued/);
  assert.match(panel.innerHTML, /Completed/);

  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.match(panel.innerHTML, /Per-worker event stream and task history\./);

  setFocusedAgent(context, {
    id: 'term-1',
    name: 'Shell Root',
    cell_type: 'terminal',
    current_branch: 'torque/feature-x',
    current_process: 'pytest',
    current_path: '/tmp/torque',
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Terminal: Shell Root/);
  assert.match(panel.innerHTML, /Open the terminal drawer to interact with this session\./);
  assert.match(panel.innerHTML, /pytest/);
});

test('engineer and architect rename use custom input dialog submit/cancel semantics', async () => {
  const { context, sendCalls } = createHarness();
  context.state.agents = {
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha' },
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha' },
  };
  const dialogCalls = [];
  context.showInputDialog = function(opts) {
    dialogCalls.push(JSON.parse(JSON.stringify(opts)));
    if (opts.title === 'Rename Engineer') return Promise.resolve({ name: ' Builder Prime ' });
    return Promise.resolve(null);
  };

  await context.engineerRenameEngineer('eng-1');
  await context.engineerRenameArchitect('arch-1');

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'rename_engineer', id: 'eng-1', new_name: 'Builder Prime' },
  ]);
  assert.equal(dialogCalls[0].title, 'Rename Engineer');
  assert.equal(dialogCalls[0].fields[0].defaultValue, 'Builder');
  assert.equal(dialogCalls[1].title, 'Rename Architect');
  assert.equal(dialogCalls[1].fields[0].defaultValue, 'Planner');
});

test('engineer panel specialization editor reads cell field and writes full replacement', () => {
  const { context, panel, sendCalls } = createHarness();
  context.state.specializations_group = 'alpha';
  context.state.specializations = [
    { name: 'ui-ux', preamble: 'UX.', global: false },
    { name: 'desktop-shell', preamble: 'Desktop.', global: false },
  ];
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
    hired_by_architect_id: 'arch-1',
    engineer_specializations: ['ui-ux'],
  });
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Specializations/);
  assert.match(panel.innerHTML, /ui-ux[\s\S]*\(primary\)/);

  context.agentPanelStartEngineerSpecializationsEdit('eng-1');
  context.agentPanelAddEngineerSpecialization('eng-1', 'desktop-shell');
  context.agentPanelMoveEngineerSpecialization('eng-1', 1, -1);
  // A routine rerender must preserve the unsaved ordered draft.
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /desktop-shell \(primary\)[\s\S]*ui-ux/);
  context.agentPanelSaveEngineerSpecializations('eng-1');

  const setCall = sendCalls.find(
    (msg) => msg.cmd === 'architect_engineer_set_specializations',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(setCall)), {
    cmd: 'architect_engineer_set_specializations',
    architect_id: 'arch-1',
    engineer_id: 'eng-1',
    specializations: ['desktop-shell', 'ui-ux'],
  });
});

test('engineer panel specialization editor reflects set response reorder and clear-to-empty', () => {
  const { context, panel } = createHarness();
  context.state.specializations_group = 'alpha';
  context.state.specializations = [
    { name: 'ui-ux', global: false },
    { name: 'desktop-shell', global: false },
  ];
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
    hired_by_architect_id: 'arch-1',
    engineer_specializations: ['ui-ux', 'desktop-shell'],
  });
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };

  context.renderAgentPanel();
  context.agentPanelReceiveEngineerSpecializations({
    type: 'engineer_specializations',
    engineer_id: 'eng-1',
    specializations: ['desktop-shell', 'ui-ux'],
  });
  assert.deepEqual(context.state.agents['eng-1'].engineer_specializations, ['desktop-shell', 'ui-ux']);
  assert.match(panel.innerHTML, /desktop-shell[\s\S]*\(primary\)[\s\S]*ui-ux/);

  context.agentPanelReceiveEngineerSpecializations({
    type: 'engineer_specializations',
    engineer_id: 'eng-1',
    specializations: [],
  });
  assert.deepEqual(context.state.agents['eng-1'].engineer_specializations, []);
  assert.match(panel.innerHTML, /Generalist \(no specialization\)/);
});

test('engineer panel specialization editor surfaces API error contract inline and toast', () => {
  const { context, panel } = createHarness();
  const toastCalls = [];
  context._showToast = function(message, type) {
    toastCalls.push({ message, type });
  };
  context.state.specializations_group = 'alpha';
  context.state.specializations = [{ name: 'ui-ux', global: false }];
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
    hired_by_architect_id: 'arch-1',
    engineer_specializations: ['ui-ux'],
  });
  context.state.agents['arch-1'] = {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  };

  const messages = [
    'specializations must be a list',
    'Unknown specialization(s): security. Valid specializations: ui-ux',
    'engineer not found in scope',
  ];
  for (const message of messages) {
    context.agentPanelStartEngineerSpecializationsEdit('eng-1');
    context.agentPanelSaveEngineerSpecializations('eng-1');
    assert.equal(
      context.agentPanelHandleEngineerSpecializationsError({ type: 'error', message }),
      true,
    );
    assert.match(panel.innerHTML, new RegExp(message.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }

  assert.deepEqual(toastCalls, messages.map((message) => ({ message, type: 'error' })));
});

test('Behavior tab renders overlay editor, proposals, and version timeline', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.behavior_overlay_active = {
    'eng-1': { agent_id: 'eng-1', active_version_id: 'bov-2' },
  };
  context.state.behavior_overlay_versions = {
    'eng-1': [
      { id: 'bov-2', agent_id: 'eng-1', version_number: 2, text_sha256: 'abcdef123456', text_bytes: 12, rationale: 'Current', created_at: 10 },
      { id: 'bov-1', agent_id: 'eng-1', version_number: 1, text_sha256: '0000000000', text_bytes: 0, rationale: 'Seed', created_at: 1 },
    ],
  };
  context.state.behavior_overlay_proposals = {
    'bop-1': {
      id: 'bop-1',
      agent_id: 'eng-1',
      status: 'proposed',
      next_actor_kind: 'architect',
      approval_route: 'architect',
      proposed_text_sha256: 'feedface1234',
      proposed_text_bytes: 18,
      rationale: 'Improve cadence',
    },
  };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Behavior overlays/);
  assert.match(panel.innerHTML, /data-behavior-overlay-layer="inherited"/);
  assert.match(panel.innerHTML, /Inherited role overlay/);
  assert.match(panel.innerHTML, /group-wide · engineers/);
  assert.match(panel.innerHTML, /read-only inherited/);
  assert.match(panel.innerHTML, /data-behavior-overlay-layer="agent-specific"/);
  assert.match(panel.innerHTML, /Agent-specific engineer overlay/);
  assert.match(panel.innerHTML, /agent-specific · editable/);
  assert.match(panel.innerHTML, /All engineers \(role\)/);
  assert.match(panel.innerHTML, /Proposed behavior text/);
  assert.match(panel.innerHTML, /Open proposals for this agent/);
  assert.match(panel.innerHTML, /Improve cadence/);
  assert.match(panel.innerHTML, /Version timeline/);
  assert.match(panel.innerHTML, /Request rollback/);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'behavior_overlay_read', seed: true, scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', role_kind: 'engineer' },
    { cmd: 'behavior_overlay_versions', limit: 50, scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', role_kind: 'engineer' },
    { cmd: 'behavior_overlay_proposals', status_filter: '', limit: 200 },
    { cmd: 'behavior_overlay_read', agent_id: 'eng-1', seed: true },
    { cmd: 'behavior_overlay_versions', agent_id: 'eng-1', limit: 50 },
  ]);
});

test('Behavior tab visually separates read-only inherited role text from agent-specific editor', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    scope_id: 'role:alpha:engineer',
    active: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-role' },
    version: { id: 'bov-role', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 3, text_sha256: 'rolehash', text_bytes: 20 },
    text: 'shared role guidance',
  });
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    agent_id: 'eng-1',
    active: { agent_id: 'eng-1', active_version_id: 'bov-agent' },
    version: { id: 'bov-agent', agent_id: 'eng-1', version_number: 2, text_sha256: 'agenthash', text_bytes: 19 },
    text: 'agent-only guidance',
  });

  context.renderAgentPanel();

  const inheritedStart = panel.innerHTML.indexOf('data-behavior-overlay-layer="inherited"');
  const agentSpecificStart = panel.innerHTML.indexOf('data-behavior-overlay-layer="agent-specific"');
  assert.notEqual(inheritedStart, -1);
  assert.notEqual(agentSpecificStart, -1);
  assert.ok(inheritedStart < agentSpecificStart);
  assert.match(panel.innerHTML, /Current inherited role text/);
  assert.match(panel.innerHTML, /shared role guidance/);
  assert.match(panel.innerHTML, /Agent-specific engineer overlay/);
  assert.match(panel.innerHTML, /agent-only guidance/);
  assert.doesNotMatch(panel.innerHTML, /behaviorOverlaySubmitDraft\('role'/);
  assert.ok(panel.innerHTML.includes("behaviorOverlaySubmitDraft('own','eng-1','eng-1','engineer',false)"));
});

test('Behavior timeline renders initial overlay version zero as v0', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    agent_id: 'eng-1',
    active: { agent_id: 'eng-1', active_version_id: 'bov-0' },
    version: { id: 'bov-0', agent_id: 'eng-1', version_number: 0, text_sha256: 'zero-hash', text_bytes: 0, rationale: 'Initial seed', created_at: 1 },
    text: '',
  });

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /<span class="behavior-overlay-version-number">v0<\/span>/);
  assert.match(panel.innerHTML, /<span class="detail-task-status">v0<\/span>/);
  assert.doesNotMatch(panel.innerHTML, /<span class="behavior-overlay-version-number">v\?<\/span>/);
});

test('Behavior proposal response does not refetch proposals on focused rerender', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.behavior_overlay_active = {
    'eng-1': { agent_id: 'eng-1', active_version_id: 'bov-1' },
  };
  context.state.behavior_overlay_versions = {
    'eng-1': [
      { id: 'bov-1', agent_id: 'eng-1', version_number: 1, text_sha256: 'aaaa', text_bytes: 4, rationale: 'Seed', created_at: 1 },
    ],
  };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);

  context.renderAgentPanel();
  assert.equal(sendCalls.filter((call) => call.cmd === 'behavior_overlay_proposals').length, 1);

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay_proposals',
    proposals: [{
      id: 'bop-loop',
      agent_id: 'eng-1',
      status: 'proposed',
      next_actor_kind: 'architect',
      proposed_text_sha256: 'bbbb',
      proposed_text_bytes: 9,
      rationale: 'No fetch loop',
    }],
  });

  assert.equal(context.state.behavior_overlay_proposals['bop-loop'].rationale, 'No fetch loop');
  assert.equal(sendCalls.filter((call) => call.cmd === 'behavior_overlay_proposals').length, 1);
});

test('Architect Behavior tab renders hired-engineer governance controls', () => {
  const { context, panel } = createHarness();
  context.state.group_settings = {
    alpha: { engineer_behavior_requires_user_approval: true },
  };
  context.state.agents = {
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent', hired_by_architect_id: 'arch-1' },
  };
  context.focusedItemId = 'arch-1';
  context.state.behavior_overlay_active = {
    'arch-1': { agent_id: 'arch-1', active_version_id: 'bov-a' },
    'eng-1': { agent_id: 'eng-1', active_version_id: 'bov-e' },
  };
  context.state.behavior_overlay_versions = {
    'eng-1': [
      { id: 'bov-e', agent_id: 'eng-1', version_number: 2, text_sha256: 'eeee', text_bytes: 5, rationale: 'Current', created_at: 2 },
      { id: 'bov-old', agent_id: 'eng-1', version_number: 1, text_sha256: 'oooo', text_bytes: 3, rationale: 'Prior', created_at: 1 },
    ],
  };
  context.state.behavior_overlay_proposals = {
    'bop-2': {
      id: 'bop-2',
      agent_id: 'eng-1',
      status: 'proposed',
      next_actor_kind: 'architect',
      approval_route: 'architect_then_user',
      proposed_text_sha256: '0123456789abcdef',
      proposed_text_bytes: 42,
      rationale: 'Architect direct edit',
    },
  };
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.architect = 'behavior';
    _behaviorOverlayInnerTabByAgent['arch-1'] = 'engineer';
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /data-agent-panel-behavior-view="engineer"/);
  assert.match(panel.innerHTML, /All engineers \(role\)/);
  assert.match(panel.innerHTML, /Hired engineer governance/);
  assert.match(panel.innerHTML, /Builder/);
  assert.match(panel.innerHTML, /architect → user approval required/);
  assert.match(panel.innerHTML, /Direct edit hired engineer overlay/);
  assert.match(panel.innerHTML, /Architect direct edit/);
  assert.match(panel.innerHTML, /Approve/);
  assert.match(panel.innerHTML, /Reject/);
  assert.match(panel.innerHTML, /Request rollback/);
});

test('architect Engineer Behavior tab refreshes when proposal list response adds visible proposals', () => {
  const { context, panel, sendCalls } = createHarness();
  const roleKey = 'role:alpha:engineer';
  context.state.group_settings = {
    alpha: { engineer_behavior_requires_user_approval: true },
  };
  context.state.agents = {
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent', hired_by_architect_id: 'arch-1' },
  };
  context.focusedItemId = 'arch-1';
  context.state.behavior_overlay_active = {
    [roleKey]: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-role' },
    'eng-1': { agent_id: 'eng-1', active_version_id: 'bov-eng' },
  };
  context.state.behavior_overlay_versions = {
    [roleKey]: [
      { id: 'bov-role', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 1, text_sha256: 'rolehash', text_bytes: 9, rationale: 'Role seed', created_at: 1 },
    ],
    'eng-1': [
      { id: 'bov-eng', agent_id: 'eng-1', version_number: 1, text_sha256: 'enghash', text_bytes: 8, rationale: 'Engineer seed', created_at: 1 },
    ],
  };
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.architect = 'behavior';
    _behaviorOverlayInnerTabByAgent['arch-1'] = 'engineer';
    _behaviorOverlayReadByAgent['role:alpha:engineer'] = {
      active: state.behavior_overlay_active['role:alpha:engineer'],
      version: state.behavior_overlay_versions['role:alpha:engineer'][0],
      text: 'role text',
      received_at: Date.now(),
    };
    _behaviorOverlayReadByAgent['eng-1'] = {
      active: state.behavior_overlay_active['eng-1'],
      version: state.behavior_overlay_versions['eng-1'][0],
      text: 'engineer text',
      received_at: Date.now(),
    };
    _behaviorOverlayVersionsByAgent['role:alpha:engineer'] = state.behavior_overlay_versions['role:alpha:engineer'].slice();
    _behaviorOverlayVersionsByAgent['eng-1'] = state.behavior_overlay_versions['eng-1'].slice();
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /data-agent-panel-behavior-view="engineer"/);
  assert.match(panel.innerHTML, /All engineers \(role\)/);
  assert.match(panel.innerHTML, /Hired engineer governance/);
  assert.doesNotMatch(panel.innerHTML, /Late proposal/);
  assert.equal(sendCalls.filter((call) => call.cmd === 'behavior_overlay_proposals').length, 1);

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay_proposals',
    proposals: [{
      id: 'bop-late',
      agent_id: 'eng-1',
      target_kind: 'engineer',
      status: 'proposed',
      next_actor_kind: 'architect',
      approval_route: 'architect',
      proposed_text_sha256: 'latehash',
      proposed_text_bytes: 18,
      rationale: 'Late proposal',
    }],
  });

  assert.match(panel.innerHTML, /Late proposal/);
  assert.equal(sendCalls.filter((call) => call.cmd === 'behavior_overlay_proposals').length, 1);

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay_proposals',
    proposals: [{
      id: 'bop-role-late',
      scope_kind: 'role',
      scope_group: 'alpha',
      scope_key: 'engineer',
      role_kind: 'engineer',
      status: 'proposed',
      next_actor_kind: 'user',
      approval_route: 'user',
      proposed_text_sha256: 'rolelatehash',
      proposed_text_bytes: 20,
      rationale: 'Late role proposal',
    }],
  });

  assert.match(panel.innerHTML, /Late role proposal/);
  assert.equal(sendCalls.filter((call) => call.cmd === 'behavior_overlay_proposals').length, 1);
});

test('active behavior deltas invalidate cached full text before submit', () => {
  const { context, sendCalls } = createHarness();
  context.state.agents = {
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
  };
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    agent_id: 'eng-1',
    active: { agent_id: 'eng-1', active_version_id: 'bov-1' },
    version: { id: 'bov-1', agent_id: 'eng-1', version_number: 1, text_sha256: 'oldhash', text_bytes: 3 },
    text: 'old',
  });

  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_version_append',
    id: 'bov-2',
    agent_id: 'eng-1',
    version_number: 2,
    text_sha256: 'newhash',
    text_bytes: 3,
  });
  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_active_update',
    agent_id: 'eng-1',
    active_version_id: 'bov-2',
  });
  context.behaviorOverlaySubmitDraft('own', 'eng-1', 'eng-1', 'engineer', false);

  assert.equal(sendCalls.some((call) => call.cmd === 'behavior_overlay_propose'), false);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'behavior_overlay_read',
    agent_id: 'eng-1',
    seed: true,
  });

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    agent_id: 'eng-1',
    active: { agent_id: 'eng-1', active_version_id: 'bov-2' },
    version: { id: 'bov-2', agent_id: 'eng-1', version_number: 2, text_sha256: 'newhash', text_bytes: 3 },
    text: 'new',
  });
  context.behaviorOverlaySubmitDraft('own', 'eng-1', 'eng-1', 'engineer', false);

  const proposal = sendCalls[sendCalls.length - 1];
  assert.equal(proposal.cmd, 'behavior_overlay_propose');
  assert.equal(proposal.text, 'new');
  assert.equal(proposal.expected_base_version_id, 'bov-2');
});

test('behavior approval modal renders diff before enabling user actions', () => {
  const { context, sendCalls } = createHarness();
  const elements = {};
  function fakeClassList() {
    const set = new Set();
    return {
      add(name) { set.add(name); },
      remove(name) { set.delete(name); },
      contains(name) { return set.has(name); },
    };
  }
  function ensure(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        innerHTML: '',
        value: '',
        disabled: false,
        classList: fakeClassList(),
      };
    }
    return elements[id];
  }
  const panel = context.document.getElementById('panel-agent');
  context.document.getElementById = function(id) {
    if (id === 'panel-agent') return panel;
    return ensure(id);
  };
  context.openNestedModal = function(id) { ensure(id).classList.add('visible'); };
  context.closeNestedModal = function(id) { ensure(id).classList.remove('visible'); return true; };
  context.state.agents = {
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
  };
  context.state.board_tasks = {
    'TORQUE:1': {
      id: 'TORQUE:1',
      task: 'Dynamic Behavior overlay approval',
      lane: 'Backlog',
      labels: ['torque:human', 'behavior-overlay-approval', 'proposal:bop-1'],
    },
  };
  context.state.behavior_overlay_proposals = {
    'bop-1': {
      id: 'bop-1',
      agent_id: 'eng-1',
      target_kind: 'engineer',
      base_version_id: 'bov-1',
      proposed_by_kind: 'architect',
      proposed_by_agent_id: 'arch-1',
      proposed_text_sha256: 'hash-123',
      status: 'approved',
      next_actor_kind: 'user',
      rationale: 'Tune review cadence',
      lint_warning_count: 1,
    },
  };

  context.openBehaviorOverlayApprovalModal('TORQUE:1');

  assert.equal(ensure('behavior-approval-approve-btn').disabled, true);
  assert.equal(ensure('behavior-approval-reject-btn').disabled, true);
  assert.match(ensure('behavior-approval-body').innerHTML, /Loading required diff/);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.slice(-2))), [
    { cmd: 'behavior_overlay_proposals', status_filter: '', limit: 200 },
    { cmd: 'behavior_overlay_diff', proposal_id: 'bop-1' },
  ]);

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay_diff',
    proposal: {
      id: 'bop-1',
      agent_id: 'eng-1',
      target_kind: 'engineer',
      base_version_id: 'bov-1',
      proposed_by_kind: 'architect',
      proposed_by_agent_id: 'arch-1',
      proposed_text_sha256: 'hash-123',
      rationale: 'Tune review cadence',
      lint_warnings: [{ code: 'possible_mcp_contract_override', message: 'Check wording', excerpt: 'do not call torque_done' }],
      status: 'approved',
      next_actor_kind: 'user',
    },
    from_version: { id: 'bov-1', agent_id: 'eng-1' },
    to_proposal: { id: 'bop-1', agent_id: 'eng-1', proposed_text_sha256: 'hash-123' },
    diff: '--- bov-1\n+++ bop-1\n@@ -1 +1 @@\n-old\n+new\n',
  });

  assert.equal(ensure('behavior-approval-approve-btn').disabled, false);
  assert.equal(ensure('behavior-approval-reject-btn').disabled, false);
  assert.match(ensure('behavior-approval-body').innerHTML, /Rendered unified diff/);
  assert.match(ensure('behavior-approval-body').innerHTML, /diff-line-del/);
  assert.match(ensure('behavior-approval-body').innerHTML, /diff-line-add/);
  ensure('behavior-approval-note').value = 'Looks good';
  context.behaviorOverlayUserApprove();
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'behavior_overlay_user_approve',
    proposal_id: 'bop-1',
    expected_proposed_text_sha256: 'hash-123',
    expected_base_version_id: 'bov-1',
    note: 'Looks good',
  });
});

test('role behavior overlay renderer still supports engineer, architect, and worker scopes', () => {
  const { context, sendCalls } = createHarness();
  const elements = {};
  function ensure(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        innerHTML: '',
        scrollTop: 0,
        scrollLeft: 0,
        contains() { return false; },
        querySelector() { return null; },
      };
    }
    return elements[id];
  }
  const panel = context.document.getElementById('panel-agent');
  context.document.getElementById = function(id) {
    if (id === 'panel-agent') return panel;
    return ensure(id);
  };
  vm.runInContext(`_settingsGroup = 'alpha';`, context);
  context.state.behavior_overlay_active = {};
  context.state.behavior_overlay_versions = {};
  for (const roleKind of ['engineer', 'architect', 'worker']) {
    const key = `role:alpha:${roleKind}`;
    context.state.behavior_overlay_active[key] = { scope_kind: 'role', scope_group: 'alpha', scope_key: roleKind, active_version_id: `bov-${roleKind}` };
    context.state.behavior_overlay_versions[key] = [
      { id: `bov-${roleKind}`, scope_kind: 'role', scope_group: 'alpha', scope_key: roleKind, version_number: 1, text_sha256: `${roleKind}-hash`, text_bytes: 9, rationale: 'Seed', created_at: 1 },
    ];
    context.renderBehaviorOverlayRolePane('alpha', roleKind);
    const html = ensure(`gs-${roleKind}-role-behavior-overlay`).innerHTML;
    assert.match(html, /Role Dynamic Behavior overlay/);
    assert.match(html, new RegExp(`role · ${roleKind}`));
    assert.match(html, /user approval required/);
    assert.match(html, /Proposed behavior text/);
    assert.match(html, /Open proposals for this role/);
    assert.match(html, /Version timeline/);
  }

  const roleReadCalls = sendCalls.filter((call) => call.cmd === 'behavior_overlay_read');
  assert.ok(roleReadCalls.some((call) => call.scope_kind === 'role' && call.role_kind === 'engineer' && call.scope_group === 'alpha'));
  assert.ok(roleReadCalls.some((call) => call.scope_kind === 'role' && call.role_kind === 'architect' && call.scope_group === 'alpha'));
  assert.ok(roleReadCalls.some((call) => call.scope_kind === 'role' && call.role_kind === 'worker' && call.scope_group === 'alpha'));
});

test('role behavior deltas invalidate only matching focused behavior scopes', () => {
  const { context } = createHarness();
  context.state.agents = {
    'eng-1': { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
    'worker-1': { id: 'worker-1', name: 'Worker', kind: 'worker', group: 'alpha', cell_type: 'agent' },
  };
  context.focusedItemId = 'eng-1';
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);

  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active_version_id: 'bov-new',
  }), true);
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'architect',
    active_version_id: 'bov-arch',
  }), false);
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'beta',
    scope_key: 'engineer',
    active_version_id: 'bov-beta',
  }), false);

  context.focusedItemId = 'arch-1';
  vm.runInContext(`_agentPanelLastSelectedTabByKind.architect = 'behavior';`, context);
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active_version_id: 'bov-engineer',
  }), false, 'architect default Architect subtab should not refresh for engineer role scope');
  vm.runInContext(`_behaviorOverlayInnerTabByAgent['arch-1'] = 'engineer';`, context);
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active_version_id: 'bov-engineer',
  }), true, 'architect Engineer subtab displays the engineer role scope');
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'architect',
    active_version_id: 'bov-architect',
  }), false, 'architect Engineer subtab should not refresh for architect role scope');

  context.focusedItemId = 'worker-1';
  assert.equal(context.behaviorOverlayDeltaInvalidatesFocusedPanel({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'worker',
    active_version_id: 'bov-worker',
  }), false, 'workers have no per-worker Behavior tab');
});

test('role active deltas invalidate cached full text before submit', () => {
  const { context, sendCalls } = createHarness();
  const roleKey = 'role:alpha:engineer';
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    scope_id: roleKey,
    active: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-r1' },
    version: { id: 'bov-r1', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 1, text_sha256: 'oldhash', text_bytes: 3 },
    text: 'old',
  });

  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_version_append',
    id: 'bov-r2',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    version_number: 2,
    text_sha256: 'newhash',
    text_bytes: 3,
  });
  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active_version_id: 'bov-r2',
  });
  context.behaviorOverlaySubmitDraft('role', roleKey, 'user', 'user', false);

  assert.equal(sendCalls.some((call) => call.cmd === 'behavior_overlay_propose'), false);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'behavior_overlay_read',
    seed: true,
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    role_kind: 'engineer',
  });

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    scope_id: roleKey,
    active: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-r2' },
    version: { id: 'bov-r2', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 2, text_sha256: 'newhash', text_bytes: 3 },
    text: 'new',
  });
  context.behaviorOverlaySubmitDraft('role', roleKey, 'user', 'user', false);

  const proposal = sendCalls[sendCalls.length - 1];
  assert.equal(proposal.cmd, 'behavior_overlay_propose');
  assert.equal(proposal.text, 'new');
  assert.equal(proposal.expected_base_version_id, 'bov-r2');
  assert.equal(proposal.scope_kind, 'role');
  assert.equal(proposal.scope_group, 'alpha');
  assert.equal(proposal.scope_key, 'engineer');
  assert.equal(proposal.role_kind, 'engineer');
});

test('focused Behavior role view refetches instead of showing stale role text after active delta', () => {
  const { context, panel, sendCalls } = createHarness();
  const roleKey = 'role:alpha:engineer';
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'behavior';`, context);
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    scope_id: roleKey,
    active: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-r1' },
    version: { id: 'bov-r1', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 1, text_sha256: 'oldhash', text_bytes: 15 },
    text: 'stale role text',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /stale role text/);

  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_version_append',
    id: 'bov-r2',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    version_number: 2,
    text_sha256: 'newhash',
    text_bytes: 13,
  });
  context.behaviorOverlayApplyDelta({
    op: 'behavior_overlay_active_update',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active_version_id: 'bov-r2',
  });
  context.renderAgentPanel();

  assert.doesNotMatch(panel.innerHTML, /stale role text/);
  assert.match(panel.innerHTML, /Refreshing inherited role overlay text/);
  assert.ok(sendCalls.some((call) => call.cmd === 'behavior_overlay_read'
    && call.scope_kind === 'role'
    && call.scope_group === 'alpha'
    && call.scope_key === 'engineer'));
});

test('role pane rerender routes through capture and restore', () => {
  const { context, captureCalls, restoreCalls } = createHarness();
  const elements = {};
  function ensure(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        innerHTML: '',
        value: '',
        selectionStart: 0,
        selectionEnd: 0,
        scrollTop: 0,
        scrollLeft: 0,
        focused: false,
        contains(el) { return el && (el === this || el._insideRolePane); },
        querySelector() { return null; },
        focus() { this.focused = true; },
      };
    }
    return elements[id];
  }
  const panel = context.document.getElementById('panel-agent');
  context.document.getElementById = function(id) {
    if (id === 'panel-agent') return panel;
    return ensure(id);
  };
  const textarea = ensure('behavior-overlay-text-role-user-role-alpha-engineer');
  textarea.value = 'operator draft';
  textarea.selectionStart = 4;
  textarea.selectionEnd = 12;
  textarea._insideRolePane = true;
  context.document.activeElement = textarea;
  context._captureSurfaceState = function(root, opts) {
    captureCalls.push({ root, opts });
    return {
      focus: {
        key: '#' + textarea.id,
        value: textarea.value,
        selectionStart: textarea.selectionStart,
        selectionEnd: textarea.selectionEnd,
        scrollTop: textarea.scrollTop,
        scrollLeft: textarea.scrollLeft,
      },
      scrolls: [{ selector: ':root', top: root.scrollTop || 0, left: root.scrollLeft || 0 }],
    };
  };
  context._restoreSurfaceState = function(root, snapshot) {
    restoreCalls.push({ root, snapshot });
    const focused = ensure(snapshot.focus.key.slice(1));
    focused.value = snapshot.focus.value;
    focused.selectionStart = snapshot.focus.selectionStart;
    focused.selectionEnd = snapshot.focus.selectionEnd;
    focused.focus();
  };
  vm.runInContext(`_settingsGroup = 'alpha';`, context);
  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay',
    scope_kind: 'role',
    scope_group: 'alpha',
    scope_key: 'engineer',
    active: { scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', active_version_id: 'bov-r1' },
    version: { id: 'bov-r1', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', version_number: 1, text_sha256: 'hash', text_bytes: 3 },
    text: 'old',
  });

  assert.ok(captureCalls.length > 0);
  assert.ok(restoreCalls.length > 0);
  assert.equal(textarea.focused, true);
  assert.equal(textarea.value, 'operator draft');
  assert.equal(textarea.selectionStart, 4);
  assert.equal(textarea.selectionEnd, 12);
});

test('role-scope behavior approval modal renders diff before enabling user actions', () => {
  const { context, sendCalls } = createHarness();
  const elements = {};
  function fakeClassList() {
    const set = new Set();
    return {
      add(name) { set.add(name); },
      remove(name) { set.delete(name); },
      contains(name) { return set.has(name); },
    };
  }
  function ensure(id) {
    if (!elements[id]) {
      elements[id] = {
        id,
        innerHTML: '',
        value: '',
        disabled: false,
        classList: fakeClassList(),
      };
    }
    return elements[id];
  }
  const panel = context.document.getElementById('panel-agent');
  context.document.getElementById = function(id) {
    if (id === 'panel-agent') return panel;
    return ensure(id);
  };
  context.openNestedModal = function(id) { ensure(id).classList.add('visible'); };
  context.closeNestedModal = function(id) { ensure(id).classList.remove('visible'); return true; };
  context.state.board_tasks = {
    'TORQUE:2': {
      id: 'TORQUE:2',
      task: 'Dynamic Behavior overlay approval',
      lane: 'Backlog',
      labels: ['torque:human', 'behavior-overlay-approval', 'proposal:bop-role', 'scope:role', 'role:engineer', 'group:alpha'],
    },
  };
  context.state.behavior_overlay_proposals = {
    'bop-role': {
      id: 'bop-role',
      scope_kind: 'role',
      scope_group: 'alpha',
      scope_key: 'engineer',
      target_kind: 'engineer',
      base_version_id: 'bov-role-base',
      proposed_by_kind: 'architect',
      proposed_by_agent_id: 'arch-1',
      proposed_text_sha256: 'role-hash-123',
      status: 'proposed',
      next_actor_kind: 'user',
      rationale: 'Shared engineer defaults',
    },
  };

  assert.match(context.behaviorOverlayApprovalCardHtml(context.state.board_tasks['TORQUE:2']), /Role behavior overlay approval/);
  assert.match(context.behaviorOverlayApprovalCardHtml(context.state.board_tasks['TORQUE:2']), /Engineer role overlay · alpha/);
  context.openBehaviorOverlayApprovalModal('TORQUE:2');

  assert.equal(ensure('behavior-approval-approve-btn').disabled, true);
  assert.equal(ensure('behavior-approval-reject-btn').disabled, true);
  assert.match(ensure('behavior-approval-body').innerHTML, /Loading required diff/);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.slice(-2))), [
    { cmd: 'behavior_overlay_proposals', status_filter: '', limit: 200 },
    { cmd: 'behavior_overlay_diff', proposal_id: 'bop-role' },
  ]);

  context.behaviorOverlayReceiveMessage({
    type: 'behavior_overlay_diff',
    proposal: {
      id: 'bop-role',
      scope_kind: 'role',
      scope_group: 'alpha',
      scope_key: 'engineer',
      target_kind: 'engineer',
      base_version_id: 'bov-role-base',
      proposed_by_kind: 'architect',
      proposed_by_agent_id: 'arch-1',
      proposed_text_sha256: 'role-hash-123',
      rationale: 'Shared engineer defaults',
      status: 'proposed',
      next_actor_kind: 'user',
    },
    from_version: { id: 'bov-role-base', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer' },
    to_proposal: { id: 'bop-role', scope_kind: 'role', scope_group: 'alpha', scope_key: 'engineer', proposed_text_sha256: 'role-hash-123' },
    diff: '--- bov-role-base\n+++ bop-role\n@@ -0,0 +1 @@\n+shared default\n',
  });

  assert.equal(ensure('behavior-approval-approve-btn').disabled, false);
  assert.equal(ensure('behavior-approval-reject-btn').disabled, false);
  assert.match(ensure('behavior-approval-body').innerHTML, /role · alpha/);
  assert.match(ensure('behavior-approval-body').innerHTML, /diff-line-add/);
  ensure('behavior-approval-note').value = 'Approve shared defaults';
  context.behaviorOverlayUserApprove();
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'behavior_overlay_user_approve',
    proposal_id: 'bop-role',
    expected_proposed_text_sha256: 'role-hash-123',
    expected_base_version_id: 'bov-role-base',
    note: 'Approve shared defaults',
  });
});

test('agent panel folds MCP into Events subtabs for standalone and toolbelt modes', () => {
  const { context, panel } = createHarness();
  const expectedSpecs = {
    architect: ['decisions', 'behavior', 'journal', 'messages', 'events'],
    engineer: ['journal', 'behavior', 'events', 'queued', 'worklog'],
    worker: ['events', 'messages', 'worklog'],
  };
  const agents = {
    architect: { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent' },
    engineer: { id: 'eng-1', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    worker: { id: 'worker-1', name: 'Worker Bee', kind: 'worker', group: 'alpha', cell_type: 'agent' },
  };
  const runtimes = [
    { mode: 'standalone', embedded_terminal: true },
    { mode: 'toolbelt', embedded_terminal: false },
  ];

  for (const runtime of runtimes) {
    context.state.runtime = runtime;
    for (const kind of Object.keys(expectedSpecs)) {
      assert.deepEqual(
        JSON.parse(JSON.stringify(vm.runInContext(
          `_agentPanelTabSpec('${kind}').map(function(tab) { return tab.key; })`,
          context
        ))),
        expectedSpecs[kind],
        kind + ' tab spec in ' + runtime.mode
      );
      context.state.agents = { [agents[kind].id]: agents[kind] };
      context.focusedItemId = agents[kind].id;
      vm.runInContext(`_agentPanelLastSelectedTabByKind.${kind} = 'events';`, context);
      context.renderAgentPanel();
      assert.doesNotMatch(panel.innerHTML, /id="agent-panel-tab-mcp"/,
        kind + ' must not expose a top-level MCP tab in ' + runtime.mode);
      assert.match(
        panel.innerHTML,
        /data-agent-panel-events-inner-tab="inbox"[\s\S]*data-agent-panel-events-inner-tab="lifecycle"[\s\S]*data-agent-panel-events-inner-tab="mcp"/,
        kind + ' Events subtabs must be inbox, lifecycle, mcp in ' + runtime.mode
      );
    }
  }

  const source = fs.readFileSync(path.join(repoRoot, 'static/js/agent_panel.js'), 'utf8');
  assert.equal(source.includes("activeTab === 'mcp'"), false);
});

test('architect hierarchy renderer uses worker kind badges with worker-specific class', () => {
  const { context } = createHarness();
  context._esc = function(value) { return String(value); };
  context.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Worker Bee',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-1',
      cell_type: 'agent',
    },
  };
  context.state.groups.alpha = ['eng-1', 'worker-1'];

  const html = vm.runInContext(`_agentPanelLegacyRenderEngineerTreeRows(
    'alpha',
    [state.agents['eng-1']],
    'architect-roster-level-1 architect-section-engineer-row',
    'architect-roster-level-2 architect-section-worker-row'
  )`, context);

  assert.match(html, /class="engineer-row-kind engineer-row-kind-engineer">engineer<\/span>/);
  assert.match(html, /class="engineer-row-kind engineer-row-kind-worker">worker<\/span>/);
});

test('agent panel roster marks architect-owned and user-owned hierarchy rows distinctly', () => {
  const { context } = createHarness();
  context._esc = function(value) { return String(value); };
  context.state.agents = {
    'eng-arch': {
      id: 'eng-arch',
      name: 'Architect Engineer',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-arch': {
      id: 'worker-arch',
      name: 'Architect Worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-arch',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
    'worker-user': {
      id: 'worker-user',
      name: 'User Worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-user',
      cell_type: 'agent',
    },
  };
  context.state.groups.alpha = ['eng-arch', 'worker-arch', 'eng-user', 'worker-user'];

  const architectTreeHtml = vm.runInContext(`_agentPanelLegacyRenderEngineerTreeRows(
    'alpha',
    [state.agents['eng-arch']],
    'architect-roster-level-1 architect-section-engineer-row',
    'architect-roster-level-2 architect-section-worker-row'
  )`, context);
  assert.match(architectTreeHtml, /agent-panel-hierarchy-branch agent-panel-hierarchy-branch-architect has-workers/);
  assert.match(architectTreeHtml, /agent-panel-hierarchy-children/);
  assert.match(architectTreeHtml, /architect-roster-level-1 architect-section-engineer-row/);
  assert.match(architectTreeHtml, /architect-roster-level-2 architect-section-worker-row/);

  const userRosterHtml = vm.runInContext(`_agentPanelLegacyRenderEngineerRoster('alpha')`, context);
  assert.match(userRosterHtml, /engineers-roster-list agent-panel-hierarchy-list agent-panel-hierarchy-list-user agent-panel-hierarchy-list-rooted/);
  assert.match(userRosterHtml, /engineer-row-virtual-parent/);
  assert.match(userRosterHtml, /agent-panel-hierarchy-branch agent-panel-hierarchy-branch-user has-workers/);
  assert.match(userRosterHtml, /engineer-roster-level-1 user-section-engineer-row/);
  assert.match(userRosterHtml, /engineer-roster-level-2 user-section-worker-row/);
  assert.doesNotMatch(userRosterHtml, /architect-section-engineer-row|architect-section-worker-row/);
});

test('worker panel header shows clickable upward architect and engineer parent chain', () => {
  const { context, panel, sendCalls, restoreCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Panelsmith',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Hierarchy worker',
      kind: 'worker',
      group: 'alpha',
      owner_engineer_id: 'eng-1',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'worker-1';

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /agent-panel-header-breadcrumb/);
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect"/);
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-worker current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer[\s\S]*ENGINEER[\s\S]*Panelsmith[\s\S]*WORKER[\s\S]*Hierarchy worker/);

  const restoreCountBeforeClick = restoreCalls.length;
  vm.runInContext(`agentPanelFocusHierarchyTarget("eng-1", "engineer", "alpha")`, context);

  assert.equal(context.focusedItemId, 'eng-1');
  assert.match(panel.innerHTML, /Engineer: Panelsmith · Group: alpha/);
  assert.ok(
    sendCalls.some((msg) => msg.cmd === 'focus_agent' && msg.id === 'eng-1'),
    'breadcrumb focus should send focus_agent even if the engineer panel also refreshes specialization options',
  );
  assert.ok(restoreCalls.length > restoreCountBeforeClick, 'breadcrumb focus rerender restores panel state');
});

test('engineer panel header shows architect or User parent chain with current self chip', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-hired': {
      id: 'eng-hired',
      name: 'Hired Engineer',
      kind: 'engineer',
      group: 'alpha',
      hired_by_architect_id: 'arch-1',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };

  context.focusedItemId = 'eng-hired';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer[\s\S]*ENGINEER[\s\S]*Hired Engineer/);

  context.focusedItemId = 'eng-user';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /button type="button" class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-user"/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-engineer current"/);
  assert.match(panel.innerHTML, /USER[\s\S]*User[\s\S]*ENGINEER[\s\S]*User Engineer/);
});

test('architect and user panel headers show only their current hierarchy chip', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Torquer',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-user': {
      id: 'eng-user',
      name: 'User Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };

  context.focusedItemId = 'arch-1';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-architect current"/);
  assert.match(panel.innerHTML, /ARCH[\s\S]*Torquer/);
  assert.doesNotMatch(panel.innerHTML, /agentPanelFocusHierarchyTarget/);

  vm.runInContext(`agentPanelFocusHierarchyTarget("", "user", "alpha")`, context);
  assert.equal(context.focusedItemId, 'principal:alpha:user');
  assert.match(panel.innerHTML, /User · Group: alpha/);
  assert.match(panel.innerHTML, /span class="agent-panel-hierarchy-crumb agent-panel-hierarchy-crumb-user current"/);
  assert.match(panel.innerHTML, /USER[\s\S]*User/);
});

test('agentPanelSelectTab remembers the last selected tab per kind', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.agentPanelSelectTab('worklog');

  assert.equal(vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer`, context), 'worklog');
  assert.match(panel.innerHTML, /data-agent-panel-tab="worklog"/);
  assert.match(panel.innerHTML, /id="agent-panel-tab-worklog" class="agent-panel-tab active"/);
});

test('_resolveFocusedAgent returns null when focusedItemId does not resolve', () => {
  const { context } = createHarness();
  context.focusedItemId = 'missing-agent';

  assert.equal(vm.runInContext(`_resolveFocusedAgent()`, context), null);
});

test('_resolveFocusedAgent resolves architect principal nav ids to the architect agent', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:arch-a';
  context.window._navGridItemMeta = {
    'principal:alpha:arch-a': { type: 'principal', principalId: 'arch-a', group: 'alpha' },
  };

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'architect principal nav id should resolve to the architect agent');
  assert.equal(agent.id, 'arch-a');
});

test('_resolveFocusedAgent resolves architect principal nav ids by string fallback when nav meta is missing', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:arch-a';

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'string fallback should resolve architect principal nav id');
  assert.equal(agent.id, 'arch-a');
});

test('_resolveFocusedAgent resolves the user principal nav id to a virtual user principal', () => {
  const { context } = createHarness();
  context.state.agents['arch-a'] = { id: 'arch-a', kind: 'architect', group: 'alpha', name: 'Arch' };
  context.focusedItemId = 'principal:alpha:user';
  context.window._navGridItemMeta = {
    'principal:alpha:user': { type: 'principal', principalId: '', group: 'alpha' },
  };

  const agent = vm.runInContext(`_resolveFocusedAgent()`, context);
  assert.ok(agent, 'user principal nav id should resolve to a virtual user panel target');
  assert.equal(agent.kind, 'user');
  assert.equal(agent.group, 'alpha');
  assert.equal(agent.id, 'principal:alpha:user');
});

test('engineer panel renders journal for the focused engineer only', () => {
  const { context, panel } = createHarness();
  context.state.group_settings = { alpha: { engineer_agent_id: 'engineer-1' } };
  context.state.agents['engineer-1'] = { id: 'engineer-1', name: 'Engineer', group: 'alpha' };
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  context.state.engineer_journal['eng-1'] = [
    { id: 2, type: 'decision', entry: 'Approved the refactor', timestamp: 50, author_cell_id: 'eng-1' },
  ];
  context.state.engineer_journal['eng-2'] = [
    { id: 3, type: 'decision', entry: 'Other engineer note', timestamp: 51, author_cell_id: 'eng-2' },
  ];
  context.state.digest_buffer_stats['eng-1'] = {
    agent_id: 'eng-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 60,
    queued_events: [
      { id: 3, kind: 'task_completed', message: 'Queued digest item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['eng-1'] = [
    { id: 2, kind: 'task_completed', message: 'Delivered digest item', timestamp: 35, delivered_at: 38 },
  ];
  context.state.engineer_worklog.alpha = [
    {
      id: 9,
      task_id: 'TORQUE:9',
      task_title: 'Ship engineer panel',
      agent_id: 'eng-1',
      agent_name: 'Builder',
      started_at: 30,
    },
  ];
  context.state.board_tasks['TORQUE:9'] = {
    id: 'TORQUE:9',
    task: 'Ship engineer panel',
    lane: 'Review',
    status: 'In progress',
    group: 'alpha',
    agent_id: 'eng-1',
  };
  context.state.board_tasks['TORQUE:10'] = {
    id: 'TORQUE:10',
    task: 'Queued backlog item',
    lane: 'Backlog',
    status: 'Waiting',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    position: 1,
  };
  context.state.board_tasks['TORQUE:11'] = {
    id: 'TORQUE:11',
    task: 'Queued in-progress item',
    lane: 'In Progress',
    status: 'Running',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    position: 2,
  };
  context.state.board_tasks['TORQUE:12'] = {
    id: 'TORQUE:12',
    task: 'Finished item should be completed only',
    lane: 'Done',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
  };
  context.state.board_tasks['TORQUE:13'] = {
    id: 'TORQUE:13',
    task: 'Other engineer item',
    lane: 'Backlog',
    group: 'alpha',
    assigned_engineer_id: 'eng-2',
  };
  context.state.board_tasks['TORQUE:14'] = {
    id: 'TORQUE:14',
    task: 'Engineer message followup',
    lane: 'To Do',
    group: 'alpha',
    assigned_engineer_id: 'eng-1',
    labels: ['torque:engineer-message'],
  };

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Approved the refactor/);
  assert.doesNotMatch(panel.innerHTML, /Other engineer note/);
  assert.doesNotMatch(panel.innerHTML, /Group: alpha \(group-wide\)/);

  context.agentPanelSelectTab('events');
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn"/);
  assert.match(panel.innerHTML, /Queued digest item/);
  assert.match(panel.innerHTML, /Already digested to Builder/);
  assert.match(panel.innerHTML, /Delivered digest item/);

  context.agentPanelSelectTab('queued');
  assert.match(panel.innerHTML, /id="agent-panel-tab-queued" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /Queued tasks/);
  assert.match(panel.innerHTML, /Queued backlog item/);
  assert.match(panel.innerHTML, /Queued in-progress item/);
  assert.doesNotMatch(panel.innerHTML, /Finished item should be completed only/);
  assert.doesNotMatch(panel.innerHTML, /Other engineer item/);
  assert.doesNotMatch(panel.innerHTML, /Engineer message followup/);

  context.agentPanelSelectTab('worklog');
  assert.match(panel.innerHTML, /Completed tasks/);
  assert.match(panel.innerHTML, /Ship engineer panel/);
  assert.match(panel.innerHTML, /Review/);
});

test('worker panel filters per-cell events and task history to the focused worker', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });

  context.state.panel_events = [
    { id: 2, cell_id: 'worker-2', kind: 'task_completed', message: 'Ignore me', timestamp: 20 },
    { id: 3, cell_id: 'worker-1', kind: 'task_completed', message: 'Finished compile step', timestamp: 30, task_id: 'TORQUE:3' },
  ];
  context.state.board_tasks = {
    'TORQUE:2': { id: 'TORQUE:2', task: 'Ignore task', agent_id: 'worker-2', lane: 'Done', status: 'done', created_at: 15 },
    'TORQUE:3': { id: 'TORQUE:3', task: 'Compile feature branch', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 25 },
  };
  vm.runInContext(`_agentPanelEventsInnerTabByAgentId['worker-1'] = 'lifecycle';`, context);

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Finished compile step/);
  assert.doesNotMatch(panel.innerHTML, /Ignore me/);

  context.agentPanelSelectTab('worklog');
  assert.match(panel.innerHTML, /Compile feature branch/);
  assert.doesNotMatch(panel.innerHTML, /Ignore task/);
});

test('focused engineer events tab renders server-merged cell events', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);
  vm.runInContext(`_agentPanelEventsInnerTabByAgentId['eng-1'] = 'lifecycle';`, context);

  context.renderAgentPanel();

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), {
    cmd: 'get_cell_events',
    cell_id: 'eng-1',
    limit: 200,
  });
  assert.match(panel.innerHTML, /Loading cell events/);

  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'eng-1',
    events: [
      {
        id: 9,
        cell_id: 'eng-1',
        kind: 'task_dispatched',
        message: 'Persisted dispatch survived restart',
        timestamp: 20,
        source: 'panel_events',
      },
      {
        id: 'live:25',
        cell_id: 'eng-1',
        kind: 'tool_start',
        message: 'Live tool call',
        timestamp: 25,
        source: 'event_log',
      },
    ],
  });

  assert.match(panel.innerHTML, /Cell events/);
  assert.match(panel.innerHTML, /Persisted dispatch survived restart/);
  assert.match(panel.innerHTML, /Live tool call/);
});

test('architect Journal header keeps entry and decision counts grouped', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.architect_journals = {
    'arch-1': [
      { id: 'j-2', architect_id: 'arch-1', type: 'observation', entry: 'Second journal entry', timestamp: 20 },
      { id: 'j-1', architect_id: 'arch-1', type: 'observation', entry: 'First journal entry', timestamp: 10 },
    ],
  };
  context.state.decisions = {
    'decision-1': { id: 'decision-1', architect_id: 'arch-1', title: 'Adopt layout', status: 'accepted' },
  };
  context.focusedItemId = 'arch-1';
  vm.runInContext(`_agentPanelLastSelectedTabByKind.architect = 'journal';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /data-agent-panel-kind="architect" data-agent-panel-tab="journal"/);
  assert.match(
    panel.innerHTML,
    /<span class="agent-panel-worklog-title">Journal<\/span><span class="agent-panel-worklog-count" data-agent-panel-journal-count>2<\/span><span class="agent-panel-worklog-note"> · 1 decision<\/span>/
  );

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(
    css,
    /\.agent-panel-panel\[data-agent-panel-kind="architect"\]\[data-agent-panel-tab="journal"\] \.agent-panel-worklog-header\s*\{[^}]*justify-content:\s*flex-start;/
  );
  assert.match(css, /\.agent-panel-worklog-header\s*\{[^}]*justify-content:\s*space-between;/);
});

test('architect panel filters decisions and messages to the focused architect', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [{ action: 'architect_reply', message: 'Need a follow-up worker', timestamp: 60 }],
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-1',
    },
    'eng-2': {
      id: 'eng-2',
      name: 'Other Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
      hired_by_architect_id: 'arch-2',
    },
  };
  context.focusedItemId = 'arch-1';
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Adopt the new layout',
      status: 'accepted',
      updated_at: 20,
    },
    'decision-2': {
      id: 'decision-2',
      architect_id: 'arch-2',
      title: 'Do not show me',
      status: 'accepted',
      updated_at: 10,
    },
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Adopt the new layout/);
  assert.doesNotMatch(panel.innerHTML, /Do not show me/);

  context.agentPanelSelectTab('messages');
  assert.match(panel.innerHTML, /Need a follow-up worker/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-message-note/);
});

test('architect Messages tab renders full-height message cards instead of the compact MCP preview', () => {
  const { context, panel } = createHarness();
  const longBody = 'Line one with enough detail to be useful to the operator.\n'
    + 'Line two remains visible instead of being collapsed into a preview snippet.';
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        {
          id: 'msg-in',
          action: 'engineer_message_architect',
          message: longBody,
          timestamp: 1712345678,
          sender_kind: 'engineer',
          direction: 'received',
        },
        {
          id: 'msg-out',
          action: 'architect_reply',
          message: 'Thanks, keep going with the full plan.',
          timestamp: 1712345688,
          sender_kind: 'architect',
          direction: 'sent',
        },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-messages-tab/);
  assert.match(panel.innerHTML, /agent-panel-message-list/);
  assert.match(panel.innerHTML, /agent-panel-message-card/);
  assert.match(panel.innerHTML, /Engineer/);
  assert.match(panel.innerHTML, /In/);
  assert.match(panel.innerHTML, /Architect/);
  assert.match(panel.innerHTML, /Out/);
  assert.match(panel.innerHTML, /2024-04-05 19:34:38 UTC/);
  assert.ok(panel.innerHTML.includes(longBody));
  assert.doesNotMatch(panel.innerHTML, /class="mcp-log"/);
  assert.doesNotMatch(panel.innerHTML, /class="mcp-text"/);
});

test('architect Messages tab labels outgoing, incoming, and three-engineer messages', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': { id: 'arch-1', name: 'Planner', kind: 'architect', group: 'alpha', cell_type: 'agent',
      mcp_messages: [
        { id: 'msg-c', action: 'engineer_message_architect', message: 'Need a decision.', timestamp: 300,
          sender_id: 'eng-c', sender_kind: 'engineer', peer_id: 'eng-c', peer_kind: 'engineer', direction: 'received' },
        { id: 'msg-b', action: 'architect_message', message: 'Please verify.', timestamp: 200,
          sender_id: 'arch-1', sender_kind: 'architect', peer_id: 'eng-b', peer_kind: 'engineer', direction: 'sent' },
        { id: 'msg-a', action: 'architect_message', message: 'Please build.', timestamp: 100,
          sender_id: 'arch-1', sender_kind: 'architect', peer_id: 'eng-a', peer_kind: 'engineer', direction: 'sent' },
      ] },
    'eng-a': { id: 'eng-a', name: 'Builder', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'eng-b': { id: 'eng-b', name: 'Verifier', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
    'eng-c': { id: 'eng-c', name: 'Debugger', kind: 'engineer', group: 'alpha', cell_type: 'agent' },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-message-attribution-in/);
  assert.match(panel.innerHTML, /agent-panel-message-attribution-out/);
  assert.match(panel.innerHTML, /From:<\/span><span class="agent-panel-message-attribution-name">Debugger/);
  assert.match(panel.innerHTML, /To:<\/span><span class="agent-panel-message-attribution-name">Verifier/);
  assert.match(panel.innerHTML, /To:<\/span><span class="agent-panel-message-attribution-name">Builder/);
});

test('architect Messages tab renders peer-message affordances and context refs', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        {
          id: 'peer-msg-1',
          action: 'architect_peer_message',
          message: 'Can you sanity-check the rollout?',
          timestamp: 1712345688,
          sender_id: 'arch-2',
          sender_kind: 'architect',
          recipient_id: 'arch-1',
          recipient_kind: 'architect',
          peer_id: 'arch-2',
          peer_kind: 'architect',
          direction: 'received',
          ack_required: true,
          context_task_ids: ['TORQUE:101'],
          context_engineer_ids: ['eng-1'],
          context_decision_ids: ['decision-1'],
          context_summary: 'API ownership is ambiguous.',
        },
      ],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'eng-1': {
      id: 'eng-1',
      name: 'Builder',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.board_tasks = {
    'TORQUE:101': {
      id: 'TORQUE:101',
      task: 'Roll out peer messaging',
      group: 'alpha',
    },
  };
  context.state.decisions = {
    'decision-1': {
      id: 'decision-1',
      architect_id: 'arch-1',
      title: 'Use direct peer messages',
      status: 'accepted',
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /Peer Architect · alpha/);
  assert.match(panel.innerHTML, /Ack required/);
  assert.match(panel.innerHTML, /API ownership is ambiguous\./);
  assert.match(panel.innerHTML, /TORQUE:101 · Roll out peer messaging/);
  assert.match(panel.innerHTML, /Builder/);
  assert.match(panel.innerHTML, /decision-1 · Use direct peer messages/);
});

test('architect Messages tab prefers peer names over architect IDs for peer messages', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'blueprint': {
      id: 'blueprint',
      name: 'Blueprint',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        {
          id: 'peer-msg-name',
          action: 'architect_peer_message',
          message: 'PM polish scope accepted.',
          timestamp: 1712345688,
          sender_id: 'torqly-id',
          sender_name: 'Torqly',
          sender_kind: 'architect',
          recipient_id: 'blueprint',
          recipient_name: 'Blueprint',
          recipient_kind: 'architect',
          peer_id: 'torqly-id',
          peer_name: 'Torqly',
          peer_kind: 'architect',
          direction: 'received',
        },
      ],
    },
  };
  context.focusedItemId = 'blueprint';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /From:<\/span><span class="agent-panel-message-attribution-name">Torqly/);
  assert.match(panel.innerHTML, /agent-panel-message-peer[^>]*>Torqly/);
  assert.doesNotMatch(panel.innerHTML, /From:<\/span><span class="agent-panel-message-attribution-name">torqly-id/);
});

test('architect Messages tab does not render peer compose controls or request peer list', () => {
  const { context, panel, sendCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');

  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-compose/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-select/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-compose-body/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-compose-ack/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-context/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-send/);
  assert.doesNotMatch(panel.innerHTML, /Send peer message/);
  assert.doesNotMatch(panel.innerHTML, /Attach context/);
  assert.equal(sendCalls.some((call) => call.cmd === 'architect_peer_list'), false);
  assert.equal(sendCalls.some((call) => call.cmd === 'architect_peer_message'), false);
});

test('architect Messages peer-message rerenders remain display-only', () => {
  const { context, panel, sendCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [],
    },
    'arch-2': {
      id: 'arch-2',
      name: 'Peer Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');

  context.state.agents['arch-1'].mcp_messages = [
    {
      id: 'peer-msg-new',
      action: 'architect_peer_message',
      message: 'Fresh peer update',
      timestamp: 200,
      peer_id: 'arch-2',
      peer_kind: 'architect',
      direction: 'received',
      ack_required: true,
    },
  ];
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Fresh peer update/);
  assert.match(panel.innerHTML, /Peer Architect · alpha/);
  assert.match(panel.innerHTML, /Ack required/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-peer-compose/);
  assert.doesNotMatch(panel.innerHTML, /Send peer message/);
  assert.equal(sendCalls.some((call) => call.cmd === 'architect_peer_list'), false);
  assert.equal(sendCalls.some((call) => call.cmd === 'architect_peer_message'), false);
});

test('architect peer compose options drop removed and dismissed peers from fallback/cache state', () => {
  const { context } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'arch-active': {
      id: 'arch-active',
      name: 'Active Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
    },
    'arch-dismissed': {
      id: 'arch-dismissed',
      name: 'Dismissed Architect',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      dismissed_at: 123,
    },
  };

  assert.deepEqual(
    JSON.parse(JSON.stringify(
      context._agentPanelArchitectPeerListFromState(context.state.agents['arch-1']).map((peer) => peer.id)
    )),
    ['arch-active']
  );

  context.agentPanelReceiveArchitectPeerList({
    architect_id: 'arch-1',
    architects: [
      { id: 'arch-active', name: 'Active Cached', group: 'alpha' },
      { id: 'arch-removed', name: 'Removed Cached', group: 'alpha' },
      { id: 'arch-dismissed', name: 'Dismissed Cached', group: 'alpha' },
    ],
  });
  context.agentPanelPeerComposeInput('arch-1', 'peer_id', 'arch-removed');

  const html = context._agentPanelArchitectPeerComposeHtml(context.state.agents['arch-1']);

  assert.match(html, /Active Cached/);
  assert.doesNotMatch(html, /Removed Cached/);
  assert.doesNotMatch(html, /Dismissed Cached/);
  assert.equal(context._agentPanelArchitectPeerComposeDrafts['arch-1'].peer_id, '');
});

test('worker Messages tab renders inline task thread entries', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'eng-1': {
      id: 'eng-1',
      name: 'Engineer',
      kind: 'engineer',
      group: 'alpha',
      cell_type: 'agent',
    },
    'worker-1': {
      id: 'worker-1',
      name: 'Worker',
      kind: 'worker',
      group: 'alpha',
      cell_type: 'agent',
    },
  };
  context.state.board_tasks = {
    'task-1': {
      id: 'task-1',
      task: 'Parent task',
      group: 'alpha',
      agent_id: 'worker-1',
      messages_thread: [
        {
          timestamp: 1712345600,
          sender_agent_id: 'eng-1',
          recipient_agent_id: 'worker-1',
          content: 'Use the smaller repro before continuing.',
          reply_required: false,
        },
      ],
    },
  };
  context.focusedItemId = 'worker-1';

  context.agentPanelSelectTab('messages');

  assert.match(panel.innerHTML, /agent-panel-messages-tab/);
  assert.match(panel.innerHTML, /Use the smaller repro before continuing\./);
  assert.match(panel.innerHTML, /Engineer/);
  assert.match(panel.innerHTML, /In/);
  assert.match(panel.innerHTML, /engineer message/);
  assert.match(panel.innerHTML, /Inline Engineer messages stored/);
});

test('architect Messages tab renders newest-first regardless of source array order', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-oldest', action: 'architect_message', message: 'OLDEST-BODY', timestamp: 100 },
        { id: 'm-middle', action: 'architect_message', message: 'MIDDLE-BODY', timestamp: 200 },
        { id: 'm-newest', action: 'architect_message', message: 'NEWEST-BODY', timestamp: 300 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  const html = panel.innerHTML;
  const iOldest = html.indexOf('OLDEST-BODY');
  const iMiddle = html.indexOf('MIDDLE-BODY');
  const iNewest = html.indexOf('NEWEST-BODY');
  assert.ok(iNewest >= 0 && iMiddle >= 0 && iOldest >= 0, 'all three messages should render');
  assert.ok(iNewest < iMiddle, 'newest message card must appear before middle card in DOM order');
  assert.ok(iMiddle < iOldest, 'middle message card must appear before oldest card in DOM order');
});

test('architect Messages tab keeps newest-first when the source array is already newest-first (server insert-at-0 shape)', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-newest', action: 'architect_message', message: 'NEWEST-BODY', timestamp: 300 },
        { id: 'm-middle', action: 'architect_message', message: 'MIDDLE-BODY', timestamp: 200 },
        { id: 'm-oldest', action: 'architect_message', message: 'OLDEST-BODY', timestamp: 100 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('messages');

  const html = panel.innerHTML;
  const iOldest = html.indexOf('OLDEST-BODY');
  const iMiddle = html.indexOf('MIDDLE-BODY');
  const iNewest = html.indexOf('NEWEST-BODY');
  assert.ok(iNewest < iMiddle && iMiddle < iOldest,
    'newest-first ordering must be stable regardless of source array order');
});

test('architect Messages tab promotes a newly arrived message to the top on rerender without disturbing existing ordering', () => {
  const { context, panel } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-old1', action: 'architect_message', message: 'OLD-ONE-BODY', timestamp: 100 },
        { id: 'm-old2', action: 'architect_message', message: 'OLD-TWO-BODY', timestamp: 200 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  const firstHtml = panel.innerHTML;
  assert.ok(firstHtml.indexOf('OLD-TWO-BODY') < firstHtml.indexOf('OLD-ONE-BODY'),
    'baseline: newer OLD-TWO-BODY must render before older OLD-ONE-BODY');

  // Simulate a WS delta that prepends a new message (server inserts at index 0 with newer timestamp).
  context.state.agents['arch-1'].mcp_messages = [
    { id: 'm-brand-new', action: 'architect_message', message: 'BRAND-NEW-BODY', timestamp: 500 },
    { id: 'm-old1', action: 'architect_message', message: 'OLD-ONE-BODY', timestamp: 100 },
    { id: 'm-old2', action: 'architect_message', message: 'OLD-TWO-BODY', timestamp: 200 },
  ];
  context.renderAgentPanel();

  const secondHtml = panel.innerHTML;
  const iNew = secondHtml.indexOf('BRAND-NEW-BODY');
  const iOld2 = secondHtml.indexOf('OLD-TWO-BODY');
  const iOld1 = secondHtml.indexOf('OLD-ONE-BODY');
  assert.ok(iNew >= 0 && iOld2 >= 0 && iOld1 >= 0, 'all three messages render after rerender');
  assert.ok(iNew < iOld2 && iOld2 < iOld1,
    'brand-new message must take the top slot; pre-existing ordering must remain newest-first below it');
});

test('architect Messages tab rerender routes through capture/restore so scroll + focus survive delta updates', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();
  context.state.agents = {
    'arch-1': {
      id: 'arch-1',
      name: 'Planner',
      kind: 'architect',
      group: 'alpha',
      cell_type: 'agent',
      mcp_messages: [
        { id: 'm-a', action: 'architect_message', message: 'first', timestamp: 100 },
        { id: 'm-b', action: 'architect_message', message: 'second', timestamp: 200 },
      ],
    },
  };
  context.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('messages');
  const captureBefore = captureCalls.length;
  const restoreBefore = restoreCalls.length;

  // Rerender via state mutation (simulate WS delta)
  context.state.agents['arch-1'].mcp_messages = [
    { id: 'm-c', action: 'architect_message', message: 'third', timestamp: 300 },
    { id: 'm-a', action: 'architect_message', message: 'first', timestamp: 100 },
    { id: 'm-b', action: 'architect_message', message: 'second', timestamp: 200 },
  ];
  context.renderAgentPanel();

  assert.ok(captureCalls.length > captureBefore, 'rerender must invoke _captureSurfaceState to snapshot scroll/focus');
  assert.ok(restoreCalls.length > restoreBefore, 'rerender must invoke _restoreSurfaceState to reapply snapshot');
  const latestCapture = captureCalls[captureCalls.length - 1];
  const scrollSelectors = JSON.parse(JSON.stringify(latestCapture.opts.scrollSelectors));
  assert.ok(scrollSelectors.indexOf('.agent-panel-message-list') >= 0,
    'capture must include .agent-panel-message-list so message-list scroll is anchored across rerenders');
});

test('focused architect decision interactions rerender the agent panel instead of the legacy engineer surface', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.decisions = {
    d1: {
      id: 'd1',
      architect_id: 'arch-1',
      title: 'Keep the focused agent surface stable',
      status: 'proposed',
      updated_at: 40,
    },
  };

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);

  context.engineerToggleDecision('d1');

  assert.match(panel.innerHTML, /Architect: Planner · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.match(panel.innerHTML, /architect-decision-body/);
  assert.equal(vm.runInContext(`_engineerDecisionUiState('d1').expanded`, context), true);
});

test('focused architect decision rows render parseable click handlers and expanded details', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.decisions = {
    d1: {
      id: 'd1',
      architect_id: 'arch-1',
      title: 'Keep the focused agent surface stable',
      rationale: 'Show the operator why this direction was chosen.',
      status: 'accepted',
      linked_task_ids: ['TORQUE:101'],
      linked_engineer_ids: ['eng-1'],
      supersedes: 'd0',
      updated_at: 40,
    },
    d2: {
      id: 'd2',
      architect_id: 'arch-1',
      title: 'Follow-up decision',
      status: 'revised',
      supersedes: 'd1',
      updated_at: 41,
    },
  };

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /role="button"/);
  assert.match(panel.innerHTML, /tabindex="0"/);
  assert.match(panel.innerHTML, /onkeydown="if\(event\.key===&#39;Enter&#39;\|\|event\.key===&#39; &#39;\)\{event\.preventDefault\(\);engineerToggleDecision\(&quot;d1&quot;\)\}"/);
  assert.match(panel.innerHTML, /onclick="engineerToggleDecision\(&quot;d1&quot;\)"/);
  assert.match(panel.innerHTML, /onclick="event\.stopPropagation\(\);engineerStartDecisionEdit\(&quot;d1&quot;\)"/);
  assert.doesNotMatch(panel.innerHTML, /onclick="engineerToggleDecision\("/);
  assert.doesNotMatch(panel.innerHTML, /engineerStartDecisionEdit\("/);

  context.engineerToggleDecision('d1');

  assert.match(panel.innerHTML, /Show the operator why this direction was chosen\./);
  assert.match(panel.innerHTML, /Status/);
  assert.match(panel.innerHTML, /accepted/);
  assert.match(panel.innerHTML, /Linked tasks/);
  assert.match(panel.innerHTML, /TORQUE:101/);
  assert.match(panel.innerHTML, /Linked engineers/);
  assert.match(panel.innerHTML, /eng-1/);
  assert.match(panel.innerHTML, /Supersedes/);
  assert.match(panel.innerHTML, /d0/);
  assert.match(panel.innerHTML, /Superseded by/);
  assert.match(panel.innerHTML, /d2/);
});

test('focused architect decision rows clamp titles and use compact secondary metadata/actions', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.decisions = {
    d1: {
      id: 'd1',
      architect_id: 'arch-1',
      title: 'A very long accepted decision title that should ellipsize before it can push buttons',
      status: 'accepted',
      updated_at: 40,
    },
    d2: {
      id: 'd2',
      architect_id: 'arch-1',
      title: 'Short accepted decision',
      status: 'accepted',
      updated_at: 41,
    },
  };

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /architect-decision-toggle/);
  assert.match(panel.innerHTML, /architect-decision-title/);
  assert.match(panel.innerHTML, /architect-decision-summary-row/);
  assert.match(panel.innerHTML, /detail-section-card-actions/);
  assert.match(panel.innerHTML, /architect-decision-action-btn/);
  assert.match(panel.innerHTML, /Edit/);
  assert.match(panel.innerHTML, /Archive/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(
    css,
    /\.architect-decision-toggle\s*\{[^}]*flex:\s*1 1 auto;/
  );
  assert.match(
    css,
    /\.architect-decision-title\s*\{[^}]*-webkit-line-clamp:\s*2;/
  );
  assert.match(
    css,
    /\.architect-decision-summary-row\s*\{[^}]*display:\s*flex;/
  );
  assert.match(
    css,
    /\.architect-decision-action-btn\s*\{[^}]*font-size:\s*8px;/
  );
});

test('focused architect decision edit sends the existing update command', () => {
  const { context, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.decisions = {
    d1: {
      id: 'd1',
      architect_id: 'arch-1',
      title: 'Initial title',
      rationale: 'Initial rationale',
      status: 'proposed',
      updated_at: 40,
    },
  };

  context.renderAgentPanel();
  context.engineerStartDecisionEdit('d1');
  context.engineerDecisionDraftInput('d1', 'title', 'Updated title');
  context.engineerDecisionDraftInput('d1', 'rationale', 'Updated rationale');
  context.engineerDecisionDraftInput('d1', 'status', 'accepted');
  context.engineerSaveDecisionEdit('arch-1', 'd1');

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    {
      cmd: 'architect_decision_update',
      architect_id: 'arch-1',
      id: 'd1',
      title: 'Updated title',
      rationale: 'Updated rationale',
      status: 'accepted',
    },
  ]);
});

test('focused engineer events tab starts the live countdown timer', () => {
  const { context, panel, intervalCalls } = createHarness();
  const countdownEl = { textContent: '' };
  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-events-countdown'
        && /agent-panel-events-countdown/.test(this.innerHTML || '')) {
      return countdownEl;
    }
    return null;
  };

  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  context.state.digest_buffer_stats['eng-1'] = {
    agent_id: 'eng-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 45,
    queued_events: [
      { id: 7, kind: 'task_completed', message: 'Queued for digest', timestamp: 30 },
    ],
    manual_flush_requested: false,
  };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Send queued now/);
  assert.match(countdownEl.textContent, /Next eligible send in/);
  assert.equal(intervalCalls.length, 1);
  assert.equal(intervalCalls[0].ms, 1000);
});

test('agent Events MCP subtab fetches calls, filters, and expands redacted details', () => {
  const { context, panel, sendCalls } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-mcp',
    name: 'Worker MCP',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.global_settings.mcp_call_log_args_capture = 'metadata';
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-mcp'] = 'mcp';
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.doesNotMatch(panel.innerHTML, /id="agent-panel-tab-mcp"/);
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);
  assert.doesNotMatch(panel.innerHTML, /agent-panel-mcp-banner/);
  assert.ok(sendCalls.some((call) => call.cmd === 'mcp_calls'
    && call.cell_id === 'worker-mcp'
    && call.limit === 50
    && call.hook_event_name === 'PostToolUse'));

  context.agentPanelReceiveMcpCalls({
    type: 'mcp_calls',
    cell_id: 'worker-mcp',
    calls: [
      {
        cursor: 10,
        cell_id: 'worker-mcp',
        tool_name: 'mcp__torque__torque_progress',
        hook_event_name: 'PostToolUse',
        appended_at: now,
        success: true,
        duration_ms: 12,
        args: { redacted: true, arg_keys: ['message'], byte_size: 42 },
        args_redacted: true,
        result: { redacted: true, byte_size: 2 },
        result_redacted: true,
      },
    ],
  });
  assert.match(panel.innerHTML, /mcp__torque__torque_progress/);
  assert.match(panel.innerHTML, /redacted keys: message/);

  context.agentPanelToggleMcpCall('worker-mcp', '10');
  assert.match(panel.innerHTML, /Args redacted at ingest\./);
  assert.match(panel.innerHTML, /Result redacted at ingest\./);

  const beforeFilterCalls = sendCalls.length;
  context.agentPanelMcpFilterChange('tool', 'progress');
  assert.ok(sendCalls.length > beforeFilterCalls);
  assert.equal(sendCalls[sendCalls.length - 1].tool_name_pattern, '*progress*');
});

test('agent Events MCP subtab hides settings banner in full capture mode and renders full args', () => {
  const { context, panel } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-full',
    name: 'Worker Full',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.global_settings.mcp_call_log_args_capture = 'full';
  context.state.mcp_calls['worker-full'] = [
    {
      cursor: 1,
      cell_id: 'worker-full',
      tool_name: 'mcp__torque__torque_done',
      hook_event_name: 'PostToolUse',
      appended_at: now,
      success: true,
      args: { message: 'shipped' },
      args_redacted: false,
      result: { ok: true },
      result_redacted: false,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-full'] = 'mcp';
  `, context);

  context.renderAgentPanel();
  context.agentPanelToggleMcpCall('worker-full', '1');

  assert.doesNotMatch(panel.innerHTML, /Args redacted by default/);
  assert.match(panel.innerHTML, /&quot;message&quot;: &quot;shipped&quot;/);
  assert.doesNotMatch(panel.innerHTML, /Args redacted at ingest\./);
});

test('agent Events MCP subtab live update prepends without losing expanded row across rerender', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();
  const now = Math.floor(Date.now() / 1000);
  setFocusedAgent(context, {
    id: 'worker-live',
    name: 'Worker Live',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.mcp_calls['worker-live'] = [
    {
      cursor: 1,
      cell_id: 'worker-live',
      tool_name: 'mcp__torque__old',
      hook_event_name: 'PostToolUse',
      appended_at: now - 10,
      success: true,
      args: { old: true },
      args_redacted: false,
      result: {},
      result_redacted: false,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-live'] = 'mcp';
  `, context);
  context.renderAgentPanel();
  context.agentPanelToggleMcpCall('worker-live', '1');
  const captureBefore = captureCalls.length;
  const restoreBefore = restoreCalls.length;

  context.agentPanelReceiveMcpCallAppend({
    cursor: 2,
    cell_id: 'worker-live',
    tool_name: 'mcp__torque__pre',
    hook_event_name: 'PreToolUse',
    appended_at: now,
    success: true,
    args: { task_id: 'TORQUE:1' },
    args_redacted: false,
    result: null,
    result_redacted: true,
  });

  assert.doesNotMatch(panel.innerHTML, /mcp__torque__pre/);
  assert.equal(context.state.mcp_calls['worker-live'].some((call) => call.tool_name === 'mcp__torque__pre'), false);

  context.agentPanelReceiveMcpCallAppend({
    cursor: 3,
    cell_id: 'worker-live',
    tool_name: 'mcp__torque__new',
    hook_event_name: 'PostToolUse',
    appended_at: now,
    success: false,
    error: 'boom',
    args: { redacted: true, arg_keys: ['task_id'] },
    args_redacted: true,
    result: null,
    result_redacted: true,
  });

  const html = panel.innerHTML;
  assert.ok(html.indexOf('mcp__torque__new') < html.indexOf('mcp__torque__old'));
  assert.match(html, /&quot;old&quot;: true/);
  assert.ok(captureCalls.length > captureBefore);
  assert.ok(restoreCalls.length > restoreBefore);
  const latestCapture = captureCalls[captureCalls.length - 1];
  const scrollSelectors = JSON.parse(JSON.stringify(latestCapture.opts.scrollSelectors));
  assert.ok(scrollSelectors.indexOf('.agent-panel-mcp-list') >= 0,
    'Events.MCP subtab must register its list for rerender scroll preservation');
});

test('agentPanelSelectTab mcp redirects to Events MCP subtab and subtab state is per-agent', () => {
  const { context, panel } = createHarness();
  const engA = {
    id: 'eng-a',
    name: 'Engineer A',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  };
  const engB = {
    id: 'eng-b',
    name: 'Engineer B',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  };
  context.state.agents = { 'eng-a': engA, 'eng-b': engB };

  context.focusedItemId = 'eng-a';
  context.renderAgentPanel();
  context.agentPanelSelectTab('mcp');
  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);

  context.focusedItemId = 'eng-b';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-inbox" class="agent-panel-events-subtab active"/);
  assert.doesNotMatch(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);

  context.agentPanelSelectEventsInnerTab('lifecycle');
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-lifecycle" class="agent-panel-events-subtab active"/);

  context.focusedItemId = 'eng-a';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-events-subtab-mcp" class="agent-panel-events-subtab active"/);
});

test('focused engineer pause toggle sends per-agent digest pause and resume commands', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'eng-1',
    name: 'Builder',
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['eng-1'] = { agent_id: 'eng-1', paused: false };
  vm.runInContext(`_agentPanelLastSelectedTabByKind.engineer = 'events';`, context);

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn"/);

  vm.runInContext(`agentPanelTogglePauseForAgent('eng-1')`, context);
  context.state.agent_digest_settings['eng-1'].paused = true;
  context.renderAgentPanel();
  vm.runInContext(`agentPanelTogglePauseForAgent('eng-1')`, context);

  const digestCalls = sendCalls.filter((call) => call.cmd && call.cmd.startsWith('digest_'));
  assert.deepEqual(JSON.parse(JSON.stringify(digestCalls)), [
    { cmd: 'digest_pause', agent_id: 'eng-1' },
    { cmd: 'digest_resume', agent_id: 'eng-1' },
  ]);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn paused"/);
});

test('focused architect Events tab renders digest data and pause control', () => {
  const { context, panel, sendCalls } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.agent_digest_settings['arch-1'] = {
    agent_id: 'arch-1',
    paused: false,
    architect_digest: true,
  };
  context.state.digest_buffer_stats['arch-1'] = {
    agent_id: 'arch-1',
    group: 'alpha',
    buffered_events: 1,
    next_push_at: Math.floor(Date.now() / 1000) + 300,
    queued_events: [
      { id: 11, kind: 'task_completed', message: 'Architect queued item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['arch-1'] = [
    { id: 10, kind: 'task_completed', message: 'Architect delivered item', timestamp: 35, delivered_at: 38 },
  ];
  vm.runInContext(`_agentPanelLastSelectedTabByKind.architect = 'events';`, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /id="agent-panel-tab-events" class="agent-panel-tab active"/);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn"/);
  assert.match(panel.innerHTML, /Architect queued item/);
  assert.match(panel.innerHTML, /Already digested to Planner/);
  assert.match(panel.innerHTML, /Architect delivered item/);

  vm.runInContext(`agentPanelTogglePauseForAgent('arch-1')`, context);
  context.state.agent_digest_settings['arch-1'].paused = true;
  context.renderAgentPanel();
  vm.runInContext(`agentPanelTogglePauseForAgent('arch-1')`, context);

  const digestCalls = sendCalls.filter((call) => call.cmd && call.cmd.startsWith('digest_'));
  assert.deepEqual(JSON.parse(JSON.stringify(digestCalls)), [
    { cmd: 'digest_pause', agent_id: 'arch-1' },
    { cmd: 'digest_resume', agent_id: 'arch-1' },
  ]);
  assert.match(panel.innerHTML, /id="agent-panel-pause-btn" class="agent-panel-pause-btn paused"/);
});

test('worker worklog updates after task changes and preserves the current anchor across rerenders', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.agentPanelSelectTab('worklog');

  const oldContent = {
    scrollTop: 100,
    getBoundingClientRect() {
      return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
    },
    querySelectorAll(selector) {
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-engineer-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:2' },
          getBoundingClientRect() {
            return { top: 20, bottom: 40, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:1' },
          getBoundingClientRect() {
            return { top: 60, bottom: 80, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:1' : '';
          },
        },
      ];
    },
  };
  const newContent = {
    scrollTop: 0,
    getBoundingClientRect() {
      return { top: 0, bottom: 120, left: 0, right: 200, width: 200, height: 120 };
    },
    querySelectorAll(selector) {
      if (selector !== '[data-agent-panel-anchor]' && selector !== '[data-engineer-anchor]') return [];
      return [
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:3' },
          getBoundingClientRect() {
            return { top: 10, bottom: 30, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:3' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:2' },
          getBoundingClientRect() {
            return { top: 40, bottom: 60, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:2' : '';
          },
        },
        {
          dataset: { agentPanelAnchor: 'worker-task-TORQUE:1' },
          getBoundingClientRect() {
            return { top: 80, bottom: 100, left: 0, right: 200, width: 200, height: 20 };
          },
          getAttribute(name) {
            return name === 'data-agent-panel-anchor' ? 'worker-task-TORQUE:1' : '';
          },
        },
      ];
    },
  };
  let currentContent = oldContent;
  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-content') return currentContent;
    return null;
  };
  Object.defineProperty(panel, 'innerHTML', {
    configurable: true,
    get() {
      return this._innerHTML || '';
    },
    set(value) {
      this._innerHTML = value;
      currentContent = newContent;
    },
  });

  context.state.board_tasks = {
    'TORQUE:2': { id: 'TORQUE:2', task: 'Second task', agent_id: 'worker-1', lane: 'Doing', status: 'running', created_at: 20 },
    'TORQUE:1': { id: 'TORQUE:1', task: 'First task', agent_id: 'worker-1', lane: 'Todo', status: 'queued', created_at: 10 },
  };

  context.renderAgentPanel();
  assert.equal(newContent.scrollTop, 20);
  assert.match(panel.innerHTML, /Second task/);

  context.state.board_tasks['TORQUE:3'] = {
    id: 'TORQUE:3',
    task: 'Newest task',
    agent_id: 'worker-1',
    lane: 'Review',
    status: 'waiting',
    created_at: 30,
  };
  currentContent = oldContent;
  oldContent.scrollTop = 100;
  newContent.scrollTop = 0;
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Newest task/);
  assert.equal(newContent.scrollTop, 20);
});

test('_engineerReceiveSessionMap keeps the focused-agent panel rendered when agent panel support is loaded', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);

  context._engineerReceiveSessionMap({
    group: 'alpha',
    session_map: { group: 'alpha', streams: { items: [] } },
  });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
});

function _eventsTabHarness(kind, agentId, agentName) {
  const harness = createHarness();
  setFocusedAgent(harness.context, {
    id: agentId,
    name: agentName,
    kind: kind,
    group: 'alpha',
    cell_type: 'agent',
  });
  harness.context.state.agent_digest_settings[agentId] = {
    agent_id: agentId,
    paused: false,
  };
  vm.runInContext(
    `_agentPanelLastSelectedTabByKind.${kind} = 'events';`,
    harness.context
  );
  vm.runInContext(`_agentPanelSectionPagers = {};`, harness.context);
  return harness;
}

test('engineer Events tab splits digest inbox from Cell events lifecycle', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-order', 'Builder');
  context.state.digest_buffer_stats['eng-order'] = {
    agent_id: 'eng-order',
    group: 'alpha',
    buffered_events: 1,
    queued_events: [
      { id: 1, kind: 'task_completed', message: 'Queued digest item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'eng-order',
    events: [
      { id: 100, cell_id: 'eng-order', kind: 'status_changed',
        message: 'Cell event row', timestamp: 20, source: 'panel_events' },
    ],
  });

  let html = panel.innerHTML;
  assert.match(html, /agent-panel-events-subtabs/);
  assert.match(html, /data-agent-panel-events-inner-tab="inbox"[^>]*aria-selected="true"/);
  assert.match(html, /Send queued now/);
  assert.match(html, /Queued for next digest/);
  assert.match(html, /Already digested to Builder/);
  assert.doesNotMatch(html, /Cell events/);

  vm.runInContext(`agentPanelSelectEventsInnerTab('lifecycle')`, context);
  html = panel.innerHTML;
  assert.match(html, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(html, /Cell events/);
  assert.match(html, /Cell event row/);
  assert.doesNotMatch(html, /Send queued now/);
  assert.doesNotMatch(html, /Queued for next digest/);
});

test('architect Events tab splits digest inbox from Cell events lifecycle', () => {
  const { context, panel } = _eventsTabHarness('architect', 'arch-order', 'Planner');
  context.state.digest_buffer_stats['arch-order'] = {
    agent_id: 'arch-order',
    group: 'alpha',
    buffered_events: 2,
    queued_events: [
      { id: 1, kind: 'task_completed', message: 'A queued item', timestamp: 40 },
    ],
    manual_flush_requested: false,
  };
  context.state.digest_sent_events['arch-order'] = [
    { id: 9, kind: 'task_completed', message: 'A sent item',
      timestamp: 30, delivered_at: 35 },
  ];
  context.agentPanelReceiveCellEvents({
    type: 'cell_events',
    cell_id: 'arch-order',
    events: [
      { id: 200, cell_id: 'arch-order', kind: 'status_changed',
        message: 'Arch cell event', timestamp: 25, source: 'panel_events' },
    ],
  });

  let html = panel.innerHTML;
  assert.match(html, /agent-panel-events-subtabs/);
  assert.match(html, /data-agent-panel-events-inner-tab="inbox"[^>]*aria-selected="true"/);
  assert.match(html, /Send queued now/);
  assert.match(html, /Queued for next digest/);
  assert.match(html, /Already digested to Planner/);
  assert.doesNotMatch(html, /Cell events/);

  vm.runInContext(`agentPanelSelectEventsInnerTab('lifecycle')`, context);
  html = panel.innerHTML;
  assert.match(html, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(html, /Cell events/);
  assert.match(html, /Arch cell event/);
  assert.doesNotMatch(html, /Send queued now/);
});

test('worker Events tab exposes inbox, lifecycle, and MCP subtabs with lifecycle event list', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-events',
    name: 'Worker Bee',
    kind: 'worker',
    group: 'alpha',
    cell_type: 'agent',
  });
  context.state.panel_events = [
    {
      id: 1,
      cell_id: 'worker-events',
      kind: 'agent_started',
      message: 'Worker booted',
      timestamp: 10,
    },
  ];
  vm.runInContext(`
    _agentPanelLastSelectedTabByKind.worker = 'events';
    _agentPanelEventsInnerTabByAgentId['worker-events'] = 'lifecycle';
  `, context);

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /agent-panel-events-subtabs/);
  assert.match(panel.innerHTML, /data-agent-panel-events-inner-tab="inbox"[\s\S]*data-agent-panel-events-inner-tab="lifecycle"[\s\S]*data-agent-panel-events-inner-tab="mcp"/);
  assert.match(panel.innerHTML, /data-agent-panel-events-inner-tab="lifecycle"[^>]*aria-selected="true"/);
  assert.match(panel.innerHTML, /Worker events/);
  assert.match(panel.innerHTML, /Worker booted/);
});

test('Already sent section caps at 20 events and exposes a Load more button', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-cap', 'Builder');
  const sent = [];
  for (let i = 0; i < 30; i++) {
    sent.push({
      id: 100 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-cap'] = sent;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/agent-panel-event-item-sent/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 sent items render initially');
  assert.match(html, /Already digested to Builder/);
  assert.match(html, /data-agent-panel-section="sent"/);
  assert.match(html, /Load 10 older events/);
  assert.match(html, /20 \/ 30/);
});

test('Queued section caps at 20 events and exposes a Load more button', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-qcap', 'Builder');
  const queued = [];
  for (let i = 0; i < 25; i++) {
    queued.push({
      id: 500 + i,
      kind: 'task_completed',
      message: 'Queued event ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.digest_buffer_stats['eng-qcap'] = {
    agent_id: 'eng-qcap',
    group: 'alpha',
    buffered_events: queued.length,
    queued_events: queued,
    manual_flush_requested: false,
  };

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/agent-panel-event-item-queued/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 queued items render initially');
  assert.match(html, /Queued for next digest/);
  assert.match(html, /data-agent-panel-section="queued"/);
  assert.match(html, /Load 5 older events/);
});

test('agentPanelLoadMoreSection expands the sent pager and preserves expansion across rerender', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-expand', 'Builder');
  const sent = [];
  for (let i = 0; i < 45; i++) {
    sent.push({
      id: 600 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-expand'] = sent;

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.match(html, /Load 20 older events/);
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    20
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'sent', 'eng-expand');`,
    context
  );

  html = panel.innerHTML;
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    40,
    'Load more extends the sent window to 40'
  );
  assert.match(html, /40 \/ 45/);

  // Simulate a WebSocket delta rerender — expansion must survive.
  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.equal(
    (html.match(/agent-panel-event-item-sent/g) || []).length,
    40,
    'Expansion is preserved across rerender'
  );
});

test('sent section pager grows to anchor around newly delivered events so prior rows remain rendered', () => {
  const { context, panel } = _eventsTabHarness('engineer', 'eng-anchor', 'Builder');
  const sent = [];
  for (let i = 0; i < 25; i++) {
    sent.push({
      id: 800 + i,
      kind: 'task_completed',
      message: 'Sent event ' + i,
      timestamp: 1000 + i,
      delivered_at: 2000 + i,
    });
  }
  context.state.digest_sent_events['eng-anchor'] = sent.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(html.indexOf('Sent event 5') >= 0,
    'Initial render shows mid-range event');

  // New deliveries land at the top (newest-first). The pager should grow so
  // the previously visible "Sent event 5" row stays in the DOM for the shared
  // anchor-restore helper to lock onto.
  const fresh = [
    { id: 900, kind: 'task_completed', message: 'Fresh delivery 1',
      timestamp: 9000, delivered_at: 9001 },
    { id: 901, kind: 'task_completed', message: 'Fresh delivery 2',
      timestamp: 9002, delivered_at: 9003 },
  ];
  context.state.digest_sent_events['eng-anchor'] = fresh.concat(sent);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(html.indexOf('Fresh delivery 1') >= 0,
    'New top rows render');
  assert.ok(html.indexOf('Sent event 5') >= 0,
    'Previously visible row stays rendered after top-insert');
});

function _engineerPanelHarness(tab, agentId, agentName) {
  const harness = createHarness();
  harness.context.state.group_settings = {
    alpha: { engineer_agent_id: 'engineer-1' },
  };
  harness.context.state.agents['engineer-1'] = {
    id: 'engineer-1',
    name: 'Engineer',
    group: 'alpha',
  };
  setFocusedAgent(harness.context, {
    id: agentId,
    name: agentName,
    kind: 'engineer',
    group: 'alpha',
    cell_type: 'agent',
  });
  vm.runInContext(
    `_agentPanelLastSelectedTabByKind.engineer = '${tab}';`,
    harness.context
  );
  vm.runInContext(`_agentPanelSectionPagers = {};`, harness.context);
  return harness;
}

test('engineer journal caps at 20 entries and exposes a Load more button', () => {
  const { context, panel } = _engineerPanelHarness('journal', 'eng-journal-cap', 'Builder');
  const entries = [];
  for (let i = 0; i < 25; i++) {
    entries.push({
      id: 1000 + i,
      type: 'observation',
      entry: 'Journal entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-cap'] = entries;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/data-engineer-anchor="journal-/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 journal entries render initially');
  assert.match(html, /Load 5 older entries/);
  assert.match(html, /data-agent-panel-section="journal"/);
  assert.match(html, /data-agent-panel-section-agent="eng-journal-cap"/);
});

test('engineer journal Load more appends 20 more entries without a re-fetch', () => {
  const { context, panel, sendCalls } = _engineerPanelHarness('journal', 'eng-journal-more', 'Builder');
  const entries = [];
  for (let i = 0; i < 45; i++) {
    entries.push({
      id: 1000 + i,
      type: 'observation',
      entry: 'Journal entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-more'] = entries;

  context.renderAgentPanel();
  const sendsBefore = sendCalls.length;
  assert.equal(
    (panel.innerHTML.match(/data-engineer-anchor="journal-/g) || []).length,
    20,
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'journal', 'eng-journal-more');`,
    context,
  );

  const html = panel.innerHTML;
  assert.equal(
    (html.match(/data-engineer-anchor="journal-/g) || []).length,
    40,
    'Load more extends the journal window to 40',
  );
  assert.match(html, /Load 5 older entries/);
  assert.equal(
    sendCalls.length,
    sendsBefore,
    'Load more must not trigger a server fetch — windowing is frontend-only',
  );
});

test('engineer journal pager grows to anchor around newly arriving entries', () => {
  const { context, panel } = _engineerPanelHarness('journal', 'eng-journal-anchor', 'Builder');
  const existing = [];
  for (let i = 0; i < 25; i++) {
    existing.push({
      id: 3000 + i,
      type: 'observation',
      entry: 'Existing entry ' + i,
      timestamp: 1000 + i,
    });
  }
  context.state.engineer_journal['eng-journal-anchor'] = existing.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(
    html.indexOf('Existing entry 5') >= 0,
    'Initial render shows mid-range anchor-candidate entry',
  );

  // WS delta: two fresh journal entries arrive while the user is scrolled
  // into older history. The pager should grow so the row the anchor helper
  // was about to lock onto stays in the DOM.
  const fresh = [
    { id: 3100, type: 'decision', entry: 'Fresh decision', timestamp: 9000 },
    { id: 3101, type: 'plan', entry: 'Fresh plan', timestamp: 9001 },
  ];
  context.state.engineer_journal['eng-journal-anchor'] = fresh.concat(existing);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(html.indexOf('Fresh decision') >= 0, 'Newest entries render at the top');
  assert.ok(
    html.indexOf('Existing entry 5') >= 0,
    'Prior anchor row stays rendered after WS delta so anchor-restore can lock onto it',
  );
});

test('engineer worklog caps at 20 dispatched tasks and exposes a Load more button', () => {
  const { context, panel } = _engineerPanelHarness('worklog', 'eng-wkl-cap', 'Builder');
  const entries = [];
  for (let i = 0; i < 25; i++) {
    entries.push({
      id: 2000 + i,
      task_id: 'TORQUE:' + (2000 + i),
      task_title: 'Dispatched task ' + i,
      agent_id: 'eng-wkl-cap',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = entries;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const itemCount = (html.match(/data-engineer-anchor="worklog-/g) || []).length;
  assert.equal(itemCount, 20, 'Only 20 worklog entries render initially');
  assert.match(html, /Load 5 older tasks/);
  assert.match(html, /data-agent-panel-section="worklog-all"/);
  assert.match(html, /data-agent-panel-section-agent="alpha"/);
});

test('engineer worklog Load more appends 20 more tasks without a re-fetch', () => {
  const { context, panel, sendCalls } = _engineerPanelHarness('worklog', 'eng-wkl-more', 'Builder');
  const entries = [];
  for (let i = 0; i < 45; i++) {
    entries.push({
      id: 2000 + i,
      task_id: 'TORQUE:' + (2000 + i),
      task_title: 'Dispatched task ' + i,
      agent_id: 'eng-wkl-more',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = entries;

  context.renderAgentPanel();
  const sendsBefore = sendCalls.length;
  assert.equal(
    (panel.innerHTML.match(/data-engineer-anchor="worklog-/g) || []).length,
    20,
  );

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'worklog-all', 'alpha');`,
    context,
  );

  const html = panel.innerHTML;
  assert.equal(
    (html.match(/data-engineer-anchor="worklog-/g) || []).length,
    40,
  );
  assert.match(html, /Load 5 older tasks/);
  assert.equal(
    sendCalls.length,
    sendsBefore,
    'Worklog pager must not issue a server fetch on Load more',
  );
});

test('engineer worklog pager grows to anchor around newly dispatched tasks', () => {
  const { context, panel } = _engineerPanelHarness('worklog', 'eng-wkl-anchor', 'Builder');
  const existing = [];
  for (let i = 0; i < 25; i++) {
    existing.push({
      id: 4000 + i,
      task_id: 'TORQUE:' + (4000 + i),
      task_title: 'Existing task ' + i,
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 1000 + i,
    });
  }
  context.state.engineer_worklog.alpha = existing.slice();

  context.renderAgentPanel();
  let html = panel.innerHTML;
  assert.ok(
    html.indexOf('Existing task 5') >= 0,
    'Initial render shows mid-range anchor-candidate task',
  );

  // WS delta: two fresh dispatched tasks land at the top while the user is
  // scrolled into older history. The pager should grow so the previously
  // visible row stays rendered for anchor-restore.
  const fresh = [
    {
      id: 4100,
      task_id: 'TORQUE:4100',
      task_title: 'Fresh dispatched task 1',
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 9000,
    },
    {
      id: 4101,
      task_id: 'TORQUE:4101',
      task_title: 'Fresh dispatched task 2',
      agent_id: 'eng-wkl-anchor',
      agent_name: 'Builder',
      started_at: 9001,
    },
  ];
  context.state.engineer_worklog.alpha = fresh.concat(existing);

  context.renderAgentPanel();
  html = panel.innerHTML;
  assert.ok(
    html.indexOf('Fresh dispatched task 1') >= 0,
    'Newest tasks render at the top',
  );
  assert.ok(
    html.indexOf('Existing task 5') >= 0,
    'Prior anchor row stays rendered after WS delta so anchor-restore can lock onto it',
  );
});

test('_engineerResetSessionMapMeta keeps the focused-agent panel rendered during reconnect resets', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  vm.runInContext(`
    _engineerSessionMapMetaByGroup.alpha = { loading: true, stale: false };
  `, context);

  context._engineerResetSessionMapMeta({ clearStale: false });

  assert.match(panel.innerHTML, /Worker: Worker Bee · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.equal(vm.runInContext(`_engineerSessionMapMetaByGroup.alpha.loading`, context), false);
});

test('agent panel renders Agent Profile badge with draft warnings and pending assignment', () => {
  const { context, classModalBody } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-1',
    name: 'PM Preview',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    agent_profile_id: 'full-architect',
    agent_profile_version: '1',
    effective_agent_profile_id: 'product-manager-draft',
    effective_agent_profile_version: '2',
    effective_agent_profile_snapshot: {
      id: 'product-manager-draft',
      version: '2',
      base_kind: 'architect',
      status: 'draft',
      warnings: [
        'product-manager-draft is scratch-only in Wave 4B',
        'Raw Architect tools are denied; use architect_product_* wrappers only',
      ],
      denied_high_risk_capabilities: ['agent.hire_engineer', 'task.dispatch'],
    },
  });

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-1');

  assert.match(classModalBody.innerHTML, /agent-profile-badge/);
  assert.match(classModalBody.innerHTML, /product-manager-draft@2 \(pending next launch\)/);
  assert.match(classModalBody.innerHTML, /Raw Architect tools are denied/);
});

test('agent panel marks cleared assignment pending default full profile next launch', () => {
  const { context, classModalBody } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-clear',
    name: 'PM Preview Clearing',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    agent_profile_id: '',
    agent_profile_version: '',
    effective_agent_profile_id: 'product-manager-draft',
    effective_agent_profile_version: '2',
    effective_agent_profile_snapshot: {
      id: 'product-manager-draft',
      version: '2',
      base_kind: 'architect',
      status: 'draft',
      warnings: ['cleared assignment should relaunch as full architect'],
      denied_high_risk_capabilities: ['agent.hire_engineer'],
    },
  });

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-clear');

  assert.match(classModalBody.innerHTML, /product-manager-draft@2 \(pending next launch\)/);
  assert.match(classModalBody.innerHTML, /desired assignment: default full-architect/);
});

test('agent class manager assigns Product Manager as desired and renders effective class identity after relaunch', () => {
  const { context, panel, classModal, classModalBody, sendCalls } = createHarness();
  const defaultArchitectClass = {
    id: 'default-architect',
    version: '1',
    base_kind: 'architect',
    display_name: 'Default Architect',
    primary_identity_label: 'Default Architect',
    secondary_base_kind_label: 'Architect',
    lifecycle: 'stable',
    status: 'full',
    launchable: true,
    builtin: true,
    agent_profile_ref: { id: 'full-architect', version: '1' },
    agent_profile: { id: 'full-architect', version: '1', status: 'full', capability_count: 12 },
    runtime_enforcement: 'launch_frozen_agent_class_profile_pairing',
  };
  const productManagerClass = {
    id: 'product-manager',
    version: '2',
    base_kind: 'architect',
    display_name: 'Product Manager',
    primary_identity_label: 'Product Manager',
    secondary_base_kind_label: 'Architect-derived',
    lifecycle: 'draft',
    status: 'draft',
    launchable: true,
    builtin: true,
    draft: { scratch_only: true },
    agent_profile_ref: { id: 'class-policy-product-manager', version: '2' },
    agent_profile: { id: 'class-policy-product-manager', version: '2', status: 'draft', capability_count: 7 },
    internal_policy: {
      mode: 'compile',
      profile_source: 'compiled_from_agent_class',
      generated_profile_written_to_project_yaml: false,
    },
    acl: {
      mode: 'allow',
      allowed_families: ['architect_product_*'],
      denied_families: ['architect_task_*'],
    },
    operator_access_summary: {
      allowed_summary: 'ACL allow-list: product wrappers, user and peer Architect communication, private journal',
      denied_summary: 'ACL denies: execution, admin, raw tool picker, direct Engineer/Worker messaging',
    },
    warnings: ['Product Manager is draft/restricted; use architect_product_* wrappers only.'],
    external_connector_caveat: 'External connector exposure is not governed by Agent Classes.',
    runtime_enforcement: 'launch_frozen_agent_class_profile_pairing',
  };
  const planningClass = {
    id: 'planning-architect',
    version: '1',
    base_kind: 'architect',
    display_name: 'Planning Architect',
    description: 'Plans and reviews task intake.',
    lifecycle: 'stable',
    status: 'restricted',
    launchable: true,
    acl: { mode: 'allow', allow: [{ capability: 'planning_reads' }, { capability: 'board_task_reads' }], deny: [{ capability: 'deny_raw_tool_picker' }, { capability: 'deny_high_risk_operations' }] },
    capability_bucket_selection: ['planning_reads', 'board_task_reads'],
    restriction_bucket_selection: ['deny_raw_tool_picker', 'deny_high_risk_operations'],
    capability_buckets: [
      { id: 'planning_reads', label: 'Planning reads', summary: 'Read planning docs and proposed decisions.' },
      { id: 'board_task_reads', label: 'Board/task reads', summary: 'Read Board tasks and MCP call telemetry.' },
    ],
    restriction_buckets: [
      { id: 'deny_raw_tool_picker', label: 'Deny raw tool picker', summary: 'Records that arbitrary raw tool picker authority is outside the class contract.' },
      { id: 'deny_high_risk_operations', label: 'Deny remaining high-risk operations', summary: 'Explicitly deny high-risk/critical operations not selected by capability buckets.' },
    ],
    operator_access_summary: {
      allowed_summary: 'Planning reads; Board/task reads; MCP call telemetry',
      denied_summary: 'Deny raw tool picker; Deny remaining high-risk operations',
    },
    apply_state: { applies_at: 'next_launch_or_relaunch', relaunch_required_after_assignment: true },
  };

  setFocusedAgent(context, {
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
    directory: '/repo',
    agent_class_id: '',
    agent_class_version: '',
    effective_agent_class_id: 'default-architect',
    effective_agent_class_version: '1',
    effective_agent_class_snapshot: defaultArchitectClass,
    effective_agent_profile_id: 'full-architect',
    effective_agent_profile_version: '1',
    effective_agent_profile_snapshot: {
      id: 'full-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Full Architect',
      lifecycle: 'stable',
      status: 'full',
    },
  });

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Agent Class/);
  assert.match(panel.innerHTML, /Change Class/);
  assert.equal(sendCalls.some((call) => /^agent_class_/.test(call.cmd || '') || /^agent_profile_/.test(call.cmd || '')), false, 'compact Behavior summary does not fetch class/profile data until Change Class opens the modal');
  context.agentPanelToggleClassAssignment(null, 'blueprint');
  assert.equal(classModal.classList.contains('visible'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.slice(-2))), [
    { cmd: 'agent_class_list', base_dir: '/repo' },
    { cmd: 'agent_class_status', agent_id: 'blueprint', base_dir: '/repo' },
  ]);

  context.agentPanelReceiveAgentClasses({
    type: 'agent_classes',
    classes: [defaultArchitectClass, planningClass, productManagerClass],
    issues: [],
  });
  assert.match(classModalBody.innerHTML, /Product Manager@2 · Architect-derived · draft/);

  context.agentPanelSelectClass('blueprint', 'planning-architect');
  assert.match(classModalBody.innerHTML, /What this class can do/);
  assert.match(classModalBody.innerHTML, /Allowed actions[\s\S]*Planning reads; Board\/task reads; tool activity history/);
  assert.match(classModalBody.innerHTML, /Not allowed[\s\S]*No arbitrary tool selection; No powerful actions beyond this class/);
  assert.match(classModalBody.innerHTML, /No arbitrary tool selection[\s\S]*No powerful actions beyond this class/);
  assert.doesNotMatch(classModalBody.innerHTML, /Operator access preview|Capability access|Allowed buckets|Restriction buckets|capability buckets|raw tool picker|high-risk/i);

  context.agentPanelSelectClass('blueprint', 'product-manager');
  assert.match(classModalBody.innerHTML, /Next relaunch freezes Product Manager@2 as the primary identity/);
  assert.doesNotMatch(classModalBody.innerHTML, /External connectors/);
  assert.match(classModalBody.innerHTML, /ACL allow-list: product wrappers/);
  assert.match(classModalBody.innerHTML, /Allowed actions[\s\S]*ACL allow-list: product wrappers/);
  assert.match(classModalBody.innerHTML, /Not allowed[\s\S]*ACL denies: execution/);
  context.renderAgentPanel();
  assert.equal(classModal.classList.contains('visible'), true, 'routine rerender keeps Change Class modal open');
  assert.match(classModalBody.innerHTML, /<option value="product-manager" selected>/);
  context.agentPanelAssignSelectedClass(null, 'blueprint');
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'agent_class_assign',
    agent_id: 'blueprint',
    actor_label: 'trusted-user-ui',
    base_dir: '/repo',
    class_id: 'product-manager',
  });

  context.agentPanelReceiveAgentClassAssignment({
    type: 'agent_class_assignment',
    status: {
      agent_id: 'blueprint',
      assigned_class_id: 'product-manager',
      assigned_class_version: '2',
      assigned_at: 100,
      assigned_by: 'trusted-user-ui',
      effective_class_id: 'default-architect',
      effective_class_version: '1',
      effective_class: defaultArchitectClass,
      assigned_class: productManagerClass,
      next_launch_class_id: 'product-manager',
      next_launch_class_version: '2',
      next_launch_primary_identity_label: 'Product Manager',
      pending_next_launch: true,
      warnings: ['External connector exposure is not governed by Agent Classes.'],
      external_connector_caveat: 'External connector exposure is not governed by Agent Classes.',
    },
  });
  assert.match(panel.innerHTML, /Desired Agent Class updated\. It will freeze on the next launch or relaunch\./);
  assert.match(panel.innerHTML, /Desired Agent Class next launch[\s\S]*Product Manager@2/);
  assert.match(panel.innerHTML, /Pending relaunch[\s\S]*next relaunch freezes Product Manager@2/);
  assert.match(classModalBody.innerHTML, /Advanced\/Internal Agent Profile policy/);

  Object.assign(context.state.agents.blueprint, {
    status: 'running',
    agent_class_id: 'product-manager',
    agent_class_version: '2',
    effective_agent_class_id: 'product-manager',
    effective_agent_class_version: '2',
    effective_agent_class_applied_at: 200,
    effective_agent_class_snapshot: productManagerClass,
  });
  assert.equal(
    context.state.agents.blueprint.agent_class_status.effective_class_id,
    'default-architect',
    'real relaunch upsert leaves the prior client-only status stale when it omits agent_class_status'
  );
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Product Manager/);
  assert.match(panel.innerHTML, /Primary identity now[\s\S]*Product Manager@2/);
  assert.match(panel.innerHTML, /Base kind metadata[\s\S]*Architect-derived/);
  assert.match(panel.innerHTML, /Pending relaunch[\s\S]*No — running session already matches desired Agent Class/);
  assert.doesNotMatch(panel.innerHTML, /Desired Agent Class updated\. It will freeze/);
  context.agentPanelCloseClassAssignmentModal(null);
  assert.equal(classModal.classList.contains('visible'), false);
});

test('agent panel renders Product Manager dogfood state as compact operator access policy UI', () => {
  const { context, panel, classModalBody } = createHarness();
  const productManagerClass = {
    id: 'product-manager',
    version: '2',
    base_kind: 'architect',
    display_name: 'Product Manager',
    primary_identity_label: 'Product Manager',
    secondary_base_kind_label: 'Architect-derived',
    lifecycle: 'stable',
    status: 'restricted',
    launchable: true,
    builtin: true,
    draft: { scratch_only: false, approved_for_live_dogfood: true },
    agent_profile_ref: { id: 'class-policy-product-manager', version: '2' },
    agent_profile: { id: 'class-policy-product-manager', version: '2', status: 'restricted', capability_count: 7 },
    internal_policy: {
      mode: 'compile',
      profile_source: 'compiled_from_agent_class',
      generated_profile_written_to_project_yaml: false,
    },
    operator_access_summary: {
      allowed_summary: 'ACL allow-list: product wrappers, user and peer Architect communication, private journal',
      denied_summary: 'ACL denies: execution, admin, raw tool picker, direct Engineer/Worker messaging',
    },
    warnings: [
      'External connectors are not governed by Agent Classes/Profile policy in Wave 7; manage connector access separately.',
      'External connector exposure is not governed or enforced by Agent Classes or Agent Profiles in Wave 7; manage connector access separately.',
      'Raw Architect tools are denied; use architect_product_* wrappers only.',
      'Product Manager cannot dispatch, merge, deploy, administer, use raw tool picker authority, or message engineers/workers directly.',
    ],
    external_connector_caveat: 'External connector exposure is not governed or enforced by Agent Classes or Agent Profiles in Wave 7; manage connector access separately.',
    runtime_enforcement: 'launch_frozen_agent_class_profile_pairing',
  };

  setFocusedAgent(context, {
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    directory: '/repo',
    agent_class_id: 'product-manager',
    agent_class_version: '2',
    effective_agent_class_id: 'product-manager',
    effective_agent_class_version: '2',
    effective_agent_class_snapshot: productManagerClass,
    effective_agent_profile_id: 'class-policy-product-manager',
    effective_agent_profile_version: '2',
    effective_agent_profile_snapshot: {
      id: 'class-policy-product-manager',
      version: '2',
      base_kind: 'architect',
      display_name: 'Product Manager internal policy',
      lifecycle: 'restricted',
      status: 'restricted',
      warnings: ['Raw Architect tools are denied; use architect_product_* wrappers only.'],
      denied_high_risk_capabilities: ['agent.hire_engineer', 'task.dispatch', 'worktree.merge', 'deploy.apply'],
    },
  });

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Product Manager · Group: alpha/);
  assert.doesNotMatch(panel.innerHTML, /Architect: Product Manager/);
  assert.match(panel.innerHTML, /Agent Class/);
  assert.match(panel.innerHTML, /Product Manager/);
  assert.doesNotMatch(panel.innerHTML, /External connectors/);
  assert.doesNotMatch(panel.innerHTML, /agent-profile-scratch-warning[\s\S]*External connector/);
  assert.match(panel.innerHTML, /Primary identity now[\s\S]*Product Manager@2/);
  assert.match(panel.innerHTML, /Base kind metadata[\s\S]*Architect-derived/);
  assert.doesNotMatch(panel.innerHTML, /Advanced\/Internal Agent Profile policy[\s\S]*class-policy-product-manager@2/);
  assert.doesNotMatch(panel.innerHTML, /class-policy-product-manager|Product Manager internal policy|default full-architect|differs from desired/);

  context.agentPanelToggleClassAssignment(null, 'blueprint');
  context.agentPanelReceiveAgentClasses({
    type: 'agent_classes',
    classes: [productManagerClass],
    issues: [],
  });
  assert.match(classModalBody.innerHTML, /<option value="product-manager" selected>/);
  assert.match(classModalBody.innerHTML, /Access source[\s\S]*Managed by Agent Class: Product Manager/);
  assert.doesNotMatch(classModalBody.innerHTML, /class-policy-product-manager|Product Manager internal policy|differs from desired default/);
  assert.doesNotMatch(panel.innerHTML, /External connectors/);
  assert.doesNotMatch(panel.innerHTML, /Raw Architect tools are denied; use architect_product_\* wrappers only\.[\s\S]*Raw Architect tools are denied/);
});

test('agent panel assigns and renders Creative as proposal-only Thinking class', () => {
  const { context, panel, classModal, classModalBody, sendCalls } = createHarness();
  const defaultArchitectClass = {
    id: 'default-architect',
    version: '1',
    base_kind: 'architect',
    display_name: 'Default Architect',
    primary_identity_label: 'Default Architect',
    secondary_base_kind_label: 'Architect',
    lifecycle: 'stable',
    status: 'full',
    launchable: true,
    builtin: true,
  };
  const creativeArchitectClass = {
    id: 'creative-architect',
    version: '1',
    base_kind: 'architect',
    display_name: 'Creative',
    primary_identity_label: 'Creative',
    secondary_base_kind_label: 'Architect-derived',
    description: 'Proposal-only ideation partner for Torque; explores possibilities with Thinking artifacts and suggests small shippable proposals.',
    purpose: 'Proposal-only ideation partner for Torque; explores possibilities with Thinking artifacts and suggests small shippable proposals.',
    lifecycle: 'stable',
    status: 'restricted',
    launchable: true,
    builtin: true,
    metadata: { proposal_only: true },
    agent_profile_ref: { id: 'class-policy-creative-architect', version: '1' },
    agent_profile: { id: 'class-policy-creative-architect', version: '1', status: 'restricted', capability_count: 9 },
    internal_policy: { mode: 'compile', profile_source: 'compiled_from_agent_class' },
    warnings: [
      'Creative Architect is proposal-only: ideas remain non-binding until accepted through normal Torque authority.',
      'Use architect_thinking_* wrappers for Scratchpad/Mind Map work and architect_product_* wrappers for product proposals.',
    ],
    external_connector_caveat: 'External connector exposure is not governed by Agent Classes.',
    apply_state: { applies_at: 'next_launch_or_relaunch', relaunch_required_after_assignment: true },
  };

  setFocusedAgent(context, {
    id: 'spark',
    name: 'Spark',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
    directory: '/repo',
    agent_class_id: '',
    agent_class_version: '',
    effective_agent_class_id: 'default-architect',
    effective_agent_class_version: '1',
    effective_agent_class_snapshot: defaultArchitectClass,
  });

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();
  context.agentPanelToggleClassAssignment(null, 'spark');
  assert.equal(classModal.classList.contains('visible'), true);
  context.agentPanelReceiveAgentClasses({
    type: 'agent_classes',
    classes: [defaultArchitectClass, creativeArchitectClass],
    issues: [],
  });
  assert.match(classModalBody.innerHTML, /Creative@1 · Architect-derived · restricted/);
  assert.doesNotMatch(classModalBody.innerHTML, /Creative Architect@1/);
  context.agentPanelSelectClass('spark', 'creative-architect');
  assert.match(classModalBody.innerHTML, /Next relaunch freezes Creative@1 as the primary identity/);
  assert.match(classModalBody.innerHTML, /proposal-only/);
  assert.match(classModalBody.innerHTML, /proposal-only/);
  assert.doesNotMatch(classModalBody.innerHTML, /class-policy-creative-architect|generated profile|compiler|raw atom/i);

  context.agentPanelAssignSelectedClass(null, 'spark');
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'agent_class_assign',
    agent_id: 'spark',
    actor_label: 'trusted-user-ui',
    base_dir: '/repo',
    class_id: 'creative-architect',
  });
  context.agentPanelReceiveAgentClassAssignment({
    type: 'agent_class_assignment',
    status: {
      agent_id: 'spark',
      assigned_class_id: 'creative-architect',
      assigned_class_version: '1',
      assigned_at: 100,
      assigned_by: 'trusted-user-ui',
      effective_class_id: 'default-architect',
      effective_class_version: '1',
      effective_class: defaultArchitectClass,
      assigned_class: creativeArchitectClass,
      next_launch_class_id: 'creative-architect',
      next_launch_class_version: '1',
      next_launch_primary_identity_label: 'Creative Architect',
      pending_next_launch: true,
    },
  });
  assert.match(panel.innerHTML, /Desired Agent Class next launch[\s\S]*Creative@1/);
  assert.match(panel.innerHTML, /Pending relaunch[\s\S]*next relaunch freezes Creative@1/);

  Object.assign(context.state.agents.spark, {
    status: 'running',
    agent_class_id: 'creative-architect',
    agent_class_version: '1',
    effective_agent_class_id: 'creative-architect',
    effective_agent_class_version: '1',
    effective_agent_class_applied_at: 200,
    effective_agent_class_snapshot: creativeArchitectClass,
  });
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Creative · Group: alpha/);
  assert.match(panel.innerHTML, /Primary identity now[\s\S]*Creative@1/);
  assert.doesNotMatch(panel.innerHTML, /Creative Architect@1|Creative Architect · Group: alpha/);
  assert.match(panel.innerHTML, /Base kind metadata[\s\S]*Architect-derived/);
  assert.match(panel.innerHTML, /Agent Class/);
  assert.match(panel.innerHTML, /Agent Class/);
  assert.doesNotMatch(panel.innerHTML, /class-policy-creative-architect|External connectors|raw atom|compiler/i);
});

test('agent panel shows Agent Class summary only on Behavior tab and opens modal from Change Class', () => {
  const { context, panel, classModal } = createHarness();
  const productManagerClass = {
    id: 'product-manager',
    version: '2',
    base_kind: 'architect',
    display_name: 'Product Manager',
    primary_identity_label: 'Product Manager',
    secondary_base_kind_label: 'Architect-derived',
    lifecycle: 'stable',
    status: 'restricted',
    launchable: true,
    builtin: true,
  };
  setFocusedAgent(context, {
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    directory: '/repo',
    effective_agent_class_id: 'product-manager',
    effective_agent_class_version: '2',
    effective_agent_class_snapshot: productManagerClass,
  });

  context.renderAgentPanel();
  assert.doesNotMatch(panel.innerHTML, /data-agent-class-manager=/);
  assert.doesNotMatch(panel.innerHTML, /Change Class/);

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /data-agent-class-manager=/);
  assert.match(panel.innerHTML, /Change Class/);
  assert.match(panel.innerHTML, /Primary identity now[\s\S]*Product Manager@2/);

  context.agentPanelOpenClassAssignment(null, 'blueprint');
  assert.equal(classModal.classList.contains('visible'), true);
  context.agentPanelCloseClassAssignmentModal(null);
  assert.equal(classModal.classList.contains('visible'), false);

  context._agentPanelLastSelectedTabByKind.architect = 'messages';
  context.renderAgentPanel();
  assert.doesNotMatch(panel.innerHTML, /data-agent-class-manager=/);
  assert.doesNotMatch(panel.innerHTML, /Change Class/);
});

test('agent panel in-place Behavior tab render includes Agent Class summary only on Behavior', () => {
  const { context, panel } = createHarness();
  const content = {
    innerHTML: '',
    _torqueLastHtml: '',
    scrollTop: 0,
    clientHeight: 400,
    scrollHeight: 800,
    querySelectorAll() { return []; },
    querySelector() { return null; },
    getBoundingClientRect() { return { top: 0, bottom: 400 }; },
    addEventListener() {},
    removeEventListener() {},
  };
  const headerRight = {
    innerHTML: '',
    _torqueLastHtml: '',
  };
  const shell = {
    dataset: {
      agentPanelAgentId: 'blueprint',
      agentPanelKind: 'architect',
      agentPanelTab: 'decisions',
    },
    setAttribute(name, value) {
      if (name === 'data-agent-panel-tab') this.dataset.agentPanelTab = String(value || '');
    },
  };
  const tabButtons = ['decisions', 'behavior', 'messages'].map((tab) => ({
    dataset: { agentPanelTabKey: tab },
    classList: { add() {}, remove() {} },
  }));
  panel.querySelector = function(selector) {
    if (selector === '.agent-panel-panel') return shell;
    if (selector === '.agent-panel-content') return content;
    if (selector === '[data-agent-panel-header-right]' || selector === '.agent-panel-header-right') return headerRight;
    return null;
  };
  panel.querySelectorAll = function(selector) {
    if (selector === '.agent-panel-tab') return tabButtons;
    return [];
  };
  const productManagerClass = {
    id: 'product-manager',
    version: '2',
    base_kind: 'architect',
    display_name: 'Product Manager',
    primary_identity_label: 'Product Manager',
    secondary_base_kind_label: 'Architect-derived',
    lifecycle: 'stable',
    status: 'restricted',
    launchable: true,
    builtin: true,
  };
  setFocusedAgent(context, {
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    directory: '/repo',
    effective_agent_class_id: 'product-manager',
    effective_agent_class_version: '2',
    effective_agent_class_snapshot: productManagerClass,
    mcp_messages: [],
  });

  context._agentPanelLastSelectedTabByKind.architect = 'decisions';
  context.agentPanelSelectTab('behavior');
  assert.equal(shell.dataset.agentPanelTab, 'behavior');
  assert.match(content.innerHTML, /data-agent-class-manager=/);
  assert.match(content.innerHTML, /Change Class/);
  assert.match(content.innerHTML, /Primary identity now[\s\S]*Product Manager@2/);

  context.agentPanelSelectTab('messages');
  assert.equal(shell.dataset.agentPanelTab, 'messages');
  assert.doesNotMatch(content.innerHTML, /data-agent-class-manager=/);
  assert.doesNotMatch(content.innerHTML, /Change Class/);
});

test('agent class manager assignment errors remain routed to active panel operation', () => {
  const { context, panel, classModal, classModalBody, sendCalls } = createHarness();
  const defaultArchitectClass = {
    id: 'default-architect',
    version: '1',
    base_kind: 'architect',
    display_name: 'Default Architect',
    primary_identity_label: 'Default Architect',
    secondary_base_kind_label: 'Architect',
    lifecycle: 'stable',
    status: 'full',
    launchable: true,
    builtin: true,
  };
  const productManagerClass = {
    id: 'product-manager',
    version: '2',
    base_kind: 'architect',
    display_name: 'Product Manager',
    primary_identity_label: 'Product Manager',
    secondary_base_kind_label: 'Architect-derived',
    lifecycle: 'draft',
    status: 'draft',
    launchable: true,
    builtin: true,
  };

  setFocusedAgent(context, {
    id: 'blueprint',
    name: 'Blueprint',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
    directory: '/repo',
    effective_agent_class_id: 'default-architect',
    effective_agent_class_version: '1',
    effective_agent_class_snapshot: defaultArchitectClass,
  });

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Agent Class/);
  assert.match(panel.innerHTML, /Change Class/);
  assert.equal(sendCalls.some((call) => /^agent_class_/.test(call.cmd || '') || /^agent_profile_/.test(call.cmd || '')), false, 'compact Behavior summary does not fetch class/profile data until Change Class opens the modal');
  context.agentPanelToggleClassAssignment(null, 'blueprint');
  assert.equal(classModal.classList.contains('visible'), true);
  context.agentPanelReceiveAgentClasses({
    type: 'agent_classes',
    classes: [defaultArchitectClass, productManagerClass],
    issues: [],
  });
  context.agentPanelSelectClass('blueprint', 'product-manager');
  context.agentPanelAssignSelectedClass(null, 'blueprint');
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_assign');

  const handled = context.agentPanelHandleAgentClassError({
    message: 'Unknown Agent Class: product-manager',
    code: 'agent_class_assign_failed',
  });
  assert.equal(handled, true);
  assert.match(classModalBody.innerHTML, /Unknown Agent Class: product-manager/);
  assert.match(panel.innerHTML, /Unknown Agent Class: product-manager/);
  assert.doesNotMatch(classModalBody.innerHTML, /Saving…/);
});

test('agent profile manager lists compatible profiles, previews PM draft, assigns, and clears', () => {
  const { context, panel, classModalBody, sendCalls } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-ui',
    name: 'PM UI',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
    directory: '/repo',
    agent_profile_id: '',
    agent_profile_version: '',
    effective_agent_profile_id: 'full-architect',
    effective_agent_profile_version: '1',
    effective_agent_profile_snapshot: {
      id: 'full-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Full Architect',
      lifecycle: 'stable',
      status: 'full',
      warnings: [],
      denied_high_risk_capabilities: [],
      communication_policy: { summary: 'full communication' },
      spawn_policy: { summary: 'full spawn' },
      scope_policy: { summary: 'full scope' },
      audit_policy: { summary: 'full audit' },
    },
  });

  context._agentPanelLastSelectedTabByKind.architect = 'behavior';
  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Agent Class/);
  assert.match(panel.innerHTML, /Primary identity now[\s\S]*Default Architect/);
  assert.match(panel.innerHTML, /Desired Agent Class next launch[\s\S]*Default Architect/);
  assert.doesNotMatch(panel.innerHTML, /Advanced\/Internal Agent Profile assignment/);
  assert.match(panel.innerHTML, /Change Class/);
  assert.equal(sendCalls.some((call) => /^agent_class_/.test(call.cmd || '') || /^agent_profile_/.test(call.cmd || '')), false, 'collapsed class/profile summaries do not fetch until the operator opens them');

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-ui');
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.slice(-2))), [
    { cmd: 'agent_profile_list', base_dir: '/repo' },
    { cmd: 'agent_profile_preview', profile_id: 'full-architect', base_dir: '/repo' },
  ]);

  context.agentPanelReceiveAgentProfiles({
    type: 'agent_profiles',
    profiles: [
      {
        id: 'full-architect',
        version: '1',
        base_kind: 'architect',
        display_name: 'Full Architect',
        lifecycle: 'stable',
        status: 'full',
        warnings: [],
        denied_high_risk_capabilities: [],
      },
      {
        id: 'product-manager-draft',
        version: '2',
        base_kind: 'architect',
        display_name: 'Product Manager (draft)',
        lifecycle: 'draft',
        status: 'draft',
        warnings: ['scratch-only warning from list'],
        denied_high_risk_capabilities: ['agent.hire_engineer'],
      },
      {
        id: 'full-worker',
        version: '1',
        base_kind: 'worker',
        display_name: 'Full Worker',
        lifecycle: 'stable',
        status: 'full',
      },
    ],
    issues: [],
  });
  assert.match(classModalBody.innerHTML, /Product Manager \(draft\)@2 · draft/);
  assert.doesNotMatch(classModalBody.innerHTML, /Full Worker/);
  assert.match(classModalBody.innerHTML, /Relaunch to apply/);

  context.agentPanelSelectProfile('arch-profile-ui', 'product-manager-draft');
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'agent_profile_preview',
    profile_id: 'product-manager-draft',
    base_dir: '/repo',
  });
  assert.match(classModalBody.innerHTML, /Wait for preview before assigning\./);

  context.agentPanelReceiveAgentProfilePreview({
    type: 'agent_profile_preview',
    profile: {
      id: 'product-manager-draft',
      version: '2',
      base_kind: 'architect',
      display_name: 'Product Manager (draft)',
      description: 'Draft product manager profile',
      lifecycle: 'draft',
      status: 'draft',
      warnings: [
        'product-manager-draft is scratch-only in Wave 4B',
        'Raw Architect tools are denied',
      ],
      denied_high_risk_capabilities: ['agent.hire_engineer', 'task.dispatch'],
      communication_policy: { summary: 'coordinate through product wrappers' },
      spawn_policy: { summary: 'dispatch denied' },
      scope_policy: { summary: 'planning-safe reads/writes only' },
      audit_policy: { summary: 'profile assignment audit rows' },
    },
  });
  assert.match(classModalBody.innerHTML, /product-manager-draft is scratch-only in Wave 4B/);
  assert.match(classModalBody.innerHTML, /Raw Architect tools are denied/);
  assert.match(classModalBody.innerHTML, /agent\.hire_engineer/);
  assert.match(classModalBody.innerHTML, /task\.dispatch/);
  assert.match(classModalBody.innerHTML, /coordinate through product wrappers/);
  assert.doesNotMatch(classModalBody.innerHTML, /Wait for preview before assigning\./);

  context.agentPanelAssignSelectedProfile(null, 'arch-profile-ui');
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[sendCalls.length - 1])), {
    cmd: 'agent_profile_assign',
    agent_id: 'arch-profile-ui',
    profile_id: 'product-manager-draft',
    actor_label: 'trusted-user-ui',
  });

  context.agentPanelReceiveAgentProfileAssignment({
    type: 'agent_profile_assignment',
    status: {
      agent_id: 'arch-profile-ui',
      assigned_profile_id: 'product-manager-draft',
      assigned_profile_version: '2',
      assigned_at: 1234,
      assigned_by: 'trusted-user-ui',
      pending_next_launch: true,
    },
  });
  assert.equal(context.state.agents['arch-profile-ui'].agent_profile_id, 'product-manager-draft');
  assert.match(classModalBody.innerHTML, /Desired profile updated\. It will apply on the next launch or relaunch\./);
  assert.match(classModalBody.innerHTML, /Desired next launch[\s\S]*product-manager-draft@2/);
  assert.match(classModalBody.innerHTML, /Pending[\s\S]*Yes — effective Full Architect@1 differs from desired product-manager-draft@2/);

  context.agentPanelClearProfileAssignment(null, 'arch-profile-ui');
  const clearCall = sendCalls.slice().reverse()
    .find((call) => call.cmd === 'agent_profile_assign' && call.profile_id === '');
  assert.deepEqual(JSON.parse(JSON.stringify(clearCall)), {
    cmd: 'agent_profile_assign',
    agent_id: 'arch-profile-ui',
    profile_id: '',
    actor_label: 'trusted-user-ui',
  });
});

test('agent profile manager clears stale pending message after relaunch applies desired profile', () => {
  const { context, classModalBody } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-relaunch',
    name: 'PM Relaunch',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'stopped',
    agent_profile_id: '',
    agent_profile_version: '',
    effective_agent_profile_id: 'full-architect',
    effective_agent_profile_version: '1',
    effective_agent_profile_snapshot: {
      id: 'full-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Full Architect',
      lifecycle: 'stable',
      status: 'full',
      warnings: [],
      denied_high_risk_capabilities: [],
    },
  });

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-relaunch');
  context.agentPanelReceiveAgentProfileAssignment({
    type: 'agent_profile_assignment',
    status: {
      agent_id: 'arch-profile-relaunch',
      assigned_profile_id: 'product-manager-draft',
      assigned_profile_version: '2',
      assigned_at: 100,
      assigned_by: 'trusted-user-ui',
      pending_next_launch: true,
    },
  });
  assert.match(classModalBody.innerHTML, /Desired profile updated\. It will apply on the next launch or relaunch\./);
  assert.match(classModalBody.innerHTML, /Pending[\s\S]*Yes/);

  Object.assign(context.state.agents['arch-profile-relaunch'], {
    status: 'running',
    effective_agent_profile_id: 'product-manager-draft',
    effective_agent_profile_version: '2',
    effective_agent_profile_applied_at: 120,
    effective_agent_profile_snapshot: {
      id: 'product-manager-draft',
      version: '2',
      base_kind: 'architect',
      display_name: 'Product Manager (draft)',
      lifecycle: 'draft',
      status: 'draft',
      warnings: ['Raw Architect tools are denied; use architect_product_* wrappers only'],
      denied_high_risk_capabilities: ['agent.hire_engineer'],
    },
  });
  context.renderAgentPanel();

  assert.doesNotMatch(classModalBody.innerHTML, /Desired profile updated\. It will apply on the next launch or relaunch\./);
  assert.doesNotMatch(classModalBody.innerHTML, /product-manager-draft@2 \(pending next launch\)/);
  assert.match(classModalBody.innerHTML, /Product Manager \(draft\)@2/);
  assert.match(classModalBody.innerHTML, /Pending[\s\S]*No — effective profile matches desired/);
});

test('agent profile manager diagnoses launch snapshot that still does not match desired profile', () => {
  const { context, classModalBody } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-nonapplied',
    name: 'PM Nonapplied',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    agent_profile_id: 'product-manager-draft',
    agent_profile_version: '2',
    agent_profile_assigned_at: 100,
    agent_profile_assigned_by: 'trusted-user-ui',
    effective_agent_profile_id: 'full-architect',
    effective_agent_profile_version: '1',
    effective_agent_profile_applied_at: 120,
    effective_agent_profile_snapshot: {
      id: 'full-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Full Architect',
      lifecycle: 'stable',
      status: 'full',
      warnings: [],
      denied_high_risk_capabilities: [],
    },
  });

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-nonapplied');

  assert.match(classModalBody.innerHTML, /full-architect@1 \(pending next launch\)/);
  assert.match(
    classModalBody.innerHTML,
    /Pending[\s\S]*Yes — last launch froze Full Architect@1, which does not match desired product-manager-draft@2/,
  );
});

test('agent profile manager preserves selected preview state across routine rerenders', () => {
  const { context, classModalBody, sendCalls, captureCalls, restoreCalls } = createHarness();

  setFocusedAgent(context, {
    id: 'arch-profile-preserve',
    name: 'PM Preserve',
    kind: 'architect',
    group: 'alpha',
    cell_type: 'agent',
    status: 'running',
    directory: '/repo',
    agent_profile_id: '',
    agent_profile_version: '',
    effective_agent_profile_id: 'full-architect',
    effective_agent_profile_version: '1',
    effective_agent_profile_snapshot: {
      id: 'full-architect',
      version: '1',
      base_kind: 'architect',
      display_name: 'Full Architect',
      lifecycle: 'stable',
      status: 'full',
    },
  });

  context.agentPanelToggleProfileAssignment(null, 'arch-profile-preserve');
  context.agentPanelReceiveAgentProfiles({
    type: 'agent_profiles',
    profiles: [
      { id: 'full-architect', version: '1', base_kind: 'architect', display_name: 'Full Architect', lifecycle: 'stable', status: 'full' },
      { id: 'product-manager-draft', version: '2', base_kind: 'architect', display_name: 'Product Manager (draft)', lifecycle: 'draft', status: 'draft' },
    ],
    issues: [],
  });
  context.agentPanelReceiveAgentProfilePreview({
    type: 'agent_profile_preview',
    profile: {
      id: 'product-manager-draft',
      version: '2',
      base_kind: 'architect',
      display_name: 'Product Manager (draft)',
      lifecycle: 'draft',
      status: 'draft',
      warnings: ['product-manager-draft is scratch-only in Wave 4B'],
      denied_high_risk_capabilities: ['task.dispatch'],
      scope_policy: { summary: 'planning scope' },
    },
  });
  context.agentPanelSelectProfile('arch-profile-preserve', 'product-manager-draft');
  const sendsAfterSelect = sendCalls.length;
  const capturesAfterSelect = captureCalls.length;
  const restoresAfterSelect = restoreCalls.length;

  context.state.agents['arch-profile-preserve'].activity_detail = 'routine heartbeat delta';
  context.renderAgentPanel();

  assert.equal(sendCalls.length, sendsAfterSelect, 'routine rerender does not refetch list or preview');
  assert.ok(captureCalls.length > capturesAfterSelect, 'rerender captures surface state');
  assert.ok(restoreCalls.length > restoresAfterSelect, 'rerender restores surface state');
  assert.match(classModalBody.innerHTML, /<option value="product-manager-draft" selected>/);
  assert.match(classModalBody.innerHTML, /Product Manager \(draft\)@2/);
  assert.match(classModalBody.innerHTML, /Agent is running; this UI will not stop or relaunch it/);
});

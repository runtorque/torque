const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(...items) { items.forEach((item) => this.items.add(item)); }
  remove(...items) { items.forEach((item) => this.items.delete(item)); }
  contains(item) { return this.items.has(item); }
  toggle(item, force) {
    if (force === true) { this.items.add(item); return true; }
    if (force === false) { this.items.delete(item); return false; }
    if (this.items.has(item)) { this.items.delete(item); return false; }
    this.items.add(item); return true;
  }
}

class FakeElement {
  constructor(id = '', document = null) {
    this.id = id;
    this.ownerDocument = document;
    this.value = '';
    this.checked = false;
    this.textContent = '';
    this.innerHTML = '';
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = {};
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollTop = 0;
    this.children = [];
    this.disabled = false;
    this.readOnly = false;
    this.onchange = null;
  }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; this.focused = true; }
  select() { this.selectionStart = 0; this.selectionEnd = String(this.value || '').length; }
  contains(target) { return target === this || this.children.includes(target) || !!(target && target.id); }
  querySelectorAll(selector) {
    if (!this.ownerDocument) return [];
    if (selector === 'textarea') return Object.values(this.ownerDocument.elements).filter((el) => el.tagName === 'TEXTAREA');
    return [];
  }
  querySelector() { return null; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name] || null; }
}

class FakeDocument {
  constructor() {
    this.elements = {};
    this.activeElement = null;
  }
  register(id, tagName = 'DIV') {
    const el = new FakeElement(id, this);
    el.tagName = tagName;
    this.elements[id] = el;
    return el;
  }
  createElement(tagName) {
    return new FakeElement('', this);
  }
  getElementById(id) { return this.elements[id] || null; }
  querySelectorAll() { return []; }
}

function loadScript(context, relPath) {
  const source = fs.readFileSync(path.join(repoRoot, relPath), 'utf8');
  vm.runInContext(source, context, { filename: relPath });
}

function createHarness({ loadModals = false, loadAgentPanel = false } = {}) {
  const document = new FakeDocument();
  const panel = document.register('panel-templates');
  document.register('agent-class-editor');
  const sendCalls = [];
  const toasts = [];
  const confirms = [];
  const sandbox = {
    console,
    Date,
    state: {
      groups: { alpha: [], beta: [] },
      group_settings: { alpha: { default_directory: '/repo' } },
      agents: {},
      agent_classes: [],
      agent_class_issues: [],
    },
    document,
    window: {},
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },
    _currentGroup() { return 'alpha'; },
    _tplAutoResize() {},
    send(message) { sendCalls.push(JSON.parse(JSON.stringify(message))); },
    _showToast(message, level) { toasts.push({ message, level }); },
    showConfirm(message) { confirms.push(message); return Promise.resolve(true); },
    closeModals() { sandbox.closed = true; },
    _textToEnv() { return {}; },
    _envToText() { return ''; },
    _populateProviderSelect() {},
    _populateTemplateSelect() {},
    _populateReasoningEffortSelect() {},
    _getProviderValue(id) { const el = document.getElementById(id); return el ? el.value : ''; },
    _runtimeDefaultProviderName() { return 'codex'; },
    _runtimeDefaultCommand() { return 'codex'; },
    _findProviderMeta() { return null; },
    onAddProviderChange() {},
    _toggleAddWorktreeFields() {},
    AGENT_ICONS: ['🤖'],
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  if (loadModals) {
    loadScript(context, 'static/js/modals.js');
    loadScript(context, 'static/js/modals/engineer-launch.js');
    loadScript(context, 'static/js/modals/add-cell.js');
  }
  loadScript(context, 'static/js/templates.js');
  if (loadAgentPanel) {
    loadScript(context, 'static/js/behavior_overlay.js');
    loadScript(context, 'static/js/agent_panel.js');
  }
  return { context, document, panel, sendCalls, toasts, confirms, sandbox };
}

function run(context, code) { return vm.runInContext(code, context); }
function json(context, expr) { return JSON.parse(run(context, `JSON.stringify(${expr})`)); }
function classUi(document, panel) {
  const editor = document.getElementById('agent-class-editor');
  return (panel.innerHTML || '') + '\n' + (editor ? editor.innerHTML || '' : '');
}
function countText(haystack, needle) {
  return String(haystack || '').split(needle).length - 1;
}

function sampleCapabilityCatalog() {
  return [
    {
      id: 'self_context',
      label: 'Self and assigned task context',
      summary: 'Read own agent/session context and visible assigned task details.',
      category: 'read',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'task_reporting',
      label: 'Task reporting and verification',
      summary: 'Report progress/completion, record verification, and attach task artifacts.',
      category: 'task',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'planning_area_reads',
      label: 'Area reads',
      summary: 'Read visible Areas and area context.',
      category: 'planning',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'planning_reads',
      label: 'Planning reads',
      summary: 'Read board/task planning summaries, Areas, Initiatives, and Decisions.',
      category: 'planning',
      risk: 'normal',
      base_kinds: ['architect', 'engineer'],
      available: true,
    },
    {
      id: 'board_task_reads',
      label: 'Board/task reads',
      summary: 'Read board/task detail, events, MCP call telemetry, and board-sync status.',
      category: 'task',
      risk: 'normal',
      base_kinds: ['architect', 'engineer'],
      available: true,
    },
    {
      id: 'user_messages',
      label: 'User messages',
      summary: 'Ask the user for blocking decisions and send non-blocking user-facing messages.',
      category: 'communication',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'private_journal',
      label: 'Private journal',
      summary: 'Use the private recovery journal for the running agent.',
      category: 'journal',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'shared_memory',
      label: 'Shared memory',
      summary: 'Read and publish shared memory entries.',
      category: 'memory',
      risk: 'normal',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'planning_writes',
      label: 'Planning writes',
      summary: 'Create/update/archive Areas and Initiatives.',
      category: 'planning',
      risk: 'high',
      base_kinds: ['architect'],
      available: true,
    },
    {
      id: 'scoped_journals',
      label: 'Scoped journals',
      summary: 'Read/write scoped Engineer or Architect journals.',
      category: 'journal',
      risk: 'normal',
      base_kinds: ['architect', 'engineer'],
      available: true,
    },
    {
      id: 'shared_memory_admin',
      label: 'Shared memory admin',
      summary: 'Pin, unpin, and link shared memory entries.',
      category: 'memory',
      risk: 'high',
      base_kinds: ['architect', 'engineer'],
      available: true,
    },
    {
      id: 'worker_dispatch',
      label: 'Worker dispatch',
      summary: 'Launch or route work to Workers.',
      category: 'agent_management',
      risk: 'critical',
      base_kinds: ['architect', 'engineer'],
      available: true,
    },
  ];
}

function sampleRestrictionCatalog() {
  return [
    {
      id: 'deny_high_risk_operations',
      label: 'Deny remaining high-risk operations',
      summary: 'Explicitly deny high-risk/critical operations not selected by capability buckets.',
      category: 'safety',
      risk: 'critical',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'deny_raw_tool_picker',
      label: 'Deny raw tool picker',
      summary: 'Records that arbitrary raw tool picker authority is outside the class contract.',
      category: 'admin',
      risk: 'critical',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
    {
      id: 'deny_worker_dispatch',
      label: 'Deny Worker dispatch',
      summary: 'Explicitly deny Worker launch/routing and task dispatch authority.',
      category: 'agent_management',
      risk: 'critical',
      base_kinds: ['architect', 'engineer', 'worker'],
      available: true,
    },
  ];
}

function catalogItem(id, restriction = false) {
  const catalog = restriction ? sampleRestrictionCatalog() : sampleCapabilityCatalog();
  return catalog.find((item) => item.id === id) || { id, label: id, summary: '' };
}

function sampleAgentClassListMessage(classes = sampleClasses(), issues = []) {
  return {
    type: 'agent_classes',
    schema_version: 3,
    classes,
    issues,
    authoring_contract: {
      schema_version: 3,
      normal_authoring_mode: 'capability_buckets',
      capability_bucket_field: 'capabilities.buckets',
      restriction_bucket_field: 'capabilities.restrictions',
      capability_bucket_catalog: sampleCapabilityCatalog(),
      restriction_bucket_catalog: sampleRestrictionCatalog(),
    },
    capability_bucket_catalog: sampleCapabilityCatalog(),
    restriction_bucket_catalog: sampleRestrictionCatalog(),
  };
}

function registerClassForm(document) {
  [
    ['agent-class-id', 'INPUT'],
    ['agent-class-version', 'INPUT'],
    ['agent-class-base-kind', 'SELECT'],
    ['agent-class-display-name', 'INPUT'],
    ['agent-class-description', 'INPUT'],
    ['agent-class-lifecycle', 'SELECT'],
    ['agent-class-profile-id', 'SELECT'],
    ['agent-class-profile-version', 'INPUT'],
    ['agent-class-prompt', 'TEXTAREA'],
    ['agent-class-ui-label', 'INPUT'],
    ['agent-class-ui-icon', 'INPUT'],
    ['agent-class-ui-badge', 'INPUT'],
    ['agent-class-ui-color', 'INPUT'],
    ['agent-class-archetype', 'INPUT'],
    ['agent-class-scratch-only', 'INPUT'],
    ['agent-class-launch-name', 'INPUT'],
    ['agent-class-launch-group', 'SELECT'],
  ].forEach(([id, tag]) => {
    if (!document.getElementById(id)) document.register(id, tag);
  });
  sampleCapabilityCatalog().forEach((bucket) => {
    const id = `agent-class-capability-bucket-${bucket.id}`;
    if (!document.getElementById(id)) document.register(id, 'INPUT');
  });
  sampleRestrictionCatalog().forEach((bucket) => {
    const id = `agent-class-restriction-bucket-${bucket.id}`;
    if (!document.getElementById(id)) document.register(id, 'INPUT');
  });
}

function sampleClasses() {
  return [
    {
      id: 'default-worker', version: '1', base_kind: 'worker', display_name: 'Default Worker',
      lifecycle: 'stable', builtin: true, custom: false, source: 'builtin', status: 'full', launchable: true,
      agent_profile_ref: { id: 'full-worker', version: '1' }, prompt_summary: { has_prompt: false, char_count: 0 },
      restrictions: ['Agent Profile remains the MCP/capability enforcement layer.'], external_connector_caveat: 'External connector caveat.',
    },
    {
      id: 'review-worker', version: '1', base_kind: 'worker', display_name: 'Review Worker', description: 'Reviews patches.',
      lifecycle: 'stable', builtin: false, custom: true, source: 'project', source_path: '/repo/.torque/agent_classes/review-worker.yaml', status: 'restricted', launchable: true,
      agent_class_schema_version: 3,
      runtime: { base_kind: 'worker', base_kind_label: 'Worker', arbitrary_runtime_kind: false },
      policy: { mode: 'compile', policy_schema_version: 1 },
      capabilities: { buckets: ['self_context', 'task_reporting', 'shared_memory'], restrictions: ['deny_raw_tool_picker', 'deny_high_risk_operations'] },
      capability_bucket_selection: ['self_context', 'task_reporting', 'shared_memory'],
      restriction_bucket_selection: ['deny_raw_tool_picker', 'deny_high_risk_operations'],
      capability_buckets: [catalogItem('self_context'), catalogItem('task_reporting'), catalogItem('shared_memory')],
      restriction_buckets: [catalogItem('deny_high_risk_operations', true), catalogItem('deny_raw_tool_picker', true)],
      operator_access_summary: {
        allowed: ['Self and assigned task context', 'Task reporting and verification', 'Shared memory'],
        denied: ['Deny remaining high-risk operations', 'Deny raw tool picker'],
        allowed_summary: 'Self and assigned task context; Task reporting and verification; Shared memory',
        denied_summary: 'Deny raw tool picker; Deny remaining high-risk operations',
      },
      apply_state: { mutates_running_sessions: false, applies_at: 'next_launch_or_relaunch', relaunch_required_after_assignment: true },
      agent_profile_ref: { id: 'class-policy-review-worker', version: '1' }, agent_profile: { id: 'class-policy-review-worker', version: '1', status: 'restricted', capability_count: 5 },
      prompt: 'Focus on UI regressions.', prompt_summary: { has_prompt: true, char_count: 24, preview: 'Focus on UI regressions.' },
      restrictions: ['No raw tool grants.'], warnings: ['Use reviewed YAML.'], external_connector_caveat: 'External connector caveat.',
    },
    {
      id: 'old-worker', version: '1', base_kind: 'worker', display_name: 'Old Worker',
      lifecycle: 'stable', builtin: false, custom: true, source: 'project', status: 'archived', archived: true, disabled: true, launchable: false,
      agent_profile_ref: { id: 'full-worker', version: '1' }, metadata: { archived: true, archived_at: '2026-06-20T00:00:00Z' },
      warnings: ['old-worker is archived/disabled and cannot be assigned or launched until re-enabled.'], external_connector_caveat: 'External connector caveat.',
    },
    {
      id: 'product-manager', version: '2', base_kind: 'architect', display_name: 'Product Manager',
      primary_identity_label: 'Product Manager', secondary_base_kind_label: 'Architect-derived',
      lifecycle: 'draft', builtin: true, custom: false, source: 'builtin', status: 'draft', scratch_only: true, launchable: true,
      agent_profile_ref: { id: 'class-policy-product-manager', version: '2' }, agent_profile: { id: 'class-policy-product-manager', version: '2', status: 'draft', capability_count: 3 },
      internal_policy: { mode: 'compile', profile_source: 'compiled_from_agent_class', generated_profile_written_to_project_yaml: false },
      prompt_summary: { has_prompt: true, char_count: 64, preview: 'PM draft instructions.' }, draft: { scratch_only: true },
      capability_bucket_selection: ['self_context', 'planning_reads', 'proposed_decisions', 'board_task_proposals', 'user_messages', 'product_peer_messages', 'private_journal'],
      restriction_bucket_selection: ['deny_worker_dispatch', 'deny_raw_tool_picker'],
      warnings: ['Product Manager is draft/scratch-only in Wave 6B.'], external_connector_caveat: 'External connector caveat.',
      restrictions: ['Do not use for live PM dogfood.'],
    },
  ];
}

test('Agent Class manager renders class list, PM operator access summary, archived disabled preview, and explicit launch command', () => {
  const { context, document, panel, sendCalls } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  assert.deepEqual(sendCalls[0], { cmd: 'agent_class_list', base_dir: '/repo' });

  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses()))})`);
  assert.match(classUi(document, panel), /Default Worker/);
  assert.match(classUi(document, panel), /Review Worker/);
  assert.match(classUi(document, panel), /Old Worker/);
  assert.match(classUi(document, panel), /Product Manager/);

  run(context, `agentClassManagerSelect('product-manager')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[3])} })`);
  assert.match(classUi(document, panel), /Product Manager@2/);
  assert.match(classUi(document, panel), /Primary identity[\s\S]*Product Manager/);
  assert.match(classUi(document, panel), /Advanced\/Internal diagnostics/);
  assert.match(classUi(document, panel), /draft/);
  assert.doesNotMatch(classUi(document, panel), /External connectors/);
  assert.match(classUi(document, panel), /Product planning and intake class with bounded Torque access/);
  assert.match(classUi(document, panel), /Allowed[\s\S]*planning reads\/writes, proposed decisions, queued task intake, user \+ peer Architect coordination/);
  assert.match(classUi(document, panel), /Denied[\s\S]*hire\/dispatch, merge\/deploy\/admin, raw tools, direct Engineer\/Worker messages/);
  assert.doesNotMatch(classUi(document, panel), /agent-class-restrictions[\s\S]*Do not use for live PM dogfood/);

  run(context, `agentClassManagerSelect('old-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[2])} })`);
  assert.match(classUi(document, panel), /Archived\/disabled Agent Classes cannot launch/);

  run(context, `agentClassManagerSelect('review-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[1])} })`);
  const reviewHtml = classUi(document, panel);
  assert.match(reviewHtml, /Operator access preview/);
  assert.match(reviewHtml, /Capability access[\s\S]*Self and assigned task context; Task reporting and verification; Shared memory/);
  assert.match(reviewHtml, /Relaunch behavior[\s\S]*Access freezes on the next launch or relaunch/);
  const previewStart = reviewHtml.indexOf('<div class="agent-class-preview');
  const diagnosticsStart = reviewHtml.indexOf('<details class="agent-class-normalized', previewStart);
  const normalPreviewHtml = reviewHtml.slice(previewStart, diagnosticsStart);
  assert.doesNotMatch(normalPreviewHtml, /class-policy-review-worker|Agent Profile|generated profile|compiler/i);
  document.getElementById('agent-class-launch-name').value = 'Patch Reviewer';
  document.getElementById('agent-class-launch-group').value = 'alpha';
  run(context, `agentClassManagerLaunchSelected()`);
  assert.deepEqual(sendCalls.at(-1), {
    cmd: 'create_agent_from_class',
    class_id: 'review-worker',
    kind: 'worker',
    name: 'Patch Reviewer',
    group: 'alpha',
    base_dir: '/repo',
  });
});

test('Agent Class manager presents approved Product Manager dogfood state with compact operator access summary', () => {
  const { context, document, panel } = createHarness();
  registerClassForm(document);
  const classes = sampleClasses();
  const pm = Object.assign({}, classes[3], {
    lifecycle: 'stable',
    status: 'restricted',
    scratch_only: false,
    draft: { scratch_only: false, approved_for_live_dogfood: true },
    warnings: [
      'External connectors are not governed by Agent Classes/Profile policy in Wave 7; manage connector access separately.',
      'External connector exposure is not governed or enforced by Agent Classes or Agent Profiles in Wave 7; manage connector access separately.',
      'Product Manager cannot dispatch, merge, deploy, administer, use raw tool picker authority, or message engineers/workers directly.',
    ],
    external_connector_caveat: 'External connector exposure is not governed or enforced by Agent Classes or Agent Profiles in Wave 7; manage connector access separately.',
    restrictions: [
      'Cannot dispatch workers.',
      'Cannot deploy.',
    ],
  });
  classes[3] = pm;

  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(classes))})`);
  run(context, `agentClassManagerSelect('product-manager')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(pm)} })`);

  const html = classUi(document, panel);
  assert.match(html, /Product Manager@2/);
  assert.match(html, /approved dogfood/);
  assert.match(html, /PM-safe authority/);
  assert.match(html, /Product planning and intake class with bounded Torque access/);
  assert.match(html, /Allowed[\s\S]*planning reads\/writes, proposed decisions, queued task intake, user \+ peer Architect coordination/);
  assert.match(html, /Denied[\s\S]*hire\/dispatch, merge\/deploy\/admin, raw tools, direct Engineer\/Worker messages/);
  assert.doesNotMatch(html, /External connectors/);
  assert.doesNotMatch(html, /agent-class-issues[\s\S]*External connector exposure/);
  assert.doesNotMatch(html, /agent-class-restrictions/);
});

test('Agent Class authoring validates before save, shows validation issues, and archives/deletes custom classes', async () => {
  const { context, document, panel, sendCalls } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses()))})`);
  run(context, `agentClassManagerNew('architect')`);
  assert.match(classUi(document, panel), /Purpose and reviewed access buckets/);
  assert.match(classUi(document, panel), /Self and assigned task context/);
  assert.match(classUi(document, panel), /Task reporting and verification/);
  assert.match(classUi(document, panel), /Advanced\/Internal bucket catalog[\s\S]*Planning writes/);
  assert.match(classUi(document, panel), /Scoped journals/);
  run(context, `agentClassManagerNew('worker')`);

  document.getElementById('agent-class-id').value = 'qa-worker';
  document.getElementById('agent-class-version').value = '1';
  document.getElementById('agent-class-base-kind').value = 'worker';
  document.getElementById('agent-class-display-name').value = 'QA Worker';
  document.getElementById('agent-class-description').value = 'Checks UI.';
  document.getElementById('agent-class-lifecycle').value = 'stable';
  document.getElementById('agent-class-profile-id').value = 'full-worker';
  document.getElementById('agent-class-profile-version').value = '1';
  document.getElementById('agent-class-prompt').value = 'Check state preservation.';
  run(context, `agentClassManagerValidate()`);
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_validate');
  assert.equal(sendCalls.at(-1).agent_class.id, 'qa-worker');
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('self_context'), true);
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('task_reporting'), true);
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('planning_writes'), false);
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('scoped_journals'), false);

  run(context, `agentClassManagerReceiveValidation({ type: 'agent_class_validation', request_id: _agentClassValidationRequestId, valid: false, ok: false, errors: [{ severity: 'error', code: 'bad', message: 'Display name is unsafe' }], warnings: [], agent_class: null })`);
  assert.match(classUi(document, panel), /Display name is unsafe/);
  run(context, `agentClassManagerSave()`);
  assert.notEqual(sendCalls.at(-1).cmd, 'agent_class_create');

  run(context, `agentClassManagerReceiveValidation({ type: 'agent_class_validation', request_id: _agentClassValidationRequestId, valid: true, ok: true, errors: [], warnings: ['Review YAML before commit.'], normalized: { id: 'qa-worker' }, authoring_contract: ${JSON.stringify(sampleAgentClassListMessage().authoring_contract)}, agent_class: { id: 'qa-worker', version: '1', base_kind: 'worker', agent_class_schema_version: 3, display_name: 'QA Worker', description: 'Checks UI.', purpose: 'Checks UI.', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, policy: { mode: 'compile', policy_schema_version: 1 }, capabilities: { buckets: ['user_messages', 'private_journal', 'shared_memory', 'planning_area_reads', 'self_context', 'task_reporting'], restrictions: ['deny_raw_tool_picker', 'deny_high_risk_operations'] }, capability_bucket_selection: ['user_messages', 'private_journal', 'shared_memory', 'planning_area_reads', 'self_context', 'task_reporting'], restriction_bucket_selection: ['deny_raw_tool_picker', 'deny_high_risk_operations'], capability_buckets: [${JSON.stringify(catalogItem('self_context'))}, ${JSON.stringify(catalogItem('task_reporting'))}, ${JSON.stringify(catalogItem('planning_area_reads'))}, ${JSON.stringify(catalogItem('user_messages'))}, ${JSON.stringify(catalogItem('private_journal'))}, ${JSON.stringify(catalogItem('shared_memory'))}], restriction_buckets: [${JSON.stringify(catalogItem('deny_high_risk_operations', true))}, ${JSON.stringify(catalogItem('deny_raw_tool_picker', true))}], operator_access_summary: { allowed_summary: 'User messages; Private journal; Shared memory; Area reads; Self and assigned task context; Task reporting and verification', denied_summary: 'Deny raw tool picker; Deny remaining high-risk operations' }, apply_state: { mutates_running_sessions: false, applies_at: 'next_launch_or_relaunch', relaunch_required_after_assignment: true }, prompt_summary: { has_prompt: true, char_count: 25, preview: 'Check state preservation.' }, external_connector_caveat: 'External connector caveat.', restrictions: [] } })`);
  run(context, `agentClassManagerSave()`);
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_create');
  assert.equal(sendCalls.at(-1).agent_class.policy.mode, 'compile');
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('self_context'), true);
  assert.equal(sendCalls.at(-1).agent_class.capabilities.buckets.includes('task_reporting'), true);
  assert.equal(Object.prototype.hasOwnProperty.call(sendCalls.at(-1).agent_class, 'agent_profile_ref'), false);

  run(context, `agentClassManagerReceiveMutation({ type: 'agent_class_save', ok: true, operation: 'created', agent_class: { id: 'qa-worker', version: '1', base_kind: 'worker', display_name: 'QA Worker', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, policy: { mode: 'compile' }, capabilities: { buckets: ['self_context', 'task_reporting'] } }, classes: ${JSON.stringify(sampleClasses())} })`);
  run(context, `agentClassManagerArchive()`);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_archive');
  assert.equal(sendCalls.at(-1).class_id, 'qa-worker');

  run(context, `agentClassManagerDelete()`);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_delete');
  assert.equal(sendCalls.at(-1).class_id, 'qa-worker');
});

test('Agent Class editor errors are not swallowed by inactive agent-panel class handler', () => {
  const { context, document, panel } = createHarness({ loadAgentPanel: true });
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses()))})`);
  run(context, `agentClassManagerNew('worker')`);
  run(context, `
    _agentClassValidationInFlight = true;
    _agentClassLastMutationRequestId = 'agent-class-save-1';
    _agentClassEditorError = '';
    _agentClassEditorMessage = 'Saving Agent Class…';
  `);

  const panelHandled = run(context, `agentPanelHandleAgentClassError({
    message: 'Unknown Agent Class: product-manager',
    code: 'agent_class_preview_failed'
  })`);
  assert.equal(panelHandled, false);

  const managerHandled = run(context, `agentClassManagerHandleError({
    message: 'Unknown Agent Class: product-manager',
    code: 'agent_class_preview_failed'
  })`);
  assert.equal(managerHandled, true);
  assert.equal(run(context, `_agentClassValidationInFlight`), false);
  assert.equal(run(context, `_agentClassLastMutationRequestId`), '');
  assert.equal(run(context, `_agentClassEditorError`), 'Unknown Agent Class: product-manager');
  assert.match(classUi(document, panel), /Unknown Agent Class: product-manager/);
});

test('Agent Class manager preserves focused draft, caret, and scroll across rerenders', () => {
  const { context, document, panel } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses()))})`);
  run(context, `agentClassManagerSelect('review-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[1])} })`);

  document.getElementById('agent-class-id').value = 'review-worker';
  document.getElementById('agent-class-version').value = '1';
  document.getElementById('agent-class-base-kind').value = 'worker';
  document.getElementById('agent-class-display-name').value = 'Review Worker';
  document.getElementById('agent-class-description').value = 'Reviews patches.';
  document.getElementById('agent-class-lifecycle').value = 'stable';
  document.getElementById('agent-class-profile-id').value = 'restricted-worker';
  document.getElementById('agent-class-profile-version').value = '2';
  const prompt = document.getElementById('agent-class-prompt');
  prompt.value = 'operator draft that must survive';
  prompt.selectionStart = 9;
  prompt.selectionEnd = 14;
  prompt.scrollTop = 33;
  prompt.focus();
  document.getElementById('agent-class-editor').scrollTop = 77;
  run(context, `_agentClassEditorDirty = true; _agentClassSkipNextDraftCapture = false; renderAgentClassesPanel();`);

  assert.match(classUi(document, panel), /operator draft that must survive/);
  assert.equal(document.activeElement.id, 'agent-class-prompt');
  assert.equal(prompt.selectionStart, 9);
  assert.equal(prompt.selectionEnd, 14);
  assert.equal(prompt.scrollTop, 33);
  assert.equal(document.getElementById('agent-class-editor').scrollTop, 77);
});

function registerAddWorkerDom(document) {
  [
    'add-name-input', 'add-group-select', 'add-cmd-input', 'add-dir-select', 'add-dir-input', 'add-shell-select',
    'add-env-vars', 'add-template-select', 'add-provider-select', 'add-model-input', 'add-reasoning-effort',
    'add-wt-enabled', 'add-wt-base-dir', 'add-wt-base-branch', 'add-wt-name', 'add-wt-auto-checkpoint',
    'add-wt-checkpoint-on-progress', 'add-wt-squash', 'add-agent-class-row', 'add-agent-class-select', 'add-agent-class-hint'
  ].forEach((id) => document.register(id, id.endsWith('select') ? 'SELECT' : 'INPUT'));
  document.getElementById('add-name-input').value = 'Standalone Worker';
  document.getElementById('add-group-select').value = 'alpha';
  document.getElementById('add-dir-select').value = '';
  document.getElementById('add-wt-enabled').checked = false;
  document.getElementById('add-wt-squash').checked = true;
}

function registerEngineerArchitectDom(document) {
  ['modal-engineer', 'modal-engineer-summary', 'modal-engineer-title', 'engineer-submit-btn', 'engineer-name-input', 'engineer-command-input', 'engineer-specializations-row', 'engineer-agent-class-row', 'engineer-agent-class-select', 'engineer-agent-class-hint', 'engineer-specializations-selected', 'engineer-specializations-available']
    .forEach((id) => document.register(id));
  ['modal-architect', 'modal-architect-summary', 'architect-name-input', 'architect-command-input', 'architect-agent-class-row', 'architect-agent-class-select', 'architect-agent-class-hint']
    .forEach((id) => document.register(id));
}

test('Agent Class pickers filter by base kind and preserve no-class default add flows', () => {
  const { context, document, sendCalls } = createHarness({ loadModals: true });
  registerAddWorkerDom(document);
  registerEngineerArchitectDom(document);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses().concat([{ id: 'team-engineer', version: '1', base_kind: 'engineer', display_name: 'Team Engineer', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-engineer', version: '1' } }])))})`);

  run(context, `agentClassPickerPrepare('worker', 'alpha', '/repo', 'add-worker')`);
  assert.match(document.getElementById('add-agent-class-select').innerHTML, /Review Worker/);
  assert.doesNotMatch(document.getElementById('add-agent-class-select').innerHTML, /Product Manager/);
  assert.match(document.getElementById('add-agent-class-select').innerHTML, /Old Worker[\s\S]*disabled/);

  run(context, `addCellMode = 'worker'; submitAdd();`);
  assert.deepEqual(sendCalls.at(-1), {
    cmd: 'add_worker',
    name: 'Standalone Worker',
    group: 'alpha',
    worktree: false,
  });

  run(context, `agentClassPickerSelect('add-worker', 'review-worker'); submitAdd();`);
  assert.equal(sendCalls.at(-1).cmd, 'add_worker');
  assert.equal(sendCalls.at(-1).agent_class_id, 'review-worker');

  run(context, `openAddEngineerModal({ group: 'alpha' }); agentClassPickerSelect('add-engineer', '');`);
  document.getElementById('engineer-name-input').value = 'Builder';
  run(context, `submitAddEngineer();`);
  assert.deepEqual(sendCalls.at(-1), { cmd: 'add_engineer', name: 'Builder', group: 'alpha' });

  document.getElementById('engineer-name-input').value = 'Builder PM';
  run(context, `agentClassPickerSelect('add-engineer', 'team-engineer'); submitAddEngineer();`);
  assert.equal(sendCalls.at(-1).cmd, 'add_engineer');
  assert.equal(sendCalls.at(-1).agent_class_id, 'team-engineer');

  run(context, `openAddArchitectModal({ group: 'alpha' }); agentClassPickerSelect('add-architect', 'product-manager');`);
  document.getElementById('architect-name-input').value = 'Planner';
  run(context, `submitAddArchitect();`);
  assert.equal(sendCalls.at(-1).cmd, 'add_architect');
  assert.equal(sendCalls.at(-1).agent_class_id, 'product-manager');
});

test('Agent Class add-worker picker blocks stale archived selections instead of defaulting', () => {
  const { context, document, sendCalls, toasts } = createHarness({ loadModals: true });
  registerAddWorkerDom(document);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses()))})`);
  run(context, `agentClassPickerPrepare('worker', 'alpha', '/repo', 'add-worker'); agentClassPickerSelect('add-worker', 'review-worker');`);

  const archivedReviewWorker = sampleClasses().map((item) => item.id === 'review-worker'
    ? { ...item, status: 'archived', archived: true, disabled: true, launchable: false, metadata: { archived: true } }
    : item);
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(archivedReviewWorker))})`);
  const staleState = json(context, `agentClassPickerSelectionState('add-worker')`);
  assert.equal(staleState.ok, false);
  assert.equal(staleState.selectedId, 'review-worker');
  assert.match(staleState.reason, /Archived\/disabled/);
  assert.match(document.getElementById('add-agent-class-hint').textContent, /Archived\/disabled/);

  const before = sendCalls.length;
  run(context, `addCellMode = 'worker'; submitAdd();`);
  assert.equal(sendCalls.length, before);
  assert.match(toasts.at(-1).message, /Archived\/disabled/);

  run(context, `agentClassPickerSelect('add-worker', ''); submitAdd();`);
  assert.deepEqual(sendCalls.at(-1), {
    cmd: 'add_worker',
    name: 'Standalone Worker',
    group: 'alpha',
    worktree: false,
  });
});

test('Engineer launch modal uses explicit launch-from-class only when a class is selected', () => {
  const { context, document, sendCalls } = createHarness({ loadModals: true });
  [
    'modal-engineer-launch', 'engineer-launch-title', 'engineer-launch-group', 'engineer-launch-submit-btn',
    'engineer-launch-provider', 'engineer-launch-boot-cmd', 'engineer-launch-model', 'engineer-launch-reasoning-effort',
    'engineer-launch-custom-instructions', 'engineer-launch-autonomy-mode', 'engineer-launch-default-worker-concurrency',
    'engineer-launch-wave-size-preference', 'engineer-launch-same-agent-follow-up-preference', 'engineer-launch-digest-verbosity',
    'engineer-launch-escalation-style', 'engineer-launch-notification-preset', 'engineer-launch-notification-preset-hint',
    'engineer-launch-specializations-selected', 'engineer-launch-specializations-available', 'engineer-launch-specializations-reset',
    'engineer-launch-agent-class-row', 'engineer-launch-agent-class-select', 'engineer-launch-agent-class-hint'
  ].forEach((id) => document.register(id));
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses().concat([{ id: 'team-engineer', version: '1', base_kind: 'engineer', display_name: 'Team Engineer', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-engineer', version: '1' } }])))})`);
  run(context, `openEngineerLaunchDialog('alpha', '')`);
  run(context, `submitEngineerLaunchDialog()`);
  assert.equal(sendCalls.at(-1).cmd, 'add_agent');
  assert.equal(sendCalls.at(-1).is_engineer, true);
  assert.equal(Object.prototype.hasOwnProperty.call(sendCalls.at(-1), 'agent_class_id'), false);

  run(context, `openEngineerLaunchDialog('alpha', ''); agentClassPickerSelect('engineer-launch', 'team-engineer'); submitEngineerLaunchDialog()`);
  assert.equal(sendCalls.at(-1).cmd, 'create_agent_from_class');
  assert.equal(sendCalls.at(-1).class_id, 'team-engineer');
  assert.equal(sendCalls.at(-1).kind, 'engineer');
});

test('Engineer launch picker blocks stale incompatible selections instead of defaulting', () => {
  const { context, document, sendCalls, toasts } = createHarness({ loadModals: true });
  [
    'modal-engineer-launch', 'engineer-launch-title', 'engineer-launch-group', 'engineer-launch-submit-btn',
    'engineer-launch-provider', 'engineer-launch-boot-cmd', 'engineer-launch-model', 'engineer-launch-reasoning-effort',
    'engineer-launch-custom-instructions', 'engineer-launch-autonomy-mode', 'engineer-launch-default-worker-concurrency',
    'engineer-launch-wave-size-preference', 'engineer-launch-same-agent-follow-up-preference', 'engineer-launch-digest-verbosity',
    'engineer-launch-escalation-style', 'engineer-launch-notification-preset', 'engineer-launch-notification-preset-hint',
    'engineer-launch-specializations-selected', 'engineer-launch-specializations-available', 'engineer-launch-specializations-reset',
    'engineer-launch-agent-class-row', 'engineer-launch-agent-class-select', 'engineer-launch-agent-class-hint'
  ].forEach((id) => document.register(id));
  const validEngineer = { id: 'team-engineer', version: '1', base_kind: 'engineer', display_name: 'Team Engineer', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-engineer', version: '1' } };
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses().concat([validEngineer])))})`);
  run(context, `openEngineerLaunchDialog('alpha', ''); agentClassPickerSelect('engineer-launch', 'team-engineer');`);

  const incompatibleEngineer = { ...validEngineer, base_kind: 'worker', agent_profile_ref: { id: 'full-worker', version: '1' } };
  run(context, `agentClassManagerReceiveList(${JSON.stringify(sampleAgentClassListMessage(sampleClasses().concat([incompatibleEngineer])))})`);
  const staleState = json(context, `agentClassPickerSelectionState('engineer-launch')`);
  assert.equal(staleState.ok, false);
  assert.equal(staleState.selectedId, 'team-engineer');
  assert.match(staleState.reason, /base kind does not match/);
  assert.match(document.getElementById('engineer-launch-agent-class-hint').textContent, /base kind does not match/);

  const before = sendCalls.length;
  run(context, `submitEngineerLaunchDialog()`);
  assert.equal(sendCalls.length, before);
  assert.match(toasts.at(-1).message, /base kind does not match/);

  run(context, `agentClassPickerSelect('engineer-launch', ''); submitEngineerLaunchDialog()`);
  assert.equal(sendCalls.at(-1).cmd, 'add_agent');
  assert.equal(sendCalls.at(-1).is_engineer, true);
  assert.equal(Object.prototype.hasOwnProperty.call(sendCalls.at(-1), 'agent_class_id'), false);
});

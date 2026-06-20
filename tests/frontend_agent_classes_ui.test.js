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
  getElementById(id) { return this.elements[id] || null; }
  querySelectorAll() { return []; }
}

function loadScript(context, relPath) {
  const source = fs.readFileSync(path.join(repoRoot, relPath), 'utf8');
  vm.runInContext(source, context, { filename: relPath });
}

function createHarness({ loadModals = false } = {}) {
  const document = new FakeDocument();
  const panel = document.register('panel-templates');
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
  return { context, document, panel, sendCalls, toasts, confirms, sandbox };
}

function run(context, code) { return vm.runInContext(code, context); }
function json(context, expr) { return JSON.parse(run(context, `JSON.stringify(${expr})`)); }

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
      agent_profile_ref: { id: 'restricted-worker', version: '2' }, agent_profile: { id: 'restricted-worker', version: '2', status: 'restricted', capability_count: 5 },
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
      id: 'product-manager', version: '1', base_kind: 'architect', display_name: 'Product Manager (draft)',
      lifecycle: 'draft', builtin: true, custom: false, source: 'builtin', status: 'draft', scratch_only: true, launchable: true,
      agent_profile_ref: { id: 'product-manager-draft', version: '2' }, agent_profile: { id: 'product-manager-draft', version: '2', status: 'draft', capability_count: 3 },
      prompt_summary: { has_prompt: true, char_count: 64, preview: 'PM draft instructions.' }, draft: { scratch_only: true },
      warnings: ['Product Manager is draft/scratch-only in Wave 6B.'], external_connector_caveat: 'External connector caveat.',
      restrictions: ['Do not use for live PM dogfood.'],
    },
  ];
}

test('Agent Class manager renders class list, PM caveat, archived disabled preview, and explicit launch command', () => {
  const { context, document, panel, sendCalls } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  assert.deepEqual(sendCalls[0], { cmd: 'agent_class_list', base_dir: '/repo' });

  run(context, `agentClassManagerReceiveList({ type: 'agent_classes', classes: ${JSON.stringify(sampleClasses())}, issues: [] })`);
  assert.match(panel.innerHTML, /Default Worker/);
  assert.match(panel.innerHTML, /Review Worker/);
  assert.match(panel.innerHTML, /Old Worker/);
  assert.match(panel.innerHTML, /Product Manager/);

  run(context, `agentClassManagerSelect('product-manager')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[3])} })`);
  assert.match(panel.innerHTML, /Product Manager \(draft\)/);
  assert.match(panel.innerHTML, /draft/);
  assert.match(panel.innerHTML, /External connector caveat/);
  assert.match(panel.innerHTML, /Do not use for live PM dogfood/);

  run(context, `agentClassManagerSelect('old-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[2])} })`);
  assert.match(panel.innerHTML, /Archived\/disabled Agent Classes cannot launch/);

  run(context, `agentClassManagerSelect('review-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[1])} })`);
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

test('Agent Class authoring validates before save, shows validation issues, and archives/deletes custom classes', async () => {
  const { context, document, panel, sendCalls } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList({ type: 'agent_classes', classes: ${JSON.stringify(sampleClasses())}, issues: [] })`);
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

  run(context, `agentClassManagerReceiveValidation({ type: 'agent_class_validation', request_id: _agentClassValidationRequestId, valid: false, ok: false, errors: [{ severity: 'error', code: 'bad', message: 'Display name is unsafe' }], warnings: [], agent_class: null })`);
  assert.match(panel.innerHTML, /Display name is unsafe/);
  run(context, `agentClassManagerSave()`);
  assert.notEqual(sendCalls.at(-1).cmd, 'agent_class_create');

  run(context, `agentClassManagerReceiveValidation({ type: 'agent_class_validation', request_id: _agentClassValidationRequestId, valid: true, ok: true, errors: [], warnings: ['Review YAML before commit.'], normalized: { id: 'qa-worker' }, agent_class: { id: 'qa-worker', version: '1', base_kind: 'worker', display_name: 'QA Worker', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-worker', version: '1' }, prompt_summary: { has_prompt: true, char_count: 25, preview: 'Check state preservation.' }, external_connector_caveat: 'External connector caveat.', restrictions: [] } })`);
  run(context, `agentClassManagerSave()`);
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_create');
  assert.equal(sendCalls.at(-1).agent_class.agent_profile_ref.id, 'full-worker');

  run(context, `agentClassManagerReceiveMutation({ type: 'agent_class_save', ok: true, operation: 'created', agent_class: { id: 'qa-worker', version: '1', base_kind: 'worker', display_name: 'QA Worker', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-worker', version: '1' } }, classes: ${JSON.stringify(sampleClasses())} })`);
  run(context, `agentClassManagerArchive()`);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_archive');
  assert.equal(sendCalls.at(-1).class_id, 'qa-worker');

  run(context, `agentClassManagerDelete()`);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sendCalls.at(-1).cmd, 'agent_class_delete');
  assert.equal(sendCalls.at(-1).class_id, 'qa-worker');
});

test('Agent Class manager preserves focused draft, caret, and scroll across rerenders', () => {
  const { context, document, panel } = createHarness();
  registerClassForm(document);
  run(context, `librarySwitchTab('agent_classes')`);
  run(context, `agentClassManagerReceiveList({ type: 'agent_classes', classes: ${JSON.stringify(sampleClasses())}, issues: [] })`);
  run(context, `agentClassManagerSelect('review-worker')`);
  run(context, `agentClassManagerReceivePreview({ type: 'agent_class_preview', agent_class: ${JSON.stringify(sampleClasses()[1])} })`);

  const prompt = document.getElementById('agent-class-prompt');
  prompt.value = 'operator draft that must survive';
  prompt.selectionStart = 9;
  prompt.selectionEnd = 14;
  prompt.scrollTop = 33;
  prompt.focus();
  document.getElementById('agent-class-editor').scrollTop = 77;
  run(context, `_agentClassEditorDirty = true; renderAgentClassesPanel();`);

  assert.match(panel.innerHTML, /operator draft that must survive/);
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
  run(context, `agentClassManagerReceiveList({ type: 'agent_classes', classes: ${JSON.stringify(sampleClasses())}, issues: [] })`);

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

  document.getElementById('engineer-name-input').value = 'Builder';
  run(context, `openAddEngineerModal({ group: 'alpha' }); agentClassPickerSelect('add-engineer', ''); submitAddEngineer();`);
  assert.deepEqual(sendCalls.at(-1), { cmd: 'add_engineer', name: 'Builder', group: 'alpha' });

  document.getElementById('engineer-name-input').value = 'Builder PM';
  run(context, `agentClassPickerSelect('add-engineer', 'product-manager'); submitAddEngineer();`);
  assert.equal(sendCalls.at(-1).cmd, 'add_engineer');
  assert.equal(sendCalls.at(-1).agent_class_id, 'product-manager');

  document.getElementById('architect-name-input').value = 'Planner';
  run(context, `openAddArchitectModal({ group: 'alpha' }); agentClassPickerSelect('add-architect', 'product-manager'); submitAddArchitect();`);
  assert.equal(sendCalls.at(-1).cmd, 'add_architect');
  assert.equal(sendCalls.at(-1).agent_class_id, 'product-manager');
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
  run(context, `agentClassManagerReceiveList({ type: 'agent_classes', classes: ${JSON.stringify(sampleClasses().concat([{ id: 'team-engineer', version: '1', base_kind: 'engineer', display_name: 'Team Engineer', custom: true, source: 'project', lifecycle: 'stable', status: 'full', launchable: true, agent_profile_ref: { id: 'full-engineer', version: '1' } }]))}, issues: [] })`);
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

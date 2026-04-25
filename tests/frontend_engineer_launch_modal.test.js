const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() {
    this._set = new Set();
  }
  add(...names) { names.forEach((name) => this._set.add(name)); }
  remove(...names) { names.forEach((name) => this._set.delete(name)); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    if (force === undefined) {
      if (this._set.has(name)) {
        this._set.delete(name);
        return false;
      }
      this._set.add(name);
      return true;
    }
    if (force) this._set.add(name);
    else this._set.delete(name);
    return !!force;
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.checked = false;
    this.innerHTML = '';
    this.textContent = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.focused = false;
    this.classList = new FakeClassList();
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  focus() {
    this.focused = true;
  }
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
  closest() {
    return this;
  }
}

function createSandbox() {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }

  const sandbox = {
    console,
    state: {
      agents: {
        'engineer-1': { id: 'engineer-1', group: 'alpha', status: 'stopped' },
      },
      engineer_settings: {
        alpha: {
          engineer_provider: 'codex',
          engineer_boot_command: 'codex --model gpt-5.4',
          engineer_model: 'gpt-5.4',
          engineer_reasoning_effort: 'medium',
          custom_instructions: 'Keep waves tight.',
          autonomy_mode: 'aggressive_auto_continue',
          default_worker_concurrency: 4,
          wave_size_preference: 'large',
          same_agent_follow_up_preference: 'prefer_same_agent',
          digest_verbosity: 'detailed',
          escalation_style: 'keep_moving',
          push_interval: 30,
          max_interval: 120,
          heartbeat_interval: 60,
          enabled_events: [
            'agent_started',
            'task_dispatched',
            'task_derived',
            'agent_progress',
            'task_health_alert',
          ],
        },
      },
    },
    _cachedProviders: [
      {
        name: 'codex',
        display_name: 'Codex',
        command: 'codex',
        reasoning_efforts: ['low', 'medium', 'high'],
      },
    ],
    sendCalls: [],
    document: {
      body: new FakeElement('body'),
      activeElement: null,
      getElementById(id) {
        return ensure(id);
      },
      createElement(tag) {
        return new FakeElement(tag);
      },
      querySelectorAll(selector) {
        if (selector === '.overlay') return [ensure('modal-engineer-launch')];
        if (selector === '.hint-pop') return [];
        return [];
      },
      querySelector() {
        return null;
      },
    },
    window: { innerWidth: 1200 },
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, ensure };
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

test('openEngineerLaunchDialog populates persisted engineer launch settings', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);

  assert.equal(ensure('engineer-launch-title').textContent, 'Create Engineer');
  assert.equal(ensure('engineer-launch-group').textContent, 'alpha');
  assert.equal(ensure('engineer-launch-submit-btn').textContent, 'Create Engineer');
  assert.equal(ensure('engineer-launch-provider').value, 'codex');
  assert.equal(ensure('engineer-launch-boot-cmd').value, 'codex --model gpt-5.4');
  assert.equal(ensure('engineer-launch-model').value, 'gpt-5.4');
  assert.equal(ensure('engineer-launch-reasoning-effort').value, 'medium');
  assert.equal(ensure('engineer-launch-custom-instructions').value, 'Keep waves tight.');
  assert.equal(ensure('engineer-launch-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('engineer-launch-default-worker-concurrency').value, '4');
  assert.equal(ensure('engineer-launch-wave-size-preference').value, 'large');
  assert.equal(ensure('engineer-launch-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('engineer-launch-notification-preset').value, 'noisy');
  assert.equal(ensure('engineer-launch-digest-verbosity').value, 'detailed');
  assert.equal(ensure('engineer-launch-escalation-style').value, 'keep_moving');
  assert.equal(ensure('modal-engineer-launch').classList.contains('visible'), true);
});

test('submitEngineerLaunchDialog persists settings then creates a Engineer', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);
  ensure('engineer-launch-provider').value = 'codex';
  ensure('engineer-launch-boot-cmd').value = 'codex --model gpt-5.5';
  ensure('engineer-launch-model').value = 'gpt-5.5';
  ensure('engineer-launch-reasoning-effort').value = 'high';
  ensure('engineer-launch-custom-instructions').value = 'Watch deploy risk.';
  ensure('engineer-launch-autonomy-mode').value = 'suggest_only';
  ensure('engineer-launch-default-worker-concurrency').value = '3';
  ensure('engineer-launch-wave-size-preference').value = 'small';
  ensure('engineer-launch-same-agent-follow-up-preference').value = 'prefer_fresh_agent';
  ensure('engineer-launch-notification-preset').value = 'quiet';
  vm.runInContext('onEngineerLaunchNotificationPresetChange()', context);
  ensure('engineer-launch-escalation-style').value = 'ask_early';

  vm.runInContext('submitEngineerLaunchDialog()', context);

  const callsWithoutSpecFetch = sandbox.sendCalls.filter(
    (msg) => msg.cmd !== 'list_specializations',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(callsWithoutSpecFetch)), [
    {
      cmd: 'engineer_update_settings',
      group: 'alpha',
      engineer_provider: 'codex',
      engineer_boot_command: 'codex --model gpt-5.5',
      engineer_model: 'gpt-5.5',
      engineer_reasoning_effort: 'high',
      custom_instructions: 'Watch deploy risk.',
      autonomy_mode: 'suggest_only',
      default_worker_concurrency: 3,
      wave_size_preference: 'small',
      same_agent_follow_up_preference: 'prefer_fresh_agent',
      digest_verbosity: 'compact',
      escalation_style: 'ask_early',
      push_interval: 120,
      max_interval: 600,
      heartbeat_interval: 0,
      enabled_events: ['task_derived', 'task_health_alert'],
    },
    {
      cmd: 'add_agent',
      name: 'Engineer',
      group: 'alpha',
      is_engineer: true,
      specializations: [],
    },
  ]);
});

test('openEngineerLaunchDialog fetches specializations and renders them', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);

  const specFetch = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'list_specializations',
  );
  assert.ok(specFetch, 'list_specializations should be requested');
  assert.equal(specFetch.group, 'alpha');
});

test('specializations picker reorders and submits ordered list on save', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.specializations = [
    { name: 'ui-ux', preamble: 'UX.', priorities: [] },
    { name: 'security', preamble: 'Sec.', priorities: [] },
  ];
  sandbox.state.agents['engineer-1'].engineer_specializations = ['ui-ux'];
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha', 'engineer-1')`, context);

  ensure('engineer-launch-specializations-available').value = 'security';
  vm.runInContext('engineerLaunchAddSpecialization()', context);
  vm.runInContext('engineerLaunchMoveSpecialization(1, -1)', context);
  vm.runInContext('submitEngineerLaunchDialog()', context);

  const setCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'set_engineer_specializations',
  );
  assert.ok(setCall, 'set_engineer_specializations should be sent');
  assert.deepEqual(setCall.specializations, ['security', 'ui-ux']);
  assert.equal(setCall.engineer_id, 'engineer-1');
});

test('submitEngineerLaunchDialog persists settings then relaunches the designated Engineer', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha', 'engineer-1')`, context);
  ensure('engineer-launch-provider').value = 'codex';

  vm.runInContext('submitEngineerLaunchDialog()', context);

  assert.equal(ensure('engineer-launch-title').textContent, 'Relaunch Engineer');
  const callsWithoutSpecFetch2 = sandbox.sendCalls.filter(
    (msg) => msg.cmd !== 'list_specializations',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(callsWithoutSpecFetch2)), [
    {
      cmd: 'engineer_update_settings',
      group: 'alpha',
      engineer_provider: 'codex',
      engineer_boot_command: 'codex --model gpt-5.4',
      engineer_model: 'gpt-5.4',
      engineer_reasoning_effort: 'medium',
      custom_instructions: 'Keep waves tight.',
      autonomy_mode: 'aggressive_auto_continue',
      default_worker_concurrency: 4,
      wave_size_preference: 'large',
      same_agent_follow_up_preference: 'prefer_same_agent',
      digest_verbosity: 'detailed',
      escalation_style: 'keep_moving',
      push_interval: 30,
      max_interval: 120,
      heartbeat_interval: 60,
      enabled_events: [
        'agent_started',
        'task_dispatched',
        'task_derived',
        'agent_progress',
        'task_health_alert',
      ],
    },
    {
      cmd: 'relaunch_agent',
      id: 'engineer-1',
    },
  ]);
});

test('create-mode previews group default specializations from group_settings', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.group_settings = {
    alpha: { default_engineer_specializations: ['ui-frontend', 'react'] },
  };
  sandbox.state.specializations = [
    { name: 'ui-frontend', preamble: 'UI', priorities: [] },
    { name: 'react', preamble: 'React', priorities: [] },
    { name: 'rust', preamble: 'Rust', priorities: [] },
  ];
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);
  // Submit without touching the picker — JS should send the previewed
  // group default verbatim, not an empty list.
  vm.runInContext('submitEngineerLaunchDialog()', context);

  const addCall = sandbox.sendCalls.find((msg) => msg.cmd === 'add_agent');
  assert.ok(addCall, 'add_agent should be sent');
  assert.deepEqual(addCall.specializations, ['ui-frontend', 'react']);
});

test('create-mode explicit empty pick is preserved (not refilled by group default)', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.group_settings = {
    alpha: { default_engineer_specializations: ['ui-frontend'] },
  };
  sandbox.state.specializations = [
    { name: 'ui-frontend', preamble: 'UI', priorities: [] },
  ];
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);
  // User clears the picker.
  vm.runInContext('engineerLaunchRemoveSpecialization(0)', context);
  vm.runInContext('submitEngineerLaunchDialog()', context);

  const addCall = sandbox.sendCalls.find((msg) => msg.cmd === 'add_agent');
  assert.ok(addCall, 'add_agent should be sent');
  assert.deepEqual(addCall.specializations, []);
});

test('relaunch-mode Reset to group default repopulates from group settings', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.group_settings = {
    alpha: { default_engineer_specializations: ['ui-frontend', 'react'] },
  };
  sandbox.state.specializations = [
    { name: 'ui-frontend', preamble: 'UI', priorities: [] },
    { name: 'react', preamble: 'React', priorities: [] },
    { name: 'rust', preamble: 'Rust', priorities: [] },
  ];
  sandbox.state.agents['engineer-1'].engineer_specializations = ['rust'];
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha', 'engineer-1')`, context);
  vm.runInContext('engineerLaunchResetSpecializationsToGroupDefault()', context);
  vm.runInContext('submitEngineerLaunchDialog()', context);

  const setCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'set_engineer_specializations',
  );
  assert.ok(setCall, 'set_engineer_specializations should be sent');
  assert.deepEqual(setCall.specializations, ['ui-frontend', 'react']);
});

test('Reset button is disabled when current selection equals group default', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.group_settings = {
    alpha: { default_engineer_specializations: ['ui-frontend', 'react'] },
  };
  sandbox.state.specializations = [
    { name: 'ui-frontend', preamble: 'UI', priorities: [] },
    { name: 'react', preamble: 'React', priorities: [] },
  ];
  sandbox.state.agents['engineer-1'].engineer_specializations = [
    'ui-frontend', 'react'
  ];
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha', 'engineer-1')`, context);

  assert.equal(
    ensure('engineer-launch-specializations-reset').disabled,
    true,
    'Reset button should be disabled when current matches group default',
  );

  // Now diverge from the default — Reset becomes enabled.
  vm.runInContext('engineerLaunchRemoveSpecialization(0)', context);
  assert.equal(
    ensure('engineer-launch-specializations-reset').disabled,
    false,
    'Reset button should be enabled when current diverges from group default',
  );
});

test('nested-modal: closeModals pops new-specialization without dismissing parent', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/engineer-launch.js');

  vm.runInContext(`openEngineerLaunchDialog('alpha')`, context);
  // Parent modal is now visible.
  assert.equal(
    ensure('modal-engineer-launch').classList.contains('visible'),
    true,
    'parent should be visible',
  );

  vm.runInContext('openNewSpecializationDialog()', context);
  assert.equal(
    ensure('modal-new-specialization').classList.contains('visible'),
    true,
    'nested should be visible',
  );

  // First closeModals() pops the nested overlay only.
  vm.runInContext('closeModals()', context);
  assert.equal(
    ensure('modal-new-specialization').classList.contains('visible'),
    false,
    'nested should be closed',
  );
  assert.equal(
    ensure('modal-engineer-launch').classList.contains('visible'),
    true,
    'parent must remain visible after popping nested',
  );

  // Second closeModals() falls through to the close-all path.
  vm.runInContext('closeModals()', context);
  assert.equal(
    ensure('modal-engineer-launch').classList.contains('visible'),
    false,
    'parent should close on second invocation',
  );
});

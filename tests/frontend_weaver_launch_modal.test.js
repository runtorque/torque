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
        'weaver-1': { id: 'weaver-1', group: 'alpha', status: 'stopped' },
      },
      weaver_settings: {
        alpha: {
          weaver_provider: 'codex',
          weaver_boot_command: 'codex --model gpt-5.4',
          weaver_model: 'gpt-5.4',
          weaver_reasoning_effort: 'medium',
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
        if (selector === '.overlay') return [ensure('modal-weaver-launch')];
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

test('openWeaverLaunchDialog populates persisted weaver launch settings', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/weaver-launch.js');

  vm.runInContext(`openWeaverLaunchDialog('alpha')`, context);

  assert.equal(ensure('weaver-launch-title').textContent, 'Create Weaver');
  assert.equal(ensure('weaver-launch-group').textContent, 'alpha');
  assert.equal(ensure('weaver-launch-submit-btn').textContent, 'Create Weaver');
  assert.equal(ensure('weaver-launch-provider').value, 'codex');
  assert.equal(ensure('weaver-launch-boot-cmd').value, 'codex --model gpt-5.4');
  assert.equal(ensure('weaver-launch-model').value, 'gpt-5.4');
  assert.equal(ensure('weaver-launch-reasoning-effort').value, 'medium');
  assert.equal(ensure('weaver-launch-custom-instructions').value, 'Keep waves tight.');
  assert.equal(ensure('weaver-launch-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('weaver-launch-default-worker-concurrency').value, '4');
  assert.equal(ensure('weaver-launch-wave-size-preference').value, 'large');
  assert.equal(ensure('weaver-launch-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('weaver-launch-notification-preset').value, 'noisy');
  assert.equal(ensure('weaver-launch-digest-verbosity').value, 'detailed');
  assert.equal(ensure('weaver-launch-escalation-style').value, 'keep_moving');
  assert.equal(ensure('modal-weaver-launch').classList.contains('visible'), true);
});

test('submitWeaverLaunchDialog persists settings then creates a Weaver', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/weaver-launch.js');

  vm.runInContext(`openWeaverLaunchDialog('alpha')`, context);
  ensure('weaver-launch-provider').value = 'codex';
  ensure('weaver-launch-boot-cmd').value = 'codex --model gpt-5.5';
  ensure('weaver-launch-model').value = 'gpt-5.5';
  ensure('weaver-launch-reasoning-effort').value = 'high';
  ensure('weaver-launch-custom-instructions').value = 'Watch deploy risk.';
  ensure('weaver-launch-autonomy-mode').value = 'suggest_only';
  ensure('weaver-launch-default-worker-concurrency').value = '3';
  ensure('weaver-launch-wave-size-preference').value = 'small';
  ensure('weaver-launch-same-agent-follow-up-preference').value = 'prefer_fresh_agent';
  ensure('weaver-launch-notification-preset').value = 'quiet';
  vm.runInContext('onWeaverLaunchNotificationPresetChange()', context);
  ensure('weaver-launch-escalation-style').value = 'ask_early';

  vm.runInContext('submitWeaverLaunchDialog()', context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'weaver_update_settings',
      group: 'alpha',
      weaver_provider: 'codex',
      weaver_boot_command: 'codex --model gpt-5.5',
      weaver_model: 'gpt-5.5',
      weaver_reasoning_effort: 'high',
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
      name: 'Weaver',
      group: 'alpha',
      is_weaver: true,
    },
  ]);
});

test('submitWeaverLaunchDialog persists settings then relaunches the designated Weaver', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/weaver-launch.js');

  vm.runInContext(`openWeaverLaunchDialog('alpha', 'weaver-1')`, context);
  ensure('weaver-launch-provider').value = 'codex';

  vm.runInContext('submitWeaverLaunchDialog()', context);

  assert.equal(ensure('weaver-launch-title').textContent, 'Relaunch Weaver');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'weaver_update_settings',
      group: 'alpha',
      weaver_provider: 'codex',
      weaver_boot_command: 'codex --model gpt-5.4',
      weaver_model: 'gpt-5.4',
      weaver_reasoning_effort: 'medium',
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
      id: 'weaver-1',
    },
  ]);
});

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
    this.open = false;
    this._innerHTML = '';
    this.textContent = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.focused = false;
    this.classList = new FakeClassList();
    this._firstSubtab = null;
    this._label = null;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(value) {
    this._innerHTML = value;
    if (value === '') this.children = [];
  }
  focus() {
    this.focused = true;
  }
  querySelector(selector) {
    if (selector === '.gs-subtab') return this._firstSubtab;
    if (selector === 'label') {
      if (!this._label) this._label = new FakeElement();
      return this._label;
    }
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

  const gsTabs = ['group', 'agents', 'engineer'].map((name) => {
    const el = new FakeElement(`tab-${name}`);
    el.dataset.tab = name;
    return el;
  });
  const gsPanes = ['group', 'agents', 'engineer'].map((name) => {
    const el = new FakeElement(`pane-${name}`);
    el.dataset.pane = name;
    return el;
  });
  const paneByName = Object.fromEntries(gsPanes.map((pane) => [pane.dataset.pane, pane]));
  const agentSubtabs = ['agent-general', 'agent-terminals', 'agent-worktree', 'agent-notifications'].map((name) => {
    const el = new FakeElement(`subtab-${name}`);
    el.dataset.subtab = name;
    return el;
  });
  paneByName.agents._firstSubtab = agentSubtabs[0];

  const sandbox = {
    console,
    TAB_COLORS: [],
    state: {
      agents: {
        'engineer-1': { id: 'engineer-1', name: 'Engineer', status: 'running' },
      },
    },
    _cachedAgentTemplates: [],
    _cachedProviders: [
      {
        name: 'codex',
        display_name: 'Codex',
        command: 'codex',
        reasoning_efforts: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
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
        if (selector === '.gs-tab') return gsTabs;
        if (selector === '.gs-pane') return gsPanes;
        if (selector === '.overlay' || selector === '.hint-pop') return [];
        if (selector === '#gs-color-swatches .swatch'
          || selector === '#gs-agent-color-swatches .swatch'
          || selector === '#gs-terminal-color-swatches .swatch'
          || selector === '#gs-engineer-color-swatches .swatch') return [];
        return [];
      },
      querySelector(selector) {
        const paneMatch = selector.match(/^\.gs-pane\[data-pane="([^"]+)"\]$/);
        if (paneMatch) return paneByName[paneMatch[1]] || null;
        const subtabMatch = selector.match(/^\.gs-pane\[data-pane="([^"]+)"\] \.gs-subtab\[data-subtab="([^"]+)"\]$/);
        if (subtabMatch && subtabMatch[1] === 'agents') {
          return agentSubtabs.find((el) => el.dataset.subtab === subtabMatch[2]) || null;
        }
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

function loadModals(context) {
  const filename = path.join(repoRoot, 'static/js/modals.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function seedProviders(context, providers) {
  vm.runInContext(`_cachedProviders = ${JSON.stringify(providers)};`, context);
}

test('group settings modal populates engineer fields and honors engineer tab deep-link', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_gsInitialTab = "engineer"', context);
  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      engineer_agent_id: "engineer-1",
      agent_provider: "codex",
      agent_model: "gpt-5.1",
      agent_reasoning_effort: "minimal",
      worktree_merge_cleanup: "close_remove",
      worktree_merge_preserve_diff: true
    },
    engineer_settings: {
      engineer_provider: "codex",
      engineer_boot_command: "codex --model gpt-5",
      engineer_model: "gpt-5.1-codex",
      engineer_reasoning_effort: "xhigh",
      engineer_directory: "/repo/.loom/engineer",
      engineer_profile: "Ops",
      engineer_shell: "fish",
      engineer_tab_color: "none",
      custom_instructions: "Watch for regressions.",
      restrict_to_created_agents: true,
      autonomy_mode: "aggressive_auto_continue",
      default_worker_concurrency: 4,
      wave_size_preference: "large",
      same_agent_follow_up_preference: "prefer_same_agent",
      digest_verbosity: "detailed",
      escalation_style: "keep_moving",
      push_interval: 120,
      max_interval: 600,
      heartbeat_interval: 60,
      enabled_events: ["agent_started", "agent_progress"]
    },
    profiles: ["Default", "Ops"]
  })`, context);

  assert.equal(ensure('gs-engineer-provider').value, 'codex');
  assert.equal(ensure('gs-engineer-boot-cmd').value, 'codex --model gpt-5');
  assert.equal(ensure('gs-agent-model').value, 'gpt-5.1');
  assert.equal(ensure('gs-agent-reasoning-effort').value, 'minimal');
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
  );
  assert.equal(ensure('gs-engineer-model').value, 'gpt-5.1-codex');
  assert.equal(ensure('gs-engineer-reasoning-effort').value, 'xhigh');
  assert.equal(ensure('gs-engineer-directory').value, '/repo/.loom/engineer');
  assert.equal(ensure('gs-engineer-profile').value, 'Ops');
  assert.equal(ensure('gs-engineer-shell').value, 'fish');
  assert.deepEqual(
    ensure('gs-engineer-reasoning-effort').children.map((child) => child.value),
    ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
  );
  assert.equal(ensure('gs-engineer-custom-instructions').value, 'Watch for regressions.');
  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, true);
  assert.equal(ensure('gs-engineer-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('gs-engineer-default-worker-concurrency').value, '4');
  assert.equal(ensure('gs-engineer-wave-size-preference').value, 'large');
  assert.equal(ensure('gs-engineer-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('gs-engineer-notification-preset').value, 'custom');
  assert.equal(ensure('gs-engineer-digest-verbosity').value, 'detailed');
  assert.equal(ensure('gs-engineer-escalation-style').value, 'keep_moving');
  assert.equal(ensure('gs-wt-merge-cleanup').value, 'close_remove');
  assert.equal(ensure('gs-wt-merge-preserve-diff').checked, true);
  assert.equal(ensure('gs-engineer-agent-name').textContent, 'Engineer');
  assert.equal(ensure('gs-engineer-provider-section').open, true);
  assert.equal(ensure('gs-engineer-autonomy-section').open, true);
  assert.equal(ensure('gs-engineer-digest-section').open, false);
  assert.equal(ensure('gs-engineer-event-agent-started').checked, true);
  assert.equal(ensure('gs-engineer-event-agent-progress').checked, true);
  assert.equal(ensure('gs-engineer-provider').focused, true);
  assert.equal(ensure('modal-group-settings').classList.contains('visible'), true);
});

test('group settings resets the Engineer section defaults when reopened', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  ensure('gs-engineer-provider-section').open = false;
  ensure('gs-engineer-autonomy-section').open = false;
  ensure('gs-engineer-digest-section').open = true;

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-engineer-provider-section').open, true);
  assert.equal(ensure('gs-engineer-autonomy-section').open, true);
  assert.equal(ensure('gs-engineer-digest-section').open, false);
  assert.equal(ensure('gs-engineer-notification-preset').value, 'normal');
});

test('submitGroupSettings sends group and engineer updates separately', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha"; _gsWtSymlinks = ["etl/**/node_modules"];', context);
  ensure('gs-directory').value = '/repo';
  ensure('gs-agent-directory').value = '/repo/agents';
  ensure('gs-terminal-prefix').value = 'Shell';
  ensure('gs-terminal-boot-cmd').value = 'npm run dev';
  ensure('gs-wt-merge-cleanup').value = 'remove';
  ensure('gs-wt-merge-preserve-diff').checked = true;
  ensure('gs-engineer-provider').value = 'codex';
  ensure('gs-engineer-boot-cmd').value = 'codex --model gpt-5';
  ensure('gs-agent-model').value = 'gpt-5';
  ensure('gs-agent-reasoning-effort').value = 'minimal';
  ensure('gs-engineer-model').value = 'gpt-5.1';
  ensure('gs-engineer-reasoning-effort').value = 'xhigh';
  ensure('gs-engineer-directory').value = '/repo/.loom/engineer';
  ensure('gs-engineer-profile').value = 'Ops';
  ensure('gs-engineer-shell').value = 'fish';
  vm.runInContext(`_gsEngineerColor = 'none';`, context);
  ensure('gs-engineer-custom-instructions').value = 'Stay focused';
  ensure('gs-engineer-restrict-to-created-agents').checked = true;
  ensure('gs-engineer-autonomy-mode').value = 'suggest_only';
  ensure('gs-engineer-default-worker-concurrency').value = '3';
  ensure('gs-engineer-wave-size-preference').value = 'small';
  ensure('gs-engineer-same-agent-follow-up-preference').value = 'prefer_fresh_agent';
  ensure('gs-engineer-digest-verbosity').value = 'compact';
  ensure('gs-engineer-escalation-style').value = 'ask_early';
  ensure('gs-engineer-push-interval').value = '120';
  ensure('gs-engineer-max-interval').value = '600';
  ensure('gs-engineer-heartbeat-interval').value = '60';
  ensure('gs-engineer-event-agent-started').checked = true;
  ensure('gs-engineer-event-agent-progress').checked = true;

  vm.runInContext('submitGroupSettings()', context);

  assert.equal(sandbox.sendCalls.length, 2);
  assert.equal(sandbox.sendCalls[0].cmd, 'update_group_settings');
  assert.equal(sandbox.sendCalls[0].group, 'alpha');
  assert.equal(sandbox.sendCalls[0].settings.terminal_name_prefix, 'Shell');
  assert.equal(sandbox.sendCalls[0].settings.terminal_boot_command, 'npm run dev');
  assert.equal(sandbox.sendCalls[0].settings.worktree_merge_cleanup, 'remove');
  assert.equal(sandbox.sendCalls[0].settings.worktree_merge_preserve_diff, true);
  assert.equal(sandbox.sendCalls[0].settings.agent_model, 'gpt-5');
  assert.equal(sandbox.sendCalls[0].settings.agent_reasoning_effort, 'minimal');
  assert.deepEqual(
    JSON.parse(JSON.stringify(sandbox.sendCalls[0].settings.worktree_symlinks)),
    ['etl/**/node_modules'],
  );
  assert.equal(sandbox.sendCalls[1].cmd, 'engineer_update_settings');
  assert.equal(sandbox.sendCalls[1].group, 'alpha');
  assert.equal(sandbox.sendCalls[1].engineer_provider, 'codex');
  assert.equal(sandbox.sendCalls[1].engineer_boot_command, 'codex --model gpt-5');
  assert.equal(sandbox.sendCalls[1].engineer_model, 'gpt-5.1');
  assert.equal(sandbox.sendCalls[1].engineer_reasoning_effort, 'xhigh');
  assert.equal(sandbox.sendCalls[1].engineer_directory, '/repo/.loom/engineer');
  assert.equal(sandbox.sendCalls[1].engineer_profile, 'Ops');
  assert.equal(sandbox.sendCalls[1].engineer_shell, 'fish');
  assert.equal(sandbox.sendCalls[1].engineer_tab_color, 'none');
  assert.equal(sandbox.sendCalls[1].custom_instructions, 'Stay focused');
  assert.equal(sandbox.sendCalls[1].restrict_to_created_agents, true);
  assert.equal(sandbox.sendCalls[1].autonomy_mode, 'suggest_only');
  assert.equal(sandbox.sendCalls[1].default_worker_concurrency, 3);
  assert.equal(sandbox.sendCalls[1].wave_size_preference, 'small');
  assert.equal(sandbox.sendCalls[1].same_agent_follow_up_preference, 'prefer_fresh_agent');
  assert.equal(sandbox.sendCalls[1].digest_verbosity, 'compact');
  assert.equal(sandbox.sendCalls[1].escalation_style, 'ask_early');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[1].enabled_events)), ['agent_started', 'agent_progress']);
});

test('group settings notification presets rewrite detailed controls before submit', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('gs-engineer-notification-preset').value = 'quiet';

  vm.runInContext('onGsEngineerNotificationPresetChange(); submitGroupSettings()', context);

  assert.equal(sandbox.sendCalls[1].cmd, 'engineer_update_settings');
  assert.equal(sandbox.sendCalls[1].digest_verbosity, 'compact');
  assert.equal(sandbox.sendCalls[1].push_interval, 120);
  assert.equal(sandbox.sendCalls[1].max_interval, 600);
  assert.equal(sandbox.sendCalls[1].heartbeat_interval, 0);
  assert.deepEqual(
    JSON.parse(JSON.stringify(sandbox.sendCalls[1].enabled_events)),
    ['task_derived', 'task_health_alert'],
  );
  assert.equal(ensure('gs-engineer-notification-preset').value, 'quiet');
});

test('group settings describes absent Engineer state without legacy creation copy', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-engineer-agent-name').textContent, 'No engineer agent');
  assert.equal(ensure('gs-engineer-agent-meta').textContent, 'No designated Engineer is configured for this group.');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), []);
});

test('_addWtSymlink trims outer slashes while preserving glob syntax', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  ensure('gs-wt-symlink-input').value = '/etl/**/node_modules/';
  vm.runInContext('_gsWtSymlinks = []; _addWtSymlink()', context);

  assert.deepEqual(
    JSON.parse(vm.runInContext('JSON.stringify(_gsWtSymlinks)', context)),
    ['etl/**/node_modules'],
  );
  assert.equal(ensure('gs-wt-symlink-input').value, '');
});

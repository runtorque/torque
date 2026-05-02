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
    this.scrollTop = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.classList = new FakeClassList();
    this._firstSubtab = null;
    this._subtabs = [];
    this._subpanes = [];
    this._closestPane = null;
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
  querySelectorAll(selector) {
    if (selector === '.gs-subtab') return this._subtabs || [];
    if (selector === '.gs-subpane') return this._subpanes || [];
    return [];
  }
  closest(selector) {
    if (selector === '.gs-pane' && this._closestPane) return this._closestPane;
    return this;
  }
}

function createSandbox() {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }

  const gsTabs = ['group', 'agents', 'engineer', 'architect'].map((name) => {
    const el = new FakeElement(`tab-${name}`);
    el.dataset.tab = name;
    return el;
  });
  const gsPanes = ['group', 'agents', 'engineer', 'architect'].map((name) => {
    const el = new FakeElement(`pane-${name}`);
    el.dataset.pane = name;
    return el;
  });
  const paneByName = Object.fromEntries(gsPanes.map((pane) => [pane.dataset.pane, pane]));
  const subtabNamesByPane = {
    group: ['group-general', 'group-advanced'],
    agents: ['agent-general', 'agent-terminals', 'agent-worktree', 'agent-notifications'],
    engineer: ['engineer-general', 'engineer-behavior', 'engineer-digests'],
    architect: ['architect-general', 'architect-behavior', 'architect-digests'],
  };
  const gsSubtabs = {};
  const gsSubpanes = {};
  for (const [paneName, names] of Object.entries(subtabNamesByPane)) {
    const pane = paneByName[paneName];
    gsSubtabs[paneName] = names.map((name, idx) => {
      const el = new FakeElement(`subtab-${name}`);
      el.dataset.subtab = name;
      el._closestPane = pane;
      if (idx === 0) el.classList.add('active');
      return el;
    });
    gsSubpanes[paneName] = names.map((name, idx) => {
      const el = new FakeElement(`subpane-${name}`);
      el.dataset.subpane = name;
      if (idx === 0) el.classList.add('active');
      return el;
    });
    pane._firstSubtab = gsSubtabs[paneName][0];
    pane._subtabs = gsSubtabs[paneName];
    pane._subpanes = gsSubpanes[paneName];
  }

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
        if (subtabMatch) {
          return (gsSubtabs[subtabMatch[1]] || []).find((el) => el.dataset.subtab === subtabMatch[2]) || null;
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

function loadEngineerLaunchModals(context) {
  loadModals(context);
  const filename = path.join(repoRoot, 'static/js/modals/engineer-launch.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function seedProviders(context, providers) {
  vm.runInContext(`_cachedProviders = ${JSON.stringify(providers)};`, context);
}

test('group settings refreshes reasoning effort options when provider changes', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, [
    {
      name: 'codex',
      display_name: 'Codex',
      command: 'codex',
      reasoning_efforts: ['low', 'medium', 'high', 'xhigh'],
    },
    {
      name: 'claude-code',
      display_name: 'Claude Code',
      command: 'claude',
      reasoning_efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    },
    {
      name: 'gemini-cli',
      display_name: 'Gemini CLI',
      command: 'gemini',
      reasoning_efforts: [],
    },
  ]);

  ensure('gs-agent-provider').value = 'codex';
  vm.runInContext('onGsProviderChange()', context);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', 'xhigh'],
  );

  ensure('gs-agent-provider').value = 'claude-code';
  vm.runInContext('onGsProviderChange()', context);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', 'xhigh', 'max'],
  );

  ensure('gs-agent-provider').value = 'gemini-cli';
  vm.runInContext('onGsProviderChange()', context);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    [''],
  );
  assert.equal(
    ensure('gs-agent-reasoning-effort').children[0].textContent,
    'Not supported for this provider',
  );
});

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
      engineer_can_override_worker_provider: false,
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
    architect_settings: {
      architect_provider: "codex",
      architect_boot_command: "codex --architect",
      architect_model: "gpt-5.1-architect",
      architect_reasoning_effort: "high",
      architect_custom_instructions: "Own scope crisply.",
      architect_autonomy_mode: "ask_always",
      architect_paused: true,
      architect_digest_verbosity: "verbose",
      architect_journal_checkpoint_frequency: "every_15_actions",
      architect_review_gate_thresholds: {
        ship_direct_max: 25,
        review_default_above: 90,
        self_review_bypass_allowed: true
      }
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
  assert.equal(ensure('gs-engineer-can-override-worker-provider').checked, false);
  assert.equal(ensure('gs-engineer-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('gs-engineer-default-worker-concurrency').value, '4');
  assert.equal(ensure('gs-engineer-wave-size-preference').value, 'large');
  assert.equal(ensure('gs-engineer-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('gs-engineer-notification-preset').value, 'custom');
  assert.equal(ensure('gs-engineer-digest-verbosity').value, 'detailed');
  assert.equal(ensure('gs-engineer-escalation-style').value, 'keep_moving');
  assert.equal(ensure('gs-wt-merge-cleanup').value, 'close_remove');
  assert.equal(ensure('gs-wt-merge-preserve-diff').checked, true);
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-general"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-digests"]').classList.contains('active'),
    false,
  );
  assert.equal(ensure('gs-engineer-event-agent-started').checked, true);
  assert.equal(ensure('gs-engineer-event-agent-progress').checked, true);
  assert.equal(ensure('gs-architect-provider').value, 'codex');
  assert.equal(ensure('gs-architect-boot-cmd').value, 'codex --architect');
  assert.equal(ensure('gs-architect-model').value, 'gpt-5.1-architect');
  assert.equal(ensure('gs-architect-reasoning-effort').value, 'high');
  assert.equal(ensure('gs-architect-custom-instructions').value, 'Own scope crisply.');
  assert.equal(ensure('gs-architect-autonomy-mode').value, 'ask_always');
  assert.equal(ensure('gs-architect-paused').checked, true);
  assert.equal(ensure('gs-architect-digest-verbosity').value, 'verbose');
  assert.equal(ensure('gs-architect-journal-checkpoint').value, 'every_15_actions');
  assert.equal(ensure('gs-engineer-provider').focused, true);
  assert.equal(ensure('modal-group-settings').classList.contains('visible'), true);
});

test('group settings resets Engineer and Architect sub-tabs to General when reopened', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext(`
    switchGsSubTab('engineer', document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-digests"]'));
    switchGsSubTab('architect', document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-digests"]'));
  `, context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-general"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-digests"]').classList.contains('active'),
    false,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-general"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-digests"]').classList.contains('active'),
    false,
  );
  assert.equal(ensure('gs-engineer-notification-preset').value, 'normal');
  assert.equal(ensure('gs-engineer-can-override-worker-provider').checked, true);
});

test('engineer and architect group settings use three scoped sub-tabs', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const topStrip = html.slice(
    html.indexOf('<div class="gs-tabs">'),
    html.indexOf('    <!-- Group tab -->'),
  );
  const topTabs = Array.from(topStrip.matchAll(/data-tab="([^"]+)"/g), (match) => match[1]);
  assert.deepEqual(topTabs, ['group', 'agents', 'engineer', 'architect']);

  assert.match(
    html,
    /<div class="gs-pane active" data-pane="group">[\s\S]*?data-subtab="group-general"[\s\S]*?data-subtab="group-advanced"/,
  );
  assert.match(html, /data-subpane="group-general"/);
  assert.match(html, /data-subpane="group-advanced"/);

  for (const pane of ['engineer', 'architect']) {
    assert.match(
      html,
      new RegExp(`<div class="gs-pane" data-pane="${pane}">[\\s\\S]*?data-subtab="${pane}-general"[\\s\\S]*?data-subtab="${pane}-behavior"[\\s\\S]*?data-subtab="${pane}-digests"`),
    );
    assert.match(html, new RegExp(`data-subpane="${pane}-general"`));
    assert.match(html, new RegExp(`data-subpane="${pane}-behavior"`));
    assert.match(html, new RegExp(`data-subpane="${pane}-digests"`));
    assert.doesNotMatch(
      html,
      new RegExp(`<details[^>]+id="gs-${pane}-[^\\"]+-section"`),
    );
  }

  const engineerGeneral = html.indexOf('data-subpane="engineer-general"');
  const engineerBehavior = html.indexOf('data-subpane="engineer-behavior"');
  const engineerDigests = html.indexOf('data-subpane="engineer-digests"');
  assert.ok(engineerGeneral < html.indexOf('id="gs-engineer-provider"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-specializations-picker"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-autonomy-mode"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-custom-instructions"'));
  assert.ok(html.indexOf('id="gs-engineer-custom-instructions"') < engineerDigests);
  assert.ok(engineerDigests < html.indexOf('id="gs-engineer-push-interval"'));

  const architectGeneral = html.indexOf('data-subpane="architect-general"');
  const architectBehavior = html.indexOf('data-subpane="architect-behavior"');
  const architectDigests = html.indexOf('data-subpane="architect-digests"');
  assert.ok(architectGeneral < html.indexOf('id="gs-architect-provider"'));
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-autonomy-mode"'));
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-custom-instructions"'));
  assert.ok(html.indexOf('id="gs-architect-custom-instructions"') < architectDigests);
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-journal-checkpoint"'));
  assert.ok(architectDigests < html.indexOf('id="gs-architect-push-interval"'));
});

test('group settings Advanced sub-tab owns Delete group action', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const commands = fs.readFileSync(path.join(repoRoot, 'static/js/commands.js'), 'utf8');
  const render = fs.readFileSync(path.join(repoRoot, 'static/js/render.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals.js'), 'utf8');

  assert.equal(html.indexOf('data-tab="advanced"'), -1);
  assert.equal(html.indexOf('data-pane="advanced"'), -1);

  const groupPaneIndex = html.indexOf('data-pane="group"');
  const agentsPaneIndex = html.indexOf('data-pane="agents"');
  assert.notEqual(groupPaneIndex, -1);
  assert.notEqual(agentsPaneIndex, -1);
  const groupPane = html.slice(groupPaneIndex, agentsPaneIndex);
  const groupGeneralIndex = groupPane.indexOf('data-subpane="group-general"');
  const groupAdvancedIndex = groupPane.indexOf('data-subpane="group-advanced"');
  assert.notEqual(groupGeneralIndex, -1);
  assert.notEqual(groupAdvancedIndex, -1);
  assert.ok(groupGeneralIndex < groupAdvancedIndex);
  assert.match(groupPane, /data-subtab="group-general"[\s\S]*data-subtab="group-advanced"/);

  const advancedPane = groupPane.slice(groupAdvancedIndex);
  assert.match(advancedPane, /Delete group/);
  assert.match(advancedPane, /class="btn-danger"/);
  assert.match(advancedPane, /deleteSettingsGroup\(\)/);
  assert.match(modals, /async function deleteSettingsGroup\(\)[\s\S]*removeGroup\(group\)[\s\S]*closeModals\(\)/);

  assert.doesNotMatch(commands, /function\s+onGroupContextMenu\b/);
  assert.doesNotMatch(render, /oncontextmenu="onGroupContextMenu/);
  assert.doesNotMatch(render, /title="Delete group"/);
  assert.match(render, /title="Group settings"[^`]*\\u2699/);
  assert.match(main, /openActiveGroupSettings\(event\)[\s\S]*&#9881;/);
  assert.doesNotMatch(main, /openActiveGroupMenu|&#8942;|Delete group/);
});

test('group settings sub-tab switching preserves scroll focus and inline draft state', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  const pane = sandbox.document.querySelector('.gs-pane[data-pane="engineer"]');
  pane.scrollTop = 144;
  const draft = ensure('gs-engineer-custom-instructions');
  draft.value = 'draft instructions in progress';
  draft.selectionStart = 6;
  draft.selectionEnd = 18;
  draft.focus();

  vm.runInContext(
    `switchGsSubTab('engineer', document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-digests"]'))`,
    context,
  );

  assert.equal(pane.scrollTop, 144);
  assert.equal(draft.focused, true);
  assert.equal(draft.value, 'draft instructions in progress');
  assert.equal(draft.selectionStart, 6);
  assert.equal(draft.selectionEnd, 18);
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-digests"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-general"]').classList.contains('active'),
    true,
  );
});

test('group settings sub-tab CSS remains reusable in narrow toolbelt layouts', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(html, /<div class="gs-pane active" data-pane="group">\s*<div class="gs-subtabs">/);
  assert.match(html, /<div class="gs-pane" data-pane="engineer">\s*<div class="gs-subtabs">/);
  assert.match(html, /<div class="gs-pane" data-pane="architect">\s*<div class="gs-subtabs">/);
  assert.match(css, /\.gs-subtabs\s*\{[^}]*display:\s*flex;[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.match(css, /\.gs-subpane\s*\{\s*display:\s*none;\s*\}/);
  assert.match(css, /\.gs-subpane\.active\s*\{\s*display:\s*block;\s*\}/);
});

test('group settings marks iTerm2-only controls for standalone mode gating', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const ws = fs.readFileSync(path.join(repoRoot, 'static/js/ws.js'), 'utf8');
  const start = html.indexOf('<!-- Group Settings modal -->');
  const end = html.indexOf('<!-- Global Settings modal -->', start);
  const modal = html.slice(start, end === -1 ? html.indexOf('<!-- Confirm dialog', start) : end);

  function assertControlMarked(id, labelText) {
    const controlMatch = modal.match(new RegExp(`<[^>]+id="${id}"[^>]*>`));
    assert.ok(controlMatch, `${id} control should exist`);
    assert.match(
      controlMatch[0],
      /class="[^"]*\biterm2-only\b[^"]*"/,
      `${id} control should carry iterm2-only`,
    );
    const before = modal.slice(Math.max(0, controlMatch.index - 220), controlMatch.index);
    assert.match(
      before,
      new RegExp(`<label class="[^"]*iterm2-only[^"]*">\\s*${labelText}\\s*</label>\\s*$`),
      `${id} label should carry iterm2-only`,
    );
  }

  [
    ['gs-profile', 'Profile'],
    ['gs-agent-profile', 'Profile'],
    ['gs-terminal-profile', 'Profile'],
    ['gs-engineer-profile', 'Profile'],
    ['gs-color-swatches', 'Tab color'],
    ['gs-agent-color-swatches', 'Tab color'],
    ['gs-terminal-color-swatches', 'Tab color'],
    ['gs-engineer-color-swatches', 'Tab color'],
  ].forEach(([id, label]) => assertControlMarked(id, label));

  [
    ['gs-collapsed', 'Start collapsed on load'],
    ['gs-filter-window', 'Pin to active window'],
    ['gs-terminal-close-on-disconnect', 'Close on disconnect'],
  ].forEach(([id, label]) => {
    assert.match(
      modal,
      new RegExp(`<label class="[^"]*iterm2-only[^"]*">\\s*<input id="${id}"[^>]*>\\s*${label}`),
      `${id} checkbox row should carry iterm2-only`,
    );
  });

  assert.match(
    css,
    /body\.standalone-mode \.iterm2-only,\s*body\[data-loom-mode="standalone"\] \.iterm2-only,\s*body\[data-loom-mode="desktop"\] \.iterm2-only\s*\{[^}]*display:\s*none\s*!important;/s,
  );
  assert.match(ws, /classList\.toggle\('standalone-mode',\s*standalone\)/);
  assert.match(ws, /classList\.toggle\('iterm2-mode',\s*iterm2\)/);
});

test('architect behavior sub-tab keeps runtime fields without fallback review-gate controls', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /data-subpane="architect-behavior"/);
  assert.match(html, /Configure architect digest verbosity and checkpoint policy\./);
  assert.match(html, /id="gs-architect-digest-verbosity"/);
  assert.match(html, /id="gs-architect-journal-checkpoint"/);
  assert.doesNotMatch(html, /fallback review-gate defaults for transitions without their own LOC gate/);
  assert.doesNotMatch(html, /Fallback review-gate thresholds/);
  assert.doesNotMatch(html, /gs-architect-review-/);
  assert.doesNotMatch(html, /gs-architect-deferred-section/);
  assert.doesNotMatch(html, /storage only|wiring in LOOM:196|⊘/);
});

test('submitGroupSettings sends group, engineer, and architect updates separately', () => {
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
  ensure('gs-engineer-can-override-worker-provider').checked = false;
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
  ensure('gs-architect-provider').value = 'codex';
  ensure('gs-architect-boot-cmd').value = 'codex --architect';
  ensure('gs-architect-model').value = 'gpt-5.1-architect';
  ensure('gs-architect-reasoning-effort').value = 'high';
  ensure('gs-architect-custom-instructions').value = 'Own scope';
  ensure('gs-architect-autonomy-mode').value = 'dispatch_freely';
  ensure('gs-architect-paused').checked = true;
  ensure('gs-architect-digest-verbosity').value = 'terse';
  ensure('gs-architect-journal-checkpoint').value = 'every_20_minutes';

  vm.runInContext('submitGroupSettings()', context);

  assert.equal(sandbox.sendCalls.length, 3);
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
  assert.equal(sandbox.sendCalls[1].engineer_can_override_worker_provider, false);
  assert.equal(sandbox.sendCalls[1].autonomy_mode, 'suggest_only');
  assert.equal(sandbox.sendCalls[1].default_worker_concurrency, 3);
  assert.equal(sandbox.sendCalls[1].wave_size_preference, 'small');
  assert.equal(sandbox.sendCalls[1].same_agent_follow_up_preference, 'prefer_fresh_agent');
  assert.equal(sandbox.sendCalls[1].digest_verbosity, 'compact');
  assert.equal(sandbox.sendCalls[1].escalation_style, 'ask_early');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[1].enabled_events)), ['agent_started', 'agent_progress']);
  assert.equal(sandbox.sendCalls[2].cmd, 'update_architect_settings');
  assert.equal(sandbox.sendCalls[2].group, 'alpha');
  assert.equal(sandbox.sendCalls[2].settings.architect_provider, 'codex');
  assert.equal(sandbox.sendCalls[2].settings.architect_boot_command, 'codex --architect');
  assert.equal(sandbox.sendCalls[2].settings.architect_model, 'gpt-5.1-architect');
  assert.equal(sandbox.sendCalls[2].settings.architect_reasoning_effort, 'high');
  assert.equal(sandbox.sendCalls[2].settings.architect_custom_instructions, 'Own scope');
  assert.equal(sandbox.sendCalls[2].settings.architect_autonomy_mode, 'dispatch_freely');
  assert.equal(sandbox.sendCalls[2].settings.architect_paused, true);
  assert.equal(sandbox.sendCalls[2].settings.architect_digest_verbosity, 'terse');
  assert.equal(sandbox.sendCalls[2].settings.architect_journal_checkpoint_frequency, 'every_20_minutes');
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      sandbox.sendCalls[2].settings,
      'architect_review_gate_thresholds',
    ),
    false,
  );
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

test('group settings no longer renders the legacy no-engineer placeholder copy', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const modalJs = fs.readFileSync(path.join(repoRoot, 'static/js/modals.js'), 'utf8');
  const legacyCopy = new RegExp('No engineer' + ' agent');
  assert.doesNotMatch(html, legacyCopy);
  assert.doesNotMatch(modalJs, legacyCopy);
  assert.match(html, /Hide other engineers' workers from this engineer/);
  assert.match(html, /Human operators still see all agents on the board/);
  // _showGroupSettings fetches specializations to populate the
  // default-specializations picker; ignore that side request here.
  const callsWithoutSpecFetch = sandbox.sendCalls.filter(
    (msg) => msg.cmd !== 'list_specializations',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(callsWithoutSpecFetch)), []);
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

test('GS Engineer tab loads default_engineer_specializations from settings', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: { default_engineer_specializations: ["ui-frontend", "react"] },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  const specs = JSON.parse(
    vm.runInContext('JSON.stringify(_gsEngineerSpecs)', context));
  assert.deepEqual(specs, ['ui-frontend', 'react']);
});

test('submitGroupSettings includes default_engineer_specializations in settings payload', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext(
    '_settingsGroup = "alpha"; _gsWtSymlinks = []; '
    + '_gsEngineerSpecs = ["rust-systems", "backend-python"];',
    context);

  vm.runInContext('submitGroupSettings()', context);

  const groupCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'update_group_settings');
  assert.ok(groupCall, 'update_group_settings should be sent');
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      groupCall.settings.default_engineer_specializations)),
    ['rust-systems', 'backend-python'],
  );
});

test('GS Engineer specializations picker rerender survives WS update', () => {
  // Rerender-regression: a `specializations` WS frame must not wipe the
  // currently-selected list (state lives in module-level _gsEngineerSpecs,
  // not in DOM). The picker re-paints from the same source.
  const { sandbox, ensure } = createSandbox();
  sandbox.state.specializations = [
    { name: 'ui-frontend', preamble: 'UI', priorities: [] },
    { name: 'react', preamble: 'React', priorities: [] },
  ];
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: { default_engineer_specializations: ["ui-frontend"] },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  const before = JSON.parse(
    vm.runInContext('JSON.stringify(_gsEngineerSpecs)', context));
  assert.deepEqual(before, ['ui-frontend']);

  // Simulate a WS rerender by directly invoking renderGsEngineerSpecializations
  // — the selection must remain.
  vm.runInContext('renderGsEngineerSpecializations()', context);
  const after = JSON.parse(
    vm.runInContext('JSON.stringify(_gsEngineerSpecs)', context));
  assert.deepEqual(after, ['ui-frontend']);
});

test('gsEngineerAddSpecialization appends and gsEngineerRemoveSpecialization drops', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.state.specializations = [
    { name: 'rust', preamble: 'Rust', priorities: [] },
    { name: 'react', preamble: 'React', priorities: [] },
  ];
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext('_gsEngineerSpecs = []; renderGsEngineerSpecializations();', context);
  ensure('gs-engineer-specializations-available').value = 'rust';
  vm.runInContext('gsEngineerAddSpecialization()', context);
  ensure('gs-engineer-specializations-available').value = 'react';
  vm.runInContext('gsEngineerAddSpecialization()', context);

  let specs = JSON.parse(
    vm.runInContext('JSON.stringify(_gsEngineerSpecs)', context));
  assert.deepEqual(specs, ['rust', 'react']);

  vm.runInContext('gsEngineerRemoveSpecialization(0)', context);
  specs = JSON.parse(
    vm.runInContext('JSON.stringify(_gsEngineerSpecs)', context));
  assert.deepEqual(specs, ['react']);
});

test('GS Engineer + New specialization opens nested above parent (modal-nested z-order)', () => {
  // Regression: #modal-new-specialization is declared earlier in the DOM
  // than #modal-group-settings, so without an explicit z-index bump the
  // parent overlay would paint on top of the nested dialog. The opener
  // tags the nested overlay with `modal-nested`, which CSS lifts to a
  // higher stacking layer.
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineerLaunchModals(context);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('modal-group-settings').classList.add('visible');

  vm.runInContext('openGsEngineerNewSpecializationDialog()', context);

  assert.equal(
    ensure('modal-new-specialization').classList.contains('visible'),
    true,
    'nested specialization modal should be visible',
  );
  assert.equal(
    ensure('modal-new-specialization').classList.contains('modal-nested'),
    true,
    'nested specialization modal must carry modal-nested for z-order',
  );
  assert.equal(
    ensure('modal-group-settings').classList.contains('visible'),
    true,
    'parent Group Settings overlay must remain visible underneath',
  );
});

test('GS Engineer + New specialization submit closes nested only and pops the stack', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineerLaunchModals(context);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('modal-group-settings').classList.add('visible');

  vm.runInContext('openGsEngineerNewSpecializationDialog()', context);
  ensure('new-specialization-name').value = 'rust-systems';
  ensure('new-specialization-preamble').value = 'Rust focus.';
  ensure('new-specialization-scope').value = 'project';

  vm.runInContext('submitNewSpecializationDialog()', context);

  // Server side fan-out: save_specialization + list_specializations.
  const saveCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'save_specialization');
  assert.ok(saveCall, 'save_specialization should be sent');
  assert.equal(saveCall.group, 'alpha',
    'group must come from _settingsGroup when launched from GS');
  assert.equal(saveCall.scope, 'project');

  // Nested overlay closed, stack popped, parent still visible.
  assert.equal(
    ensure('modal-new-specialization').classList.contains('visible'),
    false,
    'nested specialization overlay must close on submit',
  );
  assert.equal(
    ensure('modal-new-specialization').classList.contains('modal-nested'),
    false,
    'modal-nested class must be cleared on submit',
  );
  assert.equal(
    ensure('modal-group-settings').classList.contains('visible'),
    true,
    'parent Group Settings overlay must remain visible after nested submit',
  );
  const stackLen = vm.runInContext('_modalStack.length', context);
  assert.equal(stackLen, 0, 'modal stack must be empty after nested submit');
});

test('GS Engineer + New specialization Escape pops nested only (closeModals)', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadEngineerLaunchModals(context);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('modal-group-settings').classList.add('visible');

  vm.runInContext('openGsEngineerNewSpecializationDialog()', context);

  // The stack-pop path is what protects the parent — first closeModals
  // must consume the nested entry only.
  const stackBefore = vm.runInContext('_modalStack.length', context);
  assert.equal(stackBefore, 1, 'nested entry must be on the stack');

  vm.runInContext('closeModals()', context);

  assert.equal(
    ensure('modal-new-specialization').classList.contains('visible'),
    false,
    'first closeModals must dismiss the nested overlay',
  );
  assert.equal(
    ensure('modal-new-specialization').classList.contains('modal-nested'),
    false,
    'modal-nested class must be cleared on stack pop',
  );
  assert.equal(
    ensure('modal-group-settings').classList.contains('visible'),
    true,
    'parent Group Settings overlay must remain visible',
  );
  const stackAfter = vm.runInContext('_modalStack.length', context);
  assert.equal(stackAfter, 0, 'modal stack must be empty after pop');
});

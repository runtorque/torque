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

  const gsTabs = ['group', 'workers', 'engineer', 'architect'].map((name) => {
    const el = new FakeElement(`tab-${name}`);
    el.dataset.tab = name;
    return el;
  });
  const gsPanes = ['group', 'workers', 'engineer', 'architect'].map((name) => {
    const el = new FakeElement(`pane-${name}`);
    el.dataset.pane = name;
    return el;
  });
  const paneByName = Object.fromEntries(gsPanes.map((pane) => [pane.dataset.pane, pane]));
  const subtabNamesByPane = {
    group: ['group-general', 'group-worker-defaults', 'group-terminals', 'group-sync', 'group-advanced'],
    workers: ['worker-execution', 'worker-worktree', 'worker-notifications'],
    engineer: ['engineer-general', 'engineer-behavior', 'engineer-system'],
    architect: ['architect-general', 'architect-behavior', 'architect-system'],
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

function loadWs(context) {
  const filename = path.join(repoRoot, 'static/js/ws.js');
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

test('architect settings markup renders provider then command override', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const match = html.match(
    /<div class="gs-subpane active" data-subpane="architect-general">([\s\S]*?)<\/div>/,
  );
  assert.ok(match, 'architect general subpane should be present');
  assert.match(
    match[1],
    /<label>Provider<\/label>\s*<select id="gs-architect-provider"[^>]*><\/select>\s*<label>Command override<\/label>\s*<input id="gs-architect-boot-cmd"/,
  );
  assert.doesNotMatch(match[1], /Boot command/);
});

test('architect settings markup removes paused control and renders checkpoint dropdown tooltip', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const modalJs = fs.readFileSync(path.join(repoRoot, 'static/js/modals.js'), 'utf8');

  assert.doesNotMatch(html, /gs-architect-paused|Event delivery paused/);
  assert.doesNotMatch(modalJs, /gs-architect-paused|architect_paused/);
  assert.match(html, /<label>Journal checkpoint cadence\s*<span class="hint-btn"/);
  assert.match(
    html,
    /data-hint="How often Torque reminds the architect to write a `checkpoint` journal entry summarizing active engineers, open scope, pending hires, open decisions, and planned next moves\."/,
  );
  assert.match(html, /<select id="gs-architect-journal-checkpoint"><\/select>/);
});

test('group settings renders system prompt preview controls for Engineer and Architect', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    html,
    /<textarea id="gs-engineer-custom-instructions"[\s\S]*?<\/textarea>\s*<div class="system-prompt-preview-row">\s*<button type="button" id="gs-engineer-view-system-prompt"[^>]*>View system prompt<\/button>/,
  );
  assert.match(
    html,
    /<textarea id="gs-architect-custom-instructions"[\s\S]*?<\/textarea>\s*<div class="system-prompt-preview-row">\s*<button type="button" id="gs-architect-view-system-prompt"[^>]*>View system prompt<\/button>/,
  );
  assert.match(html, /id="modal-system-prompt-preview"[\s\S]*class="modal modal-tall preview-popup"/);
  assert.match(css, /body\.standalone-mode\s+\.preview-popup\s*{\s*max-width:\s*min\(80vw,\s*1180px\);/);
});

test('group settings markup renders board sync provider subtab and task sync mount', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(html, /data-subtab="group-sync"[\s\S]*>Sync provider<\/button>/);
  assert.match(html, /<select id="gs-board-sync-provider"[\s\S]*<option value="none">None<\/option>[\s\S]*<option value="github">GitHub<\/option>/);
  assert.match(html, /id="gs-board-sync-enabled"[\s\S]*Enable sync/);
  assert.match(html, /id="gs-board-sync-github-project-select"[\s\S]*Reload projects/);
  assert.match(html, /<details class="board-sync-manual-project">\s*<summary>Other owner…<\/summary>/);
  assert.match(html, /Project owner\s*<span class="label-hint">optional advanced fallback<\/span>/);
  assert.doesNotMatch(html, /<details class="board-sync-manual-project"[^>]*open/);
  assert.match(html, /Lane → status mapping[\s\S]*id="gs-board-sync-github-lane-map"/);
  assert.match(html, /id="gs-board-sync-test"[\s\S]*Test connection/);
  assert.match(html, /id="task-board-sync-section"/);
  assert.match(css, /\.task-board-sync-card/);
  assert.match(css, /\.board-card-github-chip/);
});

test('board sync inline-row buttons keep labels on one line (no wrap)', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  // Labels render as intact single-line text, not split across lines.
  assert.match(html, /id="gs-board-sync-use-current-repo"[^>]*>Use current repo<\/button>/);
  assert.match(html, /id="gs-board-sync-reload-projects"[^>]*>Reload projects<\/button>/);

  // CSS prevents the button from being squeezed/wrapped inside the flex row.
  assert.match(css, /\.board-sync-inline-row button\s*\{[^}]*flex:\s*0 0 auto[^}]*\}/);
  assert.match(css, /\.board-sync-inline-row button\s*\{[^}]*white-space:\s*nowrap[^}]*\}/);
});

test('group settings drops leftover Option A implementation-choice comment', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.doesNotMatch(html, /Option A:/);
});

test('group settings renders engineer merge mode selector', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');

  assert.match(html, /<label>Engineer merge mode[\s\S]*<select id="gs-engineer-merge-mode">/);
  assert.match(html, /<option value="pr">Pull request \(default\)<\/option>/);
  assert.match(html, /<option value="direct">Direct local<\/option>/);
  assert.match(html, /<option value="engineer-choice">Engineer choice<\/option>/);
  assert.match(html, /workflow-breach audit events/);
});

test('system prompt preview popup sends unsaved form state and closes as nested modal', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha"; _gsEngineerSpecs = ["ui"];', context);
  ensure('gs-default-agent-template').value = 'default-template';
  ensure('gs-agent-provider').value = 'codex';
  ensure('gs-agent-boot-cmd').value = 'codex';
  ensure('gs-agent-model').value = 'gpt-5';
  ensure('gs-agent-reasoning-effort').value = 'high';
  ensure('gs-agent-directory').value = '/repo';
  ensure('gs-agent-shell').value = 'zsh';
  ensure('gs-engineer-merge-mode').value = 'direct';
  ensure('gs-wt-merge-cleanup').value = 'close_remove';
  ensure('gs-engineer-provider').value = 'codex';
  ensure('gs-engineer-boot-cmd').value = 'codex --engineer';
  ensure('gs-engineer-model').value = 'gpt-5.1';
  ensure('gs-engineer-reasoning-effort').value = 'xhigh';
  ensure('gs-engineer-directory').value = '/repo/.torque/engineer';
  ensure('gs-engineer-shell').value = 'fish';
  ensure('gs-engineer-custom-instructions').value = 'UNSAVED engineer instructions';
  ensure('gs-engineer-restrict-to-created-agents').checked = true;
  ensure('gs-engineer-autonomy-mode').value = 'aggressive_auto_continue';
  ensure('gs-engineer-default-worker-concurrency').value = '4';
  ensure('gs-engineer-wave-size-preference').value = 'large';
  ensure('gs-engineer-same-agent-follow-up-preference').value = 'prefer_same_agent';
  ensure('gs-engineer-digest-verbosity').value = 'detailed';
  ensure('gs-engineer-escalation-style').value = 'keep_moving';

  vm.runInContext('openGroupSystemPromptPreview("engineer")', context);

  assert.equal(sandbox.sendCalls.length, 1);
  const call = sandbox.sendCalls[0];
  assert.equal(call.cmd, 'preview_system_prompt');
  assert.equal(call.kind, 'engineer');
  assert.equal(call.group, 'alpha');
  assert.equal(call.settings.custom_instructions, 'UNSAVED engineer instructions');
  assert.equal(call.settings.autonomy_mode, 'aggressive_auto_continue');
  assert.equal(call.group_settings.engineer_merge_mode, 'direct');
  assert.deepEqual(JSON.parse(JSON.stringify(call.group_settings.default_engineer_specializations)), ['ui']);
  assert.equal(ensure('modal-system-prompt-preview').classList.contains('visible'), true);
  assert.equal(ensure('modal-system-prompt-preview').classList.contains('modal-nested'), true);

  vm.runInContext(
    `_showSystemPromptPreview({
      type: "system_prompt_preview",
      request_id: ${JSON.stringify(call.request_id)},
      kind: "engineer",
      group: "alpha",
      prompt: "Rendered prompt with UNSAVED engineer instructions"
    })`,
    context,
  );
  assert.equal(
    ensure('system-prompt-preview-content').textContent,
    'Rendered prompt with UNSAVED engineer instructions',
  );

  vm.runInContext('closeSystemPromptPreview()', context);
  assert.equal(ensure('modal-system-prompt-preview').classList.contains('visible'), false);
  assert.equal(ensure('modal-system-prompt-preview').classList.contains('modal-nested'), false);
});

test('system prompt preview popup surfaces backend errors and clears after success', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha";', context);
  vm.runInContext('openGroupSystemPromptPreview("engineer")', context);
  const call = sandbox.sendCalls[0];

  vm.runInContext(
    `_showSystemPromptPreviewError({
      type: "error",
      request_id: ${JSON.stringify(call.request_id)},
      message: "Template syntax error"
    })`,
    context,
  );

  assert.equal(ensure('system-prompt-preview-content').textContent, '');
  assert.equal(
    ensure('system-prompt-preview-error').textContent,
    'Failed to render system prompt: Template syntax error',
  );
  assert.equal(ensure('system-prompt-preview-error').style.display, '');
  assert.equal(ensure('system-prompt-preview-copy-btn').disabled, true);

  vm.runInContext(
    `_showSystemPromptPreview({
      type: "system_prompt_preview",
      request_id: ${JSON.stringify(call.request_id)},
      kind: "engineer",
      group: "alpha",
      prompt: "Rendered prompt after fixing the template"
    })`,
    context,
  );

  assert.equal(
    ensure('system-prompt-preview-content').textContent,
    'Rendered prompt after fixing the template',
  );
  assert.equal(ensure('system-prompt-preview-error').textContent, '');
  assert.equal(ensure('system-prompt-preview-error').style.display, 'none');
  assert.equal(ensure('system-prompt-preview-copy-btn').disabled, false);
});

test('system prompt preview popup consumes WebSocket error while render is pending', () => {
  const { sandbox, ensure } = createSandbox();
  let socket = null;
  function FakeWebSocket(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    socket = this;
  }
  FakeWebSocket.OPEN = 1;
  FakeWebSocket.prototype.send = function send() {};
  FakeWebSocket.prototype.close = function close() {};
  sandbox.WebSocket = FakeWebSocket;
  sandbox.location = { host: 'localhost:18969' };
  sandbox.setTimeout = function setTimeoutStub() {};
  sandbox.toastCalls = [];
  sandbox._showToast = function _showToast(message, level) {
    sandbox.toastCalls.push({ message, level });
  };

  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha";', context);
  vm.runInContext('openGroupSystemPromptPreview("engineer")', context);
  loadWs(context);
  vm.runInContext('connect()', context);

  socket.onmessage({
    data: JSON.stringify({
      type: 'error',
      message: 'Role template could not be resolved',
    }),
  });

  assert.equal(
    ensure('system-prompt-preview-error').textContent,
    'Failed to render system prompt: Role template could not be resolved',
  );
  assert.equal(ensure('system-prompt-preview-error').style.display, '');
  assert.equal(ensure('system-prompt-preview-content').textContent, '');
  assert.deepEqual(sandbox.toastCalls, []);
});

test('architect system prompt preview uses architect form values', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('gs-architect-provider').value = 'codex';
  ensure('gs-architect-boot-cmd').value = 'codex --architect';
  ensure('gs-architect-model').value = 'gpt-5.1-architect';
  ensure('gs-architect-reasoning-effort').value = 'high';
  ensure('gs-architect-directory').value = '/repo/.torque/architect';
  ensure('gs-architect-shell').value = 'zsh';
  ensure('gs-architect-custom-instructions').value = 'UNSAVED architect instructions';
  ensure('gs-architect-autonomy-mode').value = 'ask_always';
  ensure('gs-architect-digest-verbosity').value = 'verbose';
  ensure('gs-architect-journal-checkpoint').value = 'manual_only';

  vm.runInContext('openGroupSystemPromptPreview("architect")', context);

  assert.equal(sandbox.sendCalls.length, 1);
  const call = sandbox.sendCalls[0];
  assert.equal(call.cmd, 'preview_system_prompt');
  assert.equal(call.kind, 'architect');
  assert.equal(call.settings.architect_custom_instructions, 'UNSAVED architect instructions');
  assert.equal(call.settings.architect_autonomy_mode, 'ask_always');
  assert.equal(call.settings.architect_journal_checkpoint_frequency, 'manual_only');
});

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

test('worker provider block inherits group default provider and previews model/command', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, [
    {
      name: 'codex',
      display_name: 'Codex',
      command: 'codex',
      reasoning_efforts: ['low', 'medium', 'high'],
    },
    {
      name: 'claude-code',
      display_name: 'Claude Code',
      command: 'claude',
      reasoning_efforts: ['low', 'medium', 'high', 'max'],
    },
  ]);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      agent_provider: "codex",
      agent_model: "gpt-5",
      agent_boot_command: "codex --sandbox",
      worker_provider: "",
      worker_model: "",
      worker_boot_command: "",
      worker_reasoning_effort: "high"
    },
    engineer_settings: {},
    architect_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-worker-provider').value, '');
  assert.deepEqual(
    ensure('gs-worker-provider').children.slice(0, 3).map((child) => ({
      value: child.value,
      text: child.textContent,
    })),
    [
      { value: '', text: 'Group default' },
      { value: 'codex', text: 'Codex' },
      { value: 'claude-code', text: 'Claude Code' },
    ],
  );
  assert.equal(ensure('gs-worker-model').value, '');
  assert.equal(ensure('gs-worker-model').placeholder, 'Group default: gpt-5');
  assert.equal(ensure('gs-worker-boot-command').value, '');
  assert.equal(ensure('gs-worker-boot-command').placeholder, 'Group default: codex --sandbox');
  assert.deepEqual(
    ensure('gs-worker-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high'],
  );
  assert.equal(ensure('gs-worker-reasoning-effort').value, 'high');
  assert.equal(ensure('gs-engineer-model').placeholder, 'Group default: gpt-5');
  assert.equal(ensure('gs-architect-model').placeholder, 'Group default: gpt-5');

  ensure('gs-agent-boot-cmd').value = '';
  ensure('gs-agent-model').value = '';
  ensure('gs-worker-provider').value = 'claude-code';
  vm.runInContext('onGsWorkerProviderChange()', context);
  assert.equal(ensure('gs-worker-model').placeholder, 'Group default: system default');
  assert.equal(ensure('gs-worker-boot-command').placeholder, 'Group default: claude');
  assert.deepEqual(
    ensure('gs-worker-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', 'max'],
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
      engineer_merge_mode: "engineer-choice",
      worktree_merge_cleanup: "close_remove",
      worktree_merge_preserve_diff: true
    },
    engineer_settings: {
      engineer_provider: "codex",
      engineer_boot_command: "codex --model gpt-5",
      engineer_model: "gpt-5.1-codex",
      engineer_reasoning_effort: "xhigh",
      engineer_directory: "/repo/.torque/engineer",
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
      architect_directory: "/repo/.torque/architect",
      architect_profile: "Ops",
      architect_shell: "fish",
      architect_tab_color: "none",
      architect_custom_instructions: "Own scope crisply.",
      architect_autonomy_mode: "ask_always",
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
  assert.equal(ensure('gs-engineer-directory').value, '/repo/.torque/engineer');
  assert.equal(ensure('gs-engineer-shell').value, 'fish');
  assert.deepEqual(
    ensure('gs-engineer-reasoning-effort').children.map((child) => child.value),
    ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
  );
  assert.equal(ensure('gs-engineer-custom-instructions').value, 'Watch for regressions.');
  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, false);
  assert.equal(ensure('gs-engineer-can-override-worker-provider').checked, false);
  assert.equal(ensure('gs-engineer-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('gs-engineer-default-worker-concurrency').value, '4');
  assert.equal(ensure('gs-engineer-wave-size-preference').value, 'large');
  assert.equal(ensure('gs-engineer-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('gs-engineer-notification-preset').value, 'custom');
  assert.equal(ensure('gs-engineer-digest-verbosity').value, 'detailed');
  assert.equal(ensure('gs-engineer-escalation-style').value, 'keep_moving');
  assert.equal(ensure('gs-engineer-merge-mode').value, 'engineer-choice');
  assert.equal(ensure('gs-wt-merge-cleanup').value, 'close_remove');
  assert.equal(ensure('gs-wt-merge-preserve-diff').checked, true);
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-general"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-system"]').classList.contains('active'),
    false,
  );
  assert.equal(ensure('gs-engineer-event-agent-started').checked, true);
  assert.equal(ensure('gs-engineer-event-agent-progress').checked, true);
  assert.equal(ensure('gs-architect-provider').value, 'codex');
  assert.equal(ensure('gs-architect-boot-cmd').value, 'codex --architect');
  assert.equal(ensure('gs-architect-model').value, 'gpt-5.1-architect');
  assert.equal(ensure('gs-architect-reasoning-effort').value, 'high');
  assert.equal(ensure('gs-architect-directory').value, '/repo/.torque/architect');
  assert.equal(ensure('gs-architect-shell').value, 'fish');
  assert.equal(ensure('gs-architect-custom-instructions').value, 'Own scope crisply.');
  assert.equal(ensure('gs-architect-autonomy-mode').value, 'ask_always');
  assert.equal(ensure('gs-architect-digest-verbosity').value, 'verbose');
  assert.equal(ensure('gs-architect-journal-checkpoint').value, 'every_15_actions');
  assert.ok(
    ensure('gs-architect-journal-checkpoint').children.some(
      (child) => child.value === 'every_15_actions'
        && child.textContent === 'Every 15 actions',
    ),
  );
  assert.equal(ensure('gs-engineer-provider').focused, true);
  assert.equal(ensure('modal-group-settings').classList.contains('visible'), true);
});

test('architect checkpoint dropdown preserves custom persisted cadences', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    architect_settings: {
      architect_journal_checkpoint_frequency: "every_37_actions"
    },
    profiles: ["Default"]
  })`, context);

  const select = ensure('gs-architect-journal-checkpoint');
  assert.equal(select.value, 'every_37_actions');
  assert.ok(
    select.children.some(
      (child) => child.value === 'every_37_actions'
        && child.textContent === 'Every 37 actions',
    ),
  );
});

test('group settings preserves Engineer and Architect sub-tabs across refresh', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext(`
    switchGsSubTab('engineer', document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-system"]'));
    switchGsSubTab('architect', document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-system"]'));
  `, context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-system"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-general"]').classList.contains('active'),
    false,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-system"]').classList.contains('active'),
    true,
  );
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="architect"] .gs-subtab[data-subtab="architect-general"]').classList.contains('active'),
    false,
  );
  assert.equal(ensure('gs-engineer-notification-preset').value, 'normal');
  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, true);
  assert.equal(ensure('gs-engineer-can-override-worker-provider').checked, true);
});

test('group settings uses Group/Workers split plus scoped Engineer and Architect sub-tabs', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const topStrip = html.slice(
    html.indexOf('<div class="gs-tabs">'),
    html.indexOf('    <!-- Group tab -->'),
  );
  const topTabs = Array.from(topStrip.matchAll(/data-tab="([^"]+)"/g), (match) => match[1]);
  assert.deepEqual(topTabs, ['group', 'workers', 'engineer', 'architect']);
  assert.doesNotMatch(topStrip, />Agents<\/button>/);
  assert.match(topStrip, />Workers<\/button>/);
  assert.match(topStrip, />Engineers<\/button>/);
  assert.match(topStrip, />Architects<\/button>/);
  assert.doesNotMatch(topStrip, />Engineer<\/button>/);
  assert.doesNotMatch(topStrip, />Architect<\/button>/);

  assert.match(
    html,
    /<div class="gs-pane active" data-pane="group">[\s\S]*?data-subtab="group-general"[\s\S]*?data-subtab="group-worker-defaults"[\s\S]*?data-subtab="group-terminals"[\s\S]*?data-subtab="group-advanced"/,
  );
  assert.match(html, /data-subtab="group-worker-defaults"[^>]*>Agents<\/button>/);
  assert.doesNotMatch(html, /data-subtab="group-worker-defaults"[^>]*>Worker defaults<\/button>/);
  assert.match(html, /data-subpane="group-general"/);
  assert.match(html, /data-subpane="group-worker-defaults"/);
  assert.match(html, /data-subpane="group-terminals"/);
  assert.match(html, /data-subpane="group-advanced"/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="workers">[\s\S]*?data-subtab="worker-execution"[\s\S]*?data-subtab="worker-worktree"[\s\S]*?data-subtab="worker-notifications"/,
  );
  assert.match(html, /data-subtab="worker-execution"[^>]*>General<\/button>/);
  assert.doesNotMatch(html, /data-subtab="worker-execution"[^>]*>Execution<\/button>/);
  assert.match(html, /data-subpane="worker-execution"/);
  assert.match(html, /data-subpane="worker-worktree"/);
  assert.match(html, /data-subpane="worker-notifications"/);
  assert.doesNotMatch(html, /data-pane="agents"/);
  assert.doesNotMatch(html, /data-subtab="agent-terminals"/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="engineer">[\s\S]*?data-subtab="engineer-general"[\s\S]*?data-subtab="engineer-behavior"[\s\S]*?data-subtab="engineer-system"[\s\S]*?>System<\/button>/,
  );
  assert.match(html, /data-subpane="engineer-general"/);
  assert.match(html, /data-subpane="engineer-behavior"/);
  assert.match(html, /data-subpane="engineer-system"/);
  assert.doesNotMatch(html, /<details[^>]+id="gs-engineer-[^\"]+-section"/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="architect">[\s\S]*?data-subtab="architect-general"[\s\S]*?data-subtab="architect-behavior"[\s\S]*?data-subtab="architect-system"[\s\S]*?>System<\/button>/,
  );
  assert.match(html, /data-subpane="architect-general"/);
  assert.match(html, /data-subpane="architect-behavior"/);
  assert.match(html, /data-subpane="architect-system"/);
  assert.doesNotMatch(html, /<details[^>]+id="gs-architect-[^\"]+-section"/);

  const engineerGeneral = html.indexOf('data-subpane="engineer-general"');
  const engineerBehavior = html.indexOf('data-subpane="engineer-behavior"');
  const engineerSystem = html.indexOf('data-subpane="engineer-system"');
  assert.ok(engineerGeneral < html.indexOf('id="gs-engineer-provider"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-specializations-picker"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-autonomy-mode"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-notification-preset"'));
  assert.ok(engineerBehavior < html.indexOf('id="gs-engineer-custom-instructions"'));
  assert.ok(html.indexOf('id="gs-engineer-custom-instructions"') < engineerSystem);
  assert.ok(engineerSystem < html.indexOf('id="gs-engineer-restrict-to-created-agents"'));
  assert.ok(engineerSystem < html.indexOf('id="gs-engineer-digest-verbosity"'));
  assert.ok(html.indexOf('id="gs-engineer-digest-verbosity"') < html.indexOf('id="gs-engineer-push-interval"'));

  const architectGeneral = html.indexOf('data-subpane="architect-general"');
  const architectBehavior = html.indexOf('data-subpane="architect-behavior"');
  const architectSystem = html.indexOf('data-subpane="architect-system"');
  assert.ok(architectGeneral < html.indexOf('id="gs-architect-provider"'));
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-autonomy-mode"'));
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-custom-instructions"'));
  assert.ok(html.indexOf('id="gs-architect-custom-instructions"') < architectSystem);
  assert.ok(architectBehavior < html.indexOf('id="gs-architect-journal-checkpoint"'));
  assert.ok(architectSystem < html.indexOf('id="gs-architect-directory"'));
  assert.ok(architectSystem < html.indexOf('id="gs-architect-digest-verbosity"'));
  assert.ok(html.indexOf('id="gs-architect-digest-verbosity"') < html.indexOf('id="gs-architect-push-interval"'));
});

test('group settings places all-kind defaults under Group and worker overrides under Workers General', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const groupStart = html.indexOf('<div class="gs-pane active" data-pane="group">');
  const workersStart = html.indexOf('<div class="gs-pane" data-pane="workers">');
  const engineerStart = html.indexOf('<div class="gs-pane" data-pane="engineer">');
  assert.notEqual(groupStart, -1);
  assert.notEqual(workersStart, -1);
  assert.notEqual(engineerStart, -1);

  const groupPane = html.slice(groupStart, workersStart);
  const workersPane = html.slice(workersStart, engineerStart);
  const workerDefaults = groupPane.indexOf('data-subpane="group-worker-defaults"');
  const terminals = groupPane.indexOf('data-subpane="group-terminals"');
  const advanced = groupPane.indexOf('data-subpane="group-advanced"');
  assert.ok(workerDefaults < groupPane.indexOf('id="gs-agent-provider"'));
  assert.ok(workerDefaults < groupPane.indexOf('id="gs-default-agent-template"'));
  assert.ok(workerDefaults < groupPane.indexOf('id="gs-agent-model"'));
  assert.ok(workerDefaults < terminals);
  assert.ok(terminals < groupPane.indexOf('id="gs-auto-terminals"'));
  assert.ok(terminals < groupPane.indexOf('id="gs-terminal-prefix"'));
  assert.ok(terminals < advanced);
  assert.match(groupPane, /Group-wide defaults for all agents — workers, engineers, and architects — unless overridden per-kind\./);
  assert.match(groupPane, /Default provider/);
  assert.match(groupPane, /Default role/);
  assert.match(groupPane, /Default model/);
  assert.doesNotMatch(groupPane, /Default worker provider/);
  assert.doesNotMatch(groupPane, /Default worker model/);

  assert.match(workersPane, /data-subpane="worker-execution"/);
  assert.match(workersPane, /id="gs-worker-provider"/);
  assert.match(workersPane, /id="gs-worker-boot-command"/);
  assert.match(workersPane, /id="gs-worker-model"/);
  assert.match(workersPane, /id="gs-worker-reasoning-effort"/);
  assert.match(workersPane, /id="gs-agent-directory"/);
  assert.match(workersPane, /id="gs-session-resume"/);
  assert.match(workersPane, /id="gs-agent-idle-timeout"/);
  assert.match(workersPane, /id="gs-worktree"/);
  assert.match(workersPane, /id="gs-notifications"/);
  assert.match(workersPane, /Enable macOS worker notifications/);
  assert.match(workersPane, /Group → Agents/);
  assert.doesNotMatch(workersPane, /Group → Worker defaults/);
  assert.doesNotMatch(workersPane, /id="gs-agent-provider"/);
  assert.doesNotMatch(workersPane, /id="gs-agent-model"/);
  assert.doesNotMatch(workersPane, /id="gs-terminal-prefix"/);
});

test('engineer System sub-tab groups permissions and digest settings', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals.js'), 'utf8');
  const engineerStart = html.indexOf('<div class="gs-pane" data-pane="engineer">');
  const architectStart = html.indexOf('<div class="gs-pane" data-pane="architect">');
  const engineerPane = html.slice(engineerStart, architectStart);
  const general = engineerPane.indexOf('data-subpane="engineer-general"');
  const behavior = engineerPane.indexOf('data-subpane="engineer-behavior"');
  const system = engineerPane.indexOf('data-subpane="engineer-system"');
  const permissions = engineerPane.indexOf('gs-settings-section-title">Permissions');
  const digestSettings = engineerPane.indexOf('gs-settings-section-title">Digest settings');

  assert.notEqual(system, -1);
  assert.ok(general < behavior);
  assert.ok(behavior < system);
  assert.ok(system < permissions);
  assert.ok(permissions < digestSettings);
  assert.equal(engineerPane.indexOf('>Digests</button>'), -1);
  assert.match(engineerPane, /data-subtab="engineer-system"[\s\S]*>System<\/button>/);
  assert.match(engineerPane, /Allow the Engineer to see workers created by other Engineers/);
  assert.match(engineerPane, /Allow the Engineer to override the provider for the workers it creates/);
  assert.match(
    engineerPane,
    /Engineers running Claude Code tend to choose Claude Code for their workers regardless of the group's default provider\. Disable this to force the engineer to use the group default\./,
  );
  assert.ok(permissions < engineerPane.indexOf('id="gs-engineer-restrict-to-created-agents"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-can-override-worker-provider"') < digestSettings);
  assert.ok(digestSettings < engineerPane.indexOf('id="gs-engineer-digest-verbosity"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-digest-verbosity"') < engineerPane.indexOf('id="gs-engineer-push-interval"'));
  assert.ok(behavior < engineerPane.indexOf('id="gs-engineer-notification-preset"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-notification-preset"') < system);
  assert.match(engineerPane, /id="gs-engineer-digest-verbosity-hint" class="hint-btn"/);
  assert.match(modals, /const DIGEST_VERBOSITY_TOOLTIP_HELP = 'Controls how much detail appears in digest events sent to this agent\. Higher verbosity can wake the agent more often on coarse-event activity in the group\.'/);
  assert.match(modals, /gs-engineer-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP/);
  assert.match(modals, /gs-architect-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP/);
});

test('architect System sub-tab groups terminal overrides and digest settings', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const architectStart = html.indexOf('<div class="gs-pane" data-pane="architect">');
  const footerStart = html.indexOf('<div class="modal-actions">', architectStart);
  const architectPane = html.slice(architectStart, footerStart);
  const general = architectPane.indexOf('data-subpane="architect-general"');
  const behavior = architectPane.indexOf('data-subpane="architect-behavior"');
  const system = architectPane.indexOf('data-subpane="architect-system"');
  const terminalOverrides = architectPane.indexOf('gs-settings-section-title">Terminal overrides');
  const digestSettings = architectPane.indexOf('gs-settings-section-title">Digest settings');

  assert.notEqual(system, -1);
  assert.ok(general < behavior);
  assert.ok(behavior < system);
  assert.ok(system < terminalOverrides);
  assert.ok(terminalOverrides < digestSettings);
  assert.equal(architectPane.indexOf('>Digests</button>'), -1);
  assert.match(architectPane, /data-subtab="architect-system"[\s\S]*>System<\/button>/);
  assert.ok(terminalOverrides < architectPane.indexOf('id="gs-architect-directory"'));
  assert.ok(architectPane.indexOf('id="gs-architect-directory"') < architectPane.indexOf('id="gs-architect-shell"'));
  assert.equal(architectPane.indexOf('id="gs-architect-profile"'), -1);
  assert.equal(architectPane.indexOf('id="gs-architect-color-swatches"'), -1);
  assert.ok(digestSettings < architectPane.indexOf('id="gs-architect-digest-verbosity"'));
  assert.ok(architectPane.indexOf('id="gs-architect-digest-verbosity"') < architectPane.indexOf('id="gs-architect-push-interval"'));
  assert.ok(behavior < architectPane.indexOf('id="gs-architect-journal-checkpoint"'));
  assert.ok(architectPane.indexOf('id="gs-architect-journal-checkpoint"') < system);
  assert.match(architectPane, /id="gs-architect-digest-verbosity-hint" class="hint-btn"/);
});

test('engineer worker visibility permission defaults true and inverts legacy hide setting', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, true);
  sandbox.sendCalls.length = 0;
  ensure('gs-engineer-restrict-to-created-agents').checked = true;
  vm.runInContext('submitGroupSettings()', context);
  assert.equal(sandbox.sendCalls[1].restrict_to_created_agents, false);

  sandbox.sendCalls.length = 0;
  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {},
    engineer_settings: { restrict_to_created_agents: true },
    profiles: ["Default"]
  })`, context);
  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, false);
  sandbox.sendCalls.length = 0;
  ensure('gs-engineer-restrict-to-created-agents').checked = false;
  vm.runInContext('submitGroupSettings()', context);
  assert.equal(sandbox.sendCalls[1].restrict_to_created_agents, true);
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
  const workersPaneIndex = html.indexOf('data-pane="workers"');
  assert.notEqual(groupPaneIndex, -1);
  assert.notEqual(workersPaneIndex, -1);
  const groupPane = html.slice(groupPaneIndex, workersPaneIndex);
  const groupGeneralIndex = groupPane.indexOf('data-subpane="group-general"');
  const groupAdvancedIndex = groupPane.indexOf('data-subpane="group-advanced"');
  assert.notEqual(groupGeneralIndex, -1);
  assert.notEqual(groupAdvancedIndex, -1);
  assert.ok(groupGeneralIndex < groupAdvancedIndex);
  assert.match(groupPane, /data-subtab="group-general"[\s\S]*data-subtab="group-advanced"/);

  const advancedPane = groupPane.slice(groupAdvancedIndex);
  assert.match(advancedPane, /Guidance hint cadence/);
  assert.match(
    advancedPane,
    /<input id="gs-guidance-hint-cadence" type="number" min="0" max="100" step="1" value="4">/,
  );
  assert.match(advancedPane, /show on the 1st occurrence, then every N agent messages/);
  assert.match(advancedPane, /0 = every message/);
  assert.match(advancedPane, /Delete group/);
  assert.match(advancedPane, /class="btn-danger"/);
  assert.match(advancedPane, /deleteSettingsGroup\(\)/);
  assert.match(modals, /async function deleteSettingsGroup\(\)[\s\S]*removeGroup\(group\)[\s\S]*closeModals\(\)/);

  assert.doesNotMatch(commands, /function\s+onGroupContextMenu\b/);
  assert.doesNotMatch(render, /oncontextmenu="onGroupContextMenu/);
  assert.doesNotMatch(render, /title="Delete group"/);
  assert.match(render, /title="Group settings"[^`]*\\u2699/);
  assert.match(render, /openActiveGroupSettings\(event\)[\s\S]*&#9881;/);
  assert.doesNotMatch(main, /openActiveGroupMenu|&#8942;|Delete group/);
});

test('Group Settings guidance hint cadence reads persisted value and submits parsed int', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_gsInitialSubtab = "group-advanced"', context);
  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      guidance_hint_cadence: 0
    },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-guidance-hint-cadence').value, 0);
  assert.equal(ensure('gs-guidance-hint-cadence').focused, true);

  sandbox.sendCalls.length = 0;
  ensure('gs-guidance-hint-cadence').value = '17';
  vm.runInContext('submitGroupSettings()', context);

  const groupCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'update_group_settings');
  assert.ok(groupCall, 'update_group_settings should be sent');
  assert.equal(groupCall.settings.guidance_hint_cadence, 17);
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
    `switchGsSubTab('engineer', document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-system"]'))`,
    context,
  );

  assert.equal(pane.scrollTop, 144);
  assert.equal(draft.focused, true);
  assert.equal(draft.value, 'draft instructions in progress');
  assert.equal(draft.selectionStart, 6);
  assert.equal(draft.selectionEnd, 18);
  assert.equal(
    sandbox.document.querySelector('.gs-pane[data-pane="engineer"] .gs-subtab[data-subtab="engineer-system"]').classList.contains('active'),
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
  assert.match(html, /<div class="gs-pane" data-pane="workers">\s*<div class="gs-subtabs">/);
  assert.match(html, /<div class="gs-pane" data-pane="engineer">\s*<div class="gs-subtabs">/);
  assert.match(html, /<div class="gs-pane" data-pane="architect">\s*<div class="gs-subtabs">/);
  assert.match(css, /\.gs-subtabs\s*\{[^}]*display:\s*flex;[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.match(css, /\.gs-subpane\s*\{\s*display:\s*none;\s*\}/);
  assert.match(css, /\.gs-subpane\.active\s*\{\s*display:\s*block;\s*\}/);
});

test('group settings removes terminal-backend-only profile and tab-color controls', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const ws = fs.readFileSync(path.join(repoRoot, 'static/js/ws.js'), 'utf8');
  const start = html.indexOf('<!-- Group Settings modal -->');
  const end = html.indexOf('<!-- Global Settings modal -->', start);
  const modal = html.slice(start, end === -1 ? html.indexOf('<!-- Confirm dialog', start) : end);

  [
    'gs-profile',
    'gs-agent-profile',
    'gs-terminal-profile',
    'gs-engineer-profile',
    'gs-architect-profile',
    'gs-color-swatches',
    'gs-agent-color-swatches',
    'gs-terminal-color-swatches',
    'gs-engineer-color-swatches',
    'gs-architect-color-swatches',
    'gs-terminal-close-on-disconnect',
  ].forEach((id) => {
    assert.equal(modal.indexOf(`id="${id}"`), -1, `${id} should be removed`);
  });

  assert.doesNotMatch(html, /\biterm2-only\b/);
  assert.doesNotMatch(css, /\biterm2-only\b/);
  assert.match(ws, /classList\.toggle\('standalone-mode',\s*standalone\)/);
  assert.doesNotMatch(ws, /iterm2-mode/);
});

test('architect behavior sub-tab keeps policy fields without fallback review-gate controls', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /data-subpane="architect-behavior"/);
  const behaviorStart = html.indexOf('data-subpane="architect-behavior"');
  const systemStart = html.indexOf('data-subpane="architect-system"');
  const behaviorPane = html.slice(behaviorStart, systemStart);
  assert.match(behaviorPane, /Configure architect checkpoint policy\./);
  assert.doesNotMatch(behaviorPane, /id="gs-architect-digest-verbosity"/);
  assert.match(html, /id="gs-architect-journal-checkpoint"/);
  assert.doesNotMatch(html, /fallback review-gate defaults for transitions without their own LOC gate/);
  assert.doesNotMatch(html, /Fallback review-gate thresholds/);
  assert.doesNotMatch(html, /gs-architect-review-/);
  assert.doesNotMatch(html, /gs-architect-deferred-section/);
  assert.doesNotMatch(html, /storage only|wiring in TORQUE:196|⊘/);
});

test('submitGroupSettings sends group, engineer, and architect updates separately', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha"; _gsWtSymlinks = ["etl/**/node_modules"];', context);
  ensure('gs-directory').value = '/repo';
  ensure('gs-agent-directory').value = '/repo/agents';
  ensure('gs-agent-shell').value = 'zsh';
  ensure('gs-default-agent-template').value = 'careful-reviewer';
  ensure('gs-agent-provider').value = 'codex';
  ensure('gs-agent-boot-cmd').value = 'codex --worker';
  ensure('gs-worker-provider').value = 'codex';
  ensure('gs-worker-boot-command').value = 'codex --worker-kind';
  ensure('gs-worker-model').value = 'gpt-5-worker';
  ensure('gs-worker-reasoning-effort').value = 'high';
  ensure('gs-terminal-prefix').value = 'Shell';
  ensure('gs-terminal-boot-cmd').value = 'npm run dev';
  ensure('gs-engineer-merge-mode').value = 'direct';
  ensure('gs-wt-merge-cleanup').value = 'remove';
  ensure('gs-wt-merge-preserve-diff').checked = true;
  ensure('gs-wt-symlink-gitignored').checked = true;
  ensure('gs-engineer-provider').value = 'codex';
  ensure('gs-engineer-boot-cmd').value = 'codex --model gpt-5';
  ensure('gs-agent-model').value = 'gpt-5';
  ensure('gs-agent-reasoning-effort').value = 'minimal';
  ensure('gs-session-resume').checked = false;
  ensure('gs-agent-idle-timeout').value = '15';
  ensure('gs-notifications').checked = true;
  ensure('gs-notify-finish').checked = false;
  ensure('gs-notify-error').checked = true;
  ensure('gs-notify-attention').checked = false;
  ensure('gs-engineer-model').value = 'gpt-5.1';
  ensure('gs-engineer-reasoning-effort').value = 'xhigh';
  ensure('gs-engineer-directory').value = '/repo/.torque/engineer';
  ensure('gs-engineer-shell').value = 'fish';
  ensure('gs-engineer-custom-instructions').value = 'Stay focused';
  ensure('gs-engineer-restrict-to-created-agents').checked = false;
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
  ensure('gs-architect-directory').value = '/repo/.torque/architect';
  ensure('gs-architect-shell').value = 'zsh';
  ensure('gs-architect-custom-instructions').value = 'Own scope';
  ensure('gs-architect-autonomy-mode').value = 'dispatch_freely';
  ensure('gs-architect-digest-verbosity').value = 'terse';
  ensure('gs-architect-journal-checkpoint').value = 'every_20_minutes';

  vm.runInContext('submitGroupSettings()', context);

  assert.equal(sandbox.sendCalls.length, 3);
  assert.equal(sandbox.sendCalls[0].cmd, 'update_group_settings');
  assert.equal(sandbox.sendCalls[0].group, 'alpha');
  assert.equal(sandbox.sendCalls[0].settings.agent_directory, '/repo/agents');
  assert.equal(sandbox.sendCalls[0].settings.agent_shell, 'zsh');
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'agent_profile'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'agent_tab_color'), false);
  assert.equal(sandbox.sendCalls[0].settings.default_agent_template, 'careful-reviewer');
  assert.equal(sandbox.sendCalls[0].settings.agent_provider, 'codex');
  assert.equal(sandbox.sendCalls[0].settings.agent_boot_command, 'codex --worker');
  assert.equal(sandbox.sendCalls[0].settings.worker_provider, 'codex');
  assert.equal(sandbox.sendCalls[0].settings.worker_boot_command, 'codex --worker-kind');
  assert.equal(sandbox.sendCalls[0].settings.worker_model, 'gpt-5-worker');
  assert.equal(sandbox.sendCalls[0].settings.worker_reasoning_effort, 'high');
  assert.equal(sandbox.sendCalls[0].settings.terminal_name_prefix, 'Shell');
  assert.equal(sandbox.sendCalls[0].settings.terminal_boot_command, 'npm run dev');
  assert.equal(sandbox.sendCalls[0].settings.engineer_merge_mode, 'direct');
  assert.equal(sandbox.sendCalls[0].settings.worktree_merge_cleanup, 'remove');
  assert.equal(sandbox.sendCalls[0].settings.worktree_merge_preserve_diff, true);
  assert.equal(sandbox.sendCalls[0].settings.worktree_symlink_gitignored_paths, true);
  assert.equal(sandbox.sendCalls[0].settings.agent_model, 'gpt-5');
  assert.equal(sandbox.sendCalls[0].settings.agent_reasoning_effort, 'minimal');
  assert.equal(sandbox.sendCalls[0].settings.agent_session_resume, false);
  assert.equal(sandbox.sendCalls[0].settings.agent_idle_timeout, 15);
  assert.equal(sandbox.sendCalls[0].settings.notifications, true);
  assert.equal(sandbox.sendCalls[0].settings.notify_on_finish, false);
  assert.equal(sandbox.sendCalls[0].settings.notify_on_error, true);
  assert.equal(sandbox.sendCalls[0].settings.notify_on_attention, false);
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
  assert.equal(sandbox.sendCalls[1].engineer_directory, '/repo/.torque/engineer');
  assert.equal(sandbox.sendCalls[1].engineer_shell, 'fish');
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[1], 'engineer_profile'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[1], 'engineer_tab_color'), false);
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
  assert.equal(sandbox.sendCalls[2].settings.architect_directory, '/repo/.torque/architect');
  assert.equal(sandbox.sendCalls[2].settings.architect_shell, 'zsh');
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[2].settings, 'architect_profile'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[2].settings, 'architect_tab_color'), false);
  assert.equal(sandbox.sendCalls[2].settings.architect_custom_instructions, 'Own scope');
  assert.equal(sandbox.sendCalls[2].settings.architect_autonomy_mode, 'dispatch_freely');
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      sandbox.sendCalls[2].settings,
      'architect_paused',
    ),
    false,
  );
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

test('submitGroupSettings sends empty worker launch overrides as group-default inheritance', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('gs-worker-provider').value = '';
  ensure('gs-worker-boot-command').value = '';
  ensure('gs-worker-model').value = '';
  ensure('gs-worker-reasoning-effort').value = '';
  ensure('gs-engineer-default-worker-concurrency').value = '2';

  vm.runInContext('submitGroupSettings()', context);

  assert.equal(sandbox.sendCalls[0].cmd, 'update_group_settings');
  assert.equal(sandbox.sendCalls[0].settings.worker_provider, '');
  assert.equal(sandbox.sendCalls[0].settings.worker_boot_command, '');
  assert.equal(sandbox.sendCalls[0].settings.worker_model, '');
  assert.equal(sandbox.sendCalls[0].settings.worker_reasoning_effort, '');
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
  assert.doesNotMatch(html, /Hide other engineers' workers from this engineer/);
  assert.match(html, /Allow the Engineer to see workers created by other Engineers/);
  assert.match(html, /Human operators still see all workers on the board/);
  // _showGroupSettings fetches specializations to populate the
  // default-specializations picker; ignore that side request here.
  const callsWithoutSpecFetch = sandbox.sendCalls.filter(
    (msg) => msg.cmd !== 'list_specializations',
  );
  assert.deepEqual(JSON.parse(JSON.stringify(callsWithoutSpecFetch)), []);
});

test('Group Settings board sync fields populate, gate GitHub config, and submit payload', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, sandbox._cachedProviders);

  vm.runInContext('_gsInitialSubtab = "group-sync"', context);
  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      board_sync_provider: "github",
      board_sync_enabled: true,
      board_sync_github: {
        github_repo: "acme/widgets",
        github_project_owner: "acme",
        github_project_number: 42,
        github_project_id: "PVT_42",
        github_project_status_field: "Pipeline",
        github_lane_status_map: { "Backlog": "Todo", "In Progress": "Doing" },
        github_close_issues_via_pr: false,
        github_create_missing_labels: true,
        github_assignee_map: { "worker-1": "octocat" }
      }
    },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-board-sync-provider').value, 'github');
  assert.equal(ensure('gs-board-sync-enabled').checked, true);
  assert.equal(ensure('gs-board-sync-github-config').style.display, '');
  assert.equal(ensure('gs-board-sync-github-repo').value, 'acme/widgets');
  assert.equal(ensure('gs-board-sync-github-project-owner').value, 'acme');
  assert.equal(ensure('gs-board-sync-github-project-number').value, 42);
  assert.equal(ensure('gs-board-sync-github-project-id').value, 'PVT_42');
  assert.equal(ensure('gs-board-sync-github-status-field').value, 'Pipeline');
  assert.match(ensure('gs-board-sync-github-lane-map').value, /"Backlog": "Todo"/);
  assert.equal(ensure('gs-board-sync-github-close-via-pr').checked, false);
  assert.equal(ensure('gs-board-sync-github-create-labels').checked, true);
  assert.match(ensure('gs-board-sync-github-assignee-map').value, /"worker-1": "octocat"/);
  assert.equal(ensure('gs-board-sync-provider').focused, true);
  assert.equal(
    sandbox.sendCalls.filter((msg) => msg.cmd === 'board_sync_list_projects').length,
    1,
  );

  ensure('gs-board-sync-provider').value = 'none';
  vm.runInContext('onGsBoardSyncProviderChange()', context);
  assert.equal(ensure('gs-board-sync-github-config').style.display, 'none');

  ensure('gs-board-sync-provider').value = 'github';
  ensure('gs-board-sync-enabled').checked = false;
  ensure('gs-board-sync-github-repo').value = 'acme/torque';
  ensure('gs-board-sync-github-project-owner').value = 'octo-org';
  ensure('gs-board-sync-github-project-number').value = '7';
  ensure('gs-board-sync-github-project-id').value = 'PVT_7';
  ensure('gs-board-sync-github-status-field').value = 'Status';
  ensure('gs-board-sync-github-lane-map').value = '{"Backlog":"Ready","Done":"Done"}';
  ensure('gs-board-sync-github-close-via-pr').checked = true;
  ensure('gs-board-sync-github-create-labels').checked = true;
  ensure('gs-board-sync-github-assignee-map').value = '{"worker-1":"monalisa"}';

  vm.runInContext('submitGroupSettings()', context);

  const groupCall = sandbox.sendCalls.find(
    (msg) => msg.cmd === 'update_group_settings');
  assert.ok(groupCall, 'update_group_settings should be sent');
  assert.equal(groupCall.settings.board_sync_provider, 'github');
  assert.equal(groupCall.settings.board_sync_enabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(groupCall.settings.board_sync_github)), {
    github_repo: 'acme/torque',
    github_project_owner: 'octo-org',
    github_project_number: 7,
    github_project_id: 'PVT_7',
    github_project_status_field: 'Status',
    github_lane_status_map: { Backlog: 'Ready', Done: 'Done' },
    github_close_issues_via_pr: true,
    github_create_missing_labels: true,
    github_assignee_map: { 'worker-1': 'monalisa' },
  });
});

test('Group Settings board sync project dropdown reloads and selection resolves project draft', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext('_settingsGroup = "alpha";', context);
  ensure('gs-board-sync-provider').value = 'github';
  ensure('gs-board-sync-enabled').checked = false;
  ensure('gs-board-sync-github-repo').value = 'acme/torque';
  ensure('gs-board-sync-github-project-owner').value = '';
  ensure('gs-board-sync-github-project-number').value = '';
  ensure('gs-board-sync-github-project-id').value = '';

  vm.runInContext('gsBoardSyncReloadProjects()', context);
  let call = sandbox.sendCalls.at(-1);
  assert.equal(call.cmd, 'board_sync_list_projects');
  assert.equal(call.provider, 'github');
  assert.equal(call.owner, '');
  assert.equal(call.settings.board_sync_enabled, false);
  assert.equal(call.settings.board_sync_github.github_repo, 'acme/torque');

  vm.runInContext(`_handleBoardSyncProjects({
    type: "board_sync_list_projects",
    group: "alpha",
    ok: true,
    owners: ["@me", "acme"],
    projects: [
      { number: 5, name: "Personal", owner: "octocat", id: "PVT_5", url: "https://github.com/users/octocat/projects/5" },
      { number: 7, name: "Roadmap", owner: "acme", id: "PVT_7", url: "https://github.com/orgs/acme/projects/7" }
    ]
  })`, context);

  const select = ensure('gs-board-sync-github-project-select');
  assert.equal(select.children.length, 3);
  assert.equal(select.children[1].textContent, 'octocat · #5 — Personal');
  assert.equal(select.children[2].textContent, 'acme · #7 — Roadmap');
  assert.match(ensure('gs-board-sync-project-summary').textContent, /from @me, acme/);

  select.value = 'acme#7#PVT_7';
  vm.runInContext('onGsBoardSyncProjectSelect()', context);

  assert.equal(ensure('gs-board-sync-github-project-owner').value, 'acme');
  assert.equal(ensure('gs-board-sync-github-project-number').value, 7);
  assert.equal(ensure('gs-board-sync-github-project-id').value, 'PVT_7');
  call = sandbox.sendCalls.at(-1);
  assert.equal(call.cmd, 'board_sync_preflight');
  assert.equal(call.provider, 'github');
  assert.equal(call.settings.board_sync_github.github_project_owner, 'acme');
  assert.equal(call.settings.board_sync_github.github_project_number, 7);
  assert.equal(call.settings.board_sync_github.github_project_id, 'PVT_7');
});

test('Group Settings board sync preflight auto-fills empty lane map but preserves custom map', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext('_settingsGroup = "alpha";', context);
  context.document.getElementById('gs-board-sync-github-lane-map').value = '';
  vm.runInContext(`_handleBoardSyncPreflight({
    type: "board_sync_preflight",
    group: "alpha",
    ok: true,
    repo: "acme/widgets",
    project_number: 7,
    project_id: "PVT_7",
    lane_status_map_strategy: "position",
    lane_status_map_suggestion: {
      "Backlog": "Todo",
      "In Progress": "Doing",
      "Done": "Done"
    },
    lane_status_map_unmatched_lanes: []
  })`, context);
  assert.match(
    context.document.getElementById('gs-board-sync-github-lane-map').value,
    /"In Progress": "Doing"/,
  );

  context.document.getElementById('gs-board-sync-github-lane-map').value = '{"Backlog":"Ready"}';
  vm.runInContext(`_handleBoardSyncPreflight({
    type: "board_sync_preflight",
    group: "alpha",
    ok: true,
    repo: "acme/widgets",
    lane_status_map_strategy: "position",
    lane_status_map_suggestion: { "Backlog": "Todo" },
    lane_status_map_unmatched_lanes: []
  })`, context);
  assert.equal(
    context.document.getElementById('gs-board-sync-github-lane-map').value,
    '{"Backlog":"Ready"}',
  );

  context.document.getElementById('gs-board-sync-github-lane-map').value = '';
  vm.runInContext(`_handleBoardSyncPreflight({
    type: "board_sync_preflight",
    group: "alpha",
    ok: true,
    repo: "acme/widgets",
    lane_status_map_strategy: "name",
    lane_status_map_suggestion: {},
    lane_status_map_unmatched_lanes: ["Backlog", "In Progress"]
  })`, context);
  assert.match(
    context.document.getElementById('gs-board-sync-project-summary').textContent,
    /Map these lanes manually: Backlog, In Progress/,
  );
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

test('worktree gitignored symlink checkbox renders and loads from settings', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /id="gs-wt-symlink-gitignored"/);
  assert.match(html, /Symlink gitignored paths/);

  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      worktree_symlink_gitignored_paths: true,
      worktree_symlinks: []
    },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-wt-symlink-gitignored').checked, true);
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

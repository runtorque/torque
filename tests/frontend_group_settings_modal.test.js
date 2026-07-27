const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const { appStylesheetSource } = require('./frontend_stylesheet_loader');

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
    this._controls = [];
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
    if (selector === 'input, select, textarea, button') return this._controls || [];
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
    group: ['group-general', 'group-worker-defaults', 'group-sync', 'group-advanced'],
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
  [
    'static/js/modals/core.js',
    'static/js/modals.js',
    'static/js/modals/group-settings.js',
    'static/js/modals/worktrees.js',
  ].forEach(function(relPath) {
    const filename = path.join(repoRoot, relPath);
    const source = fs.readFileSync(filename, 'utf8');
    vm.runInContext(source, context, { filename });
  });
}

function loadWs(context) {
  [
    'static/js/ws.js',
    'static/js/ws/interaction-guard.js',
    'static/js/ws/full-state.js',
    'static/js/ws/invalidation.js',
    'static/js/ws/delta-registry.js',
    'static/js/ws/delta-apply.js',
    'static/js/ws/action-router.js',
  ].forEach(function(relPath) {
    const filename = path.join(repoRoot, relPath);
    const source = fs.readFileSync(filename, 'utf8');
    vm.runInContext(source, context, { filename });
  });
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

test('architect General uses shared launch ordering and keeps command secondary', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const generalStart = html.indexOf('data-subpane="architect-general"');
  const behaviorStart = html.indexOf('data-subpane="architect-behavior"');
  const general = html.slice(generalStart, behaviorStart);
  const provider = general.indexOf('id="gs-architect-provider"');
  const model = general.indexOf('id="gs-architect-model-select"');
  const effort = general.indexOf('id="gs-architect-reasoning-effort"');
  const command = general.indexOf('id="gs-architect-boot-cmd"');

  assert.match(general, /gs-settings-section-title">Launch/);
  assert.ok(provider < model);
  assert.ok(model < effort);
  assert.ok(effort < command);
  assert.match(general, /settings-field--secondary[\s\S]*id="gs-architect-boot-cmd"/);
  assert.doesNotMatch(general, />Boot command</);
});

test('architect settings markup removes paused control and renders checkpoint dropdown tooltip', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const modalJs = fs.readFileSync(path.join(repoRoot, 'static/js/modals/group-settings.js'), 'utf8');

  assert.doesNotMatch(html, /gs-architect-paused|Event delivery paused/);
  assert.doesNotMatch(modalJs, /gs-architect-paused|architect_paused/);
  assert.match(html, /<label for="gs-architect-journal-checkpoint">Journal checkpoint\s*<span class="hint-btn"/);
  assert.match(
    html,
    /data-hint="How often Torque reminds the Architect to summarize active Engineers, open scope, pending hires, decisions, and next moves\."/,
  );
  assert.match(html, /<select id="gs-architect-journal-checkpoint"><\/select>/);
});

test('group settings renders system prompt preview controls for Engineer and Architect', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();

  assert.match(
    html,
    /<textarea id="gs-engineer-custom-instructions"[\s\S]*?<\/textarea>\s*<div class="system-prompt-preview-row">\s*<button type="button" id="gs-engineer-view-system-prompt"[^>]*>View system prompt<\/button>/,
  );
  assert.match(
    html,
    /<textarea id="gs-architect-custom-instructions"[\s\S]*?<\/textarea>\s*<div class="system-prompt-preview-row">\s*<button type="button" id="gs-architect-view-system-prompt"[^>]*>View system prompt<\/button>/,
  );
  assert.match(html, /id="modal-system-prompt-preview"[\s\S]*class="modal ui-modal ui-modal--lg ui-modal--tall ui-modal--structured preview-popup"/);
  assert.match(css, /body\.standalone-mode\s+\.preview-popup\s*{\s*max-width:\s*min\(80vw,\s*1180px\);/);
});

test('group settings markup renders board sync provider subtab and task sync mount', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();

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
  const css = appStylesheetSource();

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

test('group settings inheritance affordances omit redundant status captions', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/modals/settings-shell.js'),
    'utf8',
  );
  const css = appStylesheetSource();

  assert.doesNotMatch(source, /Inherited from Group/);
  assert.doesNotMatch(source, /Override active for this agent kind/);
  assert.doesNotMatch(source, /settings-inheritance-copy/);
  assert.match(source, /reset\.textContent = 'Use group default'/);
  assert.match(source, /note\.hidden = inherited/);
  assert.doesNotMatch(css, /\.settings-inheritance-note::before/);
});

test('settings footers place save state beneath the save action', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();

  assert.match(
    html,
    /class="settings-save-control"[\s\S]*id="gs-save-btn"[\s\S]*id="gs-save-state"/,
  );
  assert.match(
    html,
    /class="settings-save-control"[\s\S]*id="gls-global-save-btn"[\s\S]*id="gls-save-state"/,
  );
  assert.doesNotMatch(html, /id="gls-ai-save-btn"/);
  assert.doesNotMatch(html, /Save AI settings/);
  assert.doesNotMatch(html, /No unsaved changes/);
  assert.match(html, /id="gs-save-state"[^>]*hidden/);
  assert.match(html, /id="gls-save-state"[^>]*hidden/);
  assert.match(
    css,
    /\.settings-save-control\s*\{[^}]*flex-direction:\s*column[^}]*\}/,
  );
  assert.match(css, /\.settings-save-state\s*\{[^}]*text-align:\s*center[^}]*\}/);
});

test('Reset section creates an unsaved default draft without persisting it', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/modals/settings-shell.js'),
    'utf8',
  );
  const control = {
    id: 'field',
    tagName: 'INPUT',
    type: 'text',
    value: 'persisted',
    defaultValue: 'default',
    dataset: {},
    parentNode: null,
  };
  const saveState = { textContent: '', hidden: true };
  const saveButton = { disabled: true };
  const pane = {
    dataset: { pane: 'group' },
    querySelectorAll(selector) {
      return selector === 'input, select, textarea' ? [control] : [];
    },
  };
  const dialog = { classList: new FakeClassList() };
  const modal = {
    id: 'modal-group-settings',
    classList: new FakeClassList(),
    querySelector(selector) {
      if (selector === '.gs-pane.active') return pane;
      if (selector === '.settings-dialog') return dialog;
      if (selector === '.settings-save-state') return saveState;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'input, select, textarea') return [control];
      if (selector.includes('#gs-save-btn')) return [saveButton];
      return [];
    },
  };
  let persisted = 0;
  const context = vm.createContext({
    console,
    document: {
      documentElement: {
        dataset: {},
        style: { setProperty() {} },
      },
      getElementById(id) {
        return id === modal.id ? modal : null;
      },
      querySelectorAll() { return []; },
    },
    localStorage: {
      getItem() { return null; },
      setItem() { persisted += 1; },
    },
    send() { persisted += 1; },
  });

  vm.runInContext(source, context);
  vm.runInContext(
    `_settingsShellState['modal-group-settings'] = {
      baseline: { field: 'persisted' },
      dirty: false,
    };
    settingsShellResetSection('modal-group-settings');`,
    context,
  );

  assert.equal(control.value, 'default');
  assert.equal(saveState.textContent, 'Unsaved changes');
  assert.equal(saveState.hidden, false);
  assert.equal(saveButton.disabled, false);
  assert.equal(persisted, 0);
});

test('dynamic AI controls merge into the Global Settings dirty baseline', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/modals/settings-shell.js'),
    'utf8',
  );
  const profileControl = {
    id: 'gls-default-cmd',
    tagName: 'INPUT',
    type: 'text',
    value: 'codex',
  };
  const aiControl = {
    id: 'gls-ai-enabled',
    tagName: 'INPUT',
    type: 'checkbox',
    checked: false,
  };
  const aiRoot = {
    querySelectorAll(selector) {
      return selector === 'input, select, textarea' ? [aiControl] : [];
    },
  };
  const saveButton = { disabled: true };
  const saveState = { textContent: '', hidden: true };
  const dialog = { classList: new FakeClassList() };
  const modal = {
    id: 'modal-global-settings',
    classList: new FakeClassList(),
    querySelector(selector) {
      if (selector === '.settings-dialog') return dialog;
      if (selector === '.settings-save-state') return saveState;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'input, select, textarea') return [profileControl, aiControl];
      if (selector.includes('#gls-global-save-btn')) return [saveButton];
      return [];
    },
  };
  const context = vm.createContext({
    console,
    document: {
      getElementById(id) {
        return id === modal.id ? modal : null;
      },
    },
  });
  vm.runInContext(source, context);
  context.__modal = modal;
  context.__aiRoot = aiRoot;
  vm.runInContext(
    `_settingsShellState['modal-global-settings'] = {
      baseline: { 'gls-default-cmd': 'codex' },
      dirty: false,
    };
    settingsShellMergeBaseline(__modal, __aiRoot);`,
    context,
  );

  assert.equal(
    vm.runInContext(
      `settingsShellScopeDirty('modal-global-settings', __aiRoot)`,
      context,
    ),
    false,
  );
  assert.equal(saveButton.disabled, true);

  aiControl.checked = true;
  vm.runInContext(`settingsShellMarkDirty('modal-global-settings')`, context);
  assert.equal(
    vm.runInContext(
      `settingsShellScopeDirty('modal-global-settings', __aiRoot)`,
      context,
    ),
    true,
  );
  assert.equal(saveButton.disabled, false);
  assert.equal(saveState.textContent, 'Unsaved changes');
});

test('group settings renders structured worktree controls and merge-mode-specific history', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');

  assert.match(html, /Repository workflow/);
  assert.doesNotMatch(html, /Worker workspaces/);
  assert.match(html, /<label for="gs-worktree-mode">Workspace mode<\/label>[\s\S]*<select id="gs-worktree-mode"/);
  assert.match(html, /<option value="shared">Shared group checkout<\/option>/);
  assert.match(html, /<option value="isolated">Isolated worktree \(recommended\)<\/option>/);
  assert.match(html, /<label for="gs-wt-checkpoint-mode">Automatic checkpoints<\/label>/);
  assert.match(html, /<option value="manual">Manual only<\/option>/);
  assert.match(html, /<option value="stop" selected>On stop<\/option>/);
  assert.match(html, /<option value="progress-stop">On progress updates and stop<\/option>/);
  assert.match(html, /<label for="gs-engineer-merge-mode">Merge mode/);
  assert.match(html, /<option value="pr">Pull request \(default\)<\/option>/);
  assert.match(html, /<option value="direct">Direct local<\/option>/);
  assert.match(html, /<option value="engineer-choice">Engineer choice<\/option>/);
  assert.match(html, /id="gs-wt-direct-history-row"/);
  assert.match(html, /<option value="preserve" selected>Preserve commits<\/option>/);
  assert.match(html, /<option value="squash">Squash into one commit<\/option>/);
  assert.match(html, /workflow-breach audit events/);
  assert.doesNotMatch(html, /id="gs-wt-merge-instructions"/);
  assert.doesNotMatch(html, /id="gs-worktree" type="checkbox"/);
});

test('group settings renders opt-in auto-sweep cleanup mode', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');

  assert.match(html, /<div class="gs-settings-section-title">After merge<\/div>/);
  assert.match(html, /<label for="gs-wt-merge-cleanup">Default cleanup[\s\S]*<select id="gs-wt-merge-cleanup">/);
  assert.match(html, /<option value="keep">Keep worker and worktree \(default \/ warm\)<\/option>/);
  assert.match(html, /<option value="auto_sweep">Auto-sweep merged branch and worktree<\/option>/);
  assert.match(html, /Auto-sweep is opt-in and runs only after the branch is actually merged/);
});

test('worktree UI maps persisted flags to selectors and adapts merge guidance', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      git_worktree: true,
      worktree_auto_checkpoint: true,
      checkpoint_on_progress: true,
      worktree_merge_squash: true,
      engineer_merge_mode: "engineer-choice",
      worktree_symlinks: []
    },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-worktree-mode').value, 'isolated');
  assert.equal(ensure('gs-wt-checkpoint-mode').value, 'progress-stop');
  assert.equal(ensure('gs-wt-direct-history').value, 'squash');
  assert.equal(ensure('gs-wt-direct-history-row').hidden, false);
  assert.equal(ensure('gs-wt-direct-history-label').textContent, 'Direct-merge history');
  assert.match(ensure('gs-wt-merge-mode-help').textContent, /Pull request by default/);
  assert.equal(ensure('gs-worktree-mode-hint').textContent, 'Creates a separate branch and checkout.');

  ensure('gs-engineer-merge-mode').value = 'pr';
  vm.runInContext('_syncWorktreeMergeModeUi()', context);
  assert.equal(ensure('gs-wt-direct-history-row').hidden, true);
  assert.match(ensure('gs-wt-merge-mode-help').textContent, /requests a squash merge/);

  ensure('gs-worktree-mode').value = 'shared';
  vm.runInContext('_syncWorktreeSettingsUi()', context);
  assert.equal(ensure('gs-wt-dependent-settings').classList.contains('is-disabled'), true);
  assert.match(ensure('gs-worktree-mode-hint').textContent, /retained but inactive/);
});

test('worktree checkpoint selector preserves all four stored flag combinations', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  const cases = [
    ['manual', false, false],
    ['stop', true, false],
    ['progress', false, true],
    ['progress-stop', true, true],
  ];
  for (const [mode, autoCheckpoint, checkpointOnProgress] of cases) {
    assert.equal(
      vm.runInContext(
        `_worktreeCheckpointMode(${autoCheckpoint}, ${checkpointOnProgress})`,
        context,
      ),
      mode,
    );
    ensure('gs-wt-checkpoint-mode').value = mode;
    const flags = JSON.parse(vm.runInContext('JSON.stringify(_worktreeCheckpointFlags())', context));
    assert.deepEqual(flags, { autoCheckpoint, checkpointOnProgress });
  }
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
    ['', 'low', 'medium', 'high', 'xhigh', '__custom__'],
  );

  ensure('gs-agent-provider').value = 'claude-code';
  vm.runInContext('onGsProviderChange()', context);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', 'xhigh', 'max', '__custom__'],
  );

  ensure('gs-agent-provider').value = 'gemini-cli';
  vm.runInContext('onGsProviderChange()', context);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', '__custom__'],
  );
  assert.equal(
    ensure('gs-agent-reasoning-effort').children[0].textContent,
    'Not supported for this provider',
  );
});

test('Codex model controls use detected choices with editable fallbacks last', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, [
    {
      name: 'codex',
      display_name: 'Codex',
      command: 'codex',
      reasoning_efforts: ['low', 'medium', 'high', 'xhigh'],
      models: [
        {
          id: 'gpt-5.6-sol',
          display_name: 'GPT-5.6-Sol',
          is_default: true,
          default_reasoning_effort: 'low',
          reasoning_efforts: [
            { value: 'low', description: 'Fast' },
            { value: 'high', description: 'Deep' },
          ],
        },
        {
          id: 'gpt-5.6-terra',
          display_name: 'GPT-5.6-Terra',
          default_reasoning_effort: 'medium',
          reasoning_efforts: [
            { value: 'medium', description: 'Balanced' },
            { value: 'xhigh', description: 'Extra high' },
          ],
        },
      ],
    },
  ]);

  ensure('gs-agent-provider').value = 'codex';
  ensure('gs-agent-model').value = 'gpt-5.6-terra';
  vm.runInContext(`onGsProviderChange('xhigh')`, context);

  assert.deepEqual(
    ensure('gs-agent-model-select').children.map((child) => child.value),
    ['', 'gpt-5.6-sol', 'gpt-5.6-terra', '__custom__'],
  );
  assert.equal(
    ensure('gs-agent-model-select').children[1].textContent,
    'GPT-5.6-Sol (default)',
  );
  assert.equal(ensure('gs-agent-model-select').value, 'gpt-5.6-terra');
  assert.equal(ensure('gs-agent-model').classList.contains('hidden'), true);
  assert.deepEqual(
    ensure('gs-agent-reasoning-effort').children.map((child) => child.value),
    ['', 'medium', 'xhigh', '__custom__'],
  );
  assert.equal(
    ensure('gs-agent-reasoning-effort').children[0].textContent,
    'Model default (medium)',
  );

  ensure('gs-agent-model-select').value = '__custom__';
  vm.runInContext(`_onModelSelectChange('gs-agent-model')`, context);
  ensure('gs-agent-model').value = 'future-codex-model';
  vm.runInContext(`_onCustomModelInput('gs-agent-model')`, context);
  ensure('gs-agent-reasoning-effort').value = '__custom__';
  vm.runInContext(`_onReasoningEffortSelectChange('gs-agent-reasoning-effort')`, context);
  ensure('gs-agent-reasoning-effort-custom').value = 'ultra';

  assert.equal(ensure('gs-agent-model').classList.contains('hidden'), false);
  assert.equal(vm.runInContext(`_getModelValue('gs-agent-model')`, context), 'future-codex-model');
  assert.equal(
    vm.runInContext(`_getReasoningEffortValue('gs-agent-reasoning-effort')`, context),
    'ultra',
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
      { value: '', text: 'Inherit · Codex' },
      { value: 'codex', text: 'Codex' },
      { value: 'claude-code', text: 'Claude Code' },
    ],
  );
  assert.equal(ensure('gs-worker-model').value, '');
  assert.equal(ensure('gs-worker-model-select').children[0].textContent, 'Inherit · gpt-5');
  assert.equal(ensure('gs-worker-boot-command').value, '');
  assert.equal(ensure('gs-worker-boot-command').placeholder, 'Inherit · codex --sandbox');
  assert.deepEqual(
    ensure('gs-worker-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', '__custom__'],
  );
  assert.equal(ensure('gs-worker-reasoning-effort').value, 'high');
  assert.equal(ensure('gs-engineer-model-select').children[0].textContent, 'Inherit · gpt-5');
  assert.equal(ensure('gs-architect-model-select').children[0].textContent, 'Inherit · gpt-5');

  ensure('gs-agent-boot-cmd').value = '';
  ensure('gs-agent-model').value = '';
  ensure('gs-worker-provider').value = 'claude-code';
  vm.runInContext('onGsWorkerProviderChange()', context);
  assert.equal(ensure('gs-worker-model-select').children[0].textContent, 'Inherit · provider default');
  assert.equal(ensure('gs-worker-boot-command').placeholder, 'Inherit · claude');
  assert.deepEqual(
    ensure('gs-worker-reasoning-effort').children.map((child) => child.value),
    ['', 'low', 'medium', 'high', 'max', '__custom__'],
  );
  assert.equal(
    ensure('gs-worker-reasoning-effort').children[0].textContent,
    'Inherit · provider default',
  );
});

test('General panes expose resolved inheritance inside launch and runtime controls', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, [
    {
      name: 'codex',
      display_name: 'Codex',
      command: 'codex',
      models: [{
        id: 'gpt-5',
        display_name: 'GPT-5',
        is_default: true,
        reasoning_efforts: ['low', 'medium', 'high'],
        default_reasoning_effort: 'medium',
      }],
      reasoning_efforts: ['low', 'medium', 'high'],
    },
  ]);
  ['gs-agent-shell', 'gs-engineer-shell', 'gs-architect-shell'].forEach((id) => {
    const option = new FakeElement('option');
    option.value = '';
    ensure(id).appendChild(option);
  });

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      default_directory: "/repo",
      shell: "zsh",
      env_file: ".env.local",
      agent_directory: "/repo/agents",
      agent_shell: "bash",
      agent_provider: "codex",
      agent_model: "gpt-5",
      agent_reasoning_effort: "high"
    },
    engineer_settings: {},
    architect_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(ensure('gs-worker-provider').children[0].textContent, 'Inherit · Codex');
  assert.equal(ensure('gs-engineer-model-select').children[0].textContent, 'Inherit · GPT-5');
  assert.equal(ensure('gs-architect-reasoning-effort').children[0].textContent, 'Inherit · high');
  assert.equal(ensure('gs-worker-boot-command').placeholder, 'Inherit · codex');
  assert.equal(ensure('gs-agent-directory').placeholder, 'Inherit · /repo');
  assert.equal(ensure('gs-engineer-directory').placeholder, 'Inherit · /repo/agents');
  assert.equal(ensure('gs-architect-directory').placeholder, 'Inherit · /repo/agents');
  assert.equal(ensure('gs-agent-env-file').placeholder, 'Inherit · .env.local');
  assert.equal(ensure('gs-agent-shell').children[0].textContent, 'Inherit · zsh');
  assert.equal(ensure('gs-engineer-shell').children[0].textContent, 'Inherit · bash');
  assert.equal(ensure('gs-architect-shell').children[0].textContent, 'Inherit · bash');

  ensure('gs-agent-reasoning-effort').value = 'medium';
  vm.runInContext('refreshGsInheritedLaunchPlaceholders()', context);
  assert.equal(ensure('gs-worker-reasoning-effort').children[0].textContent, 'Inherit · medium');
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
      engineer_behavior_requires_user_approval: true,
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
    ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', '__custom__'],
  );
  assert.equal(ensure('gs-engineer-model').value, 'gpt-5.1-codex');
  assert.equal(ensure('gs-engineer-reasoning-effort').value, 'xhigh');
  assert.equal(ensure('gs-engineer-directory').value, '/repo/.torque/engineer');
  assert.equal(ensure('gs-engineer-shell').value, 'fish');
  assert.deepEqual(
    ensure('gs-engineer-reasoning-effort').children.map((child) => child.value),
    ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', '__custom__'],
  );
  assert.equal(ensure('gs-engineer-custom-instructions').value, 'Watch for regressions.');
  assert.equal(ensure('gs-engineer-restrict-to-created-agents').checked, false);
  assert.equal(ensure('gs-engineer-can-override-worker-provider').checked, false);
  assert.equal(ensure('gs-engineer-autonomy-mode').value, 'aggressive_auto_continue');
  assert.equal(ensure('gs-engineer-default-worker-concurrency').value, '4');
  assert.equal(ensure('gs-engineer-wave-size-preference').value, 'large');
  assert.equal(ensure('gs-engineer-same-agent-follow-up-preference').value, 'prefer_same_agent');
  assert.equal(ensure('gs-engineer-behavior-requires-user-approval').checked, true);
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
  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals/group-settings.js'), 'utf8');
  const topStrip = html.slice(
    html.indexOf('<div class="gs-tabs settings-primary-nav" role="tablist" aria-label="Group settings sections"'),
    html.indexOf('    <!-- Group tab -->'),
  );
  const topTabs = Array.from(topStrip.matchAll(/data-tab="([^"]+)"/g), (match) => match[1]);
  assert.deepEqual(topTabs, ['group', 'workers', 'engineer', 'architect']);
  assert.doesNotMatch(topStrip, />Agents<\/button>/);
  assert.match(topStrip, /<span>Workers<\/span>/);
  assert.match(topStrip, /<span>Engineers<\/span>/);
  assert.match(topStrip, /<span>Architects<\/span>/);
  assert.doesNotMatch(topStrip, />Engineer<\/button>/);
  assert.doesNotMatch(topStrip, />Architect<\/button>/);

  assert.match(
    html,
    /<div class="gs-pane active" data-pane="group">[\s\S]*?data-subtab="group-general"[\s\S]*?data-subtab="group-worker-defaults"[\s\S]*?data-subtab="group-sync"[\s\S]*?data-subtab="group-advanced"/,
  );
  assert.match(html, /data-subtab="group-worker-defaults"[^>]*>Agents<\/button>/);
  assert.doesNotMatch(html, /data-subtab="group-worker-defaults"[^>]*>Worker defaults<\/button>/);
  assert.match(html, /data-subpane="group-general"/);
  assert.match(html, /data-subpane="group-worker-defaults"/);
  assert.doesNotMatch(html, /data-subtab="group-terminals"/);
  assert.doesNotMatch(html, /data-subpane="group-terminals"/);
  assert.doesNotMatch(html, /id="gs-auto-terminals"/);
  assert.doesNotMatch(html, /id="gs-terminal-prefix"/);
  assert.match(html, /data-subpane="group-advanced"/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="workers">[\s\S]*?data-subtab="worker-execution"[\s\S]*?data-subtab="worker-worktree"[\s\S]*?data-subtab="worker-notifications"/,
  );
  assert.match(html, /data-subtab="worker-execution"[^>]*>General<\/button>/);
  assert.doesNotMatch(html, /data-subtab="worker-behavior"/);
  assert.doesNotMatch(html, /data-subtab="worker-execution"[^>]*>Execution<\/button>/);
  assert.match(html, /data-subpane="worker-execution"/);
  assert.doesNotMatch(html, /data-subpane="worker-behavior"/);
  assert.doesNotMatch(html, /id="gs-worker-role-behavior-overlay"/);
  assert.match(html, /data-subpane="worker-worktree"/);
  assert.match(html, /data-subpane="worker-notifications"/);
  assert.doesNotMatch(html, /data-pane="agents"/);
  assert.doesNotMatch(html, /data-subtab="agent-terminals"/);
  assert.doesNotMatch(modals, /renderBehaviorOverlayRolePane\(_settingsGroup/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="engineer">[\s\S]*?data-subtab="engineer-general"[\s\S]*?data-subtab="engineer-behavior"[\s\S]*?data-subtab="engineer-system"[\s\S]*?>System<\/button>/,
  );
  assert.match(html, /data-subpane="engineer-general"/);
  assert.match(html, /data-subpane="engineer-behavior"/);
  assert.doesNotMatch(html, /id="gs-engineer-role-behavior-overlay"/);
  assert.match(html, /data-subpane="engineer-system"/);
  assert.doesNotMatch(html, /<details[^>]+id="gs-engineer-[^\"]+-section"/);

  assert.match(
    html,
    /<div class="gs-pane" data-pane="architect">[\s\S]*?data-subtab="architect-general"[\s\S]*?data-subtab="architect-behavior"[\s\S]*?data-subtab="architect-system"[\s\S]*?>System<\/button>/,
  );
  assert.match(html, /data-subpane="architect-general"/);
  assert.match(html, /data-subpane="architect-behavior"/);
  assert.doesNotMatch(html, /id="gs-architect-role-behavior-overlay"/);
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
  assert.ok(architectGeneral < html.indexOf('id="gs-architect-directory"'));
  assert.ok(html.indexOf('id="gs-architect-directory"') < architectBehavior);
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
  const sync = groupPane.indexOf('data-subpane="group-sync"');
  const advanced = groupPane.indexOf('data-subpane="group-advanced"');
  assert.ok(workerDefaults < groupPane.indexOf('id="gs-agent-provider"'));
  assert.ok(workerDefaults < groupPane.indexOf('id="gs-agent-model"'));
  assert.ok(workerDefaults < sync);
  assert.ok(sync < advanced);
  assert.doesNotMatch(groupPane, /data-subpane="group-terminals"/);
  assert.doesNotMatch(groupPane, /id="gs-auto-terminals"/);
  assert.doesNotMatch(groupPane, /id="gs-terminal-prefix"/);
  assert.match(groupPane, /gs-settings-section-title">Shared launch defaults/);
  assert.match(groupPane, /<label for="gs-agent-provider">Provider<\/label>/);
  assert.doesNotMatch(groupPane, /id="gs-default-agent-template"/);
  assert.match(groupPane, /<label for="gs-agent-model-select">Model<\/label>/);
  assert.match(groupPane, /settings-field-grid settings-field-grid--three/);
  assert.match(groupPane, /Used by every agent kind unless its own General settings override them/);
  assert.match(groupPane, /settings-field--secondary[\s\S]*id="gs-agent-boot-cmd"/);
  assert.doesNotMatch(groupPane, /Default worker provider/);
  assert.doesNotMatch(groupPane, /Default worker model/);

  assert.match(workersPane, /data-subpane="worker-execution"/);
  assert.match(workersPane, /<label for="gs-default-agent-template">Default role<\/label>/);
  assert.match(workersPane, /id="gs-default-agent-template"/);
  assert.match(workersPane, /id="gs-worker-provider"/);
  assert.match(workersPane, /id="gs-worker-boot-command"/);
  assert.match(workersPane, /id="gs-worker-model"/);
  assert.match(workersPane, /id="gs-worker-reasoning-effort"/);
  assert.match(workersPane, /id="gs-agent-directory"/);
  assert.match(workersPane, /id="gs-session-resume"/);
  assert.match(workersPane, /id="gs-agent-idle-timeout"/);
  assert.match(workersPane, /id="gs-worktree-mode"/);
  assert.match(workersPane, /id="gs-wt-checkpoint-mode"/);
  assert.match(workersPane, /id="gs-wt-dependent-settings"/);
  assert.match(workersPane, /id="gs-notifications"/);
  assert.match(workersPane, /Deliver to macOS/);
  assert.doesNotMatch(workersPane, /Group → Worker defaults/);
  assert.doesNotMatch(workersPane, /id="gs-agent-provider"/);
  assert.doesNotMatch(workersPane, /id="gs-agent-model"/);
  assert.doesNotMatch(workersPane, /id="gs-terminal-prefix"/);
});

test('all General panes use shared section cards and responsive field grids', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();
  const groupStart = html.indexOf('data-subpane="group-general"');
  const groupEnd = html.indexOf('data-subpane="group-worker-defaults"');
  const workerStart = html.indexOf('data-subpane="worker-execution"');
  const workerEnd = html.indexOf('data-subpane="worker-worktree"');
  const engineerStart = html.indexOf('data-subpane="engineer-general"');
  const engineerEnd = html.indexOf('data-subpane="engineer-behavior"');
  const architectStart = html.indexOf('data-subpane="architect-general"');
  const architectEnd = html.indexOf('data-subpane="architect-behavior"');
  const group = html.slice(groupStart, groupEnd);
  const worker = html.slice(workerStart, workerEnd);
  const engineer = html.slice(engineerStart, engineerEnd);
  const architect = html.slice(architectStart, architectEnd);

  assert.match(group, /gs-settings-section-title">Workspace/);
  assert.match(group, /gs-settings-section-title">Environment/);
  assert.match(group, /gs-settings-section-title">Limits &amp; visibility/);
  assert.match(group, /Agent limit/);
  assert.doesNotMatch(group, /Max workers/);
  assert.match(worker, /gs-settings-section-title">Launch/);
  assert.match(worker, /gs-settings-section-title">Runtime/);
  assert.match(worker, /gs-settings-section-title">Session/);

  for (const pane of [engineer, architect]) {
    assert.match(pane, /gs-settings-section-title">Launch/);
    assert.match(pane, /gs-settings-section-title">Runtime/);
    assert.match(pane, /settings-field--secondary[\s\S]*Command override/);
    assert.doesNotMatch(pane, /Terminal overrides|designated Engineer|Architect agents only/);
  }

  assert.match(css, /\.settings-field-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(css, /\.settings-field-grid--three\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(css, /\.settings-field--wide\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;/);
  assert.match(css, /\.settings-field--compact\s*\{[^}]*max-width:\s*360px;/);
  assert.match(css, /\.gs-settings-section-body\s*\{\s*padding:\s*10px;\s*\}/);
  assert.match(css, /@media \(max-width: 760px\)\s*\{[\s\S]*?\.settings-field-grid\s*\{[^}]*grid-template-columns:\s*1fr;/);
});

test('remaining settings panes use the shared section hierarchy and concise labels', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();
  const pane = (start, end) => html.slice(html.indexOf(`data-subpane="${start}"`), html.indexOf(`data-subpane="${end}"`));
  const agents = pane('group-worker-defaults', 'group-sync');
  const sync = pane('group-sync', 'group-advanced');
  const advanced = pane('group-advanced', 'worker-execution');
  const notifications = pane('worker-notifications', 'engineer-general');
  const engineerBehavior = pane('engineer-behavior', 'engineer-system');
  const architectBehavior = pane('architect-behavior', 'architect-system');

  assert.match(agents, /gs-settings-section-title">Shared launch defaults/);
  assert.doesNotMatch(agents, />Default (provider|role|model|reasoning effort)</i);
  for (const title of ['Connection', 'Repository &amp; project', 'Board mapping', 'Issue behavior', 'Assignees']) {
    assert.match(sync, new RegExp(`gs-settings-section-title">${title}`));
  }
  assert.match(advanced, /gs-settings-section-title">Guidance/);
  assert.match(html, /class="gs-subpane gs-subpane--compact" data-subpane="group-advanced"/);
  assert.match(advanced, /settings-field settings-field--compact/);
  assert.match(notifications, /gs-settings-section-title">Desktop delivery/);
  assert.match(notifications, /gs-settings-section-title">Events/);
  for (const title of ['Specializations', 'Orchestration', 'Communication', 'Policy overrides']) {
    assert.match(engineerBehavior, new RegExp(`gs-settings-section-title">${title}`));
  }
  for (const title of ['Orchestration', 'Continuity', 'Instructions']) {
    assert.match(architectBehavior, new RegExp(`gs-settings-section-title">${title}`));
  }
  assert.match(css, /\.settings-field-help\s*\{/);
  assert.match(css, /\.settings-event-badges\s*\{/);
});

test('worker Inbox event choices stay editable when desktop delivery is off', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  const events = ensure('gs-worker-notification-events');
  const finish = ensure('gs-notify-finish');
  const error = ensure('gs-notify-error');
  const attention = ensure('gs-notify-attention');
  events._controls = [finish, error, attention];

  ensure('gs-notifications').checked = false;
  vm.runInContext('_syncWorkerNotificationSettingsUi()', context);
  assert.equal(events.classList.contains('is-disabled'), false);
  assert.equal(finish.disabled, false);
  assert.equal(error.disabled, false);
  assert.equal(attention.disabled, false);

  ensure('gs-notifications').checked = true;
  vm.runInContext('_syncWorkerNotificationSettingsUi()', context);
  assert.equal(events.classList.contains('is-disabled'), false);
  assert.equal(finish.disabled, false);
  assert.equal(error.disabled, false);
  assert.equal(attention.disabled, false);
});

test('engineer System groups permissions, digest delivery, and human-readable events', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals/group-settings.js'), 'utf8');
  const engineerStart = html.indexOf('<div class="gs-pane" data-pane="engineer">');
  const architectStart = html.indexOf('<div class="gs-pane" data-pane="architect">');
  const engineerPane = html.slice(engineerStart, architectStart);
  const general = engineerPane.indexOf('data-subpane="engineer-general"');
  const behavior = engineerPane.indexOf('data-subpane="engineer-behavior"');
  const system = engineerPane.indexOf('data-subpane="engineer-system"');
  const permissions = engineerPane.indexOf('gs-settings-section-title">Permissions');
  const digestDelivery = engineerPane.indexOf('gs-settings-section-title">Digest delivery');
  const events = engineerPane.indexOf('gs-settings-section-title">Events');

  assert.notEqual(system, -1);
  assert.ok(general < behavior);
  assert.ok(behavior < system);
  assert.ok(system < permissions);
  assert.ok(permissions < digestDelivery);
  assert.ok(digestDelivery < events);
  assert.equal(engineerPane.indexOf('>Digests</button>'), -1);
  assert.match(engineerPane, /data-subtab="engineer-system"[\s\S]*>System<\/button>/);
  assert.match(engineerPane, /See other Engineers' workers/);
  assert.match(engineerPane, /Choose providers for created workers/);
  assert.ok(permissions < engineerPane.indexOf('id="gs-engineer-restrict-to-created-agents"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-can-override-worker-provider"') < digestDelivery);
  assert.ok(digestDelivery < engineerPane.indexOf('id="gs-engineer-digest-verbosity"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-digest-verbosity"') < engineerPane.indexOf('id="gs-engineer-push-interval"'));
  assert.match(engineerPane, /settings-event-badges[\s\S]*Task completed[\s\S]*Question created/);
  assert.match(engineerPane, /id="gs-engineer-event-agent-started"[^>]*> Agent started/);
  assert.doesNotMatch(engineerPane, />\s*task_completed\s*</);
  assert.ok(behavior < engineerPane.indexOf('id="gs-engineer-notification-preset"'));
  assert.ok(engineerPane.indexOf('id="gs-engineer-notification-preset"') < system);
  assert.match(engineerPane, /id="gs-engineer-digest-verbosity-hint" class="hint-btn"/);
  assert.match(modals, /const DIGEST_VERBOSITY_TOOLTIP_HELP = 'Controls how much detail appears in digest events sent to this agent\. Higher verbosity can wake the agent more often on coarse-event activity in the group\.'/);
  assert.match(modals, /gs-engineer-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP/);
  assert.match(modals, /gs-architect-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP/);
});

test('architect General owns runtime while System separates digest delivery and events', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const architectStart = html.indexOf('<div class="gs-pane" data-pane="architect">');
  const footerStart = html.indexOf('<div class="modal-actions ui-modal__footer">', architectStart);
  const architectPane = html.slice(architectStart, footerStart);
  const general = architectPane.indexOf('data-subpane="architect-general"');
  const behavior = architectPane.indexOf('data-subpane="architect-behavior"');
  const system = architectPane.indexOf('data-subpane="architect-system"');
  const runtime = architectPane.indexOf('gs-settings-section-title">Runtime');
  const digestDelivery = architectPane.indexOf('gs-settings-section-title">Digest delivery');
  const events = architectPane.indexOf('gs-settings-section-title">Events');

  assert.notEqual(system, -1);
  assert.ok(general < behavior);
  assert.ok(behavior < system);
  assert.ok(general < runtime);
  assert.ok(runtime < behavior);
  assert.ok(system < digestDelivery);
  assert.ok(digestDelivery < events);
  assert.equal(architectPane.indexOf('>Digests</button>'), -1);
  assert.match(architectPane, /data-subtab="architect-system"[\s\S]*>System<\/button>/);
  assert.ok(runtime < architectPane.indexOf('id="gs-architect-directory"'));
  assert.ok(architectPane.indexOf('id="gs-architect-directory"') < architectPane.indexOf('id="gs-architect-shell"'));
  assert.ok(architectPane.indexOf('id="gs-architect-shell"') < behavior);
  assert.equal(architectPane.indexOf('Terminal overrides'), -1);
  assert.equal(architectPane.indexOf('id="gs-architect-profile"'), -1);
  assert.equal(architectPane.indexOf('id="gs-architect-color-swatches"'), -1);
  assert.ok(digestDelivery < architectPane.indexOf('id="gs-architect-digest-verbosity"'));
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
  const groupTabs = fs.readFileSync(path.join(repoRoot, 'static/js/grid/group-tabs.js'), 'utf8');
  const gridMain = fs.readFileSync(path.join(repoRoot, 'static/js/grid/main.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals/group-settings.js'), 'utf8');

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
  assert.match(advancedPane, /gs-settings-section-title">Guidance/);
  assert.match(advancedPane, />Hint cadence<\/label>/);
  assert.match(
    advancedPane,
    /<input id="gs-guidance-hint-cadence" type="number" min="0" max="100" step="1" value="4">/,
  );
  assert.match(advancedPane, /Show recurring guidance on the first applicable message, then every N messages/);
  assert.match(advancedPane, /Use 0 for every message/);
  assert.match(advancedPane, /Delete group/);
  assert.match(advancedPane, /class="btn-danger"/);
  assert.match(advancedPane, /deleteSettingsGroup\(\)/);
  assert.match(modals, /async function deleteSettingsGroup\(\)[\s\S]*removeGroup\(group\)[\s\S]*closeModals\(\)/);

  assert.doesNotMatch(commands, /function\s+onGroupContextMenu\b/);
  assert.doesNotMatch(render, /oncontextmenu="onGroupContextMenu/);
  assert.doesNotMatch(render, /title="Delete group"/);
  assert.match(gridMain, /title="Group settings"[^`]*\\u2699/);
  assert.match(groupTabs, /agent-group-tab-settings[\s\S]*openGroupSettings\([\s\S]*&#9881;/);
  assert.doesNotMatch(groupTabs, /openAgentGroupTabActions|agent-group-tab-menu|&#8943;/);
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

test('group settings sub-tab CSS remains reusable in narrow embedded layouts', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();

  assert.match(html, /<div class="gs-pane active" data-pane="group">\s*<div class="gs-subtabs ui-tablist" role="tablist"/);
  assert.match(html, /<div class="gs-pane" data-pane="workers">\s*<div class="gs-subtabs ui-tablist" role="tablist"/);
  assert.match(html, /<div class="gs-pane" data-pane="engineer">\s*<div class="gs-subtabs ui-tablist" role="tablist"/);
  assert.match(html, /<div class="gs-pane" data-pane="architect">\s*<div class="gs-subtabs ui-tablist" role="tablist"/);
  assert.match(css, /\.gs-subtabs\s*\{[^}]*display:\s*flex;[^}]*border-bottom:\s*1px solid var\(--border\);/s);
  assert.match(css, /\.gs-subpane\s*\{\s*display:\s*none;\s*\}/);
  assert.match(css, /\.gs-subpane\.active\s*\{\s*display:\s*block;\s*\}/);
});

test('group settings removes terminal-backend-only profile and tab-color controls', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = appStylesheetSource();
  const ws = fs.readFileSync(path.join(repoRoot, 'static/js/ws/full-state.js'), 'utf8');
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
    'gs-auto-terminals',
    'gs-terminal-prefix',
    'gs-terminal-boot-cmd',
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
  assert.match(behaviorPane, /gs-settings-section-title">Orchestration/);
  assert.match(behaviorPane, /gs-settings-section-title">Continuity/);
  assert.match(behaviorPane, /gs-settings-section-title">Instructions/);
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
  ensure('gs-worktree-mode').value = 'isolated';
  ensure('gs-wt-checkpoint-mode').value = 'progress';
  ensure('gs-wt-direct-history').value = 'squash';
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
  ensure('gs-engineer-behavior-requires-user-approval').checked = true;
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
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'agent_terminal_profile'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'agent_tab_color'), false);
  assert.equal(sandbox.sendCalls[0].settings.default_agent_template, 'careful-reviewer');
  assert.equal(sandbox.sendCalls[0].settings.agent_provider, 'codex');
  assert.equal(sandbox.sendCalls[0].settings.agent_boot_command, 'codex --worker');
  assert.equal(sandbox.sendCalls[0].settings.worker_provider, 'codex');
  assert.equal(sandbox.sendCalls[0].settings.worker_boot_command, 'codex --worker-kind');
  assert.equal(sandbox.sendCalls[0].settings.worker_model, 'gpt-5-worker');
  assert.equal(sandbox.sendCalls[0].settings.worker_reasoning_effort, 'high');
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'auto_terminals'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'terminal_name_prefix'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'terminal_boot_command'), false);
  assert.equal(sandbox.sendCalls[0].settings.git_worktree, true);
  assert.equal(sandbox.sendCalls[0].settings.worktree_auto_checkpoint, false);
  assert.equal(sandbox.sendCalls[0].settings.checkpoint_on_progress, true);
  assert.equal(sandbox.sendCalls[0].settings.worktree_merge_squash, true);
  assert.equal(Object.prototype.hasOwnProperty.call(sandbox.sendCalls[0].settings, 'worktree_merge_instructions'), false);
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
  assert.equal(sandbox.sendCalls[0].settings.engineer_behavior_requires_user_approval, true);
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
  const modalJs = fs.readFileSync(path.join(repoRoot, 'static/js/modals/group-settings.js'), 'utf8');
  const legacyCopy = new RegExp('No engineer' + ' agent');
  assert.doesNotMatch(html, legacyCopy);
  assert.doesNotMatch(modalJs, legacyCopy);
  assert.doesNotMatch(html, /Hide other engineers' workers from this engineer/);
  assert.match(html, /See other Engineers' workers/);
  assert.match(html, /Operators always see every worker/);
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

test('Group Settings defaults GitHub missing-label creation on when unset', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext(`_showGroupSettings("alpha", {
    settings: {
      board_sync_provider: "github",
      board_sync_enabled: true,
      board_sync_github: {
        github_repo: "acme/widgets"
      }
    },
    engineer_settings: {},
    profiles: ["Default"]
  })`, context);

  assert.equal(sandbox.document.getElementById('gs-board-sync-github-create-labels').checked, true);
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
  assert.match(html, /Symlink all gitignored paths/);
  assert.match(
    html,
    /This can expose \.env files, credentials, caches, and dependencies\.[\s\S]*Changes through these symlinks modify the original files\./,
  );

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

test('Codex Fast controls preserve three-state values and hide for other providers', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);
  seedProviders(context, [
    { name: 'codex', display_name: 'Codex', command: 'codex', reasoning_efforts: [] },
    { name: 'claude-code', display_name: 'Claude Code', command: 'claude', reasoning_efforts: [] },
  ]);
  vm.runInContext(`_showGroupSettings('alpha', {
    settings: { agent_provider: 'codex', agent_fast_mode: 'on', worker_fast_mode: 'off' },
    engineer_settings: { engineer_provider: 'codex', engineer_fast_mode: 'on' },
    architect_settings: { architect_provider: 'claude-code', architect_fast_mode: 'off' },
    profiles: ['Default']
  })`, context);
  assert.equal(ensure('gs-agent-fast-mode').value, 'on');
  assert.equal(ensure('gs-worker-fast-mode').value, 'off');
  assert.equal(ensure('gs-engineer-fast-mode').value, 'on');
  assert.equal(ensure('gs-agent-fast-mode-row').classList.contains('hidden'), false);
  assert.equal(ensure('gs-architect-fast-mode-row').classList.contains('hidden'), true);
  ensure('gs-agent-provider').value = 'claude-code';
  vm.runInContext('onGsProviderChange()', context);
  assert.equal(ensure('gs-agent-fast-mode-row').classList.contains('hidden'), true);
});

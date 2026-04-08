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

test('weaver settings render group-settings handoff', () => {
  const sandbox = createSandbox();
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  const html = vm.runInContext(
    `_weaverRenderSettings("alpha", {
      weaver_provider: "codex",
      weaver_boot_command: "codex --model gpt-5",
      custom_instructions: "Watch for regressions."
    }, { name: "Weaver", status: "running" })`,
    context,
  );

  assert.match(html, /Create a Weaver from the group’s \+ New dropdown\. Configure it in Group Settings → Weaver\./);
  assert.match(html, /Open Group Settings/);
  assert.match(html, /Provider override/);
  assert.match(html, /codex/);
  assert.match(html, /Watch for regressions\./);
});

test('weaverOpenSettings opens group settings on the weaver tab', () => {
  const sandbox = createSandbox();
  sandbox.openGroupSettingsCalls = [];
  sandbox.openGroupSettings = function(group, tab) {
    sandbox.openGroupSettingsCalls.push({ group, tab });
  };
  const context = vm.createContext(sandbox);
  loadWeaver(context);

  vm.runInContext(`weaverOpenSettings()`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.openGroupSettingsCalls)), [
    {
      group: 'alpha',
      tab: 'weaver',
    },
  ]);
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
  vm.runInContext(`_weaverTab = 'settings'`, context);

  vm.runInContext('renderWeaverPanel()', context);

  assert.match(panel.innerHTML, /Weaver — beta/);
  assert.match(panel.innerHTML, /No weaver agent/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function createSandbox() {
  const sandbox = {
    console,
    Date: { now: () => Date.now() },
    state: {
      groups: { loom: [] },
      group_settings: { loom: {} },
      agents: {},
      board_tasks: {},
      children: {},
      weaver_settings: {},
    },
    document: {
      activeElement: null,
      getElementById() { return null; },
    },
    window: {
      prompt() { return null; },
    },
    _cachedProviders: [],
    _esc(value) { return String(value); },
    _currentGroup() { return 'loom'; },
    _captureSurfaceState() { return null; },
    _restoreSurfaceState() {},
    setInterval() { return 1; },
    clearInterval() {},
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return sandbox;
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function createHarness() {
  const sandbox = createSandbox();
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  sandbox.document.getElementById = function(id) {
    return id === 'panel-agent' ? panel : null;
  };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/weaver.js');
  return { context, panel, sandbox };
}

function engineer(id, name, createdAt) {
  return {
    id,
    name,
    slug: id,
    kind: 'engineer',
    group: 'loom',
    cell_type: 'agent',
    status: 'running',
    created_at: createdAt,
  };
}

test('renderWeaverPanel shows the Engineers header and empty roster when there are zero engineers', () => {
  const { context, panel } = createHarness();

  vm.runInContext('renderWeaverPanel()', context);

  assert.match(panel.innerHTML, /Architects &amp; Engineers — loom/);
  assert.match(panel.innerHTML, />\+ Add Architect</);
  assert.match(panel.innerHTML, />\+ Add Engineer</);
  assert.match(panel.innerHTML, /No user-owned engineers yet/);
});

test('renderWeaverPanel lists one engineer in the roster', () => {
  const { context, panel, sandbox } = createHarness();
  sandbox.state.agents['eng-alice'] = engineer('eng-alice', 'Alice', 10);

  vm.runInContext('renderWeaverPanel()', context);

  assert.equal((panel.innerHTML.match(/engineer-row-kind">engineer</g) || []).length, 1);
  assert.match(panel.innerHTML, /User/);
  assert.match(panel.innerHTML, /Alice/);
  assert.match(panel.innerHTML, /eng-alice/);
});

test('renderWeaverPanel lists two engineers in created_at order', () => {
  const { context, panel, sandbox } = createHarness();
  sandbox.state.agents['eng-bob'] = engineer('eng-bob', 'Bob', 20);
  sandbox.state.agents['eng-alice'] = engineer('eng-alice', 'Alice', 10);

  vm.runInContext('renderWeaverPanel()', context);

  assert.equal((panel.innerHTML.match(/engineer-row-kind">engineer</g) || []).length, 2);
  assert.match(panel.innerHTML, /Alice[\s\S]*Bob/);
});

test('renderWeaverPanel lists three engineers in created_at order', () => {
  const { context, panel, sandbox } = createHarness();
  sandbox.state.agents['eng-cara'] = engineer('eng-cara', 'Cara', 30);
  sandbox.state.agents['eng-bob'] = engineer('eng-bob', 'Bob', 20);
  sandbox.state.agents['eng-alice'] = engineer('eng-alice', 'Alice', 10);

  vm.runInContext('renderWeaverPanel()', context);

  assert.equal((panel.innerHTML.match(/engineer-row-kind">engineer</g) || []).length, 3);
  assert.match(panel.innerHTML, /Alice[\s\S]*Bob[\s\S]*Cara/);
});

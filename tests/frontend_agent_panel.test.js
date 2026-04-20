const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function createHarness() {
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  const captureCalls = [];
  const restoreCalls = [];
  const sandbox = {
    console,
    state: { agents: {} },
    focusedItemId: '',
    document: {
      activeElement: null,
      getElementById(id) {
        return id === 'panel-agent' ? panel : null;
      },
    },
    _captureSurfaceState(root, opts) {
      captureCalls.push({ root, opts });
      return { focus: null, scrolls: [] };
    },
    _restoreSurfaceState(root, snapshot, opts) {
      restoreCalls.push({ root, snapshot, opts });
    },
    _weaverStopEventsCountdownTimer() {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/agent_panel.js');
  return { context, panel, captureCalls, restoreCalls };
}

function createWeaverSessionMapHarness() {
  const panel = {
    innerHTML: '',
    querySelector() { return null; },
  };
  const sandbox = {
    console,
    Date,
    state: {
      agents: {},
      groups: { alpha: [] },
      weaver_session_maps: {},
    },
    focusedItemId: '',
    _activePanelApp: 'weaver',
    document: {
      activeElement: null,
      getElementById(id) {
        return id === 'panel-agent' ? panel : null;
      },
    },
    _captureSurfaceState() {
      return { focus: null, scrolls: [] };
    },
    _restoreSurfaceState() {},
    _currentGroup() { return 'alpha'; },
    _weaverStopEventsCountdownTimer() {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/weaver.js');
  loadScript(context, 'static/js/agent_panel.js');
  return { context, panel };
}

function setFocusedAgent(context, agent) {
  context.state.agents = {};
  context.focusedItemId = '';
  if (agent) {
    context.state.agents[agent.id] = agent;
    context.focusedItemId = agent.id;
  }
}

test('renderAgentPanel shows an empty state when no grid item is focused', () => {
  const { context, panel, captureCalls, restoreCalls } = createHarness();

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Select an agent from the grid to see its context\./);
  assert.equal(captureCalls.length, 1);
  assert.equal(restoreCalls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(captureCalls[0].opts.scrollSelectors)), [':root']);
});

test('renderAgentPanel routes focused architect, engineer, worker, and terminal cells to kind stubs', () => {
  const { context, panel } = createHarness();
  const cases = [
    [{ id: 'arch-1', name: 'Planner', kind: 'architect', cell_type: 'agent' }, 'architect'],
    [{ id: 'eng-1', name: 'Builder', kind: 'engineer', cell_type: 'agent' }, 'engineer'],
    [{ id: 'worker-1', name: 'Worker Bee', kind: 'worker', cell_type: 'agent' }, 'worker'],
    [{ id: 'term-1', name: 'Shell Root', cell_type: 'terminal' }, 'terminal'],
  ];

  for (const [agent, kind] of cases) {
    setFocusedAgent(context, agent);
    context.renderAgentPanel();
    assert.match(panel.innerHTML, new RegExp(`Agent: ${agent.name} · Kind: ${kind}`));
    assert.match(panel.innerHTML, /Coming in stage 4/);
  }
});

test('agentPanelSelectTab remembers the last selected tab per kind', () => {
  const { context, panel } = createHarness();
  setFocusedAgent(context, {
    id: 'arch-1',
    name: 'Planner',
    kind: 'architect',
    cell_type: 'agent',
  });

  context.agentPanelSelectTab('journal');

  assert.equal(vm.runInContext(`_agentPanelLastSelectedTabByKind.architect`, context), 'journal');
  assert.match(panel.innerHTML, /data-agent-panel-tab="journal"/);
  assert.match(panel.innerHTML, /Agent: Planner · Kind: architect/);
});

test('_resolveFocusedAgent returns null when focusedItemId does not resolve', () => {
  const { context } = createHarness();
  context.focusedItemId = 'missing-agent';

  assert.equal(vm.runInContext(`_resolveFocusedAgent()`, context), null);
});

test('_weaverReceiveSessionMap keeps the focused-agent stub rendered when weaver.js is loaded', () => {
  const { context, panel } = createWeaverSessionMapHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  assert.match(panel.innerHTML, /Agent: Worker Bee · Kind: worker/);

  context._weaverReceiveSessionMap({
    group: 'alpha',
    session_map: { group: 'alpha', streams: { items: [] } },
  });

  assert.match(panel.innerHTML, /Agent: Worker Bee · Kind: worker/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
});

test('_weaverResetSessionMapMeta keeps the focused-agent stub rendered during session-map reconnect resets', () => {
  const { context, panel } = createWeaverSessionMapHarness();
  setFocusedAgent(context, {
    id: 'worker-1',
    name: 'Worker Bee',
    kind: 'worker',
    cell_type: 'agent',
    group: 'alpha',
  });

  context.renderAgentPanel();
  vm.runInContext(`
    _weaverSessionMapMetaByGroup.alpha = { loading: true, stale: false };
  `, context);

  context._weaverResetSessionMapMeta({ clearStale: false });

  assert.match(panel.innerHTML, /Agent: Worker Bee · Kind: worker/);
  assert.doesNotMatch(panel.innerHTML, /Architects &amp; Engineers/);
  assert.equal(vm.runInContext(`_weaverSessionMapMetaByGroup.alpha.loading`, context), false);
});

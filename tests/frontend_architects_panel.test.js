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

function baseSandbox() {
  const content = { scrollTop: 0 };
  const panel = {
    innerHTML: '',
    querySelector(selector) {
      return selector === '.agent-panel-content' ? content : null;
    },
  };
  const pendingHireBanner = { innerHTML: '', hidden: true };
  const sandbox = {
    console,
    Date: { now: () => Date.now() },
    state: {
      groups: { torque: [] },
      group_settings: { torque: {} },
      agents: {},
      board_tasks: {},
      children: {},
      engineer_settings: {},
      pending_hires: {},
      decisions: {},
    },
    focusedItemId: '',
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'panel-agent') return panel;
        if (id === 'pending-hire-banner') return pendingHireBanner;
        return null;
      },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    window: {
      prompt() { return null; },
    },
    _cachedProviders: [],
    _esc(value) { return String(value); },
    _currentGroup() { return 'torque'; },
    _focusedGroup() { return 'torque'; },
    _captureSurfaceState(_root, options) {
      return { scrollTop: (options && options.scrollSelectors ? content.scrollTop : 0) };
    },
    _restoreSurfaceState(_root, snapshot) {
      if (snapshot) content.scrollTop = snapshot.scrollTop;
    },
    _showToast() {},
    showConfirm() { return Promise.resolve(true); },
    requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); },
    setInterval() { return 1; },
    clearInterval() {},
    sendCalls: [],
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, panel, content, pendingHireBanner };
}

function createArchitectHarness() {
  const { sandbox, panel, content, pendingHireBanner } = baseSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/agent_panel.js');
  return { context, sandbox, panel, content, pendingHireBanner };
}

function createBannerHarness() {
  const { sandbox, pendingHireBanner } = baseSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  return { context, sandbox, pendingHireBanner };
}

function architect(id, name, createdAt) {
  return {
    id,
    name,
    slug: id,
    kind: 'architect',
    group: 'torque',
    cell_type: 'agent',
    status: 'running',
    created_at: createdAt,
  };
}

function engineer(id, name, createdAt, hiredByArchitectId = '') {
  return {
    id,
    name,
    slug: id,
    kind: 'engineer',
    hired_by_architect_id: hiredByArchitectId,
    group: 'torque',
    cell_type: 'agent',
    status: 'running',
    created_at: createdAt,
  };
}

function worker(id, name, ownerEngineerId, createdAt) {
  return {
    id,
    name,
    slug: id,
    owner_engineer_id: ownerEngineerId,
    group: 'torque',
    cell_type: 'agent',
    status: 'running',
    created_at: createdAt,
  };
}

test('focused architect panel shows decisions, hired engineers, messages, and events tabs', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.focusedItemId = 'arch-1';

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Architect: Productmind · Group: torque/);
  assert.match(panel.innerHTML, /Decisions/);
  assert.match(panel.innerHTML, /Hired engineers/);
  assert.match(panel.innerHTML, /Messages/);
  assert.match(panel.innerHTML, /Events/);
});

test('focused architect hired-engineers tab nests hired engineers and workers only', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.state.agents['eng-hired'] = engineer('eng-hired', 'Alice', 20, 'arch-1');
  sandbox.state.agents['worker-hired'] = worker('worker-hired', 'Worker A', 'eng-hired', 30);
  sandbox.state.agents['eng-user'] = engineer('eng-user', 'Bob', 40, '');
  sandbox.state.agents['worker-user'] = worker('worker-user', 'Worker B', 'eng-user', 50);
  sandbox.focusedItemId = 'arch-1';

  context.renderAgentPanel();
  context.agentPanelSelectTab('hired_engineers');

  assert.match(panel.innerHTML, /Architect: Productmind · Group: torque/);
  assert.doesNotMatch(panel.innerHTML, /<div class="engineers-roster">[\s\S]*agent-panel-hierarchy-breadcrumb[\s\S]*<div class="engineers-roster-list/);
  assert.match(panel.innerHTML, /Hired engineers<\/span><span class="engineers-roster-count">1<\/span>/);
  assert.match(panel.innerHTML, /engineers-roster-header[\s\S]*<\/div><div class="engineers-roster-list/);
  assert.match(panel.innerHTML, /Alice/);
  assert.match(panel.innerHTML, /Worker A/);
  assert.doesNotMatch(panel.innerHTML, /Bob/);
  assert.doesNotMatch(panel.innerHTML, /Worker B/);
});

test('pending hire banner approves a request and hides optimistically', () => {
  const { context, sandbox, pendingHireBanner } = createBannerHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.state.pending_hires['hire-1'] = {
    id: 'hire-1',
    architect_id: 'arch-1',
    requested_name: 'Alice',
    created_at: 1712345678,
    status: 'pending',
  };

  vm.runInContext('renderPendingHireBanner()', context);
  assert.equal(pendingHireBanner.hidden, false);
  assert.match(pendingHireBanner.innerHTML, /Productmind/);
  assert.match(pendingHireBanner.innerHTML, /Approve/);

  vm.runInContext(`approvePendingHire('hire-1')`, context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'pending_hire_approve', id: 'hire-1' },
  ]);
  assert.equal(pendingHireBanner.hidden, true);
});

test('focused architect decision expand and collapse preserves scroll position', () => {
  const { context, sandbox, content } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.state.decisions['decision-1'] = {
    id: 'decision-1',
    architect_id: 'arch-1',
    title: 'Scope the API first',
    rationale: 'Keep the dashboard for later.',
    status: 'proposed',
    updated_at: 1712345679,
  };
  sandbox.focusedItemId = 'arch-1';

  context.renderAgentPanel();
  content.scrollTop = 180;

  vm.runInContext(`engineerToggleDecision('decision-1')`, context);
  assert.equal(content.scrollTop, 180);

  content.scrollTop = 180;
  vm.runInContext(`engineerToggleDecision('decision-1')`, context);
  assert.equal(content.scrollTop, 180);
});

test('focused architect panel lists a journal tab alongside decisions', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.focusedItemId = 'arch-1';

  context.renderAgentPanel();

  assert.match(panel.innerHTML, /Decisions/);
  assert.match(panel.innerHTML, /Journal/);
});

test('focused architect journal tab requests entries and renders them', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('journal');

  const journalFetch = sandbox.sendCalls.find(function(call) {
    return call && call.cmd === 'architect_journal_read' && call.architect_id === 'arch-1';
  });
  assert.ok(journalFetch, 'journal fetch should be dispatched on tab open');
  assert.equal(journalFetch.limit, 50);
  assert.match(panel.innerHTML, /Loading architect journal/);

  vm.runInContext(
    `agentPanelReceiveArchitectJournal({architect_id:'arch-1', entries:[`
      + `{id:'j-1', architect_id:'arch-1', type:'observation', entry:'Spotted a race', timestamp:1712345600},`
      + `{id:'j-2', architect_id:'arch-1', type:'checkpoint', entry:'Holding at review gate', timestamp:1712345700}`
      + `]})`,
    context
  );

  assert.match(panel.innerHTML, /Spotted a race/);
  assert.match(panel.innerHTML, /Current architect state/);
  assert.match(panel.innerHTML, /Holding at review gate/);
});

test('focused architect journal entries reload after new delta append preserves scroll', () => {
  const { context, sandbox, content } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.state.architect_journals = {
    'arch-1': [
      { id: 'j-1', architect_id: 'arch-1', type: 'observation', entry: 'First note', timestamp: 1712345600 },
    ],
  };
  sandbox.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('journal');
  content.scrollTop = 220;

  sandbox.state.architect_journals['arch-1'].unshift({
    id: 'j-2',
    architect_id: 'arch-1',
    type: 'decision',
    entry: 'Pivot to incremental migration',
    timestamp: 1712345700,
  });
  vm.runInContext(`_agentPanelInvalidateArchitectJournalCache('arch-1')`, context);
  vm.runInContext(`renderAgentPanel()`, context);

  assert.equal(content.scrollTop, 220);
});

test('focused architect journal shows decision count in header', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.state.decisions['decision-1'] = {
    id: 'decision-1',
    architect_id: 'arch-1',
    title: 'Keep rollout paused',
    status: 'accepted',
    updated_at: 1712345670,
  };
  sandbox.state.architect_journals = {
    'arch-1': [
      { id: 'j-1', architect_id: 'arch-1', type: 'observation', entry: 'Observed regression', timestamp: 1712345600 },
    ],
  };
  sandbox.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('journal');

  assert.match(panel.innerHTML, /1 decision/);
  assert.match(panel.innerHTML, /Observed regression/);
});

test('focused architect journal renders a bounded window with Load older pagination', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  const entries = [];
  for (let i = 0; i < 150; i++) {
    entries.push({
      id: 'j-' + i,
      architect_id: 'arch-1',
      type: i === 0 ? 'checkpoint' : 'observation',
      entry: 'Entry #' + i,
      timestamp: 1712345600 + i,
    });
  }
  sandbox.state.architect_journals = { 'arch-1': entries };
  sandbox.focusedItemId = 'arch-1';

  context.agentPanelSelectTab('journal');

  const rendered = (panel.innerHTML.match(/architect-journal-j-\d+/g) || []).length;
  assert.equal(rendered, 50);
  assert.doesNotMatch(panel.innerHTML, /data-agent-panel-virtualized="true"/);
  assert.match(panel.innerHTML, /Load 50 older entries/);
  assert.match(panel.innerHTML, /Entry #49/);
  assert.doesNotMatch(panel.innerHTML, /Entry #50/);

  vm.runInContext(`agentPanelLoadOlderArchitectJournal(null, 'arch-1')`, context);

  const renderedAfterLoad = (panel.innerHTML.match(/architect-journal-j-\d+/g) || []).length;
  assert.equal(renderedAfterLoad, 100);
  assert.match(panel.innerHTML, /Entry #99/);
  assert.doesNotMatch(panel.innerHTML, /Entry #100/);
});

test('focused architect journal Load older fetches the next bounded window', () => {
  const { context, panel, sandbox } = createArchitectHarness();
  sandbox.state.agents['arch-1'] = architect('arch-1', 'Productmind', 10);
  sandbox.focusedItemId = 'arch-1';
  context.agentPanelSelectTab('journal');

  const entries = [];
  for (let i = 0; i < 50; i++) {
    entries.push({
      id: 'j-' + i,
      architect_id: 'arch-1',
      type: 'observation',
      entry: 'Entry #' + i,
      timestamp: 1712345600 + i,
    });
  }

  vm.runInContext(
    `agentPanelReceiveArchitectJournal({architect_id:'arch-1', limit:50, entries:${JSON.stringify(entries)}})`,
    context
  );
  sandbox.sendCalls.length = 0;

  vm.runInContext(`agentPanelLoadOlderArchitectJournal(null, 'arch-1')`, context);

  assert.match(panel.innerHTML, /Loading older journal entries/);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'architect_journal_read', architect_id: 'arch-1', limit: 100 },
  ]);
});

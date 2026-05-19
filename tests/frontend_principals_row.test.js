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

function createSandbox() {
  const mainEl = {
    id: 'main',
    innerHTML: '',
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 900, height: 600 }; },
  };
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    location: { host: 'localhost' },
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'main') return mainEl;
        return {
          addEventListener() {},
          removeEventListener() {},
          querySelector() { return null; },
          querySelectorAll() { return []; },
          innerHTML: '',
          textContent: '',
          value: '',
          classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
          style: {},
        };
      },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
      removeEventListener() {},
    },
    window: {},
    state: {
      agents: {},
      groups: { torque: [] },
      group_settings: { torque: { collapsed_default: false } },
      children: {},
      board_tasks: {},
      selected_principal_id: '',
      current_window_id: null,
    },
    focusedItemId: null,
    selectedAgentId: null,
    selectedTerminalId: null,
    dragInProgress: false,
    sendCalls: [],
    esc(value) { return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); },
    isSystemLabel(l) { return String(l).startsWith('torque:'); },
    displayLabel(l) { return String(l); },
    agentStatusClass(a) {
      if (!a) return '';
      if (a.needs_attention) return 'attention';
      if (a.status === 'running') return 'working';
      return 'idle';
    },
    focusAgent() {},
    getFilterByWindow() { return false; },
    setTimeout(fn) { fn(); return 1; },
    clearTimeout() {},
    requestAnimationFrame(fn) { fn(); },
  };
  sandbox.send = function(message) { sandbox.sendCalls.push(message); };
  sandbox.connect = function() {};
  sandbox.setupDrag = function() {};
  sandbox.closeMenus = function() {};
  sandbox.closeContextMenu = function() {};
  sandbox.closeModals = function() {};
  sandbox.submitAdd = function() {};
  sandbox.submitAddEngineer = function() {};
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox._captureSurfaceState = function() { return null; };
  sandbox._restoreSurfaceState = function() {};
  sandbox.renderTerminalWorkspace = function() {};
  sandbox.updateEventsAttentionBadge = function() {};
  sandbox.renderAgentPanel = function() {};
  sandbox.renderPendingHireBanner = function() {};
  sandbox._currentPanelSurfaces = function() { return []; };
  sandbox._updateEngineerTaskbarBadge = function() {};
  sandbox._pruneAgentDoneFlourishes = function() {};
  sandbox._captureAgentDetailDrafts = function() {};
  sandbox._embeddedRuntimeEnabled = function() { return false; };
  sandbox.renderAgentDetails = function() { return ''; };
  sandbox.renderTerminalRow = function() { return ''; };
  sandbox.renderTermAddBtn = function() { return ''; };
  sandbox._captureMainFocusKey = function() { return null; };
  return { sandbox, mainEl };
}

function createHarness() {
  const { sandbox, mainEl } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  vm.runInContext(
    'renderAgentCell = function(a) {'
    + ' var kind = String(a.kind || "");'
    + ' return "<div class=\\"cell " + esc(kind) + "\\" draggable=\\"true\\" data-drag-id=\\""'
    + ' + esc(a.id) + "\\" data-agent-kind=\\""'
    + ' + esc(kind) + "\\">" + esc(a.name || a.id) + "</div>";'
    + ' };',
    context,
  );
  loadScript(context, 'static/js/commands.js');
  loadScript(context, 'static/js/main.js');
  return { context, sandbox, mainEl };
}

function architect(id, name, createdAt) {
  return { id, name, slug: id, kind: 'architect', group: 'torque', cell_type: 'agent', status: 'running', created_at: createdAt || 1 };
}

function engineer(id, name, hiredBy, createdAt) {
  return { id, name, slug: id, kind: 'engineer', hired_by_architect_id: hiredBy || '', group: 'torque', cell_type: 'agent', status: 'running', created_at: createdAt || 1 };
}

function worker(id, name, ownerEngineer, createdAt) {
  const w = { id, name, slug: id, kind: 'worker', group: 'torque', cell_type: 'agent', status: 'running', created_at: createdAt || 1 };
  if (ownerEngineer !== undefined) w.owner_engineer_id = ownerEngineer;
  return w;
}

function seedMixedAgents(sandbox) {
  sandbox.state.groups.torque = [
    'arch-a', 'eng-arch', 'worker-arch',
    'eng-user', 'worker-user', 'loose-worker',
  ];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 1);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'Architect Engineer', 'arch-a', 2);
  sandbox.state.agents['worker-arch'] = worker('worker-arch', 'Architect Worker', 'eng-arch', 3);
  sandbox.state.agents['eng-user'] = engineer('eng-user', 'Orphan Engineer', '', 4);
  sandbox.state.agents['worker-user'] = worker('worker-user', 'Orphan Engineer Worker', 'eng-user', 5);
  sandbox.state.agents['loose-worker'] = worker('loose-worker', 'Loose Worker', '', 6);
}

test('stratified grid renders architects, orphan engineers, and orphan workers regardless of selected_principal_id', () => {
  const { context, sandbox, mainEl } = createHarness();
  seedMixedAgents(sandbox);
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext('render();', context);

  assert.doesNotMatch(mainEl.innerHTML, /principals-row/);
  assert.match(mainEl.innerHTML, /agent-grid agent-grid-stratified/);
  assert.match(mainEl.innerHTML, /data-agent-strata="architects"/);
  assert.match(mainEl.innerHTML, /data-agent-strata="engineers"/);
  assert.match(mainEl.innerHTML, /data-agent-strata="workers"/);
  for (const id of ['arch-a', 'eng-arch', 'worker-arch', 'eng-user', 'worker-user', 'loose-worker']) {
    assert.match(mainEl.innerHTML, new RegExp('data-drag-id="' + id + '"'));
  }
  assert.match(mainEl.innerHTML, /data-agent-section="architect:arch-a"[\s\S]*class="cell architect"[\s\S]*Productmind/);
  assert.match(mainEl.innerHTML, /data-agent-strata="engineers"[\s\S]*Orphan Engineer[\s\S]*Orphan Engineer Worker/);
  assert.match(mainEl.innerHTML, /data-agent-strata="workers"[\s\S]*loose-workers-strip[\s\S]*Loose Worker[\s\S]*\+ Add Worker/);
});

test('empty strata still render headings, empty copy, and creation controls', () => {
  const { context, mainEl } = createHarness();

  vm.runInContext('render();', context);

  assert.match(mainEl.innerHTML, /data-agent-strata="architects"[\s\S]*No architects yet\.[\s\S]*\+ New Architect/);
  assert.match(mainEl.innerHTML, /data-agent-strata="engineers"[\s\S]*No orphan engineers\.[\s\S]*\+ New Engineer/);
  assert.match(mainEl.innerHTML, /data-agent-strata="workers"[\s\S]*No orphan workers\.[\s\S]*\+ Add Worker/);
  assert.match(mainEl.innerHTML, /openAddArchitectForGroup\(&quot;torque&quot;\)/);
  assert.match(mainEl.innerHTML, /openAddEngineerForSection\(&quot;torque&quot;,&quot;&quot;\)/);
  assert.match(mainEl.innerHTML, /openAddWorkerForSection\(&quot;torque&quot;\)/);
});

test('navigation model has no principals row and includes all visible strata in visual order', () => {
  const { context, sandbox } = createHarness();
  seedMixedAgents(sandbox);
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext('render();', context);

  const rows = JSON.parse(vm.runInContext(
    'JSON.stringify((window._navGridRows || []).map(function(row) { return { rowKey: row.rowKey, rowType: row.rowType, sectionKey: row.sectionKey, items: row.items.map(function(item) { return item.id; }) }; }))',
    context,
  ));
  assert.equal(rows.some(row => row.rowType === 'principals-row'), false);
  assert.deepEqual(rows, [
    { rowKey: 'architect:arch-a:engineer:eng-arch', rowType: 'engineer-row', sectionKey: 'architect:arch-a', items: ['arch-a', 'eng-arch', 'worker-arch'] },
    { rowKey: 'architect:arch-a:section-new-engineer', rowType: 'section-creation-row', sectionKey: 'architect:arch-a', items: ['grid-control:section-new-engineer:torque:architect:arch-a'] },
    { rowKey: 'architects:agent-new-architect', rowType: 'architect-creation-row', sectionKey: 'architects', items: ['grid-control:agent-new-architect:torque'] },
    { rowKey: 'user:engineer:eng-user', rowType: 'engineer-row', sectionKey: 'user', items: ['eng-user', 'worker-user'] },
    { rowKey: 'user:section-new-engineer', rowType: 'section-creation-row', sectionKey: 'user', items: ['grid-control:section-new-engineer:torque:user'] },
    { rowKey: 'workers:standalone-workers', rowType: 'standalone-workers-row', sectionKey: 'workers', items: ['loose-worker', 'grid-control:section-new-worker:torque:user'] },
  ]);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(window._navAgents)', context)), [
    'arch-a', 'eng-arch', 'worker-arch', 'eng-user', 'worker-user', 'loose-worker',
  ]);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(window._navCreationControls.map(function(c) { return c.id; }))', context)), [
    'grid-control:section-new-engineer:torque:architect:arch-a',
    'grid-control:agent-new-architect:torque',
    'grid-control:section-new-engineer:torque:user',
    'grid-control:section-new-worker:torque:user',
  ]);
});

test('legacy selectPrincipal persists compatibility state without filtering the grid', () => {
  const { context, sandbox, mainEl } = createHarness();
  seedMixedAgents(sandbox);
  vm.runInContext('render();', context);

  vm.runInContext("selectPrincipal('arch-a', 'torque');", context);

  assert.equal(sandbox.state.selected_principal_id, 'arch-a');
  assert.equal(vm.runInContext('selectedAgentId', context), 'arch-a');
  assert.equal(vm.runInContext('focusedItemId', context), 'arch-a');
  assert.ok(sandbox.sendCalls.some(c => c.cmd === 'select_agent' && c.id === 'arch-a'));
  assert.ok(sandbox.sendCalls.some(c => c.cmd === 'focus_agent' && c.id === 'arch-a'));
  assert.ok(sandbox.sendCalls.some(c => c.cmd === 'ui_select_principal' && c.principal_id === 'arch-a'));
  assert.match(mainEl.innerHTML, /data-drag-id="eng-user"/);
  assert.match(mainEl.innerHTML, /data-drag-id="loose-worker"/);

  sandbox.sendCalls.length = 0;
  vm.runInContext("selectPrincipal('', 'torque');", context);
  assert.equal(sandbox.state.selected_principal_id, '');
  assert.equal(vm.runInContext('selectedAgentId', context), 'arch-a');
  assert.equal(vm.runInContext('focusedItemId', context), 'eng-user');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [{ cmd: 'ui_select_principal', principal_id: '' }]);
});

test('stratified grid CSS defines strata, architect bands, wrapping workers, and no full-width empty engineer override', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /\.agent-grid-stratified\s*\{[\s\S]*overflow-x:\s*auto;/);
  assert.match(css, /\.agent-strata\s*\{[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.agent-strata-heading\s*\{[\s\S]*text-transform:\s*uppercase;/);
  assert.match(css, /\.agent-band--architect\s*\{[\s\S]*grid-template-columns:\s*var\(--agent-architect-column-width\)\s+minmax\(var\(--agent-engineer-column-width\),\s*1fr\)/);
  assert.match(css, /\.agent-band-body\.agent-section-body\s*\{[\s\S]*display:\s*flex;[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.agent-grid \.engineer-row\s*\{[\s\S]*align-items:\s*stretch;/);
  assert.match(css, /\.engineer-row-workers,\s*\.loose-workers-strip\s*\{[\s\S]*flex-wrap:\s*wrap;/);
  assert.doesNotMatch(css, /\.agent-grid \.engineer-row\.engineer-row--empty-workers\s*\{[^}]*display:\s*block/s);
  assert.doesNotMatch(css, /\.agent-grid \.engineer-row\.engineer-row--empty-workers \.engineer-row-anchor\s*\{[^}]*width:\s*100%/s);
  assert.match(css, /body\.runtime-embedded \.agent-grid\s*\{[\s\S]*--agent-architect-column-width:\s*106px;/);
});

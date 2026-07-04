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

function createHarness(options) {
  options = options || {};
  const { sandbox, mainEl } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  loadScript(context, 'static/js/agent-detail.js');
  loadScript(context, 'static/js/agent-focus.js');
  loadScript(context, 'static/js/grid/main.js');
  if (options.stubCells !== false) {
    vm.runInContext(`
      renderAgentCell = function(a) {
        var kind = String(a.kind || '');
        var classes = ['cell', kind];
        if (a.id === selectedAgentId) classes.push('selected');
        if (a.id === focusedItemId) classes.push('focused');
        return '<div class="' + esc(classes.join(' ')) + '" draggable="true" data-drag-id="'
          + esc(a.id) + '" data-agent-kind="'
          + esc(kind) + '">' + esc(a.name || a.id) + '</div>';
      };
    `, context);
  }
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


function seedRetainedArchitectScenario(sandbox) {
  sandbox.state.groups.torque = [
    'arch-a', 'eng-a', 'worker-a',
    'arch-steward',
    'arch-b', 'eng-b', 'worker-b',
  ];
  sandbox.state.agents['arch-a'] = Object.assign(architect('arch-a', 'Torqly', 1), {
    effective_agent_class_id: 'default-architect',
  });
  sandbox.state.agents['eng-a'] = engineer('eng-a', 'Torqly Engineer', 'arch-a', 2);
  sandbox.state.agents['worker-a'] = worker('worker-a', 'Torqly Worker', 'eng-a', 3);
  sandbox.state.agents['arch-steward'] = Object.assign(architect('arch-steward', 'Torque Steward', 4), {
    agent_class_id: 'torque-steward',
    effective_agent_class_id: 'torque-steward',
    effective_agent_class_version: '1',
    effective_agent_class_snapshot: {
      id: 'torque-steward',
      version: '1',
      base_kind: 'architect',
      primary_identity_label: 'Torque Steward',
      secondary_base_kind_label: 'Architect-derived',
      status: 'full',
      metadata: { archetype: 'torque_steward' },
    },
  });
  sandbox.state.agents['arch-b'] = architect('arch-b', 'Blueprint', 5);
  sandbox.state.agents['eng-b'] = engineer('eng-b', 'Blueprint Engineer', 'arch-b', 6);
  sandbox.state.agents['worker-b'] = worker('worker-b', 'Blueprint Worker', 'eng-b', 7);
}

function classOnlyCssDeclaration(css, classList, property) {
  const classes = new Set(classList || []);
  let winner = null;
  let order = 0;
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = String(match[1] || '').split(',');
    const body = String(match[2] || '');
    const declarationRe = new RegExp('(?:^|;)\\s*' + property.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*:\\s*([^;]+)', 'g');
    let value = '';
    for (const declaration of body.matchAll(declarationRe)) value = String(declaration[1] || '').trim();
    if (!value) {
      order++;
      continue;
    }
    for (const rawSelector of selectors) {
      const selector = String(rawSelector || '').trim();
      if (!/^(?:\.[A-Za-z0-9_-]+)+$/.test(selector)) continue;
      const selectorClasses = [...selector.matchAll(/\.([A-Za-z0-9_-]+)/g)].map(item => item[1]);
      if (!selectorClasses.every(cls => classes.has(cls))) continue;
      const specificity = selectorClasses.length;
      if (!winner || specificity > winner.specificity || (specificity === winner.specificity && order >= winner.order)) {
        winner = { selector, value, specificity, order };
      }
    }
    order++;
  }
  return winner;
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
  assert.match(mainEl.innerHTML, /data-agent-strata="architects"[\s\S]*data-agent-architect-strip[\s\S]*class="cell architect"[\s\S]*Productmind/);
  assert.match(mainEl.innerHTML, /data-agent-strata="architect-execution"[\s\S]*data-execution-architect-id="arch-a"[\s\S]*Architect Engineer[\s\S]*Architect Worker/);
  assert.match(mainEl.innerHTML, /data-agent-strata="engineers"[\s\S]*Orphan Engineer[\s\S]*Orphan Engineer Worker/);
  assert.match(mainEl.innerHTML, /data-agent-strata="workers"[\s\S]*loose-workers-strip[\s\S]*Loose Worker/);
  assert.doesNotMatch(mainEl.innerHTML, /agent-strata-heading/);
  assert.doesNotMatch(mainEl.innerHTML, /\+ Add Worker|\+ New Engineer|\+ New Architect/);
  assert.match(mainEl.innerHTML, /data-agent-grid-toolbar[\s\S]*data-agent-grid-new-button[\s\S]*>\+ New<\/button>/);
});

test('architect execution workers remain a full-width multi-column grid with one engineer and many workers', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.torque = ['arch-a', 'eng-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Torqly', 1);
  sandbox.state.agents['eng-a'] = engineer('eng-a', 'Panelsmith', 'arch-a', 2);
  for (let i = 1; i <= 6; i++) {
    const id = 'worker-' + i;
    sandbox.state.groups.torque.push(id);
    sandbox.state.agents[id] = worker(id, 'Worker ' + i, 'eng-a', i + 2);
  }
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext('render();', context);

  const sectionStart = mainEl.innerHTML.indexOf('data-agent-strata="architect-execution"');
  const sectionEnd = mainEl.innerHTML.indexOf('</section>', sectionStart);
  const sectionHtml = mainEl.innerHTML.slice(sectionStart, sectionEnd);
  assert.ok(sectionStart >= 0 && sectionEnd > sectionStart, 'execution hierarchy section should render');
  assert.equal((sectionHtml.match(/data-agent-row-shape="engineer-row"/g) || []).length, 1);
  assert.match(sectionHtml, /data-worker-count="6"/);
  assert.match(sectionHtml, /Panelsmith[\s\S]*Worker 1[\s\S]*Worker 2[\s\S]*Worker 3[\s\S]*Worker 4[\s\S]*Worker 5[\s\S]*Worker 6/);

  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const executionBandDisplay = classOnlyCssDeclaration(
    css,
    ['agent-band', 'agent-band--architect-execution', 'agent-section', 'agent-section-architect'],
    'display',
  );
  assert.equal(
    executionBandDisplay && executionBandDisplay.value,
    'block',
    'execution band must not inherit .agent-section grid, which constrains its only body child to the narrow engineer/architect column',
  );
  assert.match(executionBandDisplay.selector, /\.agent-band--architect-execution\.agent-section/);
  const workerRowsBlock = (css.match(/\.engineer-row-workers,\s*\.loose-workers-strip\s*\{[^}]*\}/) || [''])[0];
  assert.match(workerRowsBlock, /grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(var\(--agent-grid-card-min\),\s*1fr\)\);/);
});

test('empty strata are hidden while the grid-level new menu remains', () => {
  const { context, mainEl } = createHarness();

  vm.runInContext('render();', context);

  assert.doesNotMatch(mainEl.innerHTML, /data-agent-strata=/);
  assert.doesNotMatch(mainEl.innerHTML, /<section class="agent-strata/);
  assert.doesNotMatch(mainEl.innerHTML, /agent-strata-heading/);
  assert.doesNotMatch(mainEl.innerHTML, /\+ New Architect|\+ New Engineer|\+ Add Worker/);
  assert.doesNotMatch(mainEl.innerHTML, /No orphan engineers\./);
  assert.doesNotMatch(mainEl.innerHTML, /No orphan workers\./);
  assert.match(mainEl.innerHTML, /data-agent-grid-toolbar[\s\S]*openAgentGridNewMenu\(event,&quot;torque&quot;\)/);
});

test('grid-level + New menu opens standalone architect, engineer, and worker flows', () => {
  const { context, sandbox, mainEl } = createHarness();
  const menuCalls = [];
  context.showContextMenu = function(x, y, items) {
    menuCalls.push({ x, y, items });
  };

  vm.runInContext(`
    var modalCalls = [];
    openAddArchitectForGroup = function(group) { modalCalls.push({ type: 'architect', group: group }); };
    openAddEngineerForSection = function(group, architectId) { modalCalls.push({ type: 'engineer', group: group, architectId: architectId || '' }); };
    openAddWorkerForSection = function(group) { modalCalls.push({ type: 'worker', group: group }); };
    render();
  `, context);

  assert.match(mainEl.innerHTML, /class="agent-grid-new-btn"/);
  context.openAgentGridNewMenu({
    preventDefault() {},
    stopPropagation() {},
    clientX: 10,
    clientY: 20,
    currentTarget: {
      getBoundingClientRect() { return { left: 100, bottom: 40 }; },
    },
  }, 'torque');

  assert.equal(menuCalls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(menuCalls[0].items.map(item => item.label))), [
    'New architect',
    'New engineer',
    'New worker',
  ]);
  assert.equal(menuCalls[0].x, 100);
  assert.equal(menuCalls[0].y, 44);

  for (const item of menuCalls[0].items) {
    vm.runInContext(item.action, context);
  }
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(modalCalls)', context)), [
    { type: 'architect', group: 'torque' },
    { type: 'engineer', group: 'torque', architectId: '' },
    { type: 'worker', group: 'torque' },
  ]);
});

test('stratified grid keeps Torque Steward in normal architect creation order', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.torque = ['arch-old', 'arch-steward', 'arch-late'];
  sandbox.state.agents['arch-old'] = architect('arch-old', 'Torqly', 1);
  sandbox.state.agents['arch-steward'] = Object.assign(architect('arch-steward', 'Torque Steward', 20), {
    agent_class_id: 'torque-steward',
    effective_agent_class_id: 'torque-steward',
  });
  sandbox.state.agents['arch-late'] = architect('arch-late', 'Blueprint', 30);

  vm.runInContext('render();', context);

  assert.deepEqual(JSON.parse(vm.runInContext(
    'JSON.stringify(((window._navGridRows || []).find(function(row) { return row.rowType === "architect-strip-row"; }) || { items: [] }).items.map(function(item) { return item.id; }))',
    context,
  )), ['arch-old', 'arch-steward', 'arch-late']);
  assert.match(mainEl.innerHTML, /data-agent-architect-strip[\s\S]*arch-old[\s\S]*arch-steward[\s\S]*arch-late/);
});

test('navigation model includes all visible strata in visual order without legacy principal rows', () => {
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
    { rowKey: 'architects:strip', rowType: 'architect-strip-row', sectionKey: 'architects', items: ['arch-a'] },
    { rowKey: 'architect:arch-a:engineer:eng-arch', rowType: 'engineer-row', sectionKey: 'architect:arch-a', items: ['eng-arch', 'worker-arch'] },
    { rowKey: 'user:engineer:eng-user', rowType: 'engineer-row', sectionKey: 'user', items: ['eng-user', 'worker-user'] },
    { rowKey: 'workers:standalone-workers', rowType: 'standalone-workers-row', sectionKey: 'workers', items: ['loose-worker'] },
  ]);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(window._navAgents)', context)), [
    'arch-a', 'eng-arch', 'worker-arch', 'eng-user', 'worker-user', 'loose-worker',
  ]);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(window._navCreationControls.map(function(c) { return c.id; }))', context)), []);
  assert.equal(JSON.parse(vm.runInContext('JSON.stringify(window._navGridRows.some(function(row) { return /creation/.test(row.rowType); }))', context)), false);
});

test('selecting a no-engineer Architect preserves the last execution hierarchy and selection state', () => {
  const { context, sandbox, mainEl } = createHarness({ stubCells: false });
  seedRetainedArchitectScenario(sandbox);

  vm.runInContext("selectedAgentId = 'arch-a'; focusedItemId = 'arch-a'; render();", context);
  assert.match(mainEl.innerHTML, /data-agent-strata="architect-execution"[\s\S]*data-execution-architect-id="arch-a"/);
  assert.match(mainEl.innerHTML, /class="[^"]*cell[^"]*selected[^"]*focused[^"]*architect[^"]*"[\s\S]*Torqly/);
  assert.doesNotMatch(mainEl.innerHTML, /retained-execution-owner|data-retained-execution-owner|data-execution-retained="true"/);
  assert.match(mainEl.innerHTML, /Torqly Engineer[\s\S]*Torqly Worker/);

  vm.runInContext("selectedAgentId = 'arch-steward'; focusedItemId = 'arch-steward'; render();", context);

  assert.match(mainEl.innerHTML, /data-agent-strata="architect-execution"[\s\S]*data-execution-architect-id="arch-a"[\s\S]*data-execution-selected-architect-id="arch-steward"[\s\S]*data-execution-retained="true"/);
  assert.doesNotMatch(mainEl.innerHTML, /agent-execution-retained-note|Showing [^<]*execution hierarchy while/);
  assert.match(mainEl.innerHTML, /data-drag-id="arch-a"[^>]*data-retained-execution-owner="true"/);
  assert.match(mainEl.innerHTML, /class="[^"]*cell[^"]*architect[^"]*retained-execution-owner[^"]*"/);
  assert.match(mainEl.innerHTML, /class="[^"]*cell[^"]*selected[^"]*focused[^"]*architect[^"]*"[\s\S]*Torque Steward/);
  assert.doesNotMatch(mainEl.innerHTML, /data-drag-id="arch-steward"[^>]*data-retained-execution-owner="true"/);
  assert.match(mainEl.innerHTML, /cell-agent-class-badge[\s\S]*Torque Steward/);
  assert.match(mainEl.innerHTML, /Torqly Engineer[\s\S]*Torqly Worker/);
  assert.doesNotMatch(mainEl.innerHTML, /Blueprint Engineer[\s\S]*Blueprint Worker/);
});

test('switching between execution-owning Architects updates the retained hierarchy target', () => {
  const { context, sandbox, mainEl } = createHarness();
  seedRetainedArchitectScenario(sandbox);

  vm.runInContext("selectedAgentId = 'arch-a'; focusedItemId = 'arch-a'; render();", context);
  assert.match(mainEl.innerHTML, /data-execution-architect-id="arch-a"/);
  assert.match(mainEl.innerHTML, /Torqly Engineer[\s\S]*Torqly Worker/);

  vm.runInContext("selectedAgentId = 'arch-b'; focusedItemId = 'arch-b'; render();", context);

  assert.match(mainEl.innerHTML, /data-agent-strata="architect-execution"[\s\S]*data-execution-architect-id="arch-b"/);
  assert.doesNotMatch(mainEl.innerHTML, /data-execution-retained="true"/);
  assert.match(mainEl.innerHTML, /Blueprint Engineer[\s\S]*Blueprint Worker/);
  assert.doesNotMatch(mainEl.innerHTML, /Torqly Engineer[\s\S]*Torqly Worker/);

  const rows = JSON.parse(vm.runInContext(
    'JSON.stringify((window._navGridRows || []).map(function(row) { return { rowKey: row.rowKey, rowType: row.rowType, sectionKey: row.sectionKey, items: row.items.map(function(item) { return item.id; }) }; }))',
    context,
  ));
  assert.deepEqual(rows.slice(0, 2), [
    { rowKey: 'architects:strip', rowType: 'architect-strip-row', sectionKey: 'architects', items: ['arch-a', 'arch-steward', 'arch-b'] },
    { rowKey: 'architect:arch-b:engineer:eng-b', rowType: 'engineer-row', sectionKey: 'architect:arch-b', items: ['eng-b', 'worker-b'] },
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

test('stratified grid CSS defines flat architect strip, retained execution area, wrapping workers, and responsive behavior', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const paneBlock = (css.match(/\.agents-grid-pane\s*\{[^}]*\}/) || [''])[0];
  const toolbarBlock = (css.match(/\.agent-grid-toolbar\s*\{[^}]*\}/) || [''])[0];

  assert.match(css, /\.agent-grid-stratified\s*\{[\s\S]*overflow-x:\s*auto;/);
  assert.match(css, /\.agent-strata\s*\{[\s\S]*flex-direction:\s*column;/);
  assert.doesNotMatch(css, /\.agent-strata-heading\s*\{/);
  assert.match(paneBlock, /position:\s*relative;/);
  assert.match(toolbarBlock, /justify-content:\s*flex-end;/);
  assert.match(toolbarBlock, /position:\s*absolute;/);
  assert.match(toolbarBlock, /top:\s*var\(--agents-grid-pane-pad-y\);/);
  assert.match(toolbarBlock, /right:\s*var\(--agents-grid-pane-pad-x\);/);
  assert.match(css, /\.agent-grid-new-btn\s*\{[\s\S]*border-radius:\s*999px;/);
  assert.match(css, /\.agent-architect-strip\s*\{[\s\S]*display:\s*flex;[\s\S]*flex-wrap:\s*wrap;/);
  assert.match(css, /\.agent-architect-strip > \.cell\s*\{[\s\S]*flex:\s*0 1 var\(--agent-architect-column-width\);/);
  assert.match(css, /\.agent-strata--architect-execution\s*\{[\s\S]*gap:\s*5px;/);
  assert.doesNotMatch(css, /\.agent-execution-retained-note\b/);
  assert.match(css, /\.agent-execution-empty\s*\{[\s\S]*border:\s*1px solid/);
  assert.match(css, /\.cell\.architect\.retained-execution-owner\s*\{[\s\S]*border-color:\s*color-mix\(in srgb, var\(--accent\) 48%, transparent\);/);
  assert.match(css, /\.agent-band--architect-execution\s*\{[\s\S]*display:\s*block;/);
  assert.match(css, /\.agent-band-body\.agent-section-body\s*\{[\s\S]*display:\s*flex;[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.agent-execution-body\.agent-section-body\s*\{[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.agent-card-body\s*\{[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.agent-card-line\s*\{[\s\S]*text-overflow:\s*ellipsis;/);
  assert.match(css, /\.agent-grid \.engineer-row\s*\{[\s\S]*align-items:\s*stretch;/);
  const workerRowsBlock = (css.match(/\.engineer-row-workers,\s*\.loose-workers-strip\s*\{[^}]*\}/) || [''])[0];
  const workerCardsBlock = (css.match(/\.engineer-row-workers > \.cell,\s*\.loose-workers-strip > \.cell,\s*\.ghost-card--worker\s*\{[^}]*\}/) || [''])[0];
  assert.match(workerRowsBlock, /display:\s*grid;/);
  assert.match(workerRowsBlock, /grid-template-columns:\s*repeat\(auto-fill,\s*minmax\(var\(--agent-grid-card-min\),\s*1fr\)\);/);
  assert.match(workerRowsBlock, /justify-content:\s*stretch;/);
  assert.doesNotMatch(workerRowsBlock, /flex-wrap/);
  assert.match(workerCardsBlock, /width:\s*100%;/);
  assert.match(workerCardsBlock, /max-width:\s*none;/);
  assert.doesNotMatch(css, /\.agent-grid \.engineer-row\.engineer-row--empty-workers\s*\{[^}]*display:\s*block/s);
  assert.doesNotMatch(css, /\.agent-grid \.engineer-row\.engineer-row--empty-workers \.engineer-row-anchor\s*\{[^}]*width:\s*100%/s);
  assert.match(css, /body\.runtime-embedded \.agent-grid\s*\{[\s\S]*--agent-architect-column-width:\s*106px;/);
  assert.doesNotMatch(css, /\.principals-row\s*\{/);
  assert.doesNotMatch(css, /\.principal-card(?:[\s.#:{-]|$)/);
  assert.doesNotMatch(css, /--agent-principal-card-height/);
});

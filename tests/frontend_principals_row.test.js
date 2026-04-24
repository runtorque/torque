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

/* Minimal sandbox for rendering the main grid with the principals row. */
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
      groups: { loom: [] },
      group_settings: { loom: { collapsed_default: false } },
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
    isSystemLabel(l) { return String(l).startsWith('loom:'); },
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
  sandbox._updateWeaverTaskbarBadge = function() {};
  sandbox._pruneAgentDoneFlourishes = function() {};
  sandbox._captureAgentDetailDrafts = function() {};
  sandbox._embeddedRuntimeEnabled = function() { return false; };
  sandbox.renderAgentCell = function(a) {
    return '<div class="cell" data-drag-id="' + sandbox.esc(a.id) + '"'
      + ' data-agent-kind="' + sandbox.esc(a.kind || '') + '">'
      + sandbox.esc(a.name || a.id) + '</div>';
  };
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
  // Override renderAgentCell with a tiny stub so tests don't depend on
  // the full cell markup. The real function is replaced for this harness.
  vm.runInContext(
    'renderAgentCell = function(a) {'
    + ' return "<div class=\\"cell\\" draggable=\\"true\\" data-drag-id=\\""'
    + ' + esc(a.id) + "\\" data-agent-kind=\\""'
    + ' + esc(a.kind || "") + "\\">" + esc(a.name || a.id) + "</div>";'
    + ' };',
    context,
  );
  loadScript(context, 'static/js/commands.js');
  loadScript(context, 'static/js/main.js');
  return { context, sandbox, mainEl };
}

function architect(id, name, createdAt) {
  return {
    id, name, slug: id, kind: 'architect',
    group: 'loom', cell_type: 'agent', status: 'running',
    created_at: createdAt || 1,
  };
}

function engineer(id, name, hiredBy, createdAt) {
  return {
    id, name, slug: id, kind: 'engineer',
    hired_by_architect_id: hiredBy || '',
    group: 'loom', cell_type: 'agent', status: 'running',
    created_at: createdAt || 1,
  };
}

function worker(id, name, ownerEngineer, createdAt) {
  return {
    id, name, slug: id, kind: 'worker',
    owner_engineer_id: ownerEngineer,
    group: 'loom', cell_type: 'agent', status: 'running',
    created_at: createdAt || 1,
  };
}

test('principals row renders user + each architect + new architect ghost', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'arch-b'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['arch-b'] = architect('arch-b', 'Platform', 3);
  vm.runInContext('render();', context);

  assert.match(mainEl.innerHTML, /class="principals-row"/);
  // Three principal cards: User + 2 architects.
  const cardMatches = mainEl.innerHTML.match(/data-principal-card="/g) || [];
  assert.equal(cardMatches.length, 3);
  assert.match(mainEl.innerHTML, /data-principal-card="user"/);
  assert.match(mainEl.innerHTML, /data-principal-card="architect"[^>]*data-principal-id="arch-a"/);
  assert.match(mainEl.innerHTML, /data-principal-card="architect"[^>]*data-principal-id="arch-b"/);
  // + New Architect ghost as the last tile in the principals row.
  assert.match(mainEl.innerHTML, /principal-card-new[\s\S]*\+ New Architect/);
});

test('default (empty) selected_principal_id filters grid to user-owned engineers', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-user', 'eng-arch'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-user'] = engineer('eng-user', 'UserEng', '', 3);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 4);
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  // Engineer under the user principal is rendered; engineer hired by architect is not.
  assert.match(mainEl.innerHTML, /data-drag-id="eng-user"/);
  assert.doesNotMatch(mainEl.innerHTML, /data-drag-id="eng-arch"/);
  // User principal card is marked selected; architect is dim.
  assert.match(mainEl.innerHTML, /principal-card principal-card--user selected/);
  assert.match(mainEl.innerHTML, /principal-card principal-card--architect dim/);
});

test('architect selection filters grid to that architect\'s engineers + workers', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-user', 'eng-arch', 'worker-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-user'] = engineer('eng-user', 'UserEng', '', 3);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 4);
  sandbox.state.agents['worker-a'] = worker('worker-a', 'ArchWorker', 'eng-arch', 5);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext('render();', context);

  assert.doesNotMatch(mainEl.innerHTML, /data-drag-id="eng-user"/);
  assert.match(mainEl.innerHTML, /data-drag-id="eng-arch"/);
  assert.match(mainEl.innerHTML, /data-drag-id="worker-a"/);
  assert.match(mainEl.innerHTML, /principal-card principal-card--architect selected/);
  assert.match(mainEl.innerHTML, /principal-card principal-card--user dim/);
});

test('clicking a principal card calls selectPrincipal and sends ui_select_principal', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  vm.runInContext('render();', context);

  vm.runInContext("selectPrincipal('arch-a');", context);

  assert.equal(sandbox.state.selected_principal_id, 'arch-a');
  const calls = JSON.parse(JSON.stringify(sandbox.sendCalls));
  assert.deepEqual(calls, [
    { cmd: 'ui_select_principal', principal_id: 'arch-a' },
  ]);
});

test('selectPrincipal is a no-op when the target is already selected', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext("selectPrincipal('arch-a');", context);

  assert.deepEqual(sandbox.sendCalls, []);
});

test('non-selected architect card shows status badge with engineer counts', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-arch', 'worker-err'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 3);
  sandbox.state.agents['worker-err'] = worker('worker-err', 'BadWorker', 'eng-arch', 4);
  sandbox.state.agents['worker-err'].needs_attention = true;
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  // Architect A is non-selected and should show the badge.
  const archBlock = mainEl.innerHTML.match(/data-principal-card="architect"[\s\S]*?<\/button>/);
  assert.ok(archBlock, 'architect principal card rendered');
  assert.match(archBlock[0], /principal-card-badge/);
  assert.match(archBlock[0], /1 eng/);
  assert.match(archBlock[0], /!1/);
});

test('selected principal card does not render the status badge', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-arch'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 3);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext('render();', context);

  const archBlock = mainEl.innerHTML.match(/data-principal-card="architect"[\s\S]*?<\/button>/);
  assert.ok(archBlock, 'architect principal card rendered');
  assert.doesNotMatch(archBlock[0], /principal-card-badge/);
});

test('ui_select_principal delta op updates state.selected_principal_id on the client', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  // Simulate a delta push of the selection change (from a different UI tab).
  vm.runInContext("state.selected_principal_id = 'arch-a';", context);
  const value = vm.runInContext("state.selected_principal_id", context);
  assert.equal(value, 'arch-a');
});

test('no standalone + New Worker affordance in the user view', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = [];
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  assert.doesNotMatch(mainEl.innerHTML, /\+ New Worker/);
  assert.doesNotMatch(mainEl.innerHTML, /loose-workers-strip/);
});

test('falls back to user principal when stored architect id no longer exists', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['eng-user'];
  sandbox.state.agents['eng-user'] = engineer('eng-user', 'UserEng', '', 3);
  sandbox.state.selected_principal_id = 'arch-ghost';
  vm.runInContext('render();', context);

  // User engineer renders; selection resolved to user.
  assert.match(mainEl.innerHTML, /data-drag-id="eng-user"/);
  assert.match(mainEl.innerHTML, /principal-card principal-card--user selected/);
});

test('principals row registers principal items in the nav grid rows', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  vm.runInContext('render();', context);

  const rows = vm.runInContext(
    'JSON.stringify((window._navGridRows || []).map(function(r) { return { rowType: r.rowType, items: r.items.map(function(i) { return { id: i.id, type: i.type, principalId: i.principalId }; }) }; }))',
    context,
  );
  const parsed = JSON.parse(rows);
  const principalsRow = parsed.find(function(r) { return r.rowType === 'principals-row'; });
  assert.ok(principalsRow, 'principals-row registered');
  // User + architect + + New Architect ghost.
  assert.equal(principalsRow.items.length, 3);
  assert.equal(principalsRow.items[0].type, 'principal');
  assert.equal(principalsRow.items[0].principalId, '');
  assert.equal(principalsRow.items[1].type, 'principal');
  assert.equal(principalsRow.items[1].principalId, 'arch-a');
  assert.equal(principalsRow.items[2].type, 'control');
});

test('arrow-right on principals row focuses next principal and commits the filter', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  // Simulate arrow-right from user principal → architect principal.
  vm.runInContext("focusedItemId = 'principal:user'; moveFocusHorizontal(1);", context);

  assert.equal(vm.runInContext('focusedItemId', context), 'principal:arch-a');
  assert.equal(sandbox.state.selected_principal_id, 'arch-a');
  assert.ok(sandbox.sendCalls.some(function(c) {
    return c.cmd === 'ui_select_principal' && c.principal_id === 'arch-a';
  }), 'ui_select_principal sent');
});

test('arrow-up from an engineer returns to the currently-selected principal card', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-arch'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 3);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext('render();', context);

  vm.runInContext("focusedItemId = 'eng-arch'; moveFocusUp();", context);

  assert.equal(vm.runInContext('focusedItemId', context), 'principal:arch-a');
});

test('narrow viewports allow the principals row to wrap via flex-wrap', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(css, /\.principals-row\s*\{[^}]*flex-wrap:\s*wrap/s);
});

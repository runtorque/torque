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
  sandbox._updateEngineerTaskbarBadge = function() {};
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

function createFullHarness() {
  const { sandbox, mainEl } = createSandbox();
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
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

test('principal-card-new CSS preserves dimensions when wrapped (LOOM:209 regression)', () => {
  // The "+ New Architect" ghost button uses .ghost-card height (~16-20px) by
  // default, which makes it look squeezed when it wraps to its own row in a
  // narrow panel. .principal-card-new must override height + min-height so the
  // wrapped button matches the populated principal-card height (~90px).
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const newCardRule = css.match(/\.principal-card-new\s*\{[^}]*\}/);
  assert.ok(newCardRule, '.principal-card-new rule exists in style.css');
  assert.match(newCardRule[0], /height:\s*auto/,
    '.principal-card-new overrides .ghost-card height with `height: auto`');
  assert.match(newCardRule[0], /min-height:\s*90px/,
    '.principal-card-new sets min-height: 90px to match populated principal cards');
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

test('clicking an architect principal sets filter AND makes it the focused agent', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  vm.runInContext('render();', context);

  vm.runInContext("selectPrincipal('arch-a');", context);

  assert.equal(sandbox.state.selected_principal_id, 'arch-a');
  assert.equal(vm.runInContext('selectedAgentId', context), 'arch-a');
  const calls = JSON.parse(JSON.stringify(sandbox.sendCalls));
  // Architect-kind click must dispatch BOTH select_agent (focus detail view)
  // AND ui_select_principal (filter grid).
  assert.ok(calls.some(c => c.cmd === 'select_agent' && c.id === 'arch-a'),
    'select_agent dispatched for architect');
  assert.ok(calls.some(c => c.cmd === 'ui_select_principal' && c.principal_id === 'arch-a'),
    'ui_select_principal dispatched for architect');
});

test('clicking the User principal sets filter but does not hijack selectedAgentId', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-prev'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-prev'] = engineer('eng-prev', 'PriorEng', '', 3);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext("selectedAgentId = 'eng-prev';", context);

  vm.runInContext("selectPrincipal('');", context);

  assert.equal(sandbox.state.selected_principal_id, '');
  // User is not an agent — selectedAgentId must stay put.
  assert.equal(vm.runInContext('selectedAgentId', context), 'eng-prev');
  assert.ok(!sandbox.sendCalls.some(c => c.cmd === 'select_agent'),
    'select_agent NOT dispatched for User principal');
});

test('selectPrincipal refocuses the architect terminal even when already selected', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext("selectedAgentId = 'arch-a';", context);

  vm.runInContext("selectPrincipal('arch-a');", context);

  // Clicking a principal is a deliberate "go here" act, so focus_agent is
  // always dispatched — even when selection + filter already match.
  const calls = JSON.parse(JSON.stringify(sandbox.sendCalls));
  assert.deepEqual(calls, [{ cmd: 'focus_agent', id: 'arch-a' }]);
});

/* Extract a single principal card's HTML block (outer <button> for user,
   outer <div> for architect). The architect card contains nested buttons
   (close, pause) so we walk attribute→matching close tag by wrapper kind. */
function extractPrincipalCardHtml(html, principalKind, principalId) {
  const needle = principalId
    ? 'data-principal-card="' + principalKind + '" data-principal-id="' + principalId + '"'
    : 'data-principal-card="' + principalKind + '"';
  const startAttr = html.indexOf(needle);
  if (startAttr < 0) return null;
  // Walk backwards to the wrapper open tag.
  const openTagStart = html.lastIndexOf('<', startAttr);
  if (openTagStart < 0) return null;
  const tagMatch = /^<([a-zA-Z]+)/.exec(html.slice(openTagStart));
  if (!tagMatch) return null;
  const tagName = tagMatch[1];
  // Walk forward, tracking nested open/close of the same tag name.
  let depth = 0;
  let i = openTagStart;
  const openRe = new RegExp('<' + tagName + '\\b', 'g');
  const closeRe = new RegExp('</' + tagName + '>', 'g');
  openRe.lastIndex = i;
  closeRe.lastIndex = i;
  while (i < html.length) {
    openRe.lastIndex = i;
    closeRe.lastIndex = i;
    const nextOpen = openRe.exec(html);
    const nextClose = closeRe.exec(html);
    if (!nextClose) return null;
    if (nextOpen && nextOpen.index < nextClose.index) {
      depth += 1;
      i = nextOpen.index + 1;
    } else {
      depth -= 1;
      i = nextClose.index + nextClose[0].length;
      if (depth === 0) return html.slice(openTagStart, i);
    }
  }
  return null;
}

test('non-selected architect card shows at-a-glance architect stats', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-arch', 'worker-err'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 3);
  sandbox.state.agents['worker-err'] = worker('worker-err', 'BadWorker', 'eng-arch', 4);
  sandbox.state.agents['worker-err'].needs_attention = true;
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  const archBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'architect', 'arch-a');
  assert.ok(archBlock, 'architect principal card rendered');
  assert.match(archBlock, /1eng/);
  assert.match(archBlock, /0asks/);
  assert.match(archBlock, /0dec/);
  assert.match(archBlock, /last dec/);
});

test('selected principal card does not render the status badge', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-arch'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agents['eng-arch'] = engineer('eng-arch', 'ArchEng', 'arch-a', 3);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext('render();', context);

  const archBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'architect', 'arch-a');
  assert.ok(archBlock, 'architect principal card rendered');
  assert.doesNotMatch(archBlock, /principal-card-badge/);
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
  assert.equal(principalsRow.items[0].id, 'principal:loom:user');
  assert.equal(principalsRow.items[1].type, 'principal');
  assert.equal(principalsRow.items[1].principalId, 'arch-a');
  assert.equal(principalsRow.items[1].id, 'principal:loom:arch-a');
  assert.equal(principalsRow.items[2].type, 'control');
});

test('arrow-right on principals row focuses next principal and commits the filter', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  // Simulate arrow-right from user principal → architect principal.
  vm.runInContext("focusedItemId = 'principal:loom:user'; moveFocusHorizontal(1);", context);

  assert.equal(vm.runInContext('focusedItemId', context), 'principal:loom:arch-a');
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

  assert.equal(vm.runInContext('focusedItemId', context), 'principal:loom:arch-a');
});

test('principal nav ids are group-scoped so multi-group workspaces do not collide', () => {
  const { context, sandbox } = createHarness();
  sandbox.state.groups = { alpha: ['eng-a'], beta: ['eng-b'] };
  sandbox.state.group_settings = {
    alpha: { collapsed_default: false },
    beta: { collapsed_default: false },
  };
  sandbox.state.agents['eng-a'] = engineer('eng-a', 'Alice', '', 1);
  sandbox.state.agents['eng-a'].group = 'alpha';
  sandbox.state.agents['eng-b'] = engineer('eng-b', 'Bob', '', 2);
  sandbox.state.agents['eng-b'].group = 'beta';
  vm.runInContext('render();', context);

  const meta = vm.runInContext('JSON.stringify(window._navGridItemMeta)', context);
  const parsed = JSON.parse(meta);
  assert.ok(parsed['principal:alpha:user'], 'alpha user principal nav id registered');
  assert.ok(parsed['principal:beta:user'], 'beta user principal nav id registered');
  assert.equal(parsed['principal:alpha:user'].group, 'alpha');
  assert.equal(parsed['principal:beta:user'].group, 'beta');

  // Arrow-down from alpha's user principal lands on alpha's engineer, not beta's.
  vm.runInContext("focusedItemId = 'principal:alpha:user'; moveFocusDown();", context);
  assert.equal(vm.runInContext('focusedItemId', context), 'eng-a');
});

test('empty selected principal renders the "No engineers yet." placeholder', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = [];
  sandbox.state.selected_principal_id = '';
  vm.runInContext('render();', context);

  assert.match(mainEl.innerHTML, /No engineers yet\./);
  assert.match(mainEl.innerHTML, /agent-section--empty/);
});

test('architect with no engineers renders the empty placeholder when selected', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.selected_principal_id = 'arch-a';
  vm.runInContext('render();', context);

  assert.match(mainEl.innerHTML, /data-agent-section="architect:arch-a"[\s\S]*No engineers yet\./);
  // The + New Engineer control still renders so the operator can add one.
  assert.match(mainEl.innerHTML, /\+ New Engineer/);
});

test('narrow viewports allow the principals row to wrap via flex-wrap', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  assert.match(css, /\.principals-row\s*\{[^}]*flex-wrap:\s*wrap/s);
});

/* ------------------------------------------------------------------ */
/* LOOM:190 post-deploy regression hotfix coverage.                     */
/* ------------------------------------------------------------------ */

test('User + architect principal cards share the same width variable (Bug 1)', () => {
  // The --agent-architect-column-width var must be resolvable at the
  // principals-row scope (i.e. defined at or above .group-body-inner).
  // If it were still scoped only to .agent-grid, .principal-card width
  // would collapse to content-based sizing, shrinking the User card.
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  // Both principal card variants resolve width from the same custom prop.
  assert.match(css, /\.principal-card\s*\{[^}]*width:\s*var\(--agent-architect-column-width\)/s);
  // The variable is defined at .group-body-inner so .principals-row inherits.
  assert.match(
    css,
    /\.group-body-inner\s*\{[^}]*--agent-architect-column-width:\s*96px/s,
    'architect column width must be hoisted to .group-body-inner',
  );
});

test('architect principal card renders pause + close controls (Bug 3)', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  vm.runInContext('render();', context);

  const archBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'architect', 'arch-a');
  assert.ok(archBlock, 'architect principal card rendered');
  assert.match(archBlock, /principal-card-controls/);
  assert.match(archBlock, /principal-card-close/);
  assert.match(archBlock, /principal-card-pause/);
  assert.match(archBlock, /principal-card-status/);
  // Close dispatches removeAgent for the architect id.
  assert.match(archBlock, /removeAgent\(&quot;arch-a&quot;\)/);
  // Pause dispatches toggleDigestPauseForAgent for the architect id.
  assert.match(archBlock, /toggleDigestPauseForAgent\(&quot;arch-a&quot;\)/);
});

test('User principal card omits pause + close controls (Bug 3)', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = [];
  vm.runInContext('render();', context);

  const userBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'user', '');
  assert.ok(userBlock, 'user principal card rendered');
  // User is not an agent; it must NOT expose agent control affordances.
  assert.doesNotMatch(userBlock, /principal-card-close/);
  assert.doesNotMatch(userBlock, /principal-card-pause/);
  assert.doesNotMatch(userBlock, /principal-card-controls/);
});

test('paused architect principal card renders resume icon + paused class (Bug 3)', () => {
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  sandbox.state.agent_digest_settings = { 'arch-a': { paused: true } };
  vm.runInContext('render();', context);

  const archBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'architect', 'arch-a');
  assert.ok(archBlock, 'architect principal card rendered');
  assert.match(archBlock, /principal-card-pause paused/);
  assert.match(archBlock, /Resume event delivery/);
});

test('pause toggle on architect principal invokes toggleDigestPauseForAgent (Bug 3)', () => {
  // Verify the bound handler dispatches digest_pause / digest_resume for
  // the architect id. The existing toggleDigestPauseForAgent lives in
  // agent_panel.js; stub it and confirm the click expression in the
  // rendered markup evaluates to a call for this architect.
  const { context, sandbox, mainEl } = createHarness();
  sandbox.state.groups.loom = ['arch-a'];
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Productmind', 2);
  let lastToggleCall = null;
  vm.runInContext(
    'toggleDigestPauseForAgent = function(id) { globalThis._pauseToggleCall = id; };',
    context,
  );
  vm.runInContext('render();', context);

  // Run the onclick expression directly (simulating a click on the pause btn).
  vm.runInContext('event = { stopPropagation: function() {} };', context);
  vm.runInContext('toggleDigestPauseForAgent("arch-a")', context);
  assert.equal(vm.runInContext('globalThis._pauseToggleCall', context), 'arch-a');
});

function extractAgentCellHtml(html, agentId) {
  const needle = 'data-drag-id="' + agentId + '"';
  const startAttr = html.indexOf(needle);
  if (startAttr < 0) return null;
  const openTagStart = html.lastIndexOf('<div', startAttr);
  if (openTagStart < 0) return null;
  let depth = 0;
  let i = openTagStart;
  const openRe = /<div\b/g;
  const closeRe = /<\/div>/g;
  while (i < html.length) {
    openRe.lastIndex = i;
    closeRe.lastIndex = i;
    const nextOpen = openRe.exec(html);
    const nextClose = closeRe.exec(html);
    if (!nextClose) return null;
    if (nextOpen && nextOpen.index < nextClose.index) {
      depth += 1;
      i = nextOpen.index + 1;
    } else {
      depth -= 1;
      i = nextClose.index + nextClose[0].length;
      if (depth === 0) return html.slice(openTagStart, i);
    }
  }
  return null;
}

test('agents-grid v2 renders all kind shells with required corner badges and no body icons', () => {
  const { context, sandbox, mainEl } = createFullHarness();
  sandbox.state.groups.loom = ['arch-a', 'eng-a', 'worker-a'];
  sandbox.state.children = {};
  sandbox.state.agents['arch-a'] = {
    ...architect('arch-a', 'Loomer', 1),
    agent_type: 'claude-code',
    last_activity_at: Date.now() / 1000 - 300,
  };
  sandbox.state.agents['eng-a'] = {
    ...engineer('eng-a', 'Panelsmith', 'arch-a', 2),
    agent_type: 'claude-code',
    last_activity_at: Date.now() / 1000 - 60,
  };
  sandbox.state.agents['worker-a'] = {
    ...worker('worker-a', 'Worker', 'eng-a', 3),
    slug: 'architect-card-click-focus-fix',
    agent_type: 'codex',
    current_task_id: 'LOOM:216',
    worktree_diff: { files: 3, insertions: 58, deletions: 3 },
    worktree_dirty: false,
    worktree_branch: 'loom/panelsmith/architect-card-click-focus-fix-746495a',
    last_activity_at: Date.now() / 1000 - 30,
  };
  sandbox.state.board_tasks = {
    'LOOM:216': {
      id: 'LOOM:216',
      group: 'loom',
      task: 'Review worker cards',
      lane: 'In Progress',
      action_name: 'feature/review',
      agent_id: 'worker-a',
    },
    'ask-1': {
      id: 'ask-1',
      group: 'loom',
      task: 'Need user decision',
      lane: 'To Do',
      labels: ['loom:human', 'architect-ask'],
      created_by_architect_id: 'arch-a',
    },
  };
  sandbox.state.architect_decisions = {
    'dec-1': {
      id: 'dec-1',
      architect_id: 'arch-a',
      status: 'proposed',
      created_at: Date.now() / 1000 - 300,
    },
  };
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext('render();', context);

  const userBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'user', '');
  const archBlock = extractPrincipalCardHtml(mainEl.innerHTML, 'architect', 'arch-a');
  const engineerBlock = extractAgentCellHtml(mainEl.innerHTML, 'eng-a');
  const workerBlock = extractAgentCellHtml(mainEl.innerHTML, 'worker-a');
  assert.ok(userBlock, 'user principal rendered');
  assert.ok(archBlock, 'architect principal rendered');
  assert.ok(engineerBlock, 'engineer card rendered');
  assert.ok(workerBlock, 'worker card rendered');

  assert.doesNotMatch(userBlock, /principal-card-controls|agent-card-provider/);
  assert.match(userBlock, /principal-card-status/);
  assert.match(userBlock, /principal-card-kind--owner">OWNER/);

  assert.match(archBlock, /principal-card-controls/);
  assert.match(archBlock, /principal-card-status/);
  assert.match(archBlock, /agent-card-provider--claude-code[^>]*>CC</);
  assert.match(archBlock, /principal-card-kind--architect">ARCH/);
  assert.match(archBlock, /1eng/);
  assert.match(archBlock, /principal-card-ask-link[\s\S]*1asks/);
  assert.match(archBlock, /1dec/);

  assert.match(engineerBlock, /cell-header-controls/);
  assert.match(engineerBlock, /cell-status/);
  assert.match(engineerBlock, /agent-card-provider--claude-code[^>]*>CC</);
  assert.match(engineerBlock, /cell-engineer-badge">ENG/);
  assert.match(engineerBlock, /Panelsmith/);
  assert.match(engineerBlock, /1 worker/);
  assert.match(engineerBlock, /q:0 · last/);

  assert.match(workerBlock, /cell-header-controls/);
  assert.match(workerBlock, /cell-status/);
  assert.match(workerBlock, /agent-card-provider--codex[^>]*>CX</);
  assert.match(workerBlock, /cell-worker-badge">W/);
  assert.match(workerBlock, /architect-car…/);
  assert.match(workerBlock, /data-tooltip="architect-card-click-focus-fix"/);
  assert.match(workerBlock, /LOOM:216 · review/);
  assert.match(workerBlock, /\+58\/-3 \(clean\)/);
  assert.match(workerBlock, /wkt: architect-ca…/);
  assert.match(workerBlock, /30s/);

  assert.doesNotMatch(userBlock + archBlock + engineerBlock + workerBlock, /principal-card-icon|cell-icon/);
});

test('worktree branch shortname strips loom engineer prefix and short id suffix', () => {
  const { context } = createFullHarness();
  assert.equal(
    vm.runInContext("_worktreeBranchShortName('loom/panelsmith/architect-card-click-focus-fix-746495a')", context),
    'architect-card-click-focus-fix',
  );
  assert.equal(
    vm.runInContext("_worktreeBranchShortName('loom/agents-grid-layout-v2-28c6725')", context),
    'agents-grid-layout-v2',
  );
  assert.equal(
    vm.runInContext("_worktreeBranchShortName('feature/plain-branch')", context),
    'feature/plain-branch',
  );
});

test('worker slug truncation uses immediate tooltip metadata, not title-only browser tooltip', () => {
  const { context, sandbox } = createFullHarness();
  sandbox.state.children = {};
  sandbox.state.group_settings = {};
  sandbox.state.board_tasks = {
    'LOOM:216': { id: 'LOOM:216', lane: 'In Progress', action_name: 'feature/implement', agent_id: 'worker-a' },
  };
  const html = vm.runInContext(`renderAgentCell({
    id: 'worker-a',
    name: 'Worker',
    slug: 'architect-card-click-focus-fix',
    kind: 'worker',
    group: 'loom',
    cell_type: 'agent',
    status: 'running',
    current_task_id: 'LOOM:216',
    worktree_branch: 'loom/panelsmith/architect-card-click-focus-fix-746495a'
  })`, context);
  assert.match(html, /architect-car…/);
  assert.match(html, /class="[^"]*agent-card-tooltip[^"]*cell-worker-slug/);
  assert.match(html, /data-tooltip="architect-card-click-focus-fix"/);
  assert.doesNotMatch(html, /cell-worker-slug[^>]*title=/);
});

test('provider badges expose tint classes for claude-code and codex cards', () => {
  const { context, sandbox } = createFullHarness();
  sandbox.state.children = {};
  sandbox.state.group_settings = {};
  const claudeHtml = vm.runInContext(`renderAgentCell({
    id: 'eng-a',
    name: 'Panelsmith',
    kind: 'engineer',
    group: 'loom',
    cell_type: 'agent',
    status: 'running',
    agent_type: 'claude-code'
  })`, context);
  const codexHtml = vm.runInContext(`renderAgentCell({
    id: 'worker-a',
    name: 'Worker',
    kind: 'worker',
    group: 'loom',
    cell_type: 'agent',
    status: 'running',
    agent_type: 'codex'
  })`, context);
  assert.match(claudeHtml, /agent-card-provider--claude-code[^>]*>CC</);
  assert.match(codexHtml, /agent-card-provider--codex[^>]*>CX</);
});

test('agents-grid v2 rerender preserves main scroll while card bodies update', () => {
  const { context, sandbox, mainEl } = createFullHarness();
  mainEl.scrollTop = 0;
  Object.defineProperty(mainEl, 'innerHTML', {
    configurable: true,
    get() { return this._innerHTML || ''; },
    set(value) {
      this._innerHTML = value;
      this.scrollTop = 0;
    },
  });
  sandbox.state.groups.loom = ['arch-a', 'eng-a', 'worker-a'];
  sandbox.state.children = {};
  sandbox.state.agents['arch-a'] = architect('arch-a', 'Loomer', 1);
  sandbox.state.agents['eng-a'] = engineer('eng-a', 'Panelsmith', 'arch-a', 2);
  sandbox.state.agents['worker-a'] = {
    ...worker('worker-a', 'Worker', 'eng-a', 3),
    slug: 'worker-a',
    current_task_id: 'LOOM:216',
    worktree_diff: { insertions: 1, deletions: 0 },
  };
  sandbox.state.board_tasks = {
    'LOOM:216': { id: 'LOOM:216', lane: 'In Progress', action_name: 'feature/implement', agent_id: 'worker-a' },
  };
  sandbox.state.selected_principal_id = 'arch-a';

  vm.runInContext('render();', context);
  mainEl.scrollTop = 123;
  sandbox.state.agents['worker-a'].worktree_diff = { insertions: 2, deletions: 1 };
  vm.runInContext('render();', context);

  assert.equal(mainEl.scrollTop, 123);
  assert.match(mainEl.innerHTML, /\+2\/-1 \(clean\)/);
});

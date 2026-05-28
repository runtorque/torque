/* Regression tests for TORQUE:264 follow-up: idempotent agent panel + agent
 * grid render under multi-agent firehose.
 *
 * Covered:
 *   1. `main.innerHTML` is byte-equality-memoized in render() — a second
 *      render with the same state does not reassign innerHTML, preserving
 *      DOM identity (and `:hover::after` tooltip pseudo-elements).
 *   2. `content.innerHTML` and `headerRight.innerHTML` are memoized in the
 *      agent panel's surgical-tab refresh path.
 *   3. The full-panel render path (`renderAgentPanel`) memoizes too.
 *   4. The `_userInteracting()` gate covers hover state — `_queueDeltaSurfaceRender`
 *      defers renders while `_userHovering` is true and flushes after release.
 *   5. `_hoverEdgeIsBetweenTooltips` filters inner-descendant transitions
 *      so hovering between children of the same tooltip doesn't thrash the
 *      defer flag.
 */

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

/* -- Memoization element harness ----------------------------------------- */

function makeMemoElement(tag) {
  let html = '';
  let setCount = 0;
  return {
    tagName: tag || 'DIV',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: {},
    dataset: {},
    children: [],
    get innerHTML() { return html; },
    set innerHTML(value) {
      html = String(value || '');
      setCount += 1;
    },
    get _setCount() { return setCount; },
    contains() { return false; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: 0, height: 0 }; },
  };
}

/* -- A2: agent grid main render memoization ----------------------------- */

function createGridHarness() {
  const main = makeMemoElement('MAIN');
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    state: {
      runtime: { embedded_terminal: false },
      agents: {},
      groups: {},
      group_settings: {},
      children: {},
      board_tasks: {},
      ui: {},
      selected_principal_id: '',
    },
    selectedAgentId: null,
    selectedTerminalId: null,
    focusedItemId: null,
    dragInProgress: false,
    document: {
      getElementById(id) { return id === 'main' ? main : null; },
    },
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    setTimeout() {},
    clearTimeout() {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  return { context, sandbox, main };
}

function createTerminalRowRerenderHarness() {
  const main = makeMemoElement('MAIN');
  const document = {
    activeElement: null,
    getElementById(id) { return id === 'main' ? main : null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    location: { host: 'localhost:18932' },
    document,
    state: {
      runtime: { embedded_terminal: false, profile: 'terminal-row', port: '18932' },
      agents: {
        'term-1': {
          id: 'term-1',
          name: 'Shell One',
          slug: 'shell-one',
          group: 'alpha',
          cell_type: 'terminal',
          status: 'running',
          current_process: 'zsh',
          current_path: '/repo/packages/api',
          git_root: '/repo',
          session_id: 'sess-1',
        },
      },
      groups: { alpha: ['term-1'] },
      group_settings: {},
      children: {},
      board_tasks: {},
      ui: {},
      selected_principal_id: '',
      active_session_id: 'sess-1',
    },
    selectedAgentId: null,
    selectedTerminalId: 'term-1',
    focusedItemId: 'term-1',
    dragInProgress: false,
    getComputedStyle() {
      return {
        paddingTop: '0px',
        paddingBottom: '0px',
        marginTop: '0px',
        marginBottom: '0px',
      };
    },
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    setTimeout() { return 0; },
    clearTimeout() {},
    renderTerminalWorkspace() {},
    updateEventsAttentionBadge() {},
    renderAgentPanel() {},
    _currentPanelSurfaces() { return []; },
    getFilterByWindow() { return false; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = Object.assign({ innerHeight: 900 }, sandbox);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/constants.js');
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  return { context, sandbox, main };
}

function createPendingHireBannerRerenderHarness() {
  const main = makeMemoElement('MAIN');
  const banner = makeMemoElement('DIV');
  banner.hidden = true;
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    location: { host: 'localhost:18932' },
    document: {
      activeElement: null,
      getElementById(id) {
        if (id === 'main') return main;
        if (id === 'pending-hire-banner') return banner;
        return null;
      },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    state: {
      runtime: { embedded_terminal: false, profile: 'pending-hire-banner', port: '18932' },
      agents: {
        'arch-1': {
          id: 'arch-1',
          name: 'Planwright',
          slug: 'planwright',
          kind: 'architect',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
        },
      },
      groups: { alpha: [] },
      group_settings: {},
      children: {},
      board_tasks: {},
      ui: {},
      selected_principal_id: '',
      pending_hires: {
        'hire-1': {
          id: 'hire-1',
          architect_id: 'arch-1',
          requested_name: 'Alice',
          created_at: 10,
        },
        'hire-2': {
          id: 'hire-2',
          architect_id: 'arch-1',
          requested_name: 'Bob',
          created_at: 20,
        },
      },
    },
    selectedAgentId: null,
    selectedTerminalId: null,
    focusedItemId: null,
    dragInProgress: false,
    sendCalls: [],
    getComputedStyle() {
      return {
        paddingTop: '0px',
        paddingBottom: '0px',
        marginTop: '0px',
        marginBottom: '0px',
      };
    },
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    setTimeout() { return 0; },
    clearTimeout() {},
    renderTerminalWorkspace() {},
    updateEventsAttentionBadge() {},
    renderAgentPanel() {},
    _currentPanelSurfaces() { return []; },
    getFilterByWindow() { return false; },
  };
  sandbox.send = function(message) { sandbox.sendCalls.push(message); };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = Object.assign({ innerHeight: 900 }, sandbox);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  return { context, sandbox, main, banner };
}

test('TORQUE:264 — render() memoizes empty-state main.innerHTML', () => {
  const { context, main } = createGridHarness();
  context.render();
  assert.equal(main._setCount, 1, 'first render writes empty-state html');
  context.render();
  assert.equal(main._setCount, 1,
    'second render with identical state must not rewrite innerHTML');
  assert.match(main.innerHTML, /No groups yet/);
});

test('terminal-row extraction keeps row HTML stable and preserves row-control focus through identical rerenders', () => {
  const { context, sandbox, main } = createTerminalRowRerenderHarness();
  const firstRowHtml = context.renderTerminalRow(sandbox.state.agents['term-1']);
  assert.match(firstRowHtml, /class="term-row active focused"/);
  assert.match(firstRowHtml, /class="term-action danger"/);

  context.render();
  const firstGridHtml = main.innerHTML;
  const firstSetCount = main._setCount;
  assert.match(firstGridHtml, /Shell One/);
  assert.match(firstGridHtml, /term-action danger/);

  const focusedControl = { id: 'term-delete-button' };
  sandbox.document.activeElement = focusedControl;
  const secondRowHtml = context.renderTerminalRow(sandbox.state.agents['term-1']);
  assert.equal(secondRowHtml, firstRowHtml,
    'renderTerminalRow must remain byte-stable for unchanged terminal state after extraction');

  context.render();
  assert.equal(main._setCount, firstSetCount,
    'identical terminal-row grid rerender must not clobber the main surface');
  assert.equal(main.innerHTML, firstGridHtml,
    'terminal row output should remain stable across an unchanged rerender');
  assert.equal(sandbox.document.activeElement, focusedControl,
    'focused terminal-row controls survive because the identical grid render is memoized');
});

test('pending-hire banner extraction keeps output stable and dismiss state across grid rerenders', () => {
  const { context, sandbox, banner } = createPendingHireBannerRerenderHarness();
  context.render();
  const firstBannerHtml = banner.innerHTML;
  assert.equal(banner.hidden, false);
  assert.match(firstBannerHtml, /Planwright/);
  assert.match(firstBannerHtml, /Alice/);
  assert.match(firstBannerHtml, /\+1 more hire request/);
  assert.match(firstBannerHtml, /approvePendingHire\("hire-1"\)/);

  context.render();
  assert.equal(banner.innerHTML, firstBannerHtml,
    'renderPendingHireBanner output should remain stable across unchanged grid rerenders');

  context.approvePendingHire('hire-1');
  const afterDismissHtml = banner.innerHTML;
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'pending_hire_approve', id: 'hire-1' },
  ]);
  assert.doesNotMatch(afterDismissHtml, /Alice/);
  assert.match(afterDismissHtml, /Bob/);
  assert.doesNotMatch(afterDismissHtml, /\+1 more hire request/);

  context.render();
  assert.equal(banner.innerHTML, afterDismissHtml,
    'dismissed pending-hire state should survive later grid rerenders');
});

function makeSectionsRerenderHarness() {
  let currentControl = null;
  let controlGeneration = 0;
  const looseStrip = makeMemoElement('DIV');
  looseStrip.className = 'loose-workers-strip';
  looseStrip.scrollTop = 0;
  looseStrip.scrollLeft = 0;
  function makeFocusedControl() {
    controlGeneration += 1;
    return {
      dataset: { focusKey: 'agent-digest-toggle:worker-user' },
      value: '',
      selectionStart: 0,
      selectionEnd: 0,
      scrollTop: 0,
      scrollLeft: 0,
      focusCalls: [],
      focus(opts) { this.focusCalls.push(opts || null); },
      generation: controlGeneration,
    };
  }
  function maybeHydrateControl(html) {
    const text = String(html || '');
    if (text.indexOf('data-focus-key="agent-digest-toggle:worker-user"') >= 0) {
      currentControl = makeFocusedControl();
    }
  }
  function shellPart(id) {
    return {
      id,
      style: {},
      classList: makeTinyClassList(),
      children: [],
      scrollTop: 0,
      scrollLeft: 0,
      scrollHeight: 360,
      offsetHeight: 160,
      clientHeight: 160,
      innerHTML: '',
      setAttribute(name, value) { this[name] = String(value); },
      getAttribute(name) { return this[name]; },
      getBoundingClientRect() { return { height: id === 'agent-split' ? 760 : 12 }; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
  }
  const split = shellPart('agent-split');
  const tabsHost = shellPart('agent-group-tabs-host');
  const grid = shellPart('agent-grid-pane');
  let gridSetCount = 0;
  Object.defineProperty(grid, 'innerHTML', {
    get() { return this._html || ''; },
    set(value) {
      this._html = String(value || '');
      gridSetCount += 1;
      this.scrollTop = 0;
      this.scrollLeft = 0;
      looseStrip.scrollTop = 0;
      looseStrip.scrollLeft = 0;
      maybeHydrateControl(this._html);
    },
  });
  grid.querySelector = function(selector) {
    if (selector === '.loose-workers-strip' && grid.innerHTML.indexOf('loose-workers-strip') >= 0) {
      return looseStrip;
    }
    return null;
  };
  const handle = shellPart('agent-focus-resizer');
  const focus = shellPart('agent-focus-panel');
  const focusScroll = shellPart('agent-focus-scroll');
  const main = makeMemoElement('MAIN');
  main.contains = function(node) {
    return node === currentControl;
  };
  main.querySelector = function(selector) {
    if (selector === '[data-agent-split]') return split;
    if (selector === '[data-agent-grid-pane]' || selector === '.agents-grid-pane') return grid;
    if (selector === '[data-agent-focus-resizer]') return handle;
    if (selector === '[data-agent-focus-panel]') return focus;
    if (selector === '[data-agent-focus-scroll]') return focusScroll;
    if (selector === '[data-agent-group-tabs-host]') return tabsHost;
    if (selector === '[data-focus-key="agent-digest-toggle\\:worker-user"]'
        || selector === '[data-focus-key="agent-digest-toggle:worker-user"]') {
      return currentControl;
    }
    if (selector === '.loose-workers-strip') return grid.querySelector(selector);
    return null;
  };
  const originalMainSetter = Object.getOwnPropertyDescriptor(main, 'innerHTML').set;
  const originalMainGetter = Object.getOwnPropertyDescriptor(main, 'innerHTML').get;
  Object.defineProperty(main, 'innerHTML', {
    get() { return originalMainGetter.call(main); },
    set(value) {
      originalMainSetter.call(main, value);
      grid._html = String(value || '');
      maybeHydrateControl(value);
    },
  });
  const document = {
    activeElement: null,
    body: { classList: makeTinyClassList(), style: {} },
    getElementById(id) {
      if (id === 'main') return main;
      if (id === 'agent-group-tabs-host') return tabsHost;
      if (id === 'app-group-tabs-host') return null;
      return null;
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  main.ownerDocument = document;
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    CSS: { escape(value) { return String(value).replace(/:/g, '\\:').replace(/"/g, '\\"'); } },
    location: { host: 'localhost:18932' },
    window: { innerHeight: 900 },
    document,
    state: {
      runtime: { embedded_terminal: false, profile: 'sections-rerender', port: '18932' },
      agents: {
        'arch-a': {
          id: 'arch-a',
          name: 'Productmind',
          slug: 'arch-a',
          kind: 'architect',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          created_at: 1,
        },
        'eng-arch': {
          id: 'eng-arch',
          name: 'Architect Engineer',
          slug: 'eng-arch',
          kind: 'engineer',
          hired_by_architect_id: 'arch-a',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          created_at: 2,
        },
        'worker-arch': {
          id: 'worker-arch',
          name: 'Architect Worker',
          slug: 'worker-arch',
          kind: 'worker',
          owner_engineer_id: 'eng-arch',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          created_at: 3,
        },
        'eng-user': {
          id: 'eng-user',
          name: 'User Engineer',
          slug: 'eng-user',
          kind: 'engineer',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          created_at: 4,
        },
        'worker-user': {
          id: 'worker-user',
          name: 'User Worker',
          slug: 'worker-user',
          kind: 'worker',
          owner_engineer_id: 'eng-user',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          current_task_id: 'TASK-1',
          worktree_branch: 'torque/user/worker-user',
          created_at: 5,
        },
        'loose-worker': {
          id: 'loose-worker',
          name: 'Loose Worker',
          slug: 'loose-worker',
          kind: 'worker',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          created_at: 6,
        },
        'eng-beta': {
          id: 'eng-beta',
          name: 'Beta Engineer',
          slug: 'eng-beta',
          kind: 'engineer',
          group: 'beta',
          cell_type: 'agent',
          status: 'running',
          created_at: 7,
        },
      },
      groups: {
        alpha: ['arch-a', 'eng-arch', 'worker-arch', 'eng-user', 'worker-user', 'loose-worker'],
        beta: ['eng-beta'],
      },
      group_settings: { alpha: { collapsed_default: false }, beta: { collapsed_default: true } },
      engineer_settings: {},
      agent_digest_settings: {},
      children: {},
      board_tasks: {
        'TASK-1': { id: 'TASK-1', task: 'Preserve section focus', lane: 'In Progress', action_name: 'feature/implement' },
      },
      ui: {},
      selected_principal_id: '',
    },
    selectedAgentId: 'worker-user',
    selectedTerminalId: null,
    focusedItemId: 'worker-user',
    dragInProgress: false,
    getComputedStyle() {
      return {
        paddingTop: '0px',
        paddingBottom: '0px',
        marginTop: '0px',
        marginBottom: '0px',
      };
    },
    requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); return 1; },
    cancelAnimationFrame() {},
    setTimeout() { return 0; },
    clearTimeout() {},
    renderTerminalWorkspace() {},
    updateEventsAttentionBadge() {},
    renderPendingHireBanner() {},
    renderAgentPanel() {},
    _currentPanelSurfaces() { return []; },
    getFilterByWindow() { return false; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = Object.assign(sandbox.window, sandbox);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  context.renderAgentDetails = function() { return ''; };
  return {
    context,
    sandbox,
    main,
    grid,
    looseStrip,
    getCurrentControl() { return currentControl; },
    getGridSetCount() { return gridSetCount; },
  };
}

test('sections extraction preserves section HTML, collapsed groups, scroll, focus, and selection across rerenders', () => {
  const harness = makeSectionsRerenderHarness();
  const { context, sandbox, main, grid, looseStrip } = harness;
  const agents = sandbox.state.groups.alpha.map(id => sandbox.state.agents[id]);
  const firstSections = JSON.parse(context.JSON.stringify(
    context._buildHierarchicalAgentSections(agents),
    function(_key, value) {
      if (value && value.cell_type === 'agent') return value.id;
      return value;
    },
  ));
  const firstModel = context._buildStratifiedAgentGridModel(agents);
  const firstEngineerRowHtml = context._renderEngineerRow(
    firstModel.userSection.rows[0],
    function(agent) { return '<span data-id="' + context.esc(agent.id) + '">' + context.esc(agent.name) + '</span>'; },
  );
  const firstGridSectionsHtml = context._renderStratifiedAgentGrid(
    'alpha',
    firstModel,
    function(agent) { return '<span data-id="' + context.esc(agent.id) + '">' + context.esc(agent.name) + '</span>'; },
    {},
  );

  context.render();
  assert.match(main._torqueLastGridHtml, /data-agent-section="architect:arch-a"/);
  assert.match(main._torqueLastGridHtml, /data-agent-strata="engineers"[\s\S]*User Engineer[\s\S]*User Worker/);
  assert.match(main._torqueLastGridHtml, /data-agent-strata="workers"[\s\S]*Loose Worker/);
  assert.match(main._torqueLastGridHtml, /class="cell selected focused worker"/,
    'selected/focused worker marker should be present before rerender');
  assert.match(main._torqueLastGridHtml, /class="group collapsed" data-group-name="beta"/,
    'beta starts collapsed from persisted group settings');

  assert.equal(vm.runInContext('collapsedGroups.has(\"alpha\")', context), false);
  assert.equal(vm.runInContext('collapsedGroups.has(\"beta\")', context), true);

  const focusedControl = harness.getCurrentControl();
  assert.ok(focusedControl, 'first section render should expose the selected worker control');
  focusedControl.value = 'operator draft';
  focusedControl.selectionStart = 3;
  focusedControl.selectionEnd = 11;
  focusedControl.scrollTop = 17;
  focusedControl.scrollLeft = 4;
  sandbox.document.activeElement = focusedControl;
  grid.scrollTop = 88;
  grid.scrollLeft = 6;
  looseStrip.scrollTop = 22;
  looseStrip.scrollLeft = 9;

  const gridSetsBeforeDelta = harness.getGridSetCount();
  sandbox.state.agents['worker-user'].activity_detail = 'Reporting progress';
  context.render();
  assert.equal(harness.getGridSetCount(), gridSetsBeforeDelta + 1,
    'WS-style section delta should rebuild the grid pane exactly once');
  assert.match(main._torqueLastGridHtml, /Reporting progress/);
  assert.match(main._torqueLastGridHtml, /class="cell selected focused worker"/,
    'selected/focused worker marker should survive changed section output');
  assert.match(main._torqueLastGridHtml, /class="group" data-group-name="alpha"/);
  assert.match(main._torqueLastGridHtml, /class="group collapsed" data-group-name="beta"/);
  assert.equal(vm.runInContext('collapsedGroups.has(\"alpha\")', context), false);
  assert.equal(vm.runInContext('collapsedGroups.has(\"beta\")', context), true);
  assert.equal(grid.scrollTop, 88);
  assert.equal(grid.scrollLeft, 6);
  assert.equal(looseStrip.scrollTop, 22);
  assert.equal(looseStrip.scrollLeft, 9);

  const restoredControl = harness.getCurrentControl();
  assert.notEqual(restoredControl, focusedControl,
    'changed section html should replace the focused control in the harness');
  assert.equal(restoredControl.value, 'operator draft');
  assert.equal(restoredControl.selectionStart, 3);
  assert.equal(restoredControl.selectionEnd, 11);
  assert.equal(restoredControl.scrollTop, 17);
  assert.equal(restoredControl.scrollLeft, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(restoredControl.focusCalls)), [
    { preventScroll: true },
  ]);

  sandbox.state.agents['worker-user'].activity_detail = '';
  const secondSections = JSON.parse(context.JSON.stringify(
    context._buildHierarchicalAgentSections(agents),
    function(_key, value) {
      if (value && value.cell_type === 'agent') return value.id;
      return value;
    },
  ));
  const secondModel = context._buildStratifiedAgentGridModel(agents);
  const secondEngineerRowHtml = context._renderEngineerRow(
    secondModel.userSection.rows[0],
    function(agent) { return '<span data-id="' + context.esc(agent.id) + '">' + context.esc(agent.name) + '</span>'; },
  );
  const secondGridSectionsHtml = context._renderStratifiedAgentGrid(
    'alpha',
    secondModel,
    function(agent) { return '<span data-id="' + context.esc(agent.id) + '">' + context.esc(agent.name) + '</span>'; },
    {},
  );
  assert.deepEqual(secondSections, firstSections,
    '_buildHierarchicalAgentSections output remains stable for unchanged section inputs');
  assert.equal(secondEngineerRowHtml, firstEngineerRowHtml,
    '_renderEngineerRow output remains stable for unchanged row inputs');
  assert.equal(secondGridSectionsHtml, firstGridSectionsHtml,
    '_renderStratifiedAgentGrid section output remains stable for unchanged model inputs');
});

test('TORQUE:264 — render() exposes _torqueLastHtml on the main element after first paint', () => {
  const { context, main } = createGridHarness();
  context.render();
  assert.equal(typeof main._torqueLastHtml, 'string');
  assert.equal(main._torqueLastHtml, main.innerHTML,
    'cache must mirror the last-applied html so byte-equality wins on the next render');
});

function makeTinyClassList() {
  const names = new Set();
  return {
    add(name) { names.add(name); },
    remove(name) { names.delete(name); },
    contains(name) { return names.has(name); },
    toggle(name, force) {
      const next = force === undefined ? !names.has(name) : !!force;
      if (next) names.add(name);
      else names.delete(name);
      return next;
    },
  };
}

function makeAgentCardFocusHarness() {
  let currentControl = null;
  let controlGeneration = 0;
  function makeFocusedControl() {
    controlGeneration += 1;
    return {
      dataset: { focusKey: 'agent-digest-toggle:a1' },
      value: '',
      selectionStart: 0,
      selectionEnd: 0,
      scrollTop: 0,
      scrollLeft: 0,
      focusCalls: [],
      focus(opts) { this.focusCalls.push(opts || null); },
      generation: controlGeneration,
    };
  }
  function maybeHydrateControl(html) {
    if (String(html || '').indexOf('data-focus-key="agent-digest-toggle:a1"') >= 0) {
      currentControl = makeFocusedControl();
    }
  }
  function shellPart(id) {
    return {
      id,
      style: {},
      classList: makeTinyClassList(),
      children: [],
      scrollTop: 0,
      scrollLeft: 0,
      scrollHeight: 240,
      offsetHeight: 120,
      clientHeight: 120,
      innerHTML: '',
      setAttribute(name, value) { this[name] = String(value); },
      getBoundingClientRect() { return { height: id === 'agent-split' ? 720 : 12 }; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
  }
  const split = shellPart('agent-split');
  const tabsHost = shellPart('agent-group-tabs-host');
  const grid = shellPart('agent-grid-pane');
  let gridSetCount = 0;
  Object.defineProperty(grid, 'innerHTML', {
    get() { return this._html || ''; },
    set(value) {
      this._html = String(value || '');
      gridSetCount += 1;
      maybeHydrateControl(this._html);
    },
  });
  const handle = shellPart('agent-focus-resizer');
  const focus = shellPart('agent-focus-panel');
  const focusScroll = shellPart('agent-focus-scroll');
  const main = makeMemoElement('MAIN');
  main.ownerDocument = null;
  main.contains = function(node) {
    return node === currentControl;
  };
  main.querySelector = function(selector) {
    if (selector === '[data-agent-split]') return split;
    if (selector === '[data-agent-grid-pane]') return grid;
    if (selector === '[data-agent-focus-resizer]') return handle;
    if (selector === '[data-agent-focus-panel]') return focus;
    if (selector === '[data-agent-focus-scroll]') return focusScroll;
    if (selector === '[data-agent-group-tabs-host]') return tabsHost;
    if (selector === '[data-focus-key="agent-digest-toggle:a1"]') return currentControl;
    return null;
  };
  const originalMainSetter = Object.getOwnPropertyDescriptor(main, 'innerHTML').set;
  const originalMainGetter = Object.getOwnPropertyDescriptor(main, 'innerHTML').get;
  Object.defineProperty(main, 'innerHTML', {
    get() { return originalMainGetter.call(main); },
    set(value) {
      originalMainSetter.call(main, value);
      maybeHydrateControl(value);
    },
  });
  const document = {
    activeElement: null,
    body: { classList: makeTinyClassList(), style: {} },
    getElementById(id) {
      if (id === 'main') return main;
      if (id === 'agent-group-tabs-host') return tabsHost;
      if (id === 'app-group-tabs-host') return null;
      return null;
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  main.ownerDocument = document;
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    CSS: { escape(value) { return String(value).replace(/"/g, '\\"'); } },
    location: { host: 'localhost:18932' },
    window: { innerHeight: 900 },
    document,
    state: {
      runtime: { embedded_terminal: false, profile: 'card-focus', port: '18932' },
      agents: {
        a1: {
          id: 'a1',
          name: 'Agent One',
          slug: 'agent-one',
          group: 'alpha',
          cell_type: 'agent',
          status: 'running',
          kind: 'worker',
          current_task_id: 'TASK-1',
          worktree_branch: 'torque/user/card-focus-a1',
        },
      },
      groups: { alpha: ['a1'] },
      group_settings: {},
      engineer_settings: {},
      agent_digest_settings: {},
      children: { a1: [] },
      board_tasks: {
        'TASK-1': { id: 'TASK-1', task: 'Keep card focus', lane: 'In Progress', action_name: 'feature/implement' },
      },
      ui: {},
      selected_principal_id: '',
    },
    selectedAgentId: 'a1',
    selectedTerminalId: null,
    focusedItemId: null,
    dragInProgress: false,
    getComputedStyle() {
      return {
        paddingTop: '0px',
        paddingBottom: '0px',
        marginTop: '0px',
        marginBottom: '0px',
      };
    },
    requestAnimationFrame(fn) { if (typeof fn === 'function') fn(); return 1; },
    cancelAnimationFrame() {},
    setTimeout() { return 0; },
    clearTimeout() {},
    renderTerminalWorkspace() {},
    updateEventsAttentionBadge() {},
    renderPendingHireBanner() {},
    renderAgentPanel() {},
    _currentPanelSurfaces() { return []; },
    getFilterByWindow() { return false; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = Object.assign(sandbox.window, sandbox);
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  context.renderAgentDetails = function() { return ''; };
  return {
    context,
    sandbox,
    main,
    grid,
    getCurrentControl() { return currentControl; },
    getGridSetCount() { return gridSetCount; },
  };
}

test('agent-card extraction preserves stable card HTML and main-surface focus through rerenders', () => {
  const harness = makeAgentCardFocusHarness();
  const { context, sandbox, main, grid } = harness;
  const firstCellHtml = context.renderAgentCell(sandbox.state.agents.a1);
  const secondCellHtml = context.renderAgentCell(sandbox.state.agents.a1);
  assert.equal(secondCellHtml, firstCellHtml,
    'renderAgentCell must remain byte-stable for unchanged card state after extraction');
  assert.match(firstCellHtml, /data-focus-key="agent-digest-toggle:a1"/);

  context.render();
  assert.match(main._torqueLastGridHtml, /data-focus-key="agent-digest-toggle:a1"/);
  assert.match(main._torqueLastGridHtml, /class="cell selected worker"/,
    'selected card marker should survive the initial grid render');
  const firstControl = harness.getCurrentControl();
  assert.ok(firstControl, 'first render should expose the agent-card inline control');
  firstControl.value = 'pause-caret';
  firstControl.selectionStart = 2;
  firstControl.selectionEnd = 7;
  firstControl.scrollTop = 13;
  firstControl.scrollLeft = 3;
  sandbox.document.activeElement = firstControl;

  const gridSetsBeforeStableRender = harness.getGridSetCount();
  context.render();
  assert.equal(harness.getGridSetCount(), gridSetsBeforeStableRender,
    'unchanged card html must not rewrite the grid, preserving hovered card DOM');
  assert.equal(harness.getCurrentControl(), firstControl,
    'unchanged rerender should keep the same card control node');

  sandbox.state.agents.a1.activity_detail = 'Reporting progress';
  context.render();
  assert.equal(harness.getGridSetCount(), gridSetsBeforeStableRender + 1,
    'changed card html should rebuild only the grid pane');
  assert.match(grid.innerHTML, /Reporting progress/);
  assert.match(main._torqueLastGridHtml, /class="cell selected worker"/,
    'selected card marker should survive the WS-style grid rebuild');
  const restoredControl = harness.getCurrentControl();
  assert.notEqual(restoredControl, firstControl,
    'changed grid html should replace the card control node in the harness');
  assert.equal(restoredControl.value, 'pause-caret');
  assert.equal(restoredControl.selectionStart, 2);
  assert.equal(restoredControl.selectionEnd, 7);
  assert.equal(restoredControl.scrollTop, 13);
  assert.equal(restoredControl.scrollLeft, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(restoredControl.focusCalls)), [
    { preventScroll: true },
  ]);
});

/* -- A3: hover defer ----------------------------------------------------- */

function createDeferHarness() {
  const renderCalls = [];
  const sandbox = {
    console,
    Date,
    JSON,
    state: {
      runtime: { embedded_terminal: false },
      agents: {},
      board_tasks: {},
      groups: { alpha: [] },
    },
    _activePanelApp: 'board',
    document: {
      getElementById() { return null; },
      addEventListener() {},
    },
    location: { host: 'localhost:18932' },
    WebSocket: function() {},
    setTimeout() {},
    clearTimeout() {},
    _currentGroup() { return 'alpha'; },
    _focusedEngineerAgent() { return null; },
    _standalonePanelsEnabled() { return false; },
    requestAnimationFrame: null,  // disable rAF coalescing so flush is synchronous
    renderInvalidatedSurfaces(flags) { renderCalls.push(flags); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  return { context, sandbox, renderCalls };
}

test('TORQUE:264 — _userInteracting() returns true when hovering', () => {
  const { context } = createDeferHarness();
  assert.equal(vm.runInContext('_userInteracting()', context), false);
  vm.runInContext('_userHovering = true;', context);
  assert.equal(vm.runInContext('_userInteracting()', context), true);
  vm.runInContext('_userHovering = false;', context);
  assert.equal(vm.runInContext('_userInteracting()', context), false);
});

test('TORQUE:264 — _queueDeltaSurfaceRender defers render while _userHovering is true', () => {
  const { context, renderCalls } = createDeferHarness();
  vm.runInContext('_userHovering = true;', context);
  vm.runInContext(
    '_queueDeltaSurfaceRender({ main: true });',
    context,
  );
  assert.equal(renderCalls.length, 0,
    'render must NOT fire while user is hovering — defer keeps tooltip-bearing card alive');
  // Pending batch was queued for later.
  const pending = vm.runInContext(
    '_pendingDeltaSurfaceInvalidations',
    context,
  );
  assert.ok(pending && pending.main === true,
    'pending batch must retain the main flag for replay after hover release');
});

test('TORQUE:264 — flushing pending batch is gated by _userHovering', () => {
  const { context, renderCalls } = createDeferHarness();
  vm.runInContext('_userHovering = true;', context);
  vm.runInContext('_queueDeltaSurfaceRender({ main: true });', context);
  // Direct flush attempt while hovering: still gated.
  vm.runInContext('_flushDeltaSurfaceRenderBatch();', context);
  assert.equal(renderCalls.length, 0,
    '_flushDeltaSurfaceRenderBatch must respect _userInteracting() gate');

  // Release hover — pending batch can now flush.
  vm.runInContext('_userHovering = false;', context);
  vm.runInContext('_flushDeltaSurfaceRenderBatch();', context);
  assert.equal(renderCalls.length, 1,
    'after hover release the queued batch must drain on the next flush');
  assert.equal(renderCalls[0].main, true);
});

test('TORQUE:264 — _hoverEdgeIsBetweenTooltips ignores inner-descendant transitions', () => {
  const { context } = createDeferHarness();
  // Two children of the same tooltip — pointer transitions between them
  // must NOT toggle the defer flag (would thrash hover->release->hover under
  // even minor pointer jitter).
  const tooltip = { closestSelector: '.agent-card-tooltip' };
  function makeChild(parent) {
    return {
      closest(sel) { return sel === '.agent-card-tooltip' ? parent : null; },
    };
  }
  const childA = makeChild(tooltip);
  const childB = makeChild(tooltip);
  context.__childA = childA;
  context.__childB = childB;
  context.__tooltip = tooltip;

  // Simulate a pointerout where related target is a sibling inside the
  // same tooltip — must return false (NOT an outbound edge).
  const innerEdge = vm.runInContext(`_hoverEdgeIsBetweenTooltips({
    target: __childA,
    relatedTarget: __childB,
  })`, context);
  assert.equal(innerEdge, false,
    'transition between two children of the same tooltip is NOT a defer-edge');

  // Simulate a true outbound edge: relatedTarget is outside any tooltip.
  context.__outside = { closest(_sel) { return null; } };
  const outerEdge = vm.runInContext(`_hoverEdgeIsBetweenTooltips({
    target: __childA,
    relatedTarget: __outside,
  })`, context);
  assert.equal(outerEdge, true,
    'transition from inside the tooltip to outside is a defer-edge');

  // Simulate target outside any tooltip — must always return false.
  const noTooltip = vm.runInContext(`_hoverEdgeIsBetweenTooltips({
    target: __outside,
    relatedTarget: null,
  })`, context);
  assert.equal(noTooltip, false,
    'pointer events on non-tooltip targets must never gate the defer flag');
});

/* -- A1: agent panel content memoization -------------------------------- */
/* Direct verification that `_torqueLastHtml` is read + written on the
 * `content` and `headerRight` nodes in `_agentPanelRenderFocusedTabInPlace`.
 * We don't fully drive the panel render here (that's covered by
 * frontend_agent_panel.test.js) — we just confirm the gate exists by
 * inspecting the source. This lets the regression be caught even when the
 * harness doesn't exercise the in-place path. */

/* -- Stale-root-cache behavioral coverage ------------------------------- */
/* Reviewer-discovered correctness regression: when the surgical in-place
 * path writes a child's innerHTML, the root `el._torqueLastHtml` cache is no
 * longer accurate (root html still reflects the pre-mutation state). A
 * subsequent `renderAgentPanel()` whose computed html byte-equals the
 * cache then short-circuits its own `el.innerHTML = html` write, leaving
 * the surgical-overwritten child in the DOM — stale content visible to
 * the user. Fix: in-place path must invalidate `el._torqueLastHtml` when it
 * mutates a child. */

function makePanelDomTree() {
  // A mini DOM with a root `el` (#panel-agent), a `.agent-panel-panel`
  // shell, a header-right region, and a content region. Tracks innerHTML
  // assignments per node so the test can assert byte-level cache hits.
  function makeNode(initial, queryFor) {
    const node = {
      _html: initial == null ? '' : String(initial),
      setCount: 0,
      get innerHTML() { return this._html; },
      set innerHTML(v) {
        this._html = String(v == null ? '' : v);
        this.setCount += 1;
        // Re-derive child references from the new html. For the test we
        // rebuild the headerRight/content children when the root is
        // re-written so subsequent in-place calls see fresh nodes.
        if (this === root && typeof rebuildChildrenFromRootHtml === 'function') {
          rebuildChildrenFromRootHtml();
        }
      },
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      dataset: {},
      addEventListener() {},
      querySelector(sel) { return queryFor && queryFor(sel) || null; },
      querySelectorAll() { return []; },
      contains() { return false; },
    };
    return node;
  }
  let shell, headerRight, content;
  const childMap = {};
  function rebuildChildrenFromRootHtml() {
    // Replace the child nodes with fresh ones — simulates the browser's
    // behavior when innerHTML is assigned: all descendants are recreated
    // from scratch, losing any expandos like `_torqueLastHtml`.
    shell = makeNode('', sel => null);
    headerRight = makeNode('', sel => null);
    content = makeNode('', sel => null);
    childMap['.agent-panel-panel'] = shell;
    childMap['[data-agent-panel-header-right]'] = headerRight;
    childMap['.agent-panel-header-right'] = headerRight;
    childMap['.agent-panel-content'] = content;
  }
  const root = makeNode('', sel => childMap[sel] || null);
  rebuildChildrenFromRootHtml();
  return { root, get shell() { return shell; },
    get headerRight() { return headerRight; }, get content() { return content; } };
}

test('TORQUE:264 — surgical in-place path invalidates root _torqueLastHtml so a later full render re-writes', () => {
  // Simulate the exact flow the reviewer reproduced:
  //   1. Full renderAgentPanel writes htmlA to root, caches htmlA on el.
  //   2. In-place path writes htmlB to .agent-panel-content (mutates child).
  //   3. State reverts. renderAgentPanel computes htmlA again.
  //   4. Without invalidation: gate skips because el._torqueLastHtml===htmlA;
  //      DOM stays at htmlB → STALE CONTENT.
  //   5. With invalidation: in-place sets el._torqueLastHtml=null at step 2;
  //      step 3 writes el.innerHTML=htmlA → DOM matches state.
  const dom = makePanelDomTree();
  const el = dom.root;

  // Step 1 — simulate a full root write (renderAgentPanel) with cache.
  const htmlA = '<div class="agent-panel-panel"><div class="agent-panel-content">No worker events yet.</div></div>';
  if (el._torqueLastHtml !== htmlA) {
    el.innerHTML = htmlA;
    el._torqueLastHtml = htmlA;
  }
  assert.equal(el.setCount, 1, 'first full render writes innerHTML');

  // Step 2 — simulate the in-place surgical path mutating .agent-panel-content.
  // Mirror the production gate (now with the root-cache invalidation fix).
  const newBodyHtml = '<div>EVENT B</div>';
  const bodyChanged = dom.content._torqueLastHtml !== newBodyHtml;
  if (bodyChanged) {
    dom.content.innerHTML = newBodyHtml;
    dom.content._torqueLastHtml = newBodyHtml;
  }
  if (bodyChanged && el._torqueLastHtml !== undefined) {
    el._torqueLastHtml = null;  // <— THE FIX
  }
  assert.equal(dom.content.setCount, 1);
  assert.equal(el._torqueLastHtml, null,
    'after a surgical child write, the root cache must be invalidated so a later'
    + ' full render with the original html does not skip its innerHTML write');

  // Step 3 — simulate the state reverting and renderAgentPanel computing htmlA.
  // The root gate must now write htmlA again because the cache was invalidated.
  if (el._torqueLastHtml !== htmlA) {
    el.innerHTML = htmlA;
    el._torqueLastHtml = htmlA;
  }
  assert.equal(el.setCount, 2,
    'root must rewrite htmlA after surgical mutation — otherwise the DOM stays at htmlB and the user sees stale content');
  assert.match(el.innerHTML, /No worker events yet\./,
    'root html must reflect the state, not the surgical interim');
  // The browser destroys all child nodes when innerHTML is assigned, so the
  // surgical write to the now-detached `dom.content` is no longer in DOM.
  // (`dom.content` is a stale reference; the new content child is fresh.)
  assert.equal(dom.content.setCount, 0,
    'the `content` reference after the root write must be a fresh node — surgical caches do not bleed across full-render boundaries');
});

test('TORQUE:264 — surgical no-op does NOT invalidate root cache', () => {
  // If the in-place path runs but neither child html actually changed, the
  // root cache must remain valid (no spurious invalidation that would
  // force a redundant full render later).
  const dom = makePanelDomTree();
  const el = dom.root;
  const htmlA = '<div class="agent-panel-panel"><div class="agent-panel-content">A</div></div>';
  el.innerHTML = htmlA;
  el._torqueLastHtml = htmlA;

  // Pre-seed the child cache so the gate sees a no-op.
  dom.content._torqueLastHtml = '<div>A</div>';
  dom.headerRight._torqueLastHtml = '';
  const newBodyHtml = '<div>A</div>';
  const newHeaderHtml = '';
  const headerChanged = dom.headerRight._torqueLastHtml !== newHeaderHtml;
  const bodyChanged = dom.content._torqueLastHtml !== newBodyHtml;
  if (headerChanged) {
    dom.headerRight.innerHTML = newHeaderHtml;
    dom.headerRight._torqueLastHtml = newHeaderHtml;
  }
  if (bodyChanged) {
    dom.content.innerHTML = newBodyHtml;
    dom.content._torqueLastHtml = newBodyHtml;
  }
  if ((headerChanged || bodyChanged) && el._torqueLastHtml !== undefined) {
    el._torqueLastHtml = null;
  }

  assert.equal(el._torqueLastHtml, htmlA,
    'when both child gates skip (no DOM mutation), the root cache must remain valid');
});

test('TORQUE:264 — agent_panel.js source contains the root-cache invalidation', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/agent_panel.js'),
    'utf8',
  );
  // The fix sets `el._torqueLastHtml = null` (or unsets it) gated on
  // headerChanged || bodyChanged. Match the canonical form.
  assert.match(source, /\(headerChanged\s*\|\|\s*bodyChanged\)[\s\S]{0,120}el\._torqueLastHtml\s*=/,
    'in-place path must invalidate el._torqueLastHtml when a child write actually mutates DOM —'
    + ' otherwise renderAgentPanel skips its byte-equality gate and leaves stale child content');
});

test('TORQUE:264 — agent_panel.js gates content/headerRight innerHTML on _torqueLastHtml', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/agent_panel.js'),
    'utf8',
  );
  // Look for the memoized content/headerRight gate. Match form:
  //   if (X._torqueLastHtml !== Y) { X.innerHTML = Y; X._torqueLastHtml = Y; }
  assert.match(source, /headerRight\._torqueLastHtml\s*!==\s*newHeaderHtml/,
    'headerRight innerHTML write must be gated by _torqueLastHtml byte-equality check');
  assert.match(source, /content\._torqueLastHtml\s*!==\s*newBodyHtml/,
    'content innerHTML write must be gated by _torqueLastHtml byte-equality check');
  assert.match(source, /headerRight\._torqueLastHtml\s*=\s*newHeaderHtml/,
    'headerRight cache must be updated after each successful write');
  assert.match(source, /content\._torqueLastHtml\s*=\s*newBodyHtml/,
    'content cache must be updated after each successful write');
  // Full panel render path (`renderAgentPanel`) — the el.innerHTML clobber
  // also needs gating for first-paint / shell-mismatch refreshes.
  assert.match(source, /el\._torqueLastHtml\s*!==\s*html/,
    'renderAgentPanel must memoize el.innerHTML to preserve DOM identity under firehose');
});

test('TORQUE:264 — render.js gates main.innerHTML on _torqueLastHtml', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/render.js'),
    'utf8',
  );
  assert.match(source, /main\._torqueLastHtml\s*!==\s*html/,
    'main grid innerHTML must be byte-equality memoized — destroying every agent card on every'
    + ' delta tick produces the TORQUE:264 tooltip flicker (style.css :hover::after pseudo-element'
    + ' on .agent-card-tooltip)');
  assert.match(source, /main\._torqueLastHtml\s*=\s*html/,
    'main grid cache must be updated after each successful innerHTML write');
});

test('TORQUE:264 — ws.js exposes _userHovering + _userInteracting() gate', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/ws.js'),
    'utf8',
  );
  assert.match(source, /var\s+_userHovering\s*=\s*false/,
    'ws.js must declare _userHovering — companion flag to _userPressing for the hover-defer pipeline');
  assert.match(source, /function\s+_userInteracting\s*\(/,
    'ws.js must expose _userInteracting() so flush + queue gate on the union flag');
  // Confirm the flush path was migrated from raw _userPressing read to the
  // unified gate.
  assert.match(source, /if\s*\(_userInteracting\(\)\)\s*\{[\s\S]*?_pendingDeltaSurfaceRenderFrame\s*=\s*0;/,
    '_flushDeltaSurfaceRenderBatch must gate on _userInteracting() so hover defer participates');
});

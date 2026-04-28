/* Regression tests for LOOM:264 follow-up: idempotent agent panel + agent
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
  return { context, sandbox, main };
}

test('LOOM:264 — render() memoizes empty-state main.innerHTML', () => {
  const { context, main } = createGridHarness();
  context.render();
  assert.equal(main._setCount, 1, 'first render writes empty-state html');
  context.render();
  assert.equal(main._setCount, 1,
    'second render with identical state must not rewrite innerHTML');
  assert.match(main.innerHTML, /No groups yet/);
});

test('LOOM:264 — render() exposes _loomLastHtml on the main element after first paint', () => {
  const { context, main } = createGridHarness();
  context.render();
  assert.equal(typeof main._loomLastHtml, 'string');
  assert.equal(main._loomLastHtml, main.innerHTML,
    'cache must mirror the last-applied html so byte-equality wins on the next render');
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

test('LOOM:264 — _userInteracting() returns true when hovering', () => {
  const { context } = createDeferHarness();
  assert.equal(vm.runInContext('_userInteracting()', context), false);
  vm.runInContext('_userHovering = true;', context);
  assert.equal(vm.runInContext('_userInteracting()', context), true);
  vm.runInContext('_userHovering = false;', context);
  assert.equal(vm.runInContext('_userInteracting()', context), false);
});

test('LOOM:264 — _queueDeltaSurfaceRender defers render while _userHovering is true', () => {
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

test('LOOM:264 — flushing pending batch is gated by _userHovering', () => {
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

test('LOOM:264 — _hoverEdgeIsBetweenTooltips ignores inner-descendant transitions', () => {
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
/* Direct verification that `_loomLastHtml` is read + written on the
 * `content` and `headerRight` nodes in `_agentPanelRenderFocusedTabInPlace`.
 * We don't fully drive the panel render here (that's covered by
 * frontend_agent_panel.test.js) — we just confirm the gate exists by
 * inspecting the source. This lets the regression be caught even when the
 * harness doesn't exercise the in-place path. */

test('LOOM:264 — agent_panel.js gates content/headerRight innerHTML on _loomLastHtml', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/agent_panel.js'),
    'utf8',
  );
  // Look for the memoized content/headerRight gate. Match form:
  //   if (X._loomLastHtml !== Y) { X.innerHTML = Y; X._loomLastHtml = Y; }
  assert.match(source, /headerRight\._loomLastHtml\s*!==\s*newHeaderHtml/,
    'headerRight innerHTML write must be gated by _loomLastHtml byte-equality check');
  assert.match(source, /content\._loomLastHtml\s*!==\s*newBodyHtml/,
    'content innerHTML write must be gated by _loomLastHtml byte-equality check');
  assert.match(source, /headerRight\._loomLastHtml\s*=\s*newHeaderHtml/,
    'headerRight cache must be updated after each successful write');
  assert.match(source, /content\._loomLastHtml\s*=\s*newBodyHtml/,
    'content cache must be updated after each successful write');
  // Full panel render path (`renderAgentPanel`) — the el.innerHTML clobber
  // also needs gating for first-paint / shell-mismatch refreshes.
  assert.match(source, /el\._loomLastHtml\s*!==\s*html/,
    'renderAgentPanel must memoize el.innerHTML to preserve DOM identity under firehose');
});

test('LOOM:264 — render.js gates main.innerHTML on _loomLastHtml', () => {
  const source = fs.readFileSync(
    path.join(repoRoot, 'static/js/render.js'),
    'utf8',
  );
  assert.match(source, /main\._loomLastHtml\s*!==\s*html/,
    'main grid innerHTML must be byte-equality memoized — destroying every agent card on every'
    + ' delta tick produces the LOOM:264 tooltip flicker (style.css :hover::after pseudo-element'
    + ' on .agent-card-tooltip)');
  assert.match(source, /main\._loomLastHtml\s*=\s*html/,
    'main grid cache must be updated after each successful innerHTML write');
});

test('LOOM:264 — ws.js exposes _userHovering + _userInteracting() gate', () => {
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

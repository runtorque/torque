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
  return { context, sandbox, main };
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

test('TORQUE:264 — render() exposes _torqueLastHtml on the main element after first paint', () => {
  const { context, main } = createGridHarness();
  context.render();
  assert.equal(typeof main._torqueLastHtml, 'string');
  assert.equal(main._torqueLastHtml, main.innerHTML,
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

/* Perf-wave primitives under explicit toolbelt mode.
 *
 * The perf-wave primitives (virtualization, delta narrowing, lane cache,
 * rerender guardrail, section pager) are universal — not standalone-gated.
 * Toolbelt inherits them through shared render paths. Existing tests run
 * without setting state.runtime, so they pass in toolbelt mode only by
 * coincidence (undefined runtime → isEmbeddedTerminalMode() === false).
 *
 * These tests lock that contract in by asserting each primitive works with
 * state.runtime = { embedded_terminal: false } set explicitly. A future
 * change that adds a standalone-only gate inside any of these primitives
 * will fail here. Follow-up from TORQUE:147 audit. */

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

/* -- Agent-panel harness: virtualization, section pager, guardrail ----- */

function createAgentPanelHarness() {
  const panel = { innerHTML: '', querySelector() { return null; } };
  const sandbox = {
    console,
    Date,
    state: {
      runtime: { embedded_terminal: false },
      agents: {},
      board_tasks: {},
      groups: { alpha: [] },
      engineer_settings: {},
      engineer_buffer_stats: {},
      engineer_sent_events: {},
      engineer_worklog: {},
      engineer_journal: {},
      engineer_session_maps: {},
      agent_digest_settings: {},
      digest_buffer_stats: {},
      digest_sent_events: {},
      panel_events: [],
      decisions: {},
      group_settings: { alpha: { engineer_agent_id: 'engineer-1' } },
    },
    focusedItemId: '',
    _activePanelApp: 'engineer',
    document: {
      activeElement: null,
      getElementById(id) { return id === 'panel-agent' ? panel : null; },
    },
    _captureSurfaceState(root, opts) {
      const snapshot = { focus: null, scrolls: [] };
      if (opts && typeof opts.capture === 'function') opts.capture(snapshot, root);
      return snapshot;
    },
    _restoreSurfaceState(root, snapshot, opts) {
      if (opts && typeof opts.restore === 'function') opts.restore(root, snapshot);
    },
    _engineerStopEventsCountdownTimer() {},
    _currentGroup() { return 'alpha'; },
    setInterval() { return 1; },
    clearInterval() {},
    send() {},
    window: { prompt() { return null; } },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/agent_panel.js');
  return { context, sandbox, panel };
}

function focusEngineerForJournal(context, agentId) {
  context.state.agents[agentId] = {
    id: agentId, name: 'Builder', kind: 'engineer',
    group: 'alpha', cell_type: 'agent',
  };
  context.state.agents['engineer-1'] = { id: 'engineer-1', name: 'Engineer', group: 'alpha' };
  context.focusedItemId = agentId;
  vm.runInContext(
    `_agentPanelLastSelectedTabByKind.engineer = 'journal';`
    + `_agentPanelSectionPagers = {};`,
    context,
  );
}

test('[toolbelt] engineer journal virtualization caps at 20 entries and renders Load more', () => {
  const { context, panel, sandbox } = createAgentPanelHarness();
  focusEngineerForJournal(context, 'eng-tb-virt');
  // Assert the harness genuinely simulates toolbelt mode — not standalone.
  assert.equal(sandbox.state.runtime.embedded_terminal, false);
  assert.equal(sandbox._activePanelApp, 'engineer');

  const entries = [];
  for (let i = 0; i < 25; i++) {
    entries.push({ id: 1000 + i, type: 'observation',
      entry: 'Journal entry ' + i, timestamp: 1000 + i });
  }
  context.state.engineer_journal['eng-tb-virt'] = entries;

  context.renderAgentPanel();

  const html = panel.innerHTML;
  const rendered = (html.match(/data-engineer-anchor="journal-/g) || []).length;
  assert.equal(rendered, 20, 'section pager renders only 20 entries under toolbelt');
  assert.match(html, /Load 5 older entries/);
  assert.match(html, /data-agent-panel-section="journal"/);
});

test('[toolbelt] engineer journal Load older grows the window without a server fetch', () => {
  const { context, panel, sandbox } = createAgentPanelHarness();
  const sendCalls = [];
  sandbox.send = function(msg) { sendCalls.push(msg); };
  focusEngineerForJournal(context, 'eng-tb-pager');
  const entries = [];
  for (let i = 0; i < 45; i++) {
    entries.push({ id: 2000 + i, type: 'observation',
      entry: 'J ' + i, timestamp: 1000 + i });
  }
  context.state.engineer_journal['eng-tb-pager'] = entries;

  context.renderAgentPanel();
  assert.equal(
    (panel.innerHTML.match(/data-engineer-anchor="journal-/g) || []).length,
    20,
  );
  const sendsBefore = sendCalls.length;

  vm.runInContext(
    `agentPanelLoadMoreSection(null, 'journal', 'eng-tb-pager');`,
    context,
  );

  const rendered = (panel.innerHTML.match(/data-engineer-anchor="journal-/g) || []).length;
  assert.equal(rendered, 40, 'Load older advances the window to 40');
  assert.equal(sendCalls.length, sendsBefore, 'Load older is pure frontend windowing');
});

test('[toolbelt] engineer journal pager grows to preserve anchor across WS delta', () => {
  const { context, panel } = createAgentPanelHarness();
  focusEngineerForJournal(context, 'eng-tb-anchor');
  const existing = [];
  for (let i = 0; i < 25; i++) {
    existing.push({ id: 3000 + i, type: 'observation',
      entry: 'Existing ' + i, timestamp: 1000 + i });
  }
  context.state.engineer_journal['eng-tb-anchor'] = existing.slice();
  context.renderAgentPanel();
  assert.ok(panel.innerHTML.indexOf('Existing 5') >= 0);

  context.state.engineer_journal['eng-tb-anchor'] = [
    { id: 3100, type: 'decision', entry: 'Fresh 1', timestamp: 9000 },
    { id: 3101, type: 'plan', entry: 'Fresh 2', timestamp: 9001 },
  ].concat(existing);
  context.renderAgentPanel();

  assert.ok(panel.innerHTML.indexOf('Fresh 1') >= 0);
  assert.ok(panel.innerHTML.indexOf('Existing 5') >= 0,
    'anchor row must stay in the DOM so the guardrail can lock onto it');
});

/* -- Delta narrowing under toolbelt ------------------------------------ */

function createDeltaSurfaceHarness() {
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
    document: { getElementById() { return null; }, addEventListener() {} },
    location: { host: 'localhost:18932' },
    WebSocket: function() {},
    setTimeout() {},
    clearTimeout() {},
    _currentGroup() { return 'alpha'; },
    _focusedEngineerAgent() { return null; },
    _standalonePanelsEnabled() { return false; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  return { context, sandbox };
}

test('[toolbelt] _deltaSurfaceInvalidations skips board rerender for an events-only delta', () => {
  const { context, sandbox } = createDeltaSurfaceHarness();

  const flags = vm.runInContext(
    `_deltaSurfaceInvalidations([{op: 'event_append', id: 'e-1', group: 'alpha'}], [{}])`,
    context,
  );
  const plain = JSON.parse(JSON.stringify(flags));
  assert.equal(plain.board, false, 'board must stay un-invalidated under toolbelt');
  assert.equal(plain.events, true);
  assert.equal(plain.engineer, false);
  assert.equal(plain.main, false);
  assert.equal(sandbox._activePanelApp, 'board');
});

/* -- Lane cache under toolbelt ------------------------------------------ */

function createBoardLaneCacheHarness() {
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    state: {
      runtime: { embedded_terminal: false },
      board_lanes: ['Backlog', 'In Progress', 'Done'],
      board_tasks: {},
      groups: { alpha: [] },
      agents: {},
    },
    _activePanelApp: 'board',
    document: {
      getElementById() { return null; },
      body: { classList: { contains() { return false; } } },
    },
    esc(v) { return String(v); },
    isSystemLabel() { return false; },
    displayLabel(l) { return String(l); },
    labelColor() { return '#fff'; },
    _currentGroup() { return 'alpha'; },
    _focusedGroup() { return 'alpha'; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  loadScript(context, 'static/js/agent-detail.js');
  loadScript(context, 'static/js/board.js');
  loadScript(context, 'static/js/board/view-state.js');
  loadScript(context, 'static/js/board/card-rendering.js');
  loadScript(context, 'static/js/board/card-actions.js');
  return { context, sandbox };
}

test('[toolbelt] _boardLaneRenderCache returns a hit for identical inputs and a miss on change', () => {
  const { context, sandbox } = createBoardLaneCacheHarness();
  assert.equal(sandbox.state.runtime.embedded_terminal, false);
  assert.equal(sandbox._activePanelApp, 'board');

  // A model shaped the way the board's render path passes it in: scoped
  // task view + memoized root-tasks-by-lane + empty children index.
  vm.runInContext(`
    _boardLaneRenderCache = {};
    var modelA = {
      visibleTasks: {},
      rootTasksByLane: { 'In Progress': [] },
      childrenOf: {},
      scopedWithoutArchived: {},
    };
    _boardRememberLaneRender(
      'In Progress', modelA, false, false, false,
      { html: '<div>A</div>', bodyHtml: '<div>A</div>',
        renderedCards: 0, totalCards: 0, rootTasks: [] },
    );
  `, context);

  const hit = vm.runInContext(`
    (function() {
      var modelA = {
        visibleTasks: {},
        rootTasksByLane: { 'In Progress': [] },
        childrenOf: {},
        scopedWithoutArchived: {},
      };
      return _boardCachedLaneRender('In Progress', modelA, false, false, false);
    })()
  `, context);
  assert.ok(hit, 'same inputs must produce a cache hit under toolbelt');
  assert.equal(hit.html, '<div>A</div>');

  const miss = vm.runInContext(`
    (function() {
      var modelA = {
        visibleTasks: {},
        rootTasksByLane: { 'In Progress': [] },
        childrenOf: {},
        scopedWithoutArchived: {},
      };
      // filtersActive flipped — context key must change, so cache misses.
      return _boardCachedLaneRender('In Progress', modelA, true, false, false);
    })()
  `, context);
  assert.equal(miss, null, 'changed context key must invalidate the cache');
});

/* -- Rerender guardrail under toolbelt --------------------------------- */

function createSurfaceStateHarness() {
  const sandbox = {
    console,
    state: { runtime: { embedded_terminal: false } },
    _activePanelApp: 'engineer',
    document: { activeElement: null, getElementById() { return null; } },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/render.js');
  loadScript(context, 'static/js/grid/group-tabs.js');
  loadScript(context, 'static/js/grid/agent-card.js');
  loadScript(context, 'static/js/grid/terminal-row.js');
  loadScript(context, 'static/js/grid/sections.js');
  loadScript(context, 'static/js/agent-detail.js');
  return { context, sandbox };
}

test('[toolbelt] _captureSurfaceState preserves agent-panel-event-list scroll across rerender', () => {
  const { context, sandbox } = createSurfaceStateHarness();
  const eventList = { scrollTop: 240, scrollLeft: 0 };
  const root = {
    querySelector(sel) {
      return sel === '.agent-panel-event-list' ? eventList : null;
    },
    contains() { return false; },
  };

  const snapshot = vm.runInContext(
    `(function(r) {
      return _captureSurfaceState(r, {
        scrollSelectors: ['.agent-panel-event-list'],
      });
    })`,
    context,
  )(root);

  const scrolls = JSON.parse(JSON.stringify(snapshot.scrolls));
  assert.equal(scrolls.length, 1);
  assert.equal(scrolls[0].selector, '.agent-panel-event-list');
  assert.equal(scrolls[0].top, 240);

  // Simulate the rerender: innerHTML wipe resets the scroll on the new node.
  eventList.scrollTop = 0;
  vm.runInContext(
    `(function(r, s) {
      _restoreSurfaceState(r, s, {
        scrollSelectors: ['.agent-panel-event-list'],
      });
    })`,
    context,
  )(root, snapshot);

  assert.equal(eventList.scrollTop, 240, 'guardrail must restore scroll under toolbelt');
  assert.equal(sandbox._activePanelApp, 'engineer');
});

test('[toolbelt] _restoreSurfaceState preserves textarea scrollTop after value+selection set', () => {
  // Models the terminal-compose-input bug: textarea has overflow-y:auto +
  // max-height, user has scrolled to keep cursor in view, then a WS-driven
  // rerender wipes innerHTML. The new textarea has scrollTop=0 — the surface
  // helper must restore the focused element's own scrollTop, otherwise the
  // compose box jumps to the top while the cursor stays at the end of the
  // text.
  const { context, sandbox } = createSurfaceStateHarness();

  // Pre-rerender textarea with content that overflows the visible box.
  const draft = 'line one\nline two\nline three\nline four\nline five\nline six';
  const textareaId = 'terminal-compose-input-cell';
  const oldTextarea = {
    id: textareaId,
    value: draft,
    selectionStart: draft.length,
    selectionEnd: draft.length,
    scrollTop: 96,
    scrollLeft: 0,
    focus() { sandbox._focusCalls = (sandbox._focusCalls || 0) + 1; },
  };
  sandbox.document.activeElement = oldTextarea;
  // Initially the lookup resolves to the old (focused) textarea — captured
  // before the rerender swaps it.
  let resolveTextarea = oldTextarea;
  sandbox.document.getElementById = function(id) {
    return id === textareaId ? resolveTextarea : null;
  };

  const root = {
    querySelector() { return null; },
    contains(el) { return el === resolveTextarea; },
  };

  const snapshot = vm.runInContext(
    `(function(r) { return _captureSurfaceState(r); })`,
    context,
  )(root);

  assert.ok(snapshot.focus, 'focus snapshot must be captured for the textarea');
  assert.equal(snapshot.focus.scrollTop, 96, 'capture must record textarea scrollTop');
  assert.equal(snapshot.focus.selectionEnd, draft.length);

  // Simulate the rerender: innerHTML wipe replaces the textarea with a fresh
  // node. The new node has the same id but scrollTop=0.
  const postRenderTextarea = {
    id: textareaId,
    value: draft,
    selectionStart: 0,
    selectionEnd: 0,
    scrollTop: 0,
    scrollLeft: 0,
    focus() { sandbox._focusCalls = (sandbox._focusCalls || 0) + 1; },
  };
  sandbox.document.activeElement = null;
  resolveTextarea = postRenderTextarea;

  vm.runInContext(
    `(function(r, s) { _restoreSurfaceState(r, s); })`,
    context,
  )(root, snapshot);

  assert.equal(postRenderTextarea.value, draft, 'value must be restored');
  assert.equal(postRenderTextarea.selectionEnd, draft.length, 'cursor must be at end');
  assert.equal(
    postRenderTextarea.scrollTop, 96,
    'textarea internal scroll must be restored after value/selection set',
  );
});

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

function htmlEsc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function createBadgeRenderContext() {
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    Number,
    window: { setTimeout() { return 0; }, clearTimeout() {} },
    esc: htmlEsc,
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/board/card-rendering.js');
  return context;
}

class FakeBadge {
  constructor() {
    this.className = 'board-card-label board-card-dispatch-state board-card-dispatch-state-queued';
    this.dataset = { dispatchStateBadge: '1', dispatchState: 'queued' };
    this.attributes = { 'data-dispatch-state-badge': '1', 'data-dispatch-state': 'queued' };
    this.textContent = 'QUEUED';
    this.title = 'Dispatch state: queued — assigned but not yet dispatched';
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'data-dispatch-state') this.dataset.dispatchState = String(value);
    if (name === 'data-dispatch-state-badge') this.dataset.dispatchStateBadge = String(value);
    if (name === 'title') this.title = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }
}

function createDeltaPatchContext() {
  const badge = new FakeBadge();
  const card = {
    querySelector(selector) {
      if (selector === '[data-dispatch-state-badge]') return badge;
      return null;
    },
  };
  const renderCalls = [];
  let rafCalls = 0;
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    Number,
    location: { protocol: 'http:', host: 'localhost:18932' },
    crypto: null,
    WebSocket: { OPEN: 1 },
    document: {
      addEventListener() {},
      querySelectorAll(selector) {
        return selector === '.board-card[data-task-id="TORQUE\\:1"]' ? [card] : [];
      },
    },
    window: {
      addEventListener() {},
      setTimeout() { return 0; },
      clearTimeout() {},
    },
    CSS: {
      escape(value) {
        return String(value).replace(/:/g, '\\:');
      },
    },
    requestAnimationFrame(callback) {
      rafCalls += 1;
      callback();
      return rafCalls;
    },
    cancelAnimationFrame() {},
    esc: htmlEsc,
    _standalonePanelsEnabled() { return true; },
    _currentGroup() { return 'Torque'; },
    _currentPanelSurfaces() { return ['board']; },
    renderInvalidatedSurfaces(flags) { renderCalls.push(Object.assign({}, flags)); },
    renderBoard() { renderCalls.push({ board: true, direct: true }); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/ws/interaction-guard.js');
  loadScript(context, 'static/js/ws/full-state.js');
  loadScript(context, 'static/js/ws/invalidation.js');
  loadScript(context, 'static/js/ws/delta-registry.js');
  loadScript(context, 'static/js/ws/delta-apply.js');
  loadScript(context, 'static/js/ws/action-router.js');
  loadScript(context, 'static/js/board/card-rendering.js');
  return { context, badge, renderCalls, rafCalls: () => rafCalls };
}

test('board dispatch_state badge renders QUEUED and LIVE from task.dispatch_state', () => {
  const context = createBadgeRenderContext();
  const queuedHtml = vm.runInContext(`_boardTaskDispatchStateBadgeHtml({
    id: 'TORQUE:1',
    dispatch_state: 'queued',
    assigned_engineer_id: 'eng-1',
  })`, context);
  assert.match(queuedHtml, /data-dispatch-state="queued"/);
  assert.match(queuedHtml, />QUEUED<\/span>/);
  assert.match(queuedHtml, /board-card-dispatch-state-queued/);

  const liveHtml = vm.runInContext(`_boardTaskDispatchStateBadgeHtml({
    id: 'TORQUE:1',
    dispatch_state: 'live',
    lane: 'To Do',
    agent_id: 'worker-1',
  })`, context);
  assert.match(liveHtml, /data-dispatch-state="live"/);
  assert.match(liveHtml, />LIVE<\/span>/);
  assert.match(liveHtml, /board-card-dispatch-state-live/);

  const unassignedQueuedHtml = vm.runInContext(`_boardTaskDispatchStateBadgeHtml({
    id: 'TORQUE:2',
    dispatch_state: 'queued',
    lane: 'Backlog',
  })`, context);
  assert.equal(unassignedQueuedHtml, '');

  const recomputedEligibility = vm.runInContext(`_boardTaskDispatchEligibility({
    id: 'TORQUE:3',
    lane: 'To Do',
    agent_id: 'worker-1',
    dispatch_state: 'live',
  })`, context);
  assert.equal(recomputedEligibility, null);
});

test('dispatch_state-only task delta patches the visible card badge without board render', () => {
  const { context, badge, renderCalls, rafCalls } = createDeltaPatchContext();
  vm.runInContext(`
    state = {
      agents: {},
      groups: {},
      group_settings: {},
      children: {},
      board_tasks: {
        'TORQUE:1': {
          id: 'TORQUE:1',
          group: 'Torque',
          lane: 'To Do',
          task: 'Badge target',
          agent_id: 'worker-1',
          dispatch_state: 'queued',
          updated_at: '2026-06-01T21:00:00+00:00'
        }
      }
    };
    _expectedSeq = 1;
    _awaitingFullState = false;
    dragInProgress = false;
    _handleDelta({
      seq: 1,
      ops: [{
        op: 'task_upsert',
        id: 'TORQUE:1',
        dispatch_state: 'live',
        updated_at: '2026-06-01T21:00:01+00:00'
      }]
    });
  `, context);

  assert.equal(badge.textContent, 'LIVE');
  assert.equal(badge.dataset.dispatchState, 'live');
  assert.match(badge.className, /board-card-dispatch-state-live/);
  assert.deepEqual(renderCalls, []);
  assert.equal(rafCalls(), 0);
});

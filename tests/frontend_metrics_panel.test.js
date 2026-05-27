const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() { this._set = new Set(); }
  add(...names) { names.forEach((name) => this._set.add(name)); }
  remove(...names) { names.forEach((name) => this._set.delete(name)); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const next = force === undefined ? !this._set.has(name) : !!force;
    if (next) this._set.add(name);
    else this._set.delete(name);
    return next;
  }
}

class FakeElement {
  constructor(id, ownerDocument) {
    this.id = id || '';
    this.ownerDocument = ownerDocument || null;
    this._innerHTML = '';
    this.textContent = '';
    this.value = '';
    this.title = '';
    this.hidden = false;
    this.open = false;
    this.scrollTop = 0;
    this.children = [];
    this.parentNode = null;
    this.classList = new FakeClassList();
    this.attributes = {};
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (!this.ownerDocument) return;
    if (this.id === 'panel-health' && this._innerHTML.includes('health-results')) {
      this.ownerDocument.ensure('health-results');
      this.ownerDocument.ensure('health-window-select');
      this.ownerDocument.ensure('health-scope-select');
      this.ownerDocument.ensure('health-active-group-name');
      this._hasHealthPanel = true;
    }
    if (this.id === 'health-results' && this._innerHTML.includes('health-metrics-details')) {
      const details = this.ownerDocument.ensure('health-metrics-details');
      details.open = /id="health-metrics-details"[^>]*\sopen(?:\s|>)/.test(this._innerHTML);
      this.ownerDocument.ensure('health-metrics-live');
      this.ownerDocument.ensure('health-metrics-history');
      this.ownerDocument.ensure('health-metrics-summary-status');
    }
  }

  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.children = child.parentNode.children.filter((item) => item !== child);
    }
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  querySelector(selector) {
    if (selector === '.health-panel' && this._hasHealthPanel) return this;
    if (this.id === 'health-metrics-details' && selector === '.health-metrics-summary-status') {
      return this.ownerDocument.ensure('health-metrics-summary-status');
    }
    return null;
  }
  querySelectorAll() { return []; }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  scrollIntoView() { this._scrolledIntoView = true; }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.hidden = false;
    this.body = this.ensure('body');
    this._listeners = new Map();
  }
  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }
  getElementById(id) { return this.elements.get(id) || null; }
  querySelectorAll() { return []; }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
}

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function createSandbox({ visible = true } = {}) {
  const document = new FakeDocument();
  [
    'panel-health',
    'statusbar-info',
    'statusbar-claude-usage',
    'statusbar-codex-usage',
    'statusbar-deploy',
    'statusbar-metrics',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ].forEach((id) => document.ensure(id));
  const statusInfo = document.getElementById('statusbar-info');
  [
    'statusbar-claude-usage',
    'statusbar-codex-usage',
    'statusbar-deploy',
    'statusbar-metrics',
    'statusbar-workload',
    'statusbar-tasks',
    'statusbar-attention',
  ].forEach((id) => statusInfo.appendChild(document.getElementById(id)));

  const sendCalls = [];
  const timers = [];
  const sandbox = {
    console,
    document,
    state: {
      active_group: 'Torque',
      groups: { Torque: [] },
      agents: {},
      board_tasks: {},
    },
    __now: 100000,
    Date: { now() { return sandbox.__now; } },
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay, active: true });
      return id;
    },
    clearTimeout(id) {
      const timer = timers.find((entry) => entry.id === id);
      if (timer) timer.active = false;
    },
    _panelAppVisible(app) { return app === 'health' && visible; },
    _activeGroup() { return sandbox.state.active_group; },
    send(message) { sendCalls.push(message); },
    __setVisible(next) { visible = !!next; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document, sendCalls, timers };
}

function sampleTick(overrides = {}) {
  return Object.assign({
    type: 'metrics_tick',
    schema_version: 1,
    generated_at: 100,
    enabled: true,
    interval_ms: 1500,
    perf: {
      event_loop_lag_ms: { p50: 3.2, p95: 12.4, max: 20.1 },
      ws: { deltas_per_s: 7.5, bytes_per_s: 2048, subscribers: 3 },
      db: { writes_per_s: 1.2, write_latency_p95_ms: 8.8 },
      proc: { rss_mb: 256.2, cpu_pct: 22.5 },
      live: { agents: 5, ptys: 2, prompt_queue_depth: 1 },
      frontend: { render_per_s: 4.2, render_ms_p95: 18.6 },
    },
    windows: {
      '1m': { ws_deltas_per_s: 7.1, db_writes_per_s: 1.1, event_loop_lag_p95_ms: 12.1 },
      '5m': { ws_deltas_per_s: 6.8, db_writes_per_s: 1.0, event_loop_lag_p95_ms: 13.5 },
    },
    meter_overhead: { agg_tick_ms: 0.41, collect_overhead_pct: 0.03 },
  }, overrides);
}

function sampleHistory() {
  return {
    type: 'metrics_history',
    schema_version: 1,
    generated_at: 100,
    window: '24h',
    group: 'Torque',
    scope: 'group',
    bucket_seconds: 3600,
    buckets: [{ start: 1, end: 2, label: 'a' }, { start: 2, end: 3, label: 'b' }],
    perf: {
      event_loop_lag_p95_ms: [10, 12.4],
      ws_deltas_per_s: [4, 7.5],
      db_write_latency_p95_ms: [9.2, 8.8],
      rss_mb: [240, 256.2],
      cpu_pct: [20, 22.5],
      frontend_render_per_s: [2, 4.2],
      frontend_render_ms_p95: [20, 18.6],
      live_agents: [4, 5],
      retention: { kept_seconds: 86400, rollup_resolution_seconds: 3600 },
    },
    workflow: {},
    coverage: {},
    notes: ['boundary-to-merge proxy'],
  };
}

function loadMetricsStack(context) {
  loadScript(context, 'static/js/status_bar.js');
  loadScript(context, 'static/js/health.js');
}

test('metrics tick updates status-bar indicator and health-panel live cards without fetching history per tick', () => {
  const { sandbox, document, sendCalls } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadMetricsStack(context);

  vm.runInContext('renderHealthPanel()', context);
  assert.deepEqual(sendCalls.map((call) => call.cmd), [
    'get_system_health_metrics',
    'get_metrics_history',
  ]);
  sendCalls.length = 0;

  vm.runInContext(`healthMetricsReceiveHistory(${JSON.stringify(sampleHistory())})`, context);
  vm.runInContext(`healthMetricsReceiveTick(${JSON.stringify(sampleTick())})`, context);

  const chip = document.getElementById('statusbar-metrics');
  assert.equal(chip.textContent, 'Lag 12.4ms · Mem 256MB');
  assert.equal(chip.classList.contains('statusbar-chip--normal'), true);
  const liveHtml = document.getElementById('health-metrics-live').innerHTML;
  assert.match(liveHtml, /Event-loop lag/);
  assert.match(liveHtml, /12\.4ms/);
  assert.match(liveHtml, /Frontend renders/);
  assert.match(liveHtml, /4\.2\/s/);
  assert.match(liveHtml, /health-sparkline/);
  assert.equal(sendCalls.some((call) => call.cmd === 'get_metrics_history'), false);
});

test('metrics indicator and panel degrade gracefully when metrics are absent or disabled', () => {
  const { sandbox, document } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadMetricsStack(context);

  vm.runInContext('refreshStatusBar()', context);
  assert.equal(document.getElementById('statusbar-metrics').textContent, 'Metrics —');

  vm.runInContext('renderHealthPanel()', context);
  vm.runInContext(`healthMetricsReceiveTick(${JSON.stringify({ type: 'metrics_tick', schema_version: 1, enabled: false })})`, context);

  assert.equal(document.getElementById('statusbar-metrics').textContent, 'Metrics off');
  assert.equal(document.getElementById('statusbar-metrics').classList.contains('statusbar-chip--muted'), true);
  assert.match(document.getElementById('health-metrics-live').innerHTML, /metrics off/);
  assert.doesNotMatch(document.getElementById('health-metrics-live').innerHTML, /0\/s/);
});

test('metrics section preserves collapse state and panel scroll across health rerenders', () => {
  const { sandbox, document } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadMetricsStack(context);

  vm.runInContext('renderHealthPanel()', context);
  document.getElementById('panel-health').scrollTop = 88;
  document.getElementById('health-metrics-details').open = false;
  vm.runInContext('healthMetricsSetExpanded(false)', context);
  vm.runInContext(`healthReceiveMetrics(${JSON.stringify({
    type: 'system_health_metrics',
    window: '24h',
    group: 'Torque',
    bucket_seconds: 3600,
    summary: {},
    series: {},
    distributions: {},
    coverage: {},
    notes: [],
  })})`, context);

  assert.equal(document.getElementById('health-metrics-details').open, false);
  assert.equal(document.getElementById('panel-health').scrollTop, 88);
});


test('status-bar metrics action opens health panel without collapsing an already visible panel', () => {
  const { sandbox, document } = createSandbox({ visible: false });
  const toggles = [];
  sandbox.togglePanel = (app) => { toggles.push(app); sandbox.__setVisible(true); };
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');

  vm.runInContext('healthMetricsSetExpanded(false)', context);
  vm.runInContext('healthOpenMetrics()', context);

  assert.deepEqual(toggles, ['health']);
  assert.equal(vm.runInContext('healthMetricsState.expanded', context), true);
  // Once visible, a second activation should render/expand, not toggle the panel closed.
  vm.runInContext('renderHealthPanel()', context);
  document.getElementById('health-metrics-details').open = false;
  vm.runInContext('healthOpenMetrics()', context);
  assert.deepEqual(toggles, ['health']);
  assert.equal(document.getElementById('health-metrics-details').open, true);
});

test('frontend render self-report sends render rate and p95 duration from local samples', () => {
  const { sandbox, sendCalls } = createSandbox({ visible: false });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');
  sendCalls.length = 0;

  sandbox.__now = 100000;
  vm.runInContext("healthRecordFrontendRender(10, 'test')", context);
  sandbox.__now = 101000;
  vm.runInContext("healthRecordFrontendRender(20, 'test')", context);
  sandbox.__now = 102000;
  vm.runInContext("healthRecordFrontendRender(30, 'test')", context);
  vm.runInContext('healthReportFrontendRender({ force: true })', context);

  assert.equal(sendCalls.length, 1);
  assert.equal(sendCalls[0].cmd, 'report_frontend_render');
  assert.equal(sendCalls[0].render_per_s, 1.5);
  assert.equal(sendCalls[0].render_ms_p95, 30);
});

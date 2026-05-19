const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeElement {
  constructor(id, documentRef) {
    this.id = id || '';
    this.ownerDocument = documentRef || null;
    this._innerHTML = '';
    this.value = '';
    this.textContent = '';
    this.scrollTop = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this.id === 'panel-health' && this.ownerDocument) {
      if (this._innerHTML.includes('health-results')) {
        this.ownerDocument.ensure('health-results');
        this.ownerDocument.ensure('health-window-select');
        this.ownerDocument.ensure('health-scope-select');
        this.ownerDocument.ensure('health-active-group-name');
        this._hasHealthPanel = true;
      }
    }
  }

  querySelector(selector) {
    if (selector === '.health-panel' && this._hasHealthPanel) return this;
    return null;
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.body = new FakeElement('body', this);
  }

  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  querySelectorAll() { return []; }
}

function createHealthSandbox({ visible = true } = {}) {
  const document = new FakeDocument();
  document.ensure('panel-health');
  const sendCalls = [];
  const timers = [];
  const sandbox = {
    console,
    document,
    state: {
      active_group: 'Torque',
      groups: { Torque: [], Other: [] },
    },
    sendCalls,
    timers,
    Date: { now() { return 1779180000000; } },
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout(id) { timers.push({ cleared: id }); },
    _panelAppVisible(app) { return app === 'health' && visible; },
    _activeGroup() { return sandbox.state.active_group; },
    __setVisible(next) { visible = !!next; },
  };
  sandbox.send = function(message) { sendCalls.push(message); };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document, sendCalls };
}

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function samplePayload(group = 'Torque') {
  return {
    type: 'system_health_metrics',
    window: '24h',
    group,
    bucket_seconds: 3600,
    buckets: [{ start: 1, end: 2, label: 'a' }, { start: 2, end: 3, label: 'b' }],
    summary: {
      dispatch: { count: 3, workers_per_hour: 0.125, queued_count: 1, autoresume_count: 1 },
      dispatch_shape: { serial_tool_calls: 1, batch_tool_calls: 1, batch_entries: 2, statuses: { dispatched: 2 } },
      review_cycles: { average_rounds: 1.5, first_pass_clean_pct: 0.5 },
      merge: { merged_count: 2, median_boundary_to_merge_seconds: 7200, open_count: 1, stale_open_count: 0 },
      worker_boot_doa: { count: 1, denominator: 3, rate: 1 / 3 },
      utilization: { percent: 25, busy_seconds: 3600, capacity_seconds: 14400, queue_empty_count: 1 },
    },
    series: {
      dispatches: [1, 2], reviews: [0, 1], merges: [1, 1], worker_boot_doa: [0, 1], utilization_pct: [10, 25],
    },
    distributions: {
      task_age_by_lane: {
        'To Do': { count: 2, p50_seconds: 3600, p90_seconds: 7200, max_seconds: 7200, buckets: { '<1h': 0, '1-4h': 2 } },
      },
    },
    coverage: { dispatch_shape: { partial: true, dispatch_tool_entries: 3, dispatch_events: 3 } },
    notes: ['coverage-limited'],
  };
}

test('opening the Health panel requests 24h metrics for the active group', () => {
  const { sandbox, sendCalls } = createHealthSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');

  vm.runInContext('renderHealthPanel()', context);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), {
    cmd: 'get_system_health_metrics',
    window: '24h',
    group: 'Torque',
  });
});

test('changing the window refetches and keeps selector focus/value after metrics', () => {
  const { sandbox, document, sendCalls } = createHealthSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');
  vm.runInContext('renderHealthPanel()', context);
  const select = document.getElementById('health-window-select');
  select.focus();

  vm.runInContext("healthSetWindow('7d')", context);
  vm.runInContext(`healthReceiveMetrics(${JSON.stringify(Object.assign(samplePayload(), { window: '7d' }))})`, context);

  assert.equal(sendCalls.at(-1).window, '7d');
  assert.equal(select.value, '7d');
  assert.equal(document.activeElement, select);
});

test('metrics response renders summary cards, sparklines, and age rows', () => {
  const { sandbox, document } = createHealthSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');
  vm.runInContext('renderHealthPanel()', context);

  vm.runInContext(`healthReceiveMetrics(${JSON.stringify(samplePayload())})`, context);

  const html = document.getElementById('health-results').innerHTML;
  assert.match(html, /Dispatch throughput/);
  assert.match(html, /Review cycles/);
  assert.match(html, /health-sparkline/);
  assert.match(html, /Task age by lane/);
  assert.match(html, /To Do/);
  assert.match(html, /Partial dispatch-shape coverage/);
});

test('refreshing metrics preserves panel scroll position', () => {
  const { sandbox, document } = createHealthSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');
  vm.runInContext('renderHealthPanel()', context);
  document.getElementById('panel-health').scrollTop = 123;

  vm.runInContext(`healthReceiveMetrics(${JSON.stringify(samplePayload())})`, context);

  assert.equal(document.getElementById('panel-health').scrollTop, 123);
});

test('standalone panel manager recognizes health as a panel app', () => {
  const sandbox = {
    console,
    document: { body: { classList: { add() {} }, dataset: {} }, getElementById() { return null; }, querySelectorAll() { return []; } },
    location: { search: '' },
    URLSearchParams,
    isEmbeddedTerminalMode() { return true; },
    window: { innerWidth: 1200, innerHeight: 800, nativeApi: { available() { return false; } } },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/panel_manager.js');

  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('health') >= 0", context), true);
  assert.equal(vm.runInContext("_standalonePanelTitles.health", context), 'Health');
  assert.equal(vm.runInContext("_standalonePanelDefaults.health", context), 'right');
});

test('active-group changes refetch only while Health is visible', () => {
  const { sandbox, sendCalls } = createHealthSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadScript(context, 'static/js/health.js');
  vm.runInContext('renderHealthPanel()', context);
  sendCalls.length = 0;

  sandbox.state.active_group = 'Other';
  vm.runInContext('healthActiveGroupChanged()', context);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), {
    cmd: 'get_system_health_metrics',
    window: '24h',
    group: 'Other',
  });

  sendCalls.length = 0;
  sandbox.__setVisible(false);
  sandbox.state.active_group = 'Torque';
  vm.runInContext('healthActiveGroupChanged()', context);
  assert.equal(sendCalls.length, 0);
});

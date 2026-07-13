const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() {
    this._set = new Set();
  }
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
  constructor(id = '') {
    this.id = id;
    this.textContent = '';
    this.title = '';
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
    this.classList = new FakeClassList();
    this.attributes = {};
    this.onclick = null;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'data-kind') this.dataset.kind = String(value);
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'data-kind') delete this.dataset.kind;
  }
}

function createSandbox() {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }

  const tabNames = ['gls-general', 'gls-keybindings', 'gls-system'];
  const tabs = tabNames.map((name, idx) => {
    const el = new FakeElement(`tab-${name}`);
    el.dataset.tab = name;
    if (idx === 0) el.classList.add('active');
    return el;
  });
  const panes = tabNames.map((name, idx) => {
    const el = new FakeElement(`pane-${name}`);
    el.dataset.pane = name;
    if (idx === 0) el.classList.add('active');
    return el;
  });

  const nowMs = Date.UTC(2026, 4, 6, 20, 0, 0);
  const sandbox = {
    console,
    __nowMs: nowMs,
    WebSocket: { OPEN: 1 },
    ws: { readyState: 1 },
    state: {
      runtime: {
        version: '1.2.3',
        pid: 12345,
        started_at: (nowMs / 1000) - 7200,
        port: 18970,
        profile: 'desktop',
        data_dir: '/tmp/torque-data',
        log_path: '/tmp/torque-data/torque.log',
      },
    },
    sendCalls: [],
    confirmCalls: [],
    document: {
      body: {
        appendChild() {},
      },
      getElementById(id) {
        return ensure(id);
      },
      createElement(id) {
        return new FakeElement(id);
      },
      querySelectorAll(selector) {
        if (selector === '#modal-global-settings .gs-tab') return tabs;
        if (selector === '#modal-global-settings .gs-pane') return panes;
        return [];
      },
    },
    requestAnimationFrame(fn) { fn(); },
    setTimeout() { return 1; },
    clearTimeout() {},
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.showConfirm = function(message, opts) {
    sandbox.confirmCalls.push({ message, opts: opts || {} });
    return Promise.resolve(true);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, ensure, tabs, panes, nowMs };
}

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function loadCommandsAndModals(context) {
  vm.runInContext('Date.now = function() { return __nowMs; }', context);
  loadScript(context, 'static/js/commands.js');
  loadScript(context, 'static/js/modals/core.js');
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/global-settings.js');
}

function installConfirmStub(context, sandbox) {
  sandbox.__showConfirmStub = function(message, opts) {
    sandbox.confirmCalls.push({ message, opts: opts || {} });
    return Promise.resolve(true);
  };
  vm.runInContext('showConfirm = __showConfirmStub', context);
}

test('global settings System tab markup is present without removing existing tabs', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');

  assert.match(html, /data-tab="gls-general"[^>]*>General<\/button>/);
  assert.match(html, /data-tab="gls-statusbar"[^>]*>Status bar<\/button>/);
  assert.match(html, /data-tab="gls-keybindings"[^>]*>Keybindings<\/button>/);
  assert.match(html, /data-tab="gls-system"[^>]*>System<\/button>/);
  assert.match(html, /<div class="gs-pane active" data-pane="gls-general">/);
  assert.match(html, /<div class="gs-pane" data-pane="gls-statusbar">/);
  assert.match(html, /<div class="gs-pane" data-pane="gls-keybindings">/);
  assert.match(html, /<div class="gs-pane" data-pane="gls-system">/);
  assert.match(html, /id="gls-statusbar-daemon-status"/);
  assert.match(html, /Daemon \+ Supervisor status/);
  assert.match(html, /Supervisor and\s+Relay visibility ride the daemon\/runtime status item/);
  assert.match(html, /id="gls-statusbar-daemon-status"[\s\S]*data-statusbar-item="daemon_status"/);
  assert.match(html, /id="gls-statusbar-attention"/);
  assert.match(html, /id="gls-daemon-version"/);
  assert.match(html, /id="gls-daemon-log-path"/);
});

test('System tab populates daemon status from runtime payload', () => {
  const { sandbox, ensure, tabs, panes } = createSandbox();
  ensure('conn-dot').classList.add('ok');
  const context = vm.createContext(sandbox);
  loadCommandsAndModals(context);

  vm.runInContext(`switchGlsTab('gls-system')`, context);

  assert.equal(tabs.find((el) => el.dataset.tab === 'gls-system').classList.contains('active'), true);
  assert.equal(panes.find((el) => el.dataset.pane === 'gls-system').classList.contains('active'), true);
  assert.equal(ensure('gls-daemon-status-text').textContent, 'Running');
  assert.equal(ensure('gls-daemon-status-dot').classList.contains('daemon-status-dot-ok'), true);
  assert.equal(ensure('gls-daemon-version').textContent, '1.2.3');
  assert.equal(ensure('gls-daemon-pid').textContent, '12345');
  assert.equal(ensure('gls-daemon-uptime').textContent, '2 hours');
  assert.equal(ensure('gls-daemon-port').textContent, '18970');
  assert.equal(ensure('gls-daemon-profile').textContent, 'desktop');
  assert.equal(ensure('gls-daemon-data-dir').textContent, '/tmp/torque-data');
  assert.equal(ensure('gls-daemon-log-path').textContent, '/tmp/torque-data/torque.log');
  assert.equal(ensure('gls-daemon-started-at').textContent, '2 hours ago');
  assert.equal(typeof ensure('gls-restart-daemon-btn').onclick, 'function');
  assert.equal(typeof ensure('gls-stop-daemon-btn').onclick, 'function');
  assert.equal(ensure('gls-stop-daemon-btn').disabled, false);
});

test('daemon status dot reflects disconnected websocket state', () => {
  const { sandbox, ensure } = createSandbox();
  sandbox.ws.readyState = 3;
  const context = vm.createContext(sandbox);
  loadCommandsAndModals(context);

  vm.runInContext('loadDaemonStatus()', context);

  assert.equal(ensure('gls-daemon-status-text').textContent, 'Disconnected');
  assert.equal(ensure('gls-daemon-status-dot').classList.contains('daemon-status-dot-offline'), true);
});

test('daemon relative time formatter handles recent and elapsed times', () => {
  const { sandbox, nowMs } = createSandbox();
  const context = vm.createContext(sandbox);
  loadCommandsAndModals(context);

  assert.equal(
    vm.runInContext(`_formatDaemonRelativeTime(${nowMs / 1000}, ${nowMs})`, context),
    'just now',
  );
  assert.equal(
    vm.runInContext(`_formatDaemonRelativeTime(${(nowMs / 1000) - 90}, ${nowMs})`, context),
    '1 minute ago',
  );
  assert.equal(
    vm.runInContext(`_formatDaemonRelativeTime(${(nowMs / 1000) - 7200}, ${nowMs})`, context),
    '2 hours ago',
  );
});

test('Restart and Stop daemon confirmation copy matches control-panel sketch', async () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadCommandsAndModals(context);
  installConfirmStub(context, sandbox);

  await vm.runInContext('restartDaemon()', context);
  await vm.runInContext('stopDaemon()', context);

  assert.deepEqual(sandbox.confirmCalls.map((call) => call.message), [
    'Restart Torque? Active cells will reconnect after ~15 seconds.',
    'Stop Torque daemon? Active cells will be lost.\n\n'
      + "You'll need to relaunch manually via `make standalone` (or your usual launcher).",
  ]);
  assert.equal(sandbox.confirmCalls[1].opts.label, 'Stop Daemon');
  assert.equal(sandbox.confirmCalls[1].opts.variant, 'btn-danger');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'restart' },
    { cmd: 'stop' },
  ]);
  assert.equal(ensure('system-banner').hidden, false);
  assert.equal(ensure('system-banner').dataset.kind, 'daemon_stopped');
  assert.equal(
    ensure('system-banner').textContent,
    'Torque daemon stopped. Relaunch via `make standalone` to reconnect.',
  );
});

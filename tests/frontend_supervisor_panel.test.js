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
  constructor(id = '') {
    this.id = id;
    this._innerHTML = '';
    this._selectors = new Map();
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.classList = new FakeClassList();
    this.dataset = {};
  }

  get innerHTML() { return this._innerHTML; }

  set innerHTML(value) {
    this._innerHTML = String(value || '');
    this._selectors = new Map();
    if (this._innerHTML.indexOf('supervisor-table-wrap') >= 0) {
      this._selectors.set('.supervisor-table-wrap', new FakeElement());
    }
  }

  querySelector(selector) {
    return this._selectors.get(selector) || null;
  }
}

function createSandbox({
  visible = true,
  persistedSupervisorState = null,
  withUiShim = false,
  confirmResult = true,
} = {}) {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }
  ensure('panel-supervisor');
  const timers = [];
  const cleared = [];
  const sandbox = {
    console,
    state: {
      supervisor_panel_state: persistedSupervisorState || {},
      runtime: {
        supervisor: {
          state: 'up',
          session_count: 0,
          last_op_latency_ms: 0,
        },
      },
    },
    document: {
      getElementById(id) { return ensure(id); },
    },
    sendCalls: [],
    confirmCalls: [],
    timers,
    cleared,
    Date,
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout(id) { cleared.push(id); },
    _panelAppVisible(app) { return app === 'supervisor' && visible; },
    __setVisible(next) { visible = !!next; },
  };
  if (withUiShim) {
    let persistedPanelState = persistedSupervisorState;
    sandbox.registerPanelUiState = function(panel, adapter) {
      sandbox.registeredPanelUiState = { panel, adapter };
      if (persistedPanelState && adapter && typeof adapter.setState === 'function') {
        adapter.setState(persistedPanelState);
      }
      return persistedPanelState;
    };
    sandbox.persistPanelUiState = function(panel, value) {
      persistedPanelState = JSON.parse(JSON.stringify(value));
      sandbox.persistedPanelState = { panel, value: persistedPanelState };
    };
  }
  sandbox.send = function(message) { sandbox.sendCalls.push(message); };
  sandbox.showConfirm = function(message, opts) {
    sandbox.confirmCalls.push({ message, opts });
    return Promise.resolve(confirmResult);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, ensure };
}

function loadSupervisor(context) {
  const filename = path.join(repoRoot, 'static/js/supervisor.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

test('supervisor panel renders alive and exited rows with humanized bytes', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`supervisorReceiveSessions({
    type: 'supervisor_sessions',
    available: true,
    refreshed_at: 1778344000,
    sessions: [
      {
        row_type: 'supervisor',
        session_id: '__supervisor__',
        pid: 87000,
        alive: true,
        started_at: 1778343000,
        display_command: 'PTY supervisor',
        terminable: false,
      },
      {
        session_id: '6d645f307fe64d03875ef72e6787ba66',
        cell_id: 'worker-a',
        pid: 87321,
        alive: true,
        cols: 120,
        rows: 32,
        total_bytes: 1234567,
        started_at: 1778343600,
        current_path: '/repo/.torque/worktrees/worker-a',
        display_command: 'codex',
        owner: { name: 'worker-a', group: 'Torque', kind: 'worker', status: 'running' },
      },
      {
        session_id: 'old-worker-session',
        cell_id: 'old-worker',
        pid: 87001,
        alive: false,
        cols: 80,
        rows: 24,
        total_bytes: 45056,
        started_at: 1778340000,
        cwd: '/repo',
        display_command: 'claude',
        owner: { name: 'old-worker', group: 'Torque', kind: 'worker', status: 'stopped' },
      },
    ],
  })`, context);

  const html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /Supervisor/);
  assert.match(html, /PTY supervisor/);
  assert.match(html, /Started/);
  assert.match(html, /Alive/);
  assert.match(html, /Exited/);
  assert.match(html, /worker-a/);
  assert.match(html, /codex/);
  assert.match(html, /1\.2 MB/);
  assert.match(html, /44 KB/);
  assert.match(html, /Terminate/);
  assert.doesNotMatch(html, /supervisor-detail-toggle/);
  assert.doesNotMatch(html, />Details</);
});


test('supervisor terminate confirms with owner and sends terminate command', async () => {
  const { sandbox } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`supervisorReceiveSessions({
    type: 'supervisor_sessions',
    available: true,
    sessions: [
      {
        row_type: 'supervisor', session_id: '__supervisor__', pid: 87000,
        alive: true, started_at: 1778343000, display_command: 'PTY supervisor',
        terminable: false,
      },
      {
        session_id: 's-terminate', cell_id: 'worker-a', pid: 87321, alive: true,
        started_at: 1778343600, cols: 120, rows: 32, total_bytes: 10,
        display_command: 'codex', owner: { name: 'worker-a', group: 'Torque', kind: 'worker' },
      },
    ],
  })`, context);

  const accepted = await vm.runInContext(`supervisorTerminateSession('s-terminate')`, context);

  assert.equal(accepted, true);
  assert.equal(sandbox.confirmCalls.length, 1);
  assert.match(sandbox.confirmCalls[0].message, /worker-a/);
  assert.match(sandbox.confirmCalls[0].message, /pid 87321/);
  assert.equal(sandbox.confirmCalls[0].opts.label, 'Terminate');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[sandbox.sendCalls.length - 1])), {
    cmd: 'supervisor_session_terminate',
    session_id: 's-terminate',
  });
});

test('supervisor restart confirms with preserved-worker framing and sends restart command', async () => {
  const { sandbox } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext('renderSupervisorPanel({ force: true })', context);
  const accepted = await vm.runInContext('supervisorRestart()', context);

  assert.equal(accepted, true);
  assert.equal(sandbox.confirmCalls.length, 1);
  assert.match(sandbox.confirmCalls[0].message, /re-exec the supervisor in place/);
  assert.match(sandbox.confirmCalls[0].message, /live workers are preserved/);
  assert.equal(sandbox.confirmCalls[0].opts.label, 'Restart supervisor');
  assert.equal(sandbox.confirmCalls[0].opts.variant, 'btn-rebase');
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[sandbox.sendCalls.length - 1])), {
    cmd: 'supervisor_restart',
  });
});

test('supervisor restart cancel does not dispatch command', async () => {
  const { sandbox } = createSandbox({ visible: true, confirmResult: false });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext('renderSupervisorPanel({ force: true })', context);
  const accepted = await vm.runInContext('supervisorRestart()', context);

  assert.equal(accepted, false);
  assert.equal(sandbox.confirmCalls.length, 1);
  assert.equal(sandbox.sendCalls.some((call) => call.cmd === 'supervisor_restart'), false);
});

test('supervisor restart control disables with spinner during restarting runtime window', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    renderSupervisorPanel({ force: true });
    state.runtime.supervisor.state = 'restarting';
    supervisorReceiveRuntime(state.runtime.supervisor);
  `, context);

  let html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /id="supervisor-restart-btn"[^>]*disabled/);
  assert.match(html, /supervisor-spinner/);
  assert.match(html, /Restarting…/);
  assert.match(html, /live worker PTYs will be adopted/);

  vm.runInContext(`
    state.runtime.supervisor.state = 'up';
    supervisorReceiveRuntime(state.runtime.supervisor);
  `, context);

  html = ensure('panel-supervisor').innerHTML;
  assert.doesNotMatch(html, /id="supervisor-restart-btn"[^>]*disabled/);
  assert.doesNotMatch(html, /supervisor-spinner/);
});

test('supervisor restart failure reply is surfaced in the control', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    renderSupervisorPanel({ force: true });
    supervisorReceiveRestart({
      type: 'supervisor_restart',
      ok: false,
      available: true,
      message: 'PTY supervisor restart failed.',
      error: 'adopt-state lost 2 sessions',
    });
  `, context);

  const html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /supervisor-restart-status-error/);
  assert.match(html, /role="alert"/);
  assert.match(html, /PTY supervisor restart failed\./);
  assert.match(html, /adopt-state lost 2 sessions/);
  assert.doesNotMatch(html, /id="supervisor-restart-btn"[^>]*disabled/);
});


test('supervisor terminated response hides the closed row during drop grace period', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    supervisorReceiveSessions({
      type: 'supervisor_sessions', available: true,
      sessions: [{ session_id: 's1', pid: 1, alive: true, cols: 80, rows: 24, owner: { name: 'worker-a' }, display_command: 'codex' }],
    });
    supervisorReceiveSessions({
      type: 'supervisor_sessions', available: true,
      terminate_session_id: 's1', terminated_session_id: 's1',
      sessions: [{ session_id: 's1', pid: 1, alive: false, cols: 80, rows: 24, owner: { name: 'worker-a' }, display_command: 'codex' }],
    });
  `, context);

  assert.equal(vm.runInContext(`supervisorState.sessions.some(s => s.session_id === 's1')`, context), false);
  assert.doesNotMatch(ensure('panel-supervisor').innerHTML, /worker-a/);
});

test('supervisor self-row stays top sorted and cannot be terminated', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    supervisorReceiveSessions({
      type: 'supervisor_sessions',
      available: true,
      sessions: [
        { session_id: 'z-worker', pid: 2, alive: true, started_at: 20, cols: 80, rows: 24, owner: { name: 'z-worker' }, display_command: 'z' },
        { row_type: 'supervisor', session_id: '__supervisor__', pid: 1, alive: true, started_at: 10, display_command: 'PTY supervisor', terminable: false },
        { session_id: 'a-worker', pid: 3, alive: true, started_at: 30, cols: 80, rows: 24, owner: { name: 'a-worker' }, display_command: 'a' },
      ],
    });
    supervisorSortBy('owner');
  `, context);

  const order = JSON.parse(JSON.stringify(vm.runInContext(`_supervisorSortedSessions().map(s => s.session_id)`, context)));
  assert.deepEqual(order, ['__supervisor__', 'z-worker', 'a-worker']);
  assert.equal(vm.runInContext(`_supervisorCanTerminate(_supervisorFindSession('__supervisor__'))`, context), false);
  const html = ensure('panel-supervisor').innerHTML;
  const selfIndex = html.indexOf('__supervisor__');
  const firstTerminate = html.indexOf('Terminate');
  assert.ok(selfIndex >= 0);
  assert.ok(firstTerminate === -1 || firstTerminate > selfIndex);
});

test('supervisor panel renders unavailable banner without throwing', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`supervisorReceiveSessions({
    type: 'supervisor_sessions',
    available: false,
    mode: 'toolbelt',
    terminal_backend: 'iterm2',
    sessions: [],
    message: 'PTY supervisor is only available in standalone embedded-terminal mode.',
  })`, context);

  const html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /Unavailable/);
  assert.match(html, /only available in standalone embedded-terminal mode/);
});

test('opening supervisor panel sends list command and auto-refresh only schedules while visible', () => {
  const { sandbox } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext('supervisorEnsureLoaded()', context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'supervisor_sessions_list' },
  ]);
  assert.equal(sandbox.timers.length, 2);
  assert.ok(sandbox.timers.some((timer) => timer.delay === 10000));
  assert.ok(sandbox.timers.some((timer) => timer.delay === 2000));

  const hidden = createSandbox({ visible: false });
  const hiddenContext = vm.createContext(hidden.sandbox);
  loadSupervisor(hiddenContext);
  vm.runInContext('supervisorSetAutoRefresh(true)', hiddenContext);
  assert.equal(hidden.sandbox.timers.length, 0);
});

test('supervisor transient unavailable response preserves last successful sessions and backs off', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    supervisorReceiveSessions({
      available: true,
      refreshed_at: (Date.now() / 1000) - 120,
      sessions: [{
        session_id: 's1', cell_id: 'worker-a', pid: 1, alive: true,
        cols: 100, rows: 30, total_bytes: 99, cwd: '/tmp',
        display_command: 'codex',
        owner: { name: 'worker-a', group: 'Torque', kind: 'worker', status: 'running' },
      }],
    });
    supervisorReceiveSessions({
      available: false,
      sessions: [],
      message: 'PTY supervisor is temporarily unavailable.',
    });
  `, context);

  const html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /worker-a/);
  assert.match(html, /PTY supervisor is temporarily unavailable/);
  assert.match(html, /last update 2m ago/);
  assert.ok(sandbox.timers.some((timer) => timer.delay === 5000));
  assert.equal(vm.runInContext('supervisorState.sessions.length', context), 1);
});

test('supervisor auto refresh suppresses overlapping in-flight requests but manual refresh bypasses', () => {
  const { sandbox } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  vm.runInContext(`
    supervisorRequestSessions(false);
    supervisorRequestSessions(false);
  `, context);

  assert.equal(sandbox.sendCalls.length, 1);

  vm.runInContext('supervisorRequestSessions(true)', context);
  assert.equal(sandbox.sendCalls.length, 2);

  const stall = sandbox.timers.find((timer) => timer.delay === 10000);
  assert.ok(stall, 'request stall timeout should be armed');
  stall.fn();
  assert.equal(vm.runInContext('supervisorState.requestInFlight', context), false);
});

test('supervisorReceiveSessions preserves selected row, expanded row, and scroll position', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);
  ensure('panel-supervisor').scrollTop = 88;

  vm.runInContext(`
    supervisorState.selectedSessionId = 's1';
    supervisorState.expandedSessionId = 's1';
    supervisorReceiveSessions({
      available: true,
      sessions: [{
        session_id: 's1', cell_id: 'c1', pid: 1, alive: true,
        cols: 100, rows: 30, total_bytes: 99, cwd: '/tmp',
        shell_argv: ['/bin/zsh', '-il'], display_command: 'codex',
        owner: { name: 'worker-a', group: 'Torque', kind: 'worker', status: 'running' },
      }],
    });
  `, context);

  assert.equal(vm.runInContext('supervisorState.selectedSessionId', context), 's1');
  assert.equal(vm.runInContext('supervisorState.expandedSessionId', context), 's1');
  assert.equal(ensure('panel-supervisor').scrollTop, 88);
  assert.match(ensure('panel-supervisor').innerHTML, /supervisor-detail-row/);
});

test('supervisorReceiveSessions preserves table scroll positions across refresh rerenders', () => {
  const { sandbox, ensure } = createSandbox({ visible: true });
  const context = vm.createContext(sandbox);
  loadSupervisor(context);

  const payload = `({
    available: true,
    sessions: Array.from({ length: 12 }, (_, idx) => ({
      session_id: 's' + idx, cell_id: 'c' + idx, pid: 100 + idx, alive: true,
      cols: 160, rows: 48, total_bytes: 99,
      cwd: '/tmp/really/long/path/' + idx,
      display_command: 'codex --wide-command --worker=' + idx,
      owner: { name: 'worker-' + idx, group: 'Torque', kind: 'worker', status: 'running' },
    })),
  })`;

  vm.runInContext(`supervisorReceiveSessions(${payload})`, context);
  const root = ensure('panel-supervisor');
  const firstWrap = root.querySelector('.supervisor-table-wrap');
  assert.ok(firstWrap, 'table wrapper should exist after first render');
  root.scrollTop = 88;
  firstWrap.scrollLeft = 240;
  firstWrap.scrollTop = 64;

  vm.runInContext(`
    supervisorReceiveSessions(${payload});
    supervisorReceiveSessions(${payload});
  `, context);

  const nextWrap = root.querySelector('.supervisor-table-wrap');
  assert.ok(nextWrap, 'table wrapper should exist after refresh rerender');
  assert.notEqual(nextWrap, firstWrap, 'test fake should model DOM replacement');
  assert.equal(root.scrollTop, 88);
  assert.equal(nextWrap.scrollLeft, 240);
  assert.equal(nextWrap.scrollTop, 64);
});

test('supervisor panel persists UI state and restores sort state from snapshot', () => {
  const first = createSandbox({ visible: true });
  const firstContext = vm.createContext(first.sandbox);
  loadSupervisor(firstContext);

  vm.runInContext(`
    supervisorSortBy('bytes');
    supervisorSelectSession('s1');
    supervisorToggleDetails('s1');
    supervisorSetAutoRefresh(false);
  `, firstContext);

  const persistCalls = first.sandbox.sendCalls.filter((call) => call.cmd === 'ui_set_supervisor_panel_state');
  assert.ok(persistCalls.length >= 1);
  const persisted = first.sandbox.state.supervisor_panel_state;
  assert.equal(persisted.sortKey, 'bytes');
  assert.equal(persisted.sortDirection, 'desc');
  assert.equal(persisted.selectedSessionId, 's1');
  assert.equal(persisted.expandedSessionId, 's1');
  assert.equal(persisted.autoRefresh, false);

  const second = createSandbox({ visible: true, persistedSupervisorState: persisted });
  const secondContext = vm.createContext(second.sandbox);
  loadSupervisor(secondContext);
  vm.runInContext('renderSupervisorPanel({ force: true })', secondContext);

  assert.equal(vm.runInContext('supervisorState.sortKey', secondContext), 'bytes');
  assert.equal(vm.runInContext('supervisorState.sortDirection', secondContext), 'desc');
  assert.equal(vm.runInContext('supervisorState.selectedSessionId', secondContext), 's1');
  assert.equal(vm.runInContext('supervisorState.expandedSessionId', secondContext), 's1');
  assert.equal(vm.runInContext('supervisorState.autoRefresh', secondContext), false);
});

test('supervisor taskbar CSS and panel-manager registration are bounded to standalone', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const panelManager = fs.readFileSync(path.join(repoRoot, 'static/js/panel_manager.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const render = fs.readFileSync(path.join(repoRoot, 'static/js/render.js'), 'utf8');
  const ws = fs.readFileSync(path.join(repoRoot, 'static/js/ws.js'), 'utf8');

  assert.match(html, /id="panel-supervisor"/);
  assert.match(html, /data-app="supervisor"[^>]*togglePanel\('supervisor'\)/);
  assert.match(html, /static\/js\/supervisor\.js[\s\S]*static\/js\/panel_manager\.js/);
  assert.match(css, /\.taskbar-app\[data-app="supervisor"\]\s*\{\s*display:\s*none;\s*\}/);
  assert.match(css, /body\.runtime-embedded \.taskbar-app\[data-app="supervisor"\]:not\(\.nav-secondary\)\s*\{\s*display:\s*inline-flex;\s*\}/);
  assert.match(css, /\.supervisor-auto input\[type="checkbox"\]\s*\{[^}]*width:\s*auto;[^}]*padding:\s*0;/s);
  assert.match(panelManager, /_standalonePanelApps = \[[^\]]*'supervisor'/);
  assert.match(panelManager, /supervisor:\s*'Supervisor'/);
  assert.match(panelManager, /supervisor:\s*'bottom'/);
  assert.match(panelManager, /function _standaloneDefaultTabsForZone\(zoneName\)/);
  assert.match(panelManager, /var bottomTabs = _standaloneDefaultTabsForZone\('bottom'\)/);
  assert.match(panelManager, /var rightTabs = _standaloneDefaultTabsForZone\('right'\)/);
  assert.match(main, /'panel-supervisor'/);
  assert.match(render, /surface === 'supervisor'/);
  assert.match(ws, /msg\.type === 'supervisor_sessions'[\s\S]*supervisorReceiveSessions\(msg\)/);
  assert.match(ws, /msg\.type === 'supervisor_restart'[\s\S]*supervisorReceiveRestart\(msg\)/);
  assert.match(ws, /_registerDeltaOperations\('runtime',[\s\S]*supervisorReceiveRuntime\(state\.runtime && state\.runtime\.supervisor\)/);
  assert.match(ws, /msg\.type === 'system_banner'[\s\S]*_applySystemBanner\(msg\.banner\)/);
});

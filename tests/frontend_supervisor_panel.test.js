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
    this.innerHTML = '';
    this.scrollTop = 0;
    this.classList = new FakeClassList();
    this.dataset = {};
  }
}

function createSandbox({ visible = true } = {}) {
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
    document: {
      getElementById(id) { return ensure(id); },
    },
    sendCalls: [],
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
  sandbox.send = function(message) { sandbox.sendCalls.push(message); };
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
        session_id: '6d645f307fe64d03875ef72e6787ba66',
        cell_id: 'worker-a',
        pid: 87321,
        alive: true,
        cols: 120,
        rows: 32,
        total_bytes: 1234567,
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
        cwd: '/repo',
        display_command: 'claude',
        owner: { name: 'old-worker', group: 'Torque', kind: 'worker', status: 'stopped' },
      },
    ],
  })`, context);

  const html = ensure('panel-supervisor').innerHTML;
  assert.match(html, /Supervisor/);
  assert.match(html, /Alive/);
  assert.match(html, /Exited/);
  assert.match(html, /worker-a/);
  assert.match(html, /codex/);
  assert.match(html, /1\.2 MB/);
  assert.match(html, /44 KB/);
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
  assert.equal(sandbox.timers.length, 1);
  assert.equal(sandbox.timers[0].delay, 2000);

  const hidden = createSandbox({ visible: false });
  const hiddenContext = vm.createContext(hidden.sandbox);
  loadSupervisor(hiddenContext);
  vm.runInContext('supervisorSetAutoRefresh(true)', hiddenContext);
  assert.equal(hidden.sandbox.timers.length, 0);
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
  assert.match(css, /body\.runtime-embedded \.taskbar-app\[data-app="supervisor"\]\s*\{\s*display:\s*inline-flex;\s*\}/);
  assert.match(css, /\.supervisor-auto input\[type="checkbox"\]\s*\{[^}]*width:\s*auto;[^}]*padding:\s*0;/s);
  assert.match(panelManager, /_standalonePanelApps = \[[^\]]*'supervisor'/);
  assert.match(panelManager, /supervisor:\s*'Supervisor'/);
  assert.match(panelManager, /supervisor:\s*'bottom'/);
  assert.match(panelManager, /tabs:\s*\['actions', 'templates', 'context', 'events'\]/);
  assert.match(main, /'panel-supervisor'/);
  assert.match(render, /surface === 'supervisor'/);
  assert.match(ws, /msg\.type === 'supervisor_sessions'[\s\S]*supervisorReceiveSessions\(msg\)/);
});

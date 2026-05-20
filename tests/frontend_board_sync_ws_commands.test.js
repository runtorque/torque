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
    if (force === undefined) {
      if (this._set.has(name)) { this._set.delete(name); return false; }
      this._set.add(name); return true;
    }
    if (force) this._set.add(name);
    else this._set.delete(name);
    return !!force;
  }
}

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.textContent = '';
    this.className = '';
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.classList = new FakeClassList();
    this._innerHTML = '';
    this.focused = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = String(value); this.children = []; }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  remove() { this.removed = true; }
  focus() { this.focused = true; }
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 160, bottom: 200, width: 160, height: 200 };
  }
  addEventListener() {}
  removeEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return this; }
}

function createSandbox() {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }
  const sandbox = {
    console,
    Date,
    JSON,
    Math,
    location: { host: 'localhost:18933' },
    window: { open(url) { sandbox.openedUrls.push(url); } },
    navigator: { clipboard: { writeText() {} } },
    openedUrls: [],
    sendCalls: [],
    toastCalls: [],
    state: {
      groups: { alpha: [] },
      agents: {},
      board_tasks: {
        'task-1': {
          id: 'task-1',
          group: 'alpha',
          lane: 'In Progress',
          task: 'Sync me',
          provider: 'github',
          external_url: 'https://github.com/acme/widgets/issues/123',
          board_sync: {
            provider: 'github',
            enabled: true,
            sync_state: 'queued',
            github: { issue_number: 123, issue_url: 'https://github.com/acme/widgets/issues/123' },
          },
        },
      },
      group_settings: {
        alpha: { board_sync_provider: 'github', board_sync_enabled: true },
      },
    },
    document: {
      body: new FakeElement('body'),
      activeElement: null,
      getElementById(id) { return ensure(id); },
      createElement(tagName) { const el = new FakeElement(tagName); el.tagName = String(tagName).toUpperCase(); return el; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
      removeEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    requestAnimationFrame(fn) { fn(); },
    setTimeout(fn) { fn(); return 1; },
    clearTimeout() {},
    esc(value) { return String(value); },
    formatCode(value) { return String(value); },
    isSystemLabel(label) { return String(label).startsWith('torque:'); },
    displayLabel(label) { return String(label).replace(/^torque:/, ''); },
    labelColor() { return '#58a6ff'; },
    _currentGroup() { return 'alpha'; },
    _showToast(message, level) { sandbox.toastCalls.push({ message, level }); },
  };
  sandbox.send = function(message) { sandbox.sendCalls.push(message); };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, ensure };
}

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function loadBoardSyncScripts(context) {
  loadScript(context, 'static/js/modals.js');
  loadScript(context, 'static/js/modals/task-modal.js');
  loadScript(context, 'static/js/board.js');
  loadScript(context, 'static/js/board/view-state.js');
  loadScript(context, 'static/js/board/card-rendering.js');
  loadScript(context, 'static/js/board/card-actions.js');
  vm.runInContext(`
    _closeCtxMenu = function() {};
    closeNestedModal = function() { return false; };
    openNestedModal = function(id) { document.getElementById(id).classList.add('visible'); };
  `, context);
}

function lastCall(sandbox) {
  return sandbox.sendCalls[sandbox.sendCalls.length - 1];
}

test('Group Settings Test connection sends board_sync_preflight envelope and surfaces scope guidance', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  ensure('gs-board-sync-provider').value = 'github';
  ensure('gs-board-sync-enabled').checked = false;
  ensure('gs-board-sync-github-repo').value = 'acme/widgets';
  vm.runInContext('_settingsGroup = "alpha"; testGroupBoardSyncConnection();', context);

  assert.equal(lastCall(sandbox).cmd, 'board_sync_preflight');
  assert.equal(lastCall(sandbox).group, 'alpha');
  assert.equal(lastCall(sandbox).provider, 'github');
  assert.equal(lastCall(sandbox).settings.board_sync_enabled, false);
  assert.equal(lastCall(sandbox).settings.board_sync_github.github_repo, 'acme/widgets');
  assert.equal(ensure('gs-board-sync-preflight-summary').textContent, 'Testing GitHub connection…');

  vm.runInContext(`_handleBoardSyncPreflight({
    type: 'board_sync_preflight',
    group: 'alpha',
    ok: false,
    phase: 'project_scope',
    error: "GitHub Projects v2 sync requires the 'project' scope."
  })`, context);

  assert.match(ensure('gs-board-sync-preflight-summary').textContent, /gh auth refresh -s project/);
  assert.equal(sandbox.toastCalls.at(-1).level, 'error');
});

test('Group Settings Use current repo consumes board_sync_preflight repo response', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  ensure('gs-board-sync-provider').value = 'github';
  vm.runInContext('_settingsGroup = "alpha"; gsBoardSyncUseCurrentRepo();', context);
  assert.equal(lastCall(sandbox).cmd, 'board_sync_preflight');
  assert.equal(lastCall(sandbox).provider, 'github');
  assert.equal(lastCall(sandbox).args.group, 'alpha');

  vm.runInContext(`_handleBoardSyncPreflight({
    type: 'board_sync_preflight',
    group: 'alpha',
    ok: true,
    repo: 'acme/widgets',
    project_number: 7
  })`, context);

  assert.equal(ensure('gs-board-sync-github-repo').value, 'acme/widgets');
  assert.match(ensure('gs-board-sync-preflight-summary').textContent, /GitHub connection OK/);
  assert.match(ensure('gs-board-sync-preflight-summary').textContent, /Project OK/);
});

test('task modal Sync now and Pull preview send board_sync_task and board_pull_preview envelopes', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext('_taskEditId = "task-1"; taskModalSyncNow(); taskModalPullPreview();', context);

  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[0])), {
    cmd: 'board_sync_task',
    args: { task: 'task-1' },
    task: 'task-1',
  });
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[1])), {
    cmd: 'board_pull_preview',
    args: { task: 'task-1' },
    task: 'task-1',
  });
});

test('card GitHub context menu exposes sync actions that send expected envelopes', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext('_boardRenderCardMenu("task-1")', context);
  const menuHtml = sandbox.document.getElementById('ctx-menu').innerHTML;
  assert.match(menuHtml, /Open GitHub issue/);
  assert.match(menuHtml, /Push to GitHub/);
  assert.match(menuHtml, /Sync GitHub now/);
  assert.match(menuHtml, /Pull preview/);
  assert.match(menuHtml, /Unlink external issue/);

  vm.runInContext('boardSyncTaskNow("task-1", { quiet: true }); boardPullPreview("task-1", { quiet: true });', context);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[0])), {
    cmd: 'board_sync_task',
    args: { task: 'task-1' },
    task: 'task-1',
  });
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[1])), {
    cmd: 'board_pull_preview',
    args: { task: 'task-1' },
    task: 'task-1',
  });
});

test('task modal track checkbox creates an explicit board sync opt-out payload', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  ensure('task-external-provider-input').value = 'github';
  ensure('task-board-sync-track-input').checked = false;
  vm.runInContext(`
    _taskBoardSync = {
      version: 1,
      provider: 'github',
      enabled: true,
      sync_state: 'error',
      last_error: 'missing scope',
      github: { issue_number: 123, issue_url: 'https://github.com/acme/widgets/issues/123' }
    };
    taskBoardSyncTrackChanged();
    var untrackPayload = _taskBoardSyncPayloadForSubmit();
  `, context);

  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(untrackPayload)', context)), {
    version: 1,
    provider: 'github',
    enabled: false,
    sync_state: 'idle',
    last_error: '',
    github: { issue_number: 123, issue_url: 'https://github.com/acme/widgets/issues/123' },
  });
});

test('board sync-only GitHub links can be unlinked and opt out of tracking', () => {
  const { sandbox } = createSandbox();
  sandbox.state.board_tasks['sync-only'] = {
    id: 'sync-only',
    group: 'alpha',
    lane: 'Backlog',
    task: 'Sync-created link',
    board_sync: {
      provider: 'github',
      enabled: true,
      sync_state: 'idle',
      github: {
        issue_number: 456,
        issue_url: 'https://github.com/acme/widgets/issues/456',
      },
    },
  };
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext('_boardRenderCardMenu("sync-only"); boardClearExternal("sync-only");', context);
  const menuHtml = sandbox.document.getElementById('ctx-menu').innerHTML;
  assert.match(menuHtml, /Open GitHub issue/);
  assert.match(menuHtml, /Unlink external issue/);
  assert.deepEqual(JSON.parse(JSON.stringify(lastCall(sandbox))), {
    cmd: 'external_link_task',
    id: 'sync-only',
    provider: '',
    external_id: '',
    external_url: '',
    ref: '',
    board_sync: { version: 1, enabled: false },
  });
});

test('board_sync_task responses toast success and route errors to retry toast', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext(`
    var syncErrorCalls = [];
    _boardShowSyncErrorToast = function(message, taskId) {
      syncErrorCalls.push({ message: message, taskId: taskId });
    };
    _handleBoardSyncTaskResponse({ type: 'board_sync_task', ok: true, queued: true, task_id: 'task-1' });
    _handleBoardSyncTaskResponse({ type: 'board_sync_task', ok: false, task_id: 'task-1', error: 'repo not found' });
  `, context);

  assert.deepEqual(sandbox.toastCalls.at(-1), { message: 'GitHub sync queued', level: 'success' });
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(syncErrorCalls)', context)), [
    { message: 'repo not found', taskId: 'task-1' },
  ]);
});

test('board_pull_preview response opens diff dialog with selectable fields and repo errors toast', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext(`
    var syncErrorCalls = [];
    _boardShowSyncErrorToast = function(message, taskId) {
      syncErrorCalls.push({ message: message, taskId: taskId });
    };
    _handleBoardPullPreview({
      type: 'board_pull_preview',
      ok: true,
      task_id: 'task-1',
      group: 'alpha',
      provider: 'github',
      changes: {
        task: { local: 'Local title', remote: 'GitHub title' },
        lane: { local: 'Backlog', remote: 'In Progress' }
      }
    });
  `, context);

  assert.equal(ensure('modal-board-pull-preview').classList.contains('visible'), true);
  assert.match(ensure('board-pull-preview-summary').textContent, /Review 2 inbound changes/);
  assert.match(ensure('board-pull-preview-changes').innerHTML, /board-pull-preview-field/);
  assert.match(ensure('board-pull-preview-changes').innerHTML, /GitHub title/);

  vm.runInContext(`_handleBoardPullPreview({
    type: 'board_pull_preview',
    ok: false,
    task_id: 'task-1',
    phase: 'repo',
    error: 'repo not found'
  })`, context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(syncErrorCalls)', context)), [
    { message: 'repo not found', taskId: 'task-1' },
  ]);
});

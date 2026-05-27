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
    this.attributes = {};
    this.parentNode = null;
    this.onclick = null;
    this._listeners = {};
    this._innerHTML = '';
    this.focused = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = parseButtonChildren(this._innerHTML, this);
  }
  appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
  remove() { this.removed = true; }
  focus() { this.focused = true; }
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 160, bottom: 200, width: 160, height: 200 };
  }
  addEventListener(type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  }
  removeEventListener(type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter((item) => item !== fn);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    event.currentTarget = this;
    event.preventDefault = event.preventDefault || function() { event.defaultPrevented = true; };
    event.stopPropagation = event.stopPropagation || function() { event._stopped = true; };
    if (typeof this.onclick === 'function' && event.type === 'click') this.onclick(event);
    for (const fn of this._listeners[event.type] || []) fn(event);
    if (!event._stopped && this.parentNode && this.parentNode.dispatchEvent) {
      this.parentNode.dispatchEvent(event);
    }
    return !event.defaultPrevented;
  }
  click() { this.dispatchEvent({ type: 'click', target: this }); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }
  setAttribute(name, value) {
    const stringValue = String(value);
    this.attributes[name] = stringValue;
    if (name === 'class') this.className = stringValue;
    if (name === 'disabled') this.disabled = true;
    if (name.indexOf('data-') === 0) {
      this.dataset[dataKey(name)] = stringValue;
    }
  }
  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }
  closest(selector) {
    if (selector && selector[0] === '[' && selector[selector.length - 1] === ']') {
      const attr = selector.slice(1, -1);
      let el = this;
      while (el) {
        if (el.hasAttribute && el.hasAttribute(attr)) return el;
        el = el.parentNode;
      }
      return null;
    }
    return this;
  }
}

function dataKey(attrName) {
  return attrName
    .slice(5)
    .replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
}

function decodeHtml(value) {
  return String(value)
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&');
}

function parseButtonChildren(html, parent) {
  const children = [];
  const buttonRe = /<button\b([^>]*)>([\s\S]*?)<\/button>/gi;
  let match;
  while ((match = buttonRe.exec(html)) !== null) {
    const button = new FakeElement('button');
    button.tagName = 'BUTTON';
    button.parentNode = parent;
    button.textContent = decodeHtml(match[2].replace(/<[^>]*>/g, '')).trim();
    const attrs = match[1] || '';
    const attrRe = /([a-zA-Z0-9_-]+)(?:="([^"]*)")?/g;
    let attr;
    while ((attr = attrRe.exec(attrs)) !== null) {
      button.setAttribute(attr[1], decodeHtml(attr[2] || ''));
    }
    children.push(button);
  }
  return children;
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
    window: {
      open(url) { sandbox.openedUrls.push(url); },
      prompt(message, defaultValue) {
        sandbox.promptCalls.push({ message, defaultValue });
        return sandbox.promptResponses.length ? sandbox.promptResponses.shift() : null;
      },
    },
    navigator: { clipboard: { writeText() {} } },
    openedUrls: [],
    promptCalls: [],
    promptResponses: [],
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

function menuButton(sandbox, label) {
  const menu = sandbox.document.getElementById('ctx-menu');
  return menu.children.find((child) => child.textContent === label);
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
  ensure('gs-board-sync-enabled').checked = false;
  ensure('gs-board-sync-github-repo').value = 'draft/widgets';
  vm.runInContext('_settingsGroup = "alpha"; gsBoardSyncUseCurrentRepo();', context);
  assert.equal(lastCall(sandbox).cmd, 'board_sync_preflight');
  assert.equal(lastCall(sandbox).provider, 'github');
  assert.equal(lastCall(sandbox).args.group, 'alpha');
  assert.equal(lastCall(sandbox).settings.board_sync_enabled, false);
  assert.equal(lastCall(sandbox).settings.board_sync_github.github_repo, 'draft/widgets');

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

test('card GitHub context menu exposes only Open, Sync, and Unlink actions that work on click', () => {
  const { sandbox } = createSandbox();
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext('_boardRenderCardMenu("task-1")', context);
  const menuHtml = sandbox.document.getElementById('ctx-menu').innerHTML;
  assert.match(menuHtml, />Open in GitHub<\/button>/);
  assert.match(menuHtml, />Sync<\/button>/);
  assert.match(menuHtml, />Unlink<\/button>/);
  assert.doesNotMatch(menuHtml, /Open GitHub issue/);
  assert.doesNotMatch(menuHtml, /Push to GitHub/);
  assert.doesNotMatch(menuHtml, /Sync GitHub now/);
  assert.doesNotMatch(menuHtml, /Pull preview/);
  assert.doesNotMatch(menuHtml, /Edit external link/);
  assert.doesNotMatch(menuHtml, /Preview prompt/);
  assert.doesNotMatch(menuHtml, /Unlink external issue/);

  menuButton(sandbox, 'Open in GitHub').click();
  assert.deepEqual(sandbox.openedUrls, ['https://github.com/acme/widgets/issues/123']);
  assert.equal(sandbox.sendCalls.length, 0);

  menuButton(sandbox, 'Sync').click();
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[0])), {
    cmd: 'board_sync_task',
    args: { task: 'task-1' },
    task: 'task-1',
  });

  menuButton(sandbox, 'Unlink').click();
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls[1])), {
    cmd: 'external_link_task',
    id: 'task-1',
    provider: '',
    external_id: '',
    external_url: '',
    ref: '',
    board_sync: { version: 1, enabled: false },
  });
});

test('board multi-select batch Sync and Unlink loop linked selected tasks only', () => {
  const { sandbox } = createSandbox();
  sandbox.state.board_tasks['sync-only'] = {
    id: 'sync-only',
    group: 'alpha',
    lane: 'In Progress',
    task: 'Board-sync-only GitHub issue',
    board_sync: {
      provider: 'github',
      enabled: true,
      github: {
        issue_number: 456,
        issue_url: 'https://github.com/acme/widgets/issues/456',
      },
    },
  };
  sandbox.state.board_tasks['no-link'] = {
    id: 'no-link',
    group: 'alpha',
    lane: 'In Progress',
    task: 'No external link yet',
    board_sync: { provider: 'github', enabled: true },
  };
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);
  vm.runInContext(`
    var renderCalls = 0;
    renderBoard = function() { renderCalls++; };
    _boardSelectedTasks = { 'task-1': true, 'sync-only': true, 'no-link': true };
  `, context);

  const barHtml = vm.runInContext('_renderBoardSelectionBar()', context);
  assert.match(barHtml, />Sync \(2\)<\/button>/);
  assert.match(barHtml, />Unlink \(2\)<\/button>/);

  vm.runInContext('boardBulkSyncGithub();', context);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    { cmd: 'board_sync_task', args: { task: 'task-1' }, task: 'task-1' },
    { cmd: 'board_sync_task', args: { task: 'sync-only' }, task: 'sync-only' },
  ]);
  assert.equal(vm.runInContext('_boardSelectedCount()', context), 0);
  assert.equal(vm.runInContext('renderCalls', context), 1);

  sandbox.sendCalls.length = 0;
  vm.runInContext(`
    _boardSelectedTasks = { 'task-1': true, 'sync-only': true, 'no-link': true };
    boardBulkUnlinkGithub();
  `, context);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.sendCalls)), [
    {
      cmd: 'external_link_task',
      id: 'task-1',
      provider: '',
      external_id: '',
      external_url: '',
      ref: '',
      board_sync: { version: 1, enabled: false },
    },
    {
      cmd: 'external_link_task',
      id: 'sync-only',
      provider: '',
      external_id: '',
      external_url: '',
      ref: '',
      board_sync: { version: 1, enabled: false },
    },
  ]);
  assert.equal(vm.runInContext('_boardSelectedCount()', context), 0);

  sandbox.sendCalls.length = 0;
  vm.runInContext(`
    _boardSelectedTasks = { 'no-link': true };
    boardBulkSyncGithub();
    boardBulkUnlinkGithub();
  `, context);
  assert.deepEqual(sandbox.sendCalls, []);
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

  vm.runInContext('_boardRenderCardMenu("sync-only");', context);
  const menuHtml = sandbox.document.getElementById('ctx-menu').innerHTML;
  assert.match(menuHtml, /Open in GitHub/);
  assert.match(menuHtml, />Sync<\/button>/);
  assert.match(menuHtml, />Unlink<\/button>/);
  assert.doesNotMatch(menuHtml, /Open GitHub issue/);
  assert.doesNotMatch(menuHtml, /Unlink external issue/);
  menuButton(sandbox, 'Unlink').click();
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

test('editing a board sync-only GitHub link clears stale sync metadata', () => {
  const { sandbox } = createSandbox();
  sandbox.promptResponses.push('https://github.com/acme/widgets/issues/789');
  sandbox.state.board_tasks['sync-only'] = {
    id: 'sync-only',
    group: 'alpha',
    lane: 'Backlog',
    task: 'Sync-created link',
    board_sync: {
      version: 1,
      provider: 'github',
      enabled: true,
      sync_state: 'idle',
      last_synced_hash: 'old-hash',
      last_push_at: '2026-05-01T00:00:00+00:00',
      github: {
        issue_repo: 'acme/widgets',
        issue_number: 456,
        issue_url: 'https://github.com/acme/widgets/issues/456',
      },
    },
  };
  const context = vm.createContext(sandbox);
  loadBoardSyncScripts(context);

  vm.runInContext('boardLinkExternal("sync-only");', context);
  const payload = lastCall(sandbox);

  assert.deepEqual(sandbox.promptCalls[0], {
    message: 'External reference or URL',
    defaultValue: 'https://github.com/acme/widgets/issues/456',
  });
  assert.equal(payload.cmd, 'external_link_task');
  assert.equal(payload.id, 'sync-only');
  assert.equal(payload.ref, 'https://github.com/acme/widgets/issues/789');
  assert.equal(payload.provider, '');
  assert.equal(payload.external_id, '');
  assert.equal(payload.external_url, '');
  assert.equal(payload.board_sync.version, 1);
  assert.equal(payload.board_sync.provider, 'github');
  assert.equal(payload.board_sync.enabled, true);
  assert.equal(payload.board_sync.sync_state, 'idle');
  assert.equal(Object.prototype.hasOwnProperty.call(payload.board_sync, 'github'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(payload.board_sync, 'last_synced_hash'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(payload.board_sync, 'last_push_at'), false);
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

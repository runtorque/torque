const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(...names) { names.forEach((name) => this.items.add(name)); }
  remove(...names) { names.forEach((name) => this.items.delete(name)); }
  contains(name) { return this.items.has(name); }
  toggle(name, force) {
    const next = force === undefined ? !this.items.has(name) : !!force;
    if (next) this.items.add(name);
    else this.items.delete(name);
    return next;
  }
}

function createHarness(width, panel) {
  const body = { classList: new FakeClassList(), dataset: {} };
  const listeners = {};
  const layout = {
    bottom: { open: panel.zone === 'bottom', active: panel.app },
    right: { open: panel.zone === 'right', active: panel.app },
    last_active: panel.app,
  };
  const sandbox = {
    console, JSON, Math, Date,
    URLSearchParams,
    location: { search: '' },
    window: {
      innerWidth: width,
      addEventListener(type, fn) { listeners[type] = fn; },
    },
    document: {
      body,
      addEventListener(type, fn) { listeners[type] = fn; },
      getElementById() { return null; },
    },
    requestAnimationFrame(fn) { fn(); return 1; },
    setTimeout(fn) { fn(); return 1; },
    _standalonePanelsEnabled() { return true; },
    _standalonePanelCurrentLayout() { return layout; },
    _standalonePanelPlacement() { return panel.zone; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  const filename = path.join(repoRoot, 'static/js/workspace_layout.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
  return { sandbox, context, body, listeners, layout };
}

test('workspace mode thresholds preserve wide layouts and focus medium windows', () => {
  const h = createHarness(1600, { app: 'board', zone: 'bottom' });
  assert.equal(vm.runInContext('_workspaceLayoutModeForWidth(1600)', h.context), 'wide');
  assert.equal(vm.runInContext('_workspaceLayoutModeForWidth(1280)', h.context), 'focused');
  assert.equal(vm.runInContext('_workspaceLayoutModeForWidth(800)', h.context), 'compact');
});

test('workspace_mode query override supports deterministic visual QA', () => {
  const h = createHarness(1600, { app: 'board', zone: 'bottom' });
  h.sandbox.location.search = '?workspace_mode=compact';
  vm.runInContext('_syncWorkspaceLayoutPresentation()', h.context);
  assert.equal(h.body.dataset.workspaceMode, 'compact');
});

test('focused mode presents only the last active open panel as primary canvas', () => {
  const h = createHarness(1280, { app: 'board', zone: 'bottom' });
  vm.runInContext('_syncWorkspaceLayoutPresentation()', h.context);
  assert.equal(h.body.dataset.workspaceMode, 'focused');
  assert.equal(h.body.dataset.workspacePanel, 'board');
  assert.equal(h.body.dataset.workspacePanelZone, 'bottom');
  assert.equal(h.body.classList.contains('workspace-panel-open'), true);
});

test('closed active zone exposes the terminal instead of an empty panel canvas', () => {
  const h = createHarness(1280, { app: 'engineer', zone: 'right' });
  h.layout.right.open = false;
  vm.runInContext('_syncWorkspaceLayoutPresentation()', h.context);
  assert.equal(h.body.classList.contains('workspace-panel-open'), false);
});

test('compact agent drawer is explicit and closes without mutating panel layout', () => {
  const h = createHarness(800, { app: 'chat', zone: 'bottom' });
  vm.runInContext('_syncWorkspaceLayoutPresentation(); workspaceShowAgents()', h.context);
  assert.equal(h.body.classList.contains('workspace-agents-open'), true);
  vm.runInContext('workspaceHideAgents()', h.context);
  assert.equal(h.body.classList.contains('workspace-agents-open'), false);
  assert.equal(h.layout.last_active, 'chat');
});

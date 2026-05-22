const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relativePath) {
  const filename = path.join(repoRoot, relativePath);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function createPanelManagerSandbox({ manifest = null } = {}) {
  const elements = new Map();
  if (manifest !== null) {
    elements.set('torque-ee-frontend-manifest', {
      textContent: typeof manifest === 'string' ? manifest : JSON.stringify(manifest),
      innerText: typeof manifest === 'string' ? manifest : JSON.stringify(manifest),
    });
  }
  const document = {
    body: { classList: { add() {} }, dataset: {} },
    getElementById(id) { return elements.get(id) || null; },
    querySelectorAll() { return []; },
  };
  const sandbox = {
    console,
    document,
    location: { search: '' },
    URLSearchParams,
    JSON,
    Number,
    Math,
    String,
    Array,
    Object,
    window: { innerWidth: 1200, innerHeight: 800, nativeApi: { available() { return false; } } },
    isEmbeddedTerminalMode() { return true; },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return vm.createContext(sandbox);
}

test('webview declares an empty community EE frontend manifest before panel_manager', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.match(html, /id="torque-ee-frontend-manifest"[^>]*type="application\/json">\{"version":1,"panels":\[\]\}<\/script>/);
  assert.match(html, /torque-ee-frontend-manifest[\s\S]*static\/js\/panel_manager\.js/);
  assert.doesNotMatch(html, /ee\/frontend/);
});

test('community manifest registers no remote panels', () => {
  const context = createPanelManagerSandbox({ manifest: { version: 1, panels: [] } });
  loadScript(context, 'static/js/panel_manager.js');

  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('remote')", context), -1);
  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('board') >= 0", context), true);
});

test('EE manifest can register a panel without changing core panel order', () => {
  const context = createPanelManagerSandbox({
    manifest: {
      version: 1,
      panels: [{ id: 'remote', title: 'Remote', root_id: 'panel-remote', default_zone: 'right' }],
    },
  });
  loadScript(context, 'static/js/panel_manager.js');

  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('board')", context), 0);
  assert.equal(vm.runInContext("_standalonePanelApps.includes('remote')", context), true);
  assert.equal(vm.runInContext("_standalonePanelTitles.remote", context), 'Remote');
  assert.equal(vm.runInContext("_standalonePanelDefaults.remote", context), 'right');
  assert.equal(vm.runInContext("_standalonePanelRootId('remote')", context), 'panel-remote');
});

test('invalid EE manifest entries fail closed', () => {
  const context = createPanelManagerSandbox({
    manifest: {
      version: 1,
      panels: [
        { id: '../remote', title: 'Remote' },
        { id: 'Board', title: 'Bad casing' },
        { id: 'board', title: 'Duplicate' },
      ],
    },
  });
  loadScript(context, 'static/js/panel_manager.js');

  assert.equal(vm.runInContext("_standalonePanelApps.filter((app) => app === 'board').length", context), 1);
  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('../remote')", context), -1);
  assert.equal(vm.runInContext("_standalonePanelApps.indexOf('Board')", context), -1);
});

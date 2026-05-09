const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  contains(name) { return this.items.has(name); }
  toggle(name, force) {
    if (force === true) { this.items.add(name); return true; }
    if (force === false) { this.items.delete(name); return false; }
    if (this.items.has(name)) { this.items.delete(name); return false; }
    this.items.add(name); return true;
  }
}

function loadScript(context, relPath) {
  const source = fs.readFileSync(path.join(repoRoot, relPath), 'utf8');
  vm.runInContext(source, context, { filename: relPath });
}

test('panel manager detects detached Tauri window URL mode', () => {
  const body = { classList: new FakeClassList(), dataset: {} };
  const context = vm.createContext({
    console,
    URLSearchParams,
    location: { search: '?panel=engineer&window=panel-engineer-abc' },
    document: {
      body,
      addEventListener() {},
      removeEventListener() {},
      getElementById() { return null; },
      createElement(tag) { return { tagName: tag, classList: new FakeClassList(), style: {}, dataset: {}, appendChild() {} }; },
      createElementNS(_ns, tag) { return { tagName: tag, setAttribute() {}, appendChild() {} }; },
    },
    window: { addEventListener() {}, innerWidth: 1200, innerHeight: 800 },
    state: { detached_panels: {} },
    Number,
    JSON,
    setTimeout,
    clearTimeout,
  });

  loadScript(context, 'static/js/panel_manager.js');

  assert.equal(context._detachedWindowActive(), true);
  assert.equal(context._detachedWindowInfo.panel, 'engineer');
  assert.equal(body.classList.contains('detached-window'), true);
  assert.equal(body.dataset.detachedPanel, 'engineer');
});

test('Tauri mode hides redundant main bar wordmark without hiding browser standalone branding', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(
    html,
    /<header>[\s\S]*<h1[^>]*class="[^"]*\bapp-wordmark\b[^"]*"[^>]*>TORQUE<\/h1>[\s\S]*<\/header>/,
  );
  assert.match(
    css,
    /body\.tauri-mode\s+header\s+\.app-wordmark\s*\{[^}]*display:\s*none\s*;/s,
  );
  assert.doesNotMatch(css, /body\.standalone-mode\s+header\s+\.app-wordmark/);
});

test('cheatsheet renders platform-aware shortcut labels', () => {
  const context = vm.createContext({
    console,
    navigator: { platform: 'MacIntel' },
    document: { getElementById() { return null; }, createElement() { return {}; }, body: { appendChild() {} } },
    window: {},
    fetch() { throw new Error('not used'); },
    esc(value) { return String(value); },
    setTimeout,
  });

  loadScript(context, 'static/cheatsheet/cheatsheet.js');

  assert.equal(context._displayShortcut('CmdOrCtrl+Alt+D'), '⌘⌥D');
  context.navigator.platform = 'Linux x86_64';
  assert.equal(context._displayShortcut('CmdOrCtrl+Shift+/'), 'Ctrl+Shift+/');
});

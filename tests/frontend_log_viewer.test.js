const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

class FakeClassList {
  constructor(initial = []) { this._set = new Set(initial); }
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
  constructor(id = '', ownerDocument = null) {
    this.id = id;
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.classList = new FakeClassList();
    this.attributes = {};
    this.listeners = {};
    this._innerHTML = '';
    this.textContent = '';
    this.scrollTop = 0;
    this.scrollHeight = 999;
    this.value = '';
    this.checked = false;
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (!this.ownerDocument) return;
    const idRegex = /id="([^"]+)"/g;
    let match;
    while ((match = idRegex.exec(this._innerHTML))) {
      this.ownerDocument.ensure(match[1]);
    }
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (child.id) this.ownerDocument.elements.set(child.id, child);
    return child;
  }

  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.body = this.ensure('body');
  }
  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }
  getElementById(id) { return this.elements.get(id) || null; }
  createElement(tagName) {
    const el = new FakeElement('', this);
    el.tagName = String(tagName || '').toUpperCase();
    return el;
  }
}

function loadLogViewer(context) {
  const filename = path.join(repoRoot, 'static/js/log_viewer.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function createSandbox() {
  const document = new FakeDocument();
  const fetchUrls = [];
  const timers = [];
  const sandbox = {
    console,
    document,
    Date,
    nativeApi: { revealLogDir() {} },
    esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    },
    fetch(url) {
      fetchUrls.push(String(url));
      const parsed = new URL(String(url), 'http://localhost');
      const target = parsed.searchParams.get('target') || 'daemon';
      const cursor = target === 'supervisor' ? 44 : 22;
      return Promise.resolve({
        json() {
          return Promise.resolve({
            target,
            cursor,
            lines: [{ ts: 1779180000, level: target === 'supervisor' ? 'ERROR' : 'INFO', message: `${target} line` }],
          });
        },
      });
    },
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout(id) { timers.push({ cleared: id }); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, document, fetchUrls, timers };
}

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('log viewer fetches daemon by default and switches target with cursor reset while preserving filters', async () => {
  const { sandbox, document, fetchUrls } = createSandbox();
  const context = vm.createContext(sandbox);
  loadLogViewer(context);

  vm.runInContext('_logViewerState.follow = false; openLogViewer();', context);
  await flushPromises();
  await flushPromises();

  assert.equal(new URL(fetchUrls[0], 'http://localhost').searchParams.get('target'), 'daemon');
  assert.equal(vm.runInContext('_logViewerState.cursor', context), 22);
  assert.equal(document.getElementById('log-viewer-target-daemon').classList.contains('active'), true);

  vm.runInContext("_logViewerSetLevel('ERROR'); _logViewerSetSearch('panic');", context);
  vm.runInContext("_logViewerSetTarget('supervisor');", context);
  await flushPromises();
  await flushPromises();

  const supervisorUrl = new URL(fetchUrls[fetchUrls.length - 1], 'http://localhost');
  assert.equal(supervisorUrl.searchParams.get('target'), 'supervisor');
  assert.equal(supervisorUrl.searchParams.get('since'), '0');
  assert.equal(supervisorUrl.searchParams.get('follow'), '0');
  assert.equal(vm.runInContext('_logViewerState.level', context), 'ERROR');
  assert.equal(vm.runInContext('_logViewerState.search', context), 'panic');
  assert.equal(vm.runInContext('_logViewerState.cursor', context), 44);
  assert.equal(vm.runInContext('_logViewerState.lines.length', context), 1);
  assert.equal(document.getElementById('log-viewer-target-daemon').classList.contains('active'), false);
  assert.equal(document.getElementById('log-viewer-target-supervisor').classList.contains('active'), true);
});

test('log viewer preserves scroll position during filtered re-render when follow is off', () => {
  const { sandbox, document } = createSandbox();
  const context = vm.createContext(sandbox);
  loadLogViewer(context);

  vm.runInContext('_ensureLogViewerModal(); _logViewerState.follow = false; _logViewerState.lines = [{ ts: 1, level: "INFO", message: "alpha" }];', context);
  const list = document.getElementById('log-viewer-list');
  list.scrollTop = 57;
  vm.runInContext('_renderLogViewerLines();', context);

  assert.equal(list.scrollTop, 57);
  assert.match(list.innerHTML, /alpha/);
});

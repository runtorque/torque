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

function matchesSelector(el, selector) {
  const raw = String(selector || '').trim();
  if (!raw) return false;
  if (raw === '.modal') return el.classList.contains('modal');
  if (raw === '[autofocus]') return !!el.autofocus;
  if (raw === 'h1' || raw === 'h2' || raw === 'h3') return el.tagName === raw.toUpperCase();
  if (raw === '[data-modal-title]') return !!el.dataset.modalTitle;
  if (raw.startsWith('#')) return el.id === raw.slice(1);
  if (raw === 'button') return el.tagName === 'BUTTON';
  if (raw === 'input') return el.tagName === 'INPUT';
  if (raw === 'select') return el.tagName === 'SELECT';
  if (raw === 'textarea') return el.tagName === 'TEXTAREA';
  if (raw === 'a[href]') return el.tagName === 'A' && !!el.attributes.href;
  if (raw === '[contenteditable="true"]') return el.attributes.contenteditable === 'true';
  if (raw.startsWith('[tabindex]')) return el.attributes.tabindex !== undefined && el.attributes.tabindex !== '-1';
  return false;
}

class FakeElement {
  constructor(tag = 'div', id = '', ownerDocument = null) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id;
    this.ownerDocument = ownerDocument;
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.autofocus = false;
    this.isContentEditable = false;
    this.textContent = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.classList = new FakeClassList();
    this.eventListeners = {};
    this.focusCount = 0;
    this.selectCount = 0;
    this._innerHTML = '';
  }
  appendChild(child) {
    child.parentNode = this;
    if (!child.ownerDocument) child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    return child;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this._innerHTML === '') this.children = [];
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] || ''; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) {
    this.eventListeners[type] = this.eventListeners[type] || [];
    this.eventListeners[type].push(handler);
  }
  removeEventListener(type, handler) {
    this.eventListeners[type] = (this.eventListeners[type] || []).filter((fn) => fn !== handler);
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    const path = [];
    for (let node = this; node; node = node.parentNode) path.push(node);
    for (let i = path.length - 1; i >= 0; i--) {
      for (const handler of (path[i].eventListeners[event.type] || [])) handler(event);
    }
    return !event.defaultPrevented;
  }
  focus() {
    this.focusCount += 1;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
  select() { this.selectCount += 1; }
  querySelector(selector) {
    const selectors = String(selector || '').split(',').map((s) => s.trim()).filter(Boolean);
    const nodes = this._descendants();
    return nodes.find((node) => selectors.some((sel) => matchesSelector(node, sel))) || null;
  }
  querySelectorAll(selector) {
    const selectors = String(selector || '').split(',').map((s) => s.trim()).filter(Boolean);
    return this._descendants().filter((node) => selectors.some((sel) => matchesSelector(node, sel)));
  }
  _descendants() {
    const out = [];
    const visit = (node) => {
      for (const child of node.children) {
        out.push(child);
        visit(child);
      }
    };
    visit(this);
    return out;
  }
}

function keyEvent(key, extra = {}) {
  return Object.assign({
    type: 'keydown',
    key,
    shiftKey: false,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
  }, extra);
}

function createSandbox() {
  const elements = new Map();
  const document = {
    activeElement: null,
    body: null,
    getElementById(id) { return elements.get(id) || null; },
    createElement(tag) { return new FakeElement(tag, '', document); },
    querySelectorAll(selector) {
      if (selector === '.overlay') {
        return Array.from(elements.values()).filter((el) => el.classList.contains('overlay'));
      }
      if (selector === '.hint-pop') return [];
      return [];
    },
    querySelector() { return null; },
  };
  document.body = new FakeElement('body', 'body', document);
  function make(tag, id, classes = []) {
    const el = new FakeElement(tag, id, document);
    classes.forEach((name) => el.classList.add(name));
    elements.set(id, el);
    return el;
  }
  function append(parent, child) { parent.appendChild(child); return child; }

  const confirmOverlay = make('div', 'modal-confirm', ['overlay']);
  const confirmPanel = append(confirmOverlay, new FakeElement('div', '', document));
  confirmPanel.classList.add('modal');
  const confirmMessage = append(confirmPanel, make('p', 'confirm-message'));
  const confirmExtras = append(confirmPanel, make('div', 'confirm-extras'));
  const confirmCancel = append(confirmPanel, make('button', 'confirm-cancel-btn'));
  const confirmYes = append(confirmPanel, make('button', 'confirm-yes-btn'));

  const inputOverlay = make('div', 'modal-input-dialog', ['overlay']);
  const inputPanel = append(inputOverlay, new FakeElement('div', '', document));
  inputPanel.classList.add('modal');
  const inputTitle = append(inputPanel, make('h2', 'input-dialog-title'));
  const inputSummary = append(inputPanel, make('div', 'input-dialog-summary', ['hidden']));
  const inputFields = append(inputPanel, make('div', 'input-dialog-fields'));
  const inputError = append(inputPanel, make('div', 'input-dialog-error', ['hidden']));
  const inputCancel = append(inputPanel, make('button', 'input-dialog-cancel-btn'));
  const inputSubmit = append(inputPanel, make('button', 'input-dialog-submit-btn'));

  const trigger = make('button', 'trigger');
  document.activeElement = trigger;

  const sandbox = {
    console,
    document,
    window: { innerWidth: 1200 },
    state: { groups: {}, agents: {} },
    _cachedProviders: [],
    _cachedAgentTemplates: [],
    send() {},
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return {
    sandbox,
    elements,
    trigger,
    confirmOverlay,
    confirmPanel,
    confirmMessage,
    confirmExtras,
    confirmYes,
    inputOverlay,
    inputPanel,
    inputFields,
  };
}

function loadModals(context) {
  const filename = path.join(repoRoot, 'static/js/modals/core.js');
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

test('showInputDialog applies dialog ARIA, focuses/selects input, Enter submits, and restores focus', async () => {
  const env = createSandbox();
  const context = vm.createContext(env.sandbox);
  loadModals(context);

  const promise = vm.runInContext(`showInputDialog({
    title: 'Rename item',
    summary: 'Pick a concise name.',
    fields: [{ key: 'name', label: 'Name', defaultValue: 'Draft', required: true }],
    submitLabel: 'Rename'
  })`, context);

  assert.equal(env.inputOverlay.classList.contains('visible'), true);
  assert.equal(env.inputPanel.getAttribute('role'), 'dialog');
  assert.equal(env.inputPanel.getAttribute('aria-modal'), 'true');
  assert.equal(env.inputPanel.getAttribute('aria-labelledby'), 'input-dialog-title');
  assert.equal(env.inputPanel.getAttribute('aria-describedby'), 'input-dialog-summary');

  const input = env.inputFields.children.find((child) => child.tagName === 'INPUT');
  assert.ok(input, 'dialog should render an input field');
  assert.equal(env.sandbox.document.activeElement, input);
  assert.equal(input.selectCount, 1);

  input.value = 'Renamed';
  input.dispatchEvent(keyEvent('Enter'));
  assert.deepEqual(JSON.parse(JSON.stringify(await promise)), { name: 'Renamed' });
  assert.equal(env.inputOverlay.classList.contains('visible'), false);
  assert.equal(env.sandbox.document.activeElement, env.trigger);
});

test('showInputDialog Escape cancels without values and restores focus', async () => {
  const env = createSandbox();
  const context = vm.createContext(env.sandbox);
  loadModals(context);

  const promise = vm.runInContext(`showInputDialog({
    title: 'Create group',
    fields: [{ key: 'group', label: 'Group', defaultValue: 'alpha' }]
  })`, context);
  const input = env.inputFields.children.find((child) => child.tagName === 'INPUT');
  input.value = 'mutated locally';
  input.dispatchEvent(keyEvent('Escape'));

  assert.equal(await promise, null);
  assert.equal(env.inputOverlay.classList.contains('visible'), false);
  assert.equal(env.sandbox.document.activeElement, env.trigger);
});

test('showConfirm applies alertdialog ARIA, Escape rejects, and restores focus', async () => {
  const env = createSandbox();
  const context = vm.createContext(env.sandbox);
  loadModals(context);

  const promise = vm.runInContext(`showConfirm('Delete this task?', { label: 'Delete' })`, context);

  assert.equal(env.confirmOverlay.classList.contains('visible'), true);
  assert.equal(env.confirmPanel.getAttribute('role'), 'alertdialog');
  assert.equal(env.confirmPanel.getAttribute('aria-modal'), 'true');
  assert.equal(env.confirmPanel.getAttribute('aria-label'), 'Confirm action');
  assert.equal(env.confirmPanel.getAttribute('aria-describedby'), 'confirm-message');
  assert.equal(env.sandbox.document.activeElement, env.confirmYes);

  env.confirmYes.dispatchEvent(keyEvent('Escape'));
  assert.equal(await promise, false);
  assert.equal(env.confirmOverlay.classList.contains('visible'), false);
  assert.equal(env.sandbox.document.activeElement, env.trigger);
});

test('openModalDialog traps Tab within the active dialog', () => {
  const env = createSandbox();
  const context = vm.createContext(env.sandbox);
  loadModals(context);

  vm.runInContext(`openModalDialog('modal-confirm', {
    role: 'dialog',
    label: 'Trap test',
    initialFocus: '#confirm-yes-btn'
  })`, context);
  assert.equal(env.sandbox.document.activeElement, env.confirmYes);

  env.confirmYes.dispatchEvent(keyEvent('Tab'));
  assert.equal(env.sandbox.document.activeElement.id, 'confirm-cancel-btn');

  env.sandbox.document.activeElement.dispatchEvent(keyEvent('Tab', { shiftKey: true }));
  assert.equal(env.sandbox.document.activeElement, env.confirmYes);
});

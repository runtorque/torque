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
    if (force === undefined) {
      if (this._set.has(name)) {
        this._set.delete(name);
        return false;
      }
      this._set.add(name);
      return true;
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
    this._innerHTML = '';
    this.textContent = '';
    this.title = '';
    this.type = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.focused = false;
    this.selected = false;
    this.className = '';
    this.classList = new FakeClassList();
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set innerHTML(value) {
    this._innerHTML = value;
    if (value === '') this.children = [];
  }
  focus() {
    this.focused = true;
  }
  select() {
    this.selected = true;
  }
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
  closest() {
    return this;
  }
  remove() {}
}

function createSandbox() {
  const elements = new Map();
  function ensure(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  }

  const sandbox = {
    console,
    state: {
      agents: {
        'engineer-1': {
          id: 'engineer-1',
          name: 'Forge',
          group: 'alpha',
          cell_type: 'agent',
          kind: 'engineer',
          icon: '🤖',
          engineer_specializations: ['ui-ux', 'unknown', 'runtime-pty', 'ui-ux'],
        },
        'worker-1': {
          id: 'worker-1',
          name: 'Worker',
          group: 'alpha',
          cell_type: 'agent',
          kind: 'worker',
          icon: '🛠️',
          engineer_specializations: ['ui-ux'],
        },
      },
      specializations: [
        { name: 'ui-ux', preamble: 'UX.', priorities: [] },
        { name: 'runtime-pty', preamble: 'PTY.', priorities: [] },
        { name: 'prompts-config', preamble: 'Prompts.', priorities: [] },
      ],
    },
    sendCalls: [],
    document: {
      body: new FakeElement('body'),
      activeElement: null,
      getElementById(id) {
        return ensure(id);
      },
      createElement(tag) {
        return new FakeElement(tag);
      },
      querySelectorAll(selector) {
        if (selector === '.overlay') return [ensure('modal-edit')];
        if (selector === '.hint-pop') return [];
        return [];
      },
      querySelector() {
        return null;
      },
    },
    window: { innerWidth: 1200 },
  };
  sandbox.send = function(message) {
    sandbox.sendCalls.push(message);
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, ensure };
}

function loadModals(context) {
  const filename = path.join(repoRoot, 'static/js/modals.js');
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

function labelsFor(listEl) {
  return listEl.children.map((li) => li.children[0].textContent);
}

function optionsFor(selectEl) {
  return selectEl.children.map((opt) => opt.value);
}

test('edit popup shows ordered specialization picker for engineers and saves one update_agent payload', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext("openEditCell('engineer-1')", context);

  assert.equal(ensure('edit-title').textContent, 'Edit Engineer');
  assert.equal(ensure('edit-specializations-row').classList.contains('hidden'), false);
  assert.deepEqual(
    labelsFor(ensure('edit-specializations-selected')),
    ['ui-ux (primary)', 'runtime-pty'],
    'initial engineer specializations should be filtered and deduped',
  );
  assert.deepEqual(
    optionsFor(ensure('edit-specializations-available')),
    ['', 'prompts-config'],
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(sandbox.sendCalls.find((msg) => msg.cmd === 'list_specializations'))),
    { cmd: 'list_specializations', group: 'alpha' },
  );

  ensure('edit-specializations-available').value = 'prompts-config';
  vm.runInContext('editEngineerAddSpecialization()', context);
  vm.runInContext('editEngineerMoveSpecialization(2, -1)', context);
  vm.runInContext('editEngineerMoveSpecialization(1, -1)', context);
  ensure('edit-name-input').value = 'Forge Prime';
  vm.runInContext('submitEdit()', context);

  const updateCall = sandbox.sendCalls.find((msg) => msg.cmd === 'update_agent');
  assert.ok(updateCall, 'update_agent should be sent');
  assert.deepEqual(JSON.parse(JSON.stringify(updateCall)), {
    cmd: 'update_agent',
    id: 'engineer-1',
    name: 'Forge Prime',
    engineer_specializations: ['prompts-config', 'ui-ux', 'runtime-pty'],
  });
  assert.equal(Object.hasOwn(updateCall, 'icon'), false, 'edit save must not send icon');
});

test('edit popup hides specialization picker for non-engineer agents', () => {
  const { sandbox, ensure } = createSandbox();
  const context = vm.createContext(sandbox);
  loadModals(context);

  vm.runInContext("openEditCell('worker-1')", context);

  assert.equal(ensure('edit-title').textContent, 'Edit Agent');
  assert.equal(ensure('edit-specializations-row').classList.contains('hidden'), true);
  ensure('edit-name-input').value = 'Worker Prime';
  vm.runInContext('submitEdit()', context);

  const updateCall = sandbox.sendCalls.find((msg) => msg.cmd === 'update_agent');
  assert.deepEqual(JSON.parse(JSON.stringify(updateCall)), {
    cmd: 'update_agent',
    id: 'worker-1',
    name: 'Worker Prime',
  });
  assert.equal(Object.hasOwn(updateCall, 'engineer_specializations'), false);
  assert.equal(Object.hasOwn(updateCall, 'icon'), false);
});

test('edit popup markup removes retired icon picker', () => {
  const html = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  assert.doesNotMatch(html, /id="edit-icon-row"/);
  assert.doesNotMatch(html, /id="edit-icon-picker"/);
  assert.match(html, /id="edit-specializations-row"/);

  const modals = fs.readFileSync(path.join(repoRoot, 'static/js/modals.js'), 'utf8');
  assert.doesNotMatch(modals, /selectEditIcon/);
  assert.doesNotMatch(modals, /_editIcon/);
  assert.doesNotMatch(modals, /edit-icon-picker/);
});

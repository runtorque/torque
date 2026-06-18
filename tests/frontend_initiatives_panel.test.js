const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function decodeEntities(value) {
  return String(value || '')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

class FakeElement {
  constructor(id, doc) {
    this.id = id || '';
    this.ownerDocument = doc;
    this._innerHTML = '';
    this.value = '';
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this.id === 'panel-initiatives') this.ownerDocument.rebuildFromPanelHtml(this._innerHTML, this);
  }

  contains(node) {
    return !!node && node.ownerDocument === this.ownerDocument;
  }

  querySelector(selector) {
    if (!selector) return null;
    if (selector.startsWith('#')) return this.ownerDocument.getElementById(selector.slice(1));
    const dataField = selector.match(/^\[data-field="([^"]+)"\]$/);
    if (dataField) return this.ownerDocument.findByDataField(dataField[1]);
    return null;
  }

  querySelectorAll() { return []; }

  focus() {
    this.ownerDocument.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.body = new FakeElement('body', this);
    this.panel = this.ensure('panel-initiatives');
  }

  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  findByDataField(field) {
    for (const el of this.elements.values()) {
      if (el.dataset && el.dataset.field === field) return el;
    }
    return null;
  }

  querySelector() { return null; }

  querySelectorAll() { return []; }

  rebuildFromPanelHtml(html, panel) {
    const previousPanel = panel;
    this.elements = new Map([['panel-initiatives', previousPanel]]);
    const tagRe = /<(input|textarea|select|div|aside|section|button)\b([^>]*)>/g;
    let match;
    while ((match = tagRe.exec(html))) {
      const attrs = match[2] || '';
      const idMatch = attrs.match(/\bid="([^"]+)"/);
      if (!idMatch) continue;
      const el = this.ensure(decodeEntities(idMatch[1]));
      const dataField = attrs.match(/\bdata-field="([^"]+)"/);
      if (dataField) el.dataset.field = decodeEntities(dataField[1]);
      const value = attrs.match(/\bvalue="([^"]*)"/);
      if (value) el.value = decodeEntities(value[1]);
    }
    const textareaRe = /<textarea\b([^>]*)>([\s\S]*?)<\/textarea>/g;
    while ((match = textareaRe.exec(html))) {
      const idMatch = (match[1] || '').match(/\bid="([^"]+)"/);
      if (idMatch) this.ensure(decodeEntities(idMatch[1])).value = decodeEntities(match[2] || '');
    }
    const selectRe = /<select\b([^>]*)>([\s\S]*?)<\/select>/g;
    while ((match = selectRe.exec(html))) {
      const idMatch = (match[1] || '').match(/\bid="([^"]+)"/);
      if (!idMatch) continue;
      const selected = (match[2] || '').match(/<option\b[^>]*value="([^"]*)"[^>]*\sselected\b/);
      if (selected) this.ensure(decodeEntities(idMatch[1])).value = decodeEntities(selected[1]);
    }
  }
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function createSandbox() {
  const document = new FakeDocument();
  const sendCalls = [];
  const sandbox = {
    console,
    document,
    state: {
      active_group: 'Torque',
      initiatives: {},
      board_tasks: {},
      decisions: {},
    },
    sendCalls,
    _activePanelApp: 'initiatives',
    _currentGroup() { return sandbox.state.active_group; },
    _panelAppVisible(app) { return app === 'initiatives'; },
    send(message) { sendCalls.push(message); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  loadScript(sandbox, 'static/js/initiatives.js');
  return { sandbox, document, sendCalls };
}

function sampleInitiative(overrides = {}) {
  return Object.assign({
    id: 'TORQUE-I:1',
    group: 'Torque',
    group_name: 'Torque',
    title: 'Ship initiatives UI',
    planning_status: 'now',
    priority: 'P1',
    summary: 'Build the roadmap panel.',
    why: 'Operators need a compact planning view.',
    in_scope: 'Roadmap columns',
    out_of_scope: 'Gantt charts',
    done_definition: 'Reviewed and smoke tested',
    linked_tasks: { count: 2, hidden_count: 0, by_lane: { 'In Progress': 1, Done: 1 }, items: [] },
    linked_decisions: { count: 0, hidden_count: 0, items: [] },
  }, overrides);
}


test('Planning CSS supports bottom full-width layout and side-panel responsive treatment', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /body\.runtime-embedded \.standalone-panel-zone-body > #panel-initiatives,[\s\S]*body\.runtime-embedded \.standalone-float-body > #panel-initiatives[\s\S]*\{[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*min-height:\s*0;[^}]*min-width:\s*0;/s);
  assert.match(css, /#panel-initiatives\s*\{[^}]*display:\s*flex;[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*container-type:\s*inline-size;/s);
  assert.match(css, /\.initiatives-workspace\s*\{[^}]*width:\s*100%;[^}]*box-sizing:\s*border-box;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(320px,\s*clamp\(360px,\s*28vw,\s*520px\)\);/s);
  assert.match(css, /#panel-initiatives\[data-panel-placement="right"\] \.initiatives-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*overflow:\s*auto;/s);
  assert.match(css, /#panel-initiatives\[data-panel-placement="right"\] \.initiative-primary-columns\s*\{[^}]*grid-template-columns:\s*1fr;[^}]*min-height:\s*0;/s);
  assert.match(css, /@container \(max-width:\s*640px\)\s*\{[\s\S]*?\.initiatives-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[\s\S]*?\.initiative-primary-columns\s*\{[^}]*grid-template-columns:\s*1fr;/s);
});

test('Planning panel groups initiatives into primary roadmap columns and secondary buckets', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [
      sampleInitiative({ id: 'I-now', title: 'Now item', planning_status: 'now' }),
      sampleInitiative({ id: 'I-next', title: 'Next item', planning_status: 'next' }),
      sampleInitiative({ id: 'I-later', title: 'Later item', planning_status: 'later' }),
      sampleInitiative({ id: 'I-parked', title: 'Parked item', planning_status: 'parked' }),
    ],
  })})`, sandbox);

  let html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Now item/);
  assert.match(html, /Next item/);
  assert.match(html, /Later item/);
  assert.match(html, /Secondary buckets/);
  assert.match(html, /parked <span>1<\/span>/);
  assert.doesNotMatch(html, /Parked item/);

  vm.runInContext("initiativesToggleSecondary('parked')", sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Parked item/);
  // The received list marks the group loaded; expanding secondary buckets is local state only.
  assert.equal(sendCalls.length, 0);
});

test('initiative detail rerender preserves selected drawer, draft text, caret, focus, and column scroll', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })})`, sandbox);
  vm.runInContext("initiativesSelect('TORQUE-I:1')", sandbox);

  const summary = document.getElementById('initiative-field-summary');
  summary.value = 'Draft summary while websocket deltas arrive';
  summary.selectionStart = 12;
  summary.selectionEnd = 19;
  summary.focus();
  const nowColumn = document.getElementById('initiative-col-now');
  nowColumn.scrollTop = 47;

  vm.runInContext(`state.initiatives['TORQUE-I:1'].priority = 'P0'; renderInitiativesPanel();`, sandbox);

  const restoredSummary = document.getElementById('initiative-field-summary');
  const restoredColumn = document.getElementById('initiative-col-now');
  assert.equal(restoredSummary.value, 'Draft summary while websocket deltas arrive');
  assert.equal(restoredSummary.selectionStart, 12);
  assert.equal(restoredSummary.selectionEnd, 19);
  assert.equal(document.activeElement, restoredSummary);
  assert.equal(restoredColumn.scrollTop, 47);
  assert.match(document.getElementById('panel-initiatives').innerHTML, /TORQUE-I:1/);
});


test('initiative rerender preserves drafts and scroll after side placement change', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative(), sampleInitiative({ id: 'I-parked', title: 'Parked', planning_status: 'parked' })],
  })})`, sandbox);
  vm.runInContext("initiativesToggleSecondary('parked'); initiativesSelect('TORQUE-I:1')", sandbox);

  const panel = document.getElementById('panel-initiatives');
  panel.dataset.panelPlacement = 'right';
  const why = document.getElementById('initiative-field-why');
  why.value = 'Narrow side-panel draft';
  why.selectionStart = 7;
  why.selectionEnd = 11;
  why.focus();
  const workspace = document.getElementById('initiatives-workspace');
  workspace.scrollLeft = 12;
  workspace.scrollTop = 144;
  const roadmap = document.getElementById('initiatives-roadmap-scroll');
  roadmap.scrollLeft = 36;
  roadmap.scrollTop = 58;

  vm.runInContext(`state.initiatives['TORQUE-I:1'].summary = 'Delta while side panel is open'; renderInitiativesPanel();`, sandbox);

  const restoredWhy = document.getElementById('initiative-field-why');
  const restoredWorkspace = document.getElementById('initiatives-workspace');
  const restoredRoadmap = document.getElementById('initiatives-roadmap-scroll');
  assert.equal(restoredWhy.value, 'Narrow side-panel draft');
  assert.equal(restoredWhy.selectionStart, 7);
  assert.equal(restoredWhy.selectionEnd, 11);
  assert.equal(document.activeElement, restoredWhy);
  assert.equal(restoredWorkspace.scrollLeft, 12);
  assert.equal(restoredWorkspace.scrollTop, 144);
  assert.equal(restoredRoadmap.scrollLeft, 36);
  assert.equal(restoredRoadmap.scrollTop, 58);
  assert.match(document.getElementById('panel-initiatives').innerHTML, /Parked/);
});

test('Planning surface participates in classic active-panel invalidation renders', () => {
  const { sandbox } = createSandbox();
  let renders = 0;
  sandbox.renderInitiativesPanel = function() { renders += 1; };
  sandbox._activePanelApp = 'initiatives';

  const surfaces = vm.runInContext('_currentPanelSurfaces()', sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(surfaces)), ['initiatives']);
  vm.runInContext('renderInvalidatedSurfaces({ initiatives: true })', sandbox);

  assert.equal(renders, 1);
});

test('Planning surface participates in standalone visible-surface invalidation renders', () => {
  const { sandbox } = createSandbox();
  let renders = 0;
  sandbox.renderInitiativesPanel = function() { renders += 1; };
  sandbox._standalonePanelsEnabled = function() { return true; };
  sandbox._visiblePanelSurfaces = function() { return ['board', 'initiatives', 'initiatives']; };
  sandbox.renderBoard = function() {};

  const surfaces = vm.runInContext('_currentPanelSurfaces()', sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(surfaces)), ['board', 'initiatives']);
  vm.runInContext('renderInvalidatedSurfaces({ initiatives: true })', sandbox);

  assert.equal(renders, 1);
});

test('saving scope and linking artifacts use Wave 1 initiative commands', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })})`, sandbox);
  vm.runInContext("initiativesSelect('TORQUE-I:1')", sandbox);
  document.getElementById('initiative-field-title').value = 'Updated title';
  document.getElementById('initiative-field-planning-status').value = 'next';
  vm.runInContext('initiativesSaveDetail()', sandbox);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'initiative_update',
    initiative: 'TORQUE-I:1',
    group: 'Torque',
    title: 'Updated title',
    planning_status: 'next',
    priority: 'P1',
    summary: 'Build the roadmap panel.',
    why: 'Operators need a compact planning view.',
    in_scope: 'Roadmap columns',
    out_of_scope: 'Gantt charts',
    done_definition: 'Reviewed and smoke tested',
  });

  document.getElementById('initiative-link-task-input').value = 'TORQUE:123';
  vm.runInContext('initiativesLinkTask()', sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'initiative_link_task');
  assert.equal(sendCalls.at(-1).task, 'TORQUE:123');

  document.getElementById('initiative-link-decision-input').value = 'decision-abc';
  vm.runInContext('initiativesLinkDecision()', sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'initiative_link_decision');
  assert.equal(sendCalls.at(-1).decision, 'decision-abc');
});

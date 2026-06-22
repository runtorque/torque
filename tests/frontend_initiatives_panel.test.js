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
    const dataAreaField = selector.match(/^\[data-area-field="([^"]+)"\]$/);
    if (dataAreaField) return this.ownerDocument.findByDataAreaField(dataAreaField[1]);
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

  findByDataAreaField(field) {
    for (const el of this.elements.values()) {
      if (el.dataset && el.dataset.areaField === field) return el;
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
      const dataAreaField = attrs.match(/\bdata-area-field="([^"]+)"/);
      if (dataAreaField) el.dataset.areaField = decodeEntities(dataAreaField[1]);
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
  assert.match(css, /\.areas-filters\s*\{[^}]*display:\s*flex;[^}]*flex-wrap:\s*wrap;[^}]*max-width:\s*100%;[^}]*box-sizing:\s*border-box;/s);
  assert.match(css, /\.areas-filters input,\s*\.areas-filters select\s*\{[^}]*width:\s*auto;[^}]*max-width:\s*100%;/s);
  assert.match(css, /\.areas-search-input\s*\{[^}]*flex:\s*1\s+1\s+260px;[^}]*min-width:\s*min\(100%,\s*220px\);/s);
  assert.match(css, /\.areas-filters select\s*\{[^}]*flex:\s*0\s+1\s+180px;[^}]*min-width:\s*min\(100%,\s*130px\);/s);
  assert.match(css, /#panel-initiatives\[data-panel-placement="right"\] \.areas-filters select\s*\{[^}]*flex:\s*1\s+1\s+130px;/s);
  assert.match(css, /#panel-initiatives\[data-panel-placement="right"\] \.areas-search-input\s*\{[^}]*flex-basis:\s*100%;/s);
  assert.match(css, /@container \(max-width:\s*640px\)\s*\{[\s\S]*?\.areas-filters select\s*\{[^}]*flex:\s*1\s+1\s+130px;[\s\S]*?\.areas-search-input\s*\{[^}]*flex-basis:\s*100%;/s);
});

test('Planning Areas toolbar CSS keeps search and filters contained by wrapping inside list pane', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');
  const filtersBlock = css.match(/\.areas-filters\s*\{([^}]*)\}/s);
  assert.ok(filtersBlock, 'areas filters CSS block exists');
  assert.match(filtersBlock[1], /display:\s*flex;/);
  assert.match(filtersBlock[1], /flex-wrap:\s*wrap;/);
  assert.match(filtersBlock[1], /min-width:\s*0;/);
  assert.match(filtersBlock[1], /max-width:\s*100%;/);
  assert.doesNotMatch(filtersBlock[1], /grid-template-columns/);

  const inputSelectBlock = css.match(/\.areas-filters input,\s*\.areas-filters select\s*\{([^}]*)\}/s);
  assert.ok(inputSelectBlock, 'areas filter input/select sizing block exists');
  assert.match(inputSelectBlock[1], /width:\s*auto;/);
  assert.match(inputSelectBlock[1], /max-width:\s*100%;/);
  assert.doesNotMatch(inputSelectBlock[1], /(^|\n)\s*width:\s*100%;/);

  assert.match(css, /\.areas-search-input\s*\{[^}]*flex:\s*1\s+1\s+260px;[^}]*min-width:\s*min\(100%,\s*220px\);/s);
  assert.match(css, /#panel-initiatives\[data-panel-placement="right"\] \.areas-search-input\s*\{[^}]*flex-basis:\s*100%;/s);
});

test('Planning CSS uses compact panel typography and control density', () => {
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(css, /\.planning-panel\s*\{[^}]*font-size:\s*11px;[^}]*line-height:\s*1\.35;/s);
  assert.match(css, /\.planning-panel \.tpled-new-btn\s*\{[^}]*font-size:\s*12px;[^}]*padding:\s*1px\s+7px;/s);
  assert.match(css, /\.planning-tab\s*\{[^}]*font-size:\s*11px;[^}]*padding:\s*4px\s+8px;/s);
  assert.match(css, /\.initiative-column-title\s*\{[^}]*font-size:\s*12px;/s);
  assert.match(css, /\.initiative-detail h2\s*\{[^}]*font-size:\s*14px;/s);
  assert.match(css, /\.initiative-form input,\s*\.initiative-form select,\s*\.initiative-form textarea,\s*\.initiative-link-add input\s*\{[^}]*font-size:\s*11px;[^}]*padding:\s*5px\s+7px;/s);
  assert.match(css, /\.areas-filters input,\s*\.areas-filters select,\s*\.area-note-editor input,\s*\.area-note-editor select,\s*\.area-note-editor textarea,\s*\.area-link-related-add select\s*\{[^}]*font-size:\s*11px;[^}]*padding:\s*5px\s+7px;/s);
});

test('Planning initial load requests Initiatives and Areas when opened', () => {
  const { sandbox, sendCalls } = createSandbox();

  vm.runInContext("planningEnsureLoaded({ includeInactive: true })", sandbox);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'initiative_list', group: 'Torque', include_archived: false },
    { cmd: 'area_list', group: 'Torque', include_archived: false, limit: 500 },
  ]);
});

test('Planning render refetches after full-state replacement instead of trusting stale loaded markers', () => {
  const { sandbox, sendCalls } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })}); areasReceiveList(${JSON.stringify({
    type: 'area_list',
    group: 'Torque',
    areas: [sampleArea()],
  })});`, sandbox);
  sendCalls.length = 0;

  vm.runInContext(`
    _initiativesLoadedGroup = 'Torque';
    _areasLoadedGroup = 'Torque';
    state = { active_group: 'Torque', initiatives: {}, areas: {}, board_tasks: {}, decisions: {} };
    renderInitiativesPanel();
  `, sandbox);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'initiative_list', group: 'Torque', include_archived: false },
  ]);

  sendCalls.length = 0;
  vm.runInContext("planningSetTab('areas')", sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'area_list', group: 'Torque', include_archived: false, limit: 500 },
  ]);
});

test('Planning refresh button and API refresh all Planning tab resources', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })}); areasReceiveList(${JSON.stringify({
    type: 'area_list',
    group: 'Torque',
    areas: [sampleArea()],
  })});`, sandbox);
  sendCalls.length = 0;

  assert.match(document.getElementById('panel-initiatives').innerHTML, /onclick="planningRefresh\(\)"/);
  vm.runInContext('planningRefresh()', sandbox);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls)), [
    { cmd: 'initiative_list', group: 'Torque', include_archived: false },
    { cmd: 'area_list', group: 'Torque', include_archived: false, limit: 500 },
  ]);
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

test('initiative detail opens existing task modal prefilled from editable brief', () => {
  const { sandbox, document } = createSandbox();
  let modalConfig = null;
  sandbox._generateDraftId = () => 'draft-from-initiative';
  sandbox._taskOpenModal = function(config) { modalConfig = config; };
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })})`, sandbox);
  vm.runInContext("initiativesSelect('TORQUE-I:1')", sandbox);

  document.getElementById('initiative-field-title').value = 'Edited task title';
  document.getElementById('initiative-field-summary').value = 'Draft summary from the drawer';
  document.getElementById('initiative-field-why').value = 'Draft why';
  document.getElementById('initiative-field-in-scope').value = 'Draft scope';
  document.getElementById('initiative-field-done-definition').value = 'Draft acceptance';
  const summary = document.getElementById('initiative-field-summary');
  summary.selectionStart = 5;
  summary.selectionEnd = 12;
  summary.focus();
  const drawer = document.getElementById('initiative-detail-drawer');
  drawer.scrollTop = 88;

  vm.runInContext('initiativesCreateBoardTask()', sandbox);

  assert.ok(modalConfig, 'expected the Planning affordance to reuse the Board task modal path');
  assert.equal(modalConfig.title, 'Create Board Task');
  assert.equal(modalConfig.submitLabel, 'Create task');
  assert.equal(modalConfig.task, 'Edited task title');
  assert.equal(modalConfig.group, 'Torque');
  assert.equal(modalConfig.lane, '');
  assert.equal(modalConfig.actionName, '');
  assert.equal(modalConfig.agentTemplate, '');
  assert.equal(modalConfig.draftId, 'draft-from-initiative');
  assert.equal(modalConfig.draftScope, 'initiative:TORQUE-I:1');
  assert.deepEqual(JSON.parse(JSON.stringify(modalConfig.labels)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(modalConfig.createContext)), {
    type: 'initiative',
    initiativeId: 'TORQUE-I:1',
    group: 'Torque',
  });
  assert.equal(typeof modalConfig.afterCreateSubmit, 'function');
  assert.match(modalConfig.description, /Source initiative: TORQUE-I:1 — Edited task title/);
  assert.match(modalConfig.description, /Summary\nDraft summary from the drawer/);
  assert.match(modalConfig.description, /Why\nDraft why/);
  assert.match(modalConfig.description, /In scope\nDraft scope/);
  assert.match(modalConfig.description, /Done definition\nDraft acceptance/);

  const restoredSummary = document.getElementById('initiative-field-summary');
  const restoredDrawer = document.getElementById('initiative-detail-drawer');
  assert.equal(restoredSummary.value, 'Draft summary from the drawer');
  assert.equal(restoredSummary.selectionStart, 5);
  assert.equal(restoredSummary.selectionEnd, 12);
  assert.equal(document.activeElement, restoredSummary);
  assert.equal(restoredDrawer.scrollTop, 88);
  assert.match(document.getElementById('panel-initiatives').innerHTML, /Review the prefilled Board task/);
  assert.doesNotMatch(document.getElementById('panel-initiatives').innerHTML, /Task creation modal is unavailable/);
});

test('created board task response links back to initiative without dispatching', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({
    type: 'initiative_list',
    group: 'Torque',
    initiatives: [sampleInitiative()],
  })})`, sandbox);
  vm.runInContext("initiativesSelect('TORQUE-I:1')", sandbox);
  const drawer = document.getElementById('initiative-detail-drawer');
  drawer.scrollTop = 33;

  vm.runInContext(`initiativesRegisterTaskCreatePending({
    source: { type: 'initiative', initiativeId: 'TORQUE-I:1', group: 'Torque' },
    task: 'Ship initiatives UI',
    group: 'Torque',
    draftId: 'draft-1234'
  })`, sandbox);
  vm.runInContext(`initiativesHandleBoardTaskCreated({ type: 'board_task_added', task_id: 'TORQUE:99', title: 'Ship initiatives UI' })`, sandbox);

  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'initiative_link_task',
    initiative: 'TORQUE-I:1',
    task: 'TORQUE:99',
    group: 'Torque',
  });
  assert.equal(sendCalls.some((call) => call.cmd === 'dispatch_task'), false);
  assert.equal(document.getElementById('initiative-detail-drawer').scrollTop, 33);
  assert.match(document.getElementById('panel-initiatives').innerHTML, /Linking it to the initiative/);

  vm.runInContext(`initiativesReceiveLinkMutation({
    type: 'initiative_task_linked',
    initiative_id: 'TORQUE-I:1',
    link: { initiative_id: 'TORQUE-I:1', link_type: 'task', linked_id: 'TORQUE:99' }
  })`, sandbox);
  assert.match(document.getElementById('panel-initiatives').innerHTML, /Created and linked Board task TORQUE:99/);
});

function sampleArea(overrides = {}) {
  return Object.assign({
    id: 'TORQUE-A:1',
    group: 'Torque',
    group_name: 'Torque',
    slug: 'operator-console',
    title: 'Operator console',
    area_type: 'product',
    lifecycle: 'active_investment',
    summary: 'Planning and dispatch surface.',
    user_purpose: 'Help operators steer work.',
    system_purpose: 'Coordinate agent state.',
    in_scope: 'Planning tabs and board links',
    out_of_scope: 'Graph visualization',
    links: {
      initiatives: ['TORQUE-I:1'],
      tasks: ['TORQUE:99'],
      decisions: ['decision-abc'],
      areas: [{ area_id: 'TORQUE-A:2', relation: 'supports' }],
    },
    hidden_link_counts: { tasks: 0, decisions: 0, initiatives: 0, areas: 0 },
    linked_tasks: { count: 1, hidden_count: 0, by_lane: { Done: 1 }, items: [{ id: 'TORQUE:99', title: 'Ship UI', lane: 'Done' }] },
    linked_decisions: { count: 1, hidden_count: 0, ids: ['decision-abc'], items: [{ id: 'decision-abc', title: 'Use tabs' }] },
    notes: [{ id: 7, area_id: 'TORQUE-A:1', note_type: 'invariant', title: 'Keep compact', body: 'No wiki scope', target_type: 'initiative', target_id: 'TORQUE-I:1' }],
  }, overrides);
}

test('Planning Areas tab renders list, search, lifecycle/type filters, loading and empty states', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext("planningSetTab('areas')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_list');
  let html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Loading areas/);

  vm.runInContext(`areasReceiveList(${JSON.stringify({
    type: 'area_list',
    group: 'Torque',
    areas: [
      sampleArea(),
      sampleArea({ id: 'TORQUE-A:2', title: 'Runtime platform', slug: 'runtime-platform', area_type: 'system', lifecycle: 'stable', summary: 'Daemon and PTY runtime.' }),
    ],
  })})`, sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Areas <span>2<\/span>/);
  assert.match(html, /Operator console/);
  assert.match(html, /Runtime platform/);
  assert.match(html, /Active Investment/);
  assert.match(html, /<input id="areas-search" class="areas-search-input"/);
  assert.match(html, /<option value="active_investment"[^>]*>Active Investment<\/option>/);
  assert.doesNotMatch(html, />active investment</);

  document.getElementById('areas-search').value = 'runtime';
  vm.runInContext("areasSetSearch('runtime')", sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.doesNotMatch(html, /Operator console/);
  assert.match(html, /Runtime platform/);

  vm.runInContext("areasSetLifecycleFilter('active_investment'); areasSetTypeFilter('product')", sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /No areas match the current search\/filter/);

  vm.runInContext("areasSetSearch('');", sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Operator console/);
  assert.doesNotMatch(html, /Runtime platform/);
});

test('area detail preserves active tab, selected area, drafts, caret, filters, sections, and scroll across rerenders', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`state.board_tasks['TORQUE:99'] = { id: 'TORQUE:99', task: 'Ship UI', lane: 'Done' }; state.decisions['decision-abc'] = { id: 'decision-abc', title: 'Use tabs' }; initiativesReceiveList(${JSON.stringify({ type: 'initiative_list', group: 'Torque', initiatives: [sampleInitiative()] })}); areasReceiveList(${JSON.stringify({ type: 'area_list', group: 'Torque', areas: [sampleArea(), sampleArea({ id: 'TORQUE-A:2', title: 'Runtime platform', area_type: 'system', lifecycle: 'stable' })] })}); planningSetTab('areas'); areasSelect('TORQUE-A:1'); areasReceiveDetail(${JSON.stringify(Object.assign({ type: 'area' }, sampleArea()))});`, sandbox);

  const summary = document.getElementById('area-field-summary');
  summary.value = 'Draft area summary while deltas arrive';
  summary.selectionStart = 6;
  summary.selectionEnd = 10;
  summary.focus();
  const list = document.getElementById('areas-list-scroll');
  list.scrollTop = 61;
  const drawer = document.getElementById('area-detail-drawer');
  drawer.scrollTop = 120;
  vm.runInContext("areasSetLifecycleFilter('active_investment'); areasToggleSection('tasks'); state.areas['TORQUE-A:1'].area_type = 'platform'; renderInitiativesPanel();", sandbox);

  const restored = document.getElementById('area-field-summary');
  assert.equal(restored.value, 'Draft area summary while deltas arrive');
  assert.equal(restored.selectionStart, 6);
  assert.equal(restored.selectionEnd, 10);
  assert.equal(document.activeElement, restored);
  assert.equal(document.getElementById('areas-lifecycle-filter').value, 'active_investment');
  assert.equal(document.getElementById('areas-list-scroll').scrollTop, 61);
  assert.equal(document.getElementById('area-detail-drawer').scrollTop, 120);
  const html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /class="planning-tab active" aria-selected="true" onclick="planningSetTab\('areas'\)"/);
  assert.match(html, /TORQUE-A:1/);
  assert.doesNotMatch(html, /No linked tasks/);
});

test('area save, link, note edit/archive use Wave 1 area commands', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`areasReceiveList(${JSON.stringify({ type: 'area_list', group: 'Torque', areas: [sampleArea(), sampleArea({ id: 'TORQUE-A:2', title: 'Runtime platform' })] })}); planningSetTab('areas'); areasSelect('TORQUE-A:1'); areasReceiveDetail(${JSON.stringify(Object.assign({ type: 'area' }, sampleArea()))});`, sandbox);

  document.getElementById('area-field-title').value = 'Edited area';
  document.getElementById('area-field-lifecycle').value = 'stable';
  vm.runInContext('areasSaveDetail()', sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'area_update',
    area: 'TORQUE-A:1',
    group: 'Torque',
    title: 'Edited area',
    area_type: 'product',
    lifecycle: 'stable',
    summary: 'Planning and dispatch surface.',
    user_purpose: 'Help operators steer work.',
    system_purpose: 'Coordinate agent state.',
    in_scope: 'Planning tabs and board links',
    out_of_scope: 'Graph visualization',
  });

  document.getElementById('area-link-initiative-input').value = 'TORQUE-I:1';
  vm.runInContext("areasLinkTarget('initiative')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_link_initiative');
  assert.equal(sendCalls.at(-1).initiative, 'TORQUE-I:1');

  document.getElementById('area-link-task-input').value = 'TORQUE:99';
  vm.runInContext("areasLinkTarget('task')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_link_task');
  assert.equal(sendCalls.at(-1).task, 'TORQUE:99');

  document.getElementById('area-link-decision-input').value = 'decision-abc';
  vm.runInContext("areasLinkTarget('decision')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_link_decision');
  assert.equal(sendCalls.at(-1).decision, 'decision-abc');

  document.getElementById('area-link-area-input').value = 'TORQUE-A:2';
  document.getElementById('area-link-area-relation').value = 'supports';
  vm.runInContext("areasLinkTarget('area')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_link_area');
  assert.equal(sendCalls.at(-1).area, 'TORQUE-A:1');
  assert.equal(sendCalls.at(-1).target_area, 'TORQUE-A:2');
  assert.equal(sendCalls.at(-1).relation, 'supports');

  vm.runInContext("areasEditNote('7')", sandbox);
  document.getElementById('area-note-edit-7-title').value = 'Updated invariant';
  document.getElementById('area-note-edit-7-body').value = 'Still compact';
  vm.runInContext("areasSaveNote('7')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_note_update');
  assert.equal(sendCalls.at(-1).note, '7');
  assert.equal(sendCalls.at(-1).title, 'Updated invariant');

  vm.runInContext("areasArchiveNote('7')", sandbox);
  assert.equal(sendCalls.at(-1).cmd, 'area_note_archive');
  assert.equal(sendCalls.at(-1).note, '7');
});

test('area note creation preserves draft across rerender and sends typed target command', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  vm.runInContext(`areasReceiveList(${JSON.stringify({ type: 'area_list', group: 'Torque', areas: [sampleArea()] })}); planningSetTab('areas'); areasSelect('TORQUE-A:1'); areasReceiveDetail(${JSON.stringify(Object.assign({ type: 'area' }, sampleArea({ notes: [] })))});`, sandbox);
  const title = document.getElementById('area-note-new-title');
  title.value = 'Follow up with design';
  title.selectionStart = 3;
  title.selectionEnd = 9;
  title.focus();
  document.getElementById('area-note-new-type').value = 'follow_up';
  document.getElementById('area-note-new-body').value = 'Clarify narrow mode copy.';
  document.getElementById('area-note-new-target-type').value = 'area';
  document.getElementById('area-note-new-target-id').value = 'TORQUE-A:1';

  vm.runInContext("state.areas['TORQUE-A:1'].summary = 'delta'; renderInitiativesPanel();", sandbox);
  assert.equal(document.getElementById('area-note-new-title').value, 'Follow up with design');
  assert.equal(document.activeElement, document.getElementById('area-note-new-title'));
  assert.equal(document.getElementById('area-note-new-title').selectionStart, 3);

  vm.runInContext('areasCreateNote()', sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls.at(-1))), {
    cmd: 'area_note_create',
    area: 'TORQUE-A:1',
    group: 'Torque',
    note_type: 'follow_up',
    title: 'Follow up with design',
    body: 'Clarify narrow mode copy.',
    target_type: 'area',
    target_id: 'TORQUE-A:1',
  });
});

test('area websocket responses and deltas update Planning surface without breaking initiative tab behavior', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`initiativesReceiveList(${JSON.stringify({ type: 'initiative_list', group: 'Torque', initiatives: [sampleInitiative()] })}); areasReceiveList(${JSON.stringify({ type: 'area_list', group: 'Torque', areas: [sampleArea()] })});`, sandbox);
  let html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Ship initiatives UI/);
  assert.match(html, /Areas <span>1<\/span>/);

  vm.runInContext("planningSetTab('areas')", sandbox);
  vm.runInContext(`areasReceiveMutation(${JSON.stringify({ type: 'area_updated', area: sampleArea({ title: 'Updated operator console' }) })})`, sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Updated operator console/);

  vm.runInContext(`state.areas = {}; _areasLoadedGroup = 'Torque'; _areasSelectedId = ''; _areasDetail = null;`, sandbox);
  vm.runInContext(`if (!state.areas) state.areas = {}; var op = ${JSON.stringify(Object.assign({ op: 'area_upsert' }, sampleArea({ id: 'TORQUE-A:3', title: 'Delta area' })))}; var area = Object.assign({}, op); delete area.op; state.areas[area.id] = area; renderInitiativesPanel();`, sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Delta area/);

  vm.runInContext("planningSetTab('initiatives')", sandbox);
  html = document.getElementById('panel-initiatives').innerHTML;
  assert.match(html, /Ship initiatives UI/);
  assert.match(html, /Now item|Ship initiatives UI/);
});

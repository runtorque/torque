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
    this.textContent = '';
    this.dataset = {};
    this.style = {};
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this.id === 'panel-thinking') this.ownerDocument.rebuildFromPanelHtml(this._innerHTML, this);
  }

  contains(node) {
    return !!node && node.ownerDocument === this.ownerDocument;
  }

  querySelector(selector) {
    if (!selector) return null;
    if (selector.startsWith('#')) return this.ownerDocument.getElementById(selector.slice(1));
    const thinkingField = selector.match(/^\[data-thinking-field="([^"]+)"\]$/);
    if (thinkingField) return this.ownerDocument.findByThinkingField(thinkingField[1]);
    return null;
  }

  querySelectorAll(selector) {
    return this.ownerDocument.querySelectorAll(selector);
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }

  getBoundingClientRect() {
    return { left: 0, top: 0, width: 800, height: 400, right: 800, bottom: 400 };
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.body = new FakeElement('body', this);
    this.ensure('panel-thinking');
  }

  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  findByThinkingField(field) {
    for (const el of this.elements.values()) {
      if (el.dataset && el.dataset.thinkingField === field) return el;
    }
    return null;
  }

  querySelector() { return null; }

  querySelectorAll(selector) {
    if (selector === '.thinking-map-node[data-node-id]') {
      return Array.from(this.elements.values()).filter((el) => el.dataset && el.dataset.nodeId);
    }
    if (selector === '.thinking-map-link-line[data-source-node-id]') {
      return Array.from(this.elements.values()).filter((el) => el.dataset && el.dataset.sourceNodeId);
    }
    return [];
  }

  rebuildFromPanelHtml(html, panel) {
    const previousPanel = panel;
    this.elements = new Map([['panel-thinking', previousPanel]]);
    const tagRe = /<(input|textarea|select|div|section|aside|button|line)\b([^>]*)>/g;
    let match;
    while ((match = tagRe.exec(html))) {
      const attrs = match[2] || '';
      const idMatch = attrs.match(/\bid="([^"]+)"/);
      const dataNode = attrs.match(/\bdata-node-id="([^"]+)"/);
      const dataLink = attrs.match(/\bdata-link-id="([^"]+)"/);
      const syntheticId = idMatch ? decodeEntities(idMatch[1])
        : dataNode ? `node-${decodeEntities(dataNode[1])}`
        : dataLink ? `link-${decodeEntities(dataLink[1])}`
        : '';
      if (!syntheticId) continue;
      const el = this.ensure(syntheticId);
      const dataThinking = attrs.match(/\bdata-thinking-field="([^"]+)"/);
      if (dataThinking) el.dataset.thinkingField = decodeEntities(dataThinking[1]);
      if (dataNode) el.dataset.nodeId = decodeEntities(dataNode[1]);
      if (dataLink) el.dataset.linkId = decodeEntities(dataLink[1]);
      const dataSource = attrs.match(/\bdata-source-node-id="([^"]+)"/);
      if (dataSource) el.dataset.sourceNodeId = decodeEntities(dataSource[1]);
      const dataTarget = attrs.match(/\bdata-target-node-id="([^"]+)"/);
      if (dataTarget) el.dataset.targetNodeId = decodeEntities(dataTarget[1]);
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

function createHarness() {
  const document = new FakeDocument();
  const sendCalls = [];
  const sandbox = {
    console,
    document,
    Date,
    Promise,
    Number,
    Math,
    state: {
      active_group: 'Torque',
      thinking: { scratchpad_notes: {}, mind_maps: {} },
      idea_briefs: {},
    },
    _activePanelApp: 'thinking',
    _currentGroup() { return sandbox.state.active_group; },
    _panelAppVisible(app) { return app === 'thinking'; },
    send(message) { sendCalls.push(message); },
    showConfirm() { return Promise.resolve(true); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  loadScript(sandbox, 'static/js/thinking.js');
  return { sandbox, document, sendCalls };
}

function run(context, code) {
  return vm.runInContext(code, context);
}

test('Thinking panel is registered as a first-class panel with responsive CSS', () => {
  const manager = fs.readFileSync(path.join(repoRoot, 'static/js/panel_manager.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const render = fs.readFileSync(path.join(repoRoot, 'static/js/render.js'), 'utf8');
  const ws = fs.readFileSync(path.join(repoRoot, 'static/js/ws.js'), 'utf8');
  const webview = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(manager, /_standalonePanelApps = \[[^\]]*'thinking'/);
  assert.match(manager, /thinking:\s*'Thinking'/);
  assert.match(manager, /thinking:\s*'bottom'/);
  assert.match(main, /panel-thinking/);
  assert.match(main, /thinkingEnsureLoaded\(\{ includeInactive: true \}\)/);
  assert.match(render, /surface === 'thinking'/);
  assert.match(ws, /thinking_scratchpad_note_upsert[\s\S]*_markSurface\(flags, 'thinking'\)/);
  assert.match(ws, /case 'idea_brief_upsert':[\s\S]*_markSurface\(flags, 'thinking'\)/);
  assert.match(ws, /ideaBriefReceiveMutation/);
  assert.match(webview, /id="panel-thinking"/);
  assert.match(webview, /data-app="thinking"[\s\S]*thinking-connected-nodes-icon[\s\S]*<span>Thinking<\/span>/);
  assert.doesNotMatch(webview, /&#9889;\s*Thinking/);
  assert.match(manager, /function _standalonePanelIconNode\([^)]*\)[\s\S]*app !== 'thinking'/);
  assert.match(manager, /standalone-panel-tab-has-icon/);
  assert.match(manager, /standalone-float-title-has-icon/);
  assert.match(webview, /static\/js\/thinking\.js[\s\S]*static\/js\/mission_control\.js/);
  assert.match(css, /\.thinking-connected-nodes-icon\s*\{[^}]*stroke:\s*currentColor;[^}]*stroke-linecap:\s*round;[^}]*stroke-linejoin:\s*round;/s);
  assert.match(css, /\.thinking-header-icon\s*\{[^}]*color:\s*var\(--accent\);/s);
  assert.match(css, /body\.runtime-embedded \.standalone-panel-zone-body > #panel-thinking,[\s\S]*body\.runtime-embedded \.standalone-float-body > #panel-thinking[\s\S]*\{[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*min-height:\s*0;[^}]*min-width:\s*0;/s);
  assert.match(css, /#panel-thinking\s*\{[^}]*display:\s*flex;[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*container-type:\s*inline-size;/s);
  assert.match(css, /#panel-thinking\[data-panel-placement="right"\] \.thinking-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*overflow:\s*auto;/s);
  assert.match(css, /@container \(max-width:\s*720px\)\s*\{[\s\S]*?\.thinking-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s);
});

test('Thinking panel initial load requests scratchpad and map lists and renders tabs', () => {
  const { sandbox, document, sendCalls } = createHarness();
  run(sandbox, `thinkingEnsureLoaded({ includeInactive: true }); renderThinkingPanel();`);

  assert.deepEqual(sendCalls.map((call) => call.cmd), ['scratchpad_note_list', 'mind_map_list']);
  assert.equal(sendCalls[0].group, 'Torque');
  assert.equal(sendCalls[1].group, 'Torque');
  const html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /Thinking/);
  assert.match(html, /thinking-header-icon[\s\S]*thinking-connected-nodes-icon/);
  assert.match(html, /Scratchpad/);
  assert.match(html, /Mind Map/);
  assert.match(html, /Idea Briefs/);
  assert.match(html, /stay separate from Planning/);
});

test('Scratchpad preserves draft, caret, selection, and scroll across Thinking rerenders', () => {
  const { sandbox, document } = createHarness();
  sandbox.state.thinking.scratchpad_notes['TORQUE-S:1'] = {
    id: 'TORQUE-S:1', group: 'Torque', group_name: 'Torque', title: 'Original title', body: 'server body', updated_at: '2026-06-23T12:00:00Z'
  };
  run(sandbox, `renderThinkingPanel(); thinkingScratchSelect('TORQUE-S:1');`);
  const body = document.getElementById('thinking-scratch-body');
  body.value = 'local draft body';
  body.selectionStart = 6;
  body.selectionEnd = 11;
  body.scrollTop = 42;
  body.focus();

  run(sandbox, `thinkingReceiveScratchpadDelta({ id: 'TORQUE-S:1', group: 'Torque', group_name: 'Torque', title: 'Server title', body: 'server pushed body' }); renderThinkingPanel();`);

  const restored = document.getElementById('thinking-scratch-body');
  assert.equal(restored.value, 'local draft body');
  assert.equal(document.activeElement, restored);
  assert.equal(restored.selectionStart, 6);
  assert.equal(restored.selectionEnd, 11);
  assert.equal(restored.scrollTop, 42);
});

test('Scratchpad create/update/archive/delete send trusted backend commands', async () => {
  const { sandbox, document, sendCalls } = createHarness();
  sandbox.state.thinking.scratchpad_notes['TORQUE-S:1'] = {
    id: 'TORQUE-S:1', group: 'Torque', group_name: 'Torque', title: 'Existing', body: 'Body'
  };
  run(sandbox, `renderThinkingPanel(); thinkingScratchNew();`);
  document.getElementById('thinking-scratch-title').value = 'New note';
  document.getElementById('thinking-scratch-body').value = 'Loose idea';
  run(sandbox, `thinkingScratchSave();`);
  assert.equal(sendCalls.at(-1).cmd, 'scratchpad_note_create');
  assert.equal(sendCalls.at(-1).title, 'New note');
  assert.equal(sendCalls.at(-1).context.rough, true);

  run(sandbox, `thinkingReceiveScratchpadMutation({ type: 'scratchpad_note_created', note: { id: 'TORQUE-S:2', group: 'Torque', group_name: 'Torque', title: 'New note', body: 'Loose idea' } });`);
  document.getElementById('thinking-scratch-title').value = 'Updated note';
  run(sandbox, `thinkingScratchSave();`);
  assert.equal(sendCalls.at(-1).cmd, 'scratchpad_note_update');
  assert.equal(sendCalls.at(-1).note_id, 'TORQUE-S:2');
  assert.equal(sendCalls.at(-1).title, 'Updated note');

  await run(sandbox, `thinkingScratchArchive('TORQUE-S:2'); Promise.resolve();`);
  assert.equal(sendCalls.at(-1).cmd, 'scratchpad_note_archive');
  await run(sandbox, `thinkingScratchDelete('TORQUE-S:2'); Promise.resolve();`);
  assert.equal(sendCalls.at(-1).cmd, 'scratchpad_note_delete');
});

test('Mind Map renders nodes/links and sends node/link CRUD plus position persistence commands', () => {
  const { sandbox, document, sendCalls } = createHarness();
  sandbox.state.thinking.mind_maps['TORQUE-M:1'] = {
    id: 'TORQUE-M:1', group: 'Torque', group_name: 'Torque', title: 'Launch ideas', description: 'Explore flows', node_count: 2, link_count: 1
  };
  run(sandbox, `thinkingSetTab('mind-map'); thinkingReceiveMindMapDetail({ type: 'mind_map', id: 'TORQUE-M:1', group: 'Torque', group_name: 'Torque', title: 'Launch ideas', description: 'Explore flows', nodes: [
    { id: 'TORQUE-M:1:N:1', map_id: 'TORQUE-M:1', label: 'Problem', notes: 'User pain', x: 20, y: 30, position: { x: 20, y: 30 }, sort_order: 1 },
    { id: 'TORQUE-M:1:N:2', map_id: 'TORQUE-M:1', label: 'Solution', notes: 'Sketch', x: 70, y: 60, position: { x: 70, y: 60 }, sort_order: 2 }
  ], links: [
    { id: 'TORQUE-M:1:L:1', map_id: 'TORQUE-M:1', source_node_id: 'TORQUE-M:1:N:1', target_node_id: 'TORQUE-M:1:N:2', label: 'inspires', sort_order: 1 }
  ] }); renderThinkingPanel();`);

  let html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /thinking-map-node/);
  assert.match(html, /thinking-map-link-line/);
  assert.match(html, /Problem/);
  assert.match(html, /inspires/);

  run(sandbox, `thinkingMindSelectNode('TORQUE-M:1:N:1');`);
  document.getElementById('thinking-node-edit-label').value = 'Problem updated';
  document.getElementById('thinking-node-edit-notes').value = 'Sharper note';
  run(sandbox, `thinkingMindSaveNode();`);
  assert.equal(sendCalls.at(-1).cmd, 'mind_map_node_update');
  assert.equal(sendCalls.at(-1).node_id, 'TORQUE-M:1:N:1');
  assert.equal(sendCalls.at(-1).label, 'Problem updated');

  run(sandbox, `thinkingMindMoveNode('TORQUE-M:1:N:1', 5, -5);`);
  assert.equal(sendCalls.at(-1).cmd, 'mind_map_node_position');
  assert.equal(sendCalls.at(-1).x, 25);
  assert.equal(sendCalls.at(-1).y, 25);
  assert.equal(JSON.stringify(sendCalls.at(-1).position), JSON.stringify({ x: 25, y: 25 }));

  document.getElementById('thinking-node-new-label').value = 'Evidence';
  run(sandbox, `thinkingMindAddNode();`);
  assert.equal(sendCalls.at(-1).cmd, 'mind_map_node_create');
  assert.equal(sendCalls.at(-1).label, 'Evidence');
  assert.equal(sendCalls.at(-1).mind_map_id, 'TORQUE-M:1');

  document.getElementById('thinking-link-new-source').value = 'TORQUE-M:1:N:1';
  document.getElementById('thinking-link-new-target').value = 'TORQUE-M:1:N:2';
  document.getElementById('thinking-link-new-label').value = 'supports';
  run(sandbox, `thinkingMindAddLink();`);
  assert.equal(sendCalls.at(-1).cmd, 'mind_map_link_create');
  assert.equal(sendCalls.at(-1).source_node_id, 'TORQUE-M:1:N:1');
  assert.equal(sendCalls.at(-1).target_node_id, 'TORQUE-M:1:N:2');
  assert.equal(sendCalls.at(-1).label, 'supports');

  run(sandbox, `thinkingMindSelectLink('TORQUE-M:1:L:1');`);
  document.getElementById('thinking-link-edit-label').value = 'blocks';
  run(sandbox, `thinkingMindSaveLink();`);
  assert.equal(sendCalls.at(-1).cmd, 'mind_map_link_update');
  assert.equal(sendCalls.at(-1).link_id, 'TORQUE-M:1:L:1');
  assert.equal(sendCalls.at(-1).label, 'blocks');
});

test('Mind Map tab and websocket deltas preserve focused link editor state', () => {
  const { sandbox, document } = createHarness();
  sandbox.state.thinking.mind_maps['TORQUE-M:1'] = {
    id: 'TORQUE-M:1', group: 'Torque', group_name: 'Torque', title: 'Map', node_count: 2, link_count: 1
  };
  run(sandbox, `thinkingSetTab('mind-map'); thinkingReceiveMindMapDetail({ type: 'mind_map', id: 'TORQUE-M:1', group: 'Torque', group_name: 'Torque', title: 'Map', nodes: [
    { id: 'n1', map_id: 'TORQUE-M:1', label: 'A', x: 20, y: 20, position: { x: 20, y: 20 } },
    { id: 'n2', map_id: 'TORQUE-M:1', label: 'B', x: 80, y: 80, position: { x: 80, y: 80 } }
  ], links: [{ id: 'l1', map_id: 'TORQUE-M:1', source_node_id: 'n1', target_node_id: 'n2', label: 'old' }] }); thinkingMindSelectLink('l1');`);
  const label = document.getElementById('thinking-link-edit-label');
  label.value = 'local link draft';
  label.selectionStart = 5;
  label.selectionEnd = 9;
  label.focus();

  run(sandbox, `thinkingReceiveMindMapLinkDelta({ id: 'l1', map_id: 'TORQUE-M:1', source_node_id: 'n1', target_node_id: 'n2', label: 'server link' }); renderThinkingPanel();`);

  const restored = document.getElementById('thinking-link-edit-label');
  assert.equal(restored.value, 'local link draft');
  assert.equal(document.activeElement, restored);
  assert.equal(restored.selectionStart, 5);
  assert.equal(restored.selectionEnd, 9);
});

test('Idea Brief tab renders list/detail and creates briefs with linked Thinking', () => {
  const { sandbox, document, sendCalls } = createHarness();
  sandbox.state.thinking.scratchpad_notes['TORQUE-S:1'] = {
    id: 'TORQUE-S:1', group: 'Torque', group_name: 'Torque', title: 'Pain note', body: 'Customers need a clearer intake.'
  };

  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefNew();`);
  assert.equal(sendCalls[0].cmd, 'idea_brief_list');
  assert.equal(sendCalls[0].group, 'Torque');
  let html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /Idea Briefs/);
  assert.match(html, /Review artifact/);
  assert.match(html, /Linked Thinking/);
  assert.match(html, /Pain note/);

  document.getElementById('idea-brief-title').value = 'Intake clarity';
  document.getElementById('idea-brief-problem-opportunity').value = 'Teams cannot evaluate raw ideas quickly.';
  document.getElementById('idea-brief-why-it-matters').value = 'Reduces review thrash.';
  document.getElementById('idea-brief-proposed-shape').value = 'A guided Idea Brief review surface.';
  document.getElementById('idea-brief-smallest-useful-version').value = 'List and editable detail.';
  document.getElementById('idea-brief-risks-tradeoffs').value = 'Could imply execution if copy is wrong.';
  document.getElementById('idea-brief-open-questions').value = 'Who reviews first?';
  document.getElementById('idea-brief-link-context').value = 'Source note for the problem framing.';
  run(sandbox, `ideaBriefAddLink(); ideaBriefSave();`);

  const createCall = sendCalls.at(-1);
  assert.equal(createCall.cmd, 'idea_brief_create');
  assert.equal(createCall.title, 'Intake clarity');
  assert.equal(createCall.problem_opportunity, 'Teams cannot evaluate raw ideas quickly.');
  assert.equal(createCall.thinking_links.length, 1);
  assert.equal(createCall.thinking_links[0].type, 'scratchpad_note');
  assert.equal(createCall.thinking_links[0].id, 'TORQUE-S:1');
  assert.equal(createCall.thinking_links[0].context, 'Source note for the problem framing.');
});

test('Idea Brief add link preserves non-default selected source without link context', () => {
  const { sandbox, document, sendCalls } = createHarness();
  sandbox.state.thinking.scratchpad_notes['TORQUE-S:1'] = {
    id: 'TORQUE-S:1', group: 'Torque', group_name: 'Torque', title: 'A first source', body: 'Default source'
  };
  sandbox.state.thinking.scratchpad_notes['TORQUE-S:2'] = {
    id: 'TORQUE-S:2', group: 'Torque', group_name: 'Torque', title: 'B second source', body: 'Selected source'
  };
  sandbox.state.idea_briefs['TORQUE-IB:1'] = {
    id: 'TORQUE-IB:1',
    group: 'Torque',
    group_name: 'Torque',
    title: 'Traceability source binding',
    status: 'draft',
    problem_opportunity: 'Operators need traceability links to bind to the selected source.',
    why_it_matters: '',
    proposed_shape: '',
    smallest_useful_version: '',
    risks_tradeoffs: '',
    open_questions: '',
    thinking_links: [],
  };

  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefSelect('TORQUE-IB:1');`);
  const source = document.getElementById('idea-brief-link-source');
  assert.equal(source.value, 'scratchpad_note|TORQUE-S:1');
  source.value = 'scratchpad_note|TORQUE-S:2';
  run(sandbox, `ideaBriefChanged(); ideaBriefAddLink();`);

  const linked = JSON.parse(run(sandbox, `JSON.stringify(_ideaBriefDraft('TORQUE-IB:1').thinking_links.map(function(link) {
    return { id: link.id, context: link.context || '' };
  }))`));
  assert.deepEqual(linked, [{ id: 'TORQUE-S:2', context: '' }]);
  assert.doesNotMatch(document.getElementById('panel-thinking').innerHTML, /TORQUE-S:1<\/span>/);
  assert.match(document.getElementById('panel-thinking').innerHTML, /TORQUE-S:2/);

  run(sandbox, `ideaBriefSave();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_update');
  assert.equal(sendCalls.at(-1).thinking_links.length, 1);
  assert.equal(sendCalls.at(-1).thinking_links[0].id, 'TORQUE-S:2');
  assert.equal(sendCalls.at(-1).thinking_links[0].context || '', '');
});

test('Idea Brief selection binds detail/body fields and traceability links to the selected brief id', () => {
  const { sandbox, document } = createHarness();
  const sharedSafety = 'Shared proposal-only safety boilerplate: review only, no task or dispatch.';
  const makeBrief = (id, sourceId, suffix) => ({
    id,
    group: 'Torque',
    group_name: 'Torque',
    title: `Catalyst brief ${suffix}`,
    status: 'proposed',
    problem_opportunity: `Distinct problem ${suffix}`,
    why_it_matters: `Distinct why ${suffix}`,
    proposed_shape: `Distinct product shape ${suffix}`,
    smallest_useful_version: `Distinct smallest version ${suffix}`,
    risks_tradeoffs: sharedSafety,
    open_questions: `Distinct open question ${suffix}`,
    proposal: { proposal_only: true, auto_dispatch: false, auto_assign: false },
    thinking_links: [
      { type: 'scratchpad_note', id: 'TORQUE-S:1', title: 'Shared source context' },
      { type: 'scratchpad_note', id: sourceId, title: `Draft source ${suffix}`, context: 'Shared source-context boilerplate' },
    ],
  });
  sandbox.state.idea_briefs['TORQUE-IB:1'] = makeBrief('TORQUE-IB:1', 'TORQUE-S:2', 'one');
  sandbox.state.idea_briefs['TORQUE-IB:2'] = makeBrief('TORQUE-IB:2', 'TORQUE-S:3', 'two');
  sandbox.state.idea_briefs['TORQUE-IB:3'] = makeBrief('TORQUE-IB:3', 'TORQUE-S:4', 'three');

  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefSelect('TORQUE-IB:1');`);
  assert.equal(document.getElementById('idea-brief-problem-opportunity').value, 'Distinct problem one');
  assert.equal(document.getElementById('idea-brief-why-it-matters').value, 'Distinct why one');
  assert.equal(document.getElementById('idea-brief-proposed-shape').value, 'Distinct product shape one');
  let html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /Shared proposal-only safety boilerplate/);
  assert.match(html, /Proposal for review/);
  assert.match(html, /TORQUE-S:1/);
  assert.match(html, /TORQUE-S:2/);

  run(sandbox, `ideaBriefSelect('TORQUE-IB:2');`);
  assert.equal(document.getElementById('idea-brief-title').value, 'Catalyst brief two');
  assert.equal(document.getElementById('idea-brief-problem-opportunity').value, 'Distinct problem two');
  assert.equal(document.getElementById('idea-brief-why-it-matters').value, 'Distinct why two');
  assert.equal(document.getElementById('idea-brief-proposed-shape').value, 'Distinct product shape two');
  assert.equal(document.getElementById('idea-brief-risks-tradeoffs').value, sharedSafety);
  html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /TORQUE-S:1/);
  assert.match(html, /TORQUE-S:3/);
  assert.doesNotMatch(html, /TORQUE-S:2/);

  run(sandbox, `ideaBriefSelect('TORQUE-IB:3');`);
  assert.equal(document.getElementById('idea-brief-title').value, 'Catalyst brief three');
  assert.equal(document.getElementById('idea-brief-problem-opportunity').value, 'Distinct problem three');
  assert.equal(document.getElementById('idea-brief-why-it-matters').value, 'Distinct why three');
  assert.equal(document.getElementById('idea-brief-proposed-shape').value, 'Distinct product shape three');
  assert.equal(document.getElementById('idea-brief-risks-tradeoffs').value, sharedSafety);
  html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /TORQUE-S:1/);
  assert.match(html, /TORQUE-S:4/);
  assert.doesNotMatch(html, /TORQUE-S:2/);
  assert.doesNotMatch(html, /TORQUE-S:3/);
});

test('Idea Brief edit/refine/propose/park/archive use reviewed backend commands and safety copy', async () => {
  const { sandbox, document, sendCalls } = createHarness();
  sandbox.state.idea_briefs['TORQUE-IB:1'] = {
    id: 'TORQUE-IB:1',
    group: 'Torque',
    group_name: 'Torque',
    title: 'Review flow',
    status: 'draft',
    problem_opportunity: 'Operators need a product-safe review path.',
    why_it_matters: 'Prevents accidental execution.',
    proposed_shape: 'Brief list and detail.',
    smallest_useful_version: 'Review actions only.',
    risks_tradeoffs: 'Ambiguous promote language.',
    open_questions: 'Who can approve?',
    thinking_links: [{
      type: 'scratchpad_note',
      id: 'TORQUE-S:1',
      title: 'Original note',
      summary: 'Summary metadata',
      quote: 'Important quote',
      confidence: 'medium',
    }],
  };

  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefSelect('TORQUE-IB:1');`);
  let html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /Original note/);
  assert.match(html, /Summary metadata/);
  assert.match(html, /Important quote/);
  assert.match(html, /never auto-dispatches or auto-assigns work/);

  document.getElementById('idea-brief-why-it-matters').value = 'Prevents accidental execution and assignment.';
  run(sandbox, `ideaBriefSave();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_update');
  assert.equal(sendCalls.at(-1).idea_brief, 'TORQUE-IB:1');
  assert.equal(sendCalls.at(-1).why_it_matters, 'Prevents accidental execution and assignment.');
  assert.equal(sendCalls.at(-1).thinking_links[0].quote, 'Important quote');

  document.getElementById('idea-brief-refinement-note').value = 'Tighten proposal language.';
  run(sandbox, `ideaBriefRefine();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_refine');
  assert.equal(sendCalls.at(-1).refinement_note, 'Tighten proposal language.');

  document.getElementById('idea-brief-proposal-note').value = 'Ready for Blueprint review.';
  await run(sandbox, `ideaBriefPropose(); Promise.resolve();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_propose');
  assert.equal(sendCalls.at(-1).note, 'Ready for Blueprint review.');

  run(sandbox, `ideaBriefReceiveMutation({ type: 'idea_brief_proposed', caveat: 'No task, assignment, dispatch, decision acceptance, merge, or deploy action was created.', review_scope: 'product_safe_review', proposal: { proposal_only: true, auto_dispatch: false, auto_assign: false, created_task_id: '', created_decision_id: '' }, idea_brief: Object.assign({}, state.idea_briefs['TORQUE-IB:1'], { status: 'proposed', proposal: { proposal_only: true, auto_dispatch: false, auto_assign: false, created_task_id: '', created_decision_id: '' } }) });`);
  html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /Proposal for review/);
  assert.match(html, /No task created/);
  assert.match(html, /No assignment/);
  assert.match(html, /No dispatch/);
  assert.doesNotMatch(html, /Task created:/);

  document.getElementById('idea-brief-lifecycle-reason').value = 'Needs more evidence.';
  await run(sandbox, `ideaBriefPark(); Promise.resolve();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_park');
  assert.equal(sendCalls.at(-1).reason, 'Needs more evidence.');

  await run(sandbox, `ideaBriefArchive(); Promise.resolve();`);
  assert.equal(sendCalls.at(-1).cmd, 'idea_brief_archive');
  assert.equal(sendCalls.at(-1).reason, 'Needs more evidence.');
});

test('Idea Brief clean selected detail updates from websocket deltas without a stale draft cache', () => {
  const { sandbox, document } = createHarness();
  sandbox.state.idea_briefs['TORQUE-IB:1'] = {
    id: 'TORQUE-IB:1',
    group: 'Torque',
    group_name: 'Torque',
    title: 'Server brief',
    status: 'draft',
    problem_opportunity: 'Original server problem',
    why_it_matters: 'Original server why',
    proposed_shape: 'Original server shape',
    smallest_useful_version: 'Original server version',
    risks_tradeoffs: 'Original server risk',
    open_questions: 'Original server question',
    thinking_links: [{ type: 'scratchpad_note', id: 'TORQUE-S:1', title: 'Original note' }],
  };

  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefSelect('TORQUE-IB:1');`);
  assert.equal(document.getElementById('idea-brief-problem-opportunity').value, 'Original server problem');

  run(sandbox, `ideaBriefReceiveDelta({
    id: 'TORQUE-IB:1',
    group: 'Torque',
    group_name: 'Torque',
    problem_opportunity: 'Updated server problem',
    why_it_matters: 'Updated server why',
    proposed_shape: 'Updated server shape',
    thinking_links: [{ type: 'scratchpad_note', id: 'TORQUE-S:2', title: 'Updated note' }]
  }); renderThinkingPanel();`);

  assert.equal(document.getElementById('idea-brief-problem-opportunity').value, 'Updated server problem');
  assert.equal(document.getElementById('idea-brief-why-it-matters').value, 'Updated server why');
  assert.equal(document.getElementById('idea-brief-proposed-shape').value, 'Updated server shape');
  const html = document.getElementById('panel-thinking').innerHTML;
  assert.match(html, /TORQUE-S:2/);
  assert.doesNotMatch(html, /TORQUE-S:1/);
});

test('Idea Brief preserves local draft, caret, selected link, and scroll across deltas', () => {
  const { sandbox, document } = createHarness();
  sandbox.state.idea_briefs['TORQUE-IB:1'] = {
    id: 'TORQUE-IB:1',
    group: 'Torque',
    group_name: 'Torque',
    title: 'Drafted brief',
    status: 'draft',
    problem_opportunity: 'Server problem',
    why_it_matters: 'Server why',
    proposed_shape: '',
    smallest_useful_version: '',
    risks_tradeoffs: '',
    open_questions: '',
    thinking_links: [{ type: 'scratchpad_note', id: 'TORQUE-S:1', title: 'Note' }],
  };
  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefSelect('TORQUE-IB:1'); ideaBriefSelectLink('scratchpad_note|TORQUE-S:1||0');`);
  const problem = document.getElementById('idea-brief-problem-opportunity');
  problem.value = 'Local problem draft';
  problem.selectionStart = 6;
  problem.selectionEnd = 13;
  problem.scrollTop = 33;
  problem.focus();

  run(sandbox, `ideaBriefReceiveDelta({ id: 'TORQUE-IB:1', group: 'Torque', group_name: 'Torque', problem_opportunity: 'Server pushed problem' }); renderThinkingPanel();`);

  const restored = document.getElementById('idea-brief-problem-opportunity');
  assert.equal(restored.value, 'Local problem draft');
  assert.equal(document.activeElement, restored);
  assert.equal(restored.selectionStart, 6);
  assert.equal(restored.selectionEnd, 13);
  assert.equal(restored.scrollTop, 33);
  assert.match(document.getElementById('panel-thinking').innerHTML, /idea-brief-link-card selected/);
});

test('Idea Brief error envelopes render on the review surface', () => {
  const { sandbox, document } = createHarness();
  run(sandbox, `thinkingSetTab('idea-briefs'); ideaBriefHandleError({ type: 'error', code: 'validation_error', message: 'Problem is required', contract: 'torque.idea_brief.v1' });`);
  assert.match(document.getElementById('panel-thinking').innerHTML, /validation_error: Problem is required/);
});

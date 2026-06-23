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
  assert.match(webview, /id="panel-thinking"/);
  assert.match(webview, /data-app="thinking"[^>]*>[^<]*Thinking/);
  assert.match(webview, /static\/js\/thinking\.js[\s\S]*static\/js\/mission_control\.js/);
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
  assert.match(html, /Scratchpad/);
  assert.match(html, /Mind Map/);
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

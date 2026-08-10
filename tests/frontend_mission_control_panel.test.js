const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');
const { appStylesheetSource } = require('./frontend_stylesheet_loader');

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
    if (this.id === 'panel-mission-control') this.ownerDocument.rebuildFromPanelHtml(this._innerHTML, this);
  }

  contains(node) {
    return !!node && node.ownerDocument === this.ownerDocument;
  }

  querySelector(selector) {
    if (!selector) return null;
    if (selector.startsWith('#')) return this.ownerDocument.getElementById(selector.slice(1));
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
    this.panel = this.ensure('panel-mission-control');
  }

  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  querySelector() { return null; }
  querySelectorAll() { return []; }

  rebuildFromPanelHtml(html, panel) {
    const previousPanel = panel;
    this.elements = new Map([['panel-mission-control', previousPanel]]);
    const tagRe = /<(input|main|aside|div|section|article|button)\b([^>]*)>/g;
    let match;
    while ((match = tagRe.exec(html))) {
      const attrs = match[2] || '';
      const idMatch = attrs.match(/\bid="([^"]+)"/);
      if (!idMatch) continue;
      const el = this.ensure(decodeEntities(idMatch[1]));
      const value = attrs.match(/\bvalue="([^"]*)"/);
      if (value) el.value = decodeEntities(value[1]);
      const dataPlacement = attrs.match(/\bdata-panel-placement="([^"]*)"/);
      if (dataPlacement) el.dataset.panelPlacement = decodeEntities(dataPlacement[1]);
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
    state: { active_group: 'Torque' },
    sendCalls,
    _activePanelApp: 'mission-control',
    _currentGroup() { return sandbox.state.active_group; },
    _panelAppVisible(app) { return app === 'mission-control'; },
    send(message) { sendCalls.push(message); },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  loadScript(sandbox, 'static/js/mission_control.js');
  return { sandbox, document, sendCalls };
}

function card(overrides = {}) {
  return Object.assign({
    id: 'mc:task:TORQUE:1:ask',
    ref: { kind: 'task', id: 'TORQUE:1' },
    kind: 'ask',
    title: 'Answer release question',
    group: 'Torque',
    owner: { agent_id: 'worker-1', agent_name: 'Worker One' },
    task_ids: ['TORQUE:1'],
    primary_task_id: 'TORQUE:1',
    gate: 'human_ask',
    reason: 'A worker ask is awaiting operator input.',
    recommended_next_action: 'answer_ask',
    evidence_chips: ['label:torque:human'],
    caveat_chips: ['operator_validation_required'],
    severity: 'high',
    timestamps: { updated_at: '2026-06-18T23:00:00+00:00' },
    deep_links: [{ surface: 'board_task', kind: 'inspect', task_id: 'TORQUE:1' }],
  }, overrides);
}

function summary(overrides = {}) {
  const sections = Object.assign({
    needs_operator_now: { count: 1, items: [card()], truncated: false },
    at_risk_watchlist: { count: 1, items: [card({
      id: 'mc:stream:feature:merge_conflict',
      ref: { kind: 'stream', id: 'feature' },
      kind: 'stream',
      title: 'Feature stream',
      gate: 'merge_conflict',
      reason: 'Stream has a merge conflict.',
      recommended_next_action: 'resolve_merge_conflict',
      evidence_chips: ['stream:merge_conflict'],
      caveat_chips: ['read_only_recommendation'],
      deep_links: [{ surface: 'stream', kind: 'inspect', stream_id: 'feature' }],
    })], truncated: false },
    in_flight: { count: 1, items: [card({
      id: 'mc:task:TORQUE:2:in_flight',
      ref: { kind: 'task', id: 'TORQUE:2' },
      title: 'Build feature',
      gate: 'active_work',
      recommended_next_action: 'continue_implementation',
      evidence_chips: ['lane:In Progress', 'health:healthy'],
      caveat_chips: [],
    })], truncated: false },
    recently_completed: { count: 1, items: [card({
      id: 'mc:task:TORQUE:3:completed',
      ref: { kind: 'task', id: 'TORQUE:3' },
      title: 'Review shipped',
      gate: 'completed',
      recommended_next_action: 'no_action',
      evidence_chips: ['lane:Done'],
      caveat_chips: [],
    })], truncated: false },
  }, overrides.sections || {});
  const counts = {
    total_cards: Object.values(sections).reduce((sum, section) => sum + section.count, 0),
    needs_operator_now: sections.needs_operator_now.count,
    at_risk_watchlist: sections.at_risk_watchlist.count,
    in_flight: sections.in_flight.count,
    recently_completed: sections.recently_completed.count,
  };
  return Object.assign({
    type: 'mission_control_summary',
    version: 1,
    generated_at: '2026-06-18T23:00:00+00:00',
    group: 'Torque',
    counts,
    sections,
    source_freshness: {
      tasks: { state: 'ok', count: 3 },
      streams: { state: 'error', error: 'stream cache unavailable' },
      deploy_state: { state: 'ok' },
    },
  }, overrides, { sections, counts: overrides.counts || counts });
}

test('Mission Control panel app is wired as a Board/Planning/Actions peer with responsive CSS', () => {
  const webview = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const manager = fs.readFileSync(path.join(repoRoot, 'static/js/panel_manager.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const css = appStylesheetSource();

  assert.match(webview, /id="panel-mission-control"/);
  assert.match(webview, /data-app="mission-control"/);
  assert.match(webview, /static\/js\/mission_control\.js/);
  assert.match(manager, /'mission-control': 'Mission'/);
  assert.match(manager, /'mission-control': 'bottom'/);
  assert.match(main, /missionControlEnsureLoaded/);
  assert.match(css, /body\.runtime-embedded \.standalone-panel-zone-body > #panel-mission-control,[\s\S]*body\.runtime-embedded \.standalone-float-body > #panel-mission-control[\s\S]*\{[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*min-height:\s*0;[^}]*min-width:\s*0;/s);
  assert.match(css, /#panel-mission-control\s*\{[^}]*display:\s*flex;[^}]*height:\s*100%;[^}]*width:\s*100%;[^}]*container-type:\s*inline-size;/s);
  assert.match(css, /\.mc-workspace\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+minmax\(280px,\s*clamp\(300px,\s*24vw,\s*440px\)\);/s);
  assert.match(css, /\.mc-section\s*\{[^}]*flex:\s*0\s+0\s+auto;[^}]*overflow:\s*hidden;/s);
  assert.match(css, /#panel-mission-control\[data-panel-placement="right"\] \.mc-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*overflow:\s*auto;/s);
  assert.match(css, /@container \(max-width:\s*720px\)\s*\{[\s\S]*?\.mc-workspace\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s);
});

test('Mission Control loading sends explicit group and handles empty/error states', () => {
  const { sandbox, document, sendCalls } = createSandbox();

  vm.runInContext('renderMissionControlPanel()', sandbox);
  assert.deepEqual(JSON.parse(JSON.stringify(sendCalls[0])), {
    cmd: 'get_mission_control',
    group: 'Torque',
    limit_per_section: 20,
    include_recent_completed: true,
  });
  assert.match(document.getElementById('panel-mission-control').innerHTML, /Loading Mission Control/);

  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(summary({
    sections: {
      needs_operator_now: { count: 0, items: [], truncated: false },
      at_risk_watchlist: { count: 0, items: [], truncated: false },
      in_flight: { count: 0, items: [], truncated: false },
      recently_completed: { count: 0, items: [], truncated: false },
    },
  }))})`, sandbox);
  assert.match(document.getElementById('panel-mission-control').innerHTML, /Mission Control is clear/);

  vm.runInContext(`_missionControlData = null; _missionControlLoadedGroup = null; _missionControlLoadingGroup = 'Torque'; missionControlHandleError({type:'error', message:'Mission Control backend exploded'});`, sandbox);
  assert.match(document.getElementById('panel-mission-control').innerHTML, /Mission Control backend exploded/);
});

test('Mission Control renders fixed sections, compact cards, source caveats, and deep-link descriptors', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(summary())})`, sandbox);
  const html = document.getElementById('panel-mission-control').innerHTML;

  const sectionOrder = [
    'data-section="needs_operator_now"',
    'data-section="at_risk_watchlist"',
    'data-section="in_flight"',
    'data-section="recently_completed"',
  ].map((needle) => html.indexOf(needle));
  assert.deepEqual(sectionOrder, [...sectionOrder].sort((a, b) => a - b));
  assert.ok(sectionOrder.every((idx) => idx >= 0));
  assert.match(html, /operator gates: 1/);
  assert.match(html, /Answer release question/);
  assert.match(html, /Worker One/);
  assert.match(html, /human_ask/);
  assert.match(html, /answer ask/);
  assert.match(html, /label:torque:human/);
  assert.match(html, /operator_validation_required/);
  assert.match(html, /updated at: 2026-06-18T23:00:00\+00:00/);
  assert.match(html, /board_task \/ inspect \/ TORQUE:1/);
  assert.match(html, /Source freshness/);
  assert.match(html, /streams/);
  assert.match(html, /stream cache unavailable/);
});

test('Mission Control filter, collapse, selection, and rerender state are preserved', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(summary())})`, sandbox);
  vm.runInContext("missionControlSelectCard('mc:task:TORQUE:1:ask'); missionControlToggleSection('at_risk_watchlist')", sandbox);

  const filter = document.getElementById('mission-control-filter');
  filter.value = 'answer';
  filter.selectionStart = 2;
  filter.selectionEnd = 5;
  filter.focus();
  const main = document.getElementById('mission-control-main');
  const detail = document.getElementById('mission-control-detail');
  main.scrollTop = 92;
  detail.scrollTop = 17;
  vm.runInContext("_missionControlFilter = 'answer';", sandbox);

  const changed = summary();
  changed.sections.needs_operator_now.items[0].title = 'Answer release question after delta';
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(changed)})`, sandbox);

  const restoredFilter = document.getElementById('mission-control-filter');
  const restoredMain = document.getElementById('mission-control-main');
  const restoredDetail = document.getElementById('mission-control-detail');
  const html = document.getElementById('panel-mission-control').innerHTML;

  assert.equal(restoredFilter.value, 'answer');
  assert.equal(restoredFilter.selectionStart, 2);
  assert.equal(restoredFilter.selectionEnd, 5);
  assert.equal(document.activeElement, restoredFilter);
  assert.equal(restoredMain.scrollTop, 92);
  assert.equal(restoredDetail.scrollTop, 17);
  assert.match(html, /mc-card mc-severity-high selected/);
  assert.match(html, /Answer release question after delta/);
  assert.match(html, /aria-expanded="false"[^>]*>[\s\S]*At-risk watchlist/);
  assert.doesNotMatch(html, /Feature stream/);
});

test('Mission Control dismisses only the chosen card and preserves rerender state', () => {
  const { sandbox, document, sendCalls } = createSandbox();
  const data = summary({
    sections: {
      needs_operator_now: { count: 2, items: [
        card(),
        card({
          id: 'mc:task:TORQUE:4:ask',
          title: 'Second operator question',
          primary_task_id: 'TORQUE:4',
          task_ids: ['TORQUE:4'],
        }),
      ], truncated: false },
    },
  });
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(data)})`, sandbox);
  sendCalls.length = 0;
  const filter = document.getElementById('mission-control-filter');
  filter.value = 'operator';
  filter.selectionStart = 2;
  filter.selectionEnd = 5;
  filter.focus();
  const main = document.getElementById('mission-control-main');
  main.scrollTop = 73;
  vm.runInContext("_missionControlFilter = 'operator'; missionControlDismissCard('mc:task:TORQUE:1:ask')", sandbox);

  const html = document.getElementById('panel-mission-control').innerHTML;
  const restoredFilter = document.getElementById('mission-control-filter');
  assert.equal(sendCalls.length, 1);
  assert.equal(sendCalls[0].cmd, 'mission_control_dismiss');
  assert.equal(sendCalls[0].id, 'mc:task:TORQUE:1:ask');
  assert.equal(typeof sendCalls[0].timestamp, 'number');
  assert.doesNotMatch(html, /Answer release question/);
  assert.match(html, /Second operator question/);
  assert.match(html, /operator gates: 1/);
  assert.equal(restoredFilter.value, 'operator');
  assert.equal(restoredFilter.selectionStart, 2);
  assert.equal(restoredFilter.selectionEnd, 5);
  assert.equal(document.activeElement, restoredFilter);
  assert.equal(document.getElementById('mission-control-main').scrollTop, 73);
});

test('Mission Control reconnect hydration restores persisted canonical dismissals', () => {
  const { sandbox, document } = createSandbox();
  const firstId = 'mc:task:TORQUE:1:ask';
  const secondId = 'mc:task:TORQUE:4:ask';
  const data = summary({
    sections: {
      needs_operator_now: { count: 2, items: [
        card(),
        card({
          id: secondId,
          title: 'Second operator question',
          primary_task_id: 'TORQUE:4',
          task_ids: ['TORQUE:4'],
        }),
      ], truncated: false },
    },
  });
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(data)})`, sandbox);

  // A reconnect replaces the full state snapshot rather than replaying missed
  // ui_update deltas. The persisted map must still drive the next render.
  vm.runInContext(`state = {
    active_group: 'Torque',
    mission_control_dismissed_cards: { ${JSON.stringify(firstId)}: 123 }
  }; renderMissionControlPanel();`, sandbox);

  const html = document.getElementById('panel-mission-control').innerHTML;
  assert.doesNotMatch(html, /Answer release question/);
  assert.match(html, /Second operator question/);
  assert.match(html, /operator gates: 1/);

  const invalidation = fs.readFileSync(
    path.join(repoRoot, 'static/js/ws/invalidation.js'), 'utf8',
  );
  assert.match(
    invalidation,
    /key === 'mission_control_dismissed_cards'[\s\S]*?_markSurface\(flags, 'mission-control'\)/,
  );
});

test('Mission Control side placement preserves workspace scroll and active filter during rerender', () => {
  const { sandbox, document } = createSandbox();
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(summary())})`, sandbox);

  const panel = document.getElementById('panel-mission-control');
  panel.dataset.panelPlacement = 'right';
  const filter = document.getElementById('mission-control-filter');
  filter.value = 'release';
  filter.selectionStart = 3;
  filter.selectionEnd = 6;
  filter.focus();
  const workspace = document.getElementById('mission-control-workspace');
  workspace.scrollTop = 155;
  workspace.scrollLeft = 9;
  vm.runInContext("_missionControlFilter = 'release';", sandbox);

  const changed = summary();
  changed.sections.needs_operator_now.items[0].reason = 'Delta arrived while Mission Control was docked right.';
  vm.runInContext(`missionControlReceiveSummary(${JSON.stringify(changed)})`, sandbox);

  const restoredFilter = document.getElementById('mission-control-filter');
  const restoredWorkspace = document.getElementById('mission-control-workspace');
  assert.equal(restoredFilter.value, 'release');
  assert.equal(restoredFilter.selectionStart, 3);
  assert.equal(restoredFilter.selectionEnd, 6);
  assert.equal(document.activeElement, restoredFilter);
  assert.equal(restoredWorkspace.scrollTop, 155);
  assert.equal(restoredWorkspace.scrollLeft, 9);
  assert.match(document.getElementById('panel-mission-control').innerHTML, /Delta arrived while Mission Control was docked right/);
});

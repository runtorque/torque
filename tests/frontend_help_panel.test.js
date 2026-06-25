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

class FakeClassList {
  constructor() { this._set = new Set(); }
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
  constructor(id, doc) {
    this.id = id || '';
    this.ownerDocument = doc;
    this._innerHTML = '';
    this.value = '';
    this.checked = false;
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.focusCalls = [];
    this.classList = new FakeClassList();
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this.id === 'panel-help') this.ownerDocument.rebuildFromPanelHtml(this._innerHTML, this);
  }

  contains(node) { return !!node && node.ownerDocument === this.ownerDocument; }

  querySelector(selector) {
    if (!selector) return null;
    if (selector.startsWith('#')) return this.ownerDocument.getElementById(selector.slice(1));
    return null;
  }

  querySelectorAll() { return []; }

  focus(opts) {
    this.focusCalls.push(opts === undefined ? null : opts);
    this.ownerDocument.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.elements = new Map();
    this.activeElement = null;
    this.body = new FakeElement('body', this);
    this.ensure('panel-help');
  }

  ensure(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement(id, this));
    return this.elements.get(id);
  }

  getElementById(id) { return this.elements.get(id) || null; }
  querySelector(selector) {
    if (selector && selector.startsWith('#')) return this.getElementById(selector.slice(1));
    return null;
  }
  querySelectorAll() { return []; }

  rebuildFromPanelHtml(html, panel) {
    this.elements = new Map([['panel-help', panel]]);
    const tagRe = /<(input|select|div|section|article|button|details|summary|pre|code)\b([^>]*)>/g;
    let match;
    while ((match = tagRe.exec(html))) {
      const attrs = match[2] || '';
      const idMatch = attrs.match(/\bid="([^"]+)"/);
      if (!idMatch) continue;
      const el = this.ensure(decodeEntities(idMatch[1]));
      const value = attrs.match(/\bvalue="([^"]*)"/);
      if (value) {
        el.value = decodeEntities(value[1]);
        el.selectionStart = el.value.length;
        el.selectionEnd = el.value.length;
      }
      const dataPlacement = attrs.match(/\bdata-panel-placement="([^"]*)"/);
      if (dataPlacement) el.dataset.panelPlacement = decodeEntities(dataPlacement[1]);
    }
  }
}

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
}

function listPayload(overrides = {}) {
  return Object.assign({
    type: 'help_topics',
    schema_version: 1,
    status: 'ok',
    index_hash: 'idx-list',
    source_model: {
      allowlist: 'mkdocs docs plus README/AGENTS/CLAUDE',
      cache: 'none; markdown is loaded from the installed tree at query time',
      source_paths: ['docs/reference/help.md', 'AGENTS.md'],
      restricted_safe: true,
    },
    topics: [
      {
        topic_id: 'docs-reference-help-md',
        title: 'Help docs contract',
        summary: 'Torque Help is a read-only documentation lookup surface.',
        source_path: 'docs/reference/help.md',
        source_hash: 'src-help',
        updated_at: '2026-06-25T09:00:00+00:00',
        audience_tags: ['agent', 'operator'],
        restricted_safe: true,
        examples: ['torque help list'],
        sections: [
          {
            id: 'docs-reference-help-md::wave-b-ui-brief',
            title: 'Wave B UI brief',
            anchor: 'wave-b-ui-brief',
            source_path: 'docs/reference/help.md',
            path_anchor: 'docs/reference/help.md#wave-b-ui-brief',
            line_start: 89,
            line_end: 95,
          },
        ],
      },
      {
        topic_id: 'agents-md',
        title: 'AGENTS instructions',
        summary: 'Agent-facing operating rules mirror CLAUDE.md.',
        source_path: 'AGENTS.md',
        source_hash: 'src-agents',
        updated_at: '2026-06-24T12:00:00+00:00',
        audience_tags: ['agent', 'worker'],
        restricted_safe: true,
        examples: [],
        sections: [],
      },
    ],
  }, overrides);
}

function showPayload(ref = 'docs-reference-help-md', overrides = {}) {
  const section = String(ref).includes('#wave-b-ui-brief');
  const agentTopic = /(^agents-md$|AGENTS\.md)/i.test(String(ref));
  return Object.assign({
    type: 'help_topic',
    schema_version: 1,
    status: 'ok',
    topic_id: agentTopic ? 'agents-md' : 'docs-reference-help-md',
    title: agentTopic ? 'AGENTS instructions' : (section ? 'Wave B UI brief' : 'Help docs contract'),
    summary: agentTopic ? 'Agent-facing operating rules mirror CLAUDE.md.' : 'Torque Help is a read-only documentation lookup surface.',
    source_path: agentTopic ? 'AGENTS.md' : 'docs/reference/help.md',
    path_anchor: agentTopic ? 'AGENTS.md' : (section ? 'docs/reference/help.md#wave-b-ui-brief' : 'docs/reference/help.md'),
    anchor: section ? 'wave-b-ui-brief' : '',
    source_hash: agentTopic ? 'src-agents' : 'src-help',
    updated_at: agentTopic ? '2026-06-24T12:00:00+00:00' : '2026-06-25T09:00:00+00:00',
    audience_tags: agentTopic ? ['agent', 'worker'] : ['agent', 'operator'],
    restricted_safe: true,
    body_excerpt: agentTopic
      ? '# AGENTS instructions\n\nWorkers report through Torque MCP tools and derive feature/review before done.'
      : section
      ? 'Panelsmith can build a Help panel against the response fields above.'
      : '# Help docs contract\n\nTorque Help is **read-only** and returns source references.',
    truncated: false,
    examples: agentTopic ? [] : ['torque help query "How do I derive review?"'],
    sections: agentTopic ? [] : [
      {
        id: 'docs-reference-help-md::wave-b-ui-brief',
        title: 'Wave B UI brief',
        anchor: 'wave-b-ui-brief',
        source_path: 'docs/reference/help.md',
        path_anchor: 'docs/reference/help.md#wave-b-ui-brief',
        line_start: 89,
        line_end: 95,
      },
    ],
    index_hash: 'idx-show',
    source_model: {
      allowlist: 'mkdocs docs plus README/AGENTS/CLAUDE',
      cache: 'none; markdown is loaded from the installed tree at query time',
      source_paths: ['docs/reference/help.md', 'AGENTS.md'],
      restricted_safe: true,
    },
  }, overrides);
}

function searchPayload() {
  return {
    type: 'help_search',
    schema_version: 1,
    status: 'ok',
    query: 'review',
    index_hash: 'idx-search',
    results: [
      {
        topic_id: 'agents-md',
        title: 'Review required',
        topic_title: 'AGENTS instructions',
        summary: 'Agent-facing operating rules mirror CLAUDE.md.',
        excerpt: 'Derive the feature/review transition before done.',
        source_path: 'AGENTS.md',
        path_anchor: 'AGENTS.md#worker-dispatch-and-reporting',
        anchor: 'worker-dispatch-and-reporting',
        source_hash: 'src-agents',
        updated_at: '2026-06-24T12:00:00+00:00',
        audience_tags: ['agent', 'worker'],
        restricted_safe: true,
        score: 52.5,
      },
      {
        topic_id: 'docs-reference-help-md',
        title: 'Wave B UI brief',
        topic_title: 'Help docs contract',
        summary: 'Torque Help UI brief.',
        excerpt: 'The Help UI should preserve source references.',
        source_path: 'docs/reference/help.md',
        path_anchor: 'docs/reference/help.md#wave-b-ui-brief',
        anchor: 'wave-b-ui-brief',
        source_hash: 'src-help',
        updated_at: '2026-06-25T09:00:00+00:00',
        audience_tags: ['operator'],
        restricted_safe: true,
        score: 35,
      },
    ],
    source_model: { restricted_safe: true },
  };
}

function queryPayload() {
  return {
    type: 'help_query',
    schema_version: 1,
    status: 'answered',
    question: 'How do I derive review?',
    answer: 'Deterministic Torque Help lookup found these maintained documentation sections:\n1. Review required — Derive feature/review before done. Source: AGENTS.md#worker-dispatch-and-reporting',
    sources: [
      {
        title: 'Review required',
        source_path: 'AGENTS.md',
        path_anchor: 'AGENTS.md#worker-dispatch-and-reporting',
        anchor: 'worker-dispatch-and-reporting',
        source_hash: 'src-agents',
        restricted_safe: true,
      },
    ],
    results: [],
    index_hash: 'idx-query',
    source_model: { restricted_safe: true },
  };
}

function createSandbox(options = {}) {
  const document = new FakeDocument();
  const fetchCalls = [];
  const errors = options.errors || {};
  const sandbox = {
    console,
    Date,
    JSON,
    Number,
    Promise,
    document,
    _activePanelApp: 'help',
    _panelAppVisible(app) { return app === 'help'; },
    fetch(url, init) {
      const body = JSON.parse(init.body || '{}');
      fetchCalls.push(body);
      if (errors[body.cmd]) {
        return Promise.resolve({
          ok: false,
          status: 500,
          statusText: 'Server error',
          json: async () => ({ ok: false, error: errors[body.cmd] }),
        });
      }
      let data;
      if (body.cmd === 'help_list') data = listPayload(options.list || {});
      else if (body.cmd === 'help_show') {
        if (body.topic === 'missing-topic') data = showPayload(body.topic, { status: 'not_found', message: 'No Torque Help topic matched the requested id/path.' });
        else data = showPayload(body.topic, options.show || {});
      } else if (body.cmd === 'help_search') {
        data = options.searchNoAnswer ? {
          type: 'help_search', schema_version: 1, status: 'no_answer', query: body.query,
          results: [], message: 'No maintained Torque Help docs matched.', index_hash: 'idx-empty', source_model: {},
        } : searchPayload();
      } else if (body.cmd === 'help_query') {
        data = options.queryNoAnswer ? {
          type: 'help_query', schema_version: 1, status: 'no_answer', question: body.question,
          answer: 'No maintained Torque Help documentation matched this question. Try `help_search` with broader terms or inspect `help_list`.',
          sources: [], results: [], index_hash: 'idx-no-answer', source_model: {},
        } : queryPayload();
      } else {
        data = { type: 'error', message: 'unexpected command' };
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        statusText: 'OK',
        json: async () => ({ ok: true, data }),
      });
    },
  };
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  loadScript(sandbox, 'static/js/render.js');
  loadScript(sandbox, 'static/js/markdown.js');
  loadScript(sandbox, 'static/js/help.js');
  return { sandbox, document, fetchCalls };
}

test('Help panel is wired as a first-class panel app', () => {
  const webview = fs.readFileSync(path.join(repoRoot, 'webview.html'), 'utf8');
  const manager = fs.readFileSync(path.join(repoRoot, 'static/js/panel_manager.js'), 'utf8');
  const main = fs.readFileSync(path.join(repoRoot, 'static/js/main.js'), 'utf8');
  const render = fs.readFileSync(path.join(repoRoot, 'static/js/render.js'), 'utf8');
  const css = fs.readFileSync(path.join(repoRoot, 'static/style.css'), 'utf8');

  assert.match(webview, /id="panel-help"/);
  assert.match(webview, /data-app="help"/);
  assert.match(webview, /static\/js\/help\.js[\s\S]*static\/js\/panel_manager\.js/);
  assert.match(manager, /help: 'Help'/);
  assert.match(manager, /help: 'right'/);
  assert.match(main, /helpEnsureLoaded/);
  assert.match(render, /surface === 'help'/);
  assert.match(css, /#panel-help\s*\{[\s\S]*container-type:\s*inline-size;/);
  assert.match(css, /#panel-help\[data-panel-placement="right"\] \.help-workspace\s*\{[\s\S]*flex-direction:\s*column;/);
  assert.match(css, /\.help-toolbar \.btn-primary,[\s\S]*\.help-query-row \.btn-secondary\s*\{[\s\S]*min-width:\s*64px;[\s\S]*justify-content:\s*center;/);
  assert.match(css, /#panel-help\[data-panel-placement="right"\] \.help-toolbar \.btn-primary,[\s\S]*\.help-query-row \.btn-secondary\s*\{[\s\S]*flex:\s*1 1 calc\(50% - 3px\);/);
});

test('Help panel loads topics, preserves search order, shows detail, and queries sources', async () => {
  const { sandbox, document, fetchCalls } = createSandbox();

  await vm.runInContext('helpEnsureLoaded({ force: true })', sandbox);
  let html = document.getElementById('panel-help').innerHTML;
  assert.deepEqual(fetchCalls.slice(0, 2).map((call) => call.cmd), ['help_list', 'help_show']);
  assert.equal(fetchCalls[0].audience, '');
  assert.match(html, /Help docs contract/);
  assert.match(html, /restricted-safe/);
  assert.match(html, /docs\/reference\/help\.md/);
  assert.match(html, /Excerpt/);
  assert.match(html, /torque help query/);
  assert.match(html, /Freshness and source model/);

  await vm.runInContext(`helpSearchInputChanged('review'); helpRunSearch()`, sandbox);
  html = document.getElementById('panel-help').innerHTML;
  assert.equal(fetchCalls.at(-1).cmd, 'help_search');
  assert.equal(fetchCalls.at(-1).query, 'review');
  assert.ok(html.indexOf('Review required') < html.indexOf('Wave B UI brief'));
  assert.match(html, /AGENTS\.md#worker-dispatch-and-reporting/);

  await vm.runInContext(`helpSelectReference('docs/reference/help.md#wave-b-ui-brief')`, sandbox);
  html = document.getElementById('panel-help').innerHTML;
  assert.equal(fetchCalls.at(-1).cmd, 'help_show');
  assert.equal(fetchCalls.at(-1).topic, 'docs/reference/help.md#wave-b-ui-brief');
  assert.match(html, /Panelsmith can build a Help panel/);

  await vm.runInContext(`helpQueryInputChanged('How do I derive review?'); helpRunQuery()`, sandbox);
  html = document.getElementById('panel-help').innerHTML;
  assert.equal(fetchCalls.at(-1).cmd, 'help_query');
  assert.equal(fetchCalls.at(-1).question, 'How do I derive review?');
  assert.match(html, /Extractive answer/);
  assert.match(html, /Sources/);
  assert.match(html, /AGENTS\.md#worker-dispatch-and-reporting/);

  assert.equal(fetchCalls.some((call) => /board|journal|memory|state/i.test(call.cmd)), false);
});

test('Help panel renders no-answer, not-found, and API error states', async () => {
  const empty = createSandbox({ searchNoAnswer: true, queryNoAnswer: true });
  await vm.runInContext('helpEnsureLoaded({ force: true })', empty.sandbox);
  await vm.runInContext(`helpSearchInputChanged('zzzz'); helpRunSearch()`, empty.sandbox);
  await vm.runInContext(`helpQueryInputChanged('zzzz'); helpRunQuery()`, empty.sandbox);
  await vm.runInContext(`helpSelectReference('missing-topic')`, empty.sandbox);
  let html = empty.document.getElementById('panel-help').innerHTML;
  assert.match(html, /No maintained Torque Help docs matched/);
  assert.match(html, /No answer found/);
  assert.match(html, /No Torque Help topic matched/);

  const errored = createSandbox({ errors: { help_search: 'search backend exploded' } });
  await vm.runInContext('helpEnsureLoaded({ force: true })', errored.sandbox);
  await vm.runInContext(`helpSearchInputChanged('boom'); helpRunSearch()`, errored.sandbox);
  html = errored.document.getElementById('panel-help').innerHTML;
  assert.match(html, /search backend exploded/);
});

test('Help panel rerender preserves query draft, focus/caret, selected topic, scroll, and expanded sections', async () => {
  const { sandbox, document } = createSandbox();
  await vm.runInContext('helpEnsureLoaded({ force: true })', sandbox);
  vm.runInContext(`
    helpQueryInputChanged('How do I derive review?');
    helpSearchInputChanged('review');
    helpToggleDetailSection('docs-reference-help-md::wave-b-ui-brief');
  `, sandbox);

  const query = document.getElementById('help-query-input');
  query.value = 'How do I derive review?';
  query.selectionStart = 4;
  query.selectionEnd = 10;
  query.scrollTop = 3;
  query.focus();
  document.getElementById('help-browser-scroll').scrollTop = 42;
  document.getElementById('help-detail-scroll').scrollTop = 77;

  vm.runInContext('renderHelpPanel()', sandbox);

  const nextQuery = document.getElementById('help-query-input');
  assert.equal(document.activeElement, nextQuery);
  assert.equal(nextQuery.value, 'How do I derive review?');
  assert.equal(nextQuery.selectionStart, 4);
  assert.equal(nextQuery.selectionEnd, 10);
  assert.equal(nextQuery.focusCalls.at(-1).preventScroll, true);
  assert.equal(document.getElementById('help-browser-scroll').scrollTop, 42);
  assert.equal(document.getElementById('help-detail-scroll').scrollTop, 77);
  assert.equal(vm.runInContext('_helpState.selectedRef', sandbox), 'docs-reference-help-md');
  const html = document.getElementById('panel-help').innerHTML;
  assert.match(html, /Open section/);
  assert.match(html, /docs\/reference\/help\.md#wave-b-ui-brief/);
});

test('Help topic selection preserves narrow workspace scroll and loads selected detail', async () => {
  const { sandbox, document, fetchCalls } = createSandbox();
  await vm.runInContext('helpEnsureLoaded({ force: true })', sandbox);

  let html = document.getElementById('panel-help').innerHTML;
  assert.match(html, /id="help-workspace-scroll"/);
  assert.match(html, /data-help-ref="agents-md"[\s\S]*onclick="helpSelectReference\(&quot;agents-md&quot;\)"/);

  document.getElementById('help-workspace-scroll').scrollTop = 318;
  document.getElementById('help-browser-scroll').scrollTop = 42;
  document.getElementById('help-detail-scroll').scrollTop = 7;
  vm.runInContext(`
    helpSearchInputChanged('review');
    helpQueryInputChanged('How do I derive review?');
  `, sandbox);

  await vm.runInContext(`helpSelectReference('agents-md')`, sandbox);

  html = document.getElementById('panel-help').innerHTML;
  assert.equal(fetchCalls.at(-1).cmd, 'help_show');
  assert.equal(fetchCalls.at(-1).topic, 'agents-md');
  assert.equal(vm.runInContext('_helpState.selectedRef', sandbox), 'agents-md');
  assert.equal(document.getElementById('help-workspace-scroll').scrollTop, 318);
  assert.equal(document.getElementById('help-browser-scroll').scrollTop, 42);
  assert.equal(document.getElementById('help-search-input').value, 'review');
  assert.equal(document.getElementById('help-query-input').value, 'How do I derive review?');
  assert.match(html, /AGENTS instructions/);
  assert.match(html, /Workers report through Torque MCP tools/);
  assert.match(html, /class="help-topic-card selected" data-help-ref="agents-md"/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '..');

function loadScript(context, relPath) {
  const filename = path.join(repoRoot, relPath);
  const source = fs.readFileSync(filename, 'utf8');
  vm.runInContext(source, context, { filename });
}

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

class FakeElement {
  constructor(id, document) {
    this.id = id || '';
    this.ownerDocument = document;
    this.dataset = {};
    this.classList = new FakeClassList();
    this.children = [];
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    this.style = {};
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.focusCalls = 0;
    this.innerHTMLSets = 0;
    this._innerHTML = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  contains(el) { return el === this || this.children.includes(el); }
  focus() {
    this.focusCalls += 1;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this.innerHTMLSets += 1;
    this._innerHTML = String(value || '');
  }
}

function createAiContext({ withWs = false } = {}) {
  const elements = new Map();
  const sendCalls = [];
  const confirmMessages = [];
  const renderCalls = [];
  const document = {
    activeElement: null,
    body: null,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new FakeElement(id, document));
      return elements.get(id);
    },
    createElement(tag) { return new FakeElement(tag, document); },
    querySelector(selector) {
      if (selector === '#modal-global-settings .gs-tab.active') {
        const tab = this.getElementById('gls-ai-tab');
        return tab.classList.contains('active') ? tab : null;
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-ai-provider-card]') return [];
      if (selector === '#modal-global-settings .gs-tab') return [this.getElementById('gls-ai-tab')];
      if (selector === '#modal-global-settings .gs-pane') return [this.getElementById('gls-ai-pane')];
      return [];
    },
  };
  document.body = document.getElementById('body');
  const modal = document.getElementById('modal-global-settings');
  const aiTab = document.getElementById('gls-ai-tab');
  aiTab.dataset.tab = 'gls-ai';
  const aiPane = document.getElementById('gls-ai-pane');
  aiPane.dataset.pane = 'gls-ai';
  document.getElementById('gls-ai-settings');
  const sandbox = {
    console,
    JSON,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    Promise,
    setTimeout() {},
    clearTimeout() {},
    requestAnimationFrame(fn) { fn(); return 1; },
    cancelAnimationFrame() {},
    location: { protocol: 'http:', host: 'localhost' },
    WebSocket: function FakeWebSocket() {},
    document,
    window: {},
    state: { agents: {}, groups: {}, children: {}, ai_settings: null },
    send(message) { sendCalls.push(JSON.parse(JSON.stringify(message))); },
    showConfirm(message) { confirmMessages.push(message); return Promise.resolve(false); },
    renderInvalidatedSurfaces(flags) { renderCalls.push(Object.assign({}, flags)); },
  };
  sandbox.WebSocket.OPEN = 1;
  sandbox.window = Object.assign(sandbox.window, sandbox);
  sandbox.global = sandbox;
  sandbox.globalThis = sandbox;
  const context = vm.createContext(sandbox);
  if (withWs) loadScript(context, 'static/js/ws.js');
  loadScript(context, 'static/js/ai_settings.js');
  return { context, document, elements, sendCalls, confirmMessages, renderCalls, modal, aiTab, aiPane };
}

function seedForm(document, overrides = {}) {
  document.getElementById('gls-ai-enabled').checked = overrides.enabled ?? true;
  document.getElementById('gls-ai-generation-provider').value = overrides.provider || 'anthropic';
  document.getElementById('gls-ai-anthropic-model').value = overrides.anthropicModel || 'claude-test';
  document.getElementById('gls-ai-openai-compatible-base-url').value = overrides.baseUrl || 'http://localhost:11434/v1';
  document.getElementById('gls-ai-openai-compatible-model').value = overrides.openaiModel || 'local-model';
  document.getElementById('gls-ai-embedding-model').value = overrides.embeddingModel || 'BAAI/bge-m3';
  for (const key of ['architect-journals', 'engineer-journals', 'decisions', 'tasks', 'engineer-peer-threads']) {
    document.getElementById(`gls-ai-corpus-${key}`).checked = true;
  }
  document.getElementById('gls-ai-boot-summary-enabled').checked = true;
}

function settingsFixture(extra = {}) {
  const base = {
    enabled: false,
    generation: {
      provider: 'anthropic',
      providers: ['anthropic', 'openai_compatible'],
      anthropic: { model: '', key: { configured: false, last4: '', updated_at: 0 } },
      openai_compatible: { base_url: '', model: '', key: { configured: false, last4: '', updated_at: 0 } },
    },
    embeddings: {
      runtime: 'sentence_transformers',
      model_id: 'BAAI/bge-m3',
      default_model_id: 'BAAI/bge-m3',
      dependency: { status: 'available', packages: ['sentence-transformers', 'sqlite-vec'], install_hint: 'make ai-deps' },
      active_model_id: '',
      active_dims: 0,
      desired_model_id: 'BAAI/bge-m3',
    },
    index: {
      status: 'not_built',
      corpus: { architect_journals: true, engineer_journals: true, decisions: true, tasks: true, engineer_peer_threads: true },
      counts: { sources: 0, chunks: 0, indexed: 0, pending: 0, stale: 0, errors: 0 },
      last_built_at: 0,
      last_error: '',
      current_job: null,
      rebuild_warning: { required: false, reason: '', estimated_entries: 0 },
    },
    boot_summary: { enabled: true, status: 'empty', counts: { ready: 0, stale: 0, errors: 0 }, last_refreshed_at: 0, last_error: '' },
    metering: { last_call_at: 0, calls_24h: 0, input_tokens_24h: 0, output_tokens_24h: 0, cache_read_input_tokens_24h: 0 },
  };
  return Object.assign(base, extra);
}

test('AI tab renders disabled-by-default defaults', () => {
  const { context, document } = createAiContext();
  context.renderAiSettingsTab();
  const html = document.getElementById('gls-ai-settings').innerHTML;

  assert.match(html, /Off until configured/);
  assert.match(html, /AI is disabled by default/);
  assert.match(html, /Anthropic/);
  assert.match(html, /OpenAI-compatible/);
  assert.match(html, /BAAI\/bge-m3/);
  assert.match(html, /sentence_transformers/);
  assert.doesNotMatch(html.match(/id="gls-ai-enabled"[^>]*>/)[0], /checked/);
});

test('API keys are masked, never rendered raw, sent once when dirty, then omitted unchanged', () => {
  const { context, document, sendCalls } = createAiContext();
  const rawKey = 'sk-ant-raw-secret-value';
  const configured = settingsFixture({
    generation: {
      provider: 'anthropic',
      providers: ['anthropic', 'openai_compatible'],
      anthropic: { model: 'claude-test', key: { configured: true, last4: '1234', updated_at: 10, api_key: rawKey } },
      openai_compatible: { base_url: '', model: '', key: { configured: false, last4: '', updated_at: 0 } },
    },
  });
  context.aiSettingsReceive({ type: 'ai_settings', schema_version: 1, settings: configured });
  const html = document.getElementById('gls-ai-settings').innerHTML;
  assert.match(html, /••••1234/);
  assert.doesNotMatch(html, new RegExp(rawKey));

  seedForm(document);
  document.getElementById('gls-ai-anthropic-api-key').value = 'sk-new-key';
  context.markAiSecretDirty('anthropic');
  context.submitAiSettings();

  assert.equal(sendCalls.length, 1);
  assert.equal(sendCalls[0].cmd, 'update_ai_settings');
  assert.equal(sendCalls[0].secrets.anthropic.api_key, 'sk-new-key');
  assert.equal(sendCalls[0].secrets.openai_compatible, undefined);
  assert.doesNotMatch(document.getElementById('gls-ai-settings').innerHTML, /sk-new-key/);

  const saved = settingsFixture({
    generation: {
      provider: 'anthropic',
      providers: ['anthropic', 'openai_compatible'],
      anthropic: { model: 'claude-test', key: { configured: true, last4: '9999', updated_at: 20 } },
      openai_compatible: { base_url: '', model: '', key: { configured: false, last4: '', updated_at: 0 } },
    },
  });
  context.aiSettingsReceive({ type: 'ai_settings', schema_version: 1, settings: saved });
  seedForm(document);
  context.submitAiSettings();

  assert.equal(sendCalls.length, 2);
  assert.deepEqual(sendCalls[1].secrets, {});
});

test('clear key button sets clear_secrets without sending an unchanged key', () => {
  const { context, document, sendCalls } = createAiContext();
  context.aiSettingsReceive({
    type: 'ai_settings',
    settings: settingsFixture({
      generation: {
        provider: 'anthropic',
        providers: ['anthropic', 'openai_compatible'],
        anthropic: { model: '', key: { configured: true, last4: '1234', updated_at: 1 } },
        openai_compatible: { base_url: '', model: '', key: { configured: false, last4: '', updated_at: 0 } },
      },
    }),
  });
  seedForm(document);
  context.clearAiSecret('anthropic');
  context.submitAiSettings();

  assert.deepEqual(sendCalls[0].clear_secrets, ['anthropic']);
  assert.deepEqual(sendCalls[0].secrets, {});
});

test('embedding rebuild confirmation uses the exact N-entries modal text', async () => {
  const { context, document, sendCalls, confirmMessages } = createAiContext();
  context.aiSettingsReceive({
    type: 'ai_settings',
    settings: settingsFixture({
      index: {
        status: 'ready',
        corpus: { architect_journals: true, engineer_journals: true, decisions: true, tasks: true, engineer_peer_threads: true },
        counts: { sources: 10, chunks: 123, indexed: 123, pending: 0, stale: 0, errors: 0 },
        last_built_at: 1,
        last_error: '',
        current_job: null,
        rebuild_warning: { required: false, reason: '', estimated_entries: 0 },
      },
    }),
  });
  seedForm(document, { embeddingModel: 'BAAI/new-model' });
  await context.submitAiSettings();

  assert.equal(confirmMessages[0], 'Changing the embedding model rebuilds the entire vector index (123 entries). Continue?');
  assert.equal(sendCalls.length, 0, 'declining the modal does not send update_ai_settings');
});

test('ai_index_status_update refreshes AI tab without broad rerender or focus loss', () => {
  const { context, document, renderCalls, modal, aiTab } = createAiContext({ withWs: true });
  modal.classList.add('visible');
  aiTab.classList.add('active');
  const root = document.getElementById('gls-ai-settings');
  const indexBlock = document.getElementById('gls-ai-index-status-block');
  document.getElementById('gls-ai-embedding-status-block');
  document.getElementById('gls-ai-summary-metering-block');
  const input = document.getElementById('gls-ai-embedding-model');
  input.value = 'draft-model';
  input.selectionStart = 5;
  input.selectionEnd = 5;
  input.focus();

  vm.runInContext(`
    state = { agents: {}, groups: {}, children: {}, ai_settings: ${JSON.stringify(settingsFixture())} };
    _expectedSeq = 1;
    dragInProgress = false;
    var aiCaptureCalls = 0;
    var aiRestoreCalls = 0;
    _captureSurfaceState = function(root) {
      aiCaptureCalls += 1;
      return {
        focus: {
          key: '#gls-ai-embedding-model',
          value: document.getElementById('gls-ai-embedding-model').value,
          selectionStart: document.getElementById('gls-ai-embedding-model').selectionStart,
          selectionEnd: document.getElementById('gls-ai-embedding-model').selectionEnd,
        },
        scrolls: [],
      };
    };
    _restoreSurfaceState = function(root, snapshot) {
      aiRestoreCalls += 1;
      var el = document.getElementById('gls-ai-embedding-model');
      el.value = snapshot.focus.value;
      el.selectionStart = snapshot.focus.selectionStart;
      el.selectionEnd = snapshot.focus.selectionEnd;
      el.focus();
    };
  `, context);
  context.renderAiSettingsTab({ preserveSurface: false });
  const rootWritesBefore = root.innerHTMLSets;
  input.value = 'draft-model';
  input.selectionStart = 5;
  input.selectionEnd = 5;
  input.focus();

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'ai_index_status_update',
      index: { status: 'building', counts: { chunks: 42, indexed: 7 }, current_job: { id: 'job-1', status: 'running', mode: 'rebuild' } },
    }],
  });

  assert.equal(root.innerHTMLSets, rootWritesBefore, 'root AI settings form is not fully rerendered');
  assert.deepEqual(renderCalls, [], 'no broad surface invalidation render was queued');
  assert.match(indexBlock.innerHTML, /building/);
  assert.match(indexBlock.innerHTML, /chunks 42/);
  assert.equal(input.value, 'draft-model');
  assert.equal(input.selectionStart, 5);
  assert.equal(document.activeElement, input);
  assert.equal(vm.runInContext('aiCaptureCalls', context), 1);
  assert.equal(vm.runInContext('aiRestoreCalls', context), 1);
});

test('AI-only global_settings_update does not invalidate broad surfaces', () => {
  const { context, renderCalls } = createAiContext({ withWs: true });
  vm.runInContext(`
    state = {
      agents: {},
      groups: {},
      children: {},
      global_settings: {},
      ai_settings: ${JSON.stringify(settingsFixture())}
    };
    _expectedSeq = 1;
    dragInProgress = false;
  `, context);

  context._handleDelta({
    seq: 1,
    ops: [{
      op: 'global_settings_update',
      changed_keys: ['ai_enabled', 'ai_embedding_model'],
      ai_enabled: true,
      ai_embedding_model: 'BAAI/bge-m3',
    }],
  });

  assert.deepEqual(renderCalls, [], 'AI-only global settings storage delta stays scoped to AI handling');
  assert.equal(vm.runInContext('state.global_settings.ai_enabled', context), true);
});

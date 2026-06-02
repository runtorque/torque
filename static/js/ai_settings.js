/* Settings → AI tab ------------------------------------------------------ */

var AI_SETTINGS_PROVIDERS = ['anthropic', 'openai_compatible'];
var AI_SETTINGS_CORPUS_KEYS = [
  ['architect_journals', 'Architect journals'],
  ['engineer_journals', 'Engineer journals'],
  ['decisions', 'Decisions'],
  ['tasks', 'Tasks'],
  ['engineer_peer_threads', 'Engineer peer threads'],
];
var _aiSecretDirty = { anthropic: false, openai_compatible: false };
var _aiClearSecrets = [];
var _aiSettingsLoaded = false;
var _aiSettingsLoading = false;
var _aiSettingsUpdateInFlight = false;
var _aiIndexStartInFlight = false;
var _aiPendingUpdatePayload = null;

function _aiEsc(value) {
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _aiClone(value) {
  if (value == null || typeof value !== 'object') return value;
  try { return JSON.parse(JSON.stringify(value)); } catch (_err) {}
  if (Array.isArray(value)) return value.slice();
  var out = {};
  for (var key in value) {
    if (Object.prototype.hasOwnProperty.call(value, key)) out[key] = value[key];
  }
  return out;
}

function _aiDefaultCorpus() {
  return {
    architect_journals: true,
    engineer_journals: true,
    decisions: true,
    tasks: true,
    engineer_peer_threads: true,
  };
}

function _aiDefaultSettings() {
  return {
    enabled: false,
    generation: {
      provider: 'anthropic',
      providers: AI_SETTINGS_PROVIDERS.slice(),
      anthropic: {
        model: '',
        key: { configured: false, last4: '', updated_at: 0 },
      },
      openai_compatible: {
        base_url: '',
        model: '',
        key: { configured: false, last4: '', updated_at: 0 },
      },
    },
    embeddings: {
      runtime: 'sentence_transformers',
      model_id: 'BAAI/bge-m3',
      default_model_id: 'BAAI/bge-m3',
      dependency: {
        status: 'unknown',
        packages: ['sentence-transformers', 'sqlite-vec'],
        install_hint: 'make ai-deps',
      },
      active_model_id: '',
      active_dims: 0,
      desired_model_id: 'BAAI/bge-m3',
    },
    index: {
      status: 'disabled',
      corpus: _aiDefaultCorpus(),
      counts: { sources: 0, chunks: 0, indexed: 0, pending: 0, stale: 0, errors: 0 },
      last_built_at: 0,
      last_error: '',
      current_job: null,
      rebuild_warning: { required: false, reason: '', estimated_entries: 0 },
    },
    boot_summary: {
      enabled: true,
      status: 'empty',
      counts: { ready: 0, stale: 0, errors: 0 },
      last_refreshed_at: 0,
      last_error: '',
    },
    metering: {
      last_call_at: 0,
      calls_24h: 0,
      input_tokens_24h: 0,
      output_tokens_24h: 0,
      cache_read_input_tokens_24h: 0,
    },
  };
}

function _aiObject(value) {
  return value && typeof value === 'object' ? value : {};
}

function _aiSettingsMergeDefaults(input) {
  var defaults = _aiDefaultSettings();
  var src = _aiObject(input);
  var out = _aiClone(defaults);
  out.enabled = src.enabled !== undefined ? !!src.enabled : defaults.enabled;

  var generation = _aiObject(src.generation);
  out.generation.provider = generation.provider || defaults.generation.provider;
  out.generation.providers = Array.isArray(generation.providers) && generation.providers.length
    ? generation.providers.slice()
    : defaults.generation.providers.slice();
  out.generation.anthropic = Object.assign(
    {},
    defaults.generation.anthropic,
    _aiObject(generation.anthropic),
  );
  out.generation.anthropic.key = Object.assign(
    {},
    defaults.generation.anthropic.key,
    _aiObject(_aiObject(generation.anthropic).key),
  );
  out.generation.openai_compatible = Object.assign(
    {},
    defaults.generation.openai_compatible,
    _aiObject(generation.openai_compatible),
  );
  out.generation.openai_compatible.key = Object.assign(
    {},
    defaults.generation.openai_compatible.key,
    _aiObject(_aiObject(generation.openai_compatible).key),
  );

  var embeddings = _aiObject(src.embeddings);
  out.embeddings = Object.assign({}, defaults.embeddings, embeddings);
  out.embeddings.dependency = Object.assign(
    {},
    defaults.embeddings.dependency,
    _aiObject(embeddings.dependency),
  );

  var index = _aiObject(src.index);
  out.index = Object.assign({}, defaults.index, index);
  out.index.corpus = Object.assign({}, defaults.index.corpus, _aiObject(index.corpus));
  out.index.counts = Object.assign({}, defaults.index.counts, _aiObject(index.counts));
  out.index.rebuild_warning = Object.assign(
    {},
    defaults.index.rebuild_warning,
    _aiObject(index.rebuild_warning),
  );

  var boot = _aiObject(src.boot_summary);
  out.boot_summary = Object.assign({}, defaults.boot_summary, boot);
  out.boot_summary.counts = Object.assign({}, defaults.boot_summary.counts, _aiObject(boot.counts));

  out.metering = Object.assign({}, defaults.metering, _aiObject(src.metering));
  return out;
}

function _aiSettingsRawFromState() {
  if (typeof state === 'undefined' || !state) return null;
  var cached = state.ai_settings;
  if (cached && cached.settings && typeof cached.settings === 'object') return cached.settings;
  return cached && typeof cached === 'object' ? cached : null;
}

function _aiSettingsCurrent() {
  return _aiSettingsMergeDefaults(_aiSettingsRawFromState());
}

function _aiSettingsStore(settings, schemaVersion) {
  if (typeof state === 'undefined' || !state) return;
  state.ai_settings = _aiClone(settings || {});
  if (schemaVersion !== undefined) state.ai_settings_schema_version = schemaVersion;
}

function _aiSettingsStoreMessage(msg) {
  if (!msg || typeof msg !== 'object') return;
  if (msg.settings && typeof msg.settings === 'object') {
    _aiSettingsStore(msg.settings, msg.schema_version);
  } else {
    var payload = Object.assign({}, msg);
    delete payload.type;
    delete payload.schema_version;
    _aiSettingsStore(payload, msg.schema_version);
  }
}

function _aiSettingsRoot() {
  return document.getElementById('gls-ai-settings');
}

function _aiSettingsModal() {
  return document.getElementById('modal-global-settings');
}

function _aiSettingsModalOpen() {
  var modal = _aiSettingsModal();
  return !!(modal && modal.classList && modal.classList.contains('visible'));
}

function _aiSettingsTabActive() {
  var active = document.querySelector
    ? document.querySelector('#modal-global-settings .gs-tab.active')
    : null;
  return !!(active && active.dataset && active.dataset.tab === 'gls-ai');
}

function _aiSettingsTabOpen() {
  return _aiSettingsModalOpen() && _aiSettingsTabActive();
}

function _aiSetHidden(el, hidden) {
  if (!el) return;
  el.hidden = !!hidden;
  if (el.classList) el.classList.toggle('hidden', !!hidden);
}

function _aiSetSaveStatus(message, level) {
  var el = document.getElementById('gls-ai-save-status');
  if (!el) return;
  el.textContent = message || '';
  el.dataset.level = level || '';
}


function _aiSettingsSyncSaveButtons(name) {
  var globalSaveBtn = document.getElementById('gls-global-save-btn');
  var aiSaveBtn = document.getElementById('gls-ai-save-btn');
  if (globalSaveBtn) globalSaveBtn.hidden = name === 'gls-ai';
  if (aiSaveBtn) aiSaveBtn.hidden = name !== 'gls-ai';
}

function _aiSettingsAfterTabSwitch(name) {
  _aiSettingsSyncSaveButtons(name);
  if (name === 'gls-ai') openAiSettingsTab();
}

function _aiSettingsInstallModalHooks() {
  if (typeof switchGlsTab === 'function'
      && (!switchGlsTab.__aiSettingsWrapped)) {
    var baseSwitchGlsTab = switchGlsTab;
    var wrappedSwitchGlsTab = function(name) {
      baseSwitchGlsTab(name);
      _aiSettingsAfterTabSwitch(name);
    };
    wrappedSwitchGlsTab.__aiSettingsWrapped = true;
    switchGlsTab = wrappedSwitchGlsTab;
  }
  if (typeof closeModals === 'function'
      && (!closeModals.__aiSettingsWrapped)) {
    var baseCloseModals = closeModals;
    var wrappedCloseModals = function() {
      baseCloseModals();
      // AI provider keys are write-only. If a draft key was typed and the
      // modal is dismissed, clear it from the hidden DOM instead of leaving the
      // raw value in an inactive settings pane.
      resetAiSettingsSecretDrafts();
    };
    wrappedCloseModals.__aiSettingsWrapped = true;
    closeModals = wrappedCloseModals;
  }
}

function _aiProviderLabel(provider) {
  if (provider === 'openai_compatible') return 'OpenAI-compatible';
  if (provider === 'anthropic') return 'Anthropic';
  return provider || 'Provider';
}

function _aiStatusLabel(value) {
  return String(value || 'unknown').replace(/_/g, ' ');
}

function _aiFormatCount(value) {
  var n = Number(value);
  if (!Number.isFinite(n)) return '0';
  return String(Math.max(0, Math.trunc(n)));
}

function _aiFormatTime(value) {
  var n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return 'Never';
  var millis = n > 100000000000 ? n : n * 1000;
  try { return new Date(millis).toLocaleString(); } catch (_err) { return 'Never'; }
}

function _aiKeyPlaceholder(key) {
  key = _aiObject(key);
  if (key.configured) return '••••' + String(key.last4 || '');
  return 'Paste API key (blank = unchanged)';
}

function _aiKeyStatusText(provider, key) {
  if (_aiClearSecrets.indexOf(provider) >= 0) return 'Will clear on save.';
  if (_aiSecretDirty[provider]) return 'New key will be saved once.';
  key = _aiObject(key);
  if (key.configured) return 'Configured' + (key.last4 ? ' (••••' + key.last4 + ')' : '') + '.';
  return 'Not configured.';
}

function _aiKeyStatusClass(provider, key) {
  if (_aiClearSecrets.indexOf(provider) >= 0) return 'ai-secret-status ai-secret-status-warning';
  if (_aiSecretDirty[provider]) return 'ai-secret-status ai-secret-status-dirty';
  return _aiObject(key).configured
    ? 'ai-secret-status ai-secret-status-ok'
    : 'ai-secret-status';
}

function _aiProviders(settings) {
  var providers = settings.generation && Array.isArray(settings.generation.providers)
    ? settings.generation.providers
    : AI_SETTINGS_PROVIDERS;
  var out = [];
  for (var i = 0; i < providers.length; i++) {
    var provider = String(providers[i] || '');
    if (provider && out.indexOf(provider) < 0) out.push(provider);
  }
  for (var j = 0; j < AI_SETTINGS_PROVIDERS.length; j++) {
    if (out.indexOf(AI_SETTINGS_PROVIDERS[j]) < 0) out.push(AI_SETTINGS_PROVIDERS[j]);
  }
  return out;
}

function _aiSectionHeader(title, detail) {
  var html = '<div class="gs-settings-section-title">' + _aiEsc(title);
  if (detail) html += '<span class="ai-settings-section-detail">' + _aiEsc(detail) + '</span>';
  html += '</div>';
  return html;
}

function _aiDependencyHtml(settings) {
  var dep = _aiObject(settings.embeddings && settings.embeddings.dependency);
  var status = dep.status || 'unknown';
  var packages = Array.isArray(dep.packages) ? dep.packages.join(', ') : '';
  var hint = dep.install_hint || 'make ai-deps';
  var html = '<div class="ai-status-grid">';
  html += '<div class="daemon-status-label">Runtime</div>';
  html += '<div class="daemon-status-value"><code>' + _aiEsc(settings.embeddings.runtime || 'sentence_transformers') + '</code></div>';
  html += '<div class="daemon-status-label">Dependency</div>';
  html += '<div class="daemon-status-value ai-status-line">'
    + '<span class="ai-status-pill ai-status-' + _aiEsc(status) + '">' + _aiEsc(_aiStatusLabel(status)) + '</span>';
  if (packages) html += '<span class="ai-muted">' + _aiEsc(packages) + '</span>';
  html += '</div>';
  if (status === 'missing') {
    html += '<div class="daemon-status-label">Install</div>';
    html += '<div class="daemon-status-value"><code>' + _aiEsc(hint) + '</code></div>';
  }
  html += '</div>';
  html += '<p class="ai-warning">First use downloads local embedding model weights. Models such as BAAI/bge-m3 can be GB-scale.</p>';
  return html;
}

function _aiIndexEntryEstimate(settings) {
  var warning = _aiObject(settings.index && settings.index.rebuild_warning);
  var n = Number(warning.estimated_entries);
  if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  var counts = _aiObject(settings.index && settings.index.counts);
  var candidates = [counts.chunks, counts.indexed, counts.sources];
  for (var i = 0; i < candidates.length; i++) {
    n = Number(candidates[i]);
    if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  }
  return 0;
}

function _aiRebuildConfirmMessage(estimatedEntries) {
  var n = Number(estimatedEntries);
  if (!Number.isFinite(n) || n < 0) n = 0;
  return 'Changing the embedding model rebuilds the entire vector index ('
    + String(Math.trunc(n))
    + ' entries). Continue?';
}

function _aiIndexStatusHtml(settings) {
  var index = _aiObject(settings.index);
  var counts = _aiObject(index.counts);
  var embeddings = _aiObject(settings.embeddings);
  var warning = _aiObject(index.rebuild_warning);
  var currentJob = index.current_job;
  var status = index.status || 'disabled';
  var hasRunningJob = !!(currentJob && ['queued', 'running'].indexOf(String(currentJob.status || '')) >= 0);
  var depStatus = _aiObject(embeddings.dependency).status || 'unknown';
  var disabled = hasRunningJob || status === 'building' || depStatus === 'missing';
  var mode = _aiIndexStartMode(settings);
  var btnLabel = mode === 'rebuild' ? 'Rebuild index' : 'Build index';
  var html = '<div class="ai-status-grid">';
  html += '<div class="daemon-status-label">Status</div>';
  html += '<div class="daemon-status-value ai-status-line"><span class="ai-status-pill ai-status-' + _aiEsc(status) + '">'
    + _aiEsc(_aiStatusLabel(status)) + '</span></div>';
  html += '<div class="daemon-status-label">Counts</div>';
  html += '<div class="daemon-status-value ai-counts">'
    + '<span>sources ' + _aiEsc(_aiFormatCount(counts.sources)) + '</span>'
    + '<span>chunks ' + _aiEsc(_aiFormatCount(counts.chunks)) + '</span>'
    + '<span>indexed ' + _aiEsc(_aiFormatCount(counts.indexed)) + '</span>'
    + '<span>pending ' + _aiEsc(_aiFormatCount(counts.pending)) + '</span>'
    + '<span>stale ' + _aiEsc(_aiFormatCount(counts.stale)) + '</span>'
    + '<span>errors ' + _aiEsc(_aiFormatCount(counts.errors)) + '</span>'
    + '</div>';
  html += '<div class="daemon-status-label">Active model</div>';
  html += '<div class="daemon-status-value"><code>' + _aiEsc(embeddings.active_model_id || '—') + '</code>';
  if (embeddings.active_dims) html += ' <span class="ai-muted">' + _aiEsc(embeddings.active_dims) + ' dims</span>';
  html += '</div>';
  html += '<div class="daemon-status-label">Desired</div>';
  html += '<div class="daemon-status-value"><code>' + _aiEsc(embeddings.desired_model_id || embeddings.model_id || '—') + '</code></div>';
  html += '<div class="daemon-status-label">Last built</div>';
  html += '<div class="daemon-status-value">' + _aiEsc(_aiFormatTime(index.last_built_at)) + '</div>';
  if (index.last_error) {
    html += '<div class="daemon-status-label">Last error</div>';
    html += '<div class="daemon-status-value ai-error-text">' + _aiEsc(index.last_error) + '</div>';
  }
  if (warning.required) {
    html += '<div class="daemon-status-label">Rebuild</div>';
    html += '<div class="daemon-status-value ai-warning-inline">'
      + _aiEsc(warning.reason || 'Embedding settings changed; a full rebuild is required.')
      + '</div>';
  }
  if (currentJob) {
    html += '<div class="daemon-status-label">Job</div>';
    html += '<div class="daemon-status-value">' + _aiEsc(currentJob.status || 'queued');
    if (currentJob.mode) html += ' <span class="ai-muted">' + _aiEsc(currentJob.mode) + '</span>';
    if (currentJob.message) html += '<div class="ai-muted">' + _aiEsc(currentJob.message) + '</div>';
    html += '</div>';
  }
  html += '</div>';
  html += '<div class="ai-actions-row">'
    + '<button type="button" class="btn-secondary" id="gls-ai-index-start-btn" onclick="aiIndexStart()"'
    + (disabled ? ' disabled' : '')
    + '>' + _aiEsc(btnLabel) + '</button>';
  if (depStatus === 'missing') html += '<span class="ai-muted">Install dependencies with <code>make ai-deps</code>.</span>';
  html += '<span id="gls-ai-index-status-note" class="ai-muted"></span>';
  html += '</div>';
  return html;
}

function _aiSummaryMeteringHtml(settings) {
  var boot = _aiObject(settings.boot_summary);
  var counts = _aiObject(boot.counts);
  var metering = _aiObject(settings.metering);
  var html = '<div class="ai-status-grid">';
  html += '<div class="daemon-status-label">Summary</div>';
  html += '<div class="daemon-status-value ai-status-line"><span class="ai-status-pill ai-status-' + _aiEsc(boot.status || 'unknown') + '">'
    + _aiEsc(_aiStatusLabel(boot.status || 'unknown')) + '</span>';
  html += boot.enabled === false ? '<span class="ai-muted">disabled in settings</span>' : '';
  html += '</div>';
  html += '<div class="daemon-status-label">Counts</div>';
  html += '<div class="daemon-status-value ai-counts">'
    + '<span>ready ' + _aiEsc(_aiFormatCount(counts.ready)) + '</span>'
    + '<span>stale ' + _aiEsc(_aiFormatCount(counts.stale)) + '</span>'
    + '<span>errors ' + _aiEsc(_aiFormatCount(counts.errors)) + '</span>'
    + '</div>';
  html += '<div class="daemon-status-label">Refreshed</div>';
  html += '<div class="daemon-status-value">' + _aiEsc(_aiFormatTime(boot.last_refreshed_at)) + '</div>';
  if (boot.last_error) {
    html += '<div class="daemon-status-label">Last error</div>';
    html += '<div class="daemon-status-value ai-error-text">' + _aiEsc(boot.last_error) + '</div>';
  }
  html += '<div class="daemon-status-label">Metering 24h</div>';
  html += '<div class="daemon-status-value ai-counts">'
    + '<span>calls ' + _aiEsc(_aiFormatCount(metering.calls_24h)) + '</span>'
    + '<span>input ' + _aiEsc(_aiFormatCount(metering.input_tokens_24h)) + '</span>'
    + '<span>output ' + _aiEsc(_aiFormatCount(metering.output_tokens_24h)) + '</span>'
    + '<span>cache read ' + _aiEsc(_aiFormatCount(metering.cache_read_input_tokens_24h)) + '</span>'
    + '</div>';
  html += '<div class="daemon-status-label">Last call</div>';
  html += '<div class="daemon-status-value">' + _aiEsc(_aiFormatTime(metering.last_call_at)) + '</div>';
  html += '</div>';
  return html;
}

function _aiProviderSecretHtml(provider, key) {
  var inputId = provider === 'anthropic'
    ? 'gls-ai-anthropic-api-key'
    : 'gls-ai-openai-compatible-api-key';
  return '<div class="ai-secret-row">'
    + '<input type="password" id="' + _aiEsc(inputId) + '" autocomplete="new-password" spellcheck="false"'
    + ' placeholder="' + _aiEsc(_aiKeyPlaceholder(key)) + '"'
    + ' oninput="markAiSecretDirty(\'' + _aiEsc(provider) + '\')">'
    + '<button type="button" class="btn-secondary" onclick="clearAiSecret(\'' + _aiEsc(provider) + '\')">Clear key</button>'
    + '</div>'
    + '<div id="' + _aiEsc(inputId) + '-status" class="' + _aiEsc(_aiKeyStatusClass(provider, key)) + '">'
    + _aiEsc(_aiKeyStatusText(provider, key))
    + '</div>';
}

function _aiSettingsHtml(settings) {
  var generation = settings.generation || {};
  var embeddings = settings.embeddings || {};
  var corpus = (settings.index && settings.index.corpus) || _aiDefaultCorpus();
  var providers = _aiProviders(settings);
  var selectedProvider = generation.provider || 'anthropic';
  var html = '';

  html += '<section class="gs-settings-section ai-master-section">';
  html += _aiSectionHeader('AI', 'Off until configured');
  html += '<div class="gs-settings-section-body">';
  html += '<label class="gs-checkbox ai-master-toggle">'
    + '<input type="checkbox" id="gls-ai-enabled"' + (settings.enabled ? ' checked' : '') + '> '
    + 'Enable AI features'
    + '</label>';
  html += '<p class="ai-settings-copy">AI is disabled by default. Enabling it may send selected prompts to the configured generation provider and may incur provider costs. Local embeddings use on-disk model weights and never require a remote embedding API.</p>';
  html += '</div></section>';

  html += '<section class="gs-settings-section">';
  html += _aiSectionHeader('Generation');
  html += '<div class="gs-settings-section-body">';
  html += '<label for="gls-ai-generation-provider">Provider</label>';
  html += '<select id="gls-ai-generation-provider" onchange="refreshAiProviderFields()">';
  for (var pi = 0; pi < providers.length; pi++) {
    var provider = providers[pi];
    html += '<option value="' + _aiEsc(provider) + '"' + (provider === selectedProvider ? ' selected' : '') + '>'
      + _aiEsc(_aiProviderLabel(provider)) + '</option>';
  }
  html += '</select>';
  html += '<div class="ai-provider-grid">';
  html += '<div class="ai-provider-card" data-ai-provider-card="anthropic">';
  html += '<div class="ai-provider-card-title">Anthropic</div>';
  html += '<label for="gls-ai-anthropic-model">Model</label>';
  html += '<input id="gls-ai-anthropic-model" autocomplete="off" spellcheck="false" value="'
    + _aiEsc(_aiObject(generation.anthropic).model || '') + '" placeholder="claude model">';
  html += '<label for="gls-ai-anthropic-api-key">API key <span class="label-hint">write-only</span></label>';
  html += _aiProviderSecretHtml('anthropic', _aiObject(_aiObject(generation.anthropic).key));
  html += '</div>';
  html += '<div class="ai-provider-card" data-ai-provider-card="openai_compatible">';
  html += '<div class="ai-provider-card-title">OpenAI-compatible</div>';
  html += '<label for="gls-ai-openai-compatible-base-url">Base URL</label>';
  html += '<input id="gls-ai-openai-compatible-base-url" autocomplete="off" spellcheck="false" value="'
    + _aiEsc(_aiObject(generation.openai_compatible).base_url || '') + '" placeholder="http://localhost:11434/v1">';
  html += '<label for="gls-ai-openai-compatible-model">Model</label>';
  html += '<input id="gls-ai-openai-compatible-model" autocomplete="off" spellcheck="false" value="'
    + _aiEsc(_aiObject(generation.openai_compatible).model || '') + '" placeholder="local model">';
  html += '<label for="gls-ai-openai-compatible-api-key">API key <span class="label-hint">write-only; optional for local servers</span></label>';
  html += _aiProviderSecretHtml('openai_compatible', _aiObject(_aiObject(generation.openai_compatible).key));
  html += '</div>';
  html += '</div></div></section>';

  html += '<section class="gs-settings-section">';
  html += _aiSectionHeader('Embeddings');
  html += '<div class="gs-settings-section-body">';
  html += '<label for="gls-ai-embedding-model">Embedding model</label>';
  html += '<input id="gls-ai-embedding-model" autocomplete="off" spellcheck="false" value="'
    + _aiEsc(embeddings.model_id || embeddings.default_model_id || 'BAAI/bge-m3') + '" placeholder="BAAI/bge-m3">';
  html += '<div id="gls-ai-embedding-status-block">' + _aiDependencyHtml(settings) + '</div>';
  html += '</div></section>';

  html += '<section class="gs-settings-section">';
  html += _aiSectionHeader('Corpus');
  html += '<div class="gs-settings-section-body ai-corpus-list">';
  for (var ci = 0; ci < AI_SETTINGS_CORPUS_KEYS.length; ci++) {
    var key = AI_SETTINGS_CORPUS_KEYS[ci][0];
    var label = AI_SETTINGS_CORPUS_KEYS[ci][1];
    var id = 'gls-ai-corpus-' + key.replace(/_/g, '-');
    html += '<label class="gs-checkbox"><input type="checkbox" id="' + _aiEsc(id) + '" data-ai-corpus="'
      + _aiEsc(key) + '"' + (corpus[key] !== false ? ' checked' : '') + '> '
      + _aiEsc(label) + '</label>';
  }
  html += '</div></section>';

  html += '<section class="gs-settings-section">';
  html += _aiSectionHeader('Vector index');
  html += '<div class="gs-settings-section-body" id="gls-ai-index-status-block">'
    + _aiIndexStatusHtml(settings)
    + '</div></section>';

  html += '<section class="gs-settings-section">';
  html += _aiSectionHeader('Boot summary + metering');
  html += '<div class="gs-settings-section-body">';
  html += '<label class="gs-checkbox"><input type="checkbox" id="gls-ai-boot-summary-enabled"'
    + (settings.boot_summary && settings.boot_summary.enabled === false ? '' : ' checked')
    + '> Enable cached boot summaries</label>';
  html += '<div id="gls-ai-summary-metering-block">' + _aiSummaryMeteringHtml(settings) + '</div>';
  html += '</div></section>';

  html += '<div class="ai-settings-footer">'
    + '<span id="gls-ai-save-status" class="ai-save-status"></span>'
    + '</div>';
  return html;
}

function renderAiSettingsTab(opts) {
  opts = opts || {};
  var root = _aiSettingsRoot();
  if (!root) return;
  var snapshot = null;
  if (opts.preserveSurface && typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(root, { scrollSelectors: [':root'] });
  }
  root.innerHTML = _aiSettingsHtml(_aiSettingsCurrent());
  refreshAiProviderFields();
  if (snapshot && typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot, { scrollSelectors: [':root'] });
  }
}

function refreshAiProviderFields() {
  var select = document.getElementById('gls-ai-generation-provider');
  var provider = select ? select.value : 'anthropic';
  if (!document.querySelectorAll) return;
  document.querySelectorAll('[data-ai-provider-card]').forEach(function(card) {
    var active = card && card.dataset && card.dataset.aiProviderCard === provider;
    if (card.classList) card.classList.toggle('ai-provider-card-active', !!active);
  });
}

function refreshAiSettingsDynamicSections(opts) {
  opts = opts || {};
  var root = _aiSettingsRoot();
  if (!root) return;
  var settings = _aiSettingsCurrent();
  var snapshot = null;
  if (opts.preserveSurface && typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(root, { scrollSelectors: [':root'] });
  }
  var dep = document.getElementById('gls-ai-embedding-status-block');
  if (dep) dep.innerHTML = _aiDependencyHtml(settings);
  var index = document.getElementById('gls-ai-index-status-block');
  if (index) index.innerHTML = _aiIndexStatusHtml(settings);
  var summary = document.getElementById('gls-ai-summary-metering-block');
  if (summary) summary.innerHTML = _aiSummaryMeteringHtml(settings);
  if (snapshot && typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot, { scrollSelectors: [':root'] });
  }
}

function _aiSettingsRefreshIfOpen(opts) {
  opts = opts || {};
  if (!_aiSettingsTabOpen()) return;
  if (opts.dynamicOnly) refreshAiSettingsDynamicSections({ preserveSurface: true });
  else renderAiSettingsTab({ preserveSurface: opts.preserveSurface !== false });
}

function openAiSettingsTab() {
  renderAiSettingsTab({ preserveSurface: true });
  _aiSettingsLoading = true;
  if (typeof send === 'function') send({ cmd: 'get_ai_settings' });
}

function resetAiSettingsSecretDrafts() {
  for (var i = 0; i < AI_SETTINGS_PROVIDERS.length; i++) {
    var provider = AI_SETTINGS_PROVIDERS[i];
    _aiSecretDirty[provider] = false;
    var input = _aiSecretInput(provider);
    if (input) input.value = '';
  }
  _aiClearSecrets = [];
  _aiPendingUpdatePayload = null;
}

function _aiSecretInput(provider) {
  return document.getElementById(provider === 'anthropic'
    ? 'gls-ai-anthropic-api-key'
    : 'gls-ai-openai-compatible-api-key');
}

function _aiProviderKey(provider) {
  var settings = _aiSettingsCurrent();
  var gen = settings.generation || {};
  var item = provider === 'anthropic' ? gen.anthropic : gen.openai_compatible;
  return _aiObject(_aiObject(item).key);
}

function _aiRefreshSecretStatus(provider) {
  var input = _aiSecretInput(provider);
  if (input) {
    if (_aiClearSecrets.indexOf(provider) >= 0) input.placeholder = 'Will clear on save';
    else input.placeholder = _aiKeyPlaceholder(_aiProviderKey(provider));
  }
  var statusEl = document.getElementById(
    (provider === 'anthropic' ? 'gls-ai-anthropic-api-key' : 'gls-ai-openai-compatible-api-key') + '-status'
  );
  if (statusEl) {
    statusEl.textContent = _aiKeyStatusText(provider, _aiProviderKey(provider));
    statusEl.className = _aiKeyStatusClass(provider, _aiProviderKey(provider));
  }
}

function markAiSecretDirty(provider) {
  provider = String(provider || '');
  if (AI_SETTINGS_PROVIDERS.indexOf(provider) < 0) return;
  _aiSecretDirty[provider] = true;
  var idx = _aiClearSecrets.indexOf(provider);
  if (idx >= 0) _aiClearSecrets.splice(idx, 1);
  _aiRefreshSecretStatus(provider);
}

function clearAiSecret(provider) {
  provider = String(provider || '');
  if (AI_SETTINGS_PROVIDERS.indexOf(provider) < 0) return;
  var input = _aiSecretInput(provider);
  if (input) input.value = '';
  _aiSecretDirty[provider] = false;
  if (_aiClearSecrets.indexOf(provider) < 0) _aiClearSecrets.push(provider);
  _aiRefreshSecretStatus(provider);
}

function _aiInputValue(id) {
  var el = document.getElementById(id);
  return el && typeof el.value === 'string' ? el.value.trim() : '';
}

function _aiInputChecked(id, fallback) {
  var el = document.getElementById(id);
  return el ? !!el.checked : !!fallback;
}

function _aiSelectedProvider() {
  var el = document.getElementById('gls-ai-generation-provider');
  return el && el.value ? el.value : 'anthropic';
}

function _aiCorpusFromForm() {
  var corpus = {};
  for (var i = 0; i < AI_SETTINGS_CORPUS_KEYS.length; i++) {
    var key = AI_SETTINGS_CORPUS_KEYS[i][0];
    var id = 'gls-ai-corpus-' + key.replace(/_/g, '-');
    corpus[key] = _aiInputChecked(id, true);
  }
  return corpus;
}

function _aiCollectPayload(confirmEmbeddingRebuild) {
  var settings = {
    enabled: _aiInputChecked('gls-ai-enabled', false),
    generation: {
      provider: _aiSelectedProvider(),
      anthropic: { model: _aiInputValue('gls-ai-anthropic-model') },
      openai_compatible: {
        base_url: _aiInputValue('gls-ai-openai-compatible-base-url'),
        model: _aiInputValue('gls-ai-openai-compatible-model'),
      },
    },
    embeddings: { model_id: _aiInputValue('gls-ai-embedding-model') || 'BAAI/bge-m3' },
    index: { corpus: _aiCorpusFromForm() },
    boot_summary: { enabled: _aiInputChecked('gls-ai-boot-summary-enabled', true) },
  };
  var secrets = {};
  for (var i = 0; i < AI_SETTINGS_PROVIDERS.length; i++) {
    var provider = AI_SETTINGS_PROVIDERS[i];
    var input = _aiSecretInput(provider);
    var value = input && typeof input.value === 'string' ? input.value : '';
    if (_aiSecretDirty[provider] && value !== '') {
      secrets[provider] = { api_key: value };
    }
  }
  return {
    cmd: 'update_ai_settings',
    settings: settings,
    secrets: secrets,
    clear_secrets: _aiClearSecrets.slice(),
    confirm_embedding_rebuild: !!confirmEmbeddingRebuild,
  };
}

function _aiCorpusChanged(a, b) {
  a = _aiObject(a);
  b = _aiObject(b);
  for (var i = 0; i < AI_SETTINGS_CORPUS_KEYS.length; i++) {
    var key = AI_SETTINGS_CORPUS_KEYS[i][0];
    if ((a[key] !== false) !== (b[key] !== false)) return true;
  }
  return false;
}

function _aiFormRequiresRebuildConfirmation(nextSettings) {
  var current = _aiSettingsCurrent();
  var entries = _aiIndexEntryEstimate(current);
  if (entries <= 0) return false;
  var currentModel = String(_aiObject(current.embeddings).model_id || _aiObject(current.embeddings).desired_model_id || '');
  var nextModel = String(_aiObject(nextSettings.embeddings).model_id || '');
  if (currentModel && nextModel && currentModel !== nextModel) return true;
  if (_aiCorpusChanged(_aiObject(current.index).corpus, _aiObject(_aiObject(nextSettings.index).corpus))) return true;
  return !!(_aiObject(_aiObject(current.index).rebuild_warning).required);
}

function _aiShowRebuildConfirm(estimatedEntries) {
  var message = _aiRebuildConfirmMessage(estimatedEntries);
  if (typeof showConfirm !== 'function') return Promise.resolve(false);
  return showConfirm(message, { label: 'Continue', variant: 'btn-danger' });
}

function _aiSendUpdatePayload(payload) {
  _aiPendingUpdatePayload = payload;
  _aiSettingsUpdateInFlight = true;
  _aiSetSaveStatus('Saving…', 'pending');
  if (typeof send === 'function') send(payload);
}

async function submitAiSettings(confirmEmbeddingRebuild) {
  if (_aiSettingsUpdateInFlight) return;
  var payload = _aiCollectPayload(!!confirmEmbeddingRebuild);
  if (!payload.confirm_embedding_rebuild && _aiFormRequiresRebuildConfirmation(payload.settings)) {
    var ok = await _aiShowRebuildConfirm(_aiIndexEntryEstimate(_aiSettingsCurrent()));
    if (!ok) {
      _aiSetSaveStatus('Save canceled.', 'warning');
      return;
    }
    payload.confirm_embedding_rebuild = true;
  }
  _aiSendUpdatePayload(payload);
}

function _aiIndexStartMode(settings) {
  settings = settings || _aiSettingsCurrent();
  var index = _aiObject(settings.index);
  var status = String(index.status || 'disabled');
  var warning = _aiObject(index.rebuild_warning);
  var counts = _aiObject(index.counts);
  if (warning.required || status === 'ready' || status === 'rebuild_pending') return 'rebuild';
  if (Number(counts.chunks || 0) > 0 || Number(counts.indexed || 0) > 0) return 'rebuild';
  return 'incremental';
}

function aiIndexStart(mode) {
  if (_aiIndexStartInFlight) return;
  var payload = {
    cmd: 'ai_index_start',
    mode: mode || _aiIndexStartMode(_aiSettingsCurrent()),
    confirm: true,
  };
  _aiIndexStartInFlight = true;
  var note = document.getElementById('gls-ai-index-status-note');
  if (note) note.textContent = 'Starting…';
  if (typeof send === 'function') send(payload);
}

function aiSettingsReceive(msg) {
  _aiSettingsLoading = false;
  _aiSettingsLoaded = true;
  _aiSettingsStoreMessage(msg);
  _aiSettingsUpdateInFlight = false;
  _aiPendingUpdatePayload = null;
  resetAiSettingsSecretDrafts();
  renderAiSettingsTab({ preserveSurface: false });
  if (_aiSettingsTabOpen()) _aiSetSaveStatus('AI settings loaded.', 'success');
}

async function aiSettingsRequiresConfirmation(msg) {
  var estimated = Number(msg && msg.estimated_entries);
  if (!Number.isFinite(estimated)) estimated = _aiIndexEntryEstimate(_aiSettingsCurrent());
  var ok = await _aiShowRebuildConfirm(estimated);
  if (!ok) {
    _aiSettingsUpdateInFlight = false;
    _aiPendingUpdatePayload = null;
    _aiSetSaveStatus('Save canceled.', 'warning');
    return;
  }
  var payload = _aiPendingUpdatePayload || _aiCollectPayload(true);
  payload.confirm_embedding_rebuild = true;
  _aiSettingsUpdateInFlight = false;
  _aiSendUpdatePayload(payload);
}

function aiIndexJobReceive(msg) {
  _aiIndexStartInFlight = false;
  if (msg && msg.settings) _aiSettingsStore(msg.settings, msg.schema_version);
  else if (msg && msg.job) {
    var settings = _aiSettingsCurrent();
    settings.index.current_job = _aiClone(msg.job);
    settings.index.status = ['queued', 'running'].indexOf(String(msg.job.status || '')) >= 0
      ? 'building'
      : settings.index.status;
    _aiSettingsStore(settings);
  }
  _aiSettingsRefreshIfOpen({ dynamicOnly: true });
}

function _aiDeltaPayloadWithoutOp(op) {
  var payload = Object.assign({}, op || {});
  delete payload.op;
  delete payload.changed_keys;
  return payload;
}

function aiSettingsApplyDelta(op) {
  if (op && op.settings && typeof op.settings === 'object') {
    _aiSettingsStore(op.settings, op.schema_version);
  } else {
    _aiSettingsStore(_aiDeltaPayloadWithoutOp(op), op && op.schema_version);
  }
  _aiSettingsRefreshIfOpen({ preserveSurface: true });
}

function aiIndexStatusApplyDelta(op) {
  var settings = _aiSettingsCurrent();
  if (op && op.settings && typeof op.settings === 'object') {
    _aiSettingsStore(op.settings, op.schema_version);
    _aiSettingsRefreshIfOpen({ dynamicOnly: true });
    return;
  }
  var indexPayload = op && op.index && typeof op.index === 'object'
    ? op.index
    : _aiDeltaPayloadWithoutOp(op || {});
  if (!settings.index || typeof settings.index !== 'object') settings.index = _aiDefaultSettings().index;
  var index = Object.assign({}, settings.index, indexPayload);
  if (indexPayload.counts) index.counts = Object.assign({}, settings.index.counts || {}, indexPayload.counts);
  if (indexPayload.rebuild_warning) {
    index.rebuild_warning = Object.assign({}, settings.index.rebuild_warning || {}, indexPayload.rebuild_warning);
  }
  if (indexPayload.job && !indexPayload.current_job) index.current_job = indexPayload.job;
  settings.index = index;
  if (op && op.embeddings && typeof op.embeddings === 'object') {
    settings.embeddings = Object.assign({}, settings.embeddings || {}, op.embeddings);
  }
  _aiSettingsStore(settings, op && op.schema_version);
  _aiSettingsRefreshIfOpen({ dynamicOnly: true });
}

function aiSummaryStatusApplyDelta(op) {
  var settings = _aiSettingsCurrent();
  if (op && op.settings && typeof op.settings === 'object') {
    _aiSettingsStore(op.settings, op.schema_version);
    _aiSettingsRefreshIfOpen({ dynamicOnly: true });
    return;
  }
  var bootPayload = op && op.boot_summary && typeof op.boot_summary === 'object'
    ? op.boot_summary
    : _aiDeltaPayloadWithoutOp(op || {});
  if (!settings.boot_summary || typeof settings.boot_summary !== 'object') {
    settings.boot_summary = _aiDefaultSettings().boot_summary;
  }
  var boot = Object.assign({}, settings.boot_summary, bootPayload);
  if (bootPayload.counts) boot.counts = Object.assign({}, settings.boot_summary.counts || {}, bootPayload.counts);
  settings.boot_summary = boot;
  if (op && op.metering && typeof op.metering === 'object') {
    settings.metering = Object.assign({}, settings.metering || {}, op.metering);
  }
  _aiSettingsStore(settings, op && op.schema_version);
  _aiSettingsRefreshIfOpen({ dynamicOnly: true });
}

function aiSettingsHandleError(msg) {
  if (!_aiSettingsUpdateInFlight && !_aiIndexStartInFlight) return false;
  var pending = _aiPendingUpdatePayload;
  _aiSettingsUpdateInFlight = false;
  _aiIndexStartInFlight = false;
  _aiPendingUpdatePayload = null;
  var raw = String((msg && (msg.message || msg.error || msg.reason)) || 'AI request failed.');
  var sanitized = raw;
  var secretValues = [];
  var secretPayload = pending && pending.secrets && typeof pending.secrets === 'object'
    ? pending.secrets
    : {};
  for (var provider in secretPayload) {
    if (!Object.prototype.hasOwnProperty.call(secretPayload, provider)) continue;
    var secret = secretPayload[provider] || {};
    if (secret.api_key) secretValues.push(String(secret.api_key));
  }
  for (var i = 0; i < secretValues.length; i++) {
    var value = secretValues[i];
    if (!value) continue;
    var escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    sanitized = sanitized.replace(new RegExp(escaped, 'g'), '[redacted]');
  }
  sanitized = sanitized.replace(/(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*\S+/ig, '$1: [redacted]');
  if (typeof _showToast === 'function') _showToast(sanitized, 'error');
  _aiSetSaveStatus(sanitized, 'error');
  return true;
}

_aiSettingsInstallModalHooks();

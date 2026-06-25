/* Help panel — read-only maintained documentation lookup. */

var _helpState = {
  topics: [],
  listStatus: 'idle',
  listError: '',
  audience: '',
  selectedRef: '',
  detail: null,
  detailStatus: 'idle',
  detailError: '',
  searchDraft: '',
  searchQuery: '',
  searchResults: [],
  searchStatus: 'idle',
  searchError: '',
  searchMessage: '',
  queryDraft: '',
  queryQuestion: '',
  queryResult: null,
  queryStatus: 'idle',
  queryError: '',
  queryMessage: '',
  indexHash: '',
  sourceModel: null,
  expandedSections: {},
};

var _helpRequestSeq = {
  list: 0,
  detail: 0,
  search: 0,
  query: 0,
};

function _helpEsc(value) {
  if (typeof esc === 'function') return esc(value == null ? '' : value);
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _helpJsArg(value) {
  return _helpEsc(JSON.stringify(String(value == null ? '' : value)));
}

function _helpArray(value) {
  return Array.isArray(value) ? value : [];
}

function _helpText(value) {
  return String(value == null ? '' : value).trim();
}

function _helpDate(value) {
  var raw = _helpText(value);
  if (!raw) return 'unknown';
  var parsed = Date.parse(raw);
  if (!Number.isFinite(parsed)) return raw;
  try {
    return new Date(parsed).toISOString().replace(/\.\d{3}Z$/, 'Z');
  } catch (_err) {
    return raw;
  }
}

function _helpRef(item) {
  item = item || {};
  return _helpText(item.path_anchor)
    || _helpText(item.topic_id)
    || _helpText(item.source_path)
    || _helpText(item.id);
}

function _helpPathRef(item) {
  item = item || {};
  return _helpText(item.path_anchor)
    || (_helpText(item.source_path) + (_helpText(item.anchor) ? ('#' + _helpText(item.anchor)) : ''))
    || _helpRef(item);
}

function _helpTopicTitle(item) {
  item = item || {};
  return _helpText(item.title) || _helpText(item.topic_title) || _helpText(item.topic_id) || _helpText(item.source_path) || 'Untitled Help topic';
}

function _helpTopicSummary(item) {
  item = item || {};
  return _helpText(item.summary) || _helpText(item.excerpt) || _helpText(item.body_excerpt) || '';
}

function _helpMarkdown(value) {
  var text = String(value == null ? '' : value);
  if (!text.trim()) return '';
  if (typeof torqueRenderMarkdownMessage === 'function') {
    return torqueRenderMarkdownMessage(text);
  }
  return '<pre><code>' + _helpEsc(text) + '</code></pre>';
}

function _helpPanelRoot() {
  if (typeof document === 'undefined' || !document.getElementById) return null;
  return document.getElementById('panel-help');
}

function _helpPanelVisible() {
  if (typeof _panelAppVisible === 'function') return _panelAppVisible('help');
  return typeof _activePanelApp !== 'undefined' && _activePanelApp === 'help';
}

function _helpCanRender() {
  return !!_helpPanelRoot();
}

function _helpRenderIfPresent() {
  if (_helpCanRender()) renderHelpPanel();
}

async function _helpApi(cmd, payload) {
  if (typeof fetch !== 'function') {
    throw new Error('Help API unavailable: fetch is not supported in this runtime.');
  }
  var body = Object.assign({}, payload || {}, { cmd: cmd });
  var response = await fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  var data = null;
  try {
    data = await response.json();
  } catch (_err) {
    data = null;
  }
  if (!response.ok || !data || data.ok === false) {
    var message = (data && (data.error || data.message)) || response.statusText || ('HTTP ' + response.status);
    throw new Error(message);
  }
  return data.data || data;
}

function helpEnsureLoaded(opts) {
  opts = opts || {};
  if (opts.force || _helpState.listStatus === 'idle') return helpLoadTopics(opts);
  return Promise.resolve(_helpState.topics);
}

async function helpLoadTopics(opts) {
  opts = opts || {};
  var seq = ++_helpRequestSeq.list;
  _helpState.listStatus = 'loading';
  _helpState.listError = '';
  _helpRenderIfPresent();
  try {
    var payload = { audience: _helpState.audience || '' };
    var data = await _helpApi('help_list', payload);
    if (seq !== _helpRequestSeq.list) return _helpState.topics;
    _helpState.topics = _helpArray(data.topics);
    _helpState.listStatus = 'ok';
    _helpState.listError = '';
    _helpState.indexHash = _helpText(data.index_hash) || _helpState.indexHash;
    _helpState.sourceModel = data.source_model || _helpState.sourceModel;
    var selectedStillExists = false;
    if (_helpState.selectedRef) {
      for (var i = 0; i < _helpState.topics.length; i++) {
        var topic = _helpState.topics[i];
        if (_helpRef(topic) === _helpState.selectedRef
            || _helpText(topic.topic_id) === _helpState.selectedRef
            || _helpText(topic.source_path) === _helpState.selectedRef) {
          selectedStillExists = true;
          break;
        }
      }
    }
    if (!selectedStillExists && _helpState.topics.length) {
      _helpState.selectedRef = _helpRef(_helpState.topics[0]);
      _helpState.detail = null;
      _helpState.detailStatus = 'idle';
    }
    _helpRenderIfPresent();
    if (_helpState.selectedRef && (_helpState.detailStatus === 'idle' || opts.force)) {
      await helpSelectReference(_helpState.selectedRef, { preserveList: true });
    }
    return _helpState.topics;
  } catch (err) {
    if (seq !== _helpRequestSeq.list) return _helpState.topics;
    _helpState.listStatus = 'error';
    _helpState.listError = err && err.message ? err.message : String(err || 'Failed to load Help topics.');
    _helpRenderIfPresent();
    return [];
  }
}

async function helpSelectReference(ref, opts) {
  opts = opts || {};
  var nextRef = _helpText(ref);
  if (!nextRef) return null;
  var seq = ++_helpRequestSeq.detail;
  _helpState.selectedRef = nextRef;
  _helpState.detailStatus = 'loading';
  _helpState.detailError = '';
  if (!opts.skipRender) _helpRenderIfPresent();
  try {
    var data = await _helpApi('help_show', { topic: nextRef, max_chars: opts.max_chars || 12000 });
    if (seq !== _helpRequestSeq.detail) return _helpState.detail;
    _helpState.detail = data;
    _helpState.detailStatus = data.status === 'ok' ? 'ok' : (data.status || 'ok');
    _helpState.detailError = data.status === 'ok' ? '' : (data.message || 'Help topic was not found.');
    _helpState.indexHash = _helpText(data.index_hash) || _helpState.indexHash;
    _helpState.sourceModel = data.source_model || _helpState.sourceModel;
    _helpRenderIfPresent();
    return data;
  } catch (err) {
    if (seq !== _helpRequestSeq.detail) return _helpState.detail;
    _helpState.detailStatus = 'error';
    _helpState.detailError = err && err.message ? err.message : String(err || 'Failed to load Help topic.');
    _helpRenderIfPresent();
    return null;
  }
}

function helpSearchInputChanged(value) {
  _helpState.searchDraft = String(value == null ? '' : value);
}

function helpSearchKeydown(event) {
  if (!event) return;
  if (event.key === 'Enter') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    helpRunSearch();
  } else if (event.key === 'Escape') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    helpClearSearch();
  }
}

async function helpRunSearch() {
  var query = _helpText(_helpState.searchDraft);
  _helpState.searchQuery = query;
  _helpState.searchError = '';
  _helpState.searchMessage = '';
  if (!query) {
    _helpState.searchStatus = 'no_query';
    _helpState.searchResults = [];
    _helpState.searchMessage = 'Enter search terms to search maintained Torque Help docs.';
    _helpRenderIfPresent();
    return [];
  }
  var seq = ++_helpRequestSeq.search;
  _helpState.searchStatus = 'loading';
  _helpRenderIfPresent();
  try {
    var data = await _helpApi('help_search', { query: query, limit: 12 });
    if (seq !== _helpRequestSeq.search) return _helpState.searchResults;
    _helpState.searchResults = _helpArray(data.results);
    _helpState.searchStatus = data.status || 'ok';
    _helpState.searchMessage = _helpText(data.message);
    _helpState.indexHash = _helpText(data.index_hash) || _helpState.indexHash;
    _helpState.sourceModel = data.source_model || _helpState.sourceModel;
    _helpRenderIfPresent();
    return _helpState.searchResults;
  } catch (err) {
    if (seq !== _helpRequestSeq.search) return _helpState.searchResults;
    _helpState.searchStatus = 'error';
    _helpState.searchError = err && err.message ? err.message : String(err || 'Help search failed.');
    _helpState.searchResults = [];
    _helpRenderIfPresent();
    return [];
  }
}

function helpClearSearch() {
  _helpState.searchDraft = '';
  _helpState.searchQuery = '';
  _helpState.searchResults = [];
  _helpState.searchStatus = 'idle';
  _helpState.searchError = '';
  _helpState.searchMessage = '';
  _helpRenderIfPresent();
}

function helpQueryInputChanged(value) {
  _helpState.queryDraft = String(value == null ? '' : value);
}

function helpQueryKeydown(event) {
  if (!event) return;
  if (event.key === 'Enter') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    helpRunQuery();
  } else if (event.key === 'Escape') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    helpClearQuery();
  }
}

async function helpRunQuery() {
  var question = _helpText(_helpState.queryDraft);
  _helpState.queryQuestion = question;
  _helpState.queryError = '';
  _helpState.queryMessage = '';
  if (!question) {
    _helpState.queryStatus = 'no_query';
    _helpState.queryResult = null;
    _helpState.queryMessage = 'Ask a question to run an extractive lookup over maintained Torque docs.';
    _helpRenderIfPresent();
    return null;
  }
  var seq = ++_helpRequestSeq.query;
  _helpState.queryStatus = 'loading';
  _helpRenderIfPresent();
  try {
    var data = await _helpApi('help_query', { question: question, limit: 5 });
    if (seq !== _helpRequestSeq.query) return _helpState.queryResult;
    _helpState.queryResult = data;
    _helpState.queryStatus = data.status || 'answered';
    _helpState.queryMessage = _helpText(data.message);
    _helpState.indexHash = _helpText(data.index_hash) || _helpState.indexHash;
    _helpState.sourceModel = data.source_model || _helpState.sourceModel;
    _helpRenderIfPresent();
    return data;
  } catch (err) {
    if (seq !== _helpRequestSeq.query) return _helpState.queryResult;
    _helpState.queryStatus = 'error';
    _helpState.queryError = err && err.message ? err.message : String(err || 'Help query failed.');
    _helpState.queryResult = null;
    _helpRenderIfPresent();
    return null;
  }
}

function helpClearQuery() {
  _helpState.queryDraft = '';
  _helpState.queryQuestion = '';
  _helpState.queryResult = null;
  _helpState.queryStatus = 'idle';
  _helpState.queryError = '';
  _helpState.queryMessage = '';
  _helpRenderIfPresent();
}

function helpAudienceChanged(value) {
  _helpState.audience = _helpText(value);
  _helpState.selectedRef = '';
  _helpState.detail = null;
  _helpState.detailStatus = 'idle';
  helpLoadTopics({ force: true });
}

function helpRefresh() {
  helpLoadTopics({ force: true });
  if (_helpState.searchQuery) helpRunSearch();
  if (_helpState.queryQuestion) helpRunQuery();
}

function helpToggleDetailSection(key) {
  var id = _helpText(key);
  if (!id) return;
  _helpState.expandedSections[id] = !_helpState.expandedSections[id];
  _helpRenderIfPresent();
}

function _helpRenderChips(item) {
  item = item || {};
  var tags = _helpArray(item.audience_tags);
  var html = '';
  for (var i = 0; i < tags.length; i++) {
    html += '<span class="help-chip help-chip-audience">' + _helpEsc(tags[i]) + '</span>';
  }
  if (item.restricted_safe === true) {
    html += '<span class="help-chip help-chip-safe">restricted-safe</span>';
  } else if (item.restricted_safe === false) {
    html += '<span class="help-chip help-chip-warn">not restricted-safe</span>';
  }
  return html;
}

function _helpRenderSourceLine(item, opts) {
  opts = opts || {};
  item = item || {};
  var ref = _helpPathRef(item);
  var updated = _helpText(item.updated_at) ? _helpDate(item.updated_at) : '';
  var pieces = [];
  if (ref) pieces.push('<code>' + _helpEsc(ref) + '</code>');
  if (updated) pieces.push('<span>updated ' + _helpEsc(updated) + '</span>');
  if (opts.hashes && _helpText(item.source_hash)) pieces.push('<span>source ' + _helpEsc(item.source_hash) + '</span>');
  return pieces.length ? '<div class="help-source-line">' + pieces.join('') + '</div>' : '';
}

function _helpRenderTopicCard(item, kind, index) {
  item = item || {};
  var ref = _helpRef(item);
  var selected = ref && ref === _helpState.selectedRef;
  var title = _helpTopicTitle(item);
  var summary = _helpTopicSummary(item);
  var score = Number(item.score);
  var meta = kind === 'search' && Number.isFinite(score)
    ? '<span class="help-score">score ' + _helpEsc(score.toFixed(1)) + '</span>'
    : '';
  return ''
    + '<button type="button" class="help-topic-card' + (selected ? ' selected' : '') + '"'
    + ' data-help-ref="' + _helpEsc(ref) + '"'
    + ' onclick="helpSelectReference(' + _helpJsArg(ref) + ')">'
    + '  <span class="help-topic-card-title">' + _helpEsc(title) + '</span>'
    + '  <span class="help-topic-card-summary">' + _helpEsc(summary || 'No summary available.') + '</span>'
    + '  ' + _helpRenderSourceLine(item)
    + '  <span class="help-topic-card-footer">'
    + '    <span>' + _helpEsc(kind === 'search' ? ('result #' + (index + 1)) : (_helpText(item.topic_id) || 'topic')) + '</span>'
    +      meta
    + '  </span>'
    + '  <span class="help-chip-row">' + _helpRenderChips(item) + '</span>'
    + '</button>';
}

function _helpRenderTopicBrowser() {
  var html = '<div class="help-browser" id="help-browser-scroll">';
  html += '<section class="help-browser-section">'
    + '<div class="help-section-heading">Topics</div>';
  if (_helpState.listStatus === 'loading') {
    html += '<div class="help-state help-loading">Loading Help topics…</div>';
  } else if (_helpState.listStatus === 'error') {
    html += '<div class="help-state help-error">' + _helpEsc(_helpState.listError || 'Failed to load Help topics.') + '</div>';
  } else if (!_helpState.topics.length) {
    html += '<div class="help-state help-empty">No maintained Torque Help topics matched this audience filter.</div>';
  } else {
    html += '<div class="help-topic-list">';
    for (var i = 0; i < _helpState.topics.length; i++) {
      html += _helpRenderTopicCard(_helpState.topics[i], 'topic', i);
    }
    html += '</div>';
  }
  html += '</section>';

  html += '<section class="help-browser-section help-search-results">'
    + '<div class="help-section-heading">Search results</div>';
  if (_helpState.searchStatus === 'idle') {
    html += '<div class="help-state help-empty">Search results appear here in the order returned by Help.</div>';
  } else if (_helpState.searchStatus === 'loading') {
    html += '<div class="help-state help-loading">Searching maintained docs…</div>';
  } else if (_helpState.searchStatus === 'error') {
    html += '<div class="help-state help-error">' + _helpEsc(_helpState.searchError || 'Help search failed.') + '</div>';
  } else if (_helpState.searchStatus === 'no_query') {
    html += '<div class="help-state help-empty">' + _helpEsc(_helpState.searchMessage || 'Enter search terms to search maintained Torque Help docs.') + '</div>';
  } else if (!_helpState.searchResults.length) {
    html += '<div class="help-state help-empty">' + _helpEsc(_helpState.searchMessage || 'No maintained Torque Help docs matched. Try broader terms or inspect topics.') + '</div>';
  } else {
    html += '<div class="help-search-meta">' + _helpEsc(_helpState.searchResults.length) + ' result(s) for “' + _helpEsc(_helpState.searchQuery) + '”</div>';
    html += '<div class="help-topic-list">';
    for (var j = 0; j < _helpState.searchResults.length; j++) {
      html += _helpRenderTopicCard(_helpState.searchResults[j], 'search', j);
    }
    html += '</div>';
  }
  html += '</section>';
  html += '</div>';
  return html;
}

function _helpRenderSections(topic) {
  var sections = _helpArray(topic && topic.sections);
  if (!sections.length) return '<div class="help-muted">No section index is available for this topic.</div>';
  var html = '<div class="help-section-index">';
  for (var i = 0; i < sections.length; i++) {
    var section = sections[i] || {};
    var key = _helpText(section.id) || _helpText(section.path_anchor) || ('section-' + i);
    var expanded = !!_helpState.expandedSections[key];
    var ref = _helpText(section.path_anchor) || _helpText(section.source_path);
    html += '<div class="help-section-row' + (expanded ? ' expanded' : '') + '">'
      + '<button type="button" class="help-section-toggle" onclick="helpToggleDetailSection(' + _helpJsArg(key) + ')">'
      + '<span class="help-section-caret">' + (expanded ? '▾' : '▸') + '</span>'
      + '<span class="help-section-title">' + _helpEsc(section.title || 'Untitled section') + '</span>'
      + '<span class="help-section-lines">lines ' + _helpEsc(section.line_start || '?') + '–' + _helpEsc(section.line_end || '?') + '</span>'
      + '</button>';
    if (expanded) {
      html += '<div class="help-section-expanded">'
        + '<div class="help-source-line"><code>' + _helpEsc(ref) + '</code></div>'
        + '<button type="button" class="btn-secondary help-inline-action" onclick="helpSelectReference(' + _helpJsArg(ref) + ')">Open section</button>'
        + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _helpRenderExamples(topic) {
  var examples = _helpArray(topic && topic.examples);
  if (!examples.length) return '<div class="help-muted">No examples were extracted for this topic.</div>';
  var html = '<div class="help-examples">';
  for (var i = 0; i < examples.length; i++) {
    html += '<pre><code>' + _helpEsc(examples[i]) + '</code></pre>';
  }
  html += '</div>';
  return html;
}

function _helpRenderFreshness(topic) {
  topic = topic || {};
  var model = _helpState.sourceModel || topic.source_model || {};
  var sourcePaths = _helpArray(model.source_paths);
  return '<details class="help-freshness">'
    + '<summary>Freshness and source model</summary>'
    + '<div class="help-freshness-grid">'
    + '<span>Source</span><code>' + _helpEsc(_helpPathRef(topic) || topic.source_path || '—') + '</code>'
    + '<span>Updated</span><code>' + _helpEsc(_helpDate(topic.updated_at)) + '</code>'
    + '<span>Source hash</span><code>' + _helpEsc(topic.source_hash || '—') + '</code>'
    + '<span>Index hash</span><code>' + _helpEsc(_helpState.indexHash || topic.index_hash || '—') + '</code>'
    + '<span>Cache</span><code>' + _helpEsc(model.cache || '—') + '</code>'
    + '<span>Allow-list</span><code>' + _helpEsc(model.allowlist || 'maintained Torque docs') + '</code>'
    + '<span>Indexed paths</span><code>' + _helpEsc(sourcePaths.length ? (sourcePaths.length + ' maintained source(s)') : '—') + '</code>'
    + '</div>'
    + '</details>';
}

function _helpRenderDetail() {
  var html = '<div class="help-detail" id="help-detail-scroll">';
  if (!_helpState.selectedRef) {
    html += '<div class="help-state help-empty">Select a Help topic or search result to read maintained docs.</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'loading') {
    html += '<div class="help-state help-loading">Loading Help topic…</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'error') {
    html += '<div class="help-state help-error">' + _helpEsc(_helpState.detailError || 'Failed to load Help topic.') + '</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'not_found') {
    html += '<div class="help-state help-empty">' + _helpEsc(_helpState.detailError || 'No Torque Help topic matched this reference.') + '</div>';
    return html + '</div>';
  }
  var topic = _helpState.detail || null;
  if (!topic) {
    html += '<div class="help-state help-empty">Select a Help topic or search result to read maintained docs.</div>';
    return html + '</div>';
  }
  html += '<article class="help-topic-detail">'
    + '<div class="help-detail-topline">'
    + '<h2>' + _helpEsc(_helpTopicTitle(topic)) + '</h2>'
    + '<span class="help-chip-row">' + _helpRenderChips(topic) + '</span>'
    + '</div>'
    + _helpRenderSourceLine(topic, { hashes: false });
  if (_helpTopicSummary(topic)) {
    html += '<p class="help-detail-summary">' + _helpEsc(_helpTopicSummary(topic)) + '</p>';
  }
  if (topic.truncated) {
    html += '<div class="help-state help-empty help-truncated">Showing a bounded excerpt from this source. Search or open a section for narrower context.</div>';
  }
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Excerpt</div>'
    + '<div class="help-markdown torque-markdown">' + _helpMarkdown(topic.body_excerpt || topic.excerpt || '') + '</div>'
    + '</section>';
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Sections</div>'
    + _helpRenderSections(topic)
    + '</section>';
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Examples</div>'
    + _helpRenderExamples(topic)
    + '</section>';
  html += _helpRenderFreshness(topic);
  html += '</article></div>';
  return html;
}

function _helpRenderQueryPanel() {
  var html = '<section class="help-query-panel" id="help-query-scroll">'
    + '<div class="help-query-title">Ask Help</div>'
    + '<div class="help-query-subtitle">Extractive lookup over maintained Torque docs. Answers cite source paths and do not inspect board, journal, user, or runtime state.</div>'
    + '<div class="help-query-row">'
    + '<input id="help-query-input" class="help-query-input" value="' + _helpEsc(_helpState.queryDraft) + '" '
    + 'placeholder="Ask a docs question…" autocomplete="off" '
    + 'oninput="helpQueryInputChanged(this.value)" onkeydown="helpQueryKeydown(event)">'
    + '<button type="button" class="btn-primary" onclick="helpRunQuery()">Ask</button>'
    + '<button type="button" class="btn-secondary" onclick="helpClearQuery()">Clear</button>'
    + '</div>';
  if (_helpState.queryStatus === 'idle') {
    html += '<div class="help-state help-empty">Ask Help returns extractive answers with visible sources.</div>';
  } else if (_helpState.queryStatus === 'loading') {
    html += '<div class="help-state help-loading">Looking up maintained docs…</div>';
  } else if (_helpState.queryStatus === 'error') {
    html += '<div class="help-state help-error">' + _helpEsc(_helpState.queryError || 'Help query failed.') + '</div>';
  } else if (_helpState.queryStatus === 'no_query') {
    html += '<div class="help-state help-empty">' + _helpEsc(_helpState.queryMessage || 'Ask a question to run Help lookup.') + '</div>';
  } else {
    var result = _helpState.queryResult || {};
    var noAnswer = _helpState.queryStatus === 'no_answer';
    html += '<div class="help-query-answer' + (noAnswer ? ' no-answer' : '') + '">'
      + '<div class="help-query-answer-label">' + (noAnswer ? 'No answer found' : 'Extractive answer') + '</div>'
      + '<div class="help-markdown torque-markdown">' + _helpMarkdown(result.answer || _helpState.queryMessage || '') + '</div>';
    var sources = _helpArray(result.sources);
    if (sources.length) {
      html += '<div class="help-query-sources"><div class="help-query-answer-label">Sources</div>';
      for (var i = 0; i < sources.length; i++) {
        var source = sources[i] || {};
        var ref = _helpPathRef(source);
        html += '<button type="button" class="help-source-button" onclick="helpSelectReference(' + _helpJsArg(ref) + ')">'
          + '<span>' + _helpEsc(source.title || ref || 'Source') + '</span>'
          + '<code>' + _helpEsc(ref || source.source_path || '—') + '</code>'
          + '</button>';
      }
      html += '</div>';
    } else if (noAnswer) {
      html += '<div class="help-state help-empty">Try broader search terms or inspect the topic list.</div>';
    }
    html += '</div>';
  }
  html += '</section>';
  return html;
}

function _helpRenderHeader() {
  var index = _helpState.indexHash ? ('index ' + _helpState.indexHash) : 'index loading';
  return '<div class="help-header">'
    + '<div class="help-header-copy">'
    + '<div class="help-title">Help</div>'
    + '<div class="help-subtitle">Browse, search, and query maintained Torque documentation.</div>'
    + '</div>'
    + '<div class="help-header-actions">'
    + '<span class="help-index-chip">' + _helpEsc(index) + '</span>'
    + '<button type="button" class="btn-secondary" onclick="helpRefresh()">Refresh</button>'
    + '</div>'
    + '</div>';
}

function _helpRenderToolbar() {
  var audiences = ['', 'user', 'operator', 'agent', 'worker', 'engineer', 'architect', 'maintainer'];
  var select = '<select id="help-audience-select" class="help-audience-select" onchange="helpAudienceChanged(this.value)">';
  for (var i = 0; i < audiences.length; i++) {
    var value = audiences[i];
    select += '<option value="' + _helpEsc(value) + '"' + (value === _helpState.audience ? ' selected' : '') + '>'
      + _helpEsc(value || 'All audiences') + '</option>';
  }
  select += '</select>';
  return '<div class="help-toolbar">'
    + '<input id="help-search-input" class="help-search-input" value="' + _helpEsc(_helpState.searchDraft) + '" '
    + 'placeholder="Search maintained docs…" autocomplete="off" '
    + 'oninput="helpSearchInputChanged(this.value)" onkeydown="helpSearchKeydown(event)">'
    + '<button type="button" class="btn-primary" onclick="helpRunSearch()">Search</button>'
    + '<button type="button" class="btn-secondary" onclick="helpClearSearch()">Clear</button>'
    + select
    + '</div>';
}

function renderHelpPanel() {
  var root = _helpPanelRoot();
  if (!root) return;
  if (_helpState.listStatus === 'idle') helpEnsureLoaded();
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(root, {
      scrollSelectors: ['#help-workspace-scroll', '#help-browser-scroll', '#help-detail-scroll', '#help-query-scroll'],
    });
  }
  root.innerHTML = '<div class="help-panel">'
    + _helpRenderHeader()
    + _helpRenderToolbar()
    + _helpRenderQueryPanel()
    + '<div class="help-workspace" id="help-workspace-scroll">'
    + _helpRenderTopicBrowser()
    + _helpRenderDetail()
    + '</div>'
    + '</div>';
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot);
  }
}

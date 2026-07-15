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
  browserOpen: false,
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

function _helpLiveInputValue(id, fallback) {
  if (typeof document === 'undefined' || !document.getElementById) return String(fallback == null ? '' : fallback);
  var el = document.getElementById(id);
  if (!el || !('value' in el)) return String(fallback == null ? '' : fallback);
  return String(el.value == null ? '' : el.value);
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

function _helpFocusElementById(id, opts) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var el = document.getElementById(id);
  if (!el || typeof el.focus !== 'function') return;
  try { el.focus(opts || { preventScroll: true }); } catch (_err) { try { el.focus(); } catch (_err2) {} }
}

function _helpBringDetailIntoView(opts) {
  opts = opts || {};
  if (typeof document === 'undefined' || !document.getElementById) return;
  var inBrowser = opts.target === 'browser' || opts.browser === true;
  var detail = document.getElementById(inBrowser ? 'help-browser-detail-scroll' : 'help-detail-scroll');
  if (!detail) return;
  var workspace = inBrowser ? document.getElementById('help-topic-browser-detail-pane') : document.getElementById('help-workspace-scroll');
  try { if (workspace) workspace.scrollTop = 0; } catch (_err0) {}
  try { detail.scrollTop = 0; } catch (_err) {}
  if (inBrowser && opts.scrollIntoView !== true) return;
  var anchor = document.getElementById(inBrowser ? 'help-browser-selected-detail-anchor' : 'help-selected-detail-anchor') || detail;
  if (anchor && typeof anchor.scrollIntoView === 'function') {
    try { anchor.scrollIntoView({ block: 'start', inline: 'nearest' }); } catch (_err2) { try { anchor.scrollIntoView(); } catch (_err3) {} }
  }
}

function _helpBringQueryResultIntoView() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var result = document.getElementById('help-query-result-scroll');
  if (!result) return;
  try { result.scrollTop = 0; } catch (_err) {}
  if (typeof result.scrollIntoView === 'function') {
    try { result.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (_err2) { try { result.scrollIntoView(); } catch (_err3) {} }
  }
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
      await helpSelectReference(_helpState.selectedRef, { preserveList: true, scrollDetail: false, keepBrowserOpen: true });
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
  if (_helpState.browserOpen && !opts.keepBrowserOpen) _helpState.browserOpen = false;
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
    if (opts.scrollDetail !== false) _helpBringDetailIntoView({ target: opts.detailTarget || (opts.browser ? 'browser' : 'main') });
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

function helpSearchSubmit(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  return helpRunSearch();
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
  var rawQuery = _helpLiveInputValue('help-search-input', _helpState.searchDraft);
  _helpState.searchDraft = rawQuery;
  var query = _helpText(rawQuery);
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

function helpQuerySubmit(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  return helpRunQuery();
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

function _helpBindQueryControls() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var form = document.getElementById('help-query-form');
  var input = document.getElementById('help-query-input');
  var ask = document.getElementById('help-query-ask-button');
  var clear = document.getElementById('help-query-clear-button');
  if (form && typeof form.addEventListener === 'function') {
    form.addEventListener('submit', helpQuerySubmit);
  }
  if (input && typeof input.addEventListener === 'function') {
    input.addEventListener('input', function () { helpQueryInputChanged(input.value); });
    input.addEventListener('keydown', helpQueryKeydown);
  }
  if (ask && typeof ask.addEventListener === 'function') {
    ask.addEventListener('click', helpQuerySubmit);
  }
  if (clear && typeof clear.addEventListener === 'function') {
    clear.addEventListener('click', helpClearQuery);
  }
}

function _helpBindNavigationControls() {
  var root = _helpPanelRoot();
  if (!root || typeof root.querySelectorAll !== 'function') return;
  var nodes = root.querySelectorAll('[data-help-ref]');
  for (var i = 0; i < nodes.length; i++) {
    var node = nodes[i];
    if (!node || !node.dataset || node.dataset.helpBound === '1' || typeof node.addEventListener !== 'function') continue;
    node.dataset.helpBound = '1';
    node.addEventListener('click', function (event) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      var ref = this && this.dataset ? this.dataset.helpRef : '';
      if (!ref) return;
      if (this.dataset.helpTarget === 'browser') {
        helpSelectBrowserReference(ref);
      } else {
        helpSelectReference(ref);
      }
    });
  }
}

async function helpRunQuery() {
  var rawQuestion = _helpLiveInputValue('help-query-input', _helpState.queryDraft);
  _helpState.queryDraft = rawQuestion;
  var question = _helpText(rawQuestion);
  _helpState.queryQuestion = question;
  _helpState.queryError = '';
  _helpState.queryMessage = '';
  if (!question) {
    _helpState.queryStatus = 'no_query';
    _helpState.queryResult = null;
    _helpState.queryMessage = 'Ask a question to run an extractive lookup over maintained Torque docs.';
    _helpRenderIfPresent();
    _helpBringQueryResultIntoView();
    return null;
  }
  var seq = ++_helpRequestSeq.query;
  _helpState.queryStatus = 'loading';
  _helpRenderIfPresent();
  _helpBringQueryResultIntoView();
  try {
    var data = await _helpApi('help_query', { question: question, limit: 5 });
    if (seq !== _helpRequestSeq.query) return _helpState.queryResult;
    _helpState.queryResult = data;
    _helpState.queryStatus = data.status || 'answered';
    _helpState.queryMessage = _helpText(data.message);
    _helpState.indexHash = _helpText(data.index_hash) || _helpState.indexHash;
    _helpState.sourceModel = data.source_model || _helpState.sourceModel;
    _helpRenderIfPresent();
    _helpBringQueryResultIntoView();
    return data;
  } catch (err) {
    if (seq !== _helpRequestSeq.query) return _helpState.queryResult;
    _helpState.queryStatus = 'error';
    _helpState.queryError = err && err.message ? err.message : String(err || 'Help query failed.');
    _helpState.queryResult = null;
    _helpRenderIfPresent();
    _helpBringQueryResultIntoView();
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

function helpOpenTopicBrowser() {
  _helpState.browserOpen = true;
  _helpRenderIfPresent();
  helpEnsureLoaded();
  _helpFocusElementById('help-search-input', { preventScroll: true });
}

function helpCloseTopicBrowser() {
  _helpState.browserOpen = false;
  _helpRenderIfPresent();
  _helpFocusElementById('help-topic-browser-button', { preventScroll: true });
}

function helpTopicBrowserKeydown(event) {
  if (event && event.key === 'Escape') {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
    helpCloseTopicBrowser();
  }
}

function helpSelectBrowserReference(ref) {
  return helpSelectReference(ref, { keepBrowserOpen: true, detailTarget: 'browser' });
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
    + ' data-help-target="' + (_helpState.browserOpen ? 'browser' : 'main') + '">'
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
    html += '<div class="help-state help-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading Help topics…</div>';
  } else if (_helpState.listStatus === 'error') {
    html += '<div class="help-state help-error ui-state ui-state--error ui-state--compact" role="alert">' + _helpEsc(_helpState.listError || 'Failed to load Help topics. Refresh Help to try again.') + '</div>';
  } else if (!_helpState.topics.length) {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">No maintained Torque Help topics matched this audience filter.</div>';
  } else {
    html += '<div class="help-topic-list" id="help-topic-list-scroll">';
    for (var i = 0; i < _helpState.topics.length; i++) {
      html += _helpRenderTopicCard(_helpState.topics[i], 'topic', i);
    }
    html += '</div>';
  }
  html += '</section>';

  html += '<section class="help-browser-section help-search-results">'
    + '<div class="help-section-heading">Search results</div>';
  if (_helpState.searchStatus === 'idle') {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">Search results appear here in the order returned by Help.</div>';
  } else if (_helpState.searchStatus === 'loading') {
    html += '<div class="help-state help-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Searching maintained docs…</div>';
  } else if (_helpState.searchStatus === 'error') {
    html += '<div class="help-state help-error ui-state ui-state--error ui-state--compact" role="alert">' + _helpEsc(_helpState.searchError || 'Help search failed. Adjust the query or try again.') + '</div>';
  } else if (_helpState.searchStatus === 'no_query') {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">' + _helpEsc(_helpState.searchMessage || 'Enter search terms to search maintained Torque Help docs.') + '</div>';
  } else if (!_helpState.searchResults.length) {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">' + _helpEsc(_helpState.searchMessage || 'No maintained Torque Help docs matched. Try broader terms or inspect topics.') + '</div>';
  } else {
    html += '<div class="help-search-meta">' + _helpEsc(_helpState.searchResults.length) + ' result(s) for “' + _helpEsc(_helpState.searchQuery) + '”</div>';
    html += '<div class="help-topic-list" id="help-search-results-scroll">';
    for (var j = 0; j < _helpState.searchResults.length; j++) {
      html += _helpRenderTopicCard(_helpState.searchResults[j], 'search', j);
    }
    html += '</div>';
  }
  html += '</section>';
  html += '</div>';
  return html;
}

function _helpRenderAudienceSelect() {
  var audiences = ['', 'user', 'operator', 'agent', 'worker', 'engineer', 'architect', 'maintainer'];
  var select = '<select id="help-audience-select" class="help-audience-select" onchange="helpAudienceChanged(this.value)">';
  for (var i = 0; i < audiences.length; i++) {
    var value = audiences[i];
    select += '<option value="' + _helpEsc(value) + '"' + (value === _helpState.audience ? ' selected' : '') + '>'
      + _helpEsc(value || 'All audiences') + '</option>';
  }
  select += '</select>';
  return select;
}

function _helpRenderTopicBrowserModal() {
  if (!_helpState.browserOpen) return '';
  return '<div id="modal-help-topic-browser" class="overlay help-topic-browser-overlay visible" onclick="if(event.target===this) helpCloseTopicBrowser()" onkeydown="helpTopicBrowserKeydown(event)" tabindex="-1">'
    + '<div class="modal ui-modal ui-modal--full ui-modal--tall ui-modal--structured help-topic-browser-modal" role="dialog" aria-modal="true" aria-labelledby="help-topic-browser-title">'
    + '<div class="help-topic-browser-head ui-modal__header ui-modal__header--bordered">'
    + '<div><h2 id="help-topic-browser-title" class="ui-modal__title">Browse Help topics</h2>'
    + '<div class="modal-summary ui-modal__subtitle">Search, filter, and select maintained Torque Help topics. Selecting a card keeps the detail pane inside this browser.</div></div>'
    + '</div>'
    + '<div class="help-topic-browser-workspace ui-modal__body">'
    + _helpRenderBrowserControls()
    + '<div class="help-topic-browser-body">'
    + '<div class="help-topic-browser-results-pane">' + _helpRenderTopicBrowser() + '</div>'
    + '<div class="help-topic-browser-detail-pane" id="help-topic-browser-detail-pane">' + _helpRenderDetail({ browser: true }) + '</div>'
    + '</div>'
    + '</div>'
    + '<div class="modal-actions ui-modal__footer"><button type="button" class="btn-cancel help-topic-browser-close" onclick="helpCloseTopicBrowser()">Close</button></div>'
    + '</div>';
}

function _helpRenderBrowserControls() {
  return '<form class="help-toolbar help-browser-toolbar ui-toolbar ui-toolbar--bordered" onsubmit="return helpSearchSubmit(event)">'
    + '<input id="help-search-input" class="help-search-input" value="' + _helpEsc(_helpState.searchDraft) + '" '
    + 'placeholder="Search maintained docs…" autocomplete="off" '
    + 'oninput="helpSearchInputChanged(this.value)" onkeydown="helpSearchKeydown(event)">'
    + '<button type="submit" class="btn-primary">Search</button>'
    + '<button type="button" class="btn-secondary" onclick="helpClearSearch()">Clear</button>'
    + _helpRenderAudienceSelect()
    + '</form>';
}

function _helpRenderTopicLauncher() {
  var selected = _helpState.detail || null;
  var title = selected ? _helpTopicTitle(selected) : (_helpState.selectedRef || 'No topic selected');
  var search = _helpState.searchQuery
    ? (_helpState.searchResults.length + ' result(s) for “' + _helpState.searchQuery + '”')
    : 'Search and topic navigation open in a larger popup.';
  return '<div class="help-topic-launcher">'
    + '<div class="help-topic-launcher-copy">'
    + '<div class="help-topic-launcher-label">Current topic</div>'
    + '<div class="help-topic-launcher-title">' + _helpEsc(title) + '</div>'
    + '<div class="help-topic-launcher-meta">' + _helpEsc(search) + '</div>'
    + '</div>'
    + '<button id="help-topic-browser-button" type="button" class="btn-primary" onclick="helpOpenTopicBrowser()">Browse topics…</button>'
    + '</div>';
}

function _helpRenderSections(topic, opts) {
  opts = opts || {};
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
        + '<button type="button" class="btn-secondary help-inline-action" data-help-ref="' + _helpEsc(ref) + '" data-help-target="' + (opts.browser ? 'browser' : 'main') + '">Open section</button>'
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

function _helpRenderDetail(opts) {
  opts = opts || {};
  var detailId = opts.browser ? 'help-browser-detail-scroll' : 'help-detail-scroll';
  var anchorId = opts.browser ? 'help-browser-selected-detail-anchor' : 'help-selected-detail-anchor';
  var html = '<div class="help-detail" id="' + detailId + '">';
  if (!_helpState.selectedRef) {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">Select a Help topic or search result to read maintained docs.</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'loading') {
    html += '<div class="help-state help-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading Help topic…</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'error') {
    html += '<div class="help-state help-error ui-state ui-state--error ui-state--compact" role="alert">' + _helpEsc(_helpState.detailError || 'Failed to load Help topic. Select it again to retry.') + '</div>';
    return html + '</div>';
  }
  if (_helpState.detailStatus === 'not_found') {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">' + _helpEsc(_helpState.detailError || 'No Torque Help topic matched this reference.') + '</div>';
    return html + '</div>';
  }
  var topic = _helpState.detail || null;
  if (!topic) {
    html += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">Select a Help topic or search result to read maintained docs.</div>';
    return html + '</div>';
  }
  html += '<article class="help-topic-detail" id="' + anchorId + '" tabindex="-1">'
    + '<div class="help-detail-topline">'
    + '<h2>' + _helpEsc(_helpTopicTitle(topic)) + '</h2>'
    + '<span class="help-chip-row">' + _helpRenderChips(topic) + '</span>'
    + '</div>'
    + _helpRenderSourceLine(topic, { hashes: false });
  if (_helpTopicSummary(topic)) {
    html += '<p class="help-detail-summary">' + _helpEsc(_helpTopicSummary(topic)) + '</p>';
  }
  if (topic.truncated) {
    html += '<div class="help-state help-empty help-truncated ui-state ui-state--note ui-state--compact">Showing a bounded excerpt from this source. Search or open a section for narrower context.</div>';
  }
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Excerpt</div>'
    + '<div class="help-markdown torque-markdown">' + _helpMarkdown(topic.body_excerpt || topic.excerpt || '') + '</div>'
    + '</section>';
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Sections</div>'
    + _helpRenderSections(topic, opts)
    + '</section>';
  html += '<section class="help-detail-block">'
    + '<div class="help-detail-block-title">Examples</div>'
    + _helpRenderExamples(topic)
    + '</section>';
  html += _helpRenderFreshness(topic);
  html += '</article></div>';
  return html;
}

function _helpRenderQueryResultBody() {
  var body = '';
  if (_helpState.queryStatus === 'idle') {
    body += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">Ask Help returns extractive answers with visible sources.</div>';
  } else if (_helpState.queryStatus === 'loading') {
    body += '<div class="help-state help-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Looking up maintained docs…</div>';
  } else if (_helpState.queryStatus === 'error') {
    body += '<div class="help-state help-error ui-state ui-state--error ui-state--compact" role="alert">' + _helpEsc(_helpState.queryError || 'Help query failed. Adjust the question or try again.') + '</div>';
  } else if (_helpState.queryStatus === 'no_query') {
    body += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">' + _helpEsc(_helpState.queryMessage || 'Ask a question to run Help lookup.') + '</div>';
  } else {
    var result = _helpState.queryResult || {};
    var noAnswer = _helpState.queryStatus === 'no_answer';
    body += '<div class="help-query-answer' + (noAnswer ? ' no-answer' : '') + '">'
      + '<div class="help-query-answer-label">' + (noAnswer ? 'No answer found' : 'Extractive answer') + '</div>'
      + '<div class="help-markdown torque-markdown">' + _helpMarkdown(result.answer || _helpState.queryMessage || '') + '</div>';
    var sources = _helpArray(result.sources);
    if (sources.length) {
      body += '<div class="help-query-sources"><div class="help-query-answer-label">Sources</div>';
      for (var i = 0; i < sources.length; i++) {
        var source = sources[i] || {};
        var ref = _helpPathRef(source);
        body += '<button type="button" class="help-source-button" data-help-ref="' + _helpEsc(ref) + '" data-help-target="main">'
          + '<span>' + _helpEsc(source.title || ref || 'Source') + '</span>'
          + '<code>' + _helpEsc(ref || source.source_path || '—') + '</code>'
          + '</button>';
      }
      body += '</div>';
    } else if (noAnswer) {
      body += '<div class="help-state help-empty ui-state ui-state--empty ui-state--compact">Try broader search terms or inspect the topic browser.</div>';
    }
    body += '</div>';
  }
  return body;
}

function _helpRenderQueryPanel() {
  return '<section class="help-query-panel" id="help-query-scroll">'
    + '<div class="help-query-title">Ask Help</div>'
    + '<div class="help-query-subtitle">Extractive lookup over maintained Torque docs. Answers cite source paths and do not inspect board, journal, user, or runtime state.</div>'
    + '<form class="help-query-row ui-toolbar" id="help-query-form">'
    + '<input id="help-query-input" class="help-query-input" value="' + _helpEsc(_helpState.queryDraft) + '" '
    + 'placeholder="Ask a docs question…" autocomplete="off">'
    + '<button type="button" class="btn-primary" id="help-query-ask-button">Ask</button>'
    + '<button type="button" class="btn-secondary" id="help-query-clear-button">Clear</button>'
    + '</form>'
    + '</section>';
}

function _helpRenderQueryResultCard() {
  return '<section class="help-query-result-card help-query-result-scroll" id="help-query-result-scroll" aria-live="polite">'
    + '<div class="help-query-answer-label">Ask Help result</div>'
    + _helpRenderQueryResultBody()
    + '</section>';
}

function _helpRenderHeader() {
  var index = _helpState.indexHash ? ('index ' + _helpState.indexHash) : 'index loading';
  return '<div class="help-header ui-panel-header ui-panel-header--surface">'
    + '<div class="help-header-copy ui-panel-header__copy">'
    + '<div class="help-title ui-panel-header__title">Help</div>'
    + '<div class="help-subtitle ui-panel-header__subtitle">Browse, search, and query maintained Torque documentation.</div>'
    + '</div>'
    + '<div class="help-header-actions ui-panel-header__actions">'
    + '<span class="help-index-chip">' + _helpEsc(index) + '</span>'
    + '<button type="button" class="btn-secondary" onclick="helpRefresh()">Refresh</button>'
    + '</div>'
    + '</div>';
}

function renderHelpPanel() {
  var root = _helpPanelRoot();
  if (!root) return;
  if (_helpState.listStatus === 'idle') helpEnsureLoaded();
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(root, {
      scrollSelectors: ['#help-workspace-scroll', '#help-browser-scroll', '#help-topic-list-scroll', '#help-search-results-scroll', '#help-detail-scroll', '#help-browser-detail-scroll', '#help-query-scroll', '#help-query-result-scroll'],
    });
  }
  root.innerHTML = '<div class="help-panel">'
    + _helpRenderHeader()
    + _helpRenderTopicLauncher()
    + _helpRenderQueryPanel()
    + _helpRenderQueryResultCard()
    + '<div class="help-workspace" id="help-workspace-scroll">'
    + _helpRenderDetail()
    + '</div>'
    + _helpRenderTopicBrowserModal()
    + '</div>';
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(root, snapshot);
  }
  _helpBindQueryControls();
  _helpBindNavigationControls();
}

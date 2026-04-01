/* ------------------------------------------------------------------ */
/* Events panel app — attention area + activity log                    */
/* ------------------------------------------------------------------ */

var _eventsFilterByGroup = true;
var _eventsScrollTop = 0;
var _eventsExpandedEntries = {};
var _eventsLoading = false;
var _eventsHasMore = true;
var _eventsOldestId = 0;
var _eventsSearchQuery = '';
var _eventsKindFilter = 'all';
var _eventsDismissedIds = new Set();
var _eventsSearchDebounce = null;
var _eventsSearchHadFocus = false;

/* ---- Helpers -------------------------------------------------------- */

function _eventsCurrentGroup() {
  if (!_eventsFilterByGroup) return null;
  return (typeof _currentGroup === 'function') ? _currentGroup() : null;
}

function _eventsFormatTime(ts) {
  var d = new Date(ts * 1000);
  var now = Date.now();
  var diffMs = now - d.getTime();
  var diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 0) diffSec = 0;
  if (diffSec < 60) return 'now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var hh = String(d.getHours()).padStart(2, '0');
  var mm = String(d.getMinutes()).padStart(2, '0');
  return months[d.getMonth()] + ' ' + d.getDate() + ', ' + hh + ':' + mm;
}

function _eventsDateLabel(ts) {
  var d = new Date(ts * 1000);
  var now = new Date();
  var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  var diff = Math.round((today - day) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return months[d.getMonth()] + ' ' + d.getDate();
}

var _eventsKindGroups = {
  errors: ['agent_error', 'agent_blocked'],
  tasks: ['task_dispatched', 'task_completed', 'task_derived', 'ask_created', 'ask_resolved'],
  lifecycle: ['agent_started', 'agent_finished', 'agent_renamed', 'agent_waiting', 'agent_progress']
};

function _eventsMatchesFilters(evt) {
  if (_eventsKindFilter !== 'all') {
    var kinds = _eventsKindGroups[_eventsKindFilter];
    if (kinds && kinds.indexOf(evt.kind) < 0) return false;
  }
  if (_eventsSearchQuery) {
    var q = _eventsSearchQuery.toLowerCase();
    var name = (evt.agent_name || '').toLowerCase();
    var msg = (evt.message || '').toLowerCase();
    if (name.indexOf(q) < 0 && msg.indexOf(q) < 0) return false;
  }
  return true;
}

function _eventsKindIcon(kind) {
  switch (kind) {
    case 'agent_started':  return '\u25B6';  // play
    case 'agent_finished': return '\u2713';  // check
    case 'agent_error':    return '\u2716';  // x
    case 'agent_blocked':  return '\u26D4';  // no entry
    case 'task_dispatched': return '\u2192'; // arrow
    case 'task_completed': return '\u2714';  // check
    case 'task_derived':   return '\u2934';  // curve arrow
    case 'ask_created':    return '\u2753';  // question
    case 'ask_resolved':   return '\u2705';  // green check
    case 'agent_progress': return '\u2026';  // ellipsis
    case 'agent_renamed':  return '\u270E';  // pencil
    case 'agent_waiting':  return '\u23F8';  // pause
    default:               return '\u2022';  // bullet
  }
}

function _eventsKindClass(kind) {
  if (kind === 'agent_error') return 'events-kind-error';
  if (kind === 'agent_blocked' || kind === 'agent_idle' || kind === 'agent_waiting') return 'events-kind-blocked';
  if (kind === 'ask_created') return 'events-kind-ask';
  if (kind === 'task_completed' || kind === 'ask_resolved' || kind === 'agent_finished') return 'events-kind-done';
  return '';
}

/* ---- Attention items (derived from live state) ---------------------- */

function _eventsGetAttentionItems() {
  var items = [];
  var grp = _eventsCurrentGroup();

  // Ask tasks (human-labeled, not done)
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var t = tasks[id];
    if (!t.labels || t.labels.indexOf('human') < 0) continue;
    if (t.lane === 'Done') continue;
    if (grp && t.group !== grp) continue;
    items.push({
      type: 'ask',
      id: t.id,
      agent_name: t.agent_id ? _eventsAgentName(t.agent_id) : '',
      group: t.group,
      message: t.task || '',
      timestamp: t.created_at ? new Date(t.created_at).getTime() / 1000 : 0,
      parent_agent_id: _eventsAskParentAgentId(t),
    });
  }

  // Agents needing attention (errors, blocked, idle)
  var agents = (state && state.agents) || {};
  for (var aid in agents) {
    var a = agents[aid];
    if (!a.needs_attention) continue;
    if (a.cell_type === 'terminal') continue;
    if (grp && a.group !== grp) continue;
    var atype = a.error_message ? 'error' : 'blocked';
    items.push({
      type: atype,
      id: aid,
      agent_name: a.name,
      group: a.group,
      message: a.error_message || a.activity_detail || 'Needs attention',
      timestamp: a.last_event_at || 0,
    });
  }

  // Sort by timestamp descending (newest first)
  items.sort(function(a, b) { return b.timestamp - a.timestamp; });
  return items;
}

function _eventsAgentName(agentId) {
  if (!state || !state.agents || !state.agents[agentId]) return '';
  return state.agents[agentId].name;
}

function _eventsAskParentAgentId(task) {
  if (!task.parent_task_id || !state || !state.board_tasks) return '';
  var parent = state.board_tasks[task.parent_task_id];
  return parent ? (parent.agent_id || '') : '';
}

/* ---- Render --------------------------------------------------------- */

function renderEvents() {
  var panel = document.getElementById('panel-events');
  if (!panel) return;

  // Preserve scroll position
  var logEl = panel.querySelector('.events-log');
  if (logEl) _eventsScrollTop = logEl.scrollTop;

  var html = '';

  // Header
  html += '<div class="events-header">';
  html += '<span class="events-header-title">Events</span>';
  html += '<button class="events-filter-btn' + (_eventsFilterByGroup ? ' active' : '') + '"'
    + ' onclick="eventsToggleGroupFilter()" title="Filter by current group">'
    + (_eventsFilterByGroup ? '\u{1F4CC} Group' : '\u{1F30D} All')
    + '</button>';
  html += '<select class="events-kind-filter" onchange="eventsSetKindFilter(this.value)">';
  html += '<option value="all"' + (_eventsKindFilter === 'all' ? ' selected' : '') + '>All</option>';
  html += '<option value="errors"' + (_eventsKindFilter === 'errors' ? ' selected' : '') + '>Errors</option>';
  html += '<option value="tasks"' + (_eventsKindFilter === 'tasks' ? ' selected' : '') + '>Tasks</option>';
  html += '<option value="lifecycle"' + (_eventsKindFilter === 'lifecycle' ? ' selected' : '') + '>Lifecycle</option>';
  html += '</select>';
  html += '</div>';
  html += '<div class="events-search-row">';
  html += '<input class="events-search-input" type="text" placeholder="Search events\u2026"'
    + ' value="' + esc(_eventsSearchQuery) + '"'
    + ' oninput="eventsOnSearchInput(this.value)">';
  html += '</div>';

  // Attention section
  var allAttention = _eventsGetAttentionItems();
  var attention = [];
  for (var ai = 0; ai < allAttention.length; ai++) {
    if (!_eventsDismissedIds.has(allAttention[ai].id)) attention.push(allAttention[ai]);
  }
  var attCount = attention.length;
  html += '<div class="events-attention">';
  html += '<div class="events-attention-heading">Attention'
    + (attCount > 0 ? ' <span class="events-attention-count">' + attCount + '</span>' : '')
    + '</div>';
  if (attention.length === 0) {
    html += '<div class="events-attention-empty">No items need attention</div>';
  } else {
    for (var i = 0; i < attention.length; i++) {
      html += _renderAttentionCard(attention[i]);
    }
  }
  html += '</div>';

  // Log section
  html += '<div class="events-log">';
  var events = (state && state.panel_events) || [];
  var grp = _eventsCurrentGroup();
  var count = 0;
  var lastDateLabel = '';
  for (var j = events.length - 1; j >= 0 && count < 200; j--) {
    var evt = events[j];
    if (grp && evt.group !== grp) continue;
    if (!_eventsMatchesFilters(evt)) continue;
    var dateLabel = _eventsDateLabel(evt.timestamp);
    if (dateLabel !== lastDateLabel) {
      html += '<div class="events-date-separator">' + dateLabel + '</div>';
      lastDateLabel = dateLabel;
    }
    html += _renderEventEntry(evt, j);
    count++;
  }
  if (count === 0) {
    html += '<div class="events-log-empty">No events yet</div>';
  }
  if (_eventsLoading) {
    html += '<div class="events-loading">Loading\u2026</div>';
  }
  html += '</div>';

  panel.innerHTML = html;

  // Restore scroll
  logEl = panel.querySelector('.events-log');
  if (logEl) {
    logEl.scrollTop = _eventsScrollTop;
    logEl.addEventListener('scroll', _eventsOnScroll);
  }

  // Track oldest ID for pagination
  if (events.length > 0) {
    _eventsOldestId = events[0].id;
  }

  // Restore search input focus if it was active before re-render
  if (_eventsSearchHadFocus) {
    var searchInput = panel.querySelector('.events-search-input');
    if (searchInput) { searchInput.focus(); searchInput.selectionStart = searchInput.selectionEnd = searchInput.value.length; }
    _eventsSearchHadFocus = false;
  }

  // Auto-resize textareas
  panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  });
}

/* ---- Attention card rendering --------------------------------------- */

function _renderAttentionCard(item) {
  var html = '<div class="events-attention-card events-attention-' + item.type + '">';
  html += '<button class="events-dismiss-btn" onclick="event.stopPropagation();eventsDismiss(\'' + item.id + '\')" title="Dismiss">\u00D7</button>';

  if (item.type === 'ask') {
    html += '<div class="events-attention-label">&#x2753; Question'
      + (item.agent_name ? ' from <strong>' + esc(item.agent_name) + '</strong>' : '')
      + '</div>';
    html += '<div class="events-attention-message">' + esc(item.message) + '</div>';
    html += '<textarea class="events-resolve-textarea" id="events-resolve-' + item.id + '"'
      + ' placeholder="Type your answer..."'
      + ' oninput="this.style.height=\'auto\';this.style.height=this.scrollHeight+\'px\'"'
      + ' onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();eventsResolveInline(\'' + item.id + '\')}"'
      + '></textarea>';
    html += '<div class="events-attention-actions">';
    html += '<button class="btn-primary btn-sm" onclick="eventsResolveInline(\'' + item.id + '\')">Resolve</button>';
    if (item.parent_agent_id) {
      html += '<button class="btn-secondary btn-sm" onclick="eventsFocusAgent(\'' + item.parent_agent_id + '\')">Focus Agent</button>';
    }
    html += '</div>';
  } else {
    var icon = item.type === 'error' ? '\u2716' : '\u26D4';
    var label = item.type === 'error' ? 'Error' : 'Blocked';
    html += '<div class="events-attention-label">' + icon + ' ' + label
      + ' \u2014 <strong>' + esc(item.agent_name) + '</strong></div>';
    html += '<div class="events-attention-message">' + esc(item.message) + '</div>';
    html += '<div class="events-attention-actions">';
    html += '<button class="btn-secondary btn-sm" onclick="eventsFocusAgent(\'' + item.id + '\')">Focus Agent</button>';
    html += '</div>';
  }

  html += '</div>';
  return html;
}

/* ---- Log entry rendering -------------------------------------------- */

function _renderEventEntry(evt, idx) {
  var kindClass = _eventsKindClass(evt.kind);
  var isExpanded = _eventsExpandedEntries[idx];
  var expanded = isExpanded ? ' expanded' : '';
  var isError = (evt.kind === 'agent_error' || evt.kind === 'agent_blocked');
  var html = '<div class="events-entry ' + kindClass + expanded + '"'
    + ' onclick="eventsToggleEntry(' + idx + ')">';
  html += '<span class="events-entry-time">' + _eventsFormatTime(evt.timestamp) + '</span>';
  html += '<span class="events-entry-icon">' + _eventsKindIcon(evt.kind) + '</span>';
  if (evt.agent_name) {
    html += '<span class="events-entry-agent">' + esc(evt.agent_name) + '</span>';
  }
  if (isExpanded && isError) {
    html += '<span class="events-entry-text events-entry-error-detail">'
      + esc(evt.message || evt.kind)
      + '<button class="events-copy-btn" onclick="event.stopPropagation();eventsCopyMessage(' + idx + ')" title="Copy">Copy</button>'
      + '</span>';
  } else {
    html += '<span class="events-entry-text">' + esc(evt.message || evt.kind) + '</span>';
  }
  html += '</div>';
  return html;
}

/* ---- Actions -------------------------------------------------------- */

function eventsResolveInline(taskId) {
  var textarea = document.getElementById('events-resolve-' + taskId);
  if (!textarea) return;
  var answer = textarea.value.trim();
  if (!answer) { textarea.focus(); return; }
  send({ cmd: 'resolve_ask', id: taskId, answer: answer });
  textarea.value = '';
}

function eventsFocusAgent(cellId) {
  if (typeof focusAgent === 'function') focusAgent(cellId);
}

function eventsToggleEntry(idx) {
  if (_eventsExpandedEntries[idx]) delete _eventsExpandedEntries[idx];
  else _eventsExpandedEntries[idx] = true;
  renderEvents();
}

function eventsToggleGroupFilter() {
  _eventsFilterByGroup = !_eventsFilterByGroup;
  renderEvents();
}

function eventsSetKindFilter(value) {
  _eventsKindFilter = value;
  renderEvents();
}

function eventsOnSearchInput(value) {
  if (_eventsSearchDebounce) clearTimeout(_eventsSearchDebounce);
  _eventsSearchDebounce = setTimeout(function() {
    _eventsSearchQuery = value;
    _eventsSearchHadFocus = true;
    renderEvents();
  }, 150);
}

function eventsDismiss(id) {
  _eventsDismissedIds.add(id);
  renderEvents();
  updateEventsAttentionBadge();
}

function eventsCopyMessage(idx) {
  var events = (state && state.panel_events) || [];
  var evt = events[idx];
  if (evt && evt.message) {
    navigator.clipboard.writeText(evt.message);
  }
}

function updateEventsAttentionBadge() {
  var btn = document.querySelector('.taskbar-app[data-app="events"]');
  if (!btn) return;
  var items = _eventsGetAttentionItems();
  var visible = 0;
  for (var i = 0; i < items.length; i++) {
    if (!_eventsDismissedIds.has(items[i].id)) visible++;
  }
  btn.classList.toggle('panel-attention', visible > 0);
}

/* ---- Scroll pagination ---------------------------------------------- */

function _eventsOnScroll() {
  var el = this;
  // The log renders newest first (top) — "load more" triggers near the bottom
  if (_eventsLoading || !_eventsHasMore) return;
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
    _eventsLoadMore();
  }
}

function _eventsLoadMore() {
  if (_eventsLoading || !_eventsHasMore || !_eventsOldestId) return;
  _eventsLoading = true;
  renderEvents();
  send({ cmd: 'get_events', before_id: _eventsOldestId, limit: 50 });
}

function handleEventsPage(data) {
  _eventsLoading = false;
  var page = data.events || [];
  if (page.length === 0) {
    _eventsHasMore = false;
    renderEvents();
    return;
  }
  if (page.length < 50) _eventsHasMore = false;
  // Merge older events into the front of state.panel_events
  if (!state.panel_events) state.panel_events = [];
  // Deduplicate by ID
  var existing = {};
  for (var i = 0; i < state.panel_events.length; i++) {
    existing[state.panel_events[i].id] = true;
  }
  var toAdd = [];
  for (var j = 0; j < page.length; j++) {
    if (!existing[page[j].id]) toAdd.push(page[j]);
  }
  if (toAdd.length > 0) {
    state.panel_events = toAdd.concat(state.panel_events);
  }
  renderEvents();
}

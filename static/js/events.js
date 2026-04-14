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
var _eventsResolveDrafts = {};

function _eventsDismissedMap() {
  if (!state.events_dismissed_attention
      || typeof state.events_dismissed_attention !== 'object') {
    state.events_dismissed_attention = {};
  }
  return state.events_dismissed_attention;
}

function _eventsDismissedTimestamp(id) {
  var dismissed = _eventsDismissedMap()[id];
  if (dismissed !== undefined && dismissed !== null && dismissed !== '') {
    return Number(dismissed) || 0;
  }
  return _eventsDismissedIds.has(id) ? Number.MAX_SAFE_INTEGER : 0;
}

function _eventsIsDismissed(item) {
  var dismissedAt = _eventsDismissedTimestamp(item.id);
  if (!dismissedAt) return false;
  var itemTs = Number(item.timestamp || 0);
  if (!itemTs) return true;
  return itemTs <= dismissedAt;
}

function _eventsEntryKey(evt, fallback) {
  if (evt && evt.id !== undefined && evt.id !== null && evt.id !== '') {
    return String(evt.id);
  }
  if (fallback !== undefined && fallback !== null && fallback !== '') {
    return String(fallback);
  }
  return '';
}

function _eventsAnchorDataValue(el, dataKey) {
  if (!el) return '';
  if (el.dataset && el.dataset[dataKey] !== undefined && el.dataset[dataKey] !== '') {
    return String(el.dataset[dataKey]);
  }
  var attrName = 'data-' + String(dataKey).replace(/[A-Z]/g, function(ch) {
    return '-' + ch.toLowerCase();
  });
  if (typeof el.getAttribute === 'function') {
    var value = el.getAttribute(attrName);
    if (value !== null && value !== '') return String(value);
  }
  return '';
}

function _eventsCaptureScrollAnchor(scroller, selector, dataKey) {
  if (!scroller) return null;
  var snapshot = {
    id: '',
    offset: 0,
    top: typeof scroller.scrollTop === 'number' ? scroller.scrollTop : 0,
  };
  if (typeof scroller.querySelectorAll !== 'function') return snapshot;
  var items = scroller.querySelectorAll(selector) || [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var top = typeof item.offsetTop === 'number' ? item.offsetTop : 0;
    var height = typeof item.offsetHeight === 'number' ? item.offsetHeight : 0;
    if (top + height > snapshot.top) {
      var id = _eventsAnchorDataValue(item, dataKey);
      if (id) {
        snapshot.id = id;
        snapshot.offset = snapshot.top - top;
      }
      break;
    }
  }
  return snapshot;
}

function _eventsFindScrollAnchor(scroller, selector, dataKey, id) {
  if (!scroller || !id || typeof scroller.querySelectorAll !== 'function') return null;
  var items = scroller.querySelectorAll(selector) || [];
  for (var i = 0; i < items.length; i++) {
    if (_eventsAnchorDataValue(items[i], dataKey) === String(id)) return items[i];
  }
  return null;
}

function _eventsRestoreScrollAnchor(scroller, selector, dataKey, snapshot) {
  if (!scroller || !snapshot) return;
  var nextTop = typeof snapshot.top === 'number' ? snapshot.top : 0;
  if (snapshot.id) {
    var item = _eventsFindScrollAnchor(scroller, selector, dataKey, snapshot.id);
    if (item) {
      var top = typeof item.offsetTop === 'number' ? item.offsetTop : 0;
      nextTop = top + (snapshot.offset || 0);
    }
  }
  scroller.scrollTop = Math.max(0, nextTop);
}

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
  errors: ['agent_error', 'agent_blocked', 'task_health_alert'],
  tasks: ['task_dispatched', 'task_completed', 'task_derived', 'ask_created', 'ask_resolved', 'task_health_alert', 'task_verification_updated'],
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
    case 'task_health_alert': return '\u26A0'; // warning
    case 'task_dispatched': return '\u2192'; // arrow
    case 'task_completed': return '\u2714';  // check
    case 'task_derived':   return '\u2934';  // curve arrow
    case 'ask_created':    return '\u2753';  // question
    case 'ask_resolved':   return '\u2705';  // green check
    case 'task_verification_updated': return '\u2691'; // flag
    case 'agent_progress': return '\u2026';  // ellipsis
    case 'agent_renamed':  return '\u270E';  // pencil
    case 'agent_waiting':  return '\u23F8';  // pause
    default:               return '\u2022';  // bullet
  }
}

function _eventsKindClass(kind) {
  if (kind === 'agent_error') return 'events-kind-error';
  if (kind === 'agent_blocked' || kind === 'agent_idle' || kind === 'agent_waiting' || kind === 'task_health_alert') return 'events-kind-blocked';
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
    if (!t.labels || t.labels.indexOf('loom:human') < 0) continue;
    if (t.lane === 'Done') continue;
    if (grp && t.group !== grp) continue;
    var parent = _eventsAskParentTask(t);
    var agent = _eventsAskAgent(t);
    items.push({
      type: 'ask',
      id: t.id,
      agent_name: agent ? (agent.name || '') : '',
      agent_slug: agent ? (agent.slug || '') : '',
      group: t.group,
      message: t.task || '',
      description: t.description || '',
      timestamp: t.created_at ? new Date(t.created_at).getTime() / 1000 : 0,
      parent_agent_id: parent ? (parent.agent_id || '') : '',
      parent_task_title: parent ? (parent.task || '') : '',
      parent_task_description: parent ? (parent.description || '') : '',
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

function _eventsAskParentTask(task) {
  if (!task.parent_task_id || !state || !state.board_tasks) return null;
  return state.board_tasks[task.parent_task_id] || null;
}

function _eventsAskAgent(task) {
  var parent = _eventsAskParentTask(task);
  if (!parent || !parent.agent_id || !state || !state.agents) return null;
  return state.agents[parent.agent_id] || null;
}

/* ---- Render --------------------------------------------------------- */

function renderEvents() {
  var panel = document.getElementById('panel-events');
  if (!panel) return;
  var panelStateOptions = {
    captureFocusKey: function(active) {
      if (active && active.classList
          && active.classList.contains('events-search-input')) {
        return '.events-search-input';
      }
      return '';
    },
    resolveFocus: function(root, snapshot) {
      if (snapshot && snapshot.key === '.events-search-input'
          && root && typeof root.querySelector === 'function') {
        return root.querySelector('.events-search-input');
      }
      return null;
    },
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      snapshot.attentionAnchor = _eventsCaptureScrollAnchor(
        root.querySelector('.events-attention'),
        '.events-attention-card',
        'itemId',
      );
      snapshot.logAnchor = _eventsCaptureScrollAnchor(
        root.querySelector('.events-log'),
        '.events-entry',
        'eventId',
      );
    },
    restore: function(root, snapshot) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      _eventsRestoreScrollAnchor(
        root.querySelector('.events-attention'),
        '.events-attention-card',
        'itemId',
        snapshot.attentionAnchor,
      );
      _eventsRestoreScrollAnchor(
        root.querySelector('.events-log'),
        '.events-entry',
        'eventId',
        snapshot.logAnchor,
      );
    },
  };
  var panelState = _captureSurfaceState(panel, panelStateOptions);
  var shouldRestoreSearchFocus = _eventsSearchHadFocus
    && !(panelState && panelState.focus);

  // Preserve scroll position and inline ask drafts before DOM rebuild
  var logEl = panel.querySelector('.events-log');
  if (logEl) _eventsScrollTop = logEl.scrollTop;
  panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
    var taskId = ta.id.replace('events-resolve-', '');
    _eventsResolveDrafts[taskId] = ta.value;
  });

  var html = '';

  // Header
  var grp = _eventsCurrentGroup();
  var scopeLabel = grp
    ? 'Attention inbox and recent activity for ' + grp
    : 'Attention inbox and recent activity across Loom';
  html += '<div class="events-header">';
  html += '<div class="events-header-copy">';
  html += '<div class="events-header-title">Events</div>';
  html += '<div class="events-header-subtitle">' + esc(scopeLabel) + '</div>';
  html += '</div>';
  html += '<div class="events-header-actions">';
  html += '<select class="events-kind-filter" onchange="eventsSetKindFilter(this.value)">';
  html += '<option value="all"' + (_eventsKindFilter === 'all' ? ' selected' : '') + '>All</option>';
  html += '<option value="errors"' + (_eventsKindFilter === 'errors' ? ' selected' : '') + '>Errors</option>';
  html += '<option value="tasks"' + (_eventsKindFilter === 'tasks' ? ' selected' : '') + '>Tasks</option>';
  html += '<option value="lifecycle"' + (_eventsKindFilter === 'lifecycle' ? ' selected' : '') + '>Lifecycle</option>';
  html += '</select>';
  html += '</div>';
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
    if (!_eventsIsDismissed(allAttention[ai])) attention.push(allAttention[ai]);
  }
  var attCount = attention.length;
  html += '<div class="events-attention">';
  html += '<div class="events-attention-heading">Attention inbox'
    + (attCount > 0 ? ' <span class="events-attention-count">' + attCount + '</span>' : '')
    + '</div>';
  if (attention.length === 0) {
    html += '<div class="events-attention-empty">No items need attention in this view.</div>';
  } else {
    for (var i = 0; i < attention.length; i++) {
      html += _renderAttentionCard(attention[i]);
    }
  }
  html += '</div>';

  // Log section
  html += '<div class="events-log">';
  var events = (state && state.panel_events) || [];
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
    html += '<div class="events-log-empty">No recent events in this view yet.</div>';
  }
  if (_eventsLoading) {
    html += '<div class="events-loading">Loading\u2026</div>';
  }
  html += '</div>';

  panel.innerHTML = html;

  // Track oldest ID for pagination
  if (events.length > 0) {
    _eventsOldestId = events[0].id;
  }

  // Auto-resize textareas
  panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  });
  _restoreSurfaceState(panel, panelState, panelStateOptions);

  logEl = panel.querySelector('.events-log');
  if (logEl) {
    _eventsScrollTop = logEl.scrollTop;
    logEl.addEventListener('scroll', _eventsOnScroll);
  }

  if (shouldRestoreSearchFocus) {
    var searchInput = panel.querySelector('.events-search-input');
    if (searchInput) {
      searchInput.focus();
      searchInput.selectionStart = searchInput.selectionEnd = searchInput.value.length;
    }
  }
  _eventsSearchHadFocus = false;
}

/* ---- Attention card rendering --------------------------------------- */

function _renderAttentionCard(item) {
  var html = '<div class="events-attention-card events-attention-' + item.type + '"'
    + ' data-item-id="' + esc(item.id) + '">';
  html += '<button class="events-dismiss-btn" onclick="event.stopPropagation();eventsDismiss(\'' + item.id + '\')" title="Dismiss">\u00D7</button>';

  if (item.type === 'ask') {
    var draft = _eventsResolveDrafts[item.id] || '';
    html += '<div class="events-attention-label">&#x2753; Question</div>';
    if (item.agent_name) {
      html += '<div class="events-attention-agent"><strong>' + esc(item.agent_name) + '</strong>';
      if (item.agent_slug) {
        html += '<span class="events-attention-agent-slug">@' + esc(item.agent_slug) + '</span>';
      }
      html += '</div>';
    }
    if (item.parent_task_title) {
      html += '<div class="events-attention-context-label">Current task</div>';
      html += '<div class="events-attention-message">' + esc(item.parent_task_title) + '</div>';
    }
    if (item.parent_task_description) {
      html += '<div class="events-attention-context">' + esc(item.parent_task_description) + '</div>';
    }
    html += '<div class="events-attention-context-label">Question</div>';
    html += '<div class="events-attention-message">' + esc(item.message) + '</div>';
    if (item.description) {
      html += '<div class="events-attention-context-label">Additional details</div>';
      html += '<div class="events-attention-context">' + esc(item.description) + '</div>';
    }
    html += '<textarea class="events-resolve-textarea" id="events-resolve-' + item.id + '"'
      + ' placeholder="Type your answer..."'
      + ' oninput="eventsResolveInput(\'' + item.id + '\', this)"'
      + ' onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();eventsResolveInline(\'' + item.id + '\')}"'
      + '>' + esc(draft) + '</textarea>';
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
  var entryKey = _eventsEntryKey(evt, idx);
  var entryKeyJs = esc(entryKey).replace(/'/g, "\\'");
  var kindClass = _eventsKindClass(evt.kind);
  var isExpanded = _eventsExpandedEntries[entryKey];
  var expanded = isExpanded ? ' expanded' : '';
  var isError = (evt.kind === 'agent_error' || evt.kind === 'agent_blocked');
  var html = '<div class="events-entry ' + kindClass + expanded + '"'
    + ' data-event-id="' + esc(entryKey) + '"'
    + ' onclick="eventsToggleEntry(\'' + entryKeyJs + '\')">';
  html += '<span class="events-entry-time">' + _eventsFormatTime(evt.timestamp) + '</span>';
  html += '<span class="events-entry-icon">' + _eventsKindIcon(evt.kind) + '</span>';
  if (evt.agent_name) {
    html += '<span class="events-entry-agent">' + esc(evt.agent_name) + '</span>';
  }
  if (isExpanded && isError) {
    html += '<span class="events-entry-text events-entry-error-detail">'
      + esc(evt.message || evt.kind)
      + '<button class="events-copy-btn" onclick="event.stopPropagation();eventsCopyMessage(\'' + entryKeyJs + '\')" title="Copy">Copy</button>'
      + '</span>';
  } else {
    html += '<span class="events-entry-text">' + esc(evt.message || evt.kind) + '</span>';
  }
  html += '</div>';
  return html;
}

/* ---- Actions -------------------------------------------------------- */

function eventsResolveInput(taskId, textarea) {
  _eventsResolveDrafts[taskId] = textarea.value;
  textarea.style.height = 'auto';
  textarea.style.height = textarea.scrollHeight + 'px';
}

function eventsResolveInline(taskId) {
  var textarea = document.getElementById('events-resolve-' + taskId);
  if (!textarea) return;
  var answer = textarea.value.trim();
  if (!answer) { textarea.focus(); return; }
  send({ cmd: 'resolve_ask', id: taskId, answer: answer });
  delete _eventsResolveDrafts[taskId];
  textarea.value = '';
}

function eventsFocusAgent(cellId) {
  if (typeof focusAgent === 'function') focusAgent(cellId);
}

function eventsToggleEntry(entryKey) {
  var key = String(entryKey || '');
  if (!key) return;
  if (_eventsExpandedEntries[key]) delete _eventsExpandedEntries[key];
  else _eventsExpandedEntries[key] = true;
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
  var items = _eventsGetAttentionItems();
  var current = null;
  for (var i = 0; i < items.length; i++) {
    if (items[i].id === id) {
      current = items[i];
      break;
    }
  }
  var timestamp = current ? Number(current.timestamp || 0) : 0;
  if (timestamp <= 0) timestamp = Math.floor(Date.now() / 1000);
  delete _eventsResolveDrafts[id];
  _eventsDismissedIds.add(id);
  _eventsDismissedMap()[id] = timestamp;
  send({ cmd: 'events_dismiss', id: id, timestamp: timestamp });
  renderEvents();
  updateEventsAttentionBadge();
}

function eventsCopyMessage(entryKey) {
  var events = (state && state.panel_events) || [];
  var evt = null;
  for (var i = 0; i < events.length; i++) {
    if (_eventsEntryKey(events[i], i) === String(entryKey || '')) {
      evt = events[i];
      break;
    }
  }
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
    if (!_eventsIsDismissed(items[i])) visible++;
  }
  btn.classList.toggle('panel-attention', visible > 0);
}

/* ---- Scroll pagination ---------------------------------------------- */

function _eventsOnScroll() {
  var el = this;
  _eventsScrollTop = el.scrollTop;
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

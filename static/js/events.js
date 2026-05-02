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
var _eventsRenderedShellSignature = '';
var _eventsVirtualRows = [];
var _eventsVirtualTops = [];
var _eventsVirtualHeight = 0;
var _eventsLogRenderState = {
  visibleKeys: '',
  visibleHtml: '',
  topHeight: -1,
  bottomHeight: -1,
};
var _eventsMeasuredHeights = {};
var _eventsDefaultEntryHeight = 24;
var _eventsDefaultExpandedEntryHeight = 72;
var _eventsDefaultDateHeight = 22;
var _eventsDefaultEmptyHeight = 52;
var _eventsDefaultLoadingHeight = 30;
var _eventsVirtualOverscanPx = 240;

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
  lifecycle: ['agent_started', 'agent_finished', 'agent_renamed', 'agent_waiting', 'agent_progress', 'engineer_note_dismissed', 'engineer_question_dismissed']
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

function _eventsInlineThreadEvents() {
  var tasks = (state && state.board_tasks) || {};
  var agents = (state && state.agents) || {};
  var events = [];
  for (var taskId in tasks) {
    var task = tasks[taskId] || {};
    var thread = Array.isArray(task.messages_thread) ? task.messages_thread : [];
    for (var i = 0; i < thread.length; i++) {
      var entry = thread[i] || {};
      var recipientId = String(entry.recipient_agent_id || '');
      var recipient = recipientId ? (agents[recipientId] || null) : null;
      var ts = Number(entry.timestamp || 0);
      events.push({
        id: 'inline-thread:' + taskId + ':' + i + ':' + ts,
        timestamp: ts,
        kind: 'engineer_message',
        cell_id: recipientId,
        agent_name: recipient ? (recipient.name || recipient.id || '') : recipientId,
        group: task.group || '',
        message: String(entry.content || ''),
        task_id: taskId,
        inline_thread: true,
        reply_required: !!entry.reply_required,
      });
    }
  }
  return events;
}

function _eventsCombinedEvents() {
  var events = (state && state.panel_events ? state.panel_events.slice() : []);
  var inlineEvents = _eventsInlineThreadEvents();
  for (var i = 0; i < inlineEvents.length; i++) events.push(inlineEvents[i]);
  events.sort(function(a, b) {
    var at = Number((a && a.timestamp) || 0);
    var bt = Number((b && b.timestamp) || 0);
    if (at !== bt) return at - bt;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return events;
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
    case 'engineer_note_dismissed':
    case 'engineer_question_dismissed':
      return '\u2709';  // envelope
    default:               return '\u2022';  // bullet
  }
}

function _eventsIsDismissedEngineerNoteKind(kind) {
  return kind === 'engineer_note_dismissed'
    || kind === 'engineer_question_dismissed';
}

function _eventsKindClass(kind) {
  if (kind === 'agent_error') return 'events-kind-error';
  if (kind === 'agent_blocked' || kind === 'agent_idle' || kind === 'agent_waiting' || kind === 'task_health_alert') return 'events-kind-blocked';
  if (kind === 'ask_created') return 'events-kind-ask';
  if (kind === 'task_completed' || kind === 'ask_resolved' || kind === 'agent_finished') return 'events-kind-done';
  if (_eventsIsDismissedEngineerNoteKind(kind)) return 'events-kind-dismissed-note';
  return '';
}

/* ---- Attention items (derived from live state) ---------------------- */

function _eventsGetAttentionItems() {
  var items = [];
  var grp = _eventsCurrentGroup();

  // Ask tasks (human-labeled, not done)
  var tasks = (state && state.board_tasks) || {};
  // description remains lazy in compact-v1 — hydrate every open ask so
  // the feed shows real body text, and the parent task so its description
  // renders as context under the ask.
  if (typeof _compactHydrateTasksMatching === 'function'
      && typeof ensureTaskDetail === 'function') {
    var _parentsToHydrate = {};
    _compactHydrateTasksMatching(function(t) {
      if (!t || !t.labels || t.labels.indexOf('torque:human') < 0) return false;
      if (t.lane === 'Done') return false;
      if (grp && t.group !== grp) return false;
      if (t.parent_task_id) _parentsToHydrate[t.parent_task_id] = true;
      return true;
    });
    for (var _pid in _parentsToHydrate) ensureTaskDetail(_pid);
  }
  for (var id in tasks) {
    var t = tasks[id];
    if (!t.labels || t.labels.indexOf('torque:human') < 0) continue;
    if (t.lane === 'Done') continue;
    if (grp && t.group !== grp) continue;
    var parent = _eventsAskParentTask(t);
    var isArchitectAsk = t.labels.indexOf('architect-ask') >= 0;
    var architectId = t.reply_agent_id || t.created_by_architect_id || '';
    var agent = isArchitectAsk && state && state.agents
      ? (state.agents[architectId] || null)
      : _eventsAskAgent(t);
    items.push({
      type: 'ask',
      id: t.id,
      is_architect_ask: isArchitectAsk,
      agent_name: agent ? (agent.name || '') : '',
      agent_slug: agent ? (agent.slug || '') : '',
      group: t.group,
      message: t.task || '',
      description: t.description || '',
      timestamp: t.created_at ? new Date(t.created_at).getTime() / 1000 : 0,
      parent_agent_id: isArchitectAsk ? architectId : (parent ? (parent.agent_id || '') : ''),
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

function _eventsAttentionSignature(items) {
  var parts = [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    parts.push([
      item.type || '',
      item.id || '',
      item.timestamp || 0,
      item.agent_name || '',
      item.is_architect_ask ? 'architect' : '',
      item.message || '',
      item.description || '',
      item.parent_task_title || '',
      item.parent_task_description || '',
    ].join('\u0001'));
  }
  return parts.join('\u0002');
}

function _eventsBuildShellHtml(grp, attention) {
  var html = '';
  var scopeLabel = grp
    ? 'Attention inbox and recent activity for ' + grp
    : 'Attention inbox and recent activity across Torque';

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

  html += '<div class="events-log" data-events-virtual-log="1"></div>';
  return html;
}

function _eventsBuildShellSignature(grp, attention) {
  return [
    grp || '',
    _eventsKindFilter || 'all',
    _eventsSearchQuery || '',
    _eventsAttentionSignature(attention),
  ].join('\u0003');
}

function _eventsSyncShellControls(panel) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var filter = panel.querySelector('.events-kind-filter');
  if (filter && 'value' in filter && filter.value !== _eventsKindFilter) {
    filter.value = _eventsKindFilter;
  }
  var search = panel.querySelector('.events-search-input');
  if (search && 'value' in search && search.value !== _eventsSearchQuery
      && document.activeElement !== search) {
    search.value = _eventsSearchQuery;
  }
}

function _eventsRememberResolveDrafts(panel) {
  if (!panel || typeof panel.querySelectorAll !== 'function') return;
  panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
    var taskId = ta.id.replace('events-resolve-', '');
    _eventsResolveDrafts[taskId] = ta.value;
  });
}

function _eventsVirtualEventKey(evt, idx) {
  return 'event:' + _eventsEntryKey(evt, idx);
}

function _eventsVirtualDateKey(label, timestamp) {
  var d = new Date((timestamp || 0) * 1000);
  var day = [d.getFullYear(), d.getMonth(), d.getDate()].join('-');
  return 'date:' + label + ':' + day;
}

function _eventsEstimateEventHeight(evt, idx) {
  var entryKey = _eventsEntryKey(evt, idx);
  var virtualKey = 'event:' + entryKey;
  var measured = _eventsMeasuredHeights[virtualKey];
  if (measured && measured > 0) return measured;
  var expanded = _eventsExpandedEntries[entryKey]
    && (evt.kind === 'agent_error' || evt.kind === 'agent_blocked');
  if (!expanded) return _eventsDefaultEntryHeight;
  var message = evt && evt.message ? String(evt.message) : '';
  var extraLines = Math.min(8, Math.ceil(message.length / 120));
  return _eventsDefaultExpandedEntryHeight + Math.max(0, extraLines - 1) * 16;
}

function _eventsRowHeight(row) {
  if (!row) return 0;
  var measured = _eventsMeasuredHeights[row.key];
  if (measured && measured > 0) return measured;
  if (row.type === 'date') return _eventsDefaultDateHeight;
  if (row.type === 'event') return _eventsEstimateEventHeight(row.event, row.idx);
  if (row.type === 'loading') return _eventsDefaultLoadingHeight;
  if (row.type === 'empty') return _eventsDefaultEmptyHeight;
  return _eventsDefaultEntryHeight;
}

function _eventsBuildVirtualRows() {
  var rows = [];
  var grp = _eventsCurrentGroup();
  var events = _eventsCombinedEvents();
  var count = 0;
  var lastDateLabel = '';
  for (var j = events.length - 1; j >= 0; j--) {
    var evt = events[j];
    if (grp && evt.group !== grp) continue;
    if (!_eventsMatchesFilters(evt)) continue;
    var dateLabel = _eventsDateLabel(evt.timestamp);
    if (dateLabel !== lastDateLabel) {
      var dateKey = _eventsVirtualDateKey(dateLabel, evt.timestamp);
      rows.push({
        type: 'date',
        key: dateKey,
        label: dateLabel,
        height: _eventsMeasuredHeights[dateKey] || _eventsDefaultDateHeight,
      });
      lastDateLabel = dateLabel;
    }
    var eventKey = _eventsVirtualEventKey(evt, j);
    rows.push({
      type: 'event',
      key: eventKey,
      event: evt,
      idx: j,
      height: _eventsEstimateEventHeight(evt, j),
    });
    count++;
  }
  if (count === 0) {
    rows.push({ type: 'empty', key: 'empty', height: _eventsDefaultEmptyHeight });
  }
  if (_eventsLoading) {
    rows.push({ type: 'loading', key: 'loading', height: _eventsDefaultLoadingHeight });
  }
  return rows;
}

function _eventsBuildVirtualMetrics(rows) {
  var tops = [];
  var total = 0;
  for (var i = 0; i < rows.length; i++) {
    rows[i].height = _eventsRowHeight(rows[i]);
    tops.push(total);
    total += rows[i].height;
  }
  return { tops: tops, total: total };
}

function _eventsFindRowIndexAt(rows, tops, scrollTop) {
  if (!rows.length) return 0;
  var target = Math.max(0, Number(scrollTop || 0));
  var lo = 0;
  var hi = rows.length - 1;
  var result = rows.length - 1;
  while (lo <= hi) {
    var mid = Math.floor((lo + hi) / 2);
    if (tops[mid] + rows[mid].height > target) {
      result = mid;
      hi = mid - 1;
    } else {
      lo = mid + 1;
    }
  }
  return result;
}

function _eventsVirtualRange(rows, tops, scrollTop, viewportHeight) {
  if (!rows.length) return { start: 0, end: 0 };
  var viewport = Math.max(1, Number(viewportHeight || 0) || 320);
  var startTop = Math.max(0, Number(scrollTop || 0) - _eventsVirtualOverscanPx);
  var endTop = Number(scrollTop || 0) + viewport + _eventsVirtualOverscanPx;
  var start = _eventsFindRowIndexAt(rows, tops, startTop);
  var end = start;
  while (end < rows.length && tops[end] < endTop) end++;
  if (end <= start) end = Math.min(rows.length, start + 1);
  return { start: start, end: end };
}

function _eventsFindVirtualKeyIndex(rows, key) {
  if (!key) return -1;
  for (var i = 0; i < rows.length; i++) {
    if (rows[i].key === key) return i;
  }
  return -1;
}

function _eventsFindAnchorRowIndex(rows, tops, scrollTop) {
  if (!rows.length) return 0;
  var idx = _eventsFindRowIndexAt(rows, tops, scrollTop);
  while (idx < rows.length && rows[idx] && rows[idx].type !== 'event') idx++;
  if (idx < rows.length) return idx;
  return _eventsFindRowIndexAt(rows, tops, scrollTop);
}

function _eventsCaptureVirtualLogAnchor(logEl) {
  if (!logEl) return null;
  var top = typeof logEl.scrollTop === 'number' ? logEl.scrollTop : _eventsScrollTop;
  var snapshot = {
    key: '',
    offset: 0,
    top: typeof top === 'number' ? top : 0,
    pinned: !top || top <= 2,
    domAnchor: null,
  };
  if (_eventsVirtualRows.length && _eventsVirtualTops.length) {
    var idx = _eventsFindAnchorRowIndex(_eventsVirtualRows, _eventsVirtualTops, snapshot.top);
    var row = _eventsVirtualRows[idx];
    if (row) {
      snapshot.key = row.key;
      snapshot.offset = snapshot.top - (_eventsVirtualTops[idx] || 0);
    }
  } else {
    // Migration/first-render fallback for an existing non-virtualized DOM. Normal
    // live appends use the row index/offset snapshot above and avoid DOM scans.
    snapshot.domAnchor = _eventsCaptureScrollAnchor(logEl, '.events-entry', 'eventId');
  }
  return snapshot;
}

function _eventsScrollTopForAnchor(anchor, rows, tops) {
  if (!anchor) return _eventsScrollTop || 0;
  if (anchor.pinned) return 0;
  if (anchor.key) {
    var idx = _eventsFindVirtualKeyIndex(rows, anchor.key);
    if (idx >= 0) return Math.max(0, (tops[idx] || 0) + (anchor.offset || 0));
  }
  return Math.max(0, typeof anchor.top === 'number' ? anchor.top : (_eventsScrollTop || 0));
}

function _eventsRenderVirtualRow(row) {
  if (!row) return '';
  if (row.type === 'date') {
    return '<div class="events-date-separator" data-events-virtual-key="' + esc(row.key) + '">'
      + esc(row.label) + '</div>';
  }
  if (row.type === 'event') {
    return _renderEventEntry(row.event, row.idx, row.key);
  }
  if (row.type === 'loading') {
    return '<div class="events-loading" data-events-virtual-key="loading">Loading\u2026</div>';
  }
  return '<div class="events-log-empty" data-events-virtual-key="empty">No recent events in this view yet.</div>';
}

function _eventsSpacerHtml(className, height) {
  return '<div class="events-virtual-spacer ' + className + '" aria-hidden="true"'
    + ' style="height:' + Math.max(0, Math.round(height || 0)) + 'px"></div>';
}

function _eventsSetSpacerHeight(el, height) {
  if (!el || !el.style) return;
  el.style.height = Math.max(0, Math.round(height || 0)) + 'px';
}

function _eventsPatchVirtualLog(logEl, fullHtml, visibleHtml, visibleKeys, topHeight, bottomHeight) {
  if (!logEl || typeof logEl.querySelector !== 'function') {
    if (logEl) logEl.innerHTML = fullHtml;
    return false;
  }
  var topSpacer = logEl.querySelector('.events-virtual-top');
  var windowEl = logEl.querySelector('.events-virtual-window');
  var bottomSpacer = logEl.querySelector('.events-virtual-bottom');
  if (!topSpacer || !windowEl || !bottomSpacer) {
    if (logEl.innerHTML !== fullHtml) logEl.innerHTML = fullHtml;
    return false;
  }

  _eventsSetSpacerHeight(topSpacer, topHeight);
  _eventsSetSpacerHeight(bottomSpacer, bottomHeight);
  if (_eventsLogRenderState.visibleHtml !== visibleHtml
      || _eventsLogRenderState.visibleKeys !== visibleKeys) {
    windowEl.innerHTML = visibleHtml;
  }
  return true;
}

function _eventsMeasureVisibleRows(logEl) {
  if (!logEl || typeof logEl.querySelectorAll !== 'function') return;
  var nodes = logEl.querySelectorAll('[data-events-virtual-key]') || [];
  for (var i = 0; i < nodes.length; i++) {
    var node = nodes[i];
    var key = _eventsAnchorDataValue(node, 'eventsVirtualKey');
    var height = typeof node.offsetHeight === 'number' ? node.offsetHeight : 0;
    if (key && height > 0) _eventsMeasuredHeights[key] = height;
  }
}

function _eventsUpdateOldestId() {
  var events = (state && state.panel_events) || [];
  if (events.length > 0) _eventsOldestId = events[0].id;
}

function _eventsRenderLog(options) {
  options = options || {};
  var panel = options.panel || document.getElementById('panel-events');
  if (!panel) return;
  var logEl = options.logEl || (typeof panel.querySelector === 'function'
    ? panel.querySelector('.events-log') : null);
  if (!logEl) return;

  var anchor = options.preserveScrollTop
    ? { top: typeof logEl.scrollTop === 'number' ? logEl.scrollTop : _eventsScrollTop, pinned: false }
    : (options.anchor || _eventsCaptureVirtualLogAnchor(logEl));

  var rows = _eventsBuildVirtualRows();
  var metrics = _eventsBuildVirtualMetrics(rows);
  _eventsVirtualRows = rows;
  _eventsVirtualTops = metrics.tops;
  _eventsVirtualHeight = metrics.total;
  _eventsUpdateOldestId();

  var nextTop = Math.max(0, _eventsScrollTopForAnchor(anchor, rows, metrics.tops));
  _eventsScrollTop = nextTop;

  var viewport = typeof logEl.clientHeight === 'number' && logEl.clientHeight > 0
    ? logEl.clientHeight : 320;
  var range = _eventsVirtualRange(rows, metrics.tops, _eventsScrollTop, viewport);
  var topHeight = range.start < metrics.tops.length ? metrics.tops[range.start] : metrics.total;
  var bottomStart = range.end < metrics.tops.length ? metrics.tops[range.end] : metrics.total;
  var bottomHeight = Math.max(0, metrics.total - bottomStart);
  var visibleHtml = '';
  var visibleKeys = [];
  for (var i = range.start; i < range.end; i++) {
    visibleHtml += _eventsRenderVirtualRow(rows[i]);
    visibleKeys.push(rows[i].key);
  }
  var keysSignature = visibleKeys.join('|');
  var fullHtml = _eventsSpacerHtml('events-virtual-top', topHeight)
    + '<div class="events-virtual-window">' + visibleHtml + '</div>'
    + _eventsSpacerHtml('events-virtual-bottom', bottomHeight);

  _eventsPatchVirtualLog(logEl, fullHtml, visibleHtml, keysSignature, topHeight, bottomHeight);
  _eventsLogRenderState.visibleKeys = keysSignature;
  _eventsLogRenderState.visibleHtml = visibleHtml;
  _eventsLogRenderState.topHeight = topHeight;
  _eventsLogRenderState.bottomHeight = bottomHeight;
  _eventsMeasureVisibleRows(logEl);

  if (anchor && anchor.domAnchor && anchor.domAnchor.id) {
    _eventsRestoreScrollAnchor(logEl, '.events-entry', 'eventId', anchor.domAnchor);
    if (typeof logEl.scrollTop === 'number') _eventsScrollTop = logEl.scrollTop;
  } else if (typeof logEl.scrollTop === 'number') {
    logEl.scrollTop = nextTop;
    _eventsScrollTop = logEl.scrollTop;
  }
}

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
    },
    restore: function(root, snapshot) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      _eventsRestoreScrollAnchor(
        root.querySelector('.events-attention'),
        '.events-attention-card',
        'itemId',
        snapshot.attentionAnchor,
      );
    },
  };
  var panelState = _captureSurfaceState(panel, panelStateOptions);
  var shouldRestoreSearchFocus = _eventsSearchHadFocus
    && !(panelState && panelState.focus);

  var logEl = panel.querySelector ? panel.querySelector('.events-log') : null;
  if (logEl && typeof logEl.scrollTop === 'number') _eventsScrollTop = logEl.scrollTop;
  var logAnchor = _eventsCaptureVirtualLogAnchor(logEl);
  _eventsRememberResolveDrafts(panel);

  var grp = _eventsCurrentGroup();
  var allAttention = _eventsGetAttentionItems();
  var attention = [];
  for (var ai = 0; ai < allAttention.length; ai++) {
    if (!_eventsIsDismissed(allAttention[ai])) attention.push(allAttention[ai]);
  }
  var shellSignature = _eventsBuildShellSignature(grp, attention);
  var needsShellRender = _eventsRenderedShellSignature !== shellSignature || !logEl;

  if (needsShellRender) {
    panel.innerHTML = _eventsBuildShellHtml(grp, attention);
    _eventsRenderedShellSignature = shellSignature;
    _eventsLogRenderState = { visibleKeys: '', visibleHtml: '', topHeight: -1, bottomHeight: -1 };

    panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
      ta.style.height = 'auto';
      ta.style.height = ta.scrollHeight + 'px';
    });
    _restoreSurfaceState(panel, panelState, panelStateOptions);
  } else {
    _eventsSyncShellControls(panel);
  }

  logEl = panel.querySelector ? panel.querySelector('.events-log') : null;
  if (logEl) {
    logEl.addEventListener('scroll', _eventsOnScroll);
  }
  _eventsRenderLog({ panel: panel, logEl: logEl, anchor: logAnchor });

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
    var askLabel = item.is_architect_ask ? 'Architect asks' : 'Question';
    html += '<div class="events-attention-label">&#x2753; ' + esc(askLabel) + '</div>';
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

function _renderEventEntry(evt, idx, virtualKey) {
  var entryKey = _eventsEntryKey(evt, idx);
  var rowKey = virtualKey || ('event:' + entryKey);
  var entryKeyJs = esc(entryKey).replace(/'/g, "\\'");
  var kindClass = _eventsKindClass(evt.kind);
  var isExpanded = _eventsExpandedEntries[entryKey];
  var expanded = isExpanded ? ' expanded' : '';
  var isError = (evt.kind === 'agent_error' || evt.kind === 'agent_blocked');
  var isDismissedEngineerNote = _eventsIsDismissedEngineerNoteKind(evt.kind);
  var html = '<div class="events-entry ' + kindClass + expanded + '"'
    + ' data-event-id="' + esc(entryKey) + '"'
    + ' data-events-virtual-key="' + esc(rowKey) + '"'
    + ' onclick="eventsToggleEntry(\'' + entryKeyJs + '\')">';
  html += '<span class="events-entry-time">' + _eventsFormatTime(evt.timestamp) + '</span>';
  html += '<span class="events-entry-icon">' + _eventsKindIcon(evt.kind) + '</span>';
  if (evt.agent_name) {
    html += '<span class="events-entry-agent">' + esc(evt.agent_name) + '</span>';
  }
  if (isDismissedEngineerNote) {
    var badge = evt.kind === 'engineer_question_dismissed'
      ? 'dismissed question'
      : 'dismissed';
    html += '<span class="events-entry-badge events-entry-dismissed-badge">' + badge + '</span>';
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
  var events = _eventsCombinedEvents();
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
  _eventsRenderLog({ logEl: el, preserveScrollTop: true });
  // The log renders newest first (top) — "load more" triggers near the bottom
  if (_eventsLoading || !_eventsHasMore) return;
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) {
    _eventsLoadMore();
  }
}

function _eventsLoadMore() {
  if (_eventsLoading || !_eventsHasMore || !_eventsOldestId) return;
  _eventsLoading = true;
  var panel = document.getElementById('panel-events');
  var logEl = panel && panel.querySelector ? panel.querySelector('.events-log') : null;
  if (logEl) _eventsRenderLog({ panel: panel, logEl: logEl, preserveScrollTop: true });
  else renderEvents();
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

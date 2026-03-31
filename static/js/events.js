/* ------------------------------------------------------------------ */
/* Events panel app — attention area + activity log                    */
/* ------------------------------------------------------------------ */

var _eventsFilterByGroup = true;
var _eventsScrollTop = 0;
var _eventsExpandedEntries = {};

/* ---- Helpers -------------------------------------------------------- */

function _eventsCurrentGroup() {
  if (!_eventsFilterByGroup) return null;
  return (typeof _currentGroup === 'function') ? _currentGroup() : null;
}

function _eventsFormatTime(ts) {
  var d = new Date(ts * 1000);
  var hh = String(d.getHours()).padStart(2, '0');
  var mm = String(d.getMinutes()).padStart(2, '0');
  var ss = String(d.getSeconds()).padStart(2, '0');
  return hh + ':' + mm + ':' + ss;
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
    default:               return '\u2022';  // bullet
  }
}

function _eventsKindClass(kind) {
  if (kind === 'agent_error') return 'events-kind-error';
  if (kind === 'agent_blocked' || kind === 'agent_idle') return 'events-kind-blocked';
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
  html += '</div>';

  // Attention section
  var attention = _eventsGetAttentionItems();
  html += '<div class="events-attention">';
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
  for (var j = events.length - 1; j >= 0 && count < 200; j--) {
    var evt = events[j];
    if (grp && evt.group !== grp) continue;
    html += _renderEventEntry(evt, j);
    count++;
  }
  if (count === 0) {
    html += '<div class="events-log-empty">No events yet</div>';
  }
  html += '</div>';

  panel.innerHTML = html;

  // Restore scroll
  logEl = panel.querySelector('.events-log');
  if (logEl) logEl.scrollTop = _eventsScrollTop;

  // Auto-resize textareas
  panel.querySelectorAll('.events-resolve-textarea').forEach(function(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  });
}

/* ---- Attention card rendering --------------------------------------- */

function _renderAttentionCard(item) {
  var html = '<div class="events-attention-card events-attention-' + item.type + '">';

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
  var expanded = _eventsExpandedEntries[idx] ? ' expanded' : '';
  var html = '<div class="events-entry ' + kindClass + expanded + '"'
    + ' onclick="eventsToggleEntry(' + idx + ')">';
  html += '<span class="events-entry-time">' + _eventsFormatTime(evt.timestamp) + '</span>';
  html += '<span class="events-entry-icon">' + _eventsKindIcon(evt.kind) + '</span>';
  if (evt.agent_name) {
    html += '<span class="events-entry-agent">' + esc(evt.agent_name) + '</span>';
  }
  html += '<span class="events-entry-text">' + esc(evt.message || evt.kind) + '</span>';
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

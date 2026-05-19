/* ------------------------------------------------------------------ */
/* History panel                                                       */
/* ------------------------------------------------------------------ */

var _agentHistoryDefaultFilter = 'merged';

var _agentHistoryRecords = [];
var _agentHistoryFilter = _agentHistoryDefaultFilter; // '', 'active', 'removed', 'merged'
var _agentHistorySearch = '';
var _agentHistoryExpanded = '';     // agent ID currently expanded
var _agentHistoryDetail = null;     // detail data for expanded agent

function openHistoryPanel() {
  if (typeof _panelAppVisible === 'function' && _panelAppVisible('history')) {
    agentHistoryLoad();
    renderHistoryPanel();
    return;
  }
  if (typeof togglePanel === 'function') {
    togglePanel('history');
    return;
  }
  agentHistoryLoad();
  renderHistoryPanel();
}

function renderHistoryPanel() {
  var panel = document.getElementById('panel-history');
  if (!panel) return;
  var html = '';
  html += '<div class="tpled-header">';
  html += '<div class="tpled-header-copy">';
  html += '<div class="tpled-header-title-row">';
  html += '<span class="tpled-header-title">History</span>';
  html += '</div>';
  html += '<div class="tpled-header-subtitle">Historical agent runs and their recorded activity.</div>';
  html += '</div>';
  html += '<div class="tpled-header-controls">';
  html += '<button class="tpled-new-btn" onclick="agentHistoryLoad()" title="Refresh">&#x21BB;</button>';
  html += '</div>';
  html += '</div>';
  html += '<div class="agent-history-container" id="agent-history-container"></div>';
  panel.innerHTML = html;
  renderAgentHistoryView();
}

function agentHistoryLoad() {
  send({
    cmd: 'get_agent_history',
    status: _agentHistoryFilter,
    limit: 100,
  });
}

function agentHistoryReceiveList(msg) {
  _agentHistoryRecords = msg.records || [];
  if (document.getElementById('agent-history-container')) renderAgentHistoryView();
}

function agentHistoryReceiveDetail(msg) {
  _agentHistoryDetail = msg;
  renderAgentHistoryExpanded();
}

function _ahFmtTs(ts) {
  if (!ts) return '\u2014';
  var d = new Date(ts * 1000);
  var now = Date.now();
  var diff = now - d.getTime();
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
  if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
  return d.toLocaleDateString();
}

function _ahFmtTokens(n) {
  if (!n) return '\u2014';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return '' + n;
}

function _ahStatusBadge(status) {
  var cls = 'ah-badge-removed';
  if (status === 'active') cls = 'ah-badge-active';
  else if (status === 'merged') cls = 'ah-badge-merged';
  return '<span class="ah-badge ' + cls + '">' + esc(status) + '</span>';
}

function _ahActionIcon(action) {
  var icons = {
    done: '\u2713', ready: '\u2713', blocked: '\u26A0',
    error: '\u2717', progress: '\u25B6', derive: '\u2192',
    ask: '\u2753', name: '\u270E',
    engineer_message: '\u2709', reply: '\u21A9'
  };
  return icons[action] || '\u2022';
}

function _ahKindLabel(kind) {
  var text = String(kind || '').trim();
  if (!text) return '';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function agentHistorySetFilter(f) {
  _agentHistoryFilter = f;
  agentHistoryLoad();
}

function agentHistoryOnSearch(val) {
  _agentHistorySearch = val.toLowerCase();
  renderAgentHistoryView();
}

function agentHistoryToggle(agentId) {
  if (_agentHistoryExpanded === agentId) {
    _agentHistoryExpanded = '';
    _agentHistoryDetail = null;
    renderAgentHistoryView();
  } else {
    _agentHistoryExpanded = agentId;
    _agentHistoryDetail = null;
    renderAgentHistoryView();
    send({ cmd: 'get_agent_history_detail', agent_id: agentId });
  }
}

function ahToggleMsg(id) {
  var el = document.getElementById(id);
  if (el) el.classList.toggle('visible');
}

function agentHistoryFocusAgent(agentId) {
  send({ cmd: 'focus_agent', id: agentId });
}

function agentHistoryOpenTask(taskId) {
  if (!taskId || typeof boardNavigateToTask !== 'function') return;
  boardNavigateToTask(taskId);
}

function renderAgentHistoryView() {
  var container = document.getElementById('agent-history-container');
  if (!container) return;
  var surfaceState = (typeof _captureSurfaceState === 'function')
    ? _captureSurfaceState(container, { scrollSelectors: [':root'] })
    : null;
  var restoreSurface = function() {
    if (typeof _restoreSurfaceState === 'function') {
      _restoreSurfaceState(container, surfaceState);
    }
  };

  var html = '';

  // Search + filter bar
  html += '<div class="ah-toolbar">';
  html += '<input id="agent-history-search" class="ah-search" type="text" placeholder="Search agents\u2026" '
    + 'value="' + esc(_agentHistorySearch) + '" '
    + 'oninput="agentHistoryOnSearch(this.value)">';
  var filters = [
    ['', 'All'], ['active', 'Active'], ['removed', 'Removed'], ['merged', 'Merged']
  ];
  html += '<div class="ah-filters">';
  for (var i = 0; i < filters.length; i++) {
    var f = filters[i];
    html += '<button class="ah-filter-btn' + (_agentHistoryFilter === f[0] ? ' active' : '') + '" '
      + 'onclick="agentHistorySetFilter(\'' + f[0] + '\')">' + f[1] + '</button>';
  }
  html += '</div>';
  html += '</div>';

  // Filter records by active group, then search
  var grp = (typeof _currentGroup === 'function') ? _currentGroup() : '';
  var records = _agentHistoryRecords;
  if (grp) {
    records = records.filter(function(r) { return r.group === grp; });
  }
  if (_agentHistorySearch) {
    var query = _agentHistorySearch.toLowerCase();
    records = records.filter(function(r) {
      return (r.name || '').toLowerCase().indexOf(query) >= 0
        || (r.group || '').toLowerCase().indexOf(query) >= 0
        || (r.agent_type || '').toLowerCase().indexOf(query) >= 0
        || (r.kind || '').toLowerCase().indexOf(query) >= 0;
    });
  }

  if (!records.length) {
    var empty = 'No historical agent runs match this filter.';
    if (_agentHistoryFilter === 'merged') empty = 'No merged agent runs yet.';
    else if (_agentHistoryFilter === 'removed') empty = 'No removed agent runs yet.';
    else if (_agentHistoryFilter === 'active') empty = 'No active agent runs match this filter.';
    html += '<div class="ah-empty">' + empty + '<br>Live agents stay in the left column.</div>';
    container.innerHTML = html;
    restoreSurface();
    return;
  }

  // Agent list
  html += '<div class="ah-list">';
  for (var j = 0; j < records.length; j++) {
    var r = records[j];
    var expanded = _agentHistoryExpanded === r.id;
    var isActive = r.status === 'active';
    html += '<div class="ah-row' + (expanded ? ' expanded' : '') + '">';
    html += '<div class="ah-row-header" onclick="agentHistoryToggle(\'' + esc(r.id) + '\')">';
    html += '<span class="ah-expand-arrow">' + (expanded ? '\u25BC' : '\u25B6') + '</span>';
    html += '<span class="ah-name">' + esc(r.name) + '</span>';
    if (r.agent_type) {
      var typeInfo = (typeof AGENT_TYPE_LABELS !== 'undefined' && AGENT_TYPE_LABELS[r.agent_type]) || null;
      var typeLabel = (typeInfo && typeInfo.label) || r.agent_type;
      html += '<span class="ah-type-badge">' + esc(typeLabel) + '</span>';
    }
    if (r.kind) html += '<span class="ah-type-badge">' + esc(_ahKindLabel(r.kind)) + '</span>';
    if (r.group) html += '<span class="ah-group">' + esc(r.group) + '</span>';
    html += _ahStatusBadge(r.status);
    html += '<span class="ah-meta">' + _ahFmtTs(r.created_at) + '</span>';
    html += '<span class="ah-meta" title="Tasks">' + (r.total_tasks || 0) + ' tasks</span>';
    var tokIn = r.total_tokens_in || 0;
    var tokOut = r.total_tokens_out || 0;
    if (tokIn || tokOut) {
      html += '<span class="ah-meta ah-tokens" title="Tokens in/out">'
        + _ahFmtTokens(tokIn) + '/' + _ahFmtTokens(tokOut) + '</span>';
    }
    if (isActive) {
      html += '<button class="ah-focus-btn" onclick="event.stopPropagation();agentHistoryFocusAgent(\'' + esc(r.id) + '\')" title="Focus agent">\u2192</button>';
    }
    html += '</div>';  // ah-row-header

    // Expanded detail area
    if (expanded) {
      html += '<div class="ah-detail" id="ah-detail-' + esc(r.id) + '">';
      if (!_agentHistoryDetail) {
        html += '<div class="ah-loading">Loading\u2026</div>';
      }
      html += '</div>';
    }

    html += '</div>';  // ah-row
  }
  html += '</div>';

  container.innerHTML = html;
  restoreSurface();

  // If we have detail, render it
  if (_agentHistoryExpanded && _agentHistoryDetail) {
    renderAgentHistoryExpanded();
  }
}

function renderAgentHistoryExpanded() {
  var detail = _agentHistoryDetail;
  if (!detail || !detail.record) return;
  var el = document.getElementById('ah-detail-' + detail.record.id);
  if (!el) return;

  var r = detail.record;
  var html = '';

  // Info grid
  html += '<div class="ah-info">';
  html += '<div class="ah-info-row"><span class="ah-label">Role</span><span>' + esc(r.template || '\u2014') + '</span></div>';
  html += '<div class="ah-info-row"><span class="ah-label">Branch</span><span>' + esc(r.worktree_branch || '\u2014') + '</span></div>';
  html += '<div class="ah-info-row"><span class="ah-label">Created</span><span>' + _ahFmtTs(r.created_at) + '</span></div>';
  if (r.removed_at) {
    html += '<div class="ah-info-row"><span class="ah-label">Removed</span><span>' + _ahFmtTs(r.removed_at) + '</span></div>';
  }
  html += '<div class="ah-info-row"><span class="ah-label">Tokens</span><span>' + _ahFmtTokens(r.total_tokens_in) + ' in / ' + _ahFmtTokens(r.total_tokens_out) + ' out</span></div>';
  html += '</div>';

  // Tasks
  var tasks = detail.tasks || [];
  if (tasks.length) {
    html += '<div class="ah-section-title">Tasks (' + tasks.length + ')</div>';
    html += '<div class="ah-tasks">';
    for (var i = 0; i < tasks.length; i++) {
      var t = tasks[i];
      var outcome = t.outcome || 'in-progress';
      var outClass = 'ah-outcome-' + outcome.replace(/[^a-z]/g, '');
      var hasTask = t.task_id && (state.board_tasks || {})[t.task_id];
      html += '<div class="ah-task-row' + (hasTask ? ' ah-clickable' : '') + '"'
        + (hasTask ? ' onclick="agentHistoryOpenTask(\'' + esc(t.task_id) + '\')"' : '') + '>';
      html += '<span class="ah-task-outcome ' + outClass + '">' + esc(outcome) + '</span>';
      html += '<span class="ah-task-title">' + esc(t.task_title) + '</span>';
      html += '<span class="ah-meta">' + _ahFmtTs(t.started_at) + '</span>';
      if (hasTask) html += '<span class="ah-task-link" title="View on board">\u2192</span>';
      html += '</div>';
    }
    html += '</div>';
  }

  // Messages
  var messages = detail.messages || [];
  if (messages.length) {
    html += '<div class="ah-section-title">Messages (' + messages.length + ')</div>';
    html += '<div class="ah-messages">';
    for (var j = 0; j < messages.length; j++) {
      var m = messages[j];
      var msgId = 'ah-msg-' + (m.id || j);
      var hasText = m.message && m.message.length > 0;
      var hasTaskLink = m.task_id && (state.board_tasks || {})[m.task_id];
      html += '<div class="ah-msg-row' + (hasText ? ' ah-clickable' : '') + '"'
        + (hasText ? ' onclick="ahToggleMsg(\'' + msgId + '\')"' : '') + '>';
      html += '<span class="ah-msg-icon">' + _ahActionIcon(m.action) + '</span>';
      html += '<span class="ah-msg-action">' + esc(m.action) + '</span>';
      html += '<span class="ah-msg-text">' + esc(m.message || '') + '</span>';
      html += '<span class="ah-meta">' + _ahFmtTs(m.timestamp) + '</span>';
      if (hasTaskLink) {
        html += '<span class="ah-task-link" title="View task on board"'
          + ' onclick="event.stopPropagation();agentHistoryOpenTask(\'' + esc(m.task_id) + '\')">\u2192</span>';
      }
      html += '</div>';
      if (hasText) {
        html += '<div class="ah-msg-expanded" id="' + msgId + '">' + esc(m.message) + '</div>';
      }
    }
    html += '</div>';
  }

  if (!tasks.length && !messages.length) {
    html += '<div class="ah-empty">No activity recorded.</div>';
  }

  el.innerHTML = html;
}

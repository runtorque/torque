/* Weaver panel — Journal / Settings tabs */

var _weaverTab = 'journal';  // 'journal' | 'settings'
var _weaverCustomInstrDirty = false;
var _weaverCustomInstrDraft = '';
var _weaverReplyDraft = '';
var _weaverHealthOrder = ['blocked', 'stale-in-progress', 'stalled', 'thrashing', 'idle-risk'];
var _weaverHealthLabels = {
  'blocked': 'Blocked',
  'stale-in-progress': 'Stale in progress',
  'stalled': 'Stalled',
  'thrashing': 'Thrashing',
  'idle-risk': 'Idle risk',
};
var _weaverVerificationLabels = {
  'pending': 'Verify pending',
  'attempted': 'Verify attempted',
  'passed': 'Verified',
  'failed': 'Verify failed',
};
var _weaverHealthSeverity = {
  'healthy': 0,
  'idle-risk': 1,
  'thrashing': 2,
  'stalled': 3,
  'stale-in-progress': 4,
  'blocked': 5,
};

function renderWeaverPanel() {
  var el = document.getElementById('panel-weaver');
  if (!el) return;
  var panelState = _captureSurfaceState(el, {
    scrollSelectors: ['.weaver-content'],
    captureFocusKey(active) {
      if (active && active.classList
          && active.classList.contains('weaver-instructions')) {
        return '.weaver-instructions';
      }
      return '';
    },
  });

  var group = _weaverCurrentGroup();
  var ws = _weaverGetSettings(group);
  var weaver = group ? _weaverGetAgent(group) : null;

  var html = '<div class="weaver-panel">';

  // Header
  html += '<div class="weaver-header">';
  html += '<span class="weaver-title">Weaver';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  // Buffer stats + Pause/Resume toggle
  if (group) {
    var paused = ws && ws.paused;
    var bstats = state.weaver_buffer_stats && state.weaver_buffer_stats[group];
    html += '<div class="weaver-header-right">';
    if (bstats && bstats.buffered_events > 0) {
      var evtCount = bstats.buffered_events;
      var nextIn = bstats.next_push_in;
      var timeStr = '';
      if (nextIn <= 0) {
        timeStr = 'now';
      } else if (nextIn < 60) {
        timeStr = nextIn + 's';
      } else {
        var m = Math.floor(nextIn / 60);
        var s = nextIn % 60;
        timeStr = m + 'm' + (s > 0 ? String(s).padStart(2, '0') + 's' : '');
      }
      html += '<span class="weaver-buffer-stats">'
           + evtCount + ' event' + (evtCount !== 1 ? 's' : '')
           + (paused ? ' paused' : ' in ' + timeStr)
           + '</span>';
    }
    html += '<button class="weaver-pause-btn' + (paused ? ' paused' : '') + '" '
         + 'onclick="weaverTogglePause()">'
         + (paused ? '&#x25B6;' : '&#x23F8;')
         + '</button>';
    html += '</div>';
  }
  html += '</div>';

  // Tabs
  html += '<div class="weaver-tabs">';
  html += '<button class="weaver-tab' + (_weaverTab === 'journal' ? ' active' : '') + '" '
       + 'onclick="weaverSwitchTab(\'journal\')">Journal</button>';
  html += '<button class="weaver-tab' + (_weaverTab === 'settings' ? ' active' : '') + '" '
       + 'onclick="weaverSwitchTab(\'settings\')">Settings</button>';
  html += '</div>';

  // Tab content
  html += '<div class="weaver-content">';
  if (_weaverTab === 'journal') {
    html += _weaverRenderJournal(group);
  } else {
    html += _weaverRenderSettings(group, ws, weaver);
  }
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
  _restoreSurfaceState(el, panelState);
}

function weaverSwitchTab(tab) {
  _weaverTab = tab;
  renderWeaverPanel();
}

function weaverTogglePause() {
  var group = _weaverCurrentGroup();
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var cmd = (ws && ws.paused) ? 'weaver_resume' : 'weaver_pause';
  send({ cmd: cmd, group: group });
}

// -- Journal tab -----------------------------------------------------------

function _weaverRenderJournal(group) {
  if (!group) {
    return '<div class="weaver-empty">No weaver configured for any group.</div>';
  }

  var html = '';

  // Pending question banner
  var ws = _weaverGetSettings(group);
  if (ws && ws.pending_question) {
    html += '<div class="weaver-ask-banner">';
    html += '<div class="weaver-ask-label">Weaver is asking:</div>';
    html += '<div class="weaver-ask-question">' + _esc(ws.pending_question) + '</div>';
    html += '<textarea class="weaver-ask-reply" id="weaver-reply-input" '
         + 'placeholder="Type your reply..." rows="2" '
         + 'oninput="_weaverReplyDraft=this.value">' + _esc(_weaverReplyDraft) + '</textarea>';
    html += '<div class="weaver-ask-actions">';
    html += '<button class="weaver-dismiss-btn" onclick="weaverDismissQuestion()">Dismiss</button>';
    html += '<button class="weaver-reply-btn" onclick="weaverReply()">Send Reply</button>';
    html += '</div>';
    html += '</div>';
  }
  if (ws && ws.pending_note) {
    var noteKind = ws.pending_note_kind || 'note';
    html += '<div class="weaver-note-banner">';
    html += '<div class="weaver-note-label">'
      + (noteKind === 'question'
        ? 'Weaver asks (non-blocking):'
        : 'Weaver note:')
      + '</div>';
    html += '<div class="weaver-note-text">' + _esc(ws.pending_note) + '</div>';
    html += '<div class="weaver-note-actions">';
    html += '<button class="weaver-dismiss-btn" onclick="weaverDismissNote()">Dismiss</button>';
    html += '</div>';
    html += '</div>';
  }

  html += _weaverRenderTaskHealth(group);
  html += _weaverRenderVerificationSummary(group);
  html += _weaverRenderBoundarySummary(group);

  // Journal entries come from state.weaver_journal (populated by delta ops)
  var entries = (state.weaver_journal && state.weaver_journal[group]) || [];
  if (!entries.length && !html) {
    return '<div class="weaver-empty">No journal entries yet.</div>';
  }

  if (entries.length) {
    // Sort by id descending (newest first)
    var sorted = entries.slice().sort(function(a, b) { return b.id - a.id; });
    html += '<div class="weaver-journal">';
    for (var i = 0; i < sorted.length; i++) {
      var e = sorted[i];
      var typeClass = 'weaver-badge-' + (e.type || 'observation');
      var ago = _weaverTimeAgo(e.timestamp);
      html += '<div class="weaver-entry" oncontextmenu="weaverEntryCtx(event,' + e.id + ')">';
      html += '<div class="weaver-entry-header">';
      html += '<span class="weaver-badge ' + typeClass + '">' + _esc(e.type || '?') + '</span>';
      html += '<span class="weaver-entry-time">' + ago + '</span>';
      html += '</div>';
      html += '<div class="weaver-entry-text">' + _esc(e.entry || '') + '</div>';
      html += '</div>';
    }
    html += '</div>';
  }
  return html;
}

function _weaverRenderTaskHealth(group) {
  var summary = _weaverTaskHealthSummary(group);
  if (!summary.total) return '';

  var html = '<div class="weaver-health-summary">';
  html += '<div class="weaver-health-header">';
  html += '<span class="weaver-health-title">Task health</span>';
  html += '<span class="weaver-health-total">' + summary.total + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="weaver-health-counts">';
  for (var i = 0; i < _weaverHealthOrder.length; i++) {
    var stateName = _weaverHealthOrder[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="weaver-health-pill weaver-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_weaverHealthLabels[stateName]) + '</span>';
  }
  html += '</div>';
  if (summary.items.length) {
    html += '<div class="weaver-health-list">';
    for (var j = 0; j < summary.items.length; j++) {
      var item = summary.items[j];
      html += '<div class="weaver-health-item">';
      html += '<span class="weaver-health-item-state weaver-health-pill-' + _esc(item.health_state) + '">'
        + _esc(_weaverHealthLabels[item.health_state] || item.health_state) + '</span>';
      html += '<span class="weaver-health-item-title">' + _esc(item.title) + '</span>';
      if (item.via) {
        html += '<span class="weaver-health-item-via">via ' + _esc(item.via) + '</span>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _weaverRenderVerificationSummary(group) {
  var summary = _weaverVerificationSummary(group);
  if (!summary.total) return '';

  var html = '<div class="weaver-verification-summary">';
  html += '<div class="weaver-health-header">';
  html += '<span class="weaver-health-title">Verification</span>';
  html += '<span class="weaver-health-total">' + summary.total + ' open checkpoint' + (summary.total === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="weaver-health-counts">';
  for (var i = 0; i < summary.order.length; i++) {
    var stateName = summary.order[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="weaver-health-pill weaver-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_weaverVerificationLabels[stateName] || stateName) + '</span>';
  }
  html += '</div>';
  for (var j = 0; j < summary.items.length; j++) {
    var item = summary.items[j];
    html += '<div class="weaver-verification-item">';
    html += '<span class="weaver-health-pill weaver-health-pill-' + _esc(item.verification_state) + '">'
      + _esc(_weaverVerificationLabels[item.verification_state] || item.verification_state) + '</span>';
    html += '<span class="weaver-verification-item-title">' + _esc(item.title) + '</span>';
    if (item.verification_mode) {
      html += '<span class="weaver-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="weaver-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _weaverRenderBoundarySummary(group) {
  if (!group || !state || !state.agents) return '';
  var items = [];
  var seen = {};
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent' || agent.group !== group) continue;
    var settings = (state.group_settings || {})[group] || {};
    if (settings.weaver_agent_id === agent.id) continue;
    var overview = typeof _branchBoundaryOverviewForAgent === 'function'
      ? _branchBoundaryOverviewForAgent(agent)
      : null;
    if (!overview || !overview.latest_boundary_task) continue;
    var key = (overview.repo_root || '') + '::' + (overview.branch || '');
    if (seen[key]) continue;
    seen[key] = true;
    items.push({
      agent_name: agent.name,
      branch: overview.branch || '',
      current_task: overview.current_task ? overview.current_task.task : '',
      latest_boundary_task: overview.latest_boundary_task.task || '',
      queued_followers: overview.queued_followers || [],
      started_followers: overview.started_followers || [],
      partial_review_safe: !!overview.partial_review_safe,
    });
  }
  items.sort(function(a, b) {
    return (a.branch || a.agent_name || '').localeCompare(
      b.branch || b.agent_name || ''
    );
  });
  if (!items.length) return '';

  var html = '<div class="weaver-health-summary">';
  html += '<div class="weaver-health-header">';
  html += '<span class="weaver-health-title">Branch review points</span>';
  html += '<span class="weaver-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="weaver-verification-item">';
    html += '<span class="weaver-health-pill weaver-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="weaver-verification-item-title">' + _esc(item.latest_boundary_task) + '</span>';
    if (item.branch) {
      html += '<span class="weaver-verification-item-meta">' + _esc(item.branch.replace(/^loom\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.current_task) {
      html += '<div class="weaver-verification-item-meta">Current: ' + _esc(item.current_task) + '</div>';
    }
    if (item.queued_followers.length) {
      html += '<div class="weaver-verification-item-meta">Queued next: '
        + _esc(item.queued_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
    if (item.started_followers.length) {
      html += '<div class="weaver-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _weaverTaskHealthSummary(group) {
  var summary = {
    counts: { 'blocked': 0, 'stalled': 0, 'thrashing': 0, 'idle-risk': 0 },
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var healthState = task.health_state || 'healthy';
    if (healthState === 'healthy') continue;
    summary.counts[healthState] = (summary.counts[healthState] || 0) + 1;
    summary.total += 1;
    var details = task.health_details || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      health_state: healthState,
      health_since: task.health_since || '',
      via: details.aggregate ? (details.source_task_title || '') : '',
    });
  }
  summary.items.sort(function(a, b) {
    var sev = (_weaverHealthSeverity[b.health_state] || 0) - (_weaverHealthSeverity[a.health_state] || 0);
    if (sev) return sev;
    var timeCmp = (a.health_since || '').localeCompare(b.health_since || '');
    if (timeCmp) return timeCmp;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

function _weaverVerificationSummary(group) {
  var summary = {
    counts: { pending: 0, attempted: 0, passed: 0, failed: 0 },
    order: ['failed', 'pending', 'attempted', 'passed'],
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var verificationState = task.verification_state || '';
    if (!verificationState || !summary.counts.hasOwnProperty(verificationState)) continue;
    summary.counts[verificationState] += 1;
    summary.total += 1;
    var verificationSummary = task.verification_summary || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      verification_state: verificationState,
      verification_mode: task.verification_mode || '',
      detail: verificationSummary.human_validation_pending
        || task.verification_notes
        || verificationSummary.tests_run
        || '',
    });
  }
  summary.items.sort(function(a, b) {
    var aRank = summary.order.indexOf(a.verification_state);
    var bRank = summary.order.indexOf(b.verification_state);
    if (aRank !== bRank) return aRank - bRank;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

// -- Settings tab ----------------------------------------------------------

function _weaverRenderSettings(group, ws, weaver) {
  var html = '';
  if (!group) {
    return '<div class="weaver-empty">Create a group first.</div>';
  }

  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Settings moved</div>';
  html += '<div class="weaver-empty-inline">Create a Weaver from the group’s + New dropdown. Configure it in Group Settings → Weaver.</div>';
  html += '<button class="weaver-create-btn" onclick="weaverOpenSettings()">Open Group Settings</button>';
  html += '</div>';

  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Current configuration</div>';
  if (weaver) {
    html += '<div class="weaver-agent-row">';
    html += '<span class="weaver-agent-name">' + _esc(weaver.name) + '</span>';
    html += '<span class="weaver-agent-status status-' + (weaver.status || 'stopped') + '">'
         + _esc(weaver.status || 'stopped') + '</span>';
    html += '</div>';
  } else {
    html += '<div class="weaver-empty-inline">No weaver agent configured for this group yet.</div>';
  }
  html += '<div class="weaver-field"><label>Provider override</label><div class="weaver-field-note">'
       + _esc((ws && ws.weaver_provider) || 'Group default') + '</div></div>';
  html += '<div class="weaver-field"><label>Command override</label><div class="weaver-field-note">'
       + _esc((ws && ws.weaver_boot_command) || 'Use provider default') + '</div></div>';
  html += '<div class="weaver-field"><label>Custom instructions</label><div class="weaver-field-note">'
       + _esc((ws && ws.custom_instructions) || 'None') + '</div></div>';
  html += '</div>';
  return html;
}

// -- Journal context menu --------------------------------------------------

function weaverEntryCtx(e, entryId) {
  e.preventDefault();
  e.stopPropagation();
  showContextMenu(e.clientX, e.clientY, [
    { label: 'Delete entry', danger: true, action: 'weaverDeleteEntry(' + entryId + ')' },
  ]);
}

function weaverDeleteEntry(entryId) {
  var group = _weaverCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_journal_delete', group: group, entry_id: entryId });
  // Optimistic removal from local state
  if (state.weaver_journal && state.weaver_journal[group]) {
    state.weaver_journal[group] = state.weaver_journal[group].filter(
      function(e) { return e.id !== entryId; });
  }
  renderWeaverPanel();
}

// -- Human reply -----------------------------------------------------------

function weaverReply() {
  var input = document.getElementById('weaver-reply-input');
  if (!input) return;
  var answer = input.value.trim();
  if (!answer) return;
  var group = _weaverCurrentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  // Blur so the re-render skip guard doesn't block the banner clearing
  input.blur();
  send({ cmd: 'weaver_reply', group: group, answer: answer });
}

function weaverDismissQuestion() {
  var group = _weaverCurrentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  send({ cmd: 'weaver_resume', group: group });
}

function weaverDismissNote() {
  var group = _weaverCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_dismiss_note', group: group });
}

function weaverOpenSettings() {
  var group = _weaverCurrentGroup();
  if (!group || typeof openGroupSettings !== 'function') return;
  openGroupSettings(group, 'weaver');
}

// -- Event handlers --------------------------------------------------------

function weaverInstrInput(textarea) {
  _weaverCustomInstrDirty = true;
  _weaverCustomInstrDraft = textarea.value;
  // Show save button (re-render just the settings section would be heavy;
  // instead just toggle the button visibility)
  var btn = textarea.parentElement.querySelector('.weaver-save-btn');
  if (!btn) {
    var b = document.createElement('button');
    b.className = 'weaver-save-btn';
    b.textContent = 'Save';
    b.onclick = weaverSaveInstructions;
    textarea.parentElement.appendChild(b);
  }
}

function weaverSaveInstructions() {
  var group = _weaverCurrentGroup();
  if (!group) return;
  send({
    cmd: 'weaver_update_settings',
    group: group,
    custom_instructions: _weaverCustomInstrDraft,
  });
  _weaverCustomInstrDirty = false;
  _weaverCustomInstrDraft = '';
}

function weaverUpdateSetting(key, value) {
  var group = _weaverCurrentGroup();
  if (!group) return;
  var payload = { cmd: 'weaver_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function weaverToggleEvent(evt, enabled) {
  var group = _weaverCurrentGroup();
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var current = (ws && ws.enabled_events) ? ws.enabled_events.slice() : [];
  if (enabled && current.indexOf(evt) < 0) {
    current.push(evt);
  } else if (!enabled) {
    current = current.filter(function(e) { return e !== evt; });
  }
  send({
    cmd: 'weaver_update_settings',
    group: group,
    enabled_events: current,
  });
}

// -- Helpers ---------------------------------------------------------------

function _weaverGetSettings(group) {
  if (!group || !state.weaver_settings) return null;
  return state.weaver_settings[group] || null;
}

function _weaverCurrentGroup() {
  if (typeof _focusedGroup === 'function') {
    var focused = _focusedGroup();
    if (focused) return focused;
  }
  if (state && state.active_session_id && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (agent && agent.session_id === state.active_session_id) {
        return agent.group || '';
      }
    }
  }
  if (typeof _currentGroup === 'function') {
    var group = _currentGroup();
    if (group) return group;
  }
  if (state && state.groups) {
    var groups = Object.keys(state.groups || {});
    if (groups.length) return groups[0];
  }
  return '';
}

function _weaverGetAgent(group) {
  if (!group || !state.group_settings) return null;
  var gs = state.group_settings[group];
  if (!gs || !gs.weaver_agent_id) return null;
  return state.agents ? state.agents[gs.weaver_agent_id] : null;
}

function _weaverTimeAgo(ts) {
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
          .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* Weaver panel — Journal / Settings tabs */

var _weaverTab = 'journal';  // 'journal' | 'settings'
var _weaverCustomInstrDirty = false;
var _weaverCustomInstrDraft = '';
var _weaverReplyDraft = '';
var _weaverHealthOrder = ['blocked', 'stalled', 'thrashing', 'idle-risk'];
var _weaverHealthLabels = {
  'blocked': 'Blocked',
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
  'blocked': 4,
};

function renderWeaverPanel() {
  var el = document.getElementById('panel-weaver');
  if (!el) return;

  // Don't re-render while user is typing in the reply box or instructions
  var active = document.activeElement;
  if (active && (active.id === 'weaver-reply-input' ||
                 active.classList.contains('weaver-instructions'))) {
    return;
  }

  var group = _currentGroup();
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
}

function weaverSwitchTab(tab) {
  _weaverTab = tab;
  renderWeaverPanel();
}

function weaverTogglePause() {
  var group = _currentGroup();
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

  html += _weaverRenderTaskHealth(group);
  html += _weaverRenderVerificationSummary(group);

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

  // Agent section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Agent</div>';
  if (weaver) {
    html += '<div class="weaver-agent-row">';
    html += '<span class="weaver-agent-name">' + _esc(weaver.name) + '</span>';
    html += '<span class="weaver-agent-status status-' + (weaver.status || 'stopped') + '">'
         + _esc(weaver.status || 'stopped') + '</span>';
    html += '</div>';
  } else if (group) {
    html += '<div class="weaver-create-row">';
    html += '<span class="weaver-empty-inline">No weaver agent.</span>';
    html += '<button class="weaver-create-btn" onclick="weaverCreate()">'
         + '+ Create Weaver</button>';
    html += '</div>';
  } else {
    html += '<div class="weaver-empty">Create a group first.</div>';
  }
  html += '</div>';

  // Backend section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Backend</div>';
  var wprov = (ws && ws.weaver_provider) || '';
  html += '<div class="weaver-field"><label>Provider</label>';
  html += '<select onchange="weaverUpdateSetting(\'weaver_provider\', this.value)">';
  html += '<option value=""' + (wprov === '' ? ' selected' : '') + '>Group default</option>';
  for (var i = 0; i < _cachedProviders.length; i++) {
    var p = _cachedProviders[i];
    var sel = wprov === p.name ? ' selected' : '';
    html += '<option value="' + _esc(p.name) + '"' + sel + '>' + _esc(p.display_name) + '</option>';
  }
  html += '</select></div>';
  var wbootcmd = (ws && ws.weaver_boot_command) || '';
  html += '<div class="weaver-field"><label>Command override</label>';
  html += '<input type="text" value="' + _esc(wbootcmd) + '" '
       + 'placeholder="Use provider default" '
       + 'onchange="weaverUpdateSetting(\'weaver_boot_command\', this.value.trim())">';
  html += '</div>';
  html += '</div>';

  // Custom Instructions section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Custom Instructions</div>';
  var ci = _weaverCustomInstrDirty
    ? _weaverCustomInstrDraft
    : (ws ? ws.custom_instructions || '' : '');
  html += '<textarea class="weaver-instructions" '
       + 'placeholder="Instructions appended to the weaver system prompt..." '
       + 'oninput="weaverInstrInput(this)">' + _esc(ci) + '</textarea>';
  if (_weaverCustomInstrDirty) {
    html += '<button class="weaver-save-btn" onclick="weaverSaveInstructions()">Save</button>';
  }
  html += '</div>';

  // Notifications section
  html += '<div class="weaver-section">';
  html += '<div class="weaver-section-title">Notifications</div>';

  var pushInt = (ws && ws.push_interval) || 60;
  var maxInt = (ws && ws.max_interval) || 300;
  html += '<div class="weaver-field"><label>Push interval</label>';
  html += '<select onchange="weaverUpdateSetting(\'push_interval\', +this.value)">';
  [10, 30, 60, 120, 300].forEach(function(v) {
    var sel = v === pushInt ? ' selected' : '';
    html += '<option value="' + v + '"' + sel + '>' + v + 's</option>';
  });
  html += '</select></div>';

  html += '<div class="weaver-field"><label>Max interval</label>';
  html += '<select onchange="weaverUpdateSetting(\'max_interval\', +this.value)">';
  [60, 120, 300, 600].forEach(function(v) {
    var sel = v === maxInt ? ' selected' : '';
    html += '<option value="' + v + '"' + sel + '>' + v + 's</option>';
  });
  html += '</select></div>';

  // Event checkboxes
  var mandatory = ['task_completed', 'agent_error', 'agent_reply',
                   'agent_blocked', 'ask_created'];
  var optional = ['agent_started', 'task_dispatched', 'task_derived',
                  'agent_progress', 'task_health_alert'];
  var enabled = (ws && ws.enabled_events) || [];

  html += '<div class="weaver-events-list">';
  mandatory.forEach(function(evt) {
    html += '<label class="weaver-event-check mandatory">'
         + '<input type="checkbox" checked disabled>'
         + '<span>' + evt + ' (mandatory)</span></label>';
  });
  optional.forEach(function(evt) {
    var checked = enabled.indexOf(evt) >= 0 ? ' checked' : '';
    html += '<label class="weaver-event-check">'
         + '<input type="checkbox"' + checked
         + ' onchange="weaverToggleEvent(\'' + evt + '\', this.checked)">'
         + '<span>' + evt + '</span></label>';
  });
  html += '</div>';
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
  var group = _currentGroup();
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
  var group = _currentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  // Blur so the re-render skip guard doesn't block the banner clearing
  input.blur();
  send({ cmd: 'weaver_reply', group: group, answer: answer });
}

function weaverDismissQuestion() {
  var group = _currentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  send({ cmd: 'weaver_resume', group: group });
}

// -- Create weaver ---------------------------------------------------------

function weaverCreate() {
  var group = _currentGroup();
  // If no group has settings yet, use the first group
  if (!group) {
    var groups = Object.keys(state.groups || {});
    if (!groups.length) return;
    group = groups[0];
  }
  // Check if group already has a weaver
  var gs = state.group_settings && state.group_settings[group];
  if (gs && gs.weaver_agent_id) return;

  send({
    cmd: 'add_agent',
    name: 'Weaver',
    group: group,
    is_weaver: true,
  });
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
  var group = _currentGroup();
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
  var group = _currentGroup();
  if (!group) return;
  var payload = { cmd: 'weaver_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function weaverToggleEvent(evt, enabled) {
  var group = _currentGroup();
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

/* Weaver panel — journal + events + worklog */

var _weaverReplyDraft = '';
var _weaverActiveTabByGroup = {};
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
var _weaverEventsCountdownTimer = 0;
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
  _weaverStopEventsCountdownTimer();
  if (!el) return;
  var panelStateOptions = {
    scrollSelectors: ['.weaver-content'],
    captureFocusKey(active) {
      if (active && active.classList
          && active.classList.contains('weaver-instructions')) {
        return '.weaver-instructions';
      }
      return '';
    },
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      snapshot.anchor = _weaverCaptureScrollAnchor(
        root.querySelector('.weaver-content')
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      _weaverRestoreScrollAnchor(
        root.querySelector('.weaver-content'),
        snapshot.anchor
      );
    },
  };
  var panelState = _captureSurfaceState(el, panelStateOptions);

  var group = _weaverCurrentGroup();
  var ws = _weaverGetSettings(group);
  var weaver = group ? _weaverGetAgent(group) : null;
  var bstats = (state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null;
  var paused = !!(ws && ws.paused);
  var activeTab = _weaverActiveTab(group);

  var html = '<div class="weaver-panel">';

  // Header
  html += '<div class="weaver-header">';
  html += '<span class="weaver-title">Weaver';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  // Buffer stats + Pause/Resume toggle
  if (group) {
    html += '<div class="weaver-header-right">';
    if (bstats && bstats.buffered_events > 0) {
      html += '<span class="weaver-buffer-stats">'
           + _esc(_weaverHeaderBufferStats(bstats, paused, weaver))
           + '</span>';
    }
    html += '<button id="weaver-pause-btn" class="weaver-pause-btn' + (paused ? ' paused' : '') + '" '
         + 'onclick="weaverTogglePause()">'
         + (paused ? '&#x25B6;' : '&#x23F8;')
         + '</button>';
    html += '</div>';
  }
  html += '</div>';

  html += _weaverRenderTabs(group, activeTab);
  html += '<div class="weaver-content">';
  if (activeTab === 'events') {
    html += _weaverRenderEvents(group, ws, weaver, bstats);
  } else if (activeTab === 'worklog') {
    html += _weaverRenderWorklog(group, ws);
  } else {
    html += _weaverRenderJournal(group);
  }
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
  _restoreSurfaceState(el, panelState, panelStateOptions);
  _weaverSyncEventsCountdown(el, group, activeTab);
}

function weaverTogglePause() {
  weaverTogglePauseForGroup(_weaverCurrentGroup());
}

function weaverTogglePauseForGroup(group) {
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var cmd = (ws && ws.paused) ? 'weaver_resume' : 'weaver_pause';
  send({ cmd: cmd, group: group });
}

function weaverSelectTab(tab, group) {
  group = group || _weaverCurrentGroup();
  if (!group) return;
  if (tab !== 'events' && tab !== 'worklog') tab = 'journal';
  _weaverActiveTabByGroup[group] = tab;
  renderWeaverPanel();
}

function weaverSendNow() {
  var group = _weaverCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_flush_now', group: group });
}

function _weaverActiveTab(group) {
  if (!group) return 'journal';
  var tab = _weaverActiveTabByGroup[group] || 'journal';
  if (tab === 'events' || tab === 'worklog') return tab;
  return 'journal';
}

function _weaverRenderTabs(group, activeTab) {
  if (!group) return '';
  var html = '<div class="weaver-tabs">';
  html += '<button id="weaver-tab-journal" class="weaver-tab'
    + (activeTab === 'journal' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'journal\')">Journal</button>';
  html += '<button id="weaver-tab-events" class="weaver-tab'
    + (activeTab === 'events' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'events\')">Events</button>';
  html += '<button id="weaver-tab-worklog" class="weaver-tab'
    + (activeTab === 'worklog' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'worklog\')">Worklog</button>';
  html += '</div>';
  return html;
}

function _weaverRenderEvents(group, ws, weaver, bstats) {
  if (!group) {
    return '<div class="weaver-empty">No weaver configured for any group.</div>';
  }

  var queued = (bstats && bstats.queued_events) ? bstats.queued_events.slice() : [];
  var sent = (state.weaver_sent_events && state.weaver_sent_events[group])
    ? state.weaver_sent_events[group].slice()
    : [];
  var paused = !!(ws && ws.paused);
  var sendDisabled = paused || !queued.length;
  var statusText = _weaverEventsStatusText(bstats, paused, weaver);

  sent.sort(function(a, b) {
    var deliveredDiff = (b.delivered_at || 0) - (a.delivered_at || 0);
    if (deliveredDiff) return deliveredDiff;
    return (b.id || 0) - (a.id || 0);
  });

  var html = '<div class="weaver-events-tab">';
  html += '<div class="weaver-events-toolbar">';
  html += '<div class="weaver-events-countdown">' + _esc(statusText) + '</div>';
  html += '<button id="weaver-send-now-btn" class="weaver-send-now-btn"'
    + (sendDisabled ? ' disabled' : '')
    + ' onclick="weaverSendNow()">Send queued now</button>';
  html += '</div>';
  html += _weaverRenderEventSection(
    'Queued for next digest',
    queued,
    'queued',
    'No queued events.'
  );
  html += _weaverRenderEventSection(
    'Already sent to Weaver',
    sent,
    'sent',
    'No digested events yet.'
  );
  html += '</div>';
  return html;
}

function _weaverRenderEventSection(title, events, mode, emptyText) {
  var html = '<div class="weaver-event-section">';
  html += '<div class="weaver-event-section-header">';
  html += '<span class="weaver-event-section-title">' + _esc(title) + '</span>';
  html += '<span class="weaver-event-section-count">' + events.length + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="weaver-event-empty">' + _esc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="weaver-event-list">';
  for (var i = 0; i < events.length; i++) {
    html += _weaverRenderEventItem(events[i], mode);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverRenderEventItem(event, mode) {
  var kind = _weaverEventKindLabel(event && event.kind);
  var agentName = event && event.agent_name ? String(event.agent_name) : '';
  var message = event && event.message ? String(event.message) : '';
  var summary = agentName && message
    ? agentName + ' — ' + message
    : (message || agentName || kind);
  var meta = (mode === 'sent')
    ? 'sent ' + _weaverTimeAgo(event && event.delivered_at)
    : 'queued ' + _weaverTimeAgo(event && event.timestamp);
  if (mode === 'sent' && event && event.timestamp && event.delivered_at
      && Math.abs(event.delivered_at - event.timestamp) >= 30) {
    meta += ' · event ' + _weaverTimeAgo(event.timestamp);
  }
  var anchorKey = mode + '-' + String(event && event.id ? event.id : ('idx-' + meta));
  if (mode === 'sent' && event && event.delivered_at) {
    anchorKey += '-' + Math.floor(event.delivered_at);
  }

  var html = '<div class="weaver-event-item weaver-event-item-' + _esc(mode) + '"'
    + ' data-weaver-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="weaver-event-item-header">';
  html += '<span class="weaver-event-kind">' + _esc(kind) + '</span>';
  html += '<span class="weaver-event-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  html += '<div class="weaver-event-message">' + _esc(summary) + '</div>';
  if (event && event.task_id) {
    html += '<div class="weaver-event-task">' + _esc(event.task_id) + '</div>';
  }
  html += '</div>';
  return html;
}

function _weaverHeaderBufferStats(bstats, paused, weaver) {
  if (!bstats || !bstats.buffered_events) return '';
  var evtCount = bstats.buffered_events;
  var label = evtCount + ' event' + (evtCount === 1 ? '' : 's');
  var nextPushIn = _weaverCountdownSeconds(bstats);
  if (paused) return label + ' paused';
  if (bstats.manual_flush_requested) {
    if (weaver && weaver.activity && weaver.activity !== 'waiting') {
      return label + ' queued for idle send';
    }
    return label + ' sending';
  }
  if (nextPushIn <= 0) return label + ' ready';
  return label + ' in ' + _weaverFormatCountdown(nextPushIn);
}

function _weaverEventsStatusText(bstats, paused, weaver) {
  if (!bstats || !bstats.buffered_events) {
    return 'No queued events.';
  }
  if (paused) {
    return 'Delivery is paused — resume to send queued events.';
  }
  if (bstats.manual_flush_requested) {
    if (weaver && weaver.activity && weaver.activity !== 'waiting') {
      return 'Send requested — queued events will deliver when Weaver goes idle.';
    }
    return 'Sending queued events now.';
  }
  var nextPushIn = _weaverCountdownSeconds(bstats);
  if (nextPushIn <= 0) {
    if (weaver && weaver.activity && weaver.activity !== 'waiting') {
      return 'Eligible now — waiting for Weaver to go idle.';
    }
    return 'Eligible to send now.';
  }
  return 'Next eligible send in ' + _weaverFormatCountdown(nextPushIn) + '.';
}

function _weaverFormatCountdown(seconds) {
  var remaining = Math.max(0, Number(seconds) || 0);
  if (remaining < 60) return remaining + 's';
  var minutes = Math.floor(remaining / 60);
  var secs = remaining % 60;
  return minutes + 'm' + (secs > 0 ? String(secs).padStart(2, '0') + 's' : '');
}

function _weaverCountdownSeconds(bstats) {
  if (!bstats) return 0;
  var nextPushAt = Number(bstats.next_push_at || 0);
  if (nextPushAt > 0) {
    return Math.max(0, Math.ceil(nextPushAt - (Date.now() / 1000)));
  }
  return Math.max(0, Math.ceil(Number(bstats.next_push_in || 0)));
}

function _weaverShouldLiveUpdateCountdown(group, activeTab) {
  if (!group || activeTab !== 'events') return false;
  var ws = _weaverGetSettings(group);
  var bstats = (state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null;
  if (!bstats || !bstats.buffered_events) return false;
  if (ws && ws.paused) return false;
  if (bstats.manual_flush_requested) return false;
  return _weaverCountdownSeconds(bstats) > 0;
}

function _weaverStopEventsCountdownTimer() {
  if (_weaverEventsCountdownTimer && typeof clearInterval === 'function') {
    clearInterval(_weaverEventsCountdownTimer);
  }
  _weaverEventsCountdownTimer = 0;
}

function _weaverSyncEventsCountdown(panel, group, activeTab) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var countdownEl = panel.querySelector('.weaver-events-countdown');
  if (!countdownEl) return;
  var ws = _weaverGetSettings(group);
  var weaver = group ? _weaverGetAgent(group) : null;
  var bstats = (state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null;
  countdownEl.textContent = _weaverEventsStatusText(
    bstats,
    !!(ws && ws.paused),
    weaver
  );
  if (!_weaverShouldLiveUpdateCountdown(group, activeTab)
      || typeof setInterval !== 'function') {
    return;
  }
  _weaverEventsCountdownTimer = setInterval(function() {
    var currentPanel = document.getElementById('panel-weaver');
    if (!currentPanel) {
      _weaverStopEventsCountdownTimer();
      return;
    }
    var currentGroup = _weaverCurrentGroup();
    var currentTab = _weaverActiveTab(currentGroup);
    var currentCountdown = currentPanel.querySelector('.weaver-events-countdown');
    if (!currentCountdown || currentTab !== 'events') {
      _weaverStopEventsCountdownTimer();
      return;
    }
    var currentSettings = _weaverGetSettings(currentGroup);
    var currentWeaver = currentGroup ? _weaverGetAgent(currentGroup) : null;
    var currentStats = (
      state.weaver_buffer_stats && state.weaver_buffer_stats[currentGroup]
    ) || null;
    currentCountdown.textContent = _weaverEventsStatusText(
      currentStats,
      !!(currentSettings && currentSettings.paused),
      currentWeaver
    );
    if (!_weaverShouldLiveUpdateCountdown(currentGroup, currentTab)) {
      _weaverStopEventsCountdownTimer();
    }
  }, 1000);
}

function _weaverEventKindLabel(kind) {
  kind = String(kind || '');
  if (!kind) return 'event';
  return kind.replace(/_/g, ' ');
}

function _weaverCaptureScrollAnchor(container) {
  if (!container || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function') {
    return null;
  }
  var items = container.querySelectorAll('[data-weaver-anchor]');
  if (!items || !items.length) return null;
  var containerRect = container.getBoundingClientRect();
  var best = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item || typeof item.getBoundingClientRect !== 'function') continue;
    var rect = item.getBoundingClientRect();
    if (rect.bottom >= containerRect.top) {
      best = item;
      break;
    }
  }
  if (!best) best = items[0];
  if (!best || typeof best.getBoundingClientRect !== 'function') return null;
  var anchorRect = best.getBoundingClientRect();
  return {
    key: best.getAttribute ? best.getAttribute('data-weaver-anchor') : '',
    offset: anchorRect.top - containerRect.top,
  };
}

function _weaverRestoreScrollAnchor(container, snapshot) {
  if (!container || !snapshot || !snapshot.key
      || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function'
      || typeof container.scrollTop !== 'number') {
    return;
  }
  var items = container.querySelectorAll('[data-weaver-anchor]');
  var target = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var key = item && item.getAttribute ? item.getAttribute('data-weaver-anchor') : '';
    if (key === snapshot.key) {
      target = item;
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
}

// -- Worklog tab ----------------------------------------------------------

function _weaverRenderWorklog(group, ws) {
  if (!group) {
    return '<div class="weaver-empty">No weaver configured for any group.</div>';
  }

  var entries = (state.weaver_worklog && state.weaver_worklog[group])
    ? state.weaver_worklog[group].slice()
    : [];
  if (ws && ws.restrict_to_created_agents) {
    entries = entries.filter(function(entry) {
      return !!(entry && entry.agent_owned);
    });
  }
  entries.sort(function(a, b) {
    var startedDiff = (b.started_at || 0) - (a.started_at || 0);
    if (startedDiff) return startedDiff;
    return (b.id || 0) - (a.id || 0);
  });

  var html = '<div class="weaver-worklog-tab">';
  html += '<div class="weaver-worklog-header">';
  html += '<span class="weaver-worklog-title">Dispatched tasks</span>';
  html += '<span class="weaver-worklog-count">' + entries.length + '</span>';
  html += '</div>';
  if (ws && ws.restrict_to_created_agents) {
    html += '<div class="weaver-worklog-note">Showing only tasks sent to Weaver-created agents.</div>';
  } else {
    html += '<div class="weaver-worklog-note">Recent tasks this Weaver dispatched in this group.</div>';
  }

  if (!entries.length) {
    html += '<div class="weaver-event-empty">No dispatched tasks yet.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="weaver-worklog-list">';
  for (var i = 0; i < entries.length; i++) {
    html += _weaverRenderWorklogItem(entries[i]);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverRenderWorklogItem(entry) {
  var task = (state.board_tasks && entry && entry.task_id)
    ? state.board_tasks[entry.task_id]
    : null;
  var title = (task && task.task) || (entry && entry.task_title) || (entry && entry.task_id) || 'Task';
  var taskId = (entry && entry.task_id) || '';
  var lane = task ? (task.lane || '') : 'Not on board';
  var status = task ? String(task.status || '').trim() : '';
  var agentName = _weaverWorklogAgentLabel(entry, task);
  var meta = 'dispatched ' + _weaverTimeAgo(entry && entry.started_at);
  var anchorKey = 'worklog-' + String(entry && entry.id ? entry.id : taskId || meta);

  var html = '<div class="weaver-worklog-item" data-weaver-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="weaver-worklog-item-header">';
  html += '<div class="weaver-worklog-task">';
  html += '<div class="weaver-worklog-task-title">' + _esc(title) + '</div>';
  if (taskId) {
    html += '<div class="weaver-worklog-task-id">' + _esc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="weaver-worklog-lane">' + _esc(lane || 'Unknown') + '</div>';
  html += '</div>';
  html += '<div class="weaver-worklog-meta-row">';
  html += '<span class="weaver-worklog-agent">' + _esc(agentName) + '</span>';
  html += '<span class="weaver-worklog-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="weaver-worklog-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _weaverWorklogAgentLabel(entry, task) {
  var taskAgent = (task && task.agent_id && state.agents && state.agents[task.agent_id])
    ? state.agents[task.agent_id]
    : null;
  if (taskAgent) {
    return taskAgent.name || taskAgent.slug || taskAgent.id || 'Agent';
  }
  if (entry && entry.agent_name) return String(entry.agent_name);
  if (entry && entry.agent_slug) return String(entry.agent_slug);
  if (entry && entry.agent_id) return String(entry.agent_id);
  return 'Agent';
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

  html += _weaverRenderOpenStreams(group);
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

function _weaverRenderOpenStreams(group) {
  var summary = _weaverOpenStreamsSummary(group);
  if (!summary.show) return '';

  var html = '<div class="weaver-streams-summary">';
  html += '<div class="weaver-health-header">';
  html += '<span class="weaver-health-title">Open Streams</span>';
  html += '<span class="weaver-health-total">' + summary.streams.length + ' open stream'
    + (summary.streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';

  if (!summary.streams.length) {
    html += '<div class="weaver-stream-empty">No open streams.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="weaver-stream-list">';
  for (var i = 0; i < summary.streams.length; i++) {
    html += _weaverRenderOpenStreamCard(summary.streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverOpenStreamsSummary(group) {
  var result = { show: false, streams: [] };
  if (!group || !state || !state.weaver_streams) return result;
  if (!Object.prototype.hasOwnProperty.call(state.weaver_streams, group)) {
    return result;
  }
  result.show = true;
  var raw = state.weaver_streams[group];
  var items = [];
  if (Array.isArray(raw)) items = raw.slice();
  else if (raw && Array.isArray(raw.items)) items = raw.items.slice();
  for (var i = 0; i < items.length; i++) {
    if (_weaverStreamIsOpen(items[i])) result.streams.push(items[i]);
  }
  return result;
}

function _weaverStreamIsOpen(stream) {
  if (!stream) return false;
  if (stream.is_open === false) return false;
  var mergeState = String(_weaverStreamMergeState(stream) || '').toLowerCase();
  var stateName = String(_weaverStreamStateName(stream) || '').toLowerCase();
  return mergeState !== 'merged' && stateName !== 'merged';
}

function _weaverRenderOpenStreamCard(stream, index) {
  var title = _weaverStreamTitle(stream);
  var branch = _weaverShortBranchLabel(_weaverStreamBranch(stream));
  var stateMeta = _weaverStreamStateMeta(stream);
  var mergeMeta = _weaverStreamMergeMeta(stream);
  var gateReason = _weaverStreamGateReason(stream);
  var nextAction = _weaverStreamActionLabel(stream);
  var latestCommit = _weaverStreamLatestReviewedCommit(stream);
  var productTasks = _weaverStreamTaskItems(stream, 'product');
  var workflowTasks = _weaverStreamTaskItems(stream, 'workflow');
  var visibilityItems = _weaverStreamVisibilityItems(stream);
  var key = _weaverStreamAnchorKey(stream, index, title, branch);

  var html = '<div class="weaver-stream-card" data-weaver-anchor="' + _esc(key) + '">';
  html += '<div class="weaver-stream-card-header">';
  html += '<div class="weaver-stream-heading">';
  html += '<div class="weaver-stream-title-row">';
  html += '<span class="weaver-stream-title">' + _esc(title) + '</span>';
  html += '<span class="weaver-stream-state weaver-stream-state-'
    + _esc(stateMeta.className) + '">' + _esc(stateMeta.label) + '</span>';
  html += '</div>';
  if (branch && branch !== title) {
    html += '<div class="weaver-stream-branch">' + _esc(branch) + '</div>';
  }
  html += '</div>';
  if (mergeMeta.label) {
    html += '<span class="weaver-stream-merge weaver-stream-merge-' + _esc(mergeMeta.className)
      + '">' + _esc(mergeMeta.label) + '</span>';
  }
  html += '</div>';

  var metaHtml = '';
  if (latestCommit) {
    metaHtml += _weaverRenderStreamMetaRow('Reviewed', latestCommit);
  }
  if (gateReason) {
    metaHtml += _weaverRenderStreamMetaRow('Gate', gateReason);
  }
  if (nextAction) {
    metaHtml += _weaverRenderStreamMetaRow('Next', nextAction);
  }
  if (metaHtml) {
    html += '<div class="weaver-stream-meta-list">';
    html += metaHtml;
    html += '</div>';
  }

  if (productTasks.length) {
    html += _weaverRenderStreamTaskGroup(
      'Product tasks',
      'product',
      productTasks,
      false
    );
  }
  if (workflowTasks.length) {
    html += _weaverRenderStreamTaskGroup(
      'Workflow',
      'workflow',
      workflowTasks,
      true
    );
  }
  if (visibilityItems.length) {
    html += _weaverRenderStreamVisibilityGroup(visibilityItems);
  }

  html += '</div>';
  return html;
}

function _weaverRenderStreamMetaRow(label, value) {
  return '<div class="weaver-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="weaver-stream-meta-value">' + _esc(value) + '</div>';
}

function _weaverRenderStreamTaskGroup(title, kind, tasks, summarizeOnly) {
  if (!tasks.length) return '';
  var summary = tasks.length + ' ' + kind + ' task' + (tasks.length === 1 ? '' : 's');
  var html = '<div class="weaver-stream-task-group">';
  html += '<div class="weaver-stream-task-group-header">';
  html += '<span class="weaver-stream-section-label weaver-stream-section-label-' + _esc(kind)
    + '">' + _esc(title) + '</span>';
  html += '<span class="weaver-stream-summary-count">' + _esc(summary) + '</span>';
  html += '</div>';
  html += '<div class="weaver-stream-task-list">';
  var limit = summarizeOnly ? 2 : 3;
  for (var i = 0; i < tasks.length && i < limit; i++) {
    var item = tasks[i];
    html += '<span class="weaver-stream-task-chip weaver-stream-task-chip-' + _esc(kind) + '">';
    html += _esc(item.title || item.id || '');
    html += '</span>';
  }
  if (tasks.length > limit) {
    html += '<span class="weaver-stream-task-chip weaver-stream-task-chip-more">+'
      + (tasks.length - limit) + ' more</span>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverRenderStreamVisibilityGroup(items) {
  if (!items.length) return '';
  var html = '<div class="weaver-stream-task-group">';
  html += '<div class="weaver-stream-task-group-header">';
  html += '<span class="weaver-stream-section-label weaver-stream-section-label-context">'
    + 'Recent context</span>';
  html += '<span class="weaver-stream-summary-count">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="weaver-stream-visibility-list">';
  for (var i = 0; i < items.length && i < 2; i++) {
    var item = items[i];
    var kind = _weaverVisibilityKindLabel(item);
    html += '<div class="weaver-stream-visibility-item">';
    if (kind) {
      html += '<span class="weaver-stream-visibility-kind">' + _esc(kind) + '</span>';
    }
    html += '<span class="weaver-stream-visibility-text">' + _esc(item.summary) + '</span>';
    html += '</div>';
  }
  if (items.length > 2) {
    html += '<div class="weaver-stream-visibility-more">+' + (items.length - 2)
      + ' more context item' + (items.length - 2 === 1 ? '' : 's') + '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverStreamAnchorKey(stream, index, title, branch) {
  var parts = [
    stream && (stream.stream_id || stream.id || ''),
    stream && (stream.agent_id || ''),
    branch,
    title,
    String(index || 0),
  ].filter(function(part) { return !!part; });
  return 'stream-' + parts.join('-');
}

function _weaverStreamStateMeta(stream) {
  var name = _weaverStreamStateName(stream);
  var labels = {
    'implementing': 'Implementing',
    'reviewing': 'In review',
    'fixing_blockers': 'Fixing blockers',
    'awaiting_human_validation': 'Awaiting validation',
    'ready_to_merge': 'Ready to merge',
    'merged': 'Merged',
  };
  return {
    raw: name,
    label: labels[name] || _weaverHumanizeToken(name || 'implementing'),
    className: _weaverClassSuffix(name || 'implementing'),
  };
}

function _weaverStreamStateName(stream) {
  var stateName = String((stream && stream.state) || '').toLowerCase();
  var validationState = String((stream && stream.validation_state) || '').toLowerCase();
  var mergeState = String(_weaverStreamMergeState(stream) || '').toLowerCase();
  if (validationState === 'pending_human_validation') {
    return 'awaiting_human_validation';
  }
  if (stateName) return stateName;
  if (mergeState === 'ready') return 'ready_to_merge';
  return 'implementing';
}

function _weaverStreamMergeState(stream) {
  return (stream && (stream.merge_state || stream.merge_readiness)) || '';
}

function _weaverStreamMergeMeta(stream) {
  var mergeState = String(_weaverStreamMergeState(stream) || '').toLowerCase();
  var labels = {
    'ready': 'Ready to merge',
    'not_ready': 'Not ready to merge',
    'merged': 'Merged',
  };
  if (!mergeState && _weaverStreamStateName(stream) === 'ready_to_merge') {
    mergeState = 'ready';
  }
  return {
    raw: mergeState,
    label: labels[mergeState] || '',
    className: _weaverClassSuffix(mergeState || 'unknown'),
  };
}

function _weaverStreamGateReason(stream) {
  return String(
    (stream && (
      stream.gate_reason
      || (stream.queue_gate && stream.queue_gate.reason)
      || stream.gate
    )) || ''
  );
}

function _weaverStreamActionLabel(stream) {
  var action = String((stream && stream.recommended_next_action) || '').toLowerCase();
  var labels = {
    'run_manual_validation': 'Run manual validation',
    'merge_after_validation': 'Merge after validation',
    'merge_stream': 'Merge stream',
    'resume_queued_work': 'Resume queued work',
    'resume_queued_task': 'Resume queued task',
    'review_latest_change': 'Review latest change',
    'wait_for_review': 'Wait for review',
    'fix_review_blocker': 'Fix review blocker',
    'resolve_merge_conflict': 'Resolve merge conflict',
  };
  return labels[action] || _weaverHumanizeToken(action);
}

function _weaverStreamLatestReviewedCommit(stream) {
  var value = '';
  if (stream) {
    if (stream.latest_reviewed_commit_sha) value = String(stream.latest_reviewed_commit_sha);
    else if (stream.latest_boundary_commit_sha) value = String(stream.latest_boundary_commit_sha);
    else if (stream.latest_reviewed_commit && stream.latest_reviewed_commit.sha) {
      value = String(stream.latest_reviewed_commit.sha);
    }
  }
  if (value.length > 10) return value.slice(0, 7);
  return value;
}

function _weaverStreamTaskItems(stream, kind) {
  var arrays = [];
  if (kind === 'product') {
    arrays = [
      stream && stream.product_tasks,
      stream && stream.related_product_tasks,
      stream && stream.product_task_ids,
    ];
  } else {
    arrays = [
      stream && stream.workflow_tasks,
      stream && stream.related_workflow_tasks,
      stream && stream.workflow_task_ids,
    ];
  }
  var raw = [];
  for (var i = 0; i < arrays.length; i++) {
    if (Array.isArray(arrays[i])) {
      raw = arrays[i];
      break;
    }
  }
  var items = [];
  var seen = {};
  for (var j = 0; j < raw.length; j++) {
    var item = _weaverNormalizeStreamTaskItem(raw[j]);
    if (!item.title && !item.id) continue;
    var key = item.id || item.title;
    if (seen[key]) continue;
    seen[key] = true;
    items.push(item);
  }
  return items;
}

function _weaverNormalizeStreamTaskItem(item) {
  if (typeof item === 'string' || typeof item === 'number') {
    var taskId = String(item);
    return _weaverStreamTaskFromId(taskId);
  }
  if (!item || typeof item !== 'object') return { id: '', title: '' };
  var id = item.id || item.task_id || '';
  var title = item.title || item.task || item.name || '';
  if (!title && id) {
    var resolved = _weaverStreamTaskFromId(id);
    title = resolved.title;
  }
  return {
    id: String(id || ''),
    title: String(title || ''),
  };
}

function _weaverStreamTaskFromId(taskId) {
  var task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  return {
    id: String(taskId || ''),
    title: String((task && (task.task || task.title)) || taskId || ''),
  };
}

function _weaverStreamVisibilityItems(stream) {
  var raw = [];
  if (stream) {
    if (Array.isArray(stream.visibility_items)) raw = stream.visibility_items;
    else if (Array.isArray(stream.recent_visibility_items)) raw = stream.recent_visibility_items;
  }
  var items = [];
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i];
    if (typeof item === 'string') {
      items.push({ kind: '', status: '', summary: item });
      continue;
    }
    if (!item || typeof item !== 'object') continue;
    var summary = item.summary || item.message || item.entry || item.title || '';
    if (!summary) continue;
    items.push({
      kind: item.kind || item.type || '',
      status: item.status || item.state || '',
      summary: String(summary),
    });
  }
  return items;
}

function _weaverVisibilityKindLabel(item) {
  if (!item) return '';
  var status = String(item.status || '').toLowerCase();
  var kind = String(item.kind || '').toLowerCase();
  if (status) return _weaverHumanizeToken(status);
  if (kind) return _weaverHumanizeToken(kind);
  return 'Note';
}

function _weaverStreamTitle(stream) {
  var title = '';
  if (stream) {
    title = stream.short_label
      || stream.friendly_title
      || stream.display_name
      || stream.label
      || stream.title
      || '';
  }
  if (title) return String(title);
  return _weaverShortBranchLabel(_weaverStreamBranch(stream)) || 'Untitled stream';
}

function _weaverStreamBranch(stream) {
  return String((stream && (stream.branch || stream.worktree_branch || stream.stream_branch)) || '');
}

function _weaverShortBranchLabel(branch) {
  return String(branch || '').replace(/^loom\//, '');
}

function _weaverHumanizeToken(value) {
  var text = String(value || '').trim();
  if (!text) return '';
  text = text.replace(/[_-]+/g, ' ');
  return text.replace(/\b([a-z])/g, function(match, chr) {
    return chr.toUpperCase();
  });
}

function _weaverClassSuffix(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
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

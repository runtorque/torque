/* Agent panel — focused-agent router with per-kind renderers */

var _agentPanelLastSelectedTabByKind = {};
var _agentPanelTabSpecByKind = {
  architect: [
    { key: 'decisions', label: 'Decisions' },
    { key: 'hired_engineers', label: 'Hired engineers' },
    { key: 'messages', label: 'Messages' },
    { key: 'events', label: 'Events' },
  ],
  engineer: [
    { key: 'journal', label: 'Journal' },
    { key: 'events', label: 'Events' },
    { key: 'worklog', label: 'Worklog' },
  ],
  worker: [
    { key: 'events', label: 'Events' },
    { key: 'worklog', label: 'Worklog' },
  ],
  terminal: [],
};

function _agentPanelEsc(value) {
  if (typeof _esc === 'function') return _esc(value);
  if (typeof esc === 'function') return esc(value);
  return String(value == null ? '' : value);
}

function _agentPanelKind(agent) {
  if (!agent) return '';
  if ((agent.cell_type || '') === 'terminal') return 'terminal';
  var kind = String(agent.kind || '').trim();
  if (kind === 'architect' || kind === 'engineer' || kind === 'worker') return kind;
  return 'worker';
}

function _resolveFocusedAgent() {
  if (typeof focusedItemId === 'undefined' || !focusedItemId) return null;
  if (!state || !state.agents) return null;
  return state.agents[focusedItemId] || null;
}

function _agentPanelSelectedTab(kind) {
  kind = String(kind || '').trim();
  if (!kind) return '';
  return _agentPanelLastSelectedTabByKind[kind] || '';
}

function _agentPanelTabSpec(kind) {
  return _agentPanelTabSpecByKind[String(kind || '').trim()] || [];
}

function _agentPanelDefaultTab(kind) {
  var tabs = _agentPanelTabSpec(kind);
  return tabs.length ? tabs[0].key : '';
}

function _agentPanelActiveTab(kind) {
  var selected = _agentPanelSelectedTab(kind);
  var tabs = _agentPanelTabSpec(kind);
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].key === selected) return selected;
  }
  return _agentPanelDefaultTab(kind);
}

function agentPanelSelectTab(tab) {
  var agent = _resolveFocusedAgent();
  if (!agent) return;
  var kind = _agentPanelKind(agent);
  if (!kind) return;
  _agentPanelLastSelectedTabByKind[kind] = String(tab || '');
  renderAgentPanel();
}

function _agentPanelTimeAgo(ts) {
  if (typeof _relativeTime === 'function') return _relativeTime(ts);
  if (typeof _weaverTimeAgo === 'function') return _weaverTimeAgo(ts);
  ts = Number(ts || 0);
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _agentPanelAnchorItems(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return [];
  var results = [];
  var seen = [];
  var selectors = ['[data-agent-panel-anchor]', '[data-weaver-anchor]'];
  for (var i = 0; i < selectors.length; i++) {
    var items = container.querySelectorAll(selectors[i]) || [];
    for (var j = 0; j < items.length; j++) {
      var item = items[j];
      if (!item) continue;
      if (seen.indexOf(item) >= 0) continue;
      seen.push(item);
      results.push(item);
    }
  }
  return results;
}

function _agentPanelAnchorKey(item) {
  if (!item) return '';
  if (item.dataset) {
    if (item.dataset.agentPanelAnchor) return String(item.dataset.agentPanelAnchor);
    if (item.dataset.weaverAnchor) return String(item.dataset.weaverAnchor);
  }
  if (typeof item.getAttribute === 'function') {
    var key = item.getAttribute('data-agent-panel-anchor');
    if (key) return String(key);
    key = item.getAttribute('data-weaver-anchor');
    if (key) return String(key);
  }
  return '';
}

function _agentPanelCaptureScrollAnchor(container) {
  if (!container || typeof container.getBoundingClientRect !== 'function') return null;
  var items = _agentPanelAnchorItems(container);
  if (!items.length) return null;
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
    key: _agentPanelAnchorKey(best),
    offset: anchorRect.top - containerRect.top,
  };
}

function _agentPanelRestoreScrollAnchor(container, snapshot) {
  if (!container || !snapshot || !snapshot.key
      || typeof container.getBoundingClientRect !== 'function'
      || typeof container.scrollTop !== 'number') {
    return;
  }
  var items = _agentPanelAnchorItems(container);
  var target = null;
  for (var i = 0; i < items.length; i++) {
    if (_agentPanelAnchorKey(items[i]) === snapshot.key) {
      target = items[i];
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
}

function _agentPanelRenderTabs(kind, activeTab) {
  var tabs = _agentPanelTabSpec(kind);
  if (!tabs.length) return '';
  var html = '<div class="agent-panel-tabs">';
  for (var i = 0; i < tabs.length; i++) {
    var tab = tabs[i];
    html += '<button type="button"'
      + ' id="agent-panel-tab-' + _agentPanelEsc(tab.key) + '"'
      + ' class="agent-panel-tab' + (activeTab === tab.key ? ' active' : '') + '"'
      + ' onclick="agentPanelSelectTab(\'' + _agentPanelEsc(tab.key) + '\')">'
      + _agentPanelEsc(tab.label)
      + '</button>';
  }
  html += '</div>';
  return html;
}

function _agentPanelShell(title, subtitle, kind, activeTab, bodyHtml, headerRightHtml) {
  var html = '<div class="agent-panel-panel"';
  if (kind) html += ' data-agent-panel-kind="' + _agentPanelEsc(kind) + '"';
  if (activeTab) html += ' data-agent-panel-tab="' + _agentPanelEsc(activeTab) + '"';
  html += '>';
  html += '<div class="agent-panel-header">';
  html += '<div class="agent-panel-header-copy">';
  html += '<span class="agent-panel-title">' + _agentPanelEsc(title || 'Agent') + '</span>';
  if (subtitle) {
    html += '<div class="agent-panel-subtitle">' + _agentPanelEsc(subtitle) + '</div>';
  }
  html += '</div>';
  if (headerRightHtml) {
    html += '<div class="agent-panel-header-right">' + headerRightHtml + '</div>';
  }
  html += '</div>';
  html += _agentPanelRenderTabs(kind, activeTab);
  html += '<div class="agent-panel-content">' + (bodyHtml || '') + '</div>';
  html += '</div>';
  return html;
}

function _agentPanelWeaverSettings(group) {
  if (typeof _weaverGetSettings === 'function') return _weaverGetSettings(group);
  return (state && state.weaver_settings && group) ? (state.weaver_settings[group] || null) : null;
}

function _agentPanelWeaverAgent(group) {
  if (typeof _weaverGetAgent === 'function') return _weaverGetAgent(group);
  if (!group || !state || !state.group_settings || !state.agents) return null;
  var settings = state.group_settings[group];
  return settings && settings.weaver_agent_id ? (state.agents[settings.weaver_agent_id] || null) : null;
}

function _agentPanelDigestSettings(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return null;
  if (state.agent_digest_settings && state.agent_digest_settings[agentId]) {
    return state.agent_digest_settings[agentId];
  }
  var group = String((agent && agent.group) || '');
  var legacyWeaver = group ? _agentPanelWeaverAgent(group) : null;
  if (legacyWeaver && String(legacyWeaver.id || '') === agentId) {
    return _agentPanelWeaverSettings(group);
  }
  return null;
}

function _agentPanelDigestBufferStats(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return null;
  if (state.digest_buffer_stats && state.digest_buffer_stats[agentId]) {
    return state.digest_buffer_stats[agentId];
  }
  var group = String((agent && agent.group) || '');
  var legacyWeaver = group ? _agentPanelWeaverAgent(group) : null;
  if (
    legacyWeaver
    && String(legacyWeaver.id || '') === agentId
    && state.weaver_buffer_stats
    && state.weaver_buffer_stats[group]
  ) {
    return state.weaver_buffer_stats[group];
  }
  return null;
}

function _agentPanelDigestSentEvents(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId || !state) return [];
  if (state.digest_sent_events && state.digest_sent_events[agentId]) {
    return state.digest_sent_events[agentId].slice();
  }
  var group = String((agent && agent.group) || '');
  var legacyWeaver = group ? _agentPanelWeaverAgent(group) : null;
  if (
    legacyWeaver
    && String(legacyWeaver.id || '') === agentId
    && state.weaver_sent_events
    && state.weaver_sent_events[group]
  ) {
    return state.weaver_sent_events[group].slice();
  }
  return [];
}

function _agentPanelDigestPauseButton(agent) {
  var agentId = String((agent && agent.id) || '');
  if (!agentId) return '';
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  return '<button id="agent-panel-pause-btn" class="agent-panel-pause-btn'
    + (paused ? ' paused' : '')
    + '" onclick="agentPanelTogglePauseForAgent(\'' + _agentPanelEsc(agentId) + '\')">'
    + (paused ? '&#x25B6;' : '&#x23F8;')
    + '</button>';
}

function _agentPanelDigestHeaderRight(agent) {
  if (!agent) return '';
  var bstats = _agentPanelDigestBufferStats(agent);
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  var html = '';
  if (bstats && bstats.buffered_events > 0) {
    html += '<span class="agent-panel-buffer-stats">'
      + _agentPanelEsc(_weaverHeaderBufferStats(bstats, paused, agent))
      + '</span>';
  }
  html += _agentPanelDigestPauseButton(agent);
  return html;
}

function _agentPanelRenderEventsTab(bstats, sentEvents, paused, recipient, sendNowExpr, sentTitle) {
  var queued = (bstats && bstats.queued_events) ? bstats.queued_events.slice() : [];
  var sent = Array.isArray(sentEvents) ? sentEvents.slice() : [];
  var sendDisabled = paused || !queued.length;
  var statusText = _weaverEventsStatusText(bstats, paused, recipient);

  sent.sort(function(a, b) {
    var deliveredDiff = (b.delivered_at || 0) - (a.delivered_at || 0);
    if (deliveredDiff) return deliveredDiff;
    return (b.id || 0) - (a.id || 0);
  });

  var html = '<div class="agent-panel-events-tab">';
  html += '<div class="agent-panel-events-toolbar">';
  html += '<div class="agent-panel-events-countdown">' + _esc(statusText) + '</div>';
  html += '<button id="weaver-send-now-btn" class="agent-panel-send-now-btn"'
    + (sendDisabled ? ' disabled' : '')
    + ' onclick="' + _agentPanelEsc(sendNowExpr || 'weaverSendNow()') + '">Send queued now</button>';
  html += '</div>';
  html += _agentPanelLegacyRenderEventSection(
    'Queued for next digest',
    queued,
    'queued',
    'No queued events.'
  );
  html += _agentPanelLegacyRenderEventSection(
    sentTitle || 'Already sent to Weaver',
    sent,
    'sent',
    'No digested events yet.'
  );
  html += '</div>';
  return html;
}

function _agentPanelArchitectDecisions(agentId) {
  if (typeof _architectDecisionsForAgent === 'function') return _architectDecisionsForAgent(agentId);
  var stores = [];
  if (state && state.decisions) stores.push(state.decisions);
  if (state && state.architect_decisions) stores.push(state.architect_decisions);
  var results = [];
  var architectId = String(agentId || '');
  for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
    var store = stores[storeIndex] || {};
    for (var key in store) {
      var decision = store[key];
      if (!decision) continue;
      if (String(decision.architect_id || '') !== architectId) continue;
      results.push(decision);
    }
  }
  results.sort(function(a, b) {
    var aTs = Number((a && (a.updated_at || a.created_at)) || 0);
    var bTs = Number((b && (b.updated_at || b.created_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return results;
}

function _agentPanelGroupWideNote(group) {
  var label = group ? ('Group: ' + group + ' (group-wide)') : 'Group-wide';
  return '<div class="agent-panel-worklog-note">' + _agentPanelEsc(label) + '</div>';
}

function _renderEngineerJournal(agent) {
  var group = String((agent && agent.group) || '');
  if (typeof _agentPanelLegacyRenderJournal === 'function') return _agentPanelLegacyRenderJournal(group);
  var entries = (state && state.weaver_journal && state.weaver_journal[group]) || [];
  if (typeof _agentPanelLegacyRenderJournalEntries === 'function') return _agentPanelLegacyRenderJournalEntries(entries, true);
  if (!entries.length) return '<div class="agent-panel-empty">No journal entries yet.</div>';
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i] || {};
    html += '<div class="agent-panel-entry">';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="agent-panel-badge">' + _agentPanelEsc(entry.type || 'note') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _agentPanelEsc(_agentPanelTimeAgo(entry.timestamp)) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _agentPanelEsc(entry.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _renderAgentDigestEvents(agent) {
  var group = String((agent && agent.group) || '');
  var bstats = _agentPanelDigestBufferStats(agent);
  var sentEvents = _agentPanelDigestSentEvents(agent);
  var settings = _agentPanelDigestSettings(agent);
  var paused = !!(settings && settings.paused);
  if (!group) return '<div class="agent-panel-empty">No group events yet.</div>';
  return _agentPanelRenderEventsTab(
    bstats,
    sentEvents,
    paused,
    agent,
    'agentPanelSendNow(\'' + _agentPanelEsc((agent && agent.id) || '') + '\')',
    'Already sent to ' + _agentPanelEsc((agent && (agent.name || agent.id)) || 'engineer')
  );
}

function _renderEngineerEvents(agent) {
  return _renderAgentDigestEvents(agent);
}

function _renderArchitectEvents(agent) {
  return _renderAgentDigestEvents(agent);
}

function _renderEngineerWorklog(agent) {
  var group = String((agent && agent.group) || '');
  var ws = _agentPanelWeaverSettings(group);
  if (typeof _agentPanelLegacyRenderWorklog === 'function') return _agentPanelLegacyRenderWorklog(group, ws);
  return '<div class="agent-panel-empty">No dispatched tasks yet.</div>';
}

function _renderEngineerPanel(agent) {
  var group = String((agent && agent.group) || '');
  var activeTab = _agentPanelActiveTab('engineer');
  var body = _agentPanelGroupWideNote(group);
  var headerRightHtml = '';
  if (activeTab === 'events') {
    headerRightHtml = _agentPanelDigestHeaderRight(agent);
    body += _renderEngineerEvents(agent);
  } else if (activeTab === 'worklog') {
    body += _renderEngineerWorklog(agent);
  } else {
    body += _renderEngineerJournal(agent);
  }
  return _agentPanelShell(
    'Engineer: ' + ((agent && (agent.name || agent.id)) || 'Unknown') + ' · Group: ' + (group || '—'),
    'Journal, digest queue, and worklog for this engineer\'s group.',
    'engineer',
    activeTab,
    body,
    headerRightHtml
  );
}

function _agentPanelWorkerEvents(agent) {
  var agentId = String((agent && agent.id) || '');
  var events = (state && state.panel_events ? state.panel_events.slice() : []).filter(function(evt) {
    return String((evt && evt.cell_id) || '') === agentId;
  });
  events.sort(function(a, b) {
    var tsDiff = Number((b && b.timestamp) || 0) - Number((a && a.timestamp) || 0);
    if (tsDiff) return tsDiff;
    return Number((b && b.id) || 0) - Number((a && a.id) || 0);
  });

  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">Worker events</span>';
  html += '<span class="agent-panel-event-section-count">' + events.length + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">No worker events yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < events.length; i++) {
    var evt = events[i] || {};
    var anchorKey = 'worker-event-' + String(evt.id || i);
    var kind = typeof _weaverEventKindLabel === 'function'
      ? _weaverEventKindLabel(evt.kind)
      : String(evt.kind || 'event').replace(/_/g, ' ');
    var summary = evt.message || kind;
    html += '<div class="agent-panel-event-item agent-panel-event-item-sent" data-agent-panel-anchor="'
      + _agentPanelEsc(anchorKey) + '">';
    html += '<div class="agent-panel-event-item-header">';
    html += '<span class="agent-panel-event-kind">' + _agentPanelEsc(kind) + '</span>';
    html += '<span class="agent-panel-event-meta">' + _agentPanelEsc(_agentPanelTimeAgo(evt.timestamp)) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-event-message">' + _agentPanelEsc(summary) + '</div>';
    if (evt.task_id) {
      html += '<div class="agent-panel-event-task">' + _agentPanelEsc(evt.task_id) + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelWorkerTaskEntries(agent) {
  var agentId = String((agent && agent.id) || '');
  var tasks = [];
  if (!state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (String(task.agent_id || '') !== agentId) continue;
    tasks.push(task);
  }
  tasks.sort(function(a, b) {
    var aTs = Number((a && (a.started_at || a.created_at || a.updated_at)) || 0);
    var bTs = Number((b && (b.started_at || b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((b && b.id) || '').localeCompare(String((a && a.id) || ''));
  });
  return tasks;
}

function _agentPanelRenderWorkerWorklogItem(agent, task) {
  var taskId = (task && task.id) || '';
  var title = (task && (task.task || task.title)) || taskId || 'Task';
  var lane = (task && task.lane) || 'Not on board';
  var status = task ? String(task.status || '').trim() : '';
  var startedAt = Number((task && (task.started_at || task.created_at || task.updated_at)) || 0);
  var agentName = (agent && (agent.name || agent.slug || agent.id)) || 'Worker';
  var meta = startedAt ? ('started ' + _agentPanelTimeAgo(startedAt)) : 'recent task';
  var anchorKey = 'worker-task-' + String(taskId || title);

  var html = '<div class="agent-panel-worklog-item" data-agent-panel-anchor="' + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _agentPanelEsc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _agentPanelEsc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _agentPanelEsc(lane) + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _agentPanelEsc(agentName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _agentPanelEsc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _agentPanelEsc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelWorkerWorklog(agent) {
  var tasks = _agentPanelWorkerTaskEntries(agent);
  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Task history</span>';
  html += '<span class="agent-panel-worklog-count">' + tasks.length + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-note">Tasks assigned to this worker.</div>';
  if (!tasks.length) {
    html += '<div class="agent-panel-event-empty">No tasks yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < tasks.length; i++) {
    html += _agentPanelRenderWorkerWorklogItem(agent, tasks[i]);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _renderWorkerPanel(agent) {
  var activeTab = _agentPanelActiveTab('worker');
  var body = (activeTab === 'worklog')
    ? _agentPanelWorkerWorklog(agent)
    : _agentPanelWorkerEvents(agent);
  return _agentPanelShell(
    'Worker: ' + ((agent && (agent.name || agent.id)) || 'Unknown')
      + ' · Group: ' + (((agent && agent.group) || '') || '—'),
    'Per-worker event stream and task history.',
    'worker',
    activeTab,
    body
  );
}

function _agentPanelArchitectHiredEngineers(agent) {
  var engineers = [];
  var architectId = String((agent && agent.id) || '');
  var group = String((agent && agent.group) || '');
  var allAgents = (state && state.agents) || {};
  for (var key in allAgents) {
    var candidate = allAgents[key];
    if (!candidate) continue;
    if (String(candidate.kind || '') !== 'engineer') continue;
    if (String(candidate.hired_by_architect_id || '') !== architectId) continue;
    engineers.push(candidate);
  }
  engineers.sort(function(a, b) {
    var aName = String((a && (a.name || a.slug || a.id)) || '');
    var bName = String((b && (b.name || b.slug || b.id)) || '');
    return aName.localeCompare(bName);
  });

  var html = '<div class="engineers-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Hired engineers</span>';
  html += '<span class="engineers-roster-count">' + engineers.length + '</span>';
  html += '</div>';
  if (!engineers.length) {
    html += '<div class="engineers-roster-empty">No hired engineers yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="engineers-roster-list">';
  if (typeof _agentPanelLegacyRenderEngineerTreeRows === 'function') {
    html += _agentPanelLegacyRenderEngineerTreeRows(group, engineers, 'architect-roster-level-1', 'architect-roster-level-2');
  } else {
    for (var i = 0; i < engineers.length; i++) {
      var engineer = engineers[i];
      html += '<div class="engineer-row architect-roster-level-1">';
      html += '<div class="engineer-row-main">';
      html += '<span class="engineer-row-name">' + _agentPanelEsc(engineer.name || engineer.id || '') + '</span>';
      html += '<span class="engineer-row-kind">engineer</span>';
      html += '</div></div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelArchitectDecisionsHtml(agent) {
  var decisions = _agentPanelArchitectDecisions(agent && agent.id);
  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Decisions</span>';
  html += '<span class="agent-panel-worklog-count">' + decisions.length + '</span>';
  html += '</div>';
  if (!decisions.length) {
    html += '<div class="agent-panel-event-empty">No decisions yet.</div>';
    html += '</div>';
    return html;
  }

  if (typeof _weaverDecisionGroups === 'function' && typeof _agentPanelLegacyRenderDecisionRow === 'function'
      && typeof _WEAVER_DECISION_STATUSES !== 'undefined') {
    var grouped = _weaverDecisionGroups(decisions);
    var hasRows = false;
    for (var statusIndex = 0; statusIndex < _WEAVER_DECISION_STATUSES.length; statusIndex++) {
      var statusName = _WEAVER_DECISION_STATUSES[statusIndex];
      var rows = grouped[statusName] || [];
      if (!rows.length) continue;
      hasRows = true;
      html += '<div class="architect-decision-group">';
      html += '<div class="architect-decision-group-title">' + _agentPanelEsc(statusName)
        + ' <span class="architect-decision-group-count">' + rows.length + '</span></div>';
      for (var rowIndex = 0; rowIndex < rows.length; rowIndex++) {
        html += _agentPanelLegacyRenderDecisionRow(agent.id, rows[rowIndex]);
      }
      html += '</div>';
    }
    if (!hasRows) {
      html += '<div class="agent-panel-event-empty">No decisions yet.</div>';
    }
    html += '</div>';
    return html;
  }

  for (var i = 0; i < decisions.length; i++) {
    var decision = decisions[i] || {};
    html += '<div class="detail-section-card architect-decision-card">';
    html += '<div class="detail-section-card-head">';
    html += '<span class="detail-section-primary">' + _agentPanelEsc(decision.title || 'Decision') + '</span>';
    html += '<span class="detail-task-status">' + _agentPanelEsc(decision.status || 'proposed') + '</span>';
    html += '</div>';
    if (decision.rationale) {
      html += '<div class="detail-section-card-body">' + _agentPanelEsc(decision.rationale) + '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelArchitectMessages(agent) {
  var messages = Array.isArray(agent && agent.mcp_messages) ? agent.mcp_messages.slice(0, 20) : [];
  var icons = {
    progress: '\u25CF',
    done: '\u2714',
    ready: '\u2714',
    blocked: '\u26D4',
    error: '\u2716',
    derive: '\u2934',
    ask: '\u2753',
    name: '\u270E',
    architect_reply: '\u21A9',
  };
  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Messages</span>';
  html += '<span class="agent-panel-worklog-count">' + messages.length + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-note">Reply composer lands in a later task.</div>';
  if (!messages.length) {
    html += '<div class="agent-panel-event-empty">No messages yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="mcp-log">';
  for (var i = 0; i < messages.length; i++) {
    var message = messages[i] || {};
    var action = String(message.action || 'progress');
    html += '<div class="mcp-entry-wrap">';
    html += '<div class="mcp-entry mcp-' + _agentPanelEsc(action) + '">';
    html += '<span class="mcp-icon">' + (icons[action] || '\u25CF') + '</span>';
    html += '<span class="mcp-text">' + _agentPanelEsc(message.message || action) + '</span>';
    html += '<span class="mcp-time">' + _agentPanelEsc(_agentPanelTimeAgo(message.timestamp)) + '</span>';
    html += '</div>';
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _renderArchitectPanel(agent) {
  var activeTab = _agentPanelActiveTab('architect');
  var body = '';
  var headerRightHtml = '';
  if (activeTab === 'hired_engineers') {
    body = _agentPanelArchitectHiredEngineers(agent);
  } else if (activeTab === 'messages') {
    body = _agentPanelArchitectMessages(agent);
  } else if (activeTab === 'events') {
    headerRightHtml = _agentPanelDigestHeaderRight(agent);
    body = _renderArchitectEvents(agent);
  } else {
    body = _agentPanelArchitectDecisionsHtml(agent);
  }
  return _agentPanelShell(
    'Architect: ' + ((agent && (agent.name || agent.id)) || 'Unknown')
      + ' · Group: ' + (((agent && agent.group) || '') || '—'),
    'Decisions, hired engineers, architect messages, and digest queue.',
    'architect',
    activeTab,
    body,
    headerRightHtml
  );
}

function _agentPanelTerminalValue(value, fallback) {
  var text = String(value || '').trim();
  return text || String(fallback || '—');
}

function _renderTerminalPanel(agent) {
  var branch = String((agent && (agent.worktree_branch || agent.current_branch)) || '').replace(/^loom\//, '');
  var processInfo = (typeof _terminalStatusLabel === 'function')
    ? _terminalStatusLabel(agent)
    : ((agent && (agent.current_process || agent.activity_detail || agent.activity || agent.status)) || 'idle');
  var displayPath = (typeof _terminalDisplayPath === 'function')
    ? _terminalDisplayPath(agent)
    : ((agent && (agent.current_path || agent.directory)) || '');
  var body = '<div class="detail-section-card">';
  body += '<div class="detail-row"><span class="detail-label">Agent</span><span class="detail-val">'
    + _agentPanelEsc(_agentPanelTerminalValue(agent && (agent.name || agent.id), 'Terminal')) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 '
    + _agentPanelEsc(_agentPanelTerminalValue(branch)) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Process</span><span class="detail-val">'
    + _agentPanelEsc(_agentPanelTerminalValue(processInfo, 'idle')) + '</span></div>';
  body += '<div class="detail-row"><span class="detail-label">Path</span><span class="detail-val" title="'
    + _agentPanelEsc(displayPath || '') + '">' + _agentPanelEsc(_agentPanelTerminalValue(displayPath)) + '</span></div>';
  body += '<div class="agent-panel-worklog-note">Open the terminal drawer to interact with this session.</div>';
  body += '</div>';
  return _agentPanelShell(
    'Terminal: ' + ((agent && (agent.name || agent.id)) || 'Terminal'),
    'Terminal session status.',
    'terminal',
    '',
    body
  );
}

function renderAgentPanel() {
  if (typeof _weaverStopEventsCountdownTimer === 'function') {
    _weaverStopEventsCountdownTimer();
  }
  var el = document.getElementById('panel-agent');
  if (!el) return;

  var panelStateOptions = {
    scrollSelectors: ['.agent-panel-content'],
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      snapshot.anchor = _agentPanelCaptureScrollAnchor(
        root.querySelector('.agent-panel-content')
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      _agentPanelRestoreScrollAnchor(
        root.querySelector('.agent-panel-content'),
        snapshot.anchor
      );
    },
  };
  if (typeof _captureMainFocusKey === 'function') {
    panelStateOptions.captureFocusKey = _captureMainFocusKey;
  }

  var panelState = typeof _captureSurfaceState === 'function'
    ? _captureSurfaceState(el, panelStateOptions)
    : null;
  var agent = _resolveFocusedAgent();
  var html = '';

  if (!agent) {
    html = '<div class="agent-panel">'
      + '<div class="agent-panel-empty">Select an agent from the grid to see its context.</div>'
      + '</div>';
  } else {
    switch (_agentPanelKind(agent)) {
      case 'architect':
        html = _renderArchitectPanel(agent);
        break;
      case 'engineer':
        html = _renderEngineerPanel(agent);
        break;
      case 'terminal':
        html = _renderTerminalPanel(agent);
        break;
      case 'worker':
      default:
        html = _renderWorkerPanel(agent);
        break;
    }
  }

  el.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(el, panelState, panelStateOptions);
  }
  var agentKind = agent ? _agentPanelKind(agent) : '';
  if (agent
      && (agentKind === 'engineer' || agentKind === 'architect')
      && typeof _weaverSyncEventsCountdown === 'function') {
    _weaverSyncEventsCountdown(el, agent.group || '', _agentPanelActiveTab(agentKind));
  }
}


/* ---- Legacy weaver support moved into agent_panel.js (stage 5) ---- */
/* Weaver panel — journal + events + worklog */

var _weaverReplyDraft = '';
var _weaverActiveTabByGroup = {};
var _weaverJournalSubviewByGroup = {};
var _weaverSessionMapMetaByGroup = {};
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

function _weaverCreatedSortValue(cell) {
  if (!cell) return '';
  return String(cell.created_at || cell.updated_at || cell.id || '');
}

function _weaverSortByCreatedAt(a, b) {
  return _weaverCreatedSortValue(a).localeCompare(_weaverCreatedSortValue(b));
}

var _weaverArchitectExpanded = {};
var _weaverArchitectDecisionUi = {};
var _WEAVER_DECISION_STATUSES = ['proposed', 'accepted', 'revised', 'rejected'];

function _weaverDecisionFocusKey(decisionId, field) {
  return 'weaver-decision-' + String(field || '') + ':' + String(decisionId || '');
}

function _weaverGroupAgents(group) {
  var agents = [];
  if (!state || !state.agents) return agents;
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent') continue;
    if (group && agent.group !== group) continue;
    agents.push(agent);
  }
  agents.sort(_weaverSortByCreatedAt);
  return agents;
}

function _weaverArchitectAgents(group) {
  return _weaverGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') === 'architect';
  });
}

function _weaverEngineerAgents(group, architectId) {
  return _weaverGroupAgents(group).filter(function(agent) {
    if ((agent.kind || '') !== 'engineer') return false;
    var hiredByArchitectId = String(agent.hired_by_architect_id || '').trim();
    if (typeof architectId === 'undefined') return true;
    if (architectId === null) return !hiredByArchitectId;
    return hiredByArchitectId === String(architectId || '').trim();
  });
}

function _weaverWorkerOwnerId(agent) {
  return String(
    (agent && (agent.owner_engineer_id || agent.created_by_weaver_id)) || ''
  ).trim();
}

function _weaverWorkerAgents(group, engineerId) {
  return _weaverGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') !== 'architect'
      && (agent.kind || '') !== 'engineer'
      && _weaverWorkerOwnerId(agent) === String(engineerId || '').trim();
  });
}

function _weaverAgentStatusLabel(agent) {
  if (!agent || agent.status === 'stopped') return 'stopped';
  if (agent.activity || agent.activity_detail) return 'running';
  return 'idle';
}

function _weaverEngineerStatusLabel(agent) {
  return _weaverAgentStatusLabel(agent);
}

function _weaverEngineerTransferCounts(engineerId) {
  var counts = { workers: 0, tasks: 0 };
  if (!engineerId) return counts;
  if (state && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (!agent || agent.id === engineerId || agent.cell_type !== 'agent') continue;
      if ((agent.owner_engineer_id || '') === engineerId
          || (agent.created_by_weaver_id || '') === engineerId) {
        counts.workers += 1;
      }
    }
  }
  if (state && state.board_tasks) {
    for (var taskId in state.board_tasks) {
      var task = state.board_tasks[taskId];
      if (task && (task.assigned_engineer_id || '') === engineerId) counts.tasks += 1;
    }
  }
  return counts;
}

function _weaverArchitectTransferCounts(architectId) {
  var counts = { engineers: 0, decisions: 0 };
  var architectKey = String(architectId || '').trim();
  if (!architectKey) return counts;
  counts.engineers = _weaverEngineerAgents(_agentPanelCurrentGroup(), architectKey).length;
  counts.decisions = _architectDecisionsForAgent(architectKey).length;
  return counts;
}

function _weaverArchitectExpandedState(architectId) {
  var key = String(architectId || '').trim();
  return !!_weaverArchitectExpanded[key];
}

function _weaverDecisionUiState(decisionId, decision) {
  var key = String(decisionId || '').trim();
  if (!_weaverArchitectDecisionUi[key]) {
    _weaverArchitectDecisionUi[key] = {
      expanded: false,
      editing: false,
      draft: { title: '', rationale: '', status: 'proposed' },
      link_task_id: '',
      link_engineer_id: '',
    };
  }
  var entry = _weaverArchitectDecisionUi[key];
  var current = decision || (state && state.decisions ? state.decisions[key] : null) || {};
  if (!entry.editing) {
    entry.draft.title = String(current.title || '');
    entry.draft.rationale = String(current.rationale || '');
    entry.draft.status = String(current.status || 'proposed');
  }
  return entry;
}

function _weaverMultilineHtml(text) {
  return formatCode(text || '').replace(/\n/g, '<br>');
}

function _weaverDecisionGroups(decisions) {
  var grouped = {};
  for (var i = 0; i < _WEAVER_DECISION_STATUSES.length; i++) {
    grouped[_WEAVER_DECISION_STATUSES[i]] = [];
  }
  for (var j = 0; j < decisions.length; j++) {
    var decision = decisions[j] || {};
    var status = String(decision.status || 'proposed');
    if (!grouped[status]) grouped[status] = [];
    grouped[status].push(decision);
  }
  return grouped;
}

function getArchitectDecisionTaskOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var tasks = [];
  if (!state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (group && task.group !== group) continue;
    tasks.push({
      value: task.id,
      label: (task.task || task.id || '') + ' (' + (task.lane || 'task') + ')',
    });
  }
  tasks.sort(function(a, b) {
    return String(a.label || '').localeCompare(String(b.label || ''));
  });
  return tasks;
}

function getArchitectDecisionEngineerOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var engineers = _weaverEngineerAgents(group);
  return engineers.map(function(engineer) {
    var label = engineer.name || engineer.id || '';
    if (engineer.slug) label += ' (' + engineer.slug + ')';
    return { value: engineer.id, label: label };
  });
}

function _agentPanelLegacyRenderWorkerRows(workers, levelClass) {
  if (!workers.length) return '';
  var html = '';
  for (var i = 0; i < workers.length; i++) {
    var worker = workers[i];
    html += '<div class="engineer-row ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(worker.name || worker.id || '') + '</span>';
    html += '<span class="engineer-row-kind">worker</span>';
    if (worker.slug) html += '<span class="engineer-row-slug">' + _esc(worker.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(_weaverAgentStatusLabel(worker)) + '">' + _esc(_weaverAgentStatusLabel(worker)) + '</span>';
    html += '</div></div>';
  }
  return html;
}

function _agentPanelLegacyRenderEngineerTreeRows(group, engineers, levelClass, workerLevelClass) {
  var html = '';
  for (var i = 0; i < engineers.length; i++) {
    var engineer = engineers[i];
    var status = _weaverEngineerStatusLabel(engineer);
    var workers = _weaverWorkerAgents(group, engineer.id);
    html += '<div class="engineer-row ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(engineer.name || engineer.id || '') + '</span>';
    html += '<span class="engineer-row-kind">engineer</span>';
    if (engineer.slug) html += '<span class="engineer-row-slug">' + _esc(engineer.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    html += '</div></div>';
    html += _agentPanelLegacyRenderWorkerRows(workers, workerLevelClass);
  }
  return html;
}

function _agentPanelLegacyRenderDecisionRow(architectId, decision) {
  var ui = _weaverDecisionUiState(decision.id, decision);
  var decisionIdJs = JSON.stringify(String(decision.id || ''));
  var architectIdJs = JSON.stringify(String(architectId || ''));
  var titleFocusKey = _weaverDecisionFocusKey(decision.id, 'title');
  var rationaleFocusKey = _weaverDecisionFocusKey(decision.id, 'rationale');
  var statusFocusKey = _weaverDecisionFocusKey(decision.id, 'status');
  var linkedTaskIds = Array.isArray(decision.linked_task_ids) ? decision.linked_task_ids : [];
  var linkedEngineerIds = Array.isArray(decision.linked_engineer_ids) ? decision.linked_engineer_ids : [];
  var taskOptions = getArchitectDecisionTaskOptions(architectId);
  var engineerOptions = getArchitectDecisionEngineerOptions(architectId);
  var html = '<div class="detail-section-card architect-decision-card">';
  html += '<div class="detail-section-card-head">';
  html += '<button type="button" class="architect-decision-toggle" onclick="weaverToggleDecision(' + decisionIdJs + ')">';
  html += '<span class="detail-section-primary" title="' + _esc(decision.title || '') + '">' + _esc(decision.title || 'Decision') + '</span>';
  html += '<span class="detail-task-status">' + _esc(decision.status || 'proposed') + '</span>';
  html += '<span class="detail-expand-caret">' + (ui.expanded ? '\u25BE' : '\u25B8') + '</span>';
  html += '</button>';
  html += '<div class="detail-section-card-actions">';
  if (!ui.editing) {
    html += '<button type="button" class="detail-inline-editor-btn" onclick="event.stopPropagation();weaverStartDecisionEdit(' + decisionIdJs + ')">Edit</button>';
  }
  html += '<button type="button" class="detail-inline-editor-btn" onclick="event.stopPropagation();weaverArchiveDecision(' + architectIdJs + ',' + decisionIdJs + ')">Archive</button>';
  html += '</div></div>';
  if (ui.expanded || ui.editing) {
    html += '<div class="architect-decision-body">';
    if (ui.editing) {
      html += '<input class="detail-inline-description-input architect-decision-input"'
        + ' data-focus-key="' + _esc(titleFocusKey) + '"'
        + ' value="' + _esc(ui.draft.title || '') + '"'
        + ' oninput="weaverDecisionDraftInput(' + decisionIdJs + ',\'title\',this.value)"'
        + ' placeholder="Decision title">';
      html += '<textarea class="detail-inline-description-input architect-decision-textarea" rows="4"'
        + ' data-focus-key="' + _esc(rationaleFocusKey) + '"'
        + ' oninput="weaverDecisionDraftInput(' + decisionIdJs + ',\'rationale\',this.value)"'
        + ' placeholder="Decision rationale...">' + _esc(ui.draft.rationale || '') + '</textarea>';
      html += '<select class="architect-decision-status-select"'
        + ' data-focus-key="' + _esc(statusFocusKey) + '"'
        + ' onchange="weaverDecisionDraftInput(' + decisionIdJs + ',\'status\',this.value)">';
      for (var statusIndex = 0; statusIndex < _WEAVER_DECISION_STATUSES.length; statusIndex++) {
        var statusOption = _WEAVER_DECISION_STATUSES[statusIndex];
        var selected = ui.draft.status === statusOption ? ' selected' : '';
        html += '<option value="' + _esc(statusOption) + '"' + selected + '>' + _esc(statusOption) + '</option>';
      }
      html += '</select>';
      html += '<div class="detail-inline-editor-actions">';
      html += '<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary" onclick="weaverSaveDecisionEdit(' + architectIdJs + ',' + decisionIdJs + ')">Save</button>';
      html += '<button type="button" class="detail-inline-editor-btn" onclick="weaverCancelDecisionEdit(' + decisionIdJs + ')">Cancel</button>';
      html += '</div>';
    } else {
      if (decision.rationale) {
        html += '<div class="detail-section-card-body">' + _weaverMultilineHtml(decision.rationale) + '</div>';
      }
      html += '<div class="detail-section-card-meta">';
      html += 'Linked ' + linkedTaskIds.length + ' task' + (linkedTaskIds.length === 1 ? '' : 's')
        + ' • ' + linkedEngineerIds.length + ' engineer' + (linkedEngineerIds.length === 1 ? '' : 's');
      if (decision.archived) html += ' • archived';
      html += '</div>';
      html += '<div class="architect-decision-link-row">';
      html += '<select class="architect-decision-link-select" onchange="weaverDecisionLinkSelect(' + decisionIdJs + ',\'task\',this.value)">';
      html += '<option value="">Link task…</option>';
      for (var taskIndex = 0; taskIndex < taskOptions.length; taskIndex++) {
        var taskOption = taskOptions[taskIndex];
        var taskSelected = ui.link_task_id === taskOption.value ? ' selected' : '';
        html += '<option value="' + _esc(taskOption.value) + '"' + taskSelected + '>' + _esc(taskOption.label) + '</option>';
      }
      html += '</select>';
      html += '<button type="button" class="detail-inline-editor-btn" onclick="weaverLinkDecisionTask(' + architectIdJs + ',' + decisionIdJs + ')">Link task</button>';
      html += '<select class="architect-decision-link-select" onchange="weaverDecisionLinkSelect(' + decisionIdJs + ',\'engineer\',this.value)">';
      html += '<option value="">Link engineer…</option>';
      for (var engineerIndex = 0; engineerIndex < engineerOptions.length; engineerIndex++) {
        var engineerOption = engineerOptions[engineerIndex];
        var engineerSelected = ui.link_engineer_id === engineerOption.value ? ' selected' : '';
        html += '<option value="' + _esc(engineerOption.value) + '"' + engineerSelected + '>' + _esc(engineerOption.label) + '</option>';
      }
      html += '</select>';
      html += '<button type="button" class="detail-inline-editor-btn" onclick="weaverLinkDecisionEngineer(' + architectIdJs + ',' + decisionIdJs + ')">Link engineer</button>';
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderArchitectRoster(group) {
  var architects = _weaverArchitectAgents(group);
  var html = '<section class="engineers-roster architects-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Architects</span>';
  html += '<span class="engineers-roster-count">' + architects.length + ' total</span>';
  html += '</div>';
  if (!architects.length) {
    html += '<div class="engineers-roster-empty">No architects yet. Add one to launch a dedicated architect session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list">';
  for (var i = 0; i < architects.length; i++) {
    var architect = architects[i];
    var status = _weaverAgentStatusLabel(architect);
    var expanded = _weaverArchitectExpandedState(architect.id);
    var hireCounts = _weaverArchitectTransferCounts(architect.id);
    var architectIdJs = JSON.stringify(String(architect.id || ''));
    html += '<div class="architect-row' + (expanded ? ' expanded' : '') + '">';
    html += '<div class="engineer-row architect-parent-row">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(architect.name || architect.id || '') + '</span>';
    html += '<span class="engineer-row-kind">architect</span>';
    if (architect.slug) {
      html += '<span class="engineer-row-slug">' + _esc(architect.slug) + '</span>';
    }
    html += '<span class="engineer-row-slug">decisions ' + hireCounts.decisions + ' • hired ' + hireCounts.engineers + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();relaunchAgent(\'' + _esc(architect.id) + '\')">Relaunch</button>';
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();weaverRenameArchitect(\'' + _esc(architect.id) + '\')">Rename</button>';
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();weaverToggleArchitect(\'' + _esc(architect.id) + '\')">' + (expanded ? 'Hide decision log' : 'Open decision log') + '</button>';
    html += '<button type="button" class="engineer-row-btn engineer-row-btn-danger" onclick="event.stopPropagation();weaverDeleteArchitect(\'' + _esc(architect.id) + '\')">Delete</button>';
    html += '</div></div>';
    if (expanded) {
      var hiredEngineers = _weaverEngineerAgents(group, architect.id);
      var decisions = _architectDecisionsForAgent(architect.id);
      var grouped = _weaverDecisionGroups(decisions);
      html += '<div class="architect-row-body">';
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Hired engineers</span><span class="architect-roster-section-count">' + hiredEngineers.length + '</span></div>';
      if (hiredEngineers.length) {
        html += _agentPanelLegacyRenderEngineerTreeRows(group, hiredEngineers, 'architect-roster-level-1', 'architect-roster-level-2');
      } else {
        html += '<div class="engineers-roster-empty architect-roster-empty">No hired engineers yet.</div>';
      }
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Decision log</span><span class="architect-roster-section-actions"><span class="architect-roster-section-count">' + decisions.length + '</span><button type="button" class="engineer-row-btn" onclick="event.stopPropagation();openArchitectDecisionModal(' + architectIdJs + ')">+ New decision</button></span></div>';
      var hasDecisionRows = false;
      for (var statusIndex = 0; statusIndex < _WEAVER_DECISION_STATUSES.length; statusIndex++) {
        var statusName = _WEAVER_DECISION_STATUSES[statusIndex];
        var rows = grouped[statusName] || [];
        if (!rows.length) continue;
        hasDecisionRows = true;
        html += '<div class="architect-decision-group">';
        html += '<div class="architect-decision-group-title">' + _esc(statusName) + ' <span class="architect-decision-group-count">' + rows.length + '</span></div>';
        for (var decisionIndex = 0; decisionIndex < rows.length; decisionIndex++) {
          html += _agentPanelLegacyRenderDecisionRow(architect.id, rows[decisionIndex]);
        }
        html += '</div>';
      }
      if (!hasDecisionRows) {
        html += '<div class="engineers-roster-empty architect-roster-empty">No decisions yet.</div>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</section>';
  return html;
}

function _agentPanelLegacyRenderEngineerRoster(group) {
  var engineers = _weaverEngineerAgents(group, null);
  var html = '<section class="engineers-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Engineers</span>';
  html += '<span class="engineers-roster-count">' + engineers.length + ' user-owned</span>';
  html += '</div>';
  if (!engineers.length) {
    html += '<div class="engineers-roster-empty">No user-owned engineers yet. Add one to launch a dedicated engineer session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list">';
  html += '<div class="engineer-row engineer-row-virtual-parent"><div class="engineer-row-main"><span class="engineer-row-name">User</span><span class="engineer-row-kind">owner</span></div></div>';
  html += _agentPanelLegacyRenderEngineerTreeRows(group, engineers, 'architect-roster-level-1', 'architect-roster-level-2');
  html += '</div>';
  html += '</section>';
  return html;
}

function weaverOpenAddEngineer() {
  if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
}

function weaverOpenAddArchitect() {
  if (typeof openAddArchitectModal === 'function') {
    openAddArchitectModal(_agentPanelCurrentGroup());
  }
}

function weaverRenameEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var currentName = String(engineer.name || '').trim();
  var nextName = window.prompt('Rename engineer', currentName);
  if (typeof nextName !== 'string') return;
  nextName = nextName.trim();
  if (!nextName || nextName === currentName) return;
  send({ cmd: 'rename_engineer', id: engineerId, new_name: nextName });
}

async function weaverDeleteEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var counts = _weaverEngineerTransferCounts(engineerId);
  var message = 'Deleting ' + (engineer.name || engineerId)
    + ' will transfer its ' + counts.workers + ' workers and ' + counts.tasks
    + ' tasks to the user. Continue?';
  if (await showConfirm(message, {
    label: 'Delete engineer',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_engineer', id: engineerId });
  }
}

function weaverRenameArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var currentName = String(architect.name || '').trim();
  var nextName = window.prompt('Rename architect', currentName);
  if (typeof nextName !== 'string') return;
  nextName = nextName.trim();
  if (!nextName || nextName === currentName) return;
  send({ cmd: 'update_agent', id: architectId, name: nextName });
}

async function weaverDeleteArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var counts = _weaverArchitectTransferCounts(architectId);
  var message = 'Deleting ' + (architect.name || architectId)
    + ' will transfer ' + counts.engineers + ' hired engineer'
    + (counts.engineers === 1 ? '' : 's')
    + ' to the user and archive ' + counts.decisions + ' decision'
    + (counts.decisions === 1 ? '' : 's') + '. Continue?';
  if (await showConfirm(message, {
    label: 'Delete architect',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_architect', id: architectId });
  }
}

function weaverToggleArchitect(architectId) {
  var key = String(architectId || '').trim();
  if (!key) return;
  _weaverArchitectExpanded[key] = !_weaverArchitectExpanded[key];
  _agentPanelRefreshVisibleSurface();
}

function weaverToggleDecision(decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  ui.expanded = !ui.expanded;
  _agentPanelRefreshVisibleSurface();
}

function weaverStartDecisionEdit(decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  ui.expanded = true;
  ui.editing = true;
  _agentPanelRefreshVisibleSurface();
}

function weaverDecisionDraftInput(decisionId, field, value) {
  var ui = _weaverDecisionUiState(decisionId);
  ui.draft[field] = String(value || '');
}

function weaverCancelDecisionEdit(decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  ui.editing = false;
  _agentPanelRefreshVisibleSurface();
}

function weaverSaveDecisionEdit(architectId, decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    title: String(ui.draft.title || '').trim(),
    rationale: String(ui.draft.rationale || '').trim(),
    status: String(ui.draft.status || 'proposed'),
  });
  ui.editing = false;
  if (typeof _showToast === 'function') _showToast('Decision updated', 'success');
  _agentPanelRefreshVisibleSurface();
}

function weaverArchiveDecision(architectId, decisionId) {
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    archived: true,
  });
  if (typeof _showToast === 'function') _showToast('Decision archived', 'success');
}

function weaverDecisionLinkSelect(decisionId, kind, value) {
  var ui = _weaverDecisionUiState(decisionId);
  if (kind === 'task') ui.link_task_id = String(value || '');
  if (kind === 'engineer') ui.link_engineer_id = String(value || '');
}

function weaverLinkDecisionTask(architectId, decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  if (!ui.link_task_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    task_id: ui.link_task_id,
  });
  ui.link_task_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to task', 'success');
  _agentPanelRefreshVisibleSurface();
}

function weaverLinkDecisionEngineer(architectId, decisionId) {
  var ui = _weaverDecisionUiState(decisionId);
  if (!ui.link_engineer_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    engineer_id: ui.link_engineer_id,
  });
  ui.link_engineer_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to engineer', 'success');
  _agentPanelRefreshVisibleSurface();
}

function renderLegacyGroupPanel() {
  var el = document.getElementById('panel-agent');
  _weaverStopEventsCountdownTimer();
  if (!el) return;
  var panelStateOptions = {
    scrollSelectors: ['.agent-panel-content'],
    captureFocusKey(active) {
      if (typeof _captureMainFocusKey === 'function') {
        var key = _captureMainFocusKey(active);
        if (key) return key;
      }
      if (active && active.classList
          && active.classList.contains('agent-panel-instructions')) {
        return '.agent-panel-instructions';
      }
      return '';
    },
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      snapshot.anchor = _weaverCaptureScrollAnchor(
        root.querySelector('.agent-panel-content')
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      _weaverRestoreScrollAnchor(
        root.querySelector('.agent-panel-content'),
        snapshot.anchor
      );
    },
  };
  var panelState = _captureSurfaceState(el, panelStateOptions);

  var group = _agentPanelCurrentGroup();
  var ws = _weaverGetSettings(group);
  var weaver = group ? _weaverGetAgent(group) : null;
  var bstats = (state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null;
  var paused = weaver
    ? !!(_agentPanelDigestSettings(weaver) && _agentPanelDigestSettings(weaver).paused)
    : !!(ws && ws.paused);
  var activeTab = _weaverActiveTab(group);
  var emptyMessage = _weaverPanelEmptyMessage(group, ws, weaver, bstats);

  var html = '<div class="agent-panel-panel">';

  // Header
  html += '<div class="agent-panel-header">';
  html += '<div class="agent-panel-header-copy">';
  html += '<span class="agent-panel-title">Architects &amp; Engineers';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  html += '<div class="agent-panel-subtitle">Architect roster, engineer hierarchy, orchestration journal, digest queue, worklog, and session map.</div>';
  html += '</div>';
  html += '<div class="agent-panel-header-right">';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="weaverOpenAddArchitect()">+ Add Architect</button>';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="weaverOpenAddEngineer()">+ Add Engineer</button>';
  // Buffer stats + Pause/Resume toggle
  if (!emptyMessage && group) {
    if (bstats && bstats.buffered_events > 0) {
      html += '<span class="agent-panel-buffer-stats">'
           + _esc(_weaverHeaderBufferStats(bstats, paused, weaver))
           + '</span>';
    }
    if (weaver && weaver.id) {
      html += _agentPanelDigestPauseButton(weaver);
    }
  }
  html += '</div>';
  html += '</div>';

  if (!emptyMessage) html += _agentPanelLegacyRenderTabs(group, activeTab);
  html += '<div class="agent-panel-content">';
  html += _agentPanelLegacyRenderArchitectRoster(group);
  html += _agentPanelLegacyRenderEngineerRoster(group);
  if (emptyMessage) {
    html += '<div class="agent-panel-empty">' + _esc(emptyMessage) + '</div>';
  } else if (activeTab === 'events') {
    html += _agentPanelLegacyRenderEvents(group, ws, weaver, bstats);
  } else if (activeTab === 'worklog') {
    html += _agentPanelLegacyRenderWorklog(group, ws);
  } else {
    html += _agentPanelLegacyRenderJournal(group);
  }
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
  _restoreSurfaceState(el, panelState, panelStateOptions);
  _weaverSyncEventsCountdown(el, group, activeTab);
}

function weaverTogglePause() {
  weaverTogglePauseForGroup(_agentPanelCurrentGroup());
}

function weaverTogglePauseForGroup(group) {
  if (!group) return;
  var ws = _weaverGetSettings(group);
  var cmd = (ws && ws.paused) ? 'weaver_resume' : 'weaver_pause';
  send({ cmd: cmd, group: group });
}

function toggleDigestPauseForAgent(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  var agent = (state && state.agents && state.agents[agentId]) || { id: agentId };
  var settings = _agentPanelDigestSettings(agent);
  var cmd = (settings && settings.paused) ? 'digest_resume' : 'digest_pause';
  send({ cmd: cmd, agent_id: agentId });
}

function agentPanelTogglePauseForAgent(agentId) {
  toggleDigestPauseForAgent(agentId);
}

function weaverSelectTab(tab, group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  if (tab !== 'events' && tab !== 'worklog') tab = 'journal';
  _weaverActiveTabByGroup[group] = tab;
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && _agentPanelKind(focusedAgent) === 'engineer' && typeof agentPanelSelectTab === 'function') {
      agentPanelSelectTab(tab);
      return;
    }
  }
  _agentPanelRefreshVisibleSurface();
}

function weaverSendNow() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_flush_now', group: group });
}

function agentPanelSendNow(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  send({ cmd: 'weaver_flush_now', agent_id: agentId });
}

function _weaverActiveTab(group) {
  if (!group) return 'journal';
  var tab = _weaverActiveTabByGroup[group] || 'journal';
  if (tab === 'events' || tab === 'worklog') return tab;
  return 'journal';
}

function _weaverJournalSubview(group) {
  if (!group) return 'journal';
  return _weaverJournalSubviewByGroup[group] === 'session_map'
    ? 'session_map'
    : 'journal';
}

function _weaverSessionMapMeta(group) {
  if (!group) return { loading: false, stale: false };
  if (!_weaverSessionMapMetaByGroup[group]) {
    _weaverSessionMapMetaByGroup[group] = { loading: false, stale: false };
  }
  return _weaverSessionMapMetaByGroup[group];
}

function _weaverSessionMapData(group) {
  if (!group || !state || !state.weaver_session_maps) return null;
  return state.weaver_session_maps[group] || null;
}

function _weaverRequestSessionMap(group, force) {
  if (!group) return;
  var meta = _weaverSessionMapMeta(group);
  var hasData = !!_weaverSessionMapData(group);
  if (meta.loading) return;
  if (!force && hasData && !meta.stale) return;
  meta.loading = true;
  send({ cmd: 'weaver_session_map_read', group: group });
}

function _weaverResetSessionMapMeta(options) {
  options = options || {};
  var groups = Object.keys(_weaverSessionMapMetaByGroup || {});
  if (!groups.length) return;
  var clearStale = options.clearStale !== false;
  var refetchOpenMissing = !!options.refetchOpenMissing;
  var shouldRender = false;
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i];
    if (!group) continue;
    var meta = _weaverSessionMapMeta(group);
    var wasLoading = !!meta.loading;
    meta.loading = false;
    if (clearStale) meta.stale = false;
    if (refetchOpenMissing && _weaverIsSessionMapOpen(group) && !_weaverSessionMapData(group)) {
      _weaverRequestSessionMap(group, true);
      if (_weaverShouldRenderCurrentGroup(group)) shouldRender = true;
      continue;
    }
    if (wasLoading && _weaverShouldRenderCurrentGroup(group)) shouldRender = true;
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _weaverIsSessionMapOpen(group) {
  return _weaverJournalSubview(group) === 'session_map';
}

function _weaverShouldRenderCurrentGroup(group) {
  return !!group
    && ((typeof _panelAppVisible === 'function' && _panelAppVisible('weaver'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'weaver'))
    && _agentPanelCurrentGroup() === group;
}

function _agentPanelRefreshVisibleSurface() {
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function weaverOpenSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _weaverJournalSubviewByGroup[group] = 'session_map';
  _weaverRequestSessionMap(group, false);
  _agentPanelRefreshVisibleSurface();
}

function weaverCloseSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _weaverJournalSubviewByGroup[group] = 'journal';
  _agentPanelRefreshVisibleSurface();
}

function weaverRefreshSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _weaverRequestSessionMap(group, true);
  _agentPanelRefreshVisibleSurface();
}

function _weaverReceiveSessionMap(msg) {
  var group = (msg && msg.group) || '';
  if (!group) return;
  var meta = _weaverSessionMapMeta(group);
  meta.loading = false;
  meta.stale = false;
  if (_weaverShouldRenderCurrentGroup(group)) {
    _agentPanelRefreshVisibleSurface();
  }
}

function _weaverMarkSessionMapStale(groups) {
  if (!groups || !groups.length) return;
  var shouldRender = false;
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i];
    if (!group) continue;
    var meta = _weaverSessionMapMeta(group);
    meta.stale = true;
    if (_weaverIsSessionMapOpen(group)) {
      _weaverRequestSessionMap(group, false);
      if (_weaverShouldRenderCurrentGroup(group)) shouldRender = true;
    }
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _agentPanelLegacyRenderTabs(group, activeTab) {
  if (!group) return '';
  var html = '<div class="agent-panel-tabs">';
  html += '<button id="weaver-tab-journal" class="agent-panel-tab'
    + (activeTab === 'journal' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'journal\')">Journal</button>';
  html += '<button id="weaver-tab-events" class="agent-panel-tab'
    + (activeTab === 'events' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'events\')">Events</button>';
  html += '<button id="weaver-tab-worklog" class="agent-panel-tab'
    + (activeTab === 'worklog' ? ' active' : '')
    + '" onclick="weaverSelectTab(\'worklog\')">Worklog</button>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEvents(group, ws, weaver, bstats) {
  if (!group) {
    return '<div class="agent-panel-empty">No weaver configured for any group.</div>';
  }
  var paused = !!(ws && ws.paused);
  var sent = (state.weaver_sent_events && state.weaver_sent_events[group])
    ? state.weaver_sent_events[group].slice()
    : [];
  return _agentPanelRenderEventsTab(
    bstats,
    sent,
    paused,
    weaver,
    'weaverSendNow()',
    'Already sent to Weaver'
  );
}

function _agentPanelLegacyRenderEventSection(title, events, mode, emptyText) {
  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-event-section-count">' + events.length + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">' + _esc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < events.length; i++) {
    html += _agentPanelLegacyRenderEventItem(events[i], mode);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEventItem(event, mode) {
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

  var html = '<div class="agent-panel-event-item agent-panel-event-item-' + _esc(mode) + '"'
    + ' data-weaver-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-event-item-header">';
  html += '<span class="agent-panel-event-kind">' + _esc(kind) + '</span>';
  html += '<span class="agent-panel-event-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-event-message">' + _esc(summary) + '</div>';
  if (event && event.task_id) {
    html += '<div class="agent-panel-event-task">' + _esc(event.task_id) + '</div>';
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
  var recipientName = String((weaver && (weaver.name || weaver.id)) || 'recipient');
  if (!bstats || !bstats.buffered_events) {
    return 'No queued events.';
  }
  if (paused) {
    return 'Delivery is paused — resume to send queued events.';
  }
  if (bstats.manual_flush_requested) {
    if (weaver && weaver.activity && weaver.activity !== 'waiting') {
      return 'Send requested — queued events will deliver when ' + recipientName + ' goes idle.';
    }
    return 'Sending queued events now.';
  }
  var nextPushIn = _weaverCountdownSeconds(bstats);
  if (nextPushIn <= 0) {
    if (weaver && weaver.activity && weaver.activity !== 'waiting') {
      return 'Eligible now — waiting for ' + recipientName + ' to go idle.';
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
  var surfaceState = _weaverVisibleEventsSurfaceState(group, activeTab);
  if (!surfaceState.group || surfaceState.tab !== 'events') return false;
  var payload = _weaverEventsSurfacePayload(surfaceState);
  if (!payload.bstats || !payload.bstats.buffered_events) return false;
  if (payload.paused) return false;
  if (payload.bstats.manual_flush_requested) return false;
  return _weaverCountdownSeconds(payload.bstats) > 0;
}

function _weaverStopEventsCountdownTimer() {
  if (_weaverEventsCountdownTimer && typeof clearInterval === 'function') {
    clearInterval(_weaverEventsCountdownTimer);
  }
  _weaverEventsCountdownTimer = 0;
}

function _weaverVisibleEventsSurfaceState(group, activeTab) {
  var fallbackGroup = String(group || '');
  var fallbackTab = String(activeTab || '');
  if (typeof _resolveFocusedAgent === 'function'
      && typeof _agentPanelKind === 'function'
      && typeof _agentPanelActiveTab === 'function') {
    var focused = _resolveFocusedAgent();
    if (focused && (
        _agentPanelKind(focused) === 'engineer'
        || _agentPanelKind(focused) === 'architect'
    )) {
      var focusedKind = _agentPanelKind(focused);
      return {
        agentId: String(focused.id || ''),
        group: String(focused.group || fallbackGroup || ''),
        tab: String(_agentPanelActiveTab(focusedKind) || fallbackTab || 'journal'),
      };
    }
  }
  var currentGroup = String(_agentPanelCurrentGroup() || fallbackGroup || '');
  return {
    agentId: '',
    group: currentGroup,
    tab: String(_weaverActiveTab(currentGroup) || fallbackTab || 'journal'),
  };
}

function _weaverEventsSurfacePayload(surfaceState) {
  var agentId = String((surfaceState && surfaceState.agentId) || '');
  var group = String((surfaceState && surfaceState.group) || '');
  if (agentId && state && state.agents && state.agents[agentId]) {
    var recipient = state.agents[agentId];
    var settings = _agentPanelDigestSettings(recipient);
    return {
      recipient: recipient,
      paused: !!(settings && settings.paused),
      bstats: _agentPanelDigestBufferStats(recipient),
    };
  }
  var ws = _weaverGetSettings(group);
  return {
    recipient: group ? _weaverGetAgent(group) : null,
    paused: !!(ws && ws.paused),
    bstats: (state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null,
  };
}

function _weaverSyncEventsCountdown(panel, group, activeTab) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var countdownEl = panel.querySelector('.agent-panel-events-countdown');
  if (!countdownEl) return;
  var surfaceState = _weaverVisibleEventsSurfaceState(group, activeTab);
  var surfaceGroup = surfaceState.group;
  var surfaceTab = surfaceState.tab;
  var payload = _weaverEventsSurfacePayload(surfaceState);
  countdownEl.textContent = _weaverEventsStatusText(
    payload.bstats,
    payload.paused,
    payload.recipient
  );
  if (!_weaverShouldLiveUpdateCountdown(surfaceGroup, surfaceTab)
      || typeof setInterval !== 'function') {
    return;
  }
  _weaverEventsCountdownTimer = setInterval(function() {
    var currentPanel = document.getElementById('panel-agent');
    if (!currentPanel) {
      _weaverStopEventsCountdownTimer();
      return;
    }
    var currentState = _weaverVisibleEventsSurfaceState(group, activeTab);
    var currentGroup = currentState.group;
    var currentTab = currentState.tab;
    var currentCountdown = currentPanel.querySelector('.agent-panel-events-countdown');
    if (!currentCountdown || currentTab !== 'events') {
      _weaverStopEventsCountdownTimer();
      return;
    }
    var currentPayload = _weaverEventsSurfacePayload(currentState);
    currentCountdown.textContent = _weaverEventsStatusText(
      currentPayload.bstats,
      currentPayload.paused,
      currentPayload.recipient
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

function _agentPanelLegacyRenderWorklog(group, ws) {
  if (!group) {
    return '<div class="agent-panel-empty">No weaver configured for any group.</div>';
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

  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Dispatched tasks</span>';
  html += '<span class="agent-panel-worklog-count">' + entries.length + '</span>';
  html += '</div>';
  if (ws && ws.restrict_to_created_agents) {
    html += '<div class="agent-panel-worklog-note">Showing only tasks sent to Weaver-created agents.</div>';
  } else {
    html += '<div class="agent-panel-worklog-note">Recent tasks this Weaver dispatched in this group.</div>';
  }

  if (!entries.length) {
    html += '<div class="agent-panel-event-empty">No dispatched tasks yet.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < entries.length; i++) {
    html += _agentPanelLegacyRenderWorklogItem(entries[i]);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderWorklogItem(entry) {
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

  var html = '<div class="agent-panel-worklog-item" data-weaver-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _esc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _esc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _esc(lane || 'Unknown') + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _esc(agentName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _esc(status) + '</div>';
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

function _agentPanelLegacyRenderJournal(group) {
  if (!group) {
    return '<div class="agent-panel-empty">No weaver configured for any group.</div>';
  }

  var html = '';
  var subview = _weaverJournalSubview(group);

  // Pending question banner
  var ws = _weaverGetSettings(group);
  if (ws && ws.pending_question) {
    html += '<div class="agent-panel-ask-banner">';
    html += '<div class="agent-panel-ask-label">Weaver is asking:</div>';
    html += '<div class="agent-panel-ask-question">' + _esc(ws.pending_question) + '</div>';
    html += '<textarea class="agent-panel-ask-reply" id="weaver-reply-input" '
         + 'placeholder="Type your reply..." rows="2" '
         + 'oninput="_weaverReplyDraft=this.value">' + _esc(_weaverReplyDraft) + '</textarea>';
    html += '<div class="agent-panel-ask-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="weaverDismissQuestion()">Dismiss</button>';
    html += '<button class="agent-panel-reply-btn" onclick="weaverReply()">Send Reply</button>';
    html += '</div>';
    html += '</div>';
  }
  if (ws && ws.pending_note) {
    var noteKind = ws.pending_note_kind || 'note';
    html += '<div class="agent-panel-note-banner">';
    html += '<div class="agent-panel-note-label">'
      + (noteKind === 'question'
        ? 'Weaver asks (non-blocking):'
        : 'Weaver note:')
      + '</div>';
    html += '<div class="agent-panel-note-text">' + _esc(ws.pending_note) + '</div>';
    html += '<div class="agent-panel-note-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="weaverDismissNote()">Dismiss</button>';
    html += '</div>';
    html += '</div>';
  }

  html += _agentPanelLegacyRenderJournalToolbar(group, subview);
  if (subview === 'session_map') {
    html += _agentPanelLegacyRenderSessionMap(group);
    return html;
  }

  // Journal entries come from state.weaver_journal (populated by delta ops)
  var entries = (state.weaver_journal && state.weaver_journal[group]) || [];
  if (entries.length) {
    html += _agentPanelLegacyRenderJournalEntries(entries, true);
  } else {
    html += '<div class="agent-panel-empty">No journal entries yet.</div>';
  }
  return html;
}

function _agentPanelLegacyRenderJournalToolbar(group, subview) {
  var groupJs = JSON.stringify(String(group || ''));
  var html = '<div class="agent-panel-session-map-toolbar">';
  html += '<div class="agent-panel-session-map-actions">';
  if (subview === 'session_map') {
    html += '<button class="agent-panel-session-map-btn" onclick=\'weaverCloseSessionMap('
      + groupJs + ")\'>Back to Journal</button>";
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'weaverRefreshSessionMap('
      + groupJs + ")\'>Refresh</button>";
  } else {
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'weaverOpenSessionMap('
      + groupJs + ")\'>Session Map</button>";
  }
  html += '</div>';
  var status = _weaverSessionMapStatus(group, subview);
  if (status) {
    html += '<div class="agent-panel-session-map-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _weaverSessionMapStatus(group, subview) {
  var meta = _weaverSessionMapMeta(group);
  if (subview !== 'session_map') return '';
  if (meta.loading && _weaverSessionMapData(group)) {
    return 'Refreshing deterministic snapshot…';
  }
  if (meta.loading) return 'Loading deterministic snapshot…';
  if (meta.stale) return 'Snapshot is updating…';
  return '';
}

function _agentPanelLegacyRenderSessionMap(group) {
  var sessionMap = _weaverSessionMapData(group);
  var meta = _weaverSessionMapMeta(group);
  if (!sessionMap) {
    return '<div class="agent-panel-session-map-empty">'
      + _esc(meta.loading
        ? 'Loading Session Map…'
        : 'Open Session Map to load the current deterministic orchestration snapshot.')
      + '</div>';
  }

  var html = '<div class="agent-panel-session-map">';
  html += _agentPanelLegacyRenderSessionMapOverview(sessionMap.overview || {});
  html += _agentPanelLegacyRenderSessionMapHints(sessionMap.hints || {});
  html += _agentPanelLegacyRenderSessionMapStreams(sessionMap.streams || {});
  html += _agentPanelLegacyRenderSessionMapAsks('Pending asks', sessionMap.asks || {}, 'Ask pending');
  html += _agentPanelLegacyRenderSessionMapHumanGates(sessionMap.human_gates || {});
  html += _agentPanelLegacyRenderSessionMapTaskHealth(sessionMap.task_health || {});
  html += _agentPanelLegacyRenderSessionMapVerification(sessionMap.verification || {});
  html += _agentPanelLegacyRenderSessionMapBoundaries(sessionMap.branch_boundaries || {});
  html += _agentPanelLegacyRenderSessionMapAgents(sessionMap.agents || {});
  html += _agentPanelLegacyRenderSessionMapQueuedFollowUp(sessionMap.queued_follow_up || {});
  html += _agentPanelLegacyRenderSessionMapJournal(sessionMap.journal || {});
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapOverview(overview) {
  if (!overview || typeof overview !== 'object') return '';
  var stats = [
    { label: 'Tasks', value: overview.tasks_total || 0 },
    { label: 'Streams', value: overview.active_stream_count || 0 },
    { label: 'Active agents', value: overview.active_agent_count || 0 },
    { label: 'Asks', value: overview.pending_ask_count || 0 },
    { label: 'Human gates', value: overview.human_gate_count || 0 },
    { label: 'Queued follow-up', value: overview.queued_follow_up_count || 0 },
  ];
  var html = '<div class="agent-panel-session-map-overview">';
  for (var i = 0; i < stats.length; i++) {
    html += '<div class="agent-panel-session-map-stat">';
    html += '<div class="agent-panel-session-map-stat-value">' + _esc(String(stats[i].value)) + '</div>';
    html += '<div class="agent-panel-session-map-stat-label">' + _esc(stats[i].label) + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHints(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Hints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' current hint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">'
      + _esc(_weaverHumanizeToken(item.kind || 'hint')) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.message || '') + '</span>';
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapStreams(summary) {
  var streams = _weaverSummaryItems(summary);
  if (!streams.length) return '';
  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active streams</span>';
  html += '<span class="agent-panel-health-total">' + streams.length + ' active stream'
    + (streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAsks(title, summary, pillLabel) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">' + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.title || '') + '</span>';
    if (item.parent_task_id) {
      html += '<span class="agent-panel-session-map-item-meta">via ' + _esc(item.parent_task_id) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHumanGates(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Human gates</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' waiting</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">Validation gate</span>';
    html += '<span class="agent-panel-session-map-item-text">'
      + _esc(item.stream_title || item.branch || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^loom\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapTaskHealth(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-health-item">';
    html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state || 'blocked') + '">'
      + _esc(_weaverHealthLabels[item.health_state] || item.health_state || 'Unhealthy') + '</span>';
    html += '<span class="agent-panel-health-item-title">' + _esc(item.title || '') + '</span>';
    if (item.via) {
      html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapVerification(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open checkpoint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state || 'pending') + '">'
      + _esc(_weaverVerificationLabels[item.verification_state] || item.verification_state || 'Verification') + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title || '') + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapBoundaries(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">'
      + _esc(item.latest_boundary_task || item.stream_title || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^loom\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.foreground_task_title) {
      html += '<div class="agent-panel-verification-item-meta">Current: '
        + _esc(item.foreground_task_title) + '</div>';
    }
    if (item.queued_followups && item.queued_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
    if (item.started_followups && item.started_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAgents(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active agents</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' active</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(item.status || 'agent') + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.name || item.slug || item.id || '') + '</span>';
    if (item.current_task) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.current_task) + '</span>';
    }
    html += '</div>';
    if (item.activity_detail) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.activity_detail) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapQueuedFollowUp(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Queued follow-up</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' queued item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pill = item.source === 'dispatch_queue' ? 'Dispatch queue' : _weaverHumanizeToken(item.queue_state || 'Stream queue');
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(pill) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.task_title || item.task_id || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^loom\//, '')) + '</span>';
    } else if (item.target_agent_name) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.target_agent_name) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapJournal(summary) {
  var items = _weaverSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Recent decisions, plans, and checkpoints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += _agentPanelLegacyRenderJournalEntries(items, false);
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderJournalEntries(entries, allowContextMenu) {
  var sorted = (entries || []).slice().sort(function(a, b) {
    return (b.id || 0) - (a.id || 0);
  });
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < sorted.length; i++) {
    var e = sorted[i];
    var typeClass = 'agent-panel-badge-' + (e.type || 'observation');
    var ago = _weaverTimeAgo(e.timestamp);
    html += '<div class="agent-panel-entry"'
      + (allowContextMenu && e.id
        ? ' oncontextmenu="weaverEntryCtx(event,' + e.id + ')"'
        : '')
      + '>';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="agent-panel-badge ' + typeClass + '">' + _esc(e.type || '?') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _esc(ago) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _esc(e.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _weaverSummaryItems(summary) {
  if (!summary || typeof summary !== 'object') return [];
  if (Array.isArray(summary.items)) return summary.items.slice();
  if (Array.isArray(summary.entries)) return summary.entries.slice();
  return [];
}

function _agentPanelLegacyRenderOpenStreams(group) {
  var summary = _weaverOpenStreamsSummary(group);
  if (!summary.show) return '';

  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Open Streams</span>';
  html += '<span class="agent-panel-health-total">' + summary.streams.length + ' open stream'
    + (summary.streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';

  if (!summary.streams.length) {
    html += '<div class="agent-panel-stream-empty">No open streams.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < summary.streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(summary.streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _weaverOpenStreamsSummary(group) {
  var result = { show: false, streams: [] };
  if (!group || !state) return result;
  var raw = _weaverRawStreamPayload(group);
  if (typeof raw === 'undefined') {
    return result;
  }
  result.show = true;
  var items = _weaverStreamItemsFromPayload(raw);
  for (var i = 0; i < items.length; i++) {
    if (_weaverStreamIsOpen(items[i])) result.streams.push(items[i]);
  }
  return result;
}

function _weaverRawStreamPayload(group) {
  if (!group || !state) return undefined;
  if (state.weaver_streams
      && Object.prototype.hasOwnProperty.call(state.weaver_streams, group)) {
    return state.weaver_streams[group];
  }
  if (state.weaver_board_summary
      && state.weaver_board_summary[group]
      && state.weaver_board_summary[group].streams) {
    return state.weaver_board_summary[group].streams;
  }
  return undefined;
}

function _weaverStreamItemsFromPayload(raw) {
  if (Array.isArray(raw)) return raw.slice();
  if (!raw || typeof raw !== 'object') return [];
  if (Array.isArray(raw.items)) return raw.items.slice();
  if (Array.isArray(raw.streams)) return raw.streams.slice();
  if (raw.streams && Array.isArray(raw.streams.items)) {
    return raw.streams.items.slice();
  }
  return [];
}

function _weaverStreamIsOpen(stream) {
  if (!stream) return false;
  if (stream.is_open === false) return false;
  var mergeState = String(_weaverStreamMergeState(stream) || '').toLowerCase();
  var stateName = String(_weaverStreamStateName(stream) || '').toLowerCase();
  return mergeState !== 'merged' && stateName !== 'merged';
}

function _agentPanelLegacyRenderOpenStreamCard(stream, index) {
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

  var html = '<div class="agent-panel-stream-card" data-weaver-anchor="' + _esc(key) + '">';
  html += '<div class="agent-panel-stream-card-header">';
  html += '<div class="agent-panel-stream-heading">';
  html += '<div class="agent-panel-stream-title-row">';
  html += '<span class="agent-panel-stream-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-state agent-panel-stream-state-'
    + _esc(stateMeta.className) + '">' + _esc(stateMeta.label) + '</span>';
  html += '</div>';
  if (branch && branch !== title) {
    html += '<div class="agent-panel-stream-branch">' + _esc(branch) + '</div>';
  }
  html += '</div>';
  if (mergeMeta.label) {
    html += '<span class="agent-panel-stream-merge agent-panel-stream-merge-' + _esc(mergeMeta.className)
      + '">' + _esc(mergeMeta.label) + '</span>';
  }
  html += '</div>';

  var metaHtml = '';
  if (latestCommit) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Reviewed', latestCommit);
  }
  if (gateReason) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Gate', gateReason);
  }
  if (nextAction) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Next', nextAction);
  }
  if (metaHtml) {
    html += '<div class="agent-panel-stream-meta-list">';
    html += metaHtml;
    html += '</div>';
  }

  if (productTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Product tasks',
      'product',
      productTasks,
      false
    );
  }
  if (workflowTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Workflow',
      'workflow',
      workflowTasks,
      true
    );
  }
  if (visibilityItems.length) {
    html += _agentPanelLegacyRenderStreamVisibilityGroup(visibilityItems);
  }

  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderStreamMetaRow(label, value) {
  return '<div class="agent-panel-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="agent-panel-stream-meta-value">' + _esc(value) + '</div>';
}

function _agentPanelLegacyRenderStreamTaskGroup(title, kind, tasks, summarizeOnly) {
  if (!tasks.length) return '';
  var summary = tasks.length + ' ' + kind + ' task' + (tasks.length === 1 ? '' : 's');
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-' + _esc(kind)
    + '">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-summary-count">' + _esc(summary) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-task-list">';
  var limit = summarizeOnly ? 2 : 3;
  for (var i = 0; i < tasks.length && i < limit; i++) {
    var item = tasks[i];
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-' + _esc(kind) + '">';
    html += _esc(item.title || item.id || '');
    html += '</span>';
  }
  if (tasks.length > limit) {
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-more">+'
      + (tasks.length - limit) + ' more</span>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderStreamVisibilityGroup(items) {
  if (!items.length) return '';
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-context">'
    + 'Recent context</span>';
  html += '<span class="agent-panel-stream-summary-count">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-visibility-list">';
  for (var i = 0; i < items.length && i < 2; i++) {
    var item = items[i];
    var kind = _weaverVisibilityKindLabel(item);
    html += '<div class="agent-panel-stream-visibility-item">';
    if (kind) {
      html += '<span class="agent-panel-stream-visibility-kind">' + _esc(kind) + '</span>';
    }
    html += '<span class="agent-panel-stream-visibility-text">' + _esc(item.summary) + '</span>';
    html += '</div>';
  }
  if (items.length > 2) {
    html += '<div class="agent-panel-stream-visibility-more">+' + (items.length - 2)
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
    'continue_implementation': 'Continue implementation',
    'run_manual_validation': 'Run manual validation',
    'merge_after_validation': 'Merge after validation',
    'merge': 'Merge stream',
    'merge_stream': 'Merge stream',
    'address_review_blockers': 'Address review blockers',
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
      || stream.foreground_task_title
      || stream.display_name
      || stream.label
      || stream.title
      || '';
  }
  if (!title) {
    var productTasks = _weaverStreamTaskItems(stream, 'product');
    if (productTasks.length) title = productTasks[0].title || '';
  }
  if (!title && stream && stream.latest_boundary_task_title) {
    title = stream.latest_boundary_task_title;
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

function _agentPanelLegacyRenderTaskHealth(group) {
  var summary = _weaverTaskHealthSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < _weaverHealthOrder.length; i++) {
    var stateName = _weaverHealthOrder[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_weaverHealthLabels[stateName]) + '</span>';
  }
  html += '</div>';
  if (summary.items.length) {
    html += '<div class="agent-panel-health-list">';
    for (var j = 0; j < summary.items.length; j++) {
      var item = summary.items[j];
      html += '<div class="agent-panel-health-item">';
      html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state) + '">'
        + _esc(_weaverHealthLabels[item.health_state] || item.health_state) + '</span>';
      html += '<span class="agent-panel-health-item-title">' + _esc(item.title) + '</span>';
      if (item.via) {
        html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderVerificationSummary(group) {
  var summary = _weaverVerificationSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' open checkpoint' + (summary.total === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < summary.order.length; i++) {
    var stateName = summary.order[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_weaverVerificationLabels[stateName] || stateName) + '</span>';
  }
  html += '</div>';
  for (var j = 0; j < summary.items.length; j++) {
    var item = summary.items[j];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state) + '">'
      + _esc(_weaverVerificationLabels[item.verification_state] || item.verification_state) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title) + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderBoundarySummary(group) {
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

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.latest_boundary_task) + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^loom\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.current_task) {
      html += '<div class="agent-panel-verification-item-meta">Current: ' + _esc(item.current_task) + '</div>';
    }
    if (item.queued_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
    if (item.started_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
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
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_journal_delete', group: group, entry_id: entryId });
  // Optimistic removal from local state
  if (state.weaver_journal && state.weaver_journal[group]) {
    state.weaver_journal[group] = state.weaver_journal[group].filter(
      function(e) { return e.id !== entryId; });
  }
  _agentPanelRefreshVisibleSurface();
}

// -- Human reply -----------------------------------------------------------

function weaverReply() {
  var input = document.getElementById('weaver-reply-input');
  if (!input) return;
  var answer = input.value.trim();
  if (!answer) return;
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  // Blur so the re-render skip guard doesn't block the banner clearing
  input.blur();
  send({ cmd: 'weaver_reply', group: group, answer: answer });
}

function weaverDismissQuestion() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _weaverReplyDraft = '';
  send({ cmd: 'weaver_resume', group: group });
}

function weaverDismissNote() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'weaver_dismiss_note', group: group });
}

// -- Event handlers --------------------------------------------------------

function weaverInstrInput(textarea) {
  _weaverCustomInstrDirty = true;
  _weaverCustomInstrDraft = textarea.value;
  // Show save button (re-render just the settings section would be heavy;
  // instead just toggle the button visibility)
  var btn = textarea.parentElement.querySelector('.agent-panel-save-btn');
  if (!btn) {
    var b = document.createElement('button');
    b.className = 'agent-panel-save-btn';
    b.textContent = 'Save';
    b.onclick = weaverSaveInstructions;
    textarea.parentElement.appendChild(b);
  }
}

function weaverSaveInstructions() {
  var group = _agentPanelCurrentGroup();
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
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var payload = { cmd: 'weaver_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function weaverToggleEvent(evt, enabled) {
  var group = _agentPanelCurrentGroup();
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

function _weaverPanelEmptyMessage(group, ws, weaver, bstats) {
  if (!group) return 'Select a group to inspect Weaver orchestration state.';
  if (_weaverHasPanelState(group, ws, weaver, bstats)) return '';
  return 'No Weaver configured for ' + group + ' yet.';
}

function _weaverHasPanelState(group, ws, weaver, bstats) {
  if (!group) return false;
  if (weaver) return true;
  if (_weaverGroupHasConfiguredAgent(group)) return true;
  if (_weaverGroupHasState(ws)) return true;
  if (_weaverGroupHasState(bstats)) return true;
  if (_weaverGroupStoreHasState(state.weaver_board_summary, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_buffer_stats, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_journal, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_sent_events, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_session_maps, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_streams, group)) return true;
  if (_weaverGroupStoreHasState(state.weaver_worklog, group)) return true;
  return false;
}

function _weaverGroupHasConfiguredAgent(group) {
  if (!group || !state || !state.group_settings) return false;
  var settings = state.group_settings[group];
  return !!(settings && settings.weaver_agent_id);
}

function _weaverGroupStoreHasState(store, group) {
  if (!store || !group) return false;
  return _weaverGroupHasState(store[group]);
}

function _weaverGroupHasState(value) {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return !!value;
}

function _agentPanelCurrentGroup() {
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && focusedAgent.group) return focusedAgent.group || '';
  }
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

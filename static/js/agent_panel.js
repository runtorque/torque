/* Agent panel — focused-agent router with per-kind renderers */

var _agentPanelLastSelectedTabByKind = {};
var _agentPanelTabSpecByKind = {
  architect: [
    { key: 'decisions', label: 'Decisions' },
    { key: 'hired_engineers', label: 'Hired engineers' },
    { key: 'messages', label: 'Messages' },
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

function _agentPanelShell(title, subtitle, kind, activeTab, bodyHtml) {
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
  if (typeof _weaverRenderJournal === 'function') return _weaverRenderJournal(group);
  var entries = (state && state.weaver_journal && state.weaver_journal[group]) || [];
  if (typeof _weaverRenderJournalEntries === 'function') return _weaverRenderJournalEntries(entries, true);
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

function _renderEngineerEvents(agent) {
  var group = String((agent && agent.group) || '');
  var ws = _agentPanelWeaverSettings(group);
  var weaver = _agentPanelWeaverAgent(group);
  var bstats = (state && state.weaver_buffer_stats && state.weaver_buffer_stats[group]) || null;
  if (typeof _weaverRenderEvents === 'function') return _weaverRenderEvents(group, ws, weaver, bstats);
  return '<div class="agent-panel-empty">No group events yet.</div>';
}

function _renderEngineerWorklog(agent) {
  var group = String((agent && agent.group) || '');
  var ws = _agentPanelWeaverSettings(group);
  if (typeof _weaverRenderWorklog === 'function') return _weaverRenderWorklog(group, ws);
  return '<div class="agent-panel-empty">No dispatched tasks yet.</div>';
}

function _renderEngineerPanel(agent) {
  var group = String((agent && agent.group) || '');
  var activeTab = _agentPanelActiveTab('engineer');
  var body = _agentPanelGroupWideNote(group);
  if (activeTab === 'events') {
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
    body
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
  if (typeof _weaverRenderEngineerTreeRows === 'function') {
    html += _weaverRenderEngineerTreeRows(group, engineers, 'architect-roster-level-1', 'architect-roster-level-2');
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

  if (typeof _weaverDecisionGroups === 'function' && typeof _weaverRenderDecisionRow === 'function'
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
        html += _weaverRenderDecisionRow(agent.id, rows[rowIndex]);
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
  if (activeTab === 'hired_engineers') {
    body = _agentPanelArchitectHiredEngineers(agent);
  } else if (activeTab === 'messages') {
    body = _agentPanelArchitectMessages(agent);
  } else {
    body = _agentPanelArchitectDecisionsHtml(agent);
  }
  return _agentPanelShell(
    'Architect: ' + ((agent && (agent.name || agent.id)) || 'Unknown')
      + ' · Group: ' + (((agent && agent.group) || '') || '—'),
    'Decisions, hired engineers, and architect messages.',
    'architect',
    activeTab,
    body
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
  if (agent
      && _agentPanelKind(agent) === 'engineer'
      && typeof _weaverSyncEventsCountdown === 'function') {
    _weaverSyncEventsCountdown(el, agent.group || '', _agentPanelActiveTab('engineer'));
  }
}

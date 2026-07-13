/* Agent panel module: worker. */

function _agentPanelWorkerEvents(agent) {
  var events = _agentPanelSortedWorkerEvents(agent);
  var page = _agentPanelEventPage(agent, events);

  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">Worker events</span>';
  html += '<span class="agent-panel-event-section-count">' + _agentPanelEventSectionCount(page) + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">No worker events yet.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < page.events.length; i++) {
    var evt = page.events[i] || {};
    var anchorKey = 'worker-event-' + String(evt.id || i);
    var kind = typeof _engineerEventKindLabel === 'function'
      ? _engineerEventKindLabel(evt.kind)
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
  html += _agentPanelRenderEventLoadMore(page);
  html += '</div>';
  return html;
}

function _agentPanelWorkerTaskEntries(agent) {
  var agentId = String((agent && agent.id) || '');
  var tasks = [];
  if (!agentId || !state || !state.board_tasks) return tasks;
  var boardTaskCount = 0;
  for (var countTaskId in state.board_tasks) {
    if (Object.prototype.hasOwnProperty.call(state.board_tasks, countTaskId)) {
      boardTaskCount++;
    }
  }
  var cachedIds = _agentPanelWorkerTaskIdCacheByAgent[agentId];
  if (cachedIds && cachedIds._boardTaskCount !== boardTaskCount) {
    cachedIds = null;
  }
  if (!cachedIds) {
    cachedIds = [];
    for (var taskId in state.board_tasks) {
      var candidate = state.board_tasks[taskId];
      if (!candidate) continue;
      if (String(candidate.agent_id || '') !== agentId) continue;
      cachedIds.push(taskId);
    }
    cachedIds.sort(function(aId, bId) {
      var a = state.board_tasks[aId];
      var b = state.board_tasks[bId];
      var aTs = Number((a && (a.started_at || a.created_at || a.updated_at)) || 0);
      var bTs = Number((b && (b.started_at || b.created_at || b.updated_at)) || 0);
      if (aTs !== bTs) return bTs - aTs;
      return String((b && b.id) || bId || '').localeCompare(String((a && a.id) || aId || ''));
    });
    cachedIds._boardTaskCount = boardTaskCount;
    _agentPanelWorkerTaskIdCacheByAgent[agentId] = cachedIds;
  }
  for (var i = 0; i < cachedIds.length; i++) {
    var task = state.board_tasks[cachedIds[i]];
    if (task && String(task.agent_id || '') === agentId) tasks.push(task);
  }
  return tasks;
}

function _agentPanelInvalidateWorkerTaskCacheForTask(previous, next) {
  var ids = {};
  if (previous && previous.agent_id) ids[String(previous.agent_id)] = true;
  if (next && next.agent_id) ids[String(next.agent_id)] = true;
  for (var agentId in ids) {
    delete _agentPanelWorkerTaskIdCacheByAgent[agentId];
  }
}

function _agentPanelInvalidateWorkerTaskCacheForDeltas(changes) {
  if (!Array.isArray(changes)) return;
  for (var i = 0; i < changes.length; i++) {
    var change = changes[i] || {};
    _agentPanelInvalidateWorkerTaskCacheForTask(change.previous, change.next);
  }
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
  html += _agentPanelRenderVirtualList({
    key: _agentPanelFocusedSurfaceKey(agent, 'worklog', 'worker-tasks'),
    total: tasks.length,
    rowHeight: _AGENT_PANEL_WORKLOG_ROW_HEIGHT,
    listClass: 'agent-panel-worklog-list',
    scrollSelector: '.agent-panel-content',
    renderItem: function(index) {
      return _agentPanelRenderWorkerWorklogItem(agent, tasks[index]);
    },
  });
  html += '</div>';
  return html;
}

function _renderWorkerPanel(agent) {
  var activeTab = _agentPanelActiveTab('worker');
  var parts = _agentPanelTabRenderParts(agent, 'worker', activeTab);
  return _agentPanelShell(
    _agentPanelRoleTitle(agent, 'Worker'),
    'Per-worker event stream and task history.',
    'worker',
    activeTab,
    _agentPanelBodyWithClassManager(agent, parts.bodyHtml, activeTab === 'behavior'),
    (parts.headerRightHtml || ''),
    (agent && agent.id) || '',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

function _agentPanelWorkerMessages(agent) {
  return _agentPanelMessagesHtml(
    agent,
    _agentPanelInlineThreadMessageList(agent),
    'Inline Engineer messages stored on this worker’s current tasks.'
  );
}

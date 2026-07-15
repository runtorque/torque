/* Board module: model, filtering, and lane render caches. */

function _boardLanes() {
  return (state && state.board_lanes) || [];
}

function _boardVisibleLanes() {
  var lanes = _boardLanes();
  if (_boardShowArchived) return lanes.slice();
  return lanes.filter(function(lane) {
    return lane !== _boardArchivedLane;
  });
}

function _boardAddTaskLaneOptions() {
  return _boardLanes().filter(function(lane) {
    return lane && lane !== _boardArchivedLane;
  });
}

function _boardDefaultAddTaskLane() {
  var lanes = _boardAddTaskLaneOptions();
  if (!lanes.length) return _boardSelectedLane || '';
  if (lanes.indexOf('Backlog') >= 0) return 'Backlog';
  return lanes[0];
}

function _boardNormalizeAddingTaskLane() {
  if (!_boardAddingTask) return;
  var addTaskLaneOptions = _boardAddTaskLaneOptions();
  if (!_boardAddingTaskLane || addTaskLaneOptions.indexOf(_boardAddingTaskLane) === -1) {
    _boardAddingTaskLane = _boardDefaultAddTaskLane();
  }
}

function _boardTasks() {
  return (state && state.board_tasks) || {};
}

function _boardIsEmbeddedRuntime() {
  return !!(
    (state && state.runtime && state.runtime.embedded_terminal)
    || (document && document.body && document.body.classList
      && document.body.classList.contains('runtime-embedded'))
  );
}

function _boardPanelWidth(panel) {
  if (panel && typeof panel.clientWidth === 'number' && panel.clientWidth > 0) {
    return panel.clientWidth;
  }
  if (typeof window !== 'undefined' && typeof window.innerWidth === 'number') {
    return window.innerWidth;
  }
  return 0;
}

function _boardWideShellActive(panel) {
  if (!_boardIsEmbeddedRuntime()) return false;
  return _boardPanelWidth(panel || document.getElementById('panel-board')) >= _boardWideModeMinWidth;
}

function _boardWideLayoutActive(panel) {
  return !_boardShowSchedules && _boardWideShellActive(panel);
}

function _boardPanelVisible() {
  if (typeof _standalonePanelSurfaceVisible === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()) {
    return _standalonePanelSurfaceVisible('board');
  }
  var panel = document.getElementById('bottom-panel');
  if (!panel || !panel.classList) return true;
  return !panel.classList.contains('collapsed');
}

function _boardIsArchived(task) {
  return !!(task && (task.lane === _boardArchivedLane
    || (task.labels && task.labels.indexOf(_boardArchiveLabel) >= 0)));
}

function _boardCopyTaskMap(tasks) {
  var out = {};
  for (var id in (tasks || {})) out[id] = tasks[id];
  return out;
}

function _boardFilterArchivedTasks(tasks) {
  var filtered = {};
  for (var taskId in (tasks || {})) {
    if (!_boardIsArchived(tasks[taskId])) filtered[taskId] = tasks[taskId];
  }
  return filtered;
}

function _boardScopedTasks(includeArchived) {
  var all = _boardTasks();
  var out = {};
  if (_boardFilterByGroup) {
    var grp = _currentGroup();
    if (grp) {
      for (var id in all) {
        if (all[id].group === grp) out[id] = all[id];
      }
    } else {
      for (var id in all) out[id] = all[id];
    }
  } else {
    for (var id in all) out[id] = all[id];
  }
  if (includeArchived) return out;
  return _boardFilterArchivedTasks(out);
}

function _boardArchivedCount(model) {
  if (model && typeof model.archivedCount === 'number') return model.archivedCount;
  var tasks = _boardScopedTasks(true);
  var count = 0;
  for (var id in tasks) {
    if (_boardIsArchived(tasks[id])) count++;
  }
  return count;
}

function _boardTaskIdsWithDescendants(taskIds) {
  var tasks = _boardTasks();
  var picked = {};
  var queue = [];
  for (var i = 0; i < taskIds.length; i++) {
    if (tasks[taskIds[i]] && !picked[taskIds[i]]) {
      picked[taskIds[i]] = true;
      queue.push(taskIds[i]);
    }
  }
  while (queue.length) {
    var parentId = queue.shift();
    for (var id in tasks) {
      if (tasks[id].parent_task_id === parentId && !picked[id]) {
        picked[id] = true;
        queue.push(id);
      }
    }
  }
  return Object.keys(picked);
}

function _boardArchiveTaskIds(taskIds, archived) {
  var tasks = _boardTasks();
  var expanded = _boardTaskIdsWithDescendants(taskIds);
  for (var i = 0; i < expanded.length; i++) {
    var task = tasks[expanded[i]];
    if (!task) continue;
    send({
      cmd: archived ? 'board_archive_task' : 'board_unarchive_task',
      id: task.id,
    });
  }
}

function _boardSelectedArchiveIds(archived) {
  var tasks = _boardTasks();
  var ids = [];
  for (var id in _boardSelectedTasks) {
    var task = tasks[id];
    if (!task) continue;
    if (archived) {
      if (_boardIsArchived(task)) ids.push(id);
    } else if (task.lane === 'Done' && !_boardIsArchived(task)) {
      ids.push(id);
    }
  }
  return ids;
}


/** Return visible tasks, optionally filtered to the current group. */

function _boardVisibleTasks() {
  _boardSyncFiltersForCurrentGroup();
  return _boardVisibleTasksFromScoped(_boardScopedTasks(_boardShowArchived));
}

function _boardVisibleTasksFromScoped(scopedTasks) {
  var out = _boardCopyTaskMap(scopedTasks);
  if (_boardQuickView) {
    var ranked = [];
    for (var rid in out) ranked.push(out[rid]);
    ranked.sort(_boardQuickView === 'recent'
      ? _boardNewestCompare
      : _boardRecentlyTouchedCompare);
    var limited = {};
    for (var li = 0; li < Math.min(ranked.length, 25); li++) {
      limited[ranked[li].id] = ranked[li];
    }
    out = limited;
  }
  // Text search filter
  if (_boardSearchQuery) {
    var q = _boardSearchQuery.toLowerCase();
    var filtered = {};
    // Description is lazy in compact-v1 — only include it for tasks whose
    // full detail has been loaded so a stale `undefined` can't match (or
    // silently fail to match) a query aimed at description body text.
    var compactActive = typeof _compactModeActive === 'function'
      && _compactModeActive();
    for (var id in out) {
      var t = out[id];
      var descriptionForSearch = t.description || '';
      var hasLazyDetail = !compactActive
        || typeof _compactTaskHasFullDetail !== 'function'
        || _compactTaskHasFullDetail(t);
      if (!hasLazyDetail) {
        descriptionForSearch = '';
      }
      var parts = [t.task, descriptionForSearch, t.id, t.action_name, t.agent_id];
      parts.push(t.verification_mode || '');
      parts.push(t.verification_state || '');
      if (hasLazyDetail) {
        parts.push(t.verification_notes || '');
        var verificationSummary = t.verification_summary || {};
        parts.push(verificationSummary.tests_run || '');
        parts.push(verificationSummary.human_validation_pending || '');
        parts.push(verificationSummary.test_outcome || '');
        parts.push(verificationSummary.isolated_rerun_evidence || '');
        parts.push(verificationSummary.reviewer_acceptance || '');
        if (verificationSummary.live_smoke_pending) parts.push('live smoke pending');
        if (verificationSummary.full_suite_attempted) parts.push('full suite attempted');
        if (verificationSummary.unrelated_flake_accepted) parts.push('unrelated flake accepted');
        if (verificationSummary.deploy_attempted === false) parts.push('deploy not attempted');
        var completionEvidence = t.completion_evidence || {};
        parts.push(completionEvidence.status || '');
        if (completionEvidence.sources && completionEvidence.sources.length) {
          parts.push(completionEvidence.sources.join(' '));
        }
        var completionMerge = completionEvidence.merge || {};
        parts.push(completionMerge.sha || '');
        parts.push(completionMerge.origin_summary || '');
        parts.push(completionMerge.pr_url || '');
        var completionVerification = completionEvidence.verification || {};
        var completionSummary = completionVerification.summary || {};
        parts.push(completionSummary.tests_run || '');
      }
      if (t.labels && t.labels.length) {
        for (var li = 0; li < t.labels.length; li++) {
          parts.push(t.labels[li]);
          if (typeof displayLabel === 'function') {
            parts.push(displayLabel(t.labels[li]));
          }
        }
      }
      if (t.agent_id && state && state.agents && state.agents[t.agent_id]) {
        var agent = state.agents[t.agent_id];
        parts.push(agent.name || '');
        parts.push(agent.slug || '');
      }
      var haystack = parts.join('\n').toLowerCase();
      if (haystack.indexOf(q) >= 0) {
        filtered[id] = t;
      }
    }
    out = filtered;
  }
  // Label filter (OR — task matches if it has ANY selected label)
  if (_boardFilterLabels.length) {
    var filtered = {};
    for (var id in out) {
      var t = out[id];
      var labels = t.labels || [];
      var match = false;
      for (var i = 0; i < _boardFilterLabels.length; i++) {
        if (labels.indexOf(_boardFilterLabels[i]) >= 0) { match = true; break; }
      }
      if (match) filtered[id] = t;
    }
    out = filtered;
  }
  // Action filter (OR — task matches if its action is any of the selected)
  if (_boardFilterActions.length) {
    var filtered = {};
    for (var id in out) {
      var t = out[id];
      if (t.action_name && _boardFilterActions.indexOf(t.action_name) >= 0) {
        filtered[id] = t;
      }
    }
    out = filtered;
  }
  // Agent filter (OR — task matches if its agent_id is any of the selected)
  if (_boardFilterAgents.length) {
    var filtered = {};
    for (var id in out) {
      var t = out[id];
      if (t.agent_id && _boardFilterAgents.indexOf(t.agent_id) >= 0) {
        filtered[id] = t;
      }
    }
    out = filtered;
  }
  // Health filter (OR — task matches if its health state is selected)
  if (_boardFilterHealth.length) {
    var filtered = {};
    for (var id in out) {
      var t = out[id];
      if (_boardFilterHealth.indexOf(_boardTaskHealthState(t)) >= 0) {
        filtered[id] = t;
      }
    }
    out = filtered;
  }
  return out;
}

function _boardTasksInLaneFromMap(lane, tasks) {
  var arr = [];
  for (var id in (tasks || {})) {
    if (tasks[id].lane === lane) arr.push(tasks[id]);
  }
  var sortMode = _boardLaneSortMode(lane);
  if (lane === 'Done' && (sortMode === 'manual' || sortMode === 'newest')) {
    var doneSortKeys = {};
    function doneSortKey(task) {
      var key = (task && task.id) || '';
      if (doneSortKeys[key]) return doneSortKeys[key];
      var time = _boardTimestamp(task.done_at || task.lane_entered_at || task.updated_at || task.created_at);
      doneSortKeys[key] = { time: time, valid: !Number.isNaN(time) };
      return doneSortKeys[key];
    }
    arr.sort(function(a, b) {
      var ak = doneSortKey(a);
      var bk = doneSortKey(b);
      if (ak.valid && bk.valid && ak.time !== bk.time) return bk.time - ak.time;
      if (ak.valid !== bk.valid) return ak.valid ? -1 : 1;
      return _boardRecentlyTouchedCompare(a, b);
    });
    return arr;
  }
  arr.sort(function(a, b) { return _boardCompareLaneTasks(a, b, lane); });
  return arr;
}

function _boardTasksInLane(lane, model) {
  if (model && model.laneTasks) {
    return model.laneTasks[lane] || [];
  }
  return _boardTasksInLaneFromMap(lane, _boardVisibleTasks());
}

function _boardLaneCount(lane, model) {
  if (model && model.laneCounts) return model.laneCounts[lane] || 0;
  var tasks = _boardVisibleTasks();
  var n = 0;
  for (var id in tasks) {
    var t = tasks[id];
    if (t.lane !== lane) continue;
    if (taskIsEngineerMessageFollowup(t)) continue;
    // Only count root tasks (same filter as lane body)
    if (t.parent_task_id && tasks[t.parent_task_id]) continue;
    n++;
  }
  return n;
}

function _boardHasActiveFilters() {
  return _boardSearchQuery !== ''
    || _boardQuickView !== ''
    || _boardFilterLabels.length > 0
    || _boardFilterActions.length > 0
    || _boardFilterAgents.length > 0
    || _boardFilterHealth.length > 0;
}

function _boardLanePoolTasksFromAll(lane) {
  var all = _boardTasks();
  var pool = [];
  var group = _boardFilterByGroup ? _currentGroup() : '';
  for (var id in all) {
    var task = all[id];
    if (task.lane !== lane) continue;
    if (group && task.group !== group) continue;
    pool.push(task);
  }
  return pool;
}

function _boardLanePoolTasks(lane, model) {
  if (model && model.lanePoolTasks) return model.lanePoolTasks[lane] || [];
  return _boardLanePoolTasksFromAll(lane);
}

function _renderBoardMessageState(state, noteOnly) {
  var cls = 'board-empty ui-state ui-state--empty';
  if (noteOnly) cls += ' board-empty-note ui-state--note ui-state--compact';
  var html = '<div class="' + cls + '">';
  html += '<div class="board-empty-title ui-state__title">' + esc(state.title) + '</div>';
  if (state.body) {
    html += '<div class="board-empty-body ui-state__message">' + esc(state.body) + '</div>';
  }
  if (state.actions && state.actions.length) {
    html += '<div class="board-empty-actions ui-state__actions">';
    for (var i = 0; i < state.actions.length; i++) {
      html += '<button class="board-empty-action btn-secondary btn-sm" onclick="' + state.actions[i].onclick + '">'
        + esc(state.actions[i].label) + '</button>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _boardEmptyStateForLane(lane, laneTasks, rootTasks, filtersActive) {
  var escLane = esc(lane).replace(/'/g, "\\'");
  if (filtersActive) {
    return {
      title: 'No matching tasks',
      body: 'The current search or filters hide everything in ' + lane + '. Clear filters or broaden the query.',
      actions: [{ label: 'Clear Filters', onclick: 'boardClearFilters()' }],
    };
  }
  if (lane === 'Backlog') {
    var blocked = 0;
    var scheduled = 0;
    for (var i = 0; i < laneTasks.length; i++) {
      if (_boardTaskIsFutureScheduled(laneTasks[i])) scheduled += 1;
      else if (_boardTaskIsDispatchBlocked(laneTasks[i])) blocked += 1;
    }
    if (laneTasks.length && blocked === laneTasks.length) {
      return {
        title: 'Everything in Backlog is blocked',
        body: blocked + ' task' + (blocked === 1 ? ' is' : 's are')
          + ' waiting on dependencies or blockers. Resolve prerequisites to make work dispatchable.',
        actions: !_boardAddingTask
          ? [{ label: '+ Task', onclick: 'boardStartAddTaskForLane(\'' + escLane + '\')' }]
          : [],
      };
    }
    if (laneTasks.length && blocked + scheduled === laneTasks.length) {
      return {
        title: 'Nothing is ready to dispatch',
        body: scheduled
          ? scheduled + ' task' + (scheduled === 1 ? ' is' : 's are')
            + ' scheduled for later. Wait for the dispatch window or add a ready task.'
          : 'Backlog tasks need attention before they can be dispatched.',
        actions: !_boardAddingTask
          ? [{ label: '+ Task', onclick: 'boardStartAddTaskForLane(\'' + escLane + '\')' }]
          : [],
      };
    }
  }
  if (laneTasks.length && rootTasks.length === 0) {
    return {
      title: 'No standalone tasks to show',
      body: 'The matching tasks in ' + lane + ' are nested under work shown elsewhere. Open the parent chain or adjust filters.',
      actions: filtersActive
        ? [{ label: 'Clear Filters', onclick: 'boardClearFilters()' }]
        : [],
    };
  }
  var actions = [];
  if (!_boardAddingTask && lane !== 'Done') {
    actions.push({ label: '+ Task', onclick: 'boardStartAddTaskForLane(\'' + escLane + '\')' });
  }
  return {
    title: 'No tasks in ' + lane,
    body: lane === 'Done'
      ? 'Nothing has landed here yet. Complete work in another lane to fill this view.'
      : 'Add a task here or move existing work into this lane.',
    actions: actions,
  };
}

function _boardBacklogDispatchNote(rootTasks, lane) {
  if ((lane || _boardSelectedLane) !== 'Backlog' || !rootTasks.length) return null;
  var blocked = 0;
  var scheduled = 0;
  var ready = 0;
  for (var i = 0; i < rootTasks.length; i++) {
    if (_boardTaskIsFutureScheduled(rootTasks[i])) scheduled += 1;
    else if (_boardTaskIsDispatchBlocked(rootTasks[i])) blocked += 1;
    else ready += 1;
  }
  if (ready > 0) return null;
  if (blocked && !scheduled) {
    return {
      title: 'Everything in Backlog is blocked',
      body: blocked + ' task' + (blocked === 1 ? ' is' : 's are')
        + ' waiting on dependencies or blockers. Resolve prerequisites to dispatch work.',
      actions: [],
    };
  }
  return {
    title: 'Nothing is ready to dispatch',
    body: scheduled
      ? scheduled + ' task' + (scheduled === 1 ? ' is' : 's are')
        + ' scheduled for later, and the rest are blocked. Wait for schedule windows or clear blockers.'
      : 'Backlog tasks need attention before they can be dispatched.',
    actions: [],
  };
}

function _boardLabelCountsFromTasks(pool) {
  var counts = {};
  for (var id in (pool || {})) {
    var t = pool[id];
    if (t.labels) {
      for (var i = 0; i < t.labels.length; i++) {
        counts[t.labels[i]] = (counts[t.labels[i]] || 0) + 1;
      }
    }
  }
  return counts;
}

function _boardActionCountsFromTasks(pool) {
  var counts = {};
  for (var id in (pool || {})) {
    var t = pool[id];
    if (t.action_name) {
      counts[t.action_name] = (counts[t.action_name] || 0) + 1;
    }
  }
  return counts;
}

function _boardAgentCountsFromTasks(pool) {
  var counts = {};
  for (var id in (pool || {})) {
    var t = pool[id];
    if (t.agent_id) {
      counts[t.agent_id] = (counts[t.agent_id] || 0) + 1;
    }
  }
  return counts;
}

function _boardAgentIsTombstoned(agent) {
  if (!agent) return false;
  if (typeof _isTombstonedAgent === 'function') return _isTombstonedAgent(agent);
  var value = Number(agent.deleted_at || 0);
  return Number.isFinite(value) && value > 0;
}

function _boardAgentDismissedAt(agent) {
  if (!agent) return 0;
  var value = Number(agent.dismissed_at || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function _boardAgentIsLive(agent) {
  return !!(agent
    && agent.cell_type === 'agent'
    && !_boardAgentIsTombstoned(agent)
    && !_boardAgentDismissedAt(agent));
}

function _boardHealthCountsFromTasks(pool) {
  var counts = {};
  for (var hi = 0; hi < _boardHealthOrder.length; hi++) {
    counts[_boardHealthOrder[hi]] = 0;
  }
  for (var id in (pool || {})) {
    var stateName = _boardTaskHealthState(pool[id]);
    if (stateName !== 'healthy') {
      counts[stateName] = (counts[stateName] || 0) + 1;
    }
  }
  for (var ci = _boardHealthOrder.length - 1; ci >= 0; ci--) {
    var key = _boardHealthOrder[ci];
    if (!counts[key]) delete counts[key];
  }
  return counts;
}

/** Collect all labels with counts (before search/label/action filters). */

function _boardAllLabelCounts(model) {
  if (model && model.labelCounts) return model.labelCounts;
  return _boardLabelCountsFromTasks(_boardScopedTasks(false));
}

/** Collect all action names with counts from tasks. */

function _boardAllActionCounts(model) {
  if (model && model.actionCounts) return model.actionCounts;
  return _boardActionCountsFromTasks(_boardScopedTasks(_boardShowArchived));
}

/** Collect all agent IDs with counts from tasks. */

function _boardAllAgentCounts(model) {
  if (model && model.agentCounts) return model.agentCounts;
  return _boardAgentCountsFromTasks(_boardScopedTasks(_boardShowArchived));
}

/** Collect all unhealthy task health states with counts from tasks. */

function _boardAllHealthCounts(model) {
  if (model && model.healthCounts) return model.healthCounts;
  return _boardHealthCountsFromTasks(_boardScopedTasks(true));
}

function _boardSortedCopy(values) {
  return (values || []).slice().sort();
}

function _boardObjectKeysSorted(obj) {
  var keys = [];
  for (var key in (obj || {})) keys.push(key);
  keys.sort();
  return keys;
}

function _boardLaneSortSignature(lanes) {
  var out = {};
  for (var i = 0; i < (lanes || []).length; i++) {
    out[lanes[i]] = _boardLaneSortMode(lanes[i]);
  }
  return out;
}

function _boardRenderShellKey(lanes, wideShell, wideLayout) {
  return JSON.stringify({
    group: _currentGroup() || '',
    filter_by_group: !!_boardFilterByGroup,
    lanes: (lanes || []).slice(),
    selected_lane: _boardSelectedLane || '',
    show_archived: !!_boardShowArchived,
    show_schedules: !!_boardShowSchedules,
    wide_shell: !!wideShell,
    wide_layout: !!wideLayout,
    hidden_wide_lanes: (typeof _boardHiddenWideLanesSignature === 'function')
      ? _boardHiddenWideLanesSignature()
      : [],
    search_query: _boardSearchQuery || '',
    quick_view: _boardQuickView || '',
    filter_labels: _boardSortedCopy(_boardFilterLabels),
    filter_actions: _boardSortedCopy(_boardFilterActions),
    filter_agents: _boardSortedCopy(_boardFilterAgents),
    filter_health: _boardSortedCopy(_boardFilterHealth),
    lane_sorts: _boardLaneSortSignature(lanes || []),
    card_density: _boardCardDensityMode(),
    saving_view: !!_boardSavingView,
    saving_view_name: _boardSavingViewName || '',
    adding_task: !!_boardAddingTask,
    adding_lane: _boardAddingTaskLane || '',
    view_menu_open: !!_boardViewMenuOpen,
  });
}

function _boardToolbarShapeKey(model, filtersActive) {
  var labelCounts = _boardAllLabelCounts(model);
  var actionCounts = _boardAllActionCounts(model);
  var agentCounts = _boardAllAgentCounts(model);
  var healthCounts = _boardAllHealthCounts(model);
  var archivedCount = _boardArchivedCount(model);
  var savedViews = _boardCurrentGroupSavedViews();
  var groupTaskCount = _boardGroupTaskCount(model);
  var currentViewSavable = !_boardIsDefaultFilterState(_boardCurrentViewState());
  return JSON.stringify({
    has_labels: _boardObjectKeysSorted(labelCounts).length > 0,
    has_actions: _boardObjectKeysSorted(actionCounts).length > 0,
    has_agents: _boardObjectKeysSorted(agentCounts).length > 0,
    has_health: _boardObjectKeysSorted(healthCounts).length > 0,
    has_saved_views: savedViews.length > 0,
    has_quick_views: groupTaskCount > 0 || _boardQuickView !== '',
    current_view_savable: currentViewSavable,
    show_saved_views_row: currentViewSavable || savedViews.length > 0 || _boardSavingView,
    show_toolbar: groupTaskCount > 0
      || _boardObjectKeysSorted(labelCounts).length > 0
      || _boardObjectKeysSorted(actionCounts).length > 0
      || _boardObjectKeysSorted(agentCounts).length > 0
      || _boardObjectKeysSorted(healthCounts).length > 0
      || _boardSearchQuery || _boardFilterLabels.length
      || _boardFilterActions.length || _boardFilterAgents.length
      || _boardFilterHealth.length
      || savedViews.length > 0 || archivedCount > 0 || _boardShowArchived,
    archived_present: archivedCount > 0,
    filters_active: !!filtersActive,
    schedules_present: _boardScheduleCount() > 0,
  });
}

function _boardResetRenderCaches() {
  _boardQueuedTaskDeltas = [];
  _boardQueuedTaskDeltasCanPatch = true;
  _boardLastRenderShellKey = '';
  _boardLastToolbarShapeKey = '';
  _boardLaneRenderCache = {};
}

function _boardQueueTaskDeltas(changes, options) {
  options = options || {};
  if (options.canPatch === false) _boardQueuedTaskDeltasCanPatch = false;
  if (!changes || !changes.length) return;
  for (var i = 0; i < changes.length; i++) {
    if (changes[i]) _boardQueuedTaskDeltas.push(changes[i]);
  }
}

function _boardConsumeQueuedTaskDeltas() {
  if (!_boardQueuedTaskDeltas.length) {
    var emptyOut = { changes: [], canPatch: _boardQueuedTaskDeltasCanPatch };
    _boardQueuedTaskDeltasCanPatch = true;
    return emptyOut;
  }
  var out = {
    changes: _boardQueuedTaskDeltas.slice(),
    canPatch: _boardQueuedTaskDeltasCanPatch,
  };
  _boardQueuedTaskDeltas = [];
  _boardQueuedTaskDeltasCanPatch = true;
  return out;
}

function _boardAddAffectedLane(lanes, lane) {
  if (!lane) return;
  lanes[lane] = true;
}

function _boardAddTaskAffectedLanes(lanes, task) {
  if (!task) return;
  var tasks = _boardTasks();
  var cursor = task;
  var seen = {};
  while (cursor) {
    var cursorId = cursor.id || cursor.parent_task_id || '';
    if (cursorId) {
      if (seen[cursorId]) break;
      seen[cursorId] = true;
    }
    _boardAddAffectedLane(lanes, cursor.lane);
    if (!cursor.parent_task_id) break;
    cursor = tasks[cursor.parent_task_id];
  }
}

function _boardAddDependentAffectedLanes(lanes, taskId) {
  if (!taskId) return;
  var tasks = _boardTasks();
  for (var id in tasks) {
    var task = tasks[id];
    if (!task || !Array.isArray(task.depends_on)) continue;
    if (task.depends_on.indexOf(taskId) >= 0) _boardAddTaskAffectedLanes(lanes, task);
  }
}

function _boardAddDependencyAffectedLanes(lanes, task) {
  if (!task || !Array.isArray(task.depends_on)) return;
  var tasks = _boardTasks();
  for (var i = 0; i < task.depends_on.length; i++) {
    var dep = tasks[task.depends_on[i]];
    if (dep) _boardAddTaskAffectedLanes(lanes, dep);
  }
}

function _boardAffectedLanesFromTaskDeltas(changes) {
  var lanes = {};
  var allVisible = false;
  if (_boardQuickView) allVisible = true;
  for (var i = 0; i < (changes || []).length; i++) {
    var change = changes[i] || {};
    var previous = change.previous || null;
    var next = change.next || null;
    var taskId = change.id || (next && next.id) || (previous && previous.id) || '';
    _boardAddTaskAffectedLanes(lanes, previous);
    _boardAddTaskAffectedLanes(lanes, next);
    _boardAddDependencyAffectedLanes(lanes, previous);
    _boardAddDependencyAffectedLanes(lanes, next);
    _boardAddDependentAffectedLanes(lanes, taskId);
  }
  if (allVisible) return _boardVisibleLanes();
  var visible = {};
  var visibleLanes = _boardVisibleLanes();
  for (var vi = 0; vi < visibleLanes.length; vi++) visible[visibleLanes[vi]] = true;
  return _boardObjectKeysSorted(lanes).filter(function(lane) {
    return !!visible[lane];
  });
}

function _boardTaskRenderSignature(task, model) {
  if (!task) return '';
  return JSON.stringify({
    task: task,
    dependencies: _boardTaskDependencySignature(task, model),
    active_dependents: _boardTaskActiveDependentsSignature(task, model),
    focused: _boardFocusedTask === task.id,
    selected: !!_boardSelectedTasks[task.id],
    hovered: _boardHoveredTask === task.id,
    quick_task: _boardQuickEditTask === task.id ? _boardQuickEditKind : '',
    quick_label_draft: _boardQuickEditTask === task.id ? _boardQuickLabelDraft : '',
    quick_due_draft: _boardQuickEditTask === task.id ? _boardQuickDueDraft : '',
    collapsed: !!_boardCollapsedTasks[task.id],
  });
}

function _boardDependencyRenderIndexes(model) {
  if (model && model.dependencyRenderIndexes) return model.dependencyRenderIndexes;
  var tasks = _boardTasks();
  var activeDependentsByTask = {};
  for (var id in tasks) {
    var candidate = tasks[id];
    if (!candidate || candidate.lane === 'Done' || !Array.isArray(candidate.depends_on)) continue;
    for (var i = 0; i < candidate.depends_on.length; i++) {
      var depId = candidate.depends_on[i];
      if (!activeDependentsByTask[depId]) activeDependentsByTask[depId] = [];
      activeDependentsByTask[depId].push({
        id: candidate.id || id,
        task: candidate.task || '',
        lane: candidate.lane || '',
        position: candidate.position || 0,
      });
    }
  }
  for (var depKey in activeDependentsByTask) {
    activeDependentsByTask[depKey].sort(_boardCompareDependencySignatureRefs);
  }
  var indexes = { tasks: tasks, activeDependentsByTask: activeDependentsByTask };
  if (model) model.dependencyRenderIndexes = indexes;
  return indexes;
}

function _boardCompareDependencySignatureRefs(a, b) {
  return String(a.lane || '').localeCompare(String(b.lane || ''))
    || ((a.position || 0) - (b.position || 0))
    || String(a.id || '').localeCompare(String(b.id || ''));
}

function _boardTaskDependencySignature(task, model) {
  if (!task || !Array.isArray(task.depends_on) || !task.depends_on.length) return [];
  var indexes = _boardDependencyRenderIndexes(model);
  var tasks = indexes.tasks || {};
  var refs = [];
  for (var i = 0; i < task.depends_on.length; i++) {
    var dep = tasks[task.depends_on[i]];
    if (!dep) {
      refs.push({ id: task.depends_on[i], missing: true });
      continue;
    }
    refs.push({
      id: dep.id || task.depends_on[i],
      task: dep.task || '',
      lane: dep.lane || '',
      position: dep.position || 0,
    });
  }
  refs.sort(_boardCompareDependencySignatureRefs);
  return refs;
}

function _boardTaskActiveDependentsSignature(task, model) {
  if (!task || !task.id || task.lane === 'Done') return [];
  var indexes = _boardDependencyRenderIndexes(model);
  return (indexes.activeDependentsByTask[task.id] || []).slice();
}

function _boardCollectRenderedLaneTasks(rootTasks, childrenOf, renderLimit, model) {
  var out = [];
  var remaining = Math.max(0, renderLimit || 0);
  function visit(task, depth) {
    if (!task || remaining <= 0) return;
    remaining--;
    out.push({
      id: task.id || '',
      depth: depth || 0,
      signature: _boardTaskRenderSignature(task, model),
    });
    var children = (childrenOf && childrenOf[task.id]) || [];
    if (!children.length || _boardCollapsedTasks[task.id]) return;
    for (var i = 0; i < children.length; i++) {
      visit(children[i], (depth || 0) + 1);
      if (remaining <= 0) break;
    }
  }
  for (var i = 0; i < (rootTasks || []).length; i++) {
    visit(rootTasks[i], 0);
    if (remaining <= 0) break;
  }
  return out;
}

function _boardDefaultRenderLimitForLane(lane) {
  return lane === 'Done' ? _boardDoneInitialRenderLimit : _boardDefaultRenderLimit;
}

function _boardRenderLimitValue(lane) {
  if (lane === 'Done') {
    return Math.max(0, _boardDoneRenderLimit || _boardDoneInitialRenderLimit);
  }
  return Math.max(0, _boardRenderLimit || _boardDefaultRenderLimit);
}

function _boardSetRenderLimitForLane(lane, value) {
  var next = Math.max(0, value || _boardDefaultRenderLimitForLane(lane));
  if (lane === 'Done') _boardDoneRenderLimit = next;
  else _boardRenderLimit = next;
}

function _boardResetRenderLimits() {
  _boardRenderLimit = _boardDefaultRenderLimit;
  _boardDoneRenderLimit = _boardDoneInitialRenderLimit;
}

function _boardLoadMoreBatchForLane(lane) {
  return lane === 'Done' ? _boardDoneRenderBatch : _boardDefaultRenderLimit;
}

function _boardLanePoolSignature(lane, model) {
  var pool = _boardLanePoolTasks(lane, model);
  var parts = [];
  for (var i = 0; i < pool.length; i++) {
    var task = pool[i];
    parts.push([
      task.id || '',
      task.lane || '',
      task.group || '',
      task.parent_task_id || '',
      task.scheduled_at || '',
      _boardTaskHealthState(task),
      (task.labels || []).join(','),
      JSON.stringify(_boardTaskDependencySignature(task, model)),
      JSON.stringify(_boardTaskActiveDependentsSignature(task, model)),
    ].join(':'));
  }
  parts.sort();
  return parts.join('|');
}

function _boardLaneRenderContextKey(lane, model, filtersActive, skipAddTask, wideColumn) {
  var rootTasks = _boardRootTasksForLane(lane, model ? model.visibleTasks : null, model);
  var childrenOf = (model && model.childrenOf) || {};
  var renderLimit = _boardRenderLimitValue(lane);
  var totalCards = _boardRenderableCardCountForRoots(rootTasks, childrenOf);
  var rendered = _boardCollectRenderedLaneTasks(rootTasks, childrenOf, renderLimit, model);
  var staleDoneIds = lane === 'Done' ? _boardStaleDoneTaskIds(model) : [];
  return JSON.stringify({
    lane: lane || '',
    active: lane === _boardSelectedLane,
    wide: !!wideColumn,
    skip_add: !!skipAddTask,
    filters_active: !!filtersActive,
    render_limit: renderLimit,
    lane_count: _boardLaneCount(lane, model),
    root_count: rootTasks.length,
    root_ids: rootTasks.map(function(task) { return task.id || ''; }),
    total_cards: totalCards,
    rendered: rendered,
    pool: (rootTasks.length === 0 || lane === 'Backlog') ? _boardLanePoolSignature(lane, model) : '',
    stale_done_ids: staleDoneIds,
    adding_task: !!_boardAddingTask,
    adding_lane: _boardAddingTaskLane || '',
    adding_draft: _boardAddingTaskDraft || '',
    adding_agent: _boardAddingTaskAgent || '',
  });
}

function _boardLaneCacheKeyName(lane, wideColumn) {
  return (wideColumn ? 'wide:' : 'narrow:') + (lane || '');
}

function _boardRememberLaneRender(lane, model, filtersActive, skipAddTask, wideColumn, section) {
  if (!lane || !section) return;
  var cacheName = _boardLaneCacheKeyName(lane, wideColumn);
  _boardLaneRenderCache[cacheName] = {
    key: _boardLaneRenderContextKey(lane, model, filtersActive, skipAddTask, wideColumn),
    html: section.html || '',
    bodyHtml: section.bodyHtml || section.html || '',
    laneCount: _boardLaneCount(lane, model),
    renderedCards: section.renderedCards || 0,
    totalCards: section.totalCards || 0,
    rootTasks: section.rootTasks || [],
    renderLimit: section.renderLimit || _boardRenderLimitValue(lane),
  };
}

function _boardCachedLaneRender(lane, model, filtersActive, skipAddTask, wideColumn) {
  var cacheName = _boardLaneCacheKeyName(lane, wideColumn);
  var cached = _boardLaneRenderCache[cacheName];
  if (!cached) return null;
  var nextKey = _boardLaneRenderContextKey(lane, model, filtersActive, skipAddTask, wideColumn);
  return cached.key === nextKey ? cached : null;
}

function _boardCssEscape(value) {
  if (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function') {
    return CSS.escape(String(value));
  }
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function _boardLaneAttrSelector(className, lane) {
  return '.' + className + '[data-lane="' + _boardCssEscape(lane) + '"]';
}

function _boardFindWideLaneBody(panel, lane) {
  if (!panel || typeof panel.querySelector !== 'function') return null;
  return panel.querySelector(_boardLaneAttrSelector('board-wide-lane-body', lane));
}

function _boardPatchLaneCountDom(panel, lane, count) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var escaped = _boardCssEscape(lane);
  var selectors = [
    '.board-lane-tab[data-lane="' + escaped + '"] .lane-count',
    '.board-wide-lane[data-lane="' + escaped + '"] .board-wide-lane-count',
  ];
  for (var i = 0; i < selectors.length; i++) {
    var el = panel.querySelector(selectors[i]);
    if (el) el.textContent = String(count);
  }
}

function _boardPatchAllLaneCounts(panel, lanes, model) {
  for (var i = 0; i < (lanes || []).length; i++) {
    _boardPatchLaneCountDom(panel, lanes[i], _boardLaneCount(lanes[i], model));
  }
}

function _boardPatchWideLaneBody(panel, lane, model, filtersActive) {
  if (typeof _boardIsWideLaneCollapsed === 'function' && _boardIsWideLaneCollapsed(lane)) {
    _boardPatchLaneCountDom(panel, lane, _boardLaneCount(lane, model));
    return {
      patched: false,
      rootTasks: [],
      renderLimit: 0,
    };
  }
  var cached = _boardCachedLaneRender(lane, model, filtersActive, true, true);
  if (cached) {
    _boardPatchLaneCountDom(panel, lane, cached.laneCount);
    return {
      patched: false,
      rootTasks: cached.rootTasks || [],
      renderLimit: cached.renderLimit || _boardRenderLimitValue(lane),
    };
  }
  var body = _boardFindWideLaneBody(panel, lane);
  if (!body) return null;
  var section = _boardRenderWideLaneColumn(lane, model, filtersActive);
  var savedScrollTop = typeof body.scrollTop === 'number' ? body.scrollTop : null;
  body.innerHTML = section.bodyHtml || '';
  if (typeof savedScrollTop === 'number') {
    body.scrollTop = savedScrollTop;
    _boardWideLaneScrollTops[lane] = savedScrollTop;
  }
  _boardBindWideLaneBodyScroll(body);
  _boardPatchLaneCountDom(panel, lane, _boardLaneCount(lane, model));
  _boardRememberLaneRender(lane, model, filtersActive, true, true, section);
  return {
    patched: true,
    rootTasks: section.rootTasks || [],
    renderLimit: section.renderLimit || _boardRenderLimitValue(lane),
  };
}

function _boardPatchNarrowLaneBody(panel, lane, model, filtersActive) {
  var cardsEl = document.getElementById('board-cards');
  if (!cardsEl) return null;
  var cached = _boardCachedLaneRender(lane, model, filtersActive, false, false);
  if (cached) {
    _boardPatchLaneCountDom(panel, lane, cached.laneCount);
    return {
      patched: false,
      rootTasks: cached.rootTasks || [],
      renderLimit: cached.renderLimit || _boardRenderLimitValue(lane),
    };
  }
  var section = _boardRenderLaneSection(lane, model, filtersActive, false);
  cardsEl.innerHTML = section.html || '';
  cardsEl.scrollTop = _boardCardsScrollTop;
  _boardPatchLaneCountDom(panel, lane, _boardLaneCount(lane, model));
  _boardRememberLaneRender(lane, model, filtersActive, false, false, section);
  return {
    patched: true,
    rootTasks: section.rootTasks || [],
    renderLimit: section.renderLimit || _boardRenderLimitValue(lane),
  };
}

function _boardCanTryTaskDeltaPatch(deltaBatch, shellKey) {
  if (!deltaBatch || !deltaBatch.changes || !deltaBatch.changes.length) return false;
  if (!deltaBatch.canPatch) return false;
  if (!_boardLastRenderShellKey || _boardLastRenderShellKey !== shellKey) return false;
  if (_boardShowSchedules || _boardViewMenuOpen || _boardFilterDropdownType) return false;
  if (_boardSelectedCount && _boardSelectedCount() > 0) return false;
  return true;
}

function _boardTryPatchTaskDeltas(panel, deltaBatch, lanes, filtersActive, wideLayout, shellKey, restoreState, quickEditRefocusTask, quickEditRefocusKind) {
  if (!_boardCanTryTaskDeltaPatch(deltaBatch, shellKey)) return false;
  var affectedLanes = _boardAffectedLanesFromTaskDeltas(deltaBatch.changes);
  var visibleLaneSet = {};
  for (var vi = 0; vi < lanes.length; vi++) visibleLaneSet[lanes[vi]] = true;
  affectedLanes = affectedLanes.filter(function(lane) { return !!visibleLaneSet[lane]; });
  var requestedLanes = wideLayout
    ? affectedLanes.slice()
    : (affectedLanes.indexOf(_boardSelectedLane) >= 0 ? [_boardSelectedLane] : []);
  var model = _boardBuildRenderModel(requestedLanes);
  var toolbarKey = _boardToolbarShapeKey(model, filtersActive);
  if (_boardLastToolbarShapeKey && toolbarKey !== _boardLastToolbarShapeKey) return false;
  if (filtersActive && _boardLaneCount(_boardSelectedLane, model) === 0) return false;

  _boardEnsureDispatchEligibilityRefs(_currentGroup(), model);
  _boardPatchAllLaneCounts(panel, lanes, model);

  var childrenOf = model.childrenOf;
  var nextLaneEntryDelay = 0;
  if (wideLayout) {
    var wideResultsByLane = {};
    for (var i = 0; i < requestedLanes.length; i++) {
      var wideResult = _boardPatchWideLaneBody(panel, requestedLanes[i], model, filtersActive);
      if (!wideResult) return false;
      wideResultsByLane[requestedLanes[i]] = wideResult;
    }
    for (var wi = 0; wi < lanes.length; wi++) {
      var refreshLane = lanes[wi];
      var refreshEntry = wideResultsByLane[refreshLane]
        || _boardLaneRenderCache[_boardLaneCacheKeyName(refreshLane, true)];
      if (!refreshEntry) continue;
      var wideDelay = _boardVisibleLaneEntryRefreshDelay(
        refreshEntry.rootTasks || [],
        childrenOf,
        refreshEntry.renderLimit || _boardRenderLimitValue(refreshLane),
      );
      if (wideDelay > 0 && (!nextLaneEntryDelay || wideDelay < nextLaneEntryDelay)) {
        nextLaneEntryDelay = wideDelay;
      }
    }
  } else if (requestedLanes.length) {
    var narrowResult = _boardPatchNarrowLaneBody(panel, requestedLanes[0], model, filtersActive);
    if (!narrowResult) return false;
    nextLaneEntryDelay = _boardVisibleLaneEntryRefreshDelay(
      narrowResult.rootTasks,
      childrenOf,
      narrowResult.renderLimit,
    );
  }

  _boardScheduleLaneEntryRefresh(nextLaneEntryDelay);
  _boardLastToolbarShapeKey = toolbarKey;
  if (restoreState) _restoreSurfaceState(panel, restoreState);
  if (quickEditRefocusTask) {
    _boardRefocusQuickEditInput(quickEditRefocusTask, quickEditRefocusKind);
  }
  if (_boardAddingTask) {
    var addTaskInput = document.getElementById('board-add-task-input');
    if (addTaskInput) boardAddTaskAutoResize(addTaskInput);
  }
  _boardAfterRenderLayout();
  return true;
}

function _boardBuildRenderModel(lanes) {
  _boardSyncFiltersForCurrentGroup();
  var visibleLanes = _boardVisibleLanes();
  lanes = lanes || visibleLanes;
  var requestedLaneSet = {};
  for (var ri = 0; ri < lanes.length; ri++) requestedLaneSet[lanes[ri]] = true;
  var visibleLaneSet = {};
  for (var vi = 0; vi < visibleLanes.length; vi++) visibleLaneSet[visibleLanes[vi]] = true;

  var scopedWithArchived = _boardScopedTasks(true);
  var scopedWithoutArchived = _boardFilterArchivedTasks(scopedWithArchived);
  var scopedTasks = _boardShowArchived
    ? _boardCopyTaskMap(scopedWithArchived)
    : scopedWithoutArchived;
  var visibleTasks = _boardVisibleTasksFromScoped(scopedTasks);
  var childrenOf = _boardChildrenOfVisibleTasks(visibleTasks);
  var model = {
    scopedTasks: scopedTasks,
    scopedWithArchived: scopedWithArchived,
    scopedWithoutArchived: scopedWithoutArchived,
    visibleTasks: visibleTasks,
    childrenOf: childrenOf,
    laneTasks: {},
    rootTasksByLane: {},
    laneCounts: {},
    lanePoolTasks: {},
    labelCounts: _boardLabelCountsFromTasks(scopedWithoutArchived),
    actionCounts: _boardActionCountsFromTasks(scopedTasks),
    agentCounts: _boardAgentCountsFromTasks(scopedTasks),
    healthCounts: _boardHealthCountsFromTasks(scopedWithArchived),
    archivedCount: 0,
    groupTaskCount: 0,
  };

  for (var lci = 0; lci < visibleLanes.length; lci++) {
    model.laneCounts[visibleLanes[lci]] = 0;
  }

  for (var archivedId in scopedWithArchived) {
    if (_boardIsArchived(scopedWithArchived[archivedId])) model.archivedCount++;
  }
  for (var groupTaskId in scopedWithoutArchived) model.groupTaskCount++;

  for (var visibleTaskId in visibleTasks) {
    var visibleTask = visibleTasks[visibleTaskId];
    if (!visibleLaneSet[visibleTask.lane]) continue;
    if (taskIsEngineerMessageFollowup(visibleTask)) continue;
    if (visibleTask.parent_task_id && visibleTasks[visibleTask.parent_task_id]) continue;
    model.laneCounts[visibleTask.lane] = (model.laneCounts[visibleTask.lane] || 0) + 1;
  }

  for (var i = 0; i < lanes.length; i++) {
    var lane = lanes[i];
    if (!requestedLaneSet[lane]) continue;
    var laneTasks = _boardTasksInLaneFromMap(lane, visibleTasks);
    var rootTasks = laneTasks.filter(function(task) {
      return !task.parent_task_id || !visibleTasks[task.parent_task_id];
    });
    model.laneTasks[lane] = laneTasks;
    model.rootTasksByLane[lane] = rootTasks;
    model.lanePoolTasks[lane] = _boardLanePoolTasksFromAll(lane);
  }

  return model;
}

function _boardGroupTaskCount(model) {
  if (model && typeof model.groupTaskCount === 'number') return model.groupTaskCount;
  return Object.keys(_boardScopedTasks(false)).length;
}

/* ------------------------------------------------------------------ */
/* Board panel app — Kanban board with lane tabs and task cards         */
/* ------------------------------------------------------------------ */

// Client-side state
var _boardSelectedLane = '';
var _boardFocusedTask = '';
var _boardAddingTask = false;   // true when inline task input is shown
var _boardAddingTaskDraft = '';  // preserved text across blur/reopen
var _boardAddingTaskAction = '';  // selected action name for inline add
var _boardAddingTaskAgent = '';   // selected agent ID for inline add
var _boardAddingTaskLane = '';    // selected lane for inline add
var _boardInlineDraftId = '';     // pre-generated task ID for inline attachments
var _boardInlineAttachments = []; // attachments uploaded during inline creation
var _boardAddTaskFocus = false;   // true only on explicit open, not re-renders
var _boardActDropdownWaiting = false;  // waiting for action list for dropdown
var _boardActList = null;              // fetched actions shown inline (null = hidden)
var _boardScrollLeft = 0;      // preserve scroll across re-renders
var _boardCardsScrollTop = 0;  // preserve cards scroll across re-renders
var _boardWideLaneScrollTops = {}; // preserve per-lane body scroll in wide layout
var _boardViewStates = {};     // keyed lane/filter/schedules scroll + render state
var _boardActiveViewKey = '';
var _boardNextViewDefault = null;
var _boardSkipViewCaptureOnce = false;
var _boardDragId = '';          // card being dragged
var _boardHoveredTask = '';     // task_id currently hovered when DOM rerenders

var _boardCollapsedTasks = {};  // task_id → true if collapsed
var _boardFilterByGroup = true;  // When true, board shows only tasks from the current group
var _boardSearchQuery = '';      // text search filter
var _boardQuickView = '';        // '' | 'recent' | 'touched'
var _boardFilterLabels = [];     // active label filters (OR logic)
var _boardFilterActions = [];    // active action name filters (OR logic)
var _boardSearchTimer = null;    // debounce timer for search input
var _boardFilterAgents = [];    // active agent ID filters (OR logic)
var _boardFilterHealth = [];    // active health filters (OR logic)
var _boardFilterDropdownType = null;   // 'label' | 'action' | 'agent' | 'health' | null
var _boardFilterDropdownCleanup = null;
var _boardViewMenuCleanup = null;
var _boardViewMenuOpen = false;
var _boardPreFilterLane = '';    // saved lane before search, restored on clear
var _boardFiltersByGroup = null; // persisted filter state keyed by group
var _boardSavedViewsByGroup = null; // saved view snapshots keyed by group
var _boardLaneSortsByGroup = null; // persisted lane sort modes keyed by group
var _boardCardDensityByGroup = null; // persisted card density keyed by group
var _boardFilterStateGroup = '';
var _boardShowSchedules = false; // true when "Schedules" tab is active
var _boardShowArchived = false;  // include archived tasks in the active board view
var _boardSavingView = false;    // inline saved-view naming control visibility
var _boardSavingViewName = '';   // draft name for inline saved-view creation
var _boardSaveViewFocus = false; // focus the inline saved-view input after render
var _boardRevealFocusOnRender = false; // scroll the focused card into view after navigation
var _boardRenderLimit = 50;      // virtual scroll: render this many root tasks initially
var _boardSelectedTasks = {};    // task_id → true for multi-select
var _boardLastSelectedTask = ''; // last clicked task for shift-range select
var _boardQuickEditTask = '';    // task_id with open inline quick editor
var _boardQuickEditKind = '';    // 'labels' | 'assignee' | 'due' | 'priority' | ''
var _boardQuickLabelDraft = '';  // pending label text for inline quick edit
var _boardQuickDueDraft = '';    // pending datetime-local value for inline quick edit
var _boardQuickEditRefocusTask = ''; // one-shot task_id to refocus after rerender
var _boardQuickEditRefocusKind = ''; // one-shot quick editor kind to refocus after rerender
var _boardBatchEditOpen = false; // selection bar batch edit panel visibility
var _boardBatchEditLabel = '';   // add this label to every selected task
var _boardBatchEditAssignee = '__unchanged__'; // selected assignee value
var _boardBatchEditDueMode = 'unchanged'; // unchanged | set | clear
var _boardBatchEditDue = '';     // datetime-local value for batch due edits
var _boardBatchEditAction = '__unchanged__'; // selected action value
var _boardBatchEditPriority = '__unchanged__'; // selected priority value
var _boardBatchActionWaiting = false; // waiting for batch action list
var _boardBatchActionOptions = [];    // actions for batch edit action picker
var _boardArchivedLane = 'Archived';
var _boardArchiveLabel = 'loom:archived'; // legacy compatibility for older state
var _boardArchiveStaleDays = 7;
var _boardLaneEntryRefreshTimer = 0;
var _boardWideModeMinWidth = 960;
var _boardEligibilityActionsByGroup = {};
var _boardEligibilityTemplatesByGroup = {};
var _boardEligibilityActionWaiting = false;
var _boardEligibilityTemplateWaiting = false;
var _boardHealthOrder = ['blocked', 'stale-in-progress', 'stalled', 'thrashing', 'idle-risk'];
var _boardHealthLabels = {
  'blocked': 'Blocked',
  'stale-in-progress': 'Stale in progress',
  'stalled': 'Stalled',
  'thrashing': 'Thrashing',
  'idle-risk': 'Idle risk',
  'healthy': 'Healthy',
};
var _boardHealthReasonLabels = {
  'awaiting_human': 'awaiting human',
  'explicit_blocked': 'explicitly blocked',
  'dependency_blocked': 'dependency blocked',
  'agent_waiting': 'agent waiting',
  'message_churn': 'message churn',
  'no_progress_timeout': 'no progress timeout',
  'progress_silence_warning': 'quiet progress window',
  'no_recent_signal': 'no recent signal',
};

/* ---- Helpers -------------------------------------------------------- */

// Extracted helpers live in static/js/board/view-state.js,
// static/js/board/card-rendering.js, and
// static/js/board/card-actions.js.

function _boardSetQuickEditRefocus(taskId, kind) {
  _boardQuickEditRefocusTask = taskId || '';
  _boardQuickEditRefocusKind = kind || '';
}

function _boardRefocusQuickEditInput(taskId, kind) {
  if (!taskId || !kind) return;
  var inputId = '';
  if (kind === 'labels') inputId = 'board-quick-label-input-' + taskId;
  else if (kind === 'due') inputId = 'board-quick-due-input-' + taskId;
  if (!inputId) return;
  var input = document.getElementById(inputId);
  if (!input) return;
  if (kind === 'labels' && 'value' in input) input.value = _boardQuickLabelDraft;
  else if (kind === 'due' && 'value' in input) input.value = _boardQuickDueDraft;
  if (typeof input.focus === 'function') input.focus();
  if ('selectionStart' in input && 'selectionEnd' in input) {
    var end = ('value' in input && typeof input.value === 'string') ? input.value.length : 0;
    input.selectionStart = end;
    input.selectionEnd = end;
  }
}



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
    for (var id in out) {
      var t = out[id];
      var parts = [t.task, t.description, t.id, t.action_name, t.agent_id];
      parts.push(t.verification_mode || '');
      parts.push(t.verification_state || '');
      parts.push(t.verification_notes || '');
      var verificationSummary = t.verification_summary || {};
      parts.push(verificationSummary.tests_run || '');
      parts.push(verificationSummary.human_validation_pending || '');
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
  var cls = 'board-empty';
  if (noteOnly) cls += ' board-empty-note';
  var html = '<div class="' + cls + '">';
  html += '<div class="board-empty-title">' + esc(state.title) + '</div>';
  if (state.body) {
    html += '<div class="board-empty-body">' + esc(state.body) + '</div>';
  }
  if (state.actions && state.actions.length) {
    html += '<div class="board-empty-actions">';
    for (var i = 0; i < state.actions.length; i++) {
      html += '<button class="board-empty-action" onclick="' + state.actions[i].onclick + '">'
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
  return _boardLabelCountsFromTasks(_boardScopedTasks(_boardShowArchived));
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




function _boardBuildRenderModel(lanes) {
  _boardSyncFiltersForCurrentGroup();
  lanes = lanes || _boardVisibleLanes();

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
    labelCounts: _boardLabelCountsFromTasks(scopedTasks),
    actionCounts: _boardActionCountsFromTasks(scopedTasks),
    agentCounts: _boardAgentCountsFromTasks(scopedTasks),
    healthCounts: _boardHealthCountsFromTasks(scopedWithArchived),
    archivedCount: 0,
    groupTaskCount: 0,
  };

  for (var archivedId in scopedWithArchived) {
    if (_boardIsArchived(scopedWithArchived[archivedId])) model.archivedCount++;
  }
  for (var groupTaskId in scopedWithoutArchived) model.groupTaskCount++;

  for (var i = 0; i < lanes.length; i++) {
    var lane = lanes[i];
    var laneTasks = _boardTasksInLaneFromMap(lane, visibleTasks);
    var rootTasks = laneTasks.filter(function(task) {
      return !task.parent_task_id || !visibleTasks[task.parent_task_id];
    });
    model.laneTasks[lane] = laneTasks;
    model.rootTasksByLane[lane] = rootTasks;
    model.laneCounts[lane] = rootTasks.length;
    model.lanePoolTasks[lane] = _boardLanePoolTasksFromAll(lane);
  }

  return model;
}

function _boardGroupTaskCount(model) {
  if (model && typeof model.groupTaskCount === 'number') return model.groupTaskCount;
  return Object.keys(_boardScopedTasks(false)).length;
}

function showTaskMessages(taskId) {
  var t = state.board_tasks[taskId];
  if (!t || !t.messages || !t.messages.length) return;
  var html = '';
  var total = t.messages.length;
  for (var i = total - 1; i >= 0; i--) {
    var m = t.messages[i];
    var seq = '<span class="task-msg-seq">#' + (i + 1) + '</span>';
    var badge = '<span class="task-msg-badge task-msg-' + esc(m.action) + '">' + esc(m.action) + '</span>';
    var time = m.timestamp ? _relativeTime(m.timestamp) : '';
    var absTime = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString() : '';
    var agent = m.agent ? ' <span class="task-msg-agent">' + esc(m.agent) + '</span>' : '';
    html += '<div class="task-msg-row">';
    html += '<div class="task-msg-header">' + seq + badge + agent;
    if (time) html += '<span class="task-msg-time" title="' + esc(absTime) + '">' + esc(time) + '</span>';
    html += '</div>';
    if (m.message) html += '<div class="task-msg-text">' + esc(m.message) + '</div>';
    html += '</div>';
  }
  document.getElementById('task-messages-title').textContent = 'Activity \u2014 ' + (t.task || '').substring(0, 50);
  document.getElementById('task-messages-content').innerHTML = html;
  document.getElementById('modal-task-messages').classList.add('visible');
}

function boardToggleTaskCollapse(taskId) {
  _boardCollapsedTasks[taskId] = !_boardCollapsedTasks[taskId];
  renderBoard();
}

function boardCardMouseEnter(taskId) {
  _boardHoveredTask = taskId || '';
}

function boardCardMouseLeave(taskId) {
  if (_boardHoveredTask === taskId) _boardHoveredTask = '';
}

function _boardRestoreRenderedState() {
  var tabsEl = document.getElementById('board-lane-tabs');
  if (tabsEl) {
    tabsEl.scrollLeft = _boardScrollLeft;
    tabsEl.addEventListener('scroll', function() {
      _boardScrollLeft = tabsEl.scrollLeft;
      boardUpdateScrollArrows();
    });
    boardUpdateScrollArrows();
  }

  var cardsEl = document.getElementById('board-cards');
  if (cardsEl) {
    cardsEl.scrollTop = _boardCardsScrollTop;
    cardsEl.addEventListener('scroll', function() {
      _boardCardsScrollTop = cardsEl.scrollTop;
      _boardSyncActiveViewState(cardsEl);
      // Load more when within 100px of the bottom
      if (cardsEl.scrollTop + cardsEl.clientHeight >= cardsEl.scrollHeight - 100) {
        boardLoadMore();
      }
    });
    // Click on empty space clears selection
    cardsEl.addEventListener('click', function(e) {
      var clickedEmptyWideLane = !!(
        e.target
        && e.target.classList
        && e.target.classList.contains('board-wide-lane-body')
      );
      if ((e.target === cardsEl || clickedEmptyWideLane) && _boardSelectedCount() > 0) {
        boardClearSelection();
      }
    });
  }

  // Wide-layout: each lane column scrolls independently; restore per-lane
  // scrollTop so re-renders don't jump operators back to the top.
  var panelEl = document.getElementById('panel-board');
  if (panelEl && typeof panelEl.querySelectorAll === 'function') {
    var laneBodies = panelEl.querySelectorAll('.board-wide-lane-body[data-lane]');
    for (var li = 0; li < laneBodies.length; li++) {
      _boardBindWideLaneBodyScroll(laneBodies[li]);
    }
  }
}

function _boardBindWideLaneBodyScroll(body) {
  if (!body || !body.dataset) return;
  var lane = body.dataset.lane;
  if (!lane) return;
  var saved = _boardWideLaneScrollTops[lane];
  if (typeof saved === 'number') body.scrollTop = saved;
  body.addEventListener('scroll', function() {
    _boardWideLaneScrollTops[lane] = body.scrollTop;
    if (body.scrollTop + body.clientHeight >= body.scrollHeight - 100) {
      boardLoadMore();
    }
  });
}

function _boardAfterRenderLayout() {
  requestAnimationFrame(function() {
    var tabsEl = document.getElementById('board-lane-tabs');
    if (tabsEl) {
      // Ensure active tab is fully visible
      var activeTab = tabsEl.querySelector('.board-lane-tab.active');
      if (activeTab) {
        var tabLeft = activeTab.offsetLeft;
        var tabRight = tabLeft + activeTab.offsetWidth;
        var viewLeft = tabsEl.scrollLeft;
        var viewRight = viewLeft + tabsEl.clientWidth;
        if (tabLeft < viewLeft) {
          tabsEl.scrollLeft = tabLeft;
        } else if (tabRight > viewRight) {
          tabsEl.scrollLeft = tabRight - tabsEl.clientWidth;
        }
      }

      _boardScrollLeft = tabsEl.scrollLeft;
      boardUpdateScrollArrows();
    }
    if (_boardRevealFocusOnRender) {
      _boardRevealFocusOnRender = false;
      var focusedCard = document.querySelector('.board-card.focused');
      if (focusedCard && typeof focusedCard.scrollIntoView === 'function') {
        focusedCard.scrollIntoView({ block: 'nearest' });
      }
    }
  });
}

function _boardChildrenOfVisibleTasks(allTasks) {
  var childrenOf = {};
  for (var taskId in allTasks) {
    var task = allTasks[taskId];
    if (task.parent_task_id && allTasks[task.parent_task_id]) {
      if (!childrenOf[task.parent_task_id]) childrenOf[task.parent_task_id] = [];
      childrenOf[task.parent_task_id].push(task);
    }
  }
  for (var parentId in childrenOf) {
    childrenOf[parentId].sort(function(a, b) {
      return (a.pipeline_depth - b.pipeline_depth)
        || (a.created_at || '').localeCompare(b.created_at || '');
    });
  }
  return childrenOf;
}

function _boardRootTasksForLane(lane, allTasks, model) {
  if (model && model.rootTasksByLane) return model.rootTasksByLane[lane] || [];
  allTasks = allTasks || _boardVisibleTasks();
  return _boardTasksInLane(lane).filter(function(task) {
    return !task.parent_task_id || !allTasks[task.parent_task_id];
  });
}

function _boardRenderActionListSection() {
  if (_boardActList === null) return '';
  var html = '<div class="board-tpl-list">';
  if (_boardActList.length === 0) {
    html += '<div class="board-tpl-empty">No actions found</div>';
  } else {
    var projectActs = _boardActList.filter(function(t) { return !t.global; });
    var userActs = _boardActList.filter(function(t) { return t.global; });
    if (projectActs.length) {
      html += '<div class="board-tpl-group-label">Project</div>';
      for (var pi = 0; pi < projectActs.length; pi++) {
        html += _boardActItemHtml(projectActs[pi]);
      }
    }
    if (userActs.length) {
      html += '<div class="board-tpl-group-label">User</div>';
      for (var ui = 0; ui < userActs.length; ui++) {
        html += _boardActItemHtml(userActs[ui]);
      }
    }
  }
  html += '<button class="board-tpl-item board-tpl-notemplate" onclick="_boardPickNoAction()">No action</button>';
  html += '</div>';
  return html;
}

function _boardRenderAddTaskSection(lane) {
  var html = '';
  var escLane = esc(lane).replace(/'/g, "\\'");
  var activeLane = lane === _boardSelectedLane;
  if (_boardAddingTask && activeLane) {
    var addTaskLaneOptions = _boardAddTaskLaneOptions();
    if (!_boardAddingTaskLane || addTaskLaneOptions.indexOf(_boardAddingTaskLane) === -1) {
      _boardAddingTaskLane = _boardDefaultAddTaskLane();
    }
    html += '<div class="board-add-task board-add-task-active"'
      + ' ondragover="boardInlineDragOver(event)" ondragleave="boardInlineDragLeave(event)"'
      + ' ondrop="boardInlineDrop(event)">';
    html += '<div style="position:relative">';
    html += '<textarea class="board-add-input" id="board-add-task-input" rows="1"'
      + ' placeholder="Task description..."'
      + ' onkeydown="boardAddTaskKeydown(event)"'
      + ' oninput="boardAddTaskInput(this)"'
      + ' onblur="boardCancelAddTask()">' + esc(_boardAddingTaskDraft) + '</textarea>';
    html += '<div id="board-add-label-dropdown" class="deps-dropdown" style="display:none"></div>';
    html += '</div>';
    if (_boardInlineAttachments.length) {
      html += '<div class="inline-att-chips">';
      for (var ai = 0; ai < _boardInlineAttachments.length; ai++) {
        html += '<span class="inline-att-chip">[Image #' + (ai + 1) + ']'
          + '<button class="inline-att-chip-remove" onmousedown="event.preventDefault();boardInlineRemoveAtt(' + ai + ')">&times;</button>'
          + '</span>';
      }
      html += '</div>';
    }
    html += '<div class="board-add-toolbar">';
    html += '<button class="board-add-toolbar-btn board-add-clear-btn" onmousedown="event.preventDefault();boardClearAddTask()">Clear</button>';
    html += '<div class="board-add-toolbar-right">';
    html += '<div class="board-add-dropdown" id="board-add-agent-wrap">';
    var agentLabel = _boardAddingTaskAgent ? _boardAgentName(_boardAddingTaskAgent) : 'No agent';
    html += '<button class="board-add-toolbar-btn" onmousedown="event.preventDefault();boardToggleAgentDropdown()">'
      + esc(agentLabel) + ' &#9662;</button>';
    html += '</div>';
    var actionLabel = _boardAddingTaskAction || 'No action';
    html += '<button class="board-add-toolbar-btn" onmousedown="event.preventDefault();boardToggleActionList()">'
      + esc(actionLabel) + ' &#9662;</button>';
    html += '<div class="board-add-dropdown" id="board-add-lane-wrap">';
    html += '<button class="board-add-toolbar-btn" onmousedown="event.preventDefault();boardToggleLaneDropdown()">'
      + esc(_boardAddingTaskLane) + ' &#9662;</button>';
    html += '</div>';
    html += '<button class="board-add-toolbar-btn board-add-submit-btn" onmousedown="event.preventDefault();boardSubmitAddTask()">Submit &#10132;</button>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="board-add-task" onclick="boardStartAddTaskForLane(\'' + escLane + '\')">';
  html += '<span>+ Add task</span>';
  html += '<button class="board-add-tpl-btn-idle"'
    + ' onclick="event.stopPropagation();boardStartAddTaskActionForLane(\'' + escLane + '\')">From action &#9662;</button>';
  html += '</div>';
  return html;
}

function _boardRenderWideAddTaskSection() {
  var lane = _boardSelectedLane || _boardDefaultAddTaskLane();
  if (!lane) return '';
  var html = '<div class="board-wide-add-task-wrap">';
  html += _boardRenderAddTaskSection(lane);
  html += _boardRenderActionListSection();
  html += '</div>';
  return html;
}

function _boardRenderLaneCards(rootTasks, childrenOf, renderLimit) {
  var renderState = {
    remaining: Math.max(0, renderLimit || 0),
    rendered: 0,
    limitHit: false,
  };
  var html = '';
  for (var j = 0; j < rootTasks.length; j++) {
    if (renderState.remaining <= 0) {
      renderState.limitHit = true;
      break;
    }
    html += _renderBoardCard(rootTasks[j], childrenOf, 0, renderState);
  }
  return {
    html: html,
    renderedCards: renderState.rendered,
    limitHit: renderState.limitHit,
  };
}

function _boardRenderableCardCount(task, childrenOf, depth) {
  if (!task) return 0;
  var count = 1;
  var children = (childrenOf && childrenOf[task.id]) || [];
  if (!children.length || _boardCollapsedTasks[task.id]) return count;
  for (var i = 0; i < children.length; i++) {
    count += _boardRenderableCardCount(children[i], childrenOf, (depth || 0) + 1);
  }
  return count;
}

function _boardRenderableCardCountForRoots(rootTasks, childrenOf) {
  var count = 0;
  for (var i = 0; i < rootTasks.length; i++) {
    count += _boardRenderableCardCount(rootTasks[i], childrenOf, 0);
  }
  return count;
}

function _boardRenderLimitValue() {
  return Math.max(0, _boardRenderLimit || 50);
}

function _boardRenderLaneSection(lane, model, filtersActive, skipAddTask) {
  var html = '';
  var childrenOf = (model && model.childrenOf) || {};
  var rootTasks = _boardRootTasksForLane(lane, model ? model.visibleTasks : null, model);
  var totalCards = _boardRenderableCardCountForRoots(rootTasks, childrenOf);
  var renderLimit = _boardRenderLimitValue();

  if (!skipAddTask) {
    html += _boardRenderAddTaskSection(lane);
  }
  if (!skipAddTask && lane === _boardSelectedLane) {
    html += _boardRenderActionListSection();
  }

  var archiveSuggestion = _renderBoardArchiveSuggestion(lane, model);
  if (archiveSuggestion) html += archiveSuggestion;

  var backlogDispatchNote = _boardBacklogDispatchNote(rootTasks, lane);
  if (backlogDispatchNote) {
    html += _renderBoardMessageState(backlogDispatchNote, true);
  }

  if (rootTasks.length === 0) {
    html += _renderBoardMessageState(
      _boardEmptyStateForLane(
        lane,
        _boardLanePoolTasks(lane, model),
        rootTasks,
        filtersActive,
      ),
      false,
    );
  }

  var rendered = _boardRenderLaneCards(rootTasks, childrenOf, renderLimit);
  html += rendered.html;
  if (totalCards > rendered.renderedCards) {
    var remaining = totalCards - rendered.renderedCards;
    html += '<div class="board-load-more" onclick="boardLoadMore()">'
      + remaining + ' more card' + (remaining === 1 ? '' : 's') + ' — click or scroll to load</div>';
  }

  return {
    html: html,
    rootTasks: rootTasks,
    renderLimit: renderLimit,
    renderedCards: rendered.renderedCards,
    totalCards: totalCards,
  };
}

function _boardRenderWideLaneColumn(lane, model, filtersActive) {
  var escLane = esc(lane).replace(/'/g, "\\'");
  var laneCount = _boardLaneCount(lane, model);
  var active = lane === _boardSelectedLane;
  var section = _boardRenderLaneSection(lane, model, filtersActive, true);
  var html = '<section class="board-wide-lane' + (active ? ' active' : '') + '"'
    + ' data-lane="' + esc(lane) + '" data-board-lane-column="1">';
  html += '<div class="board-wide-lane-head">';
  html += '<button class="board-wide-lane-select" onclick="boardSelectLane(\'' + escLane + '\')">';
  html += '<span class="board-wide-lane-name">' + esc(lane) + '</span>';
  html += '<span class="board-wide-lane-count">' + laneCount + '</span>';
  if (active) html += '<span class="board-wide-lane-badge">Active</span>';
  html += '</button>';
  if (filtersActive && active) {
    html += '<div class="board-wide-lane-summary">' + esc(_boardFilterSummaryText()) + '</div>';
  }
  html += '</div>';
  html += '<div class="board-wide-lane-body board-lane-drop-target"'
    + ' data-lane="' + esc(lane) + '" data-board-lane-drop="1"'
    + ' ondragover="boardLaneTabDragOver(event)"'
    + ' ondragleave="boardLaneTabDragLeave(event)"'
    + ' ondrop="boardLaneTabDrop(event)">';
  html += section.html;
  html += '</div>';
  html += '</section>';
  section.html = html;
  return section;
}

/* ---- Render --------------------------------------------------------- */

function renderBoard() {
  var panel = document.getElementById('panel-board');
  if (!panel) return;
  if (!_boardPanelVisible()) {
    _boardClearLaneEntryRefresh();
    return;
  }
  var panelState = _captureSurfaceState(panel);
  var quickEditRefocusTask = _boardQuickEditRefocusTask;
  var quickEditRefocusKind = _boardQuickEditRefocusKind;
  var skipRestoreFocus = _boardAddTaskFocus || _boardSaveViewFocus || !!quickEditRefocusTask;
  var restoreState = skipRestoreFocus ? null : panelState;
  if (quickEditRefocusTask && panelState) {
    restoreState = {
      focus: null,
      scrolls: (panelState.scrolls || []).slice(),
    };
  }
  _boardSetQuickEditRefocus('', '');
  _boardSyncFiltersForCurrentGroup();
  _boardHydrateSavedViews();
  _boardHydrateLaneSorts();
  _boardHydrateCardDensity();

  // Preserve scroll + draft before DOM rebuild
  var _cardsEl = document.getElementById('board-cards');
  if (_boardSkipViewCaptureOnce) {
    _boardSkipViewCaptureOnce = false;
  } else {
    if (!_boardActiveViewKey && _cardsEl) {
      _boardActiveViewKey = _boardCurrentViewKey();
    }
    _boardSyncActiveViewState(_cardsEl);
  }
  if (_boardAddingTask) {
    var _inp = document.getElementById('board-add-task-input');
    if (_inp) _boardAddingTaskDraft = _inp.value;
  }

  var lanes = _boardVisibleLanes();
  if (!lanes.length) {
    panel.innerHTML = '<div class="board-empty">No lanes configured</div>';
    return;
  }

  // Default to first lane if selected lane is invalid (skip when schedules tab is active)
  if (!_boardShowSchedules && (!_boardSelectedLane || lanes.indexOf(_boardSelectedLane) === -1)) {
    _boardSelectedLane = lanes[0];
  }

  var html = '';
  var filtersActive = _boardHasActiveFilters();

  // Restore saved lane when filters clear (e.g. user backspaces search to empty)
  if (!filtersActive && _boardPreFilterLane) {
    _boardSelectedLane = _boardPreFilterLane;
    _boardPreFilterLane = '';
  }
  var wideShell = _boardWideShellActive(panel);
  var wideLayout = _boardWideLayoutActive(panel);
  var renderModel = _boardBuildRenderModel(lanes);
  _boardEnsureDispatchEligibilityRefs(_currentGroup(), renderModel);

  // Search & filter toolbar
  var labelCounts = _boardAllLabelCounts(renderModel);
  var actionCounts = _boardAllActionCounts(renderModel);
  var agentCounts = _boardAllAgentCounts(renderModel);
  var healthCounts = _boardAllHealthCounts(renderModel);
  var archivedCount = _boardArchivedCount(renderModel);
  var hasLabels = Object.keys(labelCounts).length > 0;
  var hasActions = Object.keys(actionCounts).length > 0;
  var hasAgents = Object.keys(agentCounts).length > 0;
  var hasHealth = Object.keys(healthCounts).length > 0;
  var savedViews = _boardCurrentGroupSavedViews();
  var hasSavedViews = savedViews.length > 0;
  var hasQuickViews = _boardGroupTaskCount(renderModel) > 0 || _boardQuickView !== '';
  var currentViewSavable = !_boardIsDefaultFilterState(_boardCurrentViewState());
  var schedCount = _boardScheduleCount();
  var showToolbar = _boardGroupTaskCount(renderModel) > 0
    || hasLabels || hasActions || hasAgents || hasHealth
    || _boardSearchQuery || _boardFilterLabels.length
    || _boardFilterActions.length || _boardFilterAgents.length
    || _boardFilterHealth.length
    || hasSavedViews || archivedCount || _boardShowArchived;
  var showSavedViewsRow = currentViewSavable || hasSavedViews || _boardSavingView;
  var showViewMenuButton = !!_boardSelectedLane;
  var recentQuickViewActive = _boardQuickView === 'recent' || _boardQuickView === 'touched';

  html += '<div class="board-search-bar">';
  html += '<div class="board-search-input-wrap">';
  html += '<input type="text" class="board-search-input" id="board-search-input"'
    + ' placeholder="Search tasks..." value="' + esc(_boardSearchQuery) + '"'
    + ' oninput="boardUpdateSearch(this.value)">';
  html += '</div>';
  if (showToolbar) {
    if (hasLabels || _boardFilterLabels.length) {
      var lblCount = _boardFilterLabels.length;
      html += '<div class="board-filter-btn-wrap" id="board-label-filter-wrap">';
      html += '<button class="board-filter-btn' + (lblCount ? ' active' : '') + '"'
        + ' onclick="boardToggleLabelFilter()">'
        + 'Labels' + (lblCount ? ' <span class="board-filter-btn-count">' + lblCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasActions || _boardFilterActions.length) {
      var actFCount = _boardFilterActions.length;
      html += '<div class="board-filter-btn-wrap" id="board-action-filter-wrap">';
      html += '<button class="board-filter-btn' + (actFCount ? ' active' : '') + '"'
        + ' onclick="boardToggleActionFilter()">'
        + 'Actions' + (actFCount ? ' <span class="board-filter-btn-count">' + actFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasQuickViews) {
      html += '<button class="board-filter-btn' + (recentQuickViewActive ? ' active' : '') + '"'
        + ' onclick="boardApplyQuickView(\'recent\')">Recent</button>';
    }
    if (hasAgents || _boardFilterAgents.length) {
      var agtFCount = _boardFilterAgents.length;
      html += '<div class="board-filter-btn-wrap" id="board-agent-filter-wrap">';
      html += '<button class="board-filter-btn' + (agtFCount ? ' active' : '') + '"'
        + ' onclick="boardToggleAgentFilter()">'
        + 'Agents' + (agtFCount ? ' <span class="board-filter-btn-count">' + agtFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasHealth || _boardFilterHealth.length) {
      var healthFCount = _boardFilterHealth.length;
      html += '<div class="board-filter-btn-wrap" id="board-health-filter-wrap">';
      html += '<button class="board-filter-btn' + (healthFCount ? ' active' : '') + '"'
        + ' onclick="boardToggleHealthFilter()">'
        + 'Health' + (healthFCount ? ' <span class="board-filter-btn-count">' + healthFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (filtersActive) {
      html += '<button class="board-filter-clear" onclick="boardClearFilters()">Clear</button>';
    }
  }
  html += '<div class="board-search-spacer"></div>';
  if (showViewMenuButton) {
    html += '<div class="board-filter-btn-wrap" id="board-view-menu-wrap">';
    html += '<button class="board-filter-btn' + (_boardViewMenuOpen ? ' active' : '') + '"'
      + ' onclick="boardToggleViewMenu()">View &#9662;</button>';
    html += '</div>';
  }
  html += '<div class="board-filter-btn-wrap" id="board-schedules-toggle-wrap">';
  html += '<button class="board-filter-btn' + (_boardShowSchedules ? ' active' : '') + '"'
    + ' onclick="boardToggleSchedules()">'
    + 'Schedules'
    + (schedCount ? ' <span class="board-filter-btn-count">' + schedCount + '</span>' : '')
    + '</button>';
  html += '</div>';
  html += '</div>';

  if (showSavedViewsRow) {
    html += '<div class="board-saved-views">';
    if (currentViewSavable || hasSavedViews || _boardSavingView) {
      html += '<span class="board-saved-views-label">Saved</span>';
      if (_boardSavingView) {
        html += '<div class="board-save-view-form">';
        html += '<input type="text" class="board-save-view-input" id="board-save-view-input"'
          + ' placeholder="View name" value="' + esc(_boardSavingViewName) + '"'
          + ' oninput="boardUpdateSaveViewName(this.value)"'
          + ' onkeydown="boardSaveViewKeydown(event)">';
        html += '<button class="board-filter-btn active" onclick="boardSubmitSaveView()">Save</button>';
        html += '<button class="board-filter-btn" onclick="boardCancelSaveView()">Cancel</button>';
        html += '</div>';
      } else if (currentViewSavable) {
        html += '<button class="board-filter-btn" onclick="boardStartSaveView()">Save View</button>';
      }
    }
    for (var vi = 0; vi < savedViews.length; vi++) {
      var view = savedViews[vi];
      var viewName = esc(view.name).replace(/'/g, "\\'");
      html += '<div class="board-saved-view">';
      html += '<button class="board-filter-btn'
        + (_boardViewMatchesCurrent(view) ? ' active' : '')
        + '" onclick="boardApplySavedView(\'' + viewName + '\')">'
        + esc(view.name) + '</button>';
      html += '<button class="board-saved-view-delete"'
        + ' onclick="event.stopPropagation();boardDeleteSavedView(\'' + viewName + '\')">&times;</button>';
      html += '</div>';
    }
    html += '</div>';
  }

  // Active filter chips
  if (showToolbar) {
    if (_boardFilterLabels.length || _boardFilterActions.length || _boardFilterAgents.length || _boardFilterHealth.length) {
      html += '<div class="board-filter-active">';
      for (var fi = 0; fi < _boardFilterLabels.length; fi++) {
        var fl = _boardFilterLabels[fi];
        html += '<span class="board-filter-active-chip board-filter-active-label"'
          + ' onclick="boardRemoveFilterLabel(\'' + esc(fl).replace(/'/g, "\\'") + '\')">'
          + esc(fl) + ' &times;</span>';
      }
      for (var fi = 0; fi < _boardFilterActions.length; fi++) {
        var fa = _boardFilterActions[fi];
        html += '<span class="board-filter-active-chip board-filter-active-action"'
          + ' onclick="boardRemoveFilterAction(\'' + esc(fa).replace(/'/g, "\\'") + '\')">'
          + esc(fa) + ' &times;</span>';
      }
      for (var fi = 0; fi < _boardFilterAgents.length; fi++) {
        var aid = _boardFilterAgents[fi];
        html += '<span class="board-filter-active-chip board-filter-active-action"'
          + ' onclick="boardRemoveFilterAgent(\'' + esc(aid).replace(/'/g, "\\'") + '\')">'
          + esc(_boardAgentName(aid) || aid) + ' &times;</span>';
      }
      for (var fi = 0; fi < _boardFilterHealth.length; fi++) {
        var hs = _boardFilterHealth[fi];
        html += '<span class="board-filter-active-chip board-filter-active-health"'
          + ' onclick="boardRemoveFilterHealth(\'' + esc(hs).replace(/'/g, "\\'") + '\')">'
          + esc(_boardHealthDisplayName(hs)) + ' &times;</span>';
      }
      html += '</div>';
    }
  }
  // When filters become active, save the current lane; auto-select first non-empty lane
  if (filtersActive) {
    if (!_boardPreFilterLane) _boardPreFilterLane = _boardSelectedLane;
    // Check if current lane has matches; if not, jump to first that does
    var curCount = _boardLaneCount(_boardSelectedLane, renderModel);
    if (curCount === 0) {
      for (var fi = 0; fi < lanes.length; fi++) {
        if (_boardLaneCount(lanes[fi], renderModel) > 0) {
          _boardSelectedLane = lanes[fi];
          break;
        }
      }
    }
  }

  _boardActivateViewState(_boardCurrentViewKey());

  // Lane tab bar is only needed for narrow layouts. Wide standalone boards
  // expose the schedules toggle beside the View menu and render lanes as
  // headers inside each column instead.
  if (!_boardShowSchedules && !wideShell) {
    html += '<div class="board-lane-bar">';
    html += '<button class="board-lane-scroll-btn" id="board-scroll-left" onclick="boardScrollLanes(-1)" title="Scroll left">&#9664;</button>';
    html += '<div class="board-lane-tabs" id="board-lane-tabs">';
    for (var i = 0; i < lanes.length; i++) {
      var l = lanes[i];
      var cnt = _boardLaneCount(l, renderModel);
      var cls = (!_boardShowSchedules && l === _boardSelectedLane) ? ' active' : '';
      if (filtersActive && cnt === 0) cls += ' dimmed';
      var escLane = esc(l).replace(/'/g, "\\'");
      html += '<button class="board-lane-tab board-lane-drop-target' + cls + '"'
        + ' data-lane="' + esc(l) + '"'
        + ' onclick="boardSelectLane(\'' + escLane + '\')"'
        + ' ondragover="boardLaneTabDragOver(event)"'
        + ' ondragleave="boardLaneTabDragLeave(event)"'
        + ' ondrop="boardLaneTabDrop(event)">'
        + esc(l) + '<span class="lane-count">' + cnt + '</span>'
        + '</button>';
    }
    html += '</div>';
    html += '<button class="board-lane-scroll-btn" id="board-scroll-right" onclick="boardScrollLanes(1)" title="Scroll right">&#9654;</button>';
    html += '</div>';
  }

  // Schedules view (replaces cards when active)
  if (_boardShowSchedules) {
    html += _renderSchedulesView();
    panel.innerHTML = html;
    _boardRestoreRenderedState();
    _boardAfterRenderLayout();
    if (restoreState) _restoreSurfaceState(panel, restoreState);
    return;
  }

  var childrenOf = renderModel.childrenOf;
  var nextLaneEntryDelay = 0;
  if (wideLayout) {
    html += _boardRenderWideAddTaskSection();
  }
  html += '<div class="board-cards board-density-' + _boardCardDensityMode()
    + (wideLayout ? ' board-wide-grid' : '') + '" id="board-cards">';
  if (wideLayout) {
    for (var laneIdx = 0; laneIdx < lanes.length; laneIdx++) {
      var wideSection = _boardRenderWideLaneColumn(
        lanes[laneIdx],
        renderModel,
        filtersActive,
      );
      html += wideSection.html;
      var wideDelay = _boardVisibleLaneEntryRefreshDelay(
        wideSection.rootTasks,
        childrenOf,
        wideSection.renderLimit,
      );
      if (wideDelay > 0 && (!nextLaneEntryDelay || wideDelay < nextLaneEntryDelay)) {
        nextLaneEntryDelay = wideDelay;
      }
    }
  } else {
    var laneSection = _boardRenderLaneSection(
      _boardSelectedLane,
      renderModel,
      filtersActive,
    );
    html += laneSection.html;
    nextLaneEntryDelay = _boardVisibleLaneEntryRefreshDelay(
      laneSection.rootTasks,
      childrenOf,
      laneSection.renderLimit,
    );
  }
  _boardScheduleLaneEntryRefresh(nextLaneEntryDelay);

  html += '</div>';

  // Selection bar
  html += _renderBoardSelectionBar();

  panel.innerHTML = html;
  _boardRestoreRenderedState();

  // Auto-focus inputs (only when user explicitly opened, not on re-renders)
  if (_boardAddingTask && _boardAddTaskFocus) {
    _boardAddTaskFocus = false;
    var tInp = document.getElementById('board-add-task-input');
    if (tInp) {
      boardAddTaskAutoResize(tInp);
      tInp.focus();
      // Place cursor at end
      tInp.selectionStart = tInp.selectionEnd = tInp.value.length;
    }
  }
  if (_boardSavingView && _boardSaveViewFocus) {
    _boardSaveViewFocus = false;
    var viewInp = document.getElementById('board-save-view-input');
    if (viewInp) {
      viewInp.focus();
      viewInp.selectionStart = 0;
      viewInp.selectionEnd = viewInp.value.length;
    }
  }

  if (restoreState) _restoreSurfaceState(panel, restoreState);
  if (quickEditRefocusTask) {
    _boardRefocusQuickEditInput(quickEditRefocusTask, quickEditRefocusKind);
  }
  if (_boardAddingTask) {
    var addTaskInput = document.getElementById('board-add-task-input');
    if (addTaskInput) boardAddTaskAutoResize(addTaskInput);
  }
  _boardAfterRenderLayout();
}

/* ---- Virtual scroll ------------------------------------------------- */

function boardLoadMore() {
  var lanes = _boardWideLayoutActive(document.getElementById('panel-board'))
    ? _boardVisibleLanes()
    : [_boardSelectedLane || _boardVisibleLanes()[0] || ''];
  var model = _boardBuildRenderModel(lanes);
  var cardCount = 0;
  for (var i = 0; i < lanes.length; i++) {
    var laneCards = _boardRenderableCardCountForRoots(
      _boardRootTasksForLane(lanes[i], model.visibleTasks, model),
      model.childrenOf
    );
    if (laneCards > cardCount) cardCount = laneCards;
  }
  if (_boardRenderLimit >= cardCount) return;
  _boardRenderLimit += 50;
  _boardSyncActiveViewState();
  renderBoard();
}

/* ---- Lane selection ------------------------------------------------- */

function boardSelectLane(lane) {
  if (!_boardShowSchedules && lane === _boardSelectedLane) return;
  var wideLayout = _boardWideLayoutActive(document.getElementById('panel-board'));
  _boardPrepareViewChange(!wideLayout);
  _boardShowSchedules = false;  // exit schedules view on lane click
  // Save current scroll so renderBoard can restore + adjust for new active tab
  var tabs = document.getElementById('board-lane-tabs');
  if (tabs) _boardScrollLeft = tabs.scrollLeft;
  _boardSelectedLane = lane;
  _boardFocusedTask = '';
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  renderBoard();
}

function boardSetLaneSort(mode) {
  _boardHydrateLaneSorts();
  var group = _currentGroup();
  var lane = _boardSelectedLane;
  if (!group || !lane) return;
  _boardCloseViewMenu();
  _boardPrepareViewChange(false);
  mode = _boardNormalizeLaneSortMode(mode);
  var sorts = _boardLaneSortsByGroup[group] || {};
  if (mode === 'manual') {
    delete sorts[lane];
  } else {
    sorts[lane] = mode;
  }
  if (Object.keys(sorts).length) {
    _boardLaneSortsByGroup[group] = sorts;
  } else {
    delete _boardLaneSortsByGroup[group];
  }
  _boardViewStates[_boardCurrentViewKey()] = { scroll_top: 0, render_limit: 50 };
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  _boardPersistLaneSorts();
  renderBoard();
}

function boardSetCardDensity(mode) {
  _boardHydrateCardDensity();
  var group = _currentGroup();
  if (!group) return;
  _boardCloseViewMenu();
  mode = _boardNormalizeCardDensity(mode);
  if (mode === 'normal') {
    delete _boardCardDensityByGroup[group];
  } else {
    _boardCardDensityByGroup[group] = mode;
  }
  _boardPersistCardDensity();
  renderBoard();
}

/* ---- Lane scroll ---------------------------------------------------- */

function boardScrollLanes(dir) {
  var tabs = document.getElementById('board-lane-tabs');
  if (!tabs) return;

  var children = tabs.querySelectorAll('.board-lane-tab');
  if (!children.length) return;

  if (dir > 0) {
    // Find the first tab not fully visible on the right
    var viewRight = tabs.scrollLeft + tabs.clientWidth;
    for (var i = 0; i < children.length; i++) {
      var tabRight = children[i].offsetLeft + children[i].offsetWidth;
      if (tabRight > viewRight + 0.5) {
        tabs.scrollLeft = tabRight - tabs.clientWidth;
        return;
      }
    }
  } else {
    // Find the last tab not fully visible on the left
    var viewLeft = tabs.scrollLeft;
    if (viewLeft < 1) return; // already at start
    for (var i = children.length - 1; i >= 0; i--) {
      if (children[i].offsetLeft < viewLeft - 0.5) {
        tabs.scrollLeft = children[i].offsetLeft;
        return;
      }
    }
    // Close to start but not quite — snap to 0
    tabs.scrollLeft = 0;
  }
}

function boardUpdateScrollArrows() {
  var tabs = document.getElementById('board-lane-tabs');
  var left = document.getElementById('board-scroll-left');
  var right = document.getElementById('board-scroll-right');
  if (!tabs || !left || !right) return;

  var sl = tabs.scrollLeft;
  var maxScroll = tabs.scrollWidth - tabs.clientWidth;
  left.classList.toggle('hidden', sl < 1);
  right.classList.toggle('hidden', maxScroll - sl < 1);
}

/* ---- Add task dropdown ---------------------------------------------- */

function boardStartAddTask() {
  boardStartAddTaskForLane(_boardSelectedLane || _boardVisibleLanes()[0] || '');
}

function boardStartAddTaskForLane(lane) {
  var nextLane = lane || _boardSelectedLane || _boardVisibleLanes()[0] || '';
  var panel = document.getElementById('panel-board');
  var wideLayout = _boardWideLayoutActive(panel);
  if (_boardShowSchedules || nextLane !== _boardSelectedLane) {
    _boardPrepareViewChange(!wideLayout);
    _boardShowSchedules = false;
    var tabs = document.getElementById('board-lane-tabs');
    if (tabs) _boardScrollLeft = tabs.scrollLeft;
    _boardSelectedLane = nextLane;
    _boardSelectedTasks = {};
    _boardLastSelectedTask = '';
  }
  _boardAddingTask = true;
  _boardAddingTaskLane = _boardDefaultAddTaskLane();
  _boardAddTaskFocus = true;
  _boardFocusedTask = '';
  if (!_boardInlineDraftId) _boardInlineDraftId = _generateDraftId();
  renderBoard();
}

function boardStartAddTaskActionForLane(lane) {
  var nextLane = lane || _boardSelectedLane || _boardVisibleLanes()[0] || '';
  if (_boardShowSchedules || nextLane !== _boardSelectedLane) {
    var panel = document.getElementById('panel-board');
    var wideLayout = _boardWideLayoutActive(panel);
    _boardPrepareViewChange(!wideLayout);
    _boardShowSchedules = false;
    var tabs = document.getElementById('board-lane-tabs');
    if (tabs) _boardScrollLeft = tabs.scrollLeft;
    _boardSelectedLane = nextLane;
    _boardFocusedTask = '';
    _boardSelectedTasks = {};
    _boardLastSelectedTask = '';
    renderBoard();
  }
  if (_boardActList === null) boardToggleActionList();
}

function boardCancelAddTask() {
  var el = document.getElementById('board-add-task-input');
  if (el) _boardAddingTaskDraft = el.value;
  // Keep open if there's a draft or attachments — user may be dragging files
  if (_boardAddingTaskDraft || _boardInlineAttachments.length) return;
  setTimeout(function() {
    // A re-render (mouse-move over cards that invalidates surfaces, a WS
    // delta, etc.) replaces the textarea via innerHTML, which fires blur
    // on the old node. The surface-state restore in renderBoard then
    // re-focuses the new textarea synchronously. If focus is on the
    // inline input at this point, the blur was render-driven — keep the
    // form open.
    var active = document.activeElement;
    if (active && active.id === 'board-add-task-input') return;
    _boardAddingTask = false;
    _boardTplList = null;
    renderBoard();
  }, 150);
}

function boardClearAddTask() {
  // Clean up inline draft attachments
  if (_boardInlineDraftId && _boardInlineAttachments.length) {
    fetch('/api/upload/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: _boardInlineDraftId })
    });
  }
  _boardAddingTask = false;
  _boardAddingTaskDraft = '';
  _boardAddingTaskAction = '';
  _boardAddingTaskAgent = '';
  _boardAddingTaskLane = '';
  _boardInlineDraftId = '';
  _boardInlineAttachments = [];
  _boardTplList = null;
  renderBoard();
}

/** Parse trailing %label tokens from inline task input. */
function _boardParseInlineLabels(text) {
  var tokens = text.trimEnd().split(/\s+/);
  var labels = [];
  while (tokens.length > 1 && tokens[tokens.length - 1].charAt(0) === '%') {
    labels.push(tokens.pop().slice(1).toLowerCase());
  }
  // Deduplicate
  var seen = {};
  var unique = [];
  for (var i = 0; i < labels.length; i++) {
    if (!seen[labels[i]] && labels[i]) { seen[labels[i]] = true; unique.push(labels[i]); }
  }
  return { title: tokens.join(' '), labels: unique };
}

function boardSubmitAddTask() {
  var el = document.getElementById('board-add-task-input');
  var val = el ? el.value.trim() : '';
  if (!val) return;
  var parsed = _boardParseInlineLabels(val);
  if (!parsed.title) return;
  var targetLane = _boardAddingTaskLane || _boardDefaultAddTaskLane();
  // Clear the live textarea immediately so any blur fired during the
  // upcoming innerHTML replacement (WebKit dispatches blur on focused
  // elements about to be detached) cannot reseed the draft from the
  // pre-submit text.
  if (el) el.value = '';
  _boardAddingTaskDraft = '';
  var msg = { cmd: 'board_add_task', task: parsed.title, group: _currentGroup(), lane: targetLane };
  if (_boardInlineDraftId) msg.id = _boardInlineDraftId;
  if (parsed.labels.length) msg.labels = parsed.labels;
  if (_boardAddingTaskAction) msg.action_name = _boardAddingTaskAction;
  if (_boardAddingTaskAgent) msg.agent_id = _boardAddingTaskAgent;
  if (_boardInlineAttachments.length) msg.attachments = _boardInlineAttachments.slice();
  _boardAddingTaskAction = '';
  _boardAddingTaskAgent = '';
  _boardAddingTaskLane = '';
  _boardInlineDraftId = '';
  _boardInlineAttachments = [];
  _boardTplList = null;
  // Keep the form open with a cleared textarea so the operator can
  // rapid-fire consecutive tasks. Re-focus the new textarea after the
  // upcoming render recreates it.
  _boardAddTaskFocus = true;
  send(msg);
  renderBoard();
}

/* ---- Inline add: drag-and-drop attachments -------------------------- */

function boardInlineDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}

function boardInlineDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

function boardInlineDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
  if (!_boardInlineDraftId) _boardInlineDraftId = _generateDraftId();
  var files = e.dataTransfer.files;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    if (!file.type.startsWith('image/')) continue;
    var fd = new FormData();
    fd.append('task_id', _boardInlineDraftId);
    fd.append('file', file);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.data) {
          for (var j = 0; j < res.data.length; j++) {
            _boardInlineAttachments.push(res.data[j]);
          }
          renderBoard();
        }
      });
  }
}

function boardInlineRemoveAtt(idx) {
  var att = _boardInlineAttachments[idx];
  if (att && _boardInlineDraftId) {
    send({ cmd: 'remove_attachment', task_id: _boardInlineDraftId, filename: att.filename });
  }
  _boardInlineAttachments.splice(idx, 1);
  renderBoard();
}

var _boardLabelDropdownIdx = -1;

function boardAddTaskInput(el) {
  _boardAddingTaskDraft = el.value;
  boardAddTaskAutoResize(el);
  var text = el.value.substring(0, el.selectionStart);
  var match = text.match(/%([\w-]*)$/);
  var dropdown = document.getElementById('board-add-label-dropdown');
  if (!dropdown) return;
  if (!match) { dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1; return; }
  var prefix = match[1].toLowerCase();
  var all = _getAllLabels();
  var filtered = [];
  for (var i = 0; i < all.length; i++) {
    if (all[i].toLowerCase().indexOf(prefix) >= 0) filtered.push(all[i]);
    if (filtered.length >= 8) break;
  }
  if (!filtered.length) { dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1; return; }
  if (filtered.length === 1 && prefix === filtered[0].toLowerCase()) {
    dropdown.style.display = 'none'; _boardLabelDropdownIdx = -1;
    boardPickInlineLabel(filtered[0]); return;
  }
  _boardLabelDropdownIdx = -1;
  var html = '';
  for (var i = 0; i < filtered.length; i++) {
    html += '<div class="deps-option" onmousedown="boardPickInlineLabel(\'' + esc(filtered[i]) + '\')">' + esc(filtered[i]) + '</div>';
  }
  dropdown.innerHTML = html;
  dropdown.style.display = '';
}

function boardPickInlineLabel(label) {
  var el = document.getElementById('board-add-task-input');
  if (!el) return;
  var before = el.value.substring(0, el.selectionStart);
  var after = el.value.substring(el.selectionStart);
  el.value = before.replace(/%([\w-]*)$/, '%' + label + ' ') + after;
  document.getElementById('board-add-label-dropdown').style.display = 'none';
  _boardLabelDropdownIdx = -1;
  el.focus();
  el.selectionStart = el.selectionEnd = el.value.length;
}

function _boardAddTaskActiveSelection(el) {
  if (!el) return 0;
  if (el.selectionDirection === 'backward' && typeof el.selectionStart === 'number') {
    return el.selectionStart;
  }
  return typeof el.selectionEnd === 'number' ? el.selectionEnd : 0;
}

function _boardAddTaskSelectionAnchor(el) {
  if (!el) return 0;
  if (el.selectionDirection === 'backward' && typeof el.selectionEnd === 'number') {
    return el.selectionEnd;
  }
  return typeof el.selectionStart === 'number' ? el.selectionStart : 0;
}

function _boardAddTaskLineBoundary(value, caret, toEnd) {
  if (toEnd) {
    var lineEnd = value.indexOf('\n', caret);
    return lineEnd >= 0 ? lineEnd : value.length;
  }
  var lineStart = value.lastIndexOf('\n', Math.max(caret - 1, 0));
  return lineStart >= 0 ? lineStart + 1 : 0;
}

function _boardMoveAddTaskCaretToLineBoundary(el, e, toEnd) {
  if (!el || typeof el.value !== 'string') return false;
  var active = _boardAddTaskActiveSelection(el);
  var anchor = _boardAddTaskSelectionAnchor(el);
  var target = _boardAddTaskLineBoundary(el.value, active, toEnd);
  if (typeof e.preventDefault === 'function') e.preventDefault();
  if (e.shiftKey) {
    el.selectionStart = Math.min(anchor, target);
    el.selectionEnd = Math.max(anchor, target);
    if ('selectionDirection' in el) {
      el.selectionDirection = target < anchor ? 'backward' : 'forward';
    }
  } else {
    el.selectionStart = el.selectionEnd = target;
    if ('selectionDirection' in el) el.selectionDirection = 'none';
  }
  boardAddTaskInput(el);
  return true;
}

function boardAddTaskKeydown(e) {
  var input = e.target && typeof e.target.value === 'string'
    ? e.target
    : document.getElementById('board-add-task-input');
  var dropdown = document.getElementById('board-add-label-dropdown');
  var visible = dropdown && dropdown.style.display !== 'none';
  if (visible) {
    var opts = dropdown.querySelectorAll('.deps-option');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _boardLabelDropdownIdx = Math.min(_boardLabelDropdownIdx + 1, opts.length - 1);
      _boardHighlightLabelOpt(opts);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      _boardLabelDropdownIdx = Math.max(_boardLabelDropdownIdx - 1, 0);
      _boardHighlightLabelOpt(opts);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      var idx = _boardLabelDropdownIdx >= 0 ? _boardLabelDropdownIdx : 0;
      if (opts[idx]) opts[idx].onmousedown();
      return;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      dropdown.style.display = 'none';
      _boardLabelDropdownIdx = -1;
      return;
    }
  }
  if ((e.key === 'Home' || e.key === 'End') && !e.altKey && !e.ctrlKey && !e.metaKey) {
    if (_boardMoveAddTaskCaretToLineBoundary(input, e, e.key === 'End')) return;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    e.stopPropagation();
    boardClearAddTask();
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    boardSubmitAddTask();
  }
}

function _boardHighlightLabelOpt(opts) {
  for (var i = 0; i < opts.length; i++) {
    opts[i].classList.toggle('active', i === _boardLabelDropdownIdx);
  }
}

function boardToggleAgentDropdown() {
  var wrap = document.getElementById('board-add-agent-wrap');
  var existing = wrap ? wrap.querySelector('.board-add-agent-list') : null;
  if (existing) { existing.remove(); return; }
  var grp = _currentGroup();
  var agents = [];
  if (state && state.groups && state.groups[grp]) {
    var aids = state.groups[grp];
    for (var i = 0; i < aids.length; i++) {
      var a = state.agents[aids[i]];
      if (a && a.cell_type === 'agent') agents.push(a);
    }
  }
  var listEl = document.createElement('div');
  listEl.className = 'board-add-agent-list';
  var noBtn = document.createElement('button');
  noBtn.className = 'board-tpl-item' + (!_boardAddingTaskAgent ? ' selected' : '');
  noBtn.textContent = 'No agent';
  noBtn.onmousedown = function(e) { e.preventDefault(); };
  noBtn.onclick = function() { _boardAddingTaskAgent = ''; listEl.remove(); renderBoard(); };
  listEl.appendChild(noBtn);
  for (var j = 0; j < agents.length; j++) {
    (function(ag) {
      var btn = document.createElement('button');
      btn.className = 'board-tpl-item' + (ag.id === _boardAddingTaskAgent ? ' selected' : '');
      btn.textContent = ag.name;
      btn.onmousedown = function(e) { e.preventDefault(); };
      btn.onclick = function() { _boardAddingTaskAgent = ag.id; listEl.remove(); renderBoard(); };
      listEl.appendChild(btn);
    })(agents[j]);
  }
  wrap.appendChild(listEl);
}

function boardToggleLaneDropdown() {
  var wrap = document.getElementById('board-add-lane-wrap');
  var existing = wrap ? wrap.querySelector('.board-add-agent-list') : null;
  if (existing) { existing.remove(); return; }
  var lanes = _boardAddTaskLaneOptions();
  var listEl = document.createElement('div');
  listEl.className = 'board-add-agent-list board-add-lane-list';
  for (var i = 0; i < lanes.length; i++) {
    (function(nextLane) {
      var btn = document.createElement('button');
      btn.className = 'board-tpl-item' + (nextLane === _boardAddingTaskLane ? ' selected' : '');
      btn.textContent = nextLane;
      btn.onmousedown = function(e) { e.preventDefault(); };
      btn.onclick = function() {
        _boardAddingTaskLane = nextLane;
        listEl.remove();
        renderBoard();
      };
      listEl.appendChild(btn);
    })(lanes[i]);
  }
  wrap.appendChild(listEl);
}

function boardAddTaskAutoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

function _boardCloseTplListHandler(e) {
  var list = document.querySelector('.board-tpl-list');
  if (list && !list.contains(e.target)) {
    _boardTplList = null;
    document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
    renderBoard();
  }
}

function boardToggleActionList() {
  if (_boardActList !== null) {
    _boardActList = null;
    document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
    renderBoard();
  } else {
    _boardActDropdownWaiting = true;
    send({ cmd: 'list_actions', group: _currentGroup() });
  }
}

function _boardActItemHtml(act) {
  var actName = esc(act.name).replace(/'/g, "\\'");
  var h = '<button class="board-tpl-item" onclick="_boardPickAction(\'' + actName + '\')">';
  h += '<span class="board-tpl-item-name">' + esc(act.name) + '</span>';
  if (act.description) h += '<span class="board-tpl-item-desc">' + esc(act.description) + '</span>';
  h += '</button>';
  return h;
}

function _boardShowActionList(msg) {
  _boardActDropdownWaiting = false;
  _boardActList = msg.actions || [];
  renderBoard();
  document.addEventListener('mousedown', _boardCloseTplListHandler, true);
}

function _boardPickAction(name) {
  _boardActList = null;
  document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
  // If inline add is active, set action on the toolbar instead of opening modal
  if (_boardAddingTask) {
    _boardAddingTaskAction = name;
    renderBoard();
    return;
  }
  _boardAddingTask = false;
  renderBoard();
  // Open the task modal with the action pre-selected
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: '',
    description: '',
    labels: [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: name,
    agentTemplate: '',
    actionVars: {},
    group: _currentGroup(),
    lane: _boardSelectedLane || '',
    scheduledInput: '',
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

function _boardPickNoAction() {
  _boardActList = null;
  document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
  if (_boardAddingTask) {
    _boardAddingTaskAction = '';
    renderBoard();
    return;
  }
  _boardAddingTask = false;
  renderBoard();
  openAddTask(_boardSelectedLane);
}

/* ---- Card context menu ---------------------------------------------- */


/* ---- Multi-select / bulk operations --------------------------------- */

function _boardResetBatchEdit() {
  _boardBatchEditOpen = false;
  _boardBatchEditLabel = '';
  _boardBatchEditAssignee = '__unchanged__';
  _boardBatchEditDueMode = 'unchanged';
  _boardBatchEditDue = '';
  _boardBatchEditAction = '__unchanged__';
  _boardBatchEditPriority = '__unchanged__';
  _boardBatchActionWaiting = false;
  _boardBatchActionOptions = [];
}

function _boardSelectedTaskItems() {
  var out = [];
  var tasks = _boardTasks();
  for (var id in _boardSelectedTasks) {
    if (tasks[id]) out.push(tasks[id]);
  }
  return out;
}

function _boardSelectedSingleGroup() {
  var tasks = _boardSelectedTaskItems();
  if (!tasks.length) return '';
  var group = tasks[0].group || '';
  for (var i = 1; i < tasks.length; i++) {
    if ((tasks[i].group || '') !== group) return '';
  }
  return group;
}

function _boardBatchEditAgents() {
  var group = _boardSelectedSingleGroup();
  if (!group || !state || !state.agents) return [];
  var out = [];
  for (var id in state.agents) {
    var agent = state.agents[id];
    if (agent.cell_type !== 'agent' || agent.group !== group) continue;
    out.push(agent);
  }
  out.sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
  return out;
}

function boardToggleBatchEdit(evt) {
  evt.stopPropagation();
  if (_boardBatchEditOpen) {
    _boardResetBatchEdit();
    renderBoard();
    return;
  }
  _boardResetBatchEdit();
  _boardBatchEditOpen = true;
  var group = _boardSelectedSingleGroup();
  if (group) {
    _boardBatchActionWaiting = true;
    send({ cmd: 'list_actions', group: group });
  }
  renderBoard();
  requestAnimationFrame(function() {
    var input = document.getElementById('board-batch-label-input');
    if (input) input.focus();
  });
}

function _handleBoardBatchActionList(msg) {
  _boardBatchActionWaiting = false;
  _boardBatchActionOptions = msg.actions || [];
  renderBoard();
}

function boardBatchEditLabelInput(value) {
  _boardBatchEditLabel = value;
}

function boardBatchEditDueInput(value) {
  _boardBatchEditDueMode = value ? 'set' : 'unchanged';
  _boardBatchEditDue = value;
}

function boardBatchEditClearDue() {
  _boardBatchEditDueMode = 'clear';
  _boardBatchEditDue = '';
  renderBoard();
}

function boardBatchEditKeydown(evt) {
  if (evt.key === 'Escape') {
    evt.preventDefault();
    _boardResetBatchEdit();
    renderBoard();
  } else if (evt.key === 'Enter' && !evt.shiftKey) {
    evt.preventDefault();
    boardApplyBatchEdit();
  }
}

function boardApplyBatchEdit() {
  var tasks = _boardSelectedTaskItems();
  if (!tasks.length) return;
  var addLabel = _boardBatchEditLabel.trim();
  var changeAssignee = _boardBatchEditAssignee !== '__unchanged__';
  var changeAction = _boardBatchEditAction !== '__unchanged__';
  var changePriority = _boardBatchEditPriority !== '__unchanged__';
  var changeDue = _boardBatchEditDueMode !== 'unchanged';
  if (!addLabel && !changeAssignee && !changeAction && !changePriority && !changeDue) return;

  for (var i = 0; i < tasks.length; i++) {
    var task = tasks[i];
    var fields = {};

    if (addLabel && !isSystemLabel(addLabel) && !/^priority:/.test(addLabel)) {
      var labels = (task.labels || []).slice();
      if (labels.indexOf(addLabel) < 0) {
        labels.push(addLabel);
        fields.labels = labels;
      }
    }

    if (changePriority) {
      var nextLabels = (fields.labels || (task.labels || []).slice()).filter(function(label) {
        return !/^priority:/.test(label);
      });
      if (_boardBatchEditPriority) nextLabels.push('priority:' + _boardBatchEditPriority);
      fields.labels = nextLabels;
    }

    if (changeAssignee && _boardSelectedSingleGroup()) {
      fields.agent_id = _boardBatchEditAssignee;
    }

    if (changeAction && _boardSelectedSingleGroup()) {
      fields.action_name = _boardBatchEditAction;
      fields.action_vars = {};
    }

    if (changeDue) {
      if (_boardBatchEditDueMode === 'clear') {
        fields.scheduled_at = '';
      } else if (_boardBatchEditDue) {
        var d = new Date(_boardBatchEditDue);
        if (!isNaN(d.getTime())) fields.scheduled_at = d.toISOString();
      }
    }

    if (Object.keys(fields).length) {
      fields.cmd = 'board_update_task';
      fields.id = task.id;
      send(fields);
    }
  }

  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
}

function _boardSelectedCount() {
  var n = 0;
  for (var k in _boardSelectedTasks) n++;
  return n;
}

function boardClearSelection() {
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardQuickEditTask = '';
  _boardQuickEditKind = '';
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = '';
  _boardSetQuickEditRefocus('', '');
  _boardResetBatchEdit();
  renderBoard();
}

function _renderBoardSelectionBar() {
  var count = _boardSelectedCount();
  if (count === 0) return '';
  var lanes = _boardVisibleLanes();
  var singleGroup = _boardSelectedSingleGroup();
  var agents = _boardBatchEditAgents();
  var archiveIds = _boardSelectedArchiveIds(false);
  var restoreIds = _boardSelectedArchiveIds(true);
  var html = '<div class="board-selection-bar">';
  html += '<span class="board-selection-count">' + count + ' selected</span>';
  // Move to lane dropdown
  html += '<div class="board-selection-dropdown-wrap">';
  html += '<button class="board-selection-btn" onclick="boardBulkToggleMove(event)">Move to &#9662;</button>';
  html += '<div class="board-selection-dropdown" id="board-bulk-move-menu" style="display:none">';
  for (var i = 0; i < lanes.length; i++) {
    var escLane = esc(lanes[i]).replace(/'/g, "\\'");
    html += '<button class="board-selection-dropdown-item" onclick="boardBulkMove(\'' + escLane + '\')">' + esc(lanes[i]) + '</button>';
  }
  html += '</div></div>';
  // Batch edit
  html += '<div class="board-selection-dropdown-wrap">';
  html += '<button class="board-selection-btn" onclick="boardToggleBatchEdit(event)">Batch edit</button>';
  if (_boardBatchEditOpen) {
    html += '<div class="board-selection-dropdown board-selection-batch-panel" id="board-batch-edit-panel">';
    html += '<div class="board-selection-batch-grid">';
    html += '<label class="board-selection-batch-label">Add label</label>';
    html += '<input type="text" class="board-selection-label-input" id="board-batch-label-input"'
      + ' value="' + esc(_boardBatchEditLabel) + '" placeholder="Label name"'
      + ' oninput="boardBatchEditLabelInput(this.value)"'
      + ' onkeydown="boardBatchEditKeydown(event)">';
    html += '<label class="board-selection-batch-label">Assignee</label>';
    html += '<select class="board-selection-select"'
      + (singleGroup ? '' : ' disabled')
      + ' onchange="_boardBatchEditAssignee=this.value">';
    html += '<option value="__unchanged__"' + (_boardBatchEditAssignee === '__unchanged__' ? ' selected' : '') + '>No change</option>';
    html += '<option value=""' + (_boardBatchEditAssignee === '' ? ' selected' : '') + '>Unassigned</option>';
    for (var ai = 0; ai < agents.length; ai++) {
      var agent = agents[ai];
      html += '<option value="' + esc(agent.id) + '"' + (_boardBatchEditAssignee === agent.id ? ' selected' : '') + '>'
        + esc(agent.name || agent.id) + '</option>';
    }
    html += '</select>';
    html += '<label class="board-selection-batch-label">Due date</label>';
    html += '<div class="board-selection-batch-inline">';
    html += '<input type="datetime-local" class="board-selection-select" id="board-batch-due-input"'
      + ' value="' + esc(_boardBatchEditDue) + '"'
      + ' oninput="boardBatchEditDueInput(this.value)"'
      + ' onkeydown="boardBatchEditKeydown(event)">';
    html += '<button class="board-selection-btn" onclick="event.stopPropagation();boardBatchEditClearDue()">Clear</button>';
    html += '</div>';
    html += '<label class="board-selection-batch-label">Action</label>';
    html += '<select class="board-selection-select"'
      + (singleGroup ? '' : ' disabled')
      + ' onchange="_boardBatchEditAction=this.value">';
    html += '<option value="__unchanged__"' + (_boardBatchEditAction === '__unchanged__' ? ' selected' : '') + '>No change</option>';
    html += '<option value=""' + (_boardBatchEditAction === '' ? ' selected' : '') + '>None</option>';
    if (_boardBatchActionWaiting) {
      html += '<option value="" disabled>Loading actions…</option>';
    } else {
      for (var acti = 0; acti < _boardBatchActionOptions.length; acti++) {
        var action = _boardBatchActionOptions[acti];
        html += '<option value="' + esc(action.name) + '"' + (_boardBatchEditAction === action.name ? ' selected' : '') + '>'
          + esc(action.name) + '</option>';
      }
    }
    html += '</select>';
    html += '<label class="board-selection-batch-label">Priority</label>';
    html += '<select class="board-selection-select" onchange="_boardBatchEditPriority=this.value">';
    html += '<option value="__unchanged__"' + (_boardBatchEditPriority === '__unchanged__' ? ' selected' : '') + '>No change</option>';
    html += '<option value=""' + (_boardBatchEditPriority === '' ? ' selected' : '') + '>None</option>';
    html += '<option value="low"' + (_boardBatchEditPriority === 'low' ? ' selected' : '') + '>Low</option>';
    html += '<option value="medium"' + (_boardBatchEditPriority === 'medium' ? ' selected' : '') + '>Medium</option>';
    html += '<option value="high"' + (_boardBatchEditPriority === 'high' ? ' selected' : '') + '>High</option>';
    html += '</select>';
    html += '</div>';
    if (!singleGroup) {
      html += '<div class="board-selection-batch-note">Action and assignee edits require all selected tasks to be in the same group.</div>';
    }
    html += '<div class="board-selection-batch-actions">';
    html += '<button class="board-selection-btn" onclick="boardApplyBatchEdit()">Apply to ' + count + '</button>';
    html += '<button class="board-selection-btn" onclick="event.stopPropagation();_boardResetBatchEdit();renderBoard()">Cancel</button>';
    html += '</div>';
    html += '</div>';
  }
  html += '</div>';
  // Dispatch (only when all selected tasks are in Backlog)
  var allBacklog = true;
  for (var id in _boardSelectedTasks) {
    var _t = (state.board_tasks || {})[id];
    if (!_t || _t.lane !== 'Backlog') { allBacklog = false; break; }
  }
  if (allBacklog) {
    html += '<button class="board-selection-btn" onclick="boardBulkDispatch()">Dispatch</button>';
  }
  if (archiveIds.length) {
    html += '<button class="board-selection-btn" onclick="boardBulkArchiveSelected()">Archive completed'
      + (archiveIds.length === count ? '' : ' (' + archiveIds.length + ')')
      + '</button>';
  }
  if (restoreIds.length) {
    html += '<button class="board-selection-btn" onclick="boardBulkRestoreSelected()">Restore'
      + (restoreIds.length === count ? '' : ' (' + restoreIds.length + ')')
      + '</button>';
  }
  // Delete
  html += '<button class="board-selection-btn board-selection-btn-danger" onclick="boardBulkDelete()">Delete</button>';
  // Clear selection
  html += '<button class="board-selection-btn" onclick="boardClearSelection()">&#10005;</button>';
  html += '</div>';
  return html;
}

function boardBulkToggleMove(evt) {
  evt.stopPropagation();
  var menu = document.getElementById('board-bulk-move-menu');
  if (_boardBatchEditOpen) {
    _boardResetBatchEdit();
    renderBoard();
    return;
  }
  if (menu) menu.style.display = menu.style.display === 'none' ? '' : 'none';
}

function boardBulkMove(lane) {
  var tasks = _boardTasks();
  for (var id in _boardSelectedTasks) {
    if (tasks[id]) send({ cmd: 'board_move_task', id: id, lane: lane });
  }
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
}

function boardBulkArchiveSelected() {
  var ids = _boardSelectedArchiveIds(false);
  if (!ids.length) return;
  _boardArchiveTaskIds(ids, true);
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
}

function boardBulkRestoreSelected() {
  var ids = _boardSelectedArchiveIds(true);
  if (!ids.length) return;
  _boardArchiveTaskIds(ids, false);
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
}

function boardBulkAddLabel(label) {
  var tasks = _boardTasks();
  for (var id in _boardSelectedTasks) {
    var t = tasks[id];
    if (!t) continue;
    var labels = (t.labels || []).slice();
    if (labels.indexOf(label) < 0) {
      labels.push(label);
      send({ cmd: 'board_update_task', id: id, labels: labels });
    }
  }
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
}

function boardBulkDelete() {
  var count = _boardSelectedCount();
  showConfirm('Delete ' + count + ' task' + (count === 1 ? '' : 's') + '?').then(function(ok) {
    if (!ok) return;
    for (var id in _boardSelectedTasks) {
      send({ cmd: 'board_remove_task', id: id });
    }
    _boardSelectedTasks = {};
    _boardLastSelectedTask = '';
    _boardResetBatchEdit();
  });
}

function boardBulkDispatch() {
  var tasks = [];
  for (var id in _boardSelectedTasks) {
    var t = (state.board_tasks || {})[id];
    if (t) tasks.push(t);
  }
  if (!tasks.length) return;
  var assigned = [];
  var unassigned = [];
  for (var i = 0; i < tasks.length; i++) {
    if (tasks[i].agent_id) assigned.push(tasks[i]);
    else unassigned.push(tasks[i]);
  }
  if (!unassigned.length) {
    _boardBulkDispatchSend(tasks);
    return;
  }
  _boardShowBulkDispatchDialog(assigned, unassigned);
}

function _boardBulkDispatchSend(tasks) {
  for (var i = 0; i < tasks.length; i++) {
    var msg = { cmd: 'dispatch_task', id: tasks[i].id };
    if (tasks[i].agent_id) msg.agent_id = tasks[i].agent_id;
    else msg.create_agent = true;
    send(msg);
  }
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
}

function _boardShowBulkDispatchDialog(assigned, unassigned) {
  var grp = _currentGroup();
  var agents = [];
  if (state && state.groups && state.groups[grp]) {
    var aids = state.groups[grp];
    for (var i = 0; i < aids.length; i++) {
      var a = state.agents[aids[i]];
      if (a && a.cell_type === 'agent') agents.push(a);
    }
  }
  var total = assigned.length + unassigned.length;
  var msg = total + ' task' + (total === 1 ? '' : 's') + ' to dispatch';
  if (assigned.length) msg += ', ' + unassigned.length + ' need an agent';
  else msg = unassigned.length + ' task' + (unassigned.length === 1 ? '' : 's') + ' need an agent';

  document.getElementById('confirm-message').textContent = msg;
  var extras = document.getElementById('confirm-extras');
  extras.innerHTML = '';

  // "New agent for each" button
  var newBtn = document.createElement('button');
  newBtn.className = 'btn-primary';
  newBtn.textContent = 'New agent for each';
  newBtn.style.cssText = 'width:100%;margin-bottom:4px;';
  newBtn.onclick = function() {
    document.getElementById('modal-confirm').classList.remove('visible');
    _boardBulkDispatchSend(assigned.concat(unassigned));
  };
  extras.appendChild(newBtn);

  // Existing agent buttons
  for (var j = 0; j < agents.length; j++) {
    (function(ag) {
      var btn = document.createElement('button');
      btn.className = 'btn-secondary';
      btn.textContent = ag.name;
      btn.style.cssText = 'width:100%;margin-bottom:4px;';
      btn.onclick = function() {
        document.getElementById('modal-confirm').classList.remove('visible');
        for (var k = 0; k < unassigned.length; k++) unassigned[k].agent_id = ag.id;
        _boardBulkDispatchSend(assigned.concat(unassigned));
      };
      extras.appendChild(btn);
    })(agents[j]);
  }

  // Wire cancel button
  _confirmResolve = function() {};
  var yesBtn = document.getElementById('confirm-yes-btn');
  yesBtn.style.display = 'none';
  document.getElementById('modal-confirm').classList.add('visible');
  // Restore yes button on close
  var obs = new MutationObserver(function(mutations) {
    for (var m = 0; m < mutations.length; m++) {
      if (!document.getElementById('modal-confirm').classList.contains('visible')) {
        yesBtn.style.display = '';
        obs.disconnect();
        break;
      }
    }
  });
  obs.observe(document.getElementById('modal-confirm'), { attributes: true, attributeFilter: ['class'] });
}

function boardEditTask(taskId) {
  _closeCtxMenu();
  openEditTask(taskId);
}

function boardDuplicateTask(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  var clone = _boardTaskCloneFields(task);
  var msg = {
    cmd: 'board_add_task',
    task: clone.task,
    group: clone.group,
  };
  if (clone.description) msg.description = clone.description;
  if (clone.action_name) msg.action_name = clone.action_name;
  if (clone.agent_template) msg.agent_template = clone.agent_template;
  if (Object.keys(clone.action_vars).length) msg.action_vars = clone.action_vars;
  if (clone.labels.length) msg.labels = clone.labels;
  send(msg);
}

function boardCloneTask(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  var clone = _boardTaskCloneFields(task);
  _taskOpenModal({
    draftScope: 'clone:' + taskId,
    editId: null,
    title: 'Clone Task',
    submitLabel: 'Create',
    task: clone.task,
    description: clone.description,
    labels: clone.labels,
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: clone.action_name,
    agentTemplate: clone.agent_template,
    actionVars: clone.action_vars,
    group: clone.group,
    lane: '',
    scheduledInput: '',
    selectTask: false,
  });
}

function boardMoveTaskToLane(taskId, lane) {
  _closeCtxMenu();
  send({ cmd: 'board_move_task', id: taskId, lane: lane });
}

function boardDeleteTask(taskId) {
  _closeCtxMenu();
  send({ cmd: 'board_remove_task', id: taskId });
}

function boardArchiveTask(taskId) {
  _closeCtxMenu();
  _boardArchiveTaskIds([taskId], true);
}

function boardRestoreTask(taskId) {
  _closeCtxMenu();
  _boardArchiveTaskIds([taskId], false);
}

function boardMarkTaskVerified(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task || (typeof _boardCanMarkTaskVerified === 'function'
      && !_boardCanMarkTaskVerified(task))) return;
  send({
    cmd: 'board_verify_task',
    id: taskId,
    actor_name: 'Operator',
    verification_state: 'passed',
    manual_smoke_done: true,
    human_validation_pending: '',
    deploy_needed: false,
  });
}

function boardUnlinkAgent(taskId) {
  _closeCtxMenu();
  send({ cmd: 'board_update_task', id: taskId, agent_id: '' });
}

function boardImportExternal() {
  _closeCtxMenu();
  var ref = window.prompt('External reference or URL');
  if (!ref) return;
  var group = window.prompt('Group', _currentGroup() || '');
  if (!group) return;
  send({ cmd: 'external_import_task', ref: ref.trim(), group: group, lane: _boardSelectedLane || '' });
}

function boardDetachTask(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;
  var labels = (task.labels || []).filter(function(l) { return l !== 'loom:derived'; });
  send({
    cmd: 'board_update_task', id: taskId,
    parent_task_id: '', pipeline_depth: 0,
    pipeline_root_id: taskId, status: '', labels: labels
  });
}

function boardLinkAgent(taskId) {
  _closeCtxMenu();
  // Build agent list submenu
  var menu = document.getElementById('ctx-menu');
  var html = '<button class="ctx-label" disabled>Link agent</button>';

  if (state && state.agents) {
    var agents = state.agents;
    var count = 0;
    for (var id in agents) {
      var a = agents[id];
      if (a.cell_type === 'agent') {
        html += '<button onclick="boardDoLinkAgent(\'' + taskId + '\',\'' + id + '\')">'
          + esc(a.name) + ' <span style="color:var(--text-dim);font-size:9px">'
          + esc(a.group) + '</span></button>';
        count++;
      }
    }
    if (count === 0) {
      html += '<button disabled>No agents available</button>';
    }
  }

  menu.innerHTML = html;
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardDoLinkAgent(taskId, agentId) {
  _closeCtxMenu();
  send({ cmd: 'board_update_task', id: taskId, agent_id: agentId });
}

function boardLinkExternal(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  var refDefault = task.external_url || ((task.provider && task.external_id)
    ? (task.provider + ':' + task.external_id) : (task.external_id || ''));
  var ref = window.prompt('External reference or URL', refDefault);
  if (ref === null) return;
  send({
    cmd: 'external_link_task',
    id: taskId,
    ref: ref.trim(),
    provider: task.provider || '',
    external_id: task.external_id || '',
    external_url: task.external_url || '',
  });
}

function boardClearExternal(taskId) {
  _closeCtxMenu();
  send({
    cmd: 'external_link_task',
    id: taskId,
    provider: '',
    external_id: '',
    external_url: '',
    ref: '',
  });
}

function boardOpenExternal(taskId) {
  _closeCtxMenu();
  send({ cmd: 'external_open_task', id: taskId });
}

function boardPushExternalStatus(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  var status = window.prompt('External status', task.status || task.lane || '');
  if (status === null) return;
  var note = window.prompt('Optional note', '');
  if (note === null) return;
  send({ cmd: 'external_push_task_status', id: taskId, status: status.trim(), note: note.trim() });
}

function boardPostExternalComment(taskId) {
  _closeCtxMenu();
  var comment = window.prompt('Comment to post externally');
  if (comment === null || !comment.trim()) return;
  send({ cmd: 'external_post_task_comment', id: taskId, comment: comment.trim() });
}

/* ---- Dispatch task to agent ----------------------------------------- */

function boardDispatchTask(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

  // If task already has an assigned agent that still exists, dispatch directly
  if (task.agent_id && state.agents[task.agent_id]) {
    boardDispatchToExisting(taskId, task.agent_id);
    return;
  }

  var menu = document.getElementById('ctx-menu');
  var html = '<button class="ctx-label" disabled>Dispatch to</button>';

  // Option to create a new agent
  html += '<button onclick="boardDispatchToNew(\'' + taskId + '\')">New agent</button>';

  // List existing agents in the same group
  if (state && state.agents && task.group) {
    var agents = state.agents;
    for (var id in agents) {
      var a = agents[id];
      if (a.cell_type === 'agent' && a.group === task.group) {
        html += '<button onclick="boardDispatchToExisting(\'' + taskId + '\',\'' + id + '\')">'
          + esc(a.name) + '</button>';
      }
    }
  }

  menu.innerHTML = html;
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardDispatchToNew(taskId) {
  _closeCtxMenu();
  send({ cmd: 'dispatch_task', id: taskId, create_agent: true });
}

function boardDispatchToExisting(taskId, agentId) {
  _closeCtxMenu();
  send({ cmd: 'dispatch_task', id: taskId, agent_id: agentId });
}

function boardPreviewPrompt(taskId) {
  _closeCtxMenu();
  send({ cmd: 'preview_prompt', id: taskId });
}

function boardToggleArchived() {
  _boardCloseViewMenu();
  _boardShowArchived = !_boardShowArchived;
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
}

function boardArchiveSuggestedDone() {
  var ids = _boardStaleDoneTaskIds();
  if (!ids.length) return;
  _boardArchiveTaskIds(ids, true);
  renderBoard();
}

function _handleDispatchActionMissing(msg) {
  var taskId = msg.task_id;
  var actName = msg.action_name || '(unknown)';
  showConfirm('Action "' + actName + '" not found.\nDispatch without action?').then(function(yes) {
    if (yes) {
      // Task is already linked to an agent — re-dispatch to the same agent
      var t = (state && state.board_tasks || {})[taskId];
      var cmd = { cmd: 'dispatch_task', id: taskId, force_no_action: true };
      if (t && t.agent_id) {
        cmd.agent_id = t.agent_id;
      } else {
        cmd.create_agent = true;
      }
      send(cmd);
    }
  });
}

function _adjustCtxMenuOverflow() {
  var menu = document.getElementById('ctx-menu');
  if (!menu) return;
  requestAnimationFrame(function() {
    var rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth)
      menu.style.left = Math.max(0, window.innerWidth - rect.width - 4) + 'px';
    if (rect.bottom > window.innerHeight)
      menu.style.top = Math.max(0, window.innerHeight - rect.height - 4) + 'px';
  });
}

function _closeCtxMenu() {
  var m = document.getElementById('ctx-menu');
  if (m) m.classList.remove('open');
}

/* ---- Card drag and drop --------------------------------------------- */

function boardCardDragStart(e, id) {
  _boardDragId = id;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', id);
  var card = e.target.closest('.board-card');
  if (card) setTimeout(function() { card.classList.add('dragging'); }, 0);
}

function boardCardDragEnd(e) {
  _boardDragId = '';
  // Clean up all dragging/drop classes
  document.querySelectorAll('.board-card.dragging').forEach(function(el) {
    el.classList.remove('dragging');
  });
  document.querySelectorAll('.board-lane-drop-target.drop-target').forEach(function(el) {
    el.classList.remove('drop-target');
  });
  document.querySelectorAll('.board-card.drop-before,.board-card.drop-after').forEach(function(el) {
    el.classList.remove('drop-before', 'drop-after');
  });
}

function _boardClosestLaneDropTarget(node) {
  while (node) {
    if (node.classList && node.classList.contains('board-lane-drop-target')) return node;
    node = node.parentNode || null;
  }
  return null;
}

function _boardTaskLane(taskId) {
  var task = _boardTasks()[taskId];
  return task ? (task.lane || '') : '';
}

function _boardDropServerPosition(lane, targetId, placeAfter, movingWithinLane) {
  var tasks = _boardTasksInLane(lane);
  if (!movingWithinLane) {
    tasks = tasks.filter(function(task) { return task.id !== _boardDragId; });
  }
  var targetIdx = -1;
  for (var i = 0; i < tasks.length; i++) {
    if (tasks[i].id === targetId) {
      targetIdx = i;
      break;
    }
  }
  if (targetIdx < 0) return null;
  var pos = placeAfter ? targetIdx + 1 : targetIdx;
  var serverPos = tasks.length - pos - (movingWithinLane ? 1 : 0);
  return Math.max(0, serverPos);
}

function boardCardDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';

  // Show drop indicator
  var card = e.target.closest('.board-card');
  if (card && card.dataset.taskId !== _boardDragId) {
    var rect = card.getBoundingClientRect();
    var mid = rect.top + rect.height / 2;
    card.classList.remove('drop-before', 'drop-after');
    card.classList.add(e.clientY < mid ? 'drop-before' : 'drop-after');
  }
}

function boardCardDragLeave(e) {
  if (e.stopPropagation) e.stopPropagation();
  var card = e.target.closest('.board-card');
  if (card) card.classList.remove('drop-before', 'drop-after');
}

function boardCardDrop(e) {
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  var card = e.target.closest('.board-card');
  if (!card || !_boardDragId) return;
  var targetId = card.dataset.taskId;
  if (targetId === _boardDragId) return;
  var sourceLane = _boardTaskLane(_boardDragId);
  var targetLane = _boardTaskLane(targetId) || _boardSelectedLane;
  if (!targetLane) return;

  var rect = card.getBoundingClientRect();
  var mid = rect.top + rect.height / 2;
  var placeAfter = e.clientY >= mid;
  if (sourceLane === targetLane) {
    if (_boardLaneSortMode(targetLane) !== 'manual') return;
    var serverPos = _boardDropServerPosition(targetLane, targetId, placeAfter, true);
    if (serverPos === null) return;
    send({ cmd: 'board_reorder_task', id: _boardDragId, position: serverPos });
  } else {
    var move = { cmd: 'board_move_task', id: _boardDragId, lane: targetLane };
    if (_boardLaneSortMode(targetLane) === 'manual') {
      var targetPos = _boardDropServerPosition(targetLane, targetId, placeAfter, false);
      if (targetPos !== null) move.position = targetPos;
    }
    send(move);
  }
  card.classList.remove('drop-before', 'drop-after');
}

// Drop on lane tab to move card to a different lane
function boardLaneTabDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';
  var target = _boardClosestLaneDropTarget(e.target);
  if (target) target.classList.add('drop-target');
}

function boardLaneTabDragLeave(e) {
  if (e.stopPropagation) e.stopPropagation();
  var target = _boardClosestLaneDropTarget(e.target);
  if (target) target.classList.remove('drop-target');
}

function boardLaneTabDrop(e) {
  e.preventDefault();
  if (e.stopPropagation) e.stopPropagation();
  var target = _boardClosestLaneDropTarget(e.target);
  if (!target || !_boardDragId) return;
  var lane = target.dataset.lane;
  if (lane && lane !== _boardTaskLane(_boardDragId)) {
    send({ cmd: 'board_move_task', id: _boardDragId, lane: lane });
  }
  target.classList.remove('drop-target');
}

/* ---- Search & filter ------------------------------------------------ */

function boardUpdateSearch(query) {
  clearTimeout(_boardSearchTimer);
  _boardSearchTimer = setTimeout(function() {
    _boardPrepareViewChange(true);
    _boardSearchQuery = query;
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
    // Restore focus and cursor to search input
    var inp = document.getElementById('board-search-input');
    if (inp) { inp.focus(); inp.selectionStart = inp.selectionEnd = inp.value.length; }
  }, 200);
}

function boardToggleLabel(label) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardFilterLabels.splice(idx, 1);
  } else {
    _boardFilterLabels.push(label);
  }
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
  _boardPersistFilterState();
}

function boardToggleAction(action) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardFilterActions.splice(idx, 1);
  } else {
    _boardFilterActions.push(action);
  }
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterLabel(label) {
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterLabels.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardRemoveFilterAction(action) {
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterActions.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardToggleAgent(agentId) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterAgents.indexOf(agentId);
  if (idx >= 0) {
    _boardFilterAgents.splice(idx, 1);
  } else {
    _boardFilterAgents.push(agentId);
  }
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterAgent(agentId) {
  var idx = _boardFilterAgents.indexOf(agentId);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterAgents.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardToggleHealth(stateName) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterHealth.indexOf(stateName);
  if (idx >= 0) {
    _boardFilterHealth.splice(idx, 1);
  } else {
    _boardFilterHealth.push(stateName);
  }
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterHealth(stateName) {
  var idx = _boardFilterHealth.indexOf(stateName);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterHealth.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardClearFilters() {
  _boardPrepareViewChange(true);
  _boardSearchQuery = '';
  _boardQuickView = '';
  _boardFilterLabels = [];
  _boardFilterActions = [];
  _boardFilterAgents = [];
  _boardFilterHealth = [];
  _boardCloseFilterDropdown();
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  if (_boardPreFilterLane) {
    _boardSelectedLane = _boardPreFilterLane;
    _boardPreFilterLane = '';
  }
  renderBoard();
  _boardPersistFilterState();
}

function boardSaveCurrentView() {
  boardStartSaveView();
}

function boardStartSaveView() {
  if (_boardIsDefaultFilterState(_boardCurrentViewState())) return;
  _boardSavingView = true;
  _boardSavingViewName = '';
  _boardSaveViewFocus = true;
  renderBoard();
}

function boardUpdateSaveViewName(value) {
  _boardSavingViewName = value || '';
}

function boardSaveViewKeydown(e) {
  if (!e) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    boardSubmitSaveView();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    boardCancelSaveView();
  }
}

function boardCancelSaveView() {
  _boardSavingView = false;
  _boardSavingViewName = '';
  _boardSaveViewFocus = false;
  renderBoard();
}

function boardSubmitSaveView(name) {
  _boardHydrateSavedViews();
  if (_boardIsDefaultFilterState(_boardCurrentViewState())) return;
  var group = _currentGroup();
  if (!group) return;
  if (typeof name !== 'string') {
    name = _boardSavingViewName;
    var input = document.getElementById('board-save-view-input');
    if (input && typeof input.value === 'string') name = input.value;
  }
  name = (name || '').trim();
  if (!name) return;
  var views = _boardSavedViewsByGroup[group] || [];
  var next = _boardCurrentViewState();
  next.name = name;
  var normalized = _boardNormalizeSavedView(next);
  var replaced = false;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === name) {
      views[i] = normalized;
      replaced = true;
      break;
    }
  }
  if (!replaced) views.push(normalized);
  _boardSavedViewsByGroup[group] = views;
  _boardSavingView = false;
  _boardSavingViewName = '';
  _boardSaveViewFocus = false;
  _boardPersistSavedViews();
  renderBoard();
}

function boardApplyQuickView(mode) {
  _boardPrepareViewChange(true);
  _boardQuickView = (_boardQuickView === mode) ? '' : mode;
  _boardPreFilterLane = '';
  _boardCardsScrollTop = 0;
  _boardRenderLimit = 50;
  renderBoard();
  _boardPersistFilterState();
}

function boardApplySavedView(name) {
  var views = _boardCurrentGroupSavedViews();
  for (var i = 0; i < views.length; i++) {
    if (views[i].name !== name) continue;
    _boardPrepareViewChange(true);
    _boardSearchQuery = views[i].search_query;
    _boardQuickView = views[i].quick_view || '';
    _boardFilterLabels = views[i].filter_labels.slice();
    _boardFilterActions = views[i].filter_actions.slice();
    _boardFilterAgents = views[i].filter_agents.slice();
    _boardFilterHealth = (views[i].filter_health || []).slice();
    _boardPreFilterLane = '';
    _boardCardsScrollTop = 0;
    _boardRenderLimit = 50;
    renderBoard();
    _boardPersistFilterState();
    return;
  }
}

function boardDeleteSavedView(name) {
  _boardHydrateSavedViews();
  var group = _currentGroup();
  if (!group) return;
  var views = _boardSavedViewsByGroup[group] || [];
  _boardSavedViewsByGroup[group] = views.filter(function(view) {
    return view.name !== name;
  });
  if (_boardSavedViewsByGroup[group].length === 0) {
    delete _boardSavedViewsByGroup[group];
  }
  _boardPersistSavedViews();
  renderBoard();
}

/* ---- Filter dropdowns ----------------------------------------------- */

function boardToggleLabelFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'label') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllLabelCounts();
  var names = Object.keys(counts).sort();
  if (!names.length) return;
  _boardFilterDropdownType = 'label';
  _boardOpenFilterDropdown('board-label-filter-wrap', 'label', names, counts, _boardFilterLabels);
}

function boardToggleActionFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'action') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllActionCounts();
  var names = Object.keys(counts).sort();
  if (!names.length) return;
  _boardFilterDropdownType = 'action';
  _boardOpenFilterDropdown('board-action-filter-wrap', 'action', names, counts, _boardFilterActions);
}

function boardToggleAgentFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'agent') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllAgentCounts();
  var ids = Object.keys(counts).sort(function(a, b) {
    return (_boardAgentName(a) || '').localeCompare(_boardAgentName(b) || '');
  });
  if (!ids.length) return;
  _boardFilterDropdownType = 'agent';
  _boardOpenFilterDropdown('board-agent-filter-wrap', 'agent', ids, counts, _boardFilterAgents);
}

function boardToggleHealthFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'health') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllHealthCounts();
  var names = _boardHealthOrder.filter(function(name) {
    return counts[name] || _boardFilterHealth.indexOf(name) >= 0;
  });
  if (!names.length) return;
  _boardFilterDropdownType = 'health';
  _boardOpenFilterDropdown('board-health-filter-wrap', 'health', names, counts, _boardFilterHealth);
}

function _boardOpenFilterDropdown(wrapId, kind, names, counts, selectedArr) {
  var wrap = document.getElementById(wrapId);
  if (!wrap) return;
  var btn = wrap.querySelector('.board-filter-btn');
  if (!btn) return;
  var rect = btn.getBoundingClientRect();

  var dd = document.createElement('div');
  dd.className = 'board-filter-dropdown';
  dd.id = 'board-filter-dropdown-active';
  dd.style.position = 'fixed';
  dd.style.top = (rect.bottom + 2) + 'px';
  dd.style.left = rect.left + 'px';

  var search = document.createElement('input');
  search.type = 'text';
  search.className = 'board-filter-dropdown-search';
  search.placeholder = 'Filter ' + (kind === 'health' ? 'health states' : (kind + 's')) + '\u2026';
  dd.appendChild(search);

  var list = document.createElement('div');
  list.className = 'board-filter-dropdown-list';
  dd.appendChild(list);

  function buildList(query) {
    list.innerHTML = '';
    var q = (query || '').toLowerCase();
    var filtered = [];
    for (var i = 0; i < names.length; i++) {
      var searchText = kind === 'agent' ? (_boardAgentName(names[i]) || names[i])
        : (kind === 'health' ? _boardHealthDisplayName(names[i]) : names[i]);
      if (q && searchText.toLowerCase().indexOf(q) < 0) continue;
      filtered.push(names[i]);
    }

    function addRow(name) {
      var row = document.createElement('label');
      row.className = 'board-filter-dropdown-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selectedArr.indexOf(name) >= 0;
      (function(n) {
        cb.addEventListener('change', function() {
          if (kind === 'label') boardToggleLabel(n);
          else if (kind === 'agent') boardToggleAgent(n);
          else if (kind === 'health') boardToggleHealth(n);
          else boardToggleAction(n);
          buildList(search.value);
        });
      })(name);
      row.appendChild(cb);
      var span = document.createElement('span');
      span.className = 'board-filter-dropdown-name';
      var displayName = kind === 'agent' ? (_boardAgentName(name) || name)
        : (kind === 'health' ? _boardHealthDisplayName(name)
          : (kind === 'label' && isSystemLabel(name)) ? displayLabel(name) : name);
      span.textContent = displayName;
      row.appendChild(span);
      var badge = document.createElement('span');
      badge.className = 'board-filter-dropdown-count';
      badge.textContent = counts[name];
      row.appendChild(badge);
      list.appendChild(row);
    }

    if (kind === 'label') {
      var sysNames = [], userNames = [];
      for (var i = 0; i < filtered.length; i++) {
        if (isSystemLabel(filtered[i])) sysNames.push(filtered[i]);
        else userNames.push(filtered[i]);
      }
      if (sysNames.length) {
        var hdr = document.createElement('div');
        hdr.className = 'board-filter-dropdown-header';
        hdr.textContent = 'System';
        list.appendChild(hdr);
        for (var i = 0; i < sysNames.length; i++) addRow(sysNames[i]);
      }
      if (userNames.length) {
        var hdr = document.createElement('div');
        hdr.className = 'board-filter-dropdown-header';
        hdr.textContent = 'Labels';
        list.appendChild(hdr);
        for (var i = 0; i < userNames.length; i++) addRow(userNames[i]);
      }
    } else {
      for (var i = 0; i < filtered.length; i++) addRow(filtered[i]);
    }
  }

  buildList('');
  search.addEventListener('input', function() { buildList(search.value); });

  document.body.appendChild(dd);

  // Adjust if dropdown overflows viewport
  requestAnimationFrame(function() {
    var ddRect = dd.getBoundingClientRect();
    if (ddRect.right > window.innerWidth) {
      dd.style.left = Math.max(0, window.innerWidth - ddRect.width - 4) + 'px';
    }
    if (ddRect.bottom > window.innerHeight) {
      dd.style.top = Math.max(0, rect.top - ddRect.height - 2) + 'px';
    }
  });

  search.focus();

  // Close on outside click
  var handler = function(e) {
    if (!dd.contains(e.target) && !e.target.closest('.board-filter-btn')) {
      _boardCloseFilterDropdown();
    }
  };
  setTimeout(function() {
    document.addEventListener('mousedown', handler, true);
  }, 0);

  _boardFilterDropdownCleanup = function() {
    document.removeEventListener('mousedown', handler, true);
    if (dd.parentNode) dd.remove();
    _boardFilterDropdownCleanup = null;
  };
}

function _boardCloseFilterDropdown() {
  _boardFilterDropdownType = null;
  if (_boardFilterDropdownCleanup) _boardFilterDropdownCleanup();
}

/* ---- Keyboard nav --------------------------------------------------- */

function boardKeydown(e) {
  // Only handle if board panel is visible
  if (!_boardPanelVisible()) return false;

  var lanes = _boardVisibleLanes();
  var tasks = _boardTasksInLane(_boardSelectedLane);

  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    var idx = lanes.indexOf(_boardSelectedLane);
    if (idx < 0) return false;
    var newIdx = e.key === 'ArrowLeft' ? idx - 1 : idx + 1;
    if (newIdx >= 0 && newIdx < lanes.length) {
      boardSelectLane(lanes[newIdx]);
      return true;
    }
  }

  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    if (!tasks.length) return false;
    var curIdx = -1;
    for (var i = 0; i < tasks.length; i++) {
      if (tasks[i].id === _boardFocusedTask) { curIdx = i; break; }
    }
    var nextIdx;
    if (e.key === 'ArrowDown') {
      nextIdx = curIdx < tasks.length - 1 ? curIdx + 1 : 0;
    } else {
      nextIdx = curIdx > 0 ? curIdx - 1 : tasks.length - 1;
    }
    _boardFocusedTask = tasks[nextIdx].id;
    renderBoard();
    return true;
  }

  if (e.key === 'Enter' && _boardFocusedTask) {
    openEditTask(_boardFocusedTask);
    return true;
  }

  if (e.key === 'Delete' && _boardFocusedTask) {
    boardDeleteTask(_boardFocusedTask);
    _boardFocusedTask = '';
    return true;
  }

  return false;
}


/* ---- Resolve ask task ----------------------------------------------- */

function boardOpenResolve(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;
  var modal = document.getElementById('modal-resolve');
  document.getElementById('resolve-question').textContent = task.task || '';
  document.getElementById('resolve-answer').value = '';
  modal.dataset.taskId = taskId;
  modal.classList.add('visible');
  setTimeout(function() {
    document.getElementById('resolve-answer').focus();
  }, 50);
}

function submitResolve() {
  var modal = document.getElementById('modal-resolve');
  var taskId = modal.dataset.taskId;
  var answer = document.getElementById('resolve-answer').value.trim();
  if (!answer) return;
  send({ cmd: 'resolve_ask', id: taskId, answer: answer });
  closeModals();
}

/* ---- Schedule helpers ------------------------------------------------ */

function _boardScheduleCount() {
  var scheds = (state && state.schedules) || {};
  var count = 0;
  for (var id in scheds) count++;
  return count;
}

function boardToggleSchedules() {
  _boardPrepareViewChange(true);
  _boardShowSchedules = !_boardShowSchedules;
  renderBoard();
}

function _renderSchedulesView() {
  var scheds = (state && state.schedules) || {};
  var html = '<div class="board-cards" id="board-cards">';

  // Add schedule button
  html += '<div class="board-add-task" onclick="openScheduleModal()">'
    + '<button class="board-add-btn">'
    + '+ Add schedule</button></div>';

  var list = [];
  for (var id in scheds) list.push(scheds[id]);
  list.sort(function(a, b) {
    return (a.name || '').localeCompare(b.name || '');
  });

  if (!list.length) {
    html += '<div class="board-empty">No schedules</div>';
  }

  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var enabled = s.enabled !== false;
    var cls = 'board-card board-schedule-card' + (enabled ? '' : ' dimmed');
    var trigger = s.cron_expr || s.scheduled_at || '';
    var triggerLabel = s.cron_expr ? 'cron' : 'one-shot';

    html += '<div class="' + cls + '" data-schedule-id="' + esc(s.id) + '">';

    // Header
    html += '<div class="board-card-header">';
    html += '<span class="board-card-title">' + esc(s.name || '') + '</span>';
    html += '<span class="board-card-slug">' + esc(s.slug || '') + '</span>';
    html += '</div>';

    // Trigger info
    html += '<div class="board-schedule-trigger">';
    html += '<span class="board-schedule-type">' + esc(triggerLabel) + '</span> ';
    html += '<code>' + esc(trigger) + '</code>';
    if (s.timezone) html += ' <span class="board-schedule-tz">(' + esc(s.timezone) + ')</span>';
    html += '</div>';

    // Task template
    if (s.task_template) {
      html += '<div class="board-schedule-template">' + esc(s.task_template) + '</div>';
    }

    // Action badge
    if (s.action_name) {
      html += '<div class="board-card-action">' + esc(s.action_name) + '</div>';
    }

    // Status row
    html += '<div class="board-schedule-status">';
    if (s.next_run_at && enabled) {
      html += '<span class="board-schedule-next">Next: ' + _schedFormatTime(s.next_run_at) + '</span>';
    }
    if (s.run_count) {
      html += '<span class="board-schedule-runs">' + s.run_count + ' run' + (s.run_count === 1 ? '' : 's') + '</span>';
    }
    html += '</div>';

    // Actions row
    html += '<div class="board-schedule-actions">';
    html += '<button class="board-schedule-action-btn" onclick="scheduleToggleEnabled(\'' + esc(s.id) + '\')">'
      + (enabled ? 'Disable' : 'Enable') + '</button>';
    html += '<button class="board-schedule-action-btn" onclick="scheduleRunNow(\'' + esc(s.id) + '\')">Run now</button>';
    html += '<button class="board-schedule-action-btn" onclick="openScheduleModal(\'' + esc(s.id) + '\')">Edit</button>';
    html += '<button class="board-schedule-action-btn board-schedule-delete-btn" onclick="scheduleDelete(\'' + esc(s.id) + '\')">Delete</button>';
    html += '</div>';

    html += '</div>';
  }

  html += '</div>';
  return html;
}

function _schedFormatTime(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch(e) {
    return iso;
  }
}

function scheduleToggleEnabled(sid) {
  var s = (state.schedules || {})[sid];
  if (!s) return;
  send({ cmd: s.enabled !== false ? 'schedule_disable' : 'schedule_enable', id: sid });
}

function scheduleRunNow(sid) {
  send({ cmd: 'schedule_run', id: sid });
}

function scheduleDelete(sid) {
  showConfirm('Delete this schedule?', function() {
    send({ cmd: 'schedule_remove', id: sid });
  });
}

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
var _boardInlineDraftId = '';     // pre-generated task ID for inline attachments
var _boardInlineAttachments = []; // attachments uploaded during inline creation
var _boardAddTaskFocus = false;   // true only on explicit open, not re-renders
var _boardActDropdownWaiting = false;  // waiting for action list for dropdown
var _boardActList = null;              // fetched actions shown inline (null = hidden)
var _boardScrollLeft = 0;      // preserve scroll across re-renders
var _boardCardsScrollTop = 0;  // preserve cards scroll across re-renders
var _boardViewStates = {};     // keyed lane/filter/schedules scroll + render state
var _boardActiveViewKey = '';
var _boardNextViewDefault = null;
var _boardSkipViewCaptureOnce = false;
var _boardDragId = '';          // card being dragged

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
var _boardBatchEditOpen = false; // selection bar batch edit panel visibility
var _boardBatchEditLabel = '';   // add this label to every selected task
var _boardBatchEditAssignee = '__unchanged__'; // selected assignee value
var _boardBatchEditDueMode = 'unchanged'; // unchanged | set | clear
var _boardBatchEditDue = '';     // datetime-local value for batch due edits
var _boardBatchEditAction = '__unchanged__'; // selected action value
var _boardBatchEditPriority = '__unchanged__'; // selected priority value
var _boardBatchActionWaiting = false; // waiting for batch action list
var _boardBatchActionOptions = [];    // actions for batch edit action picker
var _boardArchiveLabel = 'loom:archived';
var _boardArchiveStaleDays = 7;
var _boardLaneEntryRefreshTimer = 0;
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

function _boardNormalizeFilterState(raw) {
  raw = raw || {};
  return {
    search_query: typeof raw.search_query === 'string' ? raw.search_query : '',
    quick_view: raw.quick_view === 'recent' || raw.quick_view === 'touched'
      ? raw.quick_view
      : '',
    filter_labels: Array.isArray(raw.filter_labels) ? raw.filter_labels.slice() : [],
    filter_actions: Array.isArray(raw.filter_actions) ? raw.filter_actions.slice() : [],
    filter_agents: Array.isArray(raw.filter_agents) ? raw.filter_agents.slice() : [],
    filter_health: Array.isArray(raw.filter_health) ? raw.filter_health.slice() : [],
    pre_filter_lane: typeof raw.pre_filter_lane === 'string' ? raw.pre_filter_lane : '',
  };
}

function _boardHydratePersistedFilters() {
  if (_boardFiltersByGroup) return;
  _boardFiltersByGroup = {};
  var persisted = (state && state.board_filters_by_group) || {};
  for (var group in persisted) {
    _boardFiltersByGroup[group] = _boardNormalizeFilterState(persisted[group]);
  }
}

function _boardCurrentFilterState() {
  return _boardNormalizeFilterState({
    search_query: _boardSearchQuery,
    quick_view: _boardQuickView,
    filter_labels: _boardFilterLabels,
    filter_actions: _boardFilterActions,
    filter_agents: _boardFilterAgents,
    filter_health: _boardFilterHealth,
    pre_filter_lane: _boardPreFilterLane,
  });
}

function _boardApplyFilterState(raw) {
  var next = _boardNormalizeFilterState(raw);
  _boardSearchQuery = next.search_query;
  _boardQuickView = next.quick_view;
  _boardFilterLabels = next.filter_labels;
  _boardFilterActions = next.filter_actions;
  _boardFilterAgents = next.filter_agents;
  _boardFilterHealth = next.filter_health;
  _boardPreFilterLane = next.pre_filter_lane;
}

function _boardIsDefaultFilterState(raw) {
  var next = _boardNormalizeFilterState(raw);
  return next.search_query === ''
    && next.quick_view === ''
    && next.filter_labels.length === 0
    && next.filter_actions.length === 0
    && next.filter_agents.length === 0
    && next.filter_health.length === 0
    && next.pre_filter_lane === '';
}

function _boardPersistFilterState() {
  _boardHydratePersistedFilters();
  var group = _currentGroup();
  if (!group) return;
  var next = _boardCurrentFilterState();
  if (_boardIsDefaultFilterState(next)) {
    delete _boardFiltersByGroup[group];
  } else {
    _boardFiltersByGroup[group] = next;
  }
  if (typeof send === 'function') {
    send({ cmd: 'board_set_filters', filters_by_group: _boardFiltersByGroup });
  }
}

function _boardNormalizeSavedView(raw) {
  raw = raw || {};
  var filters = _boardNormalizeFilterState(raw);
  var name = typeof raw.name === 'string' ? raw.name.trim() : '';
  if (!name) return null;
  return {
    name: name,
    search_query: filters.search_query,
    quick_view: filters.quick_view,
    filter_labels: filters.filter_labels,
    filter_actions: filters.filter_actions,
    filter_agents: filters.filter_agents,
    filter_health: filters.filter_health,
  };
}

function _boardHydrateSavedViews() {
  if (_boardSavedViewsByGroup) return;
  _boardSavedViewsByGroup = {};
  var persisted = (state && state.board_saved_views_by_group) || {};
  for (var group in persisted) {
    var views = Array.isArray(persisted[group]) ? persisted[group] : [];
    _boardSavedViewsByGroup[group] = [];
    for (var i = 0; i < views.length; i++) {
      var normalized = _boardNormalizeSavedView(views[i]);
      if (normalized) _boardSavedViewsByGroup[group].push(normalized);
    }
  }
}

function _boardNormalizeLaneSortMode(mode) {
  return mode === 'newest' || mode === 'oldest' || mode === 'due'
    ? mode
    : 'manual';
}

function _boardHydrateLaneSorts() {
  if (_boardLaneSortsByGroup) return;
  _boardLaneSortsByGroup = {};
  var persisted = (state && state.board_lane_sorts_by_group) || {};
  for (var group in persisted) {
    var raw = persisted[group];
    if (!raw || typeof raw !== 'object') continue;
    _boardLaneSortsByGroup[group] = {};
    for (var lane in raw) {
      _boardLaneSortsByGroup[group][lane] =
        _boardNormalizeLaneSortMode(raw[lane]);
    }
  }
}

function _boardNormalizeCardDensity(mode) {
  return mode === 'compact' || mode === 'detailed'
    ? mode
    : 'normal';
}

function _boardHydrateCardDensity() {
  if (_boardCardDensityByGroup) return;
  _boardCardDensityByGroup = {};
  var persisted = (state && state.board_card_density_by_group) || {};
  for (var group in persisted) {
    _boardCardDensityByGroup[group] =
      _boardNormalizeCardDensity(persisted[group]);
  }
}

function _boardCardDensityMode() {
  _boardHydrateCardDensity();
  var group = _currentGroup();
  if (!group) return 'normal';
  return _boardNormalizeCardDensity(_boardCardDensityByGroup[group]);
}

function _boardPersistCardDensity() {
  _boardHydrateCardDensity();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_card_density',
      card_density_by_group: _boardCardDensityByGroup,
    });
  }
}

function _boardCurrentGroupLaneSorts() {
  _boardHydrateLaneSorts();
  var group = _currentGroup();
  if (!group) return {};
  return _boardLaneSortsByGroup[group] || {};
}

function _boardLaneSortMode(lane) {
  if (!lane) return 'manual';
  var sorts = _boardCurrentGroupLaneSorts();
  return _boardNormalizeLaneSortMode(sorts[lane]);
}

function _boardPersistLaneSorts() {
  _boardHydrateLaneSorts();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_lane_sorts',
      lane_sorts_by_group: _boardLaneSortsByGroup,
    });
  }
}

function _boardCurrentViewState() {
  var filters = _boardCurrentFilterState();
  return {
    search_query: filters.search_query,
    quick_view: filters.quick_view,
    filter_labels: filters.filter_labels,
    filter_actions: filters.filter_actions,
    filter_agents: filters.filter_agents,
    filter_health: filters.filter_health,
  };
}

function _boardCurrentGroupSavedViews() {
  _boardHydrateSavedViews();
  var group = _currentGroup();
  if (!group) return [];
  return _boardSavedViewsByGroup[group] || [];
}

function _boardPersistSavedViews() {
  _boardHydrateSavedViews();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_saved_views',
      saved_views_by_group: _boardSavedViewsByGroup,
    });
  }
}

function _boardViewMatchesCurrent(raw) {
  var view = _boardNormalizeSavedView(raw);
  if (!view) return false;
  var current = _boardCurrentViewState();
  return JSON.stringify(current) === JSON.stringify({
    search_query: view.search_query,
    quick_view: view.quick_view,
    filter_labels: view.filter_labels,
    filter_actions: view.filter_actions,
    filter_agents: view.filter_agents,
    filter_health: view.filter_health,
  });
}

function _boardSyncFiltersForCurrentGroup() {
  _boardHydratePersistedFilters();
  var group = _currentGroup() || '';
  if (group === _boardFilterStateGroup) return;
  var hasPriorGroup = _boardFilterStateGroup !== '';
  _boardFilterStateGroup = group;
  if (!hasPriorGroup
      && !_boardFiltersByGroup[group]
      && !_boardIsDefaultFilterState(_boardCurrentFilterState())) {
    return;
  }
  _boardApplyFilterState(_boardFiltersByGroup[group]);
}

function _boardCurrentViewKey() {
  var group = _currentGroup() || '';
  if (_boardShowSchedules) {
    return JSON.stringify({ group: group, view: 'schedules' });
  }
  return JSON.stringify({
    group: group,
    view: 'lane',
    lane: _boardSelectedLane || '',
    search_query: _boardSearchQuery,
    quick_view: _boardQuickView,
    filter_labels: _boardFilterLabels.slice().sort(),
    filter_actions: _boardFilterActions.slice().sort(),
    filter_agents: _boardFilterAgents.slice().sort(),
  });
}

function _boardActivateViewState(key) {
  _boardActiveViewKey = key;
  var saved = _boardViewStates[key];
  var fallback = _boardNextViewDefault;
  _boardNextViewDefault = null;
  if (saved) {
    _boardCardsScrollTop = saved.scroll_top || 0;
    _boardRenderLimit = saved.render_limit || 50;
    return;
  }
  if (fallback) {
    _boardCardsScrollTop = fallback.scroll_top || 0;
    _boardRenderLimit = fallback.render_limit || 50;
  }
}

function _boardSyncActiveViewState(cardsEl) {
  if (!_boardActiveViewKey) return;
  var saved = _boardViewStates[_boardActiveViewKey] || {
    scroll_top: 0,
    render_limit: 50,
  };
  if (cardsEl) {
    saved.scroll_top = cardsEl.scrollTop;
    _boardCardsScrollTop = cardsEl.scrollTop;
  } else {
    saved.scroll_top = _boardCardsScrollTop || 0;
  }
  saved.render_limit = _boardRenderLimit || 50;
  _boardViewStates[_boardActiveViewKey] = saved;
}

function _boardPrepareViewChange(resetNextView) {
  var cardsEl = document.getElementById('board-cards');
  if (!_boardActiveViewKey && cardsEl) {
    _boardActiveViewKey = _boardCurrentViewKey();
  }
  _boardSyncActiveViewState(cardsEl);
  _boardSkipViewCaptureOnce = true;
  if (resetNextView) {
    _boardNextViewDefault = { scroll_top: 0, render_limit: 50 };
  }
}

function _boardTimestamp(value) {
  if (!value || typeof value !== 'string') return Number.NaN;
  return Date.parse(value);
}

function _boardQuickViewLabel(mode) {
  return mode === 'recent'
    ? 'Recent tasks'
    : mode === 'touched'
      ? 'Recently touched'
      : '';
}

function _boardManualCompare(a, b) {
  var posDelta = (b.position || 0) - (a.position || 0);
  if (posDelta) return posDelta;
  return String(a.id || '').localeCompare(String(b.id || ''));
}

function _boardNewestCompare(a, b) {
  var aTime = _boardTimestamp(a.created_at);
  var bTime = _boardTimestamp(b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return bTime - aTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardManualCompare(a, b);
}

function _boardRecentlyTouchedCompare(a, b) {
  var aTime = _boardTimestamp(a.updated_at || a.created_at);
  var bTime = _boardTimestamp(b.updated_at || b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return bTime - aTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardNewestCompare(a, b);
}

function _boardOldestCompare(a, b) {
  var aTime = _boardTimestamp(a.created_at);
  var bTime = _boardTimestamp(b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return aTime - bTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardManualCompare(a, b);
}

function _boardDueSoonestCompare(a, b) {
  var aTime = _boardTimestamp(a.scheduled_at);
  var bTime = _boardTimestamp(b.scheduled_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return aTime - bTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardNewestCompare(a, b);
}

function _boardCompareLaneTasks(a, b, lane) {
  if (_boardQuickView === 'recent') return _boardNewestCompare(a, b);
  if (_boardQuickView === 'touched') return _boardRecentlyTouchedCompare(a, b);
  var mode = _boardLaneSortMode(lane);
  if (mode === 'newest') return _boardNewestCompare(a, b);
  if (mode === 'oldest') return _boardOldestCompare(a, b);
  if (mode === 'due') return _boardDueSoonestCompare(a, b);
  return _boardManualCompare(a, b);
}

function _boardLanes() {
  return (state && state.board_lanes) || [];
}

function _boardTasks() {
  return (state && state.board_tasks) || {};
}

function _boardGroupTaskCount() {
  var all = _boardTasks();
  if (!_boardFilterByGroup) return Object.keys(all).length;
  var group = _currentGroup();
  var count = 0;
  for (var id in all) {
    if (!group || all[id].group === group) count += 1;
  }
  return count;
}

function _boardIsArchived(task) {
  return !!(task && task.labels && task.labels.indexOf(_boardArchiveLabel) >= 0);
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
  var filtered = {};
  for (var taskId in out) {
    if (!_boardIsArchived(out[taskId])) filtered[taskId] = out[taskId];
  }
  return filtered;
}

function _boardArchivedCount() {
  var tasks = _boardScopedTasks(true);
  var count = 0;
  for (var id in tasks) {
    if (_boardIsArchived(tasks[id])) count++;
  }
  return count;
}

function _boardTaskTimestampMs(task) {
  if (!task) return 0;
  var iso = task.updated_at || task.created_at || '';
  if (!iso) return 0;
  var stamp = new Date(iso).getTime();
  return isNaN(stamp) ? 0 : stamp;
}

function _boardTaskScheduleMeta(task, nowMs) {
  if (!task || !task.scheduled_at) return null;
  var when = _boardTimestamp(task.scheduled_at);
  var formatted = _schedFormatTime(task.scheduled_at) || task.scheduled_at;
  if (Number.isNaN(when)) {
    return {
      className: 'board-card-scheduled',
      label: 'Due ' + formatted,
    };
  }
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffMs = when - current;
  if (task.lane !== 'Done' && diffMs < 0) {
    return {
      className: 'board-card-scheduled board-card-overdue',
      label: 'Overdue ' + formatted,
    };
  }
  if (task.lane !== 'Done' && diffMs <= 86400000) {
    return {
      className: 'board-card-scheduled board-card-due-soon',
      label: 'Due ' + formatted,
    };
  }
  return {
    className: 'board-card-scheduled',
    label: (task.lane === 'Done' ? 'Due ' : 'Scheduled ') + formatted,
  };
}

function _boardLaneEntryTimestampMs(task) {
  if (!task) return 0;
  var iso = task.lane_entered_at || task.created_at || '';
  if (!iso) return 0;
  var stamp = new Date(iso).getTime();
  return isNaN(stamp) ? 0 : stamp;
}

function _boardRelativeAgeText(diffSec) {
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
  return Math.floor(diffSec / 86400) + 'd ago';
}

function _boardLaneEntryText(task, nowMs) {
  var enteredAt = _boardLaneEntryTimestampMs(task);
  if (!enteredAt) return '';
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffSec = Math.max(0, Math.floor((current - enteredAt) / 1000));
  return '[' + _boardRelativeAgeText(diffSec) + ']';
}

function _boardLaneEntryNextRefreshDelay(task, nowMs) {
  var enteredAt = _boardLaneEntryTimestampMs(task);
  if (!enteredAt) return 0;
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffMs = Math.max(0, current - enteredAt);
  if (diffMs < 60000) return 1000;
  if (diffMs < 3600000) return 60000 - (diffMs % 60000) || 60000;
  if (diffMs < 86400000) return 3600000 - (diffMs % 3600000) || 3600000;
  return 86400000 - (diffMs % 86400000) || 86400000;
}

function _boardClearLaneEntryRefresh() {
  if (!_boardLaneEntryRefreshTimer) return;
  if (window && typeof window.clearTimeout === 'function') {
    window.clearTimeout(_boardLaneEntryRefreshTimer);
  }
  _boardLaneEntryRefreshTimer = 0;
}

function _boardScheduleLaneEntryRefresh(delayMs) {
  if (_boardShowSchedules
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp !== 'board')
      || !(delayMs > 0)) {
    _boardClearLaneEntryRefresh();
    return;
  }
  if (!window || typeof window.setTimeout !== 'function') return;
  if (_boardLaneEntryRefreshTimer && typeof window.clearTimeout === 'function') {
    window.clearTimeout(_boardLaneEntryRefreshTimer);
  }
  _boardLaneEntryRefreshTimer = window.setTimeout(function() {
    _boardLaneEntryRefreshTimer = 0;
    if (typeof _activePanelApp !== 'undefined' && _activePanelApp !== 'board') return;
    renderBoard();
  }, Math.max(1000, delayMs));
}

function _boardVisibleLaneEntryRefreshDelay(rootTasks, childrenOf, renderCap) {
  var nextDelay = 0;

  function visit(task, depth) {
    var delay = _boardLaneEntryNextRefreshDelay(task);
    if (delay > 0 && (!nextDelay || delay < nextDelay)) nextDelay = delay;
    var children = childrenOf[task.id] || [];
    if (!children.length) return;
    if (depth === 0 && _boardCollapsedTasks[task.id]) return;
    for (var i = 0; i < children.length; i++) {
      visit(children[i], depth + 1);
    }
  }

  for (var i = 0; i < renderCap; i++) {
    visit(rootTasks[i], 0);
  }
  return nextDelay;
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
    var labels = (task.labels || []).filter(function(label) {
      return label !== _boardArchiveLabel;
    });
    if (archived) labels.push(_boardArchiveLabel);
    task.labels = labels;
    send({ cmd: 'board_update_task', id: task.id, labels: labels });
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

function _boardStaleDoneTaskIds() {
  var tasks = _boardScopedTasks(false);
  var cutoff = Date.now() - (_boardArchiveStaleDays * 24 * 60 * 60 * 1000);
  var ids = [];
  for (var id in tasks) {
    var task = tasks[id];
    if (task.lane !== 'Done') continue;
    var stamp = _boardTaskTimestampMs(task);
    if (stamp && stamp <= cutoff) ids.push(id);
  }
  ids.sort(function(a, b) {
    return _boardTaskTimestampMs(tasks[a]) - _boardTaskTimestampMs(tasks[b]);
  });
  return ids;
}

/** Return visible tasks, optionally filtered to the current group. */
function _boardVisibleTasks() {
  _boardSyncFiltersForCurrentGroup();
  var out = _boardScopedTasks(_boardShowArchived);
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
      var parts = [t.task, t.description, t.slug, t.action_name, t.agent_id];
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

function _boardTasksInLane(lane) {
  var tasks = _boardVisibleTasks();
  var arr = [];
  for (var id in tasks) {
    if (tasks[id].lane === lane) arr.push(tasks[id]);
  }
  arr.sort(function(a, b) { return _boardCompareLaneTasks(a, b, lane); });
  return arr;
}

function _boardLaneCount(lane) {
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

function _boardSummarizeNames(values, formatter) {
  if (!values || !values.length) return '';
  var shown = values.slice(0, 2).map(function(value) {
    return formatter ? formatter(value) : value;
  });
  var text = shown.join(', ');
  if (values.length > 2) text += ' +' + (values.length - 2);
  return text;
}

function _boardFilterSummaryText() {
  var parts = [];
  if (_boardQuickView) parts.push(_boardQuickViewLabel(_boardQuickView));
  if (_boardSearchQuery) parts.push('Search "' + _boardSearchQuery + '"');
  if (_boardFilterLabels.length) {
    parts.push('Labels ' + _boardSummarizeNames(_boardFilterLabels));
  }
  if (_boardFilterActions.length) {
    parts.push('Actions ' + _boardSummarizeNames(_boardFilterActions));
  }
  if (_boardFilterAgents.length) {
    parts.push('Agents ' + _boardSummarizeNames(_boardFilterAgents, function(agentId) {
      return _boardAgentName(agentId) || agentId;
    }));
  }
  if (_boardFilterHealth.length) {
    parts.push('Health ' + _boardSummarizeNames(_boardFilterHealth, function(stateName) {
      return _boardHealthDisplayName(stateName);
    }));
  }
  return parts.length ? parts.join(' · ') : 'No active filters';
}

function _renderBoardDisplayControls() {
  if (_boardShowSchedules || !_boardSelectedLane) return '';
  var sortMode = _boardLaneSortMode(_boardSelectedLane);
  var densityMode = _boardCardDensityMode();
  var archivedCount = _boardArchivedCount();
  var html = '<div class="board-view-menu-section">';
  html += '<label class="board-display-label" for="board-lane-sort-select">Sort</label>';
  html += '<select class="board-display-select" id="board-lane-sort-select"'
    + ' onchange="boardSetLaneSort(this.value)">';
  html += '<option value="manual"' + (sortMode === 'manual' ? ' selected' : '') + '>Manual</option>';
  html += '<option value="newest"' + (sortMode === 'newest' ? ' selected' : '') + '>Newest</option>';
  html += '<option value="oldest"' + (sortMode === 'oldest' ? ' selected' : '') + '>Oldest</option>';
  html += '<option value="due"' + (sortMode === 'due' ? ' selected' : '') + '>Due Soonest</option>';
  html += '</select>';
  html += '</div>';
  html += '<div class="board-view-menu-section">';
  html += '<label class="board-display-label" for="board-card-density-select">Density</label>';
  html += '<select class="board-display-select" id="board-card-density-select"'
    + ' onchange="boardSetCardDensity(this.value)">';
  html += '<option value="compact"' + (densityMode === 'compact' ? ' selected' : '') + '>Compact</option>';
  html += '<option value="normal"' + (densityMode === 'normal' ? ' selected' : '') + '>Normal</option>';
  html += '<option value="detailed"' + (densityMode === 'detailed' ? ' selected' : '') + '>Detailed</option>';
  html += '</select>';
  html += '</div>';
  html += '<button class="board-view-menu-toggle' + (_boardShowArchived ? ' active' : '') + '"'
    + ' onclick="boardToggleArchived()">'
    + (_boardShowArchived ? 'Hide archived' : 'Show archived')
    + (archivedCount ? ' <span class="board-filter-btn-count">' + archivedCount + '</span>' : '')
    + '</button>';
  return html;
}

function boardToggleViewMenu() {
  if (_boardViewMenuOpen) {
    _boardCloseViewMenu();
    renderBoard();
    return;
  }
  _boardCloseFilterDropdown();
  _boardOpenViewMenu();
  renderBoard();
}

function _boardOpenViewMenu() {
  var wrap = document.getElementById('board-view-menu-wrap');
  if (!wrap) return;
  var btn = wrap.querySelector('.board-filter-btn');
  if (!btn || typeof btn.getBoundingClientRect !== 'function') return;
  var rect = btn.getBoundingClientRect();

  _boardCloseViewMenu();
  _boardViewMenuOpen = true;

  var menu = document.createElement('div');
  menu.className = 'board-view-menu';
  menu.id = 'board-view-menu-active';
  menu.style.position = 'fixed';
  menu.style.top = (rect.bottom + 2) + 'px';
  menu.style.left = rect.left + 'px';
  menu.innerHTML = _renderBoardDisplayControls();
  document.body.appendChild(menu);

  requestAnimationFrame(function() {
    if (typeof menu.getBoundingClientRect !== 'function') return;
    var menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) {
      menu.style.left = Math.max(4, window.innerWidth - menuRect.width - 4) + 'px';
    }
    if (menuRect.bottom > window.innerHeight) {
      menu.style.top = Math.max(4, rect.top - menuRect.height - 2) + 'px';
    }
  });

  var handler = function(e) {
    var target = e && e.target;
    var insideMenu = !!(menu && typeof menu.contains === 'function' && menu.contains(target));
    var insideWrap = !!(wrap && typeof wrap.contains === 'function' && wrap.contains(target));
    if (!insideMenu && !insideWrap) {
      _boardCloseViewMenu();
      renderBoard();
    }
  };

  setTimeout(function() {
    if (document && typeof document.addEventListener === 'function') {
      document.addEventListener('mousedown', handler, true);
    }
  }, 0);

  _boardViewMenuCleanup = function() {
    if (document && typeof document.removeEventListener === 'function') {
      document.removeEventListener('mousedown', handler, true);
    }
    if (menu && menu.parentNode) menu.remove();
    _boardViewMenuCleanup = null;
  };
}

function _boardCloseViewMenu() {
  _boardViewMenuOpen = false;
  if (_boardViewMenuCleanup) _boardViewMenuCleanup();
}

function _boardLanePoolTasks(lane) {
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

function _boardTaskIsFutureScheduled(task) {
  if (!task || !task.scheduled_at) return false;
  var when = _boardTimestamp(task.scheduled_at);
  if (Number.isNaN(when)) return false;
  return when > Date.now();
}

function _boardCacheDispatchActionList(msg) {
  if (!msg || !msg.group) return;
  _boardEligibilityActionsByGroup[msg.group] = msg.actions || [];
  if (_boardEligibilityActionWaiting && msg.group === (_currentGroup() || '')) {
    _boardEligibilityActionWaiting = false;
  }
}

function _boardCacheDispatchTemplateList(msg) {
  if (!msg || !msg.group) return;
  _boardEligibilityTemplatesByGroup[msg.group] = msg.templates || [];
  if (_boardEligibilityTemplateWaiting
      && msg.group === (_currentGroup() || '')) {
    _boardEligibilityTemplateWaiting = false;
  }
}

function _boardHandleEligibilityActionList(msg) {
  _boardCacheDispatchActionList(msg);
  _boardEligibilityActionWaiting = false;
  renderBoard();
}

function _boardHandleEligibilityTemplateList(msg) {
  _boardCacheDispatchTemplateList(msg);
  _boardEligibilityTemplateWaiting = false;
  renderBoard();
}

function _boardEnsureDispatchEligibilityRefs(group) {
  if (!group || typeof send !== 'function') return;
  var tasks = _boardScopedTasks(_boardShowArchived);
  var needsActions = false;
  var needsTemplates = false;
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group) continue;
    if (task.action_name) needsActions = true;
    if (task.agent_template) needsTemplates = true;
  }
  if (needsActions && !_boardEligibilityActionsByGroup[group]
      && !_boardEligibilityActionWaiting) {
    _boardEligibilityActionWaiting = true;
    send({ cmd: 'list_actions', group: group });
  }
  if (needsTemplates && !_boardEligibilityTemplatesByGroup[group]
      && !_boardEligibilityTemplateWaiting) {
    _boardEligibilityTemplateWaiting = true;
    send({ cmd: 'list_templates', group: group });
  }
}

function _boardUnmetDependencyNames(task) {
  if (!task || !Array.isArray(task.depends_on)) return [];
  var tasks = _boardTasks();
  var names = [];
  for (var i = 0; i < task.depends_on.length; i++) {
    var dep = tasks[task.depends_on[i]];
    if (dep && dep.lane !== 'Done') names.push(dep.task || dep.id);
  }
  return names;
}

function _boardTaskHasMissingAction(task) {
  if (!task || !task.action_name) return false;
  var group = task.group || _currentGroup() || '';
  var actions = _boardEligibilityActionsByGroup[group];
  if (!actions) return false;
  for (var i = 0; i < actions.length; i++) {
    if (actions[i] && actions[i].name === task.action_name) return false;
  }
  return true;
}

function _boardTaskHasMissingTemplate(task) {
  if (!task || !task.agent_template) return false;
  var group = task.group || _currentGroup() || '';
  var templates = _boardEligibilityTemplatesByGroup[group];
  if (!templates) return false;
  for (var i = 0; i < templates.length; i++) {
    if (templates[i] && templates[i].name === task.agent_template) return false;
  }
  return true;
}

function _boardTaskNeedsEligibilityRefs(task) {
  if (!task) return false;
  var group = task.group || _currentGroup() || '';
  return !!(
    (task.action_name && !_boardEligibilityActionsByGroup[group])
    || (task.agent_template && !_boardEligibilityTemplatesByGroup[group])
  );
}

function _boardTaskIsDispatchBlocked(task) {
  return _boardDepsBlocked(task)
    || !!(task.labels && task.labels.indexOf('loom:blocked') >= 0);
}

function _boardTaskDispatchEligibility(task) {
  if (!task) return null;
  if (task.lane === 'To Do' && task.agent_id) {
    return {
      className: 'board-card-dispatch board-card-dispatch-queued',
      label: 'Queued',
      title: 'Already queued for dispatch',
    };
  }
  if (task.lane !== 'Backlog') return null;
  if (_boardTaskNeedsEligibilityRefs(task)) {
    return {
      className: 'board-card-dispatch board-card-dispatch-checking',
      label: 'Checking refs',
      title: 'Loading action/template availability',
    };
  }
  var unmetDeps = _boardUnmetDependencyNames(task);
  if (unmetDeps.length) {
    var preview = unmetDeps.slice(0, 2).join(', ');
    if (unmetDeps.length > 2) preview += ', +' + (unmetDeps.length - 2);
    return {
      className: 'board-card-dispatch board-card-dispatch-blocked',
      label: 'Blocked by deps',
      title: 'Waiting on: ' + preview,
    };
  }
  if (task.labels && task.labels.indexOf('loom:blocked') >= 0) {
    return {
      className: 'board-card-dispatch board-card-dispatch-blocked',
      label: 'Blocked',
      title: 'Task is explicitly blocked',
    };
  }
  if (_boardTaskIsFutureScheduled(task)) {
    return {
      className: 'board-card-dispatch board-card-dispatch-scheduled',
      label: 'Scheduled later',
      title: 'Dispatch window opens ' + _schedFormatTime(task.scheduled_at),
    };
  }
  var missingAction = _boardTaskHasMissingAction(task);
  var missingTemplate = _boardTaskHasMissingTemplate(task);
  if (missingAction || missingTemplate) {
    var missing = [];
    if (missingAction) missing.push('action');
    if (missingTemplate) missing.push('template');
    return {
      className: 'board-card-dispatch board-card-dispatch-warning',
      label: missing.length === 2 ? 'Missing refs' : 'Missing ' + missing[0],
      title: missingAction && missingTemplate
        ? 'Missing action "' + task.action_name + '" and template "' + task.agent_template + '"'
        : missingAction
          ? 'Missing action "' + task.action_name + '"'
          : 'Missing template "' + task.agent_template + '"',
    };
  }
  return {
    className: 'board-card-dispatch board-card-dispatch-ready',
    label: 'Ready',
    title: 'Ready to dispatch',
  };
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
          ? [{ label: '+ Task', onclick: 'boardStartAddTask()' }]
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
          ? [{ label: '+ Task', onclick: 'boardStartAddTask()' }]
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
    actions.push({ label: '+ Task', onclick: 'boardStartAddTask()' });
  }
  return {
    title: 'No tasks in ' + lane,
    body: lane === 'Done'
      ? 'Nothing has landed here yet. Complete work in another lane to fill this view.'
      : 'Add a task here or move existing work into this lane.',
    actions: actions,
  };
}

function _boardBacklogDispatchNote(rootTasks) {
  if (_boardSelectedLane !== 'Backlog' || !rootTasks.length) return null;
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

/** Collect all labels with counts (before search/label/action filters). */
function _boardAllLabelCounts() {
  var pool = _boardScopedTasks(_boardShowArchived);
  var counts = {};
  for (var id in pool) {
    var t = pool[id];
    if (t.labels) {
      for (var i = 0; i < t.labels.length; i++) {
        counts[t.labels[i]] = (counts[t.labels[i]] || 0) + 1;
      }
    }
  }
  return counts;
}

/** Collect all action names with counts from tasks. */
function _boardAllActionCounts() {
  var pool = _boardScopedTasks(_boardShowArchived);
  var counts = {};
  for (var id in pool) {
    var t = pool[id];
    if (t.action_name) {
      counts[t.action_name] = (counts[t.action_name] || 0) + 1;
    }
  }
  return counts;
}

/** Collect all agent IDs with counts from tasks. */
function _boardAllAgentCounts() {
  var pool = _boardScopedTasks(_boardShowArchived);
  var counts = {};
  for (var id in pool) {
    var t = pool[id];
    if (t.agent_id) {
      counts[t.agent_id] = (counts[t.agent_id] || 0) + 1;
    }
  }
  return counts;
}

/** Collect all unhealthy task health states with counts from tasks. */
function _boardAllHealthCounts() {
  var all = _boardTasks();
  var pool = {};
  if (_boardFilterByGroup) {
    var grp = _currentGroup();
    if (grp) {
      for (var id in all) { if (all[id].group === grp) pool[id] = all[id]; }
    } else { pool = all; }
  } else { pool = all; }
  var counts = {};
  for (var hi = 0; hi < _boardHealthOrder.length; hi++) {
    counts[_boardHealthOrder[hi]] = 0;
  }
  for (var id in pool) {
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

function _boardTaskHealthState(task) {
  if (!task || !task.health_state) return 'healthy';
  return task.health_state;
}

function _boardVerificationState(task) {
  if (!task || !task.verification_state) return '';
  return task.verification_state;
}

function _boardVerificationMode(task) {
  if (!task || !task.verification_mode) return '';
  return task.verification_mode;
}

function _boardVerificationSummary(task) {
  return (task && task.verification_summary) || {};
}

function _boardVerificationLabel(task) {
  var stateName = _boardVerificationState(task);
  if (!stateName) return '';
  if (stateName === 'pending') return 'Verify pending';
  if (stateName === 'attempted') return 'Verify attempted';
  if (stateName === 'passed') return 'Verified';
  if (stateName === 'failed') return 'Verify failed';
  return stateName;
}

function _boardVerificationPreview(task) {
  if (!task) return '';
  var summary = _boardVerificationSummary(task);
  if (summary.human_validation_pending) {
    return 'Needs human validation: ' + summary.human_validation_pending;
  }
  if (task.verification_notes) return task.verification_notes;
  if (summary.tests_run) return 'Tests: ' + summary.tests_run;
  return '';
}

function _boardHealthDisplayName(stateName) {
  return _boardHealthLabels[stateName] || stateName;
}

function _boardHealthTitle(task) {
  if (!task) return '';
  var stateName = _boardTaskHealthState(task);
  if (stateName === 'healthy') return '';
  var parts = [_boardHealthDisplayName(stateName)];
  var details = task.health_details || {};
  if (details.aggregate && details.source_task_title) {
    parts.push('via ' + details.source_task_title);
  }
  if (details.silence_secs) {
    parts.push('no progress for ' + _boardFormatDuration(details.silence_secs));
  } else if (details.reasons && details.reasons.length) {
    var reasonText = _boardHealthReasonLabels[details.reasons[0]] || details.reasons[0];
    parts.push(reasonText);
  }
  if (task.health_since) {
    parts.push('since ' + new Date(task.health_since).toLocaleString());
  }
  return parts.join(' | ');
}

function _boardFormatDuration(seconds) {
  seconds = +seconds || 0;
  if (seconds <= 0) return '0m';
  var minutes = Math.max(1, Math.floor(seconds / 60));
  if (minutes < 60) return minutes + 'm';
  var hours = Math.floor(minutes / 60);
  var rem = minutes % 60;
  return rem ? (hours + 'h ' + rem + 'm') : (hours + 'h');
}

function _boardAgentStatus(agentId) {
  if (!agentId || !state || !state.agents) return '';
  var a = state.agents[agentId];
  if (!a) return '';
  return agentStatusClass ? agentStatusClass(a) : '';
}

function _boardAgentName(agentId) {
  if (!agentId || !state || !state.agents) return '';
  var a = state.agents[agentId];
  return a ? a.name : '';
}

function _boardDepsBlocked(t) {
  if (!t.depends_on || !t.depends_on.length) return false;
  var tasks = _boardTasks();
  for (var i = 0; i < t.depends_on.length; i++) {
    var dep = tasks[t.depends_on[i]];
    if (dep && dep.lane !== 'Done') return true;
  }
  return false;
}

function _boardActiveDependentNames(task) {
  if (!task || !task.id || task.lane === 'Done') return [];
  var tasks = _boardTasks();
  var names = [];
  for (var id in tasks) {
    var candidate = tasks[id];
    if (!candidate || candidate.lane === 'Done') continue;
    if (Array.isArray(candidate.depends_on)
        && candidate.depends_on.indexOf(task.id) >= 0) {
      names.push(candidate.task || candidate.id);
    }
  }
  return names;
}

function _boardTaskDependencyBadges(task) {
  if (!task) return [];
  var badges = [];
  var deps = Array.isArray(task.depends_on) ? task.depends_on : [];
  var unmetDeps = _boardUnmetDependencyNames(task);
  if (deps.length) {
    if (unmetDeps.length) {
      var unmetPreview = unmetDeps.slice(0, 2).join(', ');
      if (unmetDeps.length > 2) unmetPreview += ', +' + (unmetDeps.length - 2);
      badges.push({
        className: 'board-card-dependency board-card-dependency-blocked',
        label: 'Blocked by ' + unmetDeps.length,
        title: 'Waiting on: ' + unmetPreview,
      });
    } else {
      badges.push({
        className: 'board-card-dependency board-card-dependency-ready',
        label: 'Deps ' + deps.length,
        title: deps.length + ' dependenc' + (deps.length === 1 ? 'y is' : 'ies are') + ' satisfied',
      });
    }
  }
  var activeDependents = _boardActiveDependentNames(task);
  if (activeDependents.length) {
    var blockingPreview = activeDependents.slice(0, 2).join(', ');
    if (activeDependents.length > 2) {
      blockingPreview += ', +' + (activeDependents.length - 2);
    }
    badges.push({
      className: 'board-card-dependency board-card-dependency-blocking',
      label: 'Blocks ' + activeDependents.length,
      title: 'Blocking: ' + blockingPreview,
    });
  }
  return badges;
}

function _boardTaskPriority(task) {
  var labels = (task && task.labels) || [];
  for (var i = 0; i < labels.length; i++) {
    var match = String(labels[i]).match(/^priority:(low|medium|high)$/);
    if (match) return match[1];
  }
  return '';
}

function _boardPriorityText(priority) {
  if (priority === 'high') return 'High';
  if (priority === 'medium') return 'Medium';
  if (priority === 'low') return 'Low';
  return '';
}

function _boardTaskUserLabels(task) {
  return ((task && task.labels) || []).filter(function(label) {
    return !isSystemLabel(label) && !/^priority:/.test(label);
  });
}

function _boardScheduledInputValue(isoValue) {
  if (!isoValue) return '';
  try {
    var d = new Date(isoValue);
    return isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 16);
  } catch (e) {
    return '';
  }
}

function _boardSetQuickEdit(taskId, kind) {
  var task = _boardTasks()[taskId];
  if (!task) return;
  if (_boardQuickEditTask === taskId && _boardQuickEditKind === kind) {
    _boardQuickEditTask = '';
    _boardQuickEditKind = '';
    _boardQuickLabelDraft = '';
    _boardQuickDueDraft = '';
    renderBoard();
    return;
  }
  _boardQuickEditTask = taskId;
  _boardQuickEditKind = kind;
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = _boardScheduledInputValue(task.scheduled_at);
  renderBoard();
  requestAnimationFrame(function() {
    var inputId = '';
    if (kind === 'labels') inputId = 'board-quick-label-input-' + taskId;
    else if (kind === 'due') inputId = 'board-quick-due-input-' + taskId;
    var el = inputId ? document.getElementById(inputId) : null;
    if (el) el.focus();
  });
}

function _boardCloseQuickEdit() {
  _boardQuickEditTask = '';
  _boardQuickEditKind = '';
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = '';
  renderBoard();
}

function _boardQuickEditAgents(task) {
  var out = [];
  if (!state || !state.agents) return out;
  for (var id in state.agents) {
    var agent = state.agents[id];
    if (agent.cell_type !== 'agent') continue;
    if (task.group && agent.group !== task.group) continue;
    out.push(agent);
  }
  out.sort(function(a, b) { return (a.name || '').localeCompare(b.name || ''); });
  return out;
}

function _renderBoardQuickEdit(t) {
  var priority = _boardTaskPriority(t);
  var labels = _boardTaskUserLabels(t);
  var assignee = t.agent_id ? (_boardAgentName(t.agent_id) || 'Assigned') : 'Assignee';
  var dueLabel = t.scheduled_at ? ('Due ' + _schedFormatTime(t.scheduled_at)) : 'Due';
  var priorityLabel = priority ? ('Priority ' + _boardPriorityText(priority)) : 'Priority';
  var labelsLabel = labels.length ? ('Labels ' + labels.length) : 'Labels';
  var open = _boardQuickEditTask === t.id ? _boardQuickEditKind : '';
  var html = '<div class="board-card-quick-controls">';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'labels\')">' + esc(labelsLabel) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'assignee\')">' + esc(assignee) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'due\')">' + esc(dueLabel) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'priority\')">' + esc(priorityLabel) + '</button>';
  html += '</div>';
  if (!open) return html;

  html += '<div class="board-card-quick-editor" onclick="event.stopPropagation()">';
  if (open === 'labels') {
    if (labels.length) {
      for (var i = 0; i < labels.length; i++) {
        var label = labels[i];
        html += '<span class="board-card-quick-chip">' + esc(label)
          + '<button class="board-card-quick-chip-remove" onclick="event.stopPropagation();boardQuickRemoveLabel(\'' + t.id + '\',\'' + esc(label).replace(/'/g, "\\'") + '\')">&times;</button></span>';
      }
    } else {
      html += '<span class="board-card-quick-empty">No labels</span>';
    }
    html += '<input type="text" id="board-quick-label-input-' + t.id + '" class="board-card-quick-input"'
      + ' placeholder="Add label" value="' + esc(_boardQuickLabelDraft) + '"'
      + ' oninput="boardQuickLabelInput(this.value)"'
      + ' onkeydown="boardQuickLabelKeydown(event,\'' + t.id + '\')">';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickAddLabel(\'' + t.id + '\')">Add</button>';
  } else if (open === 'assignee') {
    html += '<button class="board-card-quick-action' + (!t.agent_id ? ' active' : '') + '"'
      + ' onclick="event.stopPropagation();boardQuickAssignAgent(\'' + t.id + '\',\'\')">Unassigned</button>';
    var agents = _boardQuickEditAgents(t);
    if (!agents.length) {
      html += '<span class="board-card-quick-empty">No agents</span>';
    } else {
      for (var ai = 0; ai < agents.length; ai++) {
        var agent = agents[ai];
        html += '<button class="board-card-quick-action' + (agent.id === t.agent_id ? ' active' : '') + '"'
          + ' onclick="event.stopPropagation();boardQuickAssignAgent(\'' + t.id + '\',\'' + agent.id + '\')">' + esc(agent.name || agent.id) + '</button>';
      }
    }
  } else if (open === 'due') {
    html += '<input type="datetime-local" id="board-quick-due-input-' + t.id + '" class="board-card-quick-input"'
      + ' value="' + esc(_boardQuickDueDraft) + '"'
      + ' oninput="boardQuickDueInput(this.value)"'
      + ' onkeydown="boardQuickDueKeydown(event,\'' + t.id + '\')">';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickSaveDue(\'' + t.id + '\')">Set</button>';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickClearDue(\'' + t.id + '\')">Clear</button>';
  } else if (open === 'priority') {
    var levels = ['', 'low', 'medium', 'high'];
    for (var pi = 0; pi < levels.length; pi++) {
      var level = levels[pi];
      var text = level ? _boardPriorityText(level) : 'None';
      html += '<button class="board-card-quick-action' + (level === priority ? ' active' : '') + '"'
        + ' onclick="event.stopPropagation();boardQuickSetPriority(\'' + t.id + '\',\'' + level + '\')">' + esc(text) + '</button>';
    }
  }
  html += '</div>';
  return html;
}

function _renderBoardArchiveSuggestion() {
  if (_boardShowArchived || _boardSelectedLane !== 'Done') return '';
  var staleIds = _boardStaleDoneTaskIds();
  if (!staleIds.length) return '';
  return '<div class="board-archive-suggestion">'
    + '<span class="board-archive-suggestion-copy">'
    + staleIds.length + ' completed task' + (staleIds.length === 1 ? '' : 's')
    + ' inactive for ' + _boardArchiveStaleDays + '+ days</span>'
    + '<button class="board-selection-btn" onclick="boardArchiveSuggestedDone()">Archive stale</button>'
    + '</div>';
}

/* ---- Card rendering ------------------------------------------------- */

function _renderBoardCard(t, childrenOf, depth) {
  var isSubordinate = depth > 0;
  var hasChildren = childrenOf[t.id] && childrenOf[t.id].length > 0;
  var showExecutionState = isSubordinate || !hasChildren;
  var isCollapsed = _boardCollapsedTasks[t.id];
  var isDone = t.lane === 'Done';
  var dotClass = t.agent_id ? _boardAgentStatus(t.agent_id) : '';
  var focused = t.id === _boardFocusedTask ? ' focused' : '';
  var selected = _boardSelectedTasks[t.id] ? ' board-card-selected' : '';
  var quickOpen = _boardQuickEditTask === t.id ? ' board-card-quick-open' : '';
  var subClass = isSubordinate ? ' board-card-subordinate' : '';
  var doneClass = (isSubordinate && isDone) ? ' board-card-done' : '';
  var cardHtml = '<div class="board-card' + focused + selected + quickOpen + subClass + doneClass + '"'
    + ' data-task-id="' + t.id + '"'
    + ' draggable="true"'
    + ' ondragstart="boardCardDragStart(event,\'' + t.id + '\')"'
    + ' ondragend="boardCardDragEnd(event)"'
    + ' ondragover="boardCardDragOver(event)"'
    + ' ondragleave="boardCardDragLeave(event)"'
    + ' ondrop="boardCardDrop(event)"'
    + ' onclick="boardFocusTask(\'' + t.id + '\', event)"'
    + ' oncontextmenu="boardCardMenu(event,\'' + t.id + '\')"'
    + ' ondblclick="openEditTask(\'' + t.id + '\')">';
  // Collapse toggle for cards with children
  if (hasChildren && !isSubordinate) {
    cardHtml += '<div class="board-card-collapse-btn" onclick="event.stopPropagation();boardToggleTaskCollapse(\'' + t.id + '\')">'
      + (isCollapsed ? '&#9654;' : '&#9660;') + '</div>';
  } else {
    cardHtml += '<div class="board-card-dot ' + dotClass + '"></div>';
  }
  cardHtml += '<div class="board-card-info">';
  // Done checkmark for subordinate cards
  var titlePrefix = (isSubordinate && isDone) ? '&#10003; ' : '';
  var laneEntryText = _boardLaneEntryText(t);
  cardHtml += '<div class="board-card-heading">';
  cardHtml += '<div class="board-card-title">' + titlePrefix + formatCode(t.task || '') + '</div>';
  if (laneEntryText) {
    cardHtml += '<div class="board-card-lane-entered" title="Entered current lane ' + esc(laneEntryText.slice(1, -1)) + '">'
      + esc(laneEntryText) + '</div>';
  }
  cardHtml += '</div>';
  var meta = '';
  var dispatchEligibility = _boardTaskDispatchEligibility(t);
  if (dispatchEligibility) {
    meta += '<span class="board-card-label ' + esc(dispatchEligibility.className)
      + '" title="' + esc(dispatchEligibility.title) + '">'
      + esc(dispatchEligibility.label) + '</span>';
  }
  if (showExecutionState && t.status) meta += '<span class="board-card-label board-card-status">' + esc(t.status) + '</span>';
  if (showExecutionState && _boardTaskHealthState(t) !== 'healthy') {
    meta += '<span class="board-card-label board-card-health board-card-health-' + esc(_boardTaskHealthState(t))
      + '" title="' + esc(_boardHealthTitle(t)) + '">' + esc(_boardHealthDisplayName(_boardTaskHealthState(t))) + '</span>';
  }
  if (_boardVerificationState(t)) {
    meta += '<span class="board-card-label board-card-verification board-card-verification-' + esc(_boardVerificationState(t))
      + '">' + esc(_boardVerificationLabel(t)) + '</span>';
  }
  if (_boardVerificationMode(t)) {
    meta += '<span class="board-card-label board-card-verification-mode">' + esc(_boardVerificationMode(t)) + '</span>';
  }
  if (_boardHasActiveFilters() && t.lane) meta += '<span class="board-card-lane-badge">' + esc(t.lane) + '</span>';
  if (t.action_name) meta += '<span class="board-card-label board-card-template">' + esc(t.action_name) + '</span>';
  if (t.agent_template) meta += '<span class="board-card-label board-card-template">agent: ' + esc(t.agent_template) + '</span>';
  if (t.labels && t.labels.length) {
    var userLbls = [], sysLbls = [];
    var priority = _boardTaskPriority(t);
    for (var li = 0; li < t.labels.length; li++) {
      if (isSystemLabel(t.labels[li])) sysLbls.push(t.labels[li]);
      else if (!/^priority:/.test(t.labels[li])) userLbls.push(t.labels[li]);
    }
    if (priority) {
      meta += '<span class="board-card-label board-card-priority board-card-priority-' + esc(priority) + '">'
        + 'Priority: ' + esc(_boardPriorityText(priority)) + '</span>';
    }
    for (var li = 0; li < userLbls.length; li++) {
      var lc = labelColor(userLbls[li]);
      meta += '<span class="board-card-label" style="background:' + lc + '22;color:' + lc + '">' + esc(userLbls[li]) + '</span>';
    }
    for (var li = 0; li < sysLbls.length; li++) {
      var lb = sysLbls[li];
      var cls = 'board-card-label board-label-system';
      if (lb === 'loom:blocked') cls += ' board-label-blocked';
      else if (lb === 'loom:error') cls += ' board-label-error';
      meta += '<span class="' + cls + '">' + esc(displayLabel(lb)) + '</span>';
    }
  }
  var dependencyBadges = _boardTaskDependencyBadges(t);
  for (var dbi = 0; dbi < dependencyBadges.length; dbi++) {
    var depBadge = dependencyBadges[dbi];
    meta += '<span class="board-card-label ' + esc(depBadge.className)
      + '" title="' + esc(depBadge.title) + '">' + esc(depBadge.label) + '</span>';
  }
  var scheduleMeta = _boardTaskScheduleMeta(t);
  if (scheduleMeta) {
    meta += '<span class="board-card-label ' + esc(scheduleMeta.className)
      + '" title="' + esc(scheduleMeta.label) + '">' + esc(scheduleMeta.label) + '</span>';
  }
  var artifactCount = typeof _artifactCountForTask === 'function'
    ? _artifactCountForTask(t)
    : (((t.attachments || []).length) + ((t.artifacts || []).length));
  if (artifactCount) {
    meta += '<span class="board-card-label board-card-attachments"'
      + ' title="' + artifactCount + ' artifact' + (artifactCount === 1 ? '' : 's') + '"'
      + ' onclick="event.stopPropagation();openTaskArtifactBrowser(\'' + t.id + '\')">'
      + '&#x1F4CE; ' + artifactCount + '</span>';
  }
  if (t.provider || t.external_id) {
    var extLabel = (t.provider ? t.provider + ': ' : '') + (t.external_id || 'linked');
    meta += '<span class="board-card-label board-card-template" title="Linked external ticket">' + esc(extLabel) + '</span>';
  }
  if (t.external_url) {
    meta += '<a class="board-card-pr-link" href="' + esc(t.external_url)
      + '" onclick="event.stopPropagation();window.open(this.href);return false"'
      + ' title="' + esc(t.external_url) + '">&#x1F517;</a>';
  }
  if (meta) cardHtml += '<div class="board-card-meta">' + meta + '</div>';
  if (!isSubordinate) cardHtml += _renderBoardQuickEdit(t);
  // Pipeline chain indicator (only for subordinate cards)
  if (isSubordinate && t.parent_task_id) {
    var chainInfo = '↳ depth ' + (t.pipeline_depth || 0);
    if (t.labels && t.labels.indexOf('loom:human') >= 0) chainInfo += ' · awaiting human';
    cardHtml += '<div class="board-card-chain">' + chainInfo + '</div>';
  }
  if (t.agent_id) {
    var aName = _boardAgentName(t.agent_id);
    if (aName) {
      cardHtml += '<div class="board-card-agent" onclick="event.stopPropagation();boardFocusAgent(\'' + t.agent_id + '\')">'
        + '&#x1F916; ' + esc(aName) + '</div>';
    }
  }
  var verificationPreview = _boardVerificationPreview(t);
  if (verificationPreview) {
    cardHtml += '<div class="board-card-verification-note">' + esc(verificationPreview) + '</div>';
  }
  // Last activity message
  if (t.messages && t.messages.length) {
    var lastMsg = t.messages[t.messages.length - 1];
    var msgText = lastMsg.message || '';
    var msgClass = 'board-card-activity';
    if (lastMsg.action === 'done' || lastMsg.action === 'ready') msgClass += ' board-card-activity-done';
    else if (lastMsg.action === 'error') msgClass += ' board-card-activity-error';
    else if (lastMsg.action === 'blocked') msgClass += ' board-card-activity-blocked';
    cardHtml += '<div class="' + msgClass + ' board-card-activity-clickable" onclick="event.stopPropagation();showTaskMessages(\'' + t.id + '\')">' + esc(msgText) + '</div>';
  }
  cardHtml += '</div>';
  cardHtml += '<button class="board-card-menu-btn" onclick="event.stopPropagation();boardCardMenu(event,\'' + t.id + '\')" title="Actions">&#8942;</button>';
  cardHtml += '</div>';

  // Render children if expanded
  if (hasChildren && !isCollapsed) {
    var children = childrenOf[t.id];
    for (var ci = 0; ci < children.length; ci++) {
      cardHtml += _renderBoardCard(children[ci], childrenOf, depth + 1);
    }
  }
  return cardHtml;
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

function _boardAfterRenderLayout() {
  requestAnimationFrame(function() {
    var tabsEl = document.getElementById('board-lane-tabs');
    if (tabsEl) {
      // Attach scroll listener (re-attached each render since DOM is rebuilt)
      tabsEl.addEventListener('scroll', function() {
        _boardScrollLeft = tabsEl.scrollLeft;
        boardUpdateScrollArrows();
      });

      // Restore saved scroll position
      tabsEl.scrollLeft = _boardScrollLeft;

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

    // Restore cards scroll position + infinite scroll
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
        if (e.target === cardsEl && _boardSelectedCount() > 0) {
          boardClearSelection();
        }
      });
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

/* ---- Render --------------------------------------------------------- */

function renderBoard() {
  var panel = document.getElementById('panel-board');
  if (!panel) return;
  if (typeof _activePanelApp !== 'undefined' && _activePanelApp !== 'board') {
    _boardClearLaneEntryRefresh();
    return;
  }
  var panelState = _captureSurfaceState(panel);
  var skipRestoreFocus = _boardAddTaskFocus || _boardSaveViewFocus;
  _boardSyncFiltersForCurrentGroup();
  _boardHydrateSavedViews();
  _boardHydrateLaneSorts();
  _boardHydrateCardDensity();
  _boardEnsureDispatchEligibilityRefs(_currentGroup());

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

  var lanes = _boardLanes();
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

  // Search & filter toolbar
  var labelCounts = _boardAllLabelCounts();
  var actionCounts = _boardAllActionCounts();
  var agentCounts = _boardAllAgentCounts();
  var healthCounts = _boardAllHealthCounts();
  var archivedCount = _boardArchivedCount();
  var hasLabels = Object.keys(labelCounts).length > 0;
  var hasActions = Object.keys(actionCounts).length > 0;
  var hasAgents = Object.keys(agentCounts).length > 0;
  var hasHealth = Object.keys(healthCounts).length > 0;
  var savedViews = _boardCurrentGroupSavedViews();
  var hasSavedViews = savedViews.length > 0;
  var hasQuickViews = _boardGroupTaskCount() > 0 || _boardQuickView !== '';
  var currentViewSavable = !_boardIsDefaultFilterState(_boardCurrentViewState());
  var showToolbar = _boardGroupTaskCount() > 0
    || hasLabels || hasActions || hasAgents || hasHealth
    || _boardSearchQuery || _boardFilterLabels.length
    || _boardFilterActions.length || _boardFilterAgents.length
    || _boardFilterHealth.length
    || hasSavedViews || archivedCount || _boardShowArchived;

    if (showToolbar) {
    html += '<div class="board-search-bar">';
    html += '<input type="text" class="board-search-input" id="board-search-input"'
      + ' placeholder="Search tasks..." value="' + esc(_boardSearchQuery) + '"'
      + ' oninput="boardUpdateSearch(this.value)">';
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
    html += '</div>';

    if (hasQuickViews || hasSavedViews || currentViewSavable || _boardSavingView || !_boardShowSchedules) {
      html += '<div class="board-saved-views">';
      if (hasQuickViews) {
        html += '<button class="board-filter-btn'
          + (_boardQuickView === 'recent' ? ' active' : '')
          + '" onclick="boardApplyQuickView(\'recent\')">Recent</button>';
        html += '<button class="board-filter-btn'
          + (_boardQuickView === 'touched' ? ' active' : '')
          + '" onclick="boardApplyQuickView(\'touched\')">Recently Touched</button>';
      }
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
      if (!_boardShowSchedules && _boardSelectedLane) {
        html += '<div class="board-saved-views-spacer"></div>';
        html += '<div class="board-filter-btn-wrap" id="board-view-menu-wrap">';
        html += '<button class="board-filter-btn' + (_boardViewMenuOpen ? ' active' : '') + '"'
          + ' onclick="boardToggleViewMenu()">View &#9662;</button>';
        html += '</div>';
      }
      html += '</div>';
    }

    // Active filter chips
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
    var curCount = _boardLaneCount(_boardSelectedLane);
    if (curCount === 0) {
      for (var fi = 0; fi < lanes.length; fi++) {
        if (_boardLaneCount(lanes[fi]) > 0) {
          _boardSelectedLane = lanes[fi];
          break;
        }
      }
    }
  }

  _boardActivateViewState(_boardCurrentViewKey());

  // Lane tab bar
  html += '<div class="board-lane-bar">';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-left" onclick="boardScrollLanes(-1)" title="Scroll left">&#9664;</button>';
  html += '<div class="board-lane-tabs" id="board-lane-tabs">';
  for (var i = 0; i < lanes.length; i++) {
    var l = lanes[i];
    var cnt = _boardLaneCount(l);
    var cls = (!_boardShowSchedules && l === _boardSelectedLane) ? ' active' : '';
    if (filtersActive && cnt === 0) cls += ' dimmed';
    var escLane = esc(l).replace(/'/g, "\\'");
    html += '<button class="board-lane-tab' + cls + '"'
      + ' data-lane="' + esc(l) + '"'
      + ' onclick="boardSelectLane(\'' + escLane + '\')"'
      + ' ondragover="boardLaneTabDragOver(event)"'
      + ' ondragleave="boardLaneTabDragLeave(event)"'
      + ' ondrop="boardLaneTabDrop(event)">'
      + esc(l) + '<span class="lane-count">' + cnt + '</span>'
      + '</button>';
  }
  // Schedules tab (after lane tabs)
  var schedCount = _boardScheduleCount();
  html += '<button class="board-lane-tab board-lane-tab-schedules'
    + (_boardShowSchedules ? ' active' : '') + '"'
    + ' onclick="boardToggleSchedules()">'
    + 'Schedules' + (schedCount ? '<span class="lane-count">' + schedCount + '</span>' : '')
    + '</button>';

  html += '</div>';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-right" onclick="boardScrollLanes(1)" title="Scroll right">&#9654;</button>';
  html += '</div>';

  // Schedules view (replaces cards when active)
  if (_boardShowSchedules) {
    html += _renderSchedulesView();
    panel.innerHTML = html;
    _boardAfterRenderLayout();
    if (!skipRestoreFocus) _restoreSurfaceState(panel, panelState);
    return;
  }

  // Cards — always show tasks in the selected lane
  var tasks = _boardTasksInLane(_boardSelectedLane);
  var allTasks = _boardVisibleTasks();
  var childrenOf = {};  // parent_id → [tasks]
  for (var cid in allTasks) {
    var ct = allTasks[cid];
    if (ct.parent_task_id && allTasks[ct.parent_task_id]) {
      if (!childrenOf[ct.parent_task_id]) childrenOf[ct.parent_task_id] = [];
      childrenOf[ct.parent_task_id].push(ct);
    }
  }
  // Sort children by depth then created_at
  for (var pid in childrenOf) {
    childrenOf[pid].sort(function(a, b) {
      return (a.pipeline_depth - b.pipeline_depth) || (a.created_at || '').localeCompare(b.created_at || '');
    });
  }
  // Root tasks: in this lane, with no parent in the visible set
  var rootTasks = tasks.filter(function(t) {
    return !t.parent_task_id || !allTasks[t.parent_task_id];
  });

  html += '<div class="board-cards board-density-' + _boardCardDensityMode() + '" id="board-cards">';

  // Add task: inline input or button (at top)
  if (_boardAddingTask) {
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
    // Inline attachment chips (compact — no thumbnails)
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
    // Agent dropdown
    html += '<div class="board-add-dropdown" id="board-add-agent-wrap">';
    var agentLabel = _boardAddingTaskAgent ? _boardAgentName(_boardAddingTaskAgent) : 'No agent';
    html += '<button class="board-add-toolbar-btn" onmousedown="event.preventDefault();boardToggleAgentDropdown()">'
      + esc(agentLabel) + ' &#9662;</button>';
    html += '</div>';
    // Action dropdown
    var actionLabel = _boardAddingTaskAction || 'No action';
    html += '<button class="board-add-toolbar-btn" onmousedown="event.preventDefault();boardToggleActionList()">'
      + esc(actionLabel) + ' &#9662;</button>';
    // Submit
    html += '<button class="board-add-toolbar-btn board-add-submit-btn" onmousedown="event.preventDefault();boardSubmitAddTask()">Submit &#10132;</button>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
  } else {
    html += '<div class="board-add-task" onclick="boardStartAddTask()">';
    html += '<span>+ Add task</span>';
    html += '<button class="board-add-tpl-btn-idle" onclick="event.stopPropagation();boardToggleActionList()">From action &#9662;</button>';
    html += '</div>';
  }

  // Inline action list (shown below add-task)
  if (_boardActList !== null) {
    html += '<div class="board-tpl-list">';
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
  }

  html += _renderBoardArchiveSuggestion();
  var backlogDispatchNote = _boardBacklogDispatchNote(rootTasks);
  if (backlogDispatchNote) {
    html += _renderBoardMessageState(backlogDispatchNote, true);
  }

  // Task cards
  if (rootTasks.length === 0) {
    html += _renderBoardMessageState(
      _boardEmptyStateForLane(
        _boardSelectedLane,
        _boardLanePoolTasks(_boardSelectedLane),
        rootTasks,
        filtersActive,
      ),
      false,
    );
  }

  var renderCap = Math.min(rootTasks.length, _boardRenderLimit);
  for (var j = 0; j < renderCap; j++) {
    html += _renderBoardCard(rootTasks[j], childrenOf, 0);
  }
  _boardScheduleLaneEntryRefresh(
    _boardVisibleLaneEntryRefreshDelay(rootTasks, childrenOf, renderCap)
  );
  if (rootTasks.length > renderCap) {
    var remaining = rootTasks.length - renderCap;
    html += '<div class="board-load-more" onclick="boardLoadMore()">'
      + remaining + ' more task' + (remaining === 1 ? '' : 's') + ' — click or scroll to load</div>';
  }

  html += '</div>';

  // Selection bar
  html += _renderBoardSelectionBar();

  panel.innerHTML = html;

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

  _boardAfterRenderLayout();
  if (!skipRestoreFocus) _restoreSurfaceState(panel, panelState);
}

/* ---- Virtual scroll ------------------------------------------------- */

function boardLoadMore() {
  var tasks = _boardTasksInLane(_boardSelectedLane || _boardLanes()[0] || '');
  var allTasks = _boardVisibleTasks();
  var rootCount = tasks.filter(function(t) {
    return !t.parent_task_id || !allTasks[t.parent_task_id];
  }).length;
  if (_boardRenderLimit >= rootCount) return;
  _boardRenderLimit += 50;
  _boardSyncActiveViewState();
  renderBoard();
}

/* ---- Lane selection ------------------------------------------------- */

function boardSelectLane(lane) {
  if (!_boardShowSchedules && lane === _boardSelectedLane) return;
  _boardPrepareViewChange(true);
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
  _boardAddingTask = true;
  _boardAddTaskFocus = true;
  _boardFocusedTask = '';
  if (!_boardInlineDraftId) _boardInlineDraftId = _generateDraftId();
  renderBoard();
}

function boardCancelAddTask() {
  var el = document.getElementById('board-add-task-input');
  if (el) _boardAddingTaskDraft = el.value;
  // Keep open if there's a draft or attachments — user may be dragging files
  if (_boardAddingTaskDraft || _boardInlineAttachments.length) return;
  setTimeout(function() { _boardAddingTask = false; _boardTplList = null; renderBoard(); }, 150);
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
  _boardAddingTask = false;
  _boardAddingTaskDraft = '';
  var msg = { cmd: 'board_add_task', task: parsed.title, group: _currentGroup(), lane: _boardSelectedLane };
  if (_boardInlineDraftId) msg.id = _boardInlineDraftId;
  if (parsed.labels.length) msg.labels = parsed.labels;
  if (_boardAddingTaskAction) msg.action_name = _boardAddingTaskAction;
  if (_boardAddingTaskAgent) msg.agent_id = _boardAddingTaskAgent;
  if (_boardInlineAttachments.length) msg.attachments = _boardInlineAttachments.slice();
  _boardAddingTaskAction = '';
  _boardAddingTaskAgent = '';
  _boardInlineDraftId = '';
  _boardInlineAttachments = [];
  _boardTplList = null;
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

function boardAddTaskKeydown(e) {
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

function boardCardMenu(evt, taskId) {
  evt.preventDefault();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

  var menu = document.getElementById('ctx-menu');
  var lanes = _boardLanes();

  var isDerived = !!task.parent_task_id;
  var html = '';
  html += '<button onclick="event.stopPropagation();boardEditTask(\'' + taskId + '\')">Edit</button>';
  html += '<button onclick="event.stopPropagation();boardDuplicateTask(\'' + taskId + '\')">Duplicate</button>';
  html += '<button onclick="event.stopPropagation();boardCloneTask(\'' + taskId + '\')">Clone...</button>';

  html += '<div class="ctx-sep"></div>';

  // Dispatch (only from Backlog)
  if (task.lane === 'Backlog') {
    if (_boardDepsBlocked(task)) {
      html += '<button disabled title="Blocked by unmet dependencies">Dispatch (blocked)</button>';
    } else {
      html += '<button onclick="event.stopPropagation();boardDispatchTask(\'' + taskId + '\')">Dispatch...</button>';
    }
  }

  // Link/Unlink agent
  if (task.agent_id) {
    html += '<button onclick="boardUnlinkAgent(\'' + taskId + '\')">Unlink agent</button>';
  } else {
    html += '<button onclick="event.stopPropagation();boardLinkAgent(\'' + taskId + '\')">Link agent...</button>';
  }

  // Resolve (ask tasks with human label)
  if (task.labels && task.labels.indexOf('loom:human') >= 0 && task.lane !== 'Done') {
    html += '<button onclick="event.stopPropagation();boardOpenResolve(\'' + taskId + '\')">Resolve...</button>';
  }

  if (_boardIsArchived(task)) {
    html += '<button onclick="event.stopPropagation();boardRestoreTask(\'' + taskId + '\')">Restore</button>';
  } else if (task.lane === 'Done') {
    html += '<button onclick="event.stopPropagation();boardArchiveTask(\'' + taskId + '\')">Archive</button>';
  }

  // Preview prompt
  html += '<button onclick="boardPreviewPrompt(\'' + taskId + '\')">Preview prompt</button>';
  html += '<button onclick="openTaskArtifactBrowser(\'' + taskId + '\')">Artifacts...</button>';

  html += '<div class="ctx-sep"></div>';
  if (task.provider || task.external_id || task.external_url) {
    html += '<button onclick="event.stopPropagation();boardOpenExternal(\'' + taskId + '\')">Open external issue</button>';
    html += '<button onclick="event.stopPropagation();boardPushExternalStatus(\'' + taskId + '\')">Push status...</button>';
    html += '<button onclick="event.stopPropagation();boardPostExternalComment(\'' + taskId + '\')">Post comment...</button>';
    html += '<button onclick="event.stopPropagation();boardLinkExternal(\'' + taskId + '\')">Edit external link...</button>';
    html += '<button onclick="event.stopPropagation();boardClearExternal(\'' + taskId + '\')">Unlink external issue</button>';
  } else {
    html += '<button onclick="event.stopPropagation();boardLinkExternal(\'' + taskId + '\')">Link external issue...</button>';
  }

  // Detach from pipeline (derived tasks only)
  if (isDerived) {
    html += '<button onclick="boardDetachTask(\'' + taskId + '\')">Detach from pipeline</button>';
  }

  // Move to lane — only for non-derived tasks (pipeline manages derived tasks)
  if (!isDerived) {
    html += '<div class="ctx-sep"></div>';
    for (var i = 0; i < lanes.length; i++) {
      if (lanes[i] !== task.lane) {
        html += '<button onclick="boardMoveTaskToLane(\'' + taskId + '\',\''
          + esc(lanes[i]).replace(/'/g, "\\'") + '\')">Move to ' + esc(lanes[i]) + '</button>';
      }
    }
  }

  // Copy ID
  html += '<div class="ctx-sep"></div>';
  html += '<button onclick="event.stopPropagation();boardCopyTaskId(\'' + taskId + '\')">'
    + 'Copy ID: <span class="ctx-id">' + esc(taskId) + '</span></button>';

  html += '<div class="ctx-sep"></div>';
  html += '<button class="danger" onclick="boardDeleteTask(\'' + taskId + '\')">Delete</button>';

  menu.innerHTML = html;
  menu.style.top = evt.clientY + 'px';
  menu.style.left = Math.min(evt.clientX, window.innerWidth - 140) + 'px';
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardCopyTaskId(taskId) {
  navigator.clipboard.writeText(taskId).then(function() { _closeCtxMenu(); });
}

/* ---- Card actions --------------------------------------------------- */

function _boardTaskCloneFields(task) {
  return {
    task: task.task || '',
    description: task.description || '',
    group: task.group || _currentGroup(),
    action_name: task.action_name || '',
    action_vars: Object.assign({}, task.action_vars || {}),
    agent_template: task.agent_template || '',
    labels: (task.labels || []).filter(function(label) { return !isSystemLabel(label); }),
  };
}

function boardFocusTask(id, evt) {
  if (_boardQuickEditTask && _boardQuickEditTask !== id) {
    _boardQuickEditTask = '';
    _boardQuickEditKind = '';
    _boardQuickLabelDraft = '';
    _boardQuickDueDraft = '';
  }
  if (evt && (evt.metaKey || evt.ctrlKey)) {
    // Cmd/Ctrl+Click: toggle in selection
    if (_boardSelectedTasks[id]) {
      delete _boardSelectedTasks[id];
    } else {
      _boardSelectedTasks[id] = true;
    }
    _boardLastSelectedTask = id;
    _boardFocusedTask = id;
    renderBoard();
    return;
  }
  if (evt && evt.shiftKey && _boardLastSelectedTask) {
    // Shift+Click: range select within the same lane
    var lane = _boardSelectedLane || _boardLanes()[0] || '';
    var tasks = _boardTasksInLane(lane);
    var fromIdx = -1, toIdx = -1;
    for (var i = 0; i < tasks.length; i++) {
      if (tasks[i].id === _boardLastSelectedTask) fromIdx = i;
      if (tasks[i].id === id) toIdx = i;
    }
    if (fromIdx >= 0 && toIdx >= 0) {
      var lo = Math.min(fromIdx, toIdx);
      var hi = Math.max(fromIdx, toIdx);
      for (var i = lo; i <= hi; i++) {
        _boardSelectedTasks[tasks[i].id] = true;
      }
    }
    _boardFocusedTask = id;
    renderBoard();
    return;
  }
  // Plain click: clear selection, set single focus
  _boardSelectedTasks = {};
  _boardLastSelectedTask = id;
  _boardFocusedTask = id;
  renderBoard();
}

function boardFocusAgent(agentId) {
  send({ cmd: 'focus_agent', id: agentId });
  // Also select the agent in the main UI
  if (typeof selectedAgentId !== 'undefined') {
    selectedAgentId = agentId;
    focusedItemId = agentId;
  }
}

function _boardRevealTaskRootId(taskId, visibleTasks) {
  var rootId = taskId;
  var current = visibleTasks[rootId];
  while (current && current.parent_task_id && visibleTasks[current.parent_task_id]) {
    _boardCollapsedTasks[current.parent_task_id] = false;
    rootId = current.parent_task_id;
    current = visibleTasks[rootId];
  }
  return rootId;
}

function _boardEnsureRenderLimitForTask(taskId) {
  var visibleTasks = _boardVisibleTasks();
  if (!visibleTasks[taskId]) return;
  var rootId = _boardRevealTaskRootId(taskId, visibleTasks);
  var laneTasks = _boardTasksInLane(_boardSelectedLane);
  var rootIndex = -1;
  var rootCount = 0;
  for (var i = 0; i < laneTasks.length; i++) {
    var task = laneTasks[i];
    if (task.parent_task_id && visibleTasks[task.parent_task_id]) continue;
    if (task.id === rootId) {
      rootIndex = rootCount;
      break;
    }
    rootCount += 1;
  }
  if (rootIndex >= 0 && rootIndex >= _boardRenderLimit) {
    _boardRenderLimit = Math.ceil((rootIndex + 1) / 50) * 50;
  }
}

function boardNavigateToTask(taskId) {
  var task = _boardTasks()[taskId];
  if (!task) return;

  if (_boardFilterByGroup && task.group && typeof _currentGroup === 'function'
      && _currentGroup() !== task.group
      && typeof selectedAgentId !== 'undefined'
      && state && state.agents) {
    if (task.agent_id && state.agents[task.agent_id]) {
      selectedAgentId = task.agent_id;
    } else {
      for (var agentId in state.agents) {
        var agent = state.agents[agentId];
        if (agent && agent.cell_type === 'agent' && agent.group === task.group) {
          selectedAgentId = agentId;
          break;
        }
      }
    }
  }

  _boardPrepareViewChange(true);
  _boardShowSchedules = false;
  _boardSearchQuery = '';
  _boardQuickView = '';
  _boardFilterLabels = [];
  _boardFilterActions = [];
  _boardFilterAgents = [];
  _boardFilterHealth = [];
  _boardPreFilterLane = '';
  if (_boardIsArchived(task)) _boardShowArchived = true;
  if (typeof _boardCloseFilterDropdown === 'function') _boardCloseFilterDropdown();
  if (typeof _boardCloseViewMenu === 'function') _boardCloseViewMenu();

  _boardSelectedLane = task.lane || _boardSelectedLane || _boardLanes()[0] || '';
  _boardFocusedTask = task.id;
  _boardSelectedTasks = {};
  _boardLastSelectedTask = task.id;
  _boardQuickEditTask = '';
  _boardQuickEditKind = '';
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = '';
  _boardCardsScrollTop = 0;
  _boardResetBatchEdit();
  _boardEnsureRenderLimitForTask(task.id);
  _boardRevealFocusOnRender = true;
  _boardPersistFilterState();

  if (typeof _activePanelApp !== 'undefined' && _activePanelApp !== 'board'
      && typeof togglePanel === 'function') {
    togglePanel('board');
    return;
  }
  if (typeof _activePanelApp !== 'undefined') _activePanelApp = 'board';
  renderBoard();
}

function boardQuickLabelInput(value) {
  _boardQuickLabelDraft = value;
}

function boardQuickLabelKeydown(evt, taskId) {
  if (evt.key === 'Enter') {
    evt.preventDefault();
    boardQuickAddLabel(taskId);
  } else if (evt.key === 'Escape') {
    evt.preventDefault();
    _boardCloseQuickEdit();
  }
}

function boardQuickAddLabel(taskId) {
  var task = _boardTasks()[taskId];
  var label = _boardQuickLabelDraft.trim();
  if (!task || !label || isSystemLabel(label) || /^priority:/.test(label)) return;
  var labels = (task.labels || []).slice();
  if (labels.indexOf(label) < 0) {
    labels.push(label);
    send({ cmd: 'board_update_task', id: taskId, labels: labels });
  }
  _boardCloseQuickEdit();
}

function boardQuickRemoveLabel(taskId, label) {
  var task = _boardTasks()[taskId];
  if (!task) return;
  send({
    cmd: 'board_update_task',
    id: taskId,
    labels: (task.labels || []).filter(function(item) { return item !== label; })
  });
  _boardCloseQuickEdit();
}

function boardQuickAssignAgent(taskId, agentId) {
  send({ cmd: 'board_update_task', id: taskId, agent_id: agentId || '' });
  _boardCloseQuickEdit();
}

function boardQuickDueInput(value) {
  _boardQuickDueDraft = value;
}

function boardQuickDueKeydown(evt, taskId) {
  if (evt.key === 'Enter') {
    evt.preventDefault();
    boardQuickSaveDue(taskId);
  } else if (evt.key === 'Escape') {
    evt.preventDefault();
    _boardCloseQuickEdit();
  }
}

function boardQuickSaveDue(taskId) {
  var iso = '';
  if (_boardQuickDueDraft) {
    var d = new Date(_boardQuickDueDraft);
    if (!isNaN(d.getTime())) iso = d.toISOString();
  }
  send({ cmd: 'board_update_task', id: taskId, scheduled_at: iso });
  _boardCloseQuickEdit();
}

function boardQuickClearDue(taskId) {
  _boardQuickDueDraft = '';
  send({ cmd: 'board_update_task', id: taskId, scheduled_at: '' });
  _boardCloseQuickEdit();
}

function boardQuickSetPriority(taskId, priority) {
  var task = _boardTasks()[taskId];
  if (!task) return;
  var labels = (task.labels || []).filter(function(label) {
    return !/^priority:/.test(label);
  });
  if (priority) labels.push('priority:' + priority);
  send({ cmd: 'board_update_task', id: taskId, labels: labels });
  _boardCloseQuickEdit();
}

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
  _boardResetBatchEdit();
  renderBoard();
}

function _renderBoardSelectionBar() {
  var count = _boardSelectedCount();
  if (count === 0) return '';
  var lanes = _boardLanes();
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
  document.querySelectorAll('.board-lane-tab.drop-target').forEach(function(el) {
    el.classList.remove('drop-target');
  });
  document.querySelectorAll('.board-card.drop-before,.board-card.drop-after').forEach(function(el) {
    el.classList.remove('drop-before', 'drop-after');
  });
}

function boardCardDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
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
  var card = e.target.closest('.board-card');
  if (card) card.classList.remove('drop-before', 'drop-after');
}

function boardCardDrop(e) {
  e.preventDefault();
  if (_boardLaneSortMode(_boardSelectedLane) !== 'manual') return;
  var card = e.target.closest('.board-card');
  if (!card || !_boardDragId) return;
  var targetId = card.dataset.taskId;
  if (targetId === _boardDragId) return;

  // Find target position
  var tasks = _boardTasksInLane(_boardSelectedLane);
  var targetIdx = -1;
  for (var i = 0; i < tasks.length; i++) {
    if (tasks[i].id === targetId) { targetIdx = i; break; }
  }

  var rect = card.getBoundingClientRect();
  var mid = rect.top + rect.height / 2;
  var pos = e.clientY < mid ? targetIdx : targetIdx + 1;

  // UI is sorted newest-first (descending position), invert for server
  var serverPos = Math.max(0, tasks.length - pos - 1);
  send({ cmd: 'board_reorder_task', id: _boardDragId, position: serverPos });
  card.classList.remove('drop-before', 'drop-after');
}

// Drop on lane tab to move card to a different lane
function boardLaneTabDragOver(e) {
  if (!_boardDragId) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  var tab = e.target.closest('.board-lane-tab');
  if (tab) tab.classList.add('drop-target');
}

function boardLaneTabDragLeave(e) {
  var tab = e.target.closest('.board-lane-tab');
  if (tab) tab.classList.remove('drop-target');
}

function boardLaneTabDrop(e) {
  e.preventDefault();
  var tab = e.target.closest('.board-lane-tab');
  if (!tab || !_boardDragId) return;
  var lane = tab.dataset.lane;
  if (lane) {
    send({ cmd: 'board_move_task', id: _boardDragId, lane: lane });
  }
  tab.classList.remove('drop-target');
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
  // Only handle if board panel is open
  var panel = document.getElementById('bottom-panel');
  if (!panel || panel.classList.contains('collapsed')) return false;

  var lanes = _boardLanes();
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

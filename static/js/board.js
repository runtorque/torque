/* ------------------------------------------------------------------ */
/* Board panel app — Kanban board with lane tabs and task cards         */
/* ------------------------------------------------------------------ */

if (typeof taskIsEngineerMessageFollowup !== 'function') {
  var taskIsEngineerMessageFollowup = function(task) {
    var labels = (task && Array.isArray(task.labels)) ? task.labels : [];
    return labels.indexOf('torque:engineer-message') >= 0;
  };
}

// Client-side state
var _boardSelectedLane = '';
var _boardFocusedTask = '';
var _boardAddingTask = false;   // true when inline task input is shown
var _boardAddingTaskDraft = '';  // preserved text across blur/reopen
var _boardAddingTaskAgent = '';   // selected agent ID for inline add
var _boardAddingTaskLane = '';    // selected lane for inline add
var _boardInlineDraftId = '';     // pre-generated task ID for inline attachments
var _boardInlineAttachments = []; // attachments uploaded during inline creation
var _boardAddTaskFocus = false;   // true only on explicit open, not re-renders
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
var _boardFilterDropdownTriggerWrapId = '';
var _boardViewMenuCleanup = null;
var _boardViewMenuOpen = false;
var _boardPreFilterLane = '';    // saved lane before search, restored on clear
var _boardFiltersByGroup = null; // persisted filter state keyed by group
var _boardSelectedLanesByGroup = null; // persisted selected lane keyed by group
var _boardSavedViewsByGroup = null; // saved view snapshots keyed by group
var _boardLaneSortsByGroup = null; // persisted lane sort modes keyed by group
var _boardCardDensityByGroup = null; // persisted card density keyed by group
var _boardHiddenWideLanesByGroup = null; // persisted wide-layout lane collapse state keyed by group
var _boardDefaultHiddenWideLanes = { 'To Do': true }; // fresh wide-layout lane defaults
var _boardFilterStateGroup = '';
var _boardSelectedLaneStateGroup = '';
var _boardShowSchedules = false; // true when "Schedules" tab is active
var _boardShowArchived = false;  // include archived tasks in the active board view
var _boardSavingView = false;    // inline saved-view naming control visibility
var _boardSavingViewName = '';   // draft name for inline saved-view creation
var _boardSaveViewFocus = false; // focus the inline saved-view input after render
var _boardRevealFocusOnRender = false; // scroll the focused card into view after navigation
var _boardDefaultRenderLimit = 50;
var _boardDoneInitialRenderLimit = 30;
var _boardDoneRenderBatch = 30;
var _boardRenderLimit = _boardDefaultRenderLimit; // virtual scroll: non-Done card cap
var _boardDoneRenderLimit = _boardDoneInitialRenderLimit; // Done starts smaller for large history
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
var _boardArchiveLabel = 'torque:archived'; // legacy compatibility for older state
var _boardArchiveStaleDays = 7;
var _boardLaneEntryRefreshTimer = 0;
var _boardWideModeMinWidth = 960;
var _boardEligibilityActionsByGroup = {};
var _boardEligibilityTemplatesByGroup = {};
var _boardEligibilityActionWaiting = false;
var _boardEligibilityTemplateWaiting = false;
var _boardQueuedTaskDeltas = [];
var _boardQueuedTaskDeltasCanPatch = true;
var _boardLastRenderShellKey = '';
var _boardLastToolbarShapeKey = '';
var _boardLaneRenderCache = {};
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

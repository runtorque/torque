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
var _boardActDropdownWaiting = false;  // waiting for action list for dropdown
var _boardActList = null;              // fetched actions shown inline (null = hidden)
var _boardScrollLeft = 0;      // preserve scroll across re-renders
var _boardCardsScrollTop = 0;  // preserve cards scroll across re-renders
var _boardDragId = '';          // card being dragged

var _boardCollapsedTasks = {};  // task_id → true if collapsed
var _boardFilterByGroup = true;  // When true, board shows only tasks from the current group
var _boardSearchQuery = '';      // text search filter
var _boardFilterLabels = [];     // active label filters (OR logic)
var _boardFilterActions = [];    // active action name filters (OR logic)
var _boardSearchTimer = null;    // debounce timer for search input
var _boardPreFilterLane = '';    // saved lane before search, restored on clear

/* ---- Helpers -------------------------------------------------------- */

function _boardLanes() {
  return (state && state.board_lanes) || [];
}

function _boardTasks() {
  return (state && state.board_tasks) || {};
}

/** Return visible tasks, optionally filtered to the current group. */
function _boardVisibleTasks() {
  var all = _boardTasks();
  var out = {};
  // Group filter
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
  // Text search filter
  if (_boardSearchQuery) {
    var q = _boardSearchQuery.toLowerCase();
    var filtered = {};
    for (var id in out) {
      var t = out[id];
      if ((t.task && t.task.toLowerCase().indexOf(q) >= 0)
        || (t.description && t.description.toLowerCase().indexOf(q) >= 0)
        || (t.slug && t.slug.toLowerCase().indexOf(q) >= 0)) {
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
  return out;
}

function _boardTasksInLane(lane) {
  var tasks = _boardVisibleTasks();
  var arr = [];
  for (var id in tasks) {
    if (tasks[id].lane === lane) arr.push(tasks[id]);
  }
  arr.sort(function(a, b) { return b.position - a.position; });
  return arr;
}

function _boardLaneCount(lane) {
  var tasks = _boardVisibleTasks();
  var n = 0;
  for (var id in tasks) {
    if (tasks[id].lane === lane) n++;
  }
  return n;
}

function _boardHasActiveFilters() {
  return _boardSearchQuery !== '' || _boardFilterLabels.length > 0 || _boardFilterActions.length > 0;
}

/** Collect all labels with counts (before search/label/action filters). */
function _boardAllLabelCounts() {
  var all = _boardTasks();
  var pool = {};
  if (_boardFilterByGroup) {
    var grp = _currentGroup();
    if (grp) {
      for (var id in all) { if (all[id].group === grp) pool[id] = all[id]; }
    } else { pool = all; }
  } else { pool = all; }
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
  var all = _boardTasks();
  var pool = {};
  if (_boardFilterByGroup) {
    var grp = _currentGroup();
    if (grp) {
      for (var id in all) { if (all[id].group === grp) pool[id] = all[id]; }
    } else { pool = all; }
  } else { pool = all; }
  var counts = {};
  for (var id in pool) {
    var t = pool[id];
    if (t.action_name) {
      counts[t.action_name] = (counts[t.action_name] || 0) + 1;
    }
  }
  return counts;
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

/* ---- Card rendering ------------------------------------------------- */

function _renderBoardCard(t, childrenOf, depth) {
  var isSubordinate = depth > 0;
  var hasChildren = childrenOf[t.id] && childrenOf[t.id].length > 0;
  var isCollapsed = _boardCollapsedTasks[t.id];
  var isDone = t.lane === 'Done';
  var dotClass = t.agent_id ? _boardAgentStatus(t.agent_id) : '';
  var focused = t.id === _boardFocusedTask ? ' focused' : '';
  var subClass = isSubordinate ? ' board-card-subordinate' : '';
  var doneClass = (isSubordinate && isDone) ? ' board-card-done' : '';
  var cardHtml = '<div class="board-card' + focused + subClass + doneClass + '"'
    + ' data-task-id="' + t.id + '"'
    + ' draggable="true"'
    + ' ondragstart="boardCardDragStart(event,\'' + t.id + '\')"'
    + ' ondragend="boardCardDragEnd(event)"'
    + ' ondragover="boardCardDragOver(event)"'
    + ' ondragleave="boardCardDragLeave(event)"'
    + ' ondrop="boardCardDrop(event)"'
    + ' onclick="boardFocusTask(\'' + t.id + '\')"'
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
  cardHtml += '<div class="board-card-title">' + titlePrefix + esc(t.task || '') + '</div>';
  var meta = '';
  if (t.status) meta += '<span class="board-card-label board-card-status">' + esc(t.status) + '</span>';
  if (_boardHasActiveFilters() && t.lane) meta += '<span class="board-card-lane-badge">' + esc(t.lane) + '</span>';
  if (t.group && !isSubordinate) meta += '<span class="board-card-group">' + esc(t.group) + '</span>';
  if (t.action_name) meta += '<span class="board-card-label board-card-template">' + esc(t.action_name) + '</span>';
  if (t.agent_template) meta += '<span class="board-card-label board-card-template">agent: ' + esc(t.agent_template) + '</span>';
  if (t.labels && t.labels.length) {
    var userLbls = [], sysLbls = [];
    for (var li = 0; li < t.labels.length; li++) {
      if (isSystemLabel(t.labels[li])) sysLbls.push(t.labels[li]);
      else userLbls.push(t.labels[li]);
    }
    for (var li = 0; li < userLbls.length; li++) {
      meta += '<span class="board-card-label">' + esc(userLbls[li]) + '</span>';
    }
    for (var li = 0; li < sysLbls.length; li++) {
      var lb = sysLbls[li];
      var cls = 'board-card-label board-label-system';
      if (lb === 'loom:blocked') cls += ' board-label-blocked';
      else if (lb === 'loom:error') cls += ' board-label-error';
      meta += '<span class="' + cls + '">' + esc(displayLabel(lb)) + '</span>';
    }
  }
  if (t.external_url) {
    meta += '<a class="board-card-pr-link" href="' + esc(t.external_url)
      + '" onclick="event.stopPropagation();window.open(this.href);return false"'
      + ' title="' + esc(t.external_url) + '">&#x1F517;</a>';
  }
  if (meta) cardHtml += '<div class="board-card-meta">' + meta + '</div>';
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
  // Last activity message
  if (t.messages && t.messages.length) {
    var lastMsg = t.messages[t.messages.length - 1];
    var msgText = lastMsg.message || '';
    if (msgText.length > 60) msgText = msgText.substring(0, 57) + '...';
    var msgClass = 'board-card-activity';
    if (lastMsg.action === 'done' || lastMsg.action === 'ready') msgClass += ' board-card-activity-done';
    else if (lastMsg.action === 'error') msgClass += ' board-card-activity-error';
    else if (lastMsg.action === 'blocked') msgClass += ' board-card-activity-blocked';
    cardHtml += '<div class="' + msgClass + '">' + esc(msgText) + '</div>';
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

function boardToggleTaskCollapse(taskId) {
  _boardCollapsedTasks[taskId] = !_boardCollapsedTasks[taskId];
  renderBoard();
}

/* ---- Render --------------------------------------------------------- */

function renderBoard() {
  var panel = document.getElementById('panel-board');
  if (!panel) return;

  // Preserve scroll + draft before DOM rebuild
  var _cardsEl = document.getElementById('board-cards');
  if (_cardsEl) _boardCardsScrollTop = _cardsEl.scrollTop;
  if (_boardAddingTask) {
    var _inp = document.getElementById('board-add-task-input');
    if (_inp) _boardAddingTaskDraft = _inp.value;
  }

  var lanes = _boardLanes();
  if (!lanes.length) {
    panel.innerHTML = '<div class="board-empty">No lanes configured</div>';
    return;
  }

  // Default to first lane if selected lane is invalid
  if (!_boardSelectedLane || lanes.indexOf(_boardSelectedLane) === -1) {
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
  var hasLabels = Object.keys(labelCounts).length > 0;
  var hasActions = Object.keys(actionCounts).length > 0;
  var showToolbar = hasLabels || hasActions || _boardSearchQuery || _boardFilterLabels.length || _boardFilterActions.length;

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
    if (filtersActive) {
      html += '<button class="board-filter-clear" onclick="boardClearFilters()">Clear</button>';
    }
    html += '</div>';

    // Active filter chips
    if (_boardFilterLabels.length || _boardFilterActions.length) {
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

  // Lane tab bar
  html += '<div class="board-lane-bar">';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-left" onclick="boardScrollLanes(-1)" title="Scroll left">&#9664;</button>';
  html += '<div class="board-lane-tabs" id="board-lane-tabs">';
  for (var i = 0; i < lanes.length; i++) {
    var l = lanes[i];
    var cnt = _boardLaneCount(l);
    var cls = l === _boardSelectedLane ? ' active' : '';
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
  html += '</div>';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-right" onclick="boardScrollLanes(1)" title="Scroll right">&#9654;</button>';

  html += '</div>';

  // Cards — always show tasks in the selected lane
  var tasks = _boardTasksInLane(_boardSelectedLane);
  html += '<div class="board-cards" id="board-cards">';

  // Add task: inline input or button (at top)
  if (_boardAddingTask) {
    html += '<div class="board-add-task board-add-task-active">';
    html += '<textarea class="board-add-input" id="board-add-task-input" rows="1"'
      + ' placeholder="Task description..."'
      + ' onkeydown="boardAddTaskKeydown(event)"'
      + ' oninput="boardAddTaskAutoResize(this)"'
      + ' onblur="boardCancelAddTask()">' + esc(_boardAddingTaskDraft) + '</textarea>';
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

  // Build task tree: root tasks + children index
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

  // Task cards
  if (tasks.length === 0) {
    html += '<div class="board-empty">' + (filtersActive ? 'No matching tasks' : 'No tasks in this lane') + '</div>';
  }

  for (var j = 0; j < rootTasks.length; j++) {
    html += _renderBoardCard(rootTasks[j], childrenOf, 0);
  }

  html += '</div>';
  panel.innerHTML = html;

  // Auto-focus inputs
  if (_boardAddingTask) {
    var tInp = document.getElementById('board-add-task-input');
    if (tInp) {
      boardAddTaskAutoResize(tInp);
      tInp.focus();
      // Place cursor at end
      tInp.selectionStart = tInp.selectionEnd = tInp.value.length;
    }
  }

  // Defer scroll restore + arrow update to after layout
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

    // Restore cards scroll position
    var cardsEl = document.getElementById('board-cards');
    if (cardsEl) {
      cardsEl.scrollTop = _boardCardsScrollTop;
      cardsEl.addEventListener('scroll', function() {
        _boardCardsScrollTop = cardsEl.scrollTop;
      });
    }

  });
}

/* ---- Lane selection ------------------------------------------------- */

function boardSelectLane(lane) {
  if (lane === _boardSelectedLane) return;
  // Save current scroll so renderBoard can restore + adjust for new active tab
  var tabs = document.getElementById('board-lane-tabs');
  if (tabs) _boardScrollLeft = tabs.scrollLeft;
  _boardSelectedLane = lane;
  _boardFocusedTask = '';
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
  _boardFocusedTask = '';
  renderBoard();
}

function boardCancelAddTask() {
  var el = document.getElementById('board-add-task-input');
  if (el) _boardAddingTaskDraft = el.value;
  setTimeout(function() { _boardAddingTask = false; _boardTplList = null; renderBoard(); }, 150);
}

function boardClearAddTask() {
  _boardAddingTask = false;
  _boardAddingTaskDraft = '';
  _boardAddingTaskAction = '';
  _boardAddingTaskAgent = '';
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
  if (parsed.labels.length) msg.labels = parsed.labels;
  if (_boardAddingTaskAction) msg.action_name = _boardAddingTaskAction;
  if (_boardAddingTaskAgent) msg.agent_id = _boardAddingTaskAgent;
  _boardAddingTaskAction = '';
  _boardAddingTaskAgent = '';
  _boardTplList = null;
  send(msg);
  renderBoard();
}

function boardAddTaskKeydown(e) {
  if (e.key === 'Escape') {
    boardClearAddTask();
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    boardSubmitAddTask();
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
  _taskEditId = null;
  _taskSelectedAction = name;
  _taskActionVars = [];
  _taskActionVarValues = {};

  document.getElementById('task-modal-title').textContent = 'New Task';
  document.getElementById('task-submit-btn').textContent = 'Create';
  document.getElementById('task-task-input').value = '';
  document.getElementById('task-labels-input').value = '';
  document.getElementById('task-action-vars').innerHTML = '';
  _populateTaskGroupSelect(_currentGroup());
  document.getElementById('modal-task').dataset.lane = _boardSelectedLane || '';

  _taskModalWaiting = true;
  send({ cmd: 'list_actions', group: _currentGroup() });
  document.getElementById('modal-task').classList.add('visible');
  document.getElementById('task-task-input').focus();
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

  html += '<div class="ctx-sep"></div>';

  // Dispatch (only from Backlog)
  if (task.lane === 'Backlog') {
    html += '<button onclick="event.stopPropagation();boardDispatchTask(\'' + taskId + '\')">Dispatch...</button>';
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

  // Preview prompt
  html += '<button onclick="boardPreviewPrompt(\'' + taskId + '\')">Preview prompt</button>';

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

function boardFocusTask(id) {
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

function boardEditTask(taskId) {
  _closeCtxMenu();
  openEditTask(taskId);
}

function boardMoveTaskToLane(taskId, lane) {
  _closeCtxMenu();
  send({ cmd: 'board_move_task', id: taskId, lane: lane });
}

function boardDeleteTask(taskId) {
  _closeCtxMenu();
  send({ cmd: 'board_remove_task', id: taskId });
}

function boardUnlinkAgent(taskId) {
  _closeCtxMenu();
  send({ cmd: 'board_update_task', id: taskId, agent_id: '' });
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

/* ---- Dispatch task to agent ----------------------------------------- */

function boardDispatchTask(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

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
  var serverPos = tasks.length - pos;
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
    _boardSearchQuery = query;
    _boardCardsScrollTop = 0;
    renderBoard();
    // Restore focus and cursor to search input
    var inp = document.getElementById('board-search-input');
    if (inp) { inp.focus(); inp.selectionStart = inp.selectionEnd = inp.value.length; }
  }, 200);
}

function boardToggleLabel(label) {
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardFilterLabels.splice(idx, 1);
  } else {
    _boardFilterLabels.push(label);
  }
  _boardCardsScrollTop = 0;
  renderBoard();
}

function boardToggleAction(action) {
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardFilterActions.splice(idx, 1);
  } else {
    _boardFilterActions.push(action);
  }
  _boardCardsScrollTop = 0;
  renderBoard();
}

function boardRemoveFilterLabel(label) {
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardFilterLabels.splice(idx, 1);
    _boardCardsScrollTop = 0;
    renderBoard();
  }
}

function boardRemoveFilterAction(action) {
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardFilterActions.splice(idx, 1);
    _boardCardsScrollTop = 0;
    renderBoard();
  }
}

function boardClearFilters() {
  _boardSearchQuery = '';
  _boardFilterLabels = [];
  _boardFilterActions = [];
  _boardCloseFilterDropdown();
  _boardCardsScrollTop = 0;
  if (_boardPreFilterLane) {
    _boardSelectedLane = _boardPreFilterLane;
    _boardPreFilterLane = '';
  }
  renderBoard();
}

/* ---- Filter dropdowns ----------------------------------------------- */

function boardToggleLabelFilter() {
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
  search.placeholder = 'Filter ' + kind + 's\u2026';
  dd.appendChild(search);

  var list = document.createElement('div');
  list.className = 'board-filter-dropdown-list';
  dd.appendChild(list);

  function buildList(query) {
    list.innerHTML = '';
    var q = (query || '').toLowerCase();
    var filtered = [];
    for (var i = 0; i < names.length; i++) {
      if (q && names[i].toLowerCase().indexOf(q) < 0) continue;
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
          else boardToggleAction(n);
          buildList(search.value);
        });
      })(name);
      row.appendChild(cb);
      var span = document.createElement('span');
      span.className = 'board-filter-dropdown-name';
      span.textContent = (kind === 'label' && isSystemLabel(name)) ? displayLabel(name) : name;
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

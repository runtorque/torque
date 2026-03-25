/* ------------------------------------------------------------------ */
/* Board panel app — Kanban board with lane tabs and task cards         */
/* ------------------------------------------------------------------ */

// Client-side state
var _boardSelectedLane = '';
var _boardFocusedTask = '';
var _boardAddingTask = false;
var _boardAddingLane = false;
var _boardScrollLeft = 0;      // preserve scroll across re-renders
var _boardPopover = null;      // {type, lane, rect} for lane settings popover
var _boardDragId = '';          // card being dragged

/* ---- Helpers -------------------------------------------------------- */

function _boardLanes() {
  return (state && state.board_lanes) || [];
}

function _boardTasks() {
  return (state && state.board_tasks) || {};
}

function _boardTasksInLane(lane) {
  var tasks = _boardTasks();
  var arr = [];
  for (var id in tasks) {
    if (tasks[id].lane === lane) arr.push(tasks[id]);
  }
  arr.sort(function(a, b) { return a.position - b.position; });
  return arr;
}

function _boardLaneCount(lane) {
  var tasks = _boardTasks();
  var n = 0;
  for (var id in tasks) {
    if (tasks[id].lane === lane) n++;
  }
  return n;
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

/* ---- Render --------------------------------------------------------- */

function renderBoard() {
  var panel = document.getElementById('panel-board');
  if (!panel) return;

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

  // Lane tab bar
  html += '<div class="board-lane-bar">';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-left" onclick="boardScrollLanes(-1)" title="Scroll left">&#9664;</button>';
  html += '<div class="board-lane-tabs" id="board-lane-tabs">';
  for (var i = 0; i < lanes.length; i++) {
    var l = lanes[i];
    var cnt = _boardLaneCount(l);
    var cls = l === _boardSelectedLane ? ' active' : '';
    var escLane = esc(l).replace(/'/g, "\\'");
    html += '<button class="board-lane-tab' + cls + '"'
      + ' data-lane="' + esc(l) + '"'
      + ' onclick="boardSelectLane(\'' + escLane + '\')"'
      + ' oncontextmenu="boardLaneContextMenu(event,\'' + escLane + '\')"'
      + ' ondragover="boardLaneTabDragOver(event)"'
      + ' ondragleave="boardLaneTabDragLeave(event)"'
      + ' ondrop="boardLaneTabDrop(event)">'
      + esc(l) + '<span class="lane-count">' + cnt + '</span></button>';
  }
  html += '</div>';
  html += '<button class="board-lane-scroll-btn" id="board-scroll-right" onclick="boardScrollLanes(1)" title="Scroll right">&#9654;</button>';

  // Lane actions: + and settings
  html += '<div class="board-lane-actions">';
  html += '<button class="board-lane-action-btn" onclick="boardStartAddLane()" title="Add lane">+</button>';
  html += '<button class="board-lane-action-btn" onclick="boardOpenLaneSettings(event)" title="Lane settings">&#9881;</button>';
  html += '</div>';
  html += '</div>';

  // Inline add lane input
  if (_boardAddingLane) {
    html += '<div style="padding:4px 8px;border-bottom:1px solid var(--border)">';
    html += '<input class="board-add-input" id="board-add-lane-input"'
      + ' placeholder="Lane name..."'
      + ' onkeydown="boardAddLaneKeydown(event)"'
      + ' onblur="boardCancelAddLane()">';
    html += '</div>';
  }

  // Cards for selected lane
  var tasks = _boardTasksInLane(_boardSelectedLane);
  html += '<div class="board-cards" id="board-cards">';

  if (tasks.length === 0 && !_boardAddingTask) {
    html += '<div class="board-empty">No tasks in this lane</div>';
  }

  for (var j = 0; j < tasks.length; j++) {
    var t = tasks[j];
    var dotClass = t.agent_id ? _boardAgentStatus(t.agent_id) : '';
    var focused = t.id === _boardFocusedTask ? ' focused' : '';
    html += '<div class="board-card' + focused + '"'
      + ' data-task-id="' + t.id + '"'
      + ' draggable="true"'
      + ' ondragstart="boardCardDragStart(event,\'' + t.id + '\')"'
      + ' ondragend="boardCardDragEnd(event)"'
      + ' ondragover="boardCardDragOver(event)"'
      + ' ondragleave="boardCardDragLeave(event)"'
      + ' ondrop="boardCardDrop(event)"'
      + ' onclick="boardFocusTask(\'' + t.id + '\')"'
      + ' oncontextmenu="boardCardMenu(event,\'' + t.id + '\')">';
    html += '<div class="board-card-dot ' + dotClass + '"></div>';
    html += '<div class="board-card-info">';
    html += '<div class="board-card-title">' + esc(t.title) + '</div>';
    if (t.agent_id) {
      var aName = _boardAgentName(t.agent_id);
      if (aName) {
        html += '<div class="board-card-agent" onclick="event.stopPropagation();boardFocusAgent(\'' + t.agent_id + '\')">'
          + '&#x1F916; ' + esc(aName) + '</div>';
      }
    }
    html += '</div>';
    html += '<button class="board-card-menu-btn" onclick="event.stopPropagation();boardCardMenu(event,\'' + t.id + '\')" title="Actions">&#8942;</button>';
    html += '</div>';
  }

  // Add task inline
  if (_boardAddingTask) {
    html += '<input class="board-add-input" id="board-add-task-input"'
      + ' placeholder="Task title..."'
      + ' onkeydown="boardAddTaskKeydown(event)"'
      + ' onblur="boardCancelAddTask()">';
  } else {
    html += '<div class="board-add-task" onclick="boardStartAddTask()">';
    html += '<span>+ Add task</span>';
    html += '</div>';
  }

  html += '</div>';
  panel.innerHTML = html;

  // Auto-focus inputs
  if (_boardAddingTask) {
    var inp = document.getElementById('board-add-task-input');
    if (inp) inp.focus();
  }
  if (_boardAddingLane) {
    var inp2 = document.getElementById('board-add-lane-input');
    if (inp2) inp2.focus();
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

    // Render popover if open
    if (_boardPopover) _renderBoardPopover();
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
  _boardAddingTask = false;
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

/* ---- Add task ------------------------------------------------------- */

function boardStartAddTask() {
  _boardAddingTask = true;
  renderBoard();
}

function boardCancelAddTask() {
  setTimeout(function() { _boardAddingTask = false; renderBoard(); }, 150);
}

function boardAddTaskKeydown(e) {
  if (e.key === 'Escape') {
    _boardAddingTask = false;
    renderBoard();
    return;
  }
  if (e.key === 'Enter') {
    var val = e.target.value.trim();
    if (val) {
      send({ cmd: 'board_add_task', title: val, lane: _boardSelectedLane });
    }
    _boardAddingTask = false;
    // Don't re-render; broadcast will trigger it
  }
}

/* ---- Add lane ------------------------------------------------------- */

function boardStartAddLane() {
  _boardAddingLane = true;
  _boardClosePopover();
  renderBoard();
}

function boardCancelAddLane() {
  setTimeout(function() { _boardAddingLane = false; renderBoard(); }, 150);
}

function boardAddLaneKeydown(e) {
  if (e.key === 'Escape') {
    _boardAddingLane = false;
    renderBoard();
    return;
  }
  if (e.key === 'Enter') {
    var val = e.target.value.trim();
    if (val) {
      send({ cmd: 'board_add_lane', name: val });
    }
    _boardAddingLane = false;
  }
}

/* ---- Lane context menu (right-click) -------------------------------- */

function boardLaneContextMenu(evt, lane) {
  evt.preventDefault();
  var menu = document.getElementById('ctx-menu');
  var lanes = _boardLanes();
  var escLane = esc(lane).replace(/'/g, "\\'");

  var html = '<button onclick="event.stopPropagation();boardStartRenameLane(\'' + escLane + '\')">Rename</button>';
  if (lanes.length > 1) {
    html += '<button class="danger" onclick="boardRemoveLane(\'' + escLane + '\')">Delete lane</button>';
  }
  menu.innerHTML = html;
  menu.style.top = evt.clientY + 'px';
  menu.style.left = Math.min(evt.clientX, window.innerWidth - 140) + 'px';
  menu.classList.add('open');
}

/* ---- Lane settings popover ------------------------------------------ */

function boardOpenLaneSettings(evt) {
  if (_boardPopover) { _boardClosePopover(); return; }
  var btn = evt.currentTarget;
  var rect = btn.getBoundingClientRect();
  _boardPopover = { lane: _boardSelectedLane, rect: rect };
  _renderBoardPopover();
}

function _renderBoardPopover() {
  // Remove existing
  var old = document.getElementById('board-lane-popover');
  if (old) old.remove();

  if (!_boardPopover) return;

  var pop = document.createElement('div');
  pop.id = 'board-lane-popover';
  pop.className = 'board-lane-popover';

  var lane = _boardPopover.lane;
  var lanes = _boardLanes();

  var escLane = esc(lane).replace(/'/g, "\\'");
  var html = '<button onclick="event.stopPropagation();boardStartRenameLane(\'' + escLane + '\')">Rename</button>';
  if (lanes.length > 1) {
    html += '<button class="danger" onclick="boardRemoveLane(\'' + escLane + '\')">Delete lane</button>';
  }
  pop.innerHTML = html;

  // Position below the settings button
  var r = _boardPopover.rect;
  pop.style.top = (r.bottom + 2) + 'px';
  pop.style.right = (window.innerWidth - r.right) + 'px';
  document.body.appendChild(pop);
}

function _boardClosePopover() {
  _boardPopover = null;
  var old = document.getElementById('board-lane-popover');
  if (old) old.remove();
}

function boardStartRenameLane(lane) {
  _boardClosePopover();

  // Replace ctx-menu content with rename input
  var menu = document.getElementById('ctx-menu');
  menu.innerHTML = '<input id="board-rename-input" value="' + esc(lane) + '"'
    + ' style="margin:4px;width:calc(100% - 8px);font-size:10px;padding:4px 6px"'
    + ' onkeydown="boardRenameLaneKeydown(event,\'' + esc(lane).replace(/'/g, "\\'") + '\')"'
    + ' onclick="event.stopPropagation()">';
  menu.classList.add('open');
  var inp = document.getElementById('board-rename-input');
  if (inp) { inp.focus(); inp.select(); }
}

function boardRenameLaneKeydown(e, oldName) {
  if (e.key === 'Escape') { _closeCtxMenu(); return; }
  if (e.key === 'Enter') {
    var val = e.target.value.trim();
    if (val && val !== oldName) {
      send({ cmd: 'board_rename_lane', old_name: oldName, new_name: val });
      if (_boardSelectedLane === oldName) _boardSelectedLane = val;
    }
    _closeCtxMenu();
  }
}

function boardRemoveLane(lane) {
  _closeCtxMenu();
  _boardClosePopover();

  var lanes = _boardLanes();
  var cnt = _boardLaneCount(lane);
  var target = lanes[0] === lane ? lanes[1] : lanes[0];
  var msg = 'Delete lane "' + lane + '"?';
  if (cnt > 0) msg += '\n' + cnt + ' task(s) will move to "' + target + '".';

  showConfirm(msg).then(function(yes) {
    if (!yes) return;
    send({ cmd: 'board_remove_lane', name: lane, move_tasks_to: target });
    if (_boardSelectedLane === lane) _boardSelectedLane = target;
  });
}

/* ---- Card context menu ---------------------------------------------- */

function boardCardMenu(evt, taskId) {
  evt.preventDefault();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

  var menu = document.getElementById('ctx-menu');
  var lanes = _boardLanes();

  var html = '';
  html += '<button onclick="event.stopPropagation();boardEditTask(\'' + taskId + '\')">Edit</button>';

  // Move to lane submenu
  for (var i = 0; i < lanes.length; i++) {
    if (lanes[i] !== task.lane) {
      html += '<button onclick="boardMoveTaskToLane(\'' + taskId + '\',\''
        + esc(lanes[i]).replace(/'/g, "\\'") + '\')">Move to ' + esc(lanes[i]) + '</button>';
    }
  }

  html += '<div class="ctx-sep"></div>';

  // Link/Unlink agent
  if (task.agent_id) {
    html += '<button onclick="boardUnlinkAgent(\'' + taskId + '\')">Unlink agent</button>';
  } else {
    html += '<button onclick="event.stopPropagation();boardLinkAgent(\'' + taskId + '\')">Link agent...</button>';
  }

  html += '<div class="ctx-sep"></div>';
  html += '<button class="danger" onclick="boardDeleteTask(\'' + taskId + '\')">Delete</button>';

  menu.innerHTML = html;
  menu.style.top = evt.clientY + 'px';
  menu.style.left = Math.min(evt.clientX, window.innerWidth - 140) + 'px';
  menu.classList.add('open');
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
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

  // Replace ctx-menu content with an inline edit input
  var menu = document.getElementById('ctx-menu');
  menu.innerHTML = '<input id="board-edit-task-input" value="' + esc(task.title)
    + '" style="margin:4px;width:calc(100% - 8px);font-size:10px;padding:4px 6px"'
    + ' onkeydown="boardEditTaskKeydown(event,\'' + taskId + '\')"'
    + ' onclick="event.stopPropagation()">';
  menu.classList.add('open');
  var inp = document.getElementById('board-edit-task-input');
  if (inp) { inp.focus(); inp.select(); }
}

function boardEditTaskKeydown(e, taskId) {
  if (e.key === 'Escape') { _closeCtxMenu(); return; }
  if (e.key === 'Enter') {
    var val = e.target.value.trim();
    if (val) {
      send({ cmd: 'board_update_task', id: taskId, title: val });
    }
    _closeCtxMenu();
  }
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
}

function boardDoLinkAgent(taskId, agentId) {
  _closeCtxMenu();
  send({ cmd: 'board_update_task', id: taskId, agent_id: agentId });
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

  send({ cmd: 'board_reorder_task', id: _boardDragId, position: pos });
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
    boardEditTask(_boardFocusedTask);
    return true;
  }

  if (e.key === 'Delete' && _boardFocusedTask) {
    boardDeleteTask(_boardFocusedTask);
    _boardFocusedTask = '';
    return true;
  }

  return false;
}

/* ---- Close popover on outside click --------------------------------- */

document.addEventListener('click', function(e) {
  if (_boardPopover) {
    var pop = document.getElementById('board-lane-popover');
    if (pop && !pop.contains(e.target) &&
        !e.target.closest('.board-lane-action-btn')) {
      _boardClosePopover();
    }
  }
});

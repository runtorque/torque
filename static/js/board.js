/* ------------------------------------------------------------------ */
/* Board panel app — Kanban board with lane tabs and task cards         */
/* ------------------------------------------------------------------ */

// Client-side state
var _boardSelectedLane = '';
var _boardFocusedTask = '';
var _boardAddingTask = false;   // true when inline task input is shown
var _boardAddingLane = false;
var _boardTplDropdownWaiting = false;  // waiting for template list for dropdown
var _boardTplList = null;              // fetched templates shown inline (null = hidden)
var _boardScrollLeft = 0;      // preserve scroll across re-renders
var _boardPopover = null;      // {type, lane, rect} for lane settings popover
var _boardDragId = '';          // card being dragged

var _RESERVED_LANES = ['Backlog', 'In Progress'];

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
    if (_RESERVED_LANES.indexOf(l) >= 0) cls += ' reserved';
    var escLane = esc(l).replace(/'/g, "\\'");
    html += '<button class="board-lane-tab' + cls + '"'
      + ' data-lane="' + esc(l) + '"'
      + ' onclick="boardSelectLane(\'' + escLane + '\')"'
      + ' oncontextmenu="boardLaneContextMenu(event,\'' + escLane + '\')"'
      + ' ondragover="boardLaneTabDragOver(event)"'
      + ' ondragleave="boardLaneTabDragLeave(event)"'
      + ' ondrop="boardLaneTabDrop(event)">'
      + esc(l) + '<span class="lane-count">' + cnt + '</span>'
      + (_RESERVED_LANES.indexOf(l) >= 0 ? '<span class="lane-lock">&#x1F512;</span>' : '')
      + '</button>';
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

  // Add task: inline input or button (at top)
  if (_boardAddingTask) {
    html += '<div class="board-add-task board-add-task-active">';
    html += '<input class="board-add-input" id="board-add-task-input"'
      + ' placeholder="Task description..."'
      + ' onkeydown="boardAddTaskKeydown(event)"'
      + ' onblur="boardCancelAddTask()">';
    html += '<button class="board-add-tpl-btn" onmousedown="event.preventDefault()" onclick="boardToggleTemplateList()" title="Pick a template">From template &#9662;</button>';
    html += '</div>';
  } else {
    html += '<div class="board-add-task" onclick="boardStartAddTask()">';
    html += '<span>+ Add task</span>';
    html += '<button class="board-add-tpl-btn-idle" onclick="event.stopPropagation();boardToggleTemplateList()">From template &#9662;</button>';
    html += '</div>';
  }

  // Inline template list (shown below add-task)
  if (_boardTplList !== null) {
    html += '<div class="board-tpl-list">';
    if (_boardTplList.length === 0) {
      html += '<div class="board-tpl-empty">No templates found</div>';
    } else {
      var projectTpls = _boardTplList.filter(function(t) { return !t.global; });
      var userTpls = _boardTplList.filter(function(t) { return t.global; });
      if (projectTpls.length) {
        html += '<div class="board-tpl-group-label">Project</div>';
        for (var pi = 0; pi < projectTpls.length; pi++) {
          html += _boardTplItemHtml(projectTpls[pi]);
        }
      }
      if (userTpls.length) {
        html += '<div class="board-tpl-group-label">User</div>';
        for (var ui = 0; ui < userTpls.length; ui++) {
          html += _boardTplItemHtml(userTpls[ui]);
        }
      }
    }
    html += '<button class="board-tpl-item board-tpl-notemplate" onclick="_boardPickNoTemplate()">No template</button>';
    html += '</div>';
  }

  // Task cards
  if (tasks.length === 0) {
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
      + ' oncontextmenu="boardCardMenu(event,\'' + t.id + '\')"'
      + ' ondblclick="openEditTask(\'' + t.id + '\')">';
    html += '<div class="board-card-dot ' + dotClass + '"></div>';
    html += '<div class="board-card-info">';
    html += '<div class="board-card-title">' + esc(t.task || '') + '</div>';
    var meta = '';
    if (t.group) meta += '<span class="board-card-group">' + esc(t.group) + '</span>';
    if (t.assignee) meta += '<span class="board-card-assignee">' + esc(t.assignee) + '</span>';
    if (t.template_name) meta += '<span class="board-card-label board-card-template">' + esc(t.template_name) + '</span>';
    if (t.labels && t.labels.length) {
      for (var li = 0; li < t.labels.length; li++) {
        meta += '<span class="board-card-label">' + esc(t.labels[li]) + '</span>';
      }
    }
    if (meta) html += '<div class="board-card-meta">' + meta + '</div>';
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

  html += '</div>';
  panel.innerHTML = html;

  // Auto-focus inputs
  if (_boardAddingTask) {
    var tInp = document.getElementById('board-add-task-input');
    if (tInp) tInp.focus();
  } else if (_boardAddingLane) {
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
  renderBoard();
}

function boardCancelAddTask() {
  setTimeout(function() { _boardAddingTask = false; _boardTplList = null; renderBoard(); }, 150);
}

function boardAddTaskKeydown(e) {
  if (e.key === 'Escape') {
    _boardAddingTask = false;
    _boardTplList = null;
    renderBoard();
    return;
  }
  if (e.key === 'Enter') {
    var val = e.target.value.trim();
    if (!val) return;
    _boardAddingTask = false;
    _boardTplList = null;
    var group = _currentGroup();
    var lane = _boardSelectedLane;
    send({ cmd: 'board_add_task', task: val, group: group, lane: lane });
    renderBoard();
  }
}

function _boardCloseTplListHandler(e) {
  var list = document.querySelector('.board-tpl-list');
  if (list && !list.contains(e.target)) {
    _boardTplList = null;
    document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
    renderBoard();
  }
}

function boardToggleTemplateList() {
  if (_boardTplList !== null) {
    _boardTplList = null;
    document.removeEventListener('mousedown', _boardCloseTplListHandler, true);
    renderBoard();
  } else {
    _boardTplDropdownWaiting = true;
    send({ cmd: 'list_templates', group: _currentGroup() });
  }
}

function _boardTplItemHtml(tpl) {
  var tplName = esc(tpl.name).replace(/'/g, "\\'");
  var h = '<button class="board-tpl-item" onclick="_boardPickTemplate(\'' + tplName + '\')">';
  h += '<span class="board-tpl-item-name">' + esc(tpl.name) + '</span>';
  if (tpl.description) h += '<span class="board-tpl-item-desc">' + esc(tpl.description) + '</span>';
  h += '</button>';
  return h;
}

function _boardShowTemplateList(msg) {
  _boardTplDropdownWaiting = false;
  _boardTplList = msg.templates || [];
  renderBoard();
  document.addEventListener('mousedown', _boardCloseTplListHandler, true);
}

function _boardPickTemplate(name) {
  _boardTplList = null;
  _boardAddingTask = false;
  // Open the task modal with the template pre-selected
  _taskEditId = null;
  _taskSelectedTemplate = name;
  _taskTemplateVars = [];
  _taskTemplateVarValues = {};

  document.getElementById('task-modal-title').textContent = 'New Task';
  document.getElementById('task-submit-btn').textContent = 'Create';
  document.getElementById('task-task-input').value = '';
  document.getElementById('task-assignee-input').value = '';
  document.getElementById('task-labels-input').value = '';
  document.getElementById('task-template-vars').innerHTML = '';
  _populateTaskGroupSelect(_currentGroup());
  document.getElementById('modal-task').dataset.lane = _boardSelectedLane || '';

  _taskModalWaiting = true;
  send({ cmd: 'list_templates', group: _currentGroup() });
  document.getElementById('modal-task').classList.add('visible');
  document.getElementById('task-task-input').focus();
}

function _boardPickNoTemplate() {
  _boardTplList = null;
  _boardAddingTask = false;
  openAddTask(_boardSelectedLane);
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
  var reserved = _RESERVED_LANES.indexOf(lane) >= 0;
  if (reserved) return;  // no context menu for reserved lanes

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
  _adjustCtxMenuOverflow();
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

  var reserved = _RESERVED_LANES.indexOf(lane) >= 0;
  var escLane = esc(lane).replace(/'/g, "\\'");
  var html = '';
  if (!reserved) {
    html += '<button onclick="event.stopPropagation();boardStartRenameLane(\'' + escLane + '\')">Rename</button>';
    if (lanes.length > 1) {
      html += '<button class="danger" onclick="boardRemoveLane(\'' + escLane + '\')">Delete lane</button>';
    }
  } else {
    html += '<button disabled>Reserved lane</button>';
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

  // Preview prompt
  html += '<button onclick="boardPreviewPrompt(\'' + taskId + '\')">Preview prompt</button>';

  html += '<div class="ctx-sep"></div>';
  html += '<button class="danger" onclick="boardDeleteTask(\'' + taskId + '\')">Delete</button>';

  menu.innerHTML = html;
  menu.style.top = evt.clientY + 'px';
  menu.style.left = Math.min(evt.clientX, window.innerWidth - 140) + 'px';
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
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

function _handleDispatchTemplateMissing(msg) {
  var taskId = msg.task_id;
  var tplName = msg.template_name || '(unknown)';
  showConfirm('Template "' + tplName + '" not found.\nDispatch without template?').then(function(yes) {
    if (yes) {
      // Task is already linked to an agent — re-dispatch to the same agent
      var t = (state && state.board_tasks || {})[taskId];
      var cmd = { cmd: 'dispatch_task', id: taskId, force_no_template: true };
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

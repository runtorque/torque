/* Board module: selection. */

function handleBoardMoveAcknowledgementResponse(msg) {
  if (!msg || msg.type !== 'task_move_acknowledgement_required') return false;
  var taskId = String(msg.task_id || '').trim();
  if (!taskId || typeof showConfirm !== 'function') return true;
  showConfirm(String(msg.message || 'Closing will leave unmerged code behind.'), {
    title: 'Acknowledge unmerged code',
    label: 'Close anyway',
    variant: 'btn-warning',
  }).then(function(accepted) {
    if (!accepted) return;
    var command = {
      cmd: 'board_move_task',
      id: taskId,
      lane: String(msg.new_lane || 'Done'),
      acknowledge_unmerged: true,
      clear_status: !!msg.clear_status,
    };
    if (msg.position !== undefined && msg.position !== null) {
      command.position = msg.position;
    }
    send(command);
  });
  return true;
}

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

function _boardTaskHasExternalLink(task) {
  if (!task) return false;
  var gh = typeof _boardTaskGithubSync === 'function'
    ? _boardTaskGithubSync(task)
    : {};
  return !!(task.provider || task.external_id || task.external_url
    || (gh && (gh.issue_number || gh.issue_url)));
}

function _boardSelectedExternalLinkTasks() {
  var tasks = _boardSelectedTaskItems();
  var out = [];
  for (var i = 0; i < tasks.length; i++) {
    if (_boardTaskHasExternalLink(tasks[i])) out.push(tasks[i]);
  }
  return out;
}

function _boardBatchEditAgents() {
  var group = _boardSelectedSingleGroup();
  if (!group || !state || !state.agents) return [];
  var out = [];
  for (var id in state.agents) {
    var agent = state.agents[id];
    if (!_boardAgentIsLive(agent) || agent.group !== group) continue;
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
  html += '<span class="board-selection-count ui-badge ui-badge--compact ui-badge--accent ui-badge--count">'
    + count + ' selected</span>';
  // Move to lane dropdown
  html += '<div class="board-selection-dropdown-wrap">';
  html += '<button type="button" id="board-bulk-move-trigger" class="board-selection-btn"'
    + ' aria-haspopup="menu" aria-expanded="false" onclick="boardBulkToggleMove(event)">Move to &#9662;</button>';
  html += '<div class="board-selection-dropdown ui-popover ui-menu" id="board-bulk-move-menu"'
    + ' role="menu" aria-label="Move selected tasks" onkeydown="boardBulkMoveMenuKeydown(event)" style="display:none">';
  for (var i = 0; i < lanes.length; i++) {
    var escLane = esc(lanes[i]).replace(/'/g, "\\'");
    html += '<button type="button" role="menuitem" class="board-selection-dropdown-item ui-menu-item"'
      + ' onclick="boardBulkMove(\'' + escLane + '\')">' + esc(lanes[i]) + '</button>';
  }
  html += '</div></div>';
  // Batch edit
  html += '<div class="board-selection-dropdown-wrap">';
  html += '<button type="button" class="board-selection-btn" aria-haspopup="dialog" aria-expanded="'
    + (_boardBatchEditOpen ? 'true' : 'false') + '" onclick="boardToggleBatchEdit(event)">Batch edit</button>';
  if (_boardBatchEditOpen) {
    html += '<div class="board-selection-dropdown board-selection-batch-panel ui-popover"'
      + ' id="board-batch-edit-panel" role="dialog" aria-label="Batch edit selected tasks">';
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
  var externalLinkTasks = _boardSelectedExternalLinkTasks();
  if (externalLinkTasks.length) {
    var externalSuffix = externalLinkTasks.length === count ? '' : ' (' + externalLinkTasks.length + ')';
    html += '<button class="board-selection-btn" onclick="boardBulkSyncGithub()">Sync' + externalSuffix + '</button>';
    html += '<button class="board-selection-btn" onclick="boardBulkUnlinkGithub()">Unlink' + externalSuffix + '</button>';
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
  if (!menu) return;
  var opening = menu.style.display === 'none';
  menu.style.display = opening ? '' : 'none';
  var trigger = document.getElementById('board-bulk-move-trigger');
  if (trigger) trigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
  if (opening) {
    var focusFirst = function() {
      var first = menu.querySelector && menu.querySelector('.ui-menu-item:not(:disabled)');
      if (first && typeof first.focus === 'function') first.focus();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(focusFirst);
    else focusFirst();
  }
}

function boardCloseSelectionMenus(focusTrigger) {
  var menu = document.getElementById('board-bulk-move-menu');
  if (menu) menu.style.display = 'none';
  var trigger = document.getElementById('board-bulk-move-trigger');
  if (trigger) {
    trigger.setAttribute('aria-expanded', 'false');
    if (focusTrigger && typeof trigger.focus === 'function') trigger.focus();
  }
}

function boardBulkMoveMenuKeydown(evt) {
  if (!evt) return;
  var menu = document.getElementById('board-bulk-move-menu');
  var items = menu && menu.querySelectorAll
    ? Array.prototype.slice.call(menu.querySelectorAll('.ui-menu-item:not(:disabled)'))
    : [];
  if (evt.key === 'Escape') {
    evt.preventDefault();
    evt.stopPropagation();
    boardCloseSelectionMenus(true);
    return;
  }
  if (!items.length || ['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(evt.key) < 0) return;
  var index = items.indexOf(document.activeElement);
  if (evt.key === 'Home') index = 0;
  else if (evt.key === 'End') index = items.length - 1;
  else if (evt.key === 'ArrowDown') index = index < 0 ? 0 : (index + 1) % items.length;
  else index = index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length;
  evt.preventDefault();
  items[index].focus();
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

function boardBulkSyncGithub() {
  var tasks = _boardSelectedExternalLinkTasks();
  if (!tasks.length) return;
  for (var i = 0; i < tasks.length; i++) {
    boardSyncTaskNow(tasks[i].id, { keepMenu: true, quiet: true });
  }
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
}

function boardBulkUnlinkGithub() {
  var tasks = _boardSelectedExternalLinkTasks();
  if (!tasks.length) return;
  for (var i = 0; i < tasks.length; i++) {
    boardClearExternal(tasks[i].id);
  }
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  _boardResetBatchEdit();
  renderBoard();
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
      if (_boardAgentIsLive(a)) agents.push(a);
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

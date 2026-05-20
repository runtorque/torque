/* ------------------------------------------------------------------ */
/* Board card actions, navigation, and quick-edit mutations           */
/* ------------------------------------------------------------------ */

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
    _boardSetQuickEditRefocus('', '');
    renderBoard();
    return;
  }
  _boardQuickEditTask = taskId;
  _boardQuickEditKind = kind;
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = _boardScheduledInputValue(task.scheduled_at);
  _boardSetQuickEditRefocus('', '');
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
  _boardSetQuickEditRefocus('', '');
  renderBoard();
}

function _boardKeepQuickEditOpen(taskId, kind) {
  _boardQuickEditTask = taskId || '';
  _boardQuickEditKind = kind || '';
  _boardSetQuickEditRefocus(_boardQuickEditTask, _boardQuickEditKind);
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

function _boardCtxTaskTitle(task) {
  if (!task) return '';
  return task.task || task.id || '';
}

function _boardCtxTaskJumpButton(label, task) {
  if (!task || !task.id) return '';
  return '<button class="ctx-button-wrap"'
    + ' title="' + esc(label) + '"'
    + ' onclick="event.stopPropagation();boardJumpToTask(\'' + task.id + '\')">'
    + esc(label) + '</button>';
}

function _boardTaskCountsAsDone(task) {
  if (!task) return false;
  if (task.lane === 'Done') return true;
  return task.lane === _boardArchivedLane && task.archived_from_lane === 'Done';
}

function _boardCanMarkTaskVerified(task) {
  var verificationState = _boardVerificationState(task);
  if (!_boardTaskCountsAsDone(task)) return false;
  return verificationState === 'pending' || verificationState === 'attempted';
}

function _boardRenderCardMenu(taskId) {
  var tasks = _boardTasks();
  var task = tasks[taskId];
  var menu = document.getElementById('ctx-menu');
  if (!task || !menu) return;

  var lanes = _boardVisibleLanes();
  var isDerived = !!task.parent_task_id;
  var dependencies = _boardDependencyTasks(task);
  var dependents = _boardActiveDependents(task);
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
  if (task.labels && task.labels.indexOf('torque:human') >= 0 && task.lane !== 'Done') {
    html += '<button onclick="event.stopPropagation();boardOpenResolve(\'' + taskId + '\')">Resolve...</button>';
  }

  if (_boardIsArchived(task)) {
    html += '<button onclick="event.stopPropagation();boardRestoreTask(\'' + taskId + '\')">Restore</button>';
  } else {
    html += '<button onclick="event.stopPropagation();boardArchiveTask(\'' + taskId + '\')">Archive task</button>';
  }
  if (_boardCanMarkTaskVerified(task)) {
    html += '<button onclick="event.stopPropagation();boardMarkTaskVerified(\'' + taskId + '\')">Mark verified</button>';
  }

  // Preview prompt
  html += '<button onclick="boardPreviewPrompt(\'' + taskId + '\')">Preview prompt</button>';
  html += '<button onclick="openTaskArtifactBrowser(\'' + taskId + '\')">Artifacts...</button>';

  if (dependencies.length || dependents.length) {
    html += '<div class="ctx-sep"></div>';
    if (dependencies.length) {
      html += '<button onclick="event.stopPropagation();boardShowDependencyPicker(\'' + taskId + '\')">Jump to dependency...</button>';
    }
    for (var di = 0; di < Math.min(dependents.length, 5); di++) {
      html += _boardCtxTaskJumpButton(
        'Jump to dependent: ' + _boardCtxTaskTitle(dependents[di]),
        dependents[di]
      );
    }
    if (dependents.length > 5) {
      html += '<button disabled>+' + (dependents.length - 5) + ' more dependents</button>';
    }
  }

  html += '<div class="ctx-sep"></div>';
  var groupSyncSettings = state && state.group_settings && task.group
    ? (state.group_settings[task.group] || {})
    : {};
  var githubSyncAvailable = (
    (typeof _boardTaskHasGithubLink === 'function' && _boardTaskHasGithubLink(task))
    || String(groupSyncSettings.board_sync_provider || '').toLowerCase() === 'github'
  );
  if (githubSyncAvailable) {
    var gh = typeof _boardTaskGithubSync === 'function' ? _boardTaskGithubSync(task) : {};
    var hasGithubIssue = !!(task.external_url || task.external_id || gh.issue_number || gh.issue_url);
    if (hasGithubIssue) {
      html += '<button onclick="event.stopPropagation();boardOpenGithubIssue(\'' + taskId + '\')">Open GitHub issue</button>';
    } else {
      html += '<button onclick="event.stopPropagation();boardSyncTaskNow(\'' + taskId + '\')">Create GitHub issue</button>';
    }
    html += '<button onclick="event.stopPropagation();boardSyncTaskNow(\'' + taskId + '\')">Push to GitHub</button>';
    html += '<button onclick="event.stopPropagation();boardSyncTaskNow(\'' + taskId + '\')">Sync GitHub now</button>';
    html += '<button onclick="event.stopPropagation();boardPullPreview(\'' + taskId + '\')">Pull preview</button>';
    html += '<button onclick="event.stopPropagation();boardLinkExternal(\'' + taskId + '\')">Edit external link...</button>';
    if (hasGithubIssue || task.provider || task.external_id || task.external_url) {
      html += '<button onclick="event.stopPropagation();boardClearExternal(\'' + taskId + '\')">Unlink external issue</button>';
    }
  } else if (task.provider || task.external_id || task.external_url) {
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
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardCardMenu(evt, taskId) {
  evt.preventDefault();
  var menu = document.getElementById('ctx-menu');
  if (!menu) return;
  menu.style.top = evt.clientY + 'px';
  menu.style.left = Math.min(evt.clientX, window.innerWidth - 140) + 'px';
  _boardRenderCardMenu(taskId);
}

function boardCopyTaskId(taskId) {
  navigator.clipboard.writeText(taskId).then(function() { _closeCtxMenu(); });
}

function boardShowDependencyPicker(taskId) {
  var task = _boardTasks()[taskId];
  var menu = document.getElementById('ctx-menu');
  if (!task || !menu) return;

  var dependencies = _boardDependencyTasks(task);
  var html = '<button onclick="event.stopPropagation();_boardRenderCardMenu(\'' + taskId + '\')">\u25C2 Back</button>';
  html += '<button class="ctx-label" disabled>Jump to dependency</button>';
  html += '<div class="ctx-sep"></div>';
  if (!dependencies.length) {
    html += '<button disabled>No dependencies</button>';
  } else {
    for (var i = 0; i < dependencies.length; i++) {
      html += _boardCtxTaskJumpButton(_boardCtxTaskTitle(dependencies[i]), dependencies[i]);
    }
  }
  menu.innerHTML = html;
  menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardJumpToTask(taskId) {
  _closeCtxMenu();
  boardNavigateToTask(taskId);
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
  var task = _boardTasks()[id];
  if (task && typeof _boardWideLayoutActive === 'function'
      && _boardWideLayoutActive(document.getElementById('panel-board'))
      && task.lane && task.lane !== _boardSelectedLane) {
    _boardSelectedLane = task.lane;
    if (typeof _boardPersistSelectedLane === 'function') {
      _boardPersistSelectedLane();
    }
  }
  if (_boardQuickEditTask && _boardQuickEditTask !== id) {
    _boardQuickEditTask = '';
    _boardQuickEditKind = '';
    _boardQuickLabelDraft = '';
    _boardQuickDueDraft = '';
    _boardSetQuickEditRefocus('', '');
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
    var lane = _boardSelectedLane || _boardVisibleLanes()[0] || '';
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

function _boardVisibleCardIndexForTask(rootTasks, childrenOf, taskId) {
  var index = 0;
  var found = -1;

  function visit(task) {
    if (!task || found >= 0) return;
    if (task.id === taskId) {
      found = index;
      return;
    }
    index++;
    var children = (childrenOf && childrenOf[task.id]) || [];
    if (!children.length || _boardCollapsedTasks[task.id]) return;
    for (var i = 0; i < children.length; i++) {
      visit(children[i]);
      if (found >= 0) return;
    }
  }

  for (var i = 0; i < rootTasks.length; i++) {
    visit(rootTasks[i]);
    if (found >= 0) break;
  }
  return found;
}

function _boardEnsureRenderLimitForTask(taskId) {
  var lane = _boardSelectedLane || _boardVisibleLanes()[0] || '';
  var model = _boardBuildRenderModel([lane]);
  if (!model.visibleTasks[taskId]) return;
  _boardRevealTaskRootId(taskId, model.visibleTasks);
  var rootTasks = _boardRootTasksForLane(lane, model.visibleTasks, model);
  var cardIndex = _boardVisibleCardIndexForTask(rootTasks, model.childrenOf, taskId);
  if (cardIndex >= 0 && cardIndex >= _boardRenderLimit) {
    _boardRenderLimit = Math.ceil((cardIndex + 1) / 50) * 50;
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

  _boardSelectedLane = task.lane || _boardSelectedLane || _boardVisibleLanes()[0] || '';
  _boardFocusedTask = task.id;
  _boardSelectedTasks = {};
  _boardLastSelectedTask = task.id;
  _boardQuickEditTask = '';
  _boardQuickEditKind = '';
  _boardQuickLabelDraft = '';
  _boardQuickDueDraft = '';
  _boardSetQuickEditRefocus('', '');
  _boardCardsScrollTop = 0;
  _boardResetBatchEdit();
  _boardEnsureRenderLimitForTask(task.id);
  _boardRevealFocusOnRender = true;
  if (typeof _boardPersistSelectedLane === 'function') {
    _boardPersistSelectedLane();
  }
  _boardPersistFilterState();

  var boardVisible = typeof _panelAppVisible === 'function'
    ? _panelAppVisible('board')
    : (typeof _activePanelApp !== 'undefined'
      ? _activePanelApp === 'board'
      : (typeof _boardPanelVisible === 'function' ? _boardPanelVisible() : true));
  if (!boardVisible && typeof togglePanel === 'function') {
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
    task.labels = labels.slice();
    send({ cmd: 'board_update_task', id: taskId, labels: labels });
    _boardQuickLabelDraft = '';
  }
  _boardKeepQuickEditOpen(taskId, 'labels');
}

function boardQuickRemoveLabel(taskId, label) {
  var task = _boardTasks()[taskId];
  if (!task) return;
  var labels = (task.labels || []).filter(function(item) { return item !== label; });
  task.labels = labels.slice();
  send({
    cmd: 'board_update_task',
    id: taskId,
    labels: labels
  });
  _boardKeepQuickEditOpen(taskId, 'labels');
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

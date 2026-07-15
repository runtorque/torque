/* Board module: external sync. */

function boardEditTask(taskId) {
  _closeCtxMenu();
  openEditTask(taskId);
}

function boardDuplicateTask(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  // In compact mode the local task card omits description / action_vars /
  // agent_template. Hydrate the full BoardTask before cloning so we don't
  // silently drop those fields on duplicate.
  if (typeof ensureTaskDetail === 'function'
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)) {
    ensureTaskDetail(taskId, function() { boardDuplicateTask(taskId); });
    return;
  }
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
  if (typeof ensureTaskDetail === 'function'
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)) {
    ensureTaskDetail(taskId, function() { boardCloneTask(taskId); });
    return;
  }
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
  var task = _boardTasks()[taskId];
  if (!task) return;
  if (task.lane !== 'Done') {
    return showConfirm('Archive this task in `' + (task.lane || 'this lane')
      + '`? It will be removed from the board.', {
      label: 'Archive',
      variant: 'btn-danger',
    }).then(function(ok) {
      if (ok) _boardArchiveTaskIds([taskId], true);
    });
  }
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
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Import External Task',
    fields: [
      { key: 'ref', label: 'External reference or URL', autofocus: true },
      { key: 'group', label: 'Group', defaultValue: _currentGroup() || '' },
    ],
    submitLabel: 'Import',
  }).then(function(values) {
    if (!values || !values.ref || !values.group) return;
    send({
      cmd: 'external_import_task',
      ref: values.ref.trim(),
      group: values.group,
      lane: _boardSelectedLane || '',
    });
  });
}

function boardDetachTask(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;
  var labels = (task.labels || []).filter(function(l) { return l !== 'torque:derived'; });
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
      if (_boardAgentIsLive(a)) {
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
  if (typeof openContextMenuSurface === 'function') openContextMenuSurface(menu);
  else menu.classList.add('open');
  _adjustCtxMenuOverflow();
}

function boardDoLinkAgent(taskId, agentId) {
  _closeCtxMenu();
  send({ cmd: 'board_update_task', id: taskId, agent_id: agentId });
}

function _boardSyncForEditedExternalLink(task) {
  var sync = _boardTaskSync(task);
  if (!sync.github || typeof sync.github !== 'object') return null;
  var cleaned = Object.assign({}, sync);
  delete cleaned.github;
  delete cleaned.last_synced_hash;
  delete cleaned.last_seen_provider_updated_at;
  delete cleaned.last_push_at;
  delete cleaned.last_pull_at;
  cleaned.version = cleaned.version || 1;
  cleaned.provider = cleaned.provider || 'github';
  return cleaned;
}

function boardLinkExternal(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  var gh = _boardTaskGithubSync(task);
  var refDefault = task.external_url || gh.issue_url || ((task.provider && task.external_id)
    ? (task.provider + ':' + task.external_id) : (task.external_id || ''));
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Link External Task',
    fields: [
      {
        key: 'ref',
        label: 'External reference or URL',
        defaultValue: refDefault,
        autofocus: true,
      },
    ],
    submitLabel: 'Save',
  }).then(function(values) {
    if (!values) return;
    var trimmedRef = values.ref.trim();
    var payload = {
      cmd: 'external_link_task',
      id: taskId,
      ref: trimmedRef,
      provider: task.provider || '',
      external_id: task.external_id || '',
      external_url: task.external_url || '',
    };
    if (trimmedRef) {
      var boardSync = _boardSyncForEditedExternalLink(task);
      if (boardSync) payload.board_sync = boardSync;
    }
    send(payload);
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
    board_sync: {
      version: 1,
      enabled: false,
    },
  });
}

function boardOpenExternal(taskId) {
  _closeCtxMenu();
  send({ cmd: 'external_open_task', id: taskId });
}

function boardOpenGithubIssue(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  var gh = _boardTaskGithubSync(task);
  var url = String((gh && gh.issue_url) || (task && task.external_url) || '').trim();
  if (url && typeof window !== 'undefined' && window.open) {
    window.open(url);
    return;
  }
  send({ cmd: 'external_open_task', id: taskId });
}

function _boardBoardSyncPayload(cmd, args) {
  if (typeof _boardSyncCommandPayload === 'function') {
    return _boardSyncCommandPayload(cmd, args);
  }
  var payload = { cmd: cmd, args: Object.assign({}, args || {}) };
  args = args || {};
  for (var key in args) payload[key] = args[key];
  return payload;
}

function _boardTaskSync(task) {
  return (task && task.board_sync && typeof task.board_sync === 'object')
    ? task.board_sync
    : {};
}

function _boardTaskGithubSync(task) {
  var sync = _boardTaskSync(task);
  return (sync.github && typeof sync.github === 'object') ? sync.github : {};
}

function _boardTaskHasGithubLink(task) {
  if (!task) return false;
  if (String(task.provider || '').toLowerCase() === 'github') return true;
  if (String(_boardTaskSync(task).provider || '').toLowerCase() === 'github') return true;
  return !!(_boardTaskGithubSync(task).issue_number || _boardTaskGithubSync(task).issue_url);
}

function boardSyncTaskNow(taskId, opts) {
  opts = opts || {};
  if (opts.keepMenu !== true) _closeCtxMenu();
  if (!taskId) return;
  send(_boardBoardSyncPayload('board_sync_task', { task: taskId }));
  if (opts.showQueuedImmediately && !opts.quiet && typeof _showToast === 'function') {
    _showToast('GitHub sync queued', 'info');
  }
}

function boardPullPreview(taskId, opts) {
  opts = opts || {};
  if (opts.keepMenu !== true) _closeCtxMenu();
  if (!taskId) return;
  send(_boardBoardSyncPayload('board_pull_preview', { task: taskId }));
  if (!opts.quiet && typeof _showToast === 'function') {
    _showToast('Loading GitHub pull preview…', 'info');
  }
}

function _boardShowSyncErrorToast(message, taskId) {
  message = String(message || 'GitHub sync failed').trim() || 'GitHub sync failed';
  if (!taskId || !document || !document.createElement || !document.body) {
    if (typeof _showToast === 'function') _showToast(message, 'error');
    return;
  }
  var el = document.createElement('div');
  el.className = 'toast toast-error toast-actionable';
  var text = document.createElement('span');
  text.textContent = message.length > 180 ? (message.slice(0, 177) + '…') : message;
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = 'Retry';
  btn.onclick = function(ev) {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    boardSyncTaskNow(taskId, { quiet: true });
    if (el.remove) el.remove();
  };
  el.appendChild(text);
  el.appendChild(btn);
  document.body.appendChild(el);
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(function() { el.classList.add('visible'); });
  } else {
    el.classList.add('visible');
  }
  setTimeout(function() {
    el.classList.remove('visible');
    setTimeout(function() { if (el.remove) el.remove(); }, 300);
  }, 8000);
}

function _boardSyncResponseMessage(msg) {
  return String(
    (msg && (msg.error || msg.message || msg.reason))
      || 'GitHub sync request failed'
  );
}

function _handleBoardSyncTaskResponse(msg) {
  if (!msg || msg.type !== 'board_sync_task') return;
  var taskId = String(msg.task_id || msg.task || msg.id || '').trim();
  if (msg.ok && msg.queued) {
    if (typeof _showToast === 'function') _showToast('GitHub sync queued', 'success');
    return;
  }
  if (!msg.ok) _boardShowSyncErrorToast(_boardSyncResponseMessage(msg), taskId);
}

var _boardPullPreviewTaskId = '';
var _boardPullPreviewPayload = null;

function _boardPullPreviewChanges(msg) {
  if (!msg || typeof msg !== 'object') return {};
  if (msg.changes && typeof msg.changes === 'object') return msg.changes;
  if (msg.preview && msg.preview.changes && typeof msg.preview.changes === 'object') {
    return msg.preview.changes;
  }
  return {};
}

function _boardFormatPullValue(value) {
  if (Array.isArray(value)) return value.join(', ');
  if (value && typeof value === 'object') {
    try { return JSON.stringify(value); } catch (_e) { return String(value); }
  }
  return value === undefined || value === null ? '' : String(value);
}

function _showBoardPullPreview(msg) {
  var modal = document.getElementById('modal-board-pull-preview');
  var summaryEl = document.getElementById('board-pull-preview-summary');
  var changesEl = document.getElementById('board-pull-preview-changes');
  var applyBtn = document.getElementById('board-pull-preview-apply-btn');
  if (!modal || !summaryEl || !changesEl) return;
  _boardPullPreviewTaskId = String(msg.task_id || msg.task || msg.id || '');
  _boardPullPreviewPayload = msg;
  var changes = _boardPullPreviewChanges(msg);
  var fields = Object.keys(changes).sort();
  var task = _boardTasks()[_boardPullPreviewTaskId] || null;
  summaryEl.textContent = fields.length
    ? ('Review ' + fields.length + ' inbound change' + (fields.length === 1 ? '' : 's')
      + (task && task.task ? ' for “' + task.task + '”.' : '.'))
    : 'No inbound differences found.';
  var html = '';
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i];
    var change = changes[field] || {};
    html += '<label class="board-pull-preview-row">';
    html += '<input type="checkbox" class="board-pull-preview-field" value="' + esc(field) + '" checked>';
    html += '<span class="board-pull-preview-field-name">' + esc(field) + '</span>';
    html += '<span class="board-pull-preview-values">';
    html += '<span><em>Local</em>' + esc(_boardFormatPullValue(change.local)) + '</span>';
    html += '<span><em>GitHub</em>' + esc(_boardFormatPullValue(change.remote)) + '</span>';
    html += '</span>';
    html += '</label>';
  }
  changesEl.innerHTML = html || '<div class="board-pull-preview-empty">Everything is already in sync.</div>';
  if (applyBtn) applyBtn.disabled = !fields.length;
  if (typeof openNestedModal === 'function') openNestedModal('modal-board-pull-preview');
  else modal.classList.add('visible');
}

function closeBoardPullPreview() {
  _boardPullPreviewTaskId = '';
  _boardPullPreviewPayload = null;
  if (typeof closeNestedModal === 'function'
      && closeNestedModal('modal-board-pull-preview')) return;
  var modal = document.getElementById('modal-board-pull-preview');
  if (modal) {
    modal.classList.remove('visible');
    modal.classList.remove('modal-nested');
  }
}

function applyBoardPullPreview() {
  if (!_boardPullPreviewTaskId) return;
  var fields = [];
  var checks = document.querySelectorAll
    ? document.querySelectorAll('#board-pull-preview-changes .board-pull-preview-field')
    : [];
  for (var i = 0; i < checks.length; i++) {
    if (checks[i].checked) fields.push(checks[i].value);
  }
  if (!fields.length) {
    if (typeof _showToast === 'function') _showToast('Select at least one field to apply.', 'warning');
    return;
  }
  send(_boardBoardSyncPayload('board_pull_apply', {
    task: _boardPullPreviewTaskId,
    fields: fields,
  }));
}

function _handleBoardPullPreview(msg) {
  if (!msg || msg.type !== 'board_pull_preview') return;
  if (msg.ok) {
    _showBoardPullPreview(msg);
    return;
  }
  _boardShowSyncErrorToast(_boardSyncResponseMessage(msg), String(msg.task_id || ''));
}

function _handleBoardPullApply(msg) {
  if (!msg || msg.type !== 'board_pull_apply') return;
  if (msg.ok) {
    if (typeof _showToast === 'function') _showToast('Applied GitHub changes', 'success');
    closeBoardPullPreview();
    return;
  }
  _boardShowSyncErrorToast(_boardSyncResponseMessage(msg), String(msg.task_id || ''));
}

function boardPushExternalStatus(taskId) {
  _closeCtxMenu();
  var task = _boardTasks()[taskId];
  if (!task) return;
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Push External Status',
    fields: [
      {
        key: 'status',
        label: 'External status',
        defaultValue: task.status || task.lane || '',
        autofocus: true,
      },
      { key: 'note', label: 'Optional note', defaultValue: '' },
    ],
    submitLabel: 'Push',
  }).then(function(values) {
    if (!values) return;
    send({
      cmd: 'external_push_task_status',
      id: taskId,
      status: values.status.trim(),
      note: values.note.trim(),
    });
  });
}

function boardPostExternalComment(taskId) {
  _closeCtxMenu();
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Post External Comment',
    fields: [
      {
        key: 'comment',
        label: 'Comment to post externally',
        multiline: true,
        autofocus: true,
      },
    ],
    submitLabel: 'Post',
  }).then(function(values) {
    if (!values || !values.comment.trim()) return;
    send({ cmd: 'external_post_task_comment', id: taskId, comment: values.comment.trim() });
  });
}

/* ---- Dispatch task to agent ----------------------------------------- */

function boardDispatchTask(taskId) {
  _closeCtxMenu();
  var tasks = _boardTasks();
  var task = tasks[taskId];
  if (!task) return;

  // If task already has an assigned live agent, dispatch directly.
  if (task.agent_id && _boardAgentIsLive(state.agents[task.agent_id])) {
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
      if (_boardAgentIsLive(a) && a.group === task.group) {
        html += '<button onclick="boardDispatchToExisting(\'' + taskId + '\',\'' + id + '\')">'
          + esc(a.name) + '</button>';
      }
    }
  }

  menu.innerHTML = html;
  if (typeof openContextMenuSurface === 'function') openContextMenuSurface(menu);
  else menu.classList.add('open');
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
  _boardResetRenderLimits();
  if (_boardShowArchived && typeof lazyLoadArchivedTasks === 'function') {
    lazyLoadArchivedTasks(_currentGroup ? _currentGroup() : '');
  }
  renderBoard();
}

function boardArchiveSuggestedDone() {
  var ids = _boardStaleDoneTaskIds();
  if (!ids.length) return;
  if (typeof _showToast === 'function') {
    _showToast('Archiving ' + ids.length + ' stale completed task'
      + (ids.length === 1 ? '' : 's') + '…', 'info');
  }
  send({
    cmd: 'board_archive_tasks',
    ids: ids,
  });
  renderBoard();
}

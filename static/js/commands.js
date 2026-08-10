/* Commands — actions sent to the daemon */

var _daemonStopRequestedByUser = false;
var _daemonStopBannerShown = false;
var _daemonStopBannerTimer = 0;

/* Open a board task in the Board panel — used by clickable task IDs on
   worker / engineer / agent cards. Activates the board panel if needed
   then scrolls the matching card into view + adds a brief highlight.
   No-op if the task id is missing or the board fails to open. */
function openTaskInBoard(taskId) {
  var id = String(taskId || '').trim();
  if (!id) return;
  if (typeof _activePanelApp === 'undefined' || _activePanelApp !== 'board') {
    if (typeof togglePanel === 'function') togglePanel('board');
  }
  // Defer scroll/highlight one tick so the panel + board render finishes.
  setTimeout(function() {
    var card = document.querySelector('.board-card[data-task-id="' + id.replace(/"/g, '\\"') + '"]');
    if (!card) return;
    if (typeof card.scrollIntoView === 'function') {
      card.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
    }
    card.classList.add('board-card--external-focus');
    setTimeout(function() { card.classList.remove('board-card--external-focus'); }, 1500);
  }, 80);
}

function _toastStack() {
  var stack = document.getElementById('toast-stack');
  if (stack) return stack;
  stack = document.createElement('div');
  stack.id = 'toast-stack';
  stack.className = 'toast-stack';
  stack.setAttribute('aria-live', 'polite');
  stack.setAttribute('aria-relevant', 'additions');
  document.body.appendChild(stack);
  return stack;
}

function _dismissToast(el) {
  if (!el || el._torqueClosing) return;
  el._torqueClosing = true;
  if (el._torqueTimer) clearTimeout(el._torqueTimer);
  el.classList.remove('visible');
  setTimeout(function() {
    if (el.remove) el.remove();
    else if (el.parentNode) el.parentNode.removeChild(el);
  }, 180);
}

function _noticeToastSourceAgentId(notice) {
  if (!notice
      || notice.notice_type !== 'notification'
      || String(notice.category || '') !== 'direct_message'
      || String(notice.severity || '').toLowerCase() === 'error') {
    return '';
  }
  var agentId = String(notice.agent_id || '').trim();
  if (!agentId || !state || !state.agents) return '';
  var agent = state.agents[agentId];
  if (!agent || agent.cell_type !== 'agent') return '';
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent)) {
    return '';
  }
  if (typeof _agentFocusVisibleRootAgentId === 'function'
      && _agentFocusVisibleRootAgentId(agentId) !== agentId) {
    return '';
  }
  return agentId;
}

function _showToast(message, level, opts) {
  opts = opts || {};
  var kind = String(level || 'info');
  var el = document.createElement('div');
  el.className = 'toast toast-' + kind;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

  var content = document.createElement('div');
  content.className = 'toast-content';
  if (opts.title) {
    var title = document.createElement('strong');
    title.className = 'toast-title';
    title.textContent = opts.title;
    content.appendChild(title);
  }
  var text = document.createElement('span');
  text.className = 'toast-message';
  text.textContent = String(message || '');
  content.appendChild(text);
  if (typeof opts.onBodyAction === 'function') {
    content.classList.add('toast-content--interactive');
    content.setAttribute('role', 'button');
    content.setAttribute('tabindex', '0');
    content.setAttribute('aria-label', opts.bodyActionLabel || 'Open notification');
    var runBodyAction = function(event) {
      if (event && event.stopPropagation) event.stopPropagation();
      opts.onBodyAction();
      _dismissToast(el);
    };
    content.onclick = runBodyAction;
    content.onkeydown = function(event) {
      var key = event && (event.key || event.code);
      if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar' && key !== 'Space') return;
      if (event && event.preventDefault) event.preventDefault();
      runBodyAction(event);
    };
  }
  el.appendChild(content);

  var actions = null;
  if (opts.actionLabel && typeof opts.onAction === 'function') {
    actions = document.createElement('div');
    actions.className = 'toast-actions';
    var action = document.createElement('button');
    action.type = 'button';
    action.className = 'toast-action';
    action.textContent = opts.actionLabel;
    action.onclick = function(event) {
      if (event && event.stopPropagation) event.stopPropagation();
      opts.onAction();
      _dismissToast(el);
    };
    actions.appendChild(action);
    el.appendChild(actions);
  }

  var close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', 'Dismiss message');
  close.textContent = '×';
  close.onclick = function(event) {
    if (event && event.stopPropagation) event.stopPropagation();
    _dismissToast(el);
  };
  el.appendChild(close);
  var stack = _toastStack();
  stack.insertBefore(el, stack.firstChild);

  var defaultDuration = kind === 'error' ? 0 : (kind === 'warning' ? 8000 : 5000);
  var duration = Object.prototype.hasOwnProperty.call(opts, 'duration')
    ? Number(opts.duration)
    : defaultDuration;
  var remaining = Math.max(0, Number.isFinite(duration) ? duration : defaultDuration);
  var startedAt = 0;

  function startTimer() {
    if (!remaining || el._torqueClosing) return;
    startedAt = Date.now();
    el._torqueTimer = setTimeout(function() { _dismissToast(el); }, remaining);
  }
  function pauseTimer() {
    if (!el._torqueTimer) return;
    clearTimeout(el._torqueTimer);
    el._torqueTimer = 0;
    remaining = Math.max(0, remaining - (Date.now() - startedAt));
  }
  if (typeof el.addEventListener === 'function') {
    el.addEventListener('mouseenter', pauseTimer);
    el.addEventListener('mouseleave', startTimer);
    el.addEventListener('focusin', pauseTimer);
    el.addEventListener('focusout', startTimer);
  }
  requestAnimationFrame(function() { el.classList.add('visible'); });
  startTimer();
  return el;
}

function _showNoticeToast(notice) {
  if (!notice) return null;
  var isAlert = notice.notice_type === 'alert';
  var sourceAgentId = _noticeToastSourceAgentId(notice);
  var sourceAgent = sourceAgentId && state && state.agents
    ? state.agents[sourceAgentId]
    : null;
  var sourceAgentLabel = String((sourceAgent && sourceAgent.name) || 'source agent');
  return _showToast(
    notice.message || notice.title || 'Torque',
    isAlert ? 'error' : (notice.severity || 'info'),
    {
      title: notice.title || (isAlert ? 'Alert' : 'Notification'),
      duration: isAlert ? 0 : 8000,
      actionLabel: 'View inbox',
      onAction: function() {
        if (typeof openInboxPopover === 'function') openInboxPopover();
      },
      bodyActionLabel: sourceAgentId ? 'Open conversation with ' + sourceAgentLabel : '',
      onBodyAction: sourceAgentId
        ? function() {
            var currentAgentId = _noticeToastSourceAgentId(notice);
            if (currentAgentId && typeof focusAgent === 'function') {
              focusAgent(currentAgentId);
            }
          }
        : null,
    }
  );
}

function _applySystemBanner(banner) {
  const el = document.getElementById('system-banner');
  if (!el) return;
  if (!banner || !banner.message) {
    el.hidden = true;
    el.textContent = '';
    el.removeAttribute('data-kind');
    return;
  }
  el.hidden = false;
  el.setAttribute('data-kind', banner.kind || 'info');
  el.textContent = banner.message;
}

function _showDaemonStoppedBanner() {
  if (_daemonStopBannerShown) return;
  _daemonStopBannerShown = true;
  _applySystemBanner({
    kind: 'daemon_stopped',
    message: 'Torque daemon stopped. Relaunch via `make standalone` to reconnect.',
  });
  if (typeof setTimeout === 'function') {
    if (_daemonStopBannerTimer && typeof clearTimeout === 'function') {
      clearTimeout(_daemonStopBannerTimer);
    }
    _daemonStopBannerTimer = setTimeout(function() {
      _applySystemBanner(null);
      _daemonStopBannerTimer = 0;
    }, 60000);
  }
}

function _clearDaemonStoppedBanner() {
  _daemonStopRequestedByUser = false;
  _daemonStopBannerShown = false;
  if (_daemonStopBannerTimer && typeof clearTimeout === 'function') {
    clearTimeout(_daemonStopBannerTimer);
    _daemonStopBannerTimer = 0;
  }
  _applySystemBanner(null);
}

function _worktreeSharedWith(cell) {
  if (!cell.worktree_path || !state.agents) return '';
  var names = [];
  for (var aid in state.agents) {
    var a = state.agents[aid];
    if (a.id !== cell.id && a.worktree_path === cell.worktree_path) names.push(a.name);
  }
  return names.length ? '"' + names.join('", "') + '"' : '';
}

function _selectionAgentRootId(id) {
  if (!state || !state.agents || !id || !state.agents[id]) return '';
  var cell = state.agents[id];
  if (cell.cell_type === 'terminal') {
    if (cell.parent_id && state.agents[cell.parent_id]) return cell.parent_id;
    return '';
  }
  return cell.id || '';
}

function _syncPanelsAfterSelectionChange(prevSelectedId) {
  if (prevSelectedId === selectedAgentId) return;
  if (typeof refreshSelectedAgentFocus === 'function') {
    refreshSelectedAgentFocus(prevSelectedId || '');
  }
  var standaloneVisible = typeof _standaloneVisiblePanelApps === 'function'
    && _standalonePanelsEnabled()
    && _standaloneVisiblePanelApps().length > 0;
  if (typeof renderActivePanel === 'function'
      && (standaloneVisible
        || (typeof _activePanelApp !== 'undefined' && _activePanelApp))) {
    renderActivePanel();
  }
}

function _updateSelectedAgentContext(id) {
  var nextSelectedId = _selectionAgentRootId(id);
  var changed = selectedAgentId !== nextSelectedId;
  if (nextSelectedId) selectedAgentId = nextSelectedId;
  else if (typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) selectedAgentId = '';
  return changed;
}

function _persistSelectedAgentFromLocal() {
  if (typeof send === 'function') {
    send({ cmd: 'ui_select_agent', id: selectedAgentId || '' });
  }
}

function onGroupTabClick(group, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  if (typeof setActiveGroup !== 'function') return;
  setActiveGroup(group);
}

function focusAgent(id) {
  var prevSelectedId = selectedAgentId;
  focusedItemId = id;
  _updateSelectedAgentContext(id);
  if (typeof _agentFocusPersistExplicitSelection === 'function') {
    _agentFocusPersistExplicitSelection(id);
  }
  selectedTerminalId = id;
  _syncPanelsAfterSelectionChange(prevSelectedId);
  if (typeof renderAgentFocusPanel === 'function') renderAgentFocusPanel();
  if (typeof renderTerminalWorkspace === 'function' && typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) {
    renderTerminalWorkspace();
    if (typeof focusEmbeddedTerminalWorkspace === 'function') {
      focusEmbeddedTerminalWorkspace(true);
    }
  }
  send({ cmd: 'focus_agent', id });
}

function onAgentClick(id) {
  var prevSelectedId = selectedAgentId;
  focusedItemId = id;
  selectedTerminalId = id;
  if (typeof _agentFocusPersistExplicitSelection === 'function') {
    _agentFocusPersistExplicitSelection(id);
  }
  if (typeof renderTerminalWorkspace === 'function' && typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) {
    _updateSelectedAgentContext(id);
    _syncPanelsAfterSelectionChange(prevSelectedId);
    renderTerminalWorkspace();
    if (typeof focusEmbeddedTerminalWorkspace === 'function') {
      focusEmbeddedTerminalWorkspace(true);
    }
    send({ cmd: 'focus_agent', id });
    return;
  }
  if (selectedAgentId === id) {
    // Already selected → focus the agent's terminal session
    send({ cmd: 'focus_agent', id });
  } else {
    // Select this agent → show its terminal drawer
    _updateSelectedAgentContext(id);
    if (selectedAgentId) {
      send({ cmd: 'select_agent', id: selectedAgentId });
    }
    if (state.global_settings && state.global_settings.focus_on_click) {
      send({ cmd: 'focus_agent', id });
    }
    _syncPanelsAfterSelectionChange(prevSelectedId);
  }
}

function onAgentDblClick(id) {
  var prevSelectedId = selectedAgentId;
  focusedItemId = id;
  _updateSelectedAgentContext(id);
  if (typeof _agentFocusPersistExplicitSelection === 'function') {
    _agentFocusPersistExplicitSelection(id);
  }
  selectedTerminalId = id;
  send({ cmd: 'focus_agent', id });
  _syncPanelsAfterSelectionChange(prevSelectedId);
  if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
  if (typeof renderTerminalWorkspace === 'function'
      && typeof isEmbeddedTerminalMode === 'function'
      && isEmbeddedTerminalMode()
      && typeof focusEmbeddedTerminalWorkspace === 'function') {
    focusEmbeddedTerminalWorkspace(true);
  }
}

async function removeAgent(id) {
  const a = state.agents[id];
  if (!a) return;
  const name = a.name || a.id || id;
  const deleteConfirm = (message) => showConfirm(message, {
    label: 'Delete',
    variant: 'btn-danger',
  });
  if ((a.cell_type || '') === 'terminal') {
    if (await deleteConfirm(`Delete "${name}"? This terminal will close.`)) {
      if (selectedAgentId === id) selectedAgentId = null;
      if (selectedTerminalId === id) selectedTerminalId = null;
      send({ cmd: 'remove_agent', id });
    }
    return;
  }
  if ((a.kind || '') === 'architect') {
    var hiredEngineers = 0;
    if (state && state.agents) {
      for (var architectAgentId in state.agents) {
        var candidate = state.agents[architectAgentId];
        if (!candidate || candidate.cell_type !== 'agent') continue;
        if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(candidate)) continue;
        if ((candidate.kind || '') !== 'engineer') continue;
        if (String(candidate.hired_by_architect_id || '') === String(a.id || '')) {
          hiredEngineers += 1;
        }
      }
    }
    var decisionCount = 0;
    if (typeof _architectDecisionsForAgent === 'function') {
      decisionCount = _architectDecisionsForAgent(a.id)
        .filter(function(decision) { return !(decision && decision.archived); })
        .length;
    }
    var architectMsg = 'Delete "' + name + '"? '
      + hiredEngineers + ' hired engineer' + (hiredEngineers === 1 ? '' : 's')
      + ' will be transferred to the user, ' + decisionCount + ' decision'
      + (decisionCount === 1 ? '' : 's') + ' will be archived. '
      + 'The architect will be scheduled for permanent deletion in 7 days \u2014 '
      + 'you can restore it from Recently deleted before then.';
    if (await deleteConfirm(architectMsg)) {
      if (selectedAgentId === id) selectedAgentId = null;
      if (selectedTerminalId === id) selectedTerminalId = null;
      send({ cmd: 'delete_architect', id });
    }
    return;
  }
  const childCount = (state.children[id] || []).length;
  const kind = (a.kind || '') === 'engineer' ? 'engineer'
    : ((a.kind || '') === 'worker' ? 'worker' : 'agent');
  let msg = `Delete "${name}"? `;
  if (kind === 'engineer') {
    msg += 'Owned workers and assigned tasks will be transferred to the user. ';
  } else if (childCount > 0) {
    msg = `Delete "${name}" and its ${childCount} terminal(s)? `;
  }
  msg += `The ${kind}${childCount > 0 && kind !== 'engineer' ? ' and its terminal(s)' : ''} will be scheduled for permanent deletion in 7 days \u2014 you can restore it from Recently deleted before then.`;
  if (kind === 'engineer' && childCount > 0) {
    msg += ' Its terminal(s) are included in the restore window.';
  }
  if (a.worktree_path) {
    var sharedWith = _worktreeSharedWith(a);
    if (sharedWith) {
      msg += ' Its worktree is shared with ' + sharedWith + ' and will be kept.';
    } else {
      const warnings = [];
      if ((a.worktree_checkpoints || 0) > 0) warnings.push('unmerged commits');
      if (a.worktree_dirty) warnings.push('uncommitted changes');
      if (warnings.length) {
        msg += ' Its worktree has ' + warnings.join(' and ')
          + '. The worktree is preserved during the 7-day restore window; if not restored, all changes will be lost.';
      } else {
        msg += ' Its worktree will be preserved during the 7-day restore window, then permanently removed.';
      }
    }
  }
  if (await deleteConfirm(msg)) {
    if (selectedAgentId === id) selectedAgentId = null;
    if (selectedTerminalId === id) selectedTerminalId = null;
    send({ cmd: 'remove_agent', id });
  }
}

function relaunchAgent(id) {
  const cell = state.agents ? state.agents[id] : null;
  const gs = cell && state.group_settings ? state.group_settings[cell.group] : null;
  if (cell && cell.status === 'stopped' && gs && gs.engineer_agent_id === id) {
    openEngineerLaunchDialog(cell.group, id);
    return;
  }
  send({ cmd: 'relaunch_agent', id });
}

function _activeSessionCell() {
  if (!state || !state.active_session_id || !state.agents) return null;
  for (var id in state.agents) {
    var cell = state.agents[id];
    if (cell && cell.session_id === state.active_session_id) return cell;
  }
  return null;
}

function _currentArchitectSessionCell() {
  var cell = _activeSessionCell();
  return cell && (cell.kind || '') === 'architect' ? cell : null;
}

function _isEngineerDismissedCell(cell) {
  return !!(
    cell
    && (cell.kind || '') === 'engineer'
    && Number(cell.dismissed_at || 0) > 0
  );
}

function _isArchitectDismissedCell(cell) {
  return !!(
    cell
    && (cell.kind || '') === 'architect'
    && Number(cell.dismissed_at || 0) > 0
  );
}

function _isLifecycleDismissedCell(cell) {
  return _isEngineerDismissedCell(cell) || _isArchitectDismissedCell(cell);
}

function _canCurrentSessionManageEngineer(engineer) {
  if (!engineer || (engineer.kind || '') !== 'engineer') return false;
  var architect = _currentArchitectSessionCell();
  if (!architect) return true;
  return String(engineer.hired_by_architect_id || '').trim() === String(architect.id || '').trim();
}

function _engineerLifecyclePayload(cmd, engineer) {
  var payload = {
    cmd: cmd,
    engineer_id: engineer.id,
  };
  var architect = _currentArchitectSessionCell();
  if (architect) payload.architect_id = architect.id;
  return payload;
}

function _engineerOwnedWorkerCount(engineerId) {
  if (!state || !state.agents || !engineerId) return 0;
  var count = 0;
  for (var id in state.agents) {
    var cell = state.agents[id];
    if (!cell || cell.id === engineerId) continue;
    if (cell.cell_type !== 'agent') continue;
    var ownerId = String(
      cell.owner_engineer_id
      || cell.created_by_engineer_id
      || ''
    ).trim();
    if (ownerId === String(engineerId)) count += 1;
  }
  return count;
}

async function dismissEngineer(id) {
  const engineer = state.agents ? state.agents[id] : null;
  if (!engineer || !_canCurrentSessionManageEngineer(engineer)) return;
  if (_isEngineerDismissedCell(engineer)) return;
  var workerCount = _engineerOwnedWorkerCount(engineer.id);
  var msg = 'Dismiss "' + (engineer.name || engineer.id) + '"? This closes the engineer terminal';
  if (workerCount > 0) {
    msg += ' and ' + workerCount + ' owned worker terminal' + (workerCount === 1 ? '' : 's');
  }
  msg += '. You can rehire the engineer later from this menu.';
  if (await showConfirm(msg, { label: 'Dismiss engineer', variant: 'btn-danger' })) {
    var payload = _engineerLifecyclePayload('architect_engineer_dismiss', engineer);
    payload.reason = 'Dismissed from UI';
    send(payload);
  }
}

function rehireEngineer(id) {
  const engineer = state.agents ? state.agents[id] : null;
  if (!engineer || !_canCurrentSessionManageEngineer(engineer)) return;
  send(_engineerLifecyclePayload('architect_engineer_rehire', engineer));
}

async function dismissArchitect(id) {
  const architect = state.agents ? state.agents[id] : null;
  if (!architect || (architect.kind || '') !== 'architect') return;
  if (_isArchitectDismissedCell(architect)) return;
  var engineerCount = 0;
  if (state && state.agents) {
    for (var agentId in state.agents) {
      var candidate = state.agents[agentId];
      if (!candidate || (candidate.kind || '') !== 'engineer') continue;
      if (String(candidate.hired_by_architect_id || '') === String(architect.id || '')) {
        engineerCount += 1;
      }
    }
  }
  var msg = 'Dismiss "' + (architect.name || architect.id) + '"? This closes the architect terminal';
  if (engineerCount > 0) {
    msg += '. Its ' + engineerCount + ' hired engineer' + (engineerCount === 1 ? '' : 's')
      + ' will stay active';
  }
  msg += '. You can rehire the architect later from this menu.';
  if (await showConfirm(msg, { label: 'Dismiss architect', variant: 'btn-danger' })) {
    send({ cmd: 'architect_dismiss', architect_id: architect.id, reason: 'Dismissed from UI' });
  }
}

function rehireArchitect(id) {
  const architect = state.agents ? state.agents[id] : null;
  if (!architect || (architect.kind || '') !== 'architect') return;
  send({ cmd: 'architect_rehire', architect_id: architect.id });
}

async function restartAgent(id) {
  const cell = state.agents ? state.agents[id] : null;
  if (!cell) return;
  const label = cell.name || 'this agent';
  const msg = `Restart '${label}'? The current session will be closed and a new one will launch with the original startup and initial prompts. Any in-progress conversation will be lost.`;
  if (await showConfirm(msg)) {
    send({ cmd: 'restart_agent', id });
  }
}

const _AGENT_GRID_BULK_CHUNK_SIZE = 10;

function _agentGridGroupAgentCells(group) {
  const groupName = String(group || '').trim();
  const ids = ((state && state.groups) || {})[groupName] || [];
  const cells = [];
  for (const id of ids) {
    const cell = state && state.agents ? state.agents[id] : null;
    if (!cell || cell.cell_type !== 'agent') continue;
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) continue;
    cells.push(cell);
  }
  return cells;
}

function _bulkRestartCandidates(group, kind) {
  const requestedKind = String(kind || 'all');
  return _agentGridGroupAgentCells(group).filter(function(cell) {
    if (requestedKind !== 'all' && String(cell.kind || '') !== requestedKind) return false;
    if (typeof _isLifecycleDismissedCell === 'function' && _isLifecycleDismissedCell(cell)) {
      return false;
    }
    return true;
  });
}

function _bulkCloseWorkerRisk(cell) {
  if (!cell || !cell.worktree_path) {
    // No tracked path does not prove no worktree exists: relaunch validation can
    // clear tracking while leaving an orphan on disk. Worktree removal is keyed
    // by the same field, so the orphan cannot be removed and is safe for this operation.
    return 'safe';
  }
  if (cell.worktree_dirty || Number(cell.worktree_checkpoints || 0) > 0) return 'risky';
  // The clean-looking worktree fields are ephemeral and clear on daemon restart.
  // Without a persistent refreshed marker this state must fail closed as unknown.
  return 'unknown';
}

function _classifyBulkCloseWorkers(group) {
  const candidates = _agentGridGroupAgentCells(group).filter(function(cell) {
    return String(cell.kind || '') === 'worker' && String(cell.status || '') !== 'running';
  });
  const result = {
    candidates: candidates,
    safe: [],
    risky: [],
    unknown: [],
    shared: [],
    statusCounts: {},
  };
  for (const cell of candidates) {
    const risk = _bulkCloseWorkerRisk(cell);
    result[risk].push(cell);
    const status = String(cell.status || 'unknown');
    result.statusCounts[status] = Number(result.statusCounts[status] || 0) + 1;
    if (cell.worktree_path && _worktreeSharedWith(cell)) result.shared.push(cell);
  }
  return result;
}

function _bulkCountLabel(count, singular, plural) {
  return count + ' ' + (count === 1 ? singular : (plural || singular + 's'));
}

function _bulkChildTerminalCount(cells) {
  let count = 0;
  for (const cell of cells || []) {
    count += (((state && state.children) || {})[cell.id] || []).length;
  }
  return count;
}

function _bulkCloseWorkerMessage(classified) {
  const total = classified.candidates.length;
  const statusOrder = ['idle', 'error', 'stopped', 'unknown'];
  const seen = {};
  const statusParts = [];
  for (const status of statusOrder.concat(Object.keys(classified.statusCounts))) {
    if (seen[status] || !classified.statusCounts[status]) continue;
    seen[status] = true;
    statusParts.push(classified.statusCounts[status] + ' ' + status);
  }
  const lines = [
    'Close ' + _bulkCountLabel(total, 'worker') + ' that ' + (total === 1 ? 'is' : 'are') + ' not running?',
    'Status breakdown: ' + statusParts.join(', ') + '.',
    _bulkCountLabel(classified.safe.length, 'worker') + ' ha' + (classified.safe.length === 1 ? 's' : 've')
      + ' no tracked worktree path and ' + (classified.safe.length === 1 ? 'is' : 'are') + ' safe for this operation.',
    _bulkCountLabel(classified.risky.length, 'worker') + ' ha' + (classified.risky.length === 1 ? 's' : 've')
      + ' uncommitted changes or unmerged commits.',
    _bulkCountLabel(classified.unknown.length, 'worktree state') + ' '
      + (classified.unknown.length === 1 ? 'is' : 'are')
      + ' unknown (not known to be refreshed since the last daemon restart).',
  ];
  if (classified.shared.length) {
    lines.push(_bulkCountLabel(classified.shared.length, 'worker')
      + (classified.shared.length === 1
        ? ' has a worktree shared with another agent'
        : ' have worktrees shared with other agents')
      + '; shared worktrees are kept.');
  }
  const allChildTerminals = _bulkChildTerminalCount(classified.candidates);
  if (allChildTerminals) {
    const hasCleanOnlyChoice = classified.safe.length > 0
      && (classified.risky.length > 0 || classified.unknown.length > 0);
    if (hasCleanOnlyChoice) {
      const cleanChildTerminals = _bulkChildTerminalCount(classified.safe);
      if (cleanChildTerminals === allChildTerminals) {
        lines.push('Both close choices include '
          + _bulkCountLabel(allChildTerminals, 'child terminal') + '.');
      } else {
        lines.push('"Close all" includes '
          + _bulkCountLabel(allChildTerminals, 'child terminal')
          + '; "Close clean only" includes '
          + _bulkCountLabel(cleanChildTerminals, 'child terminal') + '.');
      }
    } else {
      lines.push('Closing these workers also includes '
        + _bulkCountLabel(allChildTerminals, 'child terminal') + '.');
    }
    lines.push('Child terminals are scheduled for permanent deletion with their workers and are included in the same 7-day restore window.');
  }
  lines.push('Workers are scheduled for permanent deletion in 7 days and can be restored before then. Worktrees are preserved during the 7-day restore window; after that, unshared tracked worktrees are removed and their changes are lost if the workers are not restored.');
  return lines.join('\n');
}

function _agentGridBatchCellStillInGroup(cell, group) {
  if (!cell || cell.cell_type !== 'agent') return false;
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) return false;
  const ids = ((state && state.groups) || {})[String(group || '').trim()] || [];
  return ids.indexOf(cell.id) >= 0;
}

async function _dispatchAgentGridBatch(ids, payloadForCell, stillEligible, onQueued) {
  const result = { queued: [], failed: [], skipped: [] };
  for (let start = 0; start < ids.length; start += _AGENT_GRID_BULK_CHUNK_SIZE) {
    const end = Math.min(ids.length, start + _AGENT_GRID_BULK_CHUNK_SIZE);
    for (let index = start; index < end; index += 1) {
      const id = ids[index];
      const cell = state && state.agents ? state.agents[id] : null;
      if (!cell || !stillEligible(cell)) {
        result.skipped.push(id);
        continue;
      }
      let accepted = false;
      try {
        accepted = send(payloadForCell(cell)) !== false;
      } catch (_error) {
        accepted = false;
      }
      if (accepted) {
        result.queued.push(id);
        if (onQueued) onQueued(cell);
      } else {
        result.failed.push(id);
      }
    }
    await new Promise(function(resolve) { setTimeout(resolve, 0); });
  }
  return result;
}

function _reportAgentGridBatchDispatch(label, result) {
  const parts = [];
  if (result.queued.length) parts.push(_bulkCountLabel(result.queued.length, label + ' request') + ' queued');
  if (result.failed.length) parts.push(_bulkCountLabel(result.failed.length, 'request') + ' could not be sent');
  if (result.skipped.length) {
    parts.push(_bulkCountLabel(result.skipped.length, 'agent') + ' skipped because '
      + (result.skipped.length === 1 ? 'its' : 'their') + ' state changed');
  }
  if (!parts.length) return;
  _showToast(parts.join('; ') + '.', result.failed.length ? 'error' : (result.skipped.length ? 'warning' : 'info'), {
    title: 'Bulk agent action',
  });
}

async function bulkRestartAgents(group, kind) {
  const groupName = String(group || '').trim();
  const requestedKind = String(kind || 'all');
  const candidates = _bulkRestartCandidates(groupName, requestedKind);
  if (!candidates.length) return;
  const kindLabel = requestedKind === 'all' ? 'agent' : requestedKind;
  const message = 'Restart ' + _bulkCountLabel(candidates.length, kindLabel) + '? '
    + 'Current sessions will be closed and relaunched with their original startup and initial prompts. '
    + 'Any in-progress conversations will be lost.';
  if (!await showConfirm(message, { label: 'Restart ' + candidates.length })) return;
  const ids = candidates.map(function(cell) { return cell.id; });
  const result = await _dispatchAgentGridBatch(
    ids,
    function(cell) { return { cmd: 'restart_agent', id: cell.id }; },
    function(cell) {
      return _agentGridBatchCellStillInGroup(cell, groupName)
        && (requestedKind === 'all' || String(cell.kind || '') === requestedKind)
        && !(typeof _isLifecycleDismissedCell === 'function' && _isLifecycleDismissedCell(cell));
    }
  );
  _reportAgentGridBatchDispatch('restart', result);
}

async function bulkCloseNonRunningWorkers(group) {
  const groupName = String(group || '').trim();
  const classified = _classifyBulkCloseWorkers(groupName);
  if (!classified.candidates.length) return;
  const hasRisk = classified.risky.length > 0 || classified.unknown.length > 0;
  const choice = await showConfirm(_bulkCloseWorkerMessage(classified), {
    title: 'Close workers',
    label: 'Close all ' + classified.candidates.length,
    variant: 'btn-danger',
    alternateLabel: hasRisk && classified.safe.length ? 'Close clean only' : '',
    alternateValue: 'clean',
    alternateVariant: 'btn-secondary',
  });
  if (!choice) return;
  const closeCleanOnly = choice === 'clean';
  const ids = (closeCleanOnly ? classified.safe : classified.candidates).map(function(cell) { return cell.id; });
  const result = await _dispatchAgentGridBatch(
    ids,
    function(cell) { return { cmd: 'remove_agent', id: cell.id }; },
    function(cell) {
      if (!_agentGridBatchCellStillInGroup(cell, groupName)) return false;
      if (String(cell.kind || '') !== 'worker' || String(cell.status || '') === 'running') return false;
      return !closeCleanOnly || _bulkCloseWorkerRisk(cell) === 'safe';
    },
    function(cell) {
      if (typeof selectedAgentId !== 'undefined' && selectedAgentId === cell.id) selectedAgentId = null;
      if (typeof selectedTerminalId !== 'undefined' && selectedTerminalId === cell.id) selectedTerminalId = null;
    }
  );
  _reportAgentGridBatchDispatch('close', result);
}

async function clearAgentContext(id) {
  if (await showConfirm("Clear this agent's context? This resets the conversation and the agent will receive full instructions on its next task.")) {
    send({ cmd: 'clear_agent_context', id });
  }
}

function _nextName(prefix) {
  const existing = Object.values(state.agents)
    .map(a => a.name)
    .filter(n => n.startsWith(prefix + ' '));
  let i = 1;
  while (existing.includes(prefix + ' ' + i)) i++;
  return prefix + ' ' + i;
}
function quickAddAgent(group) {
  if (typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) {
    openAddAgent(group);
    return;
  }
  send({ cmd: 'add_agent', name: _nextName('Agent'), group });
}

function newAgentFromTemplate(group, templateName) {
  if (typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) {
    openAddAgent(group, templateName || '');
    return;
  }
  send({ cmd: 'add_agent', group, template: templateName || '' });
}

function quickAddTerminal(group, parentId) {
  // Manual terminal creation is no longer an operator UI flow. Keep this
  // compatibility stub so stale inline handlers or old keybinding overrides do
  // not throw, but do not send the add_terminal command from the web UI.
  return false;
}

async function restartDaemon() {
  if (await showConfirm('Restart Torque? Active cells will reconnect after ~15 seconds.')) {
    send({ cmd: 'restart' });
  }
}

async function stopDaemon() {
  const message = 'Stop Torque daemon? Active cells will be lost.\n\n'
    + "You'll need to relaunch manually via `make standalone` (or your usual launcher).";
  if (await showConfirm(message, { label: 'Stop Daemon', variant: 'btn-danger' })) {
    _daemonStopRequestedByUser = true;
    _showDaemonStoppedBanner();
    send({ cmd: 'stop' });
  }
}

async function removeGroup(group) {
  const count = (state.groups[group] || []).length;
  const msg = count > 0
    ? `Delete group "${group}" and its ${count} cell(s)?`
    : `Delete empty group "${group}"?`;
  if (await showConfirm(msg, { label: 'Delete', variant: 'btn-danger' })) {
    send({ cmd: 'remove_group', group });
    return true;
  }
  return false;
}

/* Drag and drop
 *
 * Supported legacy behavior on the hierarchical grid DOM:
 * - group headers reorder groups via move_group.
 * - agent cards reorder within the current group/bucket via move_agent; the
 *   section/row wrappers do not mutate owner_engineer_id or
 *   hired_by_architect_id, so drag-to-reparent across architect/engineer
 *   boundaries is intentionally deferred.
 * - terminal rows move between a parent drawer and standalone group terminals:
 *   drawer -> standalone uses move_agent to detach, drawer/agent drops use
 *   reparent_terminal, and same-parent drawer ordering uses reorder_child.
 */
let _dragId = null;
let _dragType = null;
let _architectDrag = null;
let _textSelectionDragBypassInstalled = false;

const TEXT_SELECTION_DRAG_SELECTORS = [
  '.agent-card-line',
  '.agent-card-trunc',
  '.group-name',
  '.group-count',
  '.term-name',
  '.term-path',
  '.board-card-id',
  '.board-card-last-transition',
  '.board-card-title',
  '.board-card-task-id',
  '.board-card-lane-entered',
  '.board-card-label',
  '.board-card-lane-badge',
  '.board-card-created-by-label',
  '.board-card-assigned-engineer-name',
  '.board-card-agent',
  '.board-card-verification-note',
  '.board-card-chain',
  '.board-card-activity',
];

const TEXT_SELECTION_DRAG_CONTROL_SELECTORS = [
  '.cell-header-controls',
  '.term-actions',
  '.board-card-menu-btn',
  '.board-card-collapse-btn',
  '.board-card-quick-controls',
];
const TEXT_SELECTION_DRAG_CONTROL_TAGS = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);

function _matchesSimpleSelectionSelector(el, selector) {
  if (!el || !selector) return false;
  if (selector[0] === '.') {
    return !!(el.classList && el.classList.contains(selector.slice(1)));
  }
  if (selector === '[draggable="true"]') {
    return typeof el.getAttribute === 'function' && el.getAttribute('draggable') === 'true';
  }
  return String(el.tagName || '').toUpperCase() === selector.toUpperCase();
}

function _closestSelectionMatch(el, selectors) {
  for (let node = el; node; node = node.parentElement || node.parentNode) {
    for (const selector of selectors) {
      if (_matchesSimpleSelectionSelector(node, selector)) return node;
    }
  }
  return null;
}

function _closestSelectionControl(el) {
  for (let node = el; node; node = node.parentElement || node.parentNode) {
    if (TEXT_SELECTION_DRAG_CONTROL_TAGS.has(String(node.tagName || '').toUpperCase())) return node;
    for (const selector of TEXT_SELECTION_DRAG_CONTROL_SELECTORS) {
      if (_matchesSimpleSelectionSelector(node, selector)) return node;
    }
  }
  return null;
}

function _restoreSelectionDragSource(el) {
  if (!el || typeof el.getAttribute !== 'function') return;
  if (el.getAttribute('data-selection-drag-disabled') !== 'true') return;
  el.setAttribute('draggable', 'true');
  if (typeof el.removeAttribute === 'function') el.removeAttribute('data-selection-drag-disabled');
  else el.setAttribute('data-selection-drag-disabled', '');
}

function _setupTextSelectionDragBypass() {
  if (_textSelectionDragBypassInstalled || typeof document === 'undefined') return;
  _textSelectionDragBypassInstalled = true;
  document.addEventListener('mousedown', (e) => {
    if (!e || e.button !== 0 || e.defaultPrevented) return;
    let target = e.target || null;
    if (target && target.nodeType === 3) target = target.parentElement || target.parentNode;
    if (!target || _closestSelectionControl(target)) return;
    // Board cards intentionally drag from their readable, non-interactive
    // content. Other draggable chrome keeps the text-selection escape hatch.
    if (_closestSelectionMatch(target, ['.board-card'])) return;
    if (!_closestSelectionMatch(target, TEXT_SELECTION_DRAG_SELECTORS)) return;
    const draggable = _closestSelectionMatch(target, ['[draggable="true"]']);
    if (!draggable) return;
    draggable.setAttribute('data-selection-drag-disabled', 'true');
    draggable.setAttribute('draggable', 'false');
    const restore = () => {
      _restoreSelectionDragSource(draggable);
      document.removeEventListener('mouseup', restore, true);
      document.removeEventListener('mouseleave', restore, true);
    };
    document.addEventListener('mouseup', restore, true);
    document.addEventListener('mouseleave', restore, true);
  }, true);
}

function setupDrag() {
  _setupTextSelectionDragBypass();
  const main = document.getElementById('main');

  main.addEventListener('dragstart', (e) => {
    const el = e.target.closest('[data-drag-id]');
    if (!el) return;
    if (el.dataset.dragType === 'terminal') {
      if (e && typeof e.preventDefault === 'function') e.preventDefault();
      return;
    }
    _dragId = el.dataset.dragId;
    _dragType = el.dataset.dragType;
    const dragCell = _dragType === 'agent' && state && state.agents
      ? state.agents[_dragId]
      : null;
    if (dragCell && String(dragCell.kind || '') === 'architect') {
      const group = String(dragCell.group || el.dataset.dragGroup || '');
      const strip = el.closest('[data-agent-architect-strip]');
      _architectDrag = { id: _dragId, group, strip, source: el };
      if (strip && strip.classList) strip.classList.add('architect-reorder-active');
    } else {
      _architectDrag = null;
    }
    dragInProgress = true;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _dragId);
    const dimEl = _dragType === 'group' ? el.closest('.group') || el : el;
    requestAnimationFrame(() => {
      dimEl.classList.add('dragging');
      if (_architectDrag) dimEl.classList.add('architect-dragging');
    });
  });

  main.addEventListener('dragend', () => {
    const wasArchitectDrag = !!_architectDrag || !!_architectReorderFlip;
    _dragId = null;
    _dragType = null;
    dragInProgress = false;
    if (!wasArchitectDrag) _flipUntil = Date.now() + 500;
    _clearDropIndicators();
    _clearArchitectDragTransient();
    _architectDrag = null;
    document.querySelectorAll('.dragging')
      .forEach(el => el.classList.remove('dragging'));
    // A fast move_agent acknowledgement may have updated authoritative state
    // while WS rendering was suppressed by dragInProgress. Always run the
    // normal memoized render after release: it projects that expected order
    // and consumes the queued Architect FLIP. With a delayed acknowledgement
    // the HTML is unchanged, so this is a no-op and the queued FLIP remains
    // available for the later group_update render.
    render();
  });

  main.addEventListener('dragover', (e) => {
    if (!_dragId) return;
    _clearDropIndicators();

    if (_architectDrag) {
      const strip = e.target.closest('[data-agent-architect-strip]');
      if (!strip || String(strip.dataset.architectGroup || '') !== _architectDrag.group) return;
      const intent = _resolveArchitectDropIntent(
        strip,
        e.clientX,
        e.clientY,
        _architectDrag.id,
      );
      if (!intent) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      _showArchitectDropIntent(strip, intent);
      return;
    }

    if (_dragType === 'group') {
      const groupEl = e.target.closest('.group[data-group-name]');
      if (groupEl && groupEl.dataset.groupName !== _dragId) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const rect = groupEl.getBoundingClientRect();
        groupEl.classList.add(e.clientY < rect.top + rect.height / 2 ? 'drop-before' : 'drop-after');
      }
      return;
    }

    const item = e.target.closest('[data-drag-id]');
    const container = e.target.closest('[data-drop-type]');

    // Terminal dragged over an agent cell → re-parent indicator
    if (_dragType === 'terminal' && item && item.dataset.dragType === 'agent') {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      item.classList.add('drop-target');
    } else if (item && item.dataset.dragType === _dragType && item.dataset.dragId !== _dragId) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const rect = item.getBoundingClientRect();
      const isGrid = _dragType === 'agent';
      const pos = isGrid ? e.clientX : e.clientY;
      const mid = isGrid ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
      item.classList.add(pos < mid ? 'drop-before' : 'drop-after');
    } else if (container && container.dataset.dropType === _dragType) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      container.classList.add('drop-target');
    }
  });

  main.addEventListener('drop', (e) => {
    if (!_dragId) return;

    if (_architectDrag) {
      const drag = _architectDrag;
      const strip = e.target.closest('[data-agent-architect-strip]');
      if (strip && String(strip.dataset.architectGroup || '') === drag.group) {
        const intent = _resolveArchitectDropIntent(strip, e.clientX, e.clientY, drag.id);
        if (intent) {
          e.preventDefault();
          const command = _architectMoveCommand(drag.id, drag.group, intent.before);
          if (command) {
            _queueArchitectReorderFlip(
              drag.group,
              command.sourceOrder,
              command.expectedOrder,
              strip,
            );
            if (send(command.payload) === false) _architectReorderFlip = null;
          }
        }
      }
      _clearDropIndicators();
      _clearArchitectDragTransient();
      _architectDrag = null;
      return;
    }

    e.preventDefault();

    if (_dragType === 'group') {
      const groupEl = e.target.closest('.group[data-group-name]');
      if (groupEl && groupEl.dataset.groupName !== _dragId) {
        const rect = groupEl.getBoundingClientRect();
        let beforeGroup = '';
        if (e.clientY < rect.top + rect.height / 2) {
          beforeGroup = groupEl.dataset.groupName;
        } else {
          const next = groupEl.nextElementSibling;
          beforeGroup = next && next.dataset.groupName ? next.dataset.groupName : '';
        }
        send({ cmd: 'move_group', group: _dragId, before: beforeGroup });
      }
      _clearDropIndicators();
      return;
    }

    const item = e.target.closest('[data-drag-id]');
    const container = e.target.closest('[data-drop-type]');

    // Terminal dropped on an agent cell → re-parent
    if (_dragType === 'terminal' && item && item.dataset.dragType === 'agent') {
      send({ cmd: 'reparent_terminal', id: _dragId, parent_id: item.dataset.dragId });
      _clearDropIndicators();
      return;
    }

    let targetGroup = null;
    let beforeId = '';

    if (item && item.dataset.dragType === _dragType && item.dataset.dragId !== _dragId) {
      const rect = item.getBoundingClientRect();
      const isGrid = _dragType === 'agent';
      const pos = isGrid ? e.clientX : e.clientY;
      const mid = isGrid ? rect.left + rect.width / 2 : rect.top + rect.height / 2;
      if (pos < mid) {
        beforeId = item.dataset.dragId;
      } else {
        const next = _nextDragSibling(item);
        beforeId = next ? next.dataset.dragId : '';
      }

      // Check if both terminals share the same parent → reorder within parent
      const dragCell = state.agents[_dragId];
      const dropCell = state.agents[item.dataset.dragId];
      if (_dragType === 'terminal' && dragCell && dropCell
          && dragCell.parent_id && dragCell.parent_id === dropCell.parent_id) {
        send({ cmd: 'reorder_child', id: _dragId, parent_id: dragCell.parent_id, before: beforeId });
        _clearDropIndicators();
        return;
      }

      targetGroup = item.dataset.dragGroup;
    } else if (container && container.dataset.dropType === _dragType) {
      if (container.dataset.dropParent) {
        // Dropped in agent's terminal drawer → reparent
        send({ cmd: 'reparent_terminal', id: _dragId, parent_id: container.dataset.dropParent });
        _clearDropIndicators();
        return;
      }
      targetGroup = container.dataset.dropGroup;
    }

    if (targetGroup) {
      send({ cmd: 'move_agent', id: _dragId, target_group: targetGroup, before: beforeId });
    }
    _clearDropIndicators();
  });
}

function _clearDropIndicators() {
  document.querySelectorAll('.drop-before, .drop-after, .drop-target, .architect-drop-before, .architect-drop-after')
    .forEach(el => el.classList.remove(
      'drop-before', 'drop-after', 'drop-target',
      'architect-drop-before', 'architect-drop-after',
    ));
}

function _architectOrderInGroup(group) {
  const ids = state && state.groups && Array.isArray(state.groups[group])
    ? state.groups[group]
    : [];
  return ids.filter(function(id) {
    const cell = state && state.agents ? state.agents[id] : null;
    if (!cell || String(cell.kind || '') !== 'architect') return false;
    return typeof _isTombstonedAgent !== 'function' || !_isTombstonedAgent(cell);
  }).map(String);
}

function _architectCardsInStrip(strip, sourceId) {
  if (!strip || typeof strip.querySelectorAll !== 'function') return [];
  return Array.from(strip.querySelectorAll('[data-drag-id]')).filter(function(el) {
    return el
      && String(el.dataset.agentKind || '') === 'architect'
      && String(el.dataset.dragId || '') !== String(sourceId || '');
  });
}

function _resolveArchitectDropIntent(strip, clientX, clientY, sourceId) {
  if (!strip) return null;
  const cards = _architectCardsInStrip(strip, sourceId);
  if (!cards.length) return { before: '', marker: null, side: 'after' };

  for (let i = 0; i < cards.length; i++) {
    const card = cards[i];
    const rect = card.getBoundingClientRect();
    const top = Number(rect.top || 0);
    const bottom = Number(rect.bottom || (top + Number(rect.height || 0)));
    const left = Number(rect.left || 0);
    const right = Number(rect.right || (left + Number(rect.width || 0)));
    const midX = left + ((right - left) / 2);
    if (clientY < top || (clientY <= bottom && clientX < midX)) {
      return {
        before: String(card.dataset.dragId || ''),
        marker: card,
        side: 'before',
      };
    }
    const next = cards[i + 1];
    if (clientY <= bottom && (!next || clientY < Number(next.getBoundingClientRect().top || 0))) {
      return {
        before: next ? String(next.dataset.dragId || '') : '',
        marker: card,
        side: 'after',
      };
    }
  }

  return { before: '', marker: cards[cards.length - 1], side: 'after' };
}

function _showArchitectDropIntent(strip, intent) {
  if (!strip || !intent) return;
  if (strip.classList) strip.classList.add('architect-reorder-active');
  if (!intent.marker || !intent.marker.classList) return;
  intent.marker.classList.add(intent.side === 'before'
    ? 'architect-drop-before'
    : 'architect-drop-after');
}

function _architectMoveCommand(sourceId, group, beforeId) {
  const sourceOrder = _architectOrderInGroup(group);
  if (sourceOrder.length <= 1 || !sourceOrder.includes(String(sourceId || ''))) return null;
  const remaining = sourceOrder.filter(id => id !== String(sourceId || ''));
  let insertAt = beforeId ? remaining.indexOf(String(beforeId)) : remaining.length;
  if (insertAt < 0) return null;
  const expectedOrder = remaining.slice();
  expectedOrder.splice(insertAt, 0, String(sourceId || ''));
  if (_architectOrderMatches(sourceOrder, expectedOrder)) return null;
  return {
    payload: {
      cmd: 'move_agent',
      id: String(sourceId || ''),
      target_group: String(group || ''),
      before: beforeId ? String(beforeId) : '',
    },
    sourceOrder,
    expectedOrder,
  };
}

function _clearArchitectDragTransient() {
  const tracked = _architectDrag
    ? [_architectDrag.strip, _architectDrag.source]
    : [];
  tracked.forEach(function(el) {
    if (!el || !el.classList) return;
    el.classList.remove(
      'architect-reorder-active',
      'architect-dragging',
      'architect-drop-before',
      'architect-drop-after',
    );
    if (el.style) el.style.transform = '';
  });
  document.querySelectorAll('.architect-reorder-active, .architect-dragging, .architect-drop-before, .architect-drop-after')
    .forEach(function(el) {
      if (!el.classList) return;
      el.classList.remove(
        'architect-reorder-active',
        'architect-dragging',
        'architect-drop-before',
        'architect-drop-after',
      );
      if (el.style) el.style.transform = '';
    });
}

function _nextDragSibling(el) {
  let next = el.nextElementSibling;
  while (next && !next.hasAttribute('data-drag-id')) next = next.nextElementSibling;
  return next;
}

/* Context menu (right-click) */

let _contextMenuInvoker = null;
let _contextMenuInvokerId = '';

function normalizeContextMenuMarkup(menu) {
  if (!menu || !menu.querySelectorAll) return menu;
  const buttons = menu.querySelectorAll('button');
  for (let i = 0; i < buttons.length; i++) {
    const button = buttons[i];
    if (button.classList && button.classList.contains('ctx-label')) {
      button.classList.add('ui-menu-label');
      continue;
    }
    if (button.classList) {
      button.classList.add('ui-menu-item');
      if (button.classList.contains('danger')) button.classList.add('ui-menu-item--danger');
    }
    if (button.setAttribute) {
      button.setAttribute('type', 'button');
      button.setAttribute('role', 'menuitem');
    }
  }
  const separators = menu.querySelectorAll('.ctx-sep');
  for (let j = 0; j < separators.length; j++) {
    if (separators[j].classList) separators[j].classList.add('ui-menu-separator');
    if (separators[j].setAttribute) separators[j].setAttribute('role', 'separator');
  }
  return menu;
}

// Keep the one shared context-menu surface inside the viewport after its
// caller has installed the complete menu.  In particular, do not base the
// vertical position on an unconstrained content height: CSS bounds tall menus
// and makes their contents scroll within this same surface.
function positionContextMenuSurface(menu) {
  if (!menu || typeof menu.getBoundingClientRect !== 'function') return;
  const edge = 8;
  const viewportWidth = Number(window.innerWidth) || 0;
  const viewportHeight = Number(window.innerHeight) || 0;
  const rect = menu.getBoundingClientRect();
  const anchor = menu._contextMenuAnchor || {};
  const requestedLeft = Number.isFinite(anchor.left)
    ? anchor.left
    : (Number.parseFloat(menu.style.left) || rect.left || edge);
  const requestedTop = Number.isFinite(anchor.top)
    ? anchor.top
    : (Number.parseFloat(menu.style.top) || rect.top || edge);
  const maxLeft = Math.max(edge, viewportWidth - rect.width - edge);
  const maxTop = Math.max(edge, viewportHeight - rect.height - edge);

  menu.style.left = Math.max(edge, Math.min(requestedLeft, maxLeft)) + 'px';
  menu.style.top = Math.max(edge, Math.min(requestedTop, maxTop)) + 'px';
}

function openContextMenuSurface(menu, options) {
  options = options || {};
  if (!menu) return;
  normalizeContextMenuMarkup(menu);
  if (!menu._contextMenuAnchor || options.resetAnchor) {
    menu._contextMenuAnchor = {
      left: Number.parseFloat(menu.style.left),
      top: Number.parseFloat(menu.style.top),
    };
  }
  if (options.invoker) _contextMenuInvoker = options.invoker;
  else if (!_contextMenuInvoker) _contextMenuInvoker = document.activeElement || null;
  if (_contextMenuInvoker && _contextMenuInvoker.id) {
    _contextMenuInvokerId = _contextMenuInvoker.id;
  }
  menu.setAttribute('aria-hidden', 'false');
  menu.classList.add('open');
  if (_contextMenuInvoker && typeof _contextMenuInvoker.setAttribute === 'function'
      && _contextMenuInvoker.getAttribute('aria-haspopup')) {
    _contextMenuInvoker.setAttribute('aria-expanded', 'true');
  }
  requestAnimationFrame(function() {
    positionContextMenuSurface(menu);
    const firstItem = menu.querySelector('button:not(:disabled):not(.ui-menu-label)');
    if (firstItem && typeof firstItem.focus === 'function') firstItem.focus();
  });
}

function closeContextMenu(options) {
  options = options || {};
  const menu = document.getElementById('ctx-menu');
  const invoker = _contextMenuInvoker;
  const invokerId = _contextMenuInvokerId;
  const currentInvoker = invokerId ? document.getElementById(invokerId) : null;
  const focusTarget = currentInvoker || invoker;
  menu.classList.remove('open');
  menu.setAttribute('aria-hidden', 'true');
  if (focusTarget && typeof focusTarget.setAttribute === 'function') {
    focusTarget.setAttribute('aria-expanded', 'false');
  }
  _contextMenuInvoker = null;
  _contextMenuInvokerId = '';
  menu._contextMenuAnchor = null;
  if (options.restoreFocus !== false && focusTarget && typeof focusTarget.focus === 'function') {
    focusTarget.focus();
  }
}

function showContextMenu(x, y, items, options) {
  options = options || {};
  const menu = document.getElementById('ctx-menu');
  closeContextMenu({ restoreFocus: false });
  _contextMenuInvoker = options.invoker || document.activeElement || null;
  let html = '';
  for (const item of items) {
    if (item.separator) {
      html += '<div class="ctx-sep ui-menu-separator" role="separator"></div>';
    } else if (item.disabled) {
      html += `<button type="button" role="menuitem" class="ui-menu-item" disabled aria-disabled="true">${esc(item.label)}</button>`;
    } else if (item.submenu) {
      html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="event.stopPropagation();${esc(item.submenu)}">${esc(item.label)} \u25B8</button>`;
    } else {
      const cls = item.danger ? 'ui-menu-item ui-menu-item--danger danger' : 'ui-menu-item';
      html += `<button type="button" role="menuitem" class="${cls}" onclick="closeContextMenu();${esc(item.action)}">${esc(item.label)}</button>`;
    }
  }
  menu.innerHTML = html;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  openContextMenuSurface(menu, { invoker: _contextMenuInvoker, resetAnchor: true });
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('resize', function() {
    const menu = document.getElementById('ctx-menu');
    if (menu && menu.classList.contains('open')) positionContextMenuSurface(menu);
  });
}

function contextMenuKeydown(event) {
  if (!event) return;
  const menu = document.getElementById('ctx-menu');
  if (!menu || !menu.classList.contains('open')) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    closeContextMenu();
    return;
  }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(event.key) < 0) return;
  const items = Array.prototype.slice.call(menu.querySelectorAll('button:not(:disabled)'));
  if (!items.length) return;
  let index = items.indexOf(document.activeElement);
  if (event.key === 'Home') index = 0;
  else if (event.key === 'End') index = items.length - 1;
  else if (event.key === 'ArrowDown') index = index < 0 ? 0 : (index + 1) % items.length;
  else index = index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length;
  event.preventDefault();
  items[index].focus();
}

function _showWorktreeSubmenu(id) {
  const menu = document.getElementById('ctx-menu');
  const cell = state.agents[id];
  if (!cell) return;
  let html = `<button type="button" class="ctx-label ui-menu-label" disabled>Worktree</button>`;
  html += `<div class="ctx-sep ui-menu-separator" role="separator"></div>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="closeContextMenu();worktreeViewDiff('${id}')">View Diff</button>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="closeContextMenu();worktreeCheckpoint('${id}')">Checkpoint</button>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="closeContextMenu();worktreeHistory('${id}')">History\u2026</button>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="closeContextMenu();worktreeCreatePR('${id}')">Create PR</button>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item" onclick="closeContextMenu();worktreeMerge('${id}')">Create PR + Merge</button>`;
  html += `<div class="ctx-sep ui-menu-separator" role="separator"></div>`;
  html += `<button type="button" role="menuitem" class="ui-menu-item ui-menu-item--danger danger" onclick="closeContextMenu();worktreeRemove('${id}')">Delete Worktree</button>`;
  menu.innerHTML = html;
  openContextMenuSurface(menu);
}

async function worktreeCreate(id) {
  const cell = state.agents[id];
  if (!cell) return;
  if (cell.session_id) {
    // Agent is running — must relaunch to use the worktree
    if (await showConfirm('Creating a worktree will restart the agent in a fresh session. The current conversation will be lost. Continue?')) {
      send({ cmd: 'worktree_create', id, relaunch: true });
    }
  } else {
    send({ cmd: 'worktree_create', id });
  }
}
function worktreeCheckpoint(id) { send({ cmd: 'worktree_checkpoint', id }); }
function worktreeViewDiff(id) { showDiffView(id, true); }
function worktreeHistory(id) { send({ cmd: 'worktree_history', id }); }
async function worktreeCreatePR(id) {
  const cell = state.agents[id];
  if (!cell) return;
  const branch = cell.worktree_branch || 'worktree branch';
  const base = cell.worktree_base_branch || 'main';
  if (!await showConfirm(
    `Create a pull request from "${branch}" into ${base}? The branch will be pushed to origin first.`,
    { label: 'Create PR', variant: 'btn-success' }
  )) return;
  send({ cmd: 'worktree_create_pr', id });
}

function _showWorktreePR(msg) {
  if (msg.error) {
    _showToast(msg.error, 'error');
    return;
  }
  // Show PR URL in confirm dialog — reuse showConfirm flow
  var label = (msg.message || 'PR created') + ': ' + msg.url;
  showConfirm(label, { label: 'Open in browser', variant: 'btn-success' }).then(function(ok) {
    if (ok) window.open(msg.url);
  });
}
function _worktreeMergeDialogDefaults(cell) {
  var group = cell && cell.group ? cell.group : '';
  var gs = (state.group_settings && group) ? state.group_settings[group] : null;
  var mode = (gs && gs.worktree_merge_cleanup) || 'keep';
  return {
    close: mode === 'close' || mode === 'close_remove' || mode === 'auto_sweep',
    remove: mode === 'remove' || mode === 'close_remove' || mode === 'auto_sweep',
    preserveDiff: !!(gs && gs.worktree_merge_preserve_diff),
  };
}
async function _confirmWorktreeMerge(id, message) {
  const cell = state.agents[id];
  if (!cell) return false;
  const base = cell.worktree_base_branch || 'main';
  const mergeDefaults = _worktreeMergeDialogDefaults(cell);
  const result = await showConfirm(
    `Create PR and squash-merge "${cell.name}" into ${base}? Cleanup options run only after the PR merges, not when the PR is created.`,
    {
      label: 'Create PR + Merge', variant: 'btn-success',
      checkboxes: [
        { key: 'close_agent_on_merge', label: 'Close agent after PR merge', checked: mergeDefaults.close },
        { key: 'remove_worktree_on_merge', label: 'Delete worktree after PR merge', checked: mergeDefaults.remove },
        { key: 'preserve_merge_diff', label: 'Preserve merge diff on boundary task after PR merge', checked: mergeDefaults.preserveDiff },
        { key: 'clear_context', label: 'Clear context after PR merge', checked: false },
        { key: 'force_direct', label: 'Advanced: force direct local merge (skip PR workflow)', checked: false },
      ],
    }
  );
  if (result) {
    const payload = {
      cmd: 'worktree_merge', id,
      message: message || '',
      close_agent_on_merge: result.close_agent_on_merge || false,
      remove_worktree_on_merge: result.remove_worktree_on_merge || false,
      preserve_merge_diff: result.preserve_merge_diff || false,
      clear_context: result.clear_context || false,
    };
    if (result.force_direct) payload.force_direct = true;
    send(payload);
    return true;
  }
  return false;
}

function worktreeMerge(id) {
  showDiffView(id);
}
async function worktreeRemove(id) {
  const cell = state.agents[id];
  if (!cell) return;
  var sharedWith = _worktreeSharedWith(cell);
  if (sharedWith) {
    let msg = `Delete worktree link for "${cell.name}"? The worktree is shared with ${sharedWith}, so only this agent's link will be cleared.`;
    if (await showConfirm(msg, { label: 'Delete', variant: 'btn-danger' })) {
      send({ cmd: 'worktree_remove', id, relaunch: !!cell.session_id });
    }
    return;
  }
  const dirty = cell.worktree_dirty;
  const hasCommits = (cell.worktree_checkpoints || 0) > 0;
  const warnings = [];
  if (hasCommits) warnings.push('has unmerged commits');
  if (dirty) warnings.push('has uncommitted changes');
  if (cell.session_id) warnings.push('agent will restart in a fresh session');
  let msg = `Delete worktree for "${cell.name}"?`;
  if (warnings.length) msg += ' ' + warnings.join(', ').replace(/^./, c => c.toUpperCase()) + '. All changes will be lost.';
  if (await showConfirm(msg, { label: 'Delete', variant: 'btn-danger' })) {
    send({ cmd: 'worktree_remove', id, relaunch: !!cell.session_id });
  }
}

function copyAgentId(id) {
  navigator.clipboard.writeText(id).then(function() { closeContextMenu(); });
}

function copyAgentName(id) {
  const cell = state && state.agents ? state.agents[id] : null;
  if (!cell) return;
  navigator.clipboard.writeText(String(cell.name == null ? '' : cell.name)).then(function() {
    closeContextMenu();
  });
}

function _agentGridNewMenuItems(group) {
  const groupName = String(group || '').trim();
  if (!groupName) return [];
  const settings = ((state && state.group_settings) || {})[groupName] || {};
  const ids = ((state && state.groups) || {})[groupName] || [];
  const agentCount = ids.reduce(function(total, id) {
    const cell = state && state.agents && state.agents[id];
    if (!cell || cell.cell_type !== 'agent') return total;
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) return total;
    return total + 1;
  }, 0);
  const atAgentCap = settings.max_agents > 0 && agentCount >= settings.max_agents;
  const items = atAgentCap ? [
    { label: 'Agent limit reached', disabled: true },
  ] : [
    { label: 'New architect', action: 'openAddArchitectForGroup(' + JSON.stringify(groupName) + ')' },
    { label: 'New engineer', action: 'openAddEngineerForSection(' + JSON.stringify(groupName) + ', "")' },
    { label: 'New worker', action: 'openAddWorkerForSection(' + JSON.stringify(groupName) + ')' },
  ];
  items.push({ separator: true });
  items.push({ label: 'New group', action: 'openAddGroup()' });
  return items;
}

function openAgentGridNewMenu(e, group) {
  if (e) {
    if (typeof e.preventDefault === 'function') e.preventDefault();
    if (typeof e.stopPropagation === 'function') e.stopPropagation();
  }
  const items = _agentGridNewMenuItems(group);
  if (!items.length) return;
  let x = e && typeof e.clientX === 'number' ? e.clientX : 0;
  let y = e && typeof e.clientY === 'number' ? e.clientY : 0;
  const target = e && (e.currentTarget || e.target);
  if (target && typeof target.getBoundingClientRect === 'function') {
    const rect = target.getBoundingClientRect();
    x = rect.left;
    y = rect.bottom + 4;
  }
  showContextMenu(x, y, items, { invoker: target });
}

function _agentGridActionsMenuItems(group) {
  const groupName = String(group || '').trim();
  const restartKinds = [
    ['architect', 'Restart all architects'],
    ['engineer', 'Restart all engineers'],
    ['worker', 'Restart all workers'],
    ['all', 'Restart all agents'],
  ];
  const items = restartKinds.map(function(entry) {
    const kind = entry[0];
    const count = groupName ? _bulkRestartCandidates(groupName, kind).length : 0;
    return {
      label: entry[1],
      disabled: count === 0,
      action: 'bulkRestartAgents(' + JSON.stringify(groupName) + ', ' + JSON.stringify(kind) + ')',
    };
  });
  items.push({ separator: true });
  const closeCount = groupName ? _classifyBulkCloseWorkers(groupName).candidates.length : 0;
  items.push({
    label: 'Close all workers that are not running',
    disabled: closeCount === 0,
    danger: true,
    action: 'bulkCloseNonRunningWorkers(' + JSON.stringify(groupName) + ')',
  });
  return items;
}

function openAgentGridActionsMenu(e, group) {
  if (e) {
    if (typeof e.preventDefault === 'function') e.preventDefault();
    if (typeof e.stopPropagation === 'function') e.stopPropagation();
  }
  const items = _agentGridActionsMenuItems(group);
  let x = e && typeof e.clientX === 'number' ? e.clientX : 0;
  let y = e && typeof e.clientY === 'number' ? e.clientY : 0;
  const target = e && (e.currentTarget || e.target);
  if (target && typeof target.getBoundingClientRect === 'function') {
    const rect = target.getBoundingClientRect();
    x = rect.left;
    y = rect.bottom + 4;
  }
  showContextMenu(x, y, items, { invoker: target });
}

function _cellContextMenuItems(id) {
  const cell = state.agents[id];
  if (!cell) return [];
  const gs = (state.group_settings || {})[cell.group] || {};
  const isDesignatedEngineer = gs.engineer_agent_id === id;
  const isDismissedEngineer = _isEngineerDismissedCell(cell);
  const isDismissedArchitect = _isArchitectDismissedCell(cell);
  const isDismissedLifecycleCell = _isLifecycleDismissedCell(cell);
  const canOpenAgentSettings = cell.cell_type === 'agent'
    && ((cell.kind || '') === 'architect' || (cell.kind || '') === 'engineer')
    && !isDismissedLifecycleCell
    && Number(cell.deleted_at || 0) <= 0
    && !cell.tombstoned;

  const items = [
    { label: 'Edit\u2026', action: `openEditCell('${id}')` },
  ];
  if (canOpenAgentSettings) {
    items.push({ label: 'Settings\u2026', action: `openAgentSettingsDialog({ agentId: '${id}' })` });
  }
  let dismissItem = null;
  if (cell.cell_type === 'agent'
      && (cell.kind || '') === 'architect'
      && !isDismissedLifecycleCell) {
    items.push({
      label: 'New engineer',
      action: 'openAddEngineerForSection('
        + JSON.stringify(cell.group || '')
        + ', '
        + JSON.stringify(id)
        + ')',
    });
  }
  if (cell.status === 'stopped'
      && cell.cell_type !== 'terminal'
      && !isDismissedLifecycleCell) {
    items.push({
      label: isDesignatedEngineer ? 'Restart Engineer\u2026' : 'Relaunch',
      action: `relaunchAgent('${id}')`,
    });
  }
  if (cell.cell_type === 'agent' && !isDismissedLifecycleCell) {
    items.push({
      label: 'Restart\u2026',
      action: `restartAgent('${id}')`,
    });
  }
  if (cell.cell_type === 'agent'
      && (cell.kind || '') === 'engineer'
      && _canCurrentSessionManageEngineer(cell)) {
    if (isDismissedEngineer) {
      items.push({
        label: 'Rehire',
        action: `rehireEngineer('${id}')`,
      });
    } else {
      dismissItem = {
        label: 'Dismiss\u2026',
        action: `dismissEngineer('${id}')`,
        danger: true,
      };
    }
  }
  if (cell.cell_type === 'agent' && (cell.kind || '') === 'architect') {
    if (isDismissedArchitect) {
      items.push({
        label: 'Rehire',
        action: `rehireArchitect('${id}')`,
      });
    } else {
      dismissItem = {
        label: 'Dismiss\u2026',
        action: `dismissArchitect('${id}')`,
        danger: true,
      };
    }
  }
  /* Worktree submenu */
  if (cell.cell_type === 'agent') {
    if (cell.worktree_path) {
      items.push({ label: 'Worktree', submenu: `_showWorktreeSubmenu('${id}')` });
    } else {
      const kind = cell.kind || '';
      if (gs.git_worktree && kind !== 'architect' && kind !== 'engineer') {
        items.push({ label: 'Create Worktree', action: `worktreeCreate('${id}')` });
      }
    }
  }
  if (cell.cell_type === 'agent') {
    items.push({ label: 'View Tasks', action: `showTaskHistory('${id}')` });
  }
  if (cell.cell_type === 'agent' && cell.session_id
      && (cell.agent_type === 'claude-code' || cell.agent_type === 'codex')) {
    items.push({ label: 'Clear context', action: `clearAgentContext('${id}')` });
  }
  items.push({ separator: true });
  items.push({ label: `Copy ID: ${id.slice(0, 8)}\u2026`, action: `copyAgentId('${id}')` });
  items.push({ label: 'Copy Name', action: `copyAgentName('${id}')` });
  if (dismissItem) items.push(dismissItem);
  items.push({ label: 'Delete', action: `removeAgent('${id}')`, danger: true });
  return items;
}

function onCellContextMenu(e, id) {
  e.preventDefault();
  e.stopPropagation();
  const items = _cellContextMenuItems(id);
  if (!items.length) return;
  showContextMenu(e.clientX, e.clientY, items);
}

/* Legacy principal selector compatibility shim.
   The grid no longer filters by principal; state.selected_principal_id is
   still persisted/sent so older clients/servers can round-trip the field.
   Architect ids focus/select that real architect agent card. The user principal
   leaves the current agent selection alone and focuses a useful orphan
   section item/control when one is available. */
function selectPrincipal(principalId, groupName) {
  const id = String(principalId || '').trim();
  const current = String((state && state.selected_principal_id) || '');
  const group = String(groupName || _principalFocusGroupFallback() || '').trim();
  const prevSelectedId = typeof selectedAgentId !== 'undefined' ? selectedAgentId : null;
  const isArchitect = !!(id
    && state
    && state.agents
    && state.agents[id]
    && (state.agents[id].kind || '') === 'architect');
  let selectionChanged = false;
  if (isArchitect) {
    focusedItemId = id;
    if (selectedAgentId !== id) {
      selectedAgentId = id;
      selectionChanged = true;
      send({ cmd: 'select_agent', id: id });
    }
    selectedTerminalId = id;
    if (typeof _agentFocusPersistExplicitSelection === 'function') {
      _agentFocusPersistExplicitSelection(id);
    }
    send({ cmd: 'focus_agent', id: id });
  } else if (!id) {
    const userFocus = _principalLegacyUserFocusId(group);
    if (userFocus) focusedItemId = userFocus;
  } else if (state && state.agents && state.agents[id]) {
    focusedItemId = id;
  }
  if (id !== current) {
    state.selected_principal_id = id;
    send({ cmd: 'ui_select_principal', principal_id: id });
  }
  render();
  if (isArchitect
      && typeof isEmbeddedTerminalMode === 'function'
      && isEmbeddedTerminalMode()
      && typeof focusEmbeddedTerminalWorkspace === 'function') {
    focusEmbeddedTerminalWorkspace(true);
  }
  if (selectionChanged && typeof _syncPanelsAfterSelectionChange === 'function') {
    _syncPanelsAfterSelectionChange(prevSelectedId);
  }
}

function _principalLegacyUserFocusId(groupName) {
  const group = String(groupName || '').trim();
  const rows = (typeof window !== 'undefined' && Array.isArray(window._navGridRows))
    ? window._navGridRows
    : [];
  const wantedSections = ['user', 'workers'];
  for (const sectionKey of wantedSections) {
    for (const row of rows) {
      if (!row || (group && row.group !== group) || row.sectionKey !== sectionKey) continue;
      for (const item of row.items || []) {
        if (item && (item.type === 'agent' || item.type === 'control')) return item.id;
      }
    }
  }
  for (const row of rows) {
    if (!row || (group && row.group !== group)) continue;
    for (const item of row.items || []) {
      if (item && item.type === 'control') return item.id;
    }
  }
  return '';
}

function _principalFocusGroupFallback() {
  // Derive a sensible group when the caller omits one — prefer current
  // nav metadata, else the focused agent's group, else the first group in
  // the nav order.
  const meta = (typeof window !== 'undefined' && window._navGridItemMeta)
    ? window._navGridItemMeta[focusedItemId]
    : null;
  if (meta && meta.group) return meta.group;
  if (focusedItemId && state && state.agents && state.agents[focusedItemId]) {
    return state.agents[focusedItemId].group || '';
  }
  const order = (typeof window !== 'undefined' && window._navGroupOrder)
    ? window._navGroupOrder
    : [];
  return order.length ? order[0] : '';
}

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

function _showToast(message, level) {
  const el = document.createElement('div');
  el.className = 'toast toast-' + (level || 'info');
  el.textContent = message;
  document.body.appendChild(el);
  // Trigger reflow then animate in
  requestAnimationFrame(() => el.classList.add('visible'));
  setTimeout(() => {
    el.classList.remove('visible');
    setTimeout(() => el.remove(), 300);
  }, 4000);
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

function focusAgent(id) {
  var prevSelectedId = selectedAgentId;
  focusedItemId = id;
  _updateSelectedAgentContext(id);
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
    // Already selected → focus the agent's iTerm2 session
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
  const gs = (state.group_settings || {})[group] || {};
  if (gs.terminal_always_custom_dialog) { openAddTerminal(group, parentId); return; }
  const prefix = gs.terminal_name_prefix || 'Terminal';
  const msg = { cmd: 'add_terminal', name: _nextName(prefix), group };
  if (parentId) msg.parent_id = parentId;
  send(msg);
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

function setupDrag() {
  const main = document.getElementById('main');

  main.addEventListener('dragstart', (e) => {
    const el = e.target.closest('[data-drag-id]');
    if (!el) return;
    _dragId = el.dataset.dragId;
    _dragType = el.dataset.dragType;
    dragInProgress = true;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _dragId);
    const dimEl = _dragType === 'group' ? el.closest('.group') || el : el;
    requestAnimationFrame(() => dimEl.classList.add('dragging'));
  });

  main.addEventListener('dragend', () => {
    _dragId = null;
    _dragType = null;
    dragInProgress = false;
    _flipUntil = Date.now() + 500;
    _clearDropIndicators();
    document.querySelectorAll('.dragging')
      .forEach(el => el.classList.remove('dragging'));
    render();
  });

  main.addEventListener('dragover', (e) => {
    if (!_dragId) return;
    _clearDropIndicators();

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
  document.querySelectorAll('.drop-before, .drop-after, .drop-target')
    .forEach(el => el.classList.remove('drop-before', 'drop-after', 'drop-target'));
}

function _nextDragSibling(el) {
  let next = el.nextElementSibling;
  while (next && !next.hasAttribute('data-drag-id')) next = next.nextElementSibling;
  return next;
}

/* Context menu (right-click) */

function closeContextMenu() {
  document.getElementById('ctx-menu').classList.remove('open');
}

function showContextMenu(x, y, items) {
  const menu = document.getElementById('ctx-menu');
  let html = '';
  for (const item of items) {
    if (item.separator) {
      html += '<div class="ctx-sep"></div>';
    } else if (item.submenu) {
      html += `<button onclick="event.stopPropagation();${esc(item.submenu)}">${esc(item.label)} \u25B8</button>`;
    } else {
      const cls = item.danger ? ' class="danger"' : '';
      html += `<button${cls} onclick="closeContextMenu();${esc(item.action)}">${esc(item.label)}</button>`;
    }
  }
  menu.innerHTML = html;
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.classList.add('open');

  // Adjust if menu overflows viewport
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 4) + 'px';
    if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 4) + 'px';
  });
}

function _showWorktreeSubmenu(id) {
  const menu = document.getElementById('ctx-menu');
  const cell = state.agents[id];
  if (!cell) return;
  let html = `<button class="ctx-label" disabled>Worktree</button>`;
  html += `<div class="ctx-sep"></div>`;
  html += `<button onclick="closeContextMenu();showDiffView('${id}',true)">View Diff</button>`;
  html += `<button onclick="closeContextMenu();worktreeCheckpoint('${id}')">Checkpoint</button>`;
  html += `<button onclick="closeContextMenu();worktreeHistory('${id}')">History\u2026</button>`;
  html += `<button onclick="closeContextMenu();worktreeCreatePR('${id}')">Create PR</button>`;
  html += `<button onclick="closeContextMenu();worktreeMerge('${id}')">Merge to Main</button>`;
  html += `<div class="ctx-sep"></div>`;
  html += `<button class="danger" onclick="closeContextMenu();worktreeRemove('${id}')">Delete Worktree</button>`;
  menu.innerHTML = html;
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
function worktreeHistory(id) { send({ cmd: 'worktree_history', id }); }
async function worktreeCreatePR(id) {
  const cell = state.agents[id];
  if (!cell) return;
  const branch = cell.worktree_branch || 'worktree branch';
  const base = cell.worktree_base_branch || 'main';
  if (!await showConfirm(
    `Create a pull request from "${branch}" into ${base}? The branch will be pushed to origin first.`,
    { label: 'Create PR', variant: 'btn-green' }
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
  showConfirm(label, { label: 'Open in browser', variant: 'btn-green' }).then(function(ok) {
    if (ok) window.open(msg.url);
  });
}
function _worktreeMergeDialogDefaults(cell) {
  var group = cell && cell.group ? cell.group : '';
  var gs = (state.group_settings && group) ? state.group_settings[group] : null;
  var mode = (gs && gs.worktree_merge_cleanup) || 'keep';
  return {
    close: mode === 'close' || mode === 'close_remove',
    remove: mode === 'remove' || mode === 'close_remove',
    preserveDiff: !!(gs && gs.worktree_merge_preserve_diff),
  };
}
async function _confirmWorktreeMerge(id, message) {
  const cell = state.agents[id];
  if (!cell) return false;
  const base = cell.worktree_base_branch || 'main';
  const squash = cell.worktree_merge_squash !== false;
  const mergeDefaults = _worktreeMergeDialogDefaults(cell);
  const result = await showConfirm(
    `${squash ? 'Squash merge' : 'Merge'} "${cell.name}" into ${base}?`,
    {
      label: 'Merge', variant: 'btn-green',
      checkboxes: [
        { key: 'close_agent_on_merge', label: 'Close agent after merge', checked: mergeDefaults.close },
        { key: 'remove_worktree_on_merge', label: 'Delete worktree after merge', checked: mergeDefaults.remove },
        { key: 'preserve_merge_diff', label: 'Preserve merge diff on boundary task', checked: mergeDefaults.preserveDiff },
        { key: 'clear_context', label: 'Clear context after merge', checked: false },
      ],
    }
  );
  if (result) {
    send({
      cmd: 'worktree_merge', id,
      message: message || '',
      close_agent_on_merge: result.close_agent_on_merge || false,
      remove_worktree_on_merge: result.remove_worktree_on_merge || false,
      preserve_merge_diff: result.preserve_merge_diff || false,
      clear_context: result.clear_context || false,
    });
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

function onCellContextMenu(e, id) {
  e.preventDefault();
  e.stopPropagation();
  const cell = state.agents[id];
  if (!cell) return;
  const gs = (state.group_settings || {})[cell.group] || {};
  const isDesignatedEngineer = gs.engineer_agent_id === id;
  const isDismissedEngineer = _isEngineerDismissedCell(cell);
  const isDismissedArchitect = _isArchitectDismissedCell(cell);
  const isDismissedLifecycleCell = _isLifecycleDismissedCell(cell);

  const items = [
    { label: 'Edit\u2026', action: `openEditCell('${id}')` },
    { label: 'Focus', action: `focusAgent('${id}')` },
  ];
  if (cell.status === 'stopped' && !isDismissedLifecycleCell) {
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
      items.push({
        label: 'Dismiss\u2026',
        action: `dismissEngineer('${id}')`,
        danger: true,
      });
    }
  }
  if (cell.cell_type === 'agent' && (cell.kind || '') === 'architect') {
    if (isDismissedArchitect) {
      items.push({
        label: 'Rehire',
        action: `rehireArchitect('${id}')`,
      });
    } else {
      items.push({
        label: 'Dismiss\u2026',
        action: `dismissArchitect('${id}')`,
        danger: true,
      });
    }
  }
  /* Worktree submenu */
  if (cell.cell_type === 'agent') {
    if (cell.worktree_path) {
      items.push({ label: 'Worktree', submenu: `_showWorktreeSubmenu('${id}')` });
    } else {
      if (gs.git_worktree) {
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
  items.push({ label: 'Delete', action: `removeAgent('${id}')`, danger: true });
  showContextMenu(e.clientX, e.clientY, items);
}

/* Principal selector — top-row filter for the agent grid.
   `principalId` is an architect id, or '' to select the user principal.
   `groupName` scopes the focus target so multi-group workspaces don't
   collide on nav ids (selection itself is global state). */
function selectPrincipal(principalId, groupName) {
  const id = String(principalId || '').trim();
  const current = String((state && state.selected_principal_id) || '');
  const group = String(groupName || _principalFocusGroupFallback() || '').trim();
  focusedItemId = _principalRowFocusId(group, id);
  const prevSelectedId = typeof selectedAgentId !== 'undefined' ? selectedAgentId : null;
  /* Architect principal: also make the architect the focused agent so the
     detail view (renderAgentDetails) surfaces in the panel body, AND focus
     its terminal — clicking a principal is a deliberate "go here" act, not
     a passive scan, so we always focus on click (not just on second click).
     User principal is not an agent — leave selectedAgentId alone. */
  const isArchitect = !!(id && state && state.agents && state.agents[id]);
  let selectionChanged = false;
  if (isArchitect) {
    if (selectedAgentId !== id) {
      selectedAgentId = id;
      selectionChanged = true;
      send({ cmd: 'select_agent', id: id });
    }
    selectedTerminalId = id;
    send({ cmd: 'focus_agent', id: id });
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

function _principalRowFocusId(groupName, principalId) {
  const id = String(principalId || '').trim();
  const group = String(groupName || '').trim();
  return 'principal:' + group + ':' + (id || 'user');
}

function _principalFocusGroupFallback() {
  // Derive a sensible group when the caller omits one — prefer the currently
  // focused principal's group, else the focused agent's group, else the
  // first group in the nav order.
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

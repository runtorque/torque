/* Commands — actions sent to the daemon */

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

function focusAgent(id) { focusedItemId = id; send({ cmd: 'focus_agent', id }); }

function onAgentClick(id) {
  focusedItemId = id;
  if (selectedAgentId === id) {
    // Already selected → focus the agent's iTerm2 session
    send({ cmd: 'focus_agent', id });
  } else {
    // Select this agent → show its terminal drawer
    selectedAgentId = id;
    render();
  }
}

function onAgentDblClick(id) {
  focusedItemId = id;
  selectedAgentId = id;
  send({ cmd: 'focus_agent', id });
  render();
}

async function removeAgent(id) {
  const a = state.agents[id];
  if (!a) return;
  const childCount = (state.children[id] || []).length;
  let msg = `Remove "${a.name}"?`;
  if (childCount > 0) {
    msg = `Remove "${a.name}" and its ${childCount} terminal(s)?`;
  }
  if (await showConfirm(msg)) {
    if (selectedAgentId === id) selectedAgentId = null;
    send({ cmd: 'remove_agent', id });
  }
}

function relaunchAgent(id) { send({ cmd: 'relaunch_agent', id }); }

function _nextName(prefix) {
  const existing = Object.values(state.agents)
    .map(a => a.name)
    .filter(n => n.startsWith(prefix + ' '));
  let i = 1;
  while (existing.includes(prefix + ' ' + i)) i++;
  return prefix + ' ' + i;
}
function quickAddAgent(group) {
  const gs = (state.group_settings || {})[group] || {};
  if (gs.agent_always_custom_dialog) { openAddAgent(group); return; }
  send({ cmd: 'add_agent', name: _nextName('Agent'), group });
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
  if (await showConfirm('Restart Loom? Active cells will be marked as stopped.')) {
    send({ cmd: 'restart' });
  }
}

async function removeGroup(group) {
  const count = (state.groups[group] || []).length;
  const msg = count > 0
    ? `Remove group "${group}" and its ${count} cell(s)?`
    : `Remove empty group "${group}"?`;
  if (await showConfirm(msg)) send({ cmd: 'remove_group', group });
}

/* Drag and drop */
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
    if (item.submenu) {
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
  html += `<button onclick="closeContextMenu();worktreeCheckpoint('${id}')">Checkpoint</button>`;
  html += `<button onclick="closeContextMenu();worktreeHistory('${id}')">History\u2026</button>`;
  html += `<button onclick="closeContextMenu();worktreeCreatePR('${id}')" disabled>Create PR</button>`;
  html += `<button onclick="closeContextMenu();worktreeMerge('${id}')">Merge to Main</button>`;
  html += `<div class="ctx-sep"></div>`;
  html += `<button class="danger" onclick="closeContextMenu();worktreeRemove('${id}')">Remove Worktree</button>`;
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
function worktreeCreatePR(_id) { /* TODO: implement */ }
async function worktreeMerge(id) {
  const cell = state.agents[id];
  if (!cell) return;
  const base = cell.worktree_base_branch || 'main';
  if (await showConfirm(
    `Merge "${cell.name}" into ${base}? Claude will perform the merge and resolve any conflicts. You\u2019ll be notified if it fails.`
  )) {
    send({ cmd: 'worktree_merge', id });
  }
}
async function worktreeRemove(id) {
  const cell = state.agents[id];
  if (!cell) return;
  const dirty = cell.worktree_dirty;
  const hasCommits = (cell.worktree_checkpoints || 0) > 0;
  const warnings = [];
  if (hasCommits) warnings.push('has unmerged commits');
  if (dirty) warnings.push('has uncommitted changes');
  if (cell.session_id) warnings.push('agent will restart in a fresh session');
  let msg = `Remove worktree for "${cell.name}"?`;
  if (warnings.length) msg += ' ' + warnings.join(', ').replace(/^./, c => c.toUpperCase()) + '. All changes will be lost.';
  if (await showConfirm(msg)) {
    send({ cmd: 'worktree_remove', id, relaunch: !!cell.session_id });
  }
}

function onCellContextMenu(e, id) {
  e.preventDefault();
  e.stopPropagation();
  const cell = state.agents[id];
  if (!cell) return;

  const items = [
    { label: 'Edit\u2026', action: `openEditCell('${id}')` },
    { label: 'Focus', action: `focusAgent('${id}')` },
  ];
  if (cell.status === 'stopped') {
    items.push({ label: 'Relaunch', action: `relaunchAgent('${id}')` });
  }
  /* Worktree submenu */
  if (cell.cell_type === 'agent') {
    if (cell.worktree_path) {
      items.push({ label: 'Worktree', submenu: `_showWorktreeSubmenu('${id}')` });
    } else {
      const gs = (state.group_settings || {})[cell.group] || {};
      if (gs.git_worktree) {
        items.push({ label: 'Create Worktree', action: `worktreeCreate('${id}')` });
      }
    }
  }
  items.push({ label: 'Remove', action: `removeAgent('${id}')`, danger: true });
  showContextMenu(e.clientX, e.clientY, items);
}

/* Group context menu (right-click on group header) */
function onGroupContextMenu(e, group) {
  e.preventDefault();
  e.stopPropagation();
  const items = [
    { label: 'Settings\u2026', action: `openGroupSettings('${esc(group)}')` },
    { label: 'Broadcast\u2026', action: `openBroadcast('${esc(group)}')` },
    { label: 'Remove', action: `removeGroup('${esc(group)}')`, danger: true },
  ];
  showContextMenu(e.clientX, e.clientY, items);
}

/* Broadcast bar */
let broadcastGroup = null;

function openBroadcast(group) {
  broadcastGroup = group;
  document.getElementById('broadcast-target').textContent = '\u2192 ' + group;
  document.getElementById('broadcast').classList.add('visible');
  const inp = document.getElementById('broadcast-input');
  inp.value = '';
  inp.focus();
}
function closeBroadcast() {
  broadcastGroup = null;
  document.getElementById('broadcast').classList.remove('visible');
}
function sendBroadcast() {
  const text = document.getElementById('broadcast-input').value;
  if (text && broadcastGroup) {
    send({ cmd: 'broadcast_to_group', group: broadcastGroup, text: text + '\n' });
    document.getElementById('broadcast-input').value = '';
  }
}

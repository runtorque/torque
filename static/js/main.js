/* Keyboard bindings and boot */

/* -- Keyboard navigation helpers ----------------------------------------- */

function _focusedGroup() {
  if (!focusedItemId) return null;
  const cell = state.agents[focusedItemId];
  return cell ? cell.group : null;
}

function _firstGroup() {
  const names = Object.keys(state.groups);
  return names.length > 0 ? names[0] : null;
}

function _initFocus(delta) {
  // Start focus from active cell if possible, otherwise first/last in full list
  const items = window._navItems || [];
  if (items.length === 0) return;
  const activeIdx = items.findIndex(id => {
    const c = state.agents[id];
    return c && c.session_id === state.active_session_id;
  });
  if (activeIdx >= 0) {
    focusedItemId = items[activeIdx];
  } else {
    focusedItemId = delta > 0 ? items[0] : items[items.length - 1];
  }
  render();
  const el = document.querySelector('.focused');
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function _moveInList(list, delta) {
  if (list.length === 0) return;
  const idx = list.indexOf(focusedItemId);
  const next = idx < 0 ? 0 : Math.max(0, Math.min(list.length - 1, idx + delta));
  focusedItemId = list[next];
  render();
  const el = document.querySelector('.focused');
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function _currentGroupItems() {
  const gname = _focusedGroup();
  if (!gname) return null;
  const byGroup = window._navByGroup || {};
  return byGroup[gname] || null;
}

function moveFocusHorizontal(delta) {
  if (focusedItemId == null) { _initFocus(delta); return; }

  const cell = state.agents[focusedItemId];
  const onTerminal = cell && cell.cell_type === 'terminal';
  const gname = cell ? cell.group : null;

  // Get agents in the current group only
  const groupItems = gname ? ((window._navByGroup || {})[gname] || []) : [];
  const groupAgents = groupItems.filter(id => {
    const c = state.agents[id];
    return c && c.cell_type !== 'terminal';
  });
  if (groupAgents.length === 0) return;

  // Find current agent position
  let currentAgentId = focusedItemId;
  if (onTerminal) currentAgentId = cell.parent_id || null;

  let idx = currentAgentId ? groupAgents.indexOf(currentAgentId) : -1;
  const nextIdx = idx < 0 ? 0 : Math.max(0, Math.min(groupAgents.length - 1, idx + delta));
  const nextAgentId = groupAgents[nextIdx];

  // Open the target agent's drawer
  selectedAgentId = nextAgentId;

  if (onTerminal) {
    // Was on a terminal — focus the first child terminal of the new agent
    render();
    const childIds = state.children[nextAgentId] || [];
    const wid = typeof FILTER_BY_WINDOW !== 'undefined' && FILTER_BY_WINDOW ? state.current_window_id : null;
    const firstChild = childIds.find(cid => {
      const ct = state.agents[cid];
      return ct && (!wid || !ct.window_id || ct.window_id === wid);
    });
    focusedItemId = firstChild || nextAgentId;
    render();
  } else {
    focusedItemId = nextAgentId;
    render();
  }

  const el = document.querySelector('.focused');
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function moveFocusDown() {
  if (focusedItemId == null) { _initFocus(1); return; }

  const cell = state.agents[focusedItemId];
  // If focused on an agent that isn't selected yet, open its drawer first
  if (cell && cell.cell_type !== 'terminal' && selectedAgentId !== focusedItemId) {
    selectedAgentId = focusedItemId;
    render();
    const el = document.querySelector('.focused');
    if (el) el.scrollIntoView({ block: 'nearest' });
    return;
  }

  // Move down within current group only
  const groupItems = _currentGroupItems();
  if (groupItems) _moveInList(groupItems, 1);
}

function moveFocusUp() {
  if (focusedItemId == null) { _initFocus(-1); return; }

  // Move up within current group only
  const groupItems = _currentGroupItems();
  if (groupItems) _moveInList(groupItems, -1);
}

function switchGroup(delta) {
  const groups = window._navGroupOrder || [];
  if (groups.length === 0) return;

  const currentGroup = _focusedGroup();
  let idx = currentGroup ? groups.indexOf(currentGroup) : -1;
  const nextIdx = idx < 0 ? 0 : Math.max(0, Math.min(groups.length - 1, idx + delta));
  const targetGroup = groups[nextIdx];

  const groupItems = (window._navByGroup || {})[targetGroup] || [];
  if (groupItems.length > 0) {
    focusedItemId = delta > 0 ? groupItems[0] : groupItems[groupItems.length - 1];
  } else {
    // Empty group — clear focus but we could leave it
    focusedItemId = null;
  }
  render();
  const el = document.querySelector('.focused');
  if (el) el.scrollIntoView({ block: 'nearest' });
}

function activateFocused() {
  if (!focusedItemId) return;
  const cell = state.agents[focusedItemId];
  if (!cell) return;
  if (cell.cell_type !== 'terminal') {
    onAgentClick(focusedItemId);
  } else {
    focusAgent(focusedItemId);
  }
}

function removeFocused() {
  if (!focusedItemId) return;
  removeAgent(focusedItemId);
}


function openAddAgentForFocused() {
  const gname = _focusedGroup() || _firstGroup();
  if (gname) quickAddAgent(gname);
}

function openAddTerminalForFocused() {
  if (selectedAgentId) {
    const cell = state.agents[selectedAgentId];
    if (cell) quickAddTerminal(cell.group, selectedAgentId);
  }
}

function toggleBroadcastForFocused() {
  if (broadcastGroup) {
    closeBroadcast();
  } else {
    const gname = _focusedGroup() || _firstGroup();
    if (gname) openBroadcast(gname);
  }
}

function relaunchFocused() {
  if (!focusedItemId) return;
  const cell = state.agents[focusedItemId];
  if (cell && cell.status === 'stopped') relaunchAgent(focusedItemId);
}

/* -- Main keyboard handler ----------------------------------------------- */

document.addEventListener('keydown', (e) => {
  // If a modal is open, only handle Escape/Enter
  if (document.querySelector('.overlay.visible')) {
    if (e.key === 'Escape') closeModals();
    return;
  }

  // If broadcast input is focused, let it handle its own keys
  if (document.activeElement && document.activeElement.id === 'broadcast-input') {
    if (e.key === 'Escape') closeBroadcast();
    return;
  }

  // Skip shortcuts when any input/select/textarea is focused
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

  switch (e.key) {
    case 'ArrowUp':
      e.preventDefault();
      moveFocusUp();
      break;
    case 'ArrowDown':
      e.preventDefault();
      moveFocusDown();
      break;
    case 'ArrowLeft':
      e.preventDefault();
      moveFocusHorizontal(-1);
      break;
    case 'ArrowRight':
      e.preventDefault();
      moveFocusHorizontal(1);
      break;
    case 'Enter':
      e.preventDefault();
      activateFocused();
      break;
    case 'Backspace':
    case 'Delete':
      e.preventDefault();
      removeFocused();
      break;
    case 'n':
    case 'N':
      e.preventDefault();
      openAddAgentForFocused();
      break;
    case 'g':
    case 'G':
      e.preventDefault();
      openAddGroup();
      break;
    case 't':
    case 'T':
      e.preventDefault();
      openAddTerminalForFocused();
      break;
    case 'b':
    case 'B':
      e.preventDefault();
      toggleBroadcastForFocused();
      break;
    case 'r':
    case 'R':
      e.preventDefault();
      relaunchFocused();
      break;
    case 'Tab':
      e.preventDefault();
      switchGroup(e.shiftKey ? -1 : 1);
      break;
    case 'Escape':
      closeModals();
      closeBroadcast();
      closeMenus();
      break;
  }
});

document.addEventListener('click', () => closeMenus());
document.querySelectorAll('.overlay').forEach(o => {
  o.addEventListener('click', (e) => { if (e.target === o) closeModals(); });
});

['add-name-input', 'add-cmd-input', 'add-dir-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAdd();
    if (e.key === 'Escape') closeModals();
  });
});

connect();
setupDrag();

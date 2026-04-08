/* Keyboard bindings, panel management, and boot */

/* -- Panel management (taskbar app toggle) -------------------------------- */

var _activePanelApp = '';   // '' = collapsed, 'board' = board open
var _panelHeight = 0;       // persisted height in px (0 = use CSS default)
var _panelStateRestored = false;  // true after first state message restores panel

var _panelIds = ['panel-board', 'panel-actions', 'panel-templates', 'panel-context', 'panel-events', 'panel-weaver'];

function togglePanel(appName) {
  var panel = document.getElementById('bottom-panel');
  var buttons = document.querySelectorAll('.taskbar-app');

  if (_activePanelApp === appName) {
    // Collapse
    if (_activePanelApp === 'board'
        && typeof _boardClearLaneEntryRefresh === 'function') {
      _boardClearLaneEntryRefresh();
    }
    _activePanelApp = '';
    panel.classList.add('collapsed');
    buttons.forEach(function(b) { b.classList.remove('active'); });
  } else {
    // Expand / switch
    if (_activePanelApp === 'board' && appName !== 'board'
        && typeof _boardClearLaneEntryRefresh === 'function') {
      _boardClearLaneEntryRefresh();
    }
    _activePanelApp = appName;
    panel.classList.remove('collapsed');
    if (_panelHeight > 0) {
      panel.style.setProperty('--panel-height', _panelHeight + 'px');
    }
    buttons.forEach(function(b) {
      b.classList.toggle('active', b.dataset.app === appName);
    });
    // Show/hide panel content
    _panelIds.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle('panel-hidden', id !== 'panel-' + appName);
    });
    // Render the active app
    if (appName === 'board') renderBoard();
    if (appName === 'actions') tplEditorLoad();
    if (appName === 'templates') {
      if (typeof _agentsPanelView !== 'undefined' && _agentsPanelView === 'history') {
        if (typeof agentHistoryLoad === 'function') agentHistoryLoad();
      } else if (typeof agentTemplateEditorLoad === 'function') {
        agentTemplateEditorLoad();
      }
    }
    if (appName === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
    if (appName === 'events' && typeof renderEvents === 'function') renderEvents();
    if (appName === 'weaver' && typeof renderWeaverPanel === 'function') renderWeaverPanel();
  }
  // Persist panel state to server
  send({ cmd: 'board_set_panel', active: _activePanelApp || '' });
}

function _restorePanelState() {
  if (_panelStateRestored) return;
  _panelStateRestored = true;

  // panel_active: new key; backward compat from board_panel_open
  var active = state.panel_active || '';
  if (!active && state.board_panel_open) active = 'board';
  var height = state.board_panel_height;
  if (height > 0) _panelHeight = height;

  if (active) {
    _activePanelApp = active;
    var panel = document.getElementById('bottom-panel');
    if (panel) {
      panel.classList.remove('collapsed');
      if (_panelHeight > 0) {
        panel.style.setProperty('--panel-height', _panelHeight + 'px');
      }
    }
    _panelIds.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle('panel-hidden', id !== 'panel-' + active);
    });
    document.querySelectorAll('.taskbar-app').forEach(function(b) {
      b.classList.toggle('active', b.dataset.app === active);
    });
    if (active === 'board') renderBoard();
    if (active === 'actions') tplEditorLoad();
    if (active === 'templates' && typeof agentTemplateEditorLoad === 'function') agentTemplateEditorLoad();
    if (active === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
    if (active === 'events' && typeof renderEvents === 'function') renderEvents();
    if (active === 'weaver' && typeof renderWeaverPanel === 'function') renderWeaverPanel();
  }
}

/* -- Panel resize handle -------------------------------------------------- */

(function() {
  var handle = document.getElementById('panel-resize-handle');
  if (!handle) return;

  var dragging = false;
  var startY = 0;
  var startH = 0;

  handle.addEventListener('mousedown', function(e) {
    e.preventDefault();
    var panel = document.getElementById('bottom-panel');
    if (panel.classList.contains('collapsed')) return;
    dragging = true;
    startY = e.clientY;
    startH = panel.offsetHeight;
    document.body.style.cursor = 'ns-resize';
  });

  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    var panel = document.getElementById('bottom-panel');
    var delta = startY - e.clientY;
    var newH = Math.max(80, Math.min(window.innerHeight - 80, startH + delta));
    _panelHeight = newH;
    panel.style.setProperty('--panel-height', newH + 'px');
  });

  document.addEventListener('mouseup', function() {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    // Persist height to server
    if (_panelHeight > 0) {
      send({ cmd: 'board_set_panel', height: _panelHeight });
    }
  });
})();

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
  if (typeof _updateSelectedAgentContext === 'function') {
    _updateSelectedAgentContext(nextAgentId);
  } else {
    selectedAgentId = nextAgentId;
  }

  if (onTerminal) {
    // Was on a terminal — focus the first child terminal of the new agent
    render();
    const childIds = state.children[nextAgentId] || [];
    const wid = getFilterByWindow() ? state.current_window_id : null;
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
    if (typeof _updateSelectedAgentContext === 'function') {
      _updateSelectedAgentContext(focusedItemId);
    } else {
      selectedAgentId = focusedItemId;
    }
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
  // If task history overlay is open, Escape closes it
  if (typeof _taskHistoryOpen !== 'undefined' && _taskHistoryOpen) {
    if (e.key === 'Escape') hideTaskHistory();
    return;
  }

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

  // Board keyboard shortcuts (when board is open, arrows/enter/delete go to board)
  if (_activePanelApp === 'board') {
    if (boardKeydown(e)) { e.preventDefault(); return; }
  }

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
    case 'k':
    case 'K':
      e.preventDefault();
      togglePanel('board');
      break;
    case 'Tab':
      e.preventDefault();
      switchGroup(e.shiftKey ? -1 : 1);
      break;
    case 'Escape':
      if (typeof _boardSelectedCount === 'function' && _boardSelectedCount() > 0) {
        boardClearSelection();
        break;
      }
      closeModals();
      closeBroadcast();
      closeMenus();
      closeContextMenu();
      if (_activePanelApp) togglePanel(_activePanelApp);
      break;
  }
});

document.addEventListener('click', () => { closeMenus(); closeContextMenu(); });
document.querySelectorAll('.overlay').forEach(o => {
  o.addEventListener('click', (e) => { if (e.target === o) closeModals(); });
});

['add-name-input', 'add-cmd-input', 'add-dir-input',
 'add-args-input', 'add-init-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAdd();
    if (e.key === 'Escape') closeModals();
  });
});

/* Group settings modal: Escape to close (no Enter-to-submit since many fields) */
['gs-directory', 'gs-agent-directory', 'gs-terminal-prefix',
 'gs-terminal-boot-cmd', 'gs-terminal-cmd-args',
 'gs-terminal-init-script', 'gs-terminal-directory',
 'gs-weaver-boot-cmd', 'gs-weaver-custom-instructions'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModals();
  });
});

connect();
setupDrag();

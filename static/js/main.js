/* Keyboard bindings, panel management, and boot */

/* -- Panel management (taskbar app toggle) -------------------------------- */

var _activePanelApp = '';   // '' = collapsed, 'board' = board open
var _panelHeight = 0;       // persisted height in px (0 = use CSS default)
var _panelStateRestored = false;  // true after first state message restores panel

var _panelIds = ['panel-board', 'panel-actions', 'panel-templates', 'panel-context', 'panel-events', 'panel-agent'];
var _embeddedPanelMinHeight = 180;
var _defaultPanelMinHeight = 80;
var _workspaceSidebarDefaultWidth = 340;
var _workspaceSidebarStorageKey = 'torque.ide.sidebar_width';

function _syncGroupSwitcherSelectValue() {
  var select = document.getElementById('active-group-select');
  if (!select || typeof _activeGroup !== 'function') return;
  var active = _activeGroup() || '';
  if (select.value !== active) select.value = active;
}

function _scheduleGroupSwitcherSelectSync() {
  var sync = function() { _syncGroupSwitcherSelectValue(); };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(sync);
  } else if (typeof setTimeout === 'function') {
    setTimeout(sync, 0);
  } else {
    sync();
  }
}

function renderGroupSwitcher() {
  var root = document.getElementById('group-switcher');
  var addGroupButton = document.getElementById('add-group-header-btn');
  var singleGroupMode = typeof _singleGroupModeEnabled === 'function'
    && _singleGroupModeEnabled();
  if (addGroupButton) addGroupButton.hidden = singleGroupMode;
  if (!root) return;
  if (!singleGroupMode) {
    root.hidden = true;
    if (root._torqueLastHtml !== '') {
      root.innerHTML = '';
      root._torqueLastHtml = '';
    }
    return;
  }
  var groups = (typeof _groupNamesSorted === 'function')
    ? _groupNamesSorted()
    : Object.keys((state && state.groups) || {}).sort();
  var active = (typeof _activeGroup === 'function') ? (_activeGroup() || '') : '';
  root.hidden = false;
  var html = '<label class="group-switcher-label" for="active-group-select">Group:</label>';
  html += '<select id="active-group-select" class="group-switcher-select"'
    + ' onchange="onActiveGroupSelect(this.value)">';
  if (!groups.length) {
    html += '<option value="" selected disabled>No groups</option>';
  }
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i];
    html += '<option value="' + esc(group) + '"'
      + (group === active ? ' selected' : '')
      + '>' + esc(group) + '</option>';
  }
  html += '<option value="__new_group__">+ New group</option>';
  html += '</select>';
  html += '<button type="button" class="group-switcher-menu-btn"'
    + (active ? '' : ' disabled')
    + ' title="Group settings" aria-label="Group settings" onclick="openActiveGroupSettings(event)">&#9881;</button>';
  if (root._torqueLastHtml !== html) {
    root.innerHTML = html;
    root._torqueLastHtml = html;
  }
  _syncGroupSwitcherSelectValue();
}

function openActiveGroupSettings(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  var group = (typeof _activeGroup === 'function') ? (_activeGroup() || '') : '';
  if (!group || typeof openGroupSettings !== 'function') return;
  openGroupSettings(group);
}

function onActiveGroupSelect(value) {
  if (value === '__new_group__') {
    var select = document.getElementById('active-group-select');
    if (select && typeof _activeGroup === 'function') select.value = _activeGroup() || '';
    _scheduleGroupSwitcherSelectSync();
    if (typeof openAddGroup === 'function') openAddGroup();
    return;
  }
  if (typeof setActiveGroup === 'function') {
    setActiveGroup(value);
    _scheduleGroupSwitcherSelectSync();
  }
}

function _panelRootId(appName) {
  if (appName === 'engineer') return 'panel-agent';
  return 'panel-' + appName;
}

function _panelAppVisible(appName) {
  if (!appName) return false;
  if (typeof _standalonePanelSurfaceVisible === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()) {
    return _standalonePanelSurfaceVisible(appName);
  }
  return typeof _activePanelApp !== 'undefined' && _activePanelApp === appName;
}

function _loadPanelApp(appName) {
  if (appName === 'actions') {
    if (typeof tplEditorEnsureLoaded === 'function') tplEditorEnsureLoaded();
    else if (typeof tplEditorLoad === 'function') tplEditorLoad();
  }
  if (appName === 'templates') {
    if (typeof _agentsPanelView !== 'undefined' && _agentsPanelView === 'history') {
      if (typeof agentHistoryLoad === 'function') agentHistoryLoad();
    } else if (typeof agentTemplateEnsureLoaded === 'function') {
      agentTemplateEnsureLoaded();
    } else if (typeof agentTemplateEditorLoad === 'function') {
      agentTemplateEditorLoad();
    }
  }
}

function _loadVisibleStandalonePanelApps() {
  if (typeof _standaloneVisiblePanelApps !== 'function'
      || typeof _standalonePanelsEnabled !== 'function'
      || !_standalonePanelsEnabled()) {
    return;
  }
  var seen = {};
  _standaloneVisiblePanelApps().forEach(function(appName) {
    if (!appName || seen[appName]) return;
    seen[appName] = true;
    _loadPanelApp(appName);
  });
}

function _visiblePanelAppsForGroupScopeReload() {
  if (typeof _standaloneVisiblePanelApps === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()) {
    return _standaloneVisiblePanelApps();
  }
  return (typeof _activePanelApp !== 'undefined' && _activePanelApp)
    ? [_activePanelApp]
    : [];
}

function _reloadGroupScopedPanelApp(appName) {
  if (appName === 'actions') {
    if (typeof tplEditorBeginGroupSwitch === 'function') {
      tplEditorBeginGroupSwitch();
    } else if (typeof tplEditorLoad === 'function') {
      tplEditorLoad();
    } else {
      _loadPanelApp(appName);
    }
  }
  if (appName === 'templates') {
    if (typeof _agentsPanelView !== 'undefined' && _agentsPanelView === 'history') {
      if (typeof agentHistoryLoad === 'function') agentHistoryLoad();
    } else if (typeof agentTemplateBeginGroupSwitch === 'function') {
      agentTemplateBeginGroupSwitch();
    } else if (typeof agentTemplateEditorLoad === 'function') {
      agentTemplateEditorLoad();
    } else {
      _loadPanelApp(appName);
    }
  }
}

function _reloadVisibleGroupScopedPanelApps() {
  var seen = {};
  _visiblePanelAppsForGroupScopeReload().forEach(function(appName) {
    if ((appName !== 'actions' && appName !== 'templates') || seen[appName]) return;
    seen[appName] = true;
    _reloadGroupScopedPanelApp(appName);
  });
}

function _syncVisibleStandalonePanelApps(previousVisibleApps) {
  if (typeof _standaloneVisiblePanelApps !== 'function'
      || typeof _standalonePanelsEnabled !== 'function'
      || !_standalonePanelsEnabled()) {
    return;
  }
  var prev = {};
  var list = Array.isArray(previousVisibleApps) ? previousVisibleApps : [];
  list.forEach(function(appName) {
    if (appName) prev[appName] = true;
  });
  var seen = {};
  _standaloneVisiblePanelApps().forEach(function(appName) {
    if (!appName || seen[appName]) return;
    seen[appName] = true;
    if (!previousVisibleApps || !prev[appName]) _loadPanelApp(appName);
  });
}

function _panelResizeBounds() {
  var embedded = !!(typeof document !== 'undefined'
    && document
    && document.body
    && document.body.classList
    && document.body.classList.contains('runtime-embedded'));
  var minHeight = embedded ? _embeddedPanelMinHeight : _defaultPanelMinHeight;
  var viewportHeight = (typeof window !== 'undefined'
    && typeof window.innerHeight === 'number'
    && window.innerHeight > 0)
    ? window.innerHeight
    : minHeight;
  var reserved = embedded ? 160 : 80;
  var maxHeight = Math.max(minHeight, viewportHeight - reserved);
  return { min: minHeight, max: maxHeight };
}

function _normalizePanelHeight(height) {
  var next = parseInt(height, 10);
  if (!Number.isFinite(next) || next <= 0) return 0;
  var bounds = _panelResizeBounds();
  return Math.max(bounds.min, Math.min(bounds.max, next));
}

function togglePanel(appName) {
  if (typeof _detachedWindowActive === 'function' && !_detachedWindowActive()
      && typeof _detachedPanelEntry === 'function') {
    var detachedEntry = _detachedPanelEntry(appName);
    if (detachedEntry && detachedEntry.label
        && window.nativeApi && window.nativeApi.available()) {
      window.nativeApi.focusWindow(detachedEntry.label);
      return true;
    }
  }
  if (typeof _standaloneTogglePanel === 'function'
      && _standalonePanelsEnabled()) {
    var handled = _standaloneTogglePanel(appName, { skipOpenHooks: true });
    if (handled) {
      if (_panelAppVisible(appName)) _loadPanelApp(appName);
      if (typeof renderActivePanel === 'function') renderActivePanel();
    }
    return handled;
  }
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
    if (typeof isEmbeddedTerminalMode === 'function'
        && isEmbeddedTerminalMode()
        && typeof focusEmbeddedTerminalWorkspace === 'function') {
      focusEmbeddedTerminalWorkspace(true);
    }
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
      if (el) el.classList.toggle('panel-hidden', id !== _panelRootId(appName));
    });
    // Render the active app
    if (appName === 'board') renderBoard();
    _loadPanelApp(appName);
    if (appName === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
    if (appName === 'events' && typeof renderEvents === 'function') renderEvents();
    if (appName === 'engineer' && typeof renderAgentPanel === 'function') renderAgentPanel();
  }
  // Persist panel state to server
  send({ cmd: 'board_set_panel', active: _activePanelApp || '' });
}

function _restorePanelState() {
  if (_panelStateRestored) return;
  _panelStateRestored = true;

  if (typeof _detachedWindowActive === 'function' && _detachedWindowActive()) {
    var detachedApp = (_detachedWindowInfo && _detachedWindowInfo.panel) || 'board';
    _activePanelApp = detachedApp;
    var detachedPanel = document.getElementById('bottom-panel');
    if (detachedPanel) detachedPanel.classList.remove('collapsed');
    _panelIds.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle('panel-hidden', id !== _panelRootId(detachedApp));
    });
    _loadPanelApp(detachedApp);
    if (detachedApp === 'board') renderBoard();
    if (detachedApp === 'actions' && typeof renderTemplatesPanel === 'function') renderTemplatesPanel();
    if (detachedApp === 'templates' && typeof renderAgentTemplatesPanel === 'function') renderAgentTemplatesPanel();
    if (detachedApp === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
    if (detachedApp === 'events' && typeof renderEvents === 'function') renderEvents();
    if (detachedApp === 'engineer' && typeof renderAgentPanel === 'function') renderAgentPanel();
    if (typeof torqueDetachedWindowBoundsChanged === 'function') {
      torqueDetachedWindowBoundsChanged();
    }
    return;
  }

  if (typeof _restoreStandalonePanelState === 'function'
      && _standalonePanelsEnabled()) {
    _restoreStandaloneWorkspaceSidebarWidth({ persistResolved: true });
    _restoreStandalonePanelState({ persistResolved: true });
    _loadVisibleStandalonePanelApps();
    if (typeof renderActivePanel === 'function') renderActivePanel();
    if (typeof _standaloneRestoreDetachedPanels === 'function') _standaloneRestoreDetachedPanels();
    return;
  }

  // panel_active: new key; backward compat from board_panel_open
  var active = state.panel_active || '';
  if (!active && state.board_panel_open) active = 'board';
  if (!active && typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode()) {
    active = 'board';
  }
  var height = state.board_panel_height;
  if (height > 0) _panelHeight = _normalizePanelHeight(height);

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
      if (el) el.classList.toggle('panel-hidden', id !== _panelRootId(active));
    });
    document.querySelectorAll('.taskbar-app').forEach(function(b) {
      b.classList.toggle('active', b.dataset.app === active);
    });
    if (active === 'board') renderBoard();
    _loadPanelApp(active);
    if (active === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
    if (active === 'events' && typeof renderEvents === 'function') renderEvents();
    if (active === 'engineer' && typeof renderAgentPanel === 'function') renderAgentPanel();
  }
  if (typeof _standaloneRestoreDetachedPanels === 'function') _standaloneRestoreDetachedPanels();
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
    var bounds = _panelResizeBounds();
    var newH = Math.max(bounds.min, Math.min(bounds.max, startH + delta));
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

/* -- Standalone workspace resize handle ---------------------------------- */

var _workspaceSidebarWidth = _workspaceSidebarDefaultWidth;
var _workspaceBoardRenderPending = false;

function _scheduleStandaloneBoardLayoutRender() {
  if (typeof document === 'undefined'
      || !document.body
      || !document.body.classList
      || !document.body.classList.contains('runtime-embedded')) {
    return;
  }
  if (typeof renderBoard !== 'function') return;
  var detachedBoard = typeof _detachedWindowActive === 'function'
    && _detachedWindowActive()
    && typeof _detachedWindowInfo !== 'undefined'
    && _detachedWindowInfo
    && _detachedWindowInfo.panel === 'board';
  if (typeof _standalonePanelSurfaceVisible === 'function'
      && !_standalonePanelSurfaceVisible('board')
      && !detachedBoard) {
    return;
  }
  if (_workspaceBoardRenderPending || typeof requestAnimationFrame !== 'function') return;
  _workspaceBoardRenderPending = true;
  requestAnimationFrame(function() {
    _workspaceBoardRenderPending = false;
    renderBoard();
  });
}

function _workspaceSidebarWidthBounds(opts) {
  opts = opts || {};
  var minWidth = 240;
  var embedded = !!(typeof document !== 'undefined'
    && document
    && document.body
    && document.body.classList
    && document.body.classList.contains('runtime-embedded'));
  if (!opts.allowStandaloneRailShrink
      && embedded
      && typeof _standaloneMinimumShellWidthForLayout === 'function') {
    var layout = (typeof _standalonePanelLayout !== 'undefined' && _standalonePanelLayout)
      ? _standalonePanelLayout
      : (state && state.standalone_panel_layout);
    if (layout) minWidth = Math.max(minWidth, _standaloneMinimumShellWidthForLayout(layout));
  } else if (opts.allowStandaloneRailShrink
      && embedded
      && typeof _standaloneMinimumShellWidthForDrag === 'function') {
    var dragLayout = (typeof _standalonePanelLayout !== 'undefined' && _standalonePanelLayout)
      ? _standalonePanelLayout
      : (state && state.standalone_panel_layout);
    if (dragLayout) minWidth = Math.max(minWidth, _standaloneMinimumShellWidthForDrag(dragLayout));
  }
  var viewportWidth = (typeof window !== 'undefined' && typeof window.innerWidth === 'number' && window.innerWidth > 0)
    ? window.innerWidth
    : 1280;
  var maxWidth = Math.max(320, Math.floor(viewportWidth * 0.62));
  return { min: minWidth, max: Math.max(minWidth, maxWidth) };
}

function _readWorkspaceSidebarWidth() {
  try {
    if (typeof localStorage !== 'undefined') {
      var saved = parseInt(localStorage.getItem(_workspaceSidebarStorageKey) || '', 10);
      if (Number.isFinite(saved) && saved > 0) return saved;
    }
  } catch (_) {}
  return 0;
}

function _standalonePreferredSidebarWidth() {
  var bounds = _workspaceSidebarWidthBounds();
  var viewportWidth = (typeof window !== 'undefined' && typeof window.innerWidth === 'number' && window.innerWidth > 0)
    ? window.innerWidth
    : 1280;
  var preferred = Math.max(640, Math.floor(viewportWidth * 0.56));
  return Math.max(bounds.min, Math.min(bounds.max, preferred));
}

function _persistWorkspaceSidebarWidth(width) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(_workspaceSidebarStorageKey, String(width));
    }
  } catch (_) {}
}

function _applyWorkspaceSidebarWidth(width, opts) {
  opts = opts || {};
  var bounds = _workspaceSidebarWidthBounds(opts);
  var next = parseInt(width, 10);
  if (!Number.isFinite(next)) next = _workspaceSidebarDefaultWidth;
  next = Math.max(bounds.min, Math.min(bounds.max, next));
  var embedded = !!(typeof document !== 'undefined'
    && document
    && document.body
    && document.body.classList
    && document.body.classList.contains('runtime-embedded'));
  _workspaceSidebarWidth = next;
  var root = document.documentElement || document.body;
  if (root && root.style) {
    if (typeof root.style.setProperty === 'function') {
      root.style.setProperty('--standalone-sidebar-width', next + 'px');
    } else {
      root.style['--standalone-sidebar-width'] = next + 'px';
    }
  }
  if (opts.allowStandaloneRailShrink
      && embedded
      && typeof _standaloneConstrainLayoutToShellWidth === 'function'
      && typeof _standalonePanelSetLayoutFromState === 'function') {
    var layout = (typeof _standalonePanelLayout !== 'undefined' && _standalonePanelLayout)
      ? _standalonePanelLayout
      : (state && state.standalone_panel_layout);
    var constrained = _standaloneConstrainLayoutToShellWidth(layout, next);
    if (constrained.changed) _standalonePanelSetLayoutFromState(constrained.layout, { fromServer: true });
  }
  _scheduleStandaloneBoardLayoutRender();
}

function _restoreStandaloneWorkspaceSidebarWidth(opts) {
  opts = opts || {};
  var saved = _readWorkspaceSidebarWidth();
  if (!opts.forceDefault && saved > 0) {
    _applyWorkspaceSidebarWidth(saved);
    var adjusted = _workspaceSidebarWidth;
    var savedAdjusted = adjusted !== saved;
    if (savedAdjusted) _persistWorkspaceSidebarWidth(adjusted);
    return {
      width: adjusted,
      source: savedAdjusted ? 'persisted-adjusted' : 'persisted',
      shouldPersist: savedAdjusted,
    };
  }
  var hasPersistedLayout = typeof _standaloneHasPersistedLayout === 'function'
    && _standaloneHasPersistedLayout(state && state.standalone_panel_layout);
  var next = (opts.forceDefault || !hasPersistedLayout)
    ? _standalonePreferredSidebarWidth()
    : _workspaceSidebarDefaultWidth;
  if (!opts.forceDefault
      && hasPersistedLayout
      && typeof _standaloneMinimumShellWidthForLayout === 'function') {
    next = Math.max(next, _standaloneMinimumShellWidthForLayout(state && state.standalone_panel_layout));
  }
  _applyWorkspaceSidebarWidth(next);
  var shouldPersist = !!opts.persistResolved && (opts.forceDefault || !hasPersistedLayout);
  if (shouldPersist) _persistWorkspaceSidebarWidth(_workspaceSidebarWidth);
  return {
    width: _workspaceSidebarWidth,
    source: opts.forceDefault ? 'default' : (hasPersistedLayout ? 'legacy' : 'fresh-default'),
    shouldPersist: shouldPersist,
  };
}

function restoreStandaloneLayoutDefaults() {
  if (typeof _standalonePanelsEnabled !== 'function' || !_standalonePanelsEnabled()) return false;
  _restoreStandaloneWorkspaceSidebarWidth({
    forceDefault: true,
    persistResolved: true,
  });
  if (typeof _restoreStandalonePanelState === 'function') {
    _restoreStandalonePanelState({
      forceDefault: true,
      persistResolved: true,
    });
  }
  _loadVisibleStandalonePanelApps();
  if (typeof renderActivePanel === 'function') renderActivePanel();
  return true;
}

(function() {
  var handle = document.getElementById('workspace-resize-handle');
  var shell = document.getElementById('workspace-shell');
  if (!handle || !shell) return;

  var saved = _readWorkspaceSidebarWidth();
  if (saved > 0) _workspaceSidebarWidth = saved;
  _applyWorkspaceSidebarWidth(_workspaceSidebarWidth);

  var dragging = false;

  handle.addEventListener('mousedown', function(e) {
    if (!document.body.classList.contains('runtime-embedded')) return;
    e.preventDefault();
    dragging = true;
    document.body.style.cursor = 'col-resize';
    document.body.classList.add('workspace-resizing');
  });

  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    var rect = shell.getBoundingClientRect();
    _applyWorkspaceSidebarWidth(e.clientX - rect.left, { allowStandaloneRailShrink: true });
  });

  document.addEventListener('mouseup', function() {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.classList.remove('workspace-resizing');
    _persistWorkspaceSidebarWidth(_workspaceSidebarWidth);
    if (typeof _standalonePanelSaveLayout === 'function') _standalonePanelSaveLayout();
  });

  handle.addEventListener('dblclick', function() {
    _applyWorkspaceSidebarWidth(_workspaceSidebarDefaultWidth);
    _persistWorkspaceSidebarWidth(_workspaceSidebarWidth);
  });

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', function() {
      _applyWorkspaceSidebarWidth(_workspaceSidebarWidth);
    });
  }
})();

/* -- Keyboard navigation helpers ----------------------------------------- */

var _navPreferredColumn = null;

function _navMeta(id) {
  if (!id) return null;
  const meta = window._navGridItemMeta || {};
  return meta[id] || null;
}

function _navRows() {
  return window._navGridRows || [];
}

function _navCssValue(value) {
  const raw = String(value || '');
  if (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function') {
    return CSS.escape(raw);
  }
  return raw.replace(/"/g, '\\"');
}

function _activeElementNavId() {
  const active = document.activeElement;
  if (!active || !active.dataset) return '';
  return active.dataset.navId || '';
}

function _findFocusedNavElement(id) {
  const meta = _navMeta(id);
  const selectors = [];
  if (id) selectors.push('[data-nav-id="' + _navCssValue(id) + '"]');
  if (meta && meta.focusKey) {
    selectors.push('[data-focus-key="' + _navCssValue(meta.focusKey) + '"]');
  }
  const main = document.getElementById('main');
  for (let i = 0; i < selectors.length; i++) {
    const selector = selectors[i];
    let el = null;
    if (main && typeof main.querySelector === 'function') el = main.querySelector(selector);
    if (!el && typeof document.querySelector === 'function') el = document.querySelector(selector);
    if (el) return el;
  }
  return document.querySelector('.focused');
}

function _scrollFocusedIntoView(id) {
  const el = _findFocusedNavElement(id || focusedItemId);
  if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function _focusNavId(id, opts) {
  if (!id) return;
  opts = opts || {};
  focusedItemId = id;
  const meta = _navMeta(id);
  if (!opts.preserveColumn) {
    _navPreferredColumn = meta && typeof meta.colIndex === 'number'
      ? meta.colIndex
      : null;
  }
  // Auto-commit principal filter when focus lands on a principal card.
  if (meta && meta.type === 'principal'
      && typeof selectPrincipal === 'function') {
    const currentPrincipal = String((state && state.selected_principal_id) || '');
    const targetPrincipal = String(meta.principalId || '');
    if (currentPrincipal !== targetPrincipal) {
      selectPrincipal(targetPrincipal, meta.group || '');
      // selectPrincipal already called render() with the new focus.
      return;
    }
  }
  render();
  const el = _findFocusedNavElement(id);
  if (opts.focusDom && el && typeof el.focus === 'function') el.focus();
  if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function _focusedGroup() {
  const meta = _navMeta(focusedItemId);
  if (meta && meta.group) return meta.group;
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
  const focusable = (window._navFocusableItems && window._navFocusableItems.length)
    ? window._navFocusableItems
    : items;
  if (focusable.length === 0) return;
  const activeIdx = items.findIndex(id => {
    const c = state.agents[id];
    return c && c.session_id === state.active_session_id;
  });
  if (activeIdx >= 0) {
    focusedItemId = items[activeIdx];
  } else {
    focusedItemId = delta > 0 ? focusable[0] : focusable[focusable.length - 1];
  }
  const meta = _navMeta(focusedItemId);
  _navPreferredColumn = meta && typeof meta.colIndex === 'number' ? meta.colIndex : null;
  render();
  _scrollFocusedIntoView(focusedItemId);
}

function _moveInList(list, delta) {
  if (list.length === 0) return;
  const idx = list.indexOf(focusedItemId);
  const next = idx < 0 ? 0 : Math.max(0, Math.min(list.length - 1, idx + delta));
  _focusNavId(list[next]);
}

function _currentGroupItems() {
  const gname = _focusedGroup();
  if (!gname) return null;
  const byGroup = window._navByGroup || {};
  return byGroup[gname] || null;
}

function moveFocusHorizontal(delta) {
  if (focusedItemId == null) { _initFocus(delta); return; }

  const meta = _navMeta(focusedItemId);
  if (meta && typeof meta.rowIndex === 'number') {
    const rows = _navRows();
    const row = rows[meta.rowIndex];
    if (!row || !row.items || row.items.length === 0) return;
    const currentIdx = typeof meta.colIndex === 'number' ? meta.colIndex : 0;
    const nextIdx = Math.max(0, Math.min(row.items.length - 1, currentIdx + delta));
    const nextItem = row.items[nextIdx];
    if (!nextItem || !nextItem.id) return;
    _focusNavId(nextItem.id);
    return;
  }

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

  const meta = _navMeta(focusedItemId);
  if (meta && typeof meta.rowIndex === 'number') {
    const rows = _navRows();
    const targetRowIdx = Math.max(0, Math.min(rows.length - 1, meta.rowIndex + 1));
    const targetRow = rows[targetRowIdx];
    if (!targetRow || !targetRow.items || targetRow.items.length === 0) return;
    // Arrow-down from a principal card lands on the first engineer in that
    // principal's grid (not the column-preserved position).
    if (meta.type === 'principal') {
      for (const item of targetRow.items) {
        if (item && item.type === 'agent') {
          _focusNavId(item.id, { preserveColumn: false });
          return;
        }
      }
    }
    const preferred = typeof _navPreferredColumn === 'number'
      ? _navPreferredColumn
      : (typeof meta.colIndex === 'number' ? meta.colIndex : 0);
    _navPreferredColumn = preferred;
    const targetCol = Math.max(0, Math.min(targetRow.items.length - 1, preferred));
    const target = targetRow.items[targetCol];
    if (target && target.id) _focusNavId(target.id, { preserveColumn: true });
    return;
  }

  // Move down within current group only
  const groupItems = _currentGroupItems();
  if (groupItems) _moveInList(groupItems, 1);
}

function moveFocusUp() {
  if (focusedItemId == null) { _initFocus(-1); return; }

  const meta = _navMeta(focusedItemId);
  if (meta && typeof meta.rowIndex === 'number') {
    const rows = _navRows();
    const targetRowIdx = Math.max(0, Math.min(rows.length - 1, meta.rowIndex - 1));
    const targetRow = rows[targetRowIdx];
    if (!targetRow || !targetRow.items || targetRow.items.length === 0) return;
    // Arrow-up from an engineer/worker lands on the currently-selected
    // principal card, not the column-preserved position.
    if (targetRow.rowType === 'principals-row') {
      const currentPrincipal = String((state && state.selected_principal_id) || '');
      const targetGroup = targetRow.group || '';
      const principalNavId = 'principal:' + targetGroup + ':' + (currentPrincipal || 'user');
      const hasPrincipal = targetRow.items.some(function(item) {
        return item && item.id === principalNavId;
      });
      if (hasPrincipal) {
        _focusNavId(principalNavId, { preserveColumn: true });
        return;
      }
    }
    const preferred = typeof _navPreferredColumn === 'number'
      ? _navPreferredColumn
      : (typeof meta.colIndex === 'number' ? meta.colIndex : 0);
    _navPreferredColumn = preferred;
    const targetCol = Math.max(0, Math.min(targetRow.items.length - 1, preferred));
    const target = targetRow.items[targetCol];
    if (target && target.id) _focusNavId(target.id, { preserveColumn: true });
    return;
  }

  // Move up within current group only
  const groupItems = _currentGroupItems();
  if (groupItems) _moveInList(groupItems, -1);
}

function moveFocusCreationControl(delta) {
  const controls = window._navCreationControls || [];
  if (controls.length === 0) return false;

  const activeNavId = _activeElementNavId();
  const currentId = activeNavId || focusedItemId;
  let idx = controls.findIndex(item => item.id === currentId);
  let target = null;

  if (idx >= 0) {
    idx = (idx + delta + controls.length) % controls.length;
    target = controls[idx];
  } else {
    const meta = _navMeta(currentId);
    if (meta && typeof meta.sort === 'number') {
      if (delta > 0) {
        target = controls.find(item => item.sort > meta.sort) || controls[0];
      } else {
        for (let i = controls.length - 1; i >= 0; i--) {
          if (controls[i].sort < meta.sort) {
            target = controls[i];
            break;
          }
        }
        if (!target) target = controls[controls.length - 1];
      }
    } else {
      target = delta > 0 ? controls[0] : controls[controls.length - 1];
    }
  }

  if (!target || !target.id) return false;
  _focusNavId(target.id, { focusDom: true });
  return true;
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
  const meta = _navMeta(focusedItemId);
  if (meta && meta.type === 'principal'
      && typeof selectPrincipal === 'function') {
    selectPrincipal(String(meta.principalId || ''));
    return;
  }
  if (meta && meta.type === 'control') {
    if (meta.controlType === 'section-new-engineer'
        && typeof openAddEngineerForSection === 'function') {
      openAddEngineerForSection(meta.group || '', meta.architectId || '');
    } else if (meta.controlType === 'section-new-worker'
        && typeof openAddWorkerForSection === 'function') {
      openAddWorkerForSection(meta.group || '');
    } else if (meta.controlType === 'agent-new-architect'
        && typeof openAddArchitectForGroup === 'function') {
      openAddArchitectForGroup(meta.group || '');
    }
    return;
  }
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
  const meta = _navMeta(focusedItemId);
  if (meta && meta.type === 'control') return;
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

function relaunchFocused() {
  if (!focusedItemId) return;
  const cell = state.agents[focusedItemId];
  if (cell && cell.status === 'stopped') relaunchAgent(focusedItemId);
}

function _boardShortcutsEnabled() {
  if (typeof _standalonePanelSurfaceVisible === 'function'
      && _standalonePanelsEnabled()) {
    return _standalonePanelSurfaceVisible('board');
  }
  return _activePanelApp === 'board';
}

/* -- Main keyboard handler ----------------------------------------------- */

document.addEventListener('keydown', (e) => {
  // If a modal is open, only handle Escape/Enter
  if (document.querySelector('.overlay.visible')) {
    if (e.key === 'Escape') closeModals();
    return;
  }

  // Backstop for older/non-modal diff render paths. The read-only diff
  // viewer normally participates in the shared overlay stack above.
  if (typeof _diffViewOpen !== 'undefined' && _diffViewOpen
      && typeof _diffReadOnly !== 'undefined' && _diffReadOnly) {
    if (e.key === 'Escape' && typeof hideDiffView === 'function') {
      e.preventDefault();
      hideDiffView();
    }
    return;
  }

  if (e.metaKey && e.altKey && !e.shiftKey && !e.ctrlKey
      && (e.key === 'e' || e.key === 'E')) {
    e.preventDefault();
    if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
    return;
  }

  if (e.metaKey && e.altKey && !e.shiftKey && !e.ctrlKey
      && (e.key === 'p' || e.key === 'P')) {
    e.preventDefault();
    if (typeof openAddArchitectModal === 'function') {
      var architectGroup = '';
      if (typeof _agentPanelCurrentGroup === 'function') architectGroup = _agentPanelCurrentGroup() || '';
      else if (typeof _currentGroup === 'function') architectGroup = _currentGroup() || '';
      openAddArchitectModal(architectGroup);
    }
    return;
  }

  if ((e.metaKey || e.ctrlKey) && e.shiftKey && !e.altKey
      && (e.key === '?' || e.key === '/')) {
    e.preventDefault();
    if (typeof openCheatsheet === 'function') openCheatsheet();
    return;
  }

  // Skip shortcuts when any input/select/textarea is focused
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

  // Board keyboard shortcuts (when board is open, arrows/enter/delete go to board)
  if (_boardShortcutsEnabled()) {
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
      moveFocusCreationControl(e.shiftKey ? -1 : 1);
      break;
    case 'Escape':
      if (typeof _boardSelectedCount === 'function' && _boardSelectedCount() > 0) {
        boardClearSelection();
        break;
      }
      closeModals();
      closeMenus();
      closeContextMenu();
      if (_activePanelApp) togglePanel(_activePanelApp);
      break;
  }
});

document.addEventListener('click', () => { closeMenus(); closeContextMenu(); });
document.querySelectorAll('.overlay').forEach(o => {
  o.addEventListener('mousedown', (e) => { if (e.target === o) closeModals(); });
});

['add-name-input', 'add-cmd-input', 'add-dir-input',
 'add-args-input', 'add-init-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAdd();
    if (e.key === 'Escape') closeModals();
  });
});

['engineer-name-input', 'engineer-command-input'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitAddEngineer();
    if (e.key === 'Escape') closeModals();
  });
});

/* Group settings modal: Escape to close (no Enter-to-submit since many fields) */
['gs-directory', 'gs-agent-directory', 'gs-terminal-prefix',
 'gs-terminal-boot-cmd', 'gs-terminal-cmd-args',
 'gs-terminal-init-script', 'gs-terminal-directory',
 'gs-engineer-boot-cmd', 'gs-engineer-custom-instructions'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModals();
  });
});

connect();
setupDrag();

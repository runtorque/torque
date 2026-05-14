/* Rendering — main UI, agent cells, terminal rows */

if (typeof taskIsEngineerMessageFollowup !== 'function') {
  var taskIsEngineerMessageFollowup = function(task) {
    var labels = (task && Array.isArray(task.labels)) ? task.labels : [];
    return labels.indexOf('torque:engineer-message') >= 0;
  };
}

function agentIcon(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return AGENT_ICONS[Math.abs(h) % AGENT_ICONS.length];
}

function processInfo(name) {
  const key = (name || '').toLowerCase().replace(/^-/, '');
  if (PROCESS_MAP[key]) return PROCESS_MAP[key];
  const label = key ? key.slice(0, 3).toUpperCase() : '?';
  return { label, color: '#30363d' };
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function formatCode(s) {
  return esc(s).replace(/`([^`]+)`/g, '<span class="code-inline">$1</span>');
}

function _trimTrailingSlash(path) {
  const text = String(path || '');
  if (!text || text === '/') return text;
  return text.replace(/\/+$/, '');
}

function _pathBaseName(path) {
  const text = _trimTrailingSlash(path);
  if (!text) return '';
  const parts = text.split('/');
  return parts[parts.length - 1] || '';
}

function _homeDirectoryPrefix() {
  return _trimTrailingSlash(
    (state && state.runtime && state.runtime.home_directory) || ''
  );
}

function _formatDisplayPath(path, repoRoot) {
  const fullPath = _trimTrailingSlash(path);
  if (!fullPath) return '';

  const root = _trimTrailingSlash(repoRoot);
  if (root && (fullPath === root || fullPath.startsWith(root + '/'))) {
    const repoName = _pathBaseName(root);
    if (!repoName) return fullPath;
    const rel = fullPath === root ? '' : fullPath.slice(root.length + 1);
    return repoName + (rel ? '/' + rel : '');
  }

  const home = _homeDirectoryPrefix();
  if (home && (fullPath === home || fullPath.startsWith(home + '/'))) {
    const rel = fullPath === home ? '' : fullPath.slice(home.length + 1);
    return '~' + (rel ? '/' + rel : '');
  }

  return fullPath
    .replace(/^\/Users\/[^/]+(?=\/|$)/, '~')
    .replace(/^\/home\/[^/]+(?=\/|$)/, '~');
}

function _embeddedRuntimeEnabled() {
  if (typeof isEmbeddedTerminalMode === 'function') return !!isEmbeddedTerminalMode();
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}


var AGENT_FOCUS_SPLIT_KEY = 'engineer_panel_split_fraction';
var AGENT_FOCUS_DEFAULT_FRACTION = 0.30;
var AGENT_FOCUS_MIN_HEIGHT = 120;
var AGENT_GRID_MIN_HEIGHT = 200;
var _agentFocusResize = null;
var _agentFocusResizeRaf = 0;
var _agentFocusResizePendingHeight = 0;

function _agentFocusClampFraction(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return AGENT_FOCUS_DEFAULT_FRACTION;
  return Math.max(0.12, Math.min(0.75, n));
}

function _agentFocusPersistedFraction() {
  if (!state) return AGENT_FOCUS_DEFAULT_FRACTION;
  const raw = state[AGENT_FOCUS_SPLIT_KEY];
  if (raw === null || raw === undefined || raw === '') return AGENT_FOCUS_DEFAULT_FRACTION;
  return _agentFocusClampFraction(raw);
}

function _agentFocusSplitParts(root) {
  root = root || document.getElementById('main');
  if (!root || typeof root.querySelector !== 'function') return null;
  const split = root.querySelector('[data-agent-split]');
  const grid = root.querySelector('[data-agent-grid-pane]');
  const handle = root.querySelector('[data-agent-focus-resizer]');
  const focus = root.querySelector('[data-agent-focus-panel]');
  const focusScroll = root.querySelector('[data-agent-focus-scroll]');
  if (!split || !grid || !handle || !focus || !focusScroll) return null;
  return { root, split, grid, handle, focus, focusScroll };
}

function _agentFocusHandleHeight(parts) {
  if (!parts || !parts.handle || typeof parts.handle.getBoundingClientRect !== 'function') return 8;
  const rect = parts.handle.getBoundingClientRect();
  return Math.max(6, Math.round(rect.height || 8));
}

function _agentFocusContainerHeight(parts) {
  if (!parts || !parts.split || typeof parts.split.getBoundingClientRect !== 'function') return 0;
  const rect = parts.split.getBoundingClientRect();
  return Math.max(0, Math.round(rect.height || 0));
}

function _agentFocusClampHeight(height, totalHeight, handleHeight) {
  let next = Number(height);
  if (!Number.isFinite(next)) next = 0;
  const total = Math.max(0, Number(totalHeight) || 0);
  const handle = Math.max(0, Number(handleHeight) || 0);
  if (total <= 0) return Math.max(AGENT_FOCUS_MIN_HEIGHT, next);
  const maxFocus = Math.max(AGENT_FOCUS_MIN_HEIGHT, total - handle - AGENT_GRID_MIN_HEIGHT);
  return Math.max(AGENT_FOCUS_MIN_HEIGHT, Math.min(maxFocus, next));
}

function _agentFocusApplyHeight(height, opts) {
  opts = opts || {};
  const parts = _agentFocusSplitParts();
  if (!parts) return 0;
  const total = opts.totalHeight || _agentFocusContainerHeight(parts);
  const handle = opts.handleHeight || _agentFocusHandleHeight(parts);
  const clamped = _agentFocusClampHeight(height, total, handle);
  parts.focus.style.flexBasis = clamped + 'px';
  parts.focus.style.height = clamped + 'px';
  return clamped;
}

function _agentFocusApplyPersistedSplit() {
  const parts = _agentFocusSplitParts();
  if (!parts) return;
  const total = _agentFocusContainerHeight(parts);
  if (total <= 0) {
    parts.focus.style.flexBasis = (_agentFocusPersistedFraction() * 100) + '%';
    parts.focus.style.height = '';
    return;
  }
  const handle = _agentFocusHandleHeight(parts);
  _agentFocusApplyHeight(total * _agentFocusPersistedFraction(), {
    totalHeight: total,
    handleHeight: handle,
  });
}

function _agentFocusScheduleResizeHeight(height) {
  _agentFocusResizePendingHeight = height;
  if (_agentFocusResizeRaf) return;
  const apply = function() {
    _agentFocusResizeRaf = 0;
    if (!_agentFocusResize) return;
    _agentFocusApplyHeight(_agentFocusResizePendingHeight, {
      totalHeight: _agentFocusResize.totalHeight,
      handleHeight: _agentFocusResize.handleHeight,
    });
  };
  if (typeof requestAnimationFrame === 'function') {
    _agentFocusResizeRaf = requestAnimationFrame(apply);
  } else {
    apply();
  }
}

function _agentFocusResizeStart(event) {
  const parts = _agentFocusSplitParts();
  if (!parts) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const totalHeight = _agentFocusContainerHeight(parts);
  const handleHeight = _agentFocusHandleHeight(parts);
  const focusRect = parts.focus.getBoundingClientRect ? parts.focus.getBoundingClientRect() : { height: 0 };
  _agentFocusResizePendingHeight = 0;
  _agentFocusResize = {
    startY: event ? event.clientY : 0,
    startHeight: focusRect.height || parts.focus.offsetHeight || (totalHeight * _agentFocusPersistedFraction()),
    totalHeight,
    handleHeight,
  };
  if (document && document.body && document.body.classList) {
    document.body.classList.add('agent-focus-resizing');
    document.body.style.cursor = 'ns-resize';
  }
  document.addEventListener('mousemove', _agentFocusResizeMove);
  document.addEventListener('mouseup', _agentFocusResizeEnd);
}

function _agentFocusResizeMove(event) {
  if (!_agentFocusResize) return;
  const dy = (event ? event.clientY : 0) - _agentFocusResize.startY;
  _agentFocusScheduleResizeHeight(_agentFocusResize.startHeight - dy);
}

function _agentFocusResizeEnd() {
  if (!_agentFocusResize) return;
  const resize = _agentFocusResize;
  _agentFocusResize = null;
  document.removeEventListener('mousemove', _agentFocusResizeMove);
  document.removeEventListener('mouseup', _agentFocusResizeEnd);
  if (document && document.body && document.body.classList) {
    document.body.classList.remove('agent-focus-resizing');
    document.body.style.cursor = '';
  }
  const applied = _agentFocusApplyHeight(_agentFocusResizePendingHeight || resize.startHeight, {
    totalHeight: resize.totalHeight,
    handleHeight: resize.handleHeight,
  });
  const available = Math.max(1, (resize.totalHeight || 0) - (resize.handleHeight || 0));
  const fraction = _agentFocusClampFraction(applied / available);
  if (state) state[AGENT_FOCUS_SPLIT_KEY] = fraction;
  if (typeof send === 'function') {
    send({ cmd: 'ui_set_engineer_panel_split', fraction: fraction });
  }
}

var _activeGroupSurfaceStateByGroup = {};
var _activeGroupStoragePrefix = 'torque.active_group';
var _pendingActiveGroup = '';
var _lastPersistedActiveGroup = null;

function _torqueUiMode() {
  const runtime = (state && state.runtime) || {};
  const explicit = String(runtime.mode || '').trim().toLowerCase();
  if (explicit) return explicit;
  if (runtime.standalone) return 'standalone';
  if (runtime.embedded_terminal) return 'standalone';
  return 'toolbelt';
}

function _singleGroupModeEnabled() {
  const mode = _torqueUiMode();
  return mode === 'standalone' || mode === 'desktop';
}

function _groupNamesSorted(names) {
  const list = Array.isArray(names)
    ? names.slice()
    : Object.keys((state && state.groups) || {});
  list.sort(function(a, b) {
    return String(a || '').localeCompare(String(b || ''), undefined, {
      sensitivity: 'base',
    });
  });
  return list;
}

function _activeGroupStorageKey() {
  const runtime = (state && state.runtime) || {};
  const parts = [
    _activeGroupStoragePrefix,
    runtime.profile || '',
    runtime.port || ((typeof location !== 'undefined' && location.host) || ''),
  ];
  return parts.join(':');
}

function _readStoredActiveGroup() {
  if (typeof sessionStorage === 'undefined') return '';
  try {
    return String(sessionStorage.getItem(_activeGroupStorageKey()) || '').trim();
  } catch (_e) {
    return '';
  }
}

function _writeStoredActiveGroup(group) {
  if (typeof sessionStorage === 'undefined') return;
  try {
    const key = _activeGroupStorageKey();
    if (group) sessionStorage.setItem(key, group);
    else sessionStorage.removeItem(key);
  } catch (_e) {}
}

function _persistActiveGroup(group) {
  const next = String(group || '').trim();
  if (_lastPersistedActiveGroup === next) return;
  _lastPersistedActiveGroup = next;
  if (typeof send === 'function') {
    send({ cmd: 'ui_select_group', group: next });
  }
}

function _normalizeActiveGroup(group, names) {
  const available = _groupNamesSorted(names);
  if (!available.length) return '';
  const wanted = String(group || '').trim();
  if (wanted && available.indexOf(wanted) >= 0) return wanted;
  return available[0];
}

function _activeGroup() {
  const names = Object.keys((state && state.groups) || {});
  if (!names.length) {
    if (state) state.active_group = '';
    _writeStoredActiveGroup('');
    return '';
  }
  const current = String((state && state.active_group) || '').trim();
  const pending = String(_pendingActiveGroup || current || '').trim();
  const stored = _readStoredActiveGroup();
  const desired = pending || stored;
  const pendingCreate = !!(
    _pendingActiveGroup
    && desired === _pendingActiveGroup
    && names.indexOf(_pendingActiveGroup) < 0
  );
  if (pendingCreate) {
    const visible = _normalizeActiveGroup(
      current && current !== _pendingActiveGroup ? current : '',
      names,
    );
    if (state) state.active_group = visible;
    if (visible) _writeStoredActiveGroup(visible);
    return visible;
  }
  const next = _normalizeActiveGroup(desired, names);
  if (_pendingActiveGroup && next === _pendingActiveGroup) _pendingActiveGroup = '';
  if (state) state.active_group = next;
  if (next && stored !== next) _writeStoredActiveGroup(next);
  if (current && current !== next) _persistActiveGroup(next);
  return next;
}

function _activeGroupTransition(prevGroup, nextGroup, opts) {
  opts = opts || {};
  if (!_singleGroupModeEnabled()) return { changed: false, saved: null };
  const prev = String(prevGroup || '').trim();
  const next = String(nextGroup || '').trim();
  if (prev === next) return { changed: false, saved: null };

  _abortActiveGroupDrag();
  _captureActiveGroupUiState(prev);
  if (state) state.active_group = next;
  if (_pendingActiveGroup && _pendingActiveGroup === next) _pendingActiveGroup = '';
  _writeStoredActiveGroup(next);
  _persistActiveGroup(next);
  const saved = _applyActiveGroupUiState(next);
  if (typeof _persistSelectedAgentFromLocal === 'function') {
    _persistSelectedAgentFromLocal();
  }

  if (typeof _reloadVisibleGroupScopedPanelApps === 'function') {
    _reloadVisibleGroupScopedPanelApps();
  }

  const result = { changed: true, saved };
  if (opts.render === false) return result;

  render({ skipPanelRefresh: true });
  if (typeof renderActivePanel === 'function') renderActivePanel();
  _restoreActiveGroupSurfaces(saved);
  if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
  return result;
}

function _prepareActiveGroupStateTransition(prevGroup, nextGroup) {
  return _activeGroupTransition(prevGroup, nextGroup, { render: false });
}

function _finishActiveGroupStateTransition(result) {
  if (!result || !result.changed) return;
  _restoreActiveGroupSurfaces(result.saved);
  if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
}

function _activeGroupNamesForRender(names) {
  if (!_singleGroupModeEnabled()) return names;
  const active = _activeGroup();
  return active ? [active] : [];
}

function _renderAgentGroupTabsHtml() {
  if (!_singleGroupModeEnabled()) return '';
  const groups = _groupNamesSorted();
  const active = _activeGroup() || '';
  let html = '<div class="agent-group-tabs" data-agent-group-tabs>';
  html += '<div class="agent-group-tabs-list" role="tablist" aria-label="Groups">';
  if (!groups.length) {
    html += '<span class="agent-group-tabs-empty">No groups</span>';
  }
  for (const group of groups) {
    const selected = group === active;
    const count = ((state.groups || {})[group] || []).length;
    const groupArg = _jsStringAttr(group);
    html += '<div'
      + ' class="agent-group-tab' + (selected ? ' active' : '') + '"'
      + ' role="tab"'
      + ' tabindex="0"'
      + ' aria-selected="' + (selected ? 'true' : 'false') + '"'
      + ' title="' + esc(group) + '"'
      + ' onclick="onGroupTabClick(' + groupArg + ', event)"'
      + ' onkeydown="if(event.key===\'Enter\'||event.key===\' \'){onGroupTabClick(' + groupArg + ', event)}">'
      + '<span class="agent-group-tab-name">' + esc(group) + '</span>'
      + '<span class="agent-group-tab-count">' + count + '</span>'
      + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-group-tab-actions">';
  html += '<button type="button" class="agent-group-tab-action agent-group-tab-action-new"'
    + ' onclick="openAddGroup()">+ New Group</button>';
  html += '<button type="button" class="agent-group-tab-action agent-group-tab-action-settings"'
    + (active ? '' : ' disabled')
    + ' title="Group settings" aria-label="Group settings"'
    + ' onclick="openActiveGroupSettings(event)">&#9881;</button>';
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentBelongsToGroup(agentId, group) {
  if (!agentId || !state || !state.agents || !state.agents[agentId]) return false;
  return String(state.agents[agentId].group || '') === String(group || '');
}

function _focusedItemBelongsToGroup(focusId, group) {
  const id = String(focusId || '');
  const g = String(group || '');
  if (!id || !g) return false;
  if (_agentBelongsToGroup(id, g)) return true;
  if (id.indexOf('principal:' + g + ':') === 0) return true;
  if (id.indexOf('grid-control:') === 0 && id.indexOf(':' + g) >= 0) return true;
  return false;
}

function _clonePlainObject(value) {
  if (!value || typeof value !== 'object') return {};
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_e) {
    return Object.assign({}, value);
  }
}

function _captureBoardGroupUiState() {
  if (typeof _boardSelectedLane === 'undefined') return null;
  if (typeof _boardSyncActiveViewState === 'function') {
    _boardSyncActiveViewState(document.getElementById('board-cards'));
  }
  if (typeof _boardAddingTask !== 'undefined' && _boardAddingTask) {
    const input = document.getElementById('board-add-task-input');
    if (input && 'value' in input) _boardAddingTaskDraft = input.value;
  }
  return {
    selectedLane: _boardSelectedLane || '',
    focusedTask: typeof _boardFocusedTask !== 'undefined' ? (_boardFocusedTask || '') : '',
    addingTask: typeof _boardAddingTask !== 'undefined' ? !!_boardAddingTask : false,
    addingTaskDraft: typeof _boardAddingTaskDraft !== 'undefined' ? (_boardAddingTaskDraft || '') : '',
    addingTaskAgent: typeof _boardAddingTaskAgent !== 'undefined' ? (_boardAddingTaskAgent || '') : '',
    addingTaskLane: typeof _boardAddingTaskLane !== 'undefined' ? (_boardAddingTaskLane || '') : '',
    inlineDraftId: typeof _boardInlineDraftId !== 'undefined' ? (_boardInlineDraftId || '') : '',
    inlineAttachments: typeof _boardInlineAttachments !== 'undefined' && Array.isArray(_boardInlineAttachments)
      ? _boardInlineAttachments.slice()
      : [],
    showSchedules: typeof _boardShowSchedules !== 'undefined' ? !!_boardShowSchedules : false,
    showArchived: typeof _boardShowArchived !== 'undefined' ? !!_boardShowArchived : false,
    selectedTasks: typeof _boardSelectedTasks !== 'undefined' ? _clonePlainObject(_boardSelectedTasks) : {},
    lastSelectedTask: typeof _boardLastSelectedTask !== 'undefined' ? (_boardLastSelectedTask || '') : '',
    quickEditTask: typeof _boardQuickEditTask !== 'undefined' ? (_boardQuickEditTask || '') : '',
    quickEditKind: typeof _boardQuickEditKind !== 'undefined' ? (_boardQuickEditKind || '') : '',
    cardsScrollTop: typeof _boardCardsScrollTop !== 'undefined' ? (_boardCardsScrollTop || 0) : 0,
    activeViewKey: typeof _boardActiveViewKey !== 'undefined' ? (_boardActiveViewKey || '') : '',
  };
}

function _restoreBoardGroupUiState(saved) {
  if (!saved || typeof _boardSelectedLane === 'undefined') return;
  _boardSelectedLane = saved.selectedLane || _boardSelectedLane || '';
  if (typeof _boardFocusedTask !== 'undefined') _boardFocusedTask = saved.focusedTask || '';
  if (typeof _boardAddingTask !== 'undefined') _boardAddingTask = !!saved.addingTask;
  if (typeof _boardAddingTaskDraft !== 'undefined') _boardAddingTaskDraft = saved.addingTaskDraft || '';
  if (typeof _boardAddingTaskAgent !== 'undefined') _boardAddingTaskAgent = saved.addingTaskAgent || '';
  if (typeof _boardAddingTaskLane !== 'undefined') _boardAddingTaskLane = saved.addingTaskLane || '';
  if (typeof _boardInlineDraftId !== 'undefined') _boardInlineDraftId = saved.inlineDraftId || '';
  if (typeof _boardInlineAttachments !== 'undefined') {
    _boardInlineAttachments = Array.isArray(saved.inlineAttachments)
      ? saved.inlineAttachments.slice()
      : [];
  }
  if (typeof _boardShowSchedules !== 'undefined') _boardShowSchedules = !!saved.showSchedules;
  if (typeof _boardShowArchived !== 'undefined') _boardShowArchived = !!saved.showArchived;
  if (typeof _boardSelectedTasks !== 'undefined') _boardSelectedTasks = _clonePlainObject(saved.selectedTasks);
  if (typeof _boardLastSelectedTask !== 'undefined') _boardLastSelectedTask = saved.lastSelectedTask || '';
  if (typeof _boardQuickEditTask !== 'undefined') _boardQuickEditTask = saved.quickEditTask || '';
  if (typeof _boardQuickEditKind !== 'undefined') _boardQuickEditKind = saved.quickEditKind || '';
  if (typeof _boardCardsScrollTop !== 'undefined') _boardCardsScrollTop = saved.cardsScrollTop || 0;
  if (typeof _boardActiveViewKey !== 'undefined') _boardActiveViewKey = saved.activeViewKey || '';
  if (typeof _boardFilterStateGroup !== 'undefined') _boardFilterStateGroup = '';
  if (typeof _boardSelectedLaneStateGroup !== 'undefined') _boardSelectedLaneStateGroup = '';
  if (typeof _boardPersistSelectedLane === 'function') _boardPersistSelectedLane();
}

function _resetBoardGroupUiStateForFreshGroup() {
  if (typeof _boardSelectedLane === 'undefined') return;
  _boardSelectedLane = '';
  if (typeof _boardFocusedTask !== 'undefined') _boardFocusedTask = '';
  if (typeof _boardAddingTask !== 'undefined') _boardAddingTask = false;
  if (typeof _boardAddingTaskDraft !== 'undefined') _boardAddingTaskDraft = '';
  if (typeof _boardAddingTaskAgent !== 'undefined') _boardAddingTaskAgent = '';
  if (typeof _boardAddingTaskLane !== 'undefined') _boardAddingTaskLane = '';
  if (typeof _boardInlineDraftId !== 'undefined') _boardInlineDraftId = '';
  if (typeof _boardInlineAttachments !== 'undefined') _boardInlineAttachments = [];
  if (typeof _boardShowSchedules !== 'undefined') _boardShowSchedules = false;
  if (typeof _boardShowArchived !== 'undefined') _boardShowArchived = false;
  if (typeof _boardSelectedTasks !== 'undefined') _boardSelectedTasks = {};
  if (typeof _boardLastSelectedTask !== 'undefined') _boardLastSelectedTask = '';
  if (typeof _boardQuickEditTask !== 'undefined') _boardQuickEditTask = '';
  if (typeof _boardQuickEditKind !== 'undefined') _boardQuickEditKind = '';
  if (typeof _boardCardsScrollTop !== 'undefined') _boardCardsScrollTop = 0;
  if (typeof _boardActiveViewKey !== 'undefined') _boardActiveViewKey = '';
  if (typeof _boardFilterStateGroup !== 'undefined') _boardFilterStateGroup = '';
  if (typeof _boardSelectedLaneStateGroup !== 'undefined') _boardSelectedLaneStateGroup = '';
}

function _captureActiveGroupUiState(group) {
  const g = String(group || '').trim();
  if (!g) return;
  if (typeof _captureAgentDetailDrafts === 'function') _captureAgentDetailDrafts();
  if (typeof _terminalComposePersistFromDom === 'function') {
    const terminalRoot = document.getElementById('terminal-workspace');
    if (terminalRoot) _terminalComposePersistFromDom(terminalRoot);
  }
  const main = document.getElementById('main');
  const surfaces = {};
  if (main && typeof _captureSurfaceState === 'function') {
    surfaces.main = _captureSurfaceState(main, {
      scrollSelectors: [
        ':root',
        '.agents-grid-pane',
        '.agent-focus-panel-scroll',
        '.mcp-log',
        '.loose-workers-strip',
      ],
      captureFocusKey: typeof _captureMainFocusKey === 'function'
        ? _captureMainFocusKey
        : null,
    });
  }
  const panelIds = [
    'panel-board',
    'panel-actions',
    'panel-templates',
    'panel-context',
    'panel-events',
    'panel-agent',
    'panel-supervisor',
  ];
  for (let i = 0; i < panelIds.length; i++) {
    const el = document.getElementById(panelIds[i]);
    if (el && typeof _captureSurfaceState === 'function') {
      surfaces[panelIds[i]] = _captureSurfaceState(el);
    }
  }
  _activeGroupSurfaceStateByGroup[g] = {
    selectedAgentId: typeof selectedAgentId !== 'undefined' ? (selectedAgentId || '') : '',
    selectedTerminalId: typeof selectedTerminalId !== 'undefined' ? (selectedTerminalId || '') : '',
    focusedItemId: typeof focusedItemId !== 'undefined' ? (focusedItemId || '') : '',
    selectedPrincipalId: state ? String(state.selected_principal_id || '') : '',
    board: _captureBoardGroupUiState(),
    agentPanelTabs: typeof _agentPanelLastSelectedTabByKind !== 'undefined'
      ? _clonePlainObject(_agentPanelLastSelectedTabByKind)
      : null,
    surfaces,
  };
}

function _applyActiveGroupUiState(group) {
  const g = String(group || '').trim();
  const saved = g ? _activeGroupSurfaceStateByGroup[g] : null;
  if (saved) {
    selectedAgentId = _agentBelongsToGroup(saved.selectedAgentId, g)
      ? saved.selectedAgentId
      : null;
    selectedTerminalId = _agentBelongsToGroup(saved.selectedTerminalId, g)
      ? saved.selectedTerminalId
      : null;
    focusedItemId = _focusedItemBelongsToGroup(saved.focusedItemId, g)
      ? saved.focusedItemId
      : null;
    if (state) state.selected_principal_id = saved.selectedPrincipalId || '';
    _restoreBoardGroupUiState(saved.board);
    if (saved.agentPanelTabs
        && typeof _agentPanelLastSelectedTabByKind !== 'undefined') {
      _agentPanelLastSelectedTabByKind = _clonePlainObject(saved.agentPanelTabs);
    }
    return saved;
  }
  if (typeof selectedAgentId !== 'undefined'
      && !_agentBelongsToGroup(selectedAgentId, g)) selectedAgentId = null;
  if (typeof selectedTerminalId !== 'undefined'
      && !_agentBelongsToGroup(selectedTerminalId, g)) selectedTerminalId = null;
  if (typeof focusedItemId !== 'undefined'
      && !_focusedItemBelongsToGroup(focusedItemId, g)) focusedItemId = null;
  if (state) state.selected_principal_id = '';
  _resetBoardGroupUiStateForFreshGroup();
  if (typeof _agentPanelLastSelectedTabByKind !== 'undefined') {
    _agentPanelLastSelectedTabByKind = {};
  }
  if (typeof _boardFilterStateGroup !== 'undefined') _boardFilterStateGroup = '';
  return null;
}

function _restoreActiveGroupSurfaces(saved) {
  if (!saved || !saved.surfaces || typeof _restoreSurfaceState !== 'function') return;
  if (saved.surfaces.main) {
    _restoreSurfaceState(document.getElementById('main'), saved.surfaces.main);
  }
  for (const id in saved.surfaces) {
    if (id === 'main') continue;
    _restoreSurfaceState(document.getElementById(id), saved.surfaces[id]);
  }
}

function _abortActiveGroupDrag() {
  if (typeof dragInProgress !== 'undefined') dragInProgress = false;
  if (typeof _dragId !== 'undefined') _dragId = null;
  if (typeof _dragType !== 'undefined') _dragType = null;
  if (typeof _boardDragId !== 'undefined') _boardDragId = '';
  if (typeof document !== 'undefined' && document.querySelectorAll) {
    document.querySelectorAll('.dragging, .drop-before, .drop-after, .drop-target')
      .forEach(function(el) {
        if (el && el.classList) {
          el.classList.remove('dragging', 'drop-before', 'drop-after', 'drop-target');
        }
      });
  }
  if (typeof _clearDropIndicators === 'function') _clearDropIndicators();
}

function setActiveGroup(group, opts) {
  opts = opts || {};
  if (!_singleGroupModeEnabled()) return false;
  const requested = String(group || '').trim();
  const names = Object.keys((state && state.groups) || {});
  if (opts.allowPending && requested && names.indexOf(requested) < 0) {
    _pendingActiveGroup = requested;
    _writeStoredActiveGroup(requested);
    if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
    return true;
  }
  const next = _normalizeActiveGroup(requested, names);
  const prev = _activeGroup();
  if (next === prev) {
    if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
    return true;
  }
  _activeGroupTransition(prev, next);
  return true;
}

function _workerOwnerEngineerId(agent, visibleById) {
  if (!agent) return '';
  const ownerId = String(
    agent.owner_engineer_id
    || agent.created_by_engineer_id
    || ''
  ).trim();
  if (!ownerId) return '';
  const owner = visibleById && visibleById[ownerId] ? visibleById[ownerId] : null;
  if (!owner || (owner.kind || '') !== 'engineer') return '';
  return ownerId;
}

function _agentRawOwnerEngineerId(agent) {
  if (!agent) return '';
  return String(
    agent.owner_engineer_id
    || agent.created_by_engineer_id
    || ''
  ).trim();
}

function _agentCreationSortValue(agent, fallbackIndex) {
  if (!agent) return fallbackIndex;
  const raw = agent.created_at || agent.created || agent.started_at || '';
  if (raw !== '' && raw != null) {
    if (typeof raw === 'number') {
      if (Number.isFinite(raw)) return raw;
    } else {
      const numeric = Number(raw);
      if (Number.isFinite(numeric) && String(raw).trim() !== '') return numeric;
      const parsed = Date.parse(String(raw));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return fallbackIndex;
}

function _sortAgentsByCreation(agents, indexById) {
  return (Array.isArray(agents) ? agents : []).slice().sort(function(a, b) {
    const aIndex = indexById && Object.prototype.hasOwnProperty.call(indexById, a.id)
      ? indexById[a.id] : 0;
    const bIndex = indexById && Object.prototype.hasOwnProperty.call(indexById, b.id)
      ? indexById[b.id] : 0;
    const av = _agentCreationSortValue(a, aIndex);
    const bv = _agentCreationSortValue(b, bIndex);
    if (av !== bv) return av - bv;
    if (aIndex !== bIndex) return aIndex - bIndex;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function _isWorkerLikeAgent(agent) {
  if (!agent || agent.cell_type !== 'agent') return false;
  const kind = String(agent.kind || '').trim();
  return kind === 'worker' || (!kind && kind !== 'architect' && kind !== 'engineer');
}

function _agentDismissedAt(agent) {
  if (!agent) return 0;
  const value = Number(agent.dismissed_at || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function _isDismissedEngineer(agent) {
  return !!(agent && (agent.kind || '') === 'engineer' && _agentDismissedAt(agent));
}

function _isTombstonedAgent(agent) {
  const value = Number((agent && agent.deleted_at) || 0);
  return Number.isFinite(value) && value > 0;
}

function _isDismissedArchitect(agent) {
  return !!(agent && (agent.kind || '') === 'architect' && _agentDismissedAt(agent));
}

function _isLifecycleDismissedAgent(agent) {
  return _isDismissedEngineer(agent) || _isDismissedArchitect(agent);
}

function _buildHierarchicalAgentSections(agents) {
  const visibleById = {};
  const architects = [];
  const userEngineers = [];
  const engineersByArchitect = {};
  const workersByEngineer = {};
  const looseWorkers = [];
  const list = Array.isArray(agents) ? agents.slice() : [];
  const indexById = {};

  for (let i = 0; i < list.length; i++) {
    const agent = list[i];
    if (!agent) continue;
    visibleById[agent.id] = agent;
    indexById[agent.id] = i;
  }

  for (const agent of list) {
    if (!agent) continue;
    if ((agent.kind || '') === 'architect') {
      architects.push(agent);
      continue;
    }
    if ((agent.kind || '') === 'engineer') {
      const architectId = String(agent.hired_by_architect_id || '').trim();
      const architect = architectId ? visibleById[architectId] : null;
      if (architect && (architect.kind || '') === 'architect') {
        if (!engineersByArchitect[architectId]) engineersByArchitect[architectId] = [];
        engineersByArchitect[architectId].push(agent);
      } else {
        userEngineers.push(agent);
      }
      continue;
    }
    const ownerId = _workerOwnerEngineerId(agent, visibleById);
    if (_isWorkerLikeAgent(agent) && ownerId) {
      if (!workersByEngineer[ownerId]) workersByEngineer[ownerId] = [];
      workersByEngineer[ownerId].push(agent);
      continue;
    }
    if (_isWorkerLikeAgent(agent) && !_agentRawOwnerEngineerId(agent)) {
      looseWorkers.push(agent);
      continue;
    }
    if (_isWorkerLikeAgent(agent)) looseWorkers.push(agent);
  }

  const visibleEngineerIds = {};
  for (const engineer of userEngineers) visibleEngineerIds[engineer.id] = true;
  for (const architectId in engineersByArchitect) {
    for (const engineer of engineersByArchitect[architectId]) visibleEngineerIds[engineer.id] = true;
  }

  function engineerRows(engineers) {
    const rows = [];
    const sortedEngineers = _sortAgentsByCreation(engineers, indexById);
    for (const engineer of sortedEngineers) {
      rows.push({
        engineer,
        workers: _sortAgentsByCreation(workersByEngineer[engineer.id] || [], indexById),
      });
    }
    return rows;
  }

  const sections = [{
    key: 'user',
    type: 'user',
    architect: null,
    looseWorkers: _sortAgentsByCreation(looseWorkers, indexById),
    rows: engineerRows(userEngineers),
  }];

  const sortedArchitects = _sortAgentsByCreation(architects, indexById);
  for (const architect of sortedArchitects) {
    sections.push({
      key: 'architect:' + String(architect.id || ''),
      type: 'architect',
      architect,
      looseWorkers: [],
      rows: engineerRows(engineersByArchitect[architect.id] || []),
    });
  }

  const ordered = [];
  for (const section of sections) {
    if (section.architect) ordered.push(section.architect);
    if (section.type === 'user') {
      for (const worker of section.looseWorkers) ordered.push(worker);
    }
    for (const row of section.rows) {
      ordered.push(row.engineer);
      for (const worker of row.workers) ordered.push(worker);
    }
  }

  return {
    sections,
    orderedAgents: ordered,
    visibleAgentById: visibleById,
    visibleEngineerIds,
  };
}

function _sortAgentsHierarchically(agents) {
  return _buildHierarchicalAgentSections(agents).orderedAgents;
}

function _taskCreatedByValue(task) {
  if (!task) return 'user';
  const explicit = String(task.created_by || '').trim();
  if (explicit) return explicit;
  const architectId = String(task.created_by_architect_id || '').trim();
  if (architectId) return 'architect:' + architectId;
  const engineerId = String(task.created_by_engineer_id || '').trim();
  if (engineerId) return 'engineer:' + engineerId;
  if (String(task.parent_task_id || '').trim()) return 'system';
  return 'user';
}

function _lookupAgentDisplayName(agentId) {
  const id = String(agentId || '').trim();
  if (!id || !state || !state.agents || !state.agents[id]) return '';
  return state.agents[id].name || state.agents[id].slug || id;
}

function _taskCreatedByMeta(task) {
  const raw = _taskCreatedByValue(task);
  let kind = raw;
  let id = '';
  const colon = raw.indexOf(':');
  if (colon >= 0) {
    kind = raw.slice(0, colon);
    id = raw.slice(colon + 1);
  }
  kind = String(kind || 'user').trim();
  id = String(id || '').trim();
  const agentName = id ? _lookupAgentDisplayName(id) : '';
  const fallbackName = id || '';
  const name = agentName || fallbackName;
  if (kind === 'architect') {
    return {
      raw,
      kind,
      id,
      name,
      icon: '\u25B3',
      shortLabel: name || 'Architect',
      kindLabel: 'architect',
      title: 'Created by architect' + (name ? ' ' + name : '') + (id ? ' (' + id + ')' : ''),
    };
  }
  if (kind === 'engineer') {
    return {
      raw,
      kind,
      id,
      name,
      icon: '\u2692',
      shortLabel: name || 'Engineer',
      kindLabel: 'engineer',
      title: 'Created by engineer' + (name ? ' ' + name : '') + (id ? ' (' + id + ')' : ''),
    };
  }
  if (kind === 'system') {
    return {
      raw: 'system',
      kind: 'system',
      id: '',
      name: '',
      icon: '\u2699',
      shortLabel: 'System',
      kindLabel: 'system',
      title: 'Created by system',
    };
  }
  return {
    raw: 'user',
    kind: 'user',
    id: '',
    name: '',
    icon: '\u25CF',
    shortLabel: 'User',
    kindLabel: 'user',
    title: 'Created by user',
  };
}

function _taskCreatedByBadgeHtml(task) {
  const meta = _taskCreatedByMeta(task);
  const cls = 'board-card-label board-card-created-by board-card-created-by-' + meta.kind;
  return '<span class="' + esc(cls) + '"'
    + ' data-created-by="' + esc(meta.raw) + '"'
    + ' title="' + esc(meta.title) + '">'
    + '<span class="board-card-created-by-icon">' + esc(meta.icon) + '</span>'
    + '<span class="board-card-created-by-label">' + esc(meta.shortLabel) + '</span>'
    + '</span>';
}

function _taskCreatedByDetailHtml(task) {
  const meta = _taskCreatedByMeta(task);
  let body = 'Created by ' + meta.kindLabel;
  if (meta.name) body += ' ' + meta.name;
  if (meta.id) body += ' (' + meta.id + ')';
  return '<span class="task-created-by-chip task-created-by-' + esc(meta.kind) + '"'
    + ' title="' + esc(meta.title) + '">'
    + '<span class="task-created-by-icon">' + esc(meta.icon) + '</span>'
    + '<span class="task-created-by-copy">' + esc(body) + '</span>'
    + '<code>' + esc(meta.raw) + '</code>'
    + '</span>';
}

function toggleMenu(chevron) {
  const menu = chevron.nextElementSibling;
  const wasOpen = menu.classList.contains('open');
  closeMenus();
  if (!wasOpen) {
    const rect = chevron.parentElement.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 2) + 'px';
    menu.style.minWidth = rect.width + 'px';
    menu.classList.add('open');
  }
}
function closeMenus() {
  document.querySelectorAll('.split-menu.open').forEach(m => m.classList.remove('open'));
}

/* Group collapse/expand */
const collapsedGroups = new Set();
const _collapsedInitialized = new Set();

function toggleGroup(name) {
  if (collapsedGroups.has(name)) {
    collapsedGroups.delete(name);
  } else {
    collapsedGroups.add(name);
  }
  const el = document.querySelector(`.group[data-group-name="${CSS.escape(name)}"]`);
  if (el) el.classList.toggle('collapsed');
}

/* FLIP animation — capture old positions, render, animate to new positions */
let _flipUntil = 0;

function _surfacePanelApp(surface) {
  if (surface === 'board') return 'board';
  if (surface === 'actions') return 'actions';
  if (surface === 'context') return 'context';
  if (surface === 'events') return 'events';
  if (surface === 'engineer') return 'engineer';
  if (surface === 'templates') return 'templates';
  if (surface === 'supervisor') return 'supervisor';
  return '';
}

function _activePanelSurface() {
  return _surfacePanelApp(typeof _activePanelApp !== 'undefined' ? _activePanelApp : '');
}

function _currentPanelSurfaces() {
  if (typeof _visiblePanelSurfaces === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()) {
    return _visiblePanelSurfaces().filter(function(surface, idx, arr) {
      return _surfacePanelApp(surface) && arr.indexOf(surface) === idx;
    });
  }
  var surface = _activePanelSurface();
  return surface ? [surface] : [];
}

function _renderSurface(surface) {
  if (surface === 'board' && typeof renderBoard === 'function') renderBoard();
  if (surface === 'actions' && typeof renderTemplatesPanel === 'function') renderTemplatesPanel();
  if (surface === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
  if (surface === 'events' && typeof renderEvents === 'function') renderEvents();
  if (surface === 'engineer' && typeof renderAgentPanel === 'function') {
    // Prefer the surgical in-place tab refresh — clobbers only
    // `.agent-panel-content` (with focus/selection capture+restore around
    // it) instead of the full `#panel-agent` shell. Falls back to the
    // full rebuild if the in-place renderer can't satisfy the request
    // (e.g. shell mismatch / first paint / no focused agent).
    if (typeof _agentPanelRefreshCurrentTab === 'function'
        && _agentPanelRefreshCurrentTab()) {
      // Surgical path handled it.
    } else {
      renderAgentPanel();
    }
  }
  if (surface === 'templates' && typeof renderAgentTemplatesPanel === 'function') renderAgentTemplatesPanel();
  if (surface === 'supervisor' && typeof renderSupervisorPanel === 'function') renderSupervisorPanel({ force: true });
}

function renderActivePanel() {
  const surfaces = _currentPanelSurfaces();
  for (let i = 0; i < surfaces.length; i++) _renderSurface(surfaces[i]);
  _updateEngineerTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
}

function renderInvalidatedSurfaces(flags) {
  if (!flags) return;
  // TORQUE:236 v10: when the main flag fires, skip render()'s trailing
  // agent-panel refresh — the surfaces loop below already dispatches
  // `_renderSurface('engineer')` if the engineer flag is independently
  // set. This eliminates the redundant in-place panel refresh that
  // hundreds of agent_upsert pulses per second produced (cheap post-v9
  // but still wasteful + masks any future capture/restore regression).
  if (flags.main) {
    render({ skipPanelRefresh: true, skipFocusRefresh: !flags.focus });
  } else if (flags.focus && typeof renderAgentFocusPanel === 'function') {
    renderAgentFocusPanel();
  }
  const surfaces = _currentPanelSurfaces();
  for (let i = 0; i < surfaces.length; i++) {
    const surface = surfaces[i];
    if (surface && flags[surface]) _renderSurface(surface);
  }
  _updateEngineerTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
}

function _updateEngineerTaskbarBadge() {
  const btn = document.querySelector('.taskbar-app[data-app="engineer"]');
  if (!btn) return;
  let hasAsk = false;
  for (const name in (state.engineer_settings || {})) {
    if (state.engineer_settings[name].pending_question) {
      hasAsk = true;
      break;
    }
  }
  btn.classList.toggle('has-badge', hasAsk);
}

function _findSurfaceNode(root, selector) {
  if (!root || !selector) return null;
  if (selector === ':root') return root;
  if (selector.charAt(0) === '#' && document.getElementById) {
    return document.getElementById(selector.slice(1));
  }
  return root.querySelector ? root.querySelector(selector) : null;
}

function _surfaceContains(root, el) {
  if (!root || !el) return false;
  if (typeof root.contains === 'function' && root.contains(el)) return true;
  if (!el.id) return false;
  return _findSurfaceNode(root, '#' + el.id) === el;
}

function _captureSurfaceState(root, opts) {
  if (!root) return null;
  opts = opts || {};
  const snapshot = { focus: null, scrolls: [] };
  const active = document.activeElement;
  if (active && _surfaceContains(root, active)) {
    let key = active.id ? ('#' + active.id) : '';
    if (!key && typeof opts.captureFocusKey === 'function') {
      key = opts.captureFocusKey(active, root) || '';
    }
    if (key) {
      snapshot.focus = {
        key,
        value: 'value' in active ? active.value : null,
        checked: 'checked' in active ? !!active.checked : null,
        selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
        selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
        scrollTop: typeof active.scrollTop === 'number' ? active.scrollTop : null,
        scrollLeft: typeof active.scrollLeft === 'number' ? active.scrollLeft : null,
      };
    }
  }
  const selectors = opts.scrollSelectors || [];
  for (let i = 0; i < selectors.length; i++) {
    const selector = selectors[i];
    const el = _findSurfaceNode(root, selector);
    if (!el) continue;
    snapshot.scrolls.push({
      selector,
      top: typeof el.scrollTop === 'number' ? el.scrollTop : null,
      left: typeof el.scrollLeft === 'number' ? el.scrollLeft : null,
    });
  }
  if (typeof opts.capture === 'function') opts.capture(snapshot, root);
  return snapshot;
}

function _restoreSurfaceState(root, snapshot, opts) {
  if (!root || !snapshot) return;
  opts = opts || {};
  for (let i = 0; i < snapshot.scrolls.length; i++) {
    const saved = snapshot.scrolls[i];
    const el = _findSurfaceNode(root, saved.selector);
    if (!el) continue;
    if (typeof saved.top === 'number') el.scrollTop = saved.top;
    if (typeof saved.left === 'number') el.scrollLeft = saved.left;
  }
  if (typeof opts.restore === 'function') opts.restore(root, snapshot);
  if (!snapshot.focus) return;
  let el = _findSurfaceNode(root, snapshot.focus.key);
  if (!el && typeof opts.resolveFocus === 'function') {
    el = opts.resolveFocus(root, snapshot.focus);
  }
  if (!el) return;
  // Skip the value/checked re-assignment when the element already holds the
  // captured value. Setting `el.value = ...` on a focused textarea resets the
  // browser's caret / selection range and briefly kills the visible cursor;
  // under firehose render rates (TORQUE:264) this stops the cursor from
  // rendering at all even though keystrokes still route to the element.
  // Idempotent renders that preserve DOM identity hit this path repeatedly.
  // We still re-assert focus + selection below — those are no-ops on
  // already-focused elements but cheap enough to keep unconditional, and the
  // tests rely on `.focus()` being called so the FakeElement `focused` flag
  // gets set on first paint.
  const valueDrifted = snapshot.focus.value != null
    && 'value' in el
    && el.value !== snapshot.focus.value;
  const checkedDrifted = snapshot.focus.checked != null
    && 'checked' in el
    && el.checked !== !!snapshot.focus.checked;
  if (valueDrifted) el.value = snapshot.focus.value;
  if (checkedDrifted) el.checked = snapshot.focus.checked;
  // Use preventScroll so re-render-driven focus restoration doesn't override
  // explicit scroll restoration via snapshot.scrolls / panel-level scroll
  // bookkeeping. Otherwise an inline-render that re-focuses an offscreen
  // input (e.g. an empty board "Add task" textarea) drags the page back to
  // the input via the browser's default scroll-into-view-on-focus behavior.
  if (typeof el.focus === 'function') {
    try { el.focus({ preventScroll: true }); }
    catch (_e) { el.focus(); }
  }
  if (typeof snapshot.focus.selectionStart === 'number' && 'selectionStart' in el) {
    el.selectionStart = snapshot.focus.selectionStart;
  }
  if (typeof snapshot.focus.selectionEnd === 'number' && 'selectionEnd' in el) {
    el.selectionEnd = snapshot.focus.selectionEnd;
  }
  // Restore the focused element's own scrollTop last — assigning value or
  // selection on a textarea resets internal scroll, which would otherwise
  // jump a multi-line compose box back to the top on every WS rerender.
  if (typeof snapshot.focus.scrollTop === 'number' && typeof el.scrollTop === 'number') {
    el.scrollTop = snapshot.focus.scrollTop;
  }
  if (typeof snapshot.focus.scrollLeft === 'number' && typeof el.scrollLeft === 'number') {
    el.scrollLeft = snapshot.focus.scrollLeft;
  }
}

function _captureRects(main) {
  const rects = {};
  main.querySelectorAll('[data-drag-id]').forEach(el => {
    rects[el.dataset.dragId] = el.getBoundingClientRect();
  });
  main.querySelectorAll('[data-group-name]').forEach(el => {
    rects['g:' + el.dataset.groupName] = el.getBoundingClientRect();
  });
  return rects;
}

function _applyFlip(main, oldRects) {
  const els = [
    ...main.querySelectorAll('[data-drag-id]'),
    ...main.querySelectorAll('[data-group-name]'),
  ];
  for (const el of els) {
    const key = el.dataset.dragId || ('g:' + el.dataset.groupName);
    const oldRect = oldRects[key];
    if (!oldRect) continue;
    const newRect = el.getBoundingClientRect();
    const dx = oldRect.left - newRect.left;
    const dy = oldRect.top - newRect.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    el.animate([
      { transform: `translate(${dx}px, ${dy}px)` },
      { transform: 'translate(0, 0)' }
    ], { duration: 200, easing: 'ease-out' });
  }
}

function _agentGridSectionKey(section) {
  if (!section) return '';
  if (section.type === 'user') return 'user';
  return 'architect:' + String((section.architect && section.architect.id) || '');
}

/* Resolve the currently selected principal id, falling back to the user
   principal (empty string) when the stored architect id no longer exists
   in the visible agent set. */
function _resolveSelectedPrincipalId(sections) {
  const stored = String((state && state.selected_principal_id) || '');
  if (!stored) return '';
  for (let i = 0; i < (sections || []).length; i++) {
    const section = sections[i];
    if (section && section.architect && String(section.architect.id) === stored) {
      return stored;
    }
  }
  return '';
}

function _principalSectionMatches(section, principalId) {
  if (!section) return false;
  const id = String(principalId || '');
  if (!id) return section.type === 'user';
  return !!(section.architect && String(section.architect.id) === id);
}

function _principalCardNavId(groupName, principalId) {
  const id = String(principalId || '');
  const group = String(groupName || '');
  return 'principal:' + group + ':' + (id || 'user');
}

function _principalCardFocusedClass(navId) {
  return (navId && typeof focusedItemId !== 'undefined' && focusedItemId === navId)
    ? ' focused'
    : '';
}

/* Compute engineer/status counts for a section for principals-row badges. */
function _principalStatusCounts(section) {
  const counts = { engineers: 0, running: 0, idle: 0, error: 0, attention: 0 };
  if (!section) return counts;
  const rows = section.rows || [];
  for (const row of rows) {
    if (!row || !row.engineer) continue;
    counts.engineers += 1;
    const agents = [row.engineer].concat(row.workers || []);
    for (const a of agents) {
      if (!a) continue;
      if (a.needs_attention) counts.attention += 1;
      const cls = typeof agentStatusClass === 'function' ? agentStatusClass(a) : '';
      if (cls === 'attention') counts.error += 1;
      else if (cls === 'working') counts.running += 1;
      else counts.idle += 1;
    }
  }
  return counts;
}

function _renderPrincipalBadgeHtml(counts) {
  if (!counts) return '';
  const parts = [];
  parts.push(String(counts.engineers || 0) + ' eng');
  if (counts.running) parts.push(String(counts.running) + ' run');
  if (counts.error) parts.push('!' + String(counts.error));
  if (!counts.running && !counts.error && counts.engineers) {
    parts.push('idle');
  }
  return '<span class="principal-card-badge"'
    + ' data-principal-badge-counts="' + esc(JSON.stringify(counts)) + '"'
    + ' title="' + esc(counts.engineers + ' engineer(s), '
      + counts.running + ' running, '
      + counts.idle + ' idle, '
      + counts.error + ' error') + '">'
    + esc(parts.join(' · '))
    + '</span>';
}

function _agentCardTimestampSeconds(value) {
  if (value === null || value === undefined || value === '') return 0;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0;
    return value > 100000000000 ? value / 1000 : value;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() !== '') {
    if (numeric <= 0) return 0;
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }
  const parsed = Date.parse(String(value));
  if (Number.isFinite(parsed)) return parsed / 1000;
  return 0;
}

function _agentCardCompactRelativeTime(value) {
  const ts = _agentCardTimestampSeconds(value);
  if (!ts) return '—';
  const diff = Math.max(0, Math.floor((Date.now() / 1000) - ts));
  if (diff < 60) return String(diff || 0) + 's';
  if (diff < 3600) return Math.floor(diff / 60) + 'm';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h';
  return Math.floor(diff / 86400) + 'd';
}

function _truncateCardText(text, maxChars) {
  const raw = String(text || '').trim();
  const max = Math.max(1, Number(maxChars || 0) || 14);
  if (raw.length <= max) return raw;
  if (max <= 1) return '…';
  return raw.slice(0, max - 1) + '…';
}

function _agentCardTooltipHtml(text, maxChars, className, attrs) {
  const raw = String(text || '').trim();
  const visible = _truncateCardText(raw, maxChars);
  const contentClasses = ['agent-card-trunc'];
  if (className) contentClasses.push(className);
  const extraAttrs = attrs ? ' ' + attrs : '';
  return '<span class="agent-card-tooltip"'
    + ' data-tooltip="' + esc(raw) + '"'
    + ' aria-label="' + esc(raw) + '"'
    + extraAttrs + '>'
    + '<span class="' + esc(contentClasses.join(' ')) + '">'
    + esc(visible)
    + '</span>'
    + '</span>';
}

function _agentCardLatestMcpMessage(agent) {
  const messages = agent && Array.isArray(agent.mcp_messages)
    ? agent.mcp_messages : [];
  let latest = null;
  let latestTs = 0;
  for (let i = 0; i < messages.length; i++) {
    const entry = messages[i] || {};
    const ts = _agentCardTimestampSeconds(entry.timestamp);
    if (!latest || ts > latestTs || (ts === latestTs && i === 0)) {
      latest = entry;
      latestTs = ts;
    }
  }
  return latest ? { entry: latest, timestamp: latestTs } : null;
}

function _agentProviderMeta(provider) {
  const key = String(provider || '').trim().toLowerCase();
  if (key === 'claude-code') {
    return { key, label: 'Claude', cls: 'agent-card-provider--claude-code' };
  }
  if (key === 'codex') {
    return { key, label: 'Codex', cls: 'agent-card-provider--codex' };
  }
  if (!key || key === 'generic') return null;
  return { key, label: key.replace(/[-_]+/g, ' '), cls: 'agent-card-provider--unknown' };
}

function _renderAgentProviderBadge(provider, extraClass) {
  const meta = _agentProviderMeta(provider);
  if (!meta) return '';
  const classes = ['agent-card-provider', meta.cls];
  if (extraClass) classes.push(extraClass);
  return '<span class="' + esc(classes.join(' ')) + '"'
    + ' data-provider="' + esc(meta.key) + '">'
    + esc(meta.label)
    + '</span>';
}

function _agentKindBadgeLabel(kind, dismissed) {
  if (dismissed) return 'Dismissed';
  if (kind === 'architect') return 'Architect';
  if (kind === 'engineer') return 'Engineer';
  if (kind === 'worker') return 'Worker';
  return 'Agent';
}

function _agentKindBadgeClass(kind, dismissed) {
  if (dismissed) return 'cell-dismissed-badge';
  if (kind === 'architect') return 'cell-architect-badge';
  if (kind === 'engineer') return 'cell-engineer-badge';
  if (kind === 'worker') return 'cell-worker-badge';
  return 'cell-agent-badge';
}

function _agentDisplayName(agent) {
  if (!agent) return '';
  return agent.name || agent.slug || agent.id || '';
}

function _agentCardLastActionValue(agent) {
  if (!agent) return 0;
  const latestMcp = _agentCardLatestMcpMessage(agent);
  return Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    latestMcp ? latestMcp.timestamp : 0,
    _agentCardTimestampSeconds(agent.last_progress_at),
    _agentCardTimestampSeconds(agent.last_event_at),
    _agentCardTimestampSeconds(agent.last_activity_at),
    _agentCardTimestampSeconds(agent.last_heartbeat_at),
    _agentCardTimestampSeconds(agent.updated_at),
    _agentCardTimestampSeconds(agent.created_at),
  );
}

function _agentCardActionTextFromMcp(entry) {
  if (!entry) return '';
  const action = String(entry.action || '').trim();
  const message = String(entry.message || '').trim();
  if (action === 'progress' && message) return message;
  if (action === 'derive') {
    if (message) return message;
    return 'derived follow-up';
  }
  if (action === 'verify') return message || 'verified';
  if (action === 'ask') return message || 'asked for input';
  if (action === 'blocked') return message || 'blocked';
  if (action === 'error') return message || 'error';
  if (action === 'done') return message && message !== 'Done' ? message : 'done';
  if (action === 'ready') return message && message !== 'Ready' ? message : 'ready';
  return message || action;
}

function _agentCardFallbackActionText(agent) {
  if (!agent) return '—';
  const activity = String(agent.activity || '').trim();
  if (activity === 'tool_call') return 'working';
  if (activity === 'waiting') return 'waiting';
  if (activity === 'thinking' || activity === 'writing') return 'working';
  const status = String(agent.status || '').trim();
  if (status === 'running') return 'working';
  if (agent.needs_attention) return 'needs attention';
  return 'idle';
}

function _agentCardActionCandidate(text, timestamp, priority) {
  const value = String(text || '').trim();
  if (!value) return null;
  return {
    text: value,
    timestamp: _agentCardTimestampSeconds(timestamp),
    priority: Number(priority || 0) || 0,
  };
}

function _agentCardEventTimestamp(agent) {
  if (!agent) return 0;
  return Math.max(
    _agentCardTimestampSeconds(agent.last_event_at),
    _agentCardTimestampSeconds(agent.last_activity_at),
    _agentCardTimestampSeconds(agent.last_heartbeat_at),
  );
}

function _agentCardActivityDetailTimestamp(agent) {
  if (!agent) return 0;
  const activityDetail = String(agent.activity_detail || '').trim();
  if (!activityDetail) return 0;
  const eventText = String(agent.last_event_text || '').trim();
  let timestamp = Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    _agentCardTimestampSeconds(agent.last_progress_at),
  );
  /* activity_detail is advanced by progress reports, while last_event_text
     can be advanced independently by heartbeat-style events such as
     queue_empty. Only borrow the event clock when the event text is the same
     detail (or when there is no competing event text); otherwise an old
     activity_detail would hide a newer queue_empty display. */
  if (!eventText || eventText === activityDetail) {
    timestamp = Math.max(timestamp, _agentCardEventTimestamp(agent));
  }
  return timestamp;
}

function _agentCardFallbackActionTimestamp(agent, latestMcp) {
  if (!agent) return 0;
  return Math.max(
    _agentCardTimestampSeconds(agent.last_action_at),
    latestMcp ? latestMcp.timestamp : 0,
    _agentCardTimestampSeconds(agent.last_progress_at),
    _agentCardEventTimestamp(agent),
  );
}

function _agentCardNewestActionCandidate(candidates) {
  const list = (Array.isArray(candidates) ? candidates : []).filter(Boolean);
  if (!list.length) return null;
  list.sort((a, b) => {
    const aTs = _agentCardTimestampSeconds(a.timestamp);
    const bTs = _agentCardTimestampSeconds(b.timestamp);
    if (aTs !== bTs) return bTs - aTs;
    return (Number(b.priority || 0) || 0) - (Number(a.priority || 0) || 0);
  });
  return list[0];
}

function _agentCardLastActionInfo(agent) {
  const latestMcp = _agentCardLatestMcpMessage(agent);
  const candidates = [];
  if (agent) {
    candidates.push(_agentCardActionCandidate(
      agent.activity_detail,
      _agentCardActivityDetailTimestamp(agent),
      30,
    ));
    if (latestMcp) {
      candidates.push(_agentCardActionCandidate(
        _agentCardActionTextFromMcp(latestMcp.entry),
        latestMcp.timestamp,
        20,
      ));
    }
    candidates.push(_agentCardActionCandidate(
      agent.last_event_text,
      _agentCardEventTimestamp(agent),
      10,
    ));
    candidates.push(_agentCardActionCandidate(
      _agentCardFallbackActionText(agent),
      _agentCardFallbackActionTimestamp(agent, latestMcp),
      0,
    ));
  }
  return _agentCardNewestActionCandidate(candidates) || {
    text: _agentCardFallbackActionText(agent),
    timestamp: _agentCardTimestampSeconds(_agentCardLastActionValue(agent)),
  };
}

function _agentCardCurrentOrLastActionLabel(agent) {
  const info = _agentCardLastActionInfo(agent);
  // The status dot already conveys idle state — don't surface the literal
  // "idle" text in the action line. Returning empty leaves the line slot in
  // place (preserves uniform card height) but renders no content.
  if (info.text === 'idle') return '';
  if (!info.timestamp) return info.text;
  const age = Math.max(0, Math.floor((Date.now() / 1000) - info.timestamp));
  if (age < 60) return info.text;
  return info.text + ' (' + _agentCardCompactRelativeTime(info.timestamp) + ')';
}

function _agentTaskForCard(agent) {
  if (!agent) return null;
  const currentTaskId = String(agent.current_task_id || '').trim();
  if (currentTaskId && state && state.board_tasks && state.board_tasks[currentTaskId]) {
    return state.board_tasks[currentTaskId];
  }
  return _getAgentTask(agent.id);
}

function _taskCycleState(task, agent) {
  if (agent && (agent.needs_attention || agent.error_message)) return 'blocked';
  if (!task) return 'idle';
  const lane = String(task.lane || '').trim().toLowerCase();
  const status = String(task.status || '').trim().toLowerCase();
  const health = String(task.health_state || '').trim().toLowerCase();
  const action = String(task.action_name || task.suggested_action || '').trim().toLowerCase();
  const actionTail = action.split('/').filter(Boolean).pop() || action;
  if (status.indexOf('block') >= 0 || health.indexOf('block') >= 0) return 'blocked';
  if (lane === 'done') return 'done';
  if (action.indexOf('review') >= 0) return 'review';
  if (action.indexOf('fix') >= 0) return 'fix';
  if (action.indexOf('implement') >= 0 || actionTail === 'impl') return 'implementation';
  if (action) {
    return actionTail.replace(/[-_]+/g, ' ');
  }
  if (lane === 'in progress') return 'in progress';
  if (lane) return lane.replace(/[-_]+/g, ' ');
  return 'idle';
}

function _workerDiffLabel(agent) {
  const diff = (agent && agent.worktree_diff) || {};
  const committedDiff = diff.committed || diff.committed_diff || diff;
  const dirtyDiff = (agent && (
    agent.worktree_dirty_diff
    || agent.worktree_uncommitted_diff
    || agent.uncommitted_diff
    || agent.dirty_diff
  )) || diff.dirty || diff.uncommitted || diff.working_tree || {};
  const legs = [
    _workerDiffLegLabel(committedDiff, 'committed'),
    _workerDiffLegLabel(dirtyDiff, 'dirty'),
  ].filter(Boolean);
  return legs.join(' ');
}

function _workerDiffLegLabel(diff, label) {
  const insertions = Number((diff && diff.insertions) || 0) || 0;
  const deletions = Number((diff && diff.deletions) || 0) || 0;
  if (insertions + deletions === 0) return '';
  return '(+' + insertions + '/-' + deletions + ' ' + label + ')';
}

function _worktreeBranchShortName(branch) {
  const raw = String(branch || '').trim();
  if (!raw) return '';
  let text = raw;
  const parts = raw.split('/').filter(Boolean);
  if (parts[0] === 'torque') {
    if (parts.length >= 3) text = parts.slice(2).join('/');
    else if (parts.length >= 2) text = parts.slice(1).join('/');
  }
  text = text.replace(/-[0-9a-f]{6,12}$/i, '');
  return text || raw;
}

function _workerBranchLabel(agent) {
  const branch = String((agent && (agent.worktree_branch || agent.current_branch)) || '').trim();
  if (!branch) return 'worktree: —';
  return 'worktree: ' + _worktreeBranchShortName(branch);
}

function _agentStatusMixClass(agent) {
  const cls = typeof agentStatusClass === 'function' ? agentStatusClass(agent) : '';
  if (cls === 'attention' || cls === 'disconnected') return 'error';
  if (cls === 'working') return 'running';
  return 'idle';
}

function _agentStatusMixDots(agents) {
  const list = Array.isArray(agents) ? agents : [];
  if (!list.length) {
    return '<span class="agent-card-state-dot agent-card-state-dot--empty"></span>';
  }
  const shown = list.slice(0, 3);
  let html = '';
  for (const agent of shown) {
    const mix = _agentStatusMixClass(agent);
    html += '<span class="agent-card-state-dot agent-card-state-dot--' + esc(mix) + '"></span>';
  }
  if (list.length > shown.length) {
    html += '<span class="agent-card-state-more">+' + esc(list.length - shown.length) + '</span>';
  }
  return html;
}

function _workersForEngineer(engineerId) {
  const id = String(engineerId || '').trim();
  if (!id || !state || !state.agents) return [];
  const workers = [];
  for (const agentId in state.agents) {
    const agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent') continue;
    if (_isTombstonedAgent(agent)) continue;
    if (!_isWorkerLikeAgent(agent)) continue;
    const owner = String(agent.owner_engineer_id || agent.created_by_engineer_id || '').trim();
    if (owner === id) workers.push(agent);
  }
  return workers.sort(function(a, b) {
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function _engineerQueueDepth(engineerId) {
  const id = String(engineerId || '').trim();
  if (!id || !state || !state.board_tasks) return 0;
  let count = 0;
  for (const taskId in state.board_tasks) {
    const task = state.board_tasks[taskId];
    if (!task) continue;
    if (taskIsEngineerMessageFollowup(task)) continue;
    if (String(task.assigned_engineer_id || '').trim() !== id) continue;
    const lane = String(task.lane || '').trim();
    if (lane === 'Backlog' || lane === 'To Do') count += 1;
  }
  return count;
}

function _architectEngineersForCard(architectId, section) {
  if (section && Array.isArray(section.rows)) {
    return section.rows.map(function(row) { return row && row.engineer; }).filter(Boolean);
  }
  const id = String(architectId || '').trim();
  if (!id || !state || !state.agents) return [];
  const engineers = [];
  for (const agentId in state.agents) {
    const agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent' || (agent.kind || '') !== 'engineer') continue;
    if (_isTombstonedAgent(agent)) continue;
    if (String(agent.hired_by_architect_id || '').trim() === id) engineers.push(agent);
  }
  return engineers;
}

function _architectPendingAskTasks(architect) {
  if (!architect || !state || !state.board_tasks) return [];
  const architectId = String(architect.id || '').trim();
  if (!architectId) return [];
  const group = String(architect.group || '').trim();
  const asks = [];
  for (const taskId in state.board_tasks) {
    const task = state.board_tasks[taskId];
    if (!task) continue;
    const labels = Array.isArray(task.labels) ? task.labels : [];
    if (labels.indexOf('torque:human') < 0) continue;
    if (String(task.lane || '') === 'Done') continue;
    const replyId = String(task.reply_agent_id || '').trim();
    const creatorId = String(task.created_by_architect_id || '').trim();
    const taskGroup = String(task.group || '').trim();
    const architectAsk = labels.indexOf('architect-ask') >= 0;
    const replyMatches = replyId === architectId;
    const creatorMatches = creatorId === architectId;
    if (replyMatches || creatorMatches) {
      asks.push(task);
      continue;
    }
    if (replyId || creatorId || !architectAsk) continue;
    if (group && taskGroup && taskGroup !== group) continue;
    asks.push(task);
  }
  asks.sort(function(a, b) {
    const av = _agentCardTimestampSeconds((a && (a.created_at || a.updated_at)) || 0);
    const bv = _agentCardTimestampSeconds((b && (b.created_at || b.updated_at)) || 0);
    if (av !== bv) return av - bv;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return asks;
}

function _architectDecisionListForCard(architectId) {
  const id = String(architectId || '').trim();
  if (!id || !state) return [];
  const stores = [];
  if (state.decisions) stores.push(state.decisions);
  if (state.architect_decisions && state.architect_decisions !== state.decisions) {
    stores.push(state.architect_decisions);
  }
  const seen = {};
  const results = [];
  for (let i = 0; i < stores.length; i++) {
    const store = stores[i] || {};
    const values = Array.isArray(store)
      ? store
      : Object.keys(store).map(function(key) { return store[key]; });
    for (let j = 0; j < values.length; j++) {
      const decision = values[j];
      if (!decision) continue;
      const decisionId = String(decision.id || '').trim();
      if (decisionId && seen[decisionId]) continue;
      if (String(decision.architect_id || '').trim() !== id) continue;
      if (decisionId) seen[decisionId] = true;
      results.push(decision);
    }
  }
  results.sort(function(a, b) {
    const av = _agentCardTimestampSeconds((a && (a.created_at || a.updated_at)) || 0);
    const bv = _agentCardTimestampSeconds((b && (b.created_at || b.updated_at)) || 0);
    if (av !== bv) return bv - av;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return results;
}

function _architectJournalEntriesForCard(architectId) {
  const id = String(architectId || '').trim();
  if (!id || !state || !state.architect_journals) return [];
  const entries = state.architect_journals[id];
  return Array.isArray(entries) ? entries : [];
}

function _architectJournalDecisionEntriesForCard(architectId) {
  const journals = _architectJournalEntriesForCard(architectId);
  const decisions = [];
  for (const entry of journals) {
    if (String((entry && entry.type) || '').toLowerCase() !== 'decision') continue;
    decisions.push(entry);
  }
  return decisions;
}

function _architectLatestJournalDecisionTs(architectId) {
  const decisions = _architectJournalDecisionEntriesForCard(architectId);
  let latest = 0;
  for (const entry of decisions) {
    latest = Math.max(
      latest,
      _agentCardTimestampSeconds((entry && (entry.timestamp || entry.created_at || entry.updated_at)) || 0)
    );
  }
  return latest;
}

function _architectStatsForCard(architect, section) {
  const engineers = _architectEngineersForCard(architect && architect.id, section);
  const asks = _architectPendingAskTasks(architect);
  const decisions = _architectDecisionListForCard(architect && architect.id);
  const journalDecisions = _architectJournalDecisionEntriesForCard(architect && architect.id);
  let decisionCount = 0;
  let latestDecisionTs = 0;
  for (const decision of decisions) {
    if (!decision.archived) decisionCount += 1;
    latestDecisionTs = Math.max(
      latestDecisionTs,
      _agentCardTimestampSeconds((decision && (decision.updated_at || decision.created_at)) || 0)
    );
  }
  latestDecisionTs = Math.max(
    latestDecisionTs,
    _architectLatestJournalDecisionTs(architect && architect.id)
  );
  return {
    engineerCount: engineers.length,
    asks,
    askCount: asks.length,
    firstAskId: asks.length ? String(asks[0].id || '') : '',
    decisionCount: decisionCount + journalDecisions.length,
    latestDecisionTs,
  };
}

function _architectLastDecisionLabel(stats) {
  stats = stats || {};
  if (stats.latestDecisionTs) return 'last decision ' + _agentCardCompactRelativeTime(stats.latestDecisionTs);
  return 'last decision —';
}

function _userPrincipalStatsForCard(groupName, section) {
  const rows = section && Array.isArray(section.rows) ? section.rows : [];
  const engineerIds = {};
  let engineerCount = 0;
  for (const row of rows) {
    const engineer = row && row.engineer;
    const id = String((engineer && engineer.id) || '').trim();
    if (!id || engineerIds[id]) continue;
    engineerIds[id] = true;
    engineerCount += 1;
  }
  let backlogCount = 0;
  if (state && state.board_tasks) {
    const group = String(groupName || '').trim();
    for (const taskId in state.board_tasks) {
      const task = state.board_tasks[taskId];
      if (!task) continue;
      if (group && String(task.group || '').trim() !== group) continue;
      if (taskIsEngineerMessageFollowup(task)) continue;
      if (String(task.lane || '').trim().toLowerCase() !== 'backlog') continue;
      const assignedEngineerId = String(task.assigned_engineer_id || '').trim();
      if (assignedEngineerId && engineerCount && !engineerIds[assignedEngineerId]) continue;
      backlogCount += 1;
    }
  }
  return { engineerCount, backlogCount };
}

function _renderPrincipalUserCard(groupName, section, selectedPrincipalId) {
  const isSelected = !selectedPrincipalId;
  const navId = _principalCardNavId(groupName, '');
  const classes = ['principal-card', 'principal-card--user'];
  if (isSelected) classes.push('selected');
  else classes.push('dim');
  classes.push(_principalCardFocusedClass(navId).trim());
  const groupArg = _jsStringAttr(groupName);
  const stats = _userPrincipalStatsForCard(groupName, section);
  return '<button type="button" class="' + esc(classes.filter(Boolean).join(' ')) + '"'
    + ' data-principal-card="user"'
    + ' data-principal-id=""'
    + ' data-principal-group="' + esc(groupName) + '"'
    + ' data-nav-id="' + esc(navId) + '"'
    + ' data-focus-key="' + esc(navId) + '"'
    + ' aria-pressed="' + (isSelected ? 'true' : 'false') + '"'
    + ' onclick="event.stopPropagation();selectPrincipal(\'\',' + groupArg + ')"'
    + ' title="User-owned engineers">'
    + '<span class="principal-card-status idle" aria-hidden="true"></span>'
    + '<div class="principal-card-body">'
    + '<span class="principal-card-name">User</span>'
    + '<span class="principal-card-line principal-card-line--engineers">'
      + esc(stats.engineerCount + ' engineers')
    + '</span>'
    + '<span class="principal-card-line principal-card-line--backlog">'
      + esc(stats.backlogCount + ' backlog')
    + '</span>'
    + '</div>'
    + '<span class="principal-card-kind principal-card-kind--owner">Owner</span>'
    + '</button>';
}

function _renderPrincipalArchitectCard(groupName, section, selectedPrincipalId) {
  const architect = section && section.architect;
  if (!architect) return '';
  const id = String(architect.id || '');
  const isSelected = String(selectedPrincipalId || '') === id;
  const navId = _principalCardNavId(groupName, id);
  const classes = ['principal-card', 'principal-card--architect'];
  if (isSelected) classes.push('selected');
  else classes.push('dim');
  const dismissedAt = _agentDismissedAt(architect);
  if (dismissedAt) classes.push('dismissed');
  classes.push(_principalCardFocusedClass(navId).trim());
  const displayName = architect.name || architect.slug || id;
  const groupArg = _jsStringAttr(groupName);
  const idArg = _jsStringAttr(id);
  const statusCls = dismissedAt
    ? 'dismissed'
    : (typeof agentStatusClass === 'function' ? agentStatusClass(architect) : '');
  const stats = _architectStatsForCard(architect, section);
  const actionLabel = _agentCardCurrentOrLastActionLabel(architect);
  const digestSettings = (state && state.agent_digest_settings)
    ? state.agent_digest_settings[id] : null;
  const digestPaused = !!(digestSettings && digestSettings.paused);
  const pauseTitle = digestPaused ? 'Resume event delivery' : 'Pause event delivery';
  const pauseIcon = digestPaused ? '▶' : '⏸';
  const lifecycleButton = dismissedAt
    ? '<button type="button" class="principal-card-pause running"'
      + ' data-focus-key="principal-rehire:' + esc(id) + '"'
      + ' onclick="event.stopPropagation();rehireArchitect(' + idArg + ')"'
      + ' title="Rehire architect">↻</button>'
    : '<button type="button" class="principal-card-pause ' + (digestPaused ? 'paused' : 'running') + '"'
      + ' data-focus-key="principal-pause:' + esc(id) + '"'
      + ' onclick="event.stopPropagation();toggleDigestPauseForAgent(' + idArg + ')"'
      + ' title="' + esc(pauseTitle) + '">' + pauseIcon + '</button>';
  /* Nested <button> inside <button> is invalid HTML; render the card as a
     div with role="tab" so close/pause controls can be real buttons. */
  return '<div class="' + esc(classes.filter(Boolean).join(' ')) + '"'
    + ' role="tab" tabindex="0"'
    + ' data-principal-card="architect"'
    + ' data-principal-id="' + esc(id) + '"'
    + ' data-principal-group="' + esc(groupName) + '"'
    + ' data-nav-id="' + esc(navId) + '"'
    + ' data-focus-key="' + esc(navId) + '"'
    + ' aria-selected="' + (isSelected ? 'true' : 'false') + '"'
    + ' onclick="event.stopPropagation();selectPrincipal(' + idArg + ',' + groupArg + ')"'
    + ' oncontextmenu="onCellContextMenu(event,' + idArg + ')"'
    + ' title="Architect ' + esc(displayName) + (dismissedAt ? ' — dismissed' : '') + '">'
    + '<div class="principal-card-controls">'
    + '<button type="button" class="principal-card-close"'
    + ' data-focus-key="principal-close:' + esc(id) + '"'
    + ' onclick="event.stopPropagation();removeAgent(' + idArg + ')"'
    + ' title="Delete architect">✕</button>'
    + lifecycleButton
    + '</div>'
    + '<span class="principal-card-status ' + esc(statusCls) + '" aria-hidden="true"></span>'
    + '<div class="principal-card-body">'
    + '<span class="principal-card-name">' + esc(displayName) + '</span>'
    + '<span class="principal-card-line principal-card-line--engineers">'
      + esc(stats.engineerCount + ' engineers')
    + '</span>'
    + '<span class="principal-card-line principal-card-line--action" title="' + esc(actionLabel) + '">'
      + esc(actionLabel)
    + '</span>'
    + '</div>'
    + _renderAgentProviderBadge(architect.agent_type, 'principal-card-provider')
    + '<span class="principal-card-kind '
      + (dismissedAt ? 'principal-card-kind--dismissed' : 'principal-card-kind--architect')
      + '">' + (dismissedAt ? 'Dismissed' : 'Architect') + '</span>'
    + '</div>';
}

function _renderPrincipalNewArchitectGhost(groupName, disabled) {
  const groupArg = _jsStringAttr(groupName);
  const navId = _gridNavControlId('agent-new-architect', groupName, '');
  const focusKey = _gridNavControlFocusKey('agent-new-architect', groupName, 'user');
  return '<button type="button" class="ghost-card ghost-card--architect principal-card-new' + _gridNavFocusedClass(navId) + '"'
    + ' data-action="new-architect"'
    + ' data-nav-id="' + esc(navId) + '"'
    + ' data-group="' + esc(groupName) + '"'
    + ' data-focus-key="' + esc(focusKey) + '"'
    + (disabled ? ' disabled aria-disabled="true" title="Agent limit reached"' : ' title="Create a new architect in this group"')
    + (disabled ? '' : ' onclick="event.stopPropagation();openAddArchitectForGroup(' + groupArg + ')"')
    + '>+ New Architect</button>';
}

function _renderPrincipalsRow(groupName, sections, selectedPrincipalId, opts) {
  opts = opts || {};
  const disabled = !!opts.disabled;
  let html = '<div class="principals-row"'
    + ' data-principals-row-group="' + esc(groupName) + '"'
    + ' role="tablist">';
  let userSection = null;
  const architectSections = [];
  for (const section of sections) {
    if (!section) continue;
    if (section.type === 'user') userSection = section;
    else if (section.architect) architectSections.push(section);
  }
  html += _renderPrincipalUserCard(groupName, userSection, selectedPrincipalId);
  for (const section of architectSections) {
    html += _renderPrincipalArchitectCard(groupName, section, selectedPrincipalId);
  }
  html += _renderPrincipalNewArchitectGhost(groupName, disabled);
  html += '</div>';
  return html;
}

function _gridNavControlId(controlType, groupName, sectionKey) {
  let id = 'grid-control:' + String(controlType || '') + ':' + String(groupName || '');
  if (sectionKey) id += ':' + String(sectionKey);
  return id;
}

function _gridNavControlFocusKey(controlType, groupName, sectionKey) {
  if (controlType === 'section-new-engineer') {
    return 'section-new-engineer:' + String(groupName || '') + ':' + String(sectionKey || '');
  }
  if (controlType === 'section-new-worker') {
    return 'section-new-worker:' + String(groupName || '') + ':' + String(sectionKey || '');
  }
  if (controlType === 'agent-new-architect') {
    return 'agent-new-architect:' + String(groupName || '');
  }
  return '';
}

function _gridNavFocusedClass(navId) {
  return (navId && typeof focusedItemId !== 'undefined' && focusedItemId === navId)
    ? ' focused'
    : '';
}

function _ensureGroupCollapsedInitialized(gname, gsLocal) {
  if (!_collapsedInitialized.has(gname)) {
    _collapsedInitialized.add(gname);
    if (gsLocal && gsLocal.collapsed_default) collapsedGroups.add(gname);
  }
}

function _visibleGroupCellsForGrid(gname, embeddedMode) {
  const aids = state.groups[gname] || [];
  const gsFilter = (state.group_settings || {})[gname] || {};
  let wid = null;
  if (!embeddedMode && gsFilter.filter_by_window && state.current_window_id) {
    const hasActive = aids.some(id => {
      const c = state.agents[id];
      if (c && c.session_id) return true;
      const kids = state.children[id] || [];
      return kids.some(kid => {
        const ct = state.agents[kid];
        return ct && ct.session_id;
      });
    });
    if (hasActive) wid = state.current_window_id;
  } else if (!embeddedMode && getFilterByWindow()) {
    wid = state.current_window_id;
  }

  const agents = [];
  const standaloneTerms = [];
  for (const id of aids) {
    const c = state.agents[id];
    if (!c) continue;
    if (_isTombstonedAgent(c)) continue;
    if (wid && c.window_id && c.window_id !== wid) continue;
    if (c.cell_type === 'agent') {
      agents.push(c);
    } else if (c.cell_type === 'terminal' && (!c.parent_id || !state.agents[c.parent_id])) {
      standaloneTerms.push(c);
    }
  }

  return {
    aids,
    agents,
    standaloneTerms,
    wid,
    hiddenByWindow: !!(wid && agents.length === 0 && standaloneTerms.length === 0 && aids.length > 0),
  };
}

function _buildAgentGridNavigationModel(groupContexts) {
  const model = {
    navItems: [],
    navAgents: [],
    navByGroup: {},
    navGroupOrder: [],
    gridRows: [],
    itemMeta: {},
    creationControls: [],
    focusableItems: [],
  };

  let sortOrder = 0;
  const registeredRowIndexes = new Set();

  const addMeta = function(item, row, colIndex, sortValue) {
    if (!item || !item.id) return;
    const meta = Object.assign({}, item, {
      group: row ? row.group : item.group,
      sectionKey: row ? row.sectionKey : item.sectionKey,
      rowKey: row ? row.rowKey : item.rowKey,
      rowType: row ? row.rowType : item.rowType,
      rowIndex: row ? row.rowIndex : null,
      colIndex: typeof colIndex === 'number' ? colIndex : null,
      sort: typeof sortValue === 'number' ? sortValue : sortOrder,
    });
    model.itemMeta[item.id] = meta;
    model.focusableItems.push(item.id);
  };

  const addCreationControl = function(control) {
    if (!control || !control.id) return;
    model.creationControls.push(control);
    if (!model.itemMeta[control.id]) addMeta(control, null, null, control.sort);
  };

  const addRow = function(row) {
    if (!row || !row.items || row.items.length === 0) return;
    row.rowIndex = model.gridRows.length;
    row.items = row.items.filter(item => item && item.id);
    if (row.items.length === 0) return;
    const rowSortBase = sortOrder++;
    for (let i = 0; i < row.items.length; i++) {
      addMeta(row.items[i], row, i, rowSortBase + (i / 100));
    }
    model.gridRows.push(row);
  };

  const addAgentNav = function(ctx, groupNav, agentId) {
    const a = state.agents[agentId];
    if (!a) return;
    model.navItems.push(agentId);
    model.navAgents.push(agentId);
    groupNav.push(agentId);
    if (a.id === selectedAgentId) {
      const cIds = state.children[a.id] || [];
      for (const cid of cIds) {
        const ct = state.agents[cid];
        if (ct && (!ctx.wid || !ct.window_id || ct.window_id === ctx.wid)) {
          model.navItems.push(cid);
          groupNav.push(cid);
        }
      }
    }
  };

  for (const ctx of groupContexts) {
    model.navGroupOrder.push(ctx.gname);
    const groupNav = [];
    if (!ctx.collapsed) {
      const selectedPrincipalId = ctx.selectedPrincipalId || '';

      // Principals row: user card + architects + + New Architect.
      const principalsRowItems = [];
      const userPrincipalNavId = _principalCardNavId(ctx.gname, '');
      const userPrincipalItem = {
        id: userPrincipalNavId,
        type: 'principal',
        principalId: '',
        group: ctx.gname,
        focusKey: userPrincipalNavId,
      };
      principalsRowItems.push(userPrincipalItem);

      for (const section of ctx.agentLayout.sections) {
        if (!section || !section.architect) continue;
        const archId = String(section.architect.id || '');
        const navId = _principalCardNavId(ctx.gname, archId);
        principalsRowItems.push({
          id: navId,
          type: 'principal',
          principalId: archId,
          group: ctx.gname,
          focusKey: navId,
        });
      }

      if (!ctx.atAgentCap) {
        const newArchitectControlId = _gridNavControlId('agent-new-architect', ctx.gname, '');
        const newArchitectControl = {
          id: newArchitectControlId,
          type: 'control',
          controlType: 'agent-new-architect',
          group: ctx.gname,
          sectionKey: 'user',
          focusKey: _gridNavControlFocusKey('agent-new-architect', ctx.gname, 'user'),
        };
        principalsRowItems.push(newArchitectControl);
        addCreationControl(Object.assign({}, newArchitectControl, {
          sort: sortOrder++,
        }));
      }

      addRow({
        group: ctx.gname,
        sectionKey: 'principals',
        rowKey: 'principals:' + ctx.gname,
        rowType: 'principals-row',
        items: principalsRowItems,
      });

      // Grid: only the section matching the selected principal.
      for (const section of ctx.agentLayout.sections) {
        if (!_principalSectionMatches(section, selectedPrincipalId)) continue;
        const sectionKey = _agentGridSectionKey(section);
        const looseWorkerItems = section.type === 'user'
          ? (section.looseWorkers || []).map(worker => ({
            id: worker.id,
            type: 'agent',
            agentKind: 'worker',
          }))
          : [];
        const workerControl = (section.type === 'user' && !ctx.atAgentCap)
          ? {
            id: _gridNavControlId('section-new-worker', ctx.gname, sectionKey),
            type: 'control',
            controlType: 'section-new-worker',
            group: ctx.gname,
            sectionKey,
            architectId: '',
            focusKey: _gridNavControlFocusKey('section-new-worker', ctx.gname, sectionKey),
          }
          : null;

        if (looseWorkerItems.length || workerControl) {
          addRow({
            group: ctx.gname,
            sectionKey,
            rowKey: sectionKey + ':standalone-workers',
            rowType: 'standalone-workers-row',
            items: workerControl ? looseWorkerItems.concat([workerControl]) : looseWorkerItems,
          });
        }
        if (workerControl) {
          addCreationControl(Object.assign({}, workerControl, {
            sort: model.itemMeta[workerControl.id] ? model.itemMeta[workerControl.id].sort : sortOrder++,
          }));
        }

        for (let sectionRowIndex = 0; sectionRowIndex < section.rows.length; sectionRowIndex++) {
          const row = section.rows[sectionRowIndex];
          const workerItems = (row.workers || []).map(worker => ({
            id: worker.id,
            type: 'agent',
            agentKind: 'worker',
          }));
          const rowItems = [];
          rowItems.push({
            id: row.engineer.id,
            type: 'agent',
            agentKind: 'engineer',
          });
          addRow({
            group: ctx.gname,
            sectionKey,
            rowKey: sectionKey + ':engineer:' + String(row.engineer.id || ''),
            rowType: 'engineer-row',
            architectId: section.architect ? section.architect.id : '',
            engineerId: row.engineer.id,
            items: rowItems.concat(workerItems),
          });
        }

        if (!ctx.atAgentCap) {
          const engineerControlId = _gridNavControlId('section-new-engineer', ctx.gname, sectionKey);
          const controls = [{
            id: engineerControlId,
            type: 'control',
            controlType: 'section-new-engineer',
            group: ctx.gname,
            sectionKey,
            architectId: section.architect ? (section.architect.id || '') : '',
            focusKey: _gridNavControlFocusKey('section-new-engineer', ctx.gname, sectionKey),
          }];
          addRow({
            group: ctx.gname,
            sectionKey,
            rowKey: sectionKey + ':section-new-engineer',
            rowType: 'section-creation-row',
            architectId: section.architect ? (section.architect.id || '') : '',
            items: controls,
          });
          for (const control of controls) {
            addCreationControl(Object.assign({}, control, {
              sort: model.itemMeta[control.id] ? model.itemMeta[control.id].sort : sortOrder++,
            }));
          }
        }
      }

      for (let rowIndex = 0; rowIndex < model.gridRows.length; rowIndex++) {
        if (registeredRowIndexes.has(rowIndex)) continue;
        const row = model.gridRows[rowIndex];
        if (row.group !== ctx.gname) continue;
        registeredRowIndexes.add(rowIndex);
        for (const item of row.items) {
          if (item.type === 'agent') addAgentNav(ctx, groupNav, item.id);
        }
      }

      for (const t of ctx.standaloneTerms) {
        model.navItems.push(t.id);
        groupNav.push(t.id);
      }
    }
    model.navByGroup[ctx.gname] = groupNav;
  }

  model.creationControls.sort(function(a, b) {
    const av = typeof a.sort === 'number' ? a.sort : 0;
    const bv = typeof b.sort === 'number' ? b.sort : 0;
    if (av !== bv) return av - bv;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });

  return model;
}

function _navModelFirstAgentItemInRows(rows, predicate) {
  rows = Array.isArray(rows) ? rows : [];
  for (const row of rows) {
    if (predicate && !predicate(row)) continue;
    for (const item of row.items || []) {
      if (item && item.type === 'agent') return item.id;
    }
  }
  return '';
}

function _resolveFocusedItemForGridRender(currentFocusedId, navModel) {
  if (!currentFocusedId || !navModel) return currentFocusedId || null;
  if (navModel.itemMeta[currentFocusedId] || navModel.navItems.includes(currentFocusedId)) {
    return currentFocusedId;
  }

  const previousMeta = window._navGridItemMeta
    ? window._navGridItemMeta[currentFocusedId]
    : null;
  let fallback = '';

  if (previousMeta) {
    const sameRow = navModel.gridRows.find(row =>
      row.group === previousMeta.group && row.rowKey === previousMeta.rowKey);
    if (sameRow) {
      const start = typeof previousMeta.colIndex === 'number' ? previousMeta.colIndex : 0;
      for (let i = start; i < (sameRow.items || []).length; i++) {
        const item = sameRow.items[i];
        if (item && item.type === 'agent') {
          fallback = item.id;
          break;
        }
      }
      if (!fallback && sameRow.rowType === 'engineer-row' && sameRow.engineerId) {
        const engineerMeta = navModel.itemMeta[sameRow.engineerId];
        if (engineerMeta) fallback = sameRow.engineerId;
      }
    }

    if (!fallback) {
      fallback = _navModelFirstAgentItemInRows(navModel.gridRows, row =>
        row.group === previousMeta.group
        && row.sectionKey === previousMeta.sectionKey
        && row.rowType === 'engineer-row');
    }

    if (!fallback) {
      fallback = _navModelFirstAgentItemInRows(navModel.gridRows, row =>
        row.group === previousMeta.group
        && row.sectionKey === 'user');
    }
  }

  if (!fallback) {
    fallback = _navModelFirstAgentItemInRows(navModel.gridRows);
  }

  if (!fallback && navModel.creationControls.length) {
    fallback = navModel.creationControls[0].id;
  }

  if (fallback && typeof _navPreferredColumn !== 'undefined') {
    const fallbackMeta = navModel.itemMeta[fallback];
    if (fallbackMeta && typeof fallbackMeta.colIndex === 'number') {
      _navPreferredColumn = fallbackMeta.colIndex;
    }
  }
  return fallback || null;
}

function _jsStringAttr(value) {
  return esc(JSON.stringify(String(value || '')));
}

function _renderSectionControlsSlot(groupName, section, opts) {
  opts = opts || {};
  const sectionKey = _agentGridSectionKey(section);
  const architectId = section && section.architect ? String(section.architect.id || '') : '';
  const groupArg = _jsStringAttr(groupName);
  const architectArg = _jsStringAttr(architectId);
  const disabled = !!opts.disabled;
  const navId = _gridNavControlId('section-new-engineer', groupName, sectionKey);
  const focusKey = _gridNavControlFocusKey('section-new-engineer', groupName, sectionKey);
  return '<div class="agent-section-controls-slot"'
    + ' data-section-controls-for="' + esc(sectionKey) + '">'
    + '<button type="button" class="ghost-card ghost-card--engineer' + _gridNavFocusedClass(navId) + '"'
    + ' data-action="new-engineer"'
    + ' data-nav-id="' + esc(navId) + '"'
    + ' data-group="' + esc(groupName) + '"'
    + ' data-hired-by-architect-id="' + esc(architectId) + '"'
    + ' data-focus-key="' + esc(focusKey) + '"'
    + (disabled ? ' disabled aria-disabled="true" title="Agent limit reached"' : ' title="Create an engineer in this section"')
    + (disabled ? '' : ' onclick="event.stopPropagation();openAddEngineerForSection(' + groupArg + ',' + architectArg + ')"')
    + '>+ New Engineer</button>'
    + '</div>';
}

function _renderStandaloneWorkerGhost(groupName, section, opts) {
  opts = opts || {};
  if (!section || section.type !== 'user') return '';
  const sectionKey = _agentGridSectionKey(section);
  const groupArg = _jsStringAttr(groupName);
  const disabled = !!opts.disabled;
  const navId = _gridNavControlId('section-new-worker', groupName, sectionKey);
  const focusKey = _gridNavControlFocusKey('section-new-worker', groupName, sectionKey);
  return '<button type="button" class="ghost-card ghost-card--worker standalone-worker-card-new' + _gridNavFocusedClass(navId) + '"'
    + ' data-action="new-worker"'
    + ' data-nav-id="' + esc(navId) + '"'
    + ' data-group="' + esc(groupName) + '"'
    + ' data-focus-key="' + esc(focusKey) + '"'
    + (disabled ? ' disabled aria-disabled="true" title="Agent limit reached"' : ' title="Create a user-owned standalone worker"')
    + (disabled ? '' : ' onclick="event.stopPropagation();openAddWorkerForSection(' + groupArg + ')"')
    + '>+ Add Worker</button>';
}

function _renderStandaloneWorkersStrip(section, renderCell, opts) {
  opts = opts || {};
  if (!section || section.type !== 'user') return '';
  const workers = section.looseWorkers || [];
  const ghostHtml = _renderStandaloneWorkerGhost(opts.groupName || '', section, opts);
  if (!workers.length && !ghostHtml) return '';
  let html = '<div class="loose-workers-strip" data-agent-row-shape="standalone-workers-row">';
  for (const worker of workers) html += renderCell(worker);
  html += ghostHtml;
  html += '</div>';
  return html;
}

function _renderEngineerRow(row, renderCell) {
  if (!row || !row.engineer) return '';
  const workers = row.workers || [];
  const rowClasses = ['engineer-row', 'agent-grid-engineer-row'];
  if (!workers.length) rowClasses.push('engineer-row--empty-workers');
  let html = '<div class="' + esc(rowClasses.join(' ')) + '"'
    + ' data-agent-row-shape="engineer-row"'
    + ' data-worker-count="' + esc(String(workers.length)) + '"'
    + ' data-engineer-id="' + esc(row.engineer.id || '') + '">';
  html += '<div class="engineer-row-anchor">' + renderCell(row.engineer) + '</div>';
  html += '<div class="engineer-row-workers">';
  for (const worker of workers) html += renderCell(worker);
  html += '</div>';
  html += '</div>';
  return html;
}


function _selectedAgentForFocusPanel() {
  if (!selectedAgentId || !state || !state.agents) return null;
  const agent = state.agents[selectedAgentId];
  if (!agent || agent.cell_type === 'terminal') return null;
  const selectedCell = selectedTerminalId && state.agents[selectedTerminalId];
  if (_embeddedRuntimeEnabled()
      && selectedCell
      && selectedCell.cell_type === 'terminal'
      && selectedCell.parent_id !== agent.id) {
    return null;
  }
  return agent;
}

function _renderSelectedAgentTerminalDrawer(agent) {
  if (!agent) return '';
  const wid = (typeof getFilterByWindow === 'function' && getFilterByWindow())
    ? state.current_window_id
    : null;
  const childIds = (state.children && state.children[agent.id]) || [];
  const childTerms = childIds
    .map(id => state.agents[id])
    .filter(c => c && (!wid || !c.window_id || c.window_id === wid));
  let html = `<div class="terminal-drawer agent-focus-terminals">`;
  html += `<div class="drawer-hdr">`;
  html += `  <span class="drawer-label">${esc(agent.name)} terminals</span>`;
  html += `  <span class="drawer-count">${childTerms.length}</span>`;
  html += `</div>`;
  html += `<div class="term-list" data-drop-type="terminal" data-drop-group="${esc(agent.group)}" data-drop-parent="${esc(agent.id)}">`;
  for (const t of childTerms) html += renderTerminalRow(t);
  html += `</div>`;
  html += renderTermAddBtn(agent.group, agent.id);
  html += `</div>`;
  return html;
}

function _renderAgentFocusPanelHtml() {
  const agent = _selectedAgentForFocusPanel();
  let html = '<div class="agent-focus-header">'
    + '<div class="agent-focus-title">Focus</div>';
  if (agent) {
    html += '<div class="agent-focus-subtitle" title="' + esc(agent.name || '') + '">'
      + esc(agent.name || agent.id || 'Agent')
      + '</div>';
  }
  html += '</div>';
  if (!agent) {
    html += '<div class="agent-focus-empty">Select an agent to view details and terminals.</div>';
    return html;
  }
  html += '<div class="agent-focus-content">';
  html += renderAgentDetails(agent);
  html += _renderSelectedAgentTerminalDrawer(agent);
  html += '</div>';
  return html;
}

function _agentFocusShellHtml(gridHtml, focusHtml) {
  return '<div class="agent-split" data-agent-split>'
    + '<div id="agent-grid-pane" class="agents-grid-pane" data-agent-grid-pane>'
    + (gridHtml || '')
    + '</div>'
    + '<div id="agent-focus-resizer" class="agent-focus-resizer" role="separator" aria-orientation="horizontal" tabindex="0" data-agent-focus-resizer onmousedown="_agentFocusResizeStart(event)">'
    + '<div class="agent-focus-resizer-grip" aria-hidden="true"></div>'
    + '</div>'
    + '<section id="agent-focus-panel" class="agent-focus-panel" data-agent-focus-panel>'
    + '<div class="agent-focus-panel-scroll" data-agent-focus-scroll>'
    + (focusHtml || '')
    + '</div>'
    + '</section>'
    + '</div>';
}

function _renderAgentGridAndFocus(main, gridHtml, opts) {
  opts = opts || {};
  const renderFocus = opts.renderFocus !== false;
  const focusHtml = renderFocus
    ? _renderAgentFocusPanelHtml()
    : (main._torqueLastFocusHtml || _renderAgentFocusPanelHtml());
  const combined = _agentFocusShellHtml(gridHtml, focusHtml);
  const gridChanged = main._torqueLastGridHtml !== gridHtml;
  const parts = _agentFocusSplitParts(main);
  const shellMissing = !main._torqueHasAgentSplitShell;
  if (shellMissing || (gridChanged && !parts)) {
    main.innerHTML = combined;
    main._torqueHasAgentSplitShell = true;
    main._torqueLastGridHtml = gridHtml;
    main._torqueLastFocusHtml = focusHtml;
    main._torqueLastHtml = combined;
    _agentFocusApplyPersistedSplit();
    return { mainHtmlChanged: true, focusHtmlChanged: renderFocus };
  }
  if (gridChanged) {
    parts.grid.innerHTML = gridHtml || '';
    main._torqueLastGridHtml = gridHtml;
    const focusChanged = renderFocus
      ? renderAgentFocusPanel({ main, focusHtml })
      : false;
    if (!renderFocus) main._torqueLastFocusHtml = focusHtml;
    main._torqueLastHtml = _agentFocusShellHtml(
      gridHtml,
      main._torqueLastFocusHtml || focusHtml,
    );
    return { mainHtmlChanged: true, focusHtmlChanged: focusChanged };
  }
  main._torqueLastHtml = combined;
  if (renderFocus) renderAgentFocusPanel({ main, focusHtml });
  return { mainHtmlChanged: false, focusHtmlChanged: renderFocus && main._torqueLastFocusHtml !== focusHtml };
}

function renderAgentFocusPanel(opts) {
  opts = opts || {};
  const main = opts.main || document.getElementById('main');
  if (!main) return false;
  if (typeof _captureAgentDetailDrafts === 'function') _captureAgentDetailDrafts();
  const focusHtml = Object.prototype.hasOwnProperty.call(opts, 'focusHtml')
    ? opts.focusHtml
    : _renderAgentFocusPanelHtml();
  if (main._torqueLastFocusHtml === focusHtml) return false;
  const parts = _agentFocusSplitParts(main);
  if (!parts) {
    const gridHtml = main._torqueLastGridHtml || '';
    main.innerHTML = _agentFocusShellHtml(gridHtml, focusHtml);
    main._torqueHasAgentSplitShell = true;
    main._torqueLastFocusHtml = focusHtml;
    main._torqueLastHtml = main.innerHTML;
    _agentFocusApplyPersistedSplit();
    _restoreActiveDetailInputFocus();
    return true;
  }
  if (parts.focusScroll._torqueLastHtml === focusHtml || main._torqueLastFocusHtml === focusHtml) {
    parts.focusScroll._torqueLastHtml = focusHtml;
    main._torqueLastFocusHtml = focusHtml;
    return false;
  }
  const panelState = typeof _captureSurfaceState === 'function'
    ? _captureSurfaceState(parts.focusScroll, { scrollSelectors: [':root', '.mcp-log'] })
    : null;
  parts.focusScroll.innerHTML = focusHtml;
  parts.focusScroll._torqueLastHtml = focusHtml;
  main._torqueLastFocusHtml = focusHtml;
  main._torqueLastHtml = _agentFocusShellHtml(main._torqueLastGridHtml || '', focusHtml);
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(parts.focusScroll, panelState, { scrollSelectors: [':root', '.mcp-log'] });
    _restoreActiveDetailInputFocus();
  }
  return true;
}

function _updateAgentGridSelectionForFocus(prevId, nextId) {
  if (prevId === nextId) return;
  const main = document.getElementById('main');
  if (!main || typeof main.querySelector !== 'function') return;
  const cssEscape = function(value) {
    const raw = String(value || '');
    if (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function') return CSS.escape(raw);
    return raw.replace(/"/g, '\\"');
  };
  if (prevId) {
    const prev = main.querySelector('[data-drag-id="' + cssEscape(prevId) + '"]');
    if (prev && prev.classList) prev.classList.remove('selected');
  }
  if (nextId) {
    const next = main.querySelector('[data-drag-id="' + cssEscape(nextId) + '"]');
    if (next && next.classList) next.classList.add('selected');
  }
}

function refreshSelectedAgentFocus(prevSelectedId) {
  _updateAgentGridSelectionForFocus(prevSelectedId || '', selectedAgentId || '');
  if (typeof renderAgentFocusPanel === 'function') renderAgentFocusPanel();
}

function render(opts) {
  if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
  if (_torqueUiMode() === 'toolbelt') {
    return _renderMainGrid(opts, { singleGroup: false });
  }
  return _renderMainGrid(opts, { singleGroup: _singleGroupModeEnabled() });
}

function _renderMainGrid(opts, renderMode) {
  const main = document.getElementById('main');
  // TORQUE:236 v10: when render() is invoked from a delta-driven invalidation
  // and only the main surface was flagged (not engineer), skip the trailing
  // agent-panel refresh — the surfaces dispatch loop already calls
  // `_renderSurface('engineer')` independently when the engineer flag is
  // set. Without this hint, every main-surface delta caused a redundant
  // `_agentPanelRefreshCurrentTab()` even when nothing the panel displays
  // had changed (still surgical post-v9, but wasteful CPU + future-fragile).
  const _skipPanelRefresh = !!(opts && opts.skipPanelRefresh);
  let groupNames = Object.keys(state.groups);
  if (renderMode && renderMode.singleGroup) {
    groupNames = _activeGroupNamesForRender(groupNames);
  }
  const embeddedMode = _embeddedRuntimeEnabled();
  _pruneAgentDoneFlourishes((state && state.agents) || {});

  if (groupNames.length === 0) {
    const action = _singleGroupModeEnabled()
      ? '<br><button type="button" class="empty-action" onclick="openAddGroup()">+ New group</button>'
      : '';
    const emptyHtml = `
      <div class="empty">
        <div class="empty-icon">\u2B22</div>
        No groups yet.<br>Create one to get started.
        ${action}
      </div>`;
    const emptyState = _captureSurfaceState(main, { scrollSelectors: [':root', '.agents-grid-pane'] });
    const result = _renderAgentGridAndFocus(main, _renderAgentGroupTabsHtml() + emptyHtml, {
      renderFocus: !(opts && opts.skipFocusRefresh),
    });
    if (result.mainHtmlChanged) _restoreSurfaceState(main, emptyState);
    window._navItems = [];
    window._navAgents = [];
    window._navByGroup = {};
    window._navGroupOrder = [];
    window._navGridRows = [];
    window._navGridItemMeta = {};
    window._navCreationControls = [];
    window._navFocusableItems = [];
    focusedItemId = null;
    if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
    return;
  }

  const doFlip = Date.now() < _flipUntil;
  const oldRects = doFlip ? _captureRects(main) : null;
  _refreshTaskLookupIndexForRender();
  _captureAgentDetailDrafts();
  const mainState = _captureSurfaceState(main, {
    scrollSelectors: [
      ':root',
      '.agents-grid-pane',
      '.loose-workers-strip',
    ],
    captureFocusKey: _captureMainFocusKey,
  });

  // Clear selectedAgentId if it no longer exists
  if (selectedAgentId && (!state.agents[selectedAgentId] || _isTombstonedAgent(state.agents[selectedAgentId]))) selectedAgentId = null;
  if (typeof selectedTerminalId !== 'undefined'
      && selectedTerminalId
      && (!state.agents[selectedTerminalId] || _isTombstonedAgent(state.agents[selectedTerminalId]))) {
    selectedTerminalId = null;
  }

  const groupContexts = [];
  for (const gname of groupNames) {
    const cells = _visibleGroupCellsForGrid(gname, embeddedMode);
    // Hide group only if it has cells but none in this window;
    // always show truly empty groups so the user can populate them.
    if (cells.hiddenByWindow) continue;

    const gsLocal = (state.group_settings || {})[gname] || {};
    _ensureGroupCollapsedInitialized(gname, gsLocal);
    const collapsed = collapsedGroups.has(gname);
    const agentLayout = _buildHierarchicalAgentSections(cells.agents);
    const selectedPrincipalId = _resolveSelectedPrincipalId(agentLayout.sections);
    groupContexts.push({
      gname,
      aids: cells.aids,
      agents: cells.agents,
      standaloneTerms: cells.standaloneTerms,
      wid: cells.wid,
      gsLocal,
      collapsed,
      agentLayout,
      selectedPrincipalId,
      atAgentCap: gsLocal.max_agents > 0 && cells.agents.length >= gsLocal.max_agents,
    });
  }

  const navModel = _buildAgentGridNavigationModel(groupContexts);
  focusedItemId = _resolveFocusedItemForGridRender(focusedItemId, navModel);

  let html = '';
  const renderGroupChrome = !(renderMode && renderMode.singleGroup);
  for (const ctx of groupContexts) {
    const gname = ctx.gname;
    const agents = ctx.agents;
    const standaloneTerms = ctx.standaloneTerms;
    const wid = ctx.wid;
    const collapsed = ctx.collapsed;

    if (renderGroupChrome) {
      html += `<div class="group${collapsed ? ' collapsed' : ''}" data-group-name="${esc(gname)}">`;
      html += `<div class="group-hdr" draggable="true" data-drag-id="${esc(gname)}" data-drag-type="group">`;
      html += `  <button class="group-toggle" draggable="false" onclick="event.stopPropagation();toggleGroup('${esc(gname)}')">\u25BE</button>`;
      html += `  <span class="group-name" title="${esc(gname)}">${esc(gname)}</span>`;
      html += `  <span class="group-count">${agents.length}</span>`;
      html += `  <button class="group-btn" draggable="false" title="Group settings" aria-label="Group settings" onclick="event.stopPropagation();openGroupSettings('${esc(gname)}')">\u2699</button>`;
      html += `</div>`;

      html += `<div class="group-body"><div class="group-body-inner">`;
    }

    /* Agent grid — hierarchical by kind/owner */
    const agentLayout = ctx.agentLayout;
    const visibleAgentById = agentLayout.visibleAgentById;
    const visibleEngineerIds = agentLayout.visibleEngineerIds;
    const selectedPrincipalId = ctx.selectedPrincipalId || '';

    /* Principals row: User + architects + + New Architect, wraps when narrow. */
    html += _renderPrincipalsRow(
      gname,
      agentLayout.sections,
      selectedPrincipalId,
      { disabled: ctx.atAgentCap },
    );

    html += `<div class="agent-grid agent-grid-hierarchical" data-drop-group="${esc(gname)}" data-drop-type="agent">`;
    const renderCellForGrid = function(a) {
      return renderAgentCell(a, { visibleEngineerIds, visibleAgentById });
    };
    let renderedAnySection = false;
    for (let sectionIndex = 0; sectionIndex < agentLayout.sections.length; sectionIndex++) {
      const section = agentLayout.sections[sectionIndex];
      if (!_principalSectionMatches(section, selectedPrincipalId)) continue;
      const sectionKey = _agentGridSectionKey(section);
      const sectionClasses = [
        'agent-section',
        'agent-section--filtered',
        section.type === 'user' ? 'agent-section-user' : 'agent-section-architect',
      ];
      if (!section.rows || section.rows.length === 0) {
        sectionClasses.push('agent-section--empty');
      }
      html += '<section class="' + sectionClasses.join(' ') + '"'
        + ' data-agent-section="' + esc(sectionKey) + '">';
      html += '<div class="agent-section-body"'
        + ' data-agent-section-column="body"'
        + ' data-section-key="' + esc(sectionKey) + '">';
      const hasStandaloneWorkerCards = section.type === 'user'
        && section.looseWorkers
        && section.looseWorkers.length > 0;
      html += _renderStandaloneWorkersStrip(section, renderCellForGrid, {
        groupName: gname,
        disabled: ctx.atAgentCap,
      });
      if (!section.rows || section.rows.length === 0) {
        if (!hasStandaloneWorkerCards) {
          html += '<div class="agent-section-empty-msg">No engineers yet.</div>';
        }
      } else {
        for (const row of section.rows) html += _renderEngineerRow(row, renderCellForGrid);
      }
      html += _renderSectionControlsSlot(gname, section, { disabled: ctx.atAgentCap });
      html += '</div>';
      html += '</section>';
      renderedAnySection = true;
    }
    if (!renderedAnySection) {
      // Stored principal id does not match any section (e.g. architect
      // deleted and fallback resolution missed). Shouldn't happen in
      // practice because _resolveSelectedPrincipalId falls back to user.
      html += '<div class="agent-section agent-section--empty"'
        + ' data-agent-section="empty">'
        + '<div class="agent-section-empty-msg">No engineers yet.</div>'
        + '</div>';
    }
    html += `</div>`;

    if (standaloneTerms.length) {
      html += `<div class="terminal-drawer">`;
      html += `<div class="drawer-hdr">`;
      html += `  <span class="drawer-label">Group terminals</span>`;
      html += `  <span class="drawer-count">${standaloneTerms.length}</span>`;
      html += `</div>`;
      html += `<div class="term-list" data-drop-type="terminal" data-drop-group="${esc(gname)}">`;
      for (const t of standaloneTerms) html += renderTerminalRow(t);
      html += `</div>`;
      html += renderTermAddBtn(gname, '');
      html += `</div>`;
    }

    if (renderGroupChrome) {
      html += `</div></div>`;
      html += `</div>`;
    }
  }

  // TORQUE:264 follow-up: byte-equality memoize the agent grid clobber. Each
  // delta op that flips `flags.main` (e.g. `agent_upsert` on every activity
  // tick) calls render(); the unconditional `main.innerHTML = html` blast
  // destroys + recreates every agent card on every tick, which kills the
  // user-visible :hover state on `.agent-card-tooltip` pseudo-elements
  // (style.css:1142) — produces fast tooltip flicker while a card is being
  // hovered. Same `_torqueLastHtml` pattern as the topbar/tabs cache from
  // `06611b8`. When the html is unchanged the FLIP animation + surface
  // restore are no-ops on identical DOM, so skip them too.
  // The effective grid clobber is still guarded like `main._torqueLastHtml !== html`;
  // after a successful split write the aggregate cache is updated like `main._torqueLastHtml = html`.
  // The split stores the grid fragment separately so focus-only refreshes do not rewrite it.
  const mainRenderResult = _renderAgentGridAndFocus(main, _renderAgentGroupTabsHtml() + html, {
    renderFocus: !(opts && opts.skipFocusRefresh),
  });
  const mainHtmlChanged = !!mainRenderResult.mainHtmlChanged;

  // Update navigable item lists after resolving focus so the rendered
  // `.focused` marker and the keyboard model describe the same grid.
  window._navItems = navModel.navItems;
  window._navAgents = navModel.navAgents;
  window._navByGroup = navModel.navByGroup;
  window._navGroupOrder = navModel.navGroupOrder;
  window._navGridRows = navModel.gridRows;
  window._navGridItemMeta = navModel.itemMeta;
  window._navCreationControls = navModel.creationControls;
  window._navFocusableItems = navModel.focusableItems;
  if (focusedItemId
      && !navModel.navItems.includes(focusedItemId)
      && !navModel.itemMeta[focusedItemId]) {
    focusedItemId = null;
  }

  if (mainHtmlChanged) {
    if (oldRects) _applyFlip(main, oldRects);
    _restoreSurfaceState(main, mainState);
    _restoreActiveDetailInputFocus();
  }
  renderPendingHireBanner();
  _updateEngineerTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
  if (!_skipPanelRefresh && typeof renderAgentPanel === 'function') {
    const surfaces = _currentPanelSurfaces();
    if (surfaces.includes('engineer')) {
      // TORQUE:236 v9: route through surgical-first instead of full
      // `renderAgentPanel()` clobber.
      if (typeof _agentPanelRefreshCurrentTab === 'function'
          && _agentPanelRefreshCurrentTab()) {
        // Surgical path handled it.
      } else {
        renderAgentPanel();
      }
    }
  }
  if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
}

function agentStatusClass(a) {
  /* Attention overrides everything */
  if (a.needs_attention) return 'attention';
  /* Disconnected (tab closed) */
  if (a.status === 'stopped') return 'disconnected';
  /* For awareness agents, activity is the source of truth */
  if (a.agent_type) {
    if (a.activity) return 'working';
    /* No activity — idle if we've heard from the agent before;
       otherwise it just started and hasn't sent its first event yet */
    if (a.last_event_at > 0) return 'idle';
  }
  /* Non-awareness agents / agents that haven't sent events yet */
  if (a.status === 'running') return 'working';
  return 'idle';
}

var _taskLookupIndex = null;
var _taskLookupIndexSource = null;

function _invalidateTaskLookupIndex() {
  _taskLookupIndex = null;
  _taskLookupIndexSource = null;
}

function _taskLookupSortFollowers(a, b) {
  return String(a.created_at || a.updated_at || a.id || '')
    .localeCompare(String(b.created_at || b.updated_at || b.id || ''));
}

function _taskLookupPreferredAgentTask(entry) {
  if (!entry) return null;
  return entry.in_progress || entry.open || entry.done || null;
}

function _buildTaskLookupIndex(tasks) {
  const taskMap = tasks || {};
  const taskValues = Object.values(taskMap);
  const agentTaskEntries = {};
  const agentTaskById = {};
  const latestBoundaryByKey = {};
  const followersByBoundaryId = {};

  for (let i = 0; i < taskValues.length; i++) {
    const task = taskValues[i];
    if (!task) continue;

    const agentId = String(task.agent_id || '');
    if (agentId) {
      let entry = agentTaskEntries[agentId];
      if (!entry) {
        entry = { in_progress: null, open: null, done: null };
        agentTaskEntries[agentId] = entry;
      }
      if (task.lane === 'In Progress' && !entry.in_progress) entry.in_progress = task;
      if (task.lane !== 'Done' && !entry.open) entry.open = task;
      if (!entry.done) entry.done = task;
    }

    const boundaryKey = _taskBoundaryBranchKey(task);
    if (boundaryKey && (_taskBoundaryMeta(task).status || '') === 'open') {
      const latest = latestBoundaryByKey[boundaryKey];
      if (!latest || _taskBoundarySortValue(task) > _taskBoundarySortValue(latest)) {
        latestBoundaryByKey[boundaryKey] = task;
      }
    }

    const resumeAfter = String(task.resume_after_boundary_task_id || '');
    if (resumeAfter) {
      if (!followersByBoundaryId[resumeAfter]) {
        followersByBoundaryId[resumeAfter] = { queued: [], started: [] };
      }
      if (task.lane === 'Backlog' || task.lane === 'To Do') {
        followersByBoundaryId[resumeAfter].queued.push(task);
      } else {
        followersByBoundaryId[resumeAfter].started.push(task);
      }
    }
  }

  for (const agentId in agentTaskEntries) {
    const task = _taskLookupPreferredAgentTask(agentTaskEntries[agentId]);
    if (task) agentTaskById[agentId] = task;
  }

  const branchBoundaryByKey = {};
  for (const branchKey in latestBoundaryByKey) {
    const latest = latestBoundaryByKey[branchKey];
    const boundary = _taskBoundaryMeta(latest);
    const followers = followersByBoundaryId[latest.id] || { queued: [], started: [] };
    const queued = followers.queued.slice().sort(_taskLookupSortFollowers);
    const started = followers.started.slice().sort(_taskLookupSortFollowers);
    branchBoundaryByKey[branchKey] = {
      repo_root: boundary.repo_root || '',
      branch: boundary.branch || '',
      latest_boundary_task: latest,
      queued_followers: queued,
      started_followers: started,
      branch_advanced: started.length > 0,
      partial_review_safe: started.length === 0,
    };
  }

  return {
    source: taskMap,
    agentTaskById,
    branchBoundaryByKey,
    tasks: taskValues,
  };
}

function _currentTaskLookupIndex() {
  if (!state || !state.board_tasks) return null;
  if (!_taskLookupIndex || _taskLookupIndexSource !== state.board_tasks) {
    _taskLookupIndex = _buildTaskLookupIndex(state.board_tasks);
    _taskLookupIndexSource = state.board_tasks;
  }
  return _taskLookupIndex;
}

function _refreshTaskLookupIndexForRender() {
  if (!state || !state.board_tasks) {
    _invalidateTaskLookupIndex();
    return null;
  }
  _taskLookupIndex = _buildTaskLookupIndex(state.board_tasks);
  _taskLookupIndexSource = state.board_tasks;
  return _taskLookupIndex;
}

function _getAgentTask(agentId) {
  const index = _currentTaskLookupIndex();
  if (!index) return null;
  return index.agentTaskById[String(agentId || '')] || null;
}

function _taskBoundaryMeta(task) {
  if (!task || !task.worktree_boundary || typeof task.worktree_boundary !== 'object') {
    return {};
  }
  return task.worktree_boundary;
}

function _taskBoundaryBranchKey(task) {
  const boundary = _taskBoundaryMeta(task);
  const repoRoot = boundary.repo_root || '';
  const branch = boundary.branch || '';
  if (!repoRoot || !branch) return '';
  return repoRoot + '::' + branch;
}

function _taskBoundarySortValue(task) {
  const boundary = _taskBoundaryMeta(task);
  return String(boundary.recorded_at || task.updated_at || task.created_at || '');
}

function _branchBoundaryOverviewForContext(repoRoot, branch) {
  if (!repoRoot || !branch || !state || !state.board_tasks) return null;
  const branchKey = repoRoot + '::' + branch;
  const index = _currentTaskLookupIndex();
  const overview = index && index.branchBoundaryByKey
    ? index.branchBoundaryByKey[branchKey]
    : null;
  if (!overview) return null;
  return {
    repo_root: overview.repo_root,
    branch: overview.branch,
    latest_boundary_task: overview.latest_boundary_task,
    queued_followers: overview.queued_followers.slice(),
    started_followers: overview.started_followers.slice(),
    branch_advanced: overview.branch_advanced,
    partial_review_safe: overview.partial_review_safe,
  };
}

function _branchBoundaryOverviewForAgent(agent) {
  if (!agent) return null;
  const repoRoot = agent.worktree_repo_root || agent.git_root || '';
  const branch = agent.worktree_branch || '';
  const overview = _branchBoundaryOverviewForContext(repoRoot, branch);
  if (!overview) return null;
  overview.current_task = _getAgentTask(agent.id);
  return overview;
}

var _agentDetailUiState = {};

function _agentDetailState(agentId) {
  const key = String(agentId || '');
  if (!_agentDetailUiState[key]) {
    _agentDetailUiState[key] = {
      task_expanded: false,
      expanded_messages: {},
      description_editor: {
        task_id: '',
        open: false,
        draft: '',
      },
    };
  }
  return _agentDetailUiState[key];
}

function _agentDetailDescriptionState(agentId, task) {
  const detailState = _agentDetailState(agentId);
  const taskId = String((task && task.id) || '');
  const currentDescription = String((task && task.description) || '');
  if (!detailState.description_editor || detailState.description_editor.task_id !== taskId) {
    detailState.description_editor = {
      task_id: taskId,
      open: false,
      draft: currentDescription,
    };
  } else if (!detailState.description_editor.open) {
    detailState.description_editor.draft = currentDescription;
  }
  return detailState.description_editor;
}

function _agentDetailMessageKey(message, index) {
  const action = String((message && message.action) || '');
  const ts = Number((message && message.timestamp) || 0);
  if (ts > 0) return `mcp:${ts}:${action}`;
  return `mcp:${index}:${action}`;
}

function _toggleAgentDetailTask(agentId) {
  const detailState = _agentDetailState(agentId);
  detailState.task_expanded = !detailState.task_expanded;
  // In compact mode the linked task card omits description/artifacts. Fire
  // a hydrate when the panel is opening so the expanded body shows real
  // description text instead of a false "Add description" placeholder.
  if (detailState.task_expanded
      && typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof ensureTaskDetail === 'function') {
    const task = _getAgentTask(agentId);
    const taskId = task ? String(task.id || '') : '';
    if (taskId
        && typeof _compactTaskHasFullDetail === 'function'
        && !_compactTaskHasFullDetail(task)) {
      ensureTaskDetail(taskId, function() { render(); });
    }
  }
  render();
}

function _toggleAgentDetailMessage(agentId, messageKey) {
  const state = _agentDetailState(agentId);
  if (state.expanded_messages[messageKey]) delete state.expanded_messages[messageKey];
  else state.expanded_messages[messageKey] = true;
  render();
}

function agentDetailEditDescription(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  // Hydrate before opening the editor so the draft isn't seeded from an
  // empty compact card — otherwise Save could overwrite an existing
  // server-side description with whatever the user typed on top of "".
  if (typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)
      && typeof ensureTaskDetail === 'function') {
    ensureTaskDetail(taskId, function() {
      agentDetailEditDescription(agentId, taskId);
    });
    return;
  }
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = true;
  editor.draft = String(task.description || '');
  render();
  requestAnimationFrame(function() {
    const input = document.getElementById('detail-description-input');
    if (!input) return;
    if (typeof input.focus === 'function') input.focus();
    if ('value' in input && 'selectionStart' in input) {
      const cursor = String(input.value || '').length;
      input.selectionStart = cursor;
      input.selectionEnd = cursor;
    }
  });
}

function agentDetailDescriptionInput(agentId, taskId, value) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = true;
  editor.draft = String(value || '');
}

function agentDetailCancelDescriptionEdit(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  editor.open = false;
  editor.draft = String(task.description || '');
  render();
}

function agentDetailSaveDescription(agentId, taskId) {
  const task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task) return;
  const editor = _agentDetailDescriptionState(agentId, task);
  const input = document.getElementById('detail-description-input');
  if (input && 'value' in input) editor.draft = input.value;
  // Defence in depth: never issue a destructive update against a card
  // whose previous description we never actually loaded.
  if (typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(task)
      && typeof ensureTaskDetail === 'function') {
    const pendingDraft = String(editor.draft || '');
    ensureTaskDetail(taskId, function() {
      const refreshed = state && state.board_tasks ? state.board_tasks[taskId] : null;
      if (!refreshed) return;
      const refreshedEditor = _agentDetailDescriptionState(agentId, refreshed);
      refreshedEditor.open = true;
      refreshedEditor.draft = pendingDraft;
      agentDetailSaveDescription(agentId, taskId);
    });
    return;
  }
  const previousDescription = String(task.description || '');
  const nextDescription = String(editor.draft || '').trim();
  editor.open = false;
  editor.draft = nextDescription;
  if (nextDescription !== previousDescription) {
    task.description = nextDescription;
    send({ cmd: 'board_update_task', id: taskId, description: nextDescription });
  }
  render();
}

function agentDetailDescriptionKeydown(evt, agentId, taskId) {
  if (!evt) return;
  if (evt.key === 'Escape') {
    evt.preventDefault();
    evt.stopPropagation();
    agentDetailCancelDescriptionEdit(agentId, taskId);
  } else if (evt.key === 'Enter' && (evt.metaKey || evt.ctrlKey)) {
    evt.preventDefault();
    evt.stopPropagation();
    agentDetailSaveDescription(agentId, taskId);
  }
}

function _detailMultilineHtml(text) {
  return formatCode(text || '').replace(/\n/g, '<br>');
}

function _detailTaskLinkHtml(taskId) {
  if (!taskId) return '';
  return '<button type="button" class="detail-link-arrow"'
    + ' title="View task on board"'
    + ' data-focus-key="detail-task-link:' + esc(taskId) + '"'
    + ' onclick="event.stopPropagation();if(typeof boardNavigateToTask===\'function\'){boardNavigateToTask(\''
    + esc(taskId) + '\');}">\u2192</button>';
}

function _architectPendingHiresForAgent(agentId) {
  if (!state || !state.pending_hires) return [];
  const architectId = String(agentId || '');
  return Object.values(state.pending_hires).filter(function(hire) {
    return String((hire && hire.architect_id) || '') === architectId;
  }).sort(function(a, b) {
    const aTs = Number((a && (a.created_at || a.updated_at)) || 0);
    const bTs = Number((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
}

function _architectDecisionsForAgent(agentId) {
  if (!state || !state.decisions) return [];
  const architectId = String(agentId || '');
  return Object.values(state.decisions).filter(function(decision) {
    return String((decision && decision.architect_id) || '') === architectId;
  }).sort(function(a, b) {
    const aTs = _agentCardTimestampSeconds((a && (a.created_at || a.updated_at)) || 0);
    const bTs = _agentCardTimestampSeconds((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
}

var _dismissedPendingHireIds = {};

function _activePendingHireItems() {
  if (!state || !state.pending_hires) return [];
  return Object.values(state.pending_hires).filter(function(hire) {
    return String((hire && hire.status) || 'pending') === 'pending';
  }).sort(function(a, b) {
    const aTs = Number((a && (a.created_at || a.updated_at)) || 0);
    const bTs = Number((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return aTs - bTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
}

function _pruneDismissedPendingHireIds() {
  const liveIds = new Set(_activePendingHireItems().map(function(hire) {
    return String((hire && hire.id) || '');
  }));
  Object.keys(_dismissedPendingHireIds || {}).forEach(function(id) {
    if (!liveIds.has(id)) delete _dismissedPendingHireIds[id];
  });
}

function _pendingHireBannerItem() {
  _pruneDismissedPendingHireIds();
  const hires = _activePendingHireItems().filter(function(hire) {
    return !_dismissedPendingHireIds[String((hire && hire.id) || '')];
  });
  return {
    current: hires.length ? hires[0] : null,
    remaining: Math.max(0, hires.length - 1),
  };
}

function _dismissPendingHire(id) {
  const key = String(id || '').trim();
  if (!key) return;
  _dismissedPendingHireIds[key] = Date.now();
}

function renderPendingHireBanner() {
  const el = document.getElementById('pending-hire-banner');
  if (!el) return;
  const banner = _pendingHireBannerItem();
  const hire = banner.current;
  if (!hire) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const architect = state && state.agents ? state.agents[hire.architect_id] : null;
  const architectName = architect ? (architect.name || architect.id) : (hire.architect_id || 'Architect');
  const hireIdJs = JSON.stringify(String(hire.id || ''));
  const moreText = banner.remaining > 0
    ? `<span class="pending-hire-banner-more">+${banner.remaining} more hire request${banner.remaining === 1 ? '' : 's'}</span>`
    : '';
  el.hidden = false;
  el.innerHTML = ''
    + `<div class="pending-hire-banner-body">`
    + `<div class="pending-hire-banner-copy">Architect <strong>${esc(architectName)}</strong> is requesting to hire a new engineer <strong>"${esc(hire.requested_name || 'Engineer')}"</strong>. ${moreText}</div>`
    + `<div class="pending-hire-banner-actions">`
    + `<button type="button" class="pending-hire-banner-btn pending-hire-banner-btn-primary" onclick='approvePendingHire(${hireIdJs})'>Approve</button>`
    + `<button type="button" class="pending-hire-banner-btn" onclick='rejectPendingHire(${hireIdJs})'>Reject with note</button>`
    + `</div></div>`;
}

function approvePendingHire(hireId) {
  const id = String(hireId || '').trim();
  if (!id || typeof send !== 'function') return;
  _dismissPendingHire(id);
  renderPendingHireBanner();
  send({ cmd: 'pending_hire_approve', id: id });
  if (typeof _showToast === 'function') {
    _showToast('Approved hire request', 'success');
  }
}

function rejectPendingHire(hireId) {
  const id = String(hireId || '').trim();
  if (!id) return;
  const hire = state && state.pending_hires ? state.pending_hires[id] : null;
  const architect = hire && state && state.agents ? state.agents[hire.architect_id] : null;
  const architectName = architect ? (architect.name || architect.id) : (hire && hire.architect_id) || 'Architect';
  const summary = hire
    ? 'Reject ' + (hire.requested_name || 'this engineer')
      + ' for ' + architectName + ' with an optional note.'
    : 'Add an optional note for the architect.';
  if (typeof openPendingHireRejectModal === 'function') {
    openPendingHireRejectModal(id, summary);
    return;
  }
  if (typeof send !== 'function') return;
  const note = (typeof window !== 'undefined'
    && window
    && typeof window.prompt === 'function')
    ? window.prompt('Optional rejection note', '')
    : '';
  if (note === null) return;
  rejectPendingHireWithNote(id, String(note || ''));
}

function rejectPendingHireWithNote(hireId, note) {
  const id = String(hireId || '').trim();
  if (!id || typeof send !== 'function') return;
  _dismissPendingHire(id);
  renderPendingHireBanner();
  send({ cmd: 'pending_hire_reject', id: id, note: String(note || '') });
  if (typeof _showToast === 'function') {
    _showToast('Rejected hire request', 'success');
  }
}

function _renderAgentDetailDescription(agentId, task) {
  if (!task) return '';
  const editor = _agentDetailDescriptionState(agentId, task);
  const title = task.description ? 'Edit task description' : 'Add task description';
  const agentIdJs = JSON.stringify(String(agentId || ''));
  const taskIdJs = JSON.stringify(String(task.id || ''));
  let html = '<div class="detail-expand-description-wrap">';
  if (editor.open) {
    html += `<textarea id="detail-description-input" class="detail-inline-description-input" rows="3"`
      + ` data-focus-key="detail-description-input:${esc(task.id || '')}"`
      + ` placeholder="Add task description..."`
      + ` oninput='agentDetailDescriptionInput(${agentIdJs},${taskIdJs},this.value)'`
      + ` onkeydown='agentDetailDescriptionKeydown(event,${agentIdJs},${taskIdJs})'>${esc(editor.draft || '')}</textarea>`;
    html += `<div class="detail-inline-editor-actions">`;
    html += `<button type="button" id="detail-description-save-btn" class="detail-inline-editor-btn detail-inline-editor-btn-primary"`
      + ` data-focus-key="detail-description-save:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailSaveDescription(${agentIdJs},${taskIdJs})'>Save</button>`;
    html += `<button type="button" id="detail-description-cancel-btn" class="detail-inline-editor-btn"`
      + ` data-focus-key="detail-description-cancel:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailCancelDescriptionEdit(${agentIdJs},${taskIdJs})'>Cancel</button>`;
    html += `</div>`;
  } else {
    html += `<div class="detail-expand-description-row">`;
    if (task.description) {
      html += `<div class="detail-expand-description">${_detailMultilineHtml(task.description)}</div>`;
    } else {
      html += `<div class="detail-expand-description detail-expand-description-empty">Add description</div>`;
    }
    html += `<button type="button" id="detail-description-edit-btn" class="detail-description-edit"`
      + ` title="${esc(title)}"`
      + ` aria-label="${esc(title)}"`
      + ` data-focus-key="detail-description-edit:${esc(task.id || '')}"`
      + ` onclick='event.stopPropagation();agentDetailEditDescription(${agentIdJs},${taskIdJs})'>&#x270E;</button>`;
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function _taskPreservedMergeDiffArtifact(task, branch) {
  const artifacts = task && Array.isArray(task.artifacts) ? task.artifacts : [];
  for (let i = 0; i < artifacts.length; i++) {
    const artifact = artifacts[i] || {};
    const metadata = artifact.metadata || {};
    if (artifact.type !== 'diff' || !metadata.preserved_on_merge) continue;
    if (branch && metadata.worktree_branch && metadata.worktree_branch !== branch) continue;
    return artifact;
  }
  return null;
}

function _preservedMergeDiffForAgent(agent) {
  if (!agent || !state || !state.board_tasks) return null;
  const repoRoot = agent.worktree_repo_root || agent.git_root || '';
  const branch = agent.worktree_branch || '';
  const branchKey = repoRoot && branch ? (repoRoot + '::' + branch) : '';
  // worktree_boundary is eager in compact-v1; filter down to tasks whose
  // boundary matches this agent's branch and hydrate only those so the
  // lazy artifacts list resolves for the clickable "merged" badge.
  if (typeof _compactHydrateTasksMatching === 'function' && branchKey) {
    _compactHydrateTasksMatching(function(t) {
      return !!t && _taskBoundaryBranchKey(t) === branchKey;
    });
  }
  let winner = null;
  let winnerArtifact = null;
  let winnerSort = '';
  for (const task of Object.values(state.board_tasks)) {
    if (!task) continue;
    const artifact = _taskPreservedMergeDiffArtifact(task, branch);
    if (!artifact) continue;
    const taskBranchKey = _taskBoundaryBranchKey(task);
    if (branchKey && taskBranchKey && taskBranchKey !== branchKey) continue;
    const boundary = _taskBoundaryMeta(task);
    const metadata = artifact.metadata || {};
    const sortValue = String(
      boundary.merged_at
      || metadata.boundary_recorded_at
      || _taskBoundarySortValue(task)
      || task.updated_at
      || task.id
      || ''
    );
    if (!winner || sortValue > winnerSort) {
      winner = task;
      winnerArtifact = artifact;
      winnerSort = sortValue;
    }
  }
  if (!winner || !winnerArtifact) return null;
  return { task: winner, artifact: winnerArtifact };
}

function _captureMainFocusKey(el) {
  if (!el || !el.dataset || !el.dataset.focusKey) return '';
  return '[data-focus-key="' + CSS.escape(el.dataset.focusKey) + '"]';
}


function _restoreActiveDetailInputFocus() {
  const active = document && document.activeElement;
  if (!active || active.id !== 'detail-description-input' || typeof active.focus !== 'function') return;
  try { active.focus({ preventScroll: true }); }
  catch (_e) { active.focus(); }
}

function _captureAgentDetailDrafts() {
  if (typeof selectedAgentId === 'undefined' || !selectedAgentId) return;
  const task = _getAgentTask(selectedAgentId);
  if (!task) return;
  const editor = _agentDetailDescriptionState(selectedAgentId, task);
  if (!editor.open) return;
  const input = document.getElementById('detail-description-input');
  if (input && 'value' in input) editor.draft = input.value;
}

const _AGENT_DONE_FLOURISH_DURATION_MS = 3400;
var _agentDoneFlourishState = {};

function _clearAgentDoneFlourish(agentId) {
  const key = String(agentId || '');
  const entry = _agentDoneFlourishState[key];
  if (!entry) return;
  if (entry.timeout_id) clearTimeout(entry.timeout_id);
  delete _agentDoneFlourishState[key];
}

function _pruneAgentDoneFlourishes(agentMap) {
  const activeAgentIds = new Set(Object.keys(agentMap || {}));
  for (const key of Object.keys(_agentDoneFlourishState)) {
    if (activeAgentIds.has(key)) continue;
    _clearAgentDoneFlourish(key);
  }
}

function _getAgentDoneFlourish(agentId) {
  const key = String(agentId || '');
  const entry = _agentDoneFlourishState[key];
  if (!entry) return null;
  const elapsedMs = Math.max(0, Date.now() - entry.started_at);
  if (elapsedMs >= entry.duration_ms) {
    _clearAgentDoneFlourish(key);
    return null;
  }
  return {
    duration_ms: entry.duration_ms,
    elapsed_ms: elapsedMs,
    label: entry.label || 'Done',
  };
}

function _startAgentDoneFlourish(agentId, label) {
  const key = String(agentId || '');
  if (!key) return;
  _clearAgentDoneFlourish(key);
  const startedAt = Date.now();
  const durationMs = _AGENT_DONE_FLOURISH_DURATION_MS;
  const entry = {
    started_at: startedAt,
    duration_ms: durationMs,
    label: label || 'Done',
    timeout_id: 0,
  };
  entry.timeout_id = setTimeout(function() {
    const current = _agentDoneFlourishState[key];
    if (!current || current.started_at !== startedAt) return;
    delete _agentDoneFlourishState[key];
    if (typeof render === 'function'
        && (typeof dragInProgress === 'undefined' || !dragInProgress)) {
      render();
    }
  }, durationMs);
  _agentDoneFlourishState[key] = entry;
}

function _agentCellSubtitle(a) {
  const task = _getAgentTask(a.id);
  if (task && task.task) return task.task;
  if (!_embeddedRuntimeEnabled()) return '';
  return a.activity_detail
    || _formatDisplayPath(
      a.current_path || a.directory || '',
      a.git_root || a.worktree_repo_root || ''
    )
    || a.command
    || '';
}

function _renderAgentCardControls(a, opts) {
  opts = opts || {};
  const id = String((a && a.id) || '');
  const closeTitle = opts.dismissed ? 'Delete dismissed engineer' : 'Delete';
  const paused = !!opts.paused;
  const pauseTitle = opts.pauseTitle || (paused ? 'Resume event delivery' : 'Pause event delivery');
  const pauseIcon = paused ? '&#x25B6;' : '&#x23F8;';
  let html = '<div class="cell-header-controls">';
  html += '<button class="cell-close" draggable="false"'
    + ' data-focus-key="agent-close:' + esc(id) + '"'
    + ' onclick="event.stopPropagation();removeAgent(\'' + esc(id) + '\')"'
    + ' title="' + esc(closeTitle) + '">\u2715</button>';
  html += '<button class="cell-engineer-toggle ' + (paused ? 'paused' : 'running') + '"'
    + ' draggable="false"'
    + ' data-focus-key="agent-digest-toggle:' + esc(id) + '"'
    + ' onclick="' + esc(opts.pauseOnclick || '') + '"'
    + ' title="' + esc(pauseTitle) + '">' + pauseIcon + '</button>';
  html += '</div>';
  return html;
}

function _renderWorkerCardBody(a) {
  const task = _agentTaskForCard(a);
  const taskId = String((task && task.id) || a.current_task_id || '').trim();
  const cycle = _taskCycleState(task, a);
  const slug = a.slug || a.name || a.id || '';
  const branch = _workerBranchLabel(a);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  let html = '<div class="agent-card-body cell-body cell-body--worker">';
  html += _agentCardTooltipHtml(slug, 14, 'cell-name cell-worker-slug', 'data-worker-slug="' + esc(slug) + '"');
  if (taskId) {
    html += '<div class="agent-card-line cell-task cell-worker-task cell-worker-task--clickable"'
      + ' onclick="event.stopPropagation(); openTaskInBoard(\'' + esc(taskId) + '\');"'
      + ' title="' + esc(taskId) + ' — open in board">'
      + esc(taskId)
      + '</div>';
  } else {
    html += '<div class="agent-card-line cell-task cell-worker-task cell-worker-task--empty">no task</div>';
  }
  html += '<div class="agent-card-line cell-worker-cycle">'
    + esc('cycle: ' + cycle)
    + '</div>';
  const diffLabel = _workerDiffLabel(a);
  if (diffLabel) {
    html += '<div class="agent-card-line cell-worker-diff">' + esc(diffLabel) + '</div>';
  }
  html += '<div class="agent-card-line cell-worker-branch">'
    + _agentCardTooltipHtml(branch, 18, 'cell-worker-branch-name', 'data-worktree-branch="' + esc(a.worktree_branch || a.current_branch || '') + '"')
    + '</div>';
  html += '<div class="agent-card-line cell-worker-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function _renderEngineerCardBody(a, askingText) {
  const workers = _workersForEngineer(a.id);
  const queueDepth = _engineerQueueDepth(a.id);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  const workerLabel = workers.length === 1 ? 'worker' : 'workers';
  let html = '<div class="agent-card-body cell-body cell-body--engineer">';
  html += '<div class="agent-card-line cell-name">' + esc(_agentDisplayName(a)) + '</div>';
  html += '<div class="agent-card-line cell-engineer-workers">'
    + '<span class="agent-card-state-mix">' + _agentStatusMixDots(workers) + '</span>'
    + '<span class="agent-card-state-count">' + esc(String(workers.length) + ' ' + workerLabel) + '</span>'
    + '</div>';
  html += '<div class="agent-card-line cell-engineer-queue">'
    + esc('queue: ' + queueDepth)
    + '</div>';
  html += '<div class="agent-card-line cell-engineer-activity" title="' + esc(actionLabel) + '">'
    + esc(actionLabel)
    + '</div>';
  if (askingText) {
    html += '<div class="agent-card-line cell-engineer-ask" title="' + esc(askingText) + '">awaiting input</div>';
  }
  html += '</div>';
  return html;
}

function _renderArchitectCellBody(a) {
  const stats = _architectStatsForCard(a, null);
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  let html = '<div class="agent-card-body cell-body cell-body--architect">';
  html += '<div class="agent-card-line cell-name">' + esc(_agentDisplayName(a)) + '</div>';
  html += '<div class="agent-card-line cell-architect-stats">'
    + esc(stats.engineerCount + ' engineers')
    + '</div>';
  html += '<div class="agent-card-line cell-architect-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function _renderGenericAgentCardBody(a) {
  const subtitle = _agentCellSubtitle(a);
  let html = '<div class="agent-card-body cell-body cell-body--generic">';
  html += '<div class="agent-card-line cell-name">' + esc(_agentDisplayName(a)) + '</div>';
  if (subtitle) {
    html += '<div class="agent-card-line cell-task" title="' + esc(subtitle) + '">' + formatCode(subtitle) + '</div>';
  } else {
    html += '<div class="agent-card-line cell-task cell-task-empty">&nbsp;</div>';
  }
  const actionLabel = _agentCardCurrentOrLastActionLabel(a);
  html += '<div class="agent-card-line cell-generic-activity" title="' + esc(actionLabel) + '">' + esc(actionLabel) + '</div>';
  html += '</div>';
  return html;
}

function renderAgentCell(a, options) {
  options = options || {};
  const active = a.session_id && a.session_id === state.active_session_id;
  const selected = a.id === selectedAgentId;
  const childCount = (state.children[a.id] || []).length;
  const doneFlourish = _getAgentDoneFlourish(a.id);
  const cls = ['cell'];
  if (active) cls.push('active');
  if (selected) cls.push('selected');
  if (a.id === focusedItemId) cls.push('focused');
  if (a.status === 'stopped') cls.push('stopped');
  const _isArchitect = (a.kind || '') === 'architect';
  const _isEngineerKind = (a.kind || '') === 'engineer';
  const _isWorker = (a.kind || '') === 'worker';
  const _isDismissed = _isLifecycleDismissedAgent(a);
  // Check if this agent is the engineer for its group
  const _gs = (state.group_settings || {})[a.group];
  const _isDesignatedEngineer = _gs && _gs.engineer_agent_id === a.id;
  // Check if engineer is awaiting human input
  const _engineerWs = _isDesignatedEngineer && state.engineer_settings
    ? state.engineer_settings[a.group] : null;
  const _engineerPaused = !!(_engineerWs && _engineerWs.paused);
  const _engineerAsking = _engineerWs && _engineerWs.pending_question;
  const _isDigestRecipient = _isEngineerKind || _isArchitect;
  const _cardDigestSettings = state.agent_digest_settings
    ? state.agent_digest_settings[String(a.id || '')] : null;
  let _digestPaused = !!(_cardDigestSettings && _cardDigestSettings.paused);
  if (!_cardDigestSettings && _isDigestRecipient && _isDesignatedEngineer) _digestPaused = _engineerPaused;
  if (_isArchitect) cls.push('architect');
  if (_isEngineerKind) cls.push('engineer');
  if (_isWorker) cls.push('worker');
  if (_isDismissed) cls.push('dismissed');

  const statusCls = _isDismissed ? 'dismissed' : agentStatusClass(a);
  const titleParts = [a.name, `(${a.status})`];
  if (_isDismissed) titleParts.push('\u2014 dismissed');
  if (a.needs_attention && a.error_message) titleParts.push(`\u2014 ${a.error_message}`);
  else if (a.activity_detail) titleParts.push(`\u2014 ${a.activity_detail}`);
  const statusClasses = ['cell-status', statusCls];
  const statusAttrs = [];
  const showDoneFlourish = !!(doneFlourish && statusCls !== 'attention');
  if (showDoneFlourish) {
    statusClasses.push('cell-status-done-flourish');
    statusAttrs.push(
      `style="--cell-status-done-duration:${doneFlourish.duration_ms}ms;--cell-status-done-delay:-${doneFlourish.elapsed_ms}ms"`
    );
  }
  if (statusCls === 'attention') {
    statusAttrs.push(`title="${esc(a.error_message || 'Needs attention')}"`);
  }

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${a.id}" data-drag-type="agent" data-drag-group="${esc(a.group)}" data-nav-id="${esc(a.id)}"`;
  if (_isDismissed) h += ` data-dismissed-at="${esc(_agentDismissedAt(a))}"`;
  h += ` onclick="onAgentClick('${a.id}')" ondblclick="onAgentDblClick('${a.id}')" oncontextmenu="onCellContextMenu(event,'${a.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${a.id}')}" title="${esc(titleParts.join(' '))}">`;
  h += `<div class="${statusClasses.join(' ')}"${statusAttrs.length ? ' ' + statusAttrs.join(' ') : ''}>`;
  if (_isDismissed) h += '\u2013';
  else if (statusCls === 'attention') h += '!';
  else if (showDoneFlourish) h += `<span class="cell-status-flourish-label">${esc(doneFlourish.label)}</span>`;
  h += `</div>`;
  if (_isDesignatedEngineer && !_isDigestRecipient && !_isWorker) {
    const engineerGroupArg = encodeURIComponent(a.group || '');
    h += _renderAgentCardControls(a, {
      dismissed: _isDismissed,
      paused: _engineerPaused,
      pauseTitle: _engineerPaused ? 'Resume Engineer event delivery' : 'Pause Engineer event delivery',
      pauseOnclick: `event.stopPropagation();engineerTogglePauseForGroup(decodeURIComponent('${engineerGroupArg}'))`,
    });
  } else {
    const digestAgentArg = encodeURIComponent(a.id || '');
    h += _renderAgentCardControls(a, {
      dismissed: _isDismissed,
      paused: _digestPaused,
      pauseTitle: _digestPaused ? 'Resume event delivery' : 'Pause event delivery',
      pauseOnclick: `event.stopPropagation();toggleDigestPauseForAgent(decodeURIComponent('${digestAgentArg}'))`,
    });
  }
  if (_isWorker) h += _renderWorkerCardBody(a);
  else if (_isEngineerKind || _isDesignatedEngineer) h += _renderEngineerCardBody(a, _engineerAsking ? _engineerWs.pending_question : '');
  else if (_isArchitect) h += _renderArchitectCellBody(a);
  else h += _renderGenericAgentCardBody(a);
  h += _renderAgentProviderBadge(a.agent_type, 'cell-provider');
  const badgeLabel = _agentKindBadgeLabel(a.kind || '', _isDismissed);
  const badgeClass = _agentKindBadgeClass(a.kind || '', _isDismissed);
  h += '<div class="agent-card-kind ' + esc(badgeClass) + '"'
    + (_isDismissed ? ' title="Dismissed ' + esc(a.kind || 'agent') + '"' : '')
    + '>' + esc(badgeLabel) + '</div>';
  if (childCount > 0) {
    h += `<div class="cell-term-count">${childCount}</div>`;
  }
  if (_isDismissed) {
    const rehireAction = _isArchitect ? 'rehireArchitect' : 'rehireEngineer';
    const rehireTitle = _isArchitect ? 'Rehire architect' : 'Rehire engineer';
    h += `<button class="cell-relaunch cell-rehire" onclick="event.stopPropagation();${rehireAction}('${a.id}')" title="${rehireTitle}">\u21BB rehire</button>`;
  } else if (a.status === 'stopped') {
    h += `<button class="cell-relaunch" onclick="event.stopPropagation();relaunchAgent('${a.id}')" title="Relaunch">\u21BB relaunch</button>`;
  }
  h += `</div>`;
  return h;
}

function renderAgentDetails(a) {
  const statusCls = agentStatusClass(a);
  const typeInfo = a.agent_type ? (AGENT_TYPE_LABELS[a.agent_type] || { label: a.agent_type }) : null;
  const detailState = _agentDetailState(a.id);
  const isArchitect = (a.kind || '') === 'architect';

  let h = `<div class="agent-details">`;
  h += `<div class="detail-hdr">`;
  h += `  <span class="detail-name">${esc(a.name)}</span>`;
  if (typeInfo && typeInfo.label) {
    h += `  <span class="detail-type">${esc(typeInfo.label)}</span>`;
  }
  h += `  <span class="detail-status ${statusCls}">`;
  if (statusCls === 'attention') h += esc(a.error_message || 'Needs attention');
  else if (statusCls === 'working') h += 'Working';
  else if (statusCls === 'idle') h += 'Idle';
  else if (statusCls === 'disconnected') h += 'Stopped';
  h += `</span>`;
  h += `</div>`;

  /* Linked task */
  const _dt = _getAgentTask(a.id);
  if (_dt) {
    const taskExpanded = !!detailState.task_expanded;
    h += `<div class="detail-row detail-row-task${taskExpanded ? ' detail-row-expanded' : ''}"><span class="detail-label">Task</span><div class="detail-val detail-val-stack">`;
    h += `<div class="detail-inline-actions">`;
    h += `<button type="button" class="detail-inline-toggle" title="${esc(taskExpanded ? 'Collapse task details' : 'Expand task details')}" data-focus-key="detail-task-toggle:${esc(a.id)}" onclick="_toggleAgentDetailTask('${esc(a.id)}')">`;
    h += `<span class="detail-task-summary" title="${esc(_dt.task)}">${formatCode(_dt.task)}</span>`;
    if (_dt.action_name) {
      h += `<span class="detail-task-action">${esc(_dt.action_name)}</span>`;
    }
    if (_dt.status) {
      h += `<span class="detail-task-status">${esc(_dt.status)}</span>`;
    } else if (_dt.lane) {
      h += `<span class="detail-task-lane">${esc(_dt.lane)}</span>`;
    }
    h += `<span class="detail-expand-caret">${taskExpanded ? '\u25BE' : '\u25B8'}</span>`;
    h += `</button>`;
    h += _detailTaskLinkHtml(_dt.id || '');
    h += `</div>`;
    if (taskExpanded) {
      h += `<div class="detail-expand-body">`;
      h += `<div class="detail-expand-title">${_detailMultilineHtml(_dt.task)}</div>`;
      h += _renderAgentDetailDescription(a.id, _dt);
      h += `</div>`;
    }
    h += `</div></div>`;
  }

  const boundaryOverview = _branchBoundaryOverviewForAgent(a);
  if (boundaryOverview && boundaryOverview.latest_boundary_task) {
    const boundaryTask = boundaryOverview.latest_boundary_task;
    const boundaryBadge = boundaryOverview.branch_advanced
      ? 'Branch advanced'
      : 'Safe review point';
    h += `<div class="detail-row"><span class="detail-label">Review point</span><span class="detail-val detail-task" title="${esc(boundaryTask.task)}">${formatCode(boundaryTask.task)}<span class="detail-task-status">${esc(boundaryBadge)}</span></span></div>`;
    if (boundaryOverview.queued_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Queued next</span><span class="detail-val">${esc(boundaryOverview.queued_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
    if (boundaryOverview.started_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Beyond boundary</span><span class="detail-val">${esc(boundaryOverview.started_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
  }

  /* MCP Messages */
  if (a.mcp_messages && a.mcp_messages.length) {
    const icons = { progress: '\u25CF', done: '\u2714', ready: '\u2714', blocked: '\u26D4', error: '\u2716', derive: '\u2934', ask: '\u2753', name: '\u270E' };
    h += `<div class="detail-row detail-row-mcp"><span class="detail-label">Messages</span>`;
    h += `<div class="mcp-log">`;
    const msgs = a.mcp_messages.slice(0, 20);
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      const ico = icons[m.action] || '\u25CF';
      const ago = _relativeTime(m.timestamp);
      const messageKey = _agentDetailMessageKey(m, i);
      const messageExpanded = !!detailState.expanded_messages[messageKey];
      h += `<div class="mcp-entry-wrap${messageExpanded ? ' expanded' : ''}">`;
      h += `<button type="button" class="mcp-entry mcp-entry-toggle mcp-${esc(m.action)}${m.message ? ' mcp-clickable' : ''}" data-focus-key="detail-message:${esc(a.id)}:${esc(messageKey)}"`;
      if (m.message) h += ` title="${esc(messageExpanded ? 'Collapse message' : 'Expand message')}" onclick="_toggleAgentDetailMessage('${esc(a.id)}','${esc(messageKey)}')"`;
      h += `><span class="mcp-icon">${ico}</span><span class="mcp-text">${esc(m.message)}</span><span class="mcp-time">${esc(ago)}</span></button>`;
      if (m.message && messageExpanded) {
        h += `<div class="mcp-entry-expanded">${_detailMultilineHtml(m.message)}</div>`;
      }
      h += `</div>`;
    }
    h += `</div></div>`;
  }

  if (isArchitect) {
    const pendingHires = _architectPendingHiresForAgent(a.id);
    if (pendingHires.length) {
      h += `<div class="detail-section"><div class="detail-section-head"><span class="detail-section-title">Pending hires</span><span class="detail-section-count">${pendingHires.length}</span></div><div class="detail-section-list">`;
      for (let i = 0; i < pendingHires.length; i++) {
        const hire = pendingHires[i] || {};
        const hireIdJs = JSON.stringify(String(hire.id || ''));
        const summaryParts = [];
        if (hire.requested_provider) summaryParts.push(String(hire.requested_provider));
        if (hire.requested_command) summaryParts.push(String(hire.requested_command));
        if (hire.requested_directory) summaryParts.push(String(hire.requested_directory));
        h += `<div class="detail-section-card">`;
        h += `<div class="detail-section-card-head"><span class="detail-section-primary" title="${esc(hire.requested_name || '')}">${esc(hire.requested_name || 'Engineer')}</span><span class="detail-task-status">pending</span></div>`;
        if (summaryParts.length) {
          const summary = summaryParts.join(' • ');
          h += `<div class="detail-section-card-meta" title="${esc(summary)}">${esc(summary)}</div>`;
        }
        if (hire.created_at) {
          h += `<div class="detail-section-card-meta">Requested ${esc(_relativeTime(hire.created_at))}</div>`;
        }
        h += `<div class="detail-section-card-actions">`;
        h += `<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary" data-focus-key="detail-pending-hire-approve:${esc(hire.id || '')}" onclick='event.stopPropagation();approvePendingHire(${hireIdJs})'>Approve</button>`;
        h += `<button type="button" class="detail-inline-editor-btn" data-focus-key="detail-pending-hire-reject:${esc(hire.id || '')}" onclick='event.stopPropagation();rejectPendingHire(${hireIdJs})'>Reject</button>`;
        h += `</div></div>`;
      }
      h += `</div></div>`;
    }
  }

  /* Branch — worktree branch takes priority, then regular git branch */
  if (a.worktree_branch) {
    const branch = a.worktree_branch.replace(/^torque\//, '');
    let branchExtra = '';
    if (a.worktree_merged) {
      const preservedDiff = _preservedMergeDiffForAgent(a);
      if (preservedDiff && preservedDiff.task && preservedDiff.artifact) {
        branchExtra += ' <button type="button" class="detail-wt-tag detail-wt-merged detail-wt-tag-button"'
          + ` title="View preserved merge diff" data-focus-key="detail-merged-diff:${esc(preservedDiff.task.id)}:${esc(preservedDiff.artifact.id || '')}"`
          + ` data-task-id="${esc(preservedDiff.task.id)}"`
          + ` data-artifact-id="${esc(preservedDiff.artifact.id || '')}"`
          + ` data-artifact-filename="${esc(preservedDiff.artifact.filename || '')}"`
          + ` data-artifact-path="${esc((preservedDiff.artifact.path || ((preservedDiff.artifact.storage || {}).path) || ''))}"`
          + ' onclick="event.stopPropagation();if(typeof openTaskArtifactById===\'function\'){openTaskArtifactById(this.dataset.taskId,this.dataset.artifactId,this.dataset.artifactFilename,this.dataset.artifactPath);}">merged</button>';
      } else {
        branchExtra += ' <span class="detail-wt-tag detail-wt-merged">merged</span>';
      }
    } else {
      branchExtra += ' <span class="detail-wt-tag">worktree</span>';
    }
    const behind = a.worktree_behind || 0;
    const ahead = a.worktree_ahead || 0;
    if (behind || ahead) {
      let parts = [];
      if (ahead) parts.push(`<span class="detail-ahead">\u2191${ahead}</span>`);
      if (behind) parts.push(`<span class="detail-behind">\u2193${behind}</span>`);
      branchExtra += ' ' + parts.join(' ');
    }
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(branch)}${branchExtra}</span></div>`;
    const diff = a.worktree_diff || {};
    if (diff.files) {
      h += `<button type="button" class="detail-row detail-row-button detail-row-clickable" data-focus-key="detail-diff:${esc(a.id)}" title="View branch diff" onclick="event.stopPropagation();if(typeof showDiffView==='function'){showDiffView('${esc(a.id)}',true);}"><span class="detail-label">Changes</span><span class="detail-val">${diff.files} file${diff.files !== 1 ? 's' : ''} <span class="detail-ins">+${diff.insertions || 0}</span> <span class="detail-del">-${diff.deletions || 0}</span></span></button>`;
    }
    if (a.worktree_checkpoints > 0) {
      h += `<button type="button" class="detail-row detail-row-button detail-row-clickable" data-focus-key="detail-history:${esc(a.id)}" title="Open checkpoint history" onclick="event.stopPropagation();if(typeof worktreeHistory==='function'){worktreeHistory('${esc(a.id)}');}"><span class="detail-label">Checkpoints</span><span class="detail-val">${a.worktree_checkpoints}</span></button>`;
    }
  } else if (a.current_branch) {
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(a.current_branch)}</span></div>`;
  }

  /* Directory */
  const directoryPath = a.current_path || a.directory || '';
  if (directoryPath) {
    const dir = _formatDisplayPath(directoryPath, a.git_root || a.worktree_repo_root || '');
    h += `<div class="detail-row"><span class="detail-label">Directory</span><span class="detail-val detail-dir" title="${esc(directoryPath)}">${esc(dir)}</span></div>`;
  }

  /* Last event */
  if (a.last_event_at > 0) {
    const ago = _relativeTime(a.last_event_at);
    const showAgo = ((Date.now() / 1000) - a.last_event_at) > 30;
    if (a.last_event_text) {
      h += `<div class="detail-row detail-row-event"><span class="detail-label">Last event</span><span class="detail-val detail-last-event">${esc(a.last_event_text)}${showAgo ? ` <span class="detail-time">(${esc(ago)})</span>` : ''}</span></span></div>`;
    } else {
      h += `<div class="detail-row"><span class="detail-label">Last event</span><span class="detail-val">${esc(ago)}</span></div>`;
    }
  }

  h += `</div>`;
  return h;
}

function _relativeTime(ts) {
  const diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function renderTermAddBtn(gname, parentId) {
  const pid = parentId ? esc(parentId) : '';
  let h = `<div class="term-row term-add" onclick="quickAddTerminal('${esc(gname)}','${pid}')">`;
  h += `<div class="term-badge" style="background:var(--border)">+</div>`;
  h += `<div class="term-info"><div class="term-name" style="color:var(--text-dim)">New terminal</div></div>`;
  h += `<button class="term-action" onclick="event.stopPropagation();toggleMenu(this)" title="Custom">\u25BE</button>`;
  h += `<div class="split-menu"><button onclick="event.stopPropagation();closeMenus();openAddTerminal('${esc(gname)}','${pid}')">Custom\u2026</button></div>`;
  h += `</div>`;
  return h;
}

function renderTerminalRow(t) {
  const active = t.session_id && t.session_id === state.active_session_id;
  const cls = ['term-row'];
  if (active) cls.push('active');
  if (t.id === focusedItemId) cls.push('focused');
  if (t.status === 'stopped') cls.push('stopped');

  const proc = t.status === 'stopped'
    ? { label: 'OFF', color: '#6e7681' }
    : processInfo(t.current_process);

  const darkCls = proc.dark ? ' dark-text' : '';
  const fullPath = t.current_path || t.directory || '';
  const pathDisplay = _formatDisplayPath(fullPath, t.git_root || t.worktree_repo_root || '');

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${t.id}" data-drag-type="terminal" data-drag-group="${esc(t.group)}" data-nav-id="${esc(t.id)}" onclick="focusAgent('${t.id}')" oncontextmenu="onCellContextMenu(event,'${t.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${t.id}')}">`;
  h += `<div class="term-badge${darkCls}" style="background:${proc.color}">${proc.label}</div>`;
  h += `<div class="term-info">`;
  h += `  <div class="term-name">${esc(t.name)}</div>`;
  if (pathDisplay) {
    h += `<div class="term-path" title="${esc(fullPath)}">${esc(pathDisplay)}</div>`;
  }
  h += `</div>`;
  h += `<div class="term-status ${t.status}"></div>`;
  h += `<div class="term-actions">`;
  if (t.status === 'stopped') {
    h += `<button class="term-action" onclick="event.stopPropagation();relaunchAgent('${t.id}')" title="Relaunch">\u21BB</button>`;
  }
  h += `<button class="term-action danger" onclick="event.stopPropagation();removeAgent('${t.id}')" title="Delete">\u2715</button>`;
  h += `</div>`;
  h += `</div>`;
  return h;
}

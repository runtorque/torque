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

function _uiRefocusRovingChoice(group, groupRole, choiceRole, choiceIndex, choice) {
  if (typeof document === 'undefined' || !group) return;
  var groupId = group.id || '';
  var groupLabel = group.getAttribute ? (group.getAttribute('aria-label') || '') : '';
  var choiceId = choice && choice.id ? choice.id : '';
  var dataKeys = ['data-tab', 'data-subtab', 'data-lane', 'data-view', 'data-value'];
  var choiceData = {};
  dataKeys.forEach(function(key) {
    var value = choice && choice.getAttribute ? choice.getAttribute(key) : null;
    if (value != null) choiceData[key] = value;
  });

  var refocus = function() {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[role="' + groupRole + '"]'));
    var liveGroup = groups.find(function(candidate) {
      if (groupId && candidate.id === groupId) return true;
      return !groupId && groupLabel && candidate.getAttribute('aria-label') === groupLabel;
    });
    if (!liveGroup) return;
    var choices = Array.prototype.slice.call(liveGroup.querySelectorAll('[role="' + choiceRole + '"]')).filter(function(candidate) {
      return !candidate.closest || candidate.closest('[role="' + groupRole + '"]') === liveGroup;
    });
    var liveChoice = choices.find(function(candidate) {
      if (choiceId && candidate.id === choiceId) return true;
      var keys = Object.keys(choiceData);
      return !choiceId && keys.length && keys.every(function(key) {
        return candidate.getAttribute(key) === choiceData[key];
      });
    }) || choices[choiceIndex];
    if (!liveChoice || typeof liveChoice.focus !== 'function') return;
    try { liveChoice.focus({ preventScroll: true }); }
    catch (_) { liveChoice.focus(); }
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(refocus);
  else if (typeof setTimeout === 'function') setTimeout(refocus, 0);
}

function uiTablistKeydown(event) {
  if (!event || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  var list = event.currentTarget;
  if (!list || typeof list.querySelectorAll !== 'function') return;
  var key = event.key;
  var vertical = list.getAttribute && list.getAttribute('aria-orientation') === 'vertical';
  var delta = 0;
  if ((!vertical && key === 'ArrowLeft') || (vertical && key === 'ArrowUp')) delta = -1;
  else if ((!vertical && key === 'ArrowRight') || (vertical && key === 'ArrowDown')) delta = 1;
  else if (key !== 'Home' && key !== 'End') return;

  var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]')).filter(function(tab) {
    if (!tab || tab.disabled || (tab.getAttribute && tab.getAttribute('aria-disabled') === 'true')) return false;
    return !tab.closest || tab.closest('[role="tablist"]') === list;
  });
  if (!tabs.length) return;
  var current = event.target && event.target.closest
    ? event.target.closest('[role="tab"]')
    : event.target;
  var index = tabs.indexOf(current);
  if (key === 'Home') index = 0;
  else if (key === 'End') index = tabs.length - 1;
  else index = (Math.max(0, index) + delta + tabs.length) % tabs.length;
  var next = tabs[index];
  if (!next) return;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof next.focus === 'function') next.focus();
  if (typeof next.click === 'function') next.click();
  _uiRefocusRovingChoice(list, 'tablist', 'tab', index, next);
}

function uiRadioGroupKeydown(event) {
  if (!event || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
  var group = event.currentTarget;
  if (!group || typeof group.querySelectorAll !== 'function') return;
  var key = event.key;
  var delta = 0;
  if (key === 'ArrowLeft' || key === 'ArrowUp') delta = -1;
  else if (key === 'ArrowRight' || key === 'ArrowDown') delta = 1;
  else if (key !== 'Home' && key !== 'End') return;
  var choices = Array.prototype.slice.call(group.querySelectorAll('[role="radio"]')).filter(function(choice) {
    return choice && !choice.disabled && (!choice.getAttribute || choice.getAttribute('aria-disabled') !== 'true');
  });
  if (!choices.length) return;
  var current = event.target && event.target.closest
    ? event.target.closest('[role="radio"]')
    : event.target;
  var index = choices.indexOf(current);
  if (key === 'Home') index = 0;
  else if (key === 'End') index = choices.length - 1;
  else index = (Math.max(0, index) + delta + choices.length) % choices.length;
  var next = choices[index];
  if (!next) return;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof next.focus === 'function') next.focus();
  if (typeof next.click === 'function') next.click();
  _uiRefocusRovingChoice(group, 'radiogroup', 'radio', index, next);
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


function _agentGroupTabsHost(root) {
  const doc = (root && root.ownerDocument)
    || (typeof document !== 'undefined' ? document : null);
  if (!doc || typeof doc.getElementById !== 'function') return null;
  return doc.getElementById('app-group-tabs-host')
    || doc.getElementById('agent-group-tabs-host')
    || (root && typeof root.querySelector === 'function'
      ? root.querySelector('[data-agent-group-tabs-host]')
      : null);
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

  if (typeof refreshStatusBar === 'function') {
    refreshStatusBar({ groupChanged: true });
  }
  if (typeof statusBarRequestDeployState === 'function') {
    statusBarRequestDeployState({ force: true });
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
    'panel-chat',
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
  const intent = meta.kind === 'architect'
    ? 'warning'
    : (meta.kind === 'engineer' ? 'accent' : 'neutral');
  const cls = _boardMetadataBadgeClass(
    'board-card-created-by board-card-created-by-' + meta.kind,
    intent,
  );
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
  if (typeof boardCloseInlineMenus === 'function') boardCloseInlineMenus();
  if (typeof boardCloseSelectionMenus === 'function') boardCloseSelectionMenus(false);
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
let _architectReorderFlip = null;

function _agentGridReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function _architectCardRects(strip) {
  const rects = {};
  if (!strip || typeof strip.querySelectorAll !== 'function') return rects;
  strip.querySelectorAll('[data-drag-id]').forEach(function(el) {
    if (!el || String(el.dataset.agentKind || '') !== 'architect') return;
    rects[String(el.dataset.dragId || '')] = el.getBoundingClientRect();
  });
  return rects;
}

function _queueArchitectReorderFlip(group, sourceOrder, expectedOrder, strip) {
  _architectReorderFlip = {
    group: String(group || ''),
    sourceOrder: (sourceOrder || []).map(String),
    expectedOrder: (expectedOrder || []).map(String),
    rects: _architectCardRects(strip),
    until: Date.now() + 3000,
  };
}

function _architectOrderMatches(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
  for (let i = 0; i < left.length; i++) {
    if (String(left[i]) !== String(right[i])) return false;
  }
  return true;
}

function _visibleArchitectOrderForGroup(group) {
  const ids = state && state.groups && Array.isArray(state.groups[group])
    ? state.groups[group]
    : [];
  return ids.filter(function(id) {
    const cell = state && state.agents ? state.agents[id] : null;
    if (!cell || String(cell.kind || '') !== 'architect') return false;
    return typeof _isTombstonedAgent !== 'function' || !_isTombstonedAgent(cell);
  }).map(String);
}

function _applyArchitectReorderFlip(main) {
  const pending = _architectReorderFlip;
  if (!pending) return false;
  if (Date.now() > pending.until) {
    _architectReorderFlip = null;
    return false;
  }
  const currentOrder = _visibleArchitectOrderForGroup(pending.group);
  if (!_architectOrderMatches(currentOrder, pending.expectedOrder)) {
    // A snapshot restoring the source order remains authoritative. Keep the
    // queued positions briefly in case the move acknowledgement is merely
    // delayed, but never leave transforms or marker classes behind.
    if (!_architectOrderMatches(currentOrder, pending.sourceOrder)) {
      _architectReorderFlip = null;
    }
    return false;
  }

  const cards = main && typeof main.querySelectorAll === 'function'
    ? main.querySelectorAll('[data-agent-kind="architect"]')
    : [];
  const reduceMotion = _agentGridReducedMotion();
  let animated = false;
  cards.forEach(function(el) {
    if (!el || String(el.dataset.dragGroup || '') !== pending.group) return;
    if (el.classList) {
      el.classList.remove('architect-dragging', 'architect-drop-before', 'architect-drop-after');
    }
    if (el.style) el.style.transform = '';
    const oldRect = pending.rects[String(el.dataset.dragId || '')];
    if (!oldRect || reduceMotion || typeof el.animate !== 'function') return;
    const nextRect = el.getBoundingClientRect();
    const dx = oldRect.left - nextRect.left;
    const dy = oldRect.top - nextRect.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    el.animate([
      { transform: `translate(${dx}px, ${dy}px)` },
      { transform: 'translate(0, 0)' },
    ], { duration: 180, easing: 'cubic-bezier(.2,.8,.2,1)' });
    animated = true;
  });
  _architectReorderFlip = null;
  return animated;
}

function _surfacePanelApp(surface) {
  if (surface === 'board') return 'board';
  if (surface === 'chat') return 'chat';
  if (surface === 'actions') return 'actions';
  if (surface === 'initiatives') return 'initiatives';
  if (surface === 'thinking') return 'thinking';
  if (surface === 'mission-control') return 'mission-control';
  if (surface === 'context') return 'context';
  if (surface === 'events') return 'events';
  if (surface === 'engineer') return 'engineer';
  if (surface === 'templates') return 'templates';
  if (surface === 'history') return 'history';
  if (surface === 'supervisor') return 'supervisor';
  if (surface === 'health') return 'health';
  if (surface === 'help') return 'help';
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
  if (surface === 'chat' && typeof renderChatPanel === 'function') renderChatPanel();
  if (surface === 'actions' && typeof renderTemplatesPanel === 'function') renderTemplatesPanel();
  if (surface === 'initiatives' && typeof renderInitiativesPanel === 'function') renderInitiativesPanel();
  if (surface === 'thinking' && typeof renderThinkingPanel === 'function') renderThinkingPanel();
  if (surface === 'mission-control' && typeof renderMissionControlPanel === 'function') renderMissionControlPanel();
  if (surface === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
  if (surface === 'events' && typeof renderEvents === 'function') renderEvents();
  if (surface === 'history' && typeof renderHistoryPanel === 'function') renderHistoryPanel();
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
  if (surface === 'health' && typeof renderHealthPanel === 'function') renderHealthPanel();
  if (surface === 'help' && typeof renderHelpPanel === 'function') renderHelpPanel();
}

function renderActivePanel() {
  const surfaces = _currentPanelSurfaces();
  for (let i = 0; i < surfaces.length; i++) _renderSurface(surfaces[i]);
  _updateEngineerTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
  if (typeof inboxUpdateBadge === 'function') inboxUpdateBadge();
}

function renderInvalidatedSurfaces(flags) {
  if (!flags) return;
  // A detached panel window owns only its own panel surface; its main grid and
  // terminal workspace are CSS-hidden. Never run the grid rebuild / FLIP or the
  // embedded-terminal render there (a hidden xterm would fit to zero size and
  // clobber the shared PTY). Drop the main/terminal/focus flags but keep the
  // panel-surface dispatch below so the visible panel updates from deltas.
  if (typeof _detachedWindowActive === 'function' && _detachedWindowActive()) {
    flags = Object.assign({}, flags, {
      main: false,
      terminal: false,
      directMessages: false,
      composer: false,
      focus: false,
    });
  }
  // TORQUE:236 v10: when the main flag fires, skip render()'s trailing
  // agent-panel refresh — the surfaces loop below already dispatches
  // `_renderSurface('engineer')` if the engineer flag is independently
  // set. This eliminates the redundant in-place panel refresh that
  // hundreds of agent_upsert pulses per second produced (cheap post-v9
  // but still wasteful + masks any future capture/restore regression).
  if (flags.main) {
    render({
      skipPanelRefresh: true,
      skipFocusRefresh: !flags.focus,
      // Terminal and DM deltas have independent component render cycles below.
      // Do not let the grid renderer turn either one back into a full workspace
      // render as a side effect of flags.main.
      skipTerminalRefresh: true,
    });
  } else if (flags.focus && typeof renderAgentFocusPanel === 'function') {
    renderAgentFocusPanel();
  }
  if (flags.terminal && typeof renderTerminalWorkspace === 'function') {
    renderTerminalWorkspace({ component: 'terminal' });
  }
  if (flags.directMessages && typeof renderTerminalWorkspace === 'function') {
    renderTerminalWorkspace({ component: 'direct-messages' });
  }
  if (flags.composer && typeof renderTerminalWorkspace === 'function') {
    renderTerminalWorkspace({ component: 'composer' });
  }
  const surfaces = _currentPanelSurfaces();
  for (let i = 0; i < surfaces.length; i++) {
    const surface = surfaces[i];
    if (surface && flags[surface]) _renderSurface(surface);
  }
  if (flags.statusbar && typeof refreshStatusBar === 'function') {
    refreshStatusBar({ delta: true });
  }
  _updateEngineerTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
  if (typeof inboxUpdateBadge === 'function') inboxUpdateBadge();
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
      // Keep the captured DOM owner out of serialized snapshots while letting
      // restore distinguish its own still-active node from another surface's
      // detached, competing focus target.
      Object.defineProperty(snapshot.focus, 'activeElement', {
        value: active,
        enumerable: false,
      });
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

function _surfaceRestoreAllowsFocus(target, expectedActive) {
  const active = document.activeElement;
  // A restore is allowed to re-focus its logical target only when the browser
  // still has no competing focus owner. `null` is deliberately not treated as
  // available: WKWebView can report it while a native control owns the
  // keyboard, and turning that unknown state into a focus command steals the
  // desktop message composer. The captured owner is also safe when it is
  // still active; the document fallback is the normal aftermath of replacing
  // a focused surface. Every other node, including a detached one, may still
  // represent a competing operator focus owner.
  if (!active) return false;
  if (active === target || active === expectedActive
      || active === document.body || active === document.documentElement) return true;
  return false;
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
  const restoreFocus = _surfaceRestoreAllowsFocus(el, snapshot.focus.activeElement || null);
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
  if (restoreFocus && typeof el.focus === 'function') {
    try { el.focus({ preventScroll: true }); }
    catch (_e) { el.focus(); }
  }
  if (restoreFocus && typeof snapshot.focus.selectionStart === 'number' && 'selectionStart' in el) {
    el.selectionStart = snapshot.focus.selectionStart;
  }
  if (restoreFocus && typeof snapshot.focus.selectionEnd === 'number' && 'selectionEnd' in el) {
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


function _workersForEngineer(engineerId) {
  const id = String(engineerId || '').trim();
  if (!id) return [];
  const index = _currentAgentGridIndex();
  if (!index) return [];
  // Read-only shared array; card renderers only iterate it.
  return index.workersByEngineerId[id] || [];
}

function _engineerQueueDepth(engineerId) {
  const id = String(engineerId || '').trim();
  if (!id) return 0;
  const index = _currentTaskLookupIndex();
  if (!index) return 0;
  return index.queueDepthByEngineerId[id] || 0;
}

function _architectEngineersForCard(architectId, section) {
  if (section && Array.isArray(section.rows)) {
    return section.rows.map(function(row) { return row && row.engineer; }).filter(Boolean);
  }
  const id = String(architectId || '').trim();
  if (!id) return [];
  const index = _currentAgentGridIndex();
  if (!index) return [];
  return index.engineersByArchitectId[id] || [];
}

function _architectPendingAskTasks(architect) {
  if (!architect) return [];
  const architectId = String(architect.id || '').trim();
  if (!architectId) return [];
  const index = _currentTaskLookupIndex();
  if (!index) return [];
  const group = String(architect.group || '').trim();
  const asks = [];
  const pool = index.pendingHumanTasks;
  for (let i = 0; i < pool.length; i++) {
    const task = pool[i];
    if (!task) continue;
    const labels = Array.isArray(task.labels) ? task.labels : [];
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


function _jsStringAttr(value) {
  return esc(JSON.stringify(String(value || '')));
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
  html += `  <span class="drawer-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">${childTerms.length}</span>`;
  html += `</div>`;
  html += `<div class="term-list">`;
  for (const t of childTerms) html += renderTerminalRow(t);
  html += `</div>`;
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
    const tabsHtml = main._torqueLastTabsHtml || '';
    main.innerHTML = _agentFocusShellHtml(gridHtml, focusHtml, tabsHtml);
    _renderAgentGroupTabsHost(tabsHtml);
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
    _agentFocusApplyPersistedSplit();
    return false;
  }
  const panelState = typeof _captureSurfaceState === 'function'
    ? _captureSurfaceState(parts.focusScroll, { scrollSelectors: [':root', '.mcp-log'] })
    : null;
  parts.focusScroll.innerHTML = focusHtml;
  parts.focusScroll._torqueLastHtml = focusHtml;
  main._torqueLastFocusHtml = focusHtml;
  main._torqueLastHtml = _agentFocusShellHtml(main._torqueLastGridHtml || '', focusHtml, main._torqueLastTabsHtml || '');
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(parts.focusScroll, panelState, { scrollSelectors: [':root', '.mcp-log'] });
    _restoreActiveDetailInputFocus();
  }
  _agentFocusApplyPersistedSplit();
  return true;
}

function _updateAgentGridSelectionForFocus(prevId, nextId) {
  const main = document.getElementById('main');
  if (!main || typeof main.querySelector !== 'function') return;
  const cssEscape = function(value) {
    const raw = String(value || '');
    if (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function') return CSS.escape(raw);
    return raw.replace(/"/g, '\\"');
  };
  const setCardState = function(card, selected, focused) {
    if (!card || !card.classList) return;
    card.classList.toggle('selected', !!selected);
    card.classList.toggle('is-selected', !!selected);
    card.classList.toggle('focused', !!focused);
  };
  if (typeof main.querySelectorAll === 'function') {
    const cards = main.querySelectorAll('[data-drag-id]');
    if (cards && cards.length) {
      for (let i = 0; i < cards.length; i++) {
        const card = cards[i];
        const cardId = card && card.getAttribute
          ? card.getAttribute('data-drag-id')
          : ((card && card.dataset && card.dataset.dragId) || '');
        setCardState(card, cardId === nextId, cardId === focusedItemId);
      }
      return;
    }
  }
  const prev = prevId
    ? main.querySelector('[data-drag-id="' + cssEscape(prevId) + '"]')
    : null;
  const next = nextId
    ? main.querySelector('[data-drag-id="' + cssEscape(nextId) + '"]')
    : null;
  const focused = focusedItemId
    ? main.querySelector('[data-drag-id="' + cssEscape(focusedItemId) + '"]')
    : null;
  setCardState(prev, false, prev === focused);
  setCardState(next, true, next === focused);
  if (focused && focused !== prev && focused !== next) setCardState(focused, false, true);
}

function refreshSelectedAgentFocus(prevSelectedId) {
  _updateAgentGridSelectionForFocus(prevSelectedId || '', selectedAgentId || '');
  if (typeof renderAgentFocusPanel === 'function') renderAgentFocusPanel();
}

function render(opts) {
  if (typeof renderGroupSwitcher === 'function') renderGroupSwitcher();
  if (typeof _torqueAgentViewMode === 'function'
      && _torqueAgentViewMode() === 'canvas'
      && typeof _torqueRenderAgentCanvas === 'function') {
    if (typeof _torqueRefreshViewToggleButtons === 'function') {
      _torqueRefreshViewToggleButtons('canvas');
    }
    return _torqueRenderAgentCanvas(opts);
  }
  if (typeof _torqueRefreshViewToggleButtons === 'function') {
    _torqueRefreshViewToggleButtons('grid');
  }
  if (_torqueUiMode() === 'toolbelt') {
    return _renderMainGrid(opts, { singleGroup: false });
  }
  return _renderMainGrid(opts, { singleGroup: _singleGroupModeEnabled() });
}

function agentStatusClass(a) {
  /* Attention overrides everything */
  if (a.needs_attention) return 'attention';
  const status = String(a.status || '').trim().toLowerCase();
  /* Disconnected (tab closed) */
  if (status === 'stopped' || status === 'error') return 'disconnected';
  /* Authoritative lifecycle state takes precedence over transient activity. */
  if (status === 'running') return 'working';
  if (status === 'idle') return 'idle';
  /* Only legacy deltas without a usable lifecycle state fall back to activity. */
  if (a.agent_type) {
    if (a.activity) return 'working';
  }
  /* Non-awareness agents / agents that haven't sent events yet */
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

  const queueDepthByEngineerId = {};
  const pendingHumanTasks = [];

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

    // Per-engineer queue depth and the (small) open human-ask pool used
    // to be rebuilt by full board scans per card per frame.
    const assignedEngineerId = String(task.assigned_engineer_id || '').trim();
    if (assignedEngineerId
        && (task.lane === 'Backlog' || task.lane === 'To Do')
        && (typeof taskIsEngineerMessageFollowup !== 'function'
            || !taskIsEngineerMessageFollowup(task))) {
      queueDepthByEngineerId[assignedEngineerId] =
        (queueDepthByEngineerId[assignedEngineerId] || 0) + 1;
    }
    const taskLabels = Array.isArray(task.labels) ? task.labels : [];
    if (taskLabels.indexOf('torque:human') >= 0
        && String(task.lane || '') !== 'Done') {
      pendingHumanTasks.push(task);
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
    queueDepthByEngineerId,
    pendingHumanTasks,
    tasks: taskValues,
  };
}

/* Agent-derived grid lookups (workers per engineer, engineers per
 * architect). Separate from the task index because it invalidates on
 * agent deltas; keyed on the agents map identity plus the state revision
 * since agent_upsert mutates the map in place. */
var _agentGridIndex = null;
var _agentGridIndexSource = null;
var _agentGridIndexRev = -1;

function _invalidateAgentGridIndex() {
  _agentGridIndex = null;
  _agentGridIndexSource = null;
  _agentGridIndexRev = -1;
}

function _buildAgentGridIndex(agents) {
  const workersByEngineerId = {};
  const engineersByArchitectId = {};
  for (const agentId in agents) {
    const agent = agents[agentId];
    if (!agent || agent.cell_type !== 'agent') continue;
    if (_isTombstonedAgent(agent)) continue;
    if ((agent.kind || '') === 'engineer') {
      const architectId = String(agent.hired_by_architect_id || '').trim();
      if (architectId) {
        if (!engineersByArchitectId[architectId]) {
          engineersByArchitectId[architectId] = [];
        }
        engineersByArchitectId[architectId].push(agent);
      }
    }
    if (_isWorkerLikeAgent(agent)) {
      const owner = String(
        agent.owner_engineer_id || agent.created_by_engineer_id || ''
      ).trim();
      if (owner) {
        if (!workersByEngineerId[owner]) workersByEngineerId[owner] = [];
        workersByEngineerId[owner].push(agent);
      }
    }
  }
  for (const engineerId in workersByEngineerId) {
    workersByEngineerId[engineerId].sort(function(a, b) {
      return String(a.id || '').localeCompare(String(b.id || ''));
    });
  }
  return { workersByEngineerId, engineersByArchitectId };
}

function _currentAgentGridIndex() {
  if (!state || !state.agents) return null;
  const rev = (typeof _torqueStateRevision !== 'undefined')
    ? _torqueStateRevision : -1;
  if (!_agentGridIndex
      || _agentGridIndexSource !== state.agents
      || _agentGridIndexRev !== rev
      || rev < 0) {
    _agentGridIndex = _buildAgentGridIndex(state.agents);
    _agentGridIndexSource = state.agents;
    _agentGridIndexRev = rev;
  }
  return _agentGridIndex;
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

function _worktreePrNormalizeState(value, pending) {
  let state = String(value || '').trim().toLowerCase();
  if (!state && pending === true) return 'auto_merge_enabled';
  const aliases = {
    created: 'open',
    failed: 'blocked',
    merge_failed: 'blocked',
    pending: 'auto_merge_enabled',
  };
  return aliases[state] || state;
}

function _worktreePrMetadataFromBoundary(boundary) {
  if (!boundary || typeof boundary !== 'object') return {};
  let raw = boundary.pr;
  if (!raw || typeof raw !== 'object') raw = boundary.pull_request;
  if (!raw || typeof raw !== 'object') raw = {};

  let pending = null;
  if (Object.prototype.hasOwnProperty.call(raw, 'pending')) pending = !!raw.pending;
  else if (Object.prototype.hasOwnProperty.call(boundary, 'pr_pending')) pending = !!boundary.pr_pending;

  const rawState = _worktreePrNormalizeState(raw.state, pending);
  const statusState = _worktreePrNormalizeState(
    raw.status || boundary.pr_status,
    pending
  );
  let state = rawState || statusState || _worktreePrNormalizeState(boundary.pr_state, pending);
  if ((statusState === 'auto_merge_enabled' || statusState === 'blocked' || statusState === 'merged')
      && (!rawState || rawState === 'open')) {
    state = statusState;
  }

  const pr = {
    url: String(raw.url || boundary.pr_url || '').trim(),
    number: raw.number != null && raw.number !== ''
      ? raw.number
      : (boundary.pr_number != null && boundary.pr_number !== '' ? boundary.pr_number : ''),
    state,
    merge_state: String(raw.merge_state || boundary.pr_merge_state || '').trim(),
    head_sha: String(raw.head_sha || boundary.pr_head_sha || '').trim(),
  };

  const hasMetadata = pr.url || pr.number !== '' || pr.state || pr.merge_state || pr.head_sha;
  return hasMetadata ? pr : {};
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

function _architectDecisionsForAgent(agentId) {
  if (!state || !state.decisions) return [];
  const architectId = String(agentId || '');
  return Object.values(state.decisions).filter(function(decision) {
    return String((decision && decision.architect_id) || '') === architectId
      && !(decision && decision.archived);
  }).sort(function(a, b) {
    const aTs = _agentCardTimestampSeconds((a && (a.created_at || a.updated_at)) || 0);
    const bTs = _agentCardTimestampSeconds((b && (b.created_at || b.updated_at)) || 0);
    if (aTs !== bTs) return bTs - aTs;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
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


function _relativeTime(ts) {
  const diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _askReplyTargetForTask(task) {
  if (!task || !state) return { id: '', agent: null };
  var targetId = String(task.reply_agent_id || '').trim();
  if (!targetId && task.labels && task.labels.indexOf('architect-ask') >= 0) {
    targetId = String(task.created_by_architect_id || '').trim();
  }
  if (!targetId && task.parent_task_id && state.board_tasks) {
    var parent = state.board_tasks[task.parent_task_id];
    targetId = String((parent && parent.agent_id) || '').trim();
  }
  return {
    id: targetId,
    agent: targetId && state.agents ? (state.agents[targetId] || null) : null,
  };
}

function _askTargetAvailability(task) {
  var target = _askReplyTargetForTask(task);
  var agent = target.agent;
  var reason = '';
  if (!target.id || !agent) reason = 'target_not_found';
  else if (agent.cell_type !== 'agent') reason = 'target_not_agent';
  else if ((typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent))
      || Number(agent.deleted_at || 0) > 0) reason = 'agent_tombstoned';
  else if (Number(agent.dismissed_at || 0) > 0) reason = 'agent_dismissed';
  else if (!String(agent.session_id || '').trim()) reason = 'no_session';
  else if (['idle', 'running'].indexOf(String(agent.status || '').trim()) < 0) {
    reason = 'session_not_active';
  }
  return {
    answerable: !reason,
    reason: reason,
    target_id: target.id,
    agent: agent,
  };
}

var _askResolvePending = {};
var _askResolveRequestSequence = 0;

function _sendAskResolve(taskId, answer, source) {
  for (var pendingId in _askResolvePending) {
    if (_askResolvePending[pendingId].task_id === taskId) return false;
  }
  _askResolveRequestSequence += 1;
  var requestId = 'ask-resolve-' + _askResolveRequestSequence;
  _askResolvePending[requestId] = {
    task_id: taskId,
    answer: answer,
    source: source,
  };
  if (send({
    cmd: 'resolve_ask', id: taskId, answer: answer, request_id: requestId,
  }) === false) {
    delete _askResolvePending[requestId];
    return false;
  }
  return true;
}

function handleAskResolveResponse(msg) {
  if (!msg || msg.command !== 'resolve_ask' || !msg.request_id) return false;
  var pending = _askResolvePending[msg.request_id];
  if (!pending) return false;
  delete _askResolvePending[msg.request_id];
  if (msg.type === 'error') return false;
  if (typeof _eventsResolveDrafts !== 'undefined') {
    delete _eventsResolveDrafts[pending.task_id];
  }
  if (pending.source === 'modal') {
    var modal = document.getElementById('modal-resolve');
    if (modal && modal.dataset.taskId === pending.task_id
        && typeof closeModals === 'function') closeModals();
  } else {
    var textarea = document.getElementById('events-resolve-' + pending.task_id);
    if (textarea) textarea.value = '';
  }
  return true;
}

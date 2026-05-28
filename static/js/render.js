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
var AGENT_FOCUS_MODE_STORAGE_KEY = 'agent_focus_panel_mode';
var AGENT_FOCUS_COLLAPSED_STORAGE_KEY = 'agent_focus_panel_collapsed';
var AGENT_FOCUS_DEFAULT_FRACTION = 0.30;
var AGENT_FOCUS_MIN_HEIGHT = 120;
var AGENT_GRID_MIN_HEIGHT = 200;
var AGENT_FOCUS_AUTO_MAX_VIEWPORT_FRACTION = 0.45;
var AGENT_FOCUS_CLICK_MAX_DISPLACEMENT = 4;
var AGENT_FOCUS_CLICK_MAX_DURATION_MS = 500;
var AGENT_FOCUS_DRAG_CLICK_SUPPRESS_MS = 800;
var _agentFocusResize = null;
var _agentFocusResizeRaf = 0;
var _agentFocusResizePendingHeight = 0;
var _agentFocusLastPress = null;
var _agentFocusSuppressClickUntil = 0;

function _agentFocusStorageKey(name) {
  const runtime = (state && state.runtime) || {};
  const profile = String(runtime.profile || '');
  const port = String(runtime.port || ((typeof location !== 'undefined' && location.host) || ''));
  return 'torque.' + name + ':' + profile + ':' + port;
}

function _agentFocusReadStorage(name, fallback) {
  if (typeof localStorage === 'undefined') return fallback;
  try {
    const value = localStorage.getItem(_agentFocusStorageKey(name));
    return value === null || value === undefined ? fallback : value;
  } catch (_e) {
    return fallback;
  }
}

function _agentFocusWriteStorage(name, value) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(_agentFocusStorageKey(name), String(value));
  } catch (_e) {}
}

function _agentFocusMode() {
  return _agentFocusReadStorage(AGENT_FOCUS_MODE_STORAGE_KEY, 'auto') === 'manual'
    ? 'manual'
    : 'auto';
}

function _agentFocusSetMode(mode) {
  _agentFocusWriteStorage(
    AGENT_FOCUS_MODE_STORAGE_KEY,
    mode === 'manual' ? 'manual' : 'auto'
  );
}

function _agentFocusIsCollapsed() {
  return _agentFocusReadStorage(AGENT_FOCUS_COLLAPSED_STORAGE_KEY, '0') === '1';
}

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
  const tabsHost = _agentGroupTabsHost(root);
  const grid = root.querySelector('[data-agent-grid-pane]');
  const handle = root.querySelector('[data-agent-focus-resizer]');
  const focus = root.querySelector('[data-agent-focus-panel]');
  const focusScroll = root.querySelector('[data-agent-focus-scroll]');
  if (!split || !grid || !handle || !focus || !focusScroll) return null;
  return { root, split, tabsHost, grid, handle, focus, focusScroll };
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

function _agentFocusViewportHeight(totalHeight) {
  const win = (typeof window !== 'undefined' && window) ? window : null;
  const viewport = win ? Number(win.innerHeight) : 0;
  return Number.isFinite(viewport) && viewport > 0
    ? viewport
    : Math.max(0, Number(totalHeight) || 0);
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

function _agentFocusAutoMaxHeight(totalHeight, handleHeight) {
  const total = Math.max(0, Number(totalHeight) || 0);
  const handle = Math.max(0, Number(handleHeight) || 0);
  const viewport = _agentFocusViewportHeight(total);
  let cap = Math.max(
    AGENT_FOCUS_MIN_HEIGHT,
    Math.round((viewport || AGENT_FOCUS_MIN_HEIGHT) * AGENT_FOCUS_AUTO_MAX_VIEWPORT_FRACTION)
  );
  if (total > 0) {
    cap = Math.min(cap, Math.max(AGENT_FOCUS_MIN_HEIGHT, total - handle - AGENT_GRID_MIN_HEIGHT));
  }
  return Math.max(AGENT_FOCUS_MIN_HEIGHT, cap);
}

function _agentFocusStylePx(el, prop) {
  if (!el || typeof getComputedStyle !== 'function') return 0;
  const raw = getComputedStyle(el)[prop];
  const value = parseFloat(raw);
  return Number.isFinite(value) ? value : 0;
}

function _agentFocusContentHeight(parts) {
  if (!parts || !parts.focusScroll) return AGENT_FOCUS_MIN_HEIGHT;
  const children = parts.focusScroll.children || [];
  if (children.length) {
    let measured = _agentFocusStylePx(parts.focusScroll, 'paddingTop')
      + _agentFocusStylePx(parts.focusScroll, 'paddingBottom');
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (!child) continue;
      const rect = child.getBoundingClientRect ? child.getBoundingClientRect() : null;
      const childHeight = rect && rect.height
        ? rect.height
        : (Number(child.offsetHeight) || Number(child.scrollHeight) || Number(child.clientHeight) || 0);
      measured += Math.max(0, childHeight)
        + _agentFocusStylePx(child, 'marginTop')
        + _agentFocusStylePx(child, 'marginBottom');
    }
    if (measured > 0) return Math.max(AGENT_FOCUS_MIN_HEIGHT, Math.ceil(measured));
  }
  const scroll = Number(parts.focusScroll.scrollHeight) || 0;
  const offset = Number(parts.focusScroll.offsetHeight) || 0;
  const client = Number(parts.focusScroll.clientHeight) || 0;
  return Math.max(AGENT_FOCUS_MIN_HEIGHT, Math.ceil(scroll || offset || client || 0));
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

function _agentFocusApplyAutoHeight(parts, opts) {
  opts = opts || {};
  parts = parts || _agentFocusSplitParts();
  if (!parts) return 0;
  const total = opts.totalHeight || _agentFocusContainerHeight(parts);
  const handle = opts.handleHeight || _agentFocusHandleHeight(parts);
  const wanted = Math.min(
    _agentFocusContentHeight(parts),
    _agentFocusAutoMaxHeight(total, handle)
  );
  return _agentFocusApplyHeight(wanted, {
    totalHeight: total,
    handleHeight: handle,
  });
}

function _agentFocusSyncCollapsedUi(parts, collapsed) {
  if (!parts) return;
  if (parts.split && parts.split.classList) {
    parts.split.classList.toggle('agent-split--focus-collapsed', !!collapsed);
  }
  if (parts.handle) {
    parts.handle.setAttribute('role', collapsed ? 'button' : 'separator');
    parts.handle.setAttribute('aria-label', collapsed ? 'Expand focus panel' : 'Resize or collapse focus panel');
    parts.handle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

function _agentFocusApplyPersistedSplit() {
  const parts = _agentFocusSplitParts();
  if (!parts) return;
  const collapsed = _agentFocusIsCollapsed();
  _agentFocusSyncCollapsedUi(parts, collapsed);
  if (collapsed) return;
  if (_agentFocusMode() !== 'manual') {
    _agentFocusApplyAutoHeight(parts);
    return;
  }
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

function _agentFocusSetCollapsed(collapsed) {
  collapsed = !!collapsed;
  _agentFocusWriteStorage(AGENT_FOCUS_COLLAPSED_STORAGE_KEY, collapsed ? '1' : '0');
  if (!collapsed) _agentFocusSetMode('auto');
  const parts = _agentFocusSplitParts();
  if (!parts) return;
  _agentFocusSyncCollapsedUi(parts, collapsed);
  if (!collapsed) _agentFocusApplyAutoHeight(parts);
}

function _agentFocusNow() {
  return (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
}

function _agentFocusResizerClick(event) {
  const now = _agentFocusNow();
  if (_agentFocusSuppressClickUntil && now <= _agentFocusSuppressClickUntil) {
    _agentFocusSuppressClickUntil = 0;
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    return;
  }
  _agentFocusSuppressClickUntil = 0;
  const press = _agentFocusLastPress;
  if (press && now - press.endedAt <= AGENT_FOCUS_DRAG_CLICK_SUPPRESS_MS) {
    if (
      press.dragged
      || press.distance > AGENT_FOCUS_CLICK_MAX_DISPLACEMENT
      || press.duration > AGENT_FOCUS_CLICK_MAX_DURATION_MS
    ) {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      return;
    }
  }
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  _agentFocusSetCollapsed(!_agentFocusIsCollapsed());
}

function _agentFocusResizerKeydown(event) {
  const key = event && (event.key || event.code);
  if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar' && key !== 'Space') return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  _agentFocusSetCollapsed(!_agentFocusIsCollapsed());
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
  if (event && event.button !== undefined && event.button !== 0) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const startY = event ? event.clientY : 0;
  const startedAt = _agentFocusNow();
  _agentFocusLastPress = {
    startY,
    endY: startY,
    startedAt,
    endedAt: startedAt,
    duration: 0,
    distance: 0,
    dragged: false,
  };
  if (_agentFocusIsCollapsed()) return;
  const totalHeight = _agentFocusContainerHeight(parts);
  const handleHeight = _agentFocusHandleHeight(parts);
  const focusRect = parts.focus.getBoundingClientRect ? parts.focus.getBoundingClientRect() : { height: 0 };
  _agentFocusResizePendingHeight = 0;
  _agentFocusResize = {
    startY,
    startedAt,
    dragging: false,
    startHeight: focusRect.height || parts.focus.offsetHeight || (totalHeight * _agentFocusPersistedFraction()),
    totalHeight,
    handleHeight,
  };
  document.addEventListener('mousemove', _agentFocusResizeMove);
  document.addEventListener('mouseup', _agentFocusResizeEnd);
}

function _agentFocusResizeMove(event) {
  if (!_agentFocusResize) return;
  const dy = (event ? event.clientY : 0) - _agentFocusResize.startY;
  if (!_agentFocusResize.dragging) {
    if (Math.abs(dy) <= AGENT_FOCUS_CLICK_MAX_DISPLACEMENT) return;
    _agentFocusResize.dragging = true;
    _agentFocusSuppressClickUntil = _agentFocusNow() + AGENT_FOCUS_DRAG_CLICK_SUPPRESS_MS;
    if (document && document.body && document.body.classList) {
      document.body.classList.add('agent-focus-resizing');
      document.body.style.cursor = 'ns-resize';
    }
  }
  if (_agentFocusLastPress) {
    _agentFocusLastPress.dragged = true;
    _agentFocusLastPress.distance = Math.max(_agentFocusLastPress.distance || 0, Math.abs(dy));
  }
  _agentFocusScheduleResizeHeight(_agentFocusResize.startHeight - dy);
}

function _agentFocusResizeEnd(event) {
  if (!_agentFocusResize) return;
  const resize = _agentFocusResize;
  _agentFocusResize = null;
  document.removeEventListener('mousemove', _agentFocusResizeMove);
  document.removeEventListener('mouseup', _agentFocusResizeEnd);
  if (document && document.body && document.body.classList) {
    document.body.classList.remove('agent-focus-resizing');
    document.body.style.cursor = '';
  }
  const endY = event && Number.isFinite(Number(event.clientY)) ? Number(event.clientY) : resize.startY;
  if (_agentFocusLastPress) {
    const distance = Math.abs(endY - resize.startY);
    _agentFocusLastPress.endY = endY;
    _agentFocusLastPress.endedAt = _agentFocusNow();
    _agentFocusLastPress.duration = Math.max(0, _agentFocusLastPress.endedAt - resize.startedAt);
    _agentFocusLastPress.distance = Math.max(_agentFocusLastPress.distance || 0, distance);
    _agentFocusLastPress.dragged = !!resize.dragging;
  }
  if (!resize.dragging) {
    _agentFocusResizePendingHeight = 0;
    return;
  }
  const applied = _agentFocusApplyHeight(_agentFocusResizePendingHeight || resize.startHeight, {
    totalHeight: resize.totalHeight,
    handleHeight: resize.handleHeight,
  });
  const available = Math.max(1, (resize.totalHeight || 0) - (resize.handleHeight || 0));
  const fraction = _agentFocusClampFraction(applied / available);
  _agentFocusSetMode('manual');
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
  if (surface === 'chat') return 'chat';
  if (surface === 'actions') return 'actions';
  if (surface === 'context') return 'context';
  if (surface === 'events') return 'events';
  if (surface === 'engineer') return 'engineer';
  if (surface === 'templates') return 'templates';
  if (surface === 'history') return 'history';
  if (surface === 'supervisor') return 'supervisor';
  if (surface === 'health') return 'health';
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


function _renderAgentGridNewToolbar(groupName, disabled) {
  const group = String(groupName || '').trim();
  if (!group) return '';
  const groupArg = _jsStringAttr(group);
  return '<div class="agent-grid-toolbar" data-agent-grid-toolbar>'
    + '<button type="button" class="agent-grid-new-btn" data-agent-grid-new-button'
    + ' data-group="' + esc(group) + '"'
    + (disabled ? ' disabled aria-disabled="true" title="Agent limit reached"' : ' aria-haspopup="menu" aria-expanded="false" title="Create a standalone agent"')
    + (disabled ? '' : ' onclick="openAgentGridNewMenu(event,' + groupArg + ')"')
    + '>+ New</button>'
    + '</div>';
}

function _agentGridNewToolbarForContexts(groupContexts) {
  const contexts = Array.isArray(groupContexts) ? groupContexts.filter(Boolean) : [];
  if (!contexts.length) return '';
  const active = (typeof _activeGroup === 'function') ? String(_activeGroup() || '') : '';
  let target = active ? contexts.find(ctx => ctx && ctx.gname === active) : null;
  if (!target) target = contexts.find(ctx => ctx && !ctx.collapsed) || contexts[0];
  return _renderAgentGridNewToolbar(target.gname || '', !!target.atAgentCap);
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

  const agentItem = function(agent, kind) {
    if (!agent || !agent.id) return null;
    return {
      id: agent.id,
      type: 'agent',
      agentKind: kind || agent.kind || 'agent',
    };
  };

  for (const ctx of groupContexts) {
    model.navGroupOrder.push(ctx.gname);
    const groupNav = [];
    if (!ctx.collapsed) {
      const firstRowIndex = model.gridRows.length;
      const layout = ctx.agentLayout || {};

      for (const section of layout.architects || []) {
        if (!section || !section.architect) continue;
        const sectionKey = _agentGridSectionKey(section);
        const rows = Array.isArray(section.rows) ? section.rows : [];
        if (rows.length) {
          for (let sectionRowIndex = 0; sectionRowIndex < rows.length; sectionRowIndex++) {
            const row = rows[sectionRowIndex];
            if (!row || !row.engineer) continue;
            const rowItems = [];
            if (sectionRowIndex === 0) rowItems.push(agentItem(section.architect, 'architect'));
            rowItems.push(agentItem(row.engineer, 'engineer'));
            for (const worker of row.workers || []) rowItems.push(agentItem(worker, 'worker'));
            addRow({
              group: ctx.gname,
              sectionKey,
              rowKey: sectionKey + ':engineer:' + String(row.engineer.id || ''),
              rowType: 'engineer-row',
              architectId: section.architect.id || '',
              engineerId: row.engineer.id,
              items: rowItems,
            });
          }
        } else {
          const rowItems = [agentItem(section.architect, 'architect')];
          addRow({
            group: ctx.gname,
            sectionKey,
            rowKey: sectionKey + ':empty',
            rowType: 'architect-empty-row',
            architectId: section.architect.id || '',
            items: rowItems,
          });
        }
      }

      const userSection = layout.userSection || {
        key: 'user',
        type: 'user',
        architect: null,
        looseWorkers: [],
        rows: [],
      };
      for (const row of userSection.rows || []) {
        if (!row || !row.engineer) continue;
        const workerItems = (row.workers || []).map(worker => agentItem(worker, 'worker'));
        const rowItems = [agentItem(row.engineer, 'engineer')].concat(workerItems);
        addRow({
          group: ctx.gname,
          sectionKey: 'user',
          rowKey: 'user:engineer:' + String(row.engineer.id || ''),
          rowType: 'engineer-row',
          architectId: '',
          engineerId: row.engineer.id,
          items: rowItems,
        });
      }

      const looseWorkerItems = (userSection.looseWorkers || []).map(worker => agentItem(worker, 'worker'));

      if (looseWorkerItems.length) {
        addRow({
          group: ctx.gname,
          sectionKey: 'workers',
          rowKey: 'workers:standalone-workers',
          rowType: 'standalone-workers-row',
          items: looseWorkerItems,
        });
      }

      for (let rowIndex = firstRowIndex; rowIndex < model.gridRows.length; rowIndex++) {
        const row = model.gridRows[rowIndex];
        if (row.group !== ctx.gname) continue;
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

function _legacyPrincipalFocusTarget(currentFocusedId, navModel) {
  const raw = String(currentFocusedId || '');
  if (!raw || raw.indexOf('principal:') !== 0 || !navModel) return '';
  const lastColon = raw.lastIndexOf(':');
  if (lastColon <= 'principal:'.length - 1) return '';
  const group = raw.slice('principal:'.length, lastColon);
  const tail = raw.slice(lastColon + 1);
  if (tail && tail !== 'user' && navModel.itemMeta[tail]) return tail;
  const groupRows = (navModel.gridRows || []).filter(row => row.group === group);
  const userAgent = _navModelFirstAgentItemInRows(groupRows, row =>
    row.sectionKey === 'user');
  if (userAgent) return userAgent;
  const workerAgent = _navModelFirstAgentItemInRows(groupRows, row =>
    row.sectionKey === 'workers');
  if (workerAgent) return workerAgent;
  return '';
}

function _resolveFocusedItemForGridRender(currentFocusedId, navModel) {
  if (!currentFocusedId || !navModel) return currentFocusedId || null;
  if (navModel.itemMeta[currentFocusedId] || navModel.navItems.includes(currentFocusedId)) {
    return currentFocusedId;
  }

  const legacyPrincipalTarget = _legacyPrincipalFocusTarget(currentFocusedId, navModel);
  if (legacyPrincipalTarget) return legacyPrincipalTarget;

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

function _agentFocusShellHtml(gridHtml, focusHtml, tabsHtml) {
  const collapsed = _agentFocusIsCollapsed();
  return '<div class="agent-split' + (collapsed ? ' agent-split--focus-collapsed' : '') + '" data-agent-split>'
    + '<div id="agent-grid-pane" class="agents-grid-pane" data-agent-grid-pane>'
    + (gridHtml || '')
    + '</div>'
    + '<div id="agent-focus-resizer" class="agent-focus-resizer" role="' + (collapsed ? 'button' : 'separator') + '"'
    + ' aria-orientation="horizontal" aria-expanded="' + (collapsed ? 'false' : 'true') + '"'
    + ' aria-label="' + (collapsed ? 'Expand focus panel' : 'Resize or collapse focus panel') + '" tabindex="0"'
    + ' data-agent-focus-resizer onmousedown="_agentFocusResizeStart(event)" onclick="_agentFocusResizerClick(event)"'
    + ' onkeydown="_agentFocusResizerKeydown(event)">'
    + '<div class="agent-focus-resizer-grip" aria-hidden="true"></div>'
    + '<span class="agent-focus-reopen-label" data-agent-focus-reopen-label>Focus panel hidden — click to expand</span>'
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
  const tabsHtml = opts.tabsHtml || '';
  const renderFocus = opts.renderFocus !== false;
  const focusHtml = renderFocus
    ? _renderAgentFocusPanelHtml()
    : (main._torqueLastFocusHtml || _renderAgentFocusPanelHtml());
  const combined = _agentFocusShellHtml(gridHtml, focusHtml, tabsHtml);
  const gridChanged = main._torqueLastGridHtml !== gridHtml;
  const tabsChanged = main._torqueLastTabsHtml !== tabsHtml;
  const parts = _agentFocusSplitParts(main);
  const shellMissing = !main._torqueHasAgentSplitShell;
  const tabsHostChanged = _renderAgentGroupTabsHost(tabsHtml);
  if (shellMissing || (gridChanged && !parts)) {
    main.innerHTML = combined;
    main._torqueHasAgentSplitShell = true;
    main._torqueLastTabsHtml = tabsHtml;
    main._torqueLastGridHtml = gridHtml;
    main._torqueLastFocusHtml = focusHtml;
    main._torqueLastHtml = combined;
    _agentFocusApplyPersistedSplit();
    return { mainHtmlChanged: true, focusHtmlChanged: renderFocus };
  }
  if (tabsChanged) {
    main._torqueLastTabsHtml = tabsHtml;
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
      main._torqueLastTabsHtml || tabsHtml,
    );
    return { mainHtmlChanged: true, focusHtmlChanged: focusChanged };
  }
  main._torqueLastHtml = combined;
  if (renderFocus) renderAgentFocusPanel({ main, focusHtml });
  return { mainHtmlChanged: false, tabsHostChanged, focusHtmlChanged: renderFocus && main._torqueLastFocusHtml !== focusHtml };
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
    const result = _renderAgentGridAndFocus(main, emptyHtml, {
      tabsHtml: _renderAgentGroupTabsHtml(),
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
    const agentLayout = _buildStratifiedAgentGridModel(cells.agents);
    groupContexts.push({
      gname,
      aids: cells.aids,
      agents: cells.agents,
      standaloneTerms: cells.standaloneTerms,
      wid: cells.wid,
      gsLocal,
      collapsed,
      agentLayout,
      atAgentCap: gsLocal.max_agents > 0 && cells.agents.length >= gsLocal.max_agents,
    });
  }

  const navModel = _buildAgentGridNavigationModel(groupContexts);
  focusedItemId = _resolveFocusedItemForGridRender(focusedItemId, navModel);

  let html = _agentGridNewToolbarForContexts(groupContexts);
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
    const renderCellForGrid = function(a) {
      return renderAgentCell(a, { visibleEngineerIds, visibleAgentById });
    };
    html += _renderStratifiedAgentGrid(
      gname,
      agentLayout,
      renderCellForGrid,
      { disabled: ctx.atAgentCap },
    );

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
  const mainRenderResult = _renderAgentGridAndFocus(main, html, {
    tabsHtml: _renderAgentGroupTabsHtml(),
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
  const status = String(a.status || '').trim().toLowerCase();
  /* Disconnected (tab closed) */
  if (status === 'stopped' || status === 'error') return 'disconnected';
  /*
   * A live session is working even when an awareness adapter has not emitted
   * a transient activity value yet (for example while Codex is thinking
   * before its first tool hook).  The hover tooltip already uses status, so
   * keep the dot aligned with that source of truth.
   */
  if (status === 'running') return 'working';
  /* For legacy awareness-agent deltas, activity can still indicate work */
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
    return String((decision && decision.architect_id) || '') === architectId;
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

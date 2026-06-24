var AGENT_FOCUS_SPLIT_KEY = 'engineer_panel_split_fraction';
var AGENT_FOCUS_MODE_STORAGE_KEY = 'agent_focus_panel_mode';
var AGENT_FOCUS_COLLAPSED_STORAGE_KEY = 'agent_focus_panel_collapsed';
var AGENT_FOCUS_SELECTED_AGENT_STORAGE_KEY = 'agent_focus_selected_agent';
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


function _agentFocusRemoveStorage(name) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.removeItem(_agentFocusStorageKey(name));
  } catch (_e) {}
}

function _agentFocusPersistedSelectionRaw() {
  return String(_agentFocusReadStorage(AGENT_FOCUS_SELECTED_AGENT_STORAGE_KEY, '') || '').trim();
}

function _agentFocusWritePersistedSelectedAgentId(agentId) {
  var id = String(agentId || '').trim();
  if (!id) {
    _agentFocusRemoveStorage(AGENT_FOCUS_SELECTED_AGENT_STORAGE_KEY);
    return '';
  }
  _agentFocusWriteStorage(AGENT_FOCUS_SELECTED_AGENT_STORAGE_KEY, id);
  return id;
}

function _agentFocusRootAgentId(agentId) {
  var id = String(agentId || '').trim();
  if (!id || !state || !state.agents) return '';
  var cell = state.agents[id] || null;
  if (!cell) return '';
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) return '';
  if (cell.cell_type === 'terminal') {
    var parentId = String(cell.parent_id || '').trim();
    if (!parentId || !state.agents[parentId]) return '';
    var parent = state.agents[parentId];
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(parent)) return '';
    return String(parent.id || parentId || '').trim();
  }
  return String(cell.id || id || '').trim();
}

function _agentFocusEmbeddedRuntimeEnabled() {
  if (typeof _embeddedRuntimeEnabled === 'function') return !!_embeddedRuntimeEnabled();
  if (typeof isEmbeddedTerminalMode === 'function') return !!isEmbeddedTerminalMode();
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function _agentFocusVisibleRootAgentId(agentId) {
  var rootId = _agentFocusRootAgentId(agentId);
  if (!rootId || !state || !state.agents) return '';
  var root = state.agents[rootId] || null;
  if (!root) return '';
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(root)) return '';
  var group = String(root.group || '').trim();
  if (state.groups && Object.keys(state.groups).length) {
    if (!group || !Object.prototype.hasOwnProperty.call(state.groups, group)) return '';
  }
  if (typeof _visibleGroupCellsForGrid === 'function'
      && typeof getFilterByWindow === 'function'
      && group) {
    var cells = _visibleGroupCellsForGrid(group, _agentFocusEmbeddedRuntimeEnabled());
    if (cells && cells.hiddenByWindow) return '';
    var visibleAgents = (cells && Array.isArray(cells.agents)) ? cells.agents : [];
    var found = visibleAgents.some(function(agent) {
      return agent && String(agent.id || '') === rootId;
    });
    if (!found) return '';
  }
  return rootId;
}

function _agentFocusPersistExplicitSelection(agentId) {
  var rootId = _agentFocusVisibleRootAgentId(agentId);
  if (!rootId) return '';
  return _agentFocusWritePersistedSelectedAgentId(rootId);
}

function _agentFocusApplySelectionId(agentId, opts) {
  var rootId = _agentFocusVisibleRootAgentId(agentId);
  if (!rootId) return '';
  opts = opts || {};
  var activeSelection = opts.activeSessionSelection || null;
  var targetId = rootId;
  if (activeSelection
      && activeSelection.cell
      && activeSelection.cell.cell_type === 'terminal'
      && String(activeSelection.agentId || '') === rootId) {
    targetId = activeSelection.terminalId || rootId;
  }
  if (typeof _applySelectedAgentFromServer === 'function') {
    var applied = _applySelectedAgentFromServer(targetId, {
      syncGroup: true,
      persist: false,
    });
    return applied || rootId;
  }
  if (state) state.selected_agent_id = rootId;
  if (typeof selectedAgentId !== 'undefined') selectedAgentId = rootId;
  if (typeof focusedItemId !== 'undefined') focusedItemId = targetId;
  return rootId;
}

function _agentFocusUrlParams(searchText) {
  var params = null;
  if (typeof URLSearchParams === 'function') {
    try {
      params = new URLSearchParams(searchText || '');
    } catch (_e) {
      params = null;
    }
  }
  return params;
}

function _agentFocusFirstUrlParam(params, names) {
  if (!params || !names) return '';
  for (var i = 0; i < names.length; i++) {
    var value = String(params.get(names[i]) || '').trim();
    if (value) return value;
  }
  return '';
}

function _agentFocusHashTargetAgentId(hashText) {
  var hash = String(hashText || '').trim();
  if (!hash) return '';
  if (hash.charAt(0) === '#') hash = hash.slice(1);
  if (!hash) return '';
  var queryIndex = hash.indexOf('?');
  var queryText = queryIndex >= 0 ? hash.slice(queryIndex + 1) : '';
  var pathText = queryIndex >= 0 ? hash.slice(0, queryIndex) : hash;
  var params = _agentFocusUrlParams(queryText || (hash.indexOf('=') >= 0 ? hash : ''));
  var fromParams = _agentFocusFirstUrlParam(params, [
    'agent',
    'agent_id',
    'agentId',
    'cell',
    'cell_id',
    'cellId',
    'focus_agent',
    'focusAgent',
    'focus',
  ]);
  if (fromParams) return fromParams;
  var match = pathText.match(/^(?:agent|cell|focus-agent|focus_agent)[:/](.+)$/i);
  if (!match) return '';
  try {
    return decodeURIComponent(String(match[1] || '').trim());
  } catch (_e) {
    return String(match[1] || '').trim();
  }
}

function _agentFocusUrlTargetAgentId() {
  if (typeof location === 'undefined' || !location) return '';
  var searchParams = _agentFocusUrlParams(location.search || '');
  var fromSearch = _agentFocusFirstUrlParam(searchParams, [
    'agent',
    'agent_id',
    'agentId',
    'cell',
    'cell_id',
    'cellId',
    'focus_agent',
    'focusAgent',
    'focus',
  ]);
  if (fromSearch) return fromSearch;
  return _agentFocusHashTargetAgentId(location.hash || '');
}

function _agentFocusApplyUrlSelection(opts) {
  var raw = _agentFocusUrlTargetAgentId();
  if (!raw) return '';
  opts = opts || {};
  var applied = _agentFocusApplySelectionId(raw, opts);
  if (applied) _agentFocusWritePersistedSelectedAgentId(applied);
  return applied;
}

function _agentFocusRestorePersistedSelection(opts) {
  var raw = _agentFocusPersistedSelectionRaw();
  if (!raw) return '';
  var rootId = _agentFocusVisibleRootAgentId(raw);
  if (!rootId) {
    _agentFocusRemoveStorage(AGENT_FOCUS_SELECTED_AGENT_STORAGE_KEY);
    return '';
  }
  if (rootId !== raw) _agentFocusWritePersistedSelectedAgentId(rootId);
  return _agentFocusApplySelectionId(rootId, opts);
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


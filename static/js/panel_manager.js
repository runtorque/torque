/* Standalone-only panel workspace manager */

var _standalonePanelApps = ['board', 'actions', 'templates', 'history', 'context', 'events', 'engineer', 'supervisor', 'health'];
var _standalonePanelTitles = {
  board: 'Board',
  actions: 'Actions',
  templates: 'Library',
  history: 'History',
  context: 'Context',
  events: 'Events',
  engineer: 'Agent',
  supervisor: 'Supervisor',
  health: 'Health',
};
var _standalonePanelDefaults = {
  board: 'bottom',
  actions: 'right',
  templates: 'right',
  history: 'right',
  context: 'bottom',
  events: 'right',
  engineer: 'right',
  supervisor: 'bottom',
  health: 'right',
};
var _standalonePanelLayoutVersion = 1;
var _standalonePanelLayout = null;
var _standalonePanelSyncing = false;
var _standalonePanelDragApp = '';
var _standalonePanelFloatDrag = null;
var _standalonePanelFloatResizeDrag = null;
var _standalonePanelPointerDrag = null;
var _standalonePanelSuppressClick = false;
var _standalonePanelRoots = {};
var _standalonePrimaryMinWidth = 360;
var _standaloneRightRailMinWidth = 320;
var _standaloneFloatMinWidth = 360;
var _standaloneFloatMinHeight = 260;
var _standaloneDefaultFloatWidth = 460;
var _standaloneDefaultFloatHeight = 320;
var _standaloneFloatMargin = 12;
var _standaloneDetachedRestoreAttempted = false;
var _detachedWindowInfo = _detectDetachedWindowInfo();

function _detectDetachedWindowInfo() {
  if (typeof URLSearchParams === 'undefined' || typeof location === 'undefined') {
    return { active: false, panel: '', label: '' };
  }
  var params = new URLSearchParams(location.search || '');
  var panel = String(params.get('panel') || '').trim();
  var label = String(params.get('window') || '').trim();
  return {
    active: !!(panel && label),
    panel: panel,
    label: label,
  };
}

if (_detachedWindowInfo.active && document && document.body && document.body.classList) {
  document.body.classList.add('detached-window');
  if (document.body.dataset) {
    document.body.dataset.detachedPanel = _detachedWindowInfo.panel;
    document.body.dataset.detachedWindow = _detachedWindowInfo.label;
  }
}

function _detachedWindowActive() {
  return !!(_detachedWindowInfo && _detachedWindowInfo.active);
}

function _standalonePanelsEnabled() {
  if (_detachedWindowActive()) return false;
  return typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode();
}

function _standalonePanelRootId(app) {
  if (app === 'engineer') return 'panel-agent';
  return 'panel-' + app;
}

function _standalonePanelTitle(app) {
  return _standalonePanelTitles[app] || app;
}

function _standaloneEmptyZoneSize(zoneName) {
  return zoneName === 'bottom' ? 72 : 140;
}

function _standaloneZonePlaceholderVisible(zoneName, zone) {
  return !!(
    _standalonePanelDragApp
    && zone
    && zone.open
    && !zone.active
    && (!zone.tabs || !zone.tabs.length)
    && (zoneName === 'bottom' || zoneName === 'right')
  );
}

function _standaloneZoneRenderedSize(zoneName, zone) {
  if (!zone || !zone.open) return 0;
  if (zone.active) return zone.size;
  if (_standaloneZonePlaceholderVisible(zoneName, zone)) return _standaloneEmptyZoneSize(zoneName);
  return 0;
}

function _standaloneHasEmptyZonePreview(layout) {
  var next = layout || _standalonePanelCurrentLayout();
  return _standaloneZonePlaceholderVisible('bottom', next.bottom)
    || _standaloneZonePlaceholderVisible('right', next.right);
}

function _standalonePanelParkingHost() {
  return document.getElementById('bottom-panel');
}

function _standaloneRememberPanelRoot(app, root) {
  if (!app || !root) return null;
  _standalonePanelRoots[app] = root;
  return root;
}

function _standalonePanelRoot(app) {
  if (_standalonePanelRoots[app]) return _standalonePanelRoots[app];
  return _standaloneRememberPanelRoot(app, document.getElementById(_standalonePanelRootId(app)));
}

function _standaloneCapturePanelRoots() {
  var roots = {};
  for (var i = 0; i < _standalonePanelApps.length; i++) {
    var app = _standalonePanelApps[i];
    roots[app] = _standalonePanelRoot(app);
  }
  return roots;
}

function _standaloneParkPanelRoots(roots, placed) {
  var parkingHost = _standalonePanelParkingHost();
  if (!parkingHost) return;
  for (var i = 0; i < _standalonePanelApps.length; i++) {
    var app = _standalonePanelApps[i];
    var panelRoot = roots && roots[app];
    if (!panelRoot || (placed && placed[app])) continue;
    _appendPanelRoot(parkingHost, panelRoot);
    _setPanelHidden(panelRoot, true);
  }
}

function _standaloneLayoutBool(value, fallback) {
  if (typeof value === 'boolean') return value;
  return !!fallback;
}

function _standaloneClamp(value, min, max, fallback) {
  var next = parseInt(value, 10);
  if (!Number.isFinite(next)) next = fallback;
  if (!Number.isFinite(next)) next = min;
  return Math.max(min, Math.min(max, next));
}

function _standaloneBottomSizeBounds() {
  var min = 180;
  var max = Math.max(260, Math.floor(((window && window.innerHeight) || 900) * 0.55));
  return { min: min, max: max };
}

function _standaloneMainStackMinWidth() {
  return _standalonePrimaryMinWidth;
}

function _standaloneRightRailMinimumWidth() {
  return _standaloneRightRailMinWidth;
}

function _standaloneMeasuredShellWidth() {
  var shell = document.getElementById('standalone-sidebar-shell');
  var width = 0;
  if (shell && typeof shell.clientWidth === 'number' && shell.clientWidth > 0) {
    width = shell.clientWidth;
  }
  if ((!width || width <= (_standaloneMainStackMinWidth() + 8))
      && shell
      && typeof shell.getBoundingClientRect === 'function') {
    var rect = shell.getBoundingClientRect();
    if (rect && Number.isFinite(rect.width) && rect.width > 0) width = rect.width;
  }
  if (width <= (_standaloneMainStackMinWidth() + 8)) return 0;
  return width;
}

function _standaloneShellWidth(shellWidth) {
  var width = 0;
  if (Number.isFinite(shellWidth) && shellWidth > 0) width = shellWidth;
  if (!width) {
    width = _standaloneMeasuredShellWidth();
  }
  if (!width
      && typeof _workspaceSidebarWidth !== 'undefined'
      && Number.isFinite(_workspaceSidebarWidth)
      && _workspaceSidebarWidth > 0) {
    width = _workspaceSidebarWidth;
  }
  if (!width && typeof window !== 'undefined' && typeof window.innerWidth === 'number') {
    width = Math.max(0, window.innerWidth - 420);
  }
  return width;
}

function _standaloneRightSizeBounds(shellWidth) {
  var width = _standaloneShellWidth(shellWidth);
  var max = Math.max(0, Math.floor(width - _standaloneMainStackMinWidth() - 8));
  var min = _standaloneRightRailMinimumWidth();
  return {
    min: min,
    max: Math.max(min, max),
  };
}

function _standaloneMinimumShellWidthForLayout(raw) {
  var layout = raw && typeof raw === 'object' ? raw : {};
  var right = layout.right && typeof layout.right === 'object' ? layout.right : {};
  var tabs = _standaloneEnsureUniqueTabs(right.tabs);
  var active = String(right.active || '');
  if (!_standaloneLayoutBool(right.open, true) || !active || tabs.indexOf(active) < 0) {
    return _standaloneMainStackMinWidth();
  }
  var rightSize = parseInt(right.size, 10);
  if (!Number.isFinite(rightSize) || rightSize < 0) rightSize = 0;
  rightSize = Math.max(_standaloneRightRailMinimumWidth(), rightSize);
  return _standaloneMainStackMinWidth() + 8 + rightSize;
}

function _standaloneMinimumShellWidthForDrag(raw) {
  var layout = raw && typeof raw === 'object' ? raw : {};
  var right = layout.right && typeof layout.right === 'object' ? layout.right : {};
  var tabs = _standaloneEnsureUniqueTabs(right.tabs);
  var active = String(right.active || '');
  if (!_standaloneLayoutBool(right.open, true) || !active || tabs.indexOf(active) < 0) {
    return _standaloneMainStackMinWidth();
  }
  return _standaloneMainStackMinWidth() + 8 + _standaloneRightSizeBounds(_standalonePreferredShellWidth()).min;
}

function _standaloneConstrainLayoutToShellWidth(raw, shellWidth) {
  var layout = raw && typeof raw === 'object'
    ? _standaloneClone(raw)
    : _standaloneDefaultLayout();
  var right = layout.right && typeof layout.right === 'object' ? layout.right : null;
  if (!right) return { layout: layout, changed: false };
  var tabs = _standaloneEnsureUniqueTabs(right.tabs);
  var active = String(right.active || '');
  if (!_standaloneLayoutBool(right.open, true) || !active || tabs.indexOf(active) < 0) {
    return { layout: layout, changed: false };
  }
  var bounds = _standaloneRightSizeBounds(shellWidth);
  var current = parseInt(right.size, 10);
  if (!Number.isFinite(current)) current = bounds.max;
  var next = _standaloneClamp(current, bounds.min, bounds.max, bounds.max);
  if (next === current) return { layout: layout, changed: false };
  layout.right.size = next;
  return { layout: layout, changed: true };
}

function _standaloneHasPersistedLayout(layout) {
  return !!(layout && typeof layout === 'object' && Object.keys(layout).length);
}

function _standalonePreferredShellWidth() {
  var measured = _standaloneMeasuredShellWidth();
  if (measured > 0) return measured;
  if (typeof _workspaceSidebarWidth !== 'undefined'
      && Number.isFinite(_workspaceSidebarWidth)
      && _workspaceSidebarWidth > 0) {
    return _workspaceSidebarWidth;
  }
  if (typeof window !== 'undefined' && typeof window.innerWidth === 'number') {
    return Math.max(0, Math.floor(window.innerWidth * 0.56));
  }
  return 0;
}

function _standaloneDefaultBottomSize() {
  var bounds = _standaloneBottomSizeBounds();
  var viewportHeight = (typeof window !== 'undefined' && typeof window.innerHeight === 'number')
    ? window.innerHeight
    : 900;
  var preferred = Math.round(viewportHeight * 0.34);
  return _standaloneClamp(preferred, bounds.min, bounds.max, 300);
}

function _standaloneDefaultRightSize() {
  var shellWidth = _standalonePreferredShellWidth();
  var bounds = _standaloneRightSizeBounds(shellWidth);
  var preferred = Math.round(shellWidth * 0.38);
  return _standaloneClamp(preferred, bounds.min, bounds.max, 280);
}

function _standaloneViewportWidth() {
  if (typeof window !== 'undefined' && typeof window.innerWidth === 'number') return window.innerWidth;
  if (document && document.documentElement && typeof document.documentElement.clientWidth === 'number') {
    if (document.documentElement.clientWidth >= (_standaloneFloatMinWidth + (_standaloneFloatMargin * 2))) {
      return document.documentElement.clientWidth;
    }
  }
  if (document && document.body && typeof document.body.clientWidth === 'number'
      && document.body.clientWidth >= (_standaloneFloatMinWidth + (_standaloneFloatMargin * 2))) {
    return document.body.clientWidth;
  }
  return 1400;
}

function _standaloneViewportHeight() {
  if (typeof window !== 'undefined' && typeof window.innerHeight === 'number') return window.innerHeight;
  if (document && document.documentElement && typeof document.documentElement.clientHeight === 'number') {
    if (document.documentElement.clientHeight >= (_standaloneFloatMinHeight + (_standaloneFloatMargin * 2))) {
      return document.documentElement.clientHeight;
    }
  }
  if (document && document.body && typeof document.body.clientHeight === 'number'
      && document.body.clientHeight >= (_standaloneFloatMinHeight + (_standaloneFloatMargin * 2))) {
    return document.body.clientHeight;
  }
  return 900;
}

function _standaloneFloatLayerBounds() {
  var minWidth = _standaloneFloatMinWidth + (_standaloneFloatMargin * 2);
  var minHeight = _standaloneFloatMinHeight + (_standaloneFloatMargin * 2);
  var viewportWidth = _standaloneViewportWidth();
  var viewportHeight = _standaloneViewportHeight();
  var bounds = {
    left: 0,
    top: 0,
    width: viewportWidth,
    height: viewportHeight,
  };
  var layer = document.getElementById('standalone-float-layer');
  if (layer && typeof layer.getBoundingClientRect === 'function') {
    var rect = layer.getBoundingClientRect();
    if (rect) {
      var rectWidth = Number.isFinite(rect.width) ? rect.width : (rect.right - rect.left);
      var rectHeight = Number.isFinite(rect.height) ? rect.height : (rect.bottom - rect.top);
      if (Number.isFinite(rect.left)) bounds.left = rect.left;
      if (Number.isFinite(rect.top)) bounds.top = rect.top;
      if (Number.isFinite(rectWidth) && rectWidth > 0) bounds.width = rectWidth;
      if (Number.isFinite(rectHeight) && rectHeight > 0) bounds.height = rectHeight;
    }
  }
  if (bounds.width < minWidth && viewportWidth > bounds.width) bounds.width = viewportWidth;
  if (bounds.height < minHeight && viewportHeight > bounds.height) bounds.height = viewportHeight;
  bounds.width = Math.max(minWidth, Math.floor(bounds.width || minWidth));
  bounds.height = Math.max(minHeight, Math.floor(bounds.height || minHeight));
  bounds.right = bounds.left + bounds.width;
  bounds.bottom = bounds.top + bounds.height;
  return bounds;
}

function _standaloneClampFloatFrame(frame, fallback) {
  var source = frame && typeof frame === 'object' ? frame : {};
  var base = fallback && typeof fallback === 'object' ? fallback : {};
  var bounds = _standaloneFloatLayerBounds();
  var maxWidth = Math.max(_standaloneFloatMinWidth, Math.floor(bounds.width - (_standaloneFloatMargin * 2)));
  var maxHeight = Math.max(_standaloneFloatMinHeight, Math.floor(bounds.height - (_standaloneFloatMargin * 2)));
  var width = _standaloneClamp(
    source.width,
    _standaloneFloatMinWidth,
    maxWidth,
    Number.isFinite(parseInt(base.width, 10)) ? parseInt(base.width, 10) : _standaloneDefaultFloatWidth
  );
  var height = _standaloneClamp(
    source.height,
    _standaloneFloatMinHeight,
    maxHeight,
    Number.isFinite(parseInt(base.height, 10)) ? parseInt(base.height, 10) : _standaloneDefaultFloatHeight
  );
  var maxX = Math.max(_standaloneFloatMargin, Math.floor(bounds.width - width - _standaloneFloatMargin));
  var maxY = Math.max(_standaloneFloatMargin, Math.floor(bounds.height - height - _standaloneFloatMargin));
  var x = _standaloneClamp(
    source.x,
    _standaloneFloatMargin,
    maxX,
    Number.isFinite(parseInt(base.x, 10)) ? parseInt(base.x, 10) : 56
  );
  var y = _standaloneClamp(
    source.y,
    _standaloneFloatMargin,
    maxY,
    Number.isFinite(parseInt(base.y, 10)) ? parseInt(base.y, 10) : 72
  );
  return {
    x: x,
    y: y,
    width: width,
    height: height,
    z: _standaloneClamp(source.z, 1, 999, Number.isFinite(parseInt(base.z, 10)) ? parseInt(base.z, 10) : 1),
  };
}

function _standaloneDefaultFloatFrame(zIndex) {
  var z = _standaloneClamp(zIndex, 1, 999, 1);
  var cascade = Math.max(0, z - 1) * 16;
  return _standaloneClampFloatFrame({
    x: 56 + cascade,
    y: 72 + cascade,
    width: _standaloneDefaultFloatWidth,
    height: _standaloneDefaultFloatHeight,
    z: z,
  });
}

function _standaloneFloatFrameCache(layout, create) {
  var target = layout && typeof layout === 'object' ? layout : _standalonePanelCurrentLayout();
  if (!target.float_frames || typeof target.float_frames !== 'object' || Array.isArray(target.float_frames)) {
    if (create === false) return {};
    target.float_frames = {};
  }
  return target.float_frames;
}

function _standaloneRememberFloatFrame(layout, app, frame) {
  if (_standalonePanelApps.indexOf(app) < 0 || !frame || typeof frame !== 'object') return;
  var cache = _standaloneFloatFrameCache(layout, true);
  cache[app] = _standaloneClampFloatFrame(frame, frame);
}

function _standaloneDefaultTabsForZone(zoneName) {
  var tabs = [];
  for (var i = 0; i < _standalonePanelApps.length; i++) {
    var app = _standalonePanelApps[i];
    if ((_standalonePanelDefaults[app] || 'bottom') === zoneName) tabs.push(app);
  }
  return tabs;
}

function _standaloneDefaultLayout() {
  var bottomTabs = _standaloneDefaultTabsForZone('bottom');
  var rightTabs = _standaloneDefaultTabsForZone('right');
  var bottomActive = bottomTabs.indexOf('context') >= 0
    ? 'context'
    : (bottomTabs[0] || '');
  var rightActive = rightTabs.indexOf('engineer') >= 0
    ? 'engineer'
    : (rightTabs[0] || '');
  return {
    version: _standalonePanelLayoutVersion,
    bottom: {
      open: true,
      size: _standaloneDefaultBottomSize(),
      tabs: bottomTabs,
      active: bottomActive,
    },
    right: {
      open: true,
      size: _standaloneDefaultRightSize(),
      tabs: rightTabs,
      active: rightActive,
    },
    floats: {},
    last_active: bottomActive || rightActive || '',
  };
}

function _standaloneClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function _standaloneEnsureUniqueTabs(tabs) {
  var out = [];
  var seen = {};
  var list = Array.isArray(tabs) ? tabs : [];
  for (var i = 0; i < list.length; i++) {
    var app = String(list[i] || '');
    if (_standalonePanelApps.indexOf(app) < 0 || seen[app]) continue;
    seen[app] = true;
    out.push(app);
  }
  return out;
}

function _normalizeStandalonePanelLayout(raw) {
  var base = _standaloneDefaultLayout();
  var layout = raw && typeof raw === 'object' ? _standaloneClone(raw) : {};
  var bottomBounds = _standaloneBottomSizeBounds();
  var rightBounds = _standaloneRightSizeBounds(_standalonePreferredShellWidth());
  var detached = _detachedPanelsState();
  function isDetached(app) {
    var entry = detached && detached[app];
    return !!(entry && typeof entry === 'object' && entry.label);
  }
  var normalized = {
    version: _standalonePanelLayoutVersion,
    bottom: {
      open: _standaloneLayoutBool(layout.bottom && layout.bottom.open, base.bottom.open),
      size: _standaloneClamp(layout.bottom && layout.bottom.size, bottomBounds.min, bottomBounds.max, base.bottom.size),
      tabs: _standaloneEnsureUniqueTabs(layout.bottom && layout.bottom.tabs),
      active: String(layout.bottom && layout.bottom.active || ''),
    },
    right: {
      open: _standaloneLayoutBool(layout.right && layout.right.open, base.right.open),
      size: _standaloneClamp(layout.right && layout.right.size, rightBounds.min, rightBounds.max, base.right.size),
      tabs: _standaloneEnsureUniqueTabs(layout.right && layout.right.tabs),
      active: String(layout.right && layout.right.active || ''),
    },
    floats: {},
    last_active: String(layout.last_active || ''),
  };

  var placements = {};
  function claimTabs(zoneName) {
    var zone = normalized[zoneName];
    var filtered = [];
    for (var i = 0; i < zone.tabs.length; i++) {
      var app = zone.tabs[i];
      if (isDetached(app)) continue;
      if (placements[app]) continue;
      placements[app] = zoneName;
      filtered.push(app);
    }
    zone.tabs = filtered;
    if (zone.tabs.indexOf(zone.active) < 0) zone.active = zone.tabs[0] || '';
  }
  claimTabs('bottom');
  claimTabs('right');

  var floats = layout.floats && typeof layout.floats === 'object' ? layout.floats : {};
  var z = 1;
  for (var appName in floats) {
    if (isDetached(appName)) {
      _standaloneRememberFloatFrame(normalized, appName, floats[appName]);
      continue;
    }
    if (_standalonePanelApps.indexOf(appName) < 0 || placements[appName]) continue;
    var item = floats[appName] || {};
    placements[appName] = 'float';
    normalized.floats[appName] = _standaloneClampFloatFrame(item, _standaloneDefaultFloatFrame(z));
    normalized.floats[appName].z = _standaloneClamp(item.z, 1, 999, z);
    _standaloneRememberFloatFrame(normalized, appName, normalized.floats[appName]);
    z++;
  }

  var floatFrames = layout.float_frames && typeof layout.float_frames === 'object' ? layout.float_frames : {};
  for (var cachedApp in floatFrames) {
    if (_standalonePanelApps.indexOf(cachedApp) < 0) continue;
    if (normalized.floats[cachedApp]) continue;
    var cachedFrame = floatFrames[cachedApp] || {};
    _standaloneRememberFloatFrame(normalized, cachedApp, cachedFrame);
  }

  if (!placements.board && !isDetached('board')) {
    normalized.bottom.tabs.unshift('board');
    placements.board = 'bottom';
  }
  normalized.bottom.tabs = _standaloneEnsureUniqueTabs(normalized.bottom.tabs);
  if (normalized.bottom.tabs.indexOf(normalized.bottom.active) < 0) {
    normalized.bottom.active = normalized.bottom.tabs[0] || '';
  }
  if (!normalized.last_active
      || _standalonePanelApps.indexOf(normalized.last_active) < 0
      || isDetached(normalized.last_active)) {
    normalized.last_active = normalized.right.active || normalized.bottom.active || '';
  }
  return normalized;
}

function _migrateStandalonePanelLayoutFromLegacyState() {
  var layout = _standaloneDefaultLayout();
  var active = (state && state.panel_active) || '';
  var legacyHeight = state && state.board_panel_height;
  if (legacyHeight > 0) {
    layout.bottom.size = _standaloneClamp(
      legacyHeight,
      _standaloneBottomSizeBounds().min,
      _standaloneBottomSizeBounds().max,
      layout.bottom.size
    );
  }
  if (active && _standalonePanelApps.indexOf(active) >= 0) {
    var zoneName = _standalonePanelDefaults[active] || 'bottom';
    if (layout[zoneName] && layout[zoneName].tabs.indexOf(active) >= 0) {
      layout[zoneName].active = active;
      layout[zoneName].open = true;
      layout.last_active = active;
    }
  } else {
    layout.last_active = layout.bottom.active;
  }
  return layout;
}

function _standaloneResolveRestoredLayout(opts) {
  opts = opts || {};
  var stored = (state && state.standalone_panel_layout) || {};
  var hasStored = _standaloneHasPersistedLayout(stored);
  if (!opts.forceDefault && hasStored) {
    return {
      layout: stored,
      shouldPersist: false,
      source: 'persisted',
    };
  }
  return {
    layout: opts.forceDefault
      ? _standaloneDefaultLayout()
      : _migrateStandalonePanelLayoutFromLegacyState(),
    shouldPersist: !!opts.persistResolved,
    source: opts.forceDefault ? 'default' : 'legacy',
  };
}

function _standalonePanelCurrentLayout() {
  if (!_standalonePanelLayout) {
    _standalonePanelLayout = _normalizeStandalonePanelLayout(
      _standaloneResolveRestoredLayout({ persistResolved: false }).layout
    );
  }
  return _standalonePanelLayout;
}

function _standalonePanelSaveLayout() {
  if (_standalonePanelSyncing || !_standalonePanelsEnabled()) return;
  if (typeof send === 'function') {
    send({
      cmd: 'standalone_set_panel_layout',
      layout: _standaloneClone(_standalonePanelCurrentLayout()),
    });
  }
}

function _standalonePanelSetLayout(next, opts) {
  opts = opts || {};
  _standalonePanelLayout = _normalizeStandalonePanelLayout(next);
  if (state) state.standalone_panel_layout = _standaloneClone(_standalonePanelLayout);
  if (typeof _activePanelApp !== 'undefined') {
    _activePanelApp = _standalonePanelActiveApp();
  }
  _standaloneRenderPanelWorkspace();
  if (!opts.fromServer) _standalonePanelSaveLayout();
}

function _standalonePanelSetLayoutFromState(layout, opts) {
  _standalonePanelSyncing = true;
  try {
    _standalonePanelSetLayout(layout, Object.assign({}, opts || {}, { fromServer: true }));
  } finally {
    _standalonePanelSyncing = false;
  }
}

function _restoreStandalonePanelState(opts) {
  var resolved = _standaloneResolveRestoredLayout(opts || {});
  _standalonePanelSetLayoutFromState(resolved.layout, { fromServer: true });
  if (resolved.shouldPersist) _standalonePanelSaveLayout();
  return resolved;
}

function _standaloneVisiblePanelApps() {
  if (!_standalonePanelsEnabled()) return [];
  var layout = _standalonePanelCurrentLayout();
  var out = [];
  if (layout.bottom.open && layout.bottom.active) out.push(layout.bottom.active);
  if (layout.right.open && layout.right.active) out.push(layout.right.active);
  for (var app in layout.floats) out.push(app);
  return out;
}

function _visiblePanelSurfaces() {
  return _standaloneVisiblePanelApps();
}

function _standalonePanelSurfaceVisible(app) {
  return _standaloneVisiblePanelApps().indexOf(app) >= 0;
}

function _standalonePanelPlacement(app) {
  var layout = _standalonePanelCurrentLayout();
  if (layout.bottom.tabs.indexOf(app) >= 0) return 'bottom';
  if (layout.right.tabs.indexOf(app) >= 0) return 'right';
  if (layout.floats[app]) return 'float';
  return '';
}

function _standaloneRemovePanelFromLayout(layout, app) {
  ['bottom', 'right'].forEach(function(zoneName) {
    var zone = layout[zoneName];
    zone.tabs = zone.tabs.filter(function(item) { return item !== app; });
    if (zone.active === app) zone.active = zone.tabs[0] || '';
  });
  if (layout.floats && layout.floats[app]) {
    _standaloneRememberFloatFrame(layout, app, layout.floats[app]);
    delete layout.floats[app];
  }
}

function _standaloneMovePanelToZone(app, zoneName, opts) {
  if (_standalonePanelApps.indexOf(app) < 0) return;
  opts = opts || {};
  var currentLayout = _standalonePanelCurrentLayout();
  var layout = _standaloneClone(currentLayout);
  var cachedFloatFrame = (currentLayout.floats && currentLayout.floats[app])
    || (_standaloneFloatFrameCache(layout, false)[app])
    || null;
  _standaloneRemovePanelFromLayout(layout, app);
  if (zoneName === 'float') {
    var z = 1;
    for (var key in layout.floats) z = Math.max(z, (layout.floats[key] && layout.floats[key].z) || 1);
    var floatFrame = _standaloneClampFloatFrame(cachedFloatFrame || _standaloneDefaultFloatFrame(z + 1));
    floatFrame.z = z + 1;
    layout.floats[app] = floatFrame;
    _standaloneRememberFloatFrame(layout, app, floatFrame);
  } else {
    if (zoneName !== 'bottom' && zoneName !== 'right') return;
    var zone = layout[zoneName];
    zone.open = true;
    if (opts.prepend) zone.tabs.unshift(app);
    else zone.tabs.push(app);
    zone.tabs = _standaloneEnsureUniqueTabs(zone.tabs);
    zone.active = app;
  }
  layout.last_active = app;
  _standalonePanelSetLayout(layout);
}

function _standaloneRunPanelOpenHooks(app, opts) {
  opts = opts || {};
  if (opts.skipOpenHooks) return;
  if (typeof _loadPanelApp === 'function') _loadPanelApp(app);
  if (typeof renderActivePanel === 'function') renderActivePanel();
}

function _standaloneSelectPanel(app, opts) {
  if (!_standalonePanelsEnabled()) return false;
  opts = opts || {};
  var detachedEntry = _detachedPanelEntry(app);
  if (detachedEntry && detachedEntry.label
      && window.nativeApi && window.nativeApi.available()) {
    window.nativeApi.focusWindow(detachedEntry.label);
    return true;
  }
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  var placement = _standalonePanelPlacement(app);
  if (!placement) {
    _standaloneMovePanelToZone(app, _standalonePanelDefaults[app] || 'bottom');
    _standaloneRunPanelOpenHooks(app, opts);
    return true;
  }
  if (placement === 'float') {
    layout.last_active = app;
    if (layout.floats[app]) {
      var maxZ = 1;
      for (var name in layout.floats) {
        maxZ = Math.max(maxZ, layout.floats[name].z || 1);
      }
      layout.floats[app].z = maxZ + 1;
    }
    _standalonePanelSetLayout(layout, opts);
    _standaloneRunPanelOpenHooks(app, opts);
    return true;
  }
  var zone = layout[placement];
  if (!zone.open) zone.open = true;
  if (zone.tabs.indexOf(app) < 0) zone.tabs.push(app);
  zone.active = app;
  layout.last_active = app;
  _standalonePanelSetLayout(layout, opts);
  _standaloneRunPanelOpenHooks(app, opts);
  return true;
}

function _standaloneToggleZone(zoneName) {
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  var zone = layout[zoneName];
  if (!zone) return;
  zone.open = !zone.open;
  _standalonePanelSetLayout(layout);
  if (!zone.open && typeof focusEmbeddedTerminalWorkspace === 'function') {
    focusEmbeddedTerminalWorkspace(true);
  }
}

function _standaloneTogglePanel(app, opts) {
  if (!_standalonePanelsEnabled()) return false;
  opts = opts || {};
  var placement = _standalonePanelPlacement(app);
  if (!placement) {
    return _standaloneSelectPanel(app, opts);
  }
  if (placement === 'float') {
    return _standaloneSelectPanel(app, opts);
  }
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  var zone = layout[placement];
  if (zone.active === app && zone.open) {
    zone.open = false;
    _standalonePanelSetLayout(layout);
    if (typeof focusEmbeddedTerminalWorkspace === 'function') {
      focusEmbeddedTerminalWorkspace(true);
    }
    return true;
  }
  return _standaloneSelectPanel(app, opts);
}

function _standalonePanelActiveApp() {
  var layout = _standalonePanelCurrentLayout();
  return layout.last_active || layout.right.active || layout.bottom.active || '';
}

function _standaloneFocusTaskbarButton(app) {
  var btn = document.querySelector('.taskbar-app[data-app="' + app + '"]');
  if (btn && typeof btn.focus === 'function') btn.focus();
}

function _setStyleVar(el, key, value) {
  if (!el || !el.style) return;
  if (typeof el.style.setProperty === 'function') el.style.setProperty(key, value);
  else el.style[key] = value;
}

function _removeChildFromParent(child) {
  if (!child || !child.parentNode || !Array.isArray(child.parentNode.children)) return;
  child.parentNode.children = child.parentNode.children.filter(function(item) {
    return item !== child;
  });
  child.parentNode = null;
}

function _appendPanelRoot(host, root) {
  if (!host || !root) return;
  if (root.parentNode !== host) _removeChildFromParent(root);
  host.appendChild(root);
}

function _setPanelHidden(root, hidden) {
  if (!root || !root.classList) return;
  root.classList.toggle('panel-hidden', !!hidden);
}

function _clearElement(el) {
  if (!el) return;
  el.innerHTML = '';
}

function _makeStandaloneNode(tag, classNames, text) {
  var el = document.createElement(tag);
  if (classNames) {
    el.className = classNames;
    classNames.split(/\s+/).forEach(function(name) {
      if (name && el.classList && typeof el.classList.add === 'function') el.classList.add(name);
    });
  }
  if (text != null) el.textContent = text;
  return el;
}

function _standaloneSvgNode(tag, attrs) {
  var el = document && typeof document.createElementNS === 'function'
    ? document.createElementNS('http://www.w3.org/2000/svg', tag)
    : document.createElement(tag);
  var names = Object.keys(attrs || {});
  for (var i = 0; i < names.length; i++) {
    el.setAttribute(names[i], attrs[names[i]]);
  }
  return el;
}

function _standalonePanelActionIcon(iconName) {
  var svg = _standaloneSvgNode('svg', {
    class: 'standalone-panel-zone-btn-glyph',
    viewBox: '0 0 16 16',
    fill: 'none',
    'stroke-width': '1.5',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    'aria-hidden': 'true',
    focusable: 'false',
  });

  function addPath(d) {
    svg.appendChild(_standaloneSvgNode('path', { d: d }));
  }

  function addLine(x1, y1, x2, y2) {
    svg.appendChild(_standaloneSvgNode('line', {
      x1: String(x1),
      y1: String(y1),
      x2: String(x2),
      y2: String(y2),
    }));
  }

  if (iconName === 'float') {
    addPath('M9.5 3.5H12.5V6.5');
    addPath('M12.5 3.5L7 9');
    addPath('M10.5 9.5V11.25C10.5 11.9404 9.9404 12.5 9.25 12.5H4.75C4.0596 12.5 3.5 11.9404 3.5 11.25V6.75C3.5 6.0596 4.0596 5.5 4.75 5.5H6.5');
  } else if (iconName === 'detach') {
    addPath('M3.5 4.75C3.5 4.0596 4.0596 3.5 4.75 3.5H11.25C11.9404 3.5 12.5 4.0596 12.5 4.75V11.25C12.5 11.9404 11.9404 12.5 11.25 12.5H4.75C4.0596 12.5 3.5 11.9404 3.5 11.25V4.75Z');
    addPath('M6 6H10V10');
    addPath('M10 6L5.75 10.25');
  } else if (iconName === 'dock') {
    addPath('M3.5 12.25H12.5');
    addPath('M5.75 8.75L8 11.25L10.25 8.75');
    addPath('M8 4V11');
  } else {
    addLine(4.75, 4.75, 11.25, 11.25);
    addLine(11.25, 4.75, 4.75, 11.25);
  }
  return svg;
}

function _standalonePanelActionButton(label, iconName, onClick) {
  var btn = _makeStandaloneNode('button', 'standalone-panel-zone-btn standalone-panel-zone-btn-icon');
  btn.type = 'button';
  btn.draggable = false;
  btn.title = label;
  btn.setAttribute('aria-label', label);
  btn.appendChild(_standalonePanelActionIcon(iconName));
  btn.onclick = onClick;
  return btn;
}

function _standaloneZoneTab(app, active) {
  var btn = _makeStandaloneNode('button', 'standalone-panel-tab' + (active ? ' active' : ''), _standalonePanelTitle(app));
  btn.dataset.app = app;
  btn.draggable = false;
  btn.onclick = function(event) {
    if (_standaloneConsumeSuppressedPanelClick(event)) return;
    _standaloneSelectPanel(app);
  };
  btn.onmousedown = function(event) { standalonePanelPointerDragStart(event, app); };
  return btn;
}

function _standaloneBuildZone(zoneName, rootEl, roots, placed) {
  if (!rootEl) return;
  var layout = _standalonePanelCurrentLayout();
  var zone = layout[zoneName];
  var placeholderVisible = _standaloneZonePlaceholderVisible(zoneName, zone);
  _clearElement(rootEl);
  rootEl.classList.toggle('collapsed', !zone.open || (!zone.active && !placeholderVisible));
  rootEl.classList.toggle('empty', !zone.tabs.length);
  rootEl.dataset.zone = zoneName;
  rootEl.ondragover = function(event) { standalonePanelZoneDragOver(event, zoneName); };
  rootEl.ondrop = function(event) { standalonePanelZoneDrop(event, zoneName); };
  rootEl.ondragleave = function(event) {
    if (event && event.currentTarget && event.currentTarget.classList) {
      event.currentTarget.classList.remove('drag-target');
    }
  };

  if (zone.tabs.length || zone.active) {
    var header = _makeStandaloneNode('div', 'standalone-panel-zone-header');
    var tabs = _makeStandaloneNode('div', 'standalone-panel-zone-tabs');
    for (var i = 0; i < zone.tabs.length; i++) {
      tabs.appendChild(_standaloneZoneTab(zone.tabs[i], zone.tabs[i] === zone.active));
    }
    header.appendChild(tabs);

    var actions = _makeStandaloneNode('div', 'standalone-panel-zone-actions');
    if (zone.active) {
      var detachBtn = _standalonePanelActionButton('Detach to OS window', 'detach', function(activeApp) {
        return function() { _standaloneDetachPanel(activeApp); };
      }(zone.active));
      actions.appendChild(detachBtn);
      var floatBtn = _standalonePanelActionButton('Float', 'float', function(activeApp) {
        return function() { _standaloneMovePanelToZone(activeApp, 'float'); };
      }(zone.active));
      actions.appendChild(floatBtn);
    }
    var closeBtn = _standalonePanelActionButton('Hide', 'hide', function() { _standaloneToggleZone(zoneName); });
    actions.appendChild(closeBtn);
    header.appendChild(actions);
    rootEl.appendChild(header);
  }

  var body = _makeStandaloneNode('div', 'standalone-panel-zone-body');
  if (placeholderVisible) {
    body.appendChild(_makeStandaloneNode('div', 'standalone-panel-empty-drop', 'Drop panel here'));
  }
  rootEl.appendChild(body);

  for (var j = 0; j < zone.tabs.length; j++) {
    var app = zone.tabs[j];
    var panelRoot = (roots && roots[app]) || _standalonePanelRoot(app);
    if (!panelRoot) continue;
    _appendPanelRoot(body, panelRoot);
    if (placed) placed[app] = true;
    _setPanelHidden(panelRoot, !(zone.open && app === zone.active));
  }
}

function _standaloneFloatHeader(app) {
  var header = _makeStandaloneNode('div', 'standalone-float-header');
  header.onmousedown = function(event) { standalonePanelStartFloatDrag(event, app); };
  var title = _makeStandaloneNode('div', 'standalone-float-title', _standalonePanelTitle(app));
  header.appendChild(title);
  var actions = _makeStandaloneNode('div', 'standalone-float-actions');
  var detach = _standalonePanelActionButton('Detach to OS window', 'detach', function() {
    _standaloneDetachPanel(app);
  });
  actions.appendChild(detach);
  var dockBottom = _standalonePanelActionButton('Dock', 'dock', function() {
    _standaloneMovePanelToZone(app, _standalonePanelDefaults[app] || 'bottom');
  });
  actions.appendChild(dockBottom);
  var closeBtn = _standalonePanelActionButton('Hide', 'hide', function() { standalonePanelClose(app); });
  actions.appendChild(closeBtn);
  header.appendChild(actions);
  return header;
}

function _standaloneFloatResizeHandle(app, edge) {
  var handle = _makeStandaloneNode(
    'div',
    'standalone-float-resize-handle standalone-float-resize-handle-' + edge
  );
  handle.dataset.edge = edge;
  handle.setAttribute('aria-hidden', 'true');
  handle.onmousedown = function(event) {
    standalonePanelStartFloatResize(event, app, edge);
  };
  return handle;
}

function _standaloneAppendFloatResizeHandles(shell, app) {
  ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw'].forEach(function(edge) {
    shell.appendChild(_standaloneFloatResizeHandle(app, edge));
  });
}

function _standaloneBuildFloats(layer, roots, placed) {
  if (!layer) return;
  _clearElement(layer);
  layer.ondragover = standalonePanelFloatDragOver;
  layer.ondrop = standalonePanelFloatDrop;
  var layout = _standalonePanelCurrentLayout();
  var apps = Object.keys(layout.floats).sort(function(a, b) {
    return (layout.floats[a].z || 1) - (layout.floats[b].z || 1);
  });
  for (var i = 0; i < apps.length; i++) {
    var app = apps[i];
    var frame = layout.floats[app];
    var shell = _makeStandaloneNode('div', 'standalone-float-shell');
    shell.dataset.app = app;
    shell.style.left = frame.x + 'px';
    shell.style.top = frame.y + 'px';
    shell.style.width = frame.width + 'px';
    shell.style.height = frame.height + 'px';
    shell.style.zIndex = String(frame.z || 1);
    shell.appendChild(_standaloneFloatHeader(app));
    var body = _makeStandaloneNode('div', 'standalone-float-body');
    shell.appendChild(body);
    var panelRoot = (roots && roots[app]) || _standalonePanelRoot(app);
    if (panelRoot) {
      _appendPanelRoot(body, panelRoot);
      if (placed) placed[app] = true;
      _setPanelHidden(panelRoot, false);
    }
    _standaloneAppendFloatResizeHandles(shell, app);
    layer.appendChild(shell);
  }
}

function _standaloneRenderPanelWorkspace() {
  var bottomRoot = document.getElementById('standalone-bottom-dock');
  var rightRoot = document.getElementById('standalone-right-rail');
  var shell = document.getElementById('standalone-sidebar-shell');
  var stack = document.getElementById('standalone-main-stack');
  var layer = document.getElementById('standalone-float-layer');
  var bottomHandle = document.getElementById('standalone-bottom-resize-handle');
  var railHandle = document.getElementById('standalone-rail-resize-handle');
  if (!bottomRoot || !rightRoot || !shell || !stack || !layer) return;

  var panelRoots = _standaloneCapturePanelRoots();
  var placedRoots = {};
  var layout = _standalonePanelCurrentLayout();
  var rightRailWidth = _standaloneZoneRenderedSize('right', layout.right) + 'px';
  _setStyleVar(shell, '--standalone-bottom-height', _standaloneZoneRenderedSize('bottom', layout.bottom) + 'px');
  _setStyleVar(shell, '--standalone-right-rail-width', rightRailWidth);
  _setStyleVar(document.documentElement || document.body, '--standalone-right-rail-width', rightRailWidth);
  if (bottomHandle && bottomHandle.classList) bottomHandle.classList.toggle('collapsed', !(layout.bottom.open && layout.bottom.active));
  if (railHandle && railHandle.classList) railHandle.classList.toggle('collapsed', !(layout.right.open && layout.right.active));
  _standaloneBuildZone('bottom', bottomRoot, panelRoots, placedRoots);
  _standaloneBuildZone('right', rightRoot, panelRoots, placedRoots);
  _standaloneBuildFloats(layer, panelRoots, placedRoots);
  _standaloneParkPanelRoots(panelRoots, placedRoots);
  _standaloneUpdateTaskbarButtons();
}

function _standaloneUpdateTaskbarButtons() {
  if (!document.querySelectorAll) return;
  var visible = {};
  _standaloneVisiblePanelApps().forEach(function(app) { visible[app] = true; });
  var activeApp = _standalonePanelActiveApp();
  document.querySelectorAll('.taskbar-app').forEach(function(btn) {
    var app = btn && btn.dataset ? btn.dataset.app : '';
    if (!app) return;
    if (btn.classList) {
      btn.classList.toggle('active', !!visible[app]);
      btn.classList.toggle('selected', activeApp === app);
    }
  });
}

function standalonePanelDragStart(event, app) {
  _standalonePanelDragApp = app;
  if (event && event.dataTransfer) {
    try { event.dataTransfer.setData('text/plain', app); } catch (_) {}
    event.dataTransfer.effectAllowed = 'move';
  }
  document.body.classList.add('standalone-panel-dragging');
  if (_standaloneHasEmptyZonePreview()) _standaloneRenderPanelWorkspace();
}

function standalonePanelDragEnd() {
  var shouldRender = _standaloneHasEmptyZonePreview();
  _standalonePanelDragApp = '';
  document.body.classList.remove('standalone-panel-dragging');
  _standaloneSetDragTarget('');
  if (shouldRender) _standaloneRenderPanelWorkspace();
}

function _standaloneDraggedApp(event) {
  if (_standalonePanelDragApp) return _standalonePanelDragApp;
  if (event && event.dataTransfer) {
    try { return event.dataTransfer.getData('text/plain') || ''; } catch (_) { return ''; }
  }
  return '';
}

function _standaloneConsumeSuppressedPanelClick(event) {
  if (!_standalonePanelSuppressClick) return false;
  _standalonePanelSuppressClick = false;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  return true;
}

function _standaloneDropTargetElement(zoneName) {
  if (zoneName === 'bottom') return document.getElementById('standalone-bottom-dock');
  if (zoneName === 'right') return document.getElementById('standalone-right-rail');
  return null;
}

function _standaloneSetDragTarget(zoneName) {
  ['bottom', 'right'].forEach(function(name) {
    var el = _standaloneDropTargetElement(name);
    if (el && el.classList) el.classList.toggle('drag-target', name === zoneName);
  });
}

function _standaloneDropTargetZoneForElement(el) {
  var node = el || null;
  while (node) {
    if (node.id === 'standalone-bottom-dock') return 'bottom';
    if (node.id === 'standalone-right-rail') return 'right';
    node = node.parentNode || null;
  }
  return '';
}

function _standaloneDropTargetZoneAtPoint(clientX, clientY) {
  if (!document) return '';
  if (typeof document.elementsFromPoint === 'function') {
    var stack = document.elementsFromPoint(clientX, clientY) || [];
    for (var i = 0; i < stack.length; i++) {
      var zoneName = _standaloneDropTargetZoneForElement(stack[i]);
      if (zoneName) return zoneName;
    }
    return '';
  }
  if (typeof document.elementFromPoint !== 'function') return '';
  return _standaloneDropTargetZoneForElement(document.elementFromPoint(clientX, clientY));
}

function standalonePanelPointerDragStart(event, app) {
  if (!event || event.button !== 0 || !app) return;
  _standalonePanelPointerDrag = {
    app: app,
    startX: event.clientX,
    startY: event.clientY,
    active: false,
  };
  document.addEventListener('mousemove', _standalonePanelOnPointerDrag);
  document.addEventListener('mouseup', _standalonePanelStopPointerDrag);
}

function _standalonePanelOnPointerDrag(event) {
  var drag = _standalonePanelPointerDrag;
  if (!drag) return;
  var dx = Math.abs((event && Number.isFinite(event.clientX) ? event.clientX : drag.startX) - drag.startX);
  var dy = Math.abs((event && Number.isFinite(event.clientY) ? event.clientY : drag.startY) - drag.startY);
  if (!drag.active && dx < 4 && dy < 4) return;
  if (!drag.active) {
    drag.active = true;
    standalonePanelDragStart(null, drag.app);
  }
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  _standaloneSetDragTarget(_standaloneDropTargetZoneAtPoint(event.clientX, event.clientY));
}

function _standaloneClearPointerDrag() {
  document.removeEventListener('mousemove', _standalonePanelOnPointerDrag);
  document.removeEventListener('mouseup', _standalonePanelStopPointerDrag);
  _standalonePanelPointerDrag = null;
}

function _standaloneResetSuppressedPanelClickSoon() {
  if (typeof setTimeout !== 'function') return;
  setTimeout(function() {
    _standalonePanelSuppressClick = false;
  }, 0);
}

function _standalonePanelStopPointerDrag(event) {
  var drag = _standalonePanelPointerDrag;
  if (!drag) return;
  _standaloneClearPointerDrag();
  if (!drag.active) return;
  var clientX = event && Number.isFinite(event.clientX) ? event.clientX : drag.startX;
  var clientY = event && Number.isFinite(event.clientY) ? event.clientY : drag.startY;
  var zoneName = _standaloneDropTargetZoneAtPoint(clientX, clientY);
  _standalonePanelSuppressClick = true;
  _standaloneResetSuppressedPanelClickSoon();
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  standalonePanelDragEnd();
  if (zoneName) _standaloneMovePanelToZone(drag.app, zoneName);
}

function standalonePanelZoneDragOver(event, zoneName) {
  var app = _standaloneDraggedApp(event);
  if (!app) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  _standaloneSetDragTarget(zoneName);
}

function standalonePanelZoneDrop(event, zoneName) {
  var app = _standaloneDraggedApp(event);
  if (!app) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  standalonePanelDragEnd();
  _standaloneMovePanelToZone(app, zoneName);
}

function standalonePanelFloatDragOver(event) {
  var app = _standaloneDraggedApp(event);
  if (!app) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  _standaloneSetDragTarget('float');
}

function standalonePanelFloatDrop(event) {
  var app = _standaloneDraggedApp(event);
  if (!app) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  standalonePanelDragEnd();
  _standaloneMovePanelToZone(app, 'float');
}

function standalonePanelClose(app) {
  var placement = _standalonePanelPlacement(app);
  if (!placement) return;
  if (placement === 'float') {
    var layout = _standaloneClone(_standalonePanelCurrentLayout());
    if (layout.floats && layout.floats[app]) _standaloneRememberFloatFrame(layout, app, layout.floats[app]);
    delete layout.floats[app];
    layout.last_active = layout.right.active || layout.bottom.active || 'board';
    _standalonePanelSetLayout(layout);
  } else {
    _standaloneTogglePanel(app);
  }
}

function _detachedPanelsState() {
  if (!state) return {};
  if (!state.detached_panels || typeof state.detached_panels !== 'object') {
    state.detached_panels = {};
  }
  return state.detached_panels;
}

function _saveDetachedPanels(next) {
  if (state) state.detached_panels = next || {};
  if (typeof send === 'function') {
    send({ cmd: 'ui_set_detached_panels', detached_panels: state.detached_panels || {} });
  }
}

function _detachedPanelEntry(app) {
  var panels = _detachedPanelsState();
  var entry = panels && panels[app];
  return entry && typeof entry === 'object' ? entry : null;
}

function _detachedBoundsForPanel(app) {
  var layout = _standalonePanelLayout || null;
  if (!layout && _standalonePanelsEnabled()) layout = _standalonePanelCurrentLayout();
  var frame = layout
    ? ((layout.floats && layout.floats[app]) || (_standaloneFloatFrameCache(layout, false)[app]) || null)
    : null;
  if (frame) {
    return {
      x: frame.x,
      y: frame.y,
      width: Math.max(frame.width || _standaloneDefaultFloatWidth, 420),
      height: Math.max(frame.height || _standaloneDefaultFloatHeight, 300),
    };
  }
  return {
    width: app === 'board' || app === 'engineer' ? 980 : 760,
    height: app === 'board' || app === 'engineer' ? 680 : 560,
  };
}

function _standaloneDetachPanel(app) {
  if (!app || !window.nativeApi || !window.nativeApi.available()) {
    if (typeof _showToast === 'function') _showToast('Detached OS windows are available in the Tauri desktop shell.', 'info');
    return Promise.resolve('');
  }
  var existing = _detachedPanelEntry(app);
  if (existing && existing.label) {
    return window.nativeApi.focusWindow(existing.label).then(function() { return existing.label; });
  }
  var bounds = _detachedBoundsForPanel(app);
  if (!_standalonePanelsEnabled()) {
    return window.nativeApi.detach(app, bounds).then(function(label) {
      var panels = Object.assign({}, _detachedPanelsState());
      panels[app] = { label: label, bounds: bounds };
      _saveDetachedPanels(panels);
      if (typeof _activePanelApp !== 'undefined' && _activePanelApp === app) {
        _activePanelApp = '';
        var panelEl = document.getElementById('bottom-panel');
        if (panelEl && panelEl.classList) panelEl.classList.add('collapsed');
      }
      return label;
    });
  }
  return window.nativeApi.detach(app, bounds).then(function(label) {
    var panels = Object.assign({}, _detachedPanelsState());
    panels[app] = { label: label, bounds: bounds };
    _saveDetachedPanels(panels);
    var layout = _standaloneClone(_standalonePanelCurrentLayout());
    _standaloneRemovePanelFromLayout(layout, app);
    layout.last_active = layout.right.active || layout.bottom.active || 'board';
    _standalonePanelSetLayout(layout);
    return label;
  });
}

function detachActivePanel() {
  var app = '';
  if (_detachedWindowActive()) return Promise.resolve('');
  if (typeof _standalonePanelActiveApp === 'function' && _standalonePanelsEnabled()) {
    app = _standalonePanelActiveApp();
  } else if (typeof _activePanelApp !== 'undefined') {
    app = _activePanelApp || 'board';
  }
  return _standaloneDetachPanel(app || 'board');
}

function _standaloneRestoreDetachedPanels() {
  if (_standaloneDetachedRestoreAttempted || _detachedWindowActive()) return;
  _standaloneDetachedRestoreAttempted = true;
  if (!window.nativeApi || !window.nativeApi.available()) return;
  var panels = _detachedPanelsState();
  Object.keys(panels).forEach(function(app) {
    var entry = panels[app];
    if (!entry || typeof entry !== 'object') return;
    window.nativeApi.detach(app, entry.bounds || null).then(function(label) {
      if (label && label !== entry.label) {
        var next = Object.assign({}, _detachedPanelsState());
        next[app] = Object.assign({}, entry, { label: label });
        _saveDetachedPanels(next);
      }
    }).catch(function() {});
  });
}

function torqueDetachedWindowClosed(panel, label) {
  var panels = Object.assign({}, _detachedPanelsState());
  var entry = panels[panel];
  if (entry && (!label || entry.label === label)) {
    delete panels[panel];
    _saveDetachedPanels(panels);
  }
  if (typeof _restoreStandalonePanelState === 'function'
      && typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()) {
    var layout = _standaloneClone(_standalonePanelCurrentLayout());
    if (!_standalonePanelPlacement(panel)) {
      var zone = _standalonePanelDefaults[panel] || 'bottom';
      layout[zone].open = true;
      layout[zone].tabs.push(panel);
      layout[zone].tabs = _standaloneEnsureUniqueTabs(layout[zone].tabs);
      layout[zone].active = panel;
      layout.last_active = panel;
      _standalonePanelSetLayout(layout);
    }
  }
}

function torqueDetachedWindowBoundsChanged() {
  if (!_detachedWindowActive() || !window.nativeApi || !window.nativeApi.available()) return;
  if (torqueDetachedWindowBoundsChanged._timer && typeof clearTimeout === 'function') {
    clearTimeout(torqueDetachedWindowBoundsChanged._timer);
  }
  torqueDetachedWindowBoundsChanged._timer = setTimeout(function() {
    window.nativeApi.currentWindowBounds().then(function(bounds) {
      if (!bounds) return;
      var panels = Object.assign({}, _detachedPanelsState());
      var panel = _detachedWindowInfo.panel;
      panels[panel] = Object.assign({}, panels[panel] || {}, {
        label: _detachedWindowInfo.label,
        bounds: bounds,
      });
      _saveDetachedPanels(panels);
    }).catch(function() {});
  }, 300);
}

function standalonePanelStartFloatDrag(event, app) {
  if (!event || event.button !== 0) return;
  var layout = _standalonePanelCurrentLayout();
  var frame = layout.floats[app];
  if (!frame) return;
  _standalonePanelFloatDrag = {
    app: app,
    startX: event.clientX,
    startY: event.clientY,
    left: frame.x,
    top: frame.y,
  };
  document.addEventListener('mousemove', _standalonePanelOnFloatDrag);
  document.addEventListener('mouseup', _standalonePanelStopFloatDrag);
}

function _standalonePanelOnFloatDrag(event) {
  if (!_standalonePanelFloatDrag) return;
  var drag = _standalonePanelFloatDrag;
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  if (!layout.floats[drag.app]) return;
  layout.floats[drag.app] = _standaloneClampFloatFrame(Object.assign({}, layout.floats[drag.app], {
    x: drag.left + (event.clientX - drag.startX),
    y: drag.top + (event.clientY - drag.startY),
  }));
  _standaloneRememberFloatFrame(layout, drag.app, layout.floats[drag.app]);
  _standalonePanelSetLayout(layout, { fromServer: true });
}

function _standalonePanelStopFloatDrag() {
  if (!_standalonePanelFloatDrag) return;
  document.removeEventListener('mousemove', _standalonePanelOnFloatDrag);
  document.removeEventListener('mouseup', _standalonePanelStopFloatDrag);
  _standalonePanelFloatDrag = null;
  _standalonePanelSaveLayout();
}

function _standaloneFloatResizeFrame(drag, clientX, clientY) {
  var dx = clientX - drag.startX;
  var dy = clientY - drag.startY;
  var start = drag.frame;
  var left = start.x;
  var top = start.y;
  var right = start.x + start.width;
  var bottom = start.y + start.height;
  var edge = drag.edge || '';
  var bounds = _standaloneFloatLayerBounds();
  var minLeft = _standaloneFloatMargin;
  var minTop = _standaloneFloatMargin;
  var maxRight = Math.max(minLeft + _standaloneFloatMinWidth, bounds.width - _standaloneFloatMargin);
  var maxBottom = Math.max(minTop + _standaloneFloatMinHeight, bounds.height - _standaloneFloatMargin);

  if (edge.indexOf('e') >= 0) {
    right = _standaloneClamp(right + dx, left + _standaloneFloatMinWidth, maxRight, right);
  }
  if (edge.indexOf('s') >= 0) {
    bottom = _standaloneClamp(bottom + dy, top + _standaloneFloatMinHeight, maxBottom, bottom);
  }
  if (edge.indexOf('w') >= 0) {
    left = _standaloneClamp(left + dx, minLeft, right - _standaloneFloatMinWidth, left);
  }
  if (edge.indexOf('n') >= 0) {
    top = _standaloneClamp(top + dy, minTop, bottom - _standaloneFloatMinHeight, top);
  }

  return _standaloneClampFloatFrame({
    x: left,
    y: top,
    width: right - left,
    height: bottom - top,
    z: start.z,
  }, start);
}

function _standaloneApplyFloatResizeFrame(shell, frame) {
  if (!shell || !shell.style || !frame) return;
  shell.style.left = frame.x + 'px';
  shell.style.top = frame.y + 'px';
  shell.style.width = frame.width + 'px';
  shell.style.height = frame.height + 'px';
  shell.style.zIndex = String(frame.z || 1);
}

function standalonePanelStartFloatResize(event, app, edge) {
  if (!event || event.button !== 0) return;
  var layout = _standalonePanelCurrentLayout();
  var frame = layout.floats[app];
  if (!frame) return;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  if (typeof event.stopPropagation === 'function') event.stopPropagation();
  var shell = event.currentTarget && event.currentTarget.closest
    ? event.currentTarget.closest('.standalone-float-shell')
    : null;
  if (!shell && event.currentTarget && event.currentTarget.parentNode) {
    shell = event.currentTarget.parentNode;
  }
  _standalonePanelFloatResizeDrag = {
    app: app,
    edge: edge,
    shell: shell,
    startX: event.clientX,
    startY: event.clientY,
    frame: _standaloneClampFloatFrame(frame, frame),
    currentFrame: _standaloneClampFloatFrame(frame, frame),
    changed: false,
  };
  if (document && document.body && document.body.classList) {
    document.body.classList.add('standalone-float-resizing');
  }
  document.addEventListener('mousemove', _standalonePanelOnFloatResizeDrag);
  document.addEventListener('mouseup', _standalonePanelStopFloatResizeDrag);
}

function _standalonePanelOnFloatResizeDrag(event) {
  var drag = _standalonePanelFloatResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  var clientX = event && Number.isFinite(event.clientX) ? event.clientX : drag.startX;
  var clientY = event && Number.isFinite(event.clientY) ? event.clientY : drag.startY;
  var frame = _standaloneFloatResizeFrame(drag, clientX, clientY);
  drag.currentFrame = frame;
  drag.changed = true;
  _standaloneApplyFloatResizeFrame(drag.shell, frame);
}

function _standaloneClearFloatResizeDrag() {
  document.removeEventListener('mousemove', _standalonePanelOnFloatResizeDrag);
  document.removeEventListener('mouseup', _standalonePanelStopFloatResizeDrag);
  if (document && document.body && document.body.classList) {
    document.body.classList.remove('standalone-float-resizing');
  }
  _standalonePanelFloatResizeDrag = null;
}

function _standalonePanelStopFloatResizeDrag(event) {
  var drag = _standalonePanelFloatResizeDrag;
  if (!drag) return;
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  if (event && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
    drag.currentFrame = _standaloneFloatResizeFrame(drag, event.clientX, event.clientY);
    if (drag.changed) _standaloneApplyFloatResizeFrame(drag.shell, drag.currentFrame);
  }
  var app = drag.app;
  var frame = drag.currentFrame;
  var shouldCommit = !!drag.changed;
  _standaloneClearFloatResizeDrag();
  if (!shouldCommit) return;
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  if (!layout.floats || !layout.floats[app]) return;
  frame.z = layout.floats[app].z || frame.z || 1;
  layout.floats[app] = _standaloneClampFloatFrame(frame, layout.floats[app]);
  _standaloneRememberFloatFrame(layout, app, layout.floats[app]);
  _standalonePanelSetLayout(layout);
}

function standalonePanelResizeBottom(clientY) {
  var shell = document.getElementById('standalone-sidebar-shell');
  if (!shell || !shell.getBoundingClientRect) return;
  var rect = shell.getBoundingClientRect();
  var next = rect.bottom - clientY;
  var bounds = _standaloneBottomSizeBounds();
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  layout.bottom.size = _standaloneClamp(next, bounds.min, bounds.max, layout.bottom.size);
  layout.bottom.open = true;
  _standalonePanelSetLayout(layout, { fromServer: true });
}

function standalonePanelResizeRight(clientX) {
  var shell = document.getElementById('standalone-sidebar-shell');
  if (!shell || !shell.getBoundingClientRect) return;
  var rect = shell.getBoundingClientRect();
  var next = rect.right - clientX;
  var shellWidth = (typeof _workspaceSidebarWidth !== 'undefined'
      && Number.isFinite(_workspaceSidebarWidth)
      && _workspaceSidebarWidth > 0)
    ? _workspaceSidebarWidth
    : rect.width;
  var bounds = _standaloneRightSizeBounds(shellWidth);
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  layout.right.size = _standaloneClamp(next, bounds.min, bounds.max, layout.right.size);
  layout.right.open = true;
  _standalonePanelSetLayout(layout, { fromServer: true });
}

(function() {
  function bindHandle(id, onMove, onStop, adjustEvent) {
    var handle = document.getElementById(id);
    if (!handle) return;
    var dragging = false;
    var dragOffsetX = 0;
    var dragOffsetY = 0;
    handle.addEventListener('mousedown', function(event) {
      if (!_standalonePanelsEnabled()) return;
      event.preventDefault();
      dragOffsetX = 0;
      dragOffsetY = 0;
      if (handle && typeof handle.getBoundingClientRect === 'function' && event) {
        var rect = handle.getBoundingClientRect();
        if (rect) {
          if (Number.isFinite(rect.right) && Number.isFinite(event.clientX)) {
            dragOffsetX = rect.right - event.clientX;
          }
          if (Number.isFinite(rect.bottom) && Number.isFinite(event.clientY)) {
            dragOffsetY = rect.bottom - event.clientY;
          }
        }
      }
      dragging = true;
      document.body.classList.add('workspace-resizing');
    });
    document.addEventListener('mousemove', function(event) {
      if (!dragging) return;
      onMove(typeof adjustEvent === 'function' ? adjustEvent(event, dragOffsetX, dragOffsetY) : event);
    });
    document.addEventListener('mouseup', function() {
      if (!dragging) return;
      dragging = false;
      dragOffsetX = 0;
      dragOffsetY = 0;
      document.body.classList.remove('workspace-resizing');
      if (typeof onStop === 'function') onStop();
    });
  }
  bindHandle('standalone-bottom-resize-handle', function(event) {
    standalonePanelResizeBottom(event.clientY);
  }, function() {
    _standalonePanelSaveLayout();
  }, function(event, _dragOffsetX, dragOffsetY) {
    if (!event || !Number.isFinite(event.clientY)) return event;
    return Object.assign({}, event, {
      clientY: event.clientY + dragOffsetY,
    });
  });
  bindHandle('standalone-rail-resize-handle', function(event) {
    standalonePanelResizeRight(event.clientX);
  }, function() {
    _standalonePanelSaveLayout();
  }, function(event, dragOffsetX) {
    if (!event || !Number.isFinite(event.clientX)) return event;
    return Object.assign({}, event, {
      clientX: event.clientX + dragOffsetX,
    });
  });
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', function() {
      if (!_standalonePanelsEnabled() || !_standalonePanelLayout) return;
      _standalonePanelSetLayout(_standalonePanelCurrentLayout(), { fromServer: true });
    });
  }
})();

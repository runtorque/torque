/* Standalone-only panel workspace manager */

var _standalonePanelApps = ['board', 'actions', 'templates', 'context', 'events', 'weaver'];
var _standalonePanelTitles = {
  board: 'Board',
  actions: 'Actions',
  templates: 'Library',
  context: 'Context',
  events: 'Events',
  weaver: 'Weaver',
};
var _standalonePanelDefaults = {
  board: 'bottom',
  actions: 'right',
  templates: 'right',
  context: 'right',
  events: 'right',
  weaver: 'bottom',
};
var _standalonePanelLayoutVersion = 1;
var _standalonePanelLayout = null;
var _standalonePanelSyncing = false;
var _standalonePanelDragApp = '';
var _standalonePanelFloatDrag = null;
var _standalonePanelPointerDrag = null;
var _standalonePanelSuppressClick = false;
var _standalonePanelRoots = {};
var _standalonePrimaryMinWidth = 240;

function _standalonePanelsEnabled() {
  return typeof isEmbeddedTerminalMode === 'function' && isEmbeddedTerminalMode();
}

function _standalonePanelRootId(app) {
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
  var min = Math.min(240, max);
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

function _standaloneDefaultLayout() {
  return {
    version: _standalonePanelLayoutVersion,
    bottom: {
      open: true,
      size: _standaloneDefaultBottomSize(),
      tabs: ['board'],
      active: 'board',
    },
    right: {
      open: true,
      size: _standaloneDefaultRightSize(),
      tabs: ['actions', 'templates', 'context', 'events'],
      active: 'context',
    },
    floats: {},
    last_active: 'board',
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
    if (_standalonePanelApps.indexOf(appName) < 0 || placements[appName]) continue;
    var item = floats[appName] || {};
    placements[appName] = 'float';
    normalized.floats[appName] = {
      x: _standaloneClamp(item.x, 12, 1600, 48 + (z * 18)),
      y: _standaloneClamp(item.y, 12, 1000, 72 + (z * 18)),
      width: _standaloneClamp(item.width, 280, 1200, 420),
      height: _standaloneClamp(item.height, 220, 900, 320),
      z: _standaloneClamp(item.z, 1, 999, z),
    };
    z++;
  }

  if (!placements.board) {
    normalized.bottom.tabs.unshift('board');
    placements.board = 'bottom';
  }
  normalized.bottom.tabs = _standaloneEnsureUniqueTabs(normalized.bottom.tabs);
  if (normalized.bottom.tabs.indexOf(normalized.bottom.active) < 0) {
    normalized.bottom.active = normalized.bottom.tabs[0] || '';
  }
  if (!normalized.last_active || _standalonePanelApps.indexOf(normalized.last_active) < 0) {
    normalized.last_active = normalized.right.active || normalized.bottom.active || 'board';
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
  if (active === 'weaver') {
    layout.bottom.tabs = ['board', 'weaver'];
    layout.bottom.active = 'weaver';
    layout.last_active = 'weaver';
  } else if (active && layout.right.tabs.indexOf(active) >= 0) {
    layout.right.active = active;
    layout.last_active = active;
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
  if (layout.floats && layout.floats[app]) delete layout.floats[app];
}

function _standaloneMovePanelToZone(app, zoneName, opts) {
  if (_standalonePanelApps.indexOf(app) < 0) return;
  opts = opts || {};
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  _standaloneRemovePanelFromLayout(layout, app);
  if (zoneName === 'float') {
    var existing = _standalonePanelCurrentLayout().floats[app] || {};
    var z = 1;
    for (var key in layout.floats) z = Math.max(z, (layout.floats[key] && layout.floats[key].z) || 1);
    layout.floats[app] = {
      x: _standaloneClamp(existing.x, 12, 1600, 56 + z * 16),
      y: _standaloneClamp(existing.y, 12, 1000, 72 + z * 16),
      width: _standaloneClamp(existing.width, 280, 1200, 460),
      height: _standaloneClamp(existing.height, 220, 900, 320),
      z: z + 1,
    };
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

function _standaloneSelectPanel(app, opts) {
  if (!_standalonePanelsEnabled()) return false;
  opts = opts || {};
  var layout = _standaloneClone(_standalonePanelCurrentLayout());
  var placement = _standalonePanelPlacement(app);
  if (!placement) {
    _standaloneMovePanelToZone(app, _standalonePanelDefaults[app] || 'bottom');
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
    return true;
  }
  var zone = layout[placement];
  if (!zone.open) zone.open = true;
  if (zone.tabs.indexOf(app) < 0) zone.tabs.push(app);
  zone.active = app;
  layout.last_active = app;
  _standalonePanelSetLayout(layout, opts);
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

function _standaloneTogglePanel(app) {
  if (!_standalonePanelsEnabled()) return false;
  var placement = _standalonePanelPlacement(app);
  if (!placement) {
    return _standaloneSelectPanel(app);
  }
  if (placement === 'float') {
    return _standaloneSelectPanel(app);
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
  return _standaloneSelectPanel(app);
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
      var floatBtn = _makeStandaloneNode('button', 'standalone-panel-zone-btn', 'Float');
      floatBtn.onclick = function(activeApp) {
        return function() { _standaloneMovePanelToZone(activeApp, 'float'); };
      }(zone.active);
      actions.appendChild(floatBtn);
    }
    var closeBtn = _makeStandaloneNode('button', 'standalone-panel-zone-btn', 'Hide');
    closeBtn.onclick = function() { _standaloneToggleZone(zoneName); };
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
  var dockBottom = _makeStandaloneNode('button', 'standalone-panel-zone-btn', 'Dock');
  dockBottom.onclick = function() { _standaloneMovePanelToZone(app, _standalonePanelDefaults[app] || 'bottom'); };
  actions.appendChild(dockBottom);
  var closeBtn = _makeStandaloneNode('button', 'standalone-panel-zone-btn', 'Hide');
  closeBtn.onclick = function() { standalonePanelClose(app); };
  actions.appendChild(closeBtn);
  header.appendChild(actions);
  return header;
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
  _setStyleVar(shell, '--standalone-bottom-height', _standaloneZoneRenderedSize('bottom', layout.bottom) + 'px');
  _setStyleVar(shell, '--standalone-right-rail-width', _standaloneZoneRenderedSize('right', layout.right) + 'px');
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
    delete layout.floats[app];
    layout.last_active = layout.right.active || layout.bottom.active || 'board';
    _standalonePanelSetLayout(layout);
  } else {
    _standaloneTogglePanel(app);
  }
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
  layout.floats[drag.app].x = Math.max(12, drag.left + (event.clientX - drag.startX));
  layout.floats[drag.app].y = Math.max(12, drag.top + (event.clientY - drag.startY));
  _standalonePanelSetLayout(layout, { fromServer: true });
}

function _standalonePanelStopFloatDrag() {
  if (!_standalonePanelFloatDrag) return;
  document.removeEventListener('mousemove', _standalonePanelOnFloatDrag);
  document.removeEventListener('mouseup', _standalonePanelStopFloatDrag);
  _standalonePanelFloatDrag = null;
  _standalonePanelSaveLayout();
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

/* Responsive workspace presentation.
 *
 * The persisted standalone panel layout remains the source of truth for panel
 * placement and sizing. This module adds a viewport-specific presentation on
 * top of it so a layout saved on a large display remains usable in a smaller
 * window without rewriting the operator's saved arrangement.
 */

var _workspaceLayoutMode = '';
var _workspaceLayoutResizeFrame = 0;
var _workspaceLayoutFocusedBreakpoint = 1480;
var _workspaceLayoutCompactBreakpoint = 920;
var _workspaceMoreMenuOpen = false;

function _workspaceLayoutModeForWidth(width) {
  var next = Number(width || 0);
  if (!Number.isFinite(next) || next <= 0) next = 1280;
  if (next < _workspaceLayoutCompactBreakpoint) return 'compact';
  if (next < _workspaceLayoutFocusedBreakpoint) return 'focused';
  return 'wide';
}

function _workspaceLayoutModeOverride() {
  if (typeof location === 'undefined' || !location || typeof URLSearchParams === 'undefined') return '';
  var value = new URLSearchParams(location.search || '').get('workspace_mode') || '';
  return value === 'wide' || value === 'focused' || value === 'compact' ? value : '';
}

function _workspaceActivePanelPresentation() {
  if (typeof document !== 'undefined' && document && typeof document.querySelector === 'function') {
    var selectedButton = document.querySelector('.taskbar-app.selected')
      || document.querySelector('.taskbar-app.active');
    var selectedApp = selectedButton && selectedButton.dataset
      ? String(selectedButton.dataset.app || '')
      : '';
    if (selectedApp) {
      var activeTab = document.querySelector(
        '.standalone-panel-tab.active[data-app="' + selectedApp.replace(/"/g, '') + '"]'
      );
      var zoneRoot = activeTab && typeof activeTab.closest === 'function'
        ? activeTab.closest('#standalone-bottom-dock, #standalone-right-rail')
        : null;
      var domZone = zoneRoot && zoneRoot.id === 'standalone-bottom-dock'
        ? 'bottom'
        : (zoneRoot && zoneRoot.id === 'standalone-right-rail' ? 'right' : '');
      var domOpen = !!(
        zoneRoot
        && (!zoneRoot.classList || !zoneRoot.classList.contains('collapsed'))
      );
      if (domZone) return { app: selectedApp, zone: domZone, open: domOpen };
    }
  }
  if (typeof _standalonePanelsEnabled !== 'function' || !_standalonePanelsEnabled()) {
    return { app: '', zone: '', open: false };
  }
  var layout = typeof _standalonePanelCurrentLayout === 'function'
    ? _standalonePanelCurrentLayout()
    : null;
  if (!layout) return { app: '', zone: '', open: false };
  var app = String(
    layout.last_active
    || (layout.right && layout.right.open && layout.right.active)
    || (layout.bottom && layout.bottom.open && layout.bottom.active)
    || ''
  );
  var zone = typeof _standalonePanelPlacement === 'function'
    ? _standalonePanelPlacement(app)
    : '';
  if (zone !== 'bottom' && zone !== 'right') {
    return { app: app, zone: zone || '', open: false };
  }
  var zoneState = layout[zone] || {};
  return {
    app: app,
    zone: zone,
    open: !!(zoneState.open && zoneState.active === app),
  };
}

function _workspaceSetDataset(el, key, value) {
  if (!el) return;
  if (el.dataset) el.dataset[key] = value;
  else if (typeof el.setAttribute === 'function') {
    el.setAttribute('data-' + key.replace(/[A-Z]/g, function(ch) {
      return '-' + ch.toLowerCase();
    }), value);
  }
}

function _syncWorkspaceLayoutPresentation() {
  if (typeof document === 'undefined' || !document || !document.body) return '';
  var width = (typeof window !== 'undefined' && window && Number(window.innerWidth)) || 1280;
  var mode = _workspaceLayoutModeOverride() || _workspaceLayoutModeForWidth(width);
  var body = document.body;
  var panel = _workspaceActivePanelPresentation();

  _workspaceLayoutMode = mode;
  _workspaceSetDataset(body, 'workspaceMode', mode);
  _workspaceSetDataset(body, 'workspacePanel', panel.app || '');
  _workspaceSetDataset(body, 'workspacePanelZone', panel.zone || '');
  if (body.classList) {
    body.classList.toggle('workspace-mode-wide', mode === 'wide');
    body.classList.toggle('workspace-mode-focused', mode === 'focused');
    body.classList.toggle('workspace-mode-compact', mode === 'compact');
    body.classList.toggle('workspace-panel-open', !!panel.open);
    if (mode !== 'compact') body.classList.remove('workspace-agents-open');
  }
  _workspaceSyncNavigation(panel);
  return mode;
}

function _workspaceSetMoreMenuOpen(open) {
  _workspaceMoreMenuOpen = !!open;
  var menu = document.getElementById && document.getElementById('workspace-more-menu');
  var button = document.getElementById && document.getElementById('taskbar-more');
  if (menu) menu.hidden = !_workspaceMoreMenuOpen;
  if (button) {
    button.setAttribute('aria-expanded', _workspaceMoreMenuOpen ? 'true' : 'false');
    if (button.classList) button.classList.toggle('menu-open', _workspaceMoreMenuOpen);
  }
}

function workspaceCloseMoreMenu() {
  if (typeof document === 'undefined' || !document) return false;
  _workspaceSetMoreMenuOpen(false);
  return true;
}

function _workspaceBuildMoreMenu() {
  if (typeof document === 'undefined' || !document || !document.getElementById) return;
  var menu = document.getElementById('workspace-more-menu');
  if (!menu || menu.dataset.built === 'true') return;
  var sourceButtons = typeof document.querySelectorAll === 'function'
    ? document.querySelectorAll('.taskbar-app.nav-secondary[data-app]')
    : [];
  for (var i = 0; i < sourceButtons.length; i++) {
    var source = sourceButtons[i];
    var app = source && source.dataset ? String(source.dataset.app || '') : '';
    if (!app) continue;
    var item = document.createElement('button');
    item.type = 'button';
    item.className = 'workspace-more-item';
    item.dataset.app = app;
    item.setAttribute('role', 'menuitem');
    item.textContent = String(source.textContent || app).trim();
    item.onclick = function(selectedApp) {
      return function() {
        workspaceCloseMoreMenu();
        workspaceHideAgents();
        if (typeof togglePanel === 'function') togglePanel(selectedApp);
        setTimeout(_syncWorkspaceLayoutPresentation, 0);
      };
    }(app);
    menu.appendChild(item);
  }
  menu.dataset.built = 'true';
}

function workspaceToggleMoreMenu(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  _workspaceBuildMoreMenu();
  _workspaceSetMoreMenuOpen(!_workspaceMoreMenuOpen);
  return _workspaceMoreMenuOpen;
}

function workspaceToggleAgents() {
  workspaceCloseMoreMenu();
  _syncWorkspaceLayoutPresentation();
  if (_workspaceLayoutMode === 'compact') {
    var body = document.body;
    var open = !!(body.classList && body.classList.contains('workspace-agents-open'));
    if (open) workspaceHideAgents();
    else workspaceShowAgents();
    _workspaceSyncNavigation(_workspaceActivePanelPresentation());
    return !open;
  }
  var panel = _workspaceActivePanelPresentation();
  if (_workspaceLayoutMode === 'focused' && panel.open && typeof togglePanel === 'function') {
    togglePanel(panel.app);
  }
  var main = document.getElementById && document.getElementById('main');
  if (main && typeof main.focus === 'function') main.focus();
  setTimeout(_syncWorkspaceLayoutPresentation, 0);
  return true;
}

function _workspaceSyncNavigation(panel) {
  if (typeof document === 'undefined' || !document || !document.getElementById) return;
  panel = panel || _workspaceActivePanelPresentation();
  var workspaceButton = document.getElementById('taskbar-workspace');
  var moreButton = document.getElementById('taskbar-more');
  var agentsOpen = !!(
    document.body
    && document.body.classList
    && document.body.classList.contains('workspace-agents-open')
  );
  if (workspaceButton && workspaceButton.classList) {
    workspaceButton.classList.toggle(
      'selected',
      agentsOpen || (_workspaceLayoutMode === 'focused' && !panel.open)
    );
  }
  if (_workspaceLayoutMode !== 'wide' && typeof document.querySelectorAll === 'function') {
    var primaryPanelButtons = document.querySelectorAll('.taskbar-app.nav-primary[data-app]');
    for (var primaryIndex = 0; primaryIndex < primaryPanelButtons.length; primaryIndex++) {
      var primaryButton = primaryPanelButtons[primaryIndex];
      if (primaryButton.classList) {
        primaryButton.classList.toggle(
          'active',
          !!(panel.open && primaryButton.dataset.app === panel.app)
        );
      }
    }
  }
  var activeSource = typeof document.querySelector === 'function' && panel.app
    ? document.querySelector('.taskbar-app.nav-secondary[data-app="' + panel.app.replace(/"/g, '') + '"]')
    : null;
  if (moreButton && moreButton.classList) {
    moreButton.classList.toggle('selected', !!activeSource || _workspaceMoreMenuOpen);
  }
  var menuItems = typeof document.querySelectorAll === 'function'
    ? document.querySelectorAll('.workspace-more-item[data-app]')
    : [];
  for (var i = 0; i < menuItems.length; i++) {
    var item = menuItems[i];
    if (item.classList) item.classList.toggle('active', item.dataset.app === panel.app && panel.open);
  }
}

function workspaceShowAgents() {
  if (typeof document === 'undefined' || !document || !document.body) return false;
  _syncWorkspaceLayoutPresentation();
  if (_workspaceLayoutMode !== 'compact') {
    var main = document.getElementById && document.getElementById('main');
    if (main && typeof main.focus === 'function') main.focus();
    return false;
  }
  if (document.body.classList) document.body.classList.add('workspace-agents-open');
  return true;
}

function workspaceHideAgents() {
  if (typeof document === 'undefined' || !document || !document.body) return false;
  if (document.body.classList) document.body.classList.remove('workspace-agents-open');
  return true;
}

function _workspaceLayoutScheduleSync() {
  if (_workspaceLayoutResizeFrame) return;
  var raf = (typeof requestAnimationFrame === 'function')
    ? requestAnimationFrame
    : function(cb) { return setTimeout(cb, 0); };
  _workspaceLayoutResizeFrame = raf(function() {
    _workspaceLayoutResizeFrame = 0;
    _syncWorkspaceLayoutPresentation();
  });
}

if (typeof window !== 'undefined' && window) {
  window._syncWorkspaceLayoutPresentation = _syncWorkspaceLayoutPresentation;
  window.workspaceShowAgents = workspaceShowAgents;
  window.workspaceHideAgents = workspaceHideAgents;
  window.workspaceToggleAgents = workspaceToggleAgents;
  window.workspaceToggleMoreMenu = workspaceToggleMoreMenu;
  window.workspaceCloseMoreMenu = workspaceCloseMoreMenu;
}

(function() {
  if (typeof window !== 'undefined' && window && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', _workspaceLayoutScheduleSync);
  }
  if (typeof document !== 'undefined' && document && typeof document.addEventListener === 'function') {
    document.addEventListener('click', function(event) {
      var target = event && event.target;
      if (!target || typeof target.closest !== 'function') return;
      if (target.closest('.taskbar-app, .standalone-panel-tab, .standalone-panel-zone-actions')) {
        setTimeout(_syncWorkspaceLayoutPresentation, 0);
      }
      if (!target.closest('#workspace-more-menu, #taskbar-more')) workspaceCloseMoreMenu();
    });
    document.addEventListener('keydown', function(event) {
      if (event && event.key === 'Escape') workspaceCloseMoreMenu();
    });
    document.addEventListener('DOMContentLoaded', function() {
      _workspaceBuildMoreMenu();
      _syncWorkspaceLayoutPresentation();
      var main = document.getElementById && document.getElementById('main');
      if (main && typeof main.addEventListener === 'function') {
        main.addEventListener('click', function(event) {
          if (_workspaceLayoutMode !== 'compact') return;
          var target = event && event.target;
          if (!target || typeof target.closest !== 'function') return;
          if (target.closest('.cell, .canvas-agent-card, .hierarchy-agent-row')) {
            setTimeout(workspaceHideAgents, 0);
          }
        });
      }
    });
  }
})();

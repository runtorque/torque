/* Compact workspace navigation: pinned panel launcher, overflow switcher,
 * and a unified keyboard-accessible Go To palette. Preferences are local to
 * this browser profile because they describe presentation, not daemon state. */

var PANEL_NAV_ITEMS = [
  { id: 'board', label: 'Board', keywords: 'tasks lanes kanban' },
  { id: 'chat', label: 'Chat', keywords: 'messages conversation' },
  { id: 'actions', label: 'Actions', keywords: 'commands templates prompts' },
  { id: 'initiatives', label: 'Planning', keywords: 'initiatives areas roadmap' },
  { id: 'thinking', label: 'Thinking', keywords: 'notes artifacts research' },
  { id: 'mission-control', label: 'Mission', keywords: 'control streams work' },
  { id: 'templates', label: 'Library', keywords: 'roles templates agents' },
  { id: 'history', label: 'History', keywords: 'sessions timeline' },
  { id: 'context', label: 'Context', keywords: 'memory project' },
  { id: 'engineer', label: 'Agent', keywords: 'details decisions behavior journal' },
  { id: 'events', label: 'Events', keywords: 'activity alerts log' },
  { id: 'supervisor', label: 'Supervisor', keywords: 'runtime sessions process' },
  { id: 'health', label: 'Health', keywords: 'metrics daemon performance' },
  { id: 'help', label: 'Help', keywords: 'documentation support' },
];

var _panelNavDefaultPins = ['board', 'engineer', 'events', 'health'];
var _panelNavStorageKey = 'torque.panel_navigation.v1';
var _panelNavFilter = '';
var _panelNavDragApp = '';

function _panelNavItem(app) {
  return PANEL_NAV_ITEMS.find(function(item) { return item.id === app; }) || null;
}

function _panelNavValidIds() {
  return PANEL_NAV_ITEMS.map(function(item) { return item.id; });
}

function _panelNavReadPins() {
  var stored = null;
  try {
    stored = JSON.parse(localStorage.getItem(_panelNavStorageKey) || 'null');
  } catch (_err) {}
  var source = stored && Array.isArray(stored.pinned) ? stored.pinned : _panelNavDefaultPins;
  var valid = _panelNavValidIds();
  var seen = {};
  return source.filter(function(app) {
    if (valid.indexOf(app) < 0 || seen[app]) return false;
    seen[app] = true;
    return true;
  });
}

function _panelNavWritePins(pinned) {
  try {
    localStorage.setItem(_panelNavStorageKey, JSON.stringify({ pinned: pinned }));
  } catch (_err) {}
}

function _panelNavIconBody(app) {
  var icons = {
    board: '<rect x="2" y="2.5" width="4.7" height="11" rx="1"/><rect x="9.3" y="2.5" width="4.7" height="7" rx="1"/>',
    chat: '<path d="M2.2 3.2h11.6v8H7l-3.6 2.5v-2.5H2.2z"/>',
    actions: '<path d="m3 12.8.7-3.2L10.8 2.5l2.7 2.7-7.1 7.1z"/><path d="m9.7 3.6 2.7 2.7"/>',
    initiatives: '<path d="M3 14V2.2M3.5 3h8l-1.7 2.4 1.7 2.4h-8"/>',
    thinking: '<path d="M7.15 5.55 4.9 9.65M8.85 5.55l2.25 4.1M5.8 11.4h4.4"/><circle cx="8" cy="4" r="1.8"/><circle cx="4" cy="11.4" r="1.8"/><circle cx="12" cy="11.4" r="1.8"/>',
    'mission-control': '<circle cx="8" cy="8" r="5.4"/><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2"/>',
    templates: '<path d="M3 2.3h7.2l2.8 2.8v8.6H3z"/><path d="M10.2 2.3v2.8H13M5.3 8h5.4M5.3 10.5h4"/>',
    history: '<path d="M3.2 4.3A6 6 0 1 1 2 8"/><path d="M3.2 1.8v2.5H.7M8 4.5V8l2.4 1.5"/>',
    context: '<path d="M2.5 3.5c0-.8.7-1.5 1.5-1.5h3.2c1 0 2 .3 2.8.9v10a4.8 4.8 0 0 0-2.8-.9H4c-.8 0-1.5-.7-1.5-1.5z"/><path d="M13.5 3.5c0-.8-.7-1.5-1.5-1.5H8.8c-1 0-2 .3-2.8.9v10a4.8 4.8 0 0 1 2.8-.9H12c.8 0 1.5-.7 1.5-1.5z"/>',
    engineer: '<circle cx="8" cy="5" r="2.5"/><path d="M3 13.5c.5-3 2.2-4.5 5-4.5s4.5 1.5 5 4.5"/>',
    events: '<path d="M8 2.2 14 13H2z"/><path d="M8 5.5v3.4M8 11.2h.01"/>',
    supervisor: '<rect x="2" y="2.5" width="12" height="11" rx="1.5"/><path d="M5 6h6M5 9h3M10.5 9h.5"/>',
    health: '<path d="M1.8 8h3l1.3-3.2 2.2 6.5L10 8h4.2"/>',
    help: '<circle cx="8" cy="8" r="6"/><path d="M6.5 6a1.7 1.7 0 1 1 2.4 1.5c-.7.3-.9.8-.9 1.5M8 11.7h.01"/>',
  };
  return icons[app] || '<rect x="3" y="3" width="10" height="10" rx="2"/>';
}

function panelNavIcon(app) {
  return '<span class="taskbar-app-icon" aria-hidden="true"><svg viewBox="0 0 16 16">'
    + _panelNavIconBody(app) + '</svg></span>';
}

function _navigationShortcutLabel(actionId) {
  if (typeof _kbDispatchBindingEntries !== 'function'
      || typeof kbBindingDisplayName !== 'function') return '';
  var entries = _kbDispatchBindingEntries(actionId);
  if (!entries.length) return '';
  var isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');
  var wanted = entries.find(function(entry) {
    return isMac ? entry.binding.meta : entry.binding.ctrl;
  }) || entries[0];
  return kbBindingDisplayName(wanted.binding);
}

function _panelNavVisibleApps() {
  if (typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()
      && typeof _standaloneVisiblePanelApps === 'function') {
    return _standaloneVisiblePanelApps();
  }
  return (typeof _activePanelApp !== 'undefined' && _activePanelApp) ? [_activePanelApp] : [];
}

function _panelNavSelectedApp() {
  if (typeof _standalonePanelsEnabled === 'function'
      && _standalonePanelsEnabled()
      && typeof _standalonePanelActiveApp === 'function') {
    return _standalonePanelActiveApp();
  }
  return typeof _activePanelApp !== 'undefined' ? (_activePanelApp || '') : '';
}

function _panelNavDecorateButton(button, item) {
  if (!button || !item || (button.dataset && button.dataset.panelNavReady === '1')) return;
  button.innerHTML = panelNavIcon(item.id)
    + '<span class="taskbar-app-label">' + esc(item.label) + '</span>';
  button.setAttribute('aria-label', item.label + ' panel');
  if (button.dataset) button.dataset.panelNavReady = '1';
  if (typeof button.addEventListener === 'function') {
    button.addEventListener('dragstart', function(event) { panelNavDragStart(event, item.id); });
    button.addEventListener('dragover', panelNavDragOver);
    button.addEventListener('drop', function(event) { panelNavDrop(event, item.id); });
    button.addEventListener('dragend', panelNavDragEnd);
  }
}

function panelNavSyncActive() {
  var host = document.getElementById('statusbar-panel-buttons');
  if (!host || !host.querySelectorAll) return;
  var pinned = _panelNavReadPins();
  var visibleApps = _panelNavVisibleApps();
  var selected = _panelNavSelectedApp();
  var visible = {};
  visibleApps.forEach(function(app) { visible[app] = true; });
  var buttons = {};
  host.querySelectorAll('.taskbar-app[data-app]').forEach(function(button) {
    var app = button.dataset ? button.dataset.app : '';
    var item = _panelNavItem(app);
    if (!app || !item) return;
    buttons[app] = button;
    _panelNavDecorateButton(button, item);
    var isPinned = pinned.indexOf(app) >= 0;
    var isVisible = !!visible[app];
    button.classList.toggle('active', isVisible);
    button.classList.toggle('selected', selected === app);
    button.classList.toggle('panel-nav-hidden', !isPinned && !isVisible);
    button.classList.toggle('panel-nav-transient', !isPinned && isVisible);
    button.draggable = isPinned;
    button.setAttribute('aria-pressed', isVisible ? 'true' : 'false');
    var shortcut = app === 'board' ? _navigationShortcutLabel('panel.toggle') : '';
    button.title = item.label + (isVisible ? ' (open)' : '')
      + (shortcut ? ' · ' + shortcut : '')
      + (isPinned ? ' · Drag to reorder' : '');
  });

  var more = document.getElementById('panel-nav-more-button');
  var restore = document.getElementById('taskbar-restore-layout');
  pinned.forEach(function(app) {
    if (buttons[app]) host.insertBefore(buttons[app], more || restore || null);
  });
  visibleApps.forEach(function(app) {
    if (pinned.indexOf(app) < 0 && buttons[app]) host.insertBefore(buttons[app], more || restore || null);
  });
  if (more) {
    host.insertBefore(more, restore || null);
    var moreMenu = document.getElementById('panel-nav-more-menu');
    more.classList.toggle('active', !!(moreMenu && !moreMenu.hidden));
  }
  if (restore) host.appendChild(restore);
  panelNavRenderMore(_panelNavFilter);
}

function panelNavOpenPanel(app) {
  closePanelNavMore();
  if (typeof togglePanel === 'function') togglePanel(app);
  panelNavSyncActive();
  return true;
}

function panelNavDragStart(event, app) {
  if (_panelNavReadPins().indexOf(app) < 0) return;
  _panelNavDragApp = app;
  if (event && event.dataTransfer) {
    try { event.dataTransfer.setData('text/plain', app); } catch (_err) {}
    event.dataTransfer.effectAllowed = 'move';
  }
  if (event && event.currentTarget && event.currentTarget.classList) {
    event.currentTarget.classList.add('is-dragging');
  }
}

function panelNavDragOver(event) {
  if (!_panelNavDragApp || !event) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
}

function panelNavDrop(event, targetApp) {
  if (event) event.preventDefault();
  var source = _panelNavDragApp;
  _panelNavDragApp = '';
  if (!source || source === targetApp) return;
  var pinned = _panelNavReadPins();
  var from = pinned.indexOf(source);
  var to = pinned.indexOf(targetApp);
  if (from < 0 || to < 0) return;
  pinned.splice(from, 1);
  pinned.splice(to, 0, source);
  _panelNavWritePins(pinned);
  panelNavSyncActive();
}

function panelNavDragEnd(event) {
  _panelNavDragApp = '';
  if (event && event.currentTarget && event.currentTarget.classList) {
    event.currentTarget.classList.remove('is-dragging');
  }
}

function panelNavTogglePin(app) {
  var pinned = _panelNavReadPins();
  var index = pinned.indexOf(app);
  if (index >= 0) pinned.splice(index, 1);
  else if (_panelNavItem(app)) pinned.push(app);
  _panelNavWritePins(pinned);
  panelNavSyncActive();
}

function panelNavMove(app, delta) {
  var pinned = _panelNavReadPins();
  var index = pinned.indexOf(app);
  var next = index + Number(delta || 0);
  if (index < 0 || next < 0 || next >= pinned.length) return;
  pinned.splice(index, 1);
  pinned.splice(next, 0, app);
  _panelNavWritePins(pinned);
  panelNavSyncActive();
}

function panelNavRenderMore(filter) {
  var root = document.getElementById('panel-nav-more-results');
  if (!root) return;
  var wanted = String(filter || '').trim().toLowerCase();
  var pinned = _panelNavReadPins();
  var visible = {};
  _panelNavVisibleApps().forEach(function(app) { visible[app] = true; });
  var items = PANEL_NAV_ITEMS.filter(function(item) {
    return !wanted || (item.label + ' ' + item.keywords).toLowerCase().indexOf(wanted) >= 0;
  });
  if (!items.length) {
    root.innerHTML = '<div class="panel-nav-more-empty">No panels match “' + esc(filter || '') + '”.</div>';
    return;
  }
  root.innerHTML = items.map(function(item) {
    var pinIndex = pinned.indexOf(item.id);
    var arg = JSON.stringify(item.id).replace(/"/g, '&quot;');
    return '<div class="panel-nav-more-row' + (visible[item.id] ? ' active' : '') + '">'
      + '<button type="button" class="panel-nav-more-open" onclick="panelNavOpenPanel(' + arg + ')">'
      + panelNavIcon(item.id) + '<span>' + esc(item.label) + '</span>'
      + (visible[item.id] ? '<small>Open</small>' : '') + '</button>'
      + '<div class="panel-nav-more-actions">'
      + (pinIndex >= 0
        ? '<button type="button" title="Move left" aria-label="Move ' + esc(item.label) + ' left"'
          + (pinIndex === 0 ? ' disabled' : '') + ' onclick="panelNavMove(' + arg + ',-1)">←</button>'
          + '<button type="button" title="Move right" aria-label="Move ' + esc(item.label) + ' right"'
          + (pinIndex === pinned.length - 1 ? ' disabled' : '') + ' onclick="panelNavMove(' + arg + ',1)">→</button>'
        : '')
      + '<button type="button" class="panel-nav-pin' + (pinIndex >= 0 ? ' is-pinned' : '') + '"'
      + ' title="' + (pinIndex >= 0 ? 'Unpin' : 'Pin') + '" aria-label="' + (pinIndex >= 0 ? 'Unpin ' : 'Pin ') + esc(item.label) + '"'
      + ' onclick="panelNavTogglePin(' + arg + ')">'
      + '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 2.5h6l-1 3 2 2v1H8.8V14L7.2 12V8.5H4v-1l2-2z"/></svg></button>'
      + '</div></div>';
  }).join('');
}

function panelNavFilterMore(query) {
  _panelNavFilter = String(query || '');
  panelNavRenderMore(_panelNavFilter);
}

function closePanelNavMore(restoreFocus) {
  var menu = document.getElementById('panel-nav-more-menu');
  var button = document.getElementById('panel-nav-more-button');
  if (menu) menu.hidden = true;
  if (button) {
    button.classList.remove('active');
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus && typeof button.focus === 'function') button.focus();
  }
}

function togglePanelNavMore(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  var menu = document.getElementById('panel-nav-more-menu');
  var button = document.getElementById('panel-nav-more-button');
  if (!menu || !button) return;
  if (!menu.hidden) {
    closePanelNavMore();
    return;
  }
  if (typeof closeAgentGroupQuickSwitcher === 'function') closeAgentGroupQuickSwitcher();
  _panelNavFilter = '';
  var search = document.getElementById('panel-nav-more-search');
  if (search) search.value = '';
  panelNavRenderMore('');
  menu.hidden = false;
  button.classList.add('active');
  button.setAttribute('aria-expanded', 'true');
  if (typeof button.getBoundingClientRect === 'function') {
    var rect = button.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 370)) + 'px';
    menu.style.bottom = Math.max(30, window.innerHeight - rect.top + 5) + 'px';
  }
  if (search && typeof search.focus === 'function') search.focus();
}

function panelNavMoreKeydown(event) {
  if (!event) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    closePanelNavMore(true);
    return;
  }
  if (event.key !== 'ArrowDown') return;
  var first = document.querySelector && document.querySelector('.panel-nav-more-open');
  if (first && typeof first.focus === 'function') {
    event.preventDefault();
    first.focus();
  }
}

function closeNavigationMenus() {
  closePanelNavMore();
  if (typeof closeAgentGroupQuickSwitcher === 'function') closeAgentGroupQuickSwitcher();
}

function panelNavInit() {
  panelNavSyncActive();
  var more = document.getElementById('panel-nav-more-button');
  if (more) {
    var shortcut = _navigationShortcutLabel('panel.open');
    more.title = 'Open panel switcher' + (shortcut ? ' · ' + shortcut : '');
  }
}

panelNavInit();

/* Unified groups, agents, and panels Go To palette. */

var _navigationPaletteScope = 'all';
var _navigationPaletteItems = [];
var _navigationPaletteFiltered = [];
var _navigationPaletteIndex = 0;

function _navigationPaletteAgentKind(agent) {
  return String(agent.kind || agent.agent_kind || agent.role || 'agent').replace(/_/g, ' ');
}

function _navigationPaletteAgentStatus(agent) {
  if (agent.status) return String(agent.status).replace(/_/g, ' ');
  if (agent.session_id) return 'running';
  return 'stopped';
}

function _navigationPaletteBuildItems(scope) {
  var items = [];
  if (scope === 'all' || scope === 'groups') {
    var active = typeof _activeGroup === 'function' ? _activeGroup() : '';
    _groupNamesSorted().forEach(function(group) {
      var count = ((state.groups || {})[group] || []).length;
      items.push({
        id: 'group:' + group,
        type: 'group',
        label: group,
        meta: count + ' agents',
        search: (group + ' group ' + count).toLowerCase(),
        value: group,
        active: group === active,
      });
    });
  }
  if (scope === 'all' || scope === 'agents') {
    Object.keys((state && state.agents) || {}).map(function(id) {
      return state.agents[id];
    }).filter(function(agent) {
      return agent && agent.cell_type === 'agent';
    }).sort(function(a, b) {
      var ag = String(a.group || '');
      var bg = String(b.group || '');
      if (ag !== bg) return ag.localeCompare(bg);
      return String(a.name || '').localeCompare(String(b.name || ''));
    }).forEach(function(agent) {
      var kind = _navigationPaletteAgentKind(agent);
      var status = _navigationPaletteAgentStatus(agent);
      items.push({
        id: 'agent:' + agent.id,
        type: 'agent',
        label: agent.name || agent.id,
        meta: [agent.group || 'No group', kind, status].join(' · '),
        search: [agent.name, agent.id, agent.group, kind, status].join(' ').toLowerCase(),
        value: agent.id,
        group: agent.group || '',
      });
    });
  }
  if (scope === 'all' || scope === 'panels') {
    var visible = {};
    _panelNavVisibleApps().forEach(function(app) { visible[app] = true; });
    PANEL_NAV_ITEMS.forEach(function(item) {
      items.push({
        id: 'panel:' + item.id,
        type: 'panel',
        label: item.label,
        meta: 'Panel',
        search: (item.label + ' ' + item.keywords + ' panel').toLowerCase(),
        value: item.id,
        active: !!visible[item.id],
      });
    });
  }
  return items;
}

function _navigationPaletteTypeLabel(type) {
  return type === 'group' ? 'Groups' : (type === 'agent' ? 'Agents' : 'Panels');
}

function _navigationPaletteItemIcon(item) {
  if (item.type === 'panel') return panelNavIcon(item.value);
  if (item.type === 'agent') return panelNavIcon('engineer');
  return '<span class="taskbar-app-icon" aria-hidden="true"><svg viewBox="0 0 16 16">'
    + '<circle cx="5" cy="5" r="2"/><circle cx="11" cy="5" r="2"/><circle cx="8" cy="11" r="2"/>'
    + '<path d="m6.5 6.3 1 2.7M9.5 6.3l-1 2.7"/></svg></span>';
}

function _navigationPaletteRender() {
  var root = document.getElementById('navigation-palette-results');
  if (!root) return;
  if (!_navigationPaletteFiltered.length) {
    root.innerHTML = '<div class="navigation-palette-empty">No matching groups, agents, or panels.</div>';
    return;
  }
  if (_navigationPaletteIndex >= _navigationPaletteFiltered.length) _navigationPaletteIndex = 0;
  var section = '';
  var html = '';
  _navigationPaletteFiltered.forEach(function(item, index) {
    var nextSection = _navigationPaletteTypeLabel(item.type);
    if (nextSection !== section) {
      section = nextSection;
      html += '<div class="navigation-palette-section">' + section + '</div>';
    }
    html += '<button type="button" class="navigation-palette-result'
      + (index === _navigationPaletteIndex ? ' is-selected' : '')
      + (item.active ? ' is-active' : '') + '" role="option" aria-selected="'
      + (index === _navigationPaletteIndex ? 'true' : 'false') + '" data-nav-index="' + index + '"'
      + ' onmouseenter="navigationPaletteSelect(' + index + ')" onclick="navigationPaletteActivate(' + index + ')">'
      + _navigationPaletteItemIcon(item)
      + '<span class="navigation-palette-result-copy"><strong>' + esc(item.label) + '</strong><small>' + esc(item.meta) + '</small></span>'
      + (item.active ? '<span class="navigation-palette-current">' + (item.type === 'group' ? 'Current' : 'Open') + '</span>' : '')
      + '</button>';
  });
  root.innerHTML = html;
  var selected = root.querySelector && root.querySelector('.navigation-palette-result.is-selected');
  if (selected && typeof selected.scrollIntoView === 'function') selected.scrollIntoView({ block: 'nearest' });
}

function navigationPaletteFilter(query) {
  var wanted = String(query || '').trim().toLowerCase();
  _navigationPaletteFiltered = _navigationPaletteItems.filter(function(item) {
    return !wanted || item.search.indexOf(wanted) >= 0;
  });
  _navigationPaletteIndex = wanted
    ? 0
    : Math.max(0, _navigationPaletteFiltered.findIndex(function(item) { return item.active; }));
  _navigationPaletteRender();
}

function navigationPaletteSelect(index) {
  _navigationPaletteIndex = Math.max(0, Math.min(Number(index || 0), _navigationPaletteFiltered.length - 1));
  _navigationPaletteRender();
}

function closeNavigationPalette() {
  var modal = document.getElementById('modal-navigation-palette');
  if (modal && modal.classList) modal.classList.remove('visible');
  _navigationPaletteItems = [];
  _navigationPaletteFiltered = [];
  _navigationPaletteIndex = 0;
}

function navigationPaletteActivate(index) {
  var item = _navigationPaletteFiltered[Number(index)];
  if (!item) return;
  closeNavigationPalette();
  if (item.type === 'group') {
    if (typeof setActiveGroup === 'function') setActiveGroup(item.value);
  } else if (item.type === 'agent') {
    if (item.group && typeof setActiveGroup === 'function') setActiveGroup(item.group);
    if (typeof focusAgent === 'function') focusAgent(item.value);
  } else if (item.type === 'panel') {
    panelNavOpenPanel(item.value);
  }
}

function navigationPaletteKeydown(event) {
  if (!event) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeNavigationPalette();
    return;
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (!_navigationPaletteFiltered.length) return;
    var delta = event.key === 'ArrowDown' ? 1 : -1;
    _navigationPaletteIndex = (_navigationPaletteIndex + delta + _navigationPaletteFiltered.length)
      % _navigationPaletteFiltered.length;
    _navigationPaletteRender();
    return;
  }
  if (event.key === 'Enter') {
    event.preventDefault();
    navigationPaletteActivate(_navigationPaletteIndex);
  }
}

function openNavigationPalette(scope) {
  var modal = document.getElementById('modal-navigation-palette');
  var input = document.getElementById('navigation-palette-input');
  var label = document.getElementById('navigation-palette-scope-label');
  if (!modal || !input) return false;
  closeNavigationMenus();
  _navigationPaletteScope = scope || 'all';
  _navigationPaletteItems = _navigationPaletteBuildItems(_navigationPaletteScope);
  _navigationPaletteFiltered = _navigationPaletteItems.slice();
  _navigationPaletteIndex = Math.max(0, _navigationPaletteFiltered.findIndex(function(item) { return item.active; }));
  input.value = '';
  input.placeholder = _navigationPaletteScope === 'groups'
    ? 'Switch to a group…'
    : (_navigationPaletteScope === 'panels' ? 'Open a panel…' : 'Go to a group, agent, or panel…');
  if (label) {
    label.textContent = _navigationPaletteScope === 'groups'
      ? 'Groups'
      : (_navigationPaletteScope === 'panels' ? 'Panels' : 'Groups, agents, and panels');
  }
  modal.classList.add('visible');
  _navigationPaletteRender();
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(function() { input.focus(); });
  else if (typeof input.focus === 'function') input.focus();
  return true;
}

function openGroupNavigator() { return openNavigationPalette('groups'); }
function openPanelNavigator() { return openNavigationPalette('panels'); }

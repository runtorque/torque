/* ------------------------------------------------------------------ */
/* Board view-state, persistence, and display controls                 */
/* ------------------------------------------------------------------ */

function _boardNormalizeFilterState(raw) {
  raw = raw || {};
  return {
    search_query: typeof raw.search_query === 'string' ? raw.search_query : '',
    quick_view: raw.quick_view === 'recent' || raw.quick_view === 'touched'
      ? raw.quick_view
      : '',
    filter_labels: Array.isArray(raw.filter_labels) ? raw.filter_labels.slice() : [],
    filter_actions: Array.isArray(raw.filter_actions) ? raw.filter_actions.slice() : [],
    filter_agents: Array.isArray(raw.filter_agents) ? raw.filter_agents.slice() : [],
    filter_health: Array.isArray(raw.filter_health) ? raw.filter_health.slice() : [],
    pre_filter_lane: typeof raw.pre_filter_lane === 'string' ? raw.pre_filter_lane : '',
  };
}

function _boardHydratePersistedFilters() {
  if (_boardFiltersByGroup) return;
  _boardFiltersByGroup = {};
  var persisted = (state && state.board_filters_by_group) || {};
  for (var group in persisted) {
    _boardFiltersByGroup[group] = _boardNormalizeFilterState(persisted[group]);
  }
}

function _boardCurrentFilterState() {
  return _boardNormalizeFilterState({
    search_query: _boardSearchQuery,
    quick_view: _boardQuickView,
    filter_labels: _boardFilterLabels,
    filter_actions: _boardFilterActions,
    filter_agents: _boardFilterAgents,
    filter_health: _boardFilterHealth,
    pre_filter_lane: _boardPreFilterLane,
  });
}

function _boardApplyFilterState(raw) {
  var next = _boardNormalizeFilterState(raw);
  _boardSearchQuery = next.search_query;
  _boardQuickView = next.quick_view;
  _boardFilterLabels = next.filter_labels;
  _boardFilterActions = next.filter_actions;
  _boardFilterAgents = next.filter_agents;
  _boardFilterHealth = next.filter_health;
  _boardPreFilterLane = next.pre_filter_lane;
}

function _boardIsDefaultFilterState(raw) {
  var next = _boardNormalizeFilterState(raw);
  return next.search_query === ''
    && next.quick_view === ''
    && next.filter_labels.length === 0
    && next.filter_actions.length === 0
    && next.filter_agents.length === 0
    && next.filter_health.length === 0
    && next.pre_filter_lane === '';
}

function _boardPersistFilterState() {
  _boardHydratePersistedFilters();
  var group = _currentGroup();
  if (!group) return;
  var next = _boardCurrentFilterState();
  if (_boardIsDefaultFilterState(next)) {
    delete _boardFiltersByGroup[group];
  } else {
    _boardFiltersByGroup[group] = next;
  }
  if (typeof send === 'function') {
    send({ cmd: 'board_set_filters', filters_by_group: _boardFiltersByGroup });
  }
}

function _boardNormalizeSavedView(raw) {
  raw = raw || {};
  var filters = _boardNormalizeFilterState(raw);
  var name = typeof raw.name === 'string' ? raw.name.trim() : '';
  if (!name) return null;
  return {
    name: name,
    search_query: filters.search_query,
    quick_view: filters.quick_view,
    filter_labels: filters.filter_labels,
    filter_actions: filters.filter_actions,
    filter_agents: filters.filter_agents,
    filter_health: filters.filter_health,
  };
}

function _boardHydrateSavedViews() {
  if (_boardSavedViewsByGroup) return;
  _boardSavedViewsByGroup = {};
  var persisted = (state && state.board_saved_views_by_group) || {};
  for (var group in persisted) {
    var views = Array.isArray(persisted[group]) ? persisted[group] : [];
    _boardSavedViewsByGroup[group] = [];
    for (var i = 0; i < views.length; i++) {
      var normalized = _boardNormalizeSavedView(views[i]);
      if (normalized) _boardSavedViewsByGroup[group].push(normalized);
    }
  }
}

function _boardNormalizeLaneSortMode(mode) {
  return mode === 'newest' || mode === 'oldest' || mode === 'due'
    ? mode
    : 'manual';
}

function _boardHydrateLaneSorts() {
  if (_boardLaneSortsByGroup) return;
  _boardLaneSortsByGroup = {};
  var persisted = (state && state.board_lane_sorts_by_group) || {};
  for (var group in persisted) {
    var raw = persisted[group];
    if (!raw || typeof raw !== 'object') continue;
    _boardLaneSortsByGroup[group] = {};
    for (var lane in raw) {
      _boardLaneSortsByGroup[group][lane] =
        _boardNormalizeLaneSortMode(raw[lane]);
    }
  }
}

function _boardNormalizeCardDensity(mode) {
  return mode === 'compact' || mode === 'detailed'
    ? mode
    : 'normal';
}

function _boardHydrateCardDensity() {
  if (_boardCardDensityByGroup) return;
  _boardCardDensityByGroup = {};
  var persisted = (state && state.board_card_density_by_group) || {};
  for (var group in persisted) {
    _boardCardDensityByGroup[group] =
      _boardNormalizeCardDensity(persisted[group]);
  }
}

function _boardCardDensityMode() {
  _boardHydrateCardDensity();
  var group = _currentGroup();
  if (!group) return 'normal';
  return _boardNormalizeCardDensity(_boardCardDensityByGroup[group]);
}

function _boardPersistCardDensity() {
  _boardHydrateCardDensity();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_card_density',
      card_density_by_group: _boardCardDensityByGroup,
    });
  }
}

function _boardCurrentGroupLaneSorts() {
  _boardHydrateLaneSorts();
  var group = _currentGroup();
  if (!group) return {};
  return _boardLaneSortsByGroup[group] || {};
}

function _boardLaneSortMode(lane) {
  if (!lane) return 'manual';
  var sorts = _boardCurrentGroupLaneSorts();
  return _boardNormalizeLaneSortMode(sorts[lane]);
}

function _boardPersistLaneSorts() {
  _boardHydrateLaneSorts();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_lane_sorts',
      lane_sorts_by_group: _boardLaneSortsByGroup,
    });
  }
}

var _boardHiddenWideLanesStorageKey = 'loom.board.hidden_wide_lanes_by_group';

function _boardHydrateHiddenWideLanes() {
  if (_boardHiddenWideLanesByGroup) return;
  _boardHiddenWideLanesByGroup = {};
  if (typeof localStorage === 'undefined') return;
  try {
    var raw = localStorage.getItem(_boardHiddenWideLanesStorageKey);
    var parsed = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      _boardHiddenWideLanesByGroup = parsed;
    }
  } catch (e) {
    _boardHiddenWideLanesByGroup = {};
  }
}

function _boardCurrentGroupHiddenWideLanes() {
  _boardHydrateHiddenWideLanes();
  var group = _currentGroup();
  if (!group) return {};
  return _boardHiddenWideLanesByGroup[group] || {};
}

function _boardHiddenWideLanesSignature() {
  var lanes = _boardCurrentGroupHiddenWideLanes();
  var keys = Object.keys(lanes).filter(function(lane) { return !!lanes[lane]; });
  keys.sort();
  return keys;
}

function _boardIsWideLaneCollapsed(lane) {
  if (!lane) return false;
  return !!_boardCurrentGroupHiddenWideLanes()[lane];
}

function _boardPersistHiddenWideLanes() {
  if (typeof localStorage === 'undefined') return;
  try {
    if (Object.keys(_boardHiddenWideLanesByGroup).length) {
      return localStorage.setItem(_boardHiddenWideLanesStorageKey, JSON.stringify(_boardHiddenWideLanesByGroup));
    }
    localStorage.removeItem(_boardHiddenWideLanesStorageKey);
  } catch (e) {}
}

function boardToggleWideLane(evt, lane) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  _boardHydrateHiddenWideLanes();
  var group = _currentGroup();
  if (!group || !lane) return;
  var groupLanes = _boardHiddenWideLanesByGroup[group] || {};
  if (groupLanes[lane]) delete groupLanes[lane];
  else groupLanes[lane] = true;
  if (Object.keys(groupLanes).length) _boardHiddenWideLanesByGroup[group] = groupLanes;
  else delete _boardHiddenWideLanesByGroup[group];
  _boardPersistHiddenWideLanes();
  _boardLaneRenderCache = {};
  renderBoard();
}

function _boardCurrentViewState() {
  var filters = _boardCurrentFilterState();
  return {
    search_query: filters.search_query,
    quick_view: filters.quick_view,
    filter_labels: filters.filter_labels,
    filter_actions: filters.filter_actions,
    filter_agents: filters.filter_agents,
    filter_health: filters.filter_health,
  };
}

function _boardCurrentGroupSavedViews() {
  _boardHydrateSavedViews();
  var group = _currentGroup();
  if (!group) return [];
  return _boardSavedViewsByGroup[group] || [];
}

function _boardPersistSavedViews() {
  _boardHydrateSavedViews();
  if (typeof send === 'function') {
    send({
      cmd: 'board_set_saved_views',
      saved_views_by_group: _boardSavedViewsByGroup,
    });
  }
}

function _boardViewMatchesCurrent(raw) {
  var view = _boardNormalizeSavedView(raw);
  if (!view) return false;
  var current = _boardCurrentViewState();
  return JSON.stringify(current) === JSON.stringify({
    search_query: view.search_query,
    quick_view: view.quick_view,
    filter_labels: view.filter_labels,
    filter_actions: view.filter_actions,
    filter_agents: view.filter_agents,
    filter_health: view.filter_health,
  });
}

function _boardSyncFiltersForCurrentGroup() {
  _boardHydratePersistedFilters();
  var group = _currentGroup() || '';
  if (group === _boardFilterStateGroup) return;
  var hasPriorGroup = _boardFilterStateGroup !== '';
  _boardFilterStateGroup = group;
  if (!hasPriorGroup
      && !_boardFiltersByGroup[group]
      && !_boardIsDefaultFilterState(_boardCurrentFilterState())) {
    return;
  }
  _boardApplyFilterState(_boardFiltersByGroup[group]);
}

function _boardCurrentViewKey() {
  var group = _currentGroup() || '';
  if (_boardShowSchedules) {
    return JSON.stringify({ group: group, view: 'schedules' });
  }
  var wideLayout = typeof _boardWideLayoutActive === 'function'
    && _boardWideLayoutActive(document.getElementById('panel-board'));
  return JSON.stringify({
    group: group,
    view: wideLayout ? 'wide' : 'lane',
    lane: wideLayout ? '' : (_boardSelectedLane || ''),
    show_archived: _boardShowArchived,
    search_query: _boardSearchQuery,
    quick_view: _boardQuickView,
    filter_labels: _boardFilterLabels.slice().sort(),
    filter_actions: _boardFilterActions.slice().sort(),
    filter_agents: _boardFilterAgents.slice().sort(),
    filter_health: _boardFilterHealth.slice().sort(),
  });
}

function _boardActivateViewState(key) {
  _boardActiveViewKey = key;
  var saved = _boardViewStates[key];
  var fallback = _boardNextViewDefault;
  _boardNextViewDefault = null;
  if (saved) {
    _boardCardsScrollTop = saved.scroll_top || 0;
    _boardRenderLimit = saved.render_limit || _boardDefaultRenderLimit || 50;
    _boardDoneRenderLimit = saved.done_render_limit || _boardDoneInitialRenderLimit || 30;
    return;
  }
  if (fallback) {
    _boardCardsScrollTop = fallback.scroll_top || 0;
    _boardRenderLimit = fallback.render_limit || _boardDefaultRenderLimit || 50;
    _boardDoneRenderLimit = fallback.done_render_limit || _boardDoneInitialRenderLimit || 30;
  }
}

function _boardSyncActiveViewState(cardsEl) {
  if (!_boardActiveViewKey) return;
  var saved = _boardViewStates[_boardActiveViewKey] || {
    scroll_top: 0,
    render_limit: _boardDefaultRenderLimit || 50,
    done_render_limit: _boardDoneInitialRenderLimit || 30,
  };
  if (cardsEl) {
    saved.scroll_top = cardsEl.scrollTop;
    _boardCardsScrollTop = cardsEl.scrollTop;
  } else {
    saved.scroll_top = _boardCardsScrollTop || 0;
  }
  saved.render_limit = _boardRenderLimit || _boardDefaultRenderLimit || 50;
  saved.done_render_limit = _boardDoneRenderLimit || _boardDoneInitialRenderLimit || 30;
  _boardViewStates[_boardActiveViewKey] = saved;
}

function _boardPrepareViewChange(resetNextView) {
  var cardsEl = document.getElementById('board-cards');
  if (!_boardActiveViewKey && cardsEl) {
    _boardActiveViewKey = _boardCurrentViewKey();
  }
  _boardSyncActiveViewState(cardsEl);
  _boardSkipViewCaptureOnce = true;
  if (resetNextView) {
    _boardNextViewDefault = {
      scroll_top: 0,
      render_limit: _boardDefaultRenderLimit || 50,
      done_render_limit: _boardDoneInitialRenderLimit || 30,
    };
  }
}

function _boardTimestamp(value) {
  if (!value || typeof value !== 'string') return Number.NaN;
  return Date.parse(value);
}

function _boardQuickViewLabel(mode) {
  return mode === 'recent'
    ? 'Recent tasks'
    : mode === 'touched'
      ? 'Recently touched'
      : '';
}

function _boardManualCompare(a, b) {
  var posDelta = (b.position || 0) - (a.position || 0);
  if (posDelta) return posDelta;
  return String(a.id || '').localeCompare(String(b.id || ''));
}

function _boardNewestCompare(a, b) {
  var aTime = _boardTimestamp(a.created_at);
  var bTime = _boardTimestamp(b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return bTime - aTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardManualCompare(a, b);
}

function _boardRecentlyTouchedCompare(a, b) {
  var aTime = _boardTimestamp(a.updated_at || a.created_at);
  var bTime = _boardTimestamp(b.updated_at || b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return bTime - aTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardNewestCompare(a, b);
}

function _boardDoneNewestCompare(a, b) {
  var aTime = _boardTimestamp(a.done_at || a.lane_entered_at || a.updated_at || a.created_at);
  var bTime = _boardTimestamp(b.done_at || b.lane_entered_at || b.updated_at || b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return bTime - aTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardRecentlyTouchedCompare(a, b);
}

function _boardOldestCompare(a, b) {
  var aTime = _boardTimestamp(a.created_at);
  var bTime = _boardTimestamp(b.created_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return aTime - bTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardManualCompare(a, b);
}

function _boardDueSoonestCompare(a, b) {
  var aTime = _boardTimestamp(a.scheduled_at);
  var bTime = _boardTimestamp(b.scheduled_at);
  var aValid = !Number.isNaN(aTime);
  var bValid = !Number.isNaN(bTime);
  if (aValid && bValid && aTime !== bTime) return aTime - bTime;
  if (aValid !== bValid) return aValid ? -1 : 1;
  return _boardNewestCompare(a, b);
}

function _boardCompareLaneTasks(a, b, lane) {
  if (_boardQuickView === 'recent') return _boardNewestCompare(a, b);
  if (_boardQuickView === 'touched') return _boardRecentlyTouchedCompare(a, b);
  var mode = _boardLaneSortMode(lane);
  if (lane === 'Done' && (mode === 'manual' || mode === 'newest')) {
    return _boardDoneNewestCompare(a, b);
  }
  if (mode === 'newest') return _boardNewestCompare(a, b);
  if (mode === 'oldest') return _boardOldestCompare(a, b);
  if (mode === 'due') return _boardDueSoonestCompare(a, b);
  return _boardManualCompare(a, b);
}

function _boardSummarizeNames(values, formatter) {
  if (!values || !values.length) return '';
  var shown = values.slice(0, 2).map(function(value) {
    return formatter ? formatter(value) : value;
  });
  var text = shown.join(', ');
  if (values.length > 2) text += ' +' + (values.length - 2);
  return text;
}

function _boardFilterSummaryText() {
  var parts = [];
  if (_boardQuickView) parts.push(_boardQuickViewLabel(_boardQuickView));
  if (_boardSearchQuery) parts.push('Search "' + _boardSearchQuery + '"');
  if (_boardFilterLabels.length) {
    parts.push('Labels ' + _boardSummarizeNames(_boardFilterLabels));
  }
  if (_boardFilterActions.length) {
    parts.push('Actions ' + _boardSummarizeNames(_boardFilterActions));
  }
  if (_boardFilterAgents.length) {
    parts.push('Agents ' + _boardSummarizeNames(_boardFilterAgents, function(agentId) {
      return _boardAgentName(agentId) || agentId;
    }));
  }
  if (_boardFilterHealth.length) {
    parts.push('Health ' + _boardSummarizeNames(_boardFilterHealth, function(stateName) {
      return _boardHealthDisplayName(stateName);
    }));
  }
  return parts.length ? parts.join(' · ') : 'No active filters';
}

function _renderBoardDisplayControls() {
  if (_boardShowSchedules || !_boardSelectedLane) return '';
  var sortMode = _boardLaneSortMode(_boardSelectedLane);
  var densityMode = _boardCardDensityMode();
  var archivedCount = _boardArchivedCount();
  var html = '<div class="board-view-menu-section">';
  html += '<label class="board-display-label" for="board-lane-sort-select">Sort</label>';
  html += '<select class="board-display-select" id="board-lane-sort-select"'
    + ' onchange="boardSetLaneSort(this.value)">';
  html += '<option value="manual"' + (sortMode === 'manual' ? ' selected' : '') + '>Manual</option>';
  html += '<option value="newest"' + (sortMode === 'newest' ? ' selected' : '') + '>Newest</option>';
  html += '<option value="oldest"' + (sortMode === 'oldest' ? ' selected' : '') + '>Oldest</option>';
  html += '<option value="due"' + (sortMode === 'due' ? ' selected' : '') + '>Due Soonest</option>';
  html += '</select>';
  html += '</div>';
  html += '<div class="board-view-menu-section">';
  html += '<label class="board-display-label" for="board-card-density-select">Density</label>';
  html += '<select class="board-display-select" id="board-card-density-select"'
    + ' onchange="boardSetCardDensity(this.value)">';
  html += '<option value="compact"' + (densityMode === 'compact' ? ' selected' : '') + '>Compact</option>';
  html += '<option value="normal"' + (densityMode === 'normal' ? ' selected' : '') + '>Normal</option>';
  html += '<option value="detailed"' + (densityMode === 'detailed' ? ' selected' : '') + '>Detailed</option>';
  html += '</select>';
  html += '</div>';
  html += '<button class="board-view-menu-toggle' + (_boardShowArchived ? ' active' : '') + '"'
    + ' onclick="boardToggleArchived()">'
    + (_boardShowArchived ? 'Hide archived' : 'Show archived')
    + (archivedCount ? ' <span class="board-filter-btn-count">' + archivedCount + '</span>' : '')
    + '</button>';
  return html;
}

function boardToggleViewMenu() {
  if (_boardViewMenuOpen) {
    _boardCloseViewMenu();
    renderBoard();
    return;
  }
  _boardCloseFilterDropdown();
  _boardOpenViewMenu();
  renderBoard();
}

function _boardOpenViewMenu() {
  var wrap = document.getElementById('board-view-menu-wrap');
  if (!wrap) return;
  var btn = wrap.querySelector('.board-filter-btn');
  if (!btn || typeof btn.getBoundingClientRect !== 'function') return;
  var rect = btn.getBoundingClientRect();

  _boardCloseViewMenu();
  _boardViewMenuOpen = true;

  var menu = document.createElement('div');
  menu.className = 'board-view-menu';
  menu.id = 'board-view-menu-active';
  menu.style.position = 'fixed';
  menu.style.top = (rect.bottom + 2) + 'px';
  menu.style.left = rect.left + 'px';
  menu.innerHTML = _renderBoardDisplayControls();
  document.body.appendChild(menu);

  requestAnimationFrame(function() {
    if (typeof menu.getBoundingClientRect !== 'function') return;
    var menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) {
      menu.style.left = Math.max(4, window.innerWidth - menuRect.width - 4) + 'px';
    }
    if (menuRect.bottom > window.innerHeight) {
      menu.style.top = Math.max(4, rect.top - menuRect.height - 2) + 'px';
    }
  });

  var handler = function(e) {
    var target = e && e.target;
    var insideMenu = !!(menu && typeof menu.contains === 'function' && menu.contains(target));
    var insideWrap = !!(wrap && typeof wrap.contains === 'function' && wrap.contains(target));
    if (!insideMenu && !insideWrap) {
      _boardCloseViewMenu();
      renderBoard();
    }
  };

  setTimeout(function() {
    if (document && typeof document.addEventListener === 'function') {
      document.addEventListener('mousedown', handler, true);
    }
  }, 0);

  _boardViewMenuCleanup = function() {
    if (document && typeof document.removeEventListener === 'function') {
      document.removeEventListener('mousedown', handler, true);
    }
    if (menu && menu.parentNode) menu.remove();
    _boardViewMenuCleanup = null;
  };
}

function _boardCloseViewMenu() {
  _boardViewMenuOpen = false;
  if (_boardViewMenuCleanup) _boardViewMenuCleanup();
}

/* Board module: filters. */

function boardUpdateSearch(query) {
  clearTimeout(_boardSearchTimer);
  _boardSearchTimer = setTimeout(function() {
    _boardPrepareViewChange(true);
    _boardSearchQuery = query;
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
    // Restore focus and cursor to search input
    var inp = document.getElementById('board-search-input');
    if (inp) { inp.focus(); inp.selectionStart = inp.selectionEnd = inp.value.length; }
  }, 200);
}

function boardToggleLabel(label) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardFilterLabels.splice(idx, 1);
  } else {
    _boardFilterLabels.push(label);
  }
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  renderBoard();
  _boardPersistFilterState();
}

function boardToggleAction(action) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardFilterActions.splice(idx, 1);
  } else {
    _boardFilterActions.push(action);
  }
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterLabel(label) {
  var idx = _boardFilterLabels.indexOf(label);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterLabels.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardRemoveFilterAction(action) {
  var idx = _boardFilterActions.indexOf(action);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterActions.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardToggleAgent(agentId) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterAgents.indexOf(agentId);
  if (idx >= 0) {
    _boardFilterAgents.splice(idx, 1);
  } else {
    _boardFilterAgents.push(agentId);
  }
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterAgent(agentId) {
  var idx = _boardFilterAgents.indexOf(agentId);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterAgents.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardToggleHealth(stateName) {
  _boardPrepareViewChange(true);
  var idx = _boardFilterHealth.indexOf(stateName);
  if (idx >= 0) {
    _boardFilterHealth.splice(idx, 1);
  } else {
    _boardFilterHealth.push(stateName);
  }
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  renderBoard();
  _boardPersistFilterState();
}

function boardRemoveFilterHealth(stateName) {
  var idx = _boardFilterHealth.indexOf(stateName);
  if (idx >= 0) {
    _boardPrepareViewChange(true);
    _boardFilterHealth.splice(idx, 1);
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
  }
}

function boardClearFilters() {
  _boardPrepareViewChange(true);
  _boardSearchQuery = '';
  _boardQuickView = '';
  _boardFilterLabels = [];
  _boardFilterActions = [];
  _boardFilterAgents = [];
  _boardFilterHealth = [];
  _boardCloseFilterDropdown();
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  if (_boardPreFilterLane) {
    _boardSelectedLane = _boardPreFilterLane;
    _boardPreFilterLane = '';
  }
  renderBoard();
  _boardPersistFilterState();
  if (typeof _boardPersistSelectedLane === 'function') {
    _boardPersistSelectedLane();
  }
}

function boardSaveCurrentView() {
  boardStartSaveView();
}

function boardStartSaveView() {
  if (_boardIsDefaultFilterState(_boardCurrentViewState())) return;
  _boardSavingView = true;
  _boardSavingViewName = '';
  _boardSaveViewFocus = true;
  renderBoard();
}

function boardUpdateSaveViewName(value) {
  _boardSavingViewName = value || '';
}

function boardSaveViewKeydown(e) {
  if (!e) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    boardSubmitSaveView();
  } else if (e.key === 'Escape') {
    e.preventDefault();
    boardCancelSaveView();
  }
}

function boardCancelSaveView() {
  _boardSavingView = false;
  _boardSavingViewName = '';
  _boardSaveViewFocus = false;
  renderBoard();
}

function boardSubmitSaveView(name) {
  _boardHydrateSavedViews();
  if (_boardIsDefaultFilterState(_boardCurrentViewState())) return;
  var group = _currentGroup();
  if (!group) return;
  if (typeof name !== 'string') {
    name = _boardSavingViewName;
    var input = document.getElementById('board-save-view-input');
    if (input && typeof input.value === 'string') name = input.value;
  }
  name = (name || '').trim();
  if (!name) return;
  var views = _boardSavedViewsByGroup[group] || [];
  var next = _boardCurrentViewState();
  next.name = name;
  var normalized = _boardNormalizeSavedView(next);
  var replaced = false;
  for (var i = 0; i < views.length; i++) {
    if (views[i].name === name) {
      views[i] = normalized;
      replaced = true;
      break;
    }
  }
  if (!replaced) views.push(normalized);
  _boardSavedViewsByGroup[group] = views;
  _boardSavingView = false;
  _boardSavingViewName = '';
  _boardSaveViewFocus = false;
  _boardPersistSavedViews();
  renderBoard();
}

function boardApplyQuickView(mode) {
  _boardPrepareViewChange(true);
  _boardQuickView = (_boardQuickView === mode) ? '' : mode;
  _boardPreFilterLane = '';
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  renderBoard();
  _boardPersistFilterState();
}

function boardApplySavedView(name) {
  var views = _boardCurrentGroupSavedViews();
  for (var i = 0; i < views.length; i++) {
    if (views[i].name !== name) continue;
    _boardPrepareViewChange(true);
    _boardSearchQuery = views[i].search_query;
    _boardQuickView = views[i].quick_view || '';
    _boardFilterLabels = views[i].filter_labels.slice();
    _boardFilterActions = views[i].filter_actions.slice();
    _boardFilterAgents = views[i].filter_agents.slice();
    _boardFilterHealth = (views[i].filter_health || []).slice();
    _boardPreFilterLane = '';
    _boardCardsScrollTop = 0;
    _boardResetRenderLimits();
    renderBoard();
    _boardPersistFilterState();
    return;
  }
}

function boardDeleteSavedView(name) {
  _boardHydrateSavedViews();
  var group = _currentGroup();
  if (!group) return;
  var views = _boardSavedViewsByGroup[group] || [];
  _boardSavedViewsByGroup[group] = views.filter(function(view) {
    return view.name !== name;
  });
  if (_boardSavedViewsByGroup[group].length === 0) {
    delete _boardSavedViewsByGroup[group];
  }
  _boardPersistSavedViews();
  renderBoard();
}

/* ---- Filter dropdowns ----------------------------------------------- */

function boardToggleLabelFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'label') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllLabelCounts();
  var names = Object.keys(counts).sort();
  if (!names.length) return;
  _boardFilterDropdownType = 'label';
  _boardOpenFilterDropdown('board-label-filter-wrap', 'label', names, counts, _boardFilterLabels);
}

function boardToggleActionFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'action') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllActionCounts();
  var names = Object.keys(counts).sort();
  if (!names.length) return;
  _boardFilterDropdownType = 'action';
  _boardOpenFilterDropdown('board-action-filter-wrap', 'action', names, counts, _boardFilterActions);
}

function boardToggleAgentFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'agent') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllAgentCounts();
  var ids = Object.keys(counts).sort(function(a, b) {
    return (_boardAgentName(a) || '').localeCompare(_boardAgentName(b) || '');
  });
  if (!ids.length) return;
  _boardFilterDropdownType = 'agent';
  _boardOpenFilterDropdown('board-agent-filter-wrap', 'agent', ids, counts, _boardFilterAgents);
}

function boardToggleHealthFilter() {
  _boardCloseViewMenu();
  if (_boardFilterDropdownType === 'health') {
    _boardCloseFilterDropdown();
    return;
  }
  _boardCloseFilterDropdown();
  var counts = _boardAllHealthCounts();
  var names = _boardHealthOrder.filter(function(name) {
    return counts[name] || _boardFilterHealth.indexOf(name) >= 0;
  });
  if (!names.length) return;
  _boardFilterDropdownType = 'health';
  _boardOpenFilterDropdown('board-health-filter-wrap', 'health', names, counts, _boardFilterHealth);
}

function _boardOpenFilterDropdown(wrapId, kind, names, counts, selectedArr) {
  var wrap = document.getElementById(wrapId);
  if (!wrap) return;
  var btn = wrap.querySelector('.board-filter-btn');
  if (!btn) return;
  var rect = btn.getBoundingClientRect();

  var dd = document.createElement('div');
  dd.className = 'board-filter-dropdown ui-popover';
  dd.id = 'board-filter-dropdown-active';
  dd.setAttribute('role', 'dialog');
  dd.setAttribute('aria-label', 'Filter board by ' + (kind === 'health' ? 'health state' : kind));
  dd.style.position = 'fixed';
  dd.style.top = (rect.bottom + 2) + 'px';
  dd.style.left = rect.left + 'px';

  var search = document.createElement('input');
  search.type = 'text';
  search.className = 'board-filter-dropdown-search';
  search.placeholder = 'Filter ' + (kind === 'health' ? 'health states' : (kind + 's')) + '\u2026';
  dd.appendChild(search);

  var list = document.createElement('div');
  list.className = 'board-filter-dropdown-list';
  dd.appendChild(list);

  function selectedValues() {
    if (kind === 'label') return _boardFilterLabels;
    if (kind === 'agent') return _boardFilterAgents;
    if (kind === 'health') return _boardFilterHealth;
    if (kind === 'action') return _boardFilterActions;
    return selectedArr || [];
  }

  function buildList(query) {
    list.innerHTML = '';
    var q = (query || '').toLowerCase();
    var filtered = [];
    for (var i = 0; i < names.length; i++) {
      var searchText = kind === 'agent' ? (_boardAgentName(names[i]) || names[i])
        : (kind === 'health' ? _boardHealthDisplayName(names[i]) : names[i]);
      if (q && searchText.toLowerCase().indexOf(q) < 0) continue;
      filtered.push(names[i]);
    }

    function addRow(name) {
      var row = document.createElement('label');
      row.className = 'board-filter-dropdown-item ui-menu-item';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selectedValues().indexOf(name) >= 0;
      (function(n) {
        cb.addEventListener('change', function() {
          if (kind === 'label') boardToggleLabel(n);
          else if (kind === 'agent') boardToggleAgent(n);
          else if (kind === 'health') boardToggleHealth(n);
          else boardToggleAction(n);
          buildList(search.value);
        });
      })(name);
      row.appendChild(cb);
      var span = document.createElement('span');
      span.className = 'board-filter-dropdown-name';
      var displayName = kind === 'agent' ? (_boardAgentName(name) || name)
        : (kind === 'health' ? _boardHealthDisplayName(name)
          : (kind === 'label' && isSystemLabel(name)) ? displayLabel(name) : name);
      span.textContent = displayName;
      row.appendChild(span);
      var badge = document.createElement('span');
      badge.className = 'board-filter-dropdown-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count';
      badge.textContent = counts[name];
      row.appendChild(badge);
      list.appendChild(row);
    }

    if (kind === 'label') {
      var sysNames = [], userNames = [];
      for (var i = 0; i < filtered.length; i++) {
        if (isSystemLabel(filtered[i])) sysNames.push(filtered[i]);
        else userNames.push(filtered[i]);
      }
      if (sysNames.length) {
        var hdr = document.createElement('div');
        hdr.className = 'board-filter-dropdown-header ui-menu-label';
        hdr.textContent = 'System';
        list.appendChild(hdr);
        for (var i = 0; i < sysNames.length; i++) addRow(sysNames[i]);
      }
      if (userNames.length) {
        var hdr = document.createElement('div');
        hdr.className = 'board-filter-dropdown-header ui-menu-label';
        hdr.textContent = 'Labels';
        list.appendChild(hdr);
        for (var i = 0; i < userNames.length; i++) addRow(userNames[i]);
      }
    } else {
      for (var i = 0; i < filtered.length; i++) addRow(filtered[i]);
    }
  }

  buildList('');
  search.addEventListener('input', function() { buildList(search.value); });

  document.body.appendChild(dd);
  btn.setAttribute('aria-expanded', 'true');
  _boardFilterDropdownTriggerWrapId = wrapId;

  dd.addEventListener('keydown', function(e) {
    if (!e || e.key !== 'Escape') return;
    e.preventDefault();
    e.stopPropagation();
    _boardCloseFilterDropdown({ restoreFocus: true });
  });

  // Adjust if dropdown overflows viewport
  requestAnimationFrame(function() {
    var ddRect = dd.getBoundingClientRect();
    if (ddRect.right > window.innerWidth) {
      dd.style.left = Math.max(0, window.innerWidth - ddRect.width - 4) + 'px';
    }
    if (ddRect.bottom > window.innerHeight) {
      dd.style.top = Math.max(0, rect.top - ddRect.height - 2) + 'px';
    }
  });

  search.focus();

  // Close on outside click
  var handler = function(e) {
    if (!dd.contains(e.target) && !e.target.closest('.board-filter-btn')) {
      _boardCloseFilterDropdown();
    }
  };
  setTimeout(function() {
    document.addEventListener('mousedown', handler, true);
  }, 0);

  _boardFilterDropdownCleanup = function() {
    document.removeEventListener('mousedown', handler, true);
    if (dd.parentNode) dd.remove();
    _boardFilterDropdownCleanup = null;
  };
}

function _boardCloseFilterDropdown(options) {
  options = options || {};
  var triggerWrapId = _boardFilterDropdownTriggerWrapId;
  _boardFilterDropdownType = null;
  var expanded = document.querySelectorAll('.board-filter-btn[aria-expanded="true"]');
  for (var i = 0; i < expanded.length; i++) expanded[i].setAttribute('aria-expanded', 'false');
  if (_boardFilterDropdownCleanup) _boardFilterDropdownCleanup();
  _boardFilterDropdownTriggerWrapId = '';
  if (options.restoreFocus && triggerWrapId) {
    var wrap = document.getElementById(triggerWrapId);
    var trigger = wrap && wrap.querySelector ? wrap.querySelector('.board-filter-btn') : null;
    if (trigger && typeof trigger.focus === 'function') trigger.focus();
  }
}

/* ---- Keyboard nav --------------------------------------------------- */

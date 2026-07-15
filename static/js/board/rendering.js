/* Board module: rendering. */

function showTaskMessages(taskId) {
  var t = state.board_tasks[taskId];
  if (!t) return;
  // The compact preview carries a single-entry placeholder whose length
  // mirrors "has messages" truthfully, so gate cheaply before paying the
  // hydrate cost on cards with no messages.
  if (!t.messages || !t.messages.length) return;
  if (typeof _compactModeActive === 'function'
      && _compactModeActive()
      && typeof _compactTaskHasFullDetail === 'function'
      && !_compactTaskHasFullDetail(t)
      && typeof ensureTaskDetail === 'function') {
    ensureTaskDetail(taskId, function() { showTaskMessages(taskId); });
    return;
  }
  var html = '';
  var total = t.messages.length;
  for (var i = total - 1; i >= 0; i--) {
    var m = t.messages[i];
    var seq = '<span class="task-msg-seq">#' + (i + 1) + '</span>';
    var badge = '<span class="task-msg-badge task-msg-' + esc(m.action) + '">' + esc(m.action) + '</span>';
    var time = m.timestamp ? _relativeTime(m.timestamp) : '';
    var absTime = m.timestamp ? new Date(m.timestamp * 1000).toLocaleString() : '';
    var agent = m.agent ? ' <span class="task-msg-agent">' + esc(m.agent) + '</span>' : '';
    html += '<div class="task-msg-row">';
    html += '<div class="task-msg-header">' + seq + badge + agent;
    if (time) html += '<span class="task-msg-time" title="' + esc(absTime) + '">' + esc(time) + '</span>';
    html += '</div>';
    if (m.message) html += '<div class="task-msg-text">' + esc(m.message) + '</div>';
    html += '</div>';
  }
  document.getElementById('task-messages-title').textContent = 'Activity \u2014 ' + (t.task || '').substring(0, 50);
  document.getElementById('task-messages-content').innerHTML = html;
  document.getElementById('modal-task-messages').classList.add('visible');
}

function boardToggleTaskCollapse(taskId) {
  _boardCollapsedTasks[taskId] = !_boardCollapsedTasks[taskId];
  renderBoard();
}

function boardCardMouseEnter(taskId) {
  _boardHoveredTask = taskId || '';
}

function boardCardMouseLeave(taskId) {
  if (_boardHoveredTask === taskId) _boardHoveredTask = '';
}

function _boardRestoreRenderedState() {
  var tabsEl = document.getElementById('board-lane-tabs');
  if (tabsEl) {
    tabsEl.scrollLeft = _boardScrollLeft;
    tabsEl.addEventListener('scroll', function() {
      _boardScrollLeft = tabsEl.scrollLeft;
      boardUpdateScrollArrows();
    });
    boardUpdateScrollArrows();
  }

  var cardsEl = document.getElementById('board-cards');
  if (cardsEl) {
    cardsEl.scrollTop = _boardCardsScrollTop;
    cardsEl.addEventListener('scroll', function() {
      _boardCardsScrollTop = cardsEl.scrollTop;
      _boardSyncActiveViewState(cardsEl);
      // Load more when within 100px of the bottom
      if (cardsEl.scrollTop + cardsEl.clientHeight >= cardsEl.scrollHeight - 100) {
        boardLoadMore(_boardSelectedLane);
      }
    });
    // Click on empty space clears selection
    cardsEl.addEventListener('click', function(e) {
      var clickedEmptyWideLane = !!(
        e.target
        && e.target.classList
        && e.target.classList.contains('board-wide-lane-body')
      );
      if ((e.target === cardsEl || clickedEmptyWideLane) && _boardSelectedCount() > 0) {
        boardClearSelection();
      }
    });
  }

  // Wide-layout: each lane column scrolls independently; restore per-lane
  // scrollTop so re-renders don't jump operators back to the top.
  var panelEl = document.getElementById('panel-board');
  if (panelEl && typeof panelEl.querySelectorAll === 'function') {
    var laneBodies = panelEl.querySelectorAll('.board-wide-lane-body[data-lane]');
    for (var li = 0; li < laneBodies.length; li++) {
      _boardBindWideLaneBodyScroll(laneBodies[li]);
    }
  }
}

function _boardBindWideLaneBodyScroll(body) {
  if (!body || !body.dataset) return;
  var lane = body.dataset.lane;
  if (!lane) return;
  var saved = _boardWideLaneScrollTops[lane];
  if (typeof saved === 'number') body.scrollTop = saved;
  if (body._boardScrollBoundLane === lane) return;
  body._boardScrollBoundLane = lane;
  body.addEventListener('scroll', function() {
    _boardWideLaneScrollTops[lane] = body.scrollTop;
    if (body.scrollTop + body.clientHeight >= body.scrollHeight - 100) {
      boardLoadMore(lane);
    }
  });
}

function _boardAfterRenderLayout() {
  requestAnimationFrame(function() {
    var tabsEl = document.getElementById('board-lane-tabs');
    if (tabsEl) {
      // Ensure active tab is fully visible
      var activeTab = tabsEl.querySelector('.board-lane-tab.active');
      if (activeTab) {
        var tabLeft = activeTab.offsetLeft;
        var tabRight = tabLeft + activeTab.offsetWidth;
        var viewLeft = tabsEl.scrollLeft;
        var viewRight = viewLeft + tabsEl.clientWidth;
        if (tabLeft < viewLeft) {
          tabsEl.scrollLeft = tabLeft;
        } else if (tabRight > viewRight) {
          tabsEl.scrollLeft = tabRight - tabsEl.clientWidth;
        }
      }

      _boardScrollLeft = tabsEl.scrollLeft;
      boardUpdateScrollArrows();
    }
    if (_boardRevealFocusOnRender) {
      _boardRevealFocusOnRender = false;
      var focusedCard = document.querySelector('.board-card.focused');
      if (focusedCard && typeof focusedCard.scrollIntoView === 'function') {
        focusedCard.scrollIntoView({ block: 'nearest' });
      }
    }
  });
}

function _boardChildrenOfVisibleTasks(allTasks) {
  var childrenOf = {};
  for (var taskId in allTasks) {
    var task = allTasks[taskId];
    if (task.parent_task_id && allTasks[task.parent_task_id]) {
      if (!childrenOf[task.parent_task_id]) childrenOf[task.parent_task_id] = [];
      childrenOf[task.parent_task_id].push(task);
    }
  }
  for (var parentId in childrenOf) {
    childrenOf[parentId].sort(function(a, b) {
      return (a.pipeline_depth - b.pipeline_depth)
        || (a.created_at || '').localeCompare(b.created_at || '');
    });
  }
  return childrenOf;
}

function _boardRootTasksForLane(lane, allTasks, model) {
  if (model && model.rootTasksByLane) return model.rootTasksByLane[lane] || [];
  allTasks = allTasks || _boardVisibleTasks();
  return _boardTasksInLane(lane).filter(function(task) {
    return !task.parent_task_id || !allTasks[task.parent_task_id];
  });
}

function _boardRenderAddTaskSection(lane) {
  var html = '';
  var escLane = esc(lane).replace(/'/g, "\\'");
  var activeLane = lane === _boardSelectedLane;
  if (_boardAddingTask && activeLane) {
    var addTaskLaneOptions = _boardAddTaskLaneOptions();
    if (!_boardAddingTaskLane || addTaskLaneOptions.indexOf(_boardAddingTaskLane) === -1) {
      _boardAddingTaskLane = _boardDefaultAddTaskLane();
    }
    html += '<div class="board-add-task board-add-task-active"'
      + ' ondragover="boardInlineDragOver(event)" ondragleave="boardInlineDragLeave(event)"'
      + ' ondrop="boardInlineDrop(event)">';
    html += '<div style="position:relative">';
    html += '<textarea class="board-add-input" id="board-add-task-input" rows="1"'
      + ' placeholder="Task description..."'
      + ' onkeydown="boardAddTaskKeydown(event)"'
      + ' oninput="boardAddTaskInput(this)"'
      + ' onblur="boardCancelAddTask()">' + esc(_boardAddingTaskDraft) + '</textarea>';
    html += '<div id="board-add-label-dropdown" class="deps-dropdown ui-popover" role="listbox"'
      + ' aria-label="Matching labels" style="display:none"></div>';
    html += '</div>';
    if (_boardInlineAttachments.length) {
      html += '<div class="inline-att-chips">';
      for (var ai = 0; ai < _boardInlineAttachments.length; ai++) {
        html += '<span class="inline-att-chip">[Image #' + (ai + 1) + ']'
          + '<button type="button" class="inline-att-chip-remove" aria-label="Remove image ' + (ai + 1) + '" onmousedown="event.preventDefault();boardInlineRemoveAtt(' + ai + ')">&times;</button>'
          + '</span>';
      }
      html += '</div>';
    }
    html += '<div class="board-add-toolbar">';
    html += '<button class="board-add-toolbar-btn board-add-clear-btn" onmousedown="event.preventDefault();boardClearAddTask()">Clear</button>';
    html += '<div class="board-add-toolbar-right">';
    html += '<div class="board-add-dropdown" id="board-add-agent-wrap">';
    var agentLabel = _boardAddingTaskAgent ? _boardAgentName(_boardAddingTaskAgent) : 'No agent';
    html += '<button type="button" id="board-add-agent-trigger" class="board-add-toolbar-btn"'
      + ' aria-haspopup="menu" aria-expanded="false"'
      + ' onclick="event.stopPropagation();boardToggleAgentDropdown(event)">'
      + esc(agentLabel) + ' &#9662;</button>';
    html += '</div>';
    html += '<div class="board-add-dropdown" id="board-add-lane-wrap">';
    html += '<button type="button" id="board-add-lane-trigger" class="board-add-toolbar-btn"'
      + ' aria-haspopup="menu" aria-expanded="false"'
      + ' onclick="event.stopPropagation();boardToggleLaneDropdown(event)">'
      + esc(_boardAddingTaskLane) + ' &#9662;</button>';
    html += '</div>';
    html += '<button class="board-add-toolbar-btn board-add-submit-btn" onmousedown="event.preventDefault();boardSubmitAddTask()">Submit &#10132;</button>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="board-add-task" onclick="boardStartAddTaskForLane(\'' + escLane + '\')">';
  html += '<span>+ Add task</span>';
  html += '</div>';
  return html;
}

function _boardRenderWideAddTaskSection() {
  var lane = _boardSelectedLane || _boardDefaultAddTaskLane();
  if (!lane) return '';
  var html = '<div class="board-wide-add-task-wrap">';
  html += _boardRenderAddTaskSection(lane);
  html += '</div>';
  return html;
}

function _boardRenderLaneCards(rootTasks, childrenOf, renderLimit, renderOffset) {
  var renderState = {
    remaining: Math.max(0, renderLimit || 0),
    rendered: 0,
    limitHit: false,
    skip: Math.max(0, renderOffset || 0),
  };
  var html = '';
  for (var j = 0; j < rootTasks.length; j++) {
    if (renderState.remaining <= 0) {
      renderState.limitHit = true;
      break;
    }
    html += _renderBoardCard(rootTasks[j], childrenOf, 0, renderState);
  }
  return {
    html: html,
    renderedCards: renderState.rendered,
    limitHit: renderState.limitHit,
    skippedCards: Math.max(0, (renderOffset || 0) - renderState.skip),
  };
}

function _boardRenderableCardCount(task, childrenOf, depth) {
  if (!task) return 0;
  var count = 1;
  var children = (childrenOf && childrenOf[task.id]) || [];
  if (!children.length || _boardCollapsedTasks[task.id]) return count;
  for (var i = 0; i < children.length; i++) {
    count += _boardRenderableCardCount(children[i], childrenOf, (depth || 0) + 1);
  }
  return count;
}

function _boardRenderableCardCountForRoots(rootTasks, childrenOf) {
  var count = 0;
  for (var i = 0; i < rootTasks.length; i++) {
    count += _boardRenderableCardCount(rootTasks[i], childrenOf, 0);
  }
  return count;
}

function _boardLoadMoreHtml(lane, remaining) {
  var escLane = esc(lane).replace(/'/g, "\\'");
  return '<div class="board-load-more" data-board-load-more-lane="' + esc(lane) + '"'
    + ' onclick="boardLoadMore(\'' + escLane + '\')">'
    + remaining + ' more card' + (remaining === 1 ? '' : 's')
    + ' — click or scroll to load</div>';
}

function _boardRenderLaneSection(lane, model, filtersActive, skipAddTask) {
  var html = '';
  var childrenOf = (model && model.childrenOf) || {};
  var rootTasks = _boardRootTasksForLane(lane, model ? model.visibleTasks : null, model);
  var totalCards = _boardRenderableCardCountForRoots(rootTasks, childrenOf);
  var renderLimit = _boardRenderLimitValue(lane);

  if (!skipAddTask) {
    html += _boardRenderAddTaskSection(lane);
  }

  var archiveSuggestion = _renderBoardArchiveSuggestion(lane, model);
  if (archiveSuggestion) html += archiveSuggestion;

  var backlogDispatchNote = _boardBacklogDispatchNote(rootTasks, lane);
  if (backlogDispatchNote) {
    html += _renderBoardMessageState(backlogDispatchNote, true);
  }

  if (rootTasks.length === 0) {
    html += _renderBoardMessageState(
      _boardEmptyStateForLane(
        lane,
        _boardLanePoolTasks(lane, model),
        rootTasks,
        filtersActive,
      ),
      false,
    );
  }

  var rendered = _boardRenderLaneCards(rootTasks, childrenOf, renderLimit);
  html += rendered.html;
  if (totalCards > rendered.renderedCards) {
    var remaining = totalCards - rendered.renderedCards;
    html += _boardLoadMoreHtml(lane, remaining);
  }

  return {
    html: html,
    rootTasks: rootTasks,
    renderLimit: renderLimit,
    renderedCards: rendered.renderedCards,
    totalCards: totalCards,
  };
}

function _boardRenderWideLaneColumn(lane, model, filtersActive) {
  var escLane = esc(lane).replace(/'/g, "\\'");
  var laneCount = _boardLaneCount(lane, model);
  var active = lane === _boardSelectedLane;
  var collapsed = typeof _boardIsWideLaneCollapsed === 'function'
    && _boardIsWideLaneCollapsed(lane);
  var section = collapsed
    ? { html: '', bodyHtml: '', rootTasks: [], renderLimit: 0, renderedCards: 0, totalCards: 0 }
    : _boardRenderLaneSection(lane, model, filtersActive, true);
  var bodyHtml = section.html || '';
  var html = '<section class="board-wide-lane' + (active ? ' active' : '')
    + (collapsed ? ' board-wide-lane-collapsed' : '') + '"'
    + ' data-lane="' + esc(lane) + '" data-board-lane-column="1">';
  html += '<div class="board-wide-lane-head">';
  if (collapsed) {
    html += '<button class="board-wide-lane-collapsed-toggle"'
      + ' onclick="boardToggleWideLane(event,\'' + escLane + '\')"'
      + ' title="Show ' + esc(lane) + ' lane"'
      + ' aria-label="Show ' + esc(lane) + ' lane">'
      + '<span class="board-wide-lane-name">' + esc(lane) + '</span>'
      + '<span class="board-wide-lane-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + laneCount + '</span>'
      + '</button>';
    html += '</div>';
    html += '</section>';
    section.bodyHtml = '';
    section.columnHtml = html;
    section.html = html;
    return section;
  }
  html += '<div class="board-wide-lane-title-row">';
  html += '<button class="board-wide-lane-select" onclick="boardSelectLane(\'' + escLane + '\')">';
  html += '<span class="board-wide-lane-name">' + esc(lane) + '</span>';
  html += '<span class="board-wide-lane-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + laneCount + '</span>';
  html += '</button>';
  html += '<button class="board-wide-lane-toggle"'
    + ' onclick="boardToggleWideLane(event,\'' + escLane + '\')"'
    + ' title="Hide ' + esc(lane) + ' lane"'
    + ' aria-label="Hide ' + esc(lane) + ' lane">&#9712;</button>';
  html += '</div>';
  if (filtersActive && active) {
    html += '<div class="board-wide-lane-summary">' + esc(_boardFilterSummaryText()) + '</div>';
  }
  html += '</div>';
  html += '<div class="board-wide-lane-body board-lane-drop-target"'
    + ' data-lane="' + esc(lane) + '" data-board-lane-drop="1"'
    + ' ondragover="boardLaneTabDragOver(event)"'
    + ' ondragleave="boardLaneTabDragLeave(event)"'
    + ' ondrop="boardLaneTabDrop(event)">';
  html += bodyHtml;
  html += '</div>';
  html += '</section>';
  section.bodyHtml = bodyHtml;
  section.columnHtml = html;
  section.html = html;
  return section;
}

function _boardWideGridTemplate(lanes) {
  var cols = [];
  for (var i = 0; i < (lanes || []).length; i++) {
    cols.push((typeof _boardIsWideLaneCollapsed === 'function'
      && _boardIsWideLaneCollapsed(lanes[i]))
      ? '32px'
      : 'minmax(220px, 1fr)');
  }
  return cols.join(' ');
}

/* ---- Render --------------------------------------------------------- */

function renderBoard() {
  var panel = document.getElementById('panel-board');
  if (!panel) return;
  if (!_boardPanelVisible()) {
    _boardClearLaneEntryRefresh();
    return;
  }
  var panelState = _captureSurfaceState(panel);
  var quickEditRefocusTask = _boardQuickEditRefocusTask;
  var quickEditRefocusKind = _boardQuickEditRefocusKind;
  var skipRestoreFocus = _boardAddTaskFocus || _boardSaveViewFocus || !!quickEditRefocusTask;
  var restoreState = skipRestoreFocus ? null : panelState;
  if (quickEditRefocusTask && panelState) {
    restoreState = {
      focus: null,
      scrolls: (panelState.scrolls || []).slice(),
    };
  }
  _boardSetQuickEditRefocus('', '');
  var queuedTaskDeltaBatch = _boardConsumeQueuedTaskDeltas();
  _boardSyncFiltersForCurrentGroup();
  if (typeof _boardSyncSelectedLaneForCurrentGroup === 'function') {
    _boardSyncSelectedLaneForCurrentGroup(_boardVisibleLanes());
  }
  _boardHydrateSavedViews();
  _boardHydrateLaneSorts();
  _boardHydrateCardDensity();

  // Preserve scroll + draft before DOM rebuild
  var _cardsEl = document.getElementById('board-cards');
  if (_boardSkipViewCaptureOnce) {
    _boardSkipViewCaptureOnce = false;
  } else {
    if (!_boardActiveViewKey && _cardsEl) {
      _boardActiveViewKey = _boardCurrentViewKey();
    }
    _boardSyncActiveViewState(_cardsEl);
  }
  if (_boardAddingTask) {
    var _inp = document.getElementById('board-add-task-input');
    if (_inp) _boardAddingTaskDraft = _inp.value;
  }

  var lanes = _boardVisibleLanes();
  if (typeof _boardSyncSelectedLaneForCurrentGroup === 'function') {
    _boardSyncSelectedLaneForCurrentGroup(lanes);
  }
  if (!lanes.length) {
    panel.innerHTML = '<div class="board-empty ui-state ui-state--empty">No lanes are configured for this group.</div>';
    _boardLastRenderShellKey = '';
    _boardLastToolbarShapeKey = '';
    return;
  }

  // Default to first lane if selected lane is invalid (skip when schedules tab is active)
  if (!_boardShowSchedules && (!_boardSelectedLane || lanes.indexOf(_boardSelectedLane) === -1)) {
    _boardSelectedLane = lanes[0];
  }

  var html = '';
  var filtersActive = _boardHasActiveFilters();

  // Restore saved lane when filters clear (e.g. user backspaces search to empty)
  if (!filtersActive && _boardPreFilterLane) {
    _boardSelectedLane = _boardPreFilterLane;
    _boardPreFilterLane = '';
  }
  var wideShell = _boardWideShellActive(panel);
  var wideLayout = _boardWideLayoutActive(panel);
  _boardNormalizeAddingTaskLane();
  var shellKey = _boardRenderShellKey(lanes, wideShell, wideLayout);
  if (_boardTryPatchTaskDeltas(
      panel,
      queuedTaskDeltaBatch,
      lanes,
      filtersActive,
      wideLayout,
      shellKey,
      restoreState,
      quickEditRefocusTask,
      quickEditRefocusKind)) {
    return;
  }
  var renderLanes = wideLayout ? lanes : [_boardSelectedLane];
  var renderModel = _boardBuildRenderModel(renderLanes);
  _boardEnsureDispatchEligibilityRefs(_currentGroup(), renderModel);

  // Search & filter toolbar
  var labelCounts = _boardAllLabelCounts(renderModel);
  var actionCounts = _boardAllActionCounts(renderModel);
  var agentCounts = _boardAllAgentCounts(renderModel);
  var healthCounts = _boardAllHealthCounts(renderModel);
  var archivedCount = _boardArchivedCount(renderModel);
  var hasLabels = Object.keys(labelCounts).length > 0;
  var hasActions = Object.keys(actionCounts).length > 0;
  var hasAgents = Object.keys(agentCounts).length > 0;
  var hasHealth = Object.keys(healthCounts).length > 0;
  var savedViews = _boardCurrentGroupSavedViews();
  var hasSavedViews = savedViews.length > 0;
  var hasQuickViews = _boardGroupTaskCount(renderModel) > 0 || _boardQuickView !== '';
  var currentViewSavable = !_boardIsDefaultFilterState(_boardCurrentViewState());
  var schedCount = _boardScheduleCount();
  var showToolbar = _boardGroupTaskCount(renderModel) > 0
    || hasLabels || hasActions || hasAgents || hasHealth
    || _boardSearchQuery || _boardFilterLabels.length
    || _boardFilterActions.length || _boardFilterAgents.length
    || _boardFilterHealth.length
    || hasSavedViews || archivedCount || _boardShowArchived;
  var showSavedViewsRow = currentViewSavable || hasSavedViews || _boardSavingView;
  var showViewMenuButton = !!_boardSelectedLane;
  var recentQuickViewActive = _boardQuickView === 'recent' || _boardQuickView === 'touched';

  var searchHint = 'Searches titles, IDs, actions, agents, labels, and verification notes.';
  if (typeof _compactModeActive === 'function' && _compactModeActive()) {
    searchHint += ' Description bodies are searched only for tasks whose detail has been opened.';
  }
  html += '<div class="board-search-bar ui-toolbar ui-toolbar--bordered">';
  html += '<div class="board-search-input-wrap">';
  html += '<input type="text" class="board-search-input" id="board-search-input"'
    + ' placeholder="Search tasks..." value="' + esc(_boardSearchQuery) + '"'
    + ' aria-label="Search Board tasks"'
    + ' title="' + esc(searchHint) + '"'
    + ' oninput="boardUpdateSearch(this.value)">';
  html += '</div>';
  if (showToolbar) {
    if (hasLabels || _boardFilterLabels.length) {
      var lblCount = _boardFilterLabels.length;
      html += '<div class="board-filter-btn-wrap" id="board-label-filter-wrap">';
      html += '<button type="button" class="filter-chip board-filter-btn' + (lblCount ? ' active' : '') + '"'
        + ' aria-haspopup="dialog" aria-expanded="' + (_boardFilterDropdownType === 'label' ? 'true' : 'false') + '"'
        + ' onclick="boardToggleLabelFilter()">'
        + 'Labels' + (lblCount ? ' <span class="board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count">' + lblCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasActions || _boardFilterActions.length) {
      var actFCount = _boardFilterActions.length;
      html += '<div class="board-filter-btn-wrap" id="board-action-filter-wrap">';
      html += '<button type="button" class="filter-chip board-filter-btn' + (actFCount ? ' active' : '') + '"'
        + ' aria-haspopup="dialog" aria-expanded="' + (_boardFilterDropdownType === 'action' ? 'true' : 'false') + '"'
        + ' onclick="boardToggleActionFilter()">'
        + 'Actions' + (actFCount ? ' <span class="board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count">' + actFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasQuickViews) {
      html += '<button type="button" class="filter-chip board-filter-btn' + (recentQuickViewActive ? ' active' : '') + '"'
        + ' aria-pressed="' + (recentQuickViewActive ? 'true' : 'false') + '"'
        + ' onclick="boardApplyQuickView(\'recent\')">Recent</button>';
    }
    if (hasAgents || _boardFilterAgents.length) {
      var agtFCount = _boardFilterAgents.length;
      html += '<div class="board-filter-btn-wrap" id="board-agent-filter-wrap">';
      html += '<button type="button" class="filter-chip board-filter-btn' + (agtFCount ? ' active' : '') + '"'
        + ' aria-haspopup="dialog" aria-expanded="' + (_boardFilterDropdownType === 'agent' ? 'true' : 'false') + '"'
        + ' onclick="boardToggleAgentFilter()">'
        + 'Agents' + (agtFCount ? ' <span class="board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count">' + agtFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (hasHealth || _boardFilterHealth.length) {
      var healthFCount = _boardFilterHealth.length;
      html += '<div class="board-filter-btn-wrap" id="board-health-filter-wrap">';
      html += '<button type="button" class="filter-chip board-filter-btn' + (healthFCount ? ' active' : '') + '"'
        + ' aria-haspopup="dialog" aria-expanded="' + (_boardFilterDropdownType === 'health' ? 'true' : 'false') + '"'
        + ' onclick="boardToggleHealthFilter()">'
        + 'Health' + (healthFCount ? ' <span class="board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count">' + healthFCount + '</span>' : '')
        + ' &#9662;</button>';
      html += '</div>';
    }
    if (filtersActive) {
      html += '<button type="button" class="board-filter-clear btn-link" onclick="boardClearFilters()">Clear filters</button>';
    }
  }
  html += '<div class="board-search-spacer"></div>';
  if (showViewMenuButton) {
    html += '<div class="board-filter-btn-wrap" id="board-view-menu-wrap">';
    html += '<button type="button" class="filter-chip board-filter-btn' + (_boardViewMenuOpen ? ' active' : '') + '"'
      + ' aria-haspopup="dialog" aria-expanded="' + (_boardViewMenuOpen ? 'true' : 'false') + '"'
      + ' onclick="boardToggleViewMenu()">View &#9662;</button>';
    html += '</div>';
  }
  html += '<div class="board-filter-btn-wrap" id="board-schedules-toggle-wrap">';
  html += '<button type="button" class="filter-chip board-filter-btn' + (_boardShowSchedules ? ' active' : '') + '"'
    + ' aria-pressed="' + (_boardShowSchedules ? 'true' : 'false') + '"'
    + ' onclick="boardToggleSchedules()">'
    + 'Schedules'
    + (schedCount ? ' <span class="board-filter-btn-count ui-badge ui-badge--micro ui-badge--accent ui-badge--count">' + schedCount + '</span>' : '')
    + '</button>';
  html += '</div>';
  html += '</div>';

  if (showSavedViewsRow) {
    html += '<div class="board-saved-views">';
    if (currentViewSavable || hasSavedViews || _boardSavingView) {
      html += '<span class="board-saved-views-label">Saved</span>';
      if (_boardSavingView) {
        html += '<div class="board-save-view-form">';
        html += '<input type="text" class="board-save-view-input" id="board-save-view-input"'
          + ' placeholder="View name" value="' + esc(_boardSavingViewName) + '"'
          + ' oninput="boardUpdateSaveViewName(this.value)"'
          + ' onkeydown="boardSaveViewKeydown(event)">';
        html += '<button type="button" class="btn btn-primary btn-xs" onclick="boardSubmitSaveView()">Save</button>';
        html += '<button type="button" class="btn btn-quiet btn-xs" onclick="boardCancelSaveView()">Cancel</button>';
        html += '</div>';
      } else if (currentViewSavable) {
        html += '<button type="button" class="btn btn-secondary btn-xs" onclick="boardStartSaveView()">Save View</button>';
      }
    }
    for (var vi = 0; vi < savedViews.length; vi++) {
      var view = savedViews[vi];
      var viewName = esc(view.name).replace(/'/g, "\\'");
      html += '<div class="board-saved-view">';
      html += '<button type="button" class="filter-chip board-filter-btn'
        + (_boardViewMatchesCurrent(view) ? ' active' : '')
        + '" aria-pressed="' + (_boardViewMatchesCurrent(view) ? 'true' : 'false') + '"'
        + ' onclick="boardApplySavedView(\'' + viewName + '\')">'
        + esc(view.name) + '</button>';
      html += '<button class="board-saved-view-delete"'
        + ' onclick="event.stopPropagation();boardDeleteSavedView(\'' + viewName + '\')">&times;</button>';
      html += '</div>';
    }
    html += '</div>';
  }

  // Active filter chips
  if (showToolbar) {
    if (_boardFilterLabels.length || _boardFilterActions.length || _boardFilterAgents.length || _boardFilterHealth.length) {
      html += '<div class="board-filter-active">';
      for (var fi = 0; fi < _boardFilterLabels.length; fi++) {
        var fl = _boardFilterLabels[fi];
        html += '<button type="button" class="filter-chip board-filter-active-chip board-filter-active-label"'
          + ' aria-label="Remove label filter ' + esc(fl) + '"'
          + ' onclick="boardRemoveFilterLabel(\'' + esc(fl).replace(/'/g, "\\'") + '\')">'
          + esc(fl) + ' &times;</button>';
      }
      for (var fi = 0; fi < _boardFilterActions.length; fi++) {
        var fa = _boardFilterActions[fi];
        html += '<button type="button" class="filter-chip board-filter-active-chip board-filter-active-action"'
          + ' aria-label="Remove action filter ' + esc(fa) + '"'
          + ' onclick="boardRemoveFilterAction(\'' + esc(fa).replace(/'/g, "\\'") + '\')">'
          + esc(fa) + ' &times;</button>';
      }
      for (var fi = 0; fi < _boardFilterAgents.length; fi++) {
        var aid = _boardFilterAgents[fi];
        html += '<button type="button" class="filter-chip board-filter-active-chip board-filter-active-action"'
          + ' aria-label="Remove agent filter ' + esc(_boardAgentName(aid) || aid) + '"'
          + ' onclick="boardRemoveFilterAgent(\'' + esc(aid).replace(/'/g, "\\'") + '\')">'
          + esc(_boardAgentName(aid) || aid) + ' &times;</button>';
      }
      for (var fi = 0; fi < _boardFilterHealth.length; fi++) {
        var hs = _boardFilterHealth[fi];
        html += '<button type="button" class="filter-chip board-filter-active-chip board-filter-active-health"'
          + ' aria-label="Remove health filter ' + esc(_boardHealthDisplayName(hs)) + '"'
          + ' onclick="boardRemoveFilterHealth(\'' + esc(hs).replace(/'/g, "\\'") + '\')">'
          + esc(_boardHealthDisplayName(hs)) + ' &times;</button>';
      }
      html += '</div>';
    }
  }
  // When filters become active, save the current lane; auto-select first non-empty lane
  if (filtersActive) {
    if (!_boardPreFilterLane) _boardPreFilterLane = _boardSelectedLane;
    // Check if current lane has matches; if not, jump to first that does
    var curCount = _boardLaneCount(_boardSelectedLane, renderModel);
    if (curCount === 0) {
      for (var fi = 0; fi < lanes.length; fi++) {
        if (_boardLaneCount(lanes[fi], renderModel) > 0) {
          _boardSelectedLane = lanes[fi];
          break;
        }
      }
    }
    if (!wideLayout && renderLanes[0] !== _boardSelectedLane) {
      renderLanes = [_boardSelectedLane];
      renderModel = _boardBuildRenderModel(renderLanes);
      _boardEnsureDispatchEligibilityRefs(_currentGroup(), renderModel);
    }
  }

  _boardActivateViewState(_boardCurrentViewKey());

  // Lane tab bar is only needed for narrow layouts. Wide standalone boards
  // expose the schedules toggle beside the View menu and render lanes as
  // headers inside each column instead.
  if (!_boardShowSchedules && !wideShell) {
    html += '<div class="board-lane-bar">';
    html += '<button class="board-lane-scroll-btn" id="board-scroll-left" onclick="boardScrollLanes(-1)" title="Scroll left" aria-label="Scroll Board lanes left">&#9664;</button>';
    html += '<div class="board-lane-tabs ui-tablist" id="board-lane-tabs" role="tablist" aria-label="Board lanes" onkeydown="uiTablistKeydown(event)">';
    for (var i = 0; i < lanes.length; i++) {
      var l = lanes[i];
      var cnt = _boardLaneCount(l, renderModel);
      var cls = (!_boardShowSchedules && l === _boardSelectedLane) ? ' active' : '';
      if (filtersActive && cnt === 0) cls += ' dimmed';
      var escLane = esc(l).replace(/'/g, "\\'");
      html += '<button class="ui-tab ui-tab--underline board-lane-tab board-lane-drop-target' + cls + '"'
        + ' data-lane="' + esc(l) + '"'
        + ' role="tab" aria-selected="' + (l === _boardSelectedLane ? 'true' : 'false') + '" tabindex="' + (l === _boardSelectedLane ? '0' : '-1') + '"'
        + ' onclick="boardSelectLane(\'' + escLane + '\')"'
        + ' ondragover="boardLaneTabDragOver(event)"'
        + ' ondragleave="boardLaneTabDragLeave(event)"'
        + ' ondrop="boardLaneTabDrop(event)">'
        + esc(l) + '<span class="lane-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + cnt + '</span>'
        + '</button>';
    }
    html += '</div>';
    html += '<button class="board-lane-scroll-btn" id="board-scroll-right" onclick="boardScrollLanes(1)" title="Scroll right" aria-label="Scroll Board lanes right">&#9654;</button>';
    html += '</div>';
  }

  // Schedules view (replaces cards when active)
  if (_boardShowSchedules) {
    html += _renderSchedulesView();
    panel.innerHTML = html;
    _boardLastRenderShellKey = shellKey;
    _boardLastToolbarShapeKey = _boardToolbarShapeKey(renderModel, filtersActive);
    _boardRestoreRenderedState();
    _boardAfterRenderLayout();
    if (restoreState) _restoreSurfaceState(panel, restoreState);
    return;
  }

  var childrenOf = renderModel.childrenOf;
  var nextLaneEntryDelay = 0;
  if (wideLayout) {
    html += _boardRenderWideAddTaskSection();
  }
  var wideGridStyle = wideLayout
    ? ' style="grid-template-columns:' + _boardWideGridTemplate(lanes) + '"'
    : '';
  html += '<div class="board-cards board-density-' + _boardCardDensityMode()
    + (wideLayout ? ' board-wide-grid' : '') + '" id="board-cards"' + wideGridStyle + '>';
  if (wideLayout) {
    for (var laneIdx = 0; laneIdx < lanes.length; laneIdx++) {
      var wideSection = _boardRenderWideLaneColumn(
        lanes[laneIdx],
        renderModel,
        filtersActive,
      );
      _boardRememberLaneRender(lanes[laneIdx], renderModel, filtersActive, true, true, wideSection);
      html += wideSection.html;
      var wideDelay = _boardVisibleLaneEntryRefreshDelay(
        wideSection.rootTasks,
        childrenOf,
        wideSection.renderLimit,
      );
      if (wideDelay > 0 && (!nextLaneEntryDelay || wideDelay < nextLaneEntryDelay)) {
        nextLaneEntryDelay = wideDelay;
      }
    }
  } else {
    var laneSection = _boardRenderLaneSection(
      _boardSelectedLane,
      renderModel,
      filtersActive,
    );
    _boardRememberLaneRender(_boardSelectedLane, renderModel, filtersActive, false, false, laneSection);
    html += laneSection.html;
    nextLaneEntryDelay = _boardVisibleLaneEntryRefreshDelay(
      laneSection.rootTasks,
      childrenOf,
      laneSection.renderLimit,
    );
  }
  _boardScheduleLaneEntryRefresh(nextLaneEntryDelay);

  html += '</div>';

  // Selection bar
  html += _renderBoardSelectionBar();

  panel.innerHTML = html;
  _boardLastRenderShellKey = shellKey;
  _boardLastToolbarShapeKey = _boardToolbarShapeKey(renderModel, filtersActive);
  _boardRestoreRenderedState();

  // Auto-focus inputs (only when user explicitly opened, not on re-renders)
  if (_boardAddingTask && _boardAddTaskFocus) {
    _boardAddTaskFocus = false;
    var tInp = document.getElementById('board-add-task-input');
    if (tInp) {
      boardAddTaskAutoResize(tInp);
      tInp.focus();
      // Place cursor at end
      tInp.selectionStart = tInp.selectionEnd = tInp.value.length;
    }
  }
  if (_boardSavingView && _boardSaveViewFocus) {
    _boardSaveViewFocus = false;
    var viewInp = document.getElementById('board-save-view-input');
    if (viewInp) {
      viewInp.focus();
      viewInp.selectionStart = 0;
      viewInp.selectionEnd = viewInp.value.length;
    }
  }

  if (restoreState) _restoreSurfaceState(panel, restoreState);
  if (quickEditRefocusTask) {
    _boardRefocusQuickEditInput(quickEditRefocusTask, quickEditRefocusKind);
  }
  if (_boardAddingTask) {
    var addTaskInput = document.getElementById('board-add-task-input');
    if (addTaskInput) boardAddTaskAutoResize(addTaskInput);
  }
  _boardAfterRenderLayout();
}

/* ---- Virtual scroll ------------------------------------------------- */

function _boardPatchLaneAfterLoadMore(panel, lane, model, filtersActive, wideLayout) {
  if (!panel || lane !== 'Done') return false;
  var result = wideLayout
    ? _boardPatchWideLaneBody(panel, lane, model, filtersActive)
    : (lane === _boardSelectedLane
      ? _boardPatchNarrowLaneBody(panel, lane, model, filtersActive)
      : null);
  if (!result) return false;
  var delay = _boardVisibleLaneEntryRefreshDelay(
    result.rootTasks || [],
    (model && model.childrenOf) || {},
    result.renderLimit || _boardRenderLimitValue(lane),
  );
  _boardScheduleLaneEntryRefresh(delay);
  _boardAfterRenderLayout();
  return true;
}

function boardLoadMore(lane) {
  var panel = document.getElementById('panel-board');
  var wideLayout = _boardWideLayoutActive(panel);
  var lanes = lane
    ? [lane]
    : (wideLayout
      ? _boardVisibleLanes()
      : [_boardSelectedLane || _boardVisibleLanes()[0] || '']);
  var model = _boardBuildRenderModel(lanes);
  var loadedLane = '';
  for (var i = 0; i < lanes.length; i++) {
    var laneName = lanes[i];
    var laneCards = _boardRenderableCardCountForRoots(
      _boardRootTasksForLane(laneName, model.visibleTasks, model),
      model.childrenOf
    );
    var currentLimit = _boardRenderLimitValue(laneName);
    if (currentLimit >= laneCards) continue;
    _boardSetRenderLimitForLane(
      laneName,
      Math.min(laneCards, currentLimit + _boardLoadMoreBatchForLane(laneName)),
    );
    loadedLane = laneName;
  }
  if (!loadedLane) return;
  _boardSyncActiveViewState();
  if (lane && _boardPatchLaneAfterLoadMore(panel, lane, model, _boardHasActiveFilters(), wideLayout)) {
    return;
  }
  renderBoard();
}

/* ---- Lane selection ------------------------------------------------- */

function boardSelectLane(lane) {
  if (!_boardShowSchedules && lane === _boardSelectedLane) return;
  var wideLayout = _boardWideLayoutActive(document.getElementById('panel-board'));
  _boardPrepareViewChange(!wideLayout);
  _boardShowSchedules = false;  // exit schedules view on lane click
  // Save current scroll so renderBoard can restore + adjust for new active tab
  var tabs = document.getElementById('board-lane-tabs');
  if (tabs) _boardScrollLeft = tabs.scrollLeft;
  _boardSelectedLane = lane;
  _boardFocusedTask = '';
  _boardSelectedTasks = {};
  _boardLastSelectedTask = '';
  renderBoard();
  if (typeof _boardPersistSelectedLane === 'function') {
    _boardPersistSelectedLane();
  }
}

function boardSetLaneSort(mode) {
  _boardHydrateLaneSorts();
  var group = _currentGroup();
  var lane = _boardSelectedLane;
  if (!group || !lane) return;
  _boardCloseViewMenu();
  _boardPrepareViewChange(false);
  mode = _boardNormalizeLaneSortMode(mode);
  var sorts = _boardLaneSortsByGroup[group] || {};
  if (mode === 'manual') {
    delete sorts[lane];
  } else {
    sorts[lane] = mode;
  }
  if (Object.keys(sorts).length) {
    _boardLaneSortsByGroup[group] = sorts;
  } else {
    delete _boardLaneSortsByGroup[group];
  }
  _boardViewStates[_boardCurrentViewKey()] = { scroll_top: 0, render_limit: _boardDefaultRenderLimit, done_render_limit: _boardDoneInitialRenderLimit };
  _boardCardsScrollTop = 0;
  _boardResetRenderLimits();
  _boardPersistLaneSorts();
  renderBoard();
}

function boardSetCardDensity(mode) {
  _boardHydrateCardDensity();
  var group = _currentGroup();
  if (!group) return;
  _boardCloseViewMenu();
  mode = _boardNormalizeCardDensity(mode);
  if (mode === 'normal') {
    delete _boardCardDensityByGroup[group];
  } else {
    _boardCardDensityByGroup[group] = mode;
  }
  _boardPersistCardDensity();
  renderBoard();
}

/* ---- Lane scroll ---------------------------------------------------- */

function boardScrollLanes(dir) {
  var tabs = document.getElementById('board-lane-tabs');
  if (!tabs) return;

  var children = tabs.querySelectorAll('.board-lane-tab');
  if (!children.length) return;

  if (dir > 0) {
    // Find the first tab not fully visible on the right
    var viewRight = tabs.scrollLeft + tabs.clientWidth;
    for (var i = 0; i < children.length; i++) {
      var tabRight = children[i].offsetLeft + children[i].offsetWidth;
      if (tabRight > viewRight + 0.5) {
        tabs.scrollLeft = tabRight - tabs.clientWidth;
        return;
      }
    }
  } else {
    // Find the last tab not fully visible on the left
    var viewLeft = tabs.scrollLeft;
    if (viewLeft < 1) return; // already at start
    for (var i = children.length - 1; i >= 0; i--) {
      if (children[i].offsetLeft < viewLeft - 0.5) {
        tabs.scrollLeft = children[i].offsetLeft;
        return;
      }
    }
    // Close to start but not quite — snap to 0
    tabs.scrollLeft = 0;
  }
}

function boardUpdateScrollArrows() {
  var tabs = document.getElementById('board-lane-tabs');
  var left = document.getElementById('board-scroll-left');
  var right = document.getElementById('board-scroll-right');
  if (!tabs || !left || !right) return;

  var sl = tabs.scrollLeft;
  var maxScroll = tabs.scrollWidth - tabs.clientWidth;
  left.classList.toggle('hidden', sl < 1);
  right.classList.toggle('hidden', maxScroll - sl < 1);
}

/* ---- Add task dropdown ---------------------------------------------- */

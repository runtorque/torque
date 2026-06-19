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
      html += `<div class="term-list">`;
      for (const t of standaloneTerms) html += renderTerminalRow(t);
      html += `</div>`;
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

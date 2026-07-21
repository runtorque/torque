/*
 * Agents canvas view (prototype).
 *
 * Alternative to the default agent grid. Lays out architects as vertical
 * spines on an infinite-canvas pane; engineers branch off each spine to
 * the right; workers fan out horizontally below each engineer. Unowned
 * agents (engineers without an architect, workers without an engineer)
 * collect under a "Standalone" pseudo-tree at the bottom.
 *
 * Right-click context menus surface the create flow plus the same per-agent
 * actions offered by the grid view:
 *   empty canvas -> + Architect / + Engineer / + Worker
 *   architect    -> + Engineer (hired by this architect)
 *
 * Terminals do not appear on this canvas (deliberate; they live in the
 * existing drawer / standalone strip).
 */

const CANVAS_VIEW_STORAGE_KEY = 'torque.agentsCanvasView';

function _torqueAgentViewMode() {
  try {
    const v = localStorage.getItem(CANVAS_VIEW_STORAGE_KEY);
    return v === 'canvas' ? 'canvas' : 'grid';
  } catch (e) {
    return 'grid';
  }
}

function _torqueSetAgentViewMode(mode) {
  const next = mode === 'canvas' ? 'canvas' : 'grid';
  try { localStorage.setItem(CANVAS_VIEW_STORAGE_KEY, next); } catch (e) {}
  _torqueRefreshViewToggleButtons(next);
  if (typeof render === 'function') render();
}

function toggleAgentCanvasView() {
  const next = _torqueAgentViewMode() === 'canvas' ? 'grid' : 'canvas';
  _torqueSetAgentViewMode(next);
}

function _torqueRefreshViewToggleButtons(mode) {
  const active = mode || _torqueAgentViewMode();
  const buttons = document.querySelectorAll('[data-agent-view-toggle]');
  buttons.forEach(function(btn) {
    const target = btn.getAttribute('data-agent-view-toggle');
    if (target === active) btn.classList.add('is-active');
    else btn.classList.remove('is-active');
    if (typeof btn.setAttribute === 'function') {
      btn.setAttribute('aria-pressed', target === active ? 'true' : 'false');
    }
  });
}

/* -- Canvas data model -------------------------------------------------- */

function _canvasIsTombstoned(agent) {
  if (typeof _isTombstonedAgent === 'function') return _isTombstonedAgent(agent);
  return !!(agent && agent.deleted_at && Number(agent.deleted_at) > 0);
}

function _canvasGroupName() {
  if (typeof _activeGroup === 'function') {
    const g = _activeGroup();
    if (g) return g;
  }
  const groups = (state && state.groups) || {};
  const names = Object.keys(groups);
  if (names.length === 0) return '';
  if (typeof selectedAgentId === 'string' && selectedAgentId) {
    const sel = state.agents && state.agents[selectedAgentId];
    if (sel && sel.group && groups[sel.group]) return sel.group;
  }
  return names[0];
}

function _canvasBuildTrees(groupName) {
  const trees = [];
  const standalone = {
    kind: 'standalone',
    engineers: [],
    workers: [],
  };
  const seen = new Set();
  if (!state || !state.agents) return { trees, standalone };

  const all = [];
  const persistedGroupOrder = groupName && state.groups && Array.isArray(state.groups[groupName])
    ? state.groups[groupName]
    : Object.keys(state.agents);
  for (const id of persistedGroupOrder) {
    const cell = state.agents[id];
    if (!cell || cell.cell_type !== 'agent') continue;
    if (_canvasIsTombstoned(cell)) continue;
    if (groupName && cell.group !== groupName) continue;
    all.push(cell);
  }

  const archByName = function(a, b) {
    const av = Number(a.created_at || 0);
    const bv = Number(b.created_at || 0);
    if (av !== bv) return av - bv;
    return String(a.id || '').localeCompare(String(b.id || ''));
  };
  const architects = all.filter(function(a) { return (a.kind || '') === 'architect'; });
  // `all` follows the persisted group-members list, matching the standard
  // grid's Architect selector. New groups naturally begin in insertion order.

  for (const arch of architects) {
    const engineers = all.filter(function(e) {
      if ((e.kind || '') !== 'engineer') return false;
      return String(e.hired_by_architect_id || '').trim() === arch.id;
    }).sort(archByName);

    const enRows = engineers.map(function(eng) {
      seen.add(eng.id);
      const workers = all.filter(function(w) {
        if (!_canvasIsWorker(w)) return false;
        const owner = String(w.owner_engineer_id || w.created_by_engineer_id || '').trim();
        return owner === eng.id;
      }).sort(archByName);
      workers.forEach(function(w) { seen.add(w.id); });
      return { engineer: eng, workers: workers };
    });
    seen.add(arch.id);
    trees.push({ architect: arch, engineers: enRows });
  }

  for (const cell of all) {
    if (seen.has(cell.id)) continue;
    const kind = cell.kind || '';
    if (kind === 'engineer') {
      const workers = all.filter(function(w) {
        if (!_canvasIsWorker(w)) return false;
        const owner = String(w.owner_engineer_id || w.created_by_engineer_id || '').trim();
        return owner === cell.id;
      }).sort(archByName);
      workers.forEach(function(w) { seen.add(w.id); });
      standalone.engineers.push({ engineer: cell, workers: workers });
      seen.add(cell.id);
    } else if (_canvasIsWorker(cell)) {
      const owner = String(cell.owner_engineer_id || cell.created_by_engineer_id || '').trim();
      // Skip only if the owner engineer is actually PRESENT in this group:
      // a loose engineer may be iterated later in this same loop (after its
      // worker, since `all` follows state.agents insertion order, not
      // engineer-first), and it attaches its workers via an `all.filter`
      // that does not consult `seen` — so pushing here too would
      // double-render the worker. Workers with no owner, a tombstoned owner
      // (filtered from `all` upstream), or an out-of-group owner all fall
      // through to the loose-workers bar so an agent that exists is never
      // silently dropped.
      if (owner && all.some(function(c) { return c.id === owner && (c.kind || '') === 'engineer'; })) continue;
      standalone.workers.push(cell);
      seen.add(cell.id);
    }
  }

  return { trees, standalone };
}

function _canvasIsWorker(cell) {
  if (!cell) return false;
  const k = cell.kind || '';
  if (k === 'architect' || k === 'engineer' || k === 'terminal') return false;
  return true;
}

/* -- Canvas render ------------------------------------------------------ */

function _torqueRenderAgentCanvas(opts) {
  const main = document.getElementById('main');
  if (!main) return;

  const groupName = _canvasGroupName();
  const groups = (state && state.groups) || {};
  let canvasHtml;
  if (Object.keys(groups).length === 0) {
    canvasHtml = '<div class="empty ui-state ui-state--empty ui-state--fill">'
      + '<div class="ui-state__title">No groups yet</div>'
      + '<div class="ui-state__message">Create a group to start organizing agents and tasks.</div>'
      + '<div class="ui-state__actions"><button type="button" class="btn-secondary btn-sm" onclick="openAddGroup()">+ New group</button></div>'
      + '</div>';
  } else {
    const model = _canvasBuildTrees(groupName);
    canvasHtml = _canvasRenderHtml(groupName, model);
  }

  // Route through the same split-shell the grid uses, so the agent
  // focus panel + resizer + tabs host all keep working unchanged.
  // _renderAgentGridAndFocus injects the tabs host, builds the
  // grid/focus split shell inside #main, and tracks sentinel state
  // for byte-equality memoization across rerenders.
  const tabsHtml = (typeof _renderAgentGroupTabsHtml === 'function')
    ? _renderAgentGroupTabsHtml()
    : '';
  if (typeof _renderAgentGridAndFocus === 'function') {
    _renderAgentGridAndFocus(main, canvasHtml, {
      tabsHtml: tabsHtml,
      renderFocus: !(opts && opts.skipFocusRefresh),
    });
  } else {
    if (typeof _renderAgentGroupTabsHost === 'function') {
      _renderAgentGroupTabsHost(tabsHtml);
    }
    main.innerHTML = canvasHtml;
  }

  _canvasAttachInteractions(groupName);

  // The grid path ends with a renderTerminalWorkspace() call so the
  // terminal column refreshes when the selected agent / focused item
  // changes. Without this, the canvas mode showed no terminal after
  // a fresh app boot — even though `selectedAgentId` had been
  // restored from server state.
  if (!(opts && opts.skipTerminalRefresh) && typeof renderTerminalWorkspace === 'function') {
    renderTerminalWorkspace();
  }
}

function _canvasRenderHtml(groupName, model) {
  let html = '';
  html += `<div class="agent-canvas" data-canvas-group="${esc(groupName)}">`;
  html += `<div class="agent-canvas-inner">`;

  if (model.trees.length === 0
      && model.standalone.engineers.length === 0
      && model.standalone.workers.length === 0) {
    html += `<div class="canvas-empty">
      <div class="canvas-empty-title">No agents yet</div>
      <div class="canvas-empty-hint">Right-click anywhere to create an architect, engineer, or worker.</div>
    </div>`;
  } else {
    for (const tree of model.trees) {
      html += _canvasRenderTree(tree);
    }
    // Loose engineers (no architect): render each as its own node on
    // the canvas, with its workers attached to the right. No spine,
    // no wrapper card — just an unparented engineer + its workers.
    for (const row of model.standalone.engineers) {
      html += _canvasRenderLooseEngineer(row);
    }
    // Loose workers (no engineer): plain row of worker cards.
    if (model.standalone.workers.length > 0) {
      html += _canvasRenderLooseWorkersBar(model.standalone.workers);
    }
  }

  html += `</div></div>`;
  return html;
}

/* Standard tree-rendering guide model.
 *
 * Each row has `ancestorContinues` — an array, one entry per ancestor
 * depth, where true means "draw a vertical line through this column
 * because the corresponding ancestor still has more siblings below
 * this row." Plus `isLast`, which decides whether this row's own
 * connector is a T-junction (more siblings to come) or an L-corner
 * (last child). Architect rows have zero ancestors; engineer rows
 * have one; worker rows have two.
 */
function _canvasRenderRow(ancestorContinues, isLast, hasOwnConnector, cardHtml) {
  let html = '<div class="canvas-row">';
  for (const cont of ancestorContinues) {
    html += '<div class="canvas-guide canvas-guide--'
         + (cont ? 'through' : 'empty')
         + '"></div>';
  }
  if (hasOwnConnector) {
    html += '<div class="canvas-guide canvas-guide--'
         + (isLast ? 'corner' : 'tee')
         + '"></div>';
  }
  html += cardHtml;
  html += '</div>';
  return html;
}

function _canvasRenderTree(tree) {
  const arch = tree.architect;
  const engineers = tree.engineers || [];
  let html = '';
  html += `<div class="canvas-tree" data-canvas-tree="${esc(arch.id)}">`;
  // Architect row (depth 0, no guides)
  html += _canvasRenderRow([], false, false, _canvasRenderArchitectCard(arch));
  html += _canvasRenderChildren(engineers, arch.id, [], 'engineer');
  html += `</div>`;
  return html;
}

/* Render the children of a parent under the given ancestor-continues
 * stack. `kind` selects the child renderer ('engineer' under an
 * architect, 'worker' under an engineer). For engineers, recurses
 * into workers at depth+1. */
function _canvasRenderChildren(rows, parentId, ancestorContinues, kind) {
  let html = '';
  if (rows.length === 0) {
    // Architects with no engineers show an empty-state '+ Engineer'
    // row. Engineers with no workers show NOTHING — workers are
    // created by the engineer itself via task dispatch, not by the
    // user from the canvas.
    if (kind === 'engineer') {
      const guides = ancestorContinues.slice();
      const action = `_canvasAddEngineerForArchitect('${esc(parentId)}')`;
      const btnHtml = `<button type="button" class="canvas-add-inline" onclick="${action}">+ Engineer</button>`;
      html += _canvasRenderRow(guides, true, true, btnHtml);
    }
    return html;
  }
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const isLast = (i === rows.length - 1);
    if (kind === 'engineer') {
      const eng = row.engineer;
      const workers = row.workers || [];
      const cardHtml = `<div data-canvas-row-host="${esc(eng.id)}">${_canvasRenderEngineerCard(eng)}</div>`;
      html += _canvasRenderRow(ancestorContinues, isLast, true, cardHtml);
      // Recurse into workers at depth+1. Architect-level continues if
      // this engineer is NOT the last (i.e., there are more engineers
      // after this).
      const childAncestors = ancestorContinues.concat([!isLast]);
      html += _canvasRenderChildren(workers, eng.id, childAncestors, 'worker');
    } else if (kind === 'worker') {
      const w = row;
      const cardHtml = _canvasRenderWorkerCard(w);
      html += _canvasRenderRow(ancestorContinues, isLast, true, cardHtml);
    }
  }
  return html;
}

function _canvasRenderLooseEngineer(row) {
  // Render the orphan engineer as a depth-0 row (no architect spine
  // above it), then its workers as depth-1 children.
  const eng = row.engineer;
  const workers = row.workers || [];
  let html = '';
  html += `<div class="canvas-tree canvas-tree-loose">`;
  html += _canvasRenderRow([], false, false, _canvasRenderEngineerCard(eng));
  html += _canvasRenderChildren(workers, eng.id, [], 'worker');
  html += `</div>`;
  return html;
}

function _canvasRenderLooseWorkersBar(workers) {
  // Orphan workers (no engineer): each is its own depth-0 row.
  let html = '';
  for (const w of workers) {
    html += _canvasRenderRow([], false, false, _canvasRenderWorkerCard(w));
  }
  return html;
}

function _canvasStatusClass(cell) {
  const status = String(cell && cell.status || '').toLowerCase();
  const classes = [];
  if (status === 'error') classes.push('is-error');
  else if (status === 'stopped' || status === 'dismissed') classes.push('is-stopped');
  else if (status === 'running') classes.push('is-running');
  else if (status === 'idle') classes.push('is-idle');
  else classes.push('is-unknown');
  if (cell && typeof selectedAgentId !== 'undefined'
      && selectedAgentId
      && cell.id === selectedAgentId) {
    classes.push('is-selected');
  }
  if (cell && typeof focusedItemId !== 'undefined'
      && focusedItemId
      && cell.id === focusedItemId) {
    classes.push('is-focused');
  }
  return classes.join(' ');
}

function _canvasActivityDetail(cell) {
  if (!cell) return '';
  const detail = String(cell.activity_detail || '').trim();
  if (detail) return detail;
  if (typeof _agentCardCurrentOrLastActionLabel === 'function') {
    const a = _agentCardCurrentOrLastActionLabel(cell);
    if (a) return a;
  }
  const activity = String(cell.activity || '').trim();
  if (activity && activity.toLowerCase() !== 'idle') return activity;
  return '';
}

/* Third-row status line. The Claude Code / Codex adapters already
 * prefix activity_detail with a verb ('Editing render.js',
 * 'Running: ls -la', 'Reading file.py') so showing them through a
 * second 'Running: ' prefix would just double-prefix AND eat the
 * width that the actual detail (filename/command) needs. Show
 * activity_detail as-is for the running case.
 *
 *   running + activity_detail  -> '<detail>'          (adapter-prefixed)
 *   stopped / dismissed        -> 'stopped'
 *   error                      -> 'Error: <msg>'
 *   idle + recent action label -> '<label> (Nm)'      (past-event helper)
 *   running, no detail         -> 'Working'
 *   nothing                    -> 'idle'
 */
function _canvasStatusLine(cell) {
  if (!cell) return 'idle';
  const status = String(cell.status || '').toLowerCase();
  if (status === 'stopped' || status === 'dismissed') return 'stopped';
  if (status === 'error') {
    const err = String(cell.error_message || '').trim();
    return err ? 'Error: ' + err : 'Error';
  }
  const liveDetail = String(cell.activity_detail || '').trim();
  if (status === 'running' && liveDetail) return liveDetail;
  if (typeof _agentCardCurrentOrLastActionLabel === 'function') {
    const a = _agentCardCurrentOrLastActionLabel(cell);
    if (a) return a;
  }
  if (status === 'running') return 'Working';
  return 'idle';
}

function _canvasKindLabel(kind) {
  if (kind === 'architect') return 'Architect';
  if (kind === 'engineer') return 'Engineer';
  if (kind === 'worker') return 'Worker';
  return 'Agent';
}

function _canvasProviderLabel(cell) {
  const labels = (typeof AGENT_TYPE_LABELS !== 'undefined') ? AGENT_TYPE_LABELS : null;
  const at = String((cell && cell.agent_type) || '').trim();
  if (labels && at && labels[at] && labels[at].label) return labels[at].label;
  return '';
}

function _canvasPauseState(cell) {
  if (!cell || !state) return { applicable: false, paused: false, toggleAction: '' };
  const kind = cell.kind || '';
  if (kind !== 'engineer' && kind !== 'architect') {
    return { applicable: false, paused: false, toggleAction: '' };
  }
  const group = String(cell.group || '');
  const id = String(cell.id || '');
  const gs = (state.group_settings || {})[group] || {};
  const isDesignated = (kind === 'engineer' && gs.engineer_agent_id === id);
  const engineerWs = (isDesignated && state.engineer_settings)
    ? state.engineer_settings[group] : null;
  const digestSettings = (state.agent_digest_settings || {})[id] || null;
  let paused = false;
  let toggleAction = '';
  if (isDesignated) {
    paused = !!(engineerWs && engineerWs.paused);
    if (digestSettings) paused = !!digestSettings.paused;
    toggleAction = digestSettings
      ? `toggleDigestPauseForAgent('${esc(id)}')`
      : `engineerTogglePauseForGroup('${esc(group)}')`;
  } else {
    paused = !!(digestSettings && digestSettings.paused);
    toggleAction = `toggleDigestPauseForAgent('${esc(id)}')`;
  }
  return { applicable: true, paused, toggleAction };
}

function _canvasRenderHeader(cell, kind, headerStatsHtml) {
  const name = esc(cell.name || cell.slug || cell.id);
  const kindLabel = _canvasKindLabel(kind);
  const pause = _canvasPauseState(cell);
  let pauseBadge = '';
  if (pause.applicable && pause.paused) {
    pauseBadge = '<span class="canvas-card-pause" title="Event delivery paused" aria-label="Paused">&#x23F8;</span>';
  }
  return '<div class="canvas-card-header">'
    + '<span class="canvas-card-role">' + esc(kindLabel) + '</span>'
    + '<span class="canvas-card-name" title="' + name + '">' + name + '</span>'
    + '<span class="canvas-card-header-spacer"></span>'
    + (headerStatsHtml || '')
    + pauseBadge
    + '<span class="canvas-card-status-dot"></span>'
    + '</div>';
}

function _canvasRenderProviderBadge(cell) {
  const label = _canvasProviderLabel(cell);
  if (!label) return '';
  return '<span class="canvas-card-provider-badge">' + esc(label) + '</span>';
}

function _canvasRenderStatusRow(cell) {
  const line = _canvasStatusLine(cell);
  const safe = esc(line);
  return '<div class="canvas-card-status-line" title="' + safe + '">' + safe + '</div>';
}

function _canvasRenderArchitectCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const engineerCount = (typeof _architectEngineersForCard === 'function')
    ? (_architectEngineersForCard(cell.id) || []).length
    : 0;
  const engLabel = engineerCount === 1 ? 'engineer' : 'engineers';
  const headerStats = '<span class="canvas-card-header-stats">'
    + esc(engineerCount + ' ' + engLabel)
    + '</span>';
  let html = '<div class="canvas-card canvas-card-architect ' + statusCls + '"'
    + ' data-canvas-card-id="' + esc(cell.id) + '"'
    + ' data-canvas-card-kind="architect">';
  html += _canvasRenderHeader(cell, 'architect', headerStats);
  html += _canvasRenderStatusRow(cell);
  html += _canvasRenderProviderBadge(cell);
  html += '</div>';
  return html;
}

function _canvasRenderEngineerCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const workers = (typeof _workersForEngineer === 'function')
    ? (_workersForEngineer(cell.id) || [])
    : [];
  const queueDepth = (typeof _engineerQueueDepth === 'function')
    ? _engineerQueueDepth(cell.id)
    : 0;
  const workerLabel = workers.length === 1 ? 'worker' : 'workers';
  const headerStats = '<span class="canvas-card-header-stats">'
    + esc(workers.length + ' ' + workerLabel)
    + '<span class="canvas-card-sep">·</span>'
    + esc('queue ' + queueDepth)
    + '</span>';
  let html = '<div class="canvas-card canvas-card-engineer ' + statusCls + '"'
    + ' data-canvas-card-id="' + esc(cell.id) + '"'
    + ' data-canvas-card-kind="engineer">';
  html += _canvasRenderHeader(cell, 'engineer', headerStats);
  html += _canvasRenderStatusRow(cell);
  html += _canvasRenderProviderBadge(cell);
  html += '</div>';
  return html;
}

function _canvasRenderWorkerCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const taskId = String(cell.current_task_id || '').trim();
  const branch = (typeof _workerBranchLabel === 'function')
    ? String(_workerBranchLabel(cell) || '').trim()
    : '';
  const diff = (typeof _workerDiffLabel === 'function')
    ? String(_workerDiffLabel(cell) || '').trim()
    : '';
  let html = '<div class="canvas-card canvas-card-worker ' + statusCls + '"'
    + ' data-canvas-card-id="' + esc(cell.id) + '"'
    + ' data-canvas-card-kind="worker">';
  html += _canvasRenderHeader(cell, 'worker');
  const parts = [];
  if (taskId) {
    parts.push('<span class="canvas-card-stat canvas-card-stat--task" '
      + 'onclick="event.stopPropagation(); if (typeof openTaskInBoard===\'function\') openTaskInBoard(\'' + esc(taskId) + '\');" '
      + 'title="' + esc(taskId + ' — open in board') + '">' + esc(taskId) + '</span>');
  } else {
    parts.push('<span class="canvas-card-stat canvas-card-stat--muted">no task</span>');
  }
  if (branch) {
    parts.push('<span class="canvas-card-sep">·</span>'
      + '<span class="canvas-card-stat canvas-card-stat--branch" title="' + esc(branch) + '">' + esc(branch) + '</span>');
  }
  if (diff) {
    parts.push('<span class="canvas-card-sep">·</span>'
      + '<span class="canvas-card-stat canvas-card-stat--diff">' + esc(diff) + '</span>');
  }
  html += '<div class="canvas-card-meta">' + parts.join('') + '</div>';
  html += _canvasRenderStatusRow(cell);
  html += _canvasRenderProviderBadge(cell);
  html += '</div>';
  return html;
}

/* -- Interactions ------------------------------------------------------- */

function _canvasAttachInteractions(groupName) {
  const root = document.querySelector('.agent-canvas');
  if (!root) return;
  root.addEventListener('contextmenu', function(e) {
    e.preventDefault();
    const card = e.target.closest && e.target.closest('[data-canvas-card-id], [data-canvas-card-kind]');
    if (card) {
      const kind = card.getAttribute('data-canvas-card-kind') || '';
      const id = card.getAttribute('data-canvas-card-id') || '';
      _canvasShowCardMenu(e.clientX, e.clientY, kind, id, groupName);
    } else {
      _canvasShowEmptyMenu(e.clientX, e.clientY, groupName);
    }
  });
  root.addEventListener('click', function(e) {
    const card = e.target.closest && e.target.closest('[data-canvas-card-id]');
    if (!card) return;
    const id = card.getAttribute('data-canvas-card-id');
    if (!id) return;
    if (typeof onAgentClick === 'function') {
      onAgentClick(id);
    } else if (typeof focusAgent === 'function') {
      focusAgent(id);
    }
  });
  root.addEventListener('dblclick', function(e) {
    const card = e.target.closest && e.target.closest('[data-canvas-card-id]');
    if (!card) return;
    const id = card.getAttribute('data-canvas-card-id');
    if (!id) return;
    if (typeof focusAgent === 'function') {
      focusAgent(id);
    }
  });
}

function _canvasShowEmptyMenu(x, y, groupName) {
  const g = esc(groupName);
  showContextMenu(x, y, [
    { label: 'New architect', action: `openAddArchitectForGroup('${g}')` },
    { label: 'New engineer', action: `openAddEngineerForSection('${g}', '')` },
    { label: 'New worker', action: `openAddWorkerForSection('${g}')` },
  ]);
}

function _canvasCopyAgentValue(id, field) {
  const cell = state && state.agents ? state.agents[id] : null;
  if (!cell || typeof navigator === 'undefined' || !navigator.clipboard) return;
  const value = field === 'name' ? String(cell.name == null ? '' : cell.name) : String(id);
  navigator.clipboard.writeText(value).then(function() {
    if (typeof closeContextMenu === 'function') closeContextMenu();
  });
}

function _canvasFallbackCardMenuItems(id) {
  const safeId = esc(id);
  const idArg = JSON.stringify(String(id));
  return [
    { label: 'Edit…', action: `openEditCell('${safeId}')` },
    { separator: true },
    { label: `Copy ID: ${String(id).slice(0, 8)}…`, action: `_canvasCopyAgentValue(${idArg}, 'id')` },
    { label: 'Copy Name', action: `_canvasCopyAgentValue(${idArg}, 'name')` },
    { label: 'Delete', action: `removeAgent('${safeId}')`, danger: true },
  ];
}

function _canvasInsertAfterFirst(items, label, item) {
  const idx = items.findIndex(function(candidate) {
    return candidate && candidate.label === label;
  });
  if (idx >= 0) items.splice(idx + 1, 0, item);
  else items.unshift(item);
}

function _canvasShowCardMenu(x, y, kind, id, groupName) {
  const cell = state.agents ? state.agents[id] : null;
  if (!cell) return;
  const items = (typeof _cellContextMenuItems === 'function')
    ? _cellContextMenuItems(id).slice()
    : _canvasFallbackCardMenuItems(id);
  if (items.length === 0) return;

  const pause = cell ? _canvasPauseState(cell) : { applicable: false, paused: false, toggleAction: '' };
  const dismissed = (typeof _isLifecycleDismissedCell === 'function')
    ? _isLifecycleDismissedCell(cell)
    : false;

  if (pause.applicable && !dismissed) {
    _canvasInsertAfterFirst(items, 'Edit…', {
      label: pause.paused ? 'Resume event delivery' : 'Pause event delivery',
      action: pause.toggleAction,
    });
  }
  showContextMenu(x, y, items);
}

function _canvasAddEngineerForArchitect(architectId) {
  const group = _canvasGroupName();
  if (typeof openAddEngineerForSection === 'function') {
    openAddEngineerForSection(group, architectId);
  }
}

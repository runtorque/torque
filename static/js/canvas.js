/*
 * Agents canvas view (prototype).
 *
 * Alternative to the default agent grid. Lays out architects as vertical
 * spines on an infinite-canvas pane; engineers branch off each spine to
 * the right; workers fan out horizontally below each engineer. Unowned
 * agents (engineers without an architect, workers without an engineer)
 * collect under a "Standalone" pseudo-tree at the bottom.
 *
 * Right-click context menus surface the create flow:
 *   empty canvas -> + Architect / + Engineer / + Worker
 *   architect    -> + Engineer (hired by this architect)
 *   engineer     -> + Worker
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
  for (const id in state.agents) {
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
  architects.sort(archByName);

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
      if (owner) continue; // belongs to an engineer rendered elsewhere
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
    canvasHtml = '<div class="empty">'
      + '<div class="empty-icon">⬢</div>'
      + 'No groups yet.<br>Create one to get started.'
      + '<br><button type="button" class="empty-action" onclick="openAddGroup()">+ New group</button>'
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

function _canvasRenderTree(tree) {
  const arch = tree.architect;
  const hasEngineers = tree.engineers && tree.engineers.length > 0;
  let html = '';
  html += `<div class="canvas-tree" data-canvas-tree="${esc(arch.id)}">`;

  html += `<div class="canvas-tree-head">`;
  html += _canvasRenderArchitectCard(arch);
  html += `</div>`;

  html += `<div class="canvas-tree-body">`;
  html += `<div class="canvas-rows">`;
  if (hasEngineers) {
    for (const row of tree.engineers) {
      html += _canvasRenderEngineerRow(row);
    }
  } else {
    html += `<div class="canvas-tree-empty" data-canvas-arch-id="${esc(arch.id)}">`;
    html += `<div class="canvas-connector canvas-connector--last"></div>`;
    html += `<button type="button" class="canvas-add-inline" `
         + `onclick="_canvasAddEngineerForArchitect('${esc(arch.id)}')">`
         + `+ Engineer</button>`;
    html += `</div>`;
  }
  html += `</div>`;
  html += `</div>`;

  html += `</div>`;
  return html;
}

function _canvasRenderEngineerRow(row) {
  const eng = row.engineer;
  const workers = row.workers || [];
  let html = '';
  html += `<div class="canvas-eng-row" data-canvas-engineer="${esc(eng.id)}">`;
  html += `<div class="canvas-connector"></div>`;
  html += _canvasRenderEngineerCard(eng);

  if (workers.length > 0) {
    html += `<div class="canvas-worker-manifold">`;
    html += `<div class="canvas-manifold-trunk"></div>`;
    html += `<div class="canvas-worker-bar">`;
    for (const w of workers) {
      html += _canvasRenderWorkerCard(w);
    }
    html += `<button type="button" class="canvas-add-worker" `
         + `onclick="_canvasAddWorkerForEngineer('${esc(eng.id)}')" `
         + `title="Add worker">+</button>`;
    html += `</div></div>`;
  } else {
    html += `<button type="button" class="canvas-add-worker canvas-add-worker--first" `
         + `onclick="_canvasAddWorkerForEngineer('${esc(eng.id)}')">+ Worker</button>`;
  }
  html += `</div>`;
  return html;
}

function _canvasRenderLooseEngineer(row) {
  const eng = row.engineer;
  const workers = row.workers || [];
  let html = '';
  html += `<div class="canvas-loose-engineer" data-canvas-engineer="${esc(eng.id)}">`;
  html += _canvasRenderEngineerCard(eng);

  if (workers.length > 0) {
    html += `<div class="canvas-worker-manifold">`;
    html += `<div class="canvas-manifold-trunk"></div>`;
    html += `<div class="canvas-worker-bar">`;
    for (const w of workers) {
      html += _canvasRenderWorkerCard(w);
    }
    html += `<button type="button" class="canvas-add-worker" `
         + `onclick="_canvasAddWorkerForEngineer('${esc(eng.id)}')" `
         + `title="Add worker">+</button>`;
    html += `</div></div>`;
  } else {
    html += `<button type="button" class="canvas-add-worker canvas-add-worker--first" `
         + `onclick="_canvasAddWorkerForEngineer('${esc(eng.id)}')">+ Worker</button>`;
  }
  html += `</div>`;
  return html;
}

function _canvasRenderLooseWorkersBar(workers) {
  let html = '';
  html += `<div class="canvas-loose-workers">`;
  for (const w of workers) {
    html += _canvasRenderWorkerCard(w);
  }
  html += `</div>`;
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

function _canvasActivityLine(cell) {
  if (!cell) return '';
  const detail = String(cell.activity_detail || '').trim();
  const activity = String(cell.activity || '').trim();
  if (detail) return detail;
  if (activity) return activity;
  if (cell.status === 'idle' || cell.status === 'stopped') return cell.status;
  return '';
}

function _canvasRenderArchitectCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const activity = esc(_canvasActivityLine(cell));
  return `<div class="canvas-card canvas-card-architect ${statusCls}" `
    + `data-canvas-card-id="${esc(cell.id)}" `
    + `data-canvas-card-kind="architect">`
    + `<div class="canvas-card-row">`
    + `<span class="canvas-card-glyph">◆</span>`
    + `<span class="canvas-card-name">${esc(cell.name || cell.slug || cell.id)}</span>`
    + `</div>`
    + `<div class="canvas-card-meta">architect${activity ? ' · ' + activity : ''}</div>`
    + `<div class="canvas-card-status-dot"></div>`
    + `</div>`;
}

function _canvasRenderEngineerCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const activity = esc(_canvasActivityLine(cell));
  return `<div class="canvas-card canvas-card-engineer ${statusCls}" `
    + `data-canvas-card-id="${esc(cell.id)}" `
    + `data-canvas-card-kind="engineer">`
    + `<div class="canvas-card-row">`
    + `<span class="canvas-card-glyph">◇</span>`
    + `<span class="canvas-card-name">${esc(cell.name || cell.slug || cell.id)}</span>`
    + `</div>`
    + `<div class="canvas-card-meta">engineer${activity ? ' · ' + activity : ''}</div>`
    + `<div class="canvas-card-status-dot"></div>`
    + `</div>`;
}

function _canvasRenderWorkerCard(cell) {
  const statusCls = _canvasStatusClass(cell);
  const activity = esc(_canvasActivityLine(cell));
  return `<div class="canvas-card canvas-card-worker ${statusCls}" `
    + `data-canvas-card-id="${esc(cell.id)}" `
    + `data-canvas-card-kind="worker">`
    + `<div class="canvas-card-row">`
    + `<span class="canvas-card-glyph">○</span>`
    + `<span class="canvas-card-name">${esc(cell.name || cell.slug || cell.id)}</span>`
    + `</div>`
    + `<div class="canvas-card-meta">${activity || 'worker'}</div>`
    + `<div class="canvas-card-status-dot"></div>`
    + `</div>`;
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
    { label: 'New engineer (standalone)', action: `openAddEngineerForSection('${g}', '')` },
    { label: 'New worker (standalone)', action: `openAddWorkerForSection('${g}')` },
  ]);
}

function _canvasShowCardMenu(x, y, kind, id, groupName) {
  const items = [];
  const g = esc(groupName);
  const safeId = esc(id);
  if (kind === 'architect') {
    items.push({ label: '+ Engineer here', action: `openAddEngineerForSection('${g}', '${safeId}')` });
    items.push({ separator: true });
    items.push({ label: 'Focus', action: `focusAgent('${safeId}')` });
    items.push({ label: 'Rename…', action: `openEditCell('${safeId}')` });
    items.push({ separator: true });
    items.push({ label: 'Remove', action: `removeAgent('${safeId}')`, danger: true });
  } else if (kind === 'engineer') {
    items.push({ label: '+ Worker here', action: `_canvasAddWorkerForEngineer('${safeId}')` });
    items.push({ separator: true });
    items.push({ label: 'Focus', action: `focusAgent('${safeId}')` });
    items.push({ label: 'Rename…', action: `openEditCell('${safeId}')` });
    items.push({ separator: true });
    items.push({ label: 'Remove', action: `removeAgent('${safeId}')`, danger: true });
  } else if (kind === 'worker') {
    items.push({ label: 'Focus', action: `focusAgent('${safeId}')` });
    items.push({ label: 'Rename…', action: `openEditCell('${safeId}')` });
    items.push({ separator: true });
    items.push({ label: 'Remove', action: `removeAgent('${safeId}')`, danger: true });
  }
  if (items.length === 0) return;
  showContextMenu(x, y, items);
}

function _canvasAddEngineerForArchitect(architectId) {
  const group = _canvasGroupName();
  if (typeof openAddEngineerForSection === 'function') {
    openAddEngineerForSection(group, architectId);
  }
}

function _canvasAddWorkerForEngineer(engineerId) {
  // Note: user-detached workers do not currently accept owner_engineer_id
  // server-side. Prototype: open the standard worker modal; the worker
  // will be created standalone for now. Wiring engineer ownership at
  // creation time is a follow-up.
  const group = _canvasGroupName();
  if (typeof openAddWorkerForSection === 'function') {
    openAddWorkerForSection(group);
  }
  if (typeof _showToast === 'function') {
    _showToast(
      'Prototype: worker will be created as standalone (engineer-owned creation is a follow-up).',
      'info'
    );
  }
}

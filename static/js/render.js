/* Rendering — main UI, agent cells, terminal rows */

function agentIcon(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  return AGENT_ICONS[Math.abs(h) % AGENT_ICONS.length];
}

function processInfo(name) {
  const key = (name || '').toLowerCase().replace(/^-/, '');
  if (PROCESS_MAP[key]) return PROCESS_MAP[key];
  const label = key ? key.slice(0, 3).toUpperCase() : '?';
  return { label, color: '#30363d' };
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function renderSplitBtn(quickAction, customAction) {
  return `<div class="split-btn">`
    + `<button class="split-main" onclick="${quickAction}">+ New</button>`
    + `<button class="split-drop" onclick="event.stopPropagation();toggleMenu(this)">\u25BE</button>`
    + `<div class="split-menu">`
    + `  <button onclick="closeMenus();${customAction}">Custom\u2026</button>`
    + `</div></div>`;
}

function toggleMenu(chevron) {
  const menu = chevron.nextElementSibling;
  const wasOpen = menu.classList.contains('open');
  closeMenus();
  if (!wasOpen) {
    const rect = chevron.parentElement.getBoundingClientRect();
    menu.style.left = rect.left + 'px';
    menu.style.top = (rect.bottom + 2) + 'px';
    menu.style.minWidth = rect.width + 'px';
    menu.classList.add('open');
  }
}
function closeMenus() {
  document.querySelectorAll('.split-menu.open').forEach(m => m.classList.remove('open'));
}

/* Group collapse/expand */
const collapsedGroups = new Set();

function toggleGroup(name) {
  if (collapsedGroups.has(name)) {
    collapsedGroups.delete(name);
  } else {
    collapsedGroups.add(name);
  }
  const el = document.querySelector(`.group[data-group-name="${CSS.escape(name)}"]`);
  if (el) el.classList.toggle('collapsed');
}

/* FLIP animation — capture old positions, render, animate to new positions */
let _flipUntil = 0;

function _captureRects(main) {
  const rects = {};
  main.querySelectorAll('[data-drag-id]').forEach(el => {
    rects[el.dataset.dragId] = el.getBoundingClientRect();
  });
  main.querySelectorAll('[data-group-name]').forEach(el => {
    rects['g:' + el.dataset.groupName] = el.getBoundingClientRect();
  });
  return rects;
}

function _applyFlip(main, oldRects) {
  const els = [
    ...main.querySelectorAll('[data-drag-id]'),
    ...main.querySelectorAll('[data-group-name]'),
  ];
  for (const el of els) {
    const key = el.dataset.dragId || ('g:' + el.dataset.groupName);
    const oldRect = oldRects[key];
    if (!oldRect) continue;
    const newRect = el.getBoundingClientRect();
    const dx = oldRect.left - newRect.left;
    const dy = oldRect.top - newRect.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
    el.animate([
      { transform: `translate(${dx}px, ${dy}px)` },
      { transform: 'translate(0, 0)' }
    ], { duration: 200, easing: 'ease-out' });
  }
}

function render() {
  const main = document.getElementById('main');
  const groupNames = Object.keys(state.groups);

  if (groupNames.length === 0) {
    main.innerHTML = `
      <div class="empty">
        <div class="empty-icon">\u2B22</div>
        No groups yet.<br>Create one to get started.
      </div>`;
    window._navItems = [];
    focusedItemId = null;
    return;
  }

  const doFlip = Date.now() < _flipUntil;
  const oldRects = doFlip ? _captureRects(main) : null;

  const wid = FILTER_BY_WINDOW ? state.current_window_id : null;

  // Clear selectedAgentId if it no longer exists
  if (selectedAgentId && !state.agents[selectedAgentId]) selectedAgentId = null;

  const navItems = [];
  const navAgents = [];  // agents only, for left/right navigation
  const navByGroup = {};  // group name → [item IDs] for up/down within group
  const navGroupOrder = [];  // visible group names in order

  let html = '';
  for (const gname of groupNames) {
    const aids = state.groups[gname] || [];
    const agents = [];
    const standaloneTerms = [];
    for (const id of aids) {
      const c = state.agents[id];
      if (!c) continue;
      if (wid && c.window_id && c.window_id !== wid) continue;
      if (c.cell_type === 'terminal') standaloneTerms.push(c);
      else agents.push(c);
    }
    // Hide group only if it has cells but none in this window;
    // always show truly empty groups so the user can populate them.
    if (wid && agents.length === 0 && standaloneTerms.length === 0 && aids.length > 0) continue;
    navGroupOrder.push(gname);
    const groupNav = [];
    const collapsed = collapsedGroups.has(gname);
    html += `<div class="group${collapsed ? ' collapsed' : ''}" data-group-name="${esc(gname)}">`;
    html += `<div class="group-hdr" draggable="true" data-drag-id="${esc(gname)}" data-drag-type="group">`;
    html += `  <button class="group-toggle" draggable="false" onclick="event.stopPropagation();toggleGroup('${esc(gname)}')">\u25BE</button>`;
    html += `  <span class="group-name" title="${esc(gname)}">${esc(gname)}</span>`;
    html += `  <span class="group-count">${agents.length}</span>`;
    html += `  <button class="group-btn" draggable="false" title="Broadcast to ${esc(gname)}" onclick="openBroadcast('${esc(gname)}')">\u2318</button>`;
    html += `  <button class="group-btn" draggable="false" title="Remove group" onclick="removeGroup('${esc(gname)}')">\u2715</button>`;
    html += `</div>`;

    html += `<div class="group-body"><div class="group-body-inner">`;

    /* Agent grid (+ New cell is part of the grid) */
    html += `<div class="agent-grid" data-drop-group="${esc(gname)}" data-drop-type="agent">`;
    for (const a of agents) {
      if (!collapsed) {
        navItems.push(a.id);
        navAgents.push(a.id);
        groupNav.push(a.id);
        // Insert child terminals right after parent for keyboard nav
        if (a.id === selectedAgentId) {
          const cIds = state.children[a.id] || [];
          for (const cid of cIds) {
            const ct = state.agents[cid];
            if (ct && (!wid || !ct.window_id || ct.window_id === wid)) {
              navItems.push(cid);
              groupNav.push(cid);
            }
          }
        }
      }
      html += renderAgentCell(a);
    }
    html += `<div class="cell cell-add" onclick="quickAddAgent('${esc(gname)}')">`;
    html += `  <div class="cell-add-icon">+</div>`;
    html += `  <div class="cell-name">New</div>`;
    html += `  <button class="cell-add-drop" onclick="event.stopPropagation();toggleMenu(this)">\u25BE</button>`;
    html += `  <div class="split-menu"><button onclick="closeMenus();openAddAgent('${esc(gname)}')">Custom\u2026</button></div>`;
    html += `</div>`;
    html += `</div>`;

    /* Terminal drawer for selected agent (if in this group) */
    const selAgent = selectedAgentId && state.agents[selectedAgentId];
    if (selAgent && selAgent.group === gname) {
      const childIds = state.children[selectedAgentId] || [];
      const childTerms = childIds
        .map(id => state.agents[id])
        .filter(c => c && (!wid || !c.window_id || c.window_id === wid));
      html += `<div class="terminal-drawer">`;
      html += `<div class="drawer-hdr">`;
      html += `  <span class="drawer-label">${esc(selAgent.name)} terminals</span>`;
      html += `  <span class="drawer-count">${childTerms.length}</span>`;
      html += `</div>`;
      html += `<div class="term-list" data-drop-type="terminal" data-drop-group="${esc(gname)}" data-drop-parent="${esc(selectedAgentId)}">`;
      for (const t of childTerms) html += renderTerminalRow(t);
      html += `</div>`;
      html += renderTermAddBtn(gname, selectedAgentId);
      html += `</div>`;
    }

    /* Standalone terminals (no parent) */
    if (standaloneTerms.length > 0) {
      html += `<div class="section-label">Terminals</div>`;
      html += `<div class="term-list" data-drop-group="${esc(gname)}" data-drop-type="terminal">`;
      for (const t of standaloneTerms) {
        navItems.push(t.id);
        groupNav.push(t.id);
        html += renderTerminalRow(t);
      }
      html += `</div>`;
      html += renderTermAddBtn(gname, '');
    }

    html += `</div></div>`;
    html += `</div>`;
    navByGroup[gname] = groupNav;
  }

  main.innerHTML = html;

  // Update navigable items lists; clear focus if item was removed
  window._navItems = navItems;
  window._navAgents = navAgents;
  window._navByGroup = navByGroup;
  window._navGroupOrder = navGroupOrder;
  if (focusedItemId && !navItems.includes(focusedItemId)) focusedItemId = null;

  if (oldRects) _applyFlip(main, oldRects);
}

function renderAgentCell(a) {
  const active = a.session_id && a.session_id === state.active_session_id;
  const selected = a.id === selectedAgentId;
  const childCount = (state.children[a.id] || []).length;
  const cls = ['cell'];
  if (active) cls.push('active');
  if (selected) cls.push('selected');
  if (a.id === focusedItemId) cls.push('focused');
  if (a.status === 'stopped') cls.push('stopped');

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${a.id}" data-drag-type="agent" data-drag-group="${esc(a.group)}" onclick="onAgentClick('${a.id}')" ondblclick="onAgentDblClick('${a.id}')" title="${esc(a.name)} (${a.status})">`;
  h += `<div class="cell-status ${a.status}"></div>`;
  h += `<button class="cell-close" draggable="false" onclick="event.stopPropagation();removeAgent('${a.id}')" title="Remove">\u2715</button>`;
  h += `<div class="cell-icon">${agentIcon(a.name)}</div>`;
  h += `<div class="cell-name">${esc(a.name)}</div>`;
  if (childCount > 0) {
    h += `<div class="cell-term-count">${childCount}</div>`;
  }
  if (a.status === 'stopped') {
    h += `<button class="cell-relaunch" onclick="event.stopPropagation();relaunchAgent('${a.id}')" title="Relaunch">\u21BB relaunch</button>`;
  }
  h += `</div>`;
  return h;
}

function renderTermAddBtn(gname, parentId) {
  const pid = parentId ? esc(parentId) : '';
  let h = `<div class="term-row term-add" onclick="quickAddTerminal('${esc(gname)}','${pid}')">`;
  h += `<div class="term-badge" style="background:var(--border)">+</div>`;
  h += `<div class="term-info"><div class="term-name" style="color:var(--text-dim)">New terminal</div></div>`;
  h += `<button class="term-action" onclick="event.stopPropagation();toggleMenu(this)" title="Custom">\u25BE</button>`;
  h += `<div class="split-menu"><button onclick="closeMenus();openAddTerminal('${esc(gname)}','${pid}')">Custom\u2026</button></div>`;
  h += `</div>`;
  return h;
}

function renderTerminalRow(t) {
  const active = t.session_id && t.session_id === state.active_session_id;
  const cls = ['term-row'];
  if (active) cls.push('active');
  if (t.id === focusedItemId) cls.push('focused');
  if (t.status === 'stopped') cls.push('stopped');

  const proc = t.status === 'stopped'
    ? { label: 'OFF', color: '#6e7681' }
    : processInfo(t.current_process);

  const darkCls = proc.dark ? ' dark-text' : '';
  let pathDisplay = '';
  if (t.current_branch && t.git_root) {
    const repoName = t.git_root.split('/').pop();
    let rel = t.current_path || '';
    if (rel.startsWith(t.git_root)) {
      rel = rel.slice(t.git_root.length);
      if (rel.startsWith('/')) rel = rel.slice(1);
    }
    pathDisplay = t.current_branch + ' | ' + repoName + (rel ? '/' + rel : '');
  } else if (t.current_path) {
    pathDisplay = t.current_path.replace(/^\/Users\/[^/]+/, '~');
  }

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${t.id}" data-drag-type="terminal" data-drag-group="${esc(t.group)}" onclick="focusAgent('${t.id}')">`;
  h += `<div class="term-badge${darkCls}" style="background:${proc.color}">${proc.label}</div>`;
  h += `<div class="term-info">`;
  h += `  <div class="term-name">${esc(t.name)}</div>`;
  if (pathDisplay) {
    h += `<div class="term-path" title="${esc(t.current_path)}">${esc(pathDisplay)}</div>`;
  }
  h += `</div>`;
  h += `<div class="term-status ${t.status}"></div>`;
  h += `<div class="term-actions">`;
  if (t.status === 'stopped') {
    h += `<button class="term-action" onclick="event.stopPropagation();relaunchAgent('${t.id}')" title="Relaunch">\u21BB</button>`;
  }
  h += `<button class="term-action danger" onclick="event.stopPropagation();removeAgent('${t.id}')" title="Remove">\u2715</button>`;
  h += `</div>`;
  h += `</div>`;
  return h;
}

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

function formatCode(s) {
  return esc(s).replace(/`([^`]+)`/g, '<span class="code-inline">$1</span>');
}

function _trimTrailingSlash(path) {
  const text = String(path || '');
  if (!text || text === '/') return text;
  return text.replace(/\/+$/, '');
}

function _pathBaseName(path) {
  const text = _trimTrailingSlash(path);
  if (!text) return '';
  const parts = text.split('/');
  return parts[parts.length - 1] || '';
}

function _homeDirectoryPrefix() {
  return _trimTrailingSlash(
    (state && state.runtime && state.runtime.home_directory) || ''
  );
}

function _formatDisplayPath(path, repoRoot) {
  const fullPath = _trimTrailingSlash(path);
  if (!fullPath) return '';

  const root = _trimTrailingSlash(repoRoot);
  if (root && (fullPath === root || fullPath.startsWith(root + '/'))) {
    const repoName = _pathBaseName(root);
    if (!repoName) return fullPath;
    const rel = fullPath === root ? '' : fullPath.slice(root.length + 1);
    return repoName + (rel ? '/' + rel : '');
  }

  const home = _homeDirectoryPrefix();
  if (home && (fullPath === home || fullPath.startsWith(home + '/'))) {
    const rel = fullPath === home ? '' : fullPath.slice(home.length + 1);
    return '~' + (rel ? '/' + rel : '');
  }

  return fullPath
    .replace(/^\/Users\/[^/]+(?=\/|$)/, '~')
    .replace(/^\/home\/[^/]+(?=\/|$)/, '~');
}

function _embeddedRuntimeEnabled() {
  if (typeof isEmbeddedTerminalMode === 'function') return !!isEmbeddedTerminalMode();
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function renderSplitBtn(quickAction, customAction) {
  return `<div class="split-btn">`
    + `<button class="split-main" onclick="${quickAction}">+ New</button>`
    + `<button class="split-drop" onclick="event.stopPropagation();toggleMenu(this)">\u25BE</button>`
    + `<div class="split-menu">`
    + `  <button onclick="closeMenus();${customAction}">Custom\u2026</button>`
    + `</div></div>`;
}

function _renderAgentTemplateMenuItems(group) {
  const templates = (_cachedAgentTemplates || []).filter(t => !t.shadowed);
  let html = '';
  for (const t of templates) {
    const label = t.display_name || t.name;
    html += `<button onclick="event.stopPropagation();closeMenus();newAgentFromTemplate('${esc(group)}','${esc(t.name)}')">${esc(label)}</button>`;
  }
  return html;
}

function _renderWeaverMenuItem(group, groupSettings) {
  if ((groupSettings || {}).weaver_agent_id) return '';
  return `<button onclick="event.stopPropagation();closeMenus();newWeaver('${esc(group)}')">Weaver</button>`;
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
const _collapsedInitialized = new Set();

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

function _surfacePanelApp(surface) {
  if (surface === 'board') return 'board';
  if (surface === 'context') return 'context';
  if (surface === 'events') return 'events';
  if (surface === 'weaver') return 'weaver';
  if (surface === 'templates') return 'templates';
  return '';
}

function _activePanelSurface() {
  return _surfacePanelApp(typeof _activePanelApp !== 'undefined' ? _activePanelApp : '');
}

function _renderSurface(surface) {
  if (surface === 'board' && typeof renderBoard === 'function') renderBoard();
  if (surface === 'context' && typeof renderContextPanel === 'function') renderContextPanel();
  if (surface === 'events' && typeof renderEvents === 'function') renderEvents();
  if (surface === 'weaver' && typeof renderWeaverPanel === 'function') renderWeaverPanel();
  if (surface === 'templates' && typeof renderAgentTemplatesPanel === 'function') renderAgentTemplatesPanel();
}

function renderActivePanel() {
  const surface = _activePanelSurface();
  if (surface) _renderSurface(surface);
  _updateWeaverTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
}

function renderInvalidatedSurfaces(flags) {
  if (!flags) return;
  if (flags.main) render();
  const surface = _activePanelSurface();
  if (surface && flags[surface]) _renderSurface(surface);
  _updateWeaverTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
}

function _updateWeaverTaskbarBadge() {
  const btn = document.querySelector('.taskbar-app[data-app="weaver"]');
  if (!btn) return;
  let hasAsk = false;
  for (const name in (state.weaver_settings || {})) {
    if (state.weaver_settings[name].pending_question) {
      hasAsk = true;
      break;
    }
  }
  btn.classList.toggle('has-badge', hasAsk);
}

function _findSurfaceNode(root, selector) {
  if (!root || !selector) return null;
  if (selector === ':root') return root;
  if (selector.charAt(0) === '#' && document.getElementById) {
    return document.getElementById(selector.slice(1));
  }
  return root.querySelector ? root.querySelector(selector) : null;
}

function _surfaceContains(root, el) {
  if (!root || !el) return false;
  if (typeof root.contains === 'function' && root.contains(el)) return true;
  if (!el.id) return false;
  return _findSurfaceNode(root, '#' + el.id) === el;
}

function _captureSurfaceState(root, opts) {
  if (!root) return null;
  opts = opts || {};
  const snapshot = { focus: null, scrolls: [] };
  const active = document.activeElement;
  if (active && _surfaceContains(root, active)) {
    let key = active.id ? ('#' + active.id) : '';
    if (!key && typeof opts.captureFocusKey === 'function') {
      key = opts.captureFocusKey(active, root) || '';
    }
    if (key) {
      snapshot.focus = {
        key,
        value: 'value' in active ? active.value : null,
        checked: 'checked' in active ? !!active.checked : null,
        selectionStart: typeof active.selectionStart === 'number' ? active.selectionStart : null,
        selectionEnd: typeof active.selectionEnd === 'number' ? active.selectionEnd : null,
      };
    }
  }
  const selectors = opts.scrollSelectors || [];
  for (let i = 0; i < selectors.length; i++) {
    const selector = selectors[i];
    const el = _findSurfaceNode(root, selector);
    if (!el) continue;
    snapshot.scrolls.push({
      selector,
      top: typeof el.scrollTop === 'number' ? el.scrollTop : null,
      left: typeof el.scrollLeft === 'number' ? el.scrollLeft : null,
    });
  }
  if (typeof opts.capture === 'function') opts.capture(snapshot, root);
  return snapshot;
}

function _restoreSurfaceState(root, snapshot, opts) {
  if (!root || !snapshot) return;
  opts = opts || {};
  for (let i = 0; i < snapshot.scrolls.length; i++) {
    const saved = snapshot.scrolls[i];
    const el = _findSurfaceNode(root, saved.selector);
    if (!el) continue;
    if (typeof saved.top === 'number') el.scrollTop = saved.top;
    if (typeof saved.left === 'number') el.scrollLeft = saved.left;
  }
  if (typeof opts.restore === 'function') opts.restore(root, snapshot);
  if (!snapshot.focus) return;
  let el = _findSurfaceNode(root, snapshot.focus.key);
  if (!el && typeof opts.resolveFocus === 'function') {
    el = opts.resolveFocus(root, snapshot.focus);
  }
  if (!el) return;
  if (snapshot.focus.value != null && 'value' in el) el.value = snapshot.focus.value;
  if (snapshot.focus.checked != null && 'checked' in el) el.checked = snapshot.focus.checked;
  if (typeof el.focus === 'function') el.focus();
  if (typeof snapshot.focus.selectionStart === 'number' && 'selectionStart' in el) {
    el.selectionStart = snapshot.focus.selectionStart;
  }
  if (typeof snapshot.focus.selectionEnd === 'number' && 'selectionEnd' in el) {
    el.selectionEnd = snapshot.focus.selectionEnd;
  }
}

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
  const embeddedMode = _embeddedRuntimeEnabled();

  if (groupNames.length === 0) {
    main.innerHTML = `
      <div class="empty">
        <div class="empty-icon">\u2B22</div>
        No groups yet.<br>Create one to get started.
      </div>`;
    window._navItems = [];
    focusedItemId = null;
    if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
    return;
  }

  const doFlip = Date.now() < _flipUntil;
  const oldRects = doFlip ? _captureRects(main) : null;

  // Clear selectedAgentId if it no longer exists
  if (selectedAgentId && !state.agents[selectedAgentId]) selectedAgentId = null;
  if (typeof selectedTerminalId !== 'undefined'
      && selectedTerminalId
      && !state.agents[selectedTerminalId]) {
    selectedTerminalId = null;
  }

  const navItems = [];
  const navAgents = [];  // agents only, for left/right navigation
  const navByGroup = {};  // group name → [item IDs] for up/down within group
  const navGroupOrder = [];  // visible group names in order

  let html = '';
  for (const gname of groupNames) {
    const aids = state.groups[gname] || [];

    // Per-group window filtering based on filter_by_window setting
    const gsFilter = (state.group_settings || {})[gname] || {};
    let wid = null;
    if (!embeddedMode && gsFilter.filter_by_window && state.current_window_id) {
      // Check if any cell in this group has an active session
      const hasActive = aids.some(id => {
        const c = state.agents[id];
        if (c && c.session_id) return true;
        // Also check child terminals
        const kids = state.children[id] || [];
        return kids.some(kid => {
          const ct = state.agents[kid];
          return ct && ct.session_id;
        });
      });
      // Only filter if there are active sessions; otherwise show everywhere
      if (hasActive) wid = state.current_window_id;
    } else if (!embeddedMode && getFilterByWindow()) {
      wid = state.current_window_id;
    }

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

    /* collapsed-default on first render of this group */
    const gsLocal = (state.group_settings || {})[gname] || {};
    if (!_collapsedInitialized.has(gname)) {
      _collapsedInitialized.add(gname);
      if (gsLocal.collapsed_default) collapsedGroups.add(gname);
    }
    const collapsed = collapsedGroups.has(gname);

    html += `<div class="group${collapsed ? ' collapsed' : ''}" data-group-name="${esc(gname)}">`;
    html += `<div class="group-hdr" draggable="true" data-drag-id="${esc(gname)}" data-drag-type="group" oncontextmenu="onGroupContextMenu(event,'${esc(gname)}')">`;
    html += `  <button class="group-toggle" draggable="false" onclick="event.stopPropagation();toggleGroup('${esc(gname)}')">\u25BE</button>`;
    html += `  <span class="group-name" title="${esc(gname)}">${esc(gname)}</span>`;
    html += `  <span class="group-count">${agents.length}</span>`;
    html += `  <button class="group-btn" draggable="false" title="Group settings" onclick="event.stopPropagation();openGroupSettings('${esc(gname)}')">\u2699</button>`;
    html += `  <button class="group-btn" draggable="false" title="Broadcast to ${esc(gname)}" onclick="openBroadcast('${esc(gname)}')">\u2318</button>`;
    html += `  <button class="group-btn" draggable="false" title="Remove group" onclick="removeGroup('${esc(gname)}')">\u2715</button>`;
    html += `</div>`;

    html += `<div class="group-body"><div class="group-body-inner">`;

    /* Agent grid (+ New cell is part of the grid) — weaver pinned first */
    const weaverId = gsLocal.weaver_agent_id || '';
    if (weaverId) {
      agents.sort((a, b) => (a.id === weaverId ? -1 : b.id === weaverId ? 1 : 0));
    }
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
    const atCap = gsLocal.max_agents > 0 && agents.length >= gsLocal.max_agents;
    if (atCap) {
      html += `<div class="cell cell-add disabled">`;
      html += `  <div class="cell-add-icon">\u2013</div>`;
      html += `  <div class="cell-name">Full</div>`;
      html += `</div>`;
    } else {
      html += `<div class="cell cell-add" onclick="quickAddAgent('${esc(gname)}')">`;
      html += `  <div class="cell-add-icon">+</div>`;
      html += `  <div class="cell-name">New</div>`;
      html += `  <button class="cell-add-drop" onclick="event.stopPropagation();toggleMenu(this)">\u25BE</button>`;
      html += `  <div class="split-menu">`;
      html += _renderWeaverMenuItem(gname, gsLocal);
      html += _renderAgentTemplateMenuItems(gname);
      html += `<button onclick="event.stopPropagation();closeMenus();openAddAgent('${esc(gname)}')">Custom\u2026</button>`;
      html += `</div>`;
      html += `</div>`;
    }
    html += `</div>`;

    /* Details + terminal drawer for selected agent (if in this group) */
    const selectedCell = selectedTerminalId && state.agents[selectedTerminalId];
    const selAgent = selectedAgentId && state.agents[selectedAgentId];
    const showAgentDetails = !!(
      selAgent
      && selAgent.group === gname
      && (
        !embeddedMode
        || !selectedCell
        || selectedCell.cell_type === 'agent'
        || selectedCell.parent_id === selAgent.id
      )
    );
    if (showAgentDetails) {
      /* Agent details section */
      html += renderAgentDetails(selAgent);

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
  _updateWeaverTaskbarBadge();
  if (typeof updateEventsAttentionBadge === 'function') updateEventsAttentionBadge();
  if (typeof renderTerminalWorkspace === 'function') renderTerminalWorkspace();
}

function agentStatusClass(a) {
  /* Attention overrides everything */
  if (a.needs_attention) return 'attention';
  /* Disconnected (tab closed) */
  if (a.status === 'stopped') return 'disconnected';
  /* For awareness agents, activity is the source of truth */
  if (a.agent_type) {
    if (a.activity) return 'working';
    /* No activity — idle if we've heard from the agent before;
       otherwise it just started and hasn't sent its first event yet */
    if (a.last_event_at > 0) return 'idle';
  }
  /* Non-awareness agents / agents that haven't sent events yet */
  if (a.status === 'running') return 'working';
  return 'idle';
}

function _getAgentTask(agentId) {
  if (!state.board_tasks) return null;
  let openTask = null;
  let doneTask = null;
  for (const t of Object.values(state.board_tasks)) {
    if (t.agent_id !== agentId) continue;
    if (t.lane === 'In Progress') return t;
    if (t.lane !== 'Done' && !openTask) openTask = t;
    if (!doneTask) doneTask = t;
  }
  return openTask || doneTask || null;
}

function _taskBoundaryMeta(task) {
  if (!task || !task.worktree_boundary || typeof task.worktree_boundary !== 'object') {
    return {};
  }
  return task.worktree_boundary;
}

function _taskBoundaryBranchKey(task) {
  const boundary = _taskBoundaryMeta(task);
  const repoRoot = boundary.repo_root || '';
  const branch = boundary.branch || '';
  if (!repoRoot || !branch) return '';
  return repoRoot + '::' + branch;
}

function _taskBoundarySortValue(task) {
  const boundary = _taskBoundaryMeta(task);
  return String(boundary.recorded_at || task.updated_at || task.created_at || '');
}

function _branchBoundaryOverviewForContext(repoRoot, branch) {
  if (!repoRoot || !branch || !state || !state.board_tasks) return null;
  const branchKey = repoRoot + '::' + branch;
  let latest = null;
  for (const task of Object.values(state.board_tasks)) {
    if (_taskBoundaryBranchKey(task) !== branchKey) continue;
    if ((_taskBoundaryMeta(task).status || '') !== 'open') continue;
    if (!latest || _taskBoundarySortValue(task) > _taskBoundarySortValue(latest)) {
      latest = task;
    }
  }
  if (!latest) return null;
  const queued = [];
  const started = [];
  for (const task of Object.values(state.board_tasks)) {
    if ((task.resume_after_boundary_task_id || '') !== latest.id) continue;
    if (task.lane === 'Backlog' || task.lane === 'To Do') queued.push(task);
    else started.push(task);
  }
  const sortFollowers = function(a, b) {
    return String(a.created_at || a.updated_at || a.id || '')
      .localeCompare(String(b.created_at || b.updated_at || b.id || ''));
  };
  queued.sort(sortFollowers);
  started.sort(sortFollowers);
  return {
    repo_root: repoRoot,
    branch: branch,
    latest_boundary_task: latest,
    queued_followers: queued,
    started_followers: started,
    branch_advanced: started.length > 0,
    partial_review_safe: started.length === 0,
  };
}

function _branchBoundaryOverviewForAgent(agent) {
  if (!agent) return null;
  const repoRoot = agent.worktree_repo_root || agent.git_root || '';
  const branch = agent.worktree_branch || '';
  const overview = _branchBoundaryOverviewForContext(repoRoot, branch);
  if (!overview) return null;
  overview.current_task = _getAgentTask(agent.id);
  return overview;
}

function _agentCellSubtitle(a) {
  const task = _getAgentTask(a.id);
  if (task && task.task) return task.task;
  if (!_embeddedRuntimeEnabled()) return '';
  return a.activity_detail
    || _formatDisplayPath(
      a.current_path || a.directory || '',
      a.git_root || a.worktree_repo_root || ''
    )
    || a.command
    || '';
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
  // Check if this agent is the weaver for its group
  const _gs = (state.group_settings || {})[a.group];
  const _isWeaver = _gs && _gs.weaver_agent_id === a.id;
  if (_isWeaver) cls.push('weaver');
  // Check if weaver is awaiting human input
  const _weaverWs = _isWeaver && state.weaver_settings
    ? state.weaver_settings[a.group] : null;
  const _weaverPaused = !!(_weaverWs && _weaverWs.paused);
  const _weaverAsking = _weaverWs && _weaverWs.pending_question;
  if (_weaverAsking) cls.push('weaver-asking');
  if (_isWeaver) cls.push(_weaverPaused ? 'weaver-paused' : 'weaver-running');

  const statusCls = agentStatusClass(a);
  const titleParts = [a.name, `(${a.status})`];
  if (a.needs_attention && a.error_message) titleParts.push(`\u2014 ${a.error_message}`);
  else if (a.activity_detail) titleParts.push(`\u2014 ${a.activity_detail}`);

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${a.id}" data-drag-type="agent" data-drag-group="${esc(a.group)}" onclick="onAgentClick('${a.id}')" ondblclick="onAgentDblClick('${a.id}')" oncontextmenu="onCellContextMenu(event,'${a.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${a.id}')}" title="${esc(titleParts.join(' '))}">`;
  h += `<div class="cell-status ${statusCls}"${statusCls === 'attention' ? ' title="' + esc(a.error_message || 'Needs attention') + '"' : ''}>${statusCls === 'attention' ? '!' : ''}</div>`;
  h += `<div class="cell-header-controls">`;
  h += `<button class="cell-close" draggable="false" onclick="event.stopPropagation();removeAgent('${a.id}')" title="Remove">\u2715</button>`;
  if (_isWeaver) {
    const weaverGroupArg = encodeURIComponent(a.group || '');
    h += `<button class="cell-weaver-toggle ${_weaverPaused ? 'paused' : 'running'}" draggable="false" onclick="event.stopPropagation();weaverTogglePauseForGroup(decodeURIComponent('${weaverGroupArg}'))" title="${_weaverPaused ? 'Resume Weaver event delivery' : 'Pause Weaver event delivery'}">${_weaverPaused ? '&#x25B6;' : '&#x23F8;'}</button>`;
  }
  h += `</div>`;
  h += `<div class="cell-icon">${a.icon || agentIcon(a.name)}</div>`;
  h += `<div class="cell-name">${esc(a.name)}</div>`;
  if (_weaverAsking) {
    h += `<div class="cell-weaver-ask" title="${esc(_weaverWs.pending_question)}">? awaiting input</div>`;
  }
  const cellSubtitle = _agentCellSubtitle(a);
  if (cellSubtitle) {
    h += `<div class="cell-task" title="${esc(cellSubtitle)}">${formatCode(cellSubtitle)}</div>`;
  }
  /* Agent type badge */
  if (a.agent_type) {
    const typeInfo = AGENT_TYPE_LABELS[a.agent_type] || { short: a.agent_type.slice(0, 2).toUpperCase() };
    if (typeInfo.short) {
      h += `<div class="cell-type">${typeInfo.short}</div>`;
    }
  }
  if (childCount > 0) {
    h += `<div class="cell-term-count">${childCount}</div>`;
  }
  if (a.status === 'stopped') {
    h += `<button class="cell-relaunch" onclick="event.stopPropagation();relaunchAgent('${a.id}')" title="Relaunch">\u21BB relaunch</button>`;
  }
  h += `</div>`;
  return h;
}

function renderAgentDetails(a) {
  const statusCls = agentStatusClass(a);
  const typeInfo = a.agent_type ? (AGENT_TYPE_LABELS[a.agent_type] || { label: a.agent_type }) : null;

  let h = `<div class="agent-details">`;
  h += `<div class="detail-hdr">`;
  h += `  <span class="detail-name">${esc(a.name)}</span>`;
  if (typeInfo && typeInfo.label) {
    h += `  <span class="detail-type">${esc(typeInfo.label)}</span>`;
  }
  h += `  <span class="detail-status ${statusCls}">`;
  if (statusCls === 'attention') h += esc(a.error_message || 'Needs attention');
  else if (statusCls === 'working') h += 'Working';
  else if (statusCls === 'idle') h += 'Idle';
  else if (statusCls === 'disconnected') h += 'Stopped';
  h += `</span>`;
  h += `</div>`;

  /* Linked task */
  const _dt = _getAgentTask(a.id);
  if (_dt) {
    h += `<div class="detail-row detail-row-task"><span class="detail-label">Task</span><span class="detail-val detail-task" title="${esc(_dt.task)}">${formatCode(_dt.task)}</span>`;
    if (_dt.action_name) {
      h += `<span class="detail-task-action">${esc(_dt.action_name)}</span>`;
    }
    if (_dt.status) {
      h += `<span class="detail-task-status">${esc(_dt.status)}</span>`;
    } else if (_dt.lane) {
      h += `<span class="detail-task-lane">${esc(_dt.lane)}</span>`;
    }
    h += `</span></div>`;
  }

  const boundaryOverview = _branchBoundaryOverviewForAgent(a);
  if (boundaryOverview && boundaryOverview.latest_boundary_task) {
    const boundaryTask = boundaryOverview.latest_boundary_task;
    const boundaryState = boundaryOverview.branch_advanced ? 'advanced' : 'safe';
    const boundaryBadge = boundaryOverview.branch_advanced
      ? 'Branch advanced'
      : 'Safe review point';
    h += `<div class="detail-row"><span class="detail-label">Review point</span><span class="detail-val detail-task" title="${esc(boundaryTask.task)}">${formatCode(boundaryTask.task)}<span class="detail-task-status">${esc(boundaryBadge)}</span></span></div>`;
    if (boundaryOverview.queued_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Queued next</span><span class="detail-val">${esc(boundaryOverview.queued_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
    if (boundaryOverview.started_followers.length) {
      h += `<div class="detail-row"><span class="detail-label">Beyond boundary</span><span class="detail-val">${esc(boundaryOverview.started_followers.map(function(task) { return task.task; }).join(', '))}</span></div>`;
    }
  }

  /* MCP Messages */
  if (a.mcp_messages && a.mcp_messages.length) {
    const icons = { progress: '\u25CF', done: '\u2714', ready: '\u2714', blocked: '\u26D4', error: '\u2716', derive: '\u2934', ask: '\u2753', name: '\u270E' };
    h += `<div class="detail-row detail-row-mcp"><span class="detail-label">Messages</span>`;
    h += `<div class="mcp-log">`;
    const msgs = a.mcp_messages.slice(0, 20);
    for (const m of msgs) {
      const ico = icons[m.action] || '\u25CF';
      const ago = _relativeTime(m.timestamp);
      h += `<div class="mcp-entry mcp-${esc(m.action)}"><span class="mcp-icon">${ico}</span><span class="mcp-text">${esc(m.message)}</span><span class="mcp-time">${esc(ago)}</span></div>`;
    }
    h += `</div></div>`;
  }

  /* Branch — worktree branch takes priority, then regular git branch */
  if (a.worktree_branch) {
    const branch = a.worktree_branch.replace(/^loom\//, '');
    let branchExtra = '';
    if (a.worktree_merged) {
      branchExtra += ' <span class="detail-wt-tag detail-wt-merged">merged</span>';
    } else {
      branchExtra += ' <span class="detail-wt-tag">worktree</span>';
    }
    const behind = a.worktree_behind || 0;
    const ahead = a.worktree_ahead || 0;
    if (behind || ahead) {
      let parts = [];
      if (ahead) parts.push(`<span class="detail-ahead">\u2191${ahead}</span>`);
      if (behind) parts.push(`<span class="detail-behind">\u2193${behind}</span>`);
      branchExtra += ' ' + parts.join(' ');
    }
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(branch)}${branchExtra}</span></div>`;
    const diff = a.worktree_diff || {};
    if (diff.files) {
      h += `<div class="detail-row"><span class="detail-label">Changes</span><span class="detail-val">${diff.files} file${diff.files !== 1 ? 's' : ''} <span class="detail-ins">+${diff.insertions || 0}</span> <span class="detail-del">-${diff.deletions || 0}</span></span></div>`;
    }
    if (a.worktree_checkpoints > 0) {
      h += `<div class="detail-row"><span class="detail-label">Checkpoints</span><span class="detail-val">${a.worktree_checkpoints}</span></div>`;
    }
  } else if (a.current_branch) {
    h += `<div class="detail-row"><span class="detail-label">Branch</span><span class="detail-val detail-branch">\u2387 ${esc(a.current_branch)}</span></div>`;
  }

  /* Directory */
  const directoryPath = a.current_path || a.directory || '';
  if (directoryPath) {
    const dir = _formatDisplayPath(directoryPath, a.git_root || a.worktree_repo_root || '');
    h += `<div class="detail-row"><span class="detail-label">Directory</span><span class="detail-val detail-dir" title="${esc(directoryPath)}">${esc(dir)}</span></div>`;
  }

  /* Last event */
  if (a.last_event_at > 0) {
    const ago = _relativeTime(a.last_event_at);
    const showAgo = ((Date.now() / 1000) - a.last_event_at) > 30;
    if (a.last_event_text) {
      h += `<div class="detail-row detail-row-event"><span class="detail-label">Last event</span><span class="detail-val detail-last-event">${esc(a.last_event_text)}${showAgo ? ` <span class="detail-time">(${esc(ago)})</span>` : ''}</span></span></div>`;
    } else {
      h += `<div class="detail-row"><span class="detail-label">Last event</span><span class="detail-val">${esc(ago)}</span></div>`;
    }
  }

  h += `</div>`;
  return h;
}

function _relativeTime(ts) {
  const diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function renderTermAddBtn(gname, parentId) {
  const pid = parentId ? esc(parentId) : '';
  let h = `<div class="term-row term-add" onclick="quickAddTerminal('${esc(gname)}','${pid}')">`;
  h += `<div class="term-badge" style="background:var(--border)">+</div>`;
  h += `<div class="term-info"><div class="term-name" style="color:var(--text-dim)">New terminal</div></div>`;
  h += `<button class="term-action" onclick="event.stopPropagation();toggleMenu(this)" title="Custom">\u25BE</button>`;
  h += `<div class="split-menu"><button onclick="event.stopPropagation();closeMenus();openAddTerminal('${esc(gname)}','${pid}')">Custom\u2026</button></div>`;
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
  const fullPath = t.current_path || t.directory || '';
  const pathDisplay = _formatDisplayPath(fullPath, t.git_root || t.worktree_repo_root || '');

  let h = `<div class="${cls.join(' ')}" draggable="true" data-drag-id="${t.id}" data-drag-type="terminal" data-drag-group="${esc(t.group)}" onclick="focusAgent('${t.id}')" oncontextmenu="onCellContextMenu(event,'${t.id}')" onauxclick="if(event.button===1){event.preventDefault();removeAgent('${t.id}')}">`;
  h += `<div class="term-badge${darkCls}" style="background:${proc.color}">${proc.label}</div>`;
  h += `<div class="term-info">`;
  h += `  <div class="term-name">${esc(t.name)}</div>`;
  if (pathDisplay) {
    h += `<div class="term-path" title="${esc(fullPath)}">${esc(pathDisplay)}</div>`;
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

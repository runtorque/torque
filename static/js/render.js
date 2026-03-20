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
  if (!wasOpen) menu.classList.add('open');
}
function closeMenus() {
  document.querySelectorAll('.split-menu.open').forEach(m => m.classList.remove('open'));
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
    return;
  }

  let html = '';
  for (const gname of groupNames) {
    const aids = state.groups[gname] || [];
    const agents = [];
    const terminals = [];
    for (const id of aids) {
      const c = state.agents[id];
      if (!c) continue;
      (c.cell_type === 'terminal' ? terminals : agents).push(c);
    }
    html += `<div class="group">`;
    html += `<div class="group-hdr">`;
    html += `  <span class="group-name" title="${esc(gname)}">${esc(gname)}</span>`;
    html += `  <span class="group-count">${aids.length}</span>`;
    html += `  <button class="group-btn" title="Broadcast to ${esc(gname)}" onclick="openBroadcast('${esc(gname)}')">\u2318</button>`;
    html += `  <button class="group-btn" title="Remove group" onclick="removeGroup('${esc(gname)}')">\u2715</button>`;
    html += `</div>`;

    html += `<div class="section-label">Agents</div>`;
    if (agents.length > 0) {
      html += `<div class="agent-grid">`;
      for (const a of agents) html += renderAgentCell(a);
      html += `</div>`;
    }
    html += `<div class="section-btns">`;
    html += renderSplitBtn(`quickAddAgent('${esc(gname)}')`, `openAddAgent('${esc(gname)}')`);
    html += `</div>`;

    html += `<div class="section-label">Terminals</div>`;
    if (terminals.length > 0) {
      html += `<div class="term-list">`;
      for (const t of terminals) html += renderTerminalRow(t);
      html += `</div>`;
    }
    html += `<div class="section-btns">`;
    html += renderSplitBtn(`quickAddTerminal('${esc(gname)}')`, `openAddTerminal('${esc(gname)}')`);
    html += `</div>`;

    html += `</div>`;
  }

  main.innerHTML = html;
}

function renderAgentCell(a) {
  const active = a.session_id && a.session_id === state.active_session_id;
  const cls = ['cell'];
  if (active) cls.push('active');
  if (a.status === 'stopped') cls.push('stopped');

  let h = `<div class="${cls.join(' ')}" onclick="focusAgent('${a.id}')" title="${esc(a.name)} (${a.status})">`;
  h += `<div class="cell-status ${a.status}"></div>`;
  h += `<button class="cell-close" onclick="event.stopPropagation();removeAgent('${a.id}')" title="Remove">\u2715</button>`;
  h += `<div class="cell-icon">${agentIcon(a.name)}</div>`;
  h += `<div class="cell-name">${esc(a.name)}</div>`;
  if (a.status === 'stopped') {
    h += `<button class="cell-relaunch" onclick="event.stopPropagation();relaunchAgent('${a.id}')" title="Relaunch">\u21BB relaunch</button>`;
  }
  h += `</div>`;
  return h;
}

function renderTerminalRow(t) {
  const active = t.session_id && t.session_id === state.active_session_id;
  const cls = ['term-row'];
  if (active) cls.push('active');
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

  let h = `<div class="${cls.join(' ')}" onclick="focusAgent('${t.id}')">`;
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

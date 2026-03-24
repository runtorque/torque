/* Modals — add group, add agent/terminal, confirm dialog, color picker */

/* -- Hint popover (for ? buttons) ---------------------------------------- */
function toggleHint(btn) {
  const existing = document.querySelector('.hint-pop');
  if (existing) { existing.remove(); if (existing._src === btn) return; }
  const pop = document.createElement('div');
  pop.className = 'hint-pop';
  pop.textContent = btn.dataset.hint;
  pop._src = btn;
  document.body.appendChild(pop);
  const r = btn.getBoundingClientRect();
  pop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - pop.offsetWidth - 4)) + 'px';
  pop.style.top = (r.top - pop.offsetHeight - 6) + 'px';
  setTimeout(() => {
    function dismiss(e) {
      if (e.target === btn) return;
      pop.remove();
      document.removeEventListener('click', dismiss, true);
    }
    document.addEventListener('click', dismiss, true);
  }, 0);
}

let addCellMode = 'agent';
let _confirmResolve = null;
let _pendingModal = null;
let _selectedColor = '';
let _pendingParentId = '';

function closeModals() {
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('visible'));
  document.querySelectorAll('.hint-pop').forEach(p => p.remove());
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
}

/* -- Confirm dialog (replaces window.confirm for WKWebView) ----------- */
function showConfirm(message) {
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('modal-confirm').classList.add('visible');
  });
}
function confirmYes() {
  document.getElementById('modal-confirm').classList.remove('visible');
  if (_confirmResolve) { _confirmResolve(true); _confirmResolve = null; }
}
function confirmNo() {
  document.getElementById('modal-confirm').classList.remove('visible');
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
}

/* -- Add Group -------------------------------------------------------- */
function openAddGroup() {
  document.getElementById('modal-group').classList.add('visible');
  const inp = document.getElementById('group-name-input');
  inp.value = '';
  inp.focus();
}
function submitGroup() {
  const name = document.getElementById('group-name-input').value.trim();
  if (name) { send({ cmd: 'add_group', group: name }); closeModals(); }
}

/* -- Add Agent / Terminal (shared modal) ------------------------------ */
function _openAddModal(mode, group, parentId) {
  _pendingModal = { mode, group, parentId: parentId || '' };
  send({ cmd: 'get_config', group });
}

function _showAddModal(mode, group, config) {
  addCellMode = mode;
  _selectedColor = '';
  _pendingParentId = (_pendingModal && _pendingModal.parentId) || '';

  const parent = _pendingParentId ? state.agents[_pendingParentId] : null;
  document.getElementById('modal-add-title').textContent =
    parent ? `New Terminal for ${parent.name}` :
    mode === 'agent' ? 'New Agent' : 'New Terminal';

  const isTerminal = mode === 'terminal';
  const cmdRow = document.getElementById('add-cmd-row');
  const argsRow = document.getElementById('add-args-row');
  const initRow = document.getElementById('add-init-row');
  if (isTerminal) {
    cmdRow.classList.remove('hidden');
    argsRow.classList.remove('hidden');
    initRow.classList.remove('hidden');
  } else {
    cmdRow.classList.add('hidden');
    argsRow.classList.add('hidden');
    initRow.classList.add('hidden');
  }

  /* group dropdown */
  const gsel = document.getElementById('add-group-select');
  gsel.innerHTML = '';
  for (const g of Object.keys(state.groups)) {
    const opt = document.createElement('option');
    opt.value = g; opt.textContent = g;
    if (g === group) opt.selected = true;
    gsel.appendChild(opt);
  }

  /* directory dropdown */
  const dsel = document.getElementById('add-dir-select');
  dsel.innerHTML = '';
  const optCur = document.createElement('option');
  optCur.value = config.current_path || '';
  optCur.textContent = 'Current session';
  dsel.appendChild(optCur);
  for (const c of (config.group_cells || [])) {
    const opt = document.createElement('option');
    opt.value = c.current_path;
    opt.textContent = 'Same as ' + c.name;
    dsel.appendChild(opt);
  }
  const optCustom = document.createElement('option');
  optCustom.value = '__custom__';
  optCustom.textContent = 'Custom\u2026';
  dsel.appendChild(optCustom);
  document.getElementById('add-dir-input').value = config.current_path || '';
  document.getElementById('add-dir-input').classList.add('hidden');

  /* profile dropdown */
  const psel = document.getElementById('add-profile-select');
  psel.innerHTML = '';
  for (const name of (config.profiles || ['Default'])) {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    if (name === config.current_profile) opt.selected = true;
    psel.appendChild(opt);
  }

  /* color swatches */
  const sw = document.getElementById('add-color-swatches');
  let sh = '';
  for (const c of TAB_COLORS) {
    sh += `<button class="swatch" data-color="${c.hex}" style="background:${c.hex}"
            onclick="selectColor('${c.hex}')" title="${c.name}"></button>`;
  }
  sh += `<button class="swatch swatch-none" data-color="" onclick="selectColor('')" title="None">\u2715</button>`;
  sw.innerHTML = sh;

  /* pre-fill from group settings */
  const gs = config.group_settings || {};
  const isAgent = mode === 'agent';
  const prefix = isAgent ? '' : gs.terminal_name_prefix;
  const nameInput = document.getElementById('add-name-input');
  nameInput.value = prefix ? _nextName(prefix) : '';
  nameInput.placeholder = isTerminal ? 'e.g. Shell' : 'e.g. Claude 1';

  if (isTerminal) {
    document.getElementById('add-cmd-input').value = gs.terminal_boot_command || '';
    document.getElementById('add-args-input').value = gs.terminal_command_args || '';
    document.getElementById('add-init-input').value = gs.terminal_init_script || '';
  }

  const dir = (isAgent ? gs.agent_directory : gs.terminal_directory) || gs.default_directory;
  if (dir) {
    const optGrp = document.createElement('option');
    optGrp.value = dir;
    optGrp.textContent = 'Group default';
    dsel.insertBefore(optGrp, dsel.firstChild);
    optGrp.selected = true;
  }

  const prof = (isAgent ? gs.agent_profile : gs.terminal_profile) || gs.profile;
  if (prof) {
    for (const opt of psel.options) {
      if (opt.value === prof) { opt.selected = true; break; }
    }
  }

  const shell = (isAgent ? gs.agent_shell : gs.terminal_shell) || gs.shell;
  document.getElementById('add-shell-select').value = shell || '';

  const color = (isAgent ? gs.agent_tab_color : gs.terminal_tab_color) || gs.tab_color;
  if (color && color !== 'none') selectColor(color);

  const envObj = isAgent ? gs.agent_env_vars : gs.terminal_env_vars;
  document.getElementById('add-env-vars').value = _envToText(envObj);

  document.getElementById('modal-add').classList.add('visible');
  document.getElementById('add-name-input').focus();
}

function onDirChange() {
  const sel = document.getElementById('add-dir-select');
  const inp = document.getElementById('add-dir-input');
  if (sel.value === '__custom__') {
    inp.classList.remove('hidden');
    inp.focus();
  } else {
    inp.classList.add('hidden');
  }
}

function selectColor(hex) {
  _selectedColor = hex;
  document.querySelectorAll('#add-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}

/* -- Edit Agent / Terminal --------------------------------------------- */
let _editCellId = null;
let _editColor = '';

function openEditCell(id) {
  const cell = state.agents[id];
  if (!cell) return;
  _editCellId = id;
  _editColor = cell.tab_color || '';

  document.getElementById('edit-title').textContent =
    cell.cell_type === 'terminal' ? 'Edit Terminal' : 'Edit Agent';
  document.getElementById('edit-name-input').value = cell.name;

  /* color swatches */
  const sw = document.getElementById('edit-color-swatches');
  let sh = '';
  for (const c of TAB_COLORS) {
    const sel = c.hex === _editColor ? ' selected' : '';
    sh += `<button class="swatch${sel}" data-color="${c.hex}" style="background:${c.hex}"
            onclick="selectEditColor('${c.hex}')" title="${c.name}"></button>`;
  }
  const noneSel = !_editColor ? ' selected' : '';
  sh += `<button class="swatch swatch-none${noneSel}" data-color="" onclick="selectEditColor('')" title="None">\u2715</button>`;
  sw.innerHTML = sh;

  document.getElementById('modal-edit').classList.add('visible');
  document.getElementById('edit-name-input').focus();
  document.getElementById('edit-name-input').select();
}

function selectEditColor(hex) {
  _editColor = hex;
  document.querySelectorAll('#edit-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}

function submitEdit() {
  if (!_editCellId) return;
  const name = document.getElementById('edit-name-input').value.trim();
  if (!name) return;
  send({ cmd: 'update_agent', id: _editCellId, name, tab_color: _editColor });
  _editCellId = null;
  closeModals();
}

/* -- Group Settings ---------------------------------------------------- */
let _settingsGroup = null;
let _gsColor = '';
let _gsAgentColor = '';
let _gsTerminalColor = '';

function switchGsTab(name) {
  document.querySelectorAll('.gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
}

function openGroupSettings(group) {
  _settingsGroup = group;
  send({ cmd: 'get_group_settings', group });
}

function _populateProfileSelect(el, profiles, selected, emptyLabel) {
  el.innerHTML = `<option value="">${emptyLabel}</option>`;
  for (const name of (profiles || [])) {
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    if (name === selected) opt.selected = true;
    el.appendChild(opt);
  }
}

function _renderSwatches(containerId, activeColor, onClick, showInherit) {
  const sw = document.getElementById(containerId);
  let sh = '';
  if (showInherit) {
    const iSel = activeColor === '' ? ' selected' : '';
    sh += `<button class="swatch swatch-inherit${iSel}" data-color="" onclick="${onClick}('')" title="Same as group">\u2191</button>`;
  }
  for (const c of TAB_COLORS) {
    const sel = c.hex === activeColor ? ' selected' : '';
    sh += `<button class="swatch${sel}" data-color="${c.hex}" style="background:${c.hex}"
            onclick="${onClick}('${c.hex}')" title="${c.name}"></button>`;
  }
  const noneVal = showInherit ? 'none' : '';
  const noneSel = activeColor === noneVal ? ' selected' : '';
  sh += `<button class="swatch swatch-none${noneSel}" data-color="${noneVal}" onclick="${onClick}('${noneVal}')" title="None">\u2715</button>`;
  sw.innerHTML = sh;
}

function _envToText(obj) {
  return Object.entries(obj || {}).map(([k, v]) => k + '=' + v).join('\n');
}

function _textToEnv(id) {
  const text = document.getElementById(id).value.trim();
  const env = {};
  if (text) {
    for (const line of text.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    }
  }
  return env;
}

function _showGroupSettings(group, data) {
  _settingsGroup = group;
  const s = data.settings;

  document.getElementById('gs-title').textContent = group + ' Settings';

  /* -- Group tab -- */
  document.getElementById('gs-directory').value = s.default_directory || '';
  document.getElementById('gs-shell').value = s.shell || '';
  document.getElementById('gs-max-agents').value = s.max_agents || 0;
  document.getElementById('gs-auto-terminals').value = s.auto_terminals || 0;
  document.getElementById('gs-collapsed').checked = s.collapsed_default || false;
  document.getElementById('gs-filter-window').checked = s.filter_by_window || false;
  document.getElementById('gs-env-vars').value = _envToText(s.env_vars);
  _populateProfileSelect(document.getElementById('gs-profile'), data.profiles, s.profile, 'System default');
  _gsColor = s.tab_color || '';
  _renderSwatches('gs-color-swatches', _gsColor, 'selectGsColor');

  /* -- Agents tab -- */
  document.getElementById('gs-agent-directory').value = s.agent_directory || '';
  document.getElementById('gs-agent-shell').value = s.agent_shell || '';
  document.getElementById('gs-agent-boot-cmd').value = s.agent_boot_command || '';
  document.getElementById('gs-worktree').checked = s.git_worktree || false;
  document.getElementById('gs-wt-base-dir').value = s.worktree_base_dir || '.loom/worktrees';
  document.getElementById('gs-wt-base-branch').value = s.worktree_base_branch || '';
  document.getElementById('gs-wt-auto-checkpoint').checked = s.worktree_auto_checkpoint || false;
  document.getElementById('gs-wt-merge-prompt').value = s.worktree_merge_prompt || '';
  _toggleWorktreeFields();
  document.getElementById('gs-session-resume').checked = s.agent_session_resume !== false;
  document.getElementById('gs-agent-idle-timeout').value = s.agent_idle_timeout != null ? s.agent_idle_timeout : 5;
  document.getElementById('gs-agent-always-custom').checked = s.agent_always_custom_dialog || false;
  document.getElementById('gs-notifications').checked = s.notifications || false;
  document.getElementById('gs-notify-finish').checked = s.notify_on_finish !== false;
  document.getElementById('gs-notify-error').checked = s.notify_on_error !== false;
  document.getElementById('gs-notify-attention').checked = s.notify_on_attention !== false;
  document.getElementById('gs-agent-env-vars').value = _envToText(s.agent_env_vars);
  _populateProfileSelect(document.getElementById('gs-agent-profile'), data.profiles, s.agent_profile, 'Same as group');
  _gsAgentColor = s.agent_tab_color || '';
  _renderSwatches('gs-agent-color-swatches', _gsAgentColor, 'selectGsAgentColor', true);

  /* -- Terminals tab -- */
  document.getElementById('gs-terminal-prefix').value = s.terminal_name_prefix || '';
  document.getElementById('gs-terminal-boot-cmd').value = s.terminal_boot_command || '';
  document.getElementById('gs-terminal-cmd-args').value = s.terminal_command_args || '';
  document.getElementById('gs-terminal-init-script').value = s.terminal_init_script || '';
  document.getElementById('gs-terminal-directory').value = s.terminal_directory || '';
  document.getElementById('gs-terminal-shell').value = s.terminal_shell || '';
  document.getElementById('gs-terminal-always-custom').checked = s.terminal_always_custom_dialog || false;
  document.getElementById('gs-terminal-env-vars').value = _envToText(s.terminal_env_vars);
  _populateProfileSelect(document.getElementById('gs-terminal-profile'), data.profiles, s.terminal_profile, 'Same as group');
  _gsTerminalColor = s.terminal_tab_color || '';
  _renderSwatches('gs-terminal-color-swatches', _gsTerminalColor, 'selectGsTerminalColor', true);

  switchGsTab('group');
  document.getElementById('modal-group-settings').classList.add('visible');
  document.getElementById('gs-directory').focus();
}

function selectGsColor(hex) {
  _gsColor = hex;
  document.querySelectorAll('#gs-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}
function selectGsAgentColor(hex) {
  _gsAgentColor = hex;
  document.querySelectorAll('#gs-agent-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}
function selectGsTerminalColor(hex) {
  _gsTerminalColor = hex;
  document.querySelectorAll('#gs-terminal-color-swatches .swatch').forEach(s => {
    s.classList.toggle('selected', (s.dataset.color || '') === hex);
  });
}

function submitGroupSettings() {
  if (!_settingsGroup) return;

  const settings = {
    /* Group */
    default_directory: document.getElementById('gs-directory').value.trim(),
    profile: document.getElementById('gs-profile').value,
    shell: document.getElementById('gs-shell').value,
    tab_color: _gsColor,
    env_vars: _textToEnv('gs-env-vars'),
    auto_terminals: parseInt(document.getElementById('gs-auto-terminals').value) || 0,
    max_agents: parseInt(document.getElementById('gs-max-agents').value) || 0,
    collapsed_default: document.getElementById('gs-collapsed').checked,
    filter_by_window: document.getElementById('gs-filter-window').checked,
    /* Agents */
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_profile: document.getElementById('gs-agent-profile').value,
    agent_shell: document.getElementById('gs-agent-shell').value,
    agent_tab_color: _gsAgentColor,
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_env_vars: _textToEnv('gs-agent-env-vars'),
    git_worktree: document.getElementById('gs-worktree').checked,
    worktree_base_dir: document.getElementById('gs-wt-base-dir').value.trim() || '.loom/worktrees',
    worktree_base_branch: document.getElementById('gs-wt-base-branch').value.trim(),
    worktree_auto_checkpoint: document.getElementById('gs-wt-auto-checkpoint').checked,
    worktree_merge_prompt: document.getElementById('gs-wt-merge-prompt').value.trim(),
    agent_session_resume: document.getElementById('gs-session-resume').checked,
    agent_idle_timeout: parseInt(document.getElementById('gs-agent-idle-timeout').value) || 0,
    agent_always_custom_dialog: document.getElementById('gs-agent-always-custom').checked,
    notifications: document.getElementById('gs-notifications').checked,
    notify_on_finish: document.getElementById('gs-notify-finish').checked,
    notify_on_error: document.getElementById('gs-notify-error').checked,
    notify_on_attention: document.getElementById('gs-notify-attention').checked,
    /* Terminals */
    terminal_name_prefix: document.getElementById('gs-terminal-prefix').value.trim(),
    terminal_boot_command: document.getElementById('gs-terminal-boot-cmd').value.trim(),
    terminal_command_args: document.getElementById('gs-terminal-cmd-args').value.trim(),
    terminal_init_script: document.getElementById('gs-terminal-init-script').value.trim(),
    terminal_directory: document.getElementById('gs-terminal-directory').value.trim(),
    terminal_profile: document.getElementById('gs-terminal-profile').value,
    terminal_shell: document.getElementById('gs-terminal-shell').value,
    terminal_tab_color: _gsTerminalColor,
    terminal_env_vars: _textToEnv('gs-terminal-env-vars'),
    terminal_always_custom_dialog: document.getElementById('gs-terminal-always-custom').checked,
  };

  send({ cmd: 'update_group_settings', group: _settingsGroup, settings });
  _settingsGroup = null;
  closeModals();
}

function _toggleWorktreeFields() {
  const on = document.getElementById('gs-worktree').checked;
  document.getElementById('gs-wt-fields').style.display = on ? 'block' : 'none';
}

function openAddAgent(group)              { _openAddModal('agent', group); }
function openAddTerminal(group, parentId) { _openAddModal('terminal', group, parentId); }

function submitAdd() {
  const name    = document.getElementById('add-name-input').value.trim();
  const group   = document.getElementById('add-group-select').value;
  const command = document.getElementById('add-cmd-input').value.trim();
  const profile = document.getElementById('add-profile-select').value;

  const dirSel  = document.getElementById('add-dir-select');
  const directory = dirSel.value === '__custom__'
    ? document.getElementById('add-dir-input').value.trim()
    : dirSel.value;

  if (!name || !group) return;

  const shell = document.getElementById('add-shell-select').value;
  const envVars = _textToEnv('add-env-vars');

  const msg = {
    cmd: addCellMode === 'agent' ? 'add_agent' : 'add_terminal',
    name, group, profile,
  };
  if (addCellMode === 'terminal' && _pendingParentId) msg.parent_id = _pendingParentId;
  if (directory) msg.directory = directory;
  if (_selectedColor) msg.tab_color = _selectedColor;
  if (shell) msg.shell = shell;
  if (Object.keys(envVars).length > 0) msg.env_vars = envVars;
  if (addCellMode === 'terminal') {
    if (command) msg.command = command;
    const args = document.getElementById('add-args-input').value.trim();
    const init = document.getElementById('add-init-input').value.trim();
    if (args) msg.command_args = args;
    if (init) msg.init_script = init;
  }

  send(msg);
  closeModals();
}

/* -- Worktree History ----------------------------------------------------- */
let _histCellId = null;

function _showWorktreeHistory(data) {
  _histCellId = data.id;
  const cell = state.agents[data.id];
  const name = cell ? cell.name : data.id;
  const branch = (data.branch || '').replace(/^loom\//, '');

  document.getElementById('hist-title').textContent = name + ' History';
  document.getElementById('hist-branch').textContent = branch ? '\u2387 ' + branch : '';

  const list = document.getElementById('hist-list');
  if (!data.commits || data.commits.length === 0) {
    list.innerHTML = '<div class="hist-empty">No commits on this branch yet.</div>';
  } else {
    let html = '';
    for (let i = 0; i < data.commits.length; i++) {
      const c = data.commits[i];
      const isCurrent = i === 0;
      const dateStr = _formatHistDate(c.date);
      const hasBody = !!(c.body && c.body.trim());
      const clickable = hasBody ? ' hist-clickable' : '';
      const toggle = hasBody ? ` onclick="_toggleHistBody(this)"` : '';
      html += `<div class="hist-row${isCurrent ? ' hist-current' : ''}${clickable}"${toggle}>`;
      html += `  <div class="hist-dot"></div>`;
      html += `  <div class="hist-info">`;
      html += `    <div class="hist-msg">${esc(c.message)}</div>`;
      let statStr = '';
      if (c.insertions || c.deletions) statStr = ` \u00b7 +${c.insertions || 0} -${c.deletions || 0}`;
      html += `    <div class="hist-meta">${esc(c.short_sha)} \u00b7 ${esc(dateStr)}${statStr}</div>`;
      if (hasBody) {
        html += `    <div class="hist-body">${esc(c.body)}</div>`;
      }
      html += `  </div>`;
      if (!isCurrent) {
        html += `  <button class="hist-rollback" onclick="event.stopPropagation();_doRollback('${esc(c.sha)}')" title="Roll back to this commit">\u21BA</button>`;
      } else {
        html += `  <span class="hist-tag">HEAD</span>`;
      }
      html += `</div>`;
    }
    list.innerHTML = html;
  }

  document.getElementById('modal-history').classList.add('visible');
}

function _formatHistDate(iso) {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch (_) { return iso; }
}

function _toggleHistBody(row) {
  row.classList.toggle('hist-expanded');
}

async function _doRollback(sha) {
  if (!_histCellId) return;
  const cell = state.agents[_histCellId];
  const name = cell ? cell.name : _histCellId;
  if (await showConfirm(`Roll back "${name}" to ${sha.slice(0, 7)}? Changes after this commit will be lost.`)) {
    send({ cmd: 'worktree_rollback', id: _histCellId, sha });
    closeModals();
  }
}

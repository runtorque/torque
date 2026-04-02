/* Modals — add group, add agent/terminal, confirm dialog, color picker */

/* -- Provider cache (populated from get_config response) ------------------ */
let _cachedProviders = [];  // [{name, display_name, command}, ...]

function _populateProviderSelect(selectId, currentValue, includeGroupDefault) {
  const sel = document.getElementById(selectId);
  sel.innerHTML = '';
  if (includeGroupDefault) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = 'Group default';
    sel.appendChild(opt);
  } else {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = 'Default (Claude Code)';
    sel.appendChild(opt);
  }
  for (const p of _cachedProviders) {
    const opt = document.createElement('option');
    opt.value = p.name; opt.textContent = p.display_name;
    sel.appendChild(opt);
  }
  const cust = document.createElement('option');
  cust.value = '__custom__'; cust.textContent = 'Custom\u2026';
  sel.appendChild(cust);
  sel.value = currentValue || '';
}

function _getProviderValue(selectId) {
  const v = document.getElementById(selectId).value;
  return v === '__custom__' ? '' : v;
}

function _getProviderCommand(selectId) {
  const v = document.getElementById(selectId).value;
  if (!v) return 'claude';  // default provider
  const p = _cachedProviders.find(p => p.name === v);
  return p ? p.command : '';
}

function _populateTemplateSelect(selectId, currentValue, emptyLabel) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  sel.innerHTML = '';
  const opt = document.createElement('option');
  opt.value = '';
  opt.textContent = emptyLabel || 'None';
  sel.appendChild(opt);
  const templates = (_cachedAgentTemplates || []).filter(t => !t.shadowed);
  const project = templates.filter(t => !t.global);
  const user = templates.filter(t => t.global);
  function appendGroup(label, items) {
    if (!items.length) return;
    const group = document.createElement('optgroup');
    group.label = label;
    for (const t of items) {
      const o = document.createElement('option');
      o.value = t.name;
      o.textContent = t.display_name || t.name;
      group.appendChild(o);
    }
    sel.appendChild(group);
  }
  appendGroup('Project', project);
  appendGroup('User', user);
  sel.value = currentValue || '';
}

function _findTemplateMeta(name) {
  return (_cachedAgentTemplates || []).find(t => t.name === name) || null;
}

function onGsProviderChange() {
  const v = document.getElementById('gs-agent-provider').value;
  const row = document.getElementById('gs-agent-boot-cmd-row');
  const label = row.querySelector('label');
  const input = document.getElementById('gs-agent-boot-cmd');
  row.classList.remove('hidden');
  if (v === '__custom__') {
    label.textContent = 'Boot command';
    input.placeholder = 'e.g. my-agent-cli';
  } else {
    label.textContent = 'Command override';
    input.placeholder = _getProviderCommand('gs-agent-provider') + ' (default)';
  }
}

function onAddProviderChange() {
  const v = document.getElementById('add-provider-select').value;
  const cmdRow = document.getElementById('add-cmd-row');
  const label = cmdRow.querySelector('label');
  const input = document.getElementById('add-cmd-input');
  cmdRow.classList.remove('hidden');
  if (v === '__custom__') {
    label.textContent = 'Boot command';
    input.placeholder = 'e.g. npm run dev';
  } else {
    label.textContent = 'Command override';
    input.placeholder = _getProviderCommand('add-provider-select') + ' (default)';
  }
}

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
let _selectedIcon = '';
let _pendingParentId = '';
let _addModalConfig = null;
let _addTemplateApplied = '';

function _renderIconPicker(containerId, selectedIcon, onClickFn) {
  let html = `<button class="icon-btn${!selectedIcon ? ' selected' : ''}" data-icon="" onclick="${onClickFn}('')" title="Auto">auto</button>`;
  for (const icon of AGENT_ICONS) {
    const sel = icon === selectedIcon ? ' selected' : '';
    html += `<button class="icon-btn${sel}" data-icon="${icon}" onclick="${onClickFn}('${icon}')" title="${icon}">${icon}</button>`;
  }
  document.getElementById(containerId).innerHTML = html;
}

function selectIcon(icon) {
  _selectedIcon = icon;
  document.querySelectorAll('#add-icon-picker .icon-btn').forEach(b => {
    b.classList.toggle('selected', (b.dataset.icon || '') === icon);
  });
}

function closeModals() {
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('visible'));
  document.querySelectorAll('.hint-pop').forEach(p => p.remove());
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  if (typeof _glsCapturing !== 'undefined' && _glsCapturing) _cancelCapture();
}

/* -- Confirm dialog (replaces window.confirm for WKWebView) ----------- */
function showConfirm(message, opts) {
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    document.getElementById('confirm-message').textContent = message;
    const extras = document.getElementById('confirm-extras');
    extras.innerHTML = '';
    if (opts && opts.checkboxes) {
      for (const cb of opts.checkboxes) {
        const lbl = document.createElement('label');
        lbl.className = 'gs-checkbox';
        const inp = document.createElement('input');
        inp.type = 'checkbox';
        inp.checked = !!cb.checked;
        inp.dataset.key = cb.key;
        lbl.appendChild(inp);
        lbl.appendChild(document.createTextNode(cb.label));
        extras.appendChild(lbl);
      }
    }
    const btn = document.getElementById('confirm-yes-btn');
    btn.textContent = (opts && opts.label) || 'Remove';
    btn.className = 'btn-primary ' + ((opts && opts.variant) || 'btn-danger');
    document.getElementById('modal-confirm').classList.add('visible');
  });
}
function _confirmResult(accepted) {
  document.getElementById('modal-confirm').classList.remove('visible');
  if (!_confirmResolve) return;
  if (!accepted) { _confirmResolve(false); _confirmResolve = null; return; }
  const extras = document.getElementById('confirm-extras');
  const boxes = extras.querySelectorAll('input[type="checkbox"]');
  if (boxes.length === 0) { _confirmResolve(true); _confirmResolve = null; return; }
  const result = {};
  for (const b of boxes) result[b.dataset.key] = b.checked;
  _confirmResolve(result);
  _confirmResolve = null;
}
function confirmYes() { _confirmResult(true); }
function confirmNo() { _confirmResult(false); }

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
function _openAddModal(mode, group, parentId, templateName) {
  _pendingModal = {
    mode,
    group,
    parentId: parentId || '',
    template: templateName || '',
  };
  send({ cmd: 'get_config', group });
}

function _showAddModal(mode, group, config) {
  addCellMode = mode;
  _addModalConfig = config;
  _selectedColor = '';
  _selectedIcon = '';
  _addTemplateApplied = '';
  _pendingParentId = (_pendingModal && _pendingModal.parentId) || '';

  const parent = _pendingParentId ? state.agents[_pendingParentId] : null;
  document.getElementById('modal-add-title').textContent =
    parent ? `New Terminal for ${parent.name}` :
    mode === 'agent' ? 'New Agent' : 'New Terminal';

  const isTerminal = mode === 'terminal';
  const cmdRow = document.getElementById('add-cmd-row');
  const argsRow = document.getElementById('add-args-row');
  const initRow = document.getElementById('add-init-row');
  const iconRow = document.getElementById('add-icon-row');
  const providerRow = document.getElementById('add-provider-row');
  const templateRow = document.getElementById('add-template-row');
  if (isTerminal) {
    cmdRow.classList.remove('hidden');
    argsRow.classList.remove('hidden');
    initRow.classList.remove('hidden');
    iconRow.classList.add('hidden');
    providerRow.classList.add('hidden');
    templateRow.classList.add('hidden');
  } else {
    cmdRow.classList.add('hidden');
    argsRow.classList.add('hidden');
    initRow.classList.add('hidden');
    iconRow.classList.remove('hidden');
    providerRow.classList.remove('hidden');
    templateRow.classList.remove('hidden');
    _renderIconPicker('add-icon-picker', '', 'selectIcon');
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
  const resolved = config.resolved_agent_defaults || {};
  const isAgent = mode === 'agent';
  const prefix = isAgent ? '' : gs.terminal_name_prefix;
  const nameInput = document.getElementById('add-name-input');
  nameInput.value = prefix ? _nextName(prefix) : '';
  nameInput.placeholder = isTerminal ? 'e.g. Shell' : 'e.g. Claude 1';

  if (isTerminal) {
    document.getElementById('add-cmd-input').value = gs.terminal_boot_command || '';
    document.getElementById('add-args-input').value = gs.terminal_command_args || '';
    document.getElementById('add-init-input').value = gs.terminal_init_script || '';
  } else {
    _populateTemplateSelect('add-template-select',
      (_pendingModal && _pendingModal.template) || '', 'Group default');
    _populateProviderSelect('add-provider-select', resolved.provider || gs.agent_provider || '', true);
    document.getElementById('add-cmd-input').value = resolved.command || '';
    onAddProviderChange();
  }

  const dir = isAgent
    ? (resolved.directory || gs.agent_directory || gs.default_directory)
    : ((isAgent ? gs.agent_directory : gs.terminal_directory) || gs.default_directory);
  if (dir) {
    const optGrp = document.createElement('option');
    optGrp.value = dir;
    optGrp.textContent = 'Group default';
    dsel.insertBefore(optGrp, dsel.firstChild);
    optGrp.selected = true;
  }

  const prof = isAgent
    ? (resolved.profile || gs.agent_profile || gs.profile)
    : ((isAgent ? gs.agent_profile : gs.terminal_profile) || gs.profile);
  if (prof) {
    for (const opt of psel.options) {
      if (opt.value === prof) { opt.selected = true; break; }
    }
  }

  const shell = isAgent
    ? (resolved.shell || gs.agent_shell || gs.shell)
    : ((isAgent ? gs.agent_shell : gs.terminal_shell) || gs.shell);
  document.getElementById('add-shell-select').value = shell || '';

  const color = isAgent
    ? (resolved.tab_color || gs.agent_tab_color || gs.tab_color)
    : ((isAgent ? gs.agent_tab_color : gs.terminal_tab_color) || gs.tab_color);
  if (color && color !== 'none') selectColor(color);

  const envObj = isAgent
    ? (resolved.env_vars || gs.agent_env_vars)
    : gs.terminal_env_vars;
  document.getElementById('add-env-vars').value = _envToText(envObj);

  /* worktree section — agents only */
  const wtSection = document.getElementById('add-wt-section');
  if (isTerminal) {
    wtSection.classList.add('hidden');
  } else {
    wtSection.classList.remove('hidden');
    document.getElementById('add-wt-enabled').checked = !!resolved.worktree;
    document.getElementById('add-wt-base-dir').value = resolved.worktree_base_dir || gs.worktree_base_dir || '';
    document.getElementById('add-wt-base-branch').value = resolved.worktree_base_branch || gs.worktree_base_branch || '';
    document.getElementById('add-wt-auto-checkpoint').checked = resolved.worktree_auto_checkpoint || false;
    document.getElementById('add-wt-squash').checked = resolved.worktree_merge_squash !== false;
    if (resolved.icon) selectIcon(resolved.icon);
    _toggleAddWorktreeFields();
  }

  document.getElementById('modal-add').classList.add('visible');
  if (!isTerminal && _pendingModal && _pendingModal.template) {
    onAddTemplateChange();
  }
  document.getElementById('add-name-input').focus();
}

function _applyRenderedAddTemplate(config, templateName) {
  if (!config || addCellMode !== 'agent') return;
  _addTemplateApplied = templateName || '';
  document.getElementById('add-provider-select').value = config.provider || '';
  document.getElementById('add-cmd-input').value = config.command || '';
  document.getElementById('add-shell-select').value = config.shell || '';
  document.getElementById('add-env-vars').value = _envToText(config.env_vars || {});
  document.getElementById('add-wt-enabled').checked = !!config.worktree;
  document.getElementById('add-wt-base-dir').value = config.worktree_base_dir || '';
  document.getElementById('add-wt-base-branch').value = config.worktree_base_branch || '';
  document.getElementById('add-wt-auto-checkpoint').checked = !!config.worktree_auto_checkpoint;
  document.getElementById('add-wt-squash').checked = config.worktree_merge_squash !== false;
  if (config.profile) document.getElementById('add-profile-select').value = config.profile;
  if (config.tab_color) selectColor(config.tab_color);
  else selectColor('');
  if (config.icon) selectIcon(config.icon);
  else selectIcon('');
  const dirSel = document.getElementById('add-dir-select');
  const dirInput = document.getElementById('add-dir-input');
  if (config.directory) {
    let matched = false;
    for (const opt of dirSel.options) {
      if (opt.value === config.directory) {
        dirSel.value = config.directory;
        matched = true;
        break;
      }
    }
    if (!matched) {
      dirSel.value = '__custom__';
      dirInput.value = config.directory;
      dirInput.classList.remove('hidden');
    }
  }
  const nameInput = document.getElementById('add-name-input');
  const meta = _findTemplateMeta(templateName);
  if (nameInput && !nameInput.value.trim() && meta) {
    nameInput.value = meta.display_name || meta.name.split('/').pop();
  }
  onAddProviderChange();
  _toggleAddWorktreeFields();
}

function onAddTemplateChange() {
  if (addCellMode !== 'agent') return;
  const sel = document.getElementById('add-template-select');
  const name = sel ? sel.value : '';
  if (!name) {
    if (_pendingModal) _pendingModal.template = '';
    if (_addModalConfig) _showAddModal(addCellMode, document.getElementById('add-group-select').value, _addModalConfig);
    return;
  }
  if (_pendingModal) _pendingModal.template = name;
  send({
    cmd: 'render_template',
    group: document.getElementById('add-group-select').value,
    name: name,
  });
}

function _handleRenderedTemplate(msg) {
  const modal = document.getElementById('modal-add');
  if (!modal || !modal.classList.contains('visible')) return;
  const sel = document.getElementById('add-template-select');
  if (!sel || sel.value !== (msg.name || '')) return;
  _applyRenderedAddTemplate(msg.config || {}, msg.name || '');
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
let _editIcon = '';

function selectEditIcon(icon) {
  _editIcon = icon;
  document.querySelectorAll('#edit-icon-picker .icon-btn').forEach(b => {
    b.classList.toggle('selected', (b.dataset.icon || '') === icon);
  });
}

function openEditCell(id) {
  const cell = state.agents[id];
  if (!cell) return;
  _editCellId = id;
  _editColor = cell.tab_color || '';
  _editIcon = cell.icon || '';

  document.getElementById('edit-title').textContent =
    cell.cell_type === 'terminal' ? 'Edit Terminal' : 'Edit Agent';
  document.getElementById('edit-name-input').value = cell.name;

  /* icon picker (agents only) */
  const iconRow = document.getElementById('edit-icon-row');
  if (cell.cell_type === 'agent') {
    iconRow.classList.remove('hidden');
    _renderIconPicker('edit-icon-picker', _editIcon, 'selectEditIcon');
  } else {
    iconRow.classList.add('hidden');
  }

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
  send({ cmd: 'update_agent', id: _editCellId, name, tab_color: _editColor, icon: _editIcon });
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
  // Reset sub-tabs to first when switching main tabs
  const pane = document.querySelector(`.gs-pane[data-pane="${name}"]`);
  if (pane) {
    const firstSub = pane.querySelector('.gs-subtab');
    if (firstSub) switchGsSubTab(name, firstSub);
  }
}

function switchGsSubTab(pane, btn) {
  const container = btn.closest('.gs-pane');
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  const target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
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
  document.getElementById('gs-env-file').value = s.env_file || '';
  _populateProfileSelect(document.getElementById('gs-profile'), data.profiles, s.profile, 'System default');
  _gsColor = s.tab_color || '';
  _renderSwatches('gs-color-swatches', _gsColor, 'selectGsColor');

  /* -- Agents tab -- */
  document.getElementById('gs-agent-directory').value = s.agent_directory || '';
  document.getElementById('gs-agent-shell').value = s.agent_shell || '';
  _populateProviderSelect('gs-agent-provider', s.agent_provider || '', false);
  _populateTemplateSelect('gs-default-agent-template', s.default_agent_template || '', 'None');
  document.getElementById('gs-agent-boot-cmd').value = s.agent_boot_command || '';
  onGsProviderChange();
  document.getElementById('gs-worktree').checked = s.git_worktree || false;
  document.getElementById('gs-wt-base-dir').value = s.worktree_base_dir || '.loom/worktrees';
  document.getElementById('gs-wt-base-branch').value = s.worktree_base_branch || '';
  document.getElementById('gs-wt-auto-checkpoint').checked = s.worktree_auto_checkpoint || false;
  document.getElementById('gs-wt-merge-squash').checked = s.worktree_merge_squash !== false;
  document.getElementById('gs-wt-merge-instructions').value = s.worktree_merge_instructions || '';
  _toggleWorktreeFields();
  document.getElementById('gs-session-resume').checked = s.agent_session_resume !== false;
  document.getElementById('gs-agent-idle-timeout').value = s.agent_idle_timeout != null ? s.agent_idle_timeout : 5;
  document.getElementById('gs-agent-always-custom').checked = s.agent_always_custom_dialog || false;
  document.getElementById('gs-dispatch-auto-terminals').checked = s.dispatch_auto_terminals || false;
  document.getElementById('gs-notifications').checked = s.notifications || false;
  document.getElementById('gs-notify-finish').checked = s.notify_on_finish !== false;
  document.getElementById('gs-notify-error').checked = s.notify_on_error !== false;
  document.getElementById('gs-notify-attention').checked = s.notify_on_attention !== false;
  document.getElementById('gs-agent-env-vars').value = _envToText(s.agent_env_vars);
  document.getElementById('gs-agent-env-file').value = s.agent_env_file || '';
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
  document.getElementById('gs-terminal-close-on-disconnect').checked = s.terminal_close_on_disconnect || false;
  document.getElementById('gs-terminal-env-vars').value = _envToText(s.terminal_env_vars);
  document.getElementById('gs-terminal-env-file').value = s.terminal_env_file || '';
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
    env_file: document.getElementById('gs-env-file').value.trim(),
    auto_terminals: parseInt(document.getElementById('gs-auto-terminals').value) || 0,
    max_agents: parseInt(document.getElementById('gs-max-agents').value) || 0,
    collapsed_default: document.getElementById('gs-collapsed').checked,
    filter_by_window: document.getElementById('gs-filter-window').checked,
    /* Agents */
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_profile: document.getElementById('gs-agent-profile').value,
    agent_shell: document.getElementById('gs-agent-shell').value,
    agent_tab_color: _gsAgentColor,
    default_agent_template: document.getElementById('gs-default-agent-template').value,
    agent_provider: _getProviderValue('gs-agent-provider'),
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_env_vars: _textToEnv('gs-agent-env-vars'),
    agent_env_file: document.getElementById('gs-agent-env-file').value.trim(),
    git_worktree: document.getElementById('gs-worktree').checked,
    worktree_base_dir: document.getElementById('gs-wt-base-dir').value.trim() || '.loom/worktrees',
    worktree_base_branch: document.getElementById('gs-wt-base-branch').value.trim(),
    worktree_auto_checkpoint: document.getElementById('gs-wt-auto-checkpoint').checked,
    worktree_merge_squash: document.getElementById('gs-wt-merge-squash').checked,
    worktree_merge_instructions: document.getElementById('gs-wt-merge-instructions').value.trim(),
    agent_session_resume: document.getElementById('gs-session-resume').checked,
    agent_idle_timeout: parseInt(document.getElementById('gs-agent-idle-timeout').value) || 0,
    agent_always_custom_dialog: document.getElementById('gs-agent-always-custom').checked,
    dispatch_auto_terminals: document.getElementById('gs-dispatch-auto-terminals').checked,
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
    terminal_env_file: document.getElementById('gs-terminal-env-file').value.trim(),
    terminal_always_custom_dialog: document.getElementById('gs-terminal-always-custom').checked,
    terminal_close_on_disconnect: document.getElementById('gs-terminal-close-on-disconnect').checked,
  };

  send({ cmd: 'update_group_settings', group: _settingsGroup, settings });
  _settingsGroup = null;
  closeModals();
}

function _toggleWorktreeFields() {
  const on = document.getElementById('gs-worktree').checked;
  document.getElementById('gs-wt-fields').style.display = on ? 'block' : 'none';
}

function _toggleAddWorktreeFields() {
  const on = document.getElementById('add-wt-enabled').checked;
  document.getElementById('add-wt-fields').style.display = on ? 'block' : 'none';
}

function openAddAgent(group, templateName) {
  _openAddModal('agent', group, '', templateName || '');
}
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
  if (_selectedIcon) msg.icon = _selectedIcon;
  if (shell) msg.shell = shell;
  if (Object.keys(envVars).length > 0) msg.env_vars = envVars;
  if (addCellMode === 'terminal') {
    if (command) msg.command = command;
    const args = document.getElementById('add-args-input').value.trim();
    const init = document.getElementById('add-init-input').value.trim();
    if (args) msg.command_args = args;
    if (init) msg.init_script = init;
  } else {
    const tpl = document.getElementById('add-template-select').value;
    if (tpl) msg.template = tpl;
    const prov = document.getElementById('add-provider-select').value;
    if (prov && prov !== '__custom__') msg.provider = prov;
    if (command) msg.command = command;
    /* worktree overrides */
    const wtEnabled = document.getElementById('add-wt-enabled').checked;
    msg.worktree = wtEnabled;
    if (wtEnabled) {
      const wtDir = document.getElementById('add-wt-base-dir').value.trim();
      const wtBranch = document.getElementById('add-wt-base-branch').value.trim();
      if (wtDir) msg.worktree_base_dir = wtDir;
      if (wtBranch) msg.worktree_base_branch = wtBranch;
      msg.worktree_auto_checkpoint = document.getElementById('add-wt-auto-checkpoint').checked;
      msg.worktree_merge_squash = document.getElementById('add-wt-squash').checked;
    }
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

/* ------------------------------------------------------------------ */
/* Action modal                                                        */
/* ------------------------------------------------------------------ */

let _tplGroup = '';
let _tplName = '';
let _tplData = null;
let _tplMode = 'agent';  // 'agent' = create agent, 'task' = pre-fill task modal
let _tplTaskLane = '';    // lane for task mode

function openAddFromAction(group) {
  _tplGroup = group;
  _tplName = '';
  _tplData = null;
  _tplMode = 'agent';
  send({ cmd: 'list_actions', group });
}

function openTaskFromAction(group, lane) {
  _tplGroup = group;
  _tplName = '';
  _tplData = null;
  _tplMode = 'task';
  _tplTaskLane = lane || '';
  send({ cmd: 'list_actions', group });
}

function _showActionList(msg) {
  const actions = msg.actions || [];
  const listEl = document.getElementById('tpl-list');
  const emptyEl = document.getElementById('tpl-empty');
  const listPane = document.getElementById('tpl-list-pane');
  const varsPane = document.getElementById('tpl-vars-pane');

  listPane.classList.remove('hidden');
  varsPane.classList.add('hidden');
  document.getElementById('tpl-back-btn').classList.add('hidden');
  document.getElementById('tpl-submit-btn').classList.add('hidden');
  document.getElementById('tpl-title').textContent = _tplMode === 'task' ? 'Task from Action' : 'From Action';

  if (actions.length === 0) {
    listEl.innerHTML = '';
    emptyEl.classList.remove('hidden');
  } else {
    emptyEl.classList.add('hidden');
    let html = '';
    for (const t of actions) {
      const varCount = (t.vars || []).filter(v => v.name !== 'TASK').length;
      html += `<button class="tpl-item" onclick="_selectAction('${esc(t.name)}')">`;
      html += `<span class="tpl-item-name">${esc(t.name)}</span>`;
      if (t.description) html += `<span class="tpl-item-desc">${esc(t.description)}</span>`;
      if (varCount) html += `<span class="tpl-item-vars">${varCount} var${varCount > 1 ? 's' : ''}</span>`;
      html += `</button>`;
    }
    listEl.innerHTML = html;
  }
  document.getElementById('modal-action').classList.add('visible');
}

function _selectAction(name) {
  _tplName = name;
  send({ cmd: 'get_action', name, group: _tplGroup });
}

function _showActionVarForm(msg) {
  _tplData = msg;
  const vars = msg.vars || [];

  document.getElementById('tpl-list-pane').classList.add('hidden');
  document.getElementById('tpl-vars-pane').classList.remove('hidden');
  document.getElementById('tpl-back-btn').classList.remove('hidden');
  document.getElementById('tpl-submit-btn').classList.remove('hidden');
  document.getElementById('tpl-title').textContent = msg.name;

  const descEl = document.getElementById('tpl-description');
  descEl.textContent = (msg.action || {}).description || '';

  const fieldsEl = document.getElementById('tpl-var-fields');
  let html = '';

  for (const v of vars) {
    const req = v.required ? ' <span class="tpl-req">*</span>' : '';
    const label = v.description || v.name;
    html += `<label>${esc(label)}${req}</label>`;
    if (v.name === 'TASK') {
      html += `<textarea id="tpl-var-${esc(v.name)}" rows="3" placeholder="${esc(label)}">${esc(v.default || '')}</textarea>`;
    } else {
      html += `<input id="tpl-var-${esc(v.name)}" value="${esc(v.default || '')}" placeholder="${esc(label)}" autocomplete="off">`;
    }
  }
  fieldsEl.innerHTML = html;

  const first = fieldsEl.querySelector('textarea, input');
  if (first) setTimeout(() => first.focus(), 50);
}

function _tplBack() {
  send({ cmd: 'list_actions', group: _tplGroup });
}

function _tplSubmit() {
  if (!_tplData) return;
  const vars = {};
  for (const v of (_tplData.vars || [])) {
    const el = document.getElementById('tpl-var-' + v.name);
    if (el) vars[v.name] = el.value;
  }

  // Validate required fields
  for (const v of (_tplData.vars || [])) {
    if (v.required && !vars[v.name]) {
      const el = document.getElementById('tpl-var-' + v.name);
      if (el) { el.focus(); el.classList.add('input-error'); }
      return;
    }
  }

  if (_tplMode === 'task') {
    // Open the task modal with this action pre-selected
    // TASK var goes into the task text field; other vars become action_vars
    closeModals();
    _taskEditId = null;
    _taskSelectedAction = _tplName;
    _taskSelectedTemplate = '';
    _taskActionVarValues = {};
    for (var vk in vars) {
      if (vk !== 'TASK') _taskActionVarValues[vk] = vars[vk];
    }
    document.getElementById('task-modal-title').textContent = 'New Task';
    document.getElementById('task-submit-btn').textContent = 'Create';
    document.getElementById('task-task-input').value = vars['TASK'] || '';
    document.getElementById('task-description-input').value = '';
    _setTaskLabels([]);
    _populateTaskGroupSelect(_tplGroup || _currentGroup());
    document.getElementById('modal-task').dataset.lane = _tplTaskLane || '';
    _taskModalWaiting = true;
    _taskTemplateWaiting = true;
    send({ cmd: 'list_actions', group: _tplGroup || _currentGroup() });
    send({ cmd: 'list_templates', group: _tplGroup || _currentGroup() });
    document.getElementById('modal-task').classList.add('visible');
    document.getElementById('task-task-input').focus();
  } else {
    // Default: create agent from action
    send({
      cmd: 'add_agent_from_action',
      action: _tplName,
      group: _tplGroup,
      vars,
    });
    closeModals();
  }
}

function _handleActionRendered(msg) {
  // "Task from Action" flow: pre-fill task modal with action selected
  _taskEditId = null;
  document.getElementById('task-modal-title').textContent = 'New Task';
  document.getElementById('task-submit-btn').textContent = 'Create';

  document.getElementById('task-task-input').value = '';
  document.getElementById('task-description-input').value = '';
  _setTaskLabels(msg.labels || []);

  _taskSelectedAction = msg.name || _tplName || '';
  _taskSelectedTemplate = '';
  _taskActionVarValues = {};

  var group = msg.group || _tplGroup || _currentGroup();
  _populateTaskGroupSelect(group);

  document.getElementById('modal-task').dataset.lane = _tplTaskLane || '';

  // Request action list to populate the picker
  _taskModalWaiting = true;
  _taskTemplateWaiting = true;
  send({ cmd: 'list_actions', group: group });
  send({ cmd: 'list_templates', group: group });

  document.getElementById('modal-task').classList.add('visible');
  document.getElementById('task-task-input').focus();
}

/* ------------------------------------------------------------------ */
/* Task modal (create & edit)                                          */
/* ------------------------------------------------------------------ */

let _taskEditId = null;  // null = create mode, string = edit mode
let _taskActions = [];          // cached action list for task modal
let _taskTemplates = [];        // cached templates for task modal
let _taskSelectedAction = '';   // selected action name
let _taskSelectedTemplate = ''; // selected template name
let _taskActionVars = [];       // variable definitions for selected action
let _taskActionVarValues = {};  // pre-filled variable values (from edit)
let _taskModalWaiting = false;  // waiting for action list to populate picker
let _taskTemplateWaiting = false; // waiting for template list
let _taskLabels = [];           // user-editable label chips
let _taskSystemLabels = [];     // loom:* labels (read-only, preserved on save)

function taskLabelsKeydown(e) {
  if (e.key === 'Escape') { closeModals(); return; }
  if (e.key === 'Enter') {
    e.preventDefault();
    var val = e.target.value.trim();
    if (!val) return;
    if (_taskLabels.indexOf(val) < 0) _taskLabels.push(val);
    e.target.value = '';
    _renderTaskLabelChips();
  }
}

function taskRemoveLabel(idx) {
  _taskLabels.splice(idx, 1);
  _renderTaskLabelChips();
}

function _renderTaskLabelChips() {
  var container = document.getElementById('task-labels-chips');
  if (!container) return;
  var html = '';
  for (var i = 0; i < _taskLabels.length; i++) {
    html += '<span class="label-chip">' + esc(_taskLabels[i])
      + '<button onclick="taskRemoveLabel(' + i + ')">&times;</button></span>';
  }
  if (_taskSystemLabels.length) {
    for (var i = 0; i < _taskSystemLabels.length; i++) {
      html += '<span class="label-chip-system">' + esc(displayLabel(_taskSystemLabels[i])) + '</span>';
    }
  }
  container.innerHTML = html;
}

function _setTaskLabels(labels) {
  _taskLabels = [];
  _taskSystemLabels = [];
  var all = (labels || []).slice();
  for (var i = 0; i < all.length; i++) {
    if (isSystemLabel(all[i])) _taskSystemLabels.push(all[i]);
    else _taskLabels.push(all[i]);
  }
  _renderTaskLabelChips();
}

/* -- Task modal: dependency picker ---------------------------------------- */

var _taskDeps = [];

function taskDepsSearch(e) {
  var val = e.target.value.trim().toLowerCase();
  var dropdown = document.getElementById('task-deps-dropdown');
  if (!val) { dropdown.style.display = 'none'; return; }
  var tasks = (state && state.board_tasks) || {};
  var html = '';
  var count = 0;
  for (var id in tasks) {
    if (_taskDeps.indexOf(id) >= 0) continue;
    if (_taskEditId && id === _taskEditId) continue;
    var t = tasks[id];
    var title = (t.task || '').toLowerCase();
    var slug = (t.slug || '').toLowerCase();
    if (title.indexOf(val) >= 0 || slug.indexOf(val) >= 0 || id.indexOf(val) >= 0) {
      var laneBadge = '<span class="board-card-lane-badge">' + esc(t.lane || '') + '</span>';
      html += '<div class="deps-option" onmousedown="event.preventDefault()" onclick="taskAddDep(\'' + id + '\')">'
        + esc((t.task || '').substring(0, 50)) + ' ' + laneBadge + '</div>';
      count++;
      if (count >= 8) break;
    }
  }
  dropdown.innerHTML = html;
  dropdown.style.display = count ? '' : 'none';
}

function taskDepsKeydown(e) {
  if (e.key === 'Escape') {
    document.getElementById('task-deps-dropdown').style.display = 'none';
  }
}

function taskAddDep(id) {
  if (_taskDeps.indexOf(id) < 0) _taskDeps.push(id);
  document.getElementById('task-deps-input').value = '';
  document.getElementById('task-deps-dropdown').style.display = 'none';
  _renderTaskDepChips();
}

function taskRemoveDep(idx) {
  _taskDeps.splice(idx, 1);
  _renderTaskDepChips();
}

function _renderTaskDepChips() {
  var container = document.getElementById('task-deps-chips');
  if (!container) return;
  var tasks = (state && state.board_tasks) || {};
  var html = '';
  for (var i = 0; i < _taskDeps.length; i++) {
    var t = tasks[_taskDeps[i]];
    var label = t ? (t.task || '').substring(0, 30) : _taskDeps[i];
    var laneBadge = t ? ' <span class="board-card-lane-badge" style="font-size:9px">' + esc(t.lane || '') + '</span>' : '';
    html += '<span class="label-chip">' + esc(label) + laneBadge
      + '<button onclick="taskRemoveDep(' + i + ')">&times;</button></span>';
  }
  container.innerHTML = html;
}

function _setTaskDeps(deps) {
  _taskDeps = (deps || []).slice();
  _renderTaskDepChips();
}

function _currentGroup() {
  if (selectedAgentId && state && state.agents && state.agents[selectedAgentId]) {
    return state.agents[selectedAgentId].group;
  }
  if (state && state.groups) {
    var keys = Object.keys(state.groups);
    if (keys.length) return keys[0];
  }
  return '';
}

function openAddTask(lane) {
  _taskEditId = null;
  _taskSelectedAction = '';
  _taskSelectedTemplate = '';
  _taskActionVars = [];
  _taskActionVarValues = {};

  document.getElementById('task-modal-title').textContent = 'New Task';
  document.getElementById('task-submit-btn').textContent = 'Create';

  document.getElementById('task-task-input').value = '';
  document.getElementById('task-description-input').value = '';
  var schedInput = document.getElementById('task-scheduled-input');
  if (schedInput) schedInput.value = '';
  _setTaskLabels([]);
  document.getElementById('task-labels-input').value = '';
  _setTaskDeps([]);
  document.getElementById('task-action-vars').innerHTML = '';

  _populateTaskGroupSelect(_currentGroup());

  // Request actions for picker
  _taskModalWaiting = true;
  _taskTemplateWaiting = true;
  send({ cmd: 'list_actions', group: _currentGroup() });
  send({ cmd: 'list_templates', group: _currentGroup() });

  document.getElementById('modal-task').classList.add('visible');
  document.getElementById('task-task-input').focus();

  // Stash lane for submit
  document.getElementById('modal-task').dataset.lane = lane || '';
}

function openEditTask(taskId) {
  var tasks = (state && state.board_tasks) || {};
  var t = tasks[taskId];
  if (!t) return;

  _taskEditId = taskId;
  _taskSelectedAction = t.action_name || '';
  _taskSelectedTemplate = t.agent_template || '';
  _taskActionVars = [];
  _taskActionVarValues = t.action_vars || {};

  document.getElementById('task-modal-title').textContent = 'Edit Task';
  document.getElementById('task-submit-btn').textContent = 'Save';

  var taskEl = document.getElementById('task-task-input');
  taskEl.value = t.task || '';
  var descEl = document.getElementById('task-description-input');
  descEl.value = t.description || '';
  _setTaskLabels(t.labels || []);
  document.getElementById('task-labels-input').value = '';
  _setTaskDeps(t.depends_on || []);
  document.getElementById('task-action-vars').innerHTML = '';

  // Scheduled dispatch
  var schedInput = document.getElementById('task-scheduled-input');
  if (schedInput) {
    if (t.scheduled_at) {
      try {
        var d = new Date(t.scheduled_at);
        schedInput.value = (d > new Date()) ? d.toISOString().slice(0, 16) : '';
      } catch(e) { schedInput.value = ''; }
    } else {
      schedInput.value = '';
    }
  }

  _populateTaskGroupSelect(t.group || _currentGroup());

  // Request actions for picker
  _taskModalWaiting = true;
  _taskTemplateWaiting = true;
  send({ cmd: 'list_actions', group: t.group || _currentGroup() });
  send({ cmd: 'list_templates', group: t.group || _currentGroup() });

  document.getElementById('modal-task').classList.add('visible');
  taskAutoResize(taskEl);
  taskAutoResize(descEl);
  taskEl.focus();
  taskEl.select();
}

function _populateTaskGroupSelect(defaultGroup) {
  var sel = document.getElementById('task-group-select');
  sel.innerHTML = '';
  if (state && state.groups) {
    for (var g of Object.keys(state.groups)) {
      var opt = document.createElement('option');
      opt.value = g;
      opt.textContent = g;
      if (g === defaultGroup) opt.selected = true;
      sel.appendChild(opt);
    }
  }
}

function submitTask() {
  var task = document.getElementById('task-task-input').value.trim();
  var group = document.getElementById('task-group-select').value;
  if (!task || !group) {
    if (!task) document.getElementById('task-task-input').focus();
    else document.getElementById('task-group-select').focus();
    return;
  }

  var description = document.getElementById('task-description-input').value.trim();
  _taskSelectedTemplate = document.getElementById('task-template-select').value || '';
  // Include any text still in the input as a label
  var pendingLabel = document.getElementById('task-labels-input').value.trim();
  if (pendingLabel && _taskLabels.indexOf(pendingLabel) < 0) _taskLabels.push(pendingLabel);
  var labels = _taskLabels.concat(_taskSystemLabels);
  var actionVars = _collectTaskActionVars();

  var schedVal = (document.getElementById('task-scheduled-input') || {}).value || '';
  var scheduledAt = schedVal ? new Date(schedVal).toISOString() : '';

  if (_taskEditId) {
    // Edit mode
    var msg = { cmd: 'board_update_task', id: _taskEditId, task: task, group: group, description: description };
    msg.action_name = _taskSelectedAction;
    msg.agent_template = _taskSelectedTemplate;
    msg.action_vars = actionVars;
    msg.labels = labels;
    msg.scheduled_at = scheduledAt;
    msg.depends_on = _taskDeps.slice();
    send(msg);
  } else {
    // Create mode
    var lane = document.getElementById('modal-task').dataset.lane || '';
    var msg = { cmd: 'board_add_task', task: task, group: group, lane: lane };
    if (description) msg.description = description;
    if (_taskSelectedAction) msg.action_name = _taskSelectedAction;
    if (_taskSelectedTemplate) msg.agent_template = _taskSelectedTemplate;
    if (Object.keys(actionVars).length) msg.action_vars = actionVars;
    if (labels.length) msg.labels = labels;
    if (scheduledAt) msg.scheduled_at = scheduledAt;
    if (_taskDeps.length) msg.depends_on = _taskDeps.slice();
    send(msg);
  }

  _taskEditId = null;
  closeModals();
}

/* -- Task modal: action picker helpers ---------------------------------- */

function _populateTaskActionSelect(actions) {
  _taskActions = actions;
  var sel = document.getElementById('task-action-select');
  sel.innerHTML = '<option value="">None</option>';
  for (var i = 0; i < actions.length; i++) {
    var t = actions[i];
    var opt = document.createElement('option');
    opt.value = t.name;
    opt.textContent = t.name + (t.description ? ' \u2014 ' + t.description : '');
    if (t.name === _taskSelectedAction) opt.selected = true;
    sel.appendChild(opt);
  }
  var previewBtn = document.getElementById('task-preview-btn');
  if (previewBtn) previewBtn.style.display = _taskSelectedAction ? '' : 'none';
  // Render variable fields for the selected action
  if (_taskSelectedAction) {
    var act = actions.find(function(t) { return t.name === _taskSelectedAction; });
    if (act && act.vars) {
      _taskActionVars = (act.vars || []).filter(function(v) { return v.name !== 'TASK'; });
    } else {
      _taskActionVars = [];
    }
  } else {
    _taskActionVars = [];
  }
  _renderTaskActionVars();
}

function _populateTaskTemplateSelect(templates) {
  _taskTemplates = templates;
  _populateTemplateSelect('task-template-select', _taskSelectedTemplate, 'None');
}

function taskActionChanged() {
  var sel = document.getElementById('task-action-select');
  _taskSelectedAction = sel.value;
  _taskActionVarValues = {};
  var previewBtn = document.getElementById('task-preview-btn');
  if (previewBtn) previewBtn.style.display = _taskSelectedAction ? '' : 'none';
  if (_taskSelectedAction) {
    var act = _taskActions.find(function(t) { return t.name === _taskSelectedAction; });
    if (act && act.vars) {
      _taskActionVars = (act.vars || []).filter(function(v) { return v.name !== 'TASK'; });
    } else {
      _taskActionVars = [];
    }
  } else {
    _taskActionVars = [];
  }
  _renderTaskActionVars();
}

function _renderTaskActionVars() {
  var container = document.getElementById('task-action-vars');
  // If action template has variable definitions, use those
  var vars = _taskActionVars;
  // Fallback: if no definitions but task has stored values (action deleted),
  // build variable list from the stored keys
  if (!vars.length && _taskActionVarValues && Object.keys(_taskActionVarValues).length) {
    vars = Object.keys(_taskActionVarValues).map(function(k) {
      return { name: k, default: '' };
    });
  }
  if (!vars.length) {
    container.innerHTML = '';
    return;
  }
  var html = '<fieldset class="task-tpl-vars-group"><legend>Action Variables</legend>';
  for (var i = 0; i < vars.length; i++) {
    var v = vars[i];
    var savedVal = (_taskActionVarValues || {})[v.name] || '';
    var val = savedVal || v.default || '';
    html += '<label>' + esc(v.name) + '</label>';
    html += '<textarea class="task-tpl-var" data-var="' + esc(v.name)
          + '" rows="1" placeholder="' + esc(v.default || v.name)
          + '" oninput="taskAutoResize(this)"'
          + ' onkeydown="if(event.key===\'Escape\')closeModals();">' + esc(val) + '</textarea>';
  }
  html += '</fieldset>';
  container.innerHTML = html;
  // Auto-resize pre-filled textareas
  container.querySelectorAll('textarea').forEach(taskAutoResize);
}

function _collectTaskActionVars() {
  var vars = {};
  var els = document.querySelectorAll('.task-tpl-var');
  for (var i = 0; i < els.length; i++) {
    var name = els[i].dataset.var;
    if (name && els[i].value) vars[name] = els[i].value;
  }
  return vars;
}

function previewTaskPrompt() {
  var task = document.getElementById('task-task-input').value.trim();
  var description = document.getElementById('task-description-input').value.trim();
  var actionVars = _collectTaskActionVars();
  var msg = {
    cmd: 'preview_prompt',
    task: task,
    description: description,
    action_name: _taskSelectedAction,
    action_vars: actionVars,
    group: document.getElementById('task-group-select').value,
  };
  if (_taskEditId) msg.id = _taskEditId;
  send(msg);
}

function _handleTaskActionList(msg) {
  // Called when action list arrives and task modal is open
  _taskModalWaiting = false;
  _populateTaskActionSelect(msg.actions || []);
}

function _handleTaskTemplateList(msg) {
  _taskTemplateWaiting = false;
  _populateTaskTemplateSelect(msg.templates || []);
}

function taskAutoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

function _showPromptPreview(msg) {
  document.getElementById('prompt-preview-content').textContent = msg.prompt || '(empty)';
  var warnEl = document.getElementById('prompt-preview-warning');
  if (msg.warning) {
    warnEl.textContent = msg.warning;
    warnEl.style.display = '';
  } else {
    warnEl.style.display = 'none';
  }
  document.getElementById('modal-prompt-preview').classList.add('visible');
}

/* -- Global Settings ---------------------------------------------------- */
var _glsKeybindings = {};     // current keybinding overrides being edited
var _glsDefaults = {};        // default keybinding specs from server
var _glsCapturing = null;     // action name currently capturing a keypress

function switchGlsTab(name) {
  document.querySelectorAll('#modal-global-settings .gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('#modal-global-settings .gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
}

function switchGlsSubTab(btn) {
  var container = btn.closest('.gs-pane');
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  var target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
}

function openGlobalSettings() {
  send({ cmd: 'get_global_settings' });
}

function _showGlobalSettingsModal(data) {
  var s = data.settings;
  _glsDefaults = data.keybinding_defaults || {};
  _glsKeybindings = Object.assign({}, s.keybindings || {});

  // General > Server
  document.getElementById('gls-default-cmd').value = s.default_command || '';
  document.getElementById('gls-filter-window').checked =
    s.filter_by_window !== undefined ? s.filter_by_window : true;
  document.getElementById('gls-focus-new-tabs').checked =
    s.focus_new_tabs !== undefined ? s.focus_new_tabs : true;
  document.getElementById('gls-max-event-log').value =
    s.max_event_log !== undefined ? s.max_event_log : 500;

  // General > Board
  document.getElementById('gls-default-lanes').value =
    (s.default_lanes || []).join('\n');
  document.getElementById('gls-max-pipeline-depth').value =
    s.max_pipeline_depth !== undefined ? s.max_pipeline_depth : 10;

  // Keybindings
  _renderKeybindingList();

  // Reset tabs
  switchGlsTab('gls-general');
  var firstSub = document.querySelector('#modal-global-settings .gs-subtab');
  if (firstSub) switchGlsSubTab(firstSub);

  document.getElementById('modal-global-settings').classList.add('visible');
  document.getElementById('gls-default-cmd').focus();
}

function _kbDisplayName(action, binding) {
  var b = binding || _glsDefaults[action] || {};
  var mods = (b.modifiers || []).map(function(m) {
    if (m === 'command') return '\u2318';
    if (m === 'option') return '\u2325';
    if (m === 'shift') return '\u21E7';
    if (m === 'control') return '\u2303';
    return m;
  });
  var key = (b.keycode || '').replace('ANSI_', '').replace('_ARROW', '');
  var arrowMap = { 'UP': '\u2191', 'DOWN': '\u2193', 'LEFT': '\u2190', 'RIGHT': '\u2192' };
  key = arrowMap[key] || key;
  return mods.join('') + key;
}

function _renderKeybindingList() {
  var container = document.getElementById('gls-keybinding-list');
  var html = '';
  for (var action in _glsDefaults) {
    var def = _glsDefaults[action];
    var current = _glsKeybindings[action] || null;
    var display = _kbDisplayName(action, current);
    var label = def.label || action;
    var isCapturing = _glsCapturing === action;
    html += '<div class="kb-row">';
    html += '  <span class="kb-label">' + esc(label) + '</span>';
    if (isCapturing) {
      html += '  <span class="kb-combo kb-capturing">Press keys\u2026</span>';
      html += '  <button class="kb-btn" onclick="_cancelCapture()">Cancel</button>';
    } else {
      html += '  <span class="kb-combo">' + display + '</span>';
      html += '  <button class="kb-btn" onclick="_startCapture(\'' + action + '\')">Rebind</button>';
      if (current) {
        html += '  <button class="kb-btn" onclick="_resetKeybinding(\'' + action + '\')">Reset</button>';
      }
    }
    html += '</div>';
  }
  container.innerHTML = html;
}

function _startCapture(action) {
  _glsCapturing = action;
  _renderKeybindingList();
  send({ cmd: 'suspend_keybindings' });
  document.addEventListener('keydown', _captureKeydown, true);
}

function _cancelCapture() {
  _glsCapturing = null;
  document.removeEventListener('keydown', _captureKeydown, true);
  send({ cmd: 'resume_keybindings' });
  _renderKeybindingList();
}

function _captureKeydown(e) {
  e.preventDefault();
  e.stopPropagation();
  // Ignore bare modifier presses
  if (['Meta', 'Alt', 'Shift', 'Control'].includes(e.key)) return;

  var modifiers = [];
  if (e.metaKey) modifiers.push('command');
  if (e.altKey) modifiers.push('option');
  if (e.shiftKey) modifiers.push('shift');
  if (e.ctrlKey) modifiers.push('control');

  var keycode = _jsCodeToKeycode(e.code);
  var character = _jsCodeToCharacter(e.code, e.key);

  if (_glsCapturing && keycode) {
    _glsKeybindings[_glsCapturing] = {
      modifiers: modifiers,
      keycode: keycode,
      character: character,
    };
  }
  _cancelCapture();
}

function _jsCodeToKeycode(code) {
  var map = {
    'ArrowUp': 'UP_ARROW', 'ArrowDown': 'DOWN_ARROW',
    'ArrowLeft': 'LEFT_ARROW', 'ArrowRight': 'RIGHT_ARROW',
    'Enter': 'RETURN', 'Tab': 'TAB', 'Space': 'SPACE',
    'Backspace': 'DELETE', 'Escape': 'ESCAPE',
    'Delete': 'FORWARD_DELETE',
    'Home': 'HOME', 'End': 'END',
    'PageUp': 'PAGE_UP', 'PageDown': 'PAGE_DOWN',
  };
  if (map[code]) return map[code];
  var m = code.match(/^Key([A-Z])$/);
  if (m) return 'ANSI_' + m[1];
  var d = code.match(/^Digit(\d)$/);
  if (d) return 'ANSI_' + d[1];
  var f = code.match(/^F(\d+)$/);
  if (f) return 'F' + f[1];
  var punct = {
    'Minus': 'ANSI_MINUS', 'Equal': 'ANSI_EQUAL',
    'BracketLeft': 'ANSI_LEFT_BRACKET', 'BracketRight': 'ANSI_RIGHT_BRACKET',
    'Backslash': 'ANSI_BACKSLASH', 'Semicolon': 'ANSI_SEMICOLON',
    'Quote': 'ANSI_QUOTE', 'Comma': 'ANSI_COMMA',
    'Period': 'ANSI_PERIOD', 'Slash': 'ANSI_SLASH',
    'Backquote': 'ANSI_GRAVE',
  };
  return punct[code] || null;
}

function _jsCodeToCharacter(code, key) {
  var arrowChars = {
    'ArrowUp': 0xF700, 'ArrowDown': 0xF701,
    'ArrowLeft': 0xF702, 'ArrowRight': 0xF703,
  };
  if (arrowChars[code]) return arrowChars[code];
  if (key.length === 1) return key.toUpperCase().charCodeAt(0);
  var special = {
    'Enter': 13, 'Tab': 9, 'Space': 32, 'Backspace': 127, 'Escape': 27,
  };
  return special[key] || 0;
}

function _resetKeybinding(action) {
  delete _glsKeybindings[action];
  _renderKeybindingList();
}

function submitGlobalSettings() {
  var lanesText = document.getElementById('gls-default-lanes').value.trim();
  var lanes = lanesText
    ? lanesText.split('\n').map(function(l) { return l.trim(); }).filter(Boolean)
    : [];

  var settings = {
    default_command: document.getElementById('gls-default-cmd').value.trim(),
    filter_by_window: document.getElementById('gls-filter-window').checked,
    focus_new_tabs: document.getElementById('gls-focus-new-tabs').checked,
    default_lanes: lanes,
    keybindings: _glsKeybindings,
    max_pipeline_depth: parseInt(document.getElementById('gls-max-pipeline-depth').value) || 0,
    max_event_log: parseInt(document.getElementById('gls-max-event-log').value) || 500,
  };
  send({ cmd: 'update_global_settings', settings: settings });
  closeModals();
}

/* ---- Schedule modal -------------------------------------------------- */

var _schedEditId = '';        // empty = create mode, set = edit mode
var _schedType = 'recurring'; // 'recurring' | 'oneshot'
var _schedLabels = [];
var _schedModalWaiting = false; // waiting for action list
var _schedDeferredAction = '';  // action to select once list loads (edit mode)
var _schedDeferredVars = {};    // vars to pre-fill once action is selected

function openScheduleModal(editId) {
  _schedEditId = editId || '';
  _schedLabels = [];
  _schedDeferredAction = '';
  _schedDeferredVars = {};

  // Populate group select
  var sel = document.getElementById('schedule-group-select');
  sel.innerHTML = '';
  var groups = state.groups || {};
  for (var g in groups) {
    var opt = document.createElement('option');
    opt.value = g;
    opt.textContent = g;
    sel.appendChild(opt);
  }

  // Populate action select (request from server)
  var actionSel = document.getElementById('schedule-action-select');
  actionSel.innerHTML = '<option value="">None</option>';
  var grp = sel.value || '';
  if (grp) {
    _schedModalWaiting = true;
    send({ cmd: 'list_actions', group: grp });
  }

  // Reset fields
  document.getElementById('schedule-name-input').value = '';
  document.getElementById('schedule-task-input').value = '';
  document.getElementById('schedule-desc-input').value = '';
  document.getElementById('schedule-cron-input').value = '';
  document.getElementById('schedule-at-input').value = '';
  document.getElementById('schedule-tz-input').value = '';
  document.getElementById('schedule-action-vars').innerHTML = '';
  document.getElementById('schedule-labels-chips').innerHTML = '';

  if (_schedEditId) {
    // Edit mode — populate from existing schedule
    var s = (state.schedules || {})[_schedEditId];
    if (!s) return;
    document.getElementById('schedule-modal-title').textContent = 'Edit Schedule';
    document.getElementById('schedule-submit-btn').textContent = 'Save';
    document.getElementById('schedule-name-input').value = s.name || '';
    document.getElementById('schedule-task-input').value = s.task_template || '';
    document.getElementById('schedule-desc-input').value = s.description || '';
    document.getElementById('schedule-tz-input').value = s.timezone || '';
    if (s.group) sel.value = s.group;

    if (s.cron_expr) {
      _schedType = 'recurring';
      document.getElementById('schedule-cron-input').value = s.cron_expr;
    } else {
      _schedType = 'oneshot';
      if (s.scheduled_at) {
        // Convert ISO to datetime-local format
        try {
          var d = new Date(s.scheduled_at);
          document.getElementById('schedule-at-input').value =
            d.toISOString().slice(0, 16);
        } catch(e) {}
      }
    }

    _schedLabels = (s.labels || []).slice();

    // Set action — deferred until action list loads
    if (s.action_name) {
      _schedDeferredAction = s.action_name;
      _schedDeferredVars = s.action_vars || {};
    }
  } else {
    document.getElementById('schedule-modal-title').textContent = 'New Schedule';
    document.getElementById('schedule-submit-btn').textContent = 'Create';
    _schedType = 'recurring';
  }

  scheduleSetType(_schedType);
  _schedRenderLabels();

  document.getElementById('modal-schedule').classList.add('visible');
  setTimeout(function() {
    document.getElementById('schedule-name-input').focus();
  }, 50);
}

function scheduleSetType(type) {
  _schedType = type;
  document.getElementById('schedule-type-recurring')
    .classList.toggle('active', type === 'recurring');
  document.getElementById('schedule-type-oneshot')
    .classList.toggle('active', type === 'oneshot');
  document.getElementById('schedule-cron-section')
    .style.display = type === 'recurring' ? '' : 'none';
  document.getElementById('schedule-at-section')
    .style.display = type === 'oneshot' ? '' : 'none';
}

function scheduleSetCron(expr) {
  document.getElementById('schedule-cron-input').value = expr;
}

var _schedActions = []; // cached action list for schedule modal

function scheduleActionChanged() {
  var sel = document.getElementById('schedule-action-select');
  var action = sel.value;
  var varsDiv = document.getElementById('schedule-action-vars');
  varsDiv.innerHTML = '';
  if (!action) return;

  // Look up variables from cached action list
  var act = null;
  for (var i = 0; i < _schedActions.length; i++) {
    if (_schedActions[i].name === action) { act = _schedActions[i]; break; }
  }
  if (act && act.vars) {
    var varNames = act.vars.filter(function(v) { return v.name !== 'TASK' && v.name !== 'loom'; })
      .map(function(v) { return v.name; });
    _schedRenderActionVars(varNames);
  }
}

function _schedRenderActionVars(vars) {
  var div = document.getElementById('schedule-action-vars');
  if (!div) return;
  div.innerHTML = '';
  if (!vars || !vars.length) return;

  var fs = document.createElement('fieldset');
  fs.className = 'action-vars-fieldset';
  var legend = document.createElement('legend');
  legend.textContent = 'Action variables';
  fs.appendChild(legend);

  for (var i = 0; i < vars.length; i++) {
    var v = vars[i];
    if (v === 'TASK' || v === 'loom') continue;
    var label = document.createElement('label');
    label.textContent = v;
    var ta = document.createElement('textarea');
    ta.className = 'action-var-input';
    ta.rows = 1;
    ta.dataset.var = v;
    ta.oninput = function() { taskAutoResize(this); };
    fs.appendChild(label);
    fs.appendChild(ta);
  }
  div.appendChild(fs);
}

function scheduleLabelsKeydown(e) {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  var inp = document.getElementById('schedule-labels-input');
  var val = inp.value.trim();
  if (val && _schedLabels.indexOf(val) === -1) {
    _schedLabels.push(val);
    _schedRenderLabels();
  }
  inp.value = '';
}

function _schedRenderLabels() {
  var div = document.getElementById('schedule-labels-chips');
  div.innerHTML = '';
  for (var i = 0; i < _schedLabels.length; i++) {
    var lbl = _schedLabels[i];
    var chip = document.createElement('span');
    chip.className = 'label-chip';
    chip.textContent = lbl;
    chip.dataset.index = i;
    chip.onclick = function() {
      _schedLabels.splice(parseInt(this.dataset.index), 1);
      _schedRenderLabels();
    };
    div.appendChild(chip);
  }
}

function _handleScheduleActionList(msg) {
  _schedModalWaiting = false;
  var actions = msg.actions || [];
  _schedActions = actions;
  var sel = document.getElementById('schedule-action-select');
  if (!sel) return;
  var prev = sel.value;
  sel.innerHTML = '<option value="">None</option>';
  for (var i = 0; i < actions.length; i++) {
    var a = actions[i];
    var opt = document.createElement('option');
    opt.value = a.name;
    opt.textContent = a.name;
    sel.appendChild(opt);
  }
  if (prev) sel.value = prev;

  // Apply deferred action selection (edit mode)
  if (_schedDeferredAction) {
    sel.value = _schedDeferredAction;
    _schedDeferredAction = '';
    scheduleActionChanged();
    // Pre-fill vars
    if (_schedDeferredVars && Object.keys(_schedDeferredVars).length) {
      var inputs = document.getElementById('schedule-action-vars')
        .querySelectorAll('textarea');
      for (var i = 0; i < inputs.length; i++) {
        var key = inputs[i].dataset.var;
        if (key && _schedDeferredVars[key]) {
          inputs[i].value = _schedDeferredVars[key];
        }
      }
      _schedDeferredVars = {};
    }
  }
}

function submitSchedule() {
  var name = document.getElementById('schedule-name-input').value.trim();
  var group = document.getElementById('schedule-group-select').value;
  if (!name) return;
  if (!group) return;

  var payload = {
    name: name,
    group: group,
    task_template: document.getElementById('schedule-task-input').value.trim(),
    description: document.getElementById('schedule-desc-input').value.trim(),
    timezone: document.getElementById('schedule-tz-input').value.trim(),
    labels: _schedLabels.slice(),
  };

  if (_schedType === 'recurring') {
    var cron = document.getElementById('schedule-cron-input').value.trim();
    if (!cron) return;
    payload.cron_expr = cron;
  } else {
    var at = document.getElementById('schedule-at-input').value;
    if (!at) return;
    payload.scheduled_at = new Date(at).toISOString();
  }

  var action = document.getElementById('schedule-action-select').value;
  if (action) {
    payload.action_name = action;
    var vars = {};
    var inputs = document.getElementById('schedule-action-vars')
      .querySelectorAll('textarea');
    for (var i = 0; i < inputs.length; i++) {
      var key = inputs[i].dataset.var;
      var val = inputs[i].value.trim();
      if (key && val) vars[key] = val;
    }
    if (Object.keys(vars).length) payload.action_vars = vars;
  }

  if (_schedEditId) {
    payload.cmd = 'schedule_update';
    payload.id = _schedEditId;
  } else {
    payload.cmd = 'schedule_create';
  }

  send(payload);
  closeModals();
}

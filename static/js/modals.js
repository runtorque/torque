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
  var taskModal = document.getElementById('modal-task');
  if (taskModal && taskModal.classList.contains('visible') && typeof _taskClearDraft === 'function') {
    _taskClearDraft(_taskEditId, _taskDraftScope);
    _taskDraftScope = 'create';
  }
  // Clean up draft attachments if task modal was open in create mode
  if (typeof _cleanupDraftAttachments === 'function') _cleanupDraftAttachments();
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
    document.getElementById('add-wt-checkpoint-on-progress').checked = resolved.checkpoint_on_progress || false;
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
  document.getElementById('add-wt-checkpoint-on-progress').checked = !!config.checkpoint_on_progress;
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
  document.getElementById('gs-wt-checkpoint-on-progress').checked = s.checkpoint_on_progress || false;
  document.getElementById('gs-wt-merge-squash').checked = s.worktree_merge_squash !== false;
  document.getElementById('gs-wt-merge-instructions').value = s.worktree_merge_instructions || '';
  _gsWtSymlinks = (s.worktree_symlinks || []).slice();
  _renderWtSymlinks();
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
    checkpoint_on_progress: document.getElementById('gs-wt-checkpoint-on-progress').checked,
    worktree_merge_squash: document.getElementById('gs-wt-merge-squash').checked,
    worktree_merge_instructions: document.getElementById('gs-wt-merge-instructions').value.trim(),
    worktree_symlinks: _gsWtSymlinks.slice(),
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

/* -- Worktree symlinks list ------------------------------------------------ */
let _gsWtSymlinks = [];

function _renderWtSymlinks() {
  const container = document.getElementById('gs-wt-symlinks-list');
  container.innerHTML = '';
  for (let i = 0; i < _gsWtSymlinks.length; i++) {
    const chip = document.createElement('span');
    chip.className = 'wt-symlink-chip';
    chip.textContent = _gsWtSymlinks[i];
    const btn = document.createElement('button');
    btn.textContent = '×';
    btn.onclick = () => { _gsWtSymlinks.splice(i, 1); _renderWtSymlinks(); };
    chip.appendChild(btn);
    container.appendChild(chip);
  }
}

function _addWtSymlink() {
  const input = document.getElementById('gs-wt-symlink-input');
  const val = input.value.trim().replace(/^\/+|\/+$/g, '');
  if (!val || _gsWtSymlinks.includes(val)) return;
  _gsWtSymlinks.push(val);
  _renderWtSymlinks();
  input.value = '';
}

function _addWtSymlinkPreset(path) {
  if (!_gsWtSymlinks.includes(path)) {
    _gsWtSymlinks.push(path);
    _renderWtSymlinks();
  }
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
      msg.checkpoint_on_progress = document.getElementById('add-wt-checkpoint-on-progress').checked;
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
let _tplTaskLane = '';    // lane for task mode

function openTaskFromAction(group, lane) {
  _tplGroup = group;
  _tplName = '';
  _tplData = null;
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
  document.getElementById('tpl-title').textContent = 'Task from Action';

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

  // Open the task modal with this action pre-selected
  // TASK var goes into the task text field; other vars become action_vars
  closeModals();
  var actionVarValues = {};
  for (var vk in vars) {
    if (vk !== 'TASK') actionVarValues[vk] = vars[vk];
  }
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: vars['TASK'] || '',
    description: '',
    labels: [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: _tplName,
    agentTemplate: '',
    actionVars: actionVarValues,
    group: _tplGroup || _currentGroup(),
    lane: _tplTaskLane || '',
    scheduledInput: '',
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

function _handleActionRendered(msg) {
  // "Task from Action" flow: pre-fill task modal with action selected
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: '',
    description: '',
    labels: msg.labels || [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    actionName: msg.name || _tplName || '',
    agentTemplate: '',
    actionVars: {},
    group: msg.group || _tplGroup || _currentGroup(),
    lane: _tplTaskLane || '',
    scheduledInput: '',
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

/* ------------------------------------------------------------------ */
/* Task modal (create & edit)                                          */
/* ------------------------------------------------------------------ */

let _taskEditId = null;  // null = create mode, string = edit mode
let _taskDraftId = '';          // pre-generated ID for new tasks (for attachments)
let _taskAttachments = [];      // current attachments [{path, filename, mime_type}]
let _taskOriginalAttachments = []; // attachments at modal open (for cancel cleanup)
let _taskArtifacts = [];        // structured task artifacts
let _taskOriginalArtifacts = []; // artifacts at modal open (for cancel cleanup)
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
let _taskExternalProvider = '';
let _taskExternalId = '';
let _taskExternalUrl = '';
let _taskModalDrafts = {};      // keyed by create/edit mode to survive reopen
let _taskDraftScope = 'create'; // separates plain create drafts from clone flows
let _taskArtifactEditIndex = -1;
let _taskArtifactDraft = null;

var _labelDropdownIdx = -1;

var _artifactTypeLabels = {
  image: 'Image',
  file_ref: 'File ref',
  snippet: 'Snippet',
  log: 'Log',
  diff: 'Diff',
  test_report: 'Test report',
  generated_doc: 'Generated doc',
};

var _artifactPromptModeLabels = {
  auto: 'Auto',
  none: 'Skip prompt',
  path: 'Path',
  summary: 'Summary',
  inline: 'Inline',
};

function _artifactTypeLabel(type) {
  return _artifactTypeLabels[type] || (type || 'artifact');
}

function _artifactDefaultPromptMode(type) {
  if (type === 'image' || type === 'file_ref') return 'path';
  if (type === 'snippet') return 'inline';
  return 'summary';
}

function _artifactStorageKind(artifact) {
  if (!artifact) return 'inline';
  if (artifact.storage && artifact.storage.kind) return artifact.storage.kind;
  if (artifact.type === 'file_ref') return 'file_ref';
  if (artifact.path) return 'path';
  return 'inline';
}

function _artifactNormalizeClient(artifact, index) {
  var source = artifact || {};
  var path = source.path || ((source.storage || {}).path) || '';
  var content = source.content;
  if (content === undefined || content === null) {
    content = ((source.storage || {}).content) || '';
  }
  var type = source.type || 'file_ref';
  var lineStart = source.line_start;
  var lineEnd = source.line_end;
  var filename = source.filename || '';
  if (!filename && path) {
    var parts = String(path).split(/[\\/]/);
    filename = parts[parts.length - 1] || '';
  }
  return {
    id: source.id || ('artifact-' + (index + 1)),
    type: type,
    title: source.title || filename || _artifactTypeLabel(type),
    filename: filename,
    path: path,
    mime_type: source.mime_type || '',
    summary: source.summary || '',
    content: content || '',
    line_start: lineStart === undefined ? null : lineStart,
    line_end: lineEnd === undefined ? null : lineEnd,
    metadata: source.metadata || {},
    prompt: source.prompt || { mode: _artifactDefaultPromptMode(type) },
    provenance: source.provenance || {},
    storage: source.storage || {
      kind: _artifactStorageKind(source),
      path: path,
      content: content || '',
      line_start: lineStart === undefined ? null : lineStart,
      line_end: lineEnd === undefined ? null : lineEnd,
    },
    lifecycle: source.lifecycle || {},
    taskId: source.taskId || '',
    taskLabel: source.taskLabel || '',
  };
}

function _artifactFromAttachment(attachment, taskId, taskLabel, index) {
  var a = attachment || {};
  return _artifactNormalizeClient({
    id: 'attachment-image-' + (index + 1),
    type: 'image',
    title: a.filename || 'image',
    filename: a.filename || '',
    path: a.path || '',
    mime_type: a.mime_type || 'image/png',
    metadata: { legacy_attachment: true },
    prompt: { mode: 'path' },
    storage: {
      kind: 'path',
      path: a.path || '',
      content: '',
      line_start: null,
      line_end: null,
    },
    lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
    taskId: taskId || '',
    taskLabel: taskLabel || '',
  }, index);
}

function _taskArtifactsCombined(task) {
  var combined = [];
  task = task || {};
  var attachments = task.attachments || [];
  for (var i = 0; i < attachments.length; i++) {
    combined.push(_artifactFromAttachment(
      attachments[i],
      task.id || '',
      task.task || '',
      i,
    ));
  }
  var artifacts = task.artifacts || [];
  for (var j = 0; j < artifacts.length; j++) {
    var item = _artifactNormalizeClient(artifacts[j], combined.length + j);
    item.taskId = item.taskId || task.id || '';
    item.taskLabel = item.taskLabel || task.task || '';
    combined.push(item);
  }
  return combined;
}

function _artifactCountForTask(task) {
  return _taskArtifactsCombined(task).length;
}

function _artifactIsTextLike(artifact) {
  if (!artifact) return false;
  if (artifact.content) return true;
  var type = artifact.type || '';
  if (type === 'snippet' || type === 'log' || type === 'diff'
      || type === 'test_report' || type === 'generated_doc') {
    return true;
  }
  var mime = (artifact.mime_type || '').toLowerCase();
  return mime.indexOf('text/') === 0
    || mime.indexOf('json') >= 0
    || mime.indexOf('xml') >= 0
    || mime.indexOf('javascript') >= 0;
}

function _artifactPreviewText(artifact) {
  if (!artifact) return '';
  var text = artifact.content || artifact.summary || '';
  if (!text && artifact.path) text = artifact.path;
  text = String(text || '').trim();
  if (!text) return '';
  if (text.length > 480) return text.slice(0, 480) + '\n...';
  return text;
}

function _artifactStatsLabel(artifact) {
  var meta = (artifact && artifact.metadata) || {};
  if (meta.files || meta.insertions || meta.deletions) {
    return (meta.files || 0) + ' files, +' + (meta.insertions || 0)
      + '/-' + (meta.deletions || 0);
  }
  return '';
}

function _artifactFileUrl(taskId, artifact) {
  var effectiveTaskId = (artifact && artifact.taskId) || taskId || '';
  var filename = (artifact && artifact.filename) || '';
  if (!effectiveTaskId || !filename) return '';
  if (_artifactStorageKind(artifact) === 'inline' && !artifact.path) return '';
  return '/attachments/' + encodeURIComponent(effectiveTaskId) + '/'
    + encodeURIComponent(filename);
}

function _artifactMetaHtml(artifact) {
  var bits = [];
  bits.push('<span class="artifact-chip artifact-chip-type">'
    + esc(_artifactTypeLabel(artifact.type)) + '</span>');
  var promptMode = ((artifact.prompt || {}).mode) || _artifactDefaultPromptMode(artifact.type);
  bits.push('<span class="artifact-chip">' + esc(_artifactPromptModeLabels[promptMode] || promptMode) + '</span>');
  bits.push('<span class="artifact-chip">' + esc(_artifactStorageKind(artifact)) + '</span>');
  if (artifact.mime_type) {
    bits.push('<span class="artifact-chip">' + esc(artifact.mime_type) + '</span>');
  }
  if (artifact.line_start || artifact.line_end) {
    var lineLabel = 'L' + esc(String(artifact.line_start || '?'));
    if (artifact.line_end && artifact.line_end !== artifact.line_start) {
      lineLabel += '-' + esc(String(artifact.line_end));
    }
    bits.push('<span class="artifact-chip">' + lineLabel + '</span>');
  }
  var stats = _artifactStatsLabel(artifact);
  if (stats) bits.push('<span class="artifact-chip">' + esc(stats) + '</span>');
  return bits.join('');
}

function _renderArtifactCard(artifact, opts) {
  opts = opts || {};
  var taskId = opts.taskId || artifact.taskId || '';
  var html = '<div class="artifact-card">';
  html += '<div class="artifact-card-head">';
  html += '<div class="artifact-card-title-wrap">';
  html += '<div class="artifact-card-title">' + esc(artifact.title || artifact.filename || 'Artifact') + '</div>';
  if (artifact.taskLabel && opts.showTaskLabel) {
    html += '<div class="artifact-card-task">' + esc(artifact.taskLabel) + '</div>';
  } else if (artifact.path) {
    html += '<div class="artifact-card-path">' + esc(artifact.path) + '</div>';
  }
  html += '</div>';
  html += '<div class="artifact-card-actions">';
  var url = _artifactFileUrl(taskId, artifact);
  if (url) {
    html += '<a class="artifact-card-action" href="' + esc(url)
      + '" onclick="event.stopPropagation();window.open(this.href);return false">Open</a>';
  }
  if (opts.onEdit) {
    html += '<button class="artifact-card-action" onclick="' + opts.onEdit + '">Edit</button>';
  }
  if (opts.onRemove) {
    html += '<button class="artifact-card-action artifact-card-action-danger" onclick="' + opts.onRemove + '">Remove</button>';
  }
  html += '</div>';
  html += '</div>';
  html += '<div class="artifact-card-meta">' + _artifactMetaHtml(artifact) + '</div>';
  if (artifact.summary) {
    html += '<div class="artifact-card-summary">' + esc(artifact.summary) + '</div>';
  }
  if (artifact.type === 'image' && url) {
    html += '<div class="artifact-card-image"><img src="' + esc(url)
      + '" alt="' + esc(artifact.title || artifact.filename || 'artifact image') + '"></div>';
  } else {
    var preview = _artifactPreviewText(artifact);
    if (preview) {
      html += '<pre class="artifact-card-preview">' + esc(preview) + '</pre>';
    }
  }
  html += '</div>';
  return html;
}

function _renderArtifactCollection(artifacts, opts) {
  opts = opts || {};
  if (!artifacts || !artifacts.length) {
    return '<div class="artifact-empty">' + esc(opts.empty || 'No artifacts attached.') + '</div>';
  }
  var html = '<div class="artifact-collection">';
  for (var i = 0; i < artifacts.length; i++) {
    html += _renderArtifactCard(artifacts[i], opts.cardOptions || {});
  }
  html += '</div>';
  return html;
}

function _artifactDraftForType(type) {
  var resolvedType = type || 'snippet';
  var storageKind = resolvedType === 'file_ref' ? 'file_ref' : 'inline';
  return _artifactNormalizeClient({
    type: resolvedType,
    title: '',
    summary: '',
    content: '',
    path: '',
    line_start: null,
    line_end: null,
    metadata: {},
    prompt: { mode: _artifactDefaultPromptMode(resolvedType) },
    storage: {
      kind: storageKind,
      path: '',
      content: '',
      line_start: null,
      line_end: null,
    },
  }, _taskArtifacts.length);
}

function _taskArtifactUploadId() {
  return _taskEditId || _taskDraftId;
}

function _artifactClone(artifact) {
  return JSON.parse(JSON.stringify(_artifactNormalizeClient(artifact, 0)));
}

function _getAllLabels() {
  var labels = {};
  for (var id in state.board_tasks) {
    var t = state.board_tasks[id];
    (t.labels || []).forEach(function(l) {
      if (!isSystemLabel(l)) labels[l] = (labels[l] || 0) + 1;
    });
  }
  return Object.keys(labels).sort(function(a, b) { return labels[b] - labels[a]; });
}

function _cloneTaskAttachments(list) {
  return (list || []).map(function(att) { return Object.assign({}, att); });
}

function _taskDraftKey(editId, draftScope) {
  return editId ? 'edit:' + editId : (draftScope || 'create');
}

function _taskScheduledInputValue(isoValue) {
  if (!isoValue) return '';
  try {
    var d = new Date(isoValue);
    return (d > new Date()) ? d.toISOString().slice(0, 16) : '';
  } catch (e) {
    return '';
  }
}

function _taskVerificationSummaryFromDom() {
  var testsEl = document.getElementById('task-verification-tests-input');
  var smokeEl = document.getElementById('task-verification-smoke-input');
  var deployNeededEl = document.getElementById('task-verification-deploy-needed-input');
  var deployAttemptedEl = document.getElementById('task-verification-deploy-attempted-input');
  var humanEl = document.getElementById('task-verification-human-input');
  var summary = {};
  var testsRun = testsEl ? testsEl.value.trim() : '';
  var humanPending = humanEl ? humanEl.value.trim() : '';
  if (testsRun) summary.tests_run = testsRun;
  if (smokeEl && smokeEl.checked) summary.manual_smoke_done = true;
  if (deployNeededEl && deployNeededEl.checked) summary.deploy_needed = true;
  if (deployAttemptedEl && deployAttemptedEl.checked) summary.deploy_attempted = true;
  if (humanPending) summary.human_validation_pending = humanPending;
  return summary;
}

function _taskVerificationSummaryValue(summary, key) {
  summary = summary || {};
  return summary[key];
}

function _taskReadDraftFromDom() {
  var modal = document.getElementById('modal-task');
  var taskEl = document.getElementById('task-task-input');
  var descEl = document.getElementById('task-description-input');
  var groupEl = document.getElementById('task-group-select');
  var actionEl = document.getElementById('task-action-select');
  var tplEl = document.getElementById('task-template-select');
  var schedEl = document.getElementById('task-scheduled-input');
  var labelsEl = document.getElementById('task-labels-input');
  var providerEl = document.getElementById('task-external-provider-input');
  var externalIdEl = document.getElementById('task-external-id-input');
  var externalUrlEl = document.getElementById('task-external-url-input');
  var verificationModeEl = document.getElementById('task-verification-mode-input');
  var verificationStateEl = document.getElementById('task-verification-state-input');
  var verificationNotesEl = document.getElementById('task-verification-notes-input');
  return {
    lane: modal ? (modal.dataset.lane || '') : '',
    task: taskEl ? taskEl.value : '',
    description: descEl ? descEl.value : '',
    group: groupEl ? groupEl.value : '',
    action_name: actionEl ? (actionEl.value || _taskSelectedAction || '') : (_taskSelectedAction || ''),
    agent_template: tplEl ? (tplEl.value || _taskSelectedTemplate || '') : (_taskSelectedTemplate || ''),
    scheduled_input: schedEl ? schedEl.value : '',
    pending_label: labelsEl ? labelsEl.value : '',
    labels: _taskLabels.slice(),
    system_labels: _taskSystemLabels.slice(),
    depends_on: _taskDeps.slice(),
    action_vars: _collectTaskActionVars(),
    attachments: _cloneTaskAttachments(_taskAttachments),
    artifacts: _taskArtifacts.map(function(artifact) {
      return _artifactClone(artifact);
    }),
    provider: providerEl ? providerEl.value : _taskExternalProvider,
    external_id: externalIdEl ? externalIdEl.value : _taskExternalId,
    external_url: externalUrlEl ? externalUrlEl.value : _taskExternalUrl,
    verification_mode: verificationModeEl ? verificationModeEl.value : '',
    verification_state: verificationStateEl ? verificationStateEl.value : '',
    verification_notes: verificationNotesEl ? verificationNotesEl.value : '',
    verification_summary: _taskVerificationSummaryFromDom(),
    draft_id: _taskDraftId || '',
  };
}

function taskPersistDraft() {
  var modal = document.getElementById('modal-task');
  if (!modal || !modal.classList.contains('visible')) return;
  _taskModalDrafts[_taskDraftKey(_taskEditId, _taskDraftScope)] = _taskReadDraftFromDom();
}

function _taskClearDraft(editId, draftScope) {
  delete _taskModalDrafts[_taskDraftKey(editId, draftScope)];
}

function _taskOpenModal(config) {
  _taskDraftScope = config.draftScope || 'create';
  var draft = _taskModalDrafts[_taskDraftKey(config.editId, _taskDraftScope)] || null;
  var taskEl = document.getElementById('task-task-input');
  var descEl = document.getElementById('task-description-input');
  var labelsEl = document.getElementById('task-labels-input');
  var schedEl = document.getElementById('task-scheduled-input');
  var groupEl = document.getElementById('task-group-select');
  var providerEl = document.getElementById('task-external-provider-input');
  var externalIdEl = document.getElementById('task-external-id-input');
  var externalUrlEl = document.getElementById('task-external-url-input');
  var verificationModeEl = document.getElementById('task-verification-mode-input');
  var verificationStateEl = document.getElementById('task-verification-state-input');
  var verificationTestsEl = document.getElementById('task-verification-tests-input');
  var verificationSmokeEl = document.getElementById('task-verification-smoke-input');
  var verificationDeployNeededEl = document.getElementById('task-verification-deploy-needed-input');
  var verificationDeployAttemptedEl = document.getElementById('task-verification-deploy-attempted-input');
  var verificationHumanEl = document.getElementById('task-verification-human-input');
  var verificationNotesEl = document.getElementById('task-verification-notes-input');
  var modal = document.getElementById('modal-task');

  _taskEditId = config.editId || null;
  _taskDraftId = config.editId ? '' : ((draft && draft.draft_id) || config.draftId || _generateDraftId());
  _taskAttachments = _cloneTaskAttachments((draft && draft.attachments) || config.attachments || []);
  _taskOriginalAttachments = _cloneTaskAttachments(config.originalAttachments || config.attachments || []);
  _taskArtifacts = ((draft && draft.artifacts) || config.artifacts || []).map(function(item, idx) {
    return _artifactNormalizeClient(item, idx);
  });
  _taskOriginalArtifacts = (config.originalArtifacts || config.artifacts || []).map(function(item, idx) {
    return _artifactNormalizeClient(item, idx);
  });
  _taskSelectedAction = draft && draft.action_name !== undefined ? draft.action_name : (config.actionName || '');
  _taskSelectedTemplate = draft && draft.agent_template !== undefined ? draft.agent_template : (config.agentTemplate || '');
  _taskActionVars = [];
  _taskActionVarValues = Object.assign({}, (draft && draft.action_vars) || config.actionVars || {});
  _taskExternalProvider = draft && draft.provider !== undefined ? draft.provider : (config.provider || '');
  _taskExternalId = draft && draft.external_id !== undefined ? draft.external_id : (config.externalId || '');
  _taskExternalUrl = draft && draft.external_url !== undefined ? draft.external_url : (config.externalUrl || '');
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;

  document.getElementById('task-modal-title').textContent = config.title;
  document.getElementById('task-submit-btn').textContent = config.submitLabel;

  taskEl.value = draft && draft.task !== undefined ? draft.task : (config.task || '');
  descEl.value = draft && draft.description !== undefined ? draft.description : (config.description || '');
  if (schedEl) {
    schedEl.value = draft && draft.scheduled_input !== undefined
      ? draft.scheduled_input
      : (config.scheduledInput || '');
  }

  _setTaskLabels(
    draft
      ? (draft.labels || []).concat(draft.system_labels || [])
      : (config.labels || [])
  );
  if (labelsEl) labelsEl.value = draft && draft.pending_label !== undefined ? draft.pending_label : '';
  _setTaskDeps(draft && draft.depends_on ? draft.depends_on : (config.dependsOn || []));
  document.getElementById('task-action-vars').innerHTML = '';
  if (providerEl) providerEl.value = _taskExternalProvider;
  if (externalIdEl) externalIdEl.value = _taskExternalId;
  if (externalUrlEl) externalUrlEl.value = _taskExternalUrl;
  var verificationSummary = draft && draft.verification_summary !== undefined
    ? (draft.verification_summary || {})
    : (config.verificationSummary || {});
  if (verificationModeEl) {
    verificationModeEl.value = draft && draft.verification_mode !== undefined
      ? draft.verification_mode
      : (config.verificationMode || '');
  }
  if (verificationStateEl) {
    verificationStateEl.value = draft && draft.verification_state !== undefined
      ? draft.verification_state
      : (config.verificationState || '');
  }
  if (verificationTestsEl) {
    verificationTestsEl.value = _taskVerificationSummaryValue(
      verificationSummary, 'tests_run'
    ) || '';
  }
  if (verificationSmokeEl) {
    verificationSmokeEl.checked = !!_taskVerificationSummaryValue(
      verificationSummary, 'manual_smoke_done'
    );
  }
  if (verificationDeployNeededEl) {
    verificationDeployNeededEl.checked = !!_taskVerificationSummaryValue(
      verificationSummary, 'deploy_needed'
    );
  }
  if (verificationDeployAttemptedEl) {
    verificationDeployAttemptedEl.checked = !!_taskVerificationSummaryValue(
      verificationSummary, 'deploy_attempted'
    );
  }
  if (verificationHumanEl) {
    verificationHumanEl.value = _taskVerificationSummaryValue(
      verificationSummary, 'human_validation_pending'
    ) || '';
  }
  if (verificationNotesEl) {
    verificationNotesEl.value = draft
      && draft.verification_notes !== undefined
      ? draft.verification_notes
      : (config.verificationNotes || '');
  }
  _renderTaskAttachments();
  _renderTaskArtifacts();
  _renderTaskArtifactEditor();
  _renderTaskActionVars();

  var group = draft && draft.group ? draft.group : (config.group || _currentGroup());
  _populateTaskGroupSelect(group);
  if (groupEl) groupEl.value = group;
  modal.dataset.lane = draft && draft.lane !== undefined ? draft.lane : (config.lane || '');

  _taskModalWaiting = true;
  _taskTemplateWaiting = true;
  send({ cmd: 'list_actions', group: (groupEl && groupEl.value) || _currentGroup() });
  send({ cmd: 'list_templates', group: (groupEl && groupEl.value) || _currentGroup() });

  modal.classList.add('visible');
  taskAutoResize(taskEl);
  taskAutoResize(descEl);
  taskEl.focus();
  if (config.selectTask) taskEl.select();
  taskPersistDraft();
}

function taskLabelsSearch(e) {
  var val = e.target.value.trim().toLowerCase();
  var dropdown = document.getElementById('task-labels-dropdown');
  taskPersistDraft();
  if (!val) { dropdown.style.display = 'none'; _labelDropdownIdx = -1; return; }
  var all = _getAllLabels();
  var html = '';
  var count = 0;
  for (var i = 0; i < all.length; i++) {
    if (_taskLabels.indexOf(all[i]) >= 0) continue;
    if (all[i].toLowerCase().indexOf(val) < 0) continue;
    html += '<div class="deps-option" onmousedown="event.preventDefault()" onclick="taskPickLabel(\'' + esc(all[i]).replace(/'/g, "\\'") + '\')">'
      + esc(all[i]) + '</div>';
    count++;
    if (count >= 8) break;
  }
  dropdown.innerHTML = html;
  dropdown.style.display = count ? '' : 'none';
  _labelDropdownIdx = -1;
}

function taskPickLabel(label) {
  if (_taskLabels.indexOf(label) < 0) _taskLabels.push(label);
  var input = document.getElementById('task-labels-input');
  input.value = '';
  document.getElementById('task-labels-dropdown').style.display = 'none';
  _labelDropdownIdx = -1;
  _renderTaskLabelChips();
  taskPersistDraft();
  input.focus();
}

function _highlightLabelOption(idx) {
  var dropdown = document.getElementById('task-labels-dropdown');
  var opts = dropdown.querySelectorAll('.deps-option');
  for (var i = 0; i < opts.length; i++) opts[i].classList.toggle('active', i === idx);
  _labelDropdownIdx = idx;
}

function taskLabelsKeydown(e) {
  var dropdown = document.getElementById('task-labels-dropdown');
  var visible = dropdown && dropdown.style.display !== 'none';
  var opts = visible ? dropdown.querySelectorAll('.deps-option') : [];

  if (e.key === 'Escape') {
    if (visible) { dropdown.style.display = 'none'; _labelDropdownIdx = -1; e.stopPropagation(); return; }
    closeModals(); return;
  }
  if (visible && opts.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _highlightLabelOption((_labelDropdownIdx + 1) % opts.length);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      _highlightLabelOption((_labelDropdownIdx - 1 + opts.length) % opts.length);
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      if (_labelDropdownIdx >= 0 && _labelDropdownIdx < opts.length) {
        opts[_labelDropdownIdx].click();
      } else {
        var val = e.target.value.trim();
        if (val && _taskLabels.indexOf(val) < 0) _taskLabels.push(val);
        e.target.value = '';
        dropdown.style.display = 'none';
        _labelDropdownIdx = -1;
        _renderTaskLabelChips();
        taskPersistDraft();
      }
      return;
    }
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    var val = e.target.value.trim();
    if (!val) return;
    if (_taskLabels.indexOf(val) < 0) _taskLabels.push(val);
    e.target.value = '';
    _renderTaskLabelChips();
    taskPersistDraft();
  }
}

function taskRemoveLabel(idx) {
  _taskLabels.splice(idx, 1);
  _renderTaskLabelChips();
  taskPersistDraft();
}

function _renderTaskLabelChips() {
  var container = document.getElementById('task-labels-chips');
  if (!container) return;
  var html = '';
  for (var i = 0; i < _taskLabels.length; i++) {
    var lc = labelColor(_taskLabels[i]);
    html += '<span class="label-chip" style="background:' + lc + '22;color:' + lc + '">' + esc(_taskLabels[i])
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
  taskPersistDraft();
}

function taskRemoveDep(idx) {
  _taskDeps.splice(idx, 1);
  _renderTaskDepChips();
  taskPersistDraft();
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

/* -- Task modal: artifact helpers ---------------------------------------- */

function _inferArtifactTypeFromFile(name, mime) {
  var lowerName = String(name || '').toLowerCase();
  var lowerMime = String(mime || '').toLowerCase();
  if (lowerMime.indexOf('image/') === 0) return 'image';
  if (/\.(diff|patch)$/.test(lowerName)) return 'diff';
  if (/(^|[._-])(pytest|junit|tap|coverage|report|results?)([._-]|$)/.test(lowerName)) {
    return 'test_report';
  }
  if (/\.(log|out|err|trace|txt)$/.test(lowerName)) return 'log';
  if (/\.(md|markdown|html|htm|json|yaml|yml|xml|csv)$/.test(lowerName)) {
    return 'generated_doc';
  }
  if (lowerMime.indexOf('text/') === 0 || lowerMime.indexOf('json') >= 0 || lowerMime.indexOf('xml') >= 0) {
    return 'generated_doc';
  }
  return 'file_ref';
}

async function _artifactReadPreviewText(file) {
  if (!file || typeof file.text !== 'function' || file.size > 262144) return '';
  try {
    return await file.text();
  } catch (err) {
    return '';
  }
}

function _artifactSummaryFromFile(file, type) {
  var parts = [];
  if (file && typeof file.size === 'number') {
    if (file.size >= 1024 * 1024) parts.push((file.size / (1024 * 1024)).toFixed(1) + ' MB');
    else if (file.size >= 1024) parts.push(Math.round(file.size / 1024) + ' KB');
    else parts.push(file.size + ' B');
  }
  if (type === 'diff') parts.push('uploaded patch');
  else if (type === 'log') parts.push('uploaded log');
  else if (type === 'test_report') parts.push('uploaded report');
  return parts.join(' | ');
}

function _artifactFromUploadedFile(entry, file, content) {
  var type = _inferArtifactTypeFromFile(entry.filename || file.name || '', entry.mime_type || file.type || '');
  var normalized = _artifactNormalizeClient({
    type: type,
    title: entry.filename || file.name || 'artifact',
    filename: entry.filename || file.name || '',
    path: entry.path || '',
    mime_type: entry.mime_type || file.type || '',
    summary: _artifactSummaryFromFile(file, type),
    content: _artifactIsTextLike({ type: type, mime_type: entry.mime_type || file.type || '', content: content || '' }) ? (content || '') : '',
    prompt: { mode: _artifactDefaultPromptMode(type) },
    metadata: {
      size_bytes: entry.size_bytes || file.size || 0,
    },
    storage: {
      kind: type === 'file_ref' ? 'file_ref' : 'path',
      path: entry.path || '',
      content: _artifactIsTextLike({ type: type, mime_type: entry.mime_type || file.type || '', content: content || '' }) ? (content || '') : '',
      line_start: null,
      line_end: null,
    },
    lifecycle: { owner: 'task', cleanup: 'delete_with_task' },
  }, _taskArtifacts.length);
  normalized.taskId = _taskArtifactUploadId();
  return normalized;
}

function _renderTaskArtifacts() {
  var container = document.getElementById('task-artifacts-list');
  if (!container) return;
  var artifacts = [];
  for (var i = 0; i < _taskArtifacts.length; i++) {
    var item = _artifactNormalizeClient(_taskArtifacts[i], i);
    item.taskId = item.taskId || _taskArtifactUploadId();
    artifacts.push(item);
  }
  if (!artifacts.length) {
    container.innerHTML = '<div class="artifact-empty">No logs, reports, diffs, or references yet.</div>';
    return;
  }
  var html = '<div class="artifact-collection">';
  for (var j = 0; j < artifacts.length; j++) {
    html += _renderArtifactCard(artifacts[j], {
      taskId: _taskArtifactUploadId(),
      onEdit: 'taskArtifactEdit(' + j + ')',
      onRemove: 'taskArtifactRemove(' + j + ')',
    });
  }
  html += '</div>';
  container.innerHTML = html;
}

function _renderTaskArtifactEditor() {
  var container = document.getElementById('task-artifact-editor');
  if (!container) return;
  if (_taskArtifactEditIndex < 0 || !_taskArtifactDraft) {
    container.innerHTML = '';
    container.classList.remove('visible');
    return;
  }
  var draft = _taskArtifactDraft;
  var html = '<div class="task-artifact-editor-card">';
  html += '<div class="task-artifact-editor-title">'
    + (_taskArtifactEditIndex < _taskArtifacts.length ? 'Edit artifact' : 'New artifact')
    + '</div>';
  html += '<div class="task-artifact-editor-grid">';
  html += '<label>Type</label>';
  html += '<select onchange="taskArtifactDraftChange(\'type\', this.value)">';
  var types = ['snippet', 'log', 'diff', 'test_report', 'generated_doc', 'file_ref', 'image'];
  for (var i = 0; i < types.length; i++) {
    html += '<option value="' + esc(types[i]) + '"' + (draft.type === types[i] ? ' selected' : '') + '>'
      + esc(_artifactTypeLabel(types[i])) + '</option>';
  }
  html += '</select>';
  html += '<label>Title</label>';
  html += '<input value="' + esc(draft.title || '') + '" oninput="taskArtifactDraftChange(\'title\', this.value)" placeholder="e.g. pytest log">';
  html += '<label>Summary</label>';
  html += '<textarea rows="2" oninput="taskArtifactDraftChange(\'summary\', this.value);taskAutoResize(this)" placeholder="What this artifact is for...">'
    + esc(draft.summary || '') + '</textarea>';
  html += '<label>Prompt mode</label>';
  html += '<select onchange="taskArtifactDraftChange(\'prompt_mode\', this.value)">';
  var promptModes = ['auto', 'path', 'summary', 'inline', 'none'];
  var currentPrompt = ((draft.prompt || {}).mode) || _artifactDefaultPromptMode(draft.type);
  for (var j = 0; j < promptModes.length; j++) {
    html += '<option value="' + esc(promptModes[j]) + '"' + (currentPrompt === promptModes[j] ? ' selected' : '') + '>'
      + esc(_artifactPromptModeLabels[promptModes[j]]) + '</option>';
  }
  html += '</select>';
  html += '<label>Path</label>';
  html += '<input value="' + esc(draft.path || '') + '" oninput="taskArtifactDraftChange(\'path\', this.value)" placeholder="/repo/path/to/file.log">';
  html += '<label>Line start</label>';
  html += '<input value="' + esc(draft.line_start || '') + '" oninput="taskArtifactDraftChange(\'line_start\', this.value)" placeholder="12">';
  html += '<label>Line end</label>';
  html += '<input value="' + esc(draft.line_end || '') + '" oninput="taskArtifactDraftChange(\'line_end\', this.value)" placeholder="24">';
  html += '<label>Content</label>';
  html += '<textarea rows="6" oninput="taskArtifactDraftChange(\'content\', this.value);taskAutoResize(this)" placeholder="Paste a useful excerpt, command output, or report summary...">'
    + esc(draft.content || '') + '</textarea>';
  html += '</div>';
  html += '<div class="modal-actions">';
  html += '<button class="btn-cancel" type="button" onclick="taskArtifactCancelEdit()">Cancel</button>';
  html += '<button class="btn-primary" type="button" onclick="taskArtifactSave()">Save Artifact</button>';
  html += '</div></div>';
  container.innerHTML = html;
  container.classList.add('visible');
  container.querySelectorAll('textarea').forEach(taskAutoResize);
}

function taskArtifactPickFiles() {
  var input = document.getElementById('task-artifact-upload-input');
  if (input) input.click();
}

async function _taskUploadArtifactFiles(files) {
  var tid = _taskArtifactUploadId();
  if (!tid || !files || !files.length) return;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    var fd = new FormData();
    fd.append('task_id', tid);
    fd.append('file', file);
    try {
      var parts = await Promise.all([
        fetch('/api/upload', { method: 'POST', body: fd }).then(function(r) { return r.json(); }),
        _artifactReadPreviewText(file),
      ]);
      var response = parts[0];
      var previewText = parts[1];
      if (response.ok && response.data && response.data.length) {
        for (var j = 0; j < response.data.length; j++) {
          _taskArtifacts.push(_artifactFromUploadedFile(response.data[j], file, previewText));
        }
      }
    } catch (err) {
      // Keep failure silent in the UI flow for now; upload endpoint errors surface in logs.
    }
  }
  _renderTaskArtifacts();
}

function taskArtifactFilePicked(input) {
  var files = input && input.files ? Array.from(input.files) : [];
  input.value = '';
  _taskUploadArtifactFiles(files);
}

function taskArtifactStart(type) {
  _taskArtifactEditIndex = _taskArtifacts.length;
  _taskArtifactDraft = _artifactDraftForType(type);
  _renderTaskArtifactEditor();
}

function taskArtifactEdit(index) {
  var artifact = _taskArtifacts[index];
  if (!artifact) return;
  _taskArtifactEditIndex = index;
  _taskArtifactDraft = _artifactClone(artifact);
  _renderTaskArtifactEditor();
}

function taskArtifactDraftChange(field, value) {
  if (!_taskArtifactDraft) return;
  if (field === 'type') {
    _taskArtifactDraft.type = value || 'snippet';
    if (!_taskArtifactDraft.title && _taskArtifactDraft.filename) {
      _taskArtifactDraft.title = _taskArtifactDraft.filename;
    }
    _taskArtifactDraft.prompt = _taskArtifactDraft.prompt || {};
    _taskArtifactDraft.prompt.mode = _artifactDefaultPromptMode(_taskArtifactDraft.type);
    _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
    _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
  } else if (field === 'prompt_mode') {
    _taskArtifactDraft.prompt = _taskArtifactDraft.prompt || {};
    _taskArtifactDraft.prompt.mode = value || _artifactDefaultPromptMode(_taskArtifactDraft.type);
  } else if (field === 'line_start' || field === 'line_end') {
    _taskArtifactDraft[field] = value === '' ? null : Number(value);
    _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
    _taskArtifactDraft.storage[field] = _taskArtifactDraft[field];
  } else {
    _taskArtifactDraft[field] = value;
    if (field === 'path' || field === 'content') {
      _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
      _taskArtifactDraft.storage[field] = value;
      _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
    }
  }
}

function taskArtifactCancelEdit() {
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;
  _renderTaskArtifactEditor();
}

function taskArtifactSave() {
  if (!_taskArtifactDraft) return;
  _taskArtifactDraft.storage = _taskArtifactDraft.storage || {};
  _taskArtifactDraft.storage.kind = _artifactStorageKind(_taskArtifactDraft);
  var artifact = _artifactNormalizeClient(_taskArtifactDraft, _taskArtifactEditIndex);
  artifact.taskId = _taskArtifactUploadId();
  if (!artifact.title) {
    artifact.title = artifact.filename || (artifact.path ? artifact.path.split(/[\\/]/).pop() : _artifactTypeLabel(artifact.type));
  }
  if (artifact.type === 'file_ref' && !artifact.path) return;
  if (!artifact.title && !artifact.path && !artifact.content) return;
  if (_taskArtifactEditIndex >= _taskArtifacts.length) _taskArtifacts.push(artifact);
  else _taskArtifacts[_taskArtifactEditIndex] = artifact;
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;
  _renderTaskArtifacts();
  _renderTaskArtifactEditor();
}

function _artifactOwnedUpload(artifact) {
  artifact = artifact || {};
  return !!(artifact.filename && ((artifact.lifecycle || {}).owner || 'task') === 'task');
}

function taskArtifactRemove(index) {
  var artifact = _taskArtifacts[index];
  if (!artifact) return;
  _taskArtifacts.splice(index, 1);
  if (_taskArtifactEditIndex === index) {
    _taskArtifactEditIndex = -1;
    _taskArtifactDraft = null;
  } else if (_taskArtifactEditIndex > index) {
    _taskArtifactEditIndex--;
  }
  _renderTaskArtifacts();
  _renderTaskArtifactEditor();
}

function _artifactRemovedFiles(originalArtifacts, currentArtifacts) {
  var keep = {};
  for (var i = 0; i < currentArtifacts.length; i++) {
    var current = currentArtifacts[i];
    if (_artifactOwnedUpload(current) && current.filename) keep[current.filename] = true;
  }
  var removed = [];
  for (var j = 0; j < originalArtifacts.length; j++) {
    var original = originalArtifacts[j];
    if (_artifactOwnedUpload(original) && original.filename && !keep[original.filename]) {
      removed.push(original.filename);
    }
  }
  return removed;
}

function openTaskArtifactBrowser(taskId) {
  var tasks = (state && state.board_tasks) || {};
  var task = tasks[taskId];
  if (!task) return;
  var artifacts = _taskArtifactsCombined(task);
  document.getElementById('task-artifacts-modal-title').textContent = 'Artifacts - ' + (task.task || '').slice(0, 80);
  document.getElementById('task-artifacts-modal-summary').textContent =
    artifacts.length ? (artifacts.length + ' artifact' + (artifacts.length === 1 ? '' : 's')) : 'No artifacts attached.';
  document.getElementById('task-artifacts-modal-content').innerHTML = _renderArtifactCollection(artifacts, {
    empty: 'No artifacts attached to this task.',
    cardOptions: { taskId: task.id },
  });
  document.getElementById('modal-task-artifacts').classList.add('visible');
}

/* -- Task modal: attachment helpers -------------------------------------- */

function _generateDraftId() {
  var hex = '';
  for (var i = 0; i < 8; i++) hex += Math.floor(Math.random() * 16).toString(16);
  return hex;
}

function _taskAttId() {
  return _taskEditId || _taskDraftId;
}

function _uploadFiles(files) {
  var tid = _taskAttId();
  if (!tid) return;
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    if (!file.type.startsWith('image/')) continue;
    var fd = new FormData();
    fd.append('task_id', tid);
    fd.append('file', file);
    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(res) {
        if (res.ok && res.data) {
          for (var j = 0; j < res.data.length; j++) {
            _taskAttachments.push(res.data[j]);
          }
          _renderTaskAttachments();
          taskPersistDraft();
        }
      });
  }
}

function taskAttFilePicked(input) {
  if (input.files && input.files.length) _uploadFiles(input.files);
  input.value = '';
}

function taskAttDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}

function taskAttDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

function taskAttDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
    _uploadFiles(e.dataTransfer.files);
  }
}

function taskAttRemove(idx) {
  var att = _taskAttachments[idx];
  if (!att) return;
  _taskAttachments.splice(idx, 1);
  _renderTaskAttachments();
  taskPersistDraft();
}

function _renderTaskAttachments() {
  var container = document.getElementById('task-attachments-thumbs');
  if (!container) return;
  var html = '';
  var tid = _taskAttId();
  for (var i = 0; i < _taskAttachments.length; i++) {
    var a = _taskAttachments[i];
    var src = '/attachments/' + encodeURIComponent(tid) + '/' + encodeURIComponent(a.filename);
    html += '<div class="attachment-thumb">'
      + '<img src="' + src + '" alt="' + esc(a.filename) + '" title="' + esc(a.filename) + '">'
      + '<button class="attachment-remove" onclick="taskAttRemove(' + i + ')">&times;</button>'
      + '</div>';
  }
  container.innerHTML = html;
}

function _cleanupDraftAttachments() {
  var hasDraftUploads = _taskAttachments.length > 0;
  if (!hasDraftUploads) {
    for (var di = 0; di < _taskArtifacts.length; di++) {
      if (_artifactOwnedUpload(_taskArtifacts[di])) {
        hasDraftUploads = true;
        break;
      }
    }
  }
  if (_taskDraftId && hasDraftUploads && !_taskEditId) {
    // Create mode: wipe the whole draft dir
    fetch('/api/upload/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: _taskDraftId })
    });
  } else if (_taskEditId) {
    // Edit mode: remove newly uploaded files not in original set
    var origNames = {};
    for (var i = 0; i < _taskOriginalAttachments.length; i++) {
      origNames[_taskOriginalAttachments[i].filename] = true;
    }
    for (var i = 0; i < _taskAttachments.length; i++) {
      if (!origNames[_taskAttachments[i].filename]) {
        send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: _taskAttachments[i].filename });
      }
    }
    var origArtifactNames = {};
    for (var j = 0; j < _taskOriginalArtifacts.length; j++) {
      if (_artifactOwnedUpload(_taskOriginalArtifacts[j]) && _taskOriginalArtifacts[j].filename) {
        origArtifactNames[_taskOriginalArtifacts[j].filename] = true;
      }
    }
    for (var k = 0; k < _taskArtifacts.length; k++) {
      if (_artifactOwnedUpload(_taskArtifacts[k]) && _taskArtifacts[k].filename
          && !origArtifactNames[_taskArtifacts[k].filename]) {
        send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: _taskArtifacts[k].filename });
      }
    }
  }
}

function openAddTask(lane) {
  _taskOpenModal({
    editId: null,
    title: 'New Task',
    submitLabel: 'Create',
    task: '',
    description: '',
    labels: [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    artifacts: [],
    originalArtifacts: [],
    actionName: '',
    agentTemplate: '',
    actionVars: {},
    group: _currentGroup(),
    lane: lane || '',
    scheduledInput: '',
    verificationMode: '',
    verificationState: '',
    verificationNotes: '',
    verificationSummary: {},
    draftId: _generateDraftId(),
    selectTask: false,
  });
}

function openEditTask(taskId) {
  var tasks = (state && state.board_tasks) || {};
  var t = tasks[taskId];
  if (!t) return;
  _taskOpenModal({
    editId: taskId,
    title: 'Edit Task',
    submitLabel: 'Save',
    task: t.task || '',
    description: t.description || '',
    labels: t.labels || [],
    dependsOn: t.depends_on || [],
    attachments: t.attachments || [],
    originalAttachments: t.attachments || [],
    artifacts: t.artifacts || [],
    originalArtifacts: t.artifacts || [],
    actionName: t.action_name || '',
    agentTemplate: t.agent_template || '',
    actionVars: t.action_vars || {},
    group: t.group || _currentGroup(),
    lane: t.lane || '',
    provider: t.provider || '',
    externalId: t.external_id || '',
    externalUrl: t.external_url || '',
    scheduledInput: _taskScheduledInputValue(t.scheduled_at),
    verificationMode: t.verification_mode || '',
    verificationState: t.verification_state || '',
    verificationNotes: t.verification_notes || '',
    verificationSummary: t.verification_summary || {},
    selectTask: true,
  });
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
  var providerEl = document.getElementById('task-external-provider-input');
  var externalIdEl = document.getElementById('task-external-id-input');
  var externalUrlEl = document.getElementById('task-external-url-input');
  _taskExternalProvider = providerEl ? providerEl.value.trim() : '';
  _taskExternalId = externalIdEl ? externalIdEl.value.trim() : '';
  _taskExternalUrl = externalUrlEl ? externalUrlEl.value.trim() : '';
  // Include any text still in the input as a label
  var pendingLabel = document.getElementById('task-labels-input').value.trim();
  if (pendingLabel && _taskLabels.indexOf(pendingLabel) < 0) _taskLabels.push(pendingLabel);
  var labels = _taskLabels.concat(_taskSystemLabels);
  var actionVars = _collectTaskActionVars();

  var schedVal = (document.getElementById('task-scheduled-input') || {}).value || '';
  var scheduledAt = schedVal ? new Date(schedVal).toISOString() : '';
  var verificationMode = (document.getElementById('task-verification-mode-input') || {}).value || '';
  var verificationState = (document.getElementById('task-verification-state-input') || {}).value || '';
  var verificationNotes = ((document.getElementById('task-verification-notes-input') || {}).value || '').trim();
  var verificationSummary = _taskVerificationSummaryFromDom();
  var existingTask = _taskEditId && state && state.board_tasks
    ? state.board_tasks[_taskEditId]
    : null;
  var shouldIncludeVerification = !!(
    verificationMode || verificationState || verificationNotes
    || Object.keys(verificationSummary).length
    || (existingTask && (
      existingTask.verification_mode
      || existingTask.verification_state
      || existingTask.verification_notes
      || (existingTask.verification_summary
          && Object.keys(existingTask.verification_summary).length)
    ))
  );
  var draftKeyId = _taskEditId;
  var draftScope = _taskDraftScope;

  if (_taskEditId) {
    // Edit mode
    var msg = { cmd: 'board_update_task', id: _taskEditId, task: task, group: group, description: description };
    msg.action_name = _taskSelectedAction;
    msg.agent_template = _taskSelectedTemplate;
    msg.action_vars = actionVars;
    msg.labels = labels;
    msg.scheduled_at = scheduledAt;
    msg.depends_on = _taskDeps.slice();
    msg.attachments = _taskAttachments.slice();
    msg.provider = _taskExternalProvider;
    msg.external_id = _taskExternalId;
    msg.external_url = _taskExternalUrl;
    msg.artifacts = _taskArtifacts.slice();
    if (shouldIncludeVerification) {
      msg.verification_mode = verificationMode;
      msg.verification_state = verificationState;
      msg.verification_notes = verificationNotes;
      msg.verification_summary = verificationSummary;
    }
    send(msg);
    var keepAttachmentNames = {};
    for (var ai = 0; ai < _taskAttachments.length; ai++) {
      keepAttachmentNames[_taskAttachments[ai].filename] = true;
    }
    for (var aj = 0; aj < _taskOriginalAttachments.length; aj++) {
      var oldAtt = _taskOriginalAttachments[aj];
      if (oldAtt.filename && !keepAttachmentNames[oldAtt.filename]) {
        send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: oldAtt.filename });
      }
    }
    var removedArtifactFiles = _artifactRemovedFiles(_taskOriginalArtifacts, _taskArtifacts);
    for (var ak = 0; ak < removedArtifactFiles.length; ak++) {
      send({ cmd: 'remove_attachment', task_id: _taskEditId, filename: removedArtifactFiles[ak] });
    }
  } else {
    // Create mode
    var lane = document.getElementById('modal-task').dataset.lane || '';
    var msg = { cmd: 'board_add_task', task: task, group: group, lane: lane };
    if (_taskDraftId) msg.id = _taskDraftId;
    if (description) msg.description = description;
    if (_taskSelectedAction) msg.action_name = _taskSelectedAction;
    if (_taskSelectedTemplate) msg.agent_template = _taskSelectedTemplate;
    if (Object.keys(actionVars).length) msg.action_vars = actionVars;
    if (labels.length) msg.labels = labels;
    if (scheduledAt) msg.scheduled_at = scheduledAt;
    if (_taskDeps.length) msg.depends_on = _taskDeps.slice();
    if (_taskAttachments.length) msg.attachments = _taskAttachments.slice();
    if (_taskExternalProvider) msg.provider = _taskExternalProvider;
    if (_taskExternalId) msg.external_id = _taskExternalId;
    if (_taskExternalUrl) msg.external_url = _taskExternalUrl;
    if (_taskArtifacts.length) msg.artifacts = _taskArtifacts.slice();
    if (verificationMode) msg.verification_mode = verificationMode;
    if (verificationState) msg.verification_state = verificationState;
    if (verificationNotes) msg.verification_notes = verificationNotes;
    if (Object.keys(verificationSummary).length) {
      msg.verification_summary = verificationSummary;
    }
    send(msg);
  }

  _taskClearDraft(draftKeyId, draftScope);
  _taskDraftId = '';
  _taskAttachments = [];
  _taskOriginalAttachments = [];
  _taskArtifacts = [];
  _taskOriginalArtifacts = [];
  _taskEditId = null;
  _taskDraftScope = 'create';
  _taskArtifactEditIndex = -1;
  _taskArtifactDraft = null;
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
  taskPersistDraft();
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
          + '" oninput="taskAutoResize(this);taskPersistDraft()"'
          + ' onkeydown="if(event.key===\'Escape\')closeModals();">' + esc(val) + '</textarea>';
  }
  html += '</fieldset>';
  container.innerHTML = html;
  // Auto-resize pre-filled textareas
  container.querySelectorAll('textarea').forEach(taskAutoResize);
  taskPersistDraft();
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
    attachments: _taskAttachments.slice(),
    artifacts: _taskArtifacts.slice(),
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

function copyPromptPreview() {
  var text = document.getElementById('prompt-preview-content').textContent;
  navigator.clipboard.writeText(text).then(function() {
    var btn = document.getElementById('prompt-preview-copy-btn');
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
  });
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
  document.getElementById('gls-focus-on-click').checked =
    s.focus_on_click || false;
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
    focus_on_click: document.getElementById('gls-focus-on-click').checked,
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

let addCellMode = 'agent';
let _pendingModal = null;
let _selectedColor = '';
let _selectedIcon = '';
let _pendingParentId = '';
let _addModalConfig = null;
let _addTemplateApplied = '';

function _standaloneCreateFlowEnabled() {
  return !!(state && state.runtime && state.runtime.embedded_terminal);
}

function _useCompactStandaloneAddFlow(mode) {
  return _standaloneCreateFlowEnabled()
    && (mode === 'agent' || mode === 'worker');
}

function _setAddModalSummary(text) {
  const summary = document.getElementById('modal-add-summary');
  if (!summary) return;
  summary.textContent = text || '';
  summary.classList.toggle('hidden', !text);
}

function _setAddAdvancedState(mode) {
  const details = document.getElementById('add-advanced-details');
  const summary = document.getElementById('add-advanced-summary');
  if (!details || !summary) return;
  const isTerminal = mode === 'terminal';
  const expand = isTerminal
    || !_useCompactStandaloneAddFlow(mode)
    || !!(_pendingModal && _pendingModal.advanced);
  summary.textContent = isTerminal ? 'Terminal options' : 'Advanced options';
  details.open = expand;
}

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
function _openAddModal(mode, group, parentId, templateName, options) {
  _pendingModal = {
    mode,
    group,
    parentId: parentId || '',
    template: templateName || '',
    advanced: !!(options && options.advanced),
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
  const compactStandalone = _useCompactStandaloneAddFlow(mode);
  const isTerminal = mode === 'terminal';
  const isWorker = mode === 'worker';
  const isAgentLike = !isTerminal;

  const parent = _pendingParentId ? state.agents[_pendingParentId] : null;
  document.getElementById('modal-add-title').textContent =
    parent ? `New Terminal for ${parent.name}` :
    isWorker ? 'New Detached Worker' :
    mode === 'agent' ? 'New Agent' : 'New Terminal';
  document.getElementById('add-submit-btn').textContent =
    isWorker ? 'Create Worker' :
    mode === 'agent' ? 'Create Agent' : 'Create Terminal';

  const cmdRow = document.getElementById('add-cmd-row');
  const modelRow = document.getElementById('add-model-row');
  const reasoningRow = document.getElementById('add-reasoning-row');
  const argsRow = document.getElementById('add-args-row');
  const initRow = document.getElementById('add-init-row');
  const iconRow = document.getElementById('add-icon-row');
  const providerRow = document.getElementById('add-provider-row');
  const templateRow = document.getElementById('add-template-row');
  if (isTerminal) {
    cmdRow.classList.remove('hidden');
    modelRow.classList.add('hidden');
    reasoningRow.classList.add('hidden');
    argsRow.classList.remove('hidden');
    initRow.classList.remove('hidden');
    iconRow.classList.add('hidden');
    providerRow.classList.add('hidden');
    templateRow.classList.add('hidden');
  } else {
    cmdRow.classList.add('hidden');
    modelRow.classList.add('hidden');
    reasoningRow.classList.add('hidden');
    argsRow.classList.add('hidden');
    initRow.classList.add('hidden');
    iconRow.classList.remove('hidden');
    providerRow.classList.remove('hidden');
    templateRow.classList.remove('hidden');
    _renderIconPicker('add-icon-picker', '', 'selectIcon');
  }
  _setAddAdvancedState(mode);

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
  const isAgent = isAgentLike;
  const prefix = isAgentLike ? '' : gs.terminal_name_prefix;
  const nameInput = document.getElementById('add-name-input');
  if (isTerminal) {
    nameInput.value = prefix ? _nextName(prefix) : '';
  } else if (_pendingModal && _pendingModal.template) {
    nameInput.value = '';
  } else if (compactStandalone) {
    nameInput.value = _nextName(isWorker ? 'Worker' : 'Agent');
  } else {
    nameInput.value = '';
  }
  nameInput.placeholder = isTerminal ? 'e.g. Shell'
    : isWorker ? 'e.g. Reviewer'
    : 'e.g. Claude 1';
  if (isWorker) {
    _setAddModalSummary(
      'Creates a user-owned detached worker in this group. It will not be attached to an engineer.'
    );
  } else if (compactStandalone) {
    _setAddModalSummary('Uses this group’s defaults for CLI, shell, directory, environment, and worktree unless you expand Advanced.');
  } else if (isTerminal) {
    _setAddModalSummary('Terminal sessions inherit this group’s shell, directory, and profile defaults unless you override them here.');
  } else {
    _setAddModalSummary('');
  }

  if (isTerminal) {
    document.getElementById('add-cmd-input').value = gs.terminal_boot_command || '';
    document.getElementById('add-args-input').value = gs.terminal_command_args || '';
    document.getElementById('add-init-input').value = gs.terminal_init_script || '';
  } else {
    _populateTemplateSelect('add-template-select',
      (_pendingModal && _pendingModal.template) || '', 'Group default');
    _populateProviderSelect('add-provider-select', resolved.provider || gs.agent_provider || '', true);
    document.getElementById('add-cmd-input').value = resolved.command || '';
    document.getElementById('add-model-input').value = resolved.model || '';
    document.getElementById('add-reasoning-effort').value = resolved.reasoning_effort || '';
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
    document.getElementById('add-wt-name').value = '';
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
  if (compactStandalone || isTerminal) {
    document.getElementById('add-name-input').select();
  }
}

function _applyRenderedAddTemplate(config, templateName) {
  if (!config || (addCellMode !== 'agent' && addCellMode !== 'worker')) return;
  _addTemplateApplied = templateName || '';
  document.getElementById('add-provider-select').value = config.provider || '';
  document.getElementById('add-cmd-input').value = config.command || '';
  document.getElementById('add-model-input').value = config.model || '';
  document.getElementById('add-reasoning-effort').value = config.reasoning_effort || '';
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
  if (addCellMode !== 'agent' && addCellMode !== 'worker') return;
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
function openAddAgent(group, templateName) {
  _openAddModal('agent', group, '', templateName || '', { advanced: false });
}
function openAddAgentAdvanced(group, templateName) {
  _openAddModal('agent', group, '', templateName || '', { advanced: true });
}
function openAddWorkerModal(group, templateName) {
  _openAddModal('worker', group, '', templateName || '', { advanced: false });
}
function openAddTerminal(group, parentId) { _openAddModal('terminal', group, parentId, '', { advanced: true }); }

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
    cmd: addCellMode === 'worker'
      ? 'add_worker'
      : addCellMode === 'agent' ? 'add_agent' : 'add_terminal',
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
    const model = document.getElementById('add-model-input').value.trim();
    const reasoningEffort = document.getElementById('add-reasoning-effort').value;
    if (model) msg.model = model;
    if (reasoningEffort) msg.reasoning_effort = reasoningEffort;
    /* worktree overrides */
    const wtEnabled = document.getElementById('add-wt-enabled').checked;
    msg.worktree = wtEnabled;
    if (wtEnabled) {
      const wtDir = document.getElementById('add-wt-base-dir').value.trim();
      const wtBranch = document.getElementById('add-wt-base-branch').value.trim();
      const wtName = document.getElementById('add-wt-name').value.trim();
      if (wtDir) msg.worktree_base_dir = wtDir;
      if (wtBranch) msg.worktree_base_branch = wtBranch;
      if (wtName) msg.worktree_name = wtName;
      msg.worktree_auto_checkpoint = document.getElementById('add-wt-auto-checkpoint').checked;
      msg.checkpoint_on_progress = document.getElementById('add-wt-checkpoint-on-progress').checked;
      msg.worktree_merge_squash = document.getElementById('add-wt-squash').checked;
    }
  }

  send(msg);
  closeModals();
}

/* Modals — add group, add agent/terminal, confirm dialog, color picker */

/* -- Provider cache (populated from get_config response) ------------------ */
let _cachedProviders = [];  // [{name, display_name, command}, ...]

function _providerCommandToken(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  return raw.split(/\s+/)[0] || '';
}
function _findProviderMeta(name) {
  return (_cachedProviders || []).find(p => p.name === name) || null;
}

function _detectProviderNameFromCommand(command) {
  const token = _providerCommandToken(command);
  if (!token) return '';
  const match = (_cachedProviders || []).find((p) => _providerCommandToken(p.command) === token);
  return match ? match.name : '';
}

function _runtimeDefaultCommand() {
  return (state && state.runtime && state.runtime.default_command) || 'claude';
}

function _runtimeDefaultProviderName() {
  return _detectProviderNameFromCommand(_runtimeDefaultCommand());
}
function _populateReasoningEffortSelect(selectId, providerName, currentValue, emptyLabel, unsupportedLabel) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const meta = providerName ? _findProviderMeta(providerName) : null;
  const options = meta && Array.isArray(meta.reasoning_efforts) ? meta.reasoning_efforts : [];
  const current = String(currentValue || '').trim();
  sel.innerHTML = '';

  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = emptyLabel || 'Provider default';
  sel.appendChild(empty);

  for (const value of options) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    sel.appendChild(opt);
  }

  if (!options.length) {
    empty.textContent = unsupportedLabel || empty.textContent;
  }
  if (current && !options.includes(current)) {
    const custom = document.createElement('option');
    custom.value = current;
    custom.textContent = current;
    sel.appendChild(custom);
  }
  sel.value = current || '';
}

function _agentSettingsProviderForReasoning() {
  return _getProviderValue('gs-agent-provider') || _runtimeDefaultProviderName();
}

function _engineerProviderForReasoning() {
  return (
    _getProviderValue('gs-engineer-provider')
    || _getProviderValue('gs-agent-provider')
    || _runtimeDefaultProviderName()
  );
}

function _architectProviderForReasoning() {
  return (
    _getProviderValue('gs-architect-provider')
    || _getProviderValue('gs-agent-provider')
    || _runtimeDefaultProviderName()
  );
}

function _populateProviderSelect(selectId, currentValue, includeGroupDefault) {
  const sel = document.getElementById(selectId);
  sel.innerHTML = '';
  if (includeGroupDefault) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = 'Group default';
    sel.appendChild(opt);
  } else {
    const opt = document.createElement('option');
    const defaultProvider = _findProviderMeta(_runtimeDefaultProviderName());
    opt.value = '';
    opt.textContent = defaultProvider
      ? `Default (${defaultProvider.display_name})`
      : 'Default (Claude Code)';
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
  if (!v) return _runtimeDefaultCommand();
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
  _populateReasoningEffortSelect(
    'gs-agent-reasoning-effort',
    _agentSettingsProviderForReasoning(),
    document.getElementById('gs-agent-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
  if (!_getProviderValue('gs-engineer-provider')) {
    onGsEngineerProviderChange();
  }
  if (!_getProviderValue('gs-architect-provider')) {
    onGsArchitectProviderChange();
  }
}

function onAddProviderChange() {
  const v = document.getElementById('add-provider-select').value;
  const cmdRow = document.getElementById('add-cmd-row');
  const label = cmdRow.querySelector('label');
  const input = document.getElementById('add-cmd-input');
  cmdRow.classList.remove('hidden');
  document.getElementById('add-model-row').classList.remove('hidden');
  document.getElementById('add-reasoning-row').classList.remove('hidden');
  if (v === '__custom__') {
    label.textContent = 'Boot command';
    input.placeholder = 'e.g. npm run dev';
  } else {
    label.textContent = 'Command override';
    input.placeholder = _getProviderCommand('add-provider-select') + ' (default)';
  }
  _populateReasoningEffortSelect(
    'add-reasoning-effort',
    _getProviderValue('add-provider-select') || _runtimeDefaultProviderName(),
    document.getElementById('add-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
}

function onGsEngineerProviderChange() {
  const input = document.getElementById('gs-engineer-boot-cmd');
  if (input) {
    const effectiveProvider = _engineerProviderForReasoning();
    const meta = effectiveProvider ? _findProviderMeta(effectiveProvider) : null;
    input.placeholder = (meta ? meta.command : _runtimeDefaultCommand()) + ' (default)';
  }
  _populateReasoningEffortSelect(
    'gs-engineer-reasoning-effort',
    _engineerProviderForReasoning(),
    document.getElementById('gs-engineer-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
}

function onGsArchitectProviderChange() {
  const input = document.getElementById('gs-architect-boot-cmd');
  if (input) {
    const effectiveProvider = _architectProviderForReasoning();
    const meta = effectiveProvider ? _findProviderMeta(effectiveProvider) : null;
    input.placeholder = (meta ? meta.command : _runtimeDefaultCommand()) + ' (default)';
  }
  _populateReasoningEffortSelect(
    'gs-architect-reasoning-effort',
    _architectProviderForReasoning(),
    document.getElementById('gs-architect-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
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

let _confirmResolve = null;
let _addEngineerGroup = '';
let _addEngineerArchitectId = '';

// Modal stack for nested modals. When a nested modal is opened on top of
// another (e.g. "New specialization" inside the engineer-launch dialog),
// the opener pushes onto this stack via openNestedModal(), and Cancel/
// Escape pops only the topmost entry instead of dismissing the parent.
let _modalStack = [];

function openNestedModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('visible');
  // Raise above the parent overlay regardless of DOM order. Without this
  // class, two .visible overlays at the same z-index render in document
  // order — a parent declared later in the DOM (e.g. Group Settings) would
  // paint on top of an earlier-declared child like #modal-new-specialization.
  el.classList.add('modal-nested');
  if (_modalStack.indexOf(id) === -1) _modalStack.push(id);
}

function closeNestedModal(id) {
  // If id omitted, pop the topmost. Otherwise remove the matching entry.
  let target = id;
  if (!target) {
    target = _modalStack.length ? _modalStack[_modalStack.length - 1] : '';
  }
  if (!target) return false;
  const el = document.getElementById(target);
  if (el) {
    el.classList.remove('visible');
    el.classList.remove('modal-nested');
  }
  const idx = _modalStack.lastIndexOf(target);
  if (idx >= 0) _modalStack.splice(idx, 1);
  return true;
}

function closeModals() {
  // Nested-modal stack: pop only the topmost when one is active so Cancel/
  // Escape doesn't dismiss the parent dialog underneath.
  if (_modalStack.length > 0) {
    const topId = _modalStack.pop();
    const el = document.getElementById(topId);
    if (el) {
      el.classList.remove('visible');
      el.classList.remove('modal-nested');
    }
    return;
  }
  var taskModal = document.getElementById('modal-task');
  if (taskModal && taskModal.classList.contains('visible') && typeof _taskClearDraft === 'function') {
    _taskClearDraft(_taskEditId, _taskDraftScope);
    _taskDraftScope = 'create';
  }
  // Clean up draft attachments if task modal was open in create mode
  if (typeof _cleanupDraftAttachments === 'function') _cleanupDraftAttachments();
  if (typeof _taskHistoryOpen !== 'undefined' && _taskHistoryOpen
      && typeof hideTaskHistory === 'function') {
    hideTaskHistory();
  }
  document.querySelectorAll('.overlay').forEach(o => {
    o.classList.remove('visible');
    o.classList.remove('modal-nested');
  });
  document.querySelectorAll('.hint-pop').forEach(p => p.remove());
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  if (typeof _glsCapturing !== 'undefined' && _glsCapturing) _cancelCapture();
  _modalStack = [];
  _addEngineerGroup = '';
  _addEngineerArchitectId = '';
  _addArchitectGroup = '';
  _pendingHireRejectId = '';
  _architectDecisionModalArchitectId = '';
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
    const defaultLabel = /^\s*Delete\b/.test(String(message || '')) ? 'Delete' : 'OK';
    btn.textContent = (opts && opts.label) || defaultLabel;
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
  const summary = document.getElementById('modal-group-summary');
  if (summary) {
    const standalone = !!(state && state.runtime && state.runtime.embedded_terminal);
    summary.textContent = standalone
      ? 'Create the workspace first — Loom will take you straight into agent setup next.'
      : '';
    summary.classList.toggle('hidden', !standalone);
  }
  const inp = document.getElementById('group-name-input');
  inp.value = '';
  const dir = document.getElementById('group-directory-input');
  if (dir) dir.value = '';
  inp.focus();
}
function submitGroup() {
  const name = document.getElementById('group-name-input').value.trim();
  if (!name) return;
  const dirEl = document.getElementById('group-directory-input');
  const directory = dirEl ? dirEl.value.trim() : '';
  const payload = { cmd: 'add_group', group: name };
  if (directory) payload.default_directory = directory;
  if (typeof setActiveGroup === 'function'
      && typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()) {
    setActiveGroup(name, { allowPending: true });
  }
  send(payload);
  const standalone = !!(state && state.runtime && state.runtime.embedded_terminal);
  closeModals();
  if (standalone && typeof openAddAgent === 'function') openAddAgent(name);
}

/* -- Add Engineer ----------------------------------------------------- */
function _normalizeAddEngineerOptions(options, architectId) {
  const ctx = { group: '', hired_by_architect_id: '' };
  if (options && typeof options === 'object') {
    ctx.group = String(options.group || '').trim();
    ctx.hired_by_architect_id = String(
      options.hired_by_architect_id
      || options.hiredByArchitectId
      || options.architect_id
      || options.architectId
      || ''
    ).trim();
  } else {
    ctx.group = String(options || '').trim();
    ctx.hired_by_architect_id = String(architectId || '').trim();
  }
  return ctx;
}

function _engineerModalArchitectName(architectId) {
  const id = String(architectId || '').trim();
  if (!id || !state || !state.agents || !state.agents[id]) return '';
  const architect = state.agents[id];
  return architect.name || architect.slug || id;
}

function _engineerModalSummary(group, architectId) {
  const groupText = String(group || '').trim();
  const architectName = _engineerModalArchitectName(architectId);
  if (architectId) {
    return 'Create a persistent engineer session hired by '
      + (architectName || 'this architect')
      + (groupText ? ' in ' + groupText : '')
      + '.';
  }
  if (groupText) {
    return 'Create a persistent user-hired engineer session in ' + groupText + '.';
  }
  return 'Create a persistent engineer session with its own MCP scope and launch command.';
}

function openAddEngineerForSection(group, architectId) {
  openAddEngineerModal({
    group: group,
    hired_by_architect_id: architectId,
  });
}

function openAddWorkerForSection(group) {
  if (typeof openAddWorkerModal === 'function') {
    openAddWorkerModal(group);
  }
}

function openAddEngineerModal(options, architectId) {
  const modal = document.getElementById('modal-engineer');
  if (!modal) return;
  const ctx = _normalizeAddEngineerOptions(options, architectId);
  _addEngineerGroup = ctx.group;
  _addEngineerArchitectId = ctx.hired_by_architect_id;
  const nameInput = document.getElementById('engineer-name-input');
  const commandInput = document.getElementById('engineer-command-input');
  const summary = document.getElementById('modal-engineer-summary');
  if (summary) {
    summary.textContent = _engineerModalSummary(_addEngineerGroup, _addEngineerArchitectId);
    summary.classList.remove('hidden');
  }
  if (nameInput) nameInput.value = '';
  if (commandInput) commandInput.value = '';
  modal.classList.add('visible');
  if (nameInput && typeof nameInput.focus === 'function') nameInput.focus();
  if (nameInput && typeof nameInput.select === 'function') nameInput.select();
}

function submitAddEngineer() {
  const nameInput = document.getElementById('engineer-name-input');
  const commandInput = document.getElementById('engineer-command-input');
  const name = nameInput ? nameInput.value.trim() : '';
  const command = commandInput ? commandInput.value.trim() : '';
  if (!name) return;
  const payload = { cmd: 'add_engineer', name };
  if (_addEngineerGroup) payload.group = _addEngineerGroup;
  if (_addEngineerArchitectId) payload.hired_by_architect_id = _addEngineerArchitectId;
  if (command) payload.command = command;
  send(payload);
  closeModals();
}

/* -- Add Architect ---------------------------------------------------- */
let _addArchitectGroup = '';
let _pendingHireRejectId = '';
let _architectDecisionModalArchitectId = '';

function _normalizeAddArchitectOptions(options) {
  if (options && typeof options === 'object') {
    return { group: String(options.group || '').trim() };
  }
  return { group: String(options || '').trim() };
}

function openAddArchitectForGroup(group) {
  openAddArchitectModal({ group: group });
}

function openAddArchitectModal(group) {
  const modal = document.getElementById('modal-architect');
  if (!modal) return;
  const nameInput = document.getElementById('architect-name-input');
  const commandInput = document.getElementById('architect-command-input');
  const summary = document.getElementById('modal-architect-summary');
  const ctx = _normalizeAddArchitectOptions(group);
  _addArchitectGroup = ctx.group;
  if (summary) {
    const summaryText = _addArchitectGroup
      ? 'Create a persistent architect session for ' + _addArchitectGroup + '.'
      : 'Create a persistent architect session with its own MCP scope and launch command.';
    summary.textContent = summaryText;
    summary.classList.remove('hidden');
  }
  if (nameInput) nameInput.value = '';
  if (commandInput) commandInput.value = '';
  modal.classList.add('visible');
  if (nameInput && typeof nameInput.focus === 'function') nameInput.focus();
  if (nameInput && typeof nameInput.select === 'function') nameInput.select();
}

function submitAddArchitect() {
  const nameInput = document.getElementById('architect-name-input');
  const commandInput = document.getElementById('architect-command-input');
  const name = nameInput ? nameInput.value.trim() : '';
  const command = commandInput ? commandInput.value.trim() : '';
  if (!name) return;
  const payload = { cmd: 'add_architect', name };
  if (_addArchitectGroup) payload.group = _addArchitectGroup;
  if (command) payload.command = command;
  send(payload);
  if (typeof _showToast === 'function') {
    _showToast('Architect requested', 'success');
  }
  closeModals();
}

function openPendingHireRejectModal(hireId, summaryText) {
  const modal = document.getElementById('modal-pending-hire-reject');
  if (!modal) return;
  _pendingHireRejectId = String(hireId || '').trim();
  const summary = document.getElementById('pending-hire-reject-summary');
  const note = document.getElementById('pending-hire-reject-note');
  if (summary) {
    summary.textContent = String(summaryText || '').trim() || 'Add an optional note for the architect.';
    summary.classList.remove('hidden');
  }
  if (note) note.value = '';
  modal.classList.add('visible');
  if (note && typeof note.focus === 'function') note.focus();
}

function submitPendingHireReject() {
  if (!_pendingHireRejectId) return;
  const note = document.getElementById('pending-hire-reject-note');
  const value = note ? String(note.value || '').trim() : '';
  if (typeof rejectPendingHireWithNote === 'function') {
    rejectPendingHireWithNote(_pendingHireRejectId, value);
  } else {
    send({ cmd: 'pending_hire_reject', id: _pendingHireRejectId, note: value });
  }
  _pendingHireRejectId = '';
  closeModals();
}

function _setArchitectDecisionSelectOptions(selectId, options, selectedValues) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const chosen = new Set((selectedValues || []).map((value) => String(value || '')));
  select.innerHTML = '';
  const rows = Array.isArray(options) ? options : [];
  for (let i = 0; i < rows.length; i++) {
    const option = document.createElement('option');
    option.value = String(rows[i].value || '');
    option.textContent = String(rows[i].label || rows[i].value || '');
    option.selected = chosen.has(option.value);
    select.appendChild(option);
  }
}

function openArchitectDecisionModal(architectId) {
  const modal = document.getElementById('modal-architect-decision');
  if (!modal) return;
  _architectDecisionModalArchitectId = String(architectId || '').trim();
  const architect = state && state.agents ? state.agents[_architectDecisionModalArchitectId] : null;
  const summary = document.getElementById('architect-decision-modal-summary');
  const titleInput = document.getElementById('architect-decision-title-input');
  const rationaleInput = document.getElementById('architect-decision-rationale-input');
  if (summary) {
    summary.textContent = architect
      ? 'Record a product or scope decision for ' + (architect.name || architect.id) + '.'
      : 'Record an architect decision.';
    summary.classList.remove('hidden');
  }
  if (titleInput) titleInput.value = '';
  if (rationaleInput) rationaleInput.value = '';
  if (typeof getArchitectDecisionTaskOptions === 'function') {
    _setArchitectDecisionSelectOptions(
      'architect-decision-task-select',
      getArchitectDecisionTaskOptions(_architectDecisionModalArchitectId),
      [],
    );
  }
  if (typeof getArchitectDecisionEngineerOptions === 'function') {
    _setArchitectDecisionSelectOptions(
      'architect-decision-engineer-select',
      getArchitectDecisionEngineerOptions(_architectDecisionModalArchitectId),
      [],
    );
  }
  modal.classList.add('visible');
  if (titleInput && typeof titleInput.focus === 'function') titleInput.focus();
}

function _selectedMultiValues(selectId) {
  const select = document.getElementById(selectId);
  if (!select || !select.options) return [];
  const values = [];
  for (let i = 0; i < select.options.length; i++) {
    const option = select.options[i];
    if (option && option.selected) values.push(String(option.value || ''));
  }
  return values;
}

function submitArchitectDecision() {
  const architectId = String(_architectDecisionModalArchitectId || '').trim();
  if (!architectId) return;
  const titleInput = document.getElementById('architect-decision-title-input');
  const rationaleInput = document.getElementById('architect-decision-rationale-input');
  const title = titleInput ? String(titleInput.value || '').trim() : '';
  const rationale = rationaleInput ? String(rationaleInput.value || '').trim() : '';
  if (!title || !rationale) return;
  send({
    cmd: 'architect_decision_create',
    architect_id: architectId,
    title: title,
    rationale: rationale,
    linked_task_ids: _selectedMultiValues('architect-decision-task-select'),
    linked_engineer_ids: _selectedMultiValues('architect-decision-engineer-select'),
  });
  if (typeof _showToast === 'function') {
    _showToast('Decision saved', 'success');
  }
  closeModals();
}

/* -- Add agent / terminal modal extracted to static/js/modals/add-cell.js -- */
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
let _gsEngineerColor = '';
let _gsInitialTab = 'group';
let _gsInitialSubtab = '';

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

function openGroupSettings(group, initialTab, initialSubtab) {
  _settingsGroup = group;
  _gsInitialTab = initialTab || 'group';
  _gsInitialSubtab = initialSubtab || '';
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
  el.value = selected || '';
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

function _setSelectValue(id, value, fallback) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = value != null && value !== '' ? String(value) : String(fallback);
}

function _autoGrowTextArea(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.max(el.scrollHeight || 0, 110) + 'px';
}

const _ENGINEER_NOTIFICATION_PRESETS = {
  quiet: {
    label: 'Quiet',
    description: 'Major milestones only, slower digests, and no idle heartbeat.',
    digest_verbosity: 'compact',
    push_interval: 120,
    max_interval: 600,
    heartbeat_interval: 0,
    enabled_events: ['task_derived', 'task_health_alert'],
  },
  normal: {
    label: 'Normal',
    description: 'Balanced Loom defaults with key lifecycle updates and heartbeats.',
    digest_verbosity: 'balanced',
    push_interval: 60,
    max_interval: 300,
    heartbeat_interval: 300,
    enabled_events: [
      'agent_started',
      'task_dispatched',
      'task_derived',
      'task_health_alert',
    ],
  },
  noisy: {
    label: 'Noisy',
    description: 'Faster, more detailed digests including ongoing progress updates.',
    digest_verbosity: 'detailed',
    push_interval: 30,
    max_interval: 120,
    heartbeat_interval: 60,
    enabled_events: [
      'agent_started',
      'task_dispatched',
      'task_derived',
      'agent_progress',
      'task_health_alert',
    ],
  },
};

function _defaultEngineerNotificationSettings() {
  const preset = _ENGINEER_NOTIFICATION_PRESETS.normal;
  return {
    engineer_can_override_worker_provider: true,
    digest_verbosity: preset.digest_verbosity,
    push_interval: preset.push_interval,
    max_interval: preset.max_interval,
    heartbeat_interval: preset.heartbeat_interval,
    enabled_events: preset.enabled_events.slice(),
  };
}

function _getEngineerNotificationPresetSettings(name) {
  const preset = _ENGINEER_NOTIFICATION_PRESETS[String(name || '').trim().toLowerCase()];
  if (!preset) return _defaultEngineerNotificationSettings();
  return {
    digest_verbosity: preset.digest_verbosity,
    push_interval: preset.push_interval,
    max_interval: preset.max_interval,
    heartbeat_interval: preset.heartbeat_interval,
    enabled_events: preset.enabled_events.slice(),
  };
}

function _sortedEngineerEvents(events) {
  return Array.from(new Set((events || []).map((value) => String(value || ''))))
    .filter(Boolean)
    .sort();
}

function _matchEngineerNotificationPreset(settings) {
  const current = settings || {};
  const digestVerbosity = String(
    current.digest_verbosity != null && current.digest_verbosity !== ''
      ? current.digest_verbosity
      : 'balanced'
  );
  const pushInterval = parseInt(current.push_interval, 10);
  const maxInterval = parseInt(current.max_interval, 10);
  const heartbeatInterval = parseInt(current.heartbeat_interval, 10);
  const enabledEvents = _sortedEngineerEvents(current.enabled_events);

  for (const [name, preset] of Object.entries(_ENGINEER_NOTIFICATION_PRESETS)) {
    if (
      digestVerbosity === preset.digest_verbosity
      && pushInterval === preset.push_interval
      && maxInterval === preset.max_interval
      && heartbeatInterval === preset.heartbeat_interval
      && JSON.stringify(enabledEvents) === JSON.stringify(_sortedEngineerEvents(preset.enabled_events))
    ) {
      return name;
    }
  }
  return 'custom';
}

function _setEngineerNotificationPresetHint(id, presetName) {
  const el = document.getElementById(id);
  if (!el) return;
  const preset = _ENGINEER_NOTIFICATION_PRESETS[presetName];
  if (preset) {
    el.textContent = `${preset.label}: ${preset.description} Manual tweaks switch this to Custom.`;
    return;
  }
  el.textContent = 'Custom detailed settings. Pick a preset to overwrite the detailed notification controls below.';
}

function _groupFormEngineerNotificationSettings() {
  return {
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    push_interval: parseInt(document.getElementById('gs-engineer-push-interval').value, 10) || 60,
    max_interval: parseInt(document.getElementById('gs-engineer-max-interval').value, 10) || 300,
    heartbeat_interval: parseInt(document.getElementById('gs-engineer-heartbeat-interval').value, 10),
    enabled_events: _getEngineerEnabledEvents(),
  };
}

function _applyGsEngineerNotificationPreset(name) {
  const preset = _getEngineerNotificationPresetSettings(name);
  _setSelectValue('gs-engineer-digest-verbosity', preset.digest_verbosity, 'balanced');
  _setSelectValue('gs-engineer-push-interval', preset.push_interval, 60);
  _setSelectValue('gs-engineer-max-interval', preset.max_interval, 300);
  _setSelectValue('gs-engineer-heartbeat-interval', preset.heartbeat_interval, 300);
  _setEngineerEventCheckboxes(preset.enabled_events);
}

function syncGsEngineerNotificationPreset() {
  const preset = _matchEngineerNotificationPreset(_groupFormEngineerNotificationSettings());
  _setSelectValue('gs-engineer-notification-preset', preset, 'custom');
  _setEngineerNotificationPresetHint('gs-engineer-notification-preset-hint', preset);
}

function onGsEngineerNotificationPresetChange() {
  const el = document.getElementById('gs-engineer-notification-preset');
  if (!el) return;
  const preset = el.value;
  if (preset && preset !== 'custom') {
    _applyGsEngineerNotificationPreset(preset);
  }
  syncGsEngineerNotificationPreset();
}

function _setEngineerEventCheckboxes(enabled) {
  const current = new Set(enabled || []);
  document.getElementById('gs-engineer-event-agent-started').checked = current.has('agent_started');
  document.getElementById('gs-engineer-event-task-dispatched').checked = current.has('task_dispatched');
  document.getElementById('gs-engineer-event-task-derived').checked = current.has('task_derived');
  document.getElementById('gs-engineer-event-agent-progress').checked = current.has('agent_progress');
  document.getElementById('gs-engineer-event-task-health-alert').checked = current.has('task_health_alert');
}

function _getEngineerEnabledEvents() {
  const events = [];
  if (document.getElementById('gs-engineer-event-agent-started').checked) events.push('agent_started');
  if (document.getElementById('gs-engineer-event-task-dispatched').checked) events.push('task_dispatched');
  if (document.getElementById('gs-engineer-event-task-derived').checked) events.push('task_derived');
  if (document.getElementById('gs-engineer-event-agent-progress').checked) events.push('agent_progress');
  if (document.getElementById('gs-engineer-event-task-health-alert').checked) events.push('task_health_alert');
  return events;
}

function _setDetailsOpen(id, open) {
  const el = document.getElementById(id);
  if (el) el.open = !!open;
}

function _resetGsEngineerSections() {
  _setDetailsOpen('gs-engineer-provider-section', true);
  _setDetailsOpen('gs-engineer-specializations-section', true);
  _setDetailsOpen('gs-engineer-autonomy-section', true);
  _setDetailsOpen('gs-engineer-digest-section', false);
}

/* -- Group Settings: Engineer-tab default specializations picker -------- */
let _gsEngineerSpecs = [];

function _gsEngineerAvailableSpecs() {
  return (state.specializations || [])
    .map(function (s) { return s && s.name; })
    .filter(Boolean);
}

function renderGsEngineerSpecializations() {
  const selectedEl = document.getElementById('gs-engineer-specializations-selected');
  const availableEl = document.getElementById('gs-engineer-specializations-available');
  if (!selectedEl || !availableEl) return;
  const selected = _gsEngineerSpecs;
  selectedEl.innerHTML = '';
  selected.forEach(function (name, idx) {
    const li = document.createElement('li');
    li.className = 'specialization-entry';
    const tag = idx === 0 ? ' (primary)' : '';
    const label = document.createElement('span');
    label.textContent = name + tag;
    li.appendChild(label);
    const controls = document.createElement('span');
    controls.className = 'specialization-controls-row';
    if (idx > 0) {
      const up = document.createElement('button');
      up.type = 'button'; up.textContent = '↑'; up.title = 'Move up';
      up.onclick = function () { gsEngineerMoveSpecialization(idx, -1); };
      controls.appendChild(up);
    }
    if (idx < selected.length - 1) {
      const down = document.createElement('button');
      down.type = 'button'; down.textContent = '↓'; down.title = 'Move down';
      down.onclick = function () { gsEngineerMoveSpecialization(idx, 1); };
      controls.appendChild(down);
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.textContent = '×'; remove.title = 'Delete';
    remove.onclick = function () { gsEngineerRemoveSpecialization(idx); };
    controls.appendChild(remove);
    li.appendChild(controls);
    selectedEl.appendChild(li);
  });

  const available = _gsEngineerAvailableSpecs();
  availableEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = available.length ? 'Pick a specialization...' : 'No specializations available';
  availableEl.appendChild(placeholder);
  available.forEach(function (name) {
    if (selected.indexOf(name) >= 0) return;
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    const meta = (state.specializations || []).find(function (s) {
      return s && s.name === name;
    });
    if (meta && meta.preamble) opt.title = String(meta.preamble).slice(0, 200);
    availableEl.appendChild(opt);
  });
}

function gsEngineerAddSpecialization() {
  const availableEl = document.getElementById('gs-engineer-specializations-available');
  if (!availableEl) return;
  const name = availableEl.value;
  if (!name) return;
  if (_gsEngineerSpecs.indexOf(name) < 0) _gsEngineerSpecs.push(name);
  renderGsEngineerSpecializations();
}

function gsEngineerRemoveSpecialization(idx) {
  if (idx < 0 || idx >= _gsEngineerSpecs.length) return;
  _gsEngineerSpecs.splice(idx, 1);
  renderGsEngineerSpecializations();
}

function gsEngineerMoveSpecialization(idx, delta) {
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= _gsEngineerSpecs.length) return;
  const moved = _gsEngineerSpecs.splice(idx, 1)[0];
  _gsEngineerSpecs.splice(newIdx, 0, moved);
  renderGsEngineerSpecializations();
}

function openGsEngineerNewSpecializationDialog() {
  const modal = document.getElementById('modal-new-specialization');
  if (!modal) return;
  document.getElementById('new-specialization-name').value = '';
  document.getElementById('new-specialization-description').value = '';
  document.getElementById('new-specialization-preamble').value = '';
  document.getElementById('new-specialization-priorities').value = '';
  document.getElementById('new-specialization-scope').value = 'project';
  // Stash a flag so the submit handler knows to refresh the GS picker too.
  modal.dataset.gsEngineerSource = '1';
  if (typeof openNestedModal === 'function') {
    openNestedModal('modal-new-specialization');
  } else {
    modal.classList.add('visible');
  }
  document.getElementById('new-specialization-name').focus();
}

const _ARCHITECT_DIGEST_DEFAULT_EVENTS = [
  'task_done',
  'task_blocked',
  'task_error',
  'task_ask',
  'task_derive',
  'task_completed',
  'agent_blocked',
  'agent_error',
  'ask_created',
  'task_derived',
  'pipeline_complete',
  'engineer_hired',
  'engineer_fired',
  'engineer_dismissed',
  'engineer_rehired',
  'workflow_breach',
  'engineer_queue_empty',
  'engineer_awaiting_human_input',
  'engineer_ask_resolved',
];

function _defaultArchitectSettings() {
  return {
    architect_boot_command: '',
    architect_provider: '',
    architect_model: '',
    architect_reasoning_effort: '',
    architect_custom_instructions: '',
    architect_autonomy_mode: 'dispatch_after_confirm',
    architect_paused: false,
    architect_digest_verbosity: 'balanced',
    architect_push_interval: 300,
    architect_max_interval: 600,
    architect_heartbeat_interval: 0,
    architect_suppress_empty_digests: true,
    architect_enabled_events: _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice(),
    architect_journal_checkpoint_frequency: 'every_10_actions',
  };
}

function _renderArchitectEventCheckboxes(enabled) {
  const grid = document.getElementById('gs-architect-events-grid');
  if (!grid) return;
  const set = new Set((enabled || []).map((value) => String(value || '')));
  grid.innerHTML = '';
  _ARCHITECT_DIGEST_DEFAULT_EVENTS.forEach((kind) => {
    const label = document.createElement('label');
    label.className = 'gs-checkbox';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.dataset.eventKind = kind;
    input.id = `gs-architect-event-${kind.replace(/_/g, '-')}`;
    input.checked = set.has(kind);
    const text = document.createElement('span');
    text.textContent = ' ' + kind;
    label.appendChild(input);
    label.appendChild(text);
    grid.appendChild(label);
  });
}

function _getArchitectEnabledEvents() {
  const grid = document.getElementById('gs-architect-events-grid');
  if (!grid) return _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice();
  const out = [];
  grid.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    if (cb.checked && cb.dataset.eventKind) {
      out.push(cb.dataset.eventKind);
    }
  });
  return out;
}

function _resetGsArchitectSections() {
  _setDetailsOpen('gs-architect-boot-section', true);
  _setDetailsOpen('gs-architect-behavior-section', true);
  _setDetailsOpen('gs-architect-custom-section', true);
  _setDetailsOpen('gs-architect-runtime-section', true);
  _setDetailsOpen('gs-architect-digest-section', false);
}

function _showGroupSettings(group, data) {
  _settingsGroup = group;
  const s = data.settings;
  const ws = Object.assign(
    _defaultEngineerNotificationSettings(),
    data.engineer_settings || {}
  );
  const architectSettings = Object.assign(
    _defaultArchitectSettings(),
    data.architect_settings || {}
  );
  const engineer = s.engineer_agent_id && state.agents ? state.agents[s.engineer_agent_id] : null;

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
  document.getElementById('gs-agent-model').value = s.agent_model || '';
  document.getElementById('gs-agent-reasoning-effort').value = s.agent_reasoning_effort || '';
  onGsProviderChange();
  document.getElementById('gs-worktree').checked = s.git_worktree || false;
  document.getElementById('gs-wt-base-dir').value = s.worktree_base_dir || '.loom/worktrees';
  document.getElementById('gs-wt-base-branch').value = s.worktree_base_branch || '';
  document.getElementById('gs-wt-auto-checkpoint').checked = s.worktree_auto_checkpoint || false;
  document.getElementById('gs-wt-checkpoint-on-progress').checked = s.checkpoint_on_progress || false;
  document.getElementById('gs-wt-merge-squash').checked = s.worktree_merge_squash === true;
  document.getElementById('gs-wt-merge-instructions').value = s.worktree_merge_instructions || '';
  _setSelectValue('gs-wt-merge-cleanup', s.worktree_merge_cleanup, 'keep');
  document.getElementById('gs-wt-merge-preserve-diff').checked = !!s.worktree_merge_preserve_diff;
  _gsWtSymlinks = (s.worktree_symlinks || []).slice();
  _renderWtSymlinks();
  _toggleWorktreeFields();
  document.getElementById('gs-session-resume').checked = s.agent_session_resume !== false;
  document.getElementById('gs-agent-idle-timeout').value = s.agent_idle_timeout != null ? s.agent_idle_timeout : 0;
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

  /* -- Terminals sub-tab -- */
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

  /* -- Engineer tab -- */
  _populateProviderSelect('gs-engineer-provider', ws.engineer_provider || '', true);
  document.getElementById('gs-engineer-boot-cmd').value = ws.engineer_boot_command || '';
  document.getElementById('gs-engineer-model').value = ws.engineer_model || '';
  document.getElementById('gs-engineer-reasoning-effort').value = ws.engineer_reasoning_effort || '';
  document.getElementById('gs-engineer-directory').value = ws.engineer_directory || '';
  document.getElementById('gs-engineer-shell').value = ws.engineer_shell || '';
  document.getElementById('gs-engineer-custom-instructions').value = ws.custom_instructions || '';
  _populateProfileSelect(
    document.getElementById('gs-engineer-profile'),
    data.profiles,
    ws.engineer_profile,
    'Same as agent/group'
  );
  _gsEngineerColor = ws.engineer_tab_color || '';
  _renderSwatches(
    'gs-engineer-color-swatches',
    _gsEngineerColor,
    'selectGsEngineerColor',
    true
  );
  onGsEngineerProviderChange();
  // Default specializations picker state — primed from the group setting,
  // refreshed in place when state.specializations updates over WS.
  _gsEngineerSpecs = Array.isArray(s.default_engineer_specializations)
    ? s.default_engineer_specializations.slice()
    : [];
  send({ cmd: 'list_specializations', group: group });
  renderGsEngineerSpecializations();
  document.getElementById('gs-engineer-restrict-to-created-agents').checked = !!ws.restrict_to_created_agents;
  document.getElementById('gs-engineer-can-override-worker-provider').checked = ws.engineer_can_override_worker_provider !== false;
  _setSelectValue('gs-engineer-autonomy-mode', ws.autonomy_mode, 'dispatch_when_clear');
  _setSelectValue(
    'gs-engineer-default-worker-concurrency',
    ws.default_worker_concurrency,
    2
  );
  _setSelectValue(
    'gs-engineer-wave-size-preference',
    ws.wave_size_preference,
    'small'
  );
  _setSelectValue(
    'gs-engineer-same-agent-follow-up-preference',
    ws.same_agent_follow_up_preference,
    'balanced'
  );
  _setSelectValue(
    'gs-engineer-digest-verbosity',
    ws.digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'gs-engineer-escalation-style',
    ws.escalation_style,
    'note_then_ask'
  );
  _setSelectValue('gs-engineer-push-interval', ws.push_interval, 60);
  _setSelectValue('gs-engineer-max-interval', ws.max_interval, 300);
  _setSelectValue(
    'gs-engineer-heartbeat-interval',
    ws.heartbeat_interval,
    ws.max_interval || 300
  );
  _setEngineerEventCheckboxes(ws.enabled_events || []);
  syncGsEngineerNotificationPreset();
  _resetGsEngineerSections();

  /* -- Architect tab -- */
  _populateProviderSelect(
    'gs-architect-provider',
    architectSettings.architect_provider || '',
    true
  );
  document.getElementById('gs-architect-boot-cmd').value = architectSettings.architect_boot_command || '';
  document.getElementById('gs-architect-model').value = architectSettings.architect_model || '';
  document.getElementById('gs-architect-reasoning-effort').value = architectSettings.architect_reasoning_effort || '';
  document.getElementById('gs-architect-custom-instructions').value = architectSettings.architect_custom_instructions || '';
  _autoGrowTextArea('gs-architect-custom-instructions');
  _setSelectValue(
    'gs-architect-autonomy-mode',
    architectSettings.architect_autonomy_mode,
    'dispatch_after_confirm'
  );
  document.getElementById('gs-architect-paused').checked = !!architectSettings.architect_paused;
  _setSelectValue(
    'gs-architect-digest-verbosity',
    architectSettings.architect_digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'gs-architect-push-interval',
    architectSettings.architect_push_interval,
    300
  );
  _setSelectValue(
    'gs-architect-max-interval',
    architectSettings.architect_max_interval,
    600
  );
  _setSelectValue(
    'gs-architect-heartbeat-interval',
    architectSettings.architect_heartbeat_interval,
    0
  );
  document.getElementById('gs-architect-suppress-empty').checked = (
    architectSettings.architect_suppress_empty_digests !== false
  );
  const archEvents = (
    Array.isArray(architectSettings.architect_enabled_events)
    && architectSettings.architect_enabled_events.length
  )
    ? architectSettings.architect_enabled_events
    : _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice();
  _renderArchitectEventCheckboxes(archEvents);
  document.getElementById('gs-architect-journal-checkpoint').value = (
    architectSettings.architect_journal_checkpoint_frequency || 'every_10_actions'
  );
  onGsArchitectProviderChange();
  _resetGsArchitectSections();

  const initialTab = _gsInitialTab || 'group';
  const initialSubtab = _gsInitialSubtab || '';
  switchGsTab(initialTab);
  if (initialSubtab) {
    const btn = document.querySelector(`.gs-pane[data-pane="${initialTab}"] .gs-subtab[data-subtab="${initialSubtab}"]`);
    if (btn) switchGsSubTab(initialTab, btn);
  }
  _gsInitialTab = 'group';
  _gsInitialSubtab = '';
  document.getElementById('modal-group-settings').classList.add('visible');
  const focusId = initialTab === 'engineer'
    ? 'gs-engineer-provider'
    : initialTab === 'architect'
      ? 'gs-architect-provider'
      : 'gs-directory';
  const focusEl = document.getElementById(focusId);
  if (focusEl) focusEl.focus();
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
function selectGsEngineerColor(hex) {
  _gsEngineerColor = hex;
  document.querySelectorAll('#gs-engineer-color-swatches .swatch').forEach(s => {
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
    agent_model: document.getElementById('gs-agent-model').value.trim(),
    agent_reasoning_effort: document.getElementById('gs-agent-reasoning-effort').value,
    agent_env_vars: _textToEnv('gs-agent-env-vars'),
    agent_env_file: document.getElementById('gs-agent-env-file').value.trim(),
    git_worktree: document.getElementById('gs-worktree').checked,
    worktree_base_dir: document.getElementById('gs-wt-base-dir').value.trim() || '.loom/worktrees',
    worktree_base_branch: document.getElementById('gs-wt-base-branch').value.trim(),
    worktree_auto_checkpoint: document.getElementById('gs-wt-auto-checkpoint').checked,
    checkpoint_on_progress: document.getElementById('gs-wt-checkpoint-on-progress').checked,
    worktree_merge_squash: document.getElementById('gs-wt-merge-squash').checked,
    worktree_merge_instructions: document.getElementById('gs-wt-merge-instructions').value.trim(),
    worktree_merge_cleanup: document.getElementById('gs-wt-merge-cleanup').value,
    worktree_merge_preserve_diff: document.getElementById('gs-wt-merge-preserve-diff').checked,
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
    default_engineer_specializations: (_gsEngineerSpecs || []).slice(),
  };
  const engineerSettings = {
    engineer_provider: _getProviderValue('gs-engineer-provider'),
    engineer_boot_command: document.getElementById('gs-engineer-boot-cmd').value.trim(),
    engineer_model: document.getElementById('gs-engineer-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('gs-engineer-reasoning-effort').value,
    engineer_directory: document.getElementById('gs-engineer-directory').value.trim(),
    engineer_profile: document.getElementById('gs-engineer-profile').value,
    engineer_shell: document.getElementById('gs-engineer-shell').value,
    engineer_tab_color: _gsEngineerColor,
    custom_instructions: document.getElementById('gs-engineer-custom-instructions').value,
    restrict_to_created_agents: document.getElementById('gs-engineer-restrict-to-created-agents').checked,
    engineer_can_override_worker_provider: document.getElementById('gs-engineer-can-override-worker-provider').checked,
    autonomy_mode: document.getElementById('gs-engineer-autonomy-mode').value,
    default_worker_concurrency: parseInt(document.getElementById('gs-engineer-default-worker-concurrency').value, 10) || 2,
    wave_size_preference: document.getElementById('gs-engineer-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('gs-engineer-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    escalation_style: document.getElementById('gs-engineer-escalation-style').value,
    push_interval: parseInt(document.getElementById('gs-engineer-push-interval').value, 10) || 60,
    max_interval: parseInt(document.getElementById('gs-engineer-max-interval').value, 10) || 300,
    heartbeat_interval: parseInt(document.getElementById('gs-engineer-heartbeat-interval').value, 10),
    enabled_events: _getEngineerEnabledEvents(),
  };
  const architectSettings = {
    architect_provider: _getProviderValue('gs-architect-provider'),
    architect_boot_command: document.getElementById('gs-architect-boot-cmd').value.trim(),
    architect_model: document.getElementById('gs-architect-model').value.trim(),
    architect_reasoning_effort: document.getElementById('gs-architect-reasoning-effort').value,
    architect_custom_instructions: document.getElementById('gs-architect-custom-instructions').value,
    architect_autonomy_mode: document.getElementById('gs-architect-autonomy-mode').value,
    architect_paused: document.getElementById('gs-architect-paused').checked,
    architect_digest_verbosity: document.getElementById('gs-architect-digest-verbosity').value,
    architect_push_interval: parseInt(document.getElementById('gs-architect-push-interval').value, 10) || 300,
    architect_max_interval: parseInt(document.getElementById('gs-architect-max-interval').value, 10) || 600,
    architect_heartbeat_interval: parseInt(document.getElementById('gs-architect-heartbeat-interval').value, 10) || 0,
    architect_suppress_empty_digests: document.getElementById('gs-architect-suppress-empty').checked,
    architect_enabled_events: _getArchitectEnabledEvents(),
    architect_journal_checkpoint_frequency: document.getElementById('gs-architect-journal-checkpoint').value.trim() || 'every_10_actions',
  };

  send({ cmd: 'update_group_settings', group: _settingsGroup, settings });
  send({ cmd: 'engineer_update_settings', group: _settingsGroup, ...engineerSettings });
  send({ cmd: 'update_architect_settings', group: _settingsGroup, settings: architectSettings });
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

/* -- Action/task modal modules extracted to static/js/modals/*.js ------- */

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

function _glsXtermScrollbackDefault() {
  return (typeof XTERM_SCROLLBACK_DEFAULT === 'number')
    ? XTERM_SCROLLBACK_DEFAULT : 2000;
}

function _glsXtermScrollbackMin() {
  return (typeof XTERM_SCROLLBACK_MIN === 'number')
    ? XTERM_SCROLLBACK_MIN : 100;
}

function _glsXtermScrollbackMax() {
  return (typeof XTERM_SCROLLBACK_MAX === 'number')
    ? XTERM_SCROLLBACK_MAX : 100000;
}

function _parseGlsXtermScrollback() {
  var input = document.getElementById('gls-xterm-scrollback');
  if (!input) return _glsXtermScrollbackDefault();
  var min = _glsXtermScrollbackMin();
  var max = _glsXtermScrollbackMax();
  var value = Number(input.value);
  if (!Number.isFinite(value) || Math.floor(value) !== value
      || value < min || value > max) {
    var message = 'Terminal scrollback must be an integer between '
      + min + ' and ' + max + ' lines.';
    if (typeof _showToast === 'function') _showToast(message, 'error');
    else if (typeof alert === 'function') alert(message);
    if (input && typeof input.focus === 'function') input.focus();
    return null;
  }
  return Math.floor(value);
}

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
  document.getElementById('gls-xterm-scrollback').value =
    s.xterm_scrollback !== undefined ? s.xterm_scrollback
      : _glsXtermScrollbackDefault();
  var argsCapture = s.mcp_call_log_args_capture || 'metadata';
  var argsCaptureEl = document.getElementById('gls-mcp-call-log-args-capture');
  if (argsCaptureEl) argsCaptureEl.value = argsCapture;
  var fullCaptureEl = document.getElementById('gls-mcp-call-log-full-capture-tools');
  if (fullCaptureEl) {
    fullCaptureEl.value = (s.mcp_call_log_full_capture_tools || []).join('\n');
  }
  var maxRowsEl = document.getElementById('gls-event-ingest-max-rows');
  if (maxRowsEl) {
    maxRowsEl.value = s.event_ingest_max_rows !== undefined
      ? s.event_ingest_max_rows : 100000;
  }
  var maxDaysEl = document.getElementById('gls-event-ingest-max-days');
  if (maxDaysEl) {
    maxDaysEl.value = s.event_ingest_max_days !== undefined
      ? s.event_ingest_max_days : 14;
  }

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
  var xtermScrollback = _parseGlsXtermScrollback();
  if (xtermScrollback === null) return;

  var settings = {
    default_command: document.getElementById('gls-default-cmd').value.trim(),
    filter_by_window: document.getElementById('gls-filter-window').checked,
    focus_new_tabs: document.getElementById('gls-focus-new-tabs').checked,
    focus_on_click: document.getElementById('gls-focus-on-click').checked,
    xterm_scrollback: xtermScrollback,
    default_lanes: lanes,
    keybindings: _glsKeybindings,
    max_pipeline_depth: parseInt(document.getElementById('gls-max-pipeline-depth').value) || 0,
    max_event_log: parseInt(document.getElementById('gls-max-event-log').value) || 500,
  };
  var argsCaptureEl = document.getElementById('gls-mcp-call-log-args-capture');
  if (argsCaptureEl) settings.mcp_call_log_args_capture = argsCaptureEl.value || 'metadata';
  var fullCaptureEl = document.getElementById('gls-mcp-call-log-full-capture-tools');
  if (fullCaptureEl) {
    settings.mcp_call_log_full_capture_tools = fullCaptureEl.value
      .split(/\n|,/)
      .map(function(item) { return item.trim(); })
      .filter(Boolean);
  }
  var maxRowsEl = document.getElementById('gls-event-ingest-max-rows');
  if (maxRowsEl) settings.event_ingest_max_rows = parseInt(maxRowsEl.value) || 100000;
  var maxDaysEl = document.getElementById('gls-event-ingest-max-days');
  if (maxDaysEl) settings.event_ingest_max_days = parseInt(maxDaysEl.value) || 0;
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

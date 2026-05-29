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

function _workerProviderForReasoning() {
  return (
    _getProviderValue('gs-worker-provider')
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

function _gsInputValue(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || '').trim() : '';
}

function _providerDefaultCommand(providerName) {
  const meta = providerName ? _findProviderMeta(providerName) : null;
  return meta ? meta.command : _runtimeDefaultCommand();
}

function _gsGroupDefaultModelPreview() {
  return _gsInputValue('gs-agent-model') || 'system default';
}

function _gsGroupDefaultCommandPreview(providerName) {
  return _gsInputValue('gs-agent-boot-cmd')
    || _providerDefaultCommand(providerName);
}

function _setInputPlaceholder(id, text) {
  const el = document.getElementById(id);
  if (el) el.placeholder = text;
}

function refreshGsInheritedLaunchPlaceholders() {
  const modelPreview = _gsGroupDefaultModelPreview();
  _setInputPlaceholder('gs-worker-model', 'Group default: ' + modelPreview);
  _setInputPlaceholder('gs-engineer-model', 'Group default: ' + modelPreview);
  _setInputPlaceholder('gs-architect-model', 'Group default: ' + modelPreview);

  _setInputPlaceholder(
    'gs-worker-boot-command',
    'Group default: ' + _gsGroupDefaultCommandPreview(_workerProviderForReasoning())
  );
  _setInputPlaceholder(
    'gs-engineer-boot-cmd',
    'Group default: ' + _gsGroupDefaultCommandPreview(_engineerProviderForReasoning())
  );
  _setInputPlaceholder(
    'gs-architect-boot-cmd',
    'Group default: ' + _gsGroupDefaultCommandPreview(_architectProviderForReasoning())
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
  const el = document.getElementById(selectId);
  const v = el ? el.value : '';
  return v === '__custom__' ? '' : v;
}

function _getProviderCommand(selectId) {
  const el = document.getElementById(selectId);
  const v = el ? el.value : '';
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
    label.textContent = 'Default boot command';
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
  refreshGsInheritedLaunchPlaceholders();
  if (!_getProviderValue('gs-worker-provider')) {
    onGsWorkerProviderChange();
  }
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
  refreshGsInheritedLaunchPlaceholders();
  _populateReasoningEffortSelect(
    'gs-engineer-reasoning-effort',
    _engineerProviderForReasoning(),
    document.getElementById('gs-engineer-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
}

function onGsWorkerProviderChange(currentValue) {
  refreshGsInheritedLaunchPlaceholders();
  const reasoning = document.getElementById('gs-worker-reasoning-effort');
  if (!reasoning) return;
  _populateReasoningEffortSelect(
    'gs-worker-reasoning-effort',
    _workerProviderForReasoning(),
    currentValue == null ? reasoning.value : currentValue,
    'Provider default',
    'Not supported for this provider'
  );
}

function onGsArchitectProviderChange() {
  refreshGsInheritedLaunchPlaceholders();
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

function setWorktreeDiffModalVisible(visible) {
  const root = document.getElementById('diff-view-root');
  if (!root) return null;
  if (visible) {
    root.classList.add('overlay');
    root.classList.add('visible');
    root.onclick = function(event) {
      if (event && event.target === root && typeof hideDiffView === 'function') {
        hideDiffView();
      }
    };
  } else {
    root.classList.remove('visible');
    root.classList.remove('overlay');
    root.classList.remove('modal-nested');
    root.onclick = null;
  }
  return root;
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
  var diffModalOpen = typeof _diffViewOpen !== 'undefined' && _diffViewOpen
    && typeof _diffReadOnly !== 'undefined' && _diffReadOnly;
  if (diffModalOpen) {
    var closedOverlayAboveDiff = false;
    document.querySelectorAll('.overlay').forEach(o => {
      if (o && o.id === 'diff-view-root') return;
      if (o && o.classList.contains('visible')) {
        o.classList.remove('visible');
        o.classList.remove('modal-nested');
        closedOverlayAboveDiff = true;
      }
    });
    document.querySelectorAll('.hint-pop').forEach(p => p.remove());
    if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
    if (closedOverlayAboveDiff) return;
    if (typeof hideDiffView === 'function') {
      hideDiffView();
      return;
    }
  }
  document.querySelectorAll('.overlay').forEach(o => {
    o.classList.remove('visible');
    o.classList.remove('modal-nested');
  });
  document.querySelectorAll('.hint-pop').forEach(p => p.remove());
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  if (typeof _glsCapturing !== 'undefined' && _glsCapturing) _cancelCapture();
  // Display-once relay device-link (TORQUE:603 #3): drop any minted secret +
  // confirm gesture so nothing transient survives the modal close.
  if (typeof _relayDeviceLinkReset === 'function') _relayDeviceLinkReset();
  // Daemon-credential pairing token is a one-time secret pasted into the modal;
  // do not leave it in the DOM after close.
  if (typeof _relayDaemonCredentialReset === 'function') _relayDaemonCredentialReset();
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
      ? 'Create the workspace first — Torque will open its settings next.'
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
  closeModals();
  if (typeof openGroupSettings === 'function') openGroupSettings(name, 'group');
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
  if (architect && Number(architect.dismissed_at || 0) > 0) {
    if (typeof _showToast === 'function') {
      _showToast('Rehire the architect before adding decisions.', 'warning');
    }
    return;
  }
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
  const architect = state && state.agents ? state.agents[architectId] : null;
  if (architect && Number(architect.dismissed_at || 0) > 0) {
    if (typeof _showToast === 'function') {
      _showToast('Rehire the architect before adding decisions.', 'warning');
    }
    return;
  }
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
let _editEngineerSpecs = [];
let _editSpecializationsGroup = null;

function _editSpecializationsListMatchesGroup() {
  if (_editSpecializationsGroup === null) return true;
  return String((state && state.specializations_group) || '') === _editSpecializationsGroup;
}

function _editAvailableSpecs() {
  if (!_editSpecializationsListMatchesGroup()) return [];
  return (state.specializations || [])
    .map(function (s) { return s && s.name; })
    .filter(Boolean);
}

function _editNormalizeEngineerSpecs(raw, opts) {
  opts = opts || {};
  const shouldFilter = !!opts.filterKnown && _editSpecializationsListMatchesGroup();
  const available = shouldFilter ? new Set(_editAvailableSpecs()) : null;
  const out = [];
  const seen = new Set();
  (Array.isArray(raw) ? raw : []).forEach(function (item) {
    const name = String(item || '').trim();
    if (!name || seen.has(name)) return;
    if (available && available.size && !available.has(name)) return;
    seen.add(name);
    out.push(name);
  });
  return out;
}

function renderEditEngineerSpecializations() {
  const selectedEl = document.getElementById('edit-specializations-selected');
  const availableEl = document.getElementById('edit-specializations-available');
  if (!selectedEl || !availableEl) return;
  _editEngineerSpecs = _editNormalizeEngineerSpecs(_editEngineerSpecs, { filterKnown: true });
  const selected = _editEngineerSpecs;
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
      up.onclick = function () { editEngineerMoveSpecialization(idx, -1); };
      controls.appendChild(up);
    }
    if (idx < selected.length - 1) {
      const down = document.createElement('button');
      down.type = 'button'; down.textContent = '↓'; down.title = 'Move down';
      down.onclick = function () { editEngineerMoveSpecialization(idx, 1); };
      controls.appendChild(down);
    }
    const remove = document.createElement('button');
    remove.type = 'button'; remove.textContent = '×'; remove.title = 'Delete';
    remove.onclick = function () { editEngineerRemoveSpecialization(idx); };
    controls.appendChild(remove);
    li.appendChild(controls);
    selectedEl.appendChild(li);
  });

  const available = _editAvailableSpecs();
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

function editEngineerAddSpecialization() {
  const availableEl = document.getElementById('edit-specializations-available');
  if (!availableEl) return;
  const name = availableEl.value;
  if (!name) return;
  if (_editAvailableSpecs().indexOf(name) < 0) return;
  if (_editEngineerSpecs.indexOf(name) < 0) _editEngineerSpecs.push(name);
  renderEditEngineerSpecializations();
}

function editEngineerRemoveSpecialization(idx) {
  if (idx < 0 || idx >= _editEngineerSpecs.length) return;
  _editEngineerSpecs.splice(idx, 1);
  renderEditEngineerSpecializations();
}

function editEngineerMoveSpecialization(idx, delta) {
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= _editEngineerSpecs.length) return;
  const moved = _editEngineerSpecs.splice(idx, 1)[0];
  _editEngineerSpecs.splice(newIdx, 0, moved);
  renderEditEngineerSpecializations();
}

function openEditCell(id) {
  const cell = state.agents[id];
  if (!cell) return;
  _editCellId = id;
  _editEngineerSpecs = [];
  _editSpecializationsGroup = null;

  document.getElementById('edit-title').textContent =
    cell.cell_type === 'terminal' ? 'Edit Terminal' :
    cell.kind === 'engineer' ? 'Edit Engineer' : 'Edit Agent';
  document.getElementById('edit-name-input').value = cell.name;

  const specsRow = document.getElementById('edit-specializations-row');
  if (cell.kind === 'engineer') {
    _editSpecializationsGroup = String(cell.group || '');
    _editEngineerSpecs = _editNormalizeEngineerSpecs(
      cell.engineer_specializations || [],
      { filterKnown: true }
    );
    if (specsRow) specsRow.classList.remove('hidden');
    send({ cmd: 'list_specializations', group: cell.group || '' });
    renderEditEngineerSpecializations();
  } else {
    _editSpecializationsGroup = null;
    if (specsRow) specsRow.classList.add('hidden');
  }

  document.getElementById('modal-edit').classList.add('visible');
  document.getElementById('edit-name-input').focus();
  document.getElementById('edit-name-input').select();
}

function submitEdit() {
  if (!_editCellId) return;
  const cell = state.agents[_editCellId];
  const name = document.getElementById('edit-name-input').value.trim();
  if (!name) return;
  const payload = { cmd: 'update_agent', id: _editCellId, name };
  if (cell && cell.kind === 'engineer') {
    payload.engineer_specializations = _editNormalizeEngineerSpecs(
      _editEngineerSpecs,
      { filterKnown: true }
    );
  }
  send(payload);
  _editCellId = null;
  _editEngineerSpecs = [];
  _editSpecializationsGroup = null;
  closeModals();
}

/* -- Group Settings ---------------------------------------------------- */
let _settingsGroup = null;
let _gsInitialTab = 'group';
let _gsInitialSubtab = '';
let _gsActiveSubTabs = {};
let _gsBoardSyncPreflightMode = '';
let _gsBoardSyncProjectOptions = [];
let _gsBoardSyncProjectsLoadedKey = '';

const DIGEST_VERBOSITY_TOOLTIP_HELP = 'Controls how much detail appears in digest events sent to this agent. Higher verbosity can wake the agent more often on coarse-event activity in the group.';

function _setHintText(id, text) {
  const el = document.getElementById(id);
  if (el) el.dataset.hint = text;
}

function _wireGroupSettingsTooltipText() {
  _setHintText('gs-engineer-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP);
  _setHintText('gs-architect-digest-verbosity-hint', DIGEST_VERBOSITY_TOOLTIP_HELP);
}

function _setEngineerWorkerVisibilityPermission(restrictToCreatedAgents) {
  // The stored setting keeps the legacy "hide other Engineers' workers"
  // polarity. The UI presents the inverse, affirmative permission.
  document.getElementById('gs-engineer-restrict-to-created-agents').checked = !restrictToCreatedAgents;
}

function _getEngineerRestrictToCreatedAgentsFromPermission() {
  return !document.getElementById('gs-engineer-restrict-to-created-agents').checked;
}

function _normalizeGsSelection(tab, subtab) {
  const rawTab = String(tab || 'group').trim() || 'group';
  const rawSubtab = String(subtab || '').trim();
  let nextTab = rawTab;
  let nextSubtab = rawSubtab;

  // Back-compat for any callers or saved links that still use the previous
  // Agents tab names after the UI split: terminal defaults moved into Group,
  // while worker execution settings became the Workers tab.
  if (rawTab === 'agents') nextTab = 'workers';
  if (rawTab === 'terminals') {
    nextTab = 'group';
    nextSubtab = rawSubtab || 'group-terminals';
  }
  if (rawSubtab === 'agent-terminals') {
    nextTab = 'group';
    nextSubtab = 'group-terminals';
  } else if (rawSubtab === 'agent-general') {
    nextSubtab = 'worker-execution';
  } else if (rawSubtab === 'agent-worktree') {
    nextSubtab = 'worker-worktree';
  } else if (rawSubtab === 'agent-notifications') {
    nextSubtab = 'worker-notifications';
  }

  if (nextSubtab.indexOf('group-') === 0) nextTab = 'group';
  if (nextSubtab.indexOf('worker-') === 0) nextTab = 'workers';
  if (!['group', 'workers', 'engineer', 'architect'].includes(nextTab)) {
    nextTab = 'group';
  }
  return { tab: nextTab, subtab: nextSubtab };
}

function _boardSyncCommandPayload(cmd, args) {
  const payload = {
    cmd: cmd,
    args: Object.assign({}, args || {}),
  };
  args = args || {};
  for (const key in args) payload[key] = args[key];
  return payload;
}

function _gsStringifyJsonMap(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  const keys = Object.keys(value);
  if (!keys.length) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch (_e) {
    return '';
  }
}

function _gsParseJsonMap(id, label) {
  const el = document.getElementById(id);
  const raw = el ? String(el.value || '').trim() : '';
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Expected a JSON object.');
    }
    const out = {};
    Object.keys(parsed).forEach(function(key) {
      const k = String(key || '').trim();
      if (!k) return;
      const value = parsed[key];
      if (value === null || value === undefined) return;
      const v = String(value).trim();
      if (v) out[k] = v;
    });
    return out;
  } catch (err) {
    const message = label + ' must be a valid JSON object.';
    if (typeof _showToast === 'function') _showToast(message, 'error');
    if (el && typeof el.focus === 'function') el.focus();
    return null;
  }
}

function _gsParseJsonMapSilent(id) {
  const el = document.getElementById(id);
  const raw = el ? String(el.value || '').trim() : '';
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }
    const out = {};
    Object.keys(parsed).forEach(function(key) {
      const k = String(key || '').trim();
      const value = parsed[key];
      const v = value === null || value === undefined ? '' : String(value).trim();
      if (k && v) out[k] = v;
    });
    return out;
  } catch (_err) {
    return {};
  }
}

function _gsBoardSyncProjectNumberValue() {
  const el = document.getElementById('gs-board-sync-github-project-number');
  return parseInt(el && el.value, 10) || 0;
}

function _gsBoardSyncProjectIdValue() {
  const el = document.getElementById('gs-board-sync-github-project-id');
  return el ? String(el.value || '').trim() : '';
}

function _gsBoardSyncGithubSettingsFromForm() {
  return {
    github_repo: document.getElementById('gs-board-sync-github-repo').value.trim(),
    github_project_owner: document.getElementById('gs-board-sync-github-project-owner').value.trim(),
    github_project_number: _gsBoardSyncProjectNumberValue(),
    github_project_id: _gsBoardSyncProjectIdValue(),
    github_project_status_field: document.getElementById('gs-board-sync-github-status-field').value.trim() || 'Status',
    github_lane_status_map: _gsParseJsonMapSilent('gs-board-sync-github-lane-map'),
  };
}

function _gsBoardSyncDraftSettings() {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  return {
    board_sync_provider: provider,
    board_sync_enabled: document.getElementById('gs-board-sync-enabled').checked,
    board_sync_github: _gsBoardSyncGithubSettingsFromForm(),
  };
}

function onGsBoardSyncProviderChange(loadProjects) {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const configEl = document.getElementById('gs-board-sync-github-config');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (configEl) configEl.style.display = provider === 'github' ? '' : 'none';
  if (provider === 'github' && loadProjects !== false) {
    _gsBoardSyncMaybeLoadProjects();
  }
}

function _gsBoardSyncSetPreflightStatus(kind, message) {
  const el = document.getElementById('gs-board-sync-preflight-summary');
  if (!el) return;
  el.textContent = message || '';
  el.className = 'board-sync-preflight-summary';
  if (kind) el.classList.add('board-sync-preflight-' + kind);
}

function _gsBoardSyncPreflightErrorText(msg) {
  const phase = String((msg && msg.phase) || '').trim();
  const raw = String(
    (msg && (msg.error || msg.message || msg.reason))
      || 'Board sync preflight failed.'
  ).trim();
  let text = raw || 'Board sync preflight failed.';
  const lower = text.toLowerCase();
  if (phase === 'project_scope'
      || lower.indexOf('project scope') >= 0
      || lower.indexOf("'project' scope") >= 0
      || lower.indexOf('gh auth refresh -s project') >= 0) {
    if (text.indexOf('gh auth refresh -s project') < 0) {
      text += ' Run: gh auth refresh -s project';
    }
  } else if (phase === 'repo'
      || lower.indexOf('repository') >= 0
      || lower.indexOf('repo') >= 0
      || lower.indexOf('not found') >= 0) {
    text += ' Check the repo field or click “Use current repo”.';
  }
  return text;
}

function _gsBoardSyncSetProjectStatus(kind, message) {
  const el = document.getElementById('gs-board-sync-project-summary');
  if (!el) return;
  el.textContent = message || '';
  el.className = 'board-sync-project-summary';
  if (kind) el.classList.add('board-sync-preflight-' + kind);
}

function _gsBoardSyncProjectValue(project) {
  if (!project || typeof project !== 'object') return '';
  const owner = String(project.owner || '').trim();
  const number = parseInt(project.number, 10) || 0;
  const id = String(project.id || '').trim();
  return owner + '#' + number + '#' + id;
}

function _gsBoardSyncProjectLabel(project) {
  const number = parseInt(project.number, 10) || 0;
  const name = String(project.name || project.title || '').trim() || 'Untitled project';
  const owner = String(project.owner || '').trim() || 'unknown owner';
  return owner + ' · #' + (number || '?') + ' — ' + name;
}

function _gsBoardSyncFindProject(value) {
  const wanted = String(value || '');
  return (_gsBoardSyncProjectOptions || []).find(function(project) {
    return _gsBoardSyncProjectValue(project) === wanted;
  }) || null;
}

function _gsBoardSyncRenderProjectOptions() {
  const select = document.getElementById('gs-board-sync-github-project-select');
  if (!select) return;
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const numberEl = document.getElementById('gs-board-sync-github-project-number');
  const idEl = document.getElementById('gs-board-sync-github-project-id');
  const selectedOwner = ownerEl ? String(ownerEl.value || '').trim() : '';
  const selectedNumber = parseInt(numberEl && numberEl.value, 10) || 0;
  const selectedId = idEl ? String(idEl.value || '').trim() : '';
  select.innerHTML = '';

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a GitHub Project…';
  select.appendChild(placeholder);

  let selectedValue = '';
  (_gsBoardSyncProjectOptions || []).forEach(function(project) {
    const option = document.createElement('option');
    const value = _gsBoardSyncProjectValue(project);
    option.value = value;
    option.textContent = _gsBoardSyncProjectLabel(project);
    option.dataset.owner = String(project.owner || '');
    option.dataset.number = String(project.number || '');
    option.dataset.projectId = String(project.id || '');
    select.appendChild(option);
    const sameId = selectedId && String(project.id || '') === selectedId;
    const sameOwnerNumber = selectedNumber
      && String(project.owner || '').toLowerCase() === selectedOwner.toLowerCase()
      && parseInt(project.number, 10) === selectedNumber;
    if (sameId || sameOwnerNumber) selectedValue = value;
  });

  if (!selectedValue && selectedOwner && selectedNumber) {
    const manualProject = {
      owner: selectedOwner,
      number: selectedNumber,
      id: selectedId,
      name: 'Configured manually',
    };
    selectedValue = _gsBoardSyncProjectValue(manualProject);
    const option = document.createElement('option');
    option.value = selectedValue;
    option.textContent = _gsBoardSyncProjectLabel(manualProject);
    select.appendChild(option);
  }
  select.value = selectedValue;
}

function _gsBoardSyncProjectsKey() {
  const providerEl = document.getElementById('gs-board-sync-provider');
  const repoEl = document.getElementById('gs-board-sync-github-repo');
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  const repo = repoEl ? String(repoEl.value || '').trim() : '';
  const owner = ownerEl ? String(ownerEl.value || '').trim() : '';
  return [_settingsGroup || '', provider, repo, owner].join('|');
}

function _gsBoardSyncMaybeLoadProjects(force) {
  if (!_settingsGroup) return;
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (provider !== 'github') return;
  const key = _gsBoardSyncProjectsKey();
  if (!force && key && key === _gsBoardSyncProjectsLoadedKey) return;
  gsBoardSyncReloadProjects();
}

function gsBoardSyncReloadProjects() {
  if (!_settingsGroup) return;
  const providerEl = document.getElementById('gs-board-sync-provider');
  const provider = providerEl ? String(providerEl.value || 'none') : 'none';
  if (provider !== 'github') {
    _gsBoardSyncSetProjectStatus('error', 'Select GitHub as the provider to load projects.');
    return;
  }
  const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
  const owner = ownerEl ? String(ownerEl.value || '').trim() : '';
  _gsBoardSyncProjectsLoadedKey = _gsBoardSyncProjectsKey();
  _gsBoardSyncSetProjectStatus('pending', 'Loading accessible GitHub Projects…');
  send(_boardSyncCommandPayload('board_sync_list_projects', {
    group: _settingsGroup,
    provider: provider,
    owner: owner,
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _handleBoardSyncProjects(msg) {
  if (!msg || msg.type !== 'board_sync_list_projects') return;
  const group = String(msg.group || '');
  if (_settingsGroup && group && group !== _settingsGroup) return;
  if (!msg.ok) {
    _gsBoardSyncSetProjectStatus(
      'error',
      msg.error || 'Could not load GitHub Projects.'
    );
    return;
  }
  _gsBoardSyncProjectOptions = Array.isArray(msg.projects) ? msg.projects.slice() : [];
  _gsBoardSyncRenderProjectOptions();
  if (_gsBoardSyncProjectOptions.length) {
    const owners = Array.isArray(msg.owners) ? msg.owners.filter(Boolean) : [];
    let loadedMessage = 'Loaded ' + _gsBoardSyncProjectOptions.length + ' accessible project'
      + (_gsBoardSyncProjectOptions.length === 1 ? '' : 's');
    if (owners.length) loadedMessage += ' from ' + owners.join(', ');
    loadedMessage += '.';
    if (Array.isArray(msg.errors) && msg.errors.length) {
      loadedMessage += ' Some owners could not be checked.';
    }
    _gsBoardSyncSetProjectStatus(
      'ok',
      loadedMessage
    );
  } else {
    _gsBoardSyncSetProjectStatus(
      'error',
      'No accessible projects — verify gh auth scope or try Other owner…'
    );
  }
}

function onGsBoardSyncProjectSelect() {
  const select = document.getElementById('gs-board-sync-github-project-select');
  if (!select) return;
  const project = _gsBoardSyncFindProject(select.value);
  if (!project) return;
  document.getElementById('gs-board-sync-github-project-owner').value =
    String(project.owner || '').trim();
  document.getElementById('gs-board-sync-github-project-number').value =
    parseInt(project.number, 10) || '';
  const idEl = document.getElementById('gs-board-sync-github-project-id');
  if (idEl) idEl.value = String(project.id || '').trim();
  _gsBoardSyncPreflightMode = 'project-select';
  _gsBoardSyncSetProjectStatus('pending', 'Resolving project Status options…');
  _gsBoardSyncSetPreflightStatus('pending', 'Resolving GitHub Project…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: 'github',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _gsBoardSyncApplyLaneMapSuggestion(msg) {
  const textarea = document.getElementById('gs-board-sync-github-lane-map');
  if (!textarea) return false;
  if (String(textarea.value || '').trim()) return false;
  const suggestion = msg && msg.lane_status_map_suggestion;
  if (!suggestion || typeof suggestion !== 'object' || Array.isArray(suggestion)) {
    return false;
  }
  const keys = Object.keys(suggestion).filter(function(key) {
    return String(key || '').trim() && String(suggestion[key] || '').trim();
  });
  const unmatched = Array.isArray(msg.lane_status_map_unmatched_lanes)
    ? msg.lane_status_map_unmatched_lanes.filter(Boolean)
    : [];
  if (!keys.length) {
    if (unmatched.length) {
      _gsBoardSyncSetProjectStatus(
        'pending',
        'No automatic lane matches. Map these lanes manually: '
          + unmatched.join(', ') + '.'
      );
    }
    return false;
  }
  const out = {};
  keys.forEach(function(key) {
    out[String(key).trim()] = String(suggestion[key]).trim();
  });
  textarea.value = JSON.stringify(out, null, 2);
  const strategy = String(msg.lane_status_map_strategy || '').trim();
  let message = strategy === 'position'
    ? 'Auto-filled lane → status mapping by lane/status position.'
    : 'Auto-filled matching lane → status names.';
  if (unmatched.length) message += ' Review unmatched lanes: ' + unmatched.join(', ') + '.';
  _gsBoardSyncSetProjectStatus(unmatched.length ? 'pending' : 'ok', message);
  return true;
}

function testGroupBoardSyncConnection() {
  if (!_settingsGroup) return;
  _gsBoardSyncPreflightMode = 'test';
  _gsBoardSyncSetPreflightStatus('pending', 'Testing GitHub connection…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: document.getElementById('gs-board-sync-provider').value || 'none',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function gsBoardSyncUseCurrentRepo() {
  if (!_settingsGroup) return;
  _gsBoardSyncPreflightMode = 'use-current-repo';
  _gsBoardSyncSetPreflightStatus('pending', 'Inspecting current GitHub repo…');
  send(_boardSyncCommandPayload('board_sync_preflight', {
    group: _settingsGroup,
    provider: document.getElementById('gs-board-sync-provider').value || 'none',
    settings: _gsBoardSyncDraftSettings(),
  }));
}

function _handleBoardSyncPreflight(msg) {
  if (!msg || msg.type !== 'board_sync_preflight') return;
  const group = String(msg.group || '');
  if (_settingsGroup && group && group !== _settingsGroup) return;
  const mode = _gsBoardSyncPreflightMode;
  _gsBoardSyncPreflightMode = '';
  if (msg.ok) {
    const repo = String(msg.repo || msg.repository || '').trim();
    if (mode === 'use-current-repo' && repo) {
      const repoEl = document.getElementById('gs-board-sync-github-repo');
      if (repoEl) repoEl.value = repo;
    }
    if (msg.project_owner) {
      const ownerEl = document.getElementById('gs-board-sync-github-project-owner');
      if (ownerEl) ownerEl.value = String(msg.project_owner || '');
    }
    if (msg.project_number) {
      const numberEl = document.getElementById('gs-board-sync-github-project-number');
      if (numberEl) numberEl.value = msg.project_number;
    }
    if (msg.project_id) {
      const idEl = document.getElementById('gs-board-sync-github-project-id');
      if (idEl) idEl.value = String(msg.project_id || '');
    }
    _gsBoardSyncApplyLaneMapSuggestion(msg);
    let success = 'GitHub connection OK';
    if (repo) success += ': ' + repo;
    if (msg.project_number || msg.project_id) success += ' · Project OK';
    _gsBoardSyncSetPreflightStatus('ok', success);
    if (typeof _showToast === 'function') _showToast('Board sync connection OK', 'success');
    return;
  }
  const errorText = _gsBoardSyncPreflightErrorText(msg);
  _gsBoardSyncSetPreflightStatus('error', errorText);
  if (typeof _showToast === 'function') _showToast(errorText, 'error');
}

function switchGsTab(name) {
  name = _normalizeGsSelection(name, '').tab;
  document.querySelectorAll('.gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
  const pane = document.querySelector(`.gs-pane[data-pane="${name}"]`);
  if (!pane) return;
  const preferredSubtab = _gsActiveSubTabs[name] || '';
  const subtabs = Array.prototype.slice.call(pane.querySelectorAll('.gs-subtab'));
  let nextSubtab = null;
  if (preferredSubtab) {
    nextSubtab = subtabs.find(t => t.dataset && t.dataset.subtab === preferredSubtab) || null;
  }
  if (!nextSubtab) {
    nextSubtab = pane.querySelector('.gs-subtab');
  }
  if (nextSubtab) switchGsSubTab(name, nextSubtab);
}

function switchGsSubTab(pane, btn) {
  const container = btn.closest('.gs-pane');
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  const target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
  const paneName = container.dataset && container.dataset.pane
    ? container.dataset.pane
    : pane;
  if (paneName && target) _gsActiveSubTabs[paneName] = target;
  if (target === 'group-sync') _gsBoardSyncMaybeLoadProjects();
}

function openGroupSettings(group, initialTab, initialSubtab) {
  _settingsGroup = group;
  const selection = _normalizeGsSelection(initialTab || 'group', initialSubtab || '');
  _gsInitialTab = selection.tab;
  _gsInitialSubtab = selection.subtab;
  send({ cmd: 'get_group_settings', group });
}

let _systemPromptPreviewSeq = 0;
let _systemPromptPreviewRequestId = '';

function _groupSettingsPromptPreviewPayload() {
  return {
    default_agent_template: document.getElementById('gs-default-agent-template').value,
    agent_provider: _getProviderValue('gs-agent-provider'),
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_model: document.getElementById('gs-agent-model').value.trim(),
    agent_reasoning_effort: document.getElementById('gs-agent-reasoning-effort').value,
    worker_provider: _getProviderValue('gs-worker-provider'),
    worker_boot_command: document.getElementById('gs-worker-boot-command').value.trim(),
    worker_model: document.getElementById('gs-worker-model').value.trim(),
    worker_reasoning_effort: document.getElementById('gs-worker-reasoning-effort').value,
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_shell: document.getElementById('gs-agent-shell').value,
    engineer_merge_mode: document.getElementById('gs-engineer-merge-mode').value,
    worktree_merge_cleanup: document.getElementById('gs-wt-merge-cleanup').value,
    default_engineer_specializations: (_gsEngineerSpecs || []).slice(),
  };
}

function _engineerSettingsPromptPreviewPayload() {
  return {
    engineer_provider: _getProviderValue('gs-engineer-provider'),
    engineer_boot_command: document.getElementById('gs-engineer-boot-cmd').value.trim(),
    engineer_model: document.getElementById('gs-engineer-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('gs-engineer-reasoning-effort').value,
    engineer_directory: document.getElementById('gs-engineer-directory').value.trim(),
    engineer_shell: document.getElementById('gs-engineer-shell').value,
    custom_instructions: document.getElementById('gs-engineer-custom-instructions').value,
    restrict_to_created_agents: _getEngineerRestrictToCreatedAgentsFromPermission(),
    autonomy_mode: document.getElementById('gs-engineer-autonomy-mode').value,
    default_worker_concurrency: parseInt(
      document.getElementById('gs-engineer-default-worker-concurrency').value,
      10
    ) || 2,
    wave_size_preference: document.getElementById('gs-engineer-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('gs-engineer-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('gs-engineer-digest-verbosity').value,
    escalation_style: document.getElementById('gs-engineer-escalation-style').value,
  };
}

function _architectSettingsPromptPreviewPayload() {
  return {
    architect_provider: _getProviderValue('gs-architect-provider'),
    architect_boot_command: document.getElementById('gs-architect-boot-cmd').value.trim(),
    architect_model: document.getElementById('gs-architect-model').value.trim(),
    architect_reasoning_effort: document.getElementById('gs-architect-reasoning-effort').value,
    architect_directory: document.getElementById('gs-architect-directory').value.trim(),
    architect_shell: document.getElementById('gs-architect-shell').value,
    architect_custom_instructions: document.getElementById('gs-architect-custom-instructions').value,
    architect_autonomy_mode: document.getElementById('gs-architect-autonomy-mode').value,
    architect_digest_verbosity: document.getElementById('gs-architect-digest-verbosity').value,
    architect_journal_checkpoint_frequency: document.getElementById('gs-architect-journal-checkpoint').value.trim() || 'every_10_actions',
  };
}

function _systemPromptPreviewTitle(kind) {
  return kind === 'architect'
    ? 'Architect system prompt'
    : 'Engineer system prompt';
}

function _clearSystemPromptPreviewError() {
  const errorEl = document.getElementById('system-prompt-preview-error');
  if (!errorEl) return;
  errorEl.textContent = '';
  errorEl.style.display = 'none';
}

function _formatSystemPromptPreviewError(msg) {
  const reason = String(
    (msg && (msg.message || msg.error || msg.reason || msg.detail))
      || 'Unknown error'
  ).trim() || 'Unknown error';
  if (reason.indexOf('Failed to render system prompt:') === 0) {
    return reason;
  }
  return `Failed to render system prompt: ${reason}`;
}

function _systemPromptPreviewResponseIsCurrent(msg) {
  return !(
    msg
    && msg.request_id
    && _systemPromptPreviewRequestId
    && msg.request_id !== _systemPromptPreviewRequestId
  );
}

function _showSystemPromptPreviewError(msg) {
  if (!_systemPromptPreviewResponseIsCurrent(msg)) return false;
  const modal = document.getElementById('modal-system-prompt-preview');
  const contentEl = document.getElementById('system-prompt-preview-content');
  const errorEl = document.getElementById('system-prompt-preview-error');
  if (!modal || !contentEl || !errorEl) return false;
  const cmd = String((msg && (msg.cmd || msg.command || msg.request_cmd)) || '').trim();
  const hasExplicitPreviewSignal = !!(
    (msg && msg.request_id)
    || cmd === 'preview_system_prompt'
  );
  const hasPreviewRequest = hasExplicitPreviewSignal
    || !!(_systemPromptPreviewRequestId && contentEl.textContent === 'Loading…');
  if (!hasPreviewRequest) return false;

  contentEl.textContent = '';
  document.getElementById('system-prompt-preview-summary').textContent =
    'Unable to render system prompt.';
  errorEl.textContent = _formatSystemPromptPreviewError(msg);
  errorEl.style.display = '';
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) {
    copyBtn.disabled = true;
    copyBtn.textContent = 'Copy to clipboard';
  }
  return true;
}

function openGroupSystemPromptPreview(kind) {
  const previewKind = String(kind || '').trim().toLowerCase() === 'architect'
    ? 'architect'
    : 'engineer';
  const modal = document.getElementById('modal-system-prompt-preview');
  if (!modal || !_settingsGroup) return;

  const requestId = `system-prompt-preview-${Date.now()}-${++_systemPromptPreviewSeq}`;
  _systemPromptPreviewRequestId = requestId;
  document.getElementById('system-prompt-preview-title').textContent =
    _systemPromptPreviewTitle(previewKind);
  document.getElementById('system-prompt-preview-summary').textContent =
    `Rendering ${previewKind} prompt from the current unsaved form values…`;
  document.getElementById('system-prompt-preview-content').textContent = 'Loading…';
  _clearSystemPromptPreviewError();
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) {
    copyBtn.disabled = true;
    copyBtn.textContent = 'Copy to clipboard';
  }

  if (typeof openNestedModal === 'function') {
    openNestedModal('modal-system-prompt-preview');
  } else {
    modal.classList.add('visible');
  }

  send({
    cmd: 'preview_system_prompt',
    request_id: requestId,
    group: _settingsGroup,
    kind: previewKind,
    group_settings: _groupSettingsPromptPreviewPayload(),
    settings: previewKind === 'architect'
      ? _architectSettingsPromptPreviewPayload()
      : _engineerSettingsPromptPreviewPayload(),
  });
}

function closeSystemPromptPreview() {
  _systemPromptPreviewRequestId = '';
  if (typeof closeNestedModal === 'function'
      && closeNestedModal('modal-system-prompt-preview')) {
    return;
  }
  const modal = document.getElementById('modal-system-prompt-preview');
  if (modal) {
    modal.classList.remove('visible');
    modal.classList.remove('modal-nested');
  }
}

function _showSystemPromptPreview(msg) {
  msg = msg || {};
  if (!_systemPromptPreviewResponseIsCurrent(msg)) return;
  if (msg.error || msg.message || msg.reason || msg.detail) {
    _showSystemPromptPreviewError(msg);
    return;
  }
  const kind = String((msg && msg.kind) || '').trim().toLowerCase();
  if (kind) {
    document.getElementById('system-prompt-preview-title').textContent =
      _systemPromptPreviewTitle(kind);
  }
  const prompt = (msg && msg.prompt) || '(empty)';
  document.getElementById('system-prompt-preview-content').textContent = prompt;
  _clearSystemPromptPreviewError();
  const label = kind || 'system';
  document.getElementById('system-prompt-preview-summary').textContent =
    `Rendered ${label} prompt for ${msg.group || _settingsGroup || 'this group'}.`;
  const copyBtn = document.getElementById('system-prompt-preview-copy-btn');
  if (copyBtn) copyBtn.disabled = false;
}

function copySystemPromptPreview() {
  const text = document.getElementById('system-prompt-preview-content').textContent;
  const btn = document.getElementById('system-prompt-preview-copy-btn');
  if (typeof navigator === 'undefined'
      || !navigator.clipboard
      || !navigator.clipboard.writeText) return;
  navigator.clipboard.writeText(text).then(function() {
    if (!btn) return;
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy to clipboard'; }, 1500);
  });
}

async function deleteSettingsGroup() {
  const group = _settingsGroup;
  if (!group || typeof removeGroup !== 'function') return;
  if (await removeGroup(group)) {
    _settingsGroup = null;
    closeModals();
  }
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
    description: 'Balanced Torque defaults with key lifecycle updates and heartbeats.',
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

function _resetGsSubTabs(paneName, subtabName) {
  const btn = document.querySelector(`.gs-pane[data-pane="${paneName}"] .gs-subtab[data-subtab="${subtabName}"]`);
  if (btn) switchGsSubTab(paneName, btn);
}

function _resetGsEngineerSections() {
  _resetGsSubTabs('engineer', 'engineer-general');
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

const _ARCHITECT_JOURNAL_CHECKPOINT_OPTIONS = [
  'every_5_actions',
  'every_10_actions',
  'every_15_actions',
  'every_20_actions',
  'every_20_minutes',
  'every_30_minutes',
  'every_60_minutes',
  'manual_only',
];

function _defaultArchitectSettings() {
  return {
    architect_boot_command: '',
    architect_provider: '',
    architect_model: '',
    architect_reasoning_effort: '',
    architect_directory: '',
    architect_shell: '',
    architect_custom_instructions: '',
    architect_autonomy_mode: 'dispatch_after_confirm',
    architect_digest_verbosity: 'balanced',
    architect_push_interval: 300,
    architect_max_interval: 600,
    architect_heartbeat_interval: 0,
    architect_suppress_empty_digests: true,
    architect_enabled_events: _ARCHITECT_DIGEST_DEFAULT_EVENTS.slice(),
    architect_journal_checkpoint_frequency: 'every_10_actions',
  };
}

function _architectJournalCheckpointLabel(value) {
  const raw = String(value || '').trim();
  if (raw === 'manual_only') return 'Manual only';
  const match = raw.match(/^every_([1-9]\d*)_(actions|minutes)$/);
  if (match) {
    const count = parseInt(match[1], 10);
    const unit = match[2] === 'actions' ? 'action' : 'minute';
    return `Every ${count} ${unit}${count === 1 ? '' : 's'}`;
  }
  return raw || 'Every 10 actions';
}

function _populateArchitectJournalCheckpointSelect(currentValue) {
  const sel = document.getElementById('gs-architect-journal-checkpoint');
  if (!sel) return;
  const current = String(currentValue || 'every_10_actions').trim() || 'every_10_actions';
  const values = _ARCHITECT_JOURNAL_CHECKPOINT_OPTIONS.slice();
  if (values.indexOf(current) < 0) values.push(current);
  sel.innerHTML = '';
  values.forEach((value) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = _architectJournalCheckpointLabel(value);
    sel.appendChild(opt);
  });
  sel.value = current;
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
  _resetGsSubTabs('architect', 'architect-general');
}

function _showGroupSettings(group, data) {
  _settingsGroup = group;
  _wireGroupSettingsTooltipText();
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

  /* -- Group > Agents (all-kinds defaults) + Workers tab -- */
  document.getElementById('gs-agent-directory').value = s.agent_directory || '';
  document.getElementById('gs-agent-shell').value = s.agent_shell || '';
  _populateProviderSelect('gs-agent-provider', s.agent_provider || '', false);
  _populateTemplateSelect('gs-default-agent-template', s.default_agent_template || '', 'None');
  document.getElementById('gs-agent-boot-cmd').value = s.agent_boot_command || '';
  document.getElementById('gs-agent-model').value = s.agent_model || '';
  document.getElementById('gs-agent-reasoning-effort').value = s.agent_reasoning_effort || '';
  _populateProviderSelect('gs-worker-provider', s.worker_provider || '', true);
  document.getElementById('gs-worker-boot-command').value = s.worker_boot_command || '';
  document.getElementById('gs-worker-model').value = s.worker_model || '';
  document.getElementById('gs-worker-reasoning-effort').value = s.worker_reasoning_effort || '';
  onGsProviderChange();
  onGsWorkerProviderChange(s.worker_reasoning_effort || '');
  document.getElementById('gs-worktree').checked = s.git_worktree || false;
  document.getElementById('gs-wt-base-dir').value = s.worktree_base_dir || '.torque/worktrees';
  document.getElementById('gs-wt-base-branch').value = s.worktree_base_branch || '';
  document.getElementById('gs-wt-auto-checkpoint').checked = s.worktree_auto_checkpoint || false;
  document.getElementById('gs-wt-checkpoint-on-progress').checked = s.checkpoint_on_progress || false;
  document.getElementById('gs-wt-merge-squash').checked = s.worktree_merge_squash === true;
  _setSelectValue('gs-engineer-merge-mode', s.engineer_merge_mode, 'pr');
  document.getElementById('gs-wt-merge-instructions').value = s.worktree_merge_instructions || '';
  _setSelectValue('gs-wt-merge-cleanup', s.worktree_merge_cleanup, 'keep');
  document.getElementById('gs-wt-merge-preserve-diff').checked = !!s.worktree_merge_preserve_diff;
  document.getElementById('gs-wt-symlink-gitignored').checked = !!s.worktree_symlink_gitignored_paths;
  _gsWtSymlinks = (s.worktree_symlinks || []).slice();
  _renderWtSymlinks();
  _toggleWorktreeFields();
  document.getElementById('gs-session-resume').checked = s.agent_session_resume !== false;
  document.getElementById('gs-agent-idle-timeout').value = s.agent_idle_timeout != null ? s.agent_idle_timeout : 0;
  document.getElementById('gs-guidance-hint-cadence').value =
    s.guidance_hint_cadence != null ? s.guidance_hint_cadence : 4;
  document.getElementById('gs-notifications').checked = s.notifications || false;
  document.getElementById('gs-notify-finish').checked = s.notify_on_finish !== false;
  document.getElementById('gs-notify-error').checked = s.notify_on_error !== false;
  document.getElementById('gs-notify-attention').checked = s.notify_on_attention !== false;
  document.getElementById('gs-agent-env-vars').value = _envToText(s.agent_env_vars);
  document.getElementById('gs-agent-env-file').value = s.agent_env_file || '';

  /* -- Group > Terminals sub-tab -- */
  document.getElementById('gs-terminal-prefix').value = s.terminal_name_prefix || '';
  document.getElementById('gs-terminal-boot-cmd').value = s.terminal_boot_command || '';
  document.getElementById('gs-terminal-cmd-args').value = s.terminal_command_args || '';
  document.getElementById('gs-terminal-init-script').value = s.terminal_init_script || '';
  document.getElementById('gs-terminal-directory').value = s.terminal_directory || '';
  document.getElementById('gs-terminal-shell').value = s.terminal_shell || '';
  document.getElementById('gs-terminal-always-custom').checked = s.terminal_always_custom_dialog || false;
  document.getElementById('gs-terminal-env-vars').value = _envToText(s.terminal_env_vars);
  document.getElementById('gs-terminal-env-file').value = s.terminal_env_file || '';

  /* -- Group > Sync provider sub-tab -- */
  const syncProvider = s.board_sync_provider || 'none';
  const syncGithub = (s.board_sync_github && typeof s.board_sync_github === 'object')
    ? s.board_sync_github
    : {};
  _setSelectValue('gs-board-sync-provider', syncProvider, 'none');
  document.getElementById('gs-board-sync-enabled').checked = !!s.board_sync_enabled;
  document.getElementById('gs-board-sync-github-repo').value = syncGithub.github_repo || '';
  document.getElementById('gs-board-sync-github-project-owner').value = syncGithub.github_project_owner || '';
  document.getElementById('gs-board-sync-github-project-number').value = syncGithub.github_project_number || '';
  document.getElementById('gs-board-sync-github-project-id').value = syncGithub.github_project_id || '';
  document.getElementById('gs-board-sync-github-status-field').value = syncGithub.github_project_status_field || 'Status';
  document.getElementById('gs-board-sync-github-lane-map').value = _gsStringifyJsonMap(syncGithub.github_lane_status_map);
  document.getElementById('gs-board-sync-github-close-via-pr').checked = syncGithub.github_close_issues_via_pr !== false;
  document.getElementById('gs-board-sync-github-create-labels').checked = syncGithub.github_create_missing_labels !== false;
  document.getElementById('gs-board-sync-github-assignee-map').value = _gsStringifyJsonMap(syncGithub.github_assignee_map);
  _gsBoardSyncProjectOptions = [];
  _gsBoardSyncProjectsLoadedKey = '';
  _gsBoardSyncRenderProjectOptions();
  _gsBoardSyncSetProjectStatus('', '');
  _gsBoardSyncPreflightMode = '';
  _gsBoardSyncSetPreflightStatus('', '');
  onGsBoardSyncProviderChange(false);

  /* -- Engineer tab -- */
  _populateProviderSelect('gs-engineer-provider', ws.engineer_provider || '', true);
  document.getElementById('gs-engineer-boot-cmd').value = ws.engineer_boot_command || '';
  document.getElementById('gs-engineer-model').value = ws.engineer_model || '';
  document.getElementById('gs-engineer-reasoning-effort').value = ws.engineer_reasoning_effort || '';
  document.getElementById('gs-engineer-directory').value = ws.engineer_directory || '';
  document.getElementById('gs-engineer-shell').value = ws.engineer_shell || '';
  document.getElementById('gs-engineer-custom-instructions').value = ws.custom_instructions || '';
  onGsEngineerProviderChange();
  // Default specializations picker state — primed from the group setting,
  // refreshed in place when state.specializations updates over WS.
  _gsEngineerSpecs = Array.isArray(s.default_engineer_specializations)
    ? s.default_engineer_specializations.slice()
    : [];
  send({ cmd: 'list_specializations', group: group });
  renderGsEngineerSpecializations();
  _setEngineerWorkerVisibilityPermission(!!ws.restrict_to_created_agents);
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

  /* -- Architect tab -- */
  _populateProviderSelect(
    'gs-architect-provider',
    architectSettings.architect_provider || '',
    true
  );
  document.getElementById('gs-architect-boot-cmd').value = architectSettings.architect_boot_command || '';
  document.getElementById('gs-architect-model').value = architectSettings.architect_model || '';
  document.getElementById('gs-architect-reasoning-effort').value = architectSettings.architect_reasoning_effort || '';
  document.getElementById('gs-architect-directory').value = architectSettings.architect_directory || '';
  document.getElementById('gs-architect-shell').value = architectSettings.architect_shell || '';
  document.getElementById('gs-architect-custom-instructions').value = architectSettings.architect_custom_instructions || '';
  _autoGrowTextArea('gs-architect-custom-instructions');
  _setSelectValue(
    'gs-architect-autonomy-mode',
    architectSettings.architect_autonomy_mode,
    'dispatch_after_confirm'
  );
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
  _populateArchitectJournalCheckpointSelect(
    architectSettings.architect_journal_checkpoint_frequency || 'every_10_actions'
  );
  onGsArchitectProviderChange();

  const selection = _normalizeGsSelection(_gsInitialTab || 'group', _gsInitialSubtab || '');
  const initialTab = selection.tab;
  const initialSubtab = selection.subtab;
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
      : initialTab === 'workers'
        ? 'gs-agent-directory'
        : initialSubtab === 'group-worker-defaults'
          ? 'gs-agent-provider'
      : initialSubtab === 'group-terminals'
        ? 'gs-terminal-prefix'
        : initialSubtab === 'group-sync'
          ? 'gs-board-sync-provider'
        : initialSubtab === 'group-advanced'
          ? 'gs-guidance-hint-cadence'
        : 'gs-directory';
  const focusEl = document.getElementById(focusId);
  if (focusEl) focusEl.focus();
  if (initialTab === 'group' && initialSubtab === 'group-sync') {
    _gsBoardSyncMaybeLoadProjects();
  }
}

function submitGroupSettings() {
  if (!_settingsGroup) return;
  const boardSyncLaneMap = _gsParseJsonMap(
    'gs-board-sync-github-lane-map',
    'Lane → status mapping'
  );
  if (boardSyncLaneMap === null) return;
  const boardSyncAssigneeMap = _gsParseJsonMap(
    'gs-board-sync-github-assignee-map',
    'Assignee map'
  );
  if (boardSyncAssigneeMap === null) return;
  const boardSyncProjectNumber = parseInt(
    document.getElementById('gs-board-sync-github-project-number').value,
    10
  ) || 0;
  const guidanceHintCadence = parseInt(
    document.getElementById('gs-guidance-hint-cadence').value,
    10
  );

  const settings = {
    /* Group */
    default_directory: document.getElementById('gs-directory').value.trim(),
    shell: document.getElementById('gs-shell').value,
    env_vars: _textToEnv('gs-env-vars'),
    env_file: document.getElementById('gs-env-file').value.trim(),
    auto_terminals: parseInt(document.getElementById('gs-auto-terminals').value) || 0,
    max_agents: parseInt(document.getElementById('gs-max-agents').value) || 0,
    collapsed_default: document.getElementById('gs-collapsed').checked,
    filter_by_window: document.getElementById('gs-filter-window').checked,
    /* Group-wide worker defaults + worker execution */
    agent_directory: document.getElementById('gs-agent-directory').value.trim(),
    agent_shell: document.getElementById('gs-agent-shell').value,
    default_agent_template: document.getElementById('gs-default-agent-template').value,
    agent_provider: _getProviderValue('gs-agent-provider'),
    agent_boot_command: document.getElementById('gs-agent-boot-cmd').value.trim(),
    agent_model: document.getElementById('gs-agent-model').value.trim(),
    agent_reasoning_effort: document.getElementById('gs-agent-reasoning-effort').value,
    worker_provider: _getProviderValue('gs-worker-provider'),
    worker_boot_command: document.getElementById('gs-worker-boot-command').value.trim(),
    worker_model: document.getElementById('gs-worker-model').value.trim(),
    worker_reasoning_effort: document.getElementById('gs-worker-reasoning-effort').value,
    agent_env_vars: _textToEnv('gs-agent-env-vars'),
    agent_env_file: document.getElementById('gs-agent-env-file').value.trim(),
    git_worktree: document.getElementById('gs-worktree').checked,
    worktree_base_dir: document.getElementById('gs-wt-base-dir').value.trim() || '.torque/worktrees',
    worktree_base_branch: document.getElementById('gs-wt-base-branch').value.trim(),
    worktree_auto_checkpoint: document.getElementById('gs-wt-auto-checkpoint').checked,
    checkpoint_on_progress: document.getElementById('gs-wt-checkpoint-on-progress').checked,
    worktree_merge_squash: document.getElementById('gs-wt-merge-squash').checked,
    engineer_merge_mode: document.getElementById('gs-engineer-merge-mode').value,
    worktree_merge_instructions: document.getElementById('gs-wt-merge-instructions').value.trim(),
    worktree_merge_cleanup: document.getElementById('gs-wt-merge-cleanup').value,
    worktree_merge_preserve_diff: document.getElementById('gs-wt-merge-preserve-diff').checked,
    worktree_symlink_gitignored_paths: document.getElementById('gs-wt-symlink-gitignored').checked,
    worktree_symlinks: _gsWtSymlinks.slice(),
    agent_session_resume: document.getElementById('gs-session-resume').checked,
    agent_idle_timeout: parseInt(document.getElementById('gs-agent-idle-timeout').value) || 0,
    guidance_hint_cadence: Number.isNaN(guidanceHintCadence) ? 4 : guidanceHintCadence,
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
    terminal_shell: document.getElementById('gs-terminal-shell').value,
    terminal_env_vars: _textToEnv('gs-terminal-env-vars'),
    terminal_env_file: document.getElementById('gs-terminal-env-file').value.trim(),
    terminal_always_custom_dialog: document.getElementById('gs-terminal-always-custom').checked,
    /* Board sync */
    board_sync_provider: document.getElementById('gs-board-sync-provider').value || 'none',
    board_sync_enabled: document.getElementById('gs-board-sync-enabled').checked,
    board_sync_github: {
      github_repo: document.getElementById('gs-board-sync-github-repo').value.trim(),
      github_project_owner: document.getElementById('gs-board-sync-github-project-owner').value.trim(),
      github_project_number: boardSyncProjectNumber,
      github_project_id: document.getElementById('gs-board-sync-github-project-id').value.trim(),
      github_project_status_field: document.getElementById('gs-board-sync-github-status-field').value.trim() || 'Status',
      github_lane_status_map: boardSyncLaneMap,
      github_close_issues_via_pr: document.getElementById('gs-board-sync-github-close-via-pr').checked,
      github_create_missing_labels: document.getElementById('gs-board-sync-github-create-labels').checked,
      github_assignee_map: boardSyncAssigneeMap,
    },
    default_engineer_specializations: (_gsEngineerSpecs || []).slice(),
  };
  const engineerSettings = {
    engineer_provider: _getProviderValue('gs-engineer-provider'),
    engineer_boot_command: document.getElementById('gs-engineer-boot-cmd').value.trim(),
    engineer_model: document.getElementById('gs-engineer-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('gs-engineer-reasoning-effort').value,
    engineer_directory: document.getElementById('gs-engineer-directory').value.trim(),
    engineer_shell: document.getElementById('gs-engineer-shell').value,
    custom_instructions: document.getElementById('gs-engineer-custom-instructions').value,
    restrict_to_created_agents: _getEngineerRestrictToCreatedAgentsFromPermission(),
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
    architect_directory: document.getElementById('gs-architect-directory').value.trim(),
    architect_shell: document.getElementById('gs-architect-shell').value,
    architect_custom_instructions: document.getElementById('gs-architect-custom-instructions').value,
    architect_autonomy_mode: document.getElementById('gs-architect-autonomy-mode').value,
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
  const branch = (data.branch || '').replace(/^torque\//, '');

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
var _glsPendingConflict = null; // pending custom in-modal reassign confirmation
var GLS_STATUS_BAR_VISIBILITY_ITEMS = [
  'daemon_status',
  'claude_usage',
  'codex_usage',
  'deploy',
  'health',
  'workload',
  'tasks',
  'attention',
];
var GLS_STATUS_BAR_VISIBILITY_DEFAULTS = {
  daemon_status: false,
  claude_usage: false,
  codex_usage: false,
  deploy: true,
  health: false,
  workload: false,
  tasks: true,
  attention: true,
};

function _glsStatusBarVisibilityDefaults() {
  if (typeof statusBarVisibilityDefaults === 'function') {
    return statusBarVisibilityDefaults();
  }
  var defaults = {};
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    defaults[key] = !!GLS_STATUS_BAR_VISIBILITY_DEFAULTS[key];
  });
  return defaults;
}

function _glsNormalizeStatusBarVisibility(value) {
  if (typeof normalizeStatusBarVisibility === 'function') {
    return normalizeStatusBarVisibility(value);
  }
  var normalized = _glsStatusBarVisibilityDefaults();
  var raw = (value && typeof value === 'object') ? value : {};
  Object.keys(normalized).forEach(function(key) {
    if (Object.prototype.hasOwnProperty.call(raw, key)) {
      var itemValue = raw[key];
      normalized[key] = (typeof itemValue === 'string')
        ? ['1', 'true', 'yes', 'on'].indexOf(itemValue.trim().toLowerCase()) >= 0
        : !!itemValue;
    }
  });
  return normalized;
}

function _glsStatusBarInputId(key) {
  return 'gls-statusbar-' + String(key || '').replace(/_/g, '-');
}

function _syncStatusBarSettingsFromGlobal(settings, opts) {
  opts = opts || {};
  var s = settings || (state && state.global_settings) || {};
  var visibility = _glsNormalizeStatusBarVisibility(s.status_bar_visibility);
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    var input = document.getElementById(_glsStatusBarInputId(key));
    if (!input) return;
    var locked = !opts.force && (
      (typeof document !== 'undefined' && document.activeElement === input)
      || (input.dataset && input.dataset.statusBarDirty === '1')
    );
    if (!locked) input.checked = !!visibility[key];
    if (opts.force && input.dataset) delete input.dataset.statusBarDirty;
  });
}

function _collectStatusBarVisibilitySettings() {
  var visibility = _glsStatusBarVisibilityDefaults();
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    var input = document.getElementById(_glsStatusBarInputId(key));
    if (input) visibility[key] = !!input.checked;
  });
  return visibility;
}

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

function _formatDaemonDurationFromMs(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  var seconds = Math.floor(ms / 1000);
  if (seconds < 60) return seconds + ' second' + (seconds === 1 ? '' : 's');
  var minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + ' minute' + (minutes === 1 ? '' : 's');
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' hour' + (hours === 1 ? '' : 's');
  var days = Math.floor(hours / 24);
  return days + ' day' + (days === 1 ? '' : 's');
}

function _formatDaemonRelativeTime(startedAtSeconds, nowMs) {
  var started = Number(startedAtSeconds);
  if (!Number.isFinite(started) || started <= 0) return '—';
  var current = Number.isFinite(nowMs) ? nowMs : Date.now();
  var elapsedMs = Math.max(0, current - (started * 1000));
  if (elapsedMs < 5000) return 'just now';
  return _formatDaemonDurationFromMs(elapsedMs) + ' ago';
}

function _formatDaemonAbsoluteTime(startedAtSeconds) {
  var started = Number(startedAtSeconds);
  if (!Number.isFinite(started) || started <= 0) return '';
  return new Date(started * 1000).toLocaleString();
}

function _daemonDisplayValue(value, fallback) {
  if (value === null || value === undefined || value === '') return fallback || '—';
  return String(value);
}

function _setDaemonStatusText(id, value, title) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  if (title) el.title = title;
}

function _daemonWsConnected() {
  if (typeof ws !== 'undefined'
      && typeof WebSocket !== 'undefined'
      && ws
      && ws.readyState === WebSocket.OPEN) {
    return true;
  }
  var dot = document.getElementById('conn-dot');
  return !!(dot && dot.classList && dot.classList.contains('ok'));
}

function _wireDaemonStatusActions() {
  var restartBtn = document.getElementById('gls-restart-daemon-btn');
  if (restartBtn && typeof restartDaemon === 'function') {
    restartBtn.onclick = restartDaemon;
  }
  var stopBtn = document.getElementById('gls-stop-daemon-btn');
  if (stopBtn) {
    if (typeof stopDaemon === 'function') {
      stopBtn.onclick = stopDaemon;
      stopBtn.disabled = false;
      stopBtn.classList.remove('disabled');
      stopBtn.title = '';
    } else {
      stopBtn.onclick = null;
      stopBtn.disabled = true;
      stopBtn.classList.add('disabled');
      stopBtn.title = 'Pending daemon stop endpoint (:353)';
    }
  }
}

function loadDaemonStatus() {
  var runtime = (state && state.runtime) || {};
  var connected = _daemonWsConnected();
  var statusDot = document.getElementById('gls-daemon-status-dot');
  if (statusDot) {
    statusDot.classList.toggle('daemon-status-dot-ok', connected);
    statusDot.classList.toggle('daemon-status-dot-offline', !connected);
  }
  _setDaemonStatusText(
    'gls-daemon-status-text',
    connected ? 'Running' : 'Disconnected'
  );
  _setDaemonStatusText('gls-daemon-version', _daemonDisplayValue(runtime.version, 'unknown'));
  _setDaemonStatusText('gls-daemon-pid', _daemonDisplayValue(runtime.pid));
  _setDaemonStatusText('gls-daemon-uptime', _formatDaemonDurationFromMs(
    Date.now() - (Number(runtime.started_at) * 1000)
  ));
  _setDaemonStatusText('gls-daemon-port', _daemonDisplayValue(runtime.port));
  _setDaemonStatusText('gls-daemon-profile', _daemonDisplayValue(runtime.profile, 'default'));
  _setDaemonStatusText('gls-daemon-data-dir', _daemonDisplayValue(runtime.data_dir));
  _setDaemonStatusText('gls-daemon-log-path', _daemonDisplayValue(runtime.log_path));
  _setDaemonStatusText(
    'gls-daemon-started-at',
    _formatDaemonRelativeTime(runtime.started_at),
    _formatDaemonAbsoluteTime(runtime.started_at) || 'Time the daemon started'
  );
  // Relay-connection detail row (TORQUE:560). Driven from
  // `state.relay_connection`; hides itself when the field is absent.
  if (typeof _relayStatusRenderModalRow === 'function') _relayStatusRenderModalRow();
  _wireDaemonStatusActions();
}

function switchGlsTab(name) {
  document.querySelectorAll('#modal-global-settings .gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('#modal-global-settings .gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
  if (name === 'gls-system') loadDaemonStatus();
}

var _glsActiveSubTabs = {};

function _pickGlsSubTab(pane, preferred) {
  if (!pane || !pane.querySelectorAll) return null;
  var tabs = Array.prototype.slice.call(pane.querySelectorAll('.gs-subtab'));
  if (!tabs.length) return null;
  if (preferred) {
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].dataset && tabs[i].dataset.subtab === preferred && !tabs[i].hidden) {
        return tabs[i];
      }
    }
  }
  for (var j = 0; j < tabs.length; j++) {
    if (!tabs[j].hidden) return tabs[j];
  }
  return tabs[0];
}

function _syncGlsSubTabs(restoreSelection) {
  document.querySelectorAll('#modal-global-settings .gs-pane').forEach(function(pane) {
    var paneName = pane.dataset ? pane.dataset.pane : '';
    var preferred = restoreSelection && paneName ? _glsActiveSubTabs[paneName] : '';
    var tab = _pickGlsSubTab(pane, preferred);
    if (tab) switchGlsSubTab(tab);
  });
}

function switchGlsSubTab(btn) {
  var container = btn.closest('.gs-pane');
  if (!container) return;
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  var target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
  if (container.dataset && container.dataset.pane && target) {
    _glsActiveSubTabs[container.dataset.pane] = target;
  }
}

function openGlobalSettings() {
  send({ cmd: 'get_global_settings' });
}

function _showGlobalSettingsModal(data) {
  var s = data.settings;
  var modal = document.getElementById('modal-global-settings');
  var modalWasVisible = modal && modal.classList && modal.classList.contains('visible');
  var activeTab = modalWasVisible
    ? document.querySelector('#modal-global-settings .gs-tab.active')
    : null;
  var activeTabName = activeTab && activeTab.dataset ? activeTab.dataset.tab : '';
  _glsDefaults = typeof keybindingDefaults === 'function'
    ? keybindingDefaults()
    : (data.keybinding_defaults || {});
  _glsKeybindings = typeof sanitizeKeybindingOverrides === 'function'
    ? sanitizeKeybindingOverrides(s.keybindings || {})
    : Object.assign({}, s.keybindings || {});
  _glsPendingConflict = null;

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
  document.getElementById('gls-max-pipeline-depth').value =
    s.max_pipeline_depth !== undefined ? s.max_pipeline_depth : 10;

  // Status bar
  _syncStatusBarSettingsFromGlobal(s, { force: !modalWasVisible });

  // Keybindings
  _renderKeybindingList();

  // Relay config (TORQUE:603 #1). The get_global_settings response
  // carries a fresh top-level `relay_config` (same shape as the snapshot / the
  // `relay_config` delta); adopt it into `state` and force-populate the editable
  // settings-layer inputs. force=true clears any stale dirty
  // flags so the freshly opened modal reflects the authoritative resolved config.
  if (data.relay_config) state.relay_config = data.relay_config;
  if (typeof refreshRelayConfigModal === 'function') {
    refreshRelayConfigModal({ force: true });
  }

  if (modalWasVisible && activeTabName) switchGlsTab(activeTabName);
  else switchGlsTab('gls-general');
  _syncGlsSubTabs(modalWasVisible);

  modal.classList.add('visible');
  if (!modalWasVisible) document.getElementById('gls-default-cmd').focus();
}

function _kbDefaultBinding(action) {
  var def = _glsDefaults[action] || {};
  if (typeof normalizeKeybindingDescriptor === 'function') {
    return normalizeKeybindingDescriptor(def.defaultBinding);
  }
  return def.defaultBinding || null;
}

function _kbOverrideBinding(action) {
  if (!_glsKeybindings || !_glsKeybindings[action]) return null;
  if (typeof normalizeKeybindingDescriptor === 'function') {
    return normalizeKeybindingDescriptor(_glsKeybindings[action]);
  }
  return _glsKeybindings[action] || null;
}

function _kbEffectiveBindingForSettings(action) {
  return _kbOverrideBinding(action) || _kbDefaultBinding(action);
}

function _kbDisplayName(action, binding) {
  var b = binding || _kbEffectiveBindingForSettings(action);
  if (typeof kbBindingDisplayName === 'function') return kbBindingDisplayName(b);
  return b && b.key ? b.key : 'Unassigned';
}

function _kbBindingSame(a, b) {
  if (typeof _kbSameBinding === 'function') return _kbSameBinding(a, b);
  if (!a || !b) return false;
  return String(a.key || '').toLowerCase() === String(b.key || '').toLowerCase()
    && !!a.ctrl === !!b.ctrl
    && !!a.meta === !!b.meta
    && !!a.alt === !!b.alt
    && !!a.shift === !!b.shift;
}

function _kbBindingFingerprintLocal(binding) {
  if (typeof _kbBindingFingerprint === 'function') return _kbBindingFingerprint(binding);
  if (!binding) return '';
  return [String(binding.key || '').toLowerCase(), binding.ctrl ? 1 : 0,
    binding.meta ? 1 : 0, binding.alt ? 1 : 0, binding.shift ? 1 : 0].join('|');
}

function _kbActionLabel(action) {
  var def = _glsDefaults[action] || {};
  return def.label || action;
}

function _kbActionOrder() {
  return Object.keys(_glsDefaults || {}).sort(function(a, b) {
    var ao = typeof _glsDefaults[a].order === 'number' ? _glsDefaults[a].order : 1000;
    var bo = typeof _glsDefaults[b].order === 'number' ? _glsDefaults[b].order : 1000;
    if (ao !== bo) return ao - bo;
    return a < b ? -1 : (a > b ? 1 : 0);
  });
}

function _kbFindConflict(action, binding) {
  var fp = _kbBindingFingerprintLocal(binding);
  if (!fp) return null;
  var actions = _kbActionOrder();
  for (var i = 0; i < actions.length; i++) {
    var other = actions[i];
    if (other === action) continue;
    var def = _glsDefaults[other] || {};
    if (Array.isArray(def.defaultBindings) && def.defaultBindings.length) {
      for (var j = 0; j < def.defaultBindings.length; j++) {
        var fixedBinding = def.defaultBindings[j];
        if (_kbBindingFingerprintLocal(fixedBinding) === fp) {
          return { action: other, binding: fixedBinding, fixed: true };
        }
      }
      continue;
    }
    var otherBinding = _kbEffectiveBindingForSettings(other);
    if (_kbBindingFingerprintLocal(otherBinding) === fp) {
      return { action: other, binding: otherBinding, fixed: !!def.fixed };
    }
  }
  return null;
}

function _kbSetOverride(action, binding) {
  if (!_glsKeybindings) _glsKeybindings = {};
  var normalized = typeof normalizeKeybindingDescriptor === 'function'
    ? normalizeKeybindingDescriptor(binding)
    : binding;
  if (!normalized) return;
  var defBinding = _kbDefaultBinding(action);
  if (defBinding && _kbBindingSame(normalized, defBinding)) delete _glsKeybindings[action];
  else _glsKeybindings[action] = normalized;
}

function _kbApplyBindingWithConflictCheck(action, binding, reset) {
  var normalized = typeof normalizeKeybindingDescriptor === 'function'
    ? normalizeKeybindingDescriptor(binding)
    : binding;
  if (!normalized) return;
  var conflict = _kbFindConflict(action, normalized);
  if (conflict) {
    _glsPendingConflict = {
      action: action,
      binding: normalized,
      reset: !!reset,
      conflictAction: conflict.action,
      conflictBinding: conflict.binding,
      fixed: !!conflict.fixed,
      previousBinding: _kbEffectiveBindingForSettings(action),
    };
    _renderKeybindingList();
    return;
  }
  if (reset) delete _glsKeybindings[action];
  else _kbSetOverride(action, normalized);
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _kbConflictWarningHtml() {
  if (!_glsPendingConflict) return '';
  var pending = _glsPendingConflict;
  var actionLabel = _kbActionLabel(pending.action);
  var conflictLabel = _kbActionLabel(pending.conflictAction);
  var combo = _kbDisplayName(pending.action, pending.binding);
  var html = '<div class="kb-conflict-warning" role="alert">';
  html += '<div><strong>' + esc(combo) + '</strong> is already assigned to '
    + '<strong>' + esc(conflictLabel) + '</strong>.</div>';
  if (pending.fixed) {
    html += '<div class="kb-conflict-copy">That shortcut is part of a fixed key cluster. Choose another shortcut for '
      + esc(actionLabel) + '.</div>';
    html += '<div class="kb-conflict-actions">'
      + '<button type="button" class="kb-btn" onclick="_cancelKeybindingConflict()">OK</button>'
      + '</div>';
  } else {
    var previous = _kbDisplayName(pending.conflictAction, pending.previousBinding);
    html += '<div class="kb-conflict-copy">Reassign it to ' + esc(actionLabel)
      + ' and move ' + esc(conflictLabel) + ' to '
      + '<strong>' + esc(previous) + '</strong>?</div>';
    html += '<div class="kb-conflict-actions">'
      + '<button type="button" class="kb-btn kb-btn-primary" onclick="_confirmKeybindingReassign()">Reassign</button>'
      + '<button type="button" class="kb-btn" onclick="_cancelKeybindingConflict()">Cancel</button>'
      + '</div>';
  }
  html += '</div>';
  return html;
}

function _renderKeybindingList() {
  var container = document.getElementById('gls-keybinding-list');
  if (!container) return;
  var scrollTop = container.scrollTop || 0;
  var html = '';
  html += _kbConflictWarningHtml();
  var actions = _kbActionOrder();
  for (var ai = 0; ai < actions.length; ai++) {
    var action = actions[ai];
    var def = _glsDefaults[action];
    var current = _kbOverrideBinding(action);
    var display = _kbDisplayName(action, current);
    var label = def.label || action;
    var isCapturing = _glsCapturing === action;
    html += '<div class="kb-row" data-keybinding-action="' + esc(action) + '">';
    html += '  <span class="kb-label">' + esc(label);
    if (def.description) html += '<span class="kb-description">' + esc(def.description) + '</span>';
    html += '</span>';
    if (isCapturing) {
      html += '  <span class="kb-combo kb-capturing">Press keys\u2026</span>';
      html += '  <button class="kb-btn" onclick="_cancelCapture()">Cancel</button>';
    } else if (def.fixed) {
      html += '  <span class="kb-combo">' + esc(def.display || display) + '</span>';
      html += '  <span class="kb-fixed">Fixed</span>';
    } else {
      html += '  <span class="kb-combo">' + esc(display) + '</span>';
      html += '  <button class="kb-btn" onclick="_startCapture(\'' + action + '\')">Rebind</button>';
      if (current) {
        html += '  <button class="kb-btn" onclick="_resetKeybinding(\'' + action + '\')">Reset</button>';
      }
    }
    html += '</div>';
  }
  container.innerHTML = html;
  container.scrollTop = scrollTop;
}

function _startCapture(action) {
  _glsCapturing = action;
  _glsPendingConflict = null;
  _renderKeybindingList();
  document.addEventListener('keydown', _captureKeydown, true);
}

function _cancelCapture() {
  _glsCapturing = null;
  document.removeEventListener('keydown', _captureKeydown, true);
  _renderKeybindingList();
}

function _cancelKeybindingConflict() {
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _confirmKeybindingReassign() {
  var pending = _glsPendingConflict;
  if (!pending || pending.fixed) {
    _cancelKeybindingConflict();
    return;
  }
  if (pending.reset) delete _glsKeybindings[pending.action];
  else _kbSetOverride(pending.action, pending.binding);
  if (pending.previousBinding) _kbSetOverride(pending.conflictAction, pending.previousBinding);
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _captureKeydown(e) {
  e.preventDefault();
  e.stopPropagation();
  // Ignore bare modifier presses
  if (['Meta', 'Alt', 'Shift', 'Control'].includes(e.key)) return;

  var action = _glsCapturing;
  var binding = typeof keybindingDescriptorFromEvent === 'function'
    ? keybindingDescriptorFromEvent(e)
    : null;
  _glsCapturing = null;
  document.removeEventListener('keydown', _captureKeydown, true);
  if (action && binding) _kbApplyBindingWithConflictCheck(action, binding, false);
  else _renderKeybindingList();
}

function _resetKeybinding(action) {
  var defBinding = _kbDefaultBinding(action);
  if (!defBinding) {
    delete _glsKeybindings[action];
    _renderKeybindingList();
    return;
  }
  _kbApplyBindingWithConflictCheck(action, defBinding, true);
}

function _syncKeybindingSettingsFromGlobal(settings) {
  var s = settings || (state && state.global_settings) || {};
  if (!_glsCapturing) {
    _glsKeybindings = typeof sanitizeKeybindingOverrides === 'function'
      ? sanitizeKeybindingOverrides(s.keybindings || {})
      : Object.assign({}, s.keybindings || {});
  }
  _renderKeybindingList();
}

function submitGlobalSettings() {
  var xtermScrollback = _parseGlsXtermScrollback();
  if (xtermScrollback === null) return;

  var settings = {
    default_command: document.getElementById('gls-default-cmd').value.trim(),
    filter_by_window: document.getElementById('gls-filter-window').checked,
    focus_new_tabs: document.getElementById('gls-focus-new-tabs').checked,
    focus_on_click: document.getElementById('gls-focus-on-click').checked,
    xterm_scrollback: xtermScrollback,
    keybindings: typeof sanitizeKeybindingOverrides === 'function'
      ? sanitizeKeybindingOverrides(_glsKeybindings)
      : _glsKeybindings,
    status_bar_visibility: _collectStatusBarVisibilitySettings(),
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

  // Relay config (TORQUE:603 #1). Editable settings-layer overrides; the daemon
  // applies on change (stop+restart the connector) and the :601 relay_connection
  // signal reports the result. Text fields are sent trimmed — an EMPTY value is
  // a deliberate "no settings override; inherit from ee_connector.json / env"
  // (the backend only flows NON-EMPTY settings values into the connector config,
  // so re-sending "" for an untouched inherited field preserves its fallback).
  // private_key_path is BY PATH only — never inline PEM.
  //
  // `relay_enabled` is TRI-STATE/inherit (TORQUE:603 #1 review): unlike the text
  // fields (where empty="" is the inherit signal), a checkbox has no empty state
  // and reflects the EFFECTIVE config.enabled — which may be sourced from env /
  // ee_connector.json. Always sending it would silently PROMOTE an inherited
  // enabled into a settings-layer override on any unrelated save (a provenance
  // surprise). So send relay_enabled ONLY when the operator EXPLICITLY toggled
  // it (dataset.relayDirty, set by the checkbox onchange); an untouched checkbox
  // is omitted, and update_global_settings leaves the existing (inherited) value
  // unchanged.
  var relayEnabledEl = document.getElementById('gls-relay-enabled');
  if (relayEnabledEl && relayEnabledEl.dataset
      && relayEnabledEl.dataset.relayDirty === '1') {
    settings.relay_enabled = !!relayEnabledEl.checked;
  }
  var relayUrlEl = document.getElementById('gls-relay-url');
  if (relayUrlEl) settings.relay_url = relayUrlEl.value.trim();
  var relayDaemonIdEl = document.getElementById('gls-relay-daemon-id');
  if (relayDaemonIdEl) settings.relay_daemon_id = relayDaemonIdEl.value.trim();
  var relayCredentialIdEl = document.getElementById('gls-relay-credential-id');
  if (relayCredentialIdEl) settings.relay_credential_id = relayCredentialIdEl.value.trim();
  var relayPrivateKeyPathEl = document.getElementById('gls-relay-private-key-path');
  if (relayPrivateKeyPathEl) settings.relay_private_key_path = relayPrivateKeyPathEl.value.trim();

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
    var varNames = act.vars.filter(function(v) { return v.name !== 'TASK' && v.name !== 'torque'; })
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
    if (v === 'TASK' || v === 'torque') continue;
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

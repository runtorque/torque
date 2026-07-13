/* Modal state and agent-management forms. */

function _orderedSpecializationsListMatchesGroup(group) {
  if (!state) return false;
  return String((state.specializations_group) || '') === String(group || '');
}

function _orderedSpecializationsCatalog(group, opts) {
  opts = opts || {};
  const matchesGroup = _orderedSpecializationsListMatchesGroup(group);
  const raw = matchesGroup && state && Array.isArray(state.specializations)
    ? state.specializations
    : [];
  const names = [];
  const metaByName = {};
  const seen = new Set();
  raw.forEach(function(item) {
    if (!item) return;
    if (opts.projectOnly && !!item.global) return;
    const name = String(item.name || '').trim();
    if (!name || seen.has(name)) return;
    seen.add(name);
    names.push(name);
    metaByName[name] = item;
  });
  return {
    matchesGroup: matchesGroup,
    names: names,
    metaByName: metaByName,
  };
}

function _normalizeOrderedSpecializationSelection(raw, availableNames, opts) {
  opts = opts || {};
  const out = [];
  const seen = new Set();
  const available = Array.isArray(availableNames) ? availableNames : [];
  const availableSet = available.length ? new Set(available) : null;
  (Array.isArray(raw) ? raw : []).forEach(function(item) {
    const name = String(item || '').trim();
    if (!name || seen.has(name)) return;
    if (opts.filterKnown && availableSet && !availableSet.has(name)) return;
    seen.add(name);
    out.push(name);
  });
  return out;
}

function _orderedSpecializationsEqual(a, b) {
  a = Array.isArray(a) ? a : [];
  b = Array.isArray(b) ? b : [];
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function _renderOrderedSpecializationsPicker(opts) {
  opts = opts || {};
  const selectedEl = document.getElementById(opts.selectedId || '');
  const availableEl = document.getElementById(opts.availableId || '');
  if (!selectedEl || !availableEl) return;
  const catalog = _orderedSpecializationsCatalog(opts.group || '', {
    projectOnly: opts.projectOnly !== false,
  });
  const selected = _normalizeOrderedSpecializationSelection(
    typeof opts.getSelected === 'function' ? opts.getSelected() : [],
    catalog.names,
    { filterKnown: opts.filterKnown !== false && catalog.matchesGroup }
  );
  if (typeof opts.setSelected === 'function'
      && !_orderedSpecializationsEqual(selected, opts.getSelected())) {
    opts.setSelected(selected.slice());
  }

  selectedEl.innerHTML = '';
  selected.forEach(function(name, idx) {
    const li = document.createElement('li');
    li.className = 'specialization-entry';
    const label = document.createElement('span');
    label.className = 'specialization-entry-label';
    label.textContent = name + (idx === 0 ? ' (primary)' : '');
    li.appendChild(label);

    const controls = document.createElement('span');
    controls.className = 'specialization-controls-row';
    if (idx > 0) {
      const up = document.createElement('button');
      up.type = 'button';
      up.textContent = '↑';
      up.title = 'Move up';
      up.onclick = function() {
        if (typeof opts.onMove === 'function') opts.onMove(idx, -1);
      };
      controls.appendChild(up);
    }
    if (idx < selected.length - 1) {
      const down = document.createElement('button');
      down.type = 'button';
      down.textContent = '↓';
      down.title = 'Move down';
      down.onclick = function() {
        if (typeof opts.onMove === 'function') opts.onMove(idx, 1);
      };
      controls.appendChild(down);
    }
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = 'Remove';
    remove.onclick = function() {
      if (typeof opts.onRemove === 'function') opts.onRemove(idx);
    };
    controls.appendChild(remove);
    li.appendChild(controls);
    selectedEl.appendChild(li);
  });

  availableEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = !catalog.matchesGroup
    ? 'Loading specializations...'
    : (catalog.names.length ? 'Pick a specialization...' : 'No specializations available');
  availableEl.appendChild(placeholder);
  catalog.names.forEach(function(name) {
    if (selected.indexOf(name) >= 0) return;
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    const meta = catalog.metaByName[name];
    if (meta && meta.preamble) opt.title = String(meta.preamble).slice(0, 200);
    availableEl.appendChild(opt);
  });
}

function _addEngineerSpecializationsVisible() {
  return !!String(_addEngineerArchitectId || '').trim();
}

function _addEngineerCurrentSpecializations() {
  const catalog = _orderedSpecializationsCatalog(
    _addEngineerSpecializationsGroup || _addEngineerGroup || '',
    { projectOnly: true }
  );
  return _normalizeOrderedSpecializationSelection(
    _addEngineerSpecs,
    catalog.names,
    { filterKnown: catalog.matchesGroup }
  );
}

function renderAddEngineerSpecializations() {
  const row = document.getElementById('engineer-specializations-row');
  if (!row) return;
  if (!_addEngineerSpecializationsVisible()) {
    row.classList.add('hidden');
    return;
  }
  row.classList.remove('hidden');
  _renderOrderedSpecializationsPicker({
    selectedId: 'engineer-specializations-selected',
    availableId: 'engineer-specializations-available',
    group: _addEngineerSpecializationsGroup || _addEngineerGroup || '',
    projectOnly: true,
    getSelected: function() { return _addEngineerSpecs || []; },
    setSelected: function(next) { _addEngineerSpecs = next; },
    onMove: addEngineerMoveSpecialization,
    onRemove: addEngineerRemoveSpecialization,
  });
}

function addEngineerAddSpecialization() {
  if (!_addEngineerSpecializationsVisible()) return;
  const availableEl = document.getElementById('engineer-specializations-available');
  if (!availableEl) return;
  const name = String(availableEl.value || '').trim();
  if (!name) return;
  const catalog = _orderedSpecializationsCatalog(
    _addEngineerSpecializationsGroup || _addEngineerGroup || '',
    { projectOnly: true }
  );
  if (catalog.matchesGroup && catalog.names.indexOf(name) < 0) return;
  if (_addEngineerSpecs.indexOf(name) < 0) _addEngineerSpecs.push(name);
  _addEngineerSpecs = _normalizeOrderedSpecializationSelection(
    _addEngineerSpecs,
    catalog.names,
    { filterKnown: catalog.matchesGroup }
  );
  renderAddEngineerSpecializations();
}

function addEngineerRemoveSpecialization(idx) {
  if (idx < 0 || idx >= _addEngineerSpecs.length) return;
  _addEngineerSpecs.splice(idx, 1);
  renderAddEngineerSpecializations();
}

function addEngineerMoveSpecialization(idx, delta) {
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= _addEngineerSpecs.length) return;
  const moved = _addEngineerSpecs.splice(idx, 1)[0];
  _addEngineerSpecs.splice(newIdx, 0, moved);
  renderAddEngineerSpecializations();
}

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
  const architect = ctx.hired_by_architect_id && state && state.agents
    ? state.agents[ctx.hired_by_architect_id]
    : null;
  _addEngineerGroup = ctx.group || (architect ? String(architect.group || '') : '');
  _addEngineerArchitectId = ctx.hired_by_architect_id;
  _addEngineerSpecializationsGroup = _addEngineerGroup;
  _addEngineerSpecs = [];
  const nameInput = document.getElementById('engineer-name-input');
  const commandInput = document.getElementById('engineer-command-input');
  const summary = document.getElementById('modal-engineer-summary');
  const title = document.getElementById('modal-engineer-title');
  const submitBtn = document.getElementById('engineer-submit-btn');
  if (title) title.textContent = _addEngineerArchitectId ? 'Hire Engineer' : 'Add Engineer';
  if (submitBtn) submitBtn.textContent = _addEngineerArchitectId ? 'Request Hire' : 'Create Engineer';
  if (summary) {
    summary.textContent = _engineerModalSummary(_addEngineerGroup, _addEngineerArchitectId);
    summary.classList.remove('hidden');
  }
  if (nameInput) nameInput.value = '';
  if (commandInput) commandInput.value = '';
  const classRow = document.getElementById('engineer-agent-class-row');
  if (classRow) classRow.classList.toggle('hidden', !!_addEngineerArchitectId);
  if (!_addEngineerArchitectId && typeof agentClassPickerPrepare === 'function') {
    agentClassPickerPrepare('engineer', _addEngineerGroup, agentClassBaseDirForGroup(_addEngineerGroup), 'add-engineer');
  }
  if (_addEngineerArchitectId && typeof send === 'function') {
    send({ cmd: 'list_specializations', group: _addEngineerSpecializationsGroup || '' });
  }
  renderAddEngineerSpecializations();
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
  const payload = _addEngineerArchitectId
    ? {
      cmd: 'architect_engineer_hire',
      architect_id: _addEngineerArchitectId,
      name: name,
      specializations: _addEngineerCurrentSpecializations(),
    }
    : { cmd: 'add_engineer', name };
  if (!_addEngineerArchitectId && _addEngineerGroup) payload.group = _addEngineerGroup;
  if (!_addEngineerArchitectId) {
    if (typeof agentClassPickerSubmitSelection === 'function') {
      const agentClassState = agentClassPickerSubmitSelection('add-engineer');
      if (!agentClassState) return;
      if (!agentClassState.defaultSelected) payload.agent_class_id = agentClassState.selectedId;
    } else if (typeof agentClassPickerSelected === 'function') {
      const agentClassId = agentClassPickerSelected('add-engineer');
      if (agentClassId) payload.agent_class_id = agentClassId;
    }
  }
  if (command) payload.command = command;
  send(payload);
  if (_addEngineerArchitectId && typeof _showToast === 'function') {
    _showToast('Engineer hire requested', 'success');
  }
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
  if (typeof agentClassPickerPrepare === 'function') {
    agentClassPickerPrepare('architect', _addArchitectGroup, agentClassBaseDirForGroup(_addArchitectGroup), 'add-architect');
  }
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
  if (typeof agentClassPickerSubmitSelection === 'function') {
    const agentClassState = agentClassPickerSubmitSelection('add-architect');
    if (!agentClassState) return;
    if (!agentClassState.defaultSelected) payload.agent_class_id = agentClassState.selectedId;
  } else if (typeof agentClassPickerSelected === 'function') {
    const agentClassId = agentClassPickerSelected('add-architect');
    if (agentClassId) payload.agent_class_id = agentClassId;
  }
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

let _taskEditId = null;  // null = create mode, string = edit mode
let _taskDraftId = '';          // pre-generated ID for new tasks (for attachments)
let _taskAttachments = [];      // current attachments [{path, filename, mime_type}]
let _taskOriginalAttachments = []; // attachments at modal open (for cancel cleanup)
let _taskArtifacts = [];        // structured task artifacts
let _taskOriginalArtifacts = []; // artifacts at modal open (for cancel cleanup)
let _taskActions = [];          // cached action list for task modal
let _taskTemplates = [];        // cached roles/templates for task modal
let _taskSelectedAction = '';   // selected action name
let _taskSelectedTemplate = ''; // selected role name
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
var _labelDropdownIdx = -1;
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
    // wire field 'agent_template' is the legacy name; semantically this is the role slug (stage 2).
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
  var attributionEl = document.getElementById('task-created-by-attribution');

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
  if (attributionEl) {
    if (config.createdByTask && typeof _taskCreatedByDetailHtml === 'function') {
      attributionEl.innerHTML = _taskCreatedByDetailHtml(config.createdByTask);
      attributionEl.classList.remove('hidden');
    } else {
      attributionEl.innerHTML = '';
      attributionEl.classList.add('hidden');
    }
  }

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
  send({ cmd: 'list_roles', group: (groupEl && groupEl.value) || _currentGroup() });

  modal.classList.add('visible');
  var bodyEl = document.getElementById('task-modal-body');
  if (bodyEl) bodyEl.scrollTop = 0;
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
    if (title.indexOf(val) >= 0 || id.indexOf(val) >= 0) {
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
  if (typeof selectedTerminalId !== 'undefined'
      && selectedTerminalId
      && state && state.agents && state.agents[selectedTerminalId]) {
    return state.agents[selectedTerminalId].group;
  }
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
    createdByTask: t,
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
    agent_template: _taskSelectedTemplate,
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
  _populateTaskTemplateSelect(msg.roles || msg.templates || []);
}

function taskAutoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

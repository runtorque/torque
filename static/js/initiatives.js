/* ------------------------------------------------------------------ */
/* Planning / Initiatives panel                                       */
/* ------------------------------------------------------------------ */

var INITIATIVE_PRIMARY_STATUSES = ['now', 'next', 'later'];
var INITIATIVE_SECONDARY_STATUSES = ['triage', 'parked', 'shipped'];
var INITIATIVE_ALL_STATUSES = ['triage', 'now', 'next', 'later', 'parked', 'shipped'];
var INITIATIVE_SCOPE_FIELDS = [
  'title',
  'planning_status',
  'priority',
  'summary',
  'why',
  'in_scope',
  'out_of_scope',
  'done_definition',
];
var PLANNING_TABS = ['initiatives', 'areas'];
var AREA_LIFECYCLES = ['planned', 'experimental', 'active_investment', 'stable', 'maintenance', 'deprecated', 'retired'];
var AREA_NOTE_TYPES = ['caveat', 'tech_debt', 'open_question', 'follow_up', 'invariant'];
var AREA_LINK_RELATIONS = ['related', 'depends_on', 'supports'];
var AREA_SCOPE_FIELDS = [
  'title',
  'area_type',
  'lifecycle',
  'summary',
  'user_purpose',
  'system_purpose',
  'in_scope',
  'out_of_scope',
];

var _planningStateRef = null;
var _initiativesLoadedGroup = null;
var _initiativesLoadingGroup = null;
var _initiativesSelectedId = '';
var _initiativesDetail = null;
var _initiativesDetailLoadingId = '';
var _initiativesSaving = false;
var _initiativesLastError = '';
var _initiativesSecondaryExpanded = {};
var _initiativesDraftsById = {};
var _initiativesTaskCreatePending = null;
var _initiativesTaskCreateLinking = null;
var _initiativesTaskCreateStatusById = {};
var _planningActiveTab = 'initiatives';
var _areasLoadedGroup = null;
var _areasLoadingGroup = null;
var _areasSelectedId = '';
var _areasDetail = null;
var _areasDetailLoadingId = '';
var _areasSaving = false;
var _areasLastError = '';
var _areasSearch = '';
var _areasLifecycleFilter = '';
var _areasTypeFilter = '';
var _areasDraftsById = {};
var _areasNoteDraftsByKey = {};
var _areasEditingNoteId = '';
var _areasSectionExpanded = {
  initiatives: true,
  tasks: true,
  decisions: true,
  areas: true,
  notes: true,
};

function _planningSyncStateReference() {
  if (typeof state === 'undefined' || !state) return;
  if (_planningStateRef === state) return;
  _planningStateRef = state;
  _initiativesLoadedGroup = null;
  _initiativesLoadingGroup = null;
  _areasLoadedGroup = null;
  _areasLoadingGroup = null;
}

function _initiativesGroup() {
  _planningSyncStateReference();
  if (typeof _currentGroup === 'function') return _currentGroup() || '';
  return (state && state.active_group) || '';
}

function _initiativesPanelVisible() {
  return !!(
    (typeof _panelAppVisible === 'function' && _panelAppVisible('initiatives'))
    || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'initiatives')
  );
}

function _planningTabVisible(tab) {
  if (!_initiativesPanelVisible()) return false;
  return String(_planningActiveTab || 'initiatives') === String(tab || 'initiatives');
}

function planningSetTab(tab) {
  tab = String(tab || 'initiatives');
  if (PLANNING_TABS.indexOf(tab) < 0) tab = 'initiatives';
  _initiativesCaptureDrafts();
  _areasCaptureDrafts();
  _areasCaptureNoteDrafts();
  _planningActiveTab = tab;
  if (tab === 'areas') areasEnsureLoaded();
  else initiativesEnsureLoaded();
  if (typeof renderInitiativesPanel === 'function') renderInitiativesPanel();
}

function _initiativesEnsureState() {
  _planningSyncStateReference();
  if (!state) state = {};
  if (!state.initiatives || typeof state.initiatives !== 'object') state.initiatives = {};
}

function _areasEnsureState() {
  _planningSyncStateReference();
  if (!state) state = {};
  if (!state.areas || typeof state.areas !== 'object') state.areas = {};
}

function _initiativesListForGroup(group) {
  _initiativesEnsureState();
  var g = String(group || '');
  var items = [];
  for (var id in state.initiatives) {
    var item = state.initiatives[id];
    if (!item) continue;
    if (g && String(item.group || item.group_name || '') !== g) continue;
    if (item.archived || String(item.archived_at || '').trim()) continue;
    items.push(item);
  }
  items.sort(function(a, b) {
    var ap = INITIATIVE_ALL_STATUSES.indexOf(String(a.planning_status || 'triage'));
    var bp = INITIATIVE_ALL_STATUSES.indexOf(String(b.planning_status || 'triage'));
    if (ap !== bp) return ap - bp;
    var apr = String(a.priority || '').toLowerCase();
    var bpr = String(b.priority || '').toLowerCase();
    if (apr !== bpr) return apr.localeCompare(bpr);
    return String(a.title || a.id || '').localeCompare(String(b.title || b.id || ''));
  });
  return items;
}

function _initiativesByStatus(group) {
  var buckets = {};
  INITIATIVE_ALL_STATUSES.forEach(function(status) { buckets[status] = []; });
  _initiativesListForGroup(group).forEach(function(item) {
    var status = String(item.planning_status || 'triage').toLowerCase();
    if (!buckets[status]) status = 'triage';
    buckets[status].push(item);
  });
  return buckets;
}

function initiativesEnsureLoaded(opts) {
  opts = opts || {};
  var group = _initiativesGroup();
  if (!group && !opts.force) return false;
  if (!opts.force && (_initiativesLoadedGroup === group || _initiativesLoadingGroup === group)) return false;
  _initiativesLoadingGroup = group;
  _initiativesLastError = '';
  if (typeof send === 'function') {
    send({ cmd: 'initiative_list', group: group, include_archived: false });
  }
  return true;
}

function planningEnsureLoaded(opts) {
  opts = opts || {};
  var includeInactive = !!(opts.includeInactive || opts.all);
  var active = String(_planningActiveTab || 'initiatives');
  var loaded = false;
  if (active === 'areas') {
    loaded = areasEnsureLoaded(opts) || loaded;
    if (includeInactive) loaded = initiativesEnsureLoaded(opts) || loaded;
  } else {
    loaded = initiativesEnsureLoaded(opts) || loaded;
    if (includeInactive) loaded = areasEnsureLoaded(opts) || loaded;
  }
  return loaded;
}

function planningRefresh() {
  initiativesEnsureLoaded({ force: true });
  areasEnsureLoaded({ force: true });
  if (_initiativesSelectedId) initiativesLoadDetail(_initiativesSelectedId, { force: true });
  if (_areasSelectedId) areasLoadDetail(_areasSelectedId, { force: true });
  if (typeof renderInitiativesPanel === 'function') renderInitiativesPanel();
}

function initiativesRefresh() {
  planningRefresh();
}

function initiativesBeginGroupSwitch() {
  var group = _initiativesGroup() || '';
  if (_initiativesLoadedGroup !== group) {
    _initiativesSelectedId = '';
    _initiativesDetail = null;
    _initiativesDetailLoadingId = '';
    _initiativesLastError = '';
  }
  if (typeof areasBeginGroupSwitch === 'function') areasBeginGroupSwitch({ render: false, load: false });
  planningEnsureLoaded({ force: true, includeInactive: true });
  renderInitiativesPanel();
}

function initiativesReceiveList(msg) {
  _initiativesEnsureState();
  var group = (msg && msg.group != null) ? String(msg.group || '') : _initiativesGroup();
  var initiatives = (msg && msg.initiatives) || [];
  for (var existingId in state.initiatives) {
    var existing = state.initiatives[existingId];
    if (existing && String(existing.group || existing.group_name || '') === group) {
      delete state.initiatives[existingId];
    }
  }
  initiatives.forEach(function(item) {
    if (!item || !item.id) return;
    state.initiatives[item.id] = Object.assign({}, item);
  });
  if (_initiativesLoadingGroup === group) _initiativesLoadingGroup = null;
  _initiativesLoadedGroup = group;
  if (_initiativesSelectedId && !state.initiatives[_initiativesSelectedId]) {
    _initiativesSelectedId = '';
    _initiativesDetail = null;
  }
  renderInitiativesPanel();
}

function initiativesReceiveDetail(msg) {
  if (!msg) return;
  _initiativesEnsureState();
  var payload = msg.initiative || msg;
  if (!payload || !payload.id) return;
  state.initiatives[payload.id] = Object.assign({}, state.initiatives[payload.id] || {}, payload);
  _initiativesDetailLoadingId = '';
  _initiativesDetail = Object.assign({}, payload);
  if (!_initiativesSelectedId) _initiativesSelectedId = payload.id;
  if (typeof lazyLoadDecisions === 'function') lazyLoadDecisions();
  renderInitiativesPanel();
}

function initiativesReceiveMutation(msg) {
  if (msg && msg.initiative && msg.initiative.id) {
    _initiativesEnsureState();
    state.initiatives[msg.initiative.id] = Object.assign(
      {}, state.initiatives[msg.initiative.id] || {}, msg.initiative);
    _initiativesSaving = false;
    _initiativesLastError = '';
    delete _initiativesDraftsById[msg.initiative.id];
    if (_initiativesSelectedId === msg.initiative.id) {
      initiativesLoadDetail(msg.initiative.id, { force: true });
    }
  }
  initiativesEnsureLoaded({ force: true });
  renderInitiativesPanel();
}

function initiativesReceiveLinkMutation(msg) {
  _initiativesSaving = false;
  _initiativesLastError = '';
  var initiativeId = String(
    (msg && msg.initiative_id)
    || (msg && msg.link && msg.link.initiative_id)
    || _initiativesSelectedId
    || ''
  );
  if (initiativeId) {
    if (msg && msg.type === 'initiative_task_linked'
        && _initiativesTaskCreateLinking
        && _initiativesTaskCreateLinking.initiativeId === initiativeId) {
      var linkedTaskId = String((msg.link && (msg.link.linked_id || msg.link.task_id || msg.link.id)) || _initiativesTaskCreateLinking.taskId || (msg && msg.task_id) || '');
      _initiativesTaskCreateStatusById[initiativeId] = {
        kind: 'success',
        message: 'Created and linked Board task' + (linkedTaskId ? ' ' + linkedTaskId : '') + '.',
      };
      _initiativesTaskCreateLinking = null;
      if (typeof _showToast === 'function') _showToast(_initiativesTaskCreateStatusById[initiativeId].message, 'info');
    }
    initiativesLoadDetail(initiativeId, { force: true });
  }
  initiativesEnsureLoaded({ force: true });
  renderInitiativesPanel();
}

function initiativesHandleError(msg) {
  if (!_initiativesPanelVisible()) return false;
  var text = String((msg && msg.message) || 'Initiative command failed');
  if (text.toLowerCase().indexOf('initiative') < 0
      && !_initiativesSaving
      && !_initiativesTaskCreatePending
      && !_initiativesTaskCreateLinking
      && !_initiativesDetailLoadingId
      && !_initiativesLoadingGroup) {
    return false;
  }
  _initiativesSaving = false;
  _initiativesDetailLoadingId = '';
  _initiativesLoadingGroup = null;
  if (_initiativesTaskCreatePending || _initiativesTaskCreateLinking) {
    var pending = _initiativesTaskCreatePending || _initiativesTaskCreateLinking;
    _initiativesTaskCreatePending = null;
    _initiativesTaskCreateLinking = null;
    if (pending.initiativeId) {
      _initiativesTaskCreateStatusById[pending.initiativeId] = { kind: 'error', message: text };
    }
  }
  _initiativesLastError = text;
  renderInitiativesPanel();
  return true;
}

function initiativesLoadDetail(id, opts) {
  id = String(id || '').trim();
  if (!id) return false;
  opts = opts || {};
  if (!opts.force && _initiativesDetail && _initiativesDetail.id === id) return false;
  if (!opts.force && _initiativesDetailLoadingId === id) return false;
  _initiativesDetailLoadingId = id;
  _initiativesLastError = '';
  if (typeof send === 'function') {
    send({ cmd: 'initiative_show', initiative: id, group: _initiativesGroup() });
  }
  return true;
}

function initiativesSelect(id) {
  id = String(id || '').trim();
  _initiativesCaptureDrafts();
  _initiativesSelectedId = id;
  _initiativesDetail = null;
  _initiativesLastError = '';
  if (id) initiativesLoadDetail(id, { force: true });
  renderInitiativesPanel();
}

function initiativesCloseDetail() {
  _initiativesCaptureDrafts();
  _initiativesSelectedId = '';
  _initiativesDetail = null;
  _initiativesDetailLoadingId = '';
  renderInitiativesPanel();
}

function initiativesToggleSecondary(status) {
  status = String(status || '').toLowerCase();
  _initiativesSecondaryExpanded[status] = !_initiativesSecondaryExpanded[status];
  renderInitiativesPanel();
}

function _initiativeFieldElement(field) {
  return document.getElementById('initiative-field-' + field.replace(/_/g, '-'));
}

function _initiativesCaptureDrafts() {
  if (!_initiativesSelectedId || typeof document === 'undefined') return;
  var panel = document.getElementById('panel-initiatives');
  if (!panel) return;
  var draft = {};
  var found = false;
  INITIATIVE_SCOPE_FIELDS.forEach(function(field) {
    var el = _initiativeFieldElement(field);
    if (!el) return;
    if ('value' in el) {
      draft[field] = el.value;
      found = true;
    }
  });
  if (found) _initiativesDraftsById[_initiativesSelectedId] = draft;
}

function _initiativeDetailBase() {
  var base = {};
  if (_initiativesSelectedId && state && state.initiatives && state.initiatives[_initiativesSelectedId]) {
    base = Object.assign({}, state.initiatives[_initiativesSelectedId]);
  }
  if (_initiativesDetail && _initiativesDetail.id === _initiativesSelectedId) {
    base = Object.assign(base, _initiativesDetail);
  }
  var draft = _initiativesDraftsById[_initiativesSelectedId];
  if (draft) base = Object.assign(base, draft);
  return base;
}

function initiativesFieldChanged() {
  _initiativesCaptureDrafts();
}

function initiativesSaveDetail() {
  if (!_initiativesSelectedId) return;
  _initiativesCaptureDrafts();
  var draft = _initiativesDraftsById[_initiativesSelectedId] || {};
  var payload = {
    cmd: 'initiative_update',
    initiative: _initiativesSelectedId,
    group: _initiativesGroup(),
  };
  INITIATIVE_SCOPE_FIELDS.forEach(function(field) {
    if (Object.prototype.hasOwnProperty.call(draft, field)) payload[field] = draft[field];
  });
  _initiativesSaving = true;
  _initiativesLastError = '';
  if (typeof send === 'function') send(payload);
  renderInitiativesPanel();
}

function _initiativeCompactSection(label, value) {
  value = String(value || '').trim();
  if (!value) return '';
  return label + '\n' + value;
}

function _initiativeBoardTaskPrefill(detail) {
  detail = detail || {};
  var initiativeId = String(detail.id || _initiativesSelectedId || '').trim();
  var title = String(detail.title || initiativeId || 'Untitled initiative').trim();
  var sections = [];
  if (initiativeId) {
    sections.push('Source initiative: ' + initiativeId + (title ? ' — ' + title : ''));
  }
  var summary = _initiativeCompactSection('Summary', detail.summary);
  var why = _initiativeCompactSection('Why', detail.why);
  var inScope = _initiativeCompactSection('In scope', detail.in_scope);
  var done = _initiativeCompactSection('Done definition', detail.done_definition);
  [summary, why, inScope, done].forEach(function(section) {
    if (section) sections.push(section);
  });
  if (!sections.length) sections.push('Source initiative brief.');
  return {
    initiativeId: initiativeId,
    group: String(detail.group || detail.group_name || _initiativesGroup() || ''),
    task: title,
    description: sections.join('\n\n'),
    labels: [],
    createContext: {
      type: 'initiative',
      initiativeId: initiativeId,
      group: String(detail.group || detail.group_name || _initiativesGroup() || ''),
    },
  };
}

function _initiativesOpenBoardTaskModal(prefill) {
  prefill = prefill || {};
  if (typeof openAddTaskFromInitiative === 'function') {
    openAddTaskFromInitiative(prefill);
    return true;
  }
  if (typeof _taskOpenModal !== 'function' || typeof _generateDraftId !== 'function') {
    return false;
  }
  var initiativeId = String(prefill.initiativeId || (prefill.createContext && prefill.createContext.initiativeId) || '').trim();
  var group = prefill.group || _initiativesGroup();
  _taskOpenModal({
    editId: null,
    title: 'Create Board Task',
    submitLabel: 'Create task',
    task: prefill.task || '',
    description: prefill.description || '',
    labels: prefill.labels || [],
    dependsOn: [],
    attachments: [],
    originalAttachments: [],
    artifacts: [],
    originalArtifacts: [],
    actionName: '',
    agentTemplate: '',
    actionVars: {},
    group: group,
    lane: prefill.lane || '',
    scheduledInput: '',
    verificationMode: '',
    verificationState: '',
    verificationNotes: '',
    verificationSummary: {},
    draftId: _generateDraftId(),
    draftScope: initiativeId ? 'initiative:' + initiativeId : 'initiative',
    createContext: prefill.createContext || {
      type: 'initiative',
      initiativeId: initiativeId,
      group: group,
    },
    afterCreateSubmit: function(meta) {
      if (typeof initiativesRegisterTaskCreatePending === 'function') {
        initiativesRegisterTaskCreatePending(meta);
      }
    },
    selectTask: false,
  });
  return true;
}

function initiativesCreateBoardTask() {
  if (!_initiativesSelectedId) return;
  _initiativesCaptureDrafts();
  var detail = _initiativeDetailBase();
  detail.id = detail.id || _initiativesSelectedId;
  var prefill = _initiativeBoardTaskPrefill(detail);
  if (!_initiativesOpenBoardTaskModal(prefill)) {
    _initiativesLastError = 'Task creation modal is unavailable.';
    renderInitiativesPanel();
    return;
  }
  _initiativesTaskCreateStatusById[prefill.initiativeId] = {
    kind: 'info',
    message: 'Review the prefilled Board task, then create it from the modal.',
  };
  renderInitiativesPanel();
}

function initiativesRegisterTaskCreatePending(meta) {
  meta = meta || {};
  var source = meta.source || meta.createContext || {};
  if (source.type !== 'initiative') return false;
  var initiativeId = String(source.initiativeId || source.initiative_id || '').trim();
  if (!initiativeId) return false;
  _initiativesTaskCreatePending = {
    initiativeId: initiativeId,
    group: String(source.group || meta.group || _initiativesGroup() || ''),
    title: String(meta.task || meta.title || '').trim(),
    draftId: String(meta.draftId || meta.draft_id || '').trim(),
  };
  _initiativesTaskCreateStatusById[initiativeId] = {
    kind: 'info',
    message: 'Creating Board task…',
  };
  if (_initiativesSelectedId === initiativeId) renderInitiativesPanel();
  return true;
}

function initiativesHandleBoardTaskCreated(msg) {
  if (!_initiativesTaskCreatePending) return false;
  var taskId = String((msg && (msg.task_id || msg.id)) || '').trim();
  if (!taskId) return false;
  var pending = _initiativesTaskCreatePending;
  _initiativesTaskCreatePending = null;
  _initiativesTaskCreateLinking = { initiativeId: pending.initiativeId, taskId: taskId };
  _initiativesTaskCreateStatusById[pending.initiativeId] = {
    kind: 'info',
    message: 'Board task ' + taskId + ' created. Linking it to the initiative…',
  };
  _initiativesSaving = true;
  _initiativesLastError = '';
  if (typeof send === 'function') {
    send({
      cmd: 'initiative_link_task',
      initiative: pending.initiativeId,
      task: taskId,
      group: pending.group || _initiativesGroup(),
    });
  }
  if (_initiativesSelectedId === pending.initiativeId) renderInitiativesPanel();
  return true;
}

function initiativesLinkTask() {
  var el = document.getElementById('initiative-link-task-input');
  var ref = el ? String(el.value || '').trim() : '';
  if (!_initiativesSelectedId || !ref) return;
  _initiativesSaving = true;
  if (typeof send === 'function') {
    send({ cmd: 'initiative_link_task', initiative: _initiativesSelectedId, task: ref, group: _initiativesGroup() });
  }
  if (el) el.value = '';
  renderInitiativesPanel();
}

function initiativesUnlinkTask(taskId) {
  taskId = String(taskId || '').trim();
  if (!_initiativesSelectedId || !taskId) return;
  _initiativesSaving = true;
  if (typeof send === 'function') {
    send({ cmd: 'initiative_unlink_task', initiative: _initiativesSelectedId, task: taskId, group: _initiativesGroup() });
  }
  renderInitiativesPanel();
}

function initiativesLinkDecision() {
  var el = document.getElementById('initiative-link-decision-input');
  var ref = el ? String(el.value || '').trim() : '';
  if (!_initiativesSelectedId || !ref) return;
  _initiativesSaving = true;
  if (typeof send === 'function') {
    send({ cmd: 'initiative_link_decision', initiative: _initiativesSelectedId, decision: ref, group: _initiativesGroup() });
  }
  if (el) el.value = '';
  renderInitiativesPanel();
}

function initiativesUnlinkDecision(decisionId) {
  decisionId = String(decisionId || '').trim();
  if (!_initiativesSelectedId || !decisionId) return;
  _initiativesSaving = true;
  if (typeof send === 'function') {
    send({ cmd: 'initiative_unlink_decision', initiative: _initiativesSelectedId, decision: decisionId, group: _initiativesGroup() });
  }
  renderInitiativesPanel();
}

function _initiativesOpenTask(taskId) {
  taskId = String(taskId || '').trim();
  if (!taskId) return;
  if (typeof openEditTask === 'function') openEditTask(taskId);
}

function _initiativeLinkedTaskSummary(item) {
  var linked = item && item.linked_tasks;
  if (!linked || !linked.count) return 'No linked tasks';
  var byLane = Object.assign({}, linked.by_lane || {});
  var linkedItems = Array.isArray(linked.items) ? linked.items : [];
  if (linkedItems.length && state && state.board_tasks) {
    var liveByLane = {};
    linkedItems.forEach(function(task) {
      var taskId = String((task && task.id) || '');
      var live = taskId ? state.board_tasks[taskId] : null;
      var lane = String((live && live.lane) || (task && task.lane) || 'Unlaned');
      liveByLane[lane] = (liveByLane[lane] || 0) + 1;
    });
    byLane = liveByLane;
  }
  var parts = [];
  Object.keys(byLane).sort().forEach(function(lane) {
    if (byLane[lane]) parts.push(lane + ' ' + byLane[lane]);
  });
  var text = linked.count + ' linked task' + (linked.count === 1 ? '' : 's');
  if (parts.length) text += ' · ' + parts.join(', ');
  if (linked.hidden_count) text += ' · +' + linked.hidden_count + ' hidden';
  return text;
}

function _initiativeShortText(item) {
  var text = String((item && (item.summary || item.why)) || '').trim();
  if (!text) return 'No summary yet.';
  return text.length > 180 ? text.slice(0, 177) + '...' : text;
}

function _initiativeCountLabel(count) {
  count = Number(count || 0);
  return count + ' initiative' + (count === 1 ? '' : 's');
}

function _renderInitiativeCard(item) {
  var id = String(item.id || '');
  var selected = id && id === _initiativesSelectedId;
  var html = '';
  html += '<button type="button" class="initiative-card' + (selected ? ' selected' : '') + '" data-initiative-id="' + esc(id) + '" onclick="initiativesSelect(\'' + esc(id) + '\')">';
  html += '<div class="initiative-card-top">';
  html += '<span class="initiative-card-title">' + esc(item.title || id || 'Untitled initiative') + '</span>';
  if (item.priority) html += '<span class="initiative-priority">' + esc(item.priority) + '</span>';
  html += '</div>';
  html += '<div class="initiative-card-summary">' + esc(_initiativeShortText(item)) + '</div>';
  html += '<div class="initiative-card-linked">' + esc(_initiativeLinkedTaskSummary(item)) + '</div>';
  html += '</button>';
  return html;
}

function _renderInitiativeColumn(status, items, secondary) {
  var label = status.charAt(0).toUpperCase() + status.slice(1);
  var html = '';
  html += '<section class="initiative-column' + (secondary ? ' initiative-column-secondary' : '') + '" data-status="' + esc(status) + '">';
  html += '<div class="initiative-column-head">';
  html += '<div><div class="initiative-column-title">' + esc(label) + '</div>';
  html += '<div class="initiative-column-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + esc(_initiativeCountLabel(items.length)) + '</div></div>';
  html += '</div>';
  html += '<div class="initiative-column-body" id="initiative-col-' + esc(status) + '">';
  if (!items.length) {
    html += '<div class="initiative-empty ui-state ui-state--empty">No initiatives yet. Create one to organize related planning work.</div>';
  } else {
    items.forEach(function(item) { html += _renderInitiativeCard(item); });
  }
  html += '</div></section>';
  return html;
}

function _renderInitiativesRoadmap(group, buckets) {
  var html = '';
  html += '<div class="initiatives-roadmap-scroll" id="initiatives-roadmap-scroll">';
  html += '<div class="initiative-primary-columns">';
  INITIATIVE_PRIMARY_STATUSES.forEach(function(status) {
    html += _renderInitiativeColumn(status, buckets[status] || [], false);
  });
  html += '</div>';
  html += '<div class="initiative-secondary-wrap">';
  html += '<div class="initiative-secondary-head">Secondary buckets</div>';
  html += '<div class="initiative-secondary-toggles">';
  INITIATIVE_SECONDARY_STATUSES.forEach(function(status) {
    var open = !!_initiativesSecondaryExpanded[status];
    var count = (buckets[status] || []).length;
    html += '<button type="button" class="filter-chip initiative-secondary-toggle' + (open ? ' active' : '') + '" aria-pressed="' + (open ? 'true' : 'false') + '" onclick="initiativesToggleSecondary(\'' + esc(status) + '\')">';
    html += esc(status) + ' <span class="initiative-secondary-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + count + '</span></button>';
  });
  html += '</div>';
  INITIATIVE_SECONDARY_STATUSES.forEach(function(status) {
    if (!_initiativesSecondaryExpanded[status]) return;
    html += _renderInitiativeColumn(status, buckets[status] || [], true);
  });
  html += '</div>';
  html += '</div>';
  return html;
}

function _initiativeInput(field, label, value, opts) {
  opts = opts || {};
  var id = 'initiative-field-' + field.replace(/_/g, '-');
  var html = '<label for="' + id + '">' + esc(label) + '</label>';
  if (opts.select) {
    html += '<select id="' + id + '" data-field="' + esc(field) + '" onchange="initiativesFieldChanged()">';
    opts.select.forEach(function(option) {
      html += '<option value="' + esc(option) + '"' + (String(value || '') === option ? ' selected' : '') + '>' + esc(option) + '</option>';
    });
    html += '</select>';
  } else if (opts.textarea) {
    html += '<textarea id="' + id + '" data-field="' + esc(field) + '" rows="' + (opts.rows || 3) + '" oninput="initiativesFieldChanged()">' + esc(value || '') + '</textarea>';
  } else {
    html += '<input id="' + id + '" data-field="' + esc(field) + '" value="' + esc(value || '') + '" oninput="initiativesFieldChanged()" autocomplete="off">';
  }
  return html;
}

function _renderInitiativeLinkedTasks(detail) {
  var linked = (detail && detail.linked_tasks) || {};
  var items = linked.items || [];
  var html = '<div class="initiative-links-list">';
  if (!items.length) {
    html += '<div class="initiative-empty compact ui-state ui-state--empty ui-state--compact">No linked tasks.</div>';
  }
  items.forEach(function(task) {
    var id = String(task.id || '');
    html += '<div class="initiative-link-row">';
    html += '<button type="button" class="initiative-link-main" onclick="_initiativesOpenTask(\'' + esc(id) + '\')">';
    html += '<span class="initiative-link-title">' + esc(task.title || id) + '</span>';
    html += '<span class="initiative-link-meta">' + esc(id + (task.lane ? ' · ' + task.lane : '')) + '</span>';
    html += '</button>';
    html += '<button type="button" class="initiative-unlink" onclick="initiativesUnlinkTask(\'' + esc(id) + '\')">Unlink</button>';
    html += '</div>';
  });
  if (linked.hidden_count) {
    html += '<div class="initiative-empty compact ui-state ui-state--note ui-state--compact">' + esc(linked.hidden_count + ' linked task(s) hidden by scope.') + '</div>';
  }
  html += '</div>';
  html += '<div class="initiative-link-add">';
  html += '<input id="initiative-link-task-input" placeholder="Existing task id or slug" autocomplete="off" onkeydown="if(event.key===\'Enter\')initiativesLinkTask()">';
  html += '<button type="button" onclick="initiativesLinkTask()">Link task</button>';
  html += '</div>';
  return html;
}

function _decisionTitle(decisionId) {
  var decision = state && state.decisions ? state.decisions[decisionId] : null;
  if (!decision) return decisionId;
  return decision.title || decision.summary || decision.decision || decisionId;
}

function _renderInitiativeLinkedDecisions(detail) {
  var linked = (detail && detail.linked_decisions) || {};
  var ids = (linked.items || []).slice();
  var html = '<div class="initiative-links-list">';
  if (!ids.length) {
    html += '<div class="initiative-empty compact ui-state ui-state--empty ui-state--compact">No linked decisions.</div>';
  }
  ids.forEach(function(decisionId) {
    decisionId = String(decisionId || '');
    html += '<div class="initiative-link-row">';
    html += '<div class="initiative-link-main readonly">';
    html += '<span class="initiative-link-title">' + esc(_decisionTitle(decisionId)) + '</span>';
    html += '<span class="initiative-link-meta">' + esc(decisionId) + '</span>';
    html += '</div>';
    html += '<button type="button" class="initiative-unlink" onclick="initiativesUnlinkDecision(\'' + esc(decisionId) + '\')">Unlink</button>';
    html += '</div>';
  });
  if (linked.hidden_count) {
    html += '<div class="initiative-empty compact ui-state ui-state--note ui-state--compact">' + esc(linked.hidden_count + ' linked decision(s) hidden by scope.') + '</div>';
  }
  html += '</div>';
  html += '<div class="initiative-link-add">';
  html += '<input id="initiative-link-decision-input" placeholder="Existing decision id" autocomplete="off" onkeydown="if(event.key===\'Enter\')initiativesLinkDecision()">';
  html += '<button type="button" onclick="initiativesLinkDecision()">Link decision</button>';
  html += '</div>';
  return html;
}

function _renderInitiativeDetail() {
  if (!_initiativesSelectedId) {
    return '<aside class="initiative-detail empty"><div class="initiative-empty-detail ui-state ui-state--empty ui-state--fill">Select an initiative to inspect scope and links.</div></aside>';
  }
  var loading = _initiativesDetailLoadingId === _initiativesSelectedId && !_initiativesDetail;
  var detail = _initiativeDetailBase();
  var html = '<aside class="initiative-detail" id="initiative-detail-drawer">';
  html += '<div class="initiative-detail-head">';
  html += '<div><div class="initiative-detail-kicker">Initiative</div><h2>' + esc(detail.title || _initiativesSelectedId) + '</h2>';
  html += '<div class="initiative-detail-id">' + esc(_initiativesSelectedId) + '</div></div>';
  html += '<button type="button" class="initiative-close" onclick="initiativesCloseDetail()" title="Close">×</button>';
  html += '</div>';
  if (loading) html += '<div class="initiative-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading initiative detail…</div>';
  var taskCreateStatus = _initiativesTaskCreateStatusById[_initiativesSelectedId];
  if (taskCreateStatus && taskCreateStatus.message) {
    html += '<div class="initiative-status initiative-status-' + esc(taskCreateStatus.kind || 'info') + '">' + esc(taskCreateStatus.message) + '</div>';
  }
  html += '<div class="initiative-form">';
  html += _initiativeInput('title', 'Title', detail.title || '');
  html += '<div class="initiative-form-grid">';
  html += '<div>' + _initiativeInput('planning_status', 'Planning status', detail.planning_status || 'triage', { select: INITIATIVE_ALL_STATUSES }) + '</div>';
  html += '<div>' + _initiativeInput('priority', 'Priority', detail.priority || '') + '</div>';
  html += '</div>';
  html += _initiativeInput('summary', 'Summary', detail.summary || '', { textarea: true, rows: 3 });
  html += _initiativeInput('why', 'Why', detail.why || '', { textarea: true, rows: 3 });
  html += _initiativeInput('in_scope', 'In scope', detail.in_scope || '', { textarea: true, rows: 4 });
  html += _initiativeInput('out_of_scope', 'Out of scope', detail.out_of_scope || '', { textarea: true, rows: 4 });
  html += _initiativeInput('done_definition', 'Done definition', detail.done_definition || '', { textarea: true, rows: 4 });
  html += '<div class="initiative-detail-actions">';
  html += '<button type="button" class="btn-secondary" onclick="initiativesCreateBoardTask()"' + (_initiativesSaving ? ' disabled' : '') + '>Create board task</button>';
  html += '<button type="button" class="btn-primary" onclick="initiativesSaveDetail()"' + (_initiativesSaving ? ' disabled' : '') + '>' + (_initiativesSaving ? 'Saving...' : 'Save scope') + '</button>';
  html += '</div>';
  html += '</div>';
  html += '<section class="initiative-links-section"><h3>Linked tasks</h3>' + _renderInitiativeLinkedTasks(detail) + '</section>';
  html += '<section class="initiative-links-section"><h3>Linked decisions</h3>' + _renderInitiativeLinkedDecisions(detail) + '</section>';
  html += '</aside>';
  return html;
}

function _areaNormalizeLifecycle(value) {
  value = String(value || '').trim().toLowerCase();
  return AREA_LIFECYCLES.indexOf(value) >= 0 ? value : 'planned';
}

function _areaLifecycleLabel(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, function(ch) { return ch.toUpperCase(); });
}

function _areaNoteTypeLabel(value) {
  return String(value || '').replace(/_/g, ' ');
}

function _areasListForGroup(group) {
  _areasEnsureState();
  var g = String(group || '');
  var items = [];
  for (var id in state.areas) {
    var item = state.areas[id];
    if (!item) continue;
    if (g && String(item.group || item.group_name || '') !== g) continue;
    if (item.archived || String(item.archived_at || '').trim()) continue;
    items.push(item);
  }
  items.sort(function(a, b) {
    var al = AREA_LIFECYCLES.indexOf(_areaNormalizeLifecycle(a.lifecycle));
    var bl = AREA_LIFECYCLES.indexOf(_areaNormalizeLifecycle(b.lifecycle));
    if (al !== bl) return al - bl;
    var at = String(a.area_type || '').toLowerCase();
    var bt = String(b.area_type || '').toLowerCase();
    if (at !== bt) return at.localeCompare(bt);
    return String(a.title || a.id || '').localeCompare(String(b.title || b.id || ''));
  });
  return items;
}

function _areasFilteredList(group) {
  var q = String(_areasSearch || '').trim().toLowerCase();
  var lifecycle = String(_areasLifecycleFilter || '').trim().toLowerCase();
  var areaType = String(_areasTypeFilter || '').trim().toLowerCase();
  return _areasListForGroup(group).filter(function(item) {
    if (lifecycle && _areaNormalizeLifecycle(item.lifecycle) !== lifecycle) return false;
    if (areaType && String(item.area_type || '').trim().toLowerCase() !== areaType) return false;
    if (!q) return true;
    var haystack = [
      item.id,
      item.slug,
      item.title,
      item.area_type,
      item.lifecycle,
      item.summary,
      item.user_purpose,
      item.system_purpose,
    ].join(' ').toLowerCase();
    return haystack.indexOf(q) >= 0;
  });
}

function _areasTypeOptions(group) {
  var seen = {};
  _areasListForGroup(group).forEach(function(item) {
    var value = String(item.area_type || '').trim();
    if (value) seen[value] = true;
  });
  return Object.keys(seen).sort(function(a, b) { return a.localeCompare(b); });
}

function areasEnsureLoaded(opts) {
  opts = opts || {};
  var group = _initiativesGroup();
  if (!group && !opts.force) return false;
  if (!opts.force && (_areasLoadedGroup === group || _areasLoadingGroup === group)) return false;
  _areasLoadingGroup = group;
  _areasLastError = '';
  if (typeof send === 'function') {
    send({ cmd: 'area_list', group: group, include_archived: false, limit: 500 });
  }
  return true;
}

function areasRefresh() {
  planningRefresh();
}

function areasBeginGroupSwitch(opts) {
  opts = opts || {};
  var group = _initiativesGroup() || '';
  if (_areasLoadedGroup !== group) {
    _areasSelectedId = '';
    _areasDetail = null;
    _areasDetailLoadingId = '';
    _areasLastError = '';
    _areasSearch = '';
    _areasLifecycleFilter = '';
    _areasTypeFilter = '';
    _areasEditingNoteId = '';
  }
  if (opts.load !== false && _planningActiveTab === 'areas') areasEnsureLoaded({ force: true });
  if (opts.render !== false) renderInitiativesPanel();
}

function areasReceiveList(msg) {
  _areasEnsureState();
  var group = (msg && msg.group != null) ? String(msg.group || '') : _initiativesGroup();
  var areas = (msg && (msg.areas || msg.planning_areas)) || [];
  for (var existingId in state.areas) {
    var existing = state.areas[existingId];
    if (existing && String(existing.group || existing.group_name || '') === group) {
      delete state.areas[existingId];
    }
  }
  areas.forEach(function(item) {
    if (!item || !item.id) return;
    state.areas[item.id] = Object.assign({}, item);
  });
  if (_areasLoadingGroup === group) _areasLoadingGroup = null;
  _areasLoadedGroup = group;
  if (_areasSelectedId && !state.areas[_areasSelectedId]) {
    _areasSelectedId = '';
    _areasDetail = null;
  }
  renderInitiativesPanel();
}

function areasReceiveDetail(msg) {
  if (!msg) return;
  _areasEnsureState();
  var payload = msg.area || msg.planning_area || msg;
  if (!payload || !payload.id) return;
  state.areas[payload.id] = Object.assign({}, state.areas[payload.id] || {}, payload);
  _areasDetailLoadingId = '';
  _areasDetail = Object.assign({}, payload);
  if (!_areasSelectedId) _areasSelectedId = payload.id;
  if (typeof lazyLoadDecisions === 'function') lazyLoadDecisions();
  if (typeof initiativesEnsureLoaded === 'function') initiativesEnsureLoaded();
  renderInitiativesPanel();
}

function areasReceiveMutation(msg) {
  var area = msg && (msg.area || msg.planning_area || (msg.id ? msg : null));
  if (area && area.id) {
    _areasEnsureState();
    state.areas[area.id] = Object.assign({}, state.areas[area.id] || {}, area);
    _areasSaving = false;
    _areasLastError = '';
    delete _areasDraftsById[area.id];
    if (_areasSelectedId === area.id) {
      areasLoadDetail(area.id, { force: true });
    }
  }
  areasEnsureLoaded({ force: true });
  renderInitiativesPanel();
}

function areasReceiveLinkMutation(msg) {
  _areasSaving = false;
  _areasLastError = '';
  var areaId = String(
    (msg && msg.area_id)
    || (msg && msg.link && msg.link.area_id)
    || (msg && msg.removed && msg.removed.area_id)
    || _areasSelectedId
    || ''
  );
  if (areaId) areasLoadDetail(areaId, { force: true });
  areasEnsureLoaded({ force: true });
  renderInitiativesPanel();
}

function areasReceiveNoteMutation(msg) {
  _areasSaving = false;
  _areasLastError = '';
  var note = msg && (msg.note || msg.planning_area_note || null);
  var areaId = String((note && note.area_id) || _areasSelectedId || '');
  if (note && note.id) {
    delete _areasNoteDraftsByKey['note:' + note.id];
    if (String(_areasEditingNoteId || '') === String(note.id || '')) _areasEditingNoteId = '';
  }
  if (areaId) areasLoadDetail(areaId, { force: true });
  renderInitiativesPanel();
}

function areasHandleError(msg) {
  if (!_planningTabVisible('areas')) return false;
  var text = String((msg && msg.message) || 'Area command failed');
  if (text.toLowerCase().indexOf('area') < 0
      && !_areasSaving
      && !_areasDetailLoadingId
      && !_areasLoadingGroup) {
    return false;
  }
  _areasSaving = false;
  _areasDetailLoadingId = '';
  _areasLoadingGroup = null;
  _areasLastError = text;
  renderInitiativesPanel();
  return true;
}

function areasLoadDetail(id, opts) {
  id = String(id || '').trim();
  if (!id) return false;
  opts = opts || {};
  if (!opts.force && _areasDetail && _areasDetail.id === id) return false;
  if (!opts.force && _areasDetailLoadingId === id) return false;
  _areasDetailLoadingId = id;
  _areasLastError = '';
  if (typeof send === 'function') {
    send({ cmd: 'area_show', area: id, group: _initiativesGroup() });
  }
  return true;
}

function areasSelect(id) {
  id = String(id || '').trim();
  _areasCaptureDrafts();
  _areasCaptureNoteDrafts();
  _areasSelectedId = id;
  _areasDetail = null;
  _areasLastError = '';
  _areasEditingNoteId = '';
  if (id) areasLoadDetail(id, { force: true });
  renderInitiativesPanel();
}

function areasCloseDetail() {
  _areasCaptureDrafts();
  _areasCaptureNoteDrafts();
  _areasSelectedId = '';
  _areasDetail = null;
  _areasDetailLoadingId = '';
  _areasEditingNoteId = '';
  renderInitiativesPanel();
}

function areasSetSearch(value) {
  _areasSearch = String(value || '');
  renderInitiativesPanel();
}

function areasSetLifecycleFilter(value) {
  _areasLifecycleFilter = String(value || '');
  renderInitiativesPanel();
}

function areasSetTypeFilter(value) {
  _areasTypeFilter = String(value || '');
  renderInitiativesPanel();
}

function _areaFieldElement(field) {
  return document.getElementById('area-field-' + field.replace(/_/g, '-'));
}

function _areasCaptureDrafts() {
  if (!_areasSelectedId || typeof document === 'undefined') return;
  var panel = document.getElementById('panel-initiatives');
  if (!panel) return;
  var draft = {};
  var found = false;
  AREA_SCOPE_FIELDS.forEach(function(field) {
    var el = _areaFieldElement(field);
    if (!el) return;
    if ('value' in el) {
      draft[field] = el.value;
      found = true;
    }
  });
  if (found) _areasDraftsById[_areasSelectedId] = draft;
}

function _areaDetailBase() {
  var base = {};
  if (_areasSelectedId && state && state.areas && state.areas[_areasSelectedId]) {
    base = Object.assign({}, state.areas[_areasSelectedId]);
  }
  if (_areasDetail && _areasDetail.id === _areasSelectedId) {
    base = Object.assign(base, _areasDetail);
  }
  var draft = _areasDraftsById[_areasSelectedId];
  if (draft) base = Object.assign(base, draft);
  return base;
}

function areasFieldChanged() {
  _areasCaptureDrafts();
}

function areasSaveDetail() {
  if (!_areasSelectedId) return;
  _areasCaptureDrafts();
  var draft = _areasDraftsById[_areasSelectedId] || {};
  var payload = {
    cmd: 'area_update',
    area: _areasSelectedId,
    group: _initiativesGroup(),
  };
  AREA_SCOPE_FIELDS.forEach(function(field) {
    if (Object.prototype.hasOwnProperty.call(draft, field)) payload[field] = draft[field];
  });
  _areasSaving = true;
  _areasLastError = '';
  if (typeof send === 'function') send(payload);
  renderInitiativesPanel();
}

function areasToggleSection(section) {
  section = String(section || '');
  _areasSectionExpanded[section] = !_areaSectionOpen(section);
  renderInitiativesPanel();
}

function _areaSectionOpen(section) {
  if (!section) return true;
  return _areasSectionExpanded[section] !== false;
}

function _areasNoteKey(noteId) {
  noteId = String(noteId || '').trim();
  return noteId ? ('note:' + noteId) : ('new:' + (_areasSelectedId || ''));
}

function _areasCaptureNoteDrafts() {
  if (!_areasSelectedId || typeof document === 'undefined') return;
  var ids = ['new'];
  if (_areasEditingNoteId) ids.push(String(_areasEditingNoteId));
  ids.forEach(function(noteId) {
    var prefix = noteId === 'new' ? 'area-note-new' : 'area-note-edit-' + noteId;
    var typeEl = document.getElementById(prefix + '-type');
    var titleEl = document.getElementById(prefix + '-title');
    var bodyEl = document.getElementById(prefix + '-body');
    var targetTypeEl = document.getElementById(prefix + '-target-type');
    var targetIdEl = document.getElementById(prefix + '-target-id');
    if (!typeEl && !titleEl && !bodyEl && !targetTypeEl && !targetIdEl) return;
    _areasNoteDraftsByKey[_areasNoteKey(noteId === 'new' ? '' : noteId)] = {
      note_type: typeEl ? typeEl.value : '',
      title: titleEl ? titleEl.value : '',
      body: bodyEl ? bodyEl.value : '',
      target_type: targetTypeEl ? targetTypeEl.value : '',
      target_id: targetIdEl ? targetIdEl.value : '',
    };
  });
}

function areasNoteChanged() {
  _areasCaptureNoteDrafts();
}

function _areaNoteDraft(note) {
  note = note || {};
  var key = _areasNoteKey(note.id || '');
  var base = {
    note_type: note.note_type || 'caveat',
    title: note.title || '',
    body: note.body || '',
    target_type: note.target_type || '',
    target_id: note.target_id || '',
  };
  if (_areasNoteDraftsByKey[key]) base = Object.assign(base, _areasNoteDraftsByKey[key]);
  return base;
}

function areasEditNote(noteId) {
  _areasCaptureNoteDrafts();
  _areasEditingNoteId = String(noteId || '');
  renderInitiativesPanel();
}

function areasCancelEditNote() {
  _areasCaptureNoteDrafts();
  _areasEditingNoteId = '';
  renderInitiativesPanel();
}

function areasCreateNote() {
  if (!_areasSelectedId) return;
  _areasCaptureNoteDrafts();
  var draft = _areasNoteDraftsByKey[_areasNoteKey('')] || {};
  if (!String(draft.title || '').trim()) {
    _areasLastError = 'Area note title is required.';
    renderInitiativesPanel();
    return;
  }
  _areasSaving = true;
  _areasLastError = '';
  if (typeof send === 'function') {
    send({
      cmd: 'area_note_create',
      area: _areasSelectedId,
      group: _initiativesGroup(),
      note_type: draft.note_type || 'caveat',
      title: draft.title || '',
      body: draft.body || '',
      target_type: draft.target_type || '',
      target_id: draft.target_id || '',
    });
  }
  renderInitiativesPanel();
}

function areasSaveNote(noteId) {
  noteId = String(noteId || '').trim();
  if (!_areasSelectedId || !noteId) return;
  _areasCaptureNoteDrafts();
  var draft = _areasNoteDraftsByKey[_areasNoteKey(noteId)] || {};
  if (!String(draft.title || '').trim()) {
    _areasLastError = 'Area note title is required.';
    renderInitiativesPanel();
    return;
  }
  _areasSaving = true;
  _areasLastError = '';
  if (typeof send === 'function') {
    send({
      cmd: 'area_note_update',
      area: _areasSelectedId,
      note: noteId,
      group: _initiativesGroup(),
      note_type: draft.note_type || 'caveat',
      title: draft.title || '',
      body: draft.body || '',
      target_type: draft.target_type || '',
      target_id: draft.target_id || '',
    });
  }
  renderInitiativesPanel();
}

function areasArchiveNote(noteId) {
  noteId = String(noteId || '').trim();
  if (!_areasSelectedId || !noteId) return;
  _areasSaving = true;
  _areasLastError = '';
  if (typeof send === 'function') {
    send({ cmd: 'area_note_archive', area: _areasSelectedId, note: noteId, group: _initiativesGroup() });
  }
  renderInitiativesPanel();
}

function areasLinkTarget(linkType) {
  linkType = String(linkType || '').trim();
  if (!_areasSelectedId || !linkType) return;
  var id = 'area-link-' + linkType + '-input';
  var el = document.getElementById(id);
  var ref = el ? String(el.value || '').trim() : '';
  if (!ref) return;
  var payload = { cmd: 'area_link_' + linkType, area: _areasSelectedId, group: _initiativesGroup() };
  payload[linkType] = ref;
  if (linkType === 'area') {
    payload.target_area = ref;
    delete payload.area;
    payload.area = _areasSelectedId;
    var relEl = document.getElementById('area-link-area-relation');
    payload.relation = relEl ? String(relEl.value || 'related') : 'related';
  }
  _areasSaving = true;
  if (typeof send === 'function') send(payload);
  if (el) el.value = '';
  renderInitiativesPanel();
}

function areasUnlinkTarget(linkType, targetId, relation) {
  linkType = String(linkType || '').trim();
  targetId = String(targetId || '').trim();
  if (!_areasSelectedId || !linkType || !targetId) return;
  var payload = { cmd: 'area_unlink_' + linkType, area: _areasSelectedId, group: _initiativesGroup() };
  payload[linkType] = targetId;
  if (linkType === 'area') {
    payload.area = _areasSelectedId;
    payload.target_area = targetId;
    payload.relation = String(relation || 'related');
  }
  _areasSaving = true;
  if (typeof send === 'function') send(payload);
  renderInitiativesPanel();
}

function _areaInput(field, label, value, opts) {
  opts = opts || {};
  var id = 'area-field-' + field.replace(/_/g, '-');
  var html = '<label for="' + id + '">' + esc(label) + '</label>';
  if (opts.select) {
    html += '<select id="' + id + '" data-area-field="' + esc(field) + '" onchange="areasFieldChanged()">';
    opts.select.forEach(function(option) {
      html += '<option value="' + esc(option) + '"' + (String(value || '') === option ? ' selected' : '') + '>' + esc(opts.labeler ? opts.labeler(option) : option) + '</option>';
    });
    html += '</select>';
  } else if (opts.textarea) {
    html += '<textarea id="' + id + '" data-area-field="' + esc(field) + '" rows="' + (opts.rows || 3) + '" oninput="areasFieldChanged()">' + esc(value || '') + '</textarea>';
  } else {
    html += '<input id="' + id + '" data-area-field="' + esc(field) + '" value="' + esc(value || '') + '" oninput="areasFieldChanged()" autocomplete="off">';
  }
  return html;
}

function _areaShortText(item) {
  var text = String((item && (item.summary || item.user_purpose || item.system_purpose)) || '').trim();
  if (!text) return 'No summary yet.';
  return text.length > 180 ? text.slice(0, 177) + '...' : text;
}

function _areaCountLabel(count) {
  count = Number(count || 0);
  return count + ' area' + (count === 1 ? '' : 's');
}

function _areaTitle(areaId) {
  areaId = String(areaId || '');
  var area = state && state.areas ? state.areas[areaId] : null;
  return (area && (area.title || area.slug)) || areaId;
}

function _initiativeTitle(initiativeId) {
  initiativeId = String(initiativeId || '');
  var initiative = state && state.initiatives ? state.initiatives[initiativeId] : null;
  return (initiative && (initiative.title || initiative.slug)) || initiativeId;
}

function _areaTaskTitle(taskId) {
  taskId = String(taskId || '');
  var task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  return (task && (task.task || task.title || task.name)) || taskId;
}

function _renderAreaCard(item) {
  var id = String(item.id || '');
  var selected = id && id === _areasSelectedId;
  var lifecycle = _areaNormalizeLifecycle(item.lifecycle);
  var html = '';
  html += '<button type="button" class="area-card' + (selected ? ' selected' : '') + '" data-area-id="' + esc(id) + '" onclick="areasSelect(\'' + esc(id) + '\')">';
  html += '<div class="area-card-top"><span class="area-card-title">' + esc(item.title || id || 'Untitled area') + '</span>';
  html += '<span class="area-lifecycle">' + esc(_areaLifecycleLabel(lifecycle)) + '</span></div>';
  html += '<div class="area-card-meta">' + esc((item.area_type || 'untyped') + (item.slug ? ' · ' + item.slug : '')) + '</div>';
  html += '<div class="area-card-summary">' + esc(_areaShortText(item)) + '</div>';
  html += '</button>';
  return html;
}

function _renderAreasFilters(group, total, filtered) {
  var typeOptions = _areasTypeOptions(group);
  var html = '<div class="areas-filters">';
  html += '<input id="areas-search" class="areas-search-input" value="' + esc(_areasSearch || '') + '" placeholder="Search areas" oninput="areasSetSearch(this.value)" autocomplete="off">';
  html += '<select id="areas-lifecycle-filter" onchange="areasSetLifecycleFilter(this.value)">';
  html += '<option value="">All lifecycles</option>';
  AREA_LIFECYCLES.forEach(function(lifecycle) {
    html += '<option value="' + esc(lifecycle) + '"' + (_areasLifecycleFilter === lifecycle ? ' selected' : '') + '>' + esc(_areaLifecycleLabel(lifecycle)) + '</option>';
  });
  html += '</select>';
  html += '<select id="areas-type-filter" onchange="areasSetTypeFilter(this.value)">';
  html += '<option value="">All types</option>';
  typeOptions.forEach(function(type) {
    html += '<option value="' + esc(type) + '"' + (_areasTypeFilter === type ? ' selected' : '') + '>' + esc(type) + '</option>';
  });
  html += '</select>';
  html += '<span class="area-filter-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + esc(filtered + ' / ' + total) + '</span>';
  html += '</div>';
  return html;
}

function _renderAreasList(group) {
  var all = _areasListForGroup(group);
  var items = _areasFilteredList(group);
  var html = '<div class="areas-list-pane">';
  html += _renderAreasFilters(group, all.length, items.length);
  html += '<div class="areas-list-scroll" id="areas-list-scroll">';
  if (!items.length) {
    var empty = all.length ? 'No areas match the current search/filter.' : 'No areas yet.';
    html += '<div class="initiative-empty area-empty ui-state ui-state--empty">' + esc(empty) + '</div>';
  } else {
    items.forEach(function(item) { html += _renderAreaCard(item); });
  }
  html += '</div></div>';
  return html;
}

function _renderAreaSection(key, title, bodyHtml) {
  var open = _areaSectionOpen(key);
  var html = '<section class="initiative-links-section area-section" data-area-section="' + esc(key) + '">';
  html += '<button type="button" class="area-section-toggle" onclick="areasToggleSection(\'' + esc(key) + '\')">';
  html += '<span>' + esc(title) + '</span><span>' + (open ? '−' : '+') + '</span></button>';
  if (open) html += bodyHtml;
  html += '</section>';
  return html;
}

function _renderAreaLinkRows(detail, linkType) {
  var links = (detail && detail.links) || {};
  var ids = [];
  if (linkType === 'task') ids = ((detail && detail.linked_tasks && detail.linked_tasks.items) || []).map(function(task) { return task.id || task.task_id || ''; });
  else if (linkType === 'decision') ids = ((detail && detail.linked_decisions && (detail.linked_decisions.ids || detail.linked_decisions.items)) || []).map(function(item) { return typeof item === 'string' ? item : (item.id || ''); });
  else if (linkType === 'initiative') ids = (links.initiatives || []).slice();
  var html = '<div class="initiative-links-list">';
  if (!ids.length) html += '<div class="initiative-empty compact ui-state ui-state--empty ui-state--compact">No linked ' + esc(linkType === 'initiative' ? 'initiatives' : linkType + 's') + '.</div>';
  ids.forEach(function(id) {
    id = String(id || '');
    var title = id;
    var meta = id;
    if (linkType === 'task') title = _areaTaskTitle(id);
    if (linkType === 'decision') title = _decisionTitle(id);
    if (linkType === 'initiative') title = _initiativeTitle(id);
    html += '<div class="initiative-link-row">';
    if (linkType === 'task') {
      html += '<button type="button" class="initiative-link-main" onclick="_initiativesOpenTask(\'' + esc(id) + '\')">';
    } else {
      html += '<div class="initiative-link-main readonly">';
    }
    html += '<span class="initiative-link-title">' + esc(title) + '</span>';
    html += '<span class="initiative-link-meta">' + esc(meta) + '</span>';
    html += linkType === 'task' ? '</button>' : '</div>';
    html += '<button type="button" class="initiative-unlink" onclick="areasUnlinkTarget(\'' + esc(linkType) + '\',\'' + esc(id) + '\')">Unlink</button>';
    html += '</div>';
  });
  var hidden = (detail && detail.hidden_link_counts) || {};
  var hiddenCount = hidden[linkType + 's'] || 0;
  if (hiddenCount) html += '<div class="initiative-empty compact ui-state ui-state--note ui-state--compact">' + esc(hiddenCount + ' hidden by scope.') + '</div>';
  html += '</div><div class="initiative-link-add">';
  html += '<input id="area-link-' + esc(linkType) + '-input" placeholder="Existing ' + esc(linkType) + ' id or slug" autocomplete="off" onkeydown="if(event.key===\'Enter\')areasLinkTarget(\'' + esc(linkType) + '\')">';
  html += '<button type="button" onclick="areasLinkTarget(\'' + esc(linkType) + '\')">Link</button>';
  html += '</div>';
  return html;
}

function _renderAreaRelatedAreas(detail) {
  var links = (detail && detail.links && detail.links.areas) || [];
  var html = '<div class="initiative-links-list">';
  if (!links.length) html += '<div class="initiative-empty compact ui-state ui-state--empty ui-state--compact">No related areas.</div>';
  links.forEach(function(link) {
    var id = String((link && (link.area_id || link.target_id || link.id)) || '');
    var relation = String((link && link.relation) || 'related');
    html += '<div class="initiative-link-row">';
    html += '<button type="button" class="initiative-link-main" onclick="areasSelect(\'' + esc(id) + '\')">';
    html += '<span class="initiative-link-title">' + esc(_areaTitle(id)) + '</span>';
    html += '<span class="initiative-link-meta">' + esc(id + ' · ' + relation) + '</span>';
    html += '</button>';
    html += '<button type="button" class="initiative-unlink" onclick="areasUnlinkTarget(\'area\',\'' + esc(id) + '\',\'' + esc(relation) + '\')">Unlink</button>';
    html += '</div>';
  });
  html += '</div><div class="initiative-link-add area-link-related-add">';
  html += '<input id="area-link-area-input" placeholder="Existing area id or slug" autocomplete="off" onkeydown="if(event.key===\'Enter\')areasLinkTarget(\'area\')">';
  html += '<select id="area-link-area-relation">';
  AREA_LINK_RELATIONS.forEach(function(relation) { html += '<option value="' + esc(relation) + '">' + esc(relation) + '</option>'; });
  html += '</select><button type="button" onclick="areasLinkTarget(\'area\')">Link</button></div>';
  return html;
}

function _renderAreaNoteEditor(prefix, draft, submitLabel, submitCall, cancelCall) {
  draft = draft || {};
  var html = '<div class="area-note-editor">';
  html += '<div class="initiative-form-grid">';
  html += '<div><label for="' + esc(prefix) + '-type">Type</label><select id="' + esc(prefix) + '-type" oninput="areasNoteChanged()" onchange="areasNoteChanged()">';
  AREA_NOTE_TYPES.forEach(function(type) { html += '<option value="' + esc(type) + '"' + (String(draft.note_type || 'caveat') === type ? ' selected' : '') + '>' + esc(_areaNoteTypeLabel(type)) + '</option>'; });
  html += '</select></div>';
  html += '<div><label for="' + esc(prefix) + '-title">Title</label><input id="' + esc(prefix) + '-title" value="' + esc(draft.title || '') + '" oninput="areasNoteChanged()" autocomplete="off"></div>';
  html += '</div>';
  html += '<label for="' + esc(prefix) + '-body">Body</label><textarea id="' + esc(prefix) + '-body" rows="3" oninput="areasNoteChanged()">' + esc(draft.body || '') + '</textarea>';
  html += '<div class="initiative-form-grid">';
  html += '<div><label for="' + esc(prefix) + '-target-type">Target type</label><select id="' + esc(prefix) + '-target-type" onchange="areasNoteChanged()">';
  ['', 'task', 'decision', 'initiative', 'area'].forEach(function(type) { html += '<option value="' + esc(type) + '"' + (String(draft.target_type || '') === type ? ' selected' : '') + '>' + esc(type || 'none') + '</option>'; });
  html += '</select></div>';
  html += '<div><label for="' + esc(prefix) + '-target-id">Target id</label><input id="' + esc(prefix) + '-target-id" value="' + esc(draft.target_id || '') + '" oninput="areasNoteChanged()" autocomplete="off"></div>';
  html += '</div><div class="initiative-detail-actions">';
  if (cancelCall) html += '<button type="button" class="btn-secondary" onclick="' + cancelCall + '">Cancel</button>';
  html += '<button type="button" class="btn-primary" onclick="' + submitCall + '"' + (_areasSaving ? ' disabled' : '') + '>' + esc(submitLabel) + '</button>';
  html += '</div></div>';
  return html;
}

function _renderAreaNotes(detail) {
  var notes = (detail && detail.notes) || [];
  var html = '<div class="area-notes-list">';
  if (!notes.length) html += '<div class="initiative-empty compact ui-state ui-state--empty ui-state--compact">No active notes.</div>';
  notes.forEach(function(note) {
    var noteId = String(note.id || '');
    var editing = noteId && String(_areasEditingNoteId || '') === noteId;
    if (editing) {
      html += '<div class="area-note-row editing">' + _renderAreaNoteEditor(
        'area-note-edit-' + noteId,
        _areaNoteDraft(note),
        'Save note',
        'areasSaveNote(\'' + esc(noteId) + '\')',
        'areasCancelEditNote()'
      ) + '</div>';
      return;
    }
    html += '<div class="area-note-row">';
    html += '<div class="area-note-top"><span class="area-note-type">' + esc(_areaNoteTypeLabel(note.note_type || 'caveat')) + '</span><strong>' + esc(note.title || 'Untitled note') + '</strong></div>';
    if (note.body) html += '<div class="area-note-body">' + esc(note.body) + '</div>';
    var target = note.target_hidden ? 'target hidden by scope' : [note.target_type, note.target_id].filter(Boolean).join(':');
    if (target) html += '<div class="area-note-target">' + esc(target) + '</div>';
    html += '<div class="area-note-actions"><button type="button" onclick="areasEditNote(\'' + esc(noteId) + '\')">Edit</button><button type="button" onclick="areasArchiveNote(\'' + esc(noteId) + '\')">Archive</button></div>';
    html += '</div>';
  });
  html += '</div>';
  html += '<div class="area-note-new"><h4>Add note</h4>' + _renderAreaNoteEditor('area-note-new', _areaNoteDraft({}), 'Add note', 'areasCreateNote()', '') + '</div>';
  return html;
}

function _renderAreaDetail() {
  if (!_areasSelectedId) {
    return '<aside class="initiative-detail area-detail empty"><div class="initiative-empty-detail ui-state ui-state--empty ui-state--fill">Select an area to inspect its brief, links, and typed notes.</div></aside>';
  }
  var loading = _areasDetailLoadingId === _areasSelectedId && !_areasDetail;
  var detail = _areaDetailBase();
  var html = '<aside class="initiative-detail area-detail" id="area-detail-drawer">';
  html += '<div class="initiative-detail-head">';
  html += '<div><div class="initiative-detail-kicker">Area</div><h2>' + esc(detail.title || _areasSelectedId) + '</h2>';
  html += '<div class="initiative-detail-id">' + esc(_areasSelectedId) + '</div></div>';
  html += '<button type="button" class="initiative-close" onclick="areasCloseDetail()" title="Close">×</button>';
  html += '</div>';
  if (loading) html += '<div class="initiative-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading area detail…</div>';
  html += '<div class="initiative-form area-form">';
  html += _areaInput('title', 'Title', detail.title || '');
  html += '<div class="initiative-form-grid">';
  html += '<div>' + _areaInput('area_type', 'Area type', detail.area_type || '') + '</div>';
  html += '<div>' + _areaInput('lifecycle', 'Lifecycle', _areaNormalizeLifecycle(detail.lifecycle), { select: AREA_LIFECYCLES, labeler: _areaLifecycleLabel }) + '</div>';
  html += '</div>';
  html += _areaInput('summary', 'Summary', detail.summary || '', { textarea: true, rows: 3 });
  html += _areaInput('user_purpose', 'User purpose', detail.user_purpose || '', { textarea: true, rows: 3 });
  html += _areaInput('system_purpose', 'System purpose', detail.system_purpose || '', { textarea: true, rows: 3 });
  html += _areaInput('in_scope', 'In scope', detail.in_scope || '', { textarea: true, rows: 4 });
  html += _areaInput('out_of_scope', 'Out of scope', detail.out_of_scope || '', { textarea: true, rows: 4 });
  html += '<div class="initiative-detail-actions"><button type="button" class="btn-primary" onclick="areasSaveDetail()"' + (_areasSaving ? ' disabled' : '') + '>' + (_areasSaving ? 'Saving...' : 'Save area') + '</button></div>';
  html += '</div>';
  html += _renderAreaSection('initiatives', 'Linked initiatives', _renderAreaLinkRows(detail, 'initiative'));
  html += _renderAreaSection('tasks', 'Linked tasks', _renderAreaLinkRows(detail, 'task'));
  html += _renderAreaSection('decisions', 'Linked decisions', _renderAreaLinkRows(detail, 'decision'));
  html += _renderAreaSection('areas', 'Related areas', _renderAreaRelatedAreas(detail));
  html += _renderAreaSection('notes', 'Typed notes', _renderAreaNotes(detail));
  html += '</aside>';
  return html;
}

function _renderAreasWorkspace(group) {
  areasEnsureLoaded();
  var html = '';
  if (_areasLastError) html += '<div class="initiative-error ui-state ui-state--error ui-state--compact" role="alert">' + esc(_areasLastError) + ' Refresh Planning to try again.</div>';
  if (_areasLoadingGroup === group && _areasLoadedGroup !== group) {
    html += '<div class="initiative-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading areas…</div>';
  }
  html += '<div class="areas-workspace" id="areas-workspace">';
  html += _renderAreasList(group);
  html += _renderAreaDetail();
  html += '</div>';
  return html;
}

function _renderPlanningTabs(initiativeTotal, areaTotal) {
  var active = String(_planningActiveTab || 'initiatives');
  var html = '<div class="planning-tabs" role="tablist" aria-label="Planning sections">';
  html += '<button type="button" role="tab" class="ui-tab ui-tab--contained planning-tab' + (active === 'initiatives' ? ' active' : '') + '" aria-selected="' + (active === 'initiatives' ? 'true' : 'false') + '" onclick="planningSetTab(\'initiatives\')">Initiatives <span class="planning-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + esc(initiativeTotal) + '</span></button>';
  html += '<button type="button" role="tab" class="ui-tab ui-tab--contained planning-tab' + (active === 'areas' ? ' active' : '') + '" aria-selected="' + (active === 'areas' ? 'true' : 'false') + '" onclick="planningSetTab(\'areas\')">Areas <span class="planning-tab-count ui-badge ui-badge--micro ui-badge--neutral ui-badge--count">' + esc(areaTotal) + '</span></button>';
  html += '</div>';
  return html;
}

function renderInitiativesPanel() {
  var panel = document.getElementById('panel-initiatives');
  if (!panel) return;
  _initiativesCaptureDrafts();
  _areasCaptureDrafts();
  _areasCaptureNoteDrafts();
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(panel, {
      scrollSelectors: [
        '#initiatives-workspace',
        '#initiatives-roadmap-scroll',
        '#initiative-col-now',
        '#initiative-col-next',
        '#initiative-col-later',
        '#initiative-col-triage',
        '#initiative-col-parked',
        '#initiative-col-shipped',
        '#initiative-detail-drawer',
        '#areas-workspace',
        '#areas-list-scroll',
        '#area-detail-drawer',
      ],
      captureFocusKey: function(active) {
        if (active && active.dataset && active.dataset.field) {
          return '[data-field="' + active.dataset.field + '"]';
        }
        if (active && active.dataset && active.dataset.areaField) {
          return '[data-area-field="' + active.dataset.areaField + '"]';
        }
        return '';
      },
    });
  }
  var group = _initiativesGroup();
  var activeTab = String(_planningActiveTab || 'initiatives');
  if (activeTab === 'areas') areasEnsureLoaded();
  else initiativesEnsureLoaded();
  var initiativeTotal = _initiativesListForGroup(group).length;
  var areaTotal = _areasListForGroup(group).length;
  var html = '';
  html += '<div class="initiatives-panel planning-panel">';
  html += '<div class="tpled-header initiatives-header ui-panel-header ui-panel-header--surface">';
  html += '<div class="tpled-header-copy ui-panel-header__copy"><div class="tpled-header-title-row ui-panel-header__title-row"><span class="tpled-header-title ui-panel-header__title">Planning</span></div>';
  html += '<div class="tpled-header-subtitle ui-panel-header__subtitle">' + (activeTab === 'areas'
    ? 'Areas capture compact product/system briefs, links, and typed notes for ' + esc(group || 'all groups') + '.'
    : 'Initiatives grouped by roadmap bucket for ' + esc(group || 'all groups') + '. Linked execution stays on Board tasks.') + '</div></div>';
  html += '<div class="tpled-header-controls ui-panel-header__actions">';
  html += '<span class="initiative-total ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + esc(activeTab === 'areas' ? _areaCountLabel(areaTotal) : _initiativeCountLabel(initiativeTotal)) + '</span>';
  html += '<button class="tpled-new-btn" onclick="planningRefresh()" title="Refresh planning data">&#x21BB;</button>';
  html += '</div></div>';
  html += _renderPlanningTabs(initiativeTotal, areaTotal);
  if (activeTab === 'areas') {
    html += _renderAreasWorkspace(group);
  } else {
    var buckets = _initiativesByStatus(group);
    if (_initiativesLastError) html += '<div class="initiative-error ui-state ui-state--error ui-state--compact" role="alert">' + esc(_initiativesLastError) + ' Refresh Planning to try again.</div>';
    if (_initiativesLoadingGroup === group && _initiativesLoadedGroup !== group) {
      html += '<div class="initiative-loading ui-state ui-state--loading ui-state--compact" role="status" aria-live="polite">Loading initiatives…</div>';
    }
    html += '<div class="initiatives-workspace" id="initiatives-workspace">';
    html += _renderInitiativesRoadmap(group, buckets);
    html += _renderInitiativeDetail();
    html += '</div>';
  }
  html += '</div>';
  panel.innerHTML = html;
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(panel, snapshot, {
      resolveFocus: function(root, focus) {
        if (focus && focus.key && root && root.querySelector) return root.querySelector(focus.key);
        return null;
      },
    });
  }
}

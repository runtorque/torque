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

var _initiativesLoadedGroup = null;
var _initiativesLoadingGroup = null;
var _initiativesSelectedId = '';
var _initiativesDetail = null;
var _initiativesDetailLoadingId = '';
var _initiativesSaving = false;
var _initiativesLastError = '';
var _initiativesSecondaryExpanded = {};
var _initiativesDraftsById = {};

function _initiativesGroup() {
  if (typeof _currentGroup === 'function') return _currentGroup() || '';
  return (state && state.active_group) || '';
}

function _initiativesPanelVisible() {
  return !!(
    (typeof _panelAppVisible === 'function' && _panelAppVisible('initiatives'))
    || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'initiatives')
  );
}

function _initiativesEnsureState() {
  if (!state) state = {};
  if (!state.initiatives || typeof state.initiatives !== 'object') state.initiatives = {};
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

function initiativesRefresh() {
  initiativesEnsureLoaded({ force: true });
  if (_initiativesSelectedId) initiativesLoadDetail(_initiativesSelectedId, { force: true });
}

function initiativesBeginGroupSwitch() {
  var group = _initiativesGroup() || '';
  if (_initiativesLoadedGroup !== group) {
    _initiativesSelectedId = '';
    _initiativesDetail = null;
    _initiativesDetailLoadingId = '';
    _initiativesLastError = '';
  }
  initiativesEnsureLoaded({ force: true });
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
  if (initiativeId) initiativesLoadDetail(initiativeId, { force: true });
  initiativesEnsureLoaded({ force: true });
}

function initiativesHandleError(msg) {
  if (!_initiativesPanelVisible()) return false;
  var text = String((msg && msg.message) || 'Initiative command failed');
  if (text.toLowerCase().indexOf('initiative') < 0
      && !_initiativesSaving
      && !_initiativesDetailLoadingId
      && !_initiativesLoadingGroup) {
    return false;
  }
  _initiativesSaving = false;
  _initiativesDetailLoadingId = '';
  _initiativesLoadingGroup = null;
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
  html += '<div class="initiative-column-count">' + esc(_initiativeCountLabel(items.length)) + '</div></div>';
  html += '</div>';
  html += '<div class="initiative-column-body" id="initiative-col-' + esc(status) + '">';
  if (!items.length) {
    html += '<div class="initiative-empty">No initiatives.</div>';
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
    html += '<button type="button" class="initiative-secondary-toggle' + (open ? ' active' : '') + '" onclick="initiativesToggleSecondary(\'' + esc(status) + '\')">';
    html += esc(status) + ' <span>' + count + '</span></button>';
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
    html += '<div class="initiative-empty compact">No linked tasks.</div>';
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
    html += '<div class="initiative-empty compact">' + esc(linked.hidden_count + ' linked task(s) hidden by scope.') + '</div>';
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
    html += '<div class="initiative-empty compact">No linked decisions.</div>';
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
    html += '<div class="initiative-empty compact">' + esc(linked.hidden_count + ' linked decision(s) hidden by scope.') + '</div>';
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
    return '<aside class="initiative-detail empty"><div class="initiative-empty-detail">Select an initiative to inspect scope and links.</div></aside>';
  }
  var loading = _initiativesDetailLoadingId === _initiativesSelectedId && !_initiativesDetail;
  var detail = _initiativeDetailBase();
  var html = '<aside class="initiative-detail" id="initiative-detail-drawer">';
  html += '<div class="initiative-detail-head">';
  html += '<div><div class="initiative-detail-kicker">Initiative</div><h2>' + esc(detail.title || _initiativesSelectedId) + '</h2>';
  html += '<div class="initiative-detail-id">' + esc(_initiativesSelectedId) + '</div></div>';
  html += '<button type="button" class="initiative-close" onclick="initiativesCloseDetail()" title="Close">×</button>';
  html += '</div>';
  if (loading) html += '<div class="initiative-loading">Loading detail...</div>';
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
  html += '<button type="button" class="btn-primary" onclick="initiativesSaveDetail()"' + (_initiativesSaving ? ' disabled' : '') + '>' + (_initiativesSaving ? 'Saving...' : 'Save scope') + '</button>';
  html += '</div>';
  html += '</div>';
  html += '<section class="initiative-links-section"><h3>Linked tasks</h3>' + _renderInitiativeLinkedTasks(detail) + '</section>';
  html += '<section class="initiative-links-section"><h3>Linked decisions</h3>' + _renderInitiativeLinkedDecisions(detail) + '</section>';
  html += '</aside>';
  return html;
}

function renderInitiativesPanel() {
  var panel = document.getElementById('panel-initiatives');
  if (!panel) return;
  _initiativesCaptureDrafts();
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(panel, {
      scrollSelectors: [
        '#initiatives-roadmap-scroll',
        '#initiative-col-now',
        '#initiative-col-next',
        '#initiative-col-later',
        '#initiative-col-triage',
        '#initiative-col-parked',
        '#initiative-col-shipped',
        '#initiative-detail-drawer',
      ],
      captureFocusKey: function(active) {
        if (active && active.dataset && active.dataset.field) {
          return '[data-field="' + active.dataset.field + '"]';
        }
        return '';
      },
    });
  }
  var group = _initiativesGroup();
  initiativesEnsureLoaded();
  var buckets = _initiativesByStatus(group);
  var total = _initiativesListForGroup(group).length;
  var html = '';
  html += '<div class="initiatives-panel">';
  html += '<div class="tpled-header initiatives-header">';
  html += '<div class="tpled-header-copy"><div class="tpled-header-title-row"><span class="tpled-header-title">Planning</span></div>';
  html += '<div class="tpled-header-subtitle">Initiatives grouped by roadmap bucket for ' + esc(group || 'all groups') + '. Linked execution stays on Board tasks.</div></div>';
  html += '<div class="tpled-header-controls">';
  html += '<span class="initiative-total">' + esc(_initiativeCountLabel(total)) + '</span>';
  html += '<button class="tpled-new-btn" onclick="initiativesRefresh()" title="Refresh">&#x21BB;</button>';
  html += '</div></div>';
  if (_initiativesLastError) html += '<div class="initiative-error">' + esc(_initiativesLastError) + '</div>';
  if (_initiativesLoadingGroup === group && _initiativesLoadedGroup !== group) {
    html += '<div class="initiative-loading">Loading initiatives...</div>';
  }
  html += '<div class="initiatives-workspace">';
  html += _renderInitiativesRoadmap(group, buckets);
  html += _renderInitiativeDetail();
  html += '</div></div>';
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

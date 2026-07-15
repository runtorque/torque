/* Agent panel module: engineer. */

function _renderEngineerJournal(agent) {
  var group = String((agent && agent.group) || '');
  if (typeof _agentPanelLegacyRenderJournal === 'function') return _agentPanelLegacyRenderJournal(group, agent);
  var entries = _agentPanelEngineerJournalEntries(group, agent);
  if (typeof _agentPanelLegacyRenderJournalEntries === 'function') return _agentPanelLegacyRenderJournalEntries(entries, true);
  if (!entries.length) return '<div class="agent-panel-empty">No journal entries yet.</div>';
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < entries.length; i++) {
    var entry = entries[i] || {};
    html += '<div class="agent-panel-entry">';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="' + _agentPanelJournalBadgeClass(entry.type || 'note') + '">'
      + _agentPanelEsc(entry.type || 'note') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _agentPanelEsc(_agentPanelTimeAgo(entry.timestamp)) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _agentPanelEsc(entry.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelEngineerJournalAuthorId(group, agent) {
  if (agent && String((agent.kind || '')).trim() === 'engineer'
      && String(agent.id || '').trim()) {
    return String(agent.id || '').trim();
  }
  var configured = group ? _agentPanelEngineerAgent(group) : null;
  return configured && configured.id ? String(configured.id || '').trim() : '';
}

function _agentPanelEngineerJournalEntries(group, agent) {
  var store = state && state.engineer_journal ? state.engineer_journal : null;
  if (!store) return [];
  var authorId = _agentPanelEngineerJournalAuthorId(group, agent);
  if (authorId && Array.isArray(store[authorId])) return store[authorId];
  var legacy = group && Array.isArray(store[group]) ? store[group] : [];
  if (authorId && legacy.length) {
    return legacy.filter(function(entry) {
      return String((entry && entry.author_cell_id) || '') === authorId;
    });
  }
  return [];
}

function _agentPanelQueuedLaneOrder(lane) {
  lane = String(lane || '').trim();
  if (lane === 'Backlog') return 0;
  if (lane === 'To Do') return 1;
  if (lane === 'In Progress') return 2;
  return 99;
}

function _agentPanelTaskTimestampSeconds(task) {
  task = task || {};
  var raw = task.lane_entered_at || task.updated_at || task.started_at || task.created_at || 0;
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : 0;
  if (raw == null || raw === '') return 0;
  var numeric = Number(raw);
  if (Number.isFinite(numeric) && String(raw).trim() !== '') return numeric;
  var parsed = Date.parse(String(raw));
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : 0;
}

function _agentPanelTaskPosition(task) {
  var pos = Number(task && task.position);
  return Number.isFinite(pos) ? pos : 0;
}

function _agentPanelEngineerQueuedTaskEntries(agent) {
  var engineerId = String((agent && agent.id) || '').trim();
  var tasks = [];
  if (!engineerId || !state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (typeof taskIsEngineerMessageFollowup === 'function'
        && taskIsEngineerMessageFollowup(task)) continue;
    if (String(task.assigned_engineer_id || '').trim() !== engineerId) continue;
    var lane = String(task.lane || '').trim();
    if (lane !== 'Backlog' && lane !== 'To Do' && lane !== 'In Progress') continue;
    tasks.push(task);
  }
  tasks.sort(function(a, b) {
    var laneDiff = _agentPanelQueuedLaneOrder(a && a.lane)
      - _agentPanelQueuedLaneOrder(b && b.lane);
    if (laneDiff) return laneDiff;
    var posDiff = _agentPanelTaskPosition(a) - _agentPanelTaskPosition(b);
    if (posDiff) return posDiff;
    var tsDiff = _agentPanelTaskTimestampSeconds(b) - _agentPanelTaskTimestampSeconds(a);
    if (tsDiff) return tsDiff;
    return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
  });
  return tasks;
}

function _agentPanelTaskWorkerLabel(task) {
  var workerId = String((task && task.agent_id) || '').trim();
  if (!workerId || !state || !state.agents || !state.agents[workerId]) return '';
  var worker = state.agents[workerId];
  return worker.name || worker.slug || worker.id || '';
}

function _agentPanelRenderEngineerQueuedTaskItem(agent, task) {
  var taskId = (task && task.id) || '';
  var title = (task && (task.task || task.title)) || taskId || 'Task';
  var lane = (task && task.lane) || 'Queued';
  var status = task ? String(task.status || '').trim() : '';
  var engineerName = _agentPanelAgentDisplayName(agent, 'Engineer');
  var workerLabel = _agentPanelTaskWorkerLabel(task);
  var ts = _agentPanelTaskTimestampSeconds(task);
  var meta = ts ? ('updated ' + _agentPanelTimeAgo(ts)) : 'queued task';
  if (workerLabel) meta = 'worker ' + workerLabel + ' · ' + meta;
  var anchorKey = 'engineer-queued-task-' + String(taskId || title);

  var html = '<div class="agent-panel-worklog-item agent-panel-queued-task" data-agent-panel-anchor="'
    + _agentPanelEsc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _agentPanelEsc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _agentPanelEsc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _agentPanelEsc(lane) + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _agentPanelEsc(engineerName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _agentPanelEsc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _agentPanelEsc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _renderEngineerQueuedTasks(agent) {
  var tasks = _agentPanelEngineerQueuedTaskEntries(agent);
  var agentName = _agentPanelAgentDisplayName(agent, 'this engineer');
  var html = '<div class="agent-panel-worklog-tab agent-panel-queued-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Queued tasks</span>';
  html += '<span class="agent-panel-worklog-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + tasks.length + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-note">Tasks assigned to '
    + _agentPanelEsc(agentName)
    + ' in Backlog, To Do, or In Progress.</div>';
  if (!tasks.length) {
    html += '<div class="agent-panel-event-empty">No queued tasks for this engineer.</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < tasks.length; i++) {
    html += _agentPanelRenderEngineerQueuedTaskItem(agent, tasks[i]);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _renderEngineerWorklog(agent) {
  var group = String((agent && agent.group) || '');
  var ws = _agentPanelEngineerSettings(group);
  if (typeof _agentPanelLegacyRenderWorklog === 'function') return _agentPanelLegacyRenderWorklog(group, ws);
  return '<div class="agent-panel-empty">No completed tasks yet.</div>';
}

function _agentPanelSpecializationsListMatchesGroup(group) {
  if (typeof _orderedSpecializationsListMatchesGroup === 'function') {
    return _orderedSpecializationsListMatchesGroup(group);
  }
  return String((state && state.specializations_group) || '') === String(group || '');
}

function _agentPanelSpecializationCatalog(group) {
  if (typeof _orderedSpecializationsCatalog === 'function') {
    return _orderedSpecializationsCatalog(group, { projectOnly: true });
  }
  var matchesGroup = _agentPanelSpecializationsListMatchesGroup(group);
  var raw = matchesGroup && state && Array.isArray(state.specializations)
    ? state.specializations
    : [];
  var names = [];
  var metaByName = {};
  var seen = {};
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i] || {};
    if (item.global) continue;
    var name = String(item.name || '').trim();
    if (!name || seen[name]) continue;
    seen[name] = true;
    names.push(name);
    metaByName[name] = item;
  }
  return { matchesGroup: matchesGroup, names: names, metaByName: metaByName };
}

function _agentPanelNormalizeSpecializations(raw, availableNames, filterKnown) {
  if (typeof _normalizeOrderedSpecializationSelection === 'function') {
    return _normalizeOrderedSpecializationSelection(raw, availableNames, {
      filterKnown: !!filterKnown,
    });
  }
  var available = Array.isArray(availableNames) ? availableNames : [];
  var availableSet = available.length ? {} : null;
  if (availableSet) {
    for (var i = 0; i < available.length; i++) availableSet[available[i]] = true;
  }
  var out = [];
  var seen = {};
  var items = Array.isArray(raw) ? raw : [];
  for (var j = 0; j < items.length; j++) {
    var name = String(items[j] || '').trim();
    if (!name || seen[name]) continue;
    if (filterKnown && availableSet && !availableSet[name]) continue;
    seen[name] = true;
    out.push(name);
  }
  return out;
}

function _agentPanelEnsureSpecializationsList(group) {
  group = String(group || '');
  if (!group || _agentPanelSpecializationsListMatchesGroup(group)) return;
  var now = Date.now ? Date.now() : 0;
  if (_agentPanelSpecializationsRequestedGroup === group
      && now - Number(_agentPanelSpecializationsRequestedAt || 0) < 1000) {
    return;
  }
  _agentPanelSpecializationsRequestedGroup = group;
  _agentPanelSpecializationsRequestedAt = now;
  if (typeof send === 'function') send({ cmd: 'list_specializations', group: group });
}

function _agentPanelEngineerSpecializationsCurrent(agent) {
  var raw = agent && Array.isArray(agent.engineer_specializations)
    ? agent.engineer_specializations
    : [];
  return raw.map(function(item) {
    return String(item || '').trim();
  }).filter(Boolean);
}

function _agentPanelEngineerSpecializationsArchitectId(agent) {
  if (!agent || _agentPanelKind(agent) !== 'engineer') return '';
  return String(agent.hired_by_architect_id || '').trim();
}

function _agentPanelEngineerSpecializationState(engineerId) {
  engineerId = String(engineerId || '').trim();
  if (!_agentPanelEngineerSpecializationEditors[engineerId]) {
    _agentPanelEngineerSpecializationEditors[engineerId] = {
      editing: false,
      draft: [],
      error: '',
      saving: false,
    };
  }
  return _agentPanelEngineerSpecializationEditors[engineerId];
}

function _agentPanelEngineerSpecializationSelection(agent, ui, catalog) {
  var raw = ui && ui.editing ? ui.draft : _agentPanelEngineerSpecializationsCurrent(agent);
  return _agentPanelNormalizeSpecializations(
    raw,
    catalog.names,
    catalog.matchesGroup
  );
}

function _agentPanelSpecializationChipsHtml(names) {
  names = Array.isArray(names) ? names : [];
  if (!names.length) {
    return '<span class="agent-panel-specialization-empty">Generalist (no specialization)</span>';
  }
  var html = '<div class="agent-panel-specialization-chips">';
  for (var i = 0; i < names.length; i++) {
    html += '<span class="agent-panel-specialization-chip'
      + (i === 0 ? ' primary' : '')
      + '">' + _agentPanelEsc(names[i])
      + (i === 0 ? ' <span class="agent-panel-specialization-primary">(primary)</span>' : '')
      + '</span>';
  }
  html += '</div>';
  return html;
}

function _agentPanelEngineerSpecializationsEditorHtml(agent) {
  var engineerId = String((agent && agent.id) || '').trim();
  var architectId = _agentPanelEngineerSpecializationsArchitectId(agent);
  if (!engineerId || !architectId) return '';
  var group = String((agent && agent.group) || '');
  _agentPanelEnsureSpecializationsList(group);
  var architect = state && state.agents ? state.agents[architectId] : null;
  var architectLabel = architect
    ? (architect.name || architect.slug || architect.id)
    : architectId;
  var catalog = _agentPanelSpecializationCatalog(group);
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  var selected = _agentPanelEngineerSpecializationSelection(agent, ui, catalog);
  if (ui.editing) ui.draft = selected.slice();
  var engineerIdJs = JSON.stringify(engineerId);
  var safeId = _agentPanelDomIdToken(engineerId);
  var html = '<div class="detail-section-card agent-panel-specializations-card"'
    + ' data-agent-panel-specializations-editor="' + _agentPanelAttr(engineerId) + '">';
  html += '<div class="detail-section-card-head">';
  html += '<div><div class="detail-section-primary">Specializations</div>';
  html += '<div class="agent-panel-worklog-note">Architect-owned routing metadata for '
    + _agentPanelEsc(architectLabel)
    + '. First selected is primary.</div></div>';
  html += '<div class="detail-section-card-actions">';
  if (ui.editing) {
    html += '<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary"'
      + (ui.saving ? ' disabled' : '')
      + ' onclick="' + _agentPanelEventAttr('agentPanelSaveEngineerSpecializations(' + engineerIdJs + ')') + '">'
      + (ui.saving ? 'Saving…' : 'Save')
      + '</button>';
    html += '<button type="button" class="detail-inline-editor-btn" onclick="'
      + _agentPanelEventAttr('agentPanelCancelEngineerSpecializationsEdit(' + engineerIdJs + ')')
      + '">Cancel</button>';
  } else {
    html += '<button type="button" class="detail-inline-editor-btn" onclick="'
      + _agentPanelEventAttr('agentPanelStartEngineerSpecializationsEdit(' + engineerIdJs + ')')
      + '">Edit</button>';
  }
  html += '</div></div>';
  if (ui.error) {
    html += '<div class="agent-panel-specializations-error">'
      + _agentPanelEsc(ui.error)
      + '</div>';
  }
  if (!catalog.matchesGroup) {
    html += '<div class="agent-panel-worklog-note">Loading project specialization options…</div>';
  }
  if (!ui.editing) {
    html += _agentPanelSpecializationChipsHtml(selected);
    html += '</div>';
    return html;
  }
  html += '<div class="specializations-picker agent-panel-specializations-picker">';
  html += '<ul class="specializations-selected">';
  for (var i = 0; i < selected.length; i++) {
    html += '<li class="specialization-entry">';
    html += '<span class="specialization-entry-label">'
      + _agentPanelEsc(selected[i])
      + (i === 0 ? ' (primary)' : '')
      + '</span>';
    html += '<span class="specialization-controls-row">';
    if (i > 0) {
      html += '<button type="button" title="Move up" onclick="'
        + _agentPanelEventAttr('agentPanelMoveEngineerSpecialization(' + engineerIdJs + ',' + i + ',-1)')
        + '">↑</button>';
    }
    if (i < selected.length - 1) {
      html += '<button type="button" title="Move down" onclick="'
        + _agentPanelEventAttr('agentPanelMoveEngineerSpecialization(' + engineerIdJs + ',' + i + ',1)')
        + '">↓</button>';
    }
    html += '<button type="button" title="Remove" onclick="'
      + _agentPanelEventAttr('agentPanelRemoveEngineerSpecialization(' + engineerIdJs + ',' + i + ')')
      + '">×</button>';
    html += '</span></li>';
  }
  html += '</ul>';
  html += '<div class="specializations-controls">';
  html += '<select id="agent-panel-specializations-available-' + _agentPanelAttr(safeId) + '">';
  html += '<option value="">'
    + _agentPanelEsc(!catalog.matchesGroup
      ? 'Loading specializations...'
      : (catalog.names.length ? 'Pick a specialization...' : 'No specializations available'))
    + '</option>';
  for (var optionIndex = 0; optionIndex < catalog.names.length; optionIndex++) {
    var name = catalog.names[optionIndex];
    if (selected.indexOf(name) >= 0) continue;
    var meta = catalog.metaByName[name] || {};
    html += '<option value="' + _agentPanelAttr(name) + '"'
      + (meta.preamble ? ' title="' + _agentPanelAttr(String(meta.preamble).slice(0, 200)) + '"' : '')
      + '>' + _agentPanelEsc(name) + '</option>';
  }
  html += '</select>';
  html += '<button type="button" class="btn-secondary" onclick="'
    + _agentPanelEventAttr('agentPanelAddEngineerSpecialization(' + engineerIdJs + ')')
    + '">+ Add</button>';
  html += '</div></div></div>';
  return html;
}

function _agentPanelRefreshAfterSpecializationEdit() {
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) {
    return;
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function agentPanelStartEngineerSpecializationsEdit(engineerId) {
  var agent = state && state.agents ? state.agents[String(engineerId || '')] : null;
  if (!agent) return;
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  ui.editing = true;
  ui.saving = false;
  ui.error = '';
  ui.draft = _agentPanelNormalizeSpecializations(
    _agentPanelEngineerSpecializationsCurrent(agent),
    catalog.names,
    catalog.matchesGroup
  );
  _agentPanelEnsureSpecializationsList(agent.group || '');
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelCancelEngineerSpecializationsEdit(engineerId) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  ui.editing = false;
  ui.saving = false;
  ui.error = '';
  ui.draft = [];
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelAddEngineerSpecialization(engineerId, name) {
  var agent = state && state.agents ? state.agents[String(engineerId || '')] : null;
  if (!agent) return;
  var safeId = _agentPanelDomIdToken(engineerId);
  if (typeof name === 'undefined') {
    var select = document.getElementById('agent-panel-specializations-available-' + safeId);
    name = select ? select.value : '';
  }
  name = String(name || '').trim();
  if (!name) return;
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  if (catalog.matchesGroup && catalog.names.indexOf(name) < 0) return;
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  ui.editing = true;
  ui.error = '';
  if (!Array.isArray(ui.draft)) ui.draft = [];
  if (ui.draft.indexOf(name) < 0) ui.draft.push(name);
  ui.draft = _agentPanelNormalizeSpecializations(
    ui.draft,
    catalog.names,
    catalog.matchesGroup
  );
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelRemoveEngineerSpecialization(engineerId, idx) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!Array.isArray(ui.draft)) ui.draft = [];
  if (idx < 0 || idx >= ui.draft.length) return;
  ui.editing = true;
  ui.error = '';
  ui.draft.splice(idx, 1);
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelMoveEngineerSpecialization(engineerId, idx, delta) {
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!Array.isArray(ui.draft)) ui.draft = [];
  var newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= ui.draft.length) return;
  ui.editing = true;
  ui.error = '';
  var moved = ui.draft.splice(idx, 1)[0];
  ui.draft.splice(newIdx, 0, moved);
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelSaveEngineerSpecializations(engineerId) {
  engineerId = String(engineerId || '').trim();
  var agent = state && state.agents ? state.agents[engineerId] : null;
  if (!agent) return;
  var architectId = _agentPanelEngineerSpecializationsArchitectId(agent);
  var ui = _agentPanelEngineerSpecializationState(engineerId);
  if (!architectId) {
    ui.error = 'engineer not found in scope';
    _agentPanelRefreshAfterSpecializationEdit();
    return;
  }
  var catalog = _agentPanelSpecializationCatalog(agent.group || '');
  var specializations = _agentPanelNormalizeSpecializations(
    ui.draft,
    catalog.names,
    catalog.matchesGroup
  );
  ui.draft = specializations.slice();
  ui.saving = true;
  ui.error = '';
  if (typeof send === 'function') {
    send({
      cmd: 'architect_engineer_set_specializations',
      architect_id: architectId,
      engineer_id: engineerId,
      specializations: specializations,
    });
  }
  _agentPanelRefreshAfterSpecializationEdit();
}

function _agentPanelSpecializationsVisibleForFocused(agent) {
  var focused = agent || (typeof _resolveFocusedAgent === 'function' ? _resolveFocusedAgent() : null);
  if (!focused) return false;
  if (_agentPanelKind(focused) === 'engineer') {
    return !!_agentPanelEngineerSpecializationsArchitectId(focused);
  }
  return false;
}

function agentPanelRenderEngineerSpecializationsEditor() {
  if (!_agentPanelSpecializationsVisibleForFocused()) return;
  _agentPanelRefreshAfterSpecializationEdit();
}

function agentPanelReceiveEngineerSpecializations(msg) {
  msg = msg || {};
  var engineerId = String(msg.engineer_id || '').trim();
  if (!engineerId) return;
  var specializations = Array.isArray(msg.specializations)
    ? msg.specializations.slice()
    : [];
  var cell = state && state.agents ? state.agents[engineerId] : null;
  if (cell) cell.engineer_specializations = specializations;
  var ui = _agentPanelEngineerSpecializationEditors[engineerId];
  if (ui) {
    var wasSaving = !!ui.saving;
    ui.saving = false;
    if (ui.editing && !wasSaving) {
      // Preserve an unsaved local draft across unrelated response/delta
      // traffic; only a save response should collapse the editor.
    } else {
      ui.error = '';
      ui.draft = specializations.slice();
      ui.editing = false;
    }
  }
  var focused = typeof _resolveFocusedAgent === 'function'
    ? _resolveFocusedAgent()
    : null;
  if (focused) {
    var focusedId = String(focused.id || '');
    var focusedKind = _agentPanelKind(focused);
    if (focusedId === engineerId
        || (focusedKind === 'architect'
          && cell
          && String(cell.hired_by_architect_id || '') === focusedId)) {
      _agentPanelRefreshAfterSpecializationEdit();
    }
  }
}

function agentPanelHandleEngineerSpecializationsError(msg) {
  var message = String((msg && (msg.message || msg.error)) || '').trim();
  if (!message) return false;
  var handled = false;
  for (var engineerId in _agentPanelEngineerSpecializationEditors) {
    var ui = _agentPanelEngineerSpecializationEditors[engineerId];
    if (!ui || !ui.saving) continue;
    ui.saving = false;
    ui.error = message;
    handled = true;
  }
  if (!handled) return false;
  if (typeof _showToast === 'function') _showToast(message, 'error');
  _agentPanelRefreshAfterSpecializationEdit();
  return true;
}

function _agentPanelTabRenderParts(agent, kind, activeTab) {
  var parts = { bodyHtml: '', headerRightHtml: '' };
  if (kind === 'engineer') {
    if (activeTab === 'behavior') {
      parts.bodyHtml = typeof renderBehaviorOverlayTab === 'function'
        ? renderBehaviorOverlayTab(agent)
        : '<div class="agent-panel-empty">Behavior overlay UI is unavailable.</div>';
    } else if (activeTab === 'events') {
      parts.headerRightHtml = _agentPanelDigestHeaderRight(agent);
      parts.bodyHtml += _renderEngineerEvents(agent);
    } else if (activeTab === 'queued') {
      parts.bodyHtml += _renderEngineerQueuedTasks(agent);
    } else if (activeTab === 'worklog') {
      parts.bodyHtml += _renderEngineerWorklog(agent);
    } else {
      parts.bodyHtml += _renderEngineerJournal(agent);
    }
    return parts;
  }
  if (kind === 'worker') {
    parts.bodyHtml = (activeTab === 'worklog')
      ? _agentPanelWorkerWorklog(agent)
      : (activeTab === 'messages')
      ? _agentPanelWorkerMessages(agent)
      : _renderAgentEventsWithInnerTabs(agent);
    return parts;
  }
  if (kind === 'architect') {
    if (activeTab === 'behavior') {
      parts.bodyHtml = typeof renderBehaviorOverlayTab === 'function'
        ? renderBehaviorOverlayTab(agent)
        : '<div class="agent-panel-empty">Behavior overlay UI is unavailable.</div>';
    } else if (activeTab === 'messages') {
      parts.bodyHtml = _agentPanelArchitectMessages(agent);
    } else if (activeTab === 'events') {
      parts.headerRightHtml = _agentPanelDigestHeaderRight(agent);
      parts.bodyHtml = _renderArchitectEvents(agent);
    } else if (activeTab === 'journal') {
      parts.bodyHtml = _agentPanelArchitectJournalHtml(agent);
    } else {
      parts.bodyHtml = _agentPanelArchitectDecisionsHtml(agent);
    }
    return parts;
  }
  return parts;
}

function _renderEngineerPanel(agent) {
  var activeTab = _agentPanelActiveTab('engineer');
  var parts = _agentPanelTabRenderParts(agent, 'engineer', activeTab);
  var bodyHtml = _agentPanelBodyWithClassManager(
    agent,
    _agentPanelEngineerSpecializationsEditorHtml(agent) + (parts.bodyHtml || ''),
    activeTab === 'behavior'
  );
  return _agentPanelShell(
    _agentPanelRoleTitle(agent, 'Engineer'),
    'Journal, digest queue, assigned tasks, and completed work for this engineer\'s group.',
    'engineer',
    activeTab,
    bodyHtml,
    (parts.headerRightHtml || ''),
    (agent && agent.id) || '',
    _agentPanelUpwardBreadcrumbHtml(agent)
  );
}

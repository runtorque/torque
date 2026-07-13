/* Agent panel module: legacy Engineer group support. */
/* Agent panel — journal + events + worklog */

var _engineerReplyDraft = '';
var _engineerActiveTabByGroup = {};
var _engineerJournalSubviewByGroup = {};
var _engineerSessionMapMetaByGroup = {};
var _engineerHealthOrder = ['blocked', 'stale-in-progress', 'stalled', 'thrashing', 'idle-risk'];
var _engineerHealthLabels = {
  'blocked': 'Blocked',
  'stale-in-progress': 'Stale in progress',
  'stalled': 'Stalled',
  'thrashing': 'Thrashing',
  'idle-risk': 'Idle risk',
};
var _engineerVerificationLabels = {
  'pending': 'Verify pending',
  'attempted': 'Verify attempted',
  'passed': 'Verified',
  'failed': 'Verify failed',
};
var _engineerEventsCountdownTimer = 0;
var _engineerHealthSeverity = {
  'healthy': 0,
  'idle-risk': 1,
  'thrashing': 2,
  'stalled': 3,
  'stale-in-progress': 4,
  'blocked': 5,
};

function _engineerCreatedSortValue(cell) {
  if (!cell) return '';
  return String(cell.created_at || cell.updated_at || cell.id || '');
}

function _engineerSortByCreatedAt(a, b) {
  return _engineerCreatedSortValue(a).localeCompare(_engineerCreatedSortValue(b));
}

function _engineerSortArchitectsByCreatedAt(a, b) {
  return _engineerSortByCreatedAt(a, b);
}


var _engineerArchitectExpanded = {};
var _engineerArchitectDecisionUi = {};
var _ENGINEER_DECISION_STATUSES = ['proposed', 'accepted', 'revised', 'rejected'];

function _engineerDecisionFocusKey(decisionId, field) {
  return 'engineer-decision-' + String(field || '') + ':' + String(decisionId || '');
}

function _engineerGroupAgents(group) {
  var agents = [];
  if (!state || !state.agents) return agents;
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent') continue;
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent)) continue;
    if (group && agent.group !== group) continue;
    agents.push(agent);
  }
  agents.sort(_engineerSortByCreatedAt);
  return agents;
}

function _engineerArchitectAgents(group) {
  return _engineerGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') === 'architect';
  }).sort(_engineerSortArchitectsByCreatedAt);
}

function _engineerEngineerAgents(group, architectId) {
  return _engineerGroupAgents(group).filter(function(agent) {
    if ((agent.kind || '') !== 'engineer') return false;
    var hiredByArchitectId = String(agent.hired_by_architect_id || '').trim();
    if (typeof architectId === 'undefined') return true;
    if (architectId === null) return !hiredByArchitectId;
    return hiredByArchitectId === String(architectId || '').trim();
  });
}

function _engineerWorkerOwnerId(agent) {
  return String(
    (agent && (agent.owner_engineer_id || agent.created_by_engineer_id)) || ''
  ).trim();
}

function _engineerWorkerAgents(group, engineerId) {
  return _engineerGroupAgents(group).filter(function(agent) {
    return (agent.kind || '') !== 'architect'
      && (agent.kind || '') !== 'engineer'
      && _engineerWorkerOwnerId(agent) === String(engineerId || '').trim();
  });
}

function _engineerAgentStatusLabel(agent) {
  if (agent && Number(agent.dismissed_at || 0) > 0) return 'dismissed';
  if (!agent || agent.status === 'stopped') return 'stopped';
  if (agent.activity || agent.activity_detail) return 'running';
  return 'idle';
}

function _agentPanelIsArchitectDismissed(architectId) {
  var key = String(architectId || '').trim();
  var architect = key && state && state.agents ? state.agents[key] : null;
  return !!(architect && Number(architect.dismissed_at || 0) > 0);
}

function _agentPanelDecisionArchitectId(decisionId) {
  var key = String(decisionId || '').trim();
  if (!key) return '';
  var stores = _agentPanelDecisionStores();
  for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
    var store = stores[storeIndex] || {};
    var decision = Array.isArray(store)
      ? store.find(function(item) { return String((item && item.id) || '') === key; })
      : store[key];
    if (decision) return String(decision.architect_id || '').trim();
  }
  return '';
}

function _agentPanelBlockDismissedArchitectDecisionMutation(architectId) {
  if (!_agentPanelIsArchitectDismissed(architectId)) return false;
  if (typeof _showToast === 'function') {
    _showToast('Rehire architect to modify decisions', 'warning');
  }
  return true;
}

function _engineerEngineerStatusLabel(agent) {
  return _engineerAgentStatusLabel(agent);
}

function _engineerEngineerTransferCounts(engineerId) {
  var counts = { workers: 0, tasks: 0 };
  if (!engineerId) return counts;
  if (state && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (!agent || agent.id === engineerId || agent.cell_type !== 'agent') continue;
      if ((agent.owner_engineer_id || '') === engineerId
          || (agent.created_by_engineer_id || '') === engineerId) {
        counts.workers += 1;
      }
    }
  }
  if (state && state.board_tasks) {
    for (var taskId in state.board_tasks) {
      var task = state.board_tasks[taskId];
      if (task && (task.assigned_engineer_id || '') === engineerId) counts.tasks += 1;
    }
  }
  return counts;
}

function _engineerArchitectTransferCounts(architectId) {
  var counts = { engineers: 0, decisions: 0 };
  var architectKey = String(architectId || '').trim();
  if (!architectKey) return counts;
  counts.engineers = _engineerEngineerAgents(_agentPanelCurrentGroup(), architectKey).length;
  counts.decisions = _architectDecisionsForAgent(architectKey).length;
  return counts;
}

function _engineerArchitectExpandedState(architectId) {
  var key = String(architectId || '').trim();
  return !!_engineerArchitectExpanded[key];
}

function _engineerDecisionUiState(decisionId, decision) {
  var key = String(decisionId || '').trim();
  if (!_engineerArchitectDecisionUi[key]) {
    _engineerArchitectDecisionUi[key] = {
      expanded: false,
      editing: false,
      draft: { title: '', rationale: '', status: 'proposed' },
      link_task_id: '',
      link_engineer_id: '',
    };
  }
  var entry = _engineerArchitectDecisionUi[key];
  var current = decision
    || (state && state.decisions ? state.decisions[key] : null)
    || (state && state.architect_decisions ? state.architect_decisions[key] : null)
    || {};
  if (!entry.editing) {
    entry.draft.title = String(current.title || '');
    entry.draft.rationale = String(current.rationale || '');
    entry.draft.status = String(current.status || 'proposed');
  }
  return entry;
}

function _engineerMultilineHtml(text) {
  var formatter = (typeof formatCode === 'function') ? formatCode : _agentPanelEsc;
  return formatter(text || '').replace(/\n/g, '<br>');
}

function _engineerDecisionGroups(decisions) {
  var grouped = {};
  for (var i = 0; i < _ENGINEER_DECISION_STATUSES.length; i++) {
    grouped[_ENGINEER_DECISION_STATUSES[i]] = [];
  }
  for (var j = 0; j < decisions.length; j++) {
    var decision = decisions[j] || {};
    var status = String(decision.status || 'proposed');
    if (!grouped[status]) grouped[status] = [];
    grouped[status].push(decision);
  }
  Object.keys(grouped).forEach(function(status) {
    grouped[status].sort(_engineerDecisionRecencySort);
  });
  return grouped;
}

function getArchitectDecisionTaskOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var tasks = [];
  if (!state || !state.board_tasks) return tasks;
  for (var taskId in state.board_tasks) {
    var task = state.board_tasks[taskId];
    if (!task) continue;
    if (group && task.group !== group) continue;
    tasks.push({
      value: task.id,
      label: (task.task || task.id || '') + ' (' + (task.lane || 'task') + ')',
    });
  }
  tasks.sort(function(a, b) {
    return String(a.label || '').localeCompare(String(b.label || ''));
  });
  return tasks;
}

function getArchitectDecisionEngineerOptions(architectId) {
  var architect = state && state.agents ? state.agents[String(architectId || '')] : null;
  var group = architect ? architect.group : '';
  var engineers = _engineerEngineerAgents(group);
  return engineers.map(function(engineer) {
    var label = engineer.name || engineer.id || '';
    if (engineer.slug) label += ' (' + engineer.slug + ')';
    return { value: engineer.id, label: label };
  });
}

function _engineerDecisionSupersededByIds(architectId, decisionId) {
  var architectKey = String(architectId || '').trim();
  var decisionKey = String(decisionId || '').trim();
  var ids = [];
  var seen = {};
  if (!architectKey || !decisionKey || !state) return ids;
  var stores = [];
  if (state.decisions) stores.push(state.decisions);
  if (state.architect_decisions && state.architect_decisions !== state.decisions) {
    stores.push(state.architect_decisions);
  }
  for (var storeIndex = 0; storeIndex < stores.length; storeIndex++) {
    var store = stores[storeIndex];
    var values = Array.isArray(store) ? store : Object.keys(store).map(function(key) {
      return store[key];
    });
    for (var valueIndex = 0; valueIndex < values.length; valueIndex++) {
      var candidate = values[valueIndex] || {};
      var candidateId = String(candidate.id || '').trim();
      if (!candidateId || seen[candidateId]) continue;
      if (String(candidate.architect_id || '').trim() !== architectKey) continue;
      if (String(candidate.supersedes || '').trim() !== decisionKey) continue;
      seen[candidateId] = true;
      ids.push(candidateId);
    }
  }
  ids.sort();
  return ids;
}

function _engineerDecisionRefsHtml(ids, emptyText) {
  var values = Array.isArray(ids) ? ids.filter(function(id) {
    return String(id || '').trim();
  }) : [];
  if (!values.length) {
    return '<span class="architect-decision-ref-empty">'
      + _agentPanelEsc(emptyText || 'None') + '</span>';
  }
  var html = '<span class="architect-decision-ref-list">';
  for (var i = 0; i < values.length; i++) {
    html += '<span class="architect-decision-ref">' + _agentPanelEsc(values[i]) + '</span>';
  }
  html += '</span>';
  return html;
}

function _engineerDecisionMetaRowHtml(label, valueHtml) {
  return '<div class="detail-section-card-meta architect-decision-meta-row">'
    + '<span class="architect-decision-meta-label">' + _agentPanelEsc(label) + '</span>'
    + '<span class="architect-decision-meta-value">' + (valueHtml || '') + '</span>'
    + '</div>';
}

function _engineerDecisionTimestampSeconds(value) {
  if (typeof _agentCardTimestampSeconds === 'function') {
    return _agentCardTimestampSeconds(value);
  }
  var numeric = Number(value || 0);
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 100000000000 ? numeric / 1000 : numeric;
  }
  var parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed / 1000 : 0;
}

function _engineerDecisionTimestampIso(ts) {
  ts = _engineerDecisionTimestampSeconds(ts);
  if (!ts) return '';
  try {
    return new Date(ts * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z');
  } catch (err) {
    return '';
  }
}

function _engineerDecisionRelativeTimestamp(ts) {
  ts = _engineerDecisionTimestampSeconds(ts);
  if (!ts) return '';
  var diff = Math.max(0, (Date.now() / 1000) - ts);
  if (diff < 7 * 86400 && typeof _agentPanelTimeAgo === 'function') {
    return _agentPanelTimeAgo(ts);
  }
  return _engineerDecisionTimestampIso(ts).slice(0, 10);
}

function _engineerDecisionTimestampHtml(ts, extraClass) {
  var label = _engineerDecisionRelativeTimestamp(ts);
  if (!label) return '';
  var iso = _engineerDecisionTimestampIso(ts);
  var cls = 'architect-decision-ref-empty';
  if (extraClass) cls += ' ' + extraClass;
  return '<span class="' + _agentPanelAttr(cls) + '" title="'
    + _agentPanelAttr(iso) + '">' + _agentPanelEsc(label) + '</span>';
}

function _engineerDecisionDisplayTimestamp(decision) {
  var created = _engineerDecisionTimestampSeconds(decision && decision.created_at);
  var updated = _engineerDecisionTimestampSeconds(decision && decision.updated_at);
  if (created && updated && updated > created + 60) return updated;
  return created || updated;
}

function _engineerDecisionRecencySort(a, b) {
  // Decision logs are a filing timeline: amendments update updated_at but
  // should not move existing decisions ahead of newer filed decisions.
  var aTs = _engineerDecisionTimestampSeconds(a && (a.created_at || a.updated_at));
  var bTs = _engineerDecisionTimestampSeconds(b && (b.created_at || b.updated_at));
  if (aTs !== bTs) return bTs - aTs;
  return String((a && a.id) || '').localeCompare(String((b && b.id) || ''));
}

function _agentPanelLegacyRenderWorkerRows(workers, levelClass) {
  if (!workers.length) return '';
  var html = '';
  for (var i = 0; i < workers.length; i++) {
    var worker = workers[i];
    html += '<div class="engineer-row agent-panel-hierarchy-row agent-panel-hierarchy-row-worker ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(worker.name || worker.id || '') + '</span>';
    html += _agentPanelKindBadge('worker');
    if (worker.slug) html += '<span class="engineer-row-slug">' + _esc(worker.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(_engineerAgentStatusLabel(worker)) + '">' + _esc(_engineerAgentStatusLabel(worker)) + '</span>';
    html += '</div></div>';
  }
  return html;
}

function _agentPanelLegacyRenderEngineerTreeRows(group, engineers, levelClass, workerLevelClass) {
  var html = '';
  var themeClass = _agentPanelHierarchyThemeClass(levelClass, workerLevelClass);
  for (var i = 0; i < engineers.length; i++) {
    var engineer = engineers[i];
    var status = _engineerEngineerStatusLabel(engineer);
    var workers = _engineerWorkerAgents(group, engineer.id);
    html += '<div class="agent-panel-hierarchy-branch ' + themeClass
      + (workers.length ? ' has-workers' : '')
      + '" data-agent-panel-hierarchy-engineer-id="' + _agentPanelAttr(engineer.id || '') + '">';
    html += '<div class="engineer-row agent-panel-hierarchy-row agent-panel-hierarchy-row-engineer ' + levelClass + '">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(engineer.name || engineer.id || '') + '</span>';
    html += _agentPanelKindBadge('engineer');
    if (engineer.slug) html += '<span class="engineer-row-slug">' + _esc(engineer.slug) + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    html += '</div></div>';
    if (workers.length) {
      html += '<div class="agent-panel-hierarchy-children">';
      html += _agentPanelLegacyRenderWorkerRows(workers, workerLevelClass);
      html += '</div>';
    }
    html += '</div>';
  }
  return html;
}

function _agentPanelLegacyRenderDecisionRow(architectId, decision) {
  var ui = _engineerDecisionUiState(decision.id, decision);
  var readOnly = _agentPanelIsArchitectDismissed(architectId);
  var decisionIdJs = JSON.stringify(String(decision.id || ''));
  var architectIdJs = JSON.stringify(String(architectId || ''));
  var titleFocusKey = _engineerDecisionFocusKey(decision.id, 'title');
  var rationaleFocusKey = _engineerDecisionFocusKey(decision.id, 'rationale');
  var statusFocusKey = _engineerDecisionFocusKey(decision.id, 'status');
  var linkedTaskIds = Array.isArray(decision.linked_task_ids) ? decision.linked_task_ids : [];
  var linkedEngineerIds = Array.isArray(decision.linked_engineer_ids) ? decision.linked_engineer_ids : [];
  var supersedesId = String(decision.supersedes || '').trim();
  var supersededByIds = _engineerDecisionSupersededByIds(architectId, decision.id);
  var createdTs = _engineerDecisionTimestampSeconds(decision.created_at);
  var updatedTs = _engineerDecisionTimestampSeconds(decision.updated_at);
  var headTimestampHtml = _engineerDecisionTimestampHtml(
    _engineerDecisionDisplayTimestamp(decision),
    'architect-decision-time'
  );
  var taskOptions = getArchitectDecisionTaskOptions(architectId);
  var engineerOptions = getArchitectDecisionEngineerOptions(architectId);
  var archived = _agentPanelDecisionIsArchived(decision);
  var html = '<div class="detail-section-card architect-decision-card'
    + (archived ? ' architect-decision-card-archived' : '')
    + '" data-agent-panel-anchor="decision-'
    + _agentPanelEsc(decision.id || '') + '">';
  html += '<div class="detail-section-card-head">';
  html += '<div class="architect-decision-toggle" role="button" tabindex="0" aria-expanded="'
    + (ui.expanded ? 'true' : 'false') + '" onclick="'
    + _agentPanelEventAttr('engineerToggleDecision(' + decisionIdJs + ')') + '" onkeydown="'
    + _agentPanelEventAttr('if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();engineerToggleDecision(' + decisionIdJs + ')}') + '">';
  html += '<span class="architect-decision-title-row">';
  html += '<span class="detail-section-primary architect-decision-title" title="' + _esc(decision.title || '') + '">' + _esc(decision.title || 'Decision') + '</span>';
  html += '<span class="detail-expand-caret">' + (ui.expanded ? '\u25BE' : '\u25B8') + '</span>';
  html += '</span>';
  html += '<span class="architect-decision-summary-row">';
  html += '<span class="detail-task-status">' + _esc(decision.status || 'proposed') + '</span>';
  if (archived) html += '<span class="architect-decision-archive-badge">Archived</span>';
  if (headTimestampHtml) html += headTimestampHtml;
  html += '</span>';
  html += '</div>';
  html += '<div class="detail-section-card-actions architect-decision-actions">';
  if (!readOnly && !ui.editing) {
    html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Edit decision" aria-label="Edit decision" onclick="'
      + _agentPanelEventAttr('event.stopPropagation();engineerStartDecisionEdit(' + decisionIdJs + ')')
      + '">Edit</button>';
    if (String(decision.status || 'proposed') === 'proposed' && !decision.archived) {
      html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Acknowledge decision" aria-label="Acknowledge decision" onclick="'
        + _agentPanelEventAttr('event.stopPropagation();engineerAcknowledgeDecision(' + architectIdJs + ',' + decisionIdJs + ')')
        + '">Ack</button>';
    }
  }
  if (!readOnly && !archived) {
    html += '<button type="button" class="detail-inline-editor-btn architect-decision-action-btn" title="Archive decision" aria-label="Archive decision" onclick="'
      + _agentPanelEventAttr('event.stopPropagation();engineerArchiveDecision(' + architectIdJs + ',' + decisionIdJs + ')')
      + '">Archive</button>';
  }
  html += '</div></div>';
  if (ui.expanded || ui.editing) {
    html += '<div class="architect-decision-body">';
    if (ui.editing && !readOnly) {
      html += '<input class="detail-inline-description-input architect-decision-input"'
        + ' id="' + _esc(titleFocusKey) + '"'
        + ' data-focus-key="' + _esc(titleFocusKey) + '"'
        + ' value="' + _esc(ui.draft.title || '') + '"'
        + ' oninput="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'title\',this.value)') + '"'
        + ' placeholder="Decision title">';
      html += '<textarea class="detail-inline-description-input architect-decision-textarea" rows="4"'
        + ' id="' + _esc(rationaleFocusKey) + '"'
        + ' data-focus-key="' + _esc(rationaleFocusKey) + '"'
        + ' oninput="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'rationale\',this.value)') + '"'
        + ' placeholder="Decision rationale...">' + _esc(ui.draft.rationale || '') + '</textarea>';
      html += '<select class="architect-decision-status-select"'
        + ' data-focus-key="' + _esc(statusFocusKey) + '"'
        + ' onchange="' + _agentPanelEventAttr('engineerDecisionDraftInput(' + decisionIdJs + ',\'status\',this.value)') + '">';
      for (var statusIndex = 0; statusIndex < _ENGINEER_DECISION_STATUSES.length; statusIndex++) {
        var statusOption = _ENGINEER_DECISION_STATUSES[statusIndex];
        var selected = ui.draft.status === statusOption ? ' selected' : '';
        html += '<option value="' + _esc(statusOption) + '"' + selected + '>' + _esc(statusOption) + '</option>';
      }
      html += '</select>';
      html += '<div class="detail-inline-editor-actions">';
      html += '<button type="button" class="detail-inline-editor-btn detail-inline-editor-btn-primary" onclick="'
        + _agentPanelEventAttr('engineerSaveDecisionEdit(' + architectIdJs + ',' + decisionIdJs + ')')
        + '">Save</button>';
      html += '<button type="button" class="detail-inline-editor-btn" onclick="'
        + _agentPanelEventAttr('engineerCancelDecisionEdit(' + decisionIdJs + ')')
        + '">Cancel</button>';
      html += '</div>';
    } else {
      if (decision.rationale) {
        html += '<div class="detail-section-card-body">' + _engineerMultilineHtml(decision.rationale) + '</div>';
      }
      html += _engineerDecisionMetaRowHtml(
        'Status',
        '<span class="detail-task-status">' + _agentPanelEsc(decision.status || 'proposed') + '</span>'
          + (decision.archived ? ' <span class="architect-decision-ref-empty">archived</span>' : '')
      );
      if (createdTs) {
        html += _engineerDecisionMetaRowHtml('Created', _engineerDecisionTimestampHtml(createdTs));
      }
      if (createdTs && updatedTs > createdTs + 60) {
        html += _engineerDecisionMetaRowHtml('Updated', _engineerDecisionTimestampHtml(updatedTs));
      }
      html += _engineerDecisionMetaRowHtml('Linked tasks', _engineerDecisionRefsHtml(linkedTaskIds, 'None'));
      html += _engineerDecisionMetaRowHtml('Linked engineers', _engineerDecisionRefsHtml(linkedEngineerIds, 'None'));
      if (supersedesId) {
        html += _engineerDecisionMetaRowHtml('Supersedes', _engineerDecisionRefsHtml([supersedesId], 'None'));
      }
      if (supersededByIds.length) {
        html += _engineerDecisionMetaRowHtml('Superseded by', _engineerDecisionRefsHtml(supersededByIds, 'None'));
      }
      if (!readOnly) {
        html += '<div class="architect-decision-link-row">';
        html += '<select class="architect-decision-link-select" onchange="'
          + _agentPanelEventAttr('engineerDecisionLinkSelect(' + decisionIdJs + ',\'task\',this.value)')
          + '">';
        html += '<option value="">Link task…</option>';
        for (var taskIndex = 0; taskIndex < taskOptions.length; taskIndex++) {
          var taskOption = taskOptions[taskIndex];
          var taskSelected = ui.link_task_id === taskOption.value ? ' selected' : '';
          html += '<option value="' + _esc(taskOption.value) + '"' + taskSelected + '>' + _esc(taskOption.label) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="detail-inline-editor-btn" onclick="'
          + _agentPanelEventAttr('engineerLinkDecisionTask(' + architectIdJs + ',' + decisionIdJs + ')')
          + '">Link task</button>';
        html += '<select class="architect-decision-link-select" onchange="'
          + _agentPanelEventAttr('engineerDecisionLinkSelect(' + decisionIdJs + ',\'engineer\',this.value)')
          + '">';
        html += '<option value="">Link engineer…</option>';
        for (var engineerIndex = 0; engineerIndex < engineerOptions.length; engineerIndex++) {
          var engineerOption = engineerOptions[engineerIndex];
          var engineerSelected = ui.link_engineer_id === engineerOption.value ? ' selected' : '';
          html += '<option value="' + _esc(engineerOption.value) + '"' + engineerSelected + '>' + _esc(engineerOption.label) + '</option>';
        }
        html += '</select>';
        html += '<button type="button" class="detail-inline-editor-btn" onclick="'
          + _agentPanelEventAttr('engineerLinkDecisionEngineer(' + architectIdJs + ',' + decisionIdJs + ')')
          + '">Link engineer</button>';
        html += '</div>';
      }
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderArchitectRoster(group) {
  var architects = _engineerArchitectAgents(group);
  var html = '<section class="engineers-roster architects-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Architects</span>';
  html += '<span class="engineers-roster-count">' + architects.length + ' total</span>';
  html += '</div>';
  if (!architects.length) {
    html += '<div class="engineers-roster-empty">No architects yet. Add one to launch a dedicated architect session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list">';
  for (var i = 0; i < architects.length; i++) {
    var architect = architects[i];
    var status = _engineerAgentStatusLabel(architect);
    var expanded = _engineerArchitectExpandedState(architect.id);
    var hireCounts = _engineerArchitectTransferCounts(architect.id);
    var architectIdJs = JSON.stringify(String(architect.id || ''));
    var dismissed = Number(architect.dismissed_at || 0) > 0;
    html += '<div class="architect-row' + (expanded ? ' expanded' : '') + '">';
    html += '<div class="engineer-row architect-parent-row">';
    html += '<div class="engineer-row-main">';
    html += '<span class="engineer-row-name">' + _esc(architect.name || architect.id || '') + '</span>';
    html += _agentPanelKindBadge('architect');
    if (architect.slug) {
      html += '<span class="engineer-row-slug">' + _esc(architect.slug) + '</span>';
    }
    html += '<span class="engineer-row-slug">decisions ' + hireCounts.decisions + ' • hired ' + hireCounts.engineers + '</span>';
    html += '</div>';
    html += '<div class="engineer-row-meta">';
    html += '<span class="engineer-row-status engineer-row-status-' + _esc(status) + '">' + _esc(status) + '</span>';
    if (dismissed) {
      html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();rehireArchitect(\'' + _esc(architect.id) + '\')">Rehire</button>';
    } else {
      html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();relaunchAgent(\'' + _esc(architect.id) + '\')">Relaunch</button>';
      html += '<button type="button" class="engineer-row-btn engineer-row-btn-danger" onclick="event.stopPropagation();dismissArchitect(\'' + _esc(architect.id) + '\')">Dismiss</button>';
    }
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();engineerRenameArchitect(\'' + _esc(architect.id) + '\')">Rename</button>';
    html += '<button type="button" class="engineer-row-btn" onclick="event.stopPropagation();engineerToggleArchitect(\'' + _esc(architect.id) + '\')">' + (expanded ? 'Hide decision log' : 'Open decision log') + '</button>';
    html += '<button type="button" class="engineer-row-btn engineer-row-btn-danger" onclick="event.stopPropagation();engineerDeleteArchitect(\'' + _esc(architect.id) + '\')">Delete</button>';
    html += '</div></div>';
    if (expanded) {
      var hiredEngineers = _engineerEngineerAgents(group, architect.id);
      var decisions = _architectDecisionsForAgent(architect.id);
      var grouped = _engineerDecisionGroups(decisions);
      html += '<div class="architect-row-body">';
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Hired engineers</span><span class="architect-roster-section-count">' + hiredEngineers.length + '</span></div>';
      if (hiredEngineers.length) {
        html += '<div class="agent-panel-hierarchy-list agent-panel-hierarchy-list-architect architect-roster-subtree">';
        html += _agentPanelLegacyRenderEngineerTreeRows(
          group,
          hiredEngineers,
          'architect-roster-level-1 architect-section-engineer-row',
          'architect-roster-level-2 architect-section-worker-row'
        );
        html += '</div>';
      } else {
        html += '<div class="engineers-roster-empty architect-roster-empty">No hired engineers yet.</div>';
      }
      html += '<div class="architect-roster-section-head"><span class="architect-roster-section-title">Decision log</span><span class="architect-roster-section-actions"><span class="architect-roster-section-count">' + decisions.length + '</span>';
      if (dismissed) {
        html += '<button type="button" class="engineer-row-btn" disabled title="Rehire architect to add decisions">+ New decision</button>';
      } else {
        html += '<button type="button" class="engineer-row-btn" onclick="'
          + _agentPanelEventAttr('event.stopPropagation();openArchitectDecisionModal(' + architectIdJs + ')')
          + '">+ New decision</button>';
      }
      html += '</span></div>';
      var hasDecisionRows = false;
      for (var statusIndex = 0; statusIndex < _ENGINEER_DECISION_STATUSES.length; statusIndex++) {
        var statusName = _ENGINEER_DECISION_STATUSES[statusIndex];
        var rows = grouped[statusName] || [];
        if (!rows.length) continue;
        hasDecisionRows = true;
        html += '<div class="architect-decision-group">';
        html += '<div class="architect-decision-group-title">' + _esc(statusName) + ' <span class="architect-decision-group-count">' + rows.length + '</span></div>';
        for (var decisionIndex = 0; decisionIndex < rows.length; decisionIndex++) {
          html += _agentPanelLegacyRenderDecisionRow(architect.id, rows[decisionIndex]);
        }
        html += '</div>';
      }
      if (!hasDecisionRows) {
        html += '<div class="engineers-roster-empty architect-roster-empty">No decisions yet.</div>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</section>';
  return html;
}

function _agentPanelLegacyRenderEngineerRoster(group) {
  var engineers = _engineerEngineerAgents(group, null);
  var html = '<section class="engineers-roster">';
  html += '<div class="engineers-roster-header">';
  html += '<span class="engineers-roster-title">Engineers</span>';
  html += '<span class="engineers-roster-count">' + engineers.length + ' user-owned</span>';
  html += '</div>';
  if (!engineers.length) {
    html += '<div class="engineers-roster-empty">No user-owned engineers yet. Add one to launch a dedicated engineer session.</div>';
    html += '</section>';
    return html;
  }
  html += '<div class="engineers-roster-list agent-panel-hierarchy-list agent-panel-hierarchy-list-user agent-panel-hierarchy-list-rooted">';
  html += '<div class="engineer-row engineer-row-virtual-parent"><div class="engineer-row-main"><span class="engineer-row-name">User</span><span class="engineer-row-kind">owner</span></div></div>';
  html += _agentPanelLegacyRenderEngineerTreeRows(
    group,
    engineers,
    'engineer-roster-level-1 user-section-engineer-row',
    'engineer-roster-level-2 user-section-worker-row'
  );
  html += '</div>';
  html += '</section>';
  return html;
}

function engineerOpenAddEngineer() {
  if (typeof openAddEngineerModal === 'function') openAddEngineerModal();
}

function engineerOpenAddArchitect() {
  if (typeof openAddArchitectModal === 'function') {
    openAddArchitectModal(_agentPanelCurrentGroup());
  }
}

function engineerRenameEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var currentName = String(engineer.name || '').trim();
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Rename Engineer',
    fields: [
      { key: 'name', label: 'Name', defaultValue: currentName, autofocus: true },
    ],
    submitLabel: 'Rename',
  }).then(function(values) {
    if (!values || typeof values.name !== 'string') return;
    var nextName = values.name.trim();
    if (!nextName || nextName === currentName) return;
    send({ cmd: 'rename_engineer', id: engineerId, new_name: nextName });
  });
}

async function engineerDeleteEngineer(engineerId) {
  var engineer = state && state.agents ? state.agents[engineerId] : null;
  if (!engineer) return;
  var counts = _engineerEngineerTransferCounts(engineerId);
  var message = 'Deleting ' + (engineer.name || engineerId)
    + ' will transfer its ' + counts.workers + ' workers and ' + counts.tasks
    + ' tasks to the user. Continue?';
  if (await showConfirm(message, {
    label: 'Delete engineer',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_engineer', id: engineerId });
  }
}

function engineerRenameArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var currentName = String(architect.name || '').trim();
  if (typeof showInputDialog !== 'function') return;
  return showInputDialog({
    title: 'Rename Architect',
    fields: [
      { key: 'name', label: 'Name', defaultValue: currentName, autofocus: true },
    ],
    submitLabel: 'Rename',
  }).then(function(values) {
    if (!values || typeof values.name !== 'string') return;
    var nextName = values.name.trim();
    if (!nextName || nextName === currentName) return;
    send({ cmd: 'update_agent', id: architectId, name: nextName });
  });
}

async function engineerDeleteArchitect(architectId) {
  var architect = state && state.agents ? state.agents[architectId] : null;
  if (!architect) return;
  var counts = _engineerArchitectTransferCounts(architectId);
  var message = 'Deleting ' + (architect.name || architectId)
    + ' will transfer ' + counts.engineers + ' hired engineer'
    + (counts.engineers === 1 ? '' : 's')
    + ' to the user and archive ' + counts.decisions + ' decision'
    + (counts.decisions === 1 ? '' : 's') + '. Continue?';
  if (await showConfirm(message, {
    label: 'Delete architect',
    variant: 'btn-danger',
  })) {
    send({ cmd: 'delete_architect', id: architectId });
  }
}

function engineerToggleArchitect(architectId) {
  var key = String(architectId || '').trim();
  if (!key) return;
  _engineerArchitectExpanded[key] = !_engineerArchitectExpanded[key];
  _agentPanelRefreshVisibleSurface();
}

function engineerToggleDecision(decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.expanded = !ui.expanded;
  _agentPanelRefreshVisibleSurface();
}

function engineerStartDecisionEdit(decisionId) {
  var architectId = _agentPanelDecisionArchitectId(decisionId);
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  ui.expanded = true;
  ui.editing = true;
  _agentPanelRefreshVisibleSurface();
}

function engineerDecisionDraftInput(decisionId, field, value) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.draft[field] = String(value || '');
}

function engineerCancelDecisionEdit(decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  ui.editing = false;
  _agentPanelRefreshVisibleSurface();
}

function engineerSaveDecisionEdit(architectId, decisionId) {
  var ui = _engineerDecisionUiState(decisionId);
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) {
    ui.editing = false;
    _agentPanelRefreshVisibleSurface();
    return;
  }
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    title: String(ui.draft.title || '').trim(),
    rationale: String(ui.draft.rationale || '').trim(),
    status: String(ui.draft.status || 'proposed'),
  });
  ui.editing = false;
  if (typeof _showToast === 'function') _showToast('Decision updated', 'success');
  _agentPanelRefreshVisibleSurface();
}

function engineerAcknowledgeDecision(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    status: 'accepted',
  });
  if (typeof _showToast === 'function') _showToast('Decision acknowledged', 'success');
}

function engineerArchiveDecision(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  send({
    cmd: 'architect_decision_update',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    archived: true,
  });
  if (typeof _showToast === 'function') _showToast('Decision archived', 'success');
}

function engineerDecisionLinkSelect(decisionId, kind, value) {
  var ui = _engineerDecisionUiState(decisionId);
  if (kind === 'task') ui.link_task_id = String(value || '');
  if (kind === 'engineer') ui.link_engineer_id = String(value || '');
}

function engineerLinkDecisionTask(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  if (!ui.link_task_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    task_id: ui.link_task_id,
  });
  ui.link_task_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to task', 'success');
  _agentPanelRefreshVisibleSurface();
}

function engineerLinkDecisionEngineer(architectId, decisionId) {
  if (_agentPanelBlockDismissedArchitectDecisionMutation(architectId)) return;
  var ui = _engineerDecisionUiState(decisionId);
  if (!ui.link_engineer_id) return;
  send({
    cmd: 'architect_decision_link',
    architect_id: String(architectId || ''),
    id: String(decisionId || ''),
    engineer_id: ui.link_engineer_id,
  });
  ui.link_engineer_id = '';
  if (typeof _showToast === 'function') _showToast('Decision linked to engineer', 'success');
  _agentPanelRefreshVisibleSurface();
}

function renderLegacyGroupPanel() {
  var el = document.getElementById('panel-agent');
  _engineerStopEventsCountdownTimer();
  if (!el) return;
  var group = _agentPanelCurrentGroup();
  var ws = _engineerGetSettings(group);
  var activeTab = _engineerActiveTab(group);
  var legacyVirtualMetas = activeTab === 'worklog'
    ? [{
      key: _agentPanelLegacyWorklogVirtualKey(group, !!(ws && ws.restrict_to_created_agents)),
      scrollSelector: '.agent-panel-content',
    }]
    : [];
  var panelStateOptions = {
    scrollSelectors: ['.agent-panel-content'],
    captureFocusKey(active) {
      if (typeof _captureMainFocusKey === 'function') {
        var key = _captureMainFocusKey(active);
        if (key) return key;
      }
      if (active && active.classList
          && active.classList.contains('agent-panel-instructions')) {
        return '.agent-panel-instructions';
      }
      return '';
    },
    capture: function(snapshot, root) {
      if (!snapshot || !root || typeof root.querySelector !== 'function') return;
      _agentPanelCaptureVirtualScrolls(root, legacyVirtualMetas);
      snapshot.anchor = _engineerCaptureScrollAnchor(
        root.querySelector('.agent-panel-content')
      );
    },
    restore: function(root, snapshot) {
      if (!root || !snapshot || typeof root.querySelector !== 'function') return;
      _engineerRestoreScrollAnchor(
        root.querySelector('.agent-panel-content'),
        snapshot.anchor
      );
    },
  };
  var panelState = _captureSurfaceState(el, panelStateOptions);

  var engineer = group ? _engineerGetAgent(group) : null;
  var bstats = engineer
    ? _agentPanelDigestBufferStats(engineer)
    : ((state.engineer_buffer_stats && state.engineer_buffer_stats[group]) || null);
  var paused = engineer
    ? !!(_agentPanelDigestSettings(engineer) && _agentPanelDigestSettings(engineer).paused)
    : !!(ws && ws.paused);
  var emptyMessage = _engineerPanelEmptyMessage(group, ws, engineer, bstats);
  _agentPanelRenderedVirtualMetas = [];

  var html = '<div class="agent-panel-panel">';

  // Header
  html += '<div class="agent-panel-header">';
  html += '<div class="agent-panel-header-copy">';
  html += '<span class="agent-panel-title">Agent';
  if (group) html += ' — ' + _esc(group);
  html += '</span>';
  html += '<div class="agent-panel-subtitle">Architect roster, engineer hierarchy, orchestration journal, digest queue, queued assignments, completed work, and session map.</div>';
  html += '</div>';
  html += '<div class="agent-panel-header-right">';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="engineerOpenAddArchitect()">+ Add Architect</button>';
  html += '<button type="button" class="agent-panel-add-engineer-btn" onclick="engineerOpenAddEngineer()">+ Add Engineer</button>';
  // Buffer stats + Pause/Resume toggle
  if (!emptyMessage && group) {
    if (bstats && bstats.buffered_events > 0) {
      html += '<span class="agent-panel-buffer-stats">'
           + _esc(_engineerHeaderBufferStats(bstats, paused, engineer))
           + '</span>';
    }
    if (engineer && engineer.id) {
      html += _agentPanelDigestPauseButton(engineer);
    }
  }
  html += '</div>';
  html += '</div>';

  if (!emptyMessage) html += _agentPanelLegacyRenderTabs(group, activeTab);
  html += '<div class="agent-panel-content">';
  html += _agentPanelLegacyRenderArchitectRoster(group);
  html += _agentPanelLegacyRenderEngineerRoster(group);
  if (emptyMessage) {
    html += '<div class="agent-panel-empty">' + _esc(emptyMessage) + '</div>';
  } else if (activeTab === 'events') {
    html += _agentPanelLegacyRenderEvents(group, ws, engineer, bstats);
  } else if (activeTab === 'queued') {
    html += _agentPanelLegacyRenderQueuedTasks(group, engineer);
  } else if (activeTab === 'worklog') {
    html += _agentPanelLegacyRenderWorklog(group, ws);
  } else {
    html += _agentPanelLegacyRenderJournal(group, engineer);
  }
  html += '</div>';
  html += '</div>';
  // TORQUE:264 follow-up: memoize the legacy engineer panel clobber.
  if (el._torqueLastHtml !== html) {
    el.innerHTML = html;
    el._torqueLastHtml = html;
  }
  _restoreSurfaceState(el, panelState, panelStateOptions);
  _agentPanelAttachVirtualScrolls(el);
  _engineerSyncEventsCountdown(el, group, activeTab);
}

function engineerTogglePause() {
  engineerTogglePauseForGroup(_agentPanelCurrentGroup());
}

function engineerTogglePauseForGroup(group) {
  if (!group) return;
  var ws = _engineerGetSettings(group);
  var cmd = (ws && ws.paused) ? 'engineer_resume' : 'engineer_pause';
  send({ cmd: cmd, group: group });
}

function toggleDigestPauseForAgent(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  var agent = (state && state.agents && state.agents[agentId]) || { id: agentId };
  var settings = _agentPanelDigestSettings(agent);
  var cmd = (settings && settings.paused) ? 'digest_resume' : 'digest_pause';
  send({ cmd: cmd, agent_id: agentId });
}

function agentPanelTogglePauseForAgent(agentId) {
  toggleDigestPauseForAgent(agentId);
}

function engineerSelectTab(tab, group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  if (tab !== 'events' && tab !== 'queued' && tab !== 'worklog') tab = 'journal';
  _engineerActiveTabByGroup[group] = tab;
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && _agentPanelKind(focusedAgent) === 'engineer' && typeof agentPanelSelectTab === 'function') {
      agentPanelSelectTab(tab);
      return;
    }
  }
  _agentPanelRefreshVisibleSurface();
}

function engineerSendNow() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'engineer_flush_now', group: group });
}

function agentPanelSendNow(agentId) {
  agentId = String(agentId || '');
  if (!agentId) return;
  send({ cmd: 'engineer_flush_now', agent_id: agentId });
}

function _engineerActiveTab(group) {
  if (!group) return 'journal';
  var tab = _engineerActiveTabByGroup[group] || 'journal';
  if (tab === 'events' || tab === 'queued' || tab === 'worklog') return tab;
  return 'journal';
}

function _engineerJournalSubview(group) {
  if (!group) return 'journal';
  return _engineerJournalSubviewByGroup[group] === 'session_map'
    ? 'session_map'
    : 'journal';
}

function _engineerSessionMapMeta(group, engineerId) {
  if (!group) return { loading: false, stale: false };
  var key = _engineerSessionMapKey(group, engineerId);
  if (!_engineerSessionMapMetaByGroup[key]) {
    _engineerSessionMapMetaByGroup[key] = { loading: false, stale: false };
  }
  return _engineerSessionMapMetaByGroup[key];
}

function _engineerFocusedSessionMapAgentId() {
  if (typeof _resolveFocusedAgent !== 'function'
      || typeof _agentPanelKind !== 'function') return '';
  var focused = _resolveFocusedAgent();
  if (!focused || _agentPanelKind(focused) !== 'engineer') return '';
  return String(focused.id || '').trim();
}

function _engineerSessionMapKey(group, engineerId) {
  group = String(group || '').trim();
  engineerId = String(
    typeof engineerId === 'undefined'
      ? _engineerFocusedSessionMapAgentId()
      : engineerId
  ).trim();
  return engineerId ? (group + '::' + engineerId) : group;
}

function _engineerDefaultSessionMapAgentId(group) {
  group = String(group || '').trim();
  if (!group || !state || !state.group_settings) return '';
  var settings = state.group_settings[group] || null;
  return String((settings && settings.engineer_agent_id) || '').trim();
}

function _engineerSessionMapData(group) {
  if (!group || !state || !state.engineer_session_maps) return null;
  var store = state.engineer_session_maps;
  var key = _engineerSessionMapKey(group);
  if (store[key]) return store[key];

  // Legacy/group-level Session Map requests do not include an engineer id, but
  // the server may answer with the group's default engineer id for strict
  // scoping. Fall back to that scoped cache entry so the group-level panel does
  // not stay stuck on the bare group key forever.
  if (!_engineerFocusedSessionMapAgentId()) {
    var defaultEngineerId = _engineerDefaultSessionMapAgentId(group);
    if (defaultEngineerId) {
      var defaultKey = _engineerSessionMapKey(group, defaultEngineerId);
      if (store[defaultKey]) return store[defaultKey];
    }
  }
  return null;
}

function _engineerRequestSessionMap(group, force) {
  if (!group) return;
  var engineerId = _engineerFocusedSessionMapAgentId();
  var meta = _engineerSessionMapMeta(group, engineerId);
  var hasData = !!_engineerSessionMapData(group);
  if (meta.loading) return;
  if (!force && hasData && !meta.stale) return;
  meta.loading = true;
  var payload = { cmd: 'engineer_session_map_read', group: group };
  if (engineerId) payload.engineer_id = engineerId;
  send(payload);
}

function _engineerResetSessionMapMeta(options) {
  options = options || {};
  var keys = Object.keys(_engineerSessionMapMetaByGroup || {});
  if (!keys.length) return;
  var clearStale = options.clearStale !== false;
  var refetchOpenMissing = !!options.refetchOpenMissing;
  var shouldRender = false;
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    var group = String(key || '').split('::')[0];
    if (!group) continue;
    var meta = _engineerSessionMapMetaByGroup[key];
    if (!meta) continue;
    var wasLoading = !!meta.loading;
    meta.loading = false;
    if (clearStale) meta.stale = false;
    if (refetchOpenMissing && _engineerIsSessionMapOpen(group) && !_engineerSessionMapData(group)) {
      _engineerRequestSessionMap(group, true);
      if (_engineerShouldRenderCurrentGroup(group)) shouldRender = true;
      continue;
    }
    if (wasLoading && _engineerShouldRenderCurrentGroup(group)) shouldRender = true;
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _engineerIsSessionMapOpen(group) {
  return _engineerJournalSubview(group) === 'session_map';
}

function _engineerShouldRenderCurrentGroup(group) {
  return !!group
    && ((typeof _panelAppVisible === 'function' && _panelAppVisible('engineer'))
      || (typeof _activePanelApp !== 'undefined' && _activePanelApp === 'engineer'))
    && _agentPanelCurrentGroup() === group;
}

function _agentPanelRefreshVisibleSurface() {
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) {
    return;
  }
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function engineerOpenSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerJournalSubviewByGroup[group] = 'session_map';
  _engineerRequestSessionMap(group, false);
  _agentPanelRefreshVisibleSurface();
}

function engineerCloseSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerJournalSubviewByGroup[group] = 'journal';
  _agentPanelRefreshVisibleSurface();
}

function engineerRefreshSessionMap(group) {
  group = group || _agentPanelCurrentGroup();
  if (!group) return;
  _engineerRequestSessionMap(group, true);
  _agentPanelRefreshVisibleSurface();
}

function _engineerReceiveSessionMap(msg) {
  var group = (msg && msg.group) || '';
  if (!group) return;
  var engineerId = (msg && msg.engineer_id) || '';
  var meta = _engineerSessionMapMeta(group, engineerId);
  meta.loading = false;
  meta.stale = false;
  if (engineerId && _engineerSessionMapMetaByGroup[group]) {
    _engineerSessionMapMetaByGroup[group].loading = false;
    _engineerSessionMapMetaByGroup[group].stale = false;
  }
  if (_engineerShouldRenderCurrentGroup(group)) {
    _agentPanelRefreshVisibleSurface();
  }
}

function _engineerMarkSessionMapStale(groups) {
  if (!groups || !groups.length) return;
  var shouldRender = false;
  for (var i = 0; i < groups.length; i++) {
    var group = groups[i];
    if (!group) continue;
    var keys = Object.keys(_engineerSessionMapMetaByGroup || {}).filter(function(key) {
      return key === group || key.indexOf(group + '::') === 0;
    });
    if (!keys.length) keys = [_engineerSessionMapKey(group)];
    for (var j = 0; j < keys.length; j++) {
      var meta = _engineerSessionMapMetaByGroup[keys[j]]
        || _engineerSessionMapMeta(group);
      meta.stale = true;
    }
    if (_engineerIsSessionMapOpen(group)) {
      _engineerRequestSessionMap(group, false);
      if (_engineerShouldRenderCurrentGroup(group)) shouldRender = true;
    }
  }
  if (shouldRender) _agentPanelRefreshVisibleSurface();
}

function _agentPanelLegacyRenderTabs(group, activeTab) {
  if (!group) return '';
  var html = '<div class="agent-panel-tabs">';
  html += '<button id="engineer-tab-journal" class="agent-panel-tab'
    + (activeTab === 'journal' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'journal\')">Journal</button>';
  html += '<button id="engineer-tab-events" class="agent-panel-tab'
    + (activeTab === 'events' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'events\')">Events</button>';
  html += '<button id="engineer-tab-queued" class="agent-panel-tab'
    + (activeTab === 'queued' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'queued\')">Queued</button>';
  html += '<button id="engineer-tab-worklog" class="agent-panel-tab'
    + (activeTab === 'worklog' ? ' active' : '')
    + '" onclick="engineerSelectTab(\'worklog\')">Completed</button>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEvents(group, ws, engineer, bstats) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }
  var digestSettings = engineer ? _agentPanelDigestSettings(engineer) : null;
  var paused = engineer ? !!(digestSettings && digestSettings.paused) : !!(ws && ws.paused);
  var stats = bstats || (engineer ? _agentPanelDigestBufferStats(engineer) : null);
  var sent = engineer ? _agentPanelDigestSentEvents(engineer) : [];
  if (!sent.length && state.engineer_sent_events && state.engineer_sent_events[group]) {
    sent = state.engineer_sent_events[group].slice();
  }
  return _agentPanelRenderEventsTab(
    stats,
    sent,
    paused,
    engineer,
    'engineerSendNow()',
    'Already sent to Engineer'
  );
}

function _agentPanelLegacyRenderEventSection(title, events, mode, emptyText) {
  var html = '<div class="agent-panel-event-section">';
  html += '<div class="agent-panel-event-section-header">';
  html += '<span class="agent-panel-event-section-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-event-section-count">' + events.length + '</span>';
  html += '</div>';
  if (!events.length) {
    html += '<div class="agent-panel-event-empty">' + _esc(emptyText) + '</div>';
    html += '</div>';
    return html;
  }
  html += '<div class="agent-panel-event-list">';
  for (var i = 0; i < events.length; i++) {
    html += _agentPanelLegacyRenderEventItem(events[i], mode);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderEventItem(event, mode) {
  var kind = _engineerEventKindLabel(event && event.kind);
  var agentName = event && event.agent_name ? String(event.agent_name) : '';
  var message = event && event.message ? String(event.message) : '';
  var summary = agentName && message
    ? agentName + ' — ' + message
    : (message || agentName || kind);
  var meta = (mode === 'sent')
    ? 'sent ' + _engineerTimeAgo(event && event.delivered_at)
    : 'queued ' + _engineerTimeAgo(event && event.timestamp);
  if (mode === 'sent' && event && event.timestamp && event.delivered_at
      && Math.abs(event.delivered_at - event.timestamp) >= 30) {
    meta += ' · event ' + _engineerTimeAgo(event.timestamp);
  }
  var anchorKey = mode + '-' + String(event && event.id ? event.id : ('idx-' + meta));
  if (mode === 'sent' && event && event.delivered_at) {
    anchorKey += '-' + Math.floor(event.delivered_at);
  }

  var html = '<div class="agent-panel-event-item agent-panel-event-item-' + _esc(mode) + '"'
    + ' data-engineer-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-event-item-header">';
  html += '<span class="agent-panel-event-kind">' + _esc(kind) + '</span>';
  html += '<span class="agent-panel-event-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-event-message">' + _esc(summary) + '</div>';
  if (event && event.task_id) {
    html += '<div class="agent-panel-event-task">' + _esc(event.task_id) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerHeaderBufferStats(bstats, paused, engineer) {
  if (!bstats || !bstats.buffered_events) return '';
  var evtCount = bstats.buffered_events;
  var label = evtCount + ' event' + (evtCount === 1 ? '' : 's');
  var nextPushIn = _engineerCountdownSeconds(bstats);
  if (paused) return label + ' paused';
  if (bstats.manual_flush_requested) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return label + ' queued for idle send';
    }
    return label + ' sending';
  }
  if (nextPushIn <= 0) return label + ' ready';
  return label + ' in ' + _engineerFormatCountdown(nextPushIn);
}

function _engineerEventsStatusText(bstats, paused, engineer) {
  var recipientName = String((engineer && (engineer.name || engineer.id)) || 'recipient');
  if (!bstats || !bstats.buffered_events) {
    return 'No queued events.';
  }
  if (paused) {
    return 'Delivery is paused — resume to send queued events.';
  }
  if (bstats.manual_flush_requested) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return 'Send requested — queued events will deliver when ' + recipientName + ' goes idle.';
    }
    return 'Sending queued events now.';
  }
  var nextPushIn = _engineerCountdownSeconds(bstats);
  if (nextPushIn <= 0) {
    if (engineer && engineer.activity && engineer.activity !== 'waiting') {
      return 'Eligible now — waiting for ' + recipientName + ' to go idle.';
    }
    return 'Eligible to send now.';
  }
  return 'Next eligible send in ' + _engineerFormatCountdown(nextPushIn) + '.';
}

function _engineerFormatCountdown(seconds) {
  var remaining = Math.max(0, Number(seconds) || 0);
  if (remaining < 60) return remaining + 's';
  var minutes = Math.floor(remaining / 60);
  var secs = remaining % 60;
  return minutes + 'm' + (secs > 0 ? String(secs).padStart(2, '0') + 's' : '');
}

function _engineerCountdownSeconds(bstats) {
  if (!bstats) return 0;
  var nextPushAt = Number(bstats.next_push_at || 0);
  if (nextPushAt > 0) {
    return Math.max(0, Math.ceil(nextPushAt - (Date.now() / 1000)));
  }
  return Math.max(0, Math.ceil(Number(bstats.next_push_in || 0)));
}

function _engineerShouldLiveUpdateCountdown(group, activeTab) {
  var surfaceState = _engineerVisibleEventsSurfaceState(group, activeTab);
  if (!surfaceState.group || surfaceState.tab !== 'events') return false;
  var payload = _engineerEventsSurfacePayload(surfaceState);
  if (!payload.bstats || !payload.bstats.buffered_events) return false;
  if (payload.paused) return false;
  if (payload.bstats.manual_flush_requested) return false;
  return _engineerCountdownSeconds(payload.bstats) > 0;
}

function _engineerStopEventsCountdownTimer() {
  if (_engineerEventsCountdownTimer && typeof clearInterval === 'function') {
    clearInterval(_engineerEventsCountdownTimer);
  }
  _engineerEventsCountdownTimer = 0;
}

function _engineerVisibleEventsSurfaceState(group, activeTab) {
  var fallbackGroup = String(group || '');
  var fallbackTab = String(activeTab || '');
  if (typeof _resolveFocusedAgent === 'function'
      && typeof _agentPanelKind === 'function'
      && typeof _agentPanelActiveTab === 'function') {
    var focused = _resolveFocusedAgent();
    if (focused && (
        _agentPanelKind(focused) === 'engineer'
        || _agentPanelKind(focused) === 'architect'
    )) {
      var focusedKind = _agentPanelKind(focused);
      return {
        agentId: String(focused.id || ''),
        group: String(focused.group || fallbackGroup || ''),
        tab: String(_agentPanelActiveTab(focusedKind) || fallbackTab || 'journal'),
      };
    }
  }
  var currentGroup = String(_agentPanelCurrentGroup() || fallbackGroup || '');
  return {
    agentId: '',
    group: currentGroup,
    tab: String(_engineerActiveTab(currentGroup) || fallbackTab || 'journal'),
  };
}

function _engineerEventsSurfacePayload(surfaceState) {
  var agentId = String((surfaceState && surfaceState.agentId) || '');
  var group = String((surfaceState && surfaceState.group) || '');
  if (agentId && state && state.agents && state.agents[agentId]) {
    var recipient = state.agents[agentId];
    var settings = _agentPanelDigestSettings(recipient);
    return {
      recipient: recipient,
      paused: !!(settings && settings.paused),
      bstats: _agentPanelDigestBufferStats(recipient),
    };
  }
  var ws = _engineerGetSettings(group);
  return {
    recipient: group ? _engineerGetAgent(group) : null,
    paused: !!(ws && ws.paused),
    bstats: (state.engineer_buffer_stats && state.engineer_buffer_stats[group]) || null,
  };
}

function _engineerSyncEventsCountdown(panel, group, activeTab) {
  if (!panel || typeof panel.querySelector !== 'function') return;
  var countdownEl = panel.querySelector('.agent-panel-events-countdown');
  if (!countdownEl) return;
  var surfaceState = _engineerVisibleEventsSurfaceState(group, activeTab);
  var surfaceGroup = surfaceState.group;
  var surfaceTab = surfaceState.tab;
  var payload = _engineerEventsSurfacePayload(surfaceState);
  countdownEl.textContent = _engineerEventsStatusText(
    payload.bstats,
    payload.paused,
    payload.recipient
  );
  if (!_engineerShouldLiveUpdateCountdown(surfaceGroup, surfaceTab)
      || typeof setInterval !== 'function') {
    return;
  }
  _engineerEventsCountdownTimer = setInterval(function() {
    var currentPanel = document.getElementById('panel-agent');
    if (!currentPanel) {
      _engineerStopEventsCountdownTimer();
      return;
    }
    var currentState = _engineerVisibleEventsSurfaceState(group, activeTab);
    var currentGroup = currentState.group;
    var currentTab = currentState.tab;
    var currentCountdown = currentPanel.querySelector('.agent-panel-events-countdown');
    if (!currentCountdown || currentTab !== 'events') {
      _engineerStopEventsCountdownTimer();
      return;
    }
    var currentPayload = _engineerEventsSurfacePayload(currentState);
    currentCountdown.textContent = _engineerEventsStatusText(
      currentPayload.bstats,
      currentPayload.paused,
      currentPayload.recipient
    );
    if (!_engineerShouldLiveUpdateCountdown(currentGroup, currentTab)) {
      _engineerStopEventsCountdownTimer();
    }
  }, 1000);
}

function _engineerEventKindLabel(kind) {
  kind = String(kind || '');
  if (!kind) return 'event';
  return kind.replace(/_/g, ' ');
}

function _engineerCaptureScrollAnchor(container) {
  if (!container || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function') {
    return null;
  }
  var items = container.querySelectorAll('[data-engineer-anchor]');
  if (!items || !items.length) return null;
  var containerRect = container.getBoundingClientRect();
  var best = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (!item || typeof item.getBoundingClientRect !== 'function') continue;
    var rect = item.getBoundingClientRect();
    if (rect.bottom >= containerRect.top) {
      best = item;
      break;
    }
  }
  if (!best) best = items[0];
  if (!best || typeof best.getBoundingClientRect !== 'function') return null;
  var anchorRect = best.getBoundingClientRect();
  return {
    key: best.getAttribute ? best.getAttribute('data-engineer-anchor') : '',
    offset: anchorRect.top - containerRect.top,
  };
}

function _engineerRestoreScrollAnchor(container, snapshot) {
  if (!container || !snapshot || !snapshot.key
      || typeof container.querySelectorAll !== 'function'
      || typeof container.getBoundingClientRect !== 'function'
      || typeof container.scrollTop !== 'number') {
    return;
  }
  var items = container.querySelectorAll('[data-engineer-anchor]');
  var target = null;
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var key = item && item.getAttribute ? item.getAttribute('data-engineer-anchor') : '';
    if (key === snapshot.key) {
      target = item;
      break;
    }
  }
  if (!target || typeof target.getBoundingClientRect !== 'function') return;
  var containerRect = container.getBoundingClientRect();
  var targetRect = target.getBoundingClientRect();
  container.scrollTop += (targetRect.top - containerRect.top) - (snapshot.offset || 0);
}

// -- Worklog tab ----------------------------------------------------------

function _agentPanelLegacyRenderQueuedTasks(group, engineer) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }
  var focusedEngineer = engineer || _engineerGetAgent(group);
  if (!focusedEngineer) {
    return '<div class="agent-panel-worklog-tab agent-panel-queued-tab">'
      + '<div class="agent-panel-worklog-header">'
      + '<span class="agent-panel-worklog-title">Queued tasks</span>'
      + '<span class="agent-panel-worklog-count">0</span>'
      + '</div>'
      + '<div class="agent-panel-event-empty">No engineer selected for this group.</div>'
      + '</div>';
  }
  return _renderEngineerQueuedTasks(focusedEngineer);
}

function _agentPanelLegacyRenderWorklog(group, ws) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }

  var entries = (state.engineer_worklog && state.engineer_worklog[group])
    ? state.engineer_worklog[group].slice()
    : [];
  if (ws && ws.restrict_to_created_agents) {
    entries = entries.filter(function(entry) {
      return !!(entry && entry.agent_owned);
    });
  }
  entries.sort(function(a, b) {
    var startedDiff = (b.started_at || 0) - (a.started_at || 0);
    if (startedDiff) return startedDiff;
    return (b.id || 0) - (a.id || 0);
  });

  var html = '<div class="agent-panel-worklog-tab">';
  html += '<div class="agent-panel-worklog-header">';
  html += '<span class="agent-panel-worklog-title">Completed tasks</span>';
  html += '<span class="agent-panel-worklog-count">' + entries.length + '</span>';
  html += '</div>';
  if (ws && ws.restrict_to_created_agents) {
    html += '<div class="agent-panel-worklog-note">Showing completed work sent to Engineer-created agents.</div>';
  } else {
    html += '<div class="agent-panel-worklog-note">Recent completed work this Engineer dispatched in this group.</div>';
  }

  if (!entries.length) {
    html += '<div class="agent-panel-event-empty">No completed tasks yet.</div>';
    html += '</div>';
    return html;
  }

  var restricted = !!(ws && ws.restrict_to_created_agents);
  var section = restricted ? 'worklog-owned' : 'worklog-all';
  var page = _agentPanelSectionPage(group, section, entries);
  html += '<div class="agent-panel-worklog-list">';
  for (var i = 0; i < page.events.length; i++) {
    html += _agentPanelLegacyRenderWorklogItem(page.events[i]);
  }
  html += '</div>';
  html += _agentPanelRenderSectionLoadMore(page, 'task');
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderWorklogItem(entry) {
  var task = (state.board_tasks && entry && entry.task_id)
    ? state.board_tasks[entry.task_id]
    : null;
  var title = (task && task.task) || (entry && entry.task_title) || (entry && entry.task_id) || 'Task';
  var taskId = (entry && entry.task_id) || '';
  var lane = task ? (task.lane || '') : 'Not on board';
  var status = task ? String(task.status || '').trim() : '';
  var agentName = _engineerWorklogAgentLabel(entry, task);
  var meta = 'dispatched ' + _engineerTimeAgo(entry && entry.started_at);
  var anchorKey = 'worklog-' + String(entry && entry.id ? entry.id : taskId || meta);

  var html = '<div class="agent-panel-worklog-item" data-engineer-anchor="' + _esc(anchorKey) + '">';
  html += '<div class="agent-panel-worklog-item-header">';
  html += '<div class="agent-panel-worklog-task">';
  html += '<div class="agent-panel-worklog-task-title">' + _esc(title) + '</div>';
  if (taskId) {
    html += '<div class="agent-panel-worklog-task-id">' + _esc(taskId) + '</div>';
  }
  html += '</div>';
  html += '<div class="agent-panel-worklog-lane">' + _esc(lane || 'Unknown') + '</div>';
  html += '</div>';
  html += '<div class="agent-panel-worklog-meta-row">';
  html += '<span class="agent-panel-worklog-agent">' + _esc(agentName) + '</span>';
  html += '<span class="agent-panel-worklog-meta">' + _esc(meta) + '</span>';
  html += '</div>';
  if (status) {
    html += '<div class="agent-panel-worklog-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerWorklogAgentLabel(entry, task) {
  var taskAgent = (task && task.agent_id && state.agents && state.agents[task.agent_id])
    ? state.agents[task.agent_id]
    : null;
  if (taskAgent) {
    return taskAgent.name || taskAgent.slug || taskAgent.id || 'Agent';
  }
  if (entry && entry.agent_name) return String(entry.agent_name);
  if (entry && entry.agent_slug) return String(entry.agent_slug);
  if (entry && entry.agent_id) return String(entry.agent_id);
  return 'Agent';
}

// -- Journal tab -----------------------------------------------------------

function _agentPanelLegacyRenderJournal(group, agent) {
  if (!group) {
    return '<div class="agent-panel-empty">No engineer configured for any group.</div>';
  }

  var html = '';
  var subview = _engineerJournalSubview(group);

  // Pending question banner
  var ws = _engineerGetSettings(group);
  if (ws && ws.pending_question) {
    html += '<div class="agent-panel-ask-banner">';
    html += '<div class="agent-panel-ask-label">Engineer is asking:</div>';
    html += '<div class="agent-panel-ask-question">' + _esc(ws.pending_question) + '</div>';
    html += '<textarea class="agent-panel-ask-reply" id="engineer-reply-input" '
         + 'placeholder="Type your reply..." rows="2" '
         + 'oninput="_engineerReplyDraft=this.value">' + _esc(_engineerReplyDraft) + '</textarea>';
    html += '<div class="agent-panel-ask-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="engineerDismissQuestion()">Dismiss</button>';
    html += '<button class="agent-panel-reply-btn" onclick="engineerReply()">Send Reply</button>';
    html += '</div>';
    html += '</div>';
  }
  if (ws && ws.pending_note) {
    var noteKind = ws.pending_note_kind || 'note';
    html += '<div class="agent-panel-note-banner">';
    html += '<div class="agent-panel-note-label">'
      + (noteKind === 'question'
        ? 'Engineer asks (non-blocking):'
        : 'Engineer note:')
      + '</div>';
    html += '<div class="agent-panel-note-text">' + _esc(ws.pending_note) + '</div>';
    html += '<div class="agent-panel-note-actions">';
    html += '<button class="agent-panel-dismiss-btn" onclick="engineerDismissNote()">Dismiss</button>';
    html += '</div>';
    html += '</div>';
  }

  html += _agentPanelLegacyRenderJournalToolbar(group, subview);
  if (subview === 'session_map') {
    html += _agentPanelLegacyRenderSessionMap(group);
    return html;
  }

  // Journal entries come from state.engineer_journal[author_cell_id]
  // (populated by snapshots/delta ops). The legacy group-key fallback only
  // renders rows stamped with the focused/configured engineer's id.
  // Render a "last 20 + Load older" window so long sessions don't explode the
  // DOM; the shared section pager in _agentPanelSectionPage grows the window on
  // top-insert so the anchor-restore helper keeps the user's scroll position.
  var authorId = _agentPanelEngineerJournalAuthorId(group, agent);
  var entries = _agentPanelEngineerJournalEntries(group, agent);
  if (entries.length) {
    var sorted = entries.slice().sort(function(a, b) {
      return (b.id || 0) - (a.id || 0);
    });
    var page = _agentPanelSectionPage(authorId || group, 'journal', sorted);
    html += _agentPanelLegacyRenderJournalEntries(page.events, true, true);
    html += _agentPanelRenderSectionLoadMore(page, { singular: 'entry', plural: 'entries' });
  } else {
    html += '<div class="agent-panel-empty">No journal entries yet.</div>';
  }
  return html;
}

function _agentPanelLegacyRenderJournalToolbar(group, subview) {
  var groupJs = JSON.stringify(String(group || ''));
  var html = '<div class="agent-panel-session-map-toolbar">';
  html += '<div class="agent-panel-session-map-actions">';
  if (subview === 'session_map') {
    html += '<button class="agent-panel-session-map-btn" onclick=\'engineerCloseSessionMap('
      + groupJs + ")\'>Back to Journal</button>";
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'engineerRefreshSessionMap('
      + groupJs + ")\'>Refresh</button>";
  } else {
    html += '<button class="agent-panel-session-map-btn primary" onclick=\'engineerOpenSessionMap('
      + groupJs + ")\'>Session Map</button>";
  }
  html += '</div>';
  var status = _engineerSessionMapStatus(group, subview);
  if (status) {
    html += '<div class="agent-panel-session-map-status">' + _esc(status) + '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerSessionMapStatus(group, subview) {
  var meta = _engineerSessionMapMeta(group);
  if (subview !== 'session_map') return '';
  if (meta.loading && _engineerSessionMapData(group)) {
    return 'Refreshing deterministic snapshot…';
  }
  if (meta.loading) return 'Loading deterministic snapshot…';
  if (meta.stale) return 'Snapshot is updating…';
  return '';
}

function _agentPanelLegacyRenderSessionMap(group) {
  var sessionMap = _engineerSessionMapData(group);
  var meta = _engineerSessionMapMeta(group);
  if (!sessionMap) {
    return '<div class="agent-panel-session-map-empty">'
      + _esc(meta.loading
        ? 'Loading Session Map…'
        : 'Open Session Map to load the current deterministic orchestration snapshot.')
      + '</div>';
  }

  var html = '<div class="agent-panel-session-map">';
  html += _agentPanelLegacyRenderSessionMapOverview(sessionMap.overview || {});
  html += _agentPanelLegacyRenderSessionMapHints(sessionMap.hints || {});
  html += _agentPanelLegacyRenderSessionMapStreams(sessionMap.streams || {});
  html += _agentPanelLegacyRenderSessionMapAsks('Pending asks', sessionMap.asks || {}, 'Ask pending');
  html += _agentPanelLegacyRenderSessionMapHumanGates(sessionMap.human_gates || {});
  html += _agentPanelLegacyRenderSessionMapTaskHealth(sessionMap.task_health || {});
  html += _agentPanelLegacyRenderSessionMapVerification(sessionMap.verification || {});
  html += _agentPanelLegacyRenderSessionMapBoundaries(sessionMap.branch_boundaries || {});
  html += _agentPanelLegacyRenderSessionMapAgents(sessionMap.agents || {});
  html += _agentPanelLegacyRenderSessionMapQueuedFollowUp(sessionMap.queued_follow_up || {});
  html += _agentPanelLegacyRenderSessionMapJournal(sessionMap.journal || {});
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapOverview(overview) {
  if (!overview || typeof overview !== 'object') return '';
  var stats = [
    { label: 'Tasks', value: overview.tasks_total || 0 },
    { label: 'Streams', value: overview.active_stream_count || 0 },
    { label: 'Active agents', value: overview.active_agent_count || 0 },
    { label: 'Asks', value: overview.pending_ask_count || 0 },
    { label: 'Human gates', value: overview.human_gate_count || 0 },
    { label: 'Queued follow-up', value: overview.queued_follow_up_count || 0 },
  ];
  var html = '<div class="agent-panel-session-map-overview">';
  for (var i = 0; i < stats.length; i++) {
    html += '<div class="agent-panel-session-map-stat">';
    html += '<div class="agent-panel-session-map-stat-value">' + _esc(String(stats[i].value)) + '</div>';
    html += '<div class="agent-panel-session-map-stat-label">' + _esc(stats[i].label) + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHints(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Hints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' current hint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">'
      + _esc(_engineerHumanizeToken(item.kind || 'hint')) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.message || '') + '</span>';
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapStreams(summary) {
  var streams = _engineerSummaryItems(summary);
  if (!streams.length) return '';
  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active streams</span>';
  html += '<span class="agent-panel-health-total">' + streams.length + ' active stream'
    + (streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAsks(title, summary, pillLabel) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">' + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.title || '') + '</span>';
    if (item.parent_task_id) {
      html += '<span class="agent-panel-session-map-item-meta">via ' + _esc(item.parent_task_id) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapHumanGates(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Human gates</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' waiting</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-warning">Validation gate</span>';
    html += '<span class="agent-panel-session-map-item-text">'
      + _esc(item.stream_title || item.branch || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapTaskHealth(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-health-item">';
    html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state || 'blocked') + '">'
      + _esc(_engineerHealthLabels[item.health_state] || item.health_state || 'Unhealthy') + '</span>';
    html += '<span class="agent-panel-health-item-title">' + _esc(item.title || '') + '</span>';
    if (item.via) {
      html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
    }
    html += '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapVerification(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' open checkpoint'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state || 'pending') + '">'
      + _esc(_engineerVerificationLabels[item.verification_state] || item.verification_state || 'Verification') + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title || '') + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapBoundaries(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">'
      + _esc(item.latest_boundary_task || item.stream_title || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(item));
    if (prHtml) {
      html += '<div class="agent-panel-verification-item-meta">PR: ' + prHtml + '</div>';
    }
    if (item.foreground_task_title) {
      html += '<div class="agent-panel-verification-item-meta">Current: '
        + _esc(item.foreground_task_title) + '</div>';
    }
    if (item.queued_followups && item.queued_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
    if (item.started_followups && item.started_followups.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followups.map(function(task) { return task.title; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapAgents(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Active agents</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' active</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(item.status || 'agent') + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.name || item.slug || item.id || '') + '</span>';
    if (item.current_task) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.current_task) + '</span>';
    }
    html += '</div>';
    if (item.activity_detail) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.activity_detail) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapQueuedFollowUp(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Queued follow-up</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' queued item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-list">';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pill = item.source === 'dispatch_queue' ? 'Dispatch queue' : _engineerHumanizeToken(item.queue_state || 'Stream queue');
    html += '<div class="agent-panel-session-map-list-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-notice">' + _esc(pill) + '</span>';
    html += '<span class="agent-panel-session-map-item-text">' + _esc(item.task_title || item.task_id || '') + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    } else if (item.target_agent_name) {
      html += '<span class="agent-panel-session-map-item-meta">' + _esc(item.target_agent_name) + '</span>';
    }
    html += '</div>';
    if (item.gate_reason) {
      html += '<div class="agent-panel-session-map-item-subtext">' + _esc(item.gate_reason) + '</div>';
    }
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderSessionMapJournal(summary) {
  var items = _engineerSummaryItems(summary);
  if (!items.length) return '';
  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Recent decisions, plans, and checkpoints</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += _agentPanelLegacyRenderJournalEntries(items, false);
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderJournalEntries(entries, allowContextMenu, alreadySorted) {
  var sorted = alreadySorted
    ? (entries || []).slice()
    : (entries || []).slice().sort(function(a, b) {
        return (b.id || 0) - (a.id || 0);
      });
  var html = '<div class="agent-panel-journal">';
  for (var i = 0; i < sorted.length; i++) {
    var e = sorted[i];
    var typeClass = 'agent-panel-badge-' + (e.type || 'observation');
    var ago = _engineerTimeAgo(e.timestamp);
    var anchorKey = 'journal-' + String(e.id || ('idx-' + i));
    html += '<div class="agent-panel-entry" data-engineer-anchor="' + _esc(anchorKey) + '"'
      + (allowContextMenu && e.id
        ? ' oncontextmenu="engineerEntryCtx(event,' + e.id + ')"'
        : '')
      + '>';
    html += '<div class="agent-panel-entry-header">';
    html += '<span class="agent-panel-badge ' + typeClass + '">' + _esc(e.type || '?') + '</span>';
    html += '<span class="agent-panel-entry-time">' + _esc(ago) + '</span>';
    html += '</div>';
    html += '<div class="agent-panel-entry-text">' + _esc(e.entry || '') + '</div>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _engineerSummaryItems(summary) {
  if (!summary || typeof summary !== 'object') return [];
  if (Array.isArray(summary.items)) return summary.items.slice();
  if (Array.isArray(summary.entries)) return summary.entries.slice();
  return [];
}

function _agentPanelLegacyRenderOpenStreams(group) {
  var summary = _engineerOpenStreamsSummary(group);
  if (!summary.show) return '';

  var html = '<div class="agent-panel-streams-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Open Streams</span>';
  html += '<span class="agent-panel-health-total">' + summary.streams.length + ' open stream'
    + (summary.streams.length === 1 ? '' : 's') + '</span>';
  html += '</div>';

  if (!summary.streams.length) {
    html += '<div class="agent-panel-stream-empty">No open streams.</div>';
    html += '</div>';
    return html;
  }

  html += '<div class="agent-panel-stream-list">';
  for (var i = 0; i < summary.streams.length; i++) {
    html += _agentPanelLegacyRenderOpenStreamCard(summary.streams[i], i);
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _engineerOpenStreamsSummary(group) {
  var result = { show: false, streams: [] };
  if (!group || !state) return result;
  var raw = _engineerRawStreamPayload(group);
  if (typeof raw === 'undefined') {
    return result;
  }
  result.show = true;
  var items = _engineerStreamItemsFromPayload(raw);
  for (var i = 0; i < items.length; i++) {
    if (_engineerStreamIsOpen(items[i])) result.streams.push(items[i]);
  }
  return result;
}

function _engineerRawStreamPayload(group) {
  if (!group || !state) return undefined;
  if (state.engineer_streams
      && Object.prototype.hasOwnProperty.call(state.engineer_streams, group)) {
    return state.engineer_streams[group];
  }
  if (state.engineer_board_summary
      && state.engineer_board_summary[group]
      && state.engineer_board_summary[group].streams) {
    return state.engineer_board_summary[group].streams;
  }
  return undefined;
}

function _engineerStreamItemsFromPayload(raw) {
  if (Array.isArray(raw)) return raw.slice();
  if (!raw || typeof raw !== 'object') return [];
  if (Array.isArray(raw.items)) return raw.items.slice();
  if (Array.isArray(raw.streams)) return raw.streams.slice();
  if (raw.streams && Array.isArray(raw.streams.items)) {
    return raw.streams.items.slice();
  }
  return [];
}

function _engineerStreamIsOpen(stream) {
  if (!stream) return false;
  if (stream.is_open === false) return false;
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  var stateName = String(_engineerStreamStateName(stream) || '').toLowerCase();
  return mergeState !== 'merged' && stateName !== 'merged';
}

function _agentPanelLegacyRenderOpenStreamCard(stream, index) {
  var title = _engineerStreamTitle(stream);
  var branch = _engineerShortBranchLabel(_engineerStreamBranch(stream));
  var stateMeta = _engineerStreamStateMeta(stream);
  var mergeMeta = _engineerStreamMergeMeta(stream);
  var gateReason = _engineerStreamGateReason(stream);
  var nextAction = _engineerStreamActionLabel(stream);
  var latestCommit = _engineerStreamLatestReviewedCommit(stream);
  var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(stream));
  var productTasks = _engineerStreamTaskItems(stream, 'product');
  var workflowTasks = _engineerStreamTaskItems(stream, 'workflow');
  var visibilityItems = _engineerStreamVisibilityItems(stream);
  var key = _engineerStreamAnchorKey(stream, index, title, branch);

  var html = '<div class="agent-panel-stream-card" data-engineer-anchor="' + _esc(key) + '">';
  html += '<div class="agent-panel-stream-card-header">';
  html += '<div class="agent-panel-stream-heading">';
  html += '<div class="agent-panel-stream-title-row">';
  html += '<span class="agent-panel-stream-title">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-state agent-panel-stream-state-'
    + _esc(stateMeta.className) + '">' + _esc(stateMeta.label) + '</span>';
  html += '</div>';
  if (branch && branch !== title) {
    html += '<div class="agent-panel-stream-branch">' + _esc(branch) + '</div>';
  }
  html += '</div>';
  if (mergeMeta.label) {
    html += '<span class="agent-panel-stream-merge agent-panel-stream-merge-' + _esc(mergeMeta.className)
      + '">' + _esc(mergeMeta.label) + '</span>';
  }
  html += '</div>';

  var metaHtml = '';
  if (latestCommit) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Reviewed', latestCommit);
  }
  if (prHtml) {
    metaHtml += _agentPanelLegacyRenderStreamMetaHtmlRow('PR', prHtml);
  }
  if (gateReason) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Gate', gateReason);
  }
  if (nextAction) {
    metaHtml += _agentPanelLegacyRenderStreamMetaRow('Next', nextAction);
  }
  if (metaHtml) {
    html += '<div class="agent-panel-stream-meta-list">';
    html += metaHtml;
    html += '</div>';
  }

  html += _agentPanelLegacyRenderMergeReadiness(stream);

  if (productTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Product tasks',
      'product',
      productTasks,
      false
    );
  }
  if (workflowTasks.length) {
    html += _agentPanelLegacyRenderStreamTaskGroup(
      'Workflow',
      'workflow',
      workflowTasks,
      true
    );
  }
  if (visibilityItems.length) {
    html += _agentPanelLegacyRenderStreamVisibilityGroup(visibilityItems);
  }

  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderMergeReadiness(stream) {
  var packet = _engineerStreamMergeReadinessPacket(stream);
  if (!packet) return '';
  var presentation = _engineerMergeReadinessPresentation(stream, packet);
  var className = _engineerClassSuffix(presentation.className || 'neutral');
  var html = '<div class="agent-panel-merge-readiness agent-panel-merge-readiness-' + _esc(className) + '">';
  html += '<div class="agent-panel-merge-readiness-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-merge">Merge readiness</span>';
  html += '<span class="agent-panel-merge-readiness-action agent-panel-merge-readiness-action-'
    + _esc(className) + '">' + _esc(presentation.statusLabel) + '</span>';
  html += '</div>';

  var fields = _engineerMergeReadinessFields(stream, packet);
  if (fields.length) {
    html += '<div class="agent-panel-merge-readiness-grid">';
    for (var i = 0; i < fields.length; i++) {
      html += '<div class="agent-panel-merge-readiness-label">' + _esc(fields[i].label) + '</div>';
      html += '<div class="agent-panel-merge-readiness-value">' + _esc(fields[i].value) + '</div>';
    }
    html += '</div>';
  }

  var followups = _engineerMergeReadinessFollowups(packet);
  if (followups.length) {
    html += '<div class="agent-panel-merge-readiness-followups">';
    for (var j = 0; j < followups.length; j++) {
      html += '<span class="agent-panel-merge-readiness-followup agent-panel-merge-readiness-followup-'
        + _esc(_engineerClassSuffix(followups[j].kind || 'note')) + '">'
        + _esc(followups[j].label) + '</span>';
    }
    html += '</div>';
  }

  var snippet = String(packet.merge_report_snippet || '').trim();
  if (snippet) {
    html += '<pre class="agent-panel-merge-readiness-snippet">' + _esc(snippet) + '</pre>';
  }
  html += '</div>';
  return html;
}

function _engineerStreamMergeReadinessPacket(stream) {
  if (!stream || typeof stream !== 'object') return null;
  var packet = stream.merge_readiness || stream.merge_readiness_packet || null;
  if (!packet || typeof packet !== 'object' || Array.isArray(packet)) return null;
  return packet;
}

function _engineerMergeReadinessPresentation(stream, packet) {
  var action = String((packet && packet.recommended_next_action) || (stream && stream.recommended_next_action) || '').toLowerCase();
  var streamState = String((packet && packet.state) || (stream && stream.state) || '').toLowerCase();
  var mergeState = String((packet && packet.merge_state) || (stream && stream.merge_state) || '').toLowerCase();
  var stale = !!(packet && packet.stale_base && packet.stale_base.stale === true);
  var branchAdvanced = _engineerMergeReadinessBranchAdvanced(stream, packet);
  var followups = (packet && packet.followups) || {};
  var hasBlocker = !!(followups.active_blocker_fix_task
    && (followups.active_blocker_fix_task.task_id || followups.active_blocker_fix_task.task_title));

  if (streamState === 'merged' || mergeState === 'merged'
      || (packet && packet.latest_merged_commit_sha)) {
    return { className: 'merged', statusLabel: 'Merged' };
  }
  if (stale || action === 'rebase') {
    return { className: 'stale_base', statusLabel: 'Stale base' };
  }
  if (hasBlocker || action === 'address_review_blockers' || action === 'fix_review_blocker') {
    return { className: 'blocker_fix', statusLabel: 'Blocker fix' };
  }
  if (branchAdvanced || action === 're-review' || action === 'review_latest_change') {
    return { className: 're_review', statusLabel: 'Re-review needed' };
  }
  if (streamState === 'ready_to_merge' || mergeState === 'ready'
      || action === 'merge_after_validation' || action === 'merge' || action === 'merge_stream') {
    return { className: 'ready_to_merge', statusLabel: 'Ready to merge' };
  }
  if (action === 'run_manual_validation' || streamState === 'awaiting_human_validation') {
    return { className: 'validation', statusLabel: 'Validation gate' };
  }
  if (action === 'wait_for_review' || streamState === 'reviewing') {
    return { className: 'reviewing', statusLabel: 'Awaiting review' };
  }
  return { className: 'neutral', statusLabel: 'Tracked' };
}

function _engineerMergeReadinessFields(stream, packet) {
  var fields = [];
  var action = _engineerStreamActionLabel(stream);
  if (action) fields.push({ label: 'Action', value: action });

  var boundary = (packet && packet.latest_reviewed_boundary) || {};
  var head = (packet && packet.head) || {};
  var reviewed = String(
    head.reviewed_boundary_sha
    || boundary.reviewed_sha
    || packet.latest_reviewed_commit_sha
    || ''
  ).trim();
  if (reviewed) fields.push({ label: 'Reviewed', value: _engineerShortSha(reviewed) });

  var branchHead = String(head.current_branch_head_sha || packet.branch_head || '').trim();
  if (branchHead) {
    var headValue = _engineerShortSha(branchHead);
    var headSource = String(head.current_branch_head_sha_source || '').trim();
    if (headSource && headSource !== 'unknown') {
      headValue += ' · ' + _engineerHumanizeToken(headSource);
    }
    fields.push({ label: 'Head', value: headValue });
  }

  var baseState = _engineerMergeReadinessBaseState(stream, packet);
  if (baseState) fields.push({ label: 'Base', value: baseState });

  var review = (packet && packet.review_final) || {};
  var verdict = String(review.verdict || '').trim();
  if (verdict) {
    var reviewValue = _engineerHumanizeToken(verdict);
    if (review.task_id) reviewValue += ' · ' + review.task_id;
    fields.push({ label: 'Review', value: reviewValue });
  }

  var verification = (packet && packet.verification) || {};
  var verificationState = String(
    verification.state || verification.verification_state || ''
  ).trim();
  if (verificationState) {
    var verificationValue = _engineerHumanizeToken(verificationState);
    var summary = verification.summary || {};
    if (summary.tests_run) verificationValue += ' · ' + summary.tests_run;
    else if (verification.verification_notes) verificationValue += ' · ' + verification.verification_notes;
    fields.push({ label: 'Verification', value: verificationValue });
  }

  var mergedSha = String((packet && packet.latest_merged_commit_sha) || '').trim();
  if (mergedSha) fields.push({ label: 'Merged', value: _engineerShortSha(mergedSha) });
  return fields;
}

function _engineerMergeReadinessBaseState(stream, packet) {
  var stale = (packet && packet.stale_base) || {};
  if (stale.stale === true || stale.state === 'stale') {
    var warning = String(stale.warning || stale.message || stale.merge_state || '').trim();
    return warning ? ('Stale · ' + warning) : 'Stale';
  }
  if (stale.stale === false || stale.state === 'fresh') return 'Fresh';
  if (_engineerMergeReadinessBranchAdvanced(stream, packet)) return 'Branch advanced';
  return '';
}

function _engineerMergeReadinessBranchAdvanced(stream, packet) {
  var head = (packet && packet.head) || {};
  return !!(
    (packet && packet.branch_advanced)
    || head.branch_advanced
    || (stream && stream.branch_advanced)
  );
}

function _engineerMergeReadinessFollowups(packet) {
  var followups = (packet && packet.followups) || {};
  var items = [];
  var blocker = followups.active_blocker_fix_task || {};
  if (blocker.task_id || blocker.task_title) {
    items.push({
      kind: 'blocking',
      label: 'Blocker fix: ' + (blocker.task_title || blocker.task_id),
    });
  }
  var parentReview = followups.blocker_parent_review_task || {};
  if (parentReview.task_id || parentReview.task_title) {
    items.push({
      kind: 'review',
      label: 'Review: ' + (parentReview.task_title || parentReview.task_id),
    });
  }
  var queuedCount = Number(followups.queued_count || 0);
  if (queuedCount > 0) {
    items.push({
      kind: 'queued',
      label: queuedCount + ' queued follow-up' + (queuedCount === 1 ? '' : 's'),
    });
  }
  var startedCount = Number(followups.started_count || 0);
  if (startedCount > 0) {
    items.push({
      kind: 'started',
      label: startedCount + ' started after boundary',
    });
  }
  var notes = followups.notes || {};
  ['blocking', 'non_blocking', 'future_context'].forEach(function(kind) {
    var values = Array.isArray(notes[kind]) ? notes[kind] : [];
    for (var i = 0; i < values.length && i < 2; i++) {
      var value = String(values[i] || '').trim();
      if (!value) continue;
      items.push({
        kind: kind,
        label: _engineerHumanizeToken(kind) + ': ' + value,
      });
    }
  });
  return items;
}

function _engineerShortSha(value) {
  var text = String(value || '').trim();
  if (text.length > 10) return text.slice(0, 7);
  return text;
}

function _agentPanelLegacyRenderStreamMetaRow(label, value) {
  return '<div class="agent-panel-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="agent-panel-stream-meta-value">' + _esc(value) + '</div>';
}

function _agentPanelLegacyRenderStreamMetaHtmlRow(label, htmlValue) {
  return '<div class="agent-panel-stream-meta-label">' + _esc(label) + '</div>'
    + '<div class="agent-panel-stream-meta-value">' + String(htmlValue || '') + '</div>';
}

function _agentPanelLegacyRenderStreamTaskGroup(title, kind, tasks, summarizeOnly) {
  if (!tasks.length) return '';
  var summary = tasks.length + ' ' + kind + ' task' + (tasks.length === 1 ? '' : 's');
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-' + _esc(kind)
    + '">' + _esc(title) + '</span>';
  html += '<span class="agent-panel-stream-summary-count">' + _esc(summary) + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-task-list">';
  var limit = summarizeOnly ? 2 : 3;
  for (var i = 0; i < tasks.length && i < limit; i++) {
    var item = tasks[i];
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-' + _esc(kind) + '">';
    html += _esc(item.title || item.id || '');
    html += '</span>';
  }
  if (tasks.length > limit) {
    html += '<span class="agent-panel-stream-task-chip agent-panel-stream-task-chip-more">+'
      + (tasks.length - limit) + ' more</span>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderStreamVisibilityGroup(items) {
  if (!items.length) return '';
  var html = '<div class="agent-panel-stream-task-group">';
  html += '<div class="agent-panel-stream-task-group-header">';
  html += '<span class="agent-panel-stream-section-label agent-panel-stream-section-label-context">'
    + 'Recent context</span>';
  html += '<span class="agent-panel-stream-summary-count">' + items.length + ' item'
    + (items.length === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-stream-visibility-list">';
  for (var i = 0; i < items.length && i < 2; i++) {
    var item = items[i];
    var kind = _engineerVisibilityKindLabel(item);
    html += '<div class="agent-panel-stream-visibility-item">';
    if (kind) {
      html += '<span class="agent-panel-stream-visibility-kind">' + _esc(kind) + '</span>';
    }
    html += '<span class="agent-panel-stream-visibility-text">' + _esc(item.summary) + '</span>';
    html += '</div>';
  }
  if (items.length > 2) {
    html += '<div class="agent-panel-stream-visibility-more">+' + (items.length - 2)
      + ' more context item' + (items.length - 2 === 1 ? '' : 's') + '</div>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _engineerStreamAnchorKey(stream, index, title, branch) {
  var parts = [
    stream && (stream.stream_id || stream.id || ''),
    stream && (stream.agent_id || ''),
    branch,
    title,
    String(index || 0),
  ].filter(function(part) { return !!part; });
  return 'stream-' + parts.join('-');
}

function _engineerStreamStateMeta(stream) {
  var name = _engineerStreamStateName(stream);
  var labels = {
    'implementing': 'Implementing',
    'reviewing': 'In review',
    'fixing_blockers': 'Fixing blockers',
    'awaiting_human_validation': 'Awaiting validation',
    'ready_to_merge': 'Ready to merge',
    'merged': 'Merged',
  };
  return {
    raw: name,
    label: labels[name] || _engineerHumanizeToken(name || 'implementing'),
    className: _engineerClassSuffix(name || 'implementing'),
  };
}

function _engineerStreamStateName(stream) {
  var stateName = String((stream && stream.state) || '').toLowerCase();
  var validationState = String((stream && stream.validation_state) || '').toLowerCase();
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  if (validationState === 'pending_human_validation') {
    return 'awaiting_human_validation';
  }
  if (stateName) return stateName;
  if (mergeState === 'ready') return 'ready_to_merge';
  return 'implementing';
}

function _engineerStreamMergeState(stream) {
  if (!stream) return '';
  if (stream.merge_state) return stream.merge_state;
  var packet = _engineerStreamMergeReadinessPacket(stream);
  if (packet) return packet.merge_state || '';
  if (typeof stream.merge_readiness === 'string') return stream.merge_readiness;
  return '';
}

function _engineerStreamMergeMeta(stream) {
  var mergeState = String(_engineerStreamMergeState(stream) || '').toLowerCase();
  var labels = {
    'ready': 'Ready to merge',
    'not_ready': 'Not ready to merge',
    'merged': 'Merged',
  };
  if (!mergeState && _engineerStreamStateName(stream) === 'ready_to_merge') {
    mergeState = 'ready';
  }
  return {
    raw: mergeState,
    label: labels[mergeState] || '',
    className: _engineerClassSuffix(mergeState || 'unknown'),
  };
}

function _engineerStreamGateReason(stream) {
  return String(
    (stream && (
      stream.gate_reason
      || (stream.queue_gate && stream.queue_gate.reason)
      || stream.gate
    )) || ''
  );
}

function _engineerStreamActionLabel(stream) {
  var packet = _engineerStreamMergeReadinessPacket(stream);
  var action = String(
    (packet && packet.recommended_next_action)
    || (stream && stream.recommended_next_action)
    || ''
  ).toLowerCase();
  var labels = {
    'continue_implementation': 'Continue implementation',
    'run_manual_validation': 'Run manual validation',
    'merge_after_validation': 'Merge after validation',
    'merge': 'Merge stream',
    'merge_stream': 'Merge stream',
    'address_review_blockers': 'Address review blockers',
    'resume_queued_work': 'Resume queued work',
    'resume_queued_task': 'Resume queued task',
    'review_latest_change': 'Review latest change',
    'wait_for_review': 'Wait for review',
    'fix_review_blocker': 'Fix review blocker',
    'rebase': 'Rebase stale base',
    're-review': 'Request re-review',
    'resolve_merge_conflict': 'Resolve merge conflict',
    'none': 'No merge action',
  };
  return labels[action] || _engineerHumanizeToken(action);
}

function _engineerStreamLatestReviewedCommit(stream) {
  var value = '';
  if (stream) {
    var packet = _engineerStreamMergeReadinessPacket(stream);
    var head = (packet && packet.head) || {};
    var boundary = (packet && packet.latest_reviewed_boundary) || {};
    if (head.reviewed_boundary_sha) value = String(head.reviewed_boundary_sha);
    else if (boundary.reviewed_sha) value = String(boundary.reviewed_sha);
    else if (stream.latest_reviewed_commit_sha) value = String(stream.latest_reviewed_commit_sha);
    else if (stream.latest_boundary_commit_sha) value = String(stream.latest_boundary_commit_sha);
    else if (stream.latest_reviewed_commit && stream.latest_reviewed_commit.sha) {
      value = String(stream.latest_reviewed_commit.sha);
    }
  }
  if (value.length > 10) return value.slice(0, 7);
  return value;
}

function _engineerStreamTaskItems(stream, kind) {
  var arrays = [];
  if (kind === 'product') {
    arrays = [
      stream && stream.product_tasks,
      stream && stream.related_product_tasks,
      stream && stream.product_task_ids,
    ];
  } else {
    arrays = [
      stream && stream.workflow_tasks,
      stream && stream.related_workflow_tasks,
      stream && stream.workflow_task_ids,
    ];
  }
  var raw = [];
  for (var i = 0; i < arrays.length; i++) {
    if (Array.isArray(arrays[i])) {
      raw = arrays[i];
      break;
    }
  }
  var items = [];
  var seen = {};
  for (var j = 0; j < raw.length; j++) {
    var item = _engineerNormalizeStreamTaskItem(raw[j]);
    if (!item.title && !item.id) continue;
    var key = item.id || item.title;
    if (seen[key]) continue;
    seen[key] = true;
    items.push(item);
  }
  return items;
}

function _engineerNormalizeStreamTaskItem(item) {
  if (typeof item === 'string' || typeof item === 'number') {
    var taskId = String(item);
    return _engineerStreamTaskFromId(taskId);
  }
  if (!item || typeof item !== 'object') return { id: '', title: '' };
  var id = item.id || item.task_id || '';
  var title = item.title || item.task || item.name || '';
  if (!title && id) {
    var resolved = _engineerStreamTaskFromId(id);
    title = resolved.title;
  }
  return {
    id: String(id || ''),
    title: String(title || ''),
  };
}

function _engineerStreamTaskFromId(taskId) {
  var task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  return {
    id: String(taskId || ''),
    title: String((task && (task.task || task.title)) || taskId || ''),
  };
}

function _engineerStreamVisibilityItems(stream) {
  var raw = [];
  if (stream) {
    if (Array.isArray(stream.visibility_items)) raw = stream.visibility_items;
    else if (Array.isArray(stream.recent_visibility_items)) raw = stream.recent_visibility_items;
  }
  var items = [];
  for (var i = 0; i < raw.length; i++) {
    var item = raw[i];
    if (typeof item === 'string') {
      items.push({ kind: '', status: '', summary: item });
      continue;
    }
    if (!item || typeof item !== 'object') continue;
    var summary = item.summary || item.message || item.entry || item.title || '';
    if (!summary) continue;
    items.push({
      kind: item.kind || item.type || '',
      status: item.status || item.state || '',
      summary: String(summary),
    });
  }
  return items;
}

function _engineerVisibilityKindLabel(item) {
  if (!item) return '';
  var status = String(item.status || '').toLowerCase();
  var kind = String(item.kind || '').toLowerCase();
  if (status) return _engineerHumanizeToken(status);
  if (kind) return _engineerHumanizeToken(kind);
  return 'Note';
}

function _engineerStreamTitle(stream) {
  var title = '';
  if (stream) {
    title = stream.short_label
      || stream.friendly_title
      || stream.foreground_task_title
      || stream.display_name
      || stream.label
      || stream.title
      || '';
  }
  if (!title) {
    var productTasks = _engineerStreamTaskItems(stream, 'product');
    if (productTasks.length) title = productTasks[0].title || '';
  }
  if (!title && stream && stream.latest_boundary_task_title) {
    title = stream.latest_boundary_task_title;
  }
  if (title) return String(title);
  return _engineerShortBranchLabel(_engineerStreamBranch(stream)) || 'Untitled stream';
}

function _engineerStreamBranch(stream) {
  return String((stream && (stream.branch || stream.worktree_branch || stream.stream_branch)) || '');
}

function _engineerShortBranchLabel(branch) {
  return String(branch || '').replace(/^torque\//, '');
}

function _engineerHumanizeToken(value) {
  var text = String(value || '').trim();
  if (!text) return '';
  text = text.replace(/[_-]+/g, ' ');
  return text.replace(/\b([a-z])/g, function(match, chr) {
    return chr.toUpperCase();
  });
}

function _engineerClassSuffix(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
}

function _agentPanelLegacyRenderTaskHealth(group) {
  var summary = _engineerTaskHealthSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Task health</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' unhealthy</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < _engineerHealthOrder.length; i++) {
    var stateName = _engineerHealthOrder[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_engineerHealthLabels[stateName]) + '</span>';
  }
  html += '</div>';
  if (summary.items.length) {
    html += '<div class="agent-panel-health-list">';
    for (var j = 0; j < summary.items.length; j++) {
      var item = summary.items[j];
      html += '<div class="agent-panel-health-item">';
      html += '<span class="agent-panel-health-item-state agent-panel-health-pill-' + _esc(item.health_state) + '">'
        + _esc(_engineerHealthLabels[item.health_state] || item.health_state) + '</span>';
      html += '<span class="agent-panel-health-item-title">' + _esc(item.title) + '</span>';
      if (item.via) {
        html += '<span class="agent-panel-health-item-via">via ' + _esc(item.via) + '</span>';
      }
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderVerificationSummary(group) {
  var summary = _engineerVerificationSummary(group);
  if (!summary.total) return '';

  var html = '<div class="agent-panel-verification-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Verification</span>';
  html += '<span class="agent-panel-health-total">' + summary.total + ' open checkpoint' + (summary.total === 1 ? '' : 's') + '</span>';
  html += '</div>';
  html += '<div class="agent-panel-health-counts">';
  for (var i = 0; i < summary.order.length; i++) {
    var stateName = summary.order[i];
    var count = summary.counts[stateName] || 0;
    if (!count) continue;
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(stateName) + '">'
      + count + ' ' + _esc(_engineerVerificationLabels[stateName] || stateName) + '</span>';
  }
  html += '</div>';
  for (var j = 0; j < summary.items.length; j++) {
    var item = summary.items[j];
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(item.verification_state) + '">'
      + _esc(_engineerVerificationLabels[item.verification_state] || item.verification_state) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.title) + '</span>';
    if (item.verification_mode) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.verification_mode) + '</span>';
    }
    html += '</div>';
    if (item.detail) {
      html += '<div class="agent-panel-verification-item-meta">' + _esc(item.detail) + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _agentPanelLegacyRenderBoundarySummary(group) {
  if (!group || !state || !state.agents) return '';
  var items = [];
  var seen = {};
  for (var agentId in state.agents) {
    var agent = state.agents[agentId];
    if (!agent || agent.cell_type !== 'agent' || agent.group !== group) continue;
    var settings = (state.group_settings || {})[group] || {};
    if (settings.engineer_agent_id === agent.id) continue;
    var overview = typeof _branchBoundaryOverviewForAgent === 'function'
      ? _branchBoundaryOverviewForAgent(agent)
      : null;
    if (!overview || !overview.latest_boundary_task) continue;
    var key = (overview.repo_root || '') + '::' + (overview.branch || '');
    if (seen[key]) continue;
    seen[key] = true;
    items.push({
      agent_name: agent.name,
      branch: overview.branch || '',
      current_task: overview.current_task ? overview.current_task.task : '',
      latest_boundary_task: overview.latest_boundary_task.task || '',
      pr: (typeof _worktreePrMetadataFromBoundary === 'function')
        ? _worktreePrMetadataFromBoundary(
            (typeof _taskBoundaryMeta === 'function')
              ? _taskBoundaryMeta(overview.latest_boundary_task)
              : ((overview.latest_boundary_task && overview.latest_boundary_task.worktree_boundary) || {})
          )
        : {},
      queued_followers: overview.queued_followers || [],
      started_followers: overview.started_followers || [],
      partial_review_safe: !!overview.partial_review_safe,
    });
  }
  items.sort(function(a, b) {
    return (a.branch || a.agent_name || '').localeCompare(
      b.branch || b.agent_name || ''
    );
  });
  if (!items.length) return '';

  var html = '<div class="agent-panel-health-summary">';
  html += '<div class="agent-panel-health-header">';
  html += '<span class="agent-panel-health-title">Branch review points</span>';
  html += '<span class="agent-panel-health-total">' + items.length + ' branch'
    + (items.length === 1 ? '' : 'es') + '</span>';
  html += '</div>';
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    var pillState = item.partial_review_safe ? 'passed' : 'failed';
    var pillLabel = item.partial_review_safe ? 'Safe for partial review' : 'Branch advanced';
    html += '<div class="agent-panel-verification-item">';
    html += '<span class="agent-panel-health-pill agent-panel-health-pill-' + _esc(pillState) + '">'
      + _esc(pillLabel) + '</span>';
    html += '<span class="agent-panel-verification-item-title">' + _esc(item.latest_boundary_task) + '</span>';
    if (item.branch) {
      html += '<span class="agent-panel-verification-item-meta">' + _esc(item.branch.replace(/^torque\//, '')) + '</span>';
    }
    html += '</div>';
    var prHtml = _agentPanelRenderPrValue(_agentPanelPrMetaFromSource(item));
    if (prHtml) {
      html += '<div class="agent-panel-verification-item-meta">PR: ' + prHtml + '</div>';
    }
    if (item.current_task) {
      html += '<div class="agent-panel-verification-item-meta">Current: ' + _esc(item.current_task) + '</div>';
    }
    if (item.queued_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Queued next: '
        + _esc(item.queued_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
    if (item.started_followers.length) {
      html += '<div class="agent-panel-verification-item-meta">Beyond boundary: '
        + _esc(item.started_followers.map(function(task) { return task.task; }).join(', '))
        + '</div>';
    }
  }
  html += '</div>';
  return html;
}

function _engineerTaskHealthSummary(group) {
  var summary = {
    counts: { 'blocked': 0, 'stalled': 0, 'thrashing': 0, 'idle-risk': 0 },
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var healthState = task.health_state || 'healthy';
    if (healthState === 'healthy') continue;
    summary.counts[healthState] = (summary.counts[healthState] || 0) + 1;
    summary.total += 1;
    var details = task.health_details || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      health_state: healthState,
      health_since: task.health_since || '',
      via: details.aggregate ? (details.source_task_title || '') : '',
    });
  }
  summary.items.sort(function(a, b) {
    var sev = (_engineerHealthSeverity[b.health_state] || 0) - (_engineerHealthSeverity[a.health_state] || 0);
    if (sev) return sev;
    var timeCmp = (a.health_since || '').localeCompare(b.health_since || '');
    if (timeCmp) return timeCmp;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

function _engineerVerificationSummary(group) {
  if (typeof _compactHydrateTasksMatching === 'function') {
    _compactHydrateTasksMatching(function(task) {
      if (!task || task.group !== group || task.lane === 'Done') return false;
      return !!(task.verification_state || task.verification_mode);
    });
  }
  var summary = {
    counts: { pending: 0, attempted: 0, passed: 0, failed: 0 },
    order: ['failed', 'pending', 'attempted', 'passed'],
    items: [],
    total: 0,
  };
  var tasks = (state && state.board_tasks) || {};
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group || task.lane === 'Done') continue;
    var verificationState = task.verification_state || '';
    if (!verificationState || !summary.counts.hasOwnProperty(verificationState)) continue;
    summary.counts[verificationState] += 1;
    summary.total += 1;
    var verificationSummary = task.verification_summary || {};
    summary.items.push({
      id: task.id,
      title: task.task || '',
      verification_state: verificationState,
      verification_mode: task.verification_mode || '',
      detail: verificationSummary.human_validation_pending
        || task.verification_notes
        || verificationSummary.tests_run
        || '',
    });
  }
  summary.items.sort(function(a, b) {
    var aRank = summary.order.indexOf(a.verification_state);
    var bRank = summary.order.indexOf(b.verification_state);
    if (aRank !== bRank) return aRank - bRank;
    return (a.title || '').localeCompare(b.title || '');
  });
  summary.items = summary.items.slice(0, 5);
  return summary;
}

// -- Journal context menu --------------------------------------------------

function engineerEntryCtx(e, entryId) {
  e.preventDefault();
  e.stopPropagation();
  showContextMenu(e.clientX, e.clientY, [
    { label: 'Delete entry', danger: true, action: 'engineerDeleteEntry(' + entryId + ')' },
  ]);
}

function engineerDeleteEntry(entryId) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var focused = _resolveFocusedAgent();
  var authorId = _agentPanelEngineerJournalAuthorId(group, focused);
  send({
    cmd: 'engineer_journal_delete',
    group: group,
    entry_id: entryId,
    author_cell_id: authorId,
  });
  // Optimistic removal from local state
  if (state.engineer_journal && authorId && state.engineer_journal[authorId]) {
    state.engineer_journal[authorId] = state.engineer_journal[authorId].filter(
      function(e) { return e.id !== entryId; });
  } else if (state.engineer_journal && state.engineer_journal[group]) {
    state.engineer_journal[group] = state.engineer_journal[group].filter(
      function(e) { return e.id !== entryId; });
  }
  _agentPanelRefreshVisibleSurface();
}

// -- Human reply -----------------------------------------------------------

function engineerReply() {
  var input = document.getElementById('engineer-reply-input');
  if (!input) return;
  var answer = input.value.trim();
  if (!answer) return;
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _engineerReplyDraft = '';
  // Blur so the re-render skip guard doesn't block the banner clearing
  input.blur();
  send({ cmd: 'engineer_reply', group: group, answer: answer });
}

function engineerDismissQuestion() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  _engineerReplyDraft = '';
  send({ cmd: 'engineer_resume', group: group });
}

function engineerDismissNote() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({ cmd: 'engineer_dismiss_note', group: group });
}

// -- Event handlers --------------------------------------------------------

function engineerInstrInput(textarea) {
  _engineerCustomInstrDirty = true;
  _engineerCustomInstrDraft = textarea.value;
  // Show save button (re-render just the settings section would be heavy;
  // instead just toggle the button visibility)
  var btn = textarea.parentElement.querySelector('.agent-panel-save-btn');
  if (!btn) {
    var b = document.createElement('button');
    b.className = 'agent-panel-save-btn';
    b.textContent = 'Save';
    b.onclick = engineerSaveInstructions;
    textarea.parentElement.appendChild(b);
  }
}

function engineerSaveInstructions() {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  send({
    cmd: 'engineer_update_settings',
    group: group,
    custom_instructions: _engineerCustomInstrDraft,
  });
  _engineerCustomInstrDirty = false;
  _engineerCustomInstrDraft = '';
}

function engineerUpdateSetting(key, value) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var payload = { cmd: 'engineer_update_settings', group: group };
  payload[key] = value;
  send(payload);
}

function engineerToggleEvent(evt, enabled) {
  var group = _agentPanelCurrentGroup();
  if (!group) return;
  var ws = _engineerGetSettings(group);
  var current = (ws && ws.enabled_events) ? ws.enabled_events.slice() : [];
  if (enabled && current.indexOf(evt) < 0) {
    current.push(evt);
  } else if (!enabled) {
    current = current.filter(function(e) { return e !== evt; });
  }
  send({
    cmd: 'engineer_update_settings',
    group: group,
    enabled_events: current,
  });
}

// -- Helpers ---------------------------------------------------------------

function _engineerGetSettings(group) {
  if (!group || !state.engineer_settings) return null;
  return state.engineer_settings[group] || null;
}

function _engineerPanelEmptyMessage(group, ws, engineer, bstats) {
  if (!group) return 'Select a group to inspect Engineer orchestration state.';
  if (_engineerHasPanelState(group, ws, engineer, bstats)) return '';
  return 'No Engineer configured for ' + group + ' yet.';
}

function _engineerHasPanelState(group, ws, engineer, bstats) {
  if (!group) return false;
  if (engineer) return true;
  if (_engineerGroupHasConfiguredAgent(group)) return true;
  if (_engineerGroupHasState(ws)) return true;
  if (_engineerGroupHasState(bstats)) return true;
  if (_engineerGroupStoreHasState(state.engineer_board_summary, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_buffer_stats, group)) return true;
  if (_engineerJournalStoreHasStateForGroup(state.engineer_journal, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_sent_events, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_session_maps, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_streams, group)) return true;
  if (_engineerGroupStoreHasState(state.engineer_worklog, group)) return true;
  return false;
}

function _engineerGroupHasConfiguredAgent(group) {
  if (!group || !state || !state.group_settings) return false;
  var settings = state.group_settings[group];
  return !!(settings && settings.engineer_agent_id);
}

function _engineerGroupStoreHasState(store, group) {
  if (!store || !group) return false;
  if (_engineerGroupHasState(store[group])) return true;
  var prefix = String(group || '') + '::';
  for (var key in store) {
    if (key.indexOf(prefix) === 0 && _engineerGroupHasState(store[key])) {
      return true;
    }
  }
  return _engineerGroupHasState(store[group]);
}

function _engineerJournalStoreHasStateForGroup(store, group) {
  if (!store || !group) return false;
  var configured = _agentPanelEngineerAgent(group);
  if (configured && configured.id && _engineerGroupHasState(store[configured.id])) {
    return true;
  }
  if (state && state.agents) {
    for (var aid in state.agents) {
      var agent = state.agents[aid];
      if (!agent || String(agent.group || '') !== String(group || '')) continue;
      if (String(agent.kind || '') !== 'engineer') continue;
      if (_engineerGroupHasState(store[aid])) return true;
    }
  }
  for (var key in store) {
    var entries = store[key];
    if (!Array.isArray(entries)) continue;
    for (var i = 0; i < entries.length; i++) {
      if (String((entries[i] && entries[i].group) || '') === String(group || '')) {
        return true;
      }
    }
  }
  return false;
}

function _engineerGroupHasState(value) {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return !!value;
}

function _agentPanelCurrentGroup() {
  if (typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && typeof _activeGroup === 'function') {
    return _activeGroup() || '';
  }
  if (typeof _resolveFocusedAgent === 'function') {
    var focusedAgent = _resolveFocusedAgent();
    if (focusedAgent && focusedAgent.group) return focusedAgent.group || '';
  }
  if (typeof _focusedGroup === 'function') {
    var focused = _focusedGroup();
    if (focused) return focused;
  }
  if (state && state.active_session_id && state.agents) {
    for (var agentId in state.agents) {
      var agent = state.agents[agentId];
      if (agent && agent.session_id === state.active_session_id) {
        return agent.group || '';
      }
    }
  }
  if (typeof _currentGroup === 'function') {
    var group = _currentGroup();
    if (group) return group;
  }
  if (state && state.groups) {
    var groups = Object.keys(state.groups || {});
    if (groups.length) return groups[0];
  }
  return '';
}

function _engineerGetAgent(group) {
  if (!group || !state.group_settings) return null;
  var gs = state.group_settings[group];
  if (!gs || !gs.engineer_agent_id) return null;
  return state.agents ? state.agents[gs.engineer_agent_id] : null;
}

function _engineerTimeAgo(ts) {
  if (!ts) return '';
  var diff = (Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
          .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

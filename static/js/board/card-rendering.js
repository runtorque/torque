/* ------------------------------------------------------------------ */
/* Board card rendering, badges, scheduling, and archive surfaces     */
/* ------------------------------------------------------------------ */

function _boardTaskTimestampMs(task) {
  if (!task) return 0;
  var iso = task.updated_at || task.created_at || '';
  if (!iso) return 0;
  var stamp = new Date(iso).getTime();
  return isNaN(stamp) ? 0 : stamp;
}

function _boardTaskScheduleMeta(task, nowMs) {
  if (!task || !task.scheduled_at) return null;
  var when = _boardTimestamp(task.scheduled_at);
  var formatted = _schedFormatTime(task.scheduled_at) || task.scheduled_at;
  if (Number.isNaN(when)) {
    return {
      className: 'board-card-scheduled',
      label: 'Due ' + formatted,
    };
  }
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffMs = when - current;
  if (task.lane !== 'Done' && diffMs < 0) {
    return {
      className: 'board-card-scheduled board-card-overdue',
      label: 'Overdue ' + formatted,
    };
  }
  if (task.lane !== 'Done' && diffMs <= 86400000) {
    return {
      className: 'board-card-scheduled board-card-due-soon',
      label: 'Due ' + formatted,
    };
  }
  return {
    className: 'board-card-scheduled',
    label: (task.lane === 'Done' ? 'Due ' : 'Scheduled ') + formatted,
  };
}

function _boardLaneEntryTimestampMs(task) {
  if (!task) return 0;
  var iso = task.lane_entered_at || task.created_at || '';
  if (!iso) return 0;
  var stamp = new Date(iso).getTime();
  return isNaN(stamp) ? 0 : stamp;
}

function _boardRelativeAgeText(diffSec) {
  if (diffSec < 60) return 'just now';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
  if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
  return Math.floor(diffSec / 86400) + 'd ago';
}

function _boardCreatedInCurrentLane(task, enteredAt) {
  if (!task) return false;
  if (!task.lane_entered_at) return !!task.created_at;
  if (!task.created_at || !enteredAt) return false;
  var createdAt = new Date(task.created_at).getTime();
  if (isNaN(createdAt)) return false;
  return Math.abs(createdAt - enteredAt) < 1000;
}

function _boardLaneEntryText(task, nowMs) {
  var enteredAt = _boardLaneEntryTimestampMs(task);
  if (!enteredAt) return '';
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffSec = Math.max(0, Math.floor((current - enteredAt) / 1000));
  var prefix = _boardCreatedInCurrentLane(task, enteredAt)
    ? 'Created'
    : 'Moved to ' + (task && task.lane ? task.lane : 'current lane');
  return prefix + ' ' + _boardRelativeAgeText(diffSec);
}

function _boardLaneEntryNextRefreshDelay(task, nowMs) {
  var enteredAt = _boardLaneEntryTimestampMs(task);
  if (!enteredAt) return 0;
  var current = typeof nowMs === 'number' ? nowMs : Date.now();
  var diffMs = Math.max(0, current - enteredAt);
  if (diffMs < 60000) return 1000;
  if (diffMs < 3600000) return 60000 - (diffMs % 60000) || 60000;
  if (diffMs < 86400000) return 3600000 - (diffMs % 3600000) || 3600000;
  return 86400000 - (diffMs % 86400000) || 86400000;
}

function _boardClearLaneEntryRefresh() {
  if (!_boardLaneEntryRefreshTimer) return;
  if (window && typeof window.clearTimeout === 'function') {
    window.clearTimeout(_boardLaneEntryRefreshTimer);
  }
  _boardLaneEntryRefreshTimer = 0;
}

function _boardScheduleLaneEntryRefresh(delayMs) {
  var boardVisible = typeof _panelAppVisible === 'function'
    ? _panelAppVisible('board')
    : (typeof _activePanelApp !== 'undefined'
      ? _activePanelApp === 'board'
      : (typeof _boardPanelVisible === 'function' ? _boardPanelVisible() : true));
  if (_boardShowSchedules
      || !boardVisible
      || !(delayMs > 0)) {
    _boardClearLaneEntryRefresh();
    return;
  }
  if (!window || typeof window.setTimeout !== 'function') return;
  if (_boardLaneEntryRefreshTimer && typeof window.clearTimeout === 'function') {
    window.clearTimeout(_boardLaneEntryRefreshTimer);
  }
  _boardLaneEntryRefreshTimer = window.setTimeout(function() {
    _boardLaneEntryRefreshTimer = 0;
    var boardStillVisible = typeof _panelAppVisible === 'function'
      ? _panelAppVisible('board')
      : (typeof _activePanelApp !== 'undefined'
        ? _activePanelApp === 'board'
        : (typeof _boardPanelVisible === 'function' ? _boardPanelVisible() : true));
    if (!boardStillVisible) return;
    renderBoard();
  }, Math.max(1000, delayMs));
}

function _boardVisibleLaneEntryRefreshDelay(rootTasks, childrenOf, renderLimit) {
  var nextDelay = 0;
  var remaining = Math.max(0, renderLimit || 0);

  function visit(task, depth) {
    if (!task || remaining <= 0) return;
    remaining--;
    var delay = _boardLaneEntryNextRefreshDelay(task);
    if (delay > 0 && (!nextDelay || delay < nextDelay)) nextDelay = delay;
    var children = childrenOf[task.id] || [];
    if (!children.length || _boardCollapsedTasks[task.id]) return;
    for (var i = 0; i < children.length; i++) {
      visit(children[i], depth + 1);
      if (remaining <= 0) break;
    }
  }

  for (var i = 0; i < rootTasks.length; i++) {
    visit(rootTasks[i], 0);
    if (remaining <= 0) break;
  }
  return nextDelay;
}

function _boardCardIndentLevel(depth) {
  var level = Number(depth) || 0;
  if (level < 1) return 0;
  return Math.min(level, 3);
}

function _boardGithubIssueNumber(task) {
  if (!task) return 0;
  var gh = typeof _boardTaskGithubSync === 'function' ? _boardTaskGithubSync(task) : {};
  var number = parseInt((gh && gh.issue_number) || 0, 10);
  if (number) return number;
  var externalId = String(task.external_id || '');
  var match = externalId.match(/#(\d+)$/);
  if (match) return parseInt(match[1], 10) || 0;
  var externalUrl = String(task.external_url || '');
  match = externalUrl.match(/\/issues\/(\d+)(?:[/?#].*)?$/);
  return match ? (parseInt(match[1], 10) || 0) : 0;
}

function _boardGithubIssueUrl(task) {
  if (!task) return '';
  var gh = typeof _boardTaskGithubSync === 'function' ? _boardTaskGithubSync(task) : {};
  return String((gh && gh.issue_url) || task.external_url || '').trim();
}

function _boardSyncStateMeta(task) {
  var sync = typeof _boardTaskSync === 'function'
    ? _boardTaskSync(task)
    : ((task && task.board_sync) || {});
  var stateName = String((sync && sync.sync_state) || '').toLowerCase();
  if (sync && sync.enabled === false) stateName = 'untracked';
  if (!stateName && sync && (sync.last_push_at || sync.last_pull_at || sync.last_synced_hash)) {
    stateName = 'ok';
  }
  if (stateName === 'idle') stateName = 'ok';
  var labels = {
    queued: 'queued',
    syncing: 'syncing',
    error: 'error',
    untracked: 'untracked',
    ok: 'ok',
  };
  var icons = {
    queued: '⏳',
    syncing: '↻',
    error: '!',
    untracked: '○',
    ok: '✓',
  };
  if (!labels[stateName]) stateName = '';
  var title = stateName ? ('GitHub sync ' + labels[stateName]) : 'GitHub sync not run';
  if (sync && sync.last_error) title += ': ' + sync.last_error;
  return {
    state: stateName || 'none',
    label: labels[stateName] || 'not synced',
    icon: icons[stateName] || '○',
    title: title,
  };
}

function _boardExternalGithubChipHtml(task) {
  if (!task) return '';
  var provider = String(task.provider || '').toLowerCase();
  var sync = typeof _boardTaskSync === 'function'
    ? _boardTaskSync(task)
    : ((task && task.board_sync) || {});
  var syncProvider = String((sync && sync.provider) || '').toLowerCase();
  var isGithub = provider === 'github' || syncProvider === 'github'
    || (typeof _boardTaskHasGithubLink === 'function' && _boardTaskHasGithubLink(task));
  if (!isGithub && !(task.provider || task.external_id)) return '';
  if (!isGithub) {
    var extLabel = (task.provider ? task.provider + ': ' : '') + (task.external_id || 'linked');
    return '<span class="board-card-label board-card-template" title="Linked external ticket">' + esc(extLabel) + '</span>';
  }
  var issueNumber = _boardGithubIssueNumber(task);
  var issueUrl = _boardGithubIssueUrl(task);
  var syncMeta = _boardSyncStateMeta(task);
  var label = issueNumber ? ('#' + issueNumber) : (task.external_id || 'GitHub');
  var title = 'GitHub issue';
  if (issueNumber) title += ' #' + issueNumber;
  title += ' · ' + syncMeta.title;
  var content = '<span class="board-card-github-icon" aria-hidden="true">🔗</span>'
    + '<span>' + esc(label) + '</span>'
    + '<span class="board-card-sync-state board-card-sync-state-' + esc(syncMeta.state)
    + '" title="' + esc(syncMeta.title) + '">' + esc(syncMeta.icon) + '</span>';
  if (issueUrl) {
    return '<a class="board-card-label board-card-external-chip board-card-github-chip" href="' + esc(issueUrl) + '"'
      + ' onclick="event.stopPropagation();window.open(this.href);return false"'
      + ' title="' + esc(title) + '">' + content + '</a>';
  }
  return '<span class="board-card-label board-card-external-chip board-card-github-chip" title="' + esc(title) + '">' + content + '</span>';
}

function _boardStaleDoneTaskIds(model) {
  var tasks = model && model.scopedWithoutArchived
    ? model.scopedWithoutArchived
    : _boardScopedTasks(false);
  var cutoff = Date.now() - (_boardArchiveStaleDays * 24 * 60 * 60 * 1000);
  var ids = [];
  for (var id in tasks) {
    var task = tasks[id];
    if (task.lane !== 'Done') continue;
    var stamp = _boardTaskTimestampMs(task);
    if (stamp && stamp <= cutoff) ids.push(id);
  }
  ids.sort(function(a, b) {
    return _boardTaskTimestampMs(tasks[a]) - _boardTaskTimestampMs(tasks[b]);
  });
  return ids;
}

function _boardTaskIsFutureScheduled(task) {
  if (!task || !task.scheduled_at) return false;
  var when = _boardTimestamp(task.scheduled_at);
  if (Number.isNaN(when)) return false;
  return when > Date.now();
}

function _boardCacheDispatchActionList(msg) {
  if (!msg || !msg.group) return;
  _boardEligibilityActionsByGroup[msg.group] = msg.actions || [];
  if (_boardEligibilityActionWaiting && msg.group === (_currentGroup() || '')) {
    _boardEligibilityActionWaiting = false;
  }
}

function _boardCacheDispatchTemplateList(msg) {
  if (!msg || !msg.group) return;
  _boardEligibilityTemplatesByGroup[msg.group] = msg.roles || msg.templates || [];
  if (_boardEligibilityTemplateWaiting
      && msg.group === (_currentGroup() || '')) {
    _boardEligibilityTemplateWaiting = false;
  }
}

function _boardHandleEligibilityActionList(msg) {
  _boardCacheDispatchActionList(msg);
  _boardEligibilityActionWaiting = false;
  renderBoard();
}

function _boardHandleEligibilityTemplateList(msg) {
  _boardCacheDispatchTemplateList(msg);
  _boardEligibilityTemplateWaiting = false;
  renderBoard();
}

function _boardEnsureDispatchEligibilityRefs(group, model) {
  if (!group || typeof send !== 'function') return;
  var tasks = model && model.scopedTasks
    ? model.scopedTasks
    : _boardScopedTasks(_boardShowArchived);
  var needsActions = false;
  var needsTemplates = false;
  for (var id in tasks) {
    var task = tasks[id];
    if (task.group !== group) continue;
    if (task.action_name) needsActions = true;
    if (task.agent_template) needsTemplates = true;
  }
  if (needsActions && !_boardEligibilityActionsByGroup[group]
      && !_boardEligibilityActionWaiting) {
    _boardEligibilityActionWaiting = true;
    send({ cmd: 'list_actions', group: group });
  }
  if (needsTemplates && !_boardEligibilityTemplatesByGroup[group]
      && !_boardEligibilityTemplateWaiting) {
    _boardEligibilityTemplateWaiting = true;
    send({ cmd: 'list_roles', group: group });
  }
}

function _boardUnmetDependencyNames(task) {
  var unmet = _boardUnmetDependencyTasks(task);
  var names = [];
  for (var i = 0; i < unmet.length; i++) {
    names.push(unmet[i].task || unmet[i].id);
  }
  return names;
}

function _boardTaskHasMissingAction(task) {
  if (!task || !task.action_name) return false;
  var group = task.group || _currentGroup() || '';
  var actions = _boardEligibilityActionsByGroup[group];
  if (!actions) return false;
  for (var i = 0; i < actions.length; i++) {
    if (actions[i] && actions[i].name === task.action_name) return false;
  }
  return true;
}

function _boardTaskHasMissingTemplate(task) {
  if (!task || !task.agent_template) return false;
  var group = task.group || _currentGroup() || '';
  var templates = _boardEligibilityTemplatesByGroup[group];
  if (!templates) return false;
  for (var i = 0; i < templates.length; i++) {
    if (templates[i] && templates[i].name === task.agent_template) return false;
  }
  return true;
}

function _boardTaskNeedsEligibilityRefs(task) {
  if (!task) return false;
  var group = task.group || _currentGroup() || '';
  return !!(
    (task.action_name && !_boardEligibilityActionsByGroup[group])
    || (task.agent_template && !_boardEligibilityTemplatesByGroup[group])
  );
}

function _boardTaskIsDispatchBlocked(task) {
  return _boardDepsBlocked(task)
    || !!(task.labels && task.labels.indexOf('torque:blocked') >= 0);
}

function _boardTaskDispatchEligibility(task) {
  if (!task) return null;
  if (task.lane === 'To Do' && task.agent_id) {
    return {
      className: 'board-card-dispatch board-card-dispatch-queued',
      label: 'Queued',
      title: 'Already queued for dispatch',
    };
  }
  if (task.lane !== 'Backlog') return null;
  if (_boardTaskNeedsEligibilityRefs(task)) {
    return {
      className: 'board-card-dispatch board-card-dispatch-checking',
      label: 'Checking refs',
      title: 'Loading action/role availability',
    };
  }
  var unmetDeps = _boardUnmetDependencyNames(task);
  if (unmetDeps.length) {
    var preview = unmetDeps.slice(0, 2).join(', ');
    if (unmetDeps.length > 2) preview += ', +' + (unmetDeps.length - 2);
    return {
      className: 'board-card-dispatch board-card-dispatch-blocked',
      label: _boardDependencyBlockedLabel(task),
      title: 'Waiting on: ' + preview,
    };
  }
  if (task.labels && task.labels.indexOf('torque:blocked') >= 0) {
    return {
      className: 'board-card-dispatch board-card-dispatch-blocked',
      label: 'Blocked',
      title: 'Task is explicitly blocked',
    };
  }
  if (_boardTaskIsFutureScheduled(task)) {
    return {
      className: 'board-card-dispatch board-card-dispatch-scheduled',
      label: 'Scheduled later',
      title: 'Dispatch window opens ' + _schedFormatTime(task.scheduled_at),
    };
  }
  var missingAction = _boardTaskHasMissingAction(task);
  var missingTemplate = _boardTaskHasMissingTemplate(task);
  if (missingAction || missingTemplate) {
    var missing = [];
    if (missingAction) missing.push('action');
    if (missingTemplate) missing.push('role');
    return {
      className: 'board-card-dispatch board-card-dispatch-warning',
      label: missing.length === 2 ? 'Missing refs' : 'Missing ' + missing[0],
      title: missingAction && missingTemplate
        ? 'Missing action "' + task.action_name + '" and role "' + task.agent_template + '"'
        : missingAction
          ? 'Missing action "' + task.action_name + '"'
          : 'Missing role "' + task.agent_template + '"',
    };
  }
  return null;
}

function _boardTaskHealthState(task) {
  if (!task || !task.health_state) return 'healthy';
  return task.health_state;
}

function _boardTaskHasDependencyBlockedReason(task) {
  var details = (task && task.health_details) || {};
  var reasons = Array.isArray(details.reasons) ? details.reasons : [];
  return reasons.indexOf('dependency_blocked') >= 0;
}

function _boardVerificationState(task) {
  if (!task || !task.verification_state) return '';
  return task.verification_state;
}

function _boardVerificationMode(task) {
  if (!task || !task.verification_mode) return '';
  return task.verification_mode;
}

function _boardVerificationSummary(task) {
  return (task && task.verification_summary) || {};
}

function _boardCompletionEvidence(task) {
  return (task && task.completion_evidence) || {};
}

function _boardCompletionEvidenceStatus(task) {
  var evidence = _boardCompletionEvidence(task);
  if (!evidence || !Object.keys(evidence).length) return '';
  return evidence.status || (evidence.verified ? 'verified' : 'evidence_attached');
}

function _boardCompletionEvidenceLabel(task) {
  var status = _boardCompletionEvidenceStatus(task);
  if (!status) return '';
  return status === 'verified' ? 'Verified' : 'Evidence';
}

function _boardCompletionEvidenceTitle(task) {
  var evidence = _boardCompletionEvidence(task);
  if (!evidence || !Object.keys(evidence).length) return '';
  var parts = [];
  if (evidence.sources && evidence.sources.length) {
    parts.push('Sources: ' + evidence.sources.join(', '));
  }
  var merge = evidence.merge || {};
  if (merge.sha) parts.push('Merge SHA: ' + merge.sha);
  if (merge.origin_summary) parts.push('Origin: ' + merge.origin_summary);
  else if (merge.origin_ref && merge.origin_sha) {
    parts.push('Origin: ' + merge.origin_ref + ' == ' + merge.origin_sha);
  }
  var verification = evidence.verification || {};
  var summary = verification.summary || {};
  if (summary.tests_run) parts.push('Tests: ' + summary.tests_run);
  var artifacts = evidence.artifacts || {};
  if (artifacts.count) {
    parts.push(artifacts.count + ' artifact' + (artifacts.count === 1 ? '' : 's'));
  }
  if (evidence.updated_by) parts.push('Updated by: ' + evidence.updated_by);
  return parts.join(' · ') || 'Completion evidence attached';
}

function _boardVerificationLabel(task) {
  var stateName = _boardVerificationState(task);
  if (!stateName) return '';
  if (stateName === 'pending') return 'Verify pending';
  if (stateName === 'attempted') return 'Verify attempted';
  if (stateName === 'passed') return 'Verified';
  if (stateName === 'failed') return 'Verify failed';
  return stateName;
}

function _boardVerificationPreview(task) {
  if (!task) return '';
  var summary = _boardVerificationSummary(task);
  if (summary.human_validation_pending) {
    return 'Needs human validation: ' + summary.human_validation_pending;
  }
  if (task.verification_notes) return task.verification_notes;
  if (summary.tests_run) return 'Tests: ' + summary.tests_run;
  return '';
}

function _boardHealthDisplayName(stateName) {
  return _boardHealthLabels[stateName] || stateName;
}

function _boardTaskHealthLabel(task) {
  var stateName = _boardTaskHealthState(task);
  if (stateName === 'blocked'
      && (_boardTaskHasDependencyBlockedReason(task) || _boardDepsBlocked(task))) {
    // Defensive fallback: dependency_blocked normally has at least one unmet
    // depends_on task, but stale/malformed state can lack a resolvable blocker.
    return _boardDependencyBlockedLabel(task) || _boardHealthDisplayName(stateName);
  }
  return _boardHealthDisplayName(stateName);
}

function _boardHealthTitle(task) {
  if (!task) return '';
  var stateName = _boardTaskHealthState(task);
  if (stateName === 'healthy') return '';
  var parts = [_boardTaskHealthLabel(task)];
  var details = task.health_details || {};
  if (details.aggregate && details.source_task_title) {
    parts.push('via ' + details.source_task_title);
  }
  if (details.silence_secs) {
    parts.push('no progress for ' + _boardFormatDuration(details.silence_secs));
  } else if (details.reasons && details.reasons.length) {
    var reasonText = _boardHealthReasonLabels[details.reasons[0]] || details.reasons[0];
    parts.push(reasonText);
  }
  if (task.health_since) {
    parts.push('since ' + new Date(task.health_since).toLocaleString());
  }
  return parts.join(' | ');
}

function _boardFormatDuration(seconds) {
  seconds = +seconds || 0;
  if (seconds <= 0) return '0m';
  var minutes = Math.max(1, Math.floor(seconds / 60));
  if (minutes < 60) return minutes + 'm';
  var hours = Math.floor(minutes / 60);
  var rem = minutes % 60;
  return rem ? (hours + 'h ' + rem + 'm') : (hours + 'h');
}

function _boardAgentStatus(agentId) {
  if (!agentId || !state || !state.agents) return '';
  var a = state.agents[agentId];
  if (!a) return '';
  return agentStatusClass ? agentStatusClass(a) : '';
}

function _boardAgentForId(agentId) {
  agentId = String(agentId || '').trim();
  if (!agentId || !state || !state.agents) return null;
  return state.agents[agentId] || null;
}

function _boardAgentName(agentId) {
  var a = _boardAgentForId(agentId);
  return a ? a.name : '';
}

function _boardAgentDisplayName(agentId) {
  var a = _boardAgentForId(agentId);
  if (!a) return '';
  return a.name || a.slug || a.id || '';
}

function _boardAssignedEngineerBadgeHtml(task) {
  var engineerId = String((task && task.assigned_engineer_id) || '').trim();
  if (!engineerId) return '';
  var engineer = _boardAgentForId(engineerId);
  var displayName = _boardAgentDisplayName(engineerId) || engineerId;
  var initial = String(displayName || '?').trim().charAt(0).toUpperCase() || '?';
  var status = engineer ? (_boardAgentStatus(engineerId) || 'idle') : 'missing';
  var cls = 'board-card-label board-card-assigned-engineer';
  if (!engineer) cls += ' board-card-assigned-engineer-missing';
  var attrs = ' data-assigned-engineer-id="' + esc(engineerId) + '"'
    + ' title="' + esc('Assigned engineer: ' + displayName) + '"';
  if (engineer) {
    attrs += ' onclick="event.stopPropagation();boardFocusAgent(\''
      + esc(engineerId).replace(/'/g, "\\'") + '\')"';
  }
  return '<span class="' + esc(cls) + '"' + attrs + '>'
    + '<span class="board-card-assigned-engineer-avatar '
      + esc(status) + '" aria-hidden="true">' + esc(initial) + '</span>'
    + '<span class="board-card-assigned-engineer-name">' + esc(displayName) + '</span>'
    + '</span>';
}

function _boardDepsBlocked(t) {
  if (!t.depends_on || !t.depends_on.length) return false;
  var tasks = _boardTasks();
  for (var i = 0; i < t.depends_on.length; i++) {
    var dep = tasks[t.depends_on[i]];
    if (dep && dep.lane !== 'Done') return true;
  }
  return false;
}

function _boardSortTaskRefs(tasks) {
  return tasks.slice().sort(function(a, b) {
    var laneCmp = String(a.lane || '').localeCompare(String(b.lane || ''));
    if (laneCmp) return laneCmp;
    var posCmp = (a.position || 0) - (b.position || 0);
    if (posCmp) return posCmp;
    return String(a.id || '').localeCompare(String(b.id || ''));
  });
}

function _boardDependencyTasks(task) {
  if (!task || !Array.isArray(task.depends_on)) return [];
  var tasks = _boardTasks();
  var deps = [];
  for (var i = 0; i < task.depends_on.length; i++) {
    var dep = tasks[task.depends_on[i]];
    if (dep) deps.push(dep);
  }
  return _boardSortTaskRefs(deps);
}

function _boardUnmetDependencyTasks(task) {
  if (!task || !Array.isArray(task.depends_on)) return [];
  var tasks = _boardTasks();
  var unmet = [];
  for (var i = 0; i < task.depends_on.length; i++) {
    var dep = tasks[task.depends_on[i]];
    if (dep && dep.lane !== 'Done') unmet.push(dep);
  }
  return unmet;
}

function _boardDependencyBlockedLabel(task) {
  var unmet = _boardUnmetDependencyTasks(task);
  if (!unmet.length) return '';
  var label = 'Blocked by ' + (unmet[0].id || unmet[0].task || 'dependency');
  if (unmet.length > 1) label += ' +' + (unmet.length - 1) + ' more';
  return label;
}

function _boardActiveDependentNames(task) {
  var dependents = _boardActiveDependents(task);
  var names = [];
  for (var i = 0; i < dependents.length; i++) {
    names.push(dependents[i].task || dependents[i].id);
  }
  return names;
}

function _boardActiveDependents(task) {
  if (!task || !task.id || task.lane === 'Done') return [];
  var tasks = _boardTasks();
  var dependents = [];
  for (var id in tasks) {
    var candidate = tasks[id];
    if (!candidate || candidate.lane === 'Done') continue;
    if (Array.isArray(candidate.depends_on)
        && candidate.depends_on.indexOf(task.id) >= 0) {
      dependents.push(candidate);
    }
  }
  return _boardSortTaskRefs(dependents);
}

function _boardTaskDependencyBadges(task) {
  if (!task) return [];
  var badges = [];
  var deps = Array.isArray(task.depends_on) ? task.depends_on : [];
  var unmetDepTasks = _boardUnmetDependencyTasks(task);
  var unmetDeps = _boardUnmetDependencyNames(task);
  if (deps.length) {
    if (unmetDepTasks.length) {
      var unmetPreview = unmetDeps.slice(0, 2).join(', ');
      if (unmetDeps.length > 2) unmetPreview += ', +' + (unmetDeps.length - 2);
      badges.push({
        className: 'board-card-dependency board-card-dependency-blocked',
        label: _boardDependencyBlockedLabel(task),
        title: 'Waiting on: ' + unmetPreview,
        targetTaskId: unmetDepTasks[0].id,
      });
    } else {
      badges.push({
        className: 'board-card-dependency board-card-dependency-ready',
        label: 'Deps ' + deps.length,
        title: deps.length + ' dependenc' + (deps.length === 1 ? 'y is' : 'ies are') + ' satisfied',
      });
    }
  }
  var activeDependents = _boardActiveDependentNames(task);
  if (activeDependents.length) {
    var blockingPreview = activeDependents.slice(0, 2).join(', ');
    if (activeDependents.length > 2) {
      blockingPreview += ', +' + (activeDependents.length - 2);
    }
    var activeDependentTasks = _boardActiveDependents(task);
    badges.push({
      className: 'board-card-dependency board-card-dependency-blocking',
      label: 'Blocks ' + activeDependents.length,
      title: 'Blocking: ' + blockingPreview,
      targetTaskId: activeDependentTasks[0].id,
    });
  }
  return badges;
}

function _boardTaskPriority(task) {
  var labels = (task && task.labels) || [];
  for (var i = 0; i < labels.length; i++) {
    var match = String(labels[i]).match(/^priority:(low|medium|high)$/);
    if (match) return match[1];
  }
  return '';
}

function _boardPriorityText(priority) {
  if (priority === 'high') return 'High';
  if (priority === 'medium') return 'Medium';
  if (priority === 'low') return 'Low';
  return '';
}

function _boardTaskUserLabels(task) {
  return ((task && task.labels) || []).filter(function(label) {
    return !isSystemLabel(label) && !/^priority:/.test(label);
  });
}

function _renderBoardQuickEdit(t) {
  var priority = _boardTaskPriority(t);
  var labels = _boardTaskUserLabels(t);
  var assignee = t.agent_id ? (_boardAgentName(t.agent_id) || 'Assigned') : 'Assignee';
  var dueLabel = t.scheduled_at ? ('Due ' + _schedFormatTime(t.scheduled_at)) : 'Due';
  var priorityLabel = priority ? ('Priority ' + _boardPriorityText(priority)) : 'Priority';
  var labelsLabel = labels.length ? ('Labels ' + labels.length) : 'Labels';
  var open = _boardQuickEditTask === t.id ? _boardQuickEditKind : '';
  var html = '<div class="board-card-quick-controls">';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'labels\')">' + esc(labelsLabel) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'assignee\')">' + esc(assignee) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'due\')">' + esc(dueLabel) + '</button>';
  html += '<button class="board-card-quick-btn" onclick="event.stopPropagation();_boardSetQuickEdit(\'' + t.id + '\',\'priority\')">' + esc(priorityLabel) + '</button>';
  html += '</div>';
  if (!open) return html;

  html += '<div class="board-card-quick-editor" onclick="event.stopPropagation()">';
  if (open === 'labels') {
    if (labels.length) {
      for (var i = 0; i < labels.length; i++) {
        var label = labels[i];
        html += '<span class="board-card-quick-chip">' + esc(label)
          + '<button class="board-card-quick-chip-remove" onclick="event.stopPropagation();boardQuickRemoveLabel(\'' + t.id + '\',\'' + esc(label).replace(/'/g, "\\'") + '\')">&times;</button></span>';
      }
    } else {
      html += '<span class="board-card-quick-empty">No labels</span>';
    }
    html += '<input type="text" id="board-quick-label-input-' + t.id + '" class="board-card-quick-input"'
      + ' placeholder="Add label" value="' + esc(_boardQuickLabelDraft) + '"'
      + ' oninput="boardQuickLabelInput(this.value)"'
      + ' onkeydown="boardQuickLabelKeydown(event,\'' + t.id + '\')">';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickAddLabel(\'' + t.id + '\')">Add</button>';
  } else if (open === 'assignee') {
    html += '<button class="board-card-quick-action' + (!t.agent_id ? ' active' : '') + '"'
      + ' onclick="event.stopPropagation();boardQuickAssignAgent(\'' + t.id + '\',\'\')">Unassigned</button>';
    var agents = _boardQuickEditAgents(t);
    if (!agents.length) {
      html += '<span class="board-card-quick-empty">No agents</span>';
    } else {
      for (var ai = 0; ai < agents.length; ai++) {
        var agent = agents[ai];
        html += '<button class="board-card-quick-action' + (agent.id === t.agent_id ? ' active' : '') + '"'
          + ' onclick="event.stopPropagation();boardQuickAssignAgent(\'' + t.id + '\',\'' + agent.id + '\')">' + esc(agent.name || agent.id) + '</button>';
      }
    }
  } else if (open === 'due') {
    html += '<input type="datetime-local" id="board-quick-due-input-' + t.id + '" class="board-card-quick-input"'
      + ' value="' + esc(_boardQuickDueDraft) + '"'
      + ' oninput="boardQuickDueInput(this.value)"'
      + ' onkeydown="boardQuickDueKeydown(event,\'' + t.id + '\')">';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickSaveDue(\'' + t.id + '\')">Set</button>';
    html += '<button class="board-card-quick-action" onclick="event.stopPropagation();boardQuickClearDue(\'' + t.id + '\')">Clear</button>';
  } else if (open === 'priority') {
    var levels = ['', 'low', 'medium', 'high'];
    for (var pi = 0; pi < levels.length; pi++) {
      var level = levels[pi];
      var text = level ? _boardPriorityText(level) : 'None';
      html += '<button class="board-card-quick-action' + (level === priority ? ' active' : '') + '"'
        + ' onclick="event.stopPropagation();boardQuickSetPriority(\'' + t.id + '\',\'' + level + '\')">' + esc(text) + '</button>';
    }
  }
  html += '</div>';
  return html;
}

function _renderBoardArchiveSuggestion(lane, model) {
  if (_boardShowArchived || (lane || _boardSelectedLane) !== 'Done') return '';
  var staleIds = _boardStaleDoneTaskIds(model);
  if (!staleIds.length) return '';
  var taskLabel = staleIds.length + ' completed task' + (staleIds.length === 1 ? '' : 's')
    + ' inactive for ' + _boardArchiveStaleDays + '+ days';
  var archiveLabel = 'Archive ' + taskLabel;
  return '<button type="button" class="board-archive-suggestion"'
    + ' onclick="event.stopPropagation();boardArchiveSuggestedDone()"'
    + ' title="' + esc(archiveLabel) + '">'
    + '<span class="board-archive-suggestion-copy">'
    + esc(archiveLabel) + '</span>'
    + '</button>';
}

/* ---- Card rendering ------------------------------------------------- */

function _renderBoardCard(t, childrenOf, depth, renderState) {
  var isSubordinate = depth > 0;
  var indentLevel = _boardCardIndentLevel(depth);
  var hasChildren = childrenOf[t.id] && childrenOf[t.id].length > 0;
  var showExecutionState = isSubordinate || !hasChildren;
  var isCollapsed = _boardCollapsedTasks[t.id];
  if (renderState) {
    if (renderState.remaining <= 0) {
      renderState.limitHit = true;
      return '';
    }
    if (renderState.skip && renderState.skip > 0) {
      renderState.skip--;
      var skippedChildHtml = '';
      if (hasChildren && !isCollapsed) {
        var skippedChildren = childrenOf[t.id];
        for (var si = 0; si < skippedChildren.length; si++) {
          if (renderState.remaining <= 0) {
            renderState.limitHit = true;
            break;
          }
          skippedChildHtml += _renderBoardCard(skippedChildren[si], childrenOf, depth + 1, renderState);
        }
      }
      return skippedChildHtml;
    }
    renderState.remaining--;
    renderState.rendered++;
  }
  var isDone = t.lane === 'Done';
  var dotClass = t.agent_id ? _boardAgentStatus(t.agent_id) : '';
  var focused = t.id === _boardFocusedTask ? ' focused' : '';
  var selected = _boardSelectedTasks[t.id] ? ' board-card-selected' : '';
  var hovered = t.id === _boardHoveredTask ? ' board-card-hovered' : '';
  var quickOpen = _boardQuickEditTask === t.id ? ' board-card-quick-open' : '';
  var subClass = isSubordinate ? ' board-card-subordinate' : '';
  var doneClass = (isSubordinate && isDone) ? ' board-card-done' : '';
  var indentAttrs = isSubordinate
    ? ' data-depth="' + depth + '"'
      + ' data-indent-level="' + indentLevel + '"'
      + ' style="--board-card-indent-level:' + indentLevel + '"'
    : '';
  var cardHtml = '<div class="board-card' + focused + selected + hovered + quickOpen + subClass + doneClass + '"'
    + ' data-task-id="' + t.id + '"'
    + indentAttrs
    + ' draggable="true"'
    + ' ondragstart="boardCardDragStart(event,\'' + t.id + '\')"'
    + ' ondragend="boardCardDragEnd(event)"'
    + ' ondragover="boardCardDragOver(event)"'
    + ' ondragleave="boardCardDragLeave(event)"'
    + ' ondrop="boardCardDrop(event)"'
    + ' onmouseenter="boardCardMouseEnter(\'' + t.id + '\')"'
    + ' onmouseleave="boardCardMouseLeave(\'' + t.id + '\')"'
    + ' onclick="boardFocusTask(\'' + t.id + '\', event)"'
    + ' oncontextmenu="boardCardMenu(event,\'' + t.id + '\')"'
    + ' ondblclick="openEditTask(\'' + t.id + '\')">';
  // Collapse toggle for cards with children
  if (hasChildren && !isSubordinate) {
    cardHtml += '<div class="board-card-collapse-btn" onclick="event.stopPropagation();boardToggleTaskCollapse(\'' + t.id + '\')">'
      + (isCollapsed ? '&#9654;' : '&#9660;') + '</div>';
  } else {
    cardHtml += '<div class="board-card-dot ' + dotClass + '"></div>';
  }
  cardHtml += '<div class="board-card-info">';
  if (t.id) {
    var copyTaskId = String(t.id || '').replace(/\\/g, "\\\\").replace(/'/g, "\\'");
    cardHtml += '<button type="button" class="board-card-id-copy"'
      + ' title="Copy task ID" aria-label="Copy task ID ' + esc(t.id) + '"'
      + ' onclick="event.stopPropagation();boardCopyTaskIdFromCard(\'' + copyTaskId + '\')">'
      + '&#x29C9;</button>';
  }
  // Done checkmark for subordinate cards
  var titlePrefix = (isSubordinate && isDone) ? '&#10003; ' : '';
  var laneEntryText = _boardLaneEntryText(t);
  var statusBarTitle = (t.id && laneEntryText)
    ? t.id + ' · ' + laneEntryText
    : (t.id || laneEntryText || '');
  cardHtml += '<div class="board-card-status-bar" title="' + esc(statusBarTitle) + '">';
  if (t.id) {
    cardHtml += '<span class="board-card-id">' + esc(t.id) + '</span>';
  }
  if (t.id && laneEntryText) {
    cardHtml += '<span class="board-card-status-separator" aria-hidden="true">·</span>';
  }
  if (laneEntryText) {
    cardHtml += '<span class="board-card-last-transition">' + esc(laneEntryText) + '</span>';
  }
  cardHtml += '</div>';
  cardHtml += '<div class="board-card-heading">';
  cardHtml += '<div class="board-card-title">' + titlePrefix + formatCode(t.task || '') + '</div>';
  cardHtml += '</div>';
  var meta = '';
  var dependencyBlockedLabel = _boardDependencyBlockedLabel(t);
  if (typeof _taskCreatedByBadgeHtml === 'function') {
    meta += _taskCreatedByBadgeHtml(t);
  }
  meta += _boardAssignedEngineerBadgeHtml(t);
  var dispatchEligibility = _boardTaskDispatchEligibility(t);
  if (dispatchEligibility && dispatchEligibility.label !== dependencyBlockedLabel) {
    meta += '<span class="board-card-label ' + esc(dispatchEligibility.className)
      + '" title="' + esc(dispatchEligibility.title) + '">'
      + esc(dispatchEligibility.label) + '</span>';
  }
  if (showExecutionState && t.status) meta += '<span class="board-card-label board-card-status">' + esc(t.status) + '</span>';
  var healthState = _boardTaskHealthState(t);
  var healthLabel = _boardTaskHealthLabel(t);
  if (showExecutionState && healthState !== 'healthy'
      && healthLabel !== dependencyBlockedLabel) {
    meta += '<span class="board-card-label board-card-health board-card-health-' + esc(healthState)
      + '" title="' + esc(_boardHealthTitle(t)) + '">' + esc(healthLabel) + '</span>';
  }
  if (_boardVerificationState(t)) {
    meta += '<span class="board-card-label board-card-verification board-card-verification-' + esc(_boardVerificationState(t))
      + '">' + esc(_boardVerificationLabel(t)) + '</span>';
  }
  if (_boardVerificationMode(t)) {
    meta += '<span class="board-card-label board-card-verification-mode">' + esc(_boardVerificationMode(t)) + '</span>';
  }
  if (_boardCompletionEvidenceStatus(t)) {
    var evidenceStatus = _boardCompletionEvidenceStatus(t);
    meta += '<span class="board-card-label board-card-completion-evidence board-card-completion-evidence-' + esc(evidenceStatus)
      + '" title="' + esc(_boardCompletionEvidenceTitle(t)) + '">'
      + esc(_boardCompletionEvidenceLabel(t)) + '</span>';
  }
  if (_boardHasActiveFilters() && t.lane) meta += '<span class="board-card-lane-badge">' + esc(t.lane) + '</span>';
  if (t.action_name) meta += '<span class="board-card-label board-card-template">' + esc(t.action_name) + '</span>';
  if (t.agent_template) meta += '<span class="board-card-label board-card-template">role: ' + esc(t.agent_template) + '</span>';
  if (t.labels && t.labels.length) {
    var userLbls = [], sysLbls = [];
    var priority = _boardTaskPriority(t);
    for (var li = 0; li < t.labels.length; li++) {
      if (isSystemLabel(t.labels[li])) sysLbls.push(t.labels[li]);
      else if (!/^priority:/.test(t.labels[li])) userLbls.push(t.labels[li]);
    }
    if (priority) {
      meta += '<span class="board-card-label board-card-priority board-card-priority-' + esc(priority) + '">'
        + 'Priority: ' + esc(_boardPriorityText(priority)) + '</span>';
    }
    for (var li = 0; li < userLbls.length; li++) {
      var lc = labelColor(userLbls[li]);
      meta += '<span class="board-card-label" style="background:' + lc + '22;color:' + lc + '">' + esc(userLbls[li]) + '</span>';
    }
    for (var li = 0; li < sysLbls.length; li++) {
      var lb = sysLbls[li];
      var cls = 'board-card-label board-label-system';
      if (lb === 'torque:blocked') cls += ' board-label-blocked';
      else if (lb === 'torque:error') cls += ' board-label-error';
      meta += '<span class="' + cls + '">' + esc(displayLabel(lb)) + '</span>';
    }
  }
  var dependencyBadges = _boardTaskDependencyBadges(t);
  for (var dbi = 0; dbi < dependencyBadges.length; dbi++) {
    var depBadge = dependencyBadges[dbi];
    var depClassName = depBadge.className;
    var depAttrs = '';
    if (depBadge.targetTaskId) {
      depClassName += ' board-card-badge-jump';
      depAttrs = ' onclick="event.stopPropagation();boardJumpToTask(\''
        + esc(depBadge.targetTaskId).replace(/'/g, "\\'") + '\')"';
    }
    meta += '<span class="board-card-label ' + esc(depClassName) + '"'
      + depAttrs + ' title="' + esc(depBadge.title) + '">' + esc(depBadge.label) + '</span>';
  }
  var scheduleMeta = _boardTaskScheduleMeta(t);
  if (scheduleMeta) {
    meta += '<span class="board-card-label ' + esc(scheduleMeta.className)
      + '" title="' + esc(scheduleMeta.label) + '">' + esc(scheduleMeta.label) + '</span>';
  }
  var artifactCount = typeof _artifactCountForTask === 'function'
    ? _artifactCountForTask(t)
    : (((t.attachments || []).length) + ((t.artifacts || []).length));
  if (artifactCount) {
    meta += '<span class="board-card-label board-card-attachments"'
      + ' title="' + artifactCount + ' artifact' + (artifactCount === 1 ? '' : 's') + '"'
      + ' onclick="event.stopPropagation();openTaskArtifactBrowser(\'' + t.id + '\')">'
      + '&#x1F4CE; ' + artifactCount + '</span>';
  }
  var externalChip = _boardExternalGithubChipHtml(t);
  if (externalChip) {
    meta += externalChip;
  }
  var syncForExternal = typeof _boardTaskSync === 'function'
    ? _boardTaskSync(t)
    : ((t && t.board_sync) || {});
  var externalIsGithub = String(t.provider || '').toLowerCase() === 'github'
    || String((syncForExternal && syncForExternal.provider) || '').toLowerCase() === 'github'
    || (typeof _boardTaskHasGithubLink === 'function' && _boardTaskHasGithubLink(t));
  if (t.external_url && !externalIsGithub) {
    meta += '<a class="board-card-pr-link" href="' + esc(t.external_url)
      + '" onclick="event.stopPropagation();window.open(this.href);return false"'
      + ' title="' + esc(t.external_url) + '">&#x1F517;</a>';
  }
  if (meta) cardHtml += '<div class="board-card-meta">' + meta + '</div>';
  if (typeof behaviorOverlayApprovalCardHtml === 'function') {
    cardHtml += behaviorOverlayApprovalCardHtml(t);
  }
  if (!isSubordinate) cardHtml += _renderBoardQuickEdit(t);
  // Pipeline chain indicator (only for subordinate cards)
  if (isSubordinate && t.parent_task_id) {
    var chainInfo = '↳ depth ' + (t.pipeline_depth || 0);
    if (t.labels && t.labels.indexOf('torque:human') >= 0) chainInfo += ' · awaiting human';
    cardHtml += '<div class="board-card-chain">' + chainInfo + '</div>';
  }
  if (t.agent_id) {
    var aName = _boardAgentName(t.agent_id);
    if (aName) {
      cardHtml += '<div class="board-card-agent" onclick="event.stopPropagation();boardFocusAgent(\'' + t.agent_id + '\')">'
        + '&#x1F916; ' + esc(aName) + '</div>';
    }
  }
  var verificationPreview = _boardVerificationPreview(t);
  if (verificationPreview) {
    cardHtml += '<div class="board-card-verification-note">' + esc(verificationPreview) + '</div>';
  }
  var boundaryNote = '';
  if (typeof _branchBoundaryOverviewForContext === 'function') {
    var boundary = t.worktree_boundary || {};
    if (boundary && boundary.status === 'open'
        && boundary.repo_root && boundary.branch) {
      var overview = _branchBoundaryOverviewForContext(
        boundary.repo_root,
        boundary.branch
      );
      if (overview && overview.latest_boundary_task
          && overview.latest_boundary_task.id === t.id) {
        if (overview.branch_advanced) {
          boundaryNote = 'Branch advanced beyond this review point';
        } else if (overview.queued_followers.length) {
          boundaryNote = 'Safe review point · '
            + overview.queued_followers.length + ' queued follow-up'
            + (overview.queued_followers.length === 1 ? '' : 's');
        } else {
          boundaryNote = 'Latest clean review point for this branch';
        }
      }
    } else if (t.resume_after_boundary_task_id) {
      var boundaryTask = state.board_tasks
        ? state.board_tasks[t.resume_after_boundary_task_id]
        : null;
      if (boundaryTask) {
        boundaryNote = (t.lane === 'Backlog' || t.lane === 'To Do')
          ? 'Queued after "' + boundaryTask.task + '"'
          : 'Started after "' + boundaryTask.task + '"';
      }
    }
  }
  if (boundaryNote) {
    cardHtml += '<div class="board-card-verification-note">' + esc(boundaryNote) + '</div>';
  }
  // Last activity message
  if (t.messages && t.messages.length) {
    var lastMsg = t.messages[t.messages.length - 1];
    var msgText = lastMsg.message || '';
    var msgClass = 'board-card-activity';
    if (lastMsg.action === 'done' || lastMsg.action === 'ready') msgClass += ' board-card-activity-done';
    else if (lastMsg.action === 'error') msgClass += ' board-card-activity-error';
    else if (lastMsg.action === 'blocked') msgClass += ' board-card-activity-blocked';
    cardHtml += '<div class="' + msgClass + ' board-card-activity-clickable" onclick="event.stopPropagation();showTaskMessages(\'' + t.id + '\')">' + esc(msgText) + '</div>';
  }
  cardHtml += '</div>';
  cardHtml += '<button class="board-card-menu-btn" onclick="event.stopPropagation();boardCardMenu(event,\'' + t.id + '\')" title="Actions">&#8942;</button>';
  cardHtml += '</div>';

  // Render children if expanded
  if (hasChildren && !isCollapsed) {
    var children = childrenOf[t.id];
    for (var ci = 0; ci < children.length; ci++) {
      if (renderState && renderState.remaining <= 0) {
        renderState.limitHit = true;
        break;
      }
      cardHtml += _renderBoardCard(children[ci], childrenOf, depth + 1, renderState);
    }
  }
  return cardHtml;
}

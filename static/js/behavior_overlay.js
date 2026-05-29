/* Dynamic Behavior overlay UI (agent Behavior tab + approval modal) */

var BEHAVIOR_OVERLAY_APPROVAL_LABEL = 'behavior-overlay-approval';
var _behaviorOverlayReadByAgent = {};
var _behaviorOverlayReadLoadingByAgent = {};
var _behaviorOverlayVersionsByAgent = {};
var _behaviorOverlayVersionsLoadingByAgent = {};
var _behaviorOverlayProposalListLoadingKey = '';
var _behaviorOverlayDiffByKey = {};
var _behaviorOverlayDiffLoadingByKey = {};
var _behaviorOverlayDrafts = {};
var _behaviorOverlaySelectedDiffKeyByAgent = {};
var _behaviorOverlayGovernanceTargetByArchitect = {};
var _behaviorOverlayApprovalModal = {
  open: false,
  taskId: '',
  proposalId: '',
  diffKey: '',
};

function _behaviorOverlayEsc(value) {
  value = String(value == null ? '' : value);
  if (typeof _agentPanelEsc === 'function') return _agentPanelEsc(value);
  if (typeof esc === 'function') return esc(value);
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _behaviorOverlayAttr(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _behaviorOverlayJs(value) {
  return JSON.stringify(String(value == null ? '' : value));
}

function _behaviorOverlayDomId(prefix, key) {
  return String(prefix || 'behavior-overlay') + '-'
    + String(key || '').replace(/[^A-Za-z0-9_-]/g, '-');
}

function behaviorOverlayNormalizeState() {
  if (typeof state === 'undefined' || !state) return;
  if (!state.behavior_overlay_active || typeof state.behavior_overlay_active !== 'object') {
    state.behavior_overlay_active = {};
  }
  if (!state.behavior_overlay_proposals || typeof state.behavior_overlay_proposals !== 'object') {
    state.behavior_overlay_proposals = {};
  }
  if (!state.behavior_overlay_versions || typeof state.behavior_overlay_versions !== 'object') {
    state.behavior_overlay_versions = {};
  }
}

function _behaviorOverlayAgent(agentId) {
  agentId = String(agentId || '').trim();
  return agentId && state && state.agents ? (state.agents[agentId] || null) : null;
}

function _behaviorOverlayKind(agent) {
  if (typeof _agentPanelKind === 'function') return _agentPanelKind(agent);
  return String((agent && agent.kind) || 'worker');
}

function _behaviorOverlayName(agentId) {
  var agent = _behaviorOverlayAgent(agentId);
  return agent ? (agent.name || agent.slug || agent.id || agentId) : agentId;
}

function _behaviorOverlayTimestamp(ts) {
  if (typeof _agentPanelTimestamp === 'function') return _agentPanelTimestamp(ts);
  var n = Number(ts || 0);
  if (!Number.isFinite(n) || n <= 0) return '—';
  try { return new Date(n * 1000).toLocaleString(); }
  catch (_e) { return String(ts || ''); }
}

function _behaviorOverlayShortHash(value) {
  value = String(value || '');
  return value ? value.slice(0, 10) : '—';
}

function _behaviorOverlayGroupSetting(agent) {
  var group = String((agent && agent.group) || '');
  var settings = state && state.group_settings ? (state.group_settings[group] || {}) : {};
  return !!settings.engineer_behavior_requires_user_approval;
}

function _behaviorOverlayActive(agentId) {
  behaviorOverlayNormalizeState();
  agentId = String(agentId || '').trim();
  var read = _behaviorOverlayReadByAgent[agentId] || {};
  return (state.behavior_overlay_active && state.behavior_overlay_active[agentId])
    || read.active
    || {};
}

function _behaviorOverlayActiveVersion(agentId) {
  var read = _behaviorOverlayReadByAgent[String(agentId || '')] || {};
  return read.version || {};
}

function _behaviorOverlayBaseVersionId(agentId) {
  var active = _behaviorOverlayActive(agentId);
  var version = _behaviorOverlayActiveVersion(agentId);
  return String(active.active_version_id || version.id || '');
}

function _behaviorOverlayVersionList(agentId) {
  behaviorOverlayNormalizeState();
  agentId = String(agentId || '').trim();
  if (_behaviorOverlayVersionsByAgent[agentId]) return _behaviorOverlayVersionsByAgent[agentId];
  var map = state.behavior_overlay_versions || {};
  var rows = map[agentId];
  return Array.isArray(rows) ? rows : [];
}

function _behaviorOverlayProposalMap() {
  behaviorOverlayNormalizeState();
  return state.behavior_overlay_proposals || {};
}

function _behaviorOverlayProposal(proposalId) {
  proposalId = String(proposalId || '').trim();
  if (!proposalId) return null;
  return _behaviorOverlayProposalMap()[proposalId] || null;
}

function _behaviorOverlayProposalValues() {
  var map = _behaviorOverlayProposalMap();
  var rows = [];
  for (var id in map) {
    if (Object.prototype.hasOwnProperty.call(map, id) && map[id]) rows.push(map[id]);
  }
  rows.sort(function(a, b) {
    return Number((b && b.updated_at) || (b && b.created_at) || 0)
      - Number((a && a.updated_at) || (a && a.created_at) || 0);
  });
  return rows;
}

function _behaviorOverlayOpenProposalsForAgent(agentId) {
  agentId = String(agentId || '').trim();
  return _behaviorOverlayProposalValues().filter(function(proposal) {
    var status = String((proposal && proposal.status) || '');
    return String((proposal && proposal.agent_id) || '') === agentId
      && (status === 'proposed' || status === 'approved');
  });
}

function _behaviorOverlayHiredEngineers(architect) {
  var architectId = String((architect && architect.id) || '');
  var group = String((architect && architect.group) || '');
  var rows = [];
  var agents = (state && state.agents) || {};
  for (var id in agents) {
    if (!Object.prototype.hasOwnProperty.call(agents, id)) continue;
    var agent = agents[id];
    if (!agent || String(agent.kind || '') !== 'engineer') continue;
    if (String(agent.hired_by_architect_id || '') !== architectId) continue;
    if (group && String(agent.group || '') !== group) continue;
    rows.push(agent);
  }
  rows.sort(function(a, b) {
    return String(a.name || a.id || '').localeCompare(String(b.name || b.id || ''));
  });
  return rows;
}

function _behaviorOverlayDraftKey(mode, targetAgentId, authorAgentId) {
  return [String(mode || 'own'), String(authorAgentId || ''), String(targetAgentId || '')].join(':');
}

function _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, seedText) {
  var key = _behaviorOverlayDraftKey(mode, targetAgentId, authorAgentId);
  if (!_behaviorOverlayDrafts[key]) {
    _behaviorOverlayDrafts[key] = {
      text: String(seedText || ''),
      rationale: '',
      dirty: false,
    };
  } else if (!_behaviorOverlayDrafts[key].dirty && seedText !== undefined) {
    _behaviorOverlayDrafts[key].text = String(seedText || '');
  }
  return _behaviorOverlayDrafts[key];
}

function behaviorOverlayDraftInput(mode, targetAgentId, authorAgentId, field, value) {
  var read = _behaviorOverlayReadByAgent[String(targetAgentId || '')] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  if (field === 'rationale') draft.rationale = String(value || '');
  else {
    draft.text = String(value || '');
    draft.dirty = true;
  }
}

function _behaviorOverlayRequestRead(agentId, seed, force) {
  agentId = String(agentId || '').trim();
  if (!agentId || typeof send !== 'function') return;
  if (!force && (_behaviorOverlayReadByAgent[agentId] || _behaviorOverlayReadLoadingByAgent[agentId])) return;
  _behaviorOverlayReadLoadingByAgent[agentId] = true;
  send({ cmd: 'behavior_overlay_read', agent_id: agentId, seed: seed !== false });
}

function _behaviorOverlayRequestVersions(agentId, force) {
  agentId = String(agentId || '').trim();
  if (!agentId || typeof send !== 'function') return;
  if (!force && (_behaviorOverlayVersionsByAgent[agentId] || _behaviorOverlayVersionsLoadingByAgent[agentId])) return;
  _behaviorOverlayVersionsLoadingByAgent[agentId] = true;
  send({ cmd: 'behavior_overlay_versions', agent_id: agentId, limit: 50 });
}

function _behaviorOverlayRequestProposals(force) {
  if (typeof send !== 'function') return;
  if (!force && _behaviorOverlayProposalListLoadingKey === 'open') return;
  _behaviorOverlayProposalListLoadingKey = 'open';
  send({ cmd: 'behavior_overlay_proposals', status_filter: '', limit: 200 });
}

function _behaviorOverlayDiffKeyForProposal(proposalId) {
  return 'proposal:' + String(proposalId || '');
}

function _behaviorOverlayDiffKeyForVersions(fromVersionId, toVersionId) {
  return 'versions:' + String(fromVersionId || '') + ':' + String(toVersionId || '');
}

function _behaviorOverlayRequestProposalDiff(proposalId, targetAgentId, force) {
  proposalId = String(proposalId || '').trim();
  if (!proposalId || typeof send !== 'function') return '';
  var key = _behaviorOverlayDiffKeyForProposal(proposalId);
  if (!force && (_behaviorOverlayDiffByKey[key] || _behaviorOverlayDiffLoadingByKey[key])) return key;
  _behaviorOverlayDiffLoadingByKey[key] = true;
  var msg = { cmd: 'behavior_overlay_diff', proposal_id: proposalId };
  if (targetAgentId) msg.agent_id = String(targetAgentId || '');
  send(msg);
  return key;
}

function behaviorOverlayViewProposalDiff(proposalId, targetAgentId) {
  var key = _behaviorOverlayRequestProposalDiff(proposalId, targetAgentId, false);
  if (targetAgentId) _behaviorOverlaySelectedDiffKeyByAgent[String(targetAgentId)] = key;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function behaviorOverlayDiffVersions(agentId, fromVersionId, toVersionId) {
  agentId = String(agentId || '').trim();
  fromVersionId = String(fromVersionId || '').trim();
  toVersionId = String(toVersionId || '').trim();
  if (!agentId || !fromVersionId || !toVersionId || typeof send !== 'function') return;
  var key = _behaviorOverlayDiffKeyForVersions(fromVersionId, toVersionId);
  _behaviorOverlaySelectedDiffKeyByAgent[agentId] = key;
  if (!_behaviorOverlayDiffByKey[key] && !_behaviorOverlayDiffLoadingByKey[key]) {
    _behaviorOverlayDiffLoadingByKey[key] = true;
    send({
      cmd: 'behavior_overlay_diff',
      agent_id: agentId,
      from_version_id: fromVersionId,
      to_version_id: toVersionId,
    });
  }
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function behaviorOverlayRefreshAgent(agentId) {
  _behaviorOverlayRequestRead(agentId, true, true);
  _behaviorOverlayRequestVersions(agentId, true);
  _behaviorOverlayRequestProposals(true);
}

function _behaviorOverlayDraftUnifiedDiff(fromText, toText, fromLabel, toLabel) {
  fromText = String(fromText || '');
  toText = String(toText || '');
  if (fromText === toText) return '';
  var a = fromText.split(/\r\n|\r|\n/);
  var b = toText.split(/\r\n|\r|\n/);
  var prefix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
  var suffix = 0;
  while (suffix + prefix < a.length
      && suffix + prefix < b.length
      && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
  var start = Math.max(0, prefix - 3);
  var endA = Math.min(a.length, a.length - suffix + 3);
  var endB = Math.min(b.length, b.length - suffix + 3);
  var lines = [
    '--- ' + (fromLabel || 'active'),
    '+++ ' + (toLabel || 'draft'),
    '@@ -' + (start + 1) + ',' + Math.max(0, endA - start)
      + ' +' + (start + 1) + ',' + Math.max(0, endB - start) + ' @@',
  ];
  for (var i = start; i < prefix; i++) lines.push(' ' + a[i]);
  for (var di = prefix; di < a.length - suffix; di++) lines.push('-' + a[di]);
  for (var ai = prefix; ai < b.length - suffix; ai++) lines.push('+' + b[ai]);
  for (var j = Math.max(prefix, a.length - suffix); j < endA; j++) lines.push(' ' + a[j]);
  return lines.join('\n') + '\n';
}

function behaviorOverlayPreviewDraft(mode, targetAgentId, authorAgentId) {
  targetAgentId = String(targetAgentId || '').trim();
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  var key = 'draft:' + _behaviorOverlayDraftKey(mode, targetAgentId, authorAgentId);
  _behaviorOverlayDiffByKey[key] = {
    type: 'behavior_overlay_diff',
    diff: _behaviorOverlayDraftUnifiedDiff(read.text || '', draft.text || '', 'active', 'draft'),
    draft: true,
  };
  _behaviorOverlaySelectedDiffKeyByAgent[targetAgentId] = key;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function behaviorOverlaySubmitDraft(mode, targetAgentId, authorAgentId, authorKind, directEdit) {
  targetAgentId = String(targetAgentId || '').trim();
  authorAgentId = String(authorAgentId || '').trim();
  authorKind = String(authorKind || '').trim() || 'user';
  if (!targetAgentId || !authorAgentId || typeof send !== 'function') return;
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  var text = String(draft.text || '');
  var baseVersionId = _behaviorOverlayBaseVersionId(targetAgentId);
  send({
    cmd: 'behavior_overlay_propose',
    agent_id: targetAgentId,
    proposed_by_agent_id: authorAgentId,
    proposed_by_kind: authorKind,
    text: text,
    rationale: String(draft.rationale || ''),
    proposal_type: 'set_text',
    expected_base_version_id: baseVersionId,
    architect_approver_id: directEdit ? authorAgentId : '',
    auto_apply_architect_direct: !!directEdit,
  });
  draft.dirty = false;
  if (typeof _showToast === 'function') _showToast('Behavior overlay proposal submitted', 'info');
}

function behaviorOverlayRequestRollback(targetAgentId, versionId, authorAgentId, authorKind, directEdit) {
  targetAgentId = String(targetAgentId || '').trim();
  versionId = String(versionId || '').trim();
  authorAgentId = String(authorAgentId || '').trim();
  authorKind = String(authorKind || '').trim() || 'user';
  if (!targetAgentId || !versionId || !authorAgentId || typeof send !== 'function') return;
  send({
    cmd: 'behavior_overlay_propose',
    agent_id: targetAgentId,
    proposed_by_agent_id: authorAgentId,
    proposed_by_kind: authorKind,
    proposal_type: 'rollback',
    target_version_id: versionId,
    expected_base_version_id: _behaviorOverlayBaseVersionId(targetAgentId),
    rationale: 'Rollback requested from Behavior tab',
    architect_approver_id: directEdit ? authorAgentId : '',
    auto_apply_architect_direct: !!directEdit,
  });
}

function behaviorOverlayArchitectApprove(proposalId, architectId) {
  var proposal = _behaviorOverlayProposal(proposalId) || {};
  if (!proposalId || !architectId || typeof send !== 'function') return;
  send({
    cmd: 'behavior_overlay_architect_approve',
    proposal_id: String(proposalId || ''),
    architect_id: String(architectId || ''),
    expected_proposed_text_sha256: String(proposal.proposed_text_sha256 || ''),
  });
}

function behaviorOverlayArchitectReject(proposalId) {
  if (!proposalId || typeof send !== 'function') return;
  send({ cmd: 'behavior_overlay_architect_reject', proposal_id: String(proposalId || '') });
}

function behaviorOverlayGovernanceSelect(architectId, targetAgentId) {
  _behaviorOverlayGovernanceTargetByArchitect[String(architectId || '')] = String(targetAgentId || '');
  if (targetAgentId) {
    _behaviorOverlayRequestRead(targetAgentId, true, false);
    _behaviorOverlayRequestVersions(targetAgentId, false);
  }
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _behaviorOverlayRenderedDiff(diffPayload) {
  if (!diffPayload) return '<div class="behavior-overlay-diff-placeholder">Select a diff to inspect.</div>';
  if (_behaviorOverlayDiffLoadingByKey[diffPayload]) {
    return '<div class="behavior-overlay-diff-placeholder">Loading diff…</div>';
  }
  var diffText = typeof diffPayload === 'string'
    ? String((_behaviorOverlayDiffByKey[diffPayload] || {}).diff || '')
    : String((diffPayload && diffPayload.diff) || '');
  return behaviorOverlayRenderUnifiedDiff(diffText);
}

function behaviorOverlayRenderUnifiedDiff(diffText) {
  diffText = String(diffText || '');
  if (!diffText.trim()) {
    return '<div class="diff-empty behavior-overlay-diff-empty">No behavior text changes.</div>';
  }
  var lines = diffText.replace(/\n$/, '').split('\n');
  var html = '<div class="behavior-overlay-diff diff-hunk-body" data-behavior-overlay-diff="1">';
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var cls = 'diff-line-context';
    var prefix = ' ';
    var text = line;
    if (line.indexOf('@@') === 0) {
      html += '<div class="diff-hunk-header behavior-overlay-diff-hunk">'
        + _behaviorOverlayEsc(line) + '</div>';
      continue;
    }
    if (line.indexOf('+++') === 0 || line.indexOf('---') === 0) {
      cls = 'diff-line-context behavior-overlay-diff-file';
      prefix = line.slice(0, 3);
      text = line.slice(3).replace(/^\s?/, '');
    } else if (line.charAt(0) === '+') {
      cls = 'diff-line-add';
      prefix = '+';
      text = line.slice(1);
    } else if (line.charAt(0) === '-') {
      cls = 'diff-line-del';
      prefix = '-';
      text = line.slice(1);
    } else if (line.charAt(0) === ' ') {
      text = line.slice(1);
    }
    html += '<div class="diff-line ' + cls + '"><span class="diff-line-prefix">'
      + _behaviorOverlayEsc(prefix) + '</span><span class="diff-line-text">'
      + _behaviorOverlayEsc(text) + '</span></div>';
  }
  html += '</div>';
  return html;
}

function _behaviorOverlayProposalStatusLabel(proposal) {
  var status = String((proposal && proposal.status) || 'proposed');
  var next = String((proposal && proposal.next_actor_kind) || '');
  if (next) return status + ' · awaiting ' + next;
  return status;
}

function _behaviorOverlayProposalCard(proposal, viewer, opts) {
  opts = opts || {};
  proposal = proposal || {};
  var proposalId = String(proposal.id || '');
  var targetAgentId = String(proposal.agent_id || '');
  var viewerKind = _behaviorOverlayKind(viewer);
  var viewerId = String((viewer && viewer.id) || '');
  var canArchitectAct = viewerKind === 'architect'
    && String(proposal.next_actor_kind || '') === 'architect';
  var html = '<div class="behavior-overlay-proposal-card" data-agent-panel-anchor="behavior-proposal-'
    + _behaviorOverlayAttr(proposalId) + '">';
  html += '<div class="behavior-overlay-card-head">';
  html += '<span class="detail-section-primary">'
    + _behaviorOverlayEsc(_behaviorOverlayName(targetAgentId)) + '</span>';
  html += '<span class="detail-task-status">'
    + _behaviorOverlayEsc(_behaviorOverlayProposalStatusLabel(proposal)) + '</span>';
  html += '</div>';
  html += '<div class="detail-section-card-meta behavior-overlay-meta-row">';
  html += '<span>' + _behaviorOverlayEsc(proposal.proposal_type || 'set_text') + '</span>';
  html += '<span>' + _behaviorOverlayEsc(proposal.approval_route || 'route') + '</span>';
  html += '<span>' + _behaviorOverlayEsc((proposal.proposed_text_bytes || 0) + ' bytes') + '</span>';
  html += '<span>sha ' + _behaviorOverlayEsc(_behaviorOverlayShortHash(proposal.proposed_text_sha256)) + '</span>';
  html += '</div>';
  if (proposal.rationale) {
    html += '<div class="detail-section-card-body">' + _behaviorOverlayEsc(proposal.rationale) + '</div>';
  }
  if (Number(proposal.lint_warning_count || 0) > 0) {
    html += '<div class="behavior-overlay-warning-inline">⚠ '
      + _behaviorOverlayEsc(proposal.lint_warning_count + ' advisory lint warning'
        + (Number(proposal.lint_warning_count) === 1 ? '' : 's')) + '</div>';
  }
  html += '<div class="detail-section-card-actions behavior-overlay-actions">';
  html += '<button type="button" class="btn-secondary btn-sm" onclick="behaviorOverlayViewProposalDiff('
    + _behaviorOverlayJs(proposalId) + ',' + _behaviorOverlayJs(targetAgentId) + ')">View diff</button>';
  if (canArchitectAct) {
    html += '<button type="button" class="btn-primary btn-sm" onclick="behaviorOverlayArchitectApprove('
      + _behaviorOverlayJs(proposalId) + ',' + _behaviorOverlayJs(viewerId) + ')">Approve</button>';
    html += '<button type="button" class="btn-cancel btn-sm" onclick="behaviorOverlayArchitectReject('
      + _behaviorOverlayJs(proposalId) + ')">Reject</button>';
  } else if (String(proposal.next_actor_kind || '') === 'user') {
    html += '<span class="behavior-overlay-awaiting">Awaiting user approval</span>';
  }
  html += '</div>';
  html += '</div>';
  return html;
}

function _behaviorOverlayProposalsSection(agent, proposals, title) {
  var html = '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">'
    + _behaviorOverlayEsc(title || 'Open proposals') + '</span><span class="detail-task-status">'
    + _behaviorOverlayEsc(String(proposals.length)) + '</span></div>';
  if (!proposals.length) {
    html += '<div class="agent-panel-empty">No open behavior proposals.</div>';
  } else {
    for (var i = 0; i < proposals.length; i++) {
      html += _behaviorOverlayProposalCard(proposals[i], agent);
    }
  }
  html += '</section>';
  return html;
}

function _behaviorOverlayTimeline(agent, targetAgentId, viewerId, viewerKind, directEdit) {
  var activeId = _behaviorOverlayBaseVersionId(targetAgentId);
  var versions = _behaviorOverlayVersionList(targetAgentId);
  var html = '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">Version timeline</span>';
  html += '<span class="detail-task-status">' + _behaviorOverlayEsc(String(versions.length)) + '</span></div>';
  if (_behaviorOverlayVersionsLoadingByAgent[targetAgentId] && !versions.length) {
    html += '<div class="agent-panel-empty">Loading versions…</div>';
  } else if (!versions.length) {
    html += '<div class="agent-panel-empty">No versions yet.</div>';
  } else {
    html += '<div class="behavior-overlay-timeline">';
    for (var i = 0; i < versions.length; i++) {
      var version = versions[i] || {};
      var vid = String(version.id || '');
      var isActive = activeId && vid === activeId;
      html += '<div class="behavior-overlay-version-row" data-agent-panel-anchor="behavior-version-'
        + _behaviorOverlayAttr(vid) + '">';
      html += '<div class="behavior-overlay-version-main">';
      html += '<span class="behavior-overlay-version-number">v'
        + _behaviorOverlayEsc(version.version_number || '?') + '</span>';
      html += '<span class="behavior-overlay-version-meta">'
        + _behaviorOverlayEsc(_behaviorOverlayTimestamp(version.created_at)) + '</span>';
      if (isActive) html += '<span class="detail-task-status">active</span>';
      html += '</div>';
      html += '<div class="behavior-overlay-version-sub">'
        + _behaviorOverlayEsc(version.rationale || 'No rationale') + '</div>';
      html += '<div class="behavior-overlay-version-actions">';
      if (!isActive && activeId) {
        html += '<button type="button" class="btn-secondary btn-sm" onclick="behaviorOverlayDiffVersions('
          + _behaviorOverlayJs(targetAgentId) + ',' + _behaviorOverlayJs(activeId) + ',' + _behaviorOverlayJs(vid) + ')">Diff active</button>';
        html += '<button type="button" class="btn-secondary btn-sm" onclick="behaviorOverlayRequestRollback('
          + _behaviorOverlayJs(targetAgentId) + ',' + _behaviorOverlayJs(vid) + ','
          + _behaviorOverlayJs(viewerId) + ',' + _behaviorOverlayJs(viewerKind) + ','
          + (directEdit ? 'true' : 'false') + ')">Request rollback</button>';
      }
      html += '<span class="behavior-overlay-sha">' + _behaviorOverlayEsc(_behaviorOverlayShortHash(version.text_sha256)) + '</span>';
      html += '</div></div>';
    }
    html += '</div>';
  }
  html += '</section>';
  return html;
}

function _behaviorOverlayEditor(agent, targetAgent, mode, authorAgent, directEdit) {
  var targetAgentId = String((targetAgent && targetAgent.id) || '');
  var authorId = String((authorAgent && authorAgent.id) || '');
  var authorKind = _behaviorOverlayKind(authorAgent);
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var loading = !!_behaviorOverlayReadLoadingByAgent[targetAgentId] && !read.version;
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorId, read.text || '');
  var key = _behaviorOverlayDraftKey(mode, targetAgentId, authorId);
  var textId = _behaviorOverlayDomId('behavior-overlay-text', key);
  var rationaleId = _behaviorOverlayDomId('behavior-overlay-rationale', key);
  var active = _behaviorOverlayActive(targetAgentId);
  var version = _behaviorOverlayActiveVersion(targetAgentId);
  var html = '<section class="detail-section-card behavior-overlay-section behavior-overlay-editor">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">';
  html += _behaviorOverlayEsc(mode === 'direct' ? 'Direct edit hired engineer overlay' : 'Current overlay');
  html += '</span><span class="detail-task-status">'
    + _behaviorOverlayEsc(loading ? 'loading' : ('v' + (version.version_number || '0'))) + '</span></div>';
  html += '<div class="behavior-overlay-summary-grid">';
  html += '<div><span class="detail-label">Target</span><span class="detail-val">'
    + _behaviorOverlayEsc((targetAgent && (targetAgent.name || targetAgent.id)) || targetAgentId) + '</span></div>';
  html += '<div><span class="detail-label">Active version</span><span class="detail-val">'
    + _behaviorOverlayEsc(active.active_version_id || version.id || '—') + '</span></div>';
  html += '<div><span class="detail-label">Text</span><span class="detail-val">'
    + _behaviorOverlayEsc((version.text_bytes != null ? version.text_bytes : (read.text || '').length) + ' bytes') + '</span></div>';
  html += '<div><span class="detail-label">Hash</span><span class="detail-val">'
    + _behaviorOverlayEsc(_behaviorOverlayShortHash(version.text_sha256)) + '</span></div>';
  html += '</div>';
  html += '<label for="' + _behaviorOverlayAttr(textId) + '">Proposed behavior text</label>';
  html += '<textarea id="' + _behaviorOverlayAttr(textId) + '" class="behavior-overlay-textarea" rows="8"'
    + ' placeholder="Additive, subordinate behavior guidance for this agent…"'
    + ' oninput="behaviorOverlayDraftInput(' + _behaviorOverlayJs(mode) + ','
    + _behaviorOverlayJs(targetAgentId) + ',' + _behaviorOverlayJs(authorId)
    + ',\'text\',this.value)">'
    + _behaviorOverlayEsc(draft.text || '') + '</textarea>';
  html += '<label for="' + _behaviorOverlayAttr(rationaleId) + '">Rationale</label>';
  html += '<input id="' + _behaviorOverlayAttr(rationaleId) + '" value="'
    + _behaviorOverlayAttr(draft.rationale || '') + '" placeholder="Why this behavior change?"'
    + ' oninput="behaviorOverlayDraftInput(' + _behaviorOverlayJs(mode) + ','
    + _behaviorOverlayJs(targetAgentId) + ',' + _behaviorOverlayJs(authorId)
    + ',\'rationale\',this.value)">';
  html += '<div class="behavior-overlay-editor-actions">';
  html += '<button type="button" class="btn-secondary" onclick="behaviorOverlayRefreshAgent('
    + _behaviorOverlayJs(targetAgentId) + ')">Refresh</button>';
  html += '<button type="button" class="btn-secondary" onclick="behaviorOverlayPreviewDraft('
    + _behaviorOverlayJs(mode) + ',' + _behaviorOverlayJs(targetAgentId) + ',' + _behaviorOverlayJs(authorId)
    + ')">Preview draft diff</button>';
  html += '<button type="button" class="btn-primary" onclick="behaviorOverlaySubmitDraft('
    + _behaviorOverlayJs(mode) + ',' + _behaviorOverlayJs(targetAgentId) + ','
    + _behaviorOverlayJs(authorId) + ',' + _behaviorOverlayJs(authorKind) + ','
    + (directEdit ? 'true' : 'false') + ')">Submit proposal</button>';
  html += '</div>';
  html += '</section>';
  return html;
}

function _behaviorOverlayDiffSection(agentId) {
  var key = _behaviorOverlaySelectedDiffKeyByAgent[String(agentId || '')] || '';
  if (!key) return '';
  var title = key.indexOf('draft:') === 0 ? 'Draft diff' : 'Selected diff';
  return '<section class="detail-section-card behavior-overlay-section behavior-overlay-diff-section">'
    + '<div class="detail-section-card-head"><span class="detail-section-primary">'
    + _behaviorOverlayEsc(title) + '</span></div>'
    + _behaviorOverlayRenderedDiff(key)
    + '</section>';
}

function _behaviorOverlayOwn(agent) {
  var agentId = String((agent && agent.id) || '');
  _behaviorOverlayRequestRead(agentId, true, false);
  _behaviorOverlayRequestVersions(agentId, false);
  _behaviorOverlayRequestProposals(false);
  var proposals = _behaviorOverlayOpenProposalsForAgent(agentId);
  var html = '';
  html += _behaviorOverlayEditor(agent, agent, 'own', agent, false);
  html += _behaviorOverlayDiffSection(agentId);
  html += _behaviorOverlayProposalsSection(agent, proposals, 'Open proposals for this agent');
  html += _behaviorOverlayTimeline(agent, agentId, agentId, _behaviorOverlayKind(agent), false);
  return html;
}

function _behaviorOverlayHiredGovernance(agent) {
  var hired = _behaviorOverlayHiredEngineers(agent);
  var architectId = String((agent && agent.id) || '');
  var selected = _behaviorOverlayGovernanceTargetByArchitect[architectId] || '';
  if (!selected && hired.length) selected = hired[0].id || '';
  if (selected) _behaviorOverlayGovernanceTargetByArchitect[architectId] = selected;
  var target = selected ? _behaviorOverlayAgent(selected) : null;
  if (target) {
    _behaviorOverlayRequestRead(selected, true, false);
    _behaviorOverlayRequestVersions(selected, false);
  }
  var hiredIds = {};
  for (var i = 0; i < hired.length; i++) hiredIds[String(hired[i].id || '')] = true;
  var proposals = _behaviorOverlayProposalValues().filter(function(proposal) {
    return hiredIds[String((proposal && proposal.agent_id) || '')];
  });

  var html = '<section class="detail-section-card behavior-overlay-section behavior-overlay-governance">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">Hired engineer governance</span>';
  html += '<span class="detail-task-status">' + _behaviorOverlayEsc(String(hired.length)) + '</span></div>';
  if (!hired.length) {
    html += '<div class="agent-panel-empty">No hired engineers for this architect.</div></section>';
    return html;
  }
  html += '<label>Target engineer</label><select class="behavior-overlay-target-select" onchange="behaviorOverlayGovernanceSelect('
    + _behaviorOverlayJs(architectId) + ',this.value)">';
  for (var h = 0; h < hired.length; h++) {
    var eng = hired[h];
    var engId = String(eng.id || '');
    html += '<option value="' + _behaviorOverlayAttr(engId) + '"'
      + (engId === selected ? ' selected' : '') + '>'
      + _behaviorOverlayEsc(eng.name || eng.id) + '</option>';
  }
  html += '</select>';
  if (target) {
    var requiresUser = _behaviorOverlayGroupSetting(target);
    html += '<div class="behavior-overlay-route-note">Route: architect'
      + (requiresUser ? ' → user approval required' : ' final approval') + '</div>';
  }
  html += '</section>';
  if (target) {
    html += _behaviorOverlayEditor(agent, target, 'direct', agent, true);
    html += _behaviorOverlayDiffSection(selected);
    html += _behaviorOverlayTimeline(agent, selected, architectId, 'architect', true);
  }
  html += _behaviorOverlayProposalsSection(agent, proposals, 'Open hired-engineer proposals');
  return html;
}

function renderBehaviorOverlayTab(agent) {
  behaviorOverlayNormalizeState();
  if (!agent) return '<div class="agent-panel-empty">Select an agent to inspect behavior overlays.</div>';
  var kind = _behaviorOverlayKind(agent);
  if (kind !== 'engineer' && kind !== 'architect') {
    return '<div class="agent-panel-empty">Behavior overlays are supported for engineers and architects.</div>';
  }
  var html = '<div class="behavior-overlay-tab" data-agent-panel-anchor="behavior-overlay-root">';
  html += '<div class="agent-panel-worklog-header behavior-overlay-header">';
  html += '<span class="agent-panel-worklog-title">Behavior overlay</span>';
  html += '<span class="agent-panel-worklog-note">Additive, governed prompt guidance; full text and diffs load on demand.</span>';
  html += '</div>';
  html += _behaviorOverlayOwn(agent);
  if (kind === 'architect') html += _behaviorOverlayHiredGovernance(agent);
  html += '</div>';
  return html;
}

function _behaviorOverlayRefreshPanelIfFocused(agentId) {
  agentId = String(agentId || '').trim();
  var focused = (typeof _focusedEngineerAgent === 'function')
    ? _focusedEngineerAgent()
    : (typeof _resolveFocusedAgent === 'function' ? _resolveFocusedAgent() : null);
  if (!focused) return;
  var kind = _behaviorOverlayKind(focused);
  if (typeof _agentPanelActiveTab === 'function'
      && _agentPanelActiveTab(kind) !== 'behavior') return;
  if (!behaviorOverlayDeltaInvalidatesFocusedPanel({ agent_id: agentId })) return;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _behaviorOverlayDiffKeyFromPayload(msg) {
  msg = msg || {};
  var proposalId = String(
    (msg.to_proposal && msg.to_proposal.id)
    || (msg.proposal && msg.proposal.id)
    || ''
  );
  if (proposalId) return _behaviorOverlayDiffKeyForProposal(proposalId);
  var fromId = String((msg.from_version && msg.from_version.id) || '');
  var toId = String((msg.to_version && msg.to_version.id) || '');
  if (fromId || toId) return _behaviorOverlayDiffKeyForVersions(fromId, toId);
  return '';
}

function _behaviorOverlayUpsertProposal(proposal) {
  if (!proposal || !proposal.id) return;
  behaviorOverlayNormalizeState();
  var status = String(proposal.status || '');
  if (status === 'rejected' || status === 'applied') {
    delete state.behavior_overlay_proposals[proposal.id];
  } else {
    state.behavior_overlay_proposals[proposal.id] = Object.assign(
      {},
      state.behavior_overlay_proposals[proposal.id] || {},
      proposal
    );
  }
}

function behaviorOverlayReceiveMessage(msg) {
  if (!msg || !msg.type) return false;
  behaviorOverlayNormalizeState();
  if (msg.type === 'behavior_overlay') {
    var agentId = String(msg.agent_id || '');
    if (agentId) {
      _behaviorOverlayReadLoadingByAgent[agentId] = false;
      _behaviorOverlayReadByAgent[agentId] = {
        active: msg.active || {},
        version: msg.version || {},
        text: String(msg.text || ''),
        received_at: Date.now ? Date.now() : 0,
      };
      if (msg.active) state.behavior_overlay_active[agentId] = msg.active;
      if (msg.version && msg.version.id) {
        var versions = _behaviorOverlayVersionList(agentId).slice();
        var found = false;
        for (var i = 0; i < versions.length; i++) {
          if (String(versions[i].id || '') === String(msg.version.id || '')) {
            versions[i] = msg.version;
            found = true;
            break;
          }
        }
        if (!found) versions.unshift(msg.version);
        _behaviorOverlayVersionsByAgent[agentId] = versions;
        state.behavior_overlay_versions[agentId] = versions;
      }
      _behaviorOverlayRefreshPanelIfFocused(agentId);
    }
    return true;
  }
  if (msg.type === 'behavior_overlay_versions') {
    var vidAgent = String(msg.agent_id || '');
    _behaviorOverlayVersionsLoadingByAgent[vidAgent] = false;
    _behaviorOverlayVersionsByAgent[vidAgent] = Array.isArray(msg.versions) ? msg.versions.slice() : [];
    state.behavior_overlay_versions[vidAgent] = _behaviorOverlayVersionsByAgent[vidAgent];
    _behaviorOverlayRefreshPanelIfFocused(vidAgent);
    return true;
  }
  if (msg.type === 'behavior_overlay_proposals') {
    _behaviorOverlayProposalListLoadingKey = '';
    var proposals = Array.isArray(msg.proposals) ? msg.proposals : [];
    for (var p = 0; p < proposals.length; p++) _behaviorOverlayUpsertProposal(proposals[p]);
    var focused = (typeof _focusedEngineerAgent === 'function') ? _focusedEngineerAgent() : null;
    if (focused) _behaviorOverlayRefreshPanelIfFocused(focused.id || '');
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return true;
  }
  if (msg.type === 'behavior_overlay_diff') {
    var key = _behaviorOverlayDiffKeyFromPayload(msg);
    if (key) {
      _behaviorOverlayDiffLoadingByKey[key] = false;
      _behaviorOverlayDiffByKey[key] = msg;
    }
    if (msg.proposal) _behaviorOverlayUpsertProposal(msg.proposal);
    if (msg.to_proposal) _behaviorOverlayUpsertProposal(msg.to_proposal);
    var affectedAgent = String(
      (msg.to_proposal && msg.to_proposal.agent_id)
      || (msg.proposal && msg.proposal.agent_id)
      || (msg.from_version && msg.from_version.agent_id)
      || (msg.to_version && msg.to_version.agent_id)
      || ''
    );
    _behaviorOverlayRefreshPanelIfFocused(affectedAgent);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return true;
  }
  if (msg.type === 'behavior_overlay_proposal') {
    if (msg.proposal) _behaviorOverlayUpsertProposal(msg.proposal);
    var proposal = msg.proposal || _behaviorOverlayProposal(msg.proposal_id) || {};
    _behaviorOverlayRefreshPanelIfFocused(proposal.agent_id || '');
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return true;
  }
  return false;
}

function behaviorOverlayApplyDelta(op) {
  if (!op || !op.op) return;
  behaviorOverlayNormalizeState();
  if (op.op === 'behavior_overlay_active_update') {
    var active = Object.assign({}, op);
    delete active.op;
    if (active.agent_id) state.behavior_overlay_active[active.agent_id] = active;
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return;
  }
  if (op.op === 'behavior_overlay_version_append') {
    var version = Object.assign({}, op);
    delete version.op;
    var agentId = String(version.agent_id || '');
    if (!agentId || !version.id) return;
    var versions = _behaviorOverlayVersionList(agentId).slice();
    var replaced = false;
    for (var i = 0; i < versions.length; i++) {
      if (String(versions[i].id || '') === String(version.id || '')) {
        versions[i] = Object.assign({}, versions[i], version);
        replaced = true;
        break;
      }
    }
    if (!replaced) versions.unshift(version);
    versions.sort(function(a, b) {
      return Number((b && b.version_number) || 0) - Number((a && a.version_number) || 0);
    });
    _behaviorOverlayVersionsByAgent[agentId] = versions;
    state.behavior_overlay_versions[agentId] = versions;
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return;
  }
  if (op.op === 'behavior_overlay_proposal_upsert'
      || op.op === 'behavior_overlay_proposal_resolve') {
    var proposal = Object.assign({}, op);
    delete proposal.op;
    _behaviorOverlayUpsertProposal(proposal);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return;
  }
}

function behaviorOverlayDeltaInvalidatesFocusedPanel(op) {
  op = op || {};
  var focused = (typeof _focusedEngineerAgent === 'function')
    ? _focusedEngineerAgent()
    : (typeof _resolveFocusedAgent === 'function' ? _resolveFocusedAgent() : null);
  if (!focused) return false;
  var focusedKind = _behaviorOverlayKind(focused);
  if (focusedKind !== 'engineer' && focusedKind !== 'architect') return false;
  if (typeof _agentPanelActiveTab === 'function'
      && _agentPanelActiveTab(focusedKind) !== 'behavior') return false;
  var agentId = String(op.agent_id || '');
  if (!agentId && op.id && state && state.behavior_overlay_proposals) {
    var cached = state.behavior_overlay_proposals[op.id];
    if (cached) agentId = String(cached.agent_id || '');
  }
  if (!agentId) return false;
  if (agentId === String(focused.id || '')) return true;
  if (focusedKind === 'architect') {
    var target = _behaviorOverlayAgent(agentId);
    return !!(target && String(target.hired_by_architect_id || '') === String(focused.id || ''));
  }
  return false;
}

function behaviorOverlayApprovalTask(task) {
  var labels = (task && Array.isArray(task.labels)) ? task.labels : [];
  return !!(
    task
    && labels.indexOf(BEHAVIOR_OVERLAY_APPROVAL_LABEL) >= 0
    && String(task.lane || '') !== 'Done'
  );
}

function behaviorOverlayProposalIdFromTask(task) {
  var labels = (task && Array.isArray(task.labels)) ? task.labels : [];
  for (var i = 0; i < labels.length; i++) {
    var text = String(labels[i] || '');
    if (text.indexOf('proposal:') === 0) return text.slice('proposal:'.length);
  }
  var desc = String((task && task.description) || '');
  var match = desc.match(/Proposal:\s*(\S+)/i);
  return match ? match[1] : '';
}

function behaviorOverlayApprovalCardHtml(task) {
  if (!behaviorOverlayApprovalTask(task)) return '';
  var proposalId = behaviorOverlayProposalIdFromTask(task);
  var proposal = _behaviorOverlayProposal(proposalId) || {};
  var target = _behaviorOverlayName(proposal.agent_id || '');
  var html = '<div class="behavior-overlay-approval-card" data-behavior-overlay-approval="1">';
  html += '<div class="behavior-overlay-approval-card-title">Behavior overlay approval</div>';
  html += '<div class="behavior-overlay-approval-card-body">Review the rendered diff before approving this governed behavior change.';
  if (target) html += ' Target: ' + _behaviorOverlayEsc(target) + '.';
  html += '</div>';
  html += '<button type="button" class="btn-primary btn-sm" onclick="event.stopPropagation();openBehaviorOverlayApprovalModal('
    + _behaviorOverlayJs((task && task.id) || '') + ')">Review behavior diff</button>';
  html += '</div>';
  return html;
}

function openBehaviorOverlayApprovalModal(taskId) {
  taskId = String(taskId || '').trim();
  var task = state && state.board_tasks ? state.board_tasks[taskId] : null;
  if (!task || !behaviorOverlayApprovalTask(task)) return;
  var proposalId = behaviorOverlayProposalIdFromTask(task);
  _behaviorOverlayApprovalModal = {
    open: true,
    taskId: taskId,
    proposalId: proposalId,
    diffKey: _behaviorOverlayDiffKeyForProposal(proposalId),
  };
  var modal = document.getElementById('modal-behavior-approval');
  if (modal) {
    if (typeof openNestedModal === 'function') openNestedModal('modal-behavior-approval');
    else modal.classList.add('visible');
  }
  _behaviorOverlayRequestProposals(false);
  _behaviorOverlayRequestProposalDiff(proposalId, '', true);
  renderBehaviorOverlayApprovalModal();
}

function closeBehaviorOverlayApprovalModal() {
  _behaviorOverlayApprovalModal = { open: false, taskId: '', proposalId: '', diffKey: '' };
  if (typeof closeNestedModal === 'function') {
    closeNestedModal('modal-behavior-approval');
    return;
  }
  var modal = document.getElementById('modal-behavior-approval');
  if (modal) modal.classList.remove('visible');
}

function _behaviorOverlayApprovalFullProposal() {
  var key = _behaviorOverlayApprovalModal.diffKey;
  var diff = key ? _behaviorOverlayDiffByKey[key] : null;
  return (diff && diff.proposal)
    || (diff && diff.to_proposal)
    || _behaviorOverlayProposal(_behaviorOverlayApprovalModal.proposalId)
    || {};
}

function _behaviorOverlayLintWarningsHtml(proposal) {
  var warnings = Array.isArray(proposal && proposal.lint_warnings)
    ? proposal.lint_warnings
    : [];
  if (!warnings.length && Number((proposal && proposal.lint_warning_count) || 0) > 0) {
    return '<div class="behavior-overlay-warning-inline">⚠ '
      + _behaviorOverlayEsc(proposal.lint_warning_count + ' advisory lint warning(s). Load the diff to inspect details.')
      + '</div>';
  }
  if (!warnings.length) return '<div class="behavior-overlay-no-warnings">No advisory lint warnings.</div>';
  var html = '<div class="behavior-overlay-warning-list">';
  for (var i = 0; i < warnings.length; i++) {
    var warning = warnings[i] || {};
    html += '<div class="behavior-overlay-warning-item">';
    html += '<strong>' + _behaviorOverlayEsc(warning.code || 'warning') + '</strong>';
    html += '<span>' + _behaviorOverlayEsc(warning.message || '') + '</span>';
    if (warning.excerpt) html += '<code>' + _behaviorOverlayEsc(warning.excerpt) + '</code>';
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function renderBehaviorOverlayApprovalModal() {
  var body = document.getElementById('behavior-approval-body');
  var approveBtn = document.getElementById('behavior-approval-approve-btn');
  var rejectBtn = document.getElementById('behavior-approval-reject-btn');
  if (!body) return;
  var task = state && state.board_tasks
    ? state.board_tasks[_behaviorOverlayApprovalModal.taskId]
    : null;
  var proposal = _behaviorOverlayApprovalFullProposal();
  var diffPayload = _behaviorOverlayDiffByKey[_behaviorOverlayApprovalModal.diffKey] || null;
  var diffReady = !!diffPayload;
  var targetAgent = _behaviorOverlayAgent(proposal.agent_id || '');
  var authorAgent = _behaviorOverlayAgent(proposal.proposed_by_agent_id || '');
  var html = '';
  html += '<div class="behavior-overlay-approval-summary">';
  html += '<div><span class="detail-label">Task</span><span class="detail-val">'
    + _behaviorOverlayEsc((task && (task.id + ' · ' + task.task)) || _behaviorOverlayApprovalModal.taskId) + '</span></div>';
  html += '<div><span class="detail-label">Target</span><span class="detail-val">'
    + _behaviorOverlayEsc((targetAgent && (targetAgent.name || targetAgent.id)) || proposal.agent_id || '—') + '</span></div>';
  html += '<div><span class="detail-label">Target kind</span><span class="detail-val">'
    + _behaviorOverlayEsc(proposal.target_kind || (targetAgent && targetAgent.kind) || '—') + '</span></div>';
  html += '<div><span class="detail-label">Author</span><span class="detail-val">'
    + _behaviorOverlayEsc((proposal.proposed_by_kind || 'agent') + ':'
      + ((authorAgent && (authorAgent.name || authorAgent.id)) || proposal.proposed_by_agent_id || '—')) + '</span></div>';
  html += '<div><span class="detail-label">Base</span><span class="detail-val">'
    + _behaviorOverlayEsc(proposal.base_version_id || '—') + '</span></div>';
  html += '<div><span class="detail-label">Proposed hash</span><span class="detail-val">'
    + _behaviorOverlayEsc(_behaviorOverlayShortHash(proposal.proposed_text_sha256)) + '</span></div>';
  html += '</div>';
  if (proposal.rationale) {
    html += '<div class="behavior-overlay-approval-rationale"><strong>Rationale</strong><p>'
      + _behaviorOverlayEsc(proposal.rationale) + '</p></div>';
  }
  html += '<div class="behavior-overlay-approval-warnings"><strong>Advisory lint</strong>'
    + _behaviorOverlayLintWarningsHtml(proposal) + '</div>';
  html += '<div class="behavior-overlay-approval-diff-wrap"><div class="behavior-overlay-approval-diff-title">Rendered unified diff</div>';
  if (!diffReady) {
    html += '<div class="behavior-overlay-diff-placeholder">Loading required diff… Approve/reject is disabled until this renders.</div>';
  } else {
    html += behaviorOverlayRenderUnifiedDiff(diffPayload.diff || '');
  }
  html += '</div>';
  body.innerHTML = html;
  if (approveBtn) approveBtn.disabled = !diffReady || !proposal.proposed_text_sha256;
  if (rejectBtn) rejectBtn.disabled = !diffReady;
}

function behaviorOverlayUserApprove() {
  var proposal = _behaviorOverlayApprovalFullProposal();
  var diffPayload = _behaviorOverlayDiffByKey[_behaviorOverlayApprovalModal.diffKey] || null;
  if (!diffPayload || !proposal.id || !proposal.proposed_text_sha256 || typeof send !== 'function') return;
  var noteEl = document.getElementById('behavior-approval-note');
  send({
    cmd: 'behavior_overlay_user_approve',
    proposal_id: String(proposal.id || ''),
    expected_proposed_text_sha256: String(proposal.proposed_text_sha256 || ''),
    expected_base_version_id: String(proposal.base_version_id || ''),
    note: noteEl ? String(noteEl.value || '') : '',
  });
  closeBehaviorOverlayApprovalModal();
}

function behaviorOverlayUserReject() {
  var proposal = _behaviorOverlayApprovalFullProposal();
  var diffPayload = _behaviorOverlayDiffByKey[_behaviorOverlayApprovalModal.diffKey] || null;
  if (!diffPayload || !proposal.id || typeof send !== 'function') return;
  var noteEl = document.getElementById('behavior-approval-note');
  send({
    cmd: 'behavior_overlay_user_reject',
    proposal_id: String(proposal.id || ''),
    expected_proposed_text_sha256: String(proposal.proposed_text_sha256 || ''),
    expected_base_version_id: String(proposal.base_version_id || ''),
    note: noteEl ? String(noteEl.value || '') : '',
  });
  closeBehaviorOverlayApprovalModal();
}

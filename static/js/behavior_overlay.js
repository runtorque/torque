/* Dynamic Behavior overlay UI (agent Behavior tab + approval modal) */

var BEHAVIOR_OVERLAY_APPROVAL_LABEL = 'behavior-overlay-approval';
var _behaviorOverlayReadByAgent = {};
var _behaviorOverlayReadLoadingByAgent = {};
var _behaviorOverlayVersionsByAgent = {};
var _behaviorOverlayVersionsLoadingByAgent = {};
var _behaviorOverlayProposalListLoadingKey = '';
var _behaviorOverlayProposalListLoaded = false;
var _behaviorOverlayDiffByKey = {};
var _behaviorOverlayDiffLoadingByKey = {};
var _behaviorOverlayDrafts = {};
var _behaviorOverlaySelectedDiffKeyByAgent = {};
var _behaviorOverlayGovernanceTargetByArchitect = {};
var _behaviorOverlayInnerTabByAgent = {};
var _behaviorOverlayApprovalModal = {
  open: false,
  taskId: '',
  proposalId: '',
  diffKey: '',
};
var _behaviorOverlayRoleKinds = ['engineer', 'architect', 'worker'];

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
  value = String(value == null ? '' : value);
  return "'" + value
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\x22')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029')
    .replace(/</g, '\\x3C')
    .replace(/>/g, '\\x3E')
    .replace(/&/g, '\\x26') + "'";
}

function _behaviorOverlayDomId(prefix, key) {
  return String(prefix || 'behavior-overlay') + '-'
    + String(key || '').replace(/[^A-Za-z0-9_-]/g, '-');
}

function _behaviorOverlayTitleCase(value) {
  value = String(value || '').trim();
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : '';
}

function _behaviorOverlayPluralRoleLabel(roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  if (roleKind === 'engineer') return 'engineers';
  if (roleKind === 'architect') return 'architects';
  if (roleKind === 'worker') return 'workers';
  return 'agents';
}

function _behaviorOverlayRoleScopeTitle(roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  return 'All ' + _behaviorOverlayPluralRoleLabel(roleKind) + ' (role)';
}

function _behaviorOverlayVersionLabel(versionNumber) {
  return 'v' + (versionNumber == null ? '?' : String(versionNumber));
}

function _behaviorOverlayNormalizeRoleKind(kind) {
  kind = String(kind || '').trim().toLowerCase();
  return _behaviorOverlayRoleKinds.indexOf(kind) >= 0 ? kind : '';
}

function _behaviorOverlayRoleScopeId(group, roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  group = String(group || '').trim();
  return roleKind && group ? ('role:' + group + ':' + roleKind) : '';
}

function _behaviorOverlayRoleScope(group, roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  group = String(group || '').trim();
  var scopeId = _behaviorOverlayRoleScopeId(group, roleKind);
  return scopeId ? {
    scope_kind: 'role',
    scope_group: group,
    scope_key: roleKind,
    role_kind: roleKind,
    agent_id: '',
    scope_id: scopeId,
  } : null;
}

function _behaviorOverlayScopeFromId(scopeId) {
  scopeId = String(scopeId || '').trim();
  if (scopeId.indexOf('role:') === 0) {
    var parts = scopeId.split(':');
    var roleKind = _behaviorOverlayNormalizeRoleKind(parts[parts.length - 1] || '');
    var group = parts.slice(1, parts.length - 1).join(':');
    return _behaviorOverlayRoleScope(group, roleKind);
  }
  if (!scopeId) return null;
  var agentId = scopeId.indexOf('agent:') === 0
    ? scopeId.slice('agent:'.length)
    : scopeId;
  return {
    scope_kind: 'agent',
    scope_group: '',
    scope_key: agentId,
    role_kind: '',
    agent_id: agentId,
    // Keep the legacy agent-id cache key for Phase-2 per-agent UI.
    scope_id: agentId,
  };
}

function _behaviorOverlayScopeFromPayload(payload, fallbackKey) {
  payload = payload || {};
  var scopeKind = String(payload.scope_kind || '').trim();
  var roleKind = _behaviorOverlayNormalizeRoleKind(
    payload.role_kind || payload.role || (
      scopeKind === 'role' ? (payload.scope_key || payload.target_kind || '') : ''
    )
  );
  var group = String(payload.scope_group || payload.group || '').trim();
  if (scopeKind === 'role' || roleKind) {
    return _behaviorOverlayRoleScope(group, roleKind);
  }
  var agentId = String(payload.agent_id || '').trim();
  if (agentId.indexOf('role:') === 0) return _behaviorOverlayScopeFromId(agentId);
  if (!agentId && payload.scope_id && String(payload.scope_id).indexOf('agent:') === 0) {
    agentId = String(payload.scope_id).slice('agent:'.length);
  }
  if (!agentId) agentId = String(payload.scope_key || fallbackKey || '').trim();
  if (agentId) return _behaviorOverlayScopeFromId(agentId);
  if (fallbackKey) return _behaviorOverlayScopeFromId(fallbackKey);
  return null;
}

function _behaviorOverlayScopeKey(value) {
  if (!value) return '';
  if (typeof value === 'string') {
    var fromId = _behaviorOverlayScopeFromId(value);
    return fromId ? fromId.scope_id : '';
  }
  var scope = _behaviorOverlayScopeFromPayload(value, '');
  return scope ? scope.scope_id : '';
}

function _behaviorOverlayScopeArgs(value) {
  var scope = typeof value === 'string'
    ? _behaviorOverlayScopeFromId(value)
    : _behaviorOverlayScopeFromPayload(value, '');
  if (!scope) return {};
  if (scope.scope_kind === 'role') {
    return {
      scope_kind: 'role',
      scope_group: scope.scope_group,
      scope_key: scope.scope_key,
      role_kind: scope.scope_key,
    };
  }
  return { agent_id: scope.agent_id || scope.scope_key || '' };
}

function _behaviorOverlayScopeLabel(value) {
  var scope = typeof value === 'string'
    ? _behaviorOverlayScopeFromId(value)
    : _behaviorOverlayScopeFromPayload(value, '');
  if (!scope) return '';
  if (scope.scope_kind === 'role') {
    return _behaviorOverlayTitleCase(scope.scope_key)
      + ' role overlay'
      + (scope.scope_group ? (' · ' + scope.scope_group) : '');
  }
  return _behaviorOverlayName(scope.agent_id || scope.scope_key || '');
}

function _behaviorOverlayProposalScopeKey(proposal) {
  return _behaviorOverlayScopeKey(proposal || {});
}

function _behaviorOverlayProposalLabel(proposal) {
  proposal = proposal || {};
  if (String(proposal.scope_kind || '') === 'role') {
    return _behaviorOverlayScopeLabel(proposal);
  }
  return _behaviorOverlayName(proposal.agent_id || proposal.scope_key || '');
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
  if (String(agentId || '').indexOf('role:') === 0) {
    return _behaviorOverlayScopeLabel(agentId);
  }
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
  agentId = _behaviorOverlayScopeKey(agentId);
  var read = _behaviorOverlayReadByAgent[agentId] || {};
  return (state.behavior_overlay_active && state.behavior_overlay_active[agentId])
    || read.active
    || {};
}

function _behaviorOverlayActiveVersion(agentId) {
  agentId = _behaviorOverlayScopeKey(agentId);
  var read = _behaviorOverlayReadByAgent[agentId] || {};
  return read.version || {};
}

function _behaviorOverlayBaseVersionId(agentId) {
  var active = _behaviorOverlayActive(agentId);
  var version = _behaviorOverlayActiveVersion(agentId);
  return String(active.active_version_id || version.id || '');
}

function _behaviorOverlayReadVersionId(agentId) {
  agentId = _behaviorOverlayScopeKey(agentId);
  var read = _behaviorOverlayReadByAgent[agentId] || {};
  return String((read.version && read.version.id) || '');
}

function _behaviorOverlayReadIsFresh(agentId) {
  agentId = _behaviorOverlayScopeKey(agentId);
  var readVersionId = _behaviorOverlayReadVersionId(agentId);
  if (!readVersionId) return false;
  var baseVersionId = _behaviorOverlayBaseVersionId(agentId);
  return !baseVersionId || readVersionId === baseVersionId;
}

function _behaviorOverlayClearDraftsForAgent(agentId) {
  agentId = _behaviorOverlayScopeKey(agentId);
  if (!agentId) return;
  var suffix = ':' + agentId;
  for (var key in _behaviorOverlayDrafts) {
    if (!Object.prototype.hasOwnProperty.call(_behaviorOverlayDrafts, key)) continue;
    if (String(key || '').slice(-suffix.length) === suffix) {
      delete _behaviorOverlayDrafts[key];
    }
  }
}

function _behaviorOverlayInvalidateFullRead(agentId) {
  agentId = _behaviorOverlayScopeKey(agentId);
  if (!agentId) return;
  delete _behaviorOverlayReadByAgent[agentId];
  _behaviorOverlayReadLoadingByAgent[agentId] = false;
  _behaviorOverlayClearDraftsForAgent(agentId);
}

function _behaviorOverlayVersionList(agentId) {
  behaviorOverlayNormalizeState();
  agentId = _behaviorOverlayScopeKey(agentId);
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

function _behaviorOverlayOpenProposalsForScope(scopeKey) {
  scopeKey = _behaviorOverlayScopeKey(scopeKey);
  return _behaviorOverlayProposalValues().filter(function(proposal) {
    var status = String((proposal && proposal.status) || '');
    return _behaviorOverlayProposalScopeKey(proposal) === scopeKey
      && (status === 'proposed' || status === 'approved');
  });
}

function _behaviorOverlayOpenProposalsForAgent(agentId) {
  return _behaviorOverlayOpenProposalsForScope(agentId);
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
  targetAgentId = _behaviorOverlayScopeKey(targetAgentId);
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  if (field === 'rationale') draft.rationale = String(value || '');
  else {
    draft.text = String(value || '');
    draft.dirty = true;
  }
}

function _behaviorOverlayRequestRead(agentId, seed, force) {
  var args = _behaviorOverlayScopeArgs(agentId);
  agentId = _behaviorOverlayScopeKey(agentId);
  if (!agentId || typeof send !== 'function') return;
  if (!force && (_behaviorOverlayReadByAgent[agentId] || _behaviorOverlayReadLoadingByAgent[agentId])) return;
  _behaviorOverlayReadLoadingByAgent[agentId] = true;
  send(Object.assign({ cmd: 'behavior_overlay_read', seed: seed !== false }, args));
}

function _behaviorOverlayRequestVersions(agentId, force) {
  var args = _behaviorOverlayScopeArgs(agentId);
  agentId = _behaviorOverlayScopeKey(agentId);
  if (!agentId || typeof send !== 'function') return;
  if (!force && (_behaviorOverlayVersionsByAgent[agentId] || _behaviorOverlayVersionsLoadingByAgent[agentId])) return;
  _behaviorOverlayVersionsLoadingByAgent[agentId] = true;
  send(Object.assign({ cmd: 'behavior_overlay_versions', limit: 50 }, args));
}

function _behaviorOverlayRequestProposals(force) {
  if (typeof send !== 'function') return;
  if (!force && (_behaviorOverlayProposalListLoaded || _behaviorOverlayProposalListLoadingKey === 'open')) return;
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
  if (targetAgentId) Object.assign(msg, _behaviorOverlayScopeArgs(targetAgentId));
  send(msg);
  return key;
}

function behaviorOverlayViewProposalDiff(proposalId, targetAgentId) {
  var key = _behaviorOverlayRequestProposalDiff(proposalId, targetAgentId, false);
  targetAgentId = _behaviorOverlayScopeKey(targetAgentId);
  if (targetAgentId) _behaviorOverlaySelectedDiffKeyByAgent[targetAgentId] = key;
  if (_behaviorOverlayRefreshRolePaneIfOpenForScope(targetAgentId)) return;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function behaviorOverlayDiffVersions(agentId, fromVersionId, toVersionId) {
  var args = _behaviorOverlayScopeArgs(agentId);
  agentId = _behaviorOverlayScopeKey(agentId);
  fromVersionId = String(fromVersionId || '').trim();
  toVersionId = String(toVersionId || '').trim();
  if (!agentId || !fromVersionId || !toVersionId || typeof send !== 'function') return;
  var key = _behaviorOverlayDiffKeyForVersions(fromVersionId, toVersionId);
  _behaviorOverlaySelectedDiffKeyByAgent[agentId] = key;
  if (!_behaviorOverlayDiffByKey[key] && !_behaviorOverlayDiffLoadingByKey[key]) {
    _behaviorOverlayDiffLoadingByKey[key] = true;
    send(Object.assign({
      cmd: 'behavior_overlay_diff',
      from_version_id: fromVersionId,
      to_version_id: toVersionId,
    }, args));
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
  targetAgentId = _behaviorOverlayScopeKey(targetAgentId);
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  var key = 'draft:' + _behaviorOverlayDraftKey(mode, targetAgentId, authorAgentId);
  _behaviorOverlayDiffByKey[key] = {
    type: 'behavior_overlay_diff',
    diff: _behaviorOverlayDraftUnifiedDiff(read.text || '', draft.text || '', 'active', 'draft'),
    draft: true,
  };
  _behaviorOverlaySelectedDiffKeyByAgent[targetAgentId] = key;
  if (_behaviorOverlayRefreshRolePaneIfOpenForScope(targetAgentId)) return;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function behaviorOverlaySubmitDraft(mode, targetAgentId, authorAgentId, authorKind, directEdit) {
  var targetArgs = _behaviorOverlayScopeArgs(targetAgentId);
  targetAgentId = _behaviorOverlayScopeKey(targetAgentId);
  authorAgentId = String(authorAgentId || '').trim();
  authorKind = String(authorKind || '').trim() || 'user';
  if (!targetAgentId || !authorAgentId || typeof send !== 'function') return;
  if (!_behaviorOverlayReadIsFresh(targetAgentId)) {
    _behaviorOverlayRequestRead(targetAgentId, true, true);
    if (typeof _showToast === 'function') {
      _showToast('Refreshing current behavior text before submitting', 'info');
    }
    return;
  }
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorAgentId, read.text || '');
  var text = String(draft.text || '');
  var baseVersionId = _behaviorOverlayBaseVersionId(targetAgentId);
  send(Object.assign({
    cmd: 'behavior_overlay_propose',
    proposed_by_agent_id: authorAgentId,
    proposed_by_kind: authorKind,
    text: text,
    rationale: String(draft.rationale || ''),
    proposal_type: 'set_text',
    expected_base_version_id: baseVersionId,
    architect_approver_id: directEdit ? authorAgentId : '',
    auto_apply_architect_direct: !!directEdit,
  }, targetArgs));
  draft.dirty = false;
  if (typeof _showToast === 'function') _showToast('Behavior overlay proposal submitted', 'info');
}

function behaviorOverlayRequestRollback(targetAgentId, versionId, authorAgentId, authorKind, directEdit) {
  var targetArgs = _behaviorOverlayScopeArgs(targetAgentId);
  targetAgentId = _behaviorOverlayScopeKey(targetAgentId);
  versionId = String(versionId || '').trim();
  authorAgentId = String(authorAgentId || '').trim();
  authorKind = String(authorKind || '').trim() || 'user';
  if (!targetAgentId || !versionId || !authorAgentId || typeof send !== 'function') return;
  send(Object.assign({
    cmd: 'behavior_overlay_propose',
    proposed_by_agent_id: authorAgentId,
    proposed_by_kind: authorKind,
    proposal_type: 'rollback',
    target_version_id: versionId,
    expected_base_version_id: _behaviorOverlayBaseVersionId(targetAgentId),
    rationale: 'Rollback requested from Behavior tab',
    architect_approver_id: directEdit ? authorAgentId : '',
    auto_apply_architect_direct: !!directEdit,
  }, targetArgs));
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
  var targetKey = _behaviorOverlayProposalScopeKey(proposal);
  var viewerKind = _behaviorOverlayKind(viewer);
  var viewerId = String((viewer && viewer.id) || '');
  var canArchitectAct = !opts.readOnly && viewerKind === 'architect'
    && String(proposal.next_actor_kind || '') === 'architect';
  var html = '<div class="behavior-overlay-proposal-card" data-agent-panel-anchor="behavior-proposal-'
    + _behaviorOverlayAttr(proposalId) + '">';
  html += '<div class="behavior-overlay-card-head">';
  html += '<span class="detail-section-primary">'
    + _behaviorOverlayEsc(_behaviorOverlayProposalLabel(proposal)) + '</span>';
  html += '<span class="detail-task-status">'
    + _behaviorOverlayEsc(_behaviorOverlayProposalStatusLabel(proposal)) + '</span>';
  html += '</div>';
  html += '<div class="detail-section-card-meta behavior-overlay-meta-row">';
  html += '<span>' + _behaviorOverlayEsc(proposal.proposal_type || 'set_text') + '</span>';
  if (String(proposal.scope_kind || '') === 'role') {
    html += '<span>role · ' + _behaviorOverlayEsc(proposal.scope_group || 'group') + '</span>';
  }
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
    + _behaviorOverlayJs(proposalId) + ',' + _behaviorOverlayJs(targetKey) + ')">View diff</button>';
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

function _behaviorOverlayProposalsSection(agent, proposals, title, opts) {
  opts = opts || {};
  var html = '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">'
    + _behaviorOverlayEsc(title || 'Open proposals') + '</span><span class="detail-task-status">'
    + _behaviorOverlayEsc(String(proposals.length)) + '</span></div>';
  if (!proposals.length) {
    html += '<div class="agent-panel-empty">No open behavior proposals.</div>';
  } else {
    for (var i = 0; i < proposals.length; i++) {
      html += _behaviorOverlayProposalCard(proposals[i], agent, opts);
    }
  }
  html += '</section>';
  return html;
}

function _behaviorOverlayTimeline(agent, targetAgentId, viewerId, viewerKind, directEdit, opts) {
  opts = opts || {};
  var activeId = _behaviorOverlayBaseVersionId(targetAgentId);
  var versions = _behaviorOverlayVersionList(targetAgentId);
  var html = '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">Version timeline</span>';
  html += '<span class="behavior-overlay-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + _behaviorOverlayEsc(String(versions.length)) + '</span></div>';
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
      html += '<span class="behavior-overlay-version-number">'
        + _behaviorOverlayEsc(_behaviorOverlayVersionLabel(version.version_number)) + '</span>';
      html += '<span class="behavior-overlay-version-meta">'
        + _behaviorOverlayEsc(_behaviorOverlayTimestamp(version.created_at)) + '</span>';
      if (isActive) html += '<span class="detail-task-status">active</span>';
      html += '</div>';
      html += '<div class="behavior-overlay-version-sub">'
        + _behaviorOverlayEsc(version.rationale || 'No rationale') + '</div>';
      html += '<div class="behavior-overlay-version-actions">';
      if (!opts.readOnly && !isActive && activeId) {
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
  var targetAgentId = _behaviorOverlayScopeKey((targetAgent && targetAgent.id) || targetAgent);
  var authorId = String((authorAgent && authorAgent.id) || '');
  var authorKind = _behaviorOverlayKind(authorAgent);
  var read = _behaviorOverlayReadByAgent[targetAgentId] || {};
  var readFresh = _behaviorOverlayReadIsFresh(targetAgentId);
  var loading = (!!_behaviorOverlayReadLoadingByAgent[targetAgentId] && !read.version) || !readFresh;
  var draft = _behaviorOverlayDraft(mode, targetAgentId, authorId, read.text || '');
  var key = _behaviorOverlayDraftKey(mode, targetAgentId, authorId);
  var textId = _behaviorOverlayDomId('behavior-overlay-text', key);
  var rationaleId = _behaviorOverlayDomId('behavior-overlay-rationale', key);
  var active = _behaviorOverlayActive(targetAgentId);
  var version = _behaviorOverlayActiveVersion(targetAgentId);
  var html = '<section class="detail-section-card behavior-overlay-section behavior-overlay-editor">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">';
  html += _behaviorOverlayEsc(mode === 'direct'
    ? 'Direct edit hired engineer overlay'
    : (mode === 'role' ? 'Role overlay text' : 'Current overlay'));
  html += '</span><span class="detail-task-status">'
    + _behaviorOverlayEsc(loading ? 'loading' : _behaviorOverlayVersionLabel(version.version_number)) + '</span></div>';
  html += '<div class="behavior-overlay-summary-grid">';
  html += '<div><span class="detail-label">Target</span><span class="detail-val">'
    + _behaviorOverlayEsc((targetAgent && targetAgent.name) || _behaviorOverlayScopeLabel(targetAgentId) || targetAgentId) + '</span></div>';
  if (String((targetAgent && targetAgent.scope_kind) || '') === 'role') {
    html += '<div><span class="detail-label">Scope</span><span class="detail-val">'
      + _behaviorOverlayEsc('role · ' + (targetAgent.scope_group || '')) + '</span></div>';
  }
  html += '<div><span class="detail-label">Active version</span><span class="detail-val">'
    + _behaviorOverlayEsc(active.active_version_id || version.id || '—') + '</span></div>';
  html += '<div><span class="detail-label">Text</span><span class="detail-val">'
    + _behaviorOverlayEsc((version.text_bytes != null ? version.text_bytes : (read.text || '').length) + ' bytes') + '</span></div>';
  html += '<div><span class="detail-label">Hash</span><span class="detail-val">'
    + _behaviorOverlayEsc(_behaviorOverlayShortHash(version.text_sha256)) + '</span></div>';
  html += '</div>';
  html += '<label for="' + _behaviorOverlayAttr(textId) + '">Proposed behavior text</label>';
  if (!readFresh) {
    html += '<div class="behavior-overlay-warning-inline">Refreshing current full overlay text before edits can be submitted.</div>';
  }
  html += '<textarea id="' + _behaviorOverlayAttr(textId) + '" class="behavior-overlay-textarea" rows="8"'
    + ' placeholder="Additive, subordinate behavior guidance for this scope…"'
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
    + ')"' + (readFresh ? '' : ' disabled') + '>Preview draft diff</button>';
  html += '<button type="button" class="btn-primary"'
    + (readFresh ? '' : ' disabled title="Waiting for current behavior text"')
    + ' onclick="behaviorOverlaySubmitDraft('
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

function _behaviorOverlayInnerTabs(agent) {
  var kind = _behaviorOverlayKind(agent);
  if (kind === 'architect') {
    return [
      { key: 'architect', label: 'Architect overlays' },
      { key: 'engineer', label: 'Engineer overlays' },
    ];
  }
  if (kind === 'engineer') {
    return [{ key: 'engineer', label: 'Engineer overlays' }];
  }
  return [];
}

function _behaviorOverlayInnerTab(agent) {
  var agentId = String((agent && agent.id) || '');
  var selected = agentId ? String(_behaviorOverlayInnerTabByAgent[agentId] || '') : '';
  var tabs = _behaviorOverlayInnerTabs(agent);
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].key === selected) return selected;
  }
  return tabs.length ? tabs[0].key : '';
}

function _behaviorOverlayRenderInnerTabs(agent) {
  var tabs = _behaviorOverlayInnerTabs(agent);
  if (!tabs.length) return '';
  var active = _behaviorOverlayInnerTab(agent);
  var html = '<div class="agent-panel-events-subtabs behavior-overlay-subtabs" role="tablist" aria-label="Behavior overlay scopes">';
  for (var i = 0; i < tabs.length; i++) {
    var tab = tabs[i];
    html += '<button type="button"'
      + ' id="agent-panel-behavior-subtab-' + _behaviorOverlayAttr(tab.key) + '"'
      + ' class="agent-panel-events-subtab behavior-overlay-subtab' + (active === tab.key ? ' active' : '') + '"'
      + ' data-agent-panel-behavior-inner-tab="' + _behaviorOverlayAttr(tab.key) + '"'
      + ' role="tab"'
      + ' aria-selected="' + (active === tab.key ? 'true' : 'false') + '"'
      + ' onclick="behaviorOverlaySelectInnerTab(' + _behaviorOverlayJs(tab.key) + ')">'
      + _behaviorOverlayEsc(tab.label)
      + '</button>';
  }
  html += '</div>';
  return html;
}

function behaviorOverlaySelectInnerTab(tab) {
  var focused = (typeof _focusedEngineerAgent === 'function')
    ? _focusedEngineerAgent()
    : (typeof _resolveFocusedAgent === 'function' ? _resolveFocusedAgent() : null);
  if (!focused) return;
  var kind = _behaviorOverlayKind(focused);
  if (typeof _agentPanelActiveTab === 'function'
      && _agentPanelActiveTab(kind) !== 'behavior') return;
  tab = String(tab || '');
  var tabs = _behaviorOverlayInnerTabs(focused);
  var next = tabs.length ? tabs[0].key : '';
  for (var i = 0; i < tabs.length; i++) {
    if (tabs[i].key === tab) {
      next = tab;
      break;
    }
  }
  if (!next) return;
  var agentId = String(focused.id || '');
  if (agentId && _behaviorOverlayInnerTabByAgent[agentId] === next) return;
  if (agentId) _behaviorOverlayInnerTabByAgent[agentId] = next;
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _behaviorOverlayRolePaneForAgent(group, roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  group = String(group || '').trim();
  if (!roleKind || !group) {
    return '<div class="agent-panel-empty">Role behavior overlays require a group and role kind.</div>';
  }
  var title = _behaviorOverlayRoleScopeTitle(roleKind);
  var target = _behaviorOverlayRoleTarget(group, roleKind);
  var targetKey = target ? target.id : '';
  if (!target || !targetKey) {
    return '<div class="agent-panel-empty">Role behavior overlays require a group and role kind.</div>';
  }
  _behaviorOverlayRequestRead(targetKey, true, false);
  _behaviorOverlayRequestVersions(targetKey, false);
  _behaviorOverlayRequestProposals(false);

  var active = _behaviorOverlayActive(targetKey);
  var version = _behaviorOverlayActiveVersion(targetKey);
  var read = _behaviorOverlayReadByAgent[targetKey] || {};
  var readFresh = _behaviorOverlayReadIsFresh(targetKey);
  var loading = (!!_behaviorOverlayReadLoadingByAgent[targetKey] && !read.version) || !readFresh;
  var text = readFresh ? String(read.text || '') : '';
  var proposals = _behaviorOverlayOpenProposalsForScope(targetKey);
  var viewer = { id: 'user', name: 'User', kind: 'user' };

  var html = '<div class="behavior-overlay-tab behavior-overlay-role-tab behavior-overlay-role-readonly" data-behavior-overlay-role="'
    + _behaviorOverlayAttr(roleKind) + '" data-behavior-overlay-scope="'
    + _behaviorOverlayAttr(targetKey) + '">';
  html += '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">'
    + _behaviorOverlayEsc(title) + '</span>'
    + '<span class="detail-task-status">read-only inherited</span></div>';
  html += '<div class="behavior-overlay-route-note">'
    + _behaviorOverlayEsc(title + ' applies group-wide and is layered before each individual ' + roleKind + ' overlay.')
    + '</div>';
  html += '<div class="behavior-overlay-summary-grid">';
  html += '<div><span class="detail-label">Group</span><span class="detail-val">'
    + _behaviorOverlayEsc(group) + '</span></div>';
  html += '<div><span class="detail-label">Role kind</span><span class="detail-val">'
    + _behaviorOverlayEsc(roleKind) + '</span></div>';
  html += '<div><span class="detail-label">Active version</span><span class="detail-val">'
    + _behaviorOverlayEsc(active.active_version_id || version.id || '—') + '</span></div>';
  html += '<div><span class="detail-label">Text</span><span class="detail-val">'
    + _behaviorOverlayEsc((version.text_bytes != null ? version.text_bytes : text.length) + ' bytes') + '</span></div>';
  html += '<div><span class="detail-label">Hash</span><span class="detail-val">'
    + _behaviorOverlayEsc(_behaviorOverlayShortHash(version.text_sha256)) + '</span></div>';
  html += '</div>';
  html += '<label>Current inherited role text</label>';
  if (loading) {
    html += '<div class="agent-panel-empty">Refreshing inherited role overlay text…</div>';
  } else if (!text) {
    html += '<div class="agent-panel-empty">No inherited role overlay text is active.</div>';
  } else {
    html += '<pre class="behavior-overlay-readonly-text">' + _behaviorOverlayEsc(text) + '</pre>';
  }
  html += '</section>';
  html += _behaviorOverlayDiffSection(targetKey);
  html += _behaviorOverlayProposalsSection(viewer, proposals, 'Open proposals for this inherited role', { readOnly: true });
  html += _behaviorOverlayTimeline(viewer, targetKey, 'user', 'user', false, { readOnly: true });
  html += '</div>';
  return html;
}

function _behaviorOverlayLayerSection(layerKey, title, badge, note, bodyHtml) {
  layerKey = String(layerKey || 'section').replace(/[^A-Za-z0-9_-]/g, '-');
  var html = '<section class="behavior-overlay-layer behavior-overlay-layer-'
    + _behaviorOverlayAttr(layerKey) + '" data-behavior-overlay-layer="'
    + _behaviorOverlayAttr(layerKey) + '">';
  html += '<div class="behavior-overlay-layer-head">';
  html += '<span class="behavior-overlay-layer-title">' + _behaviorOverlayEsc(title) + '</span>';
  if (badge) {
    html += '<span class="behavior-overlay-layer-badge">' + _behaviorOverlayEsc(badge) + '</span>';
  }
  html += '</div>';
  if (note) {
    html += '<div class="behavior-overlay-layer-note">' + _behaviorOverlayEsc(note) + '</div>';
  }
  html += '<div class="behavior-overlay-layer-body">' + (bodyHtml || '') + '</div>';
  html += '</section>';
  return html;
}

function _behaviorOverlayInheritedLayer(group, roleKind) {
  var roleLabel = _behaviorOverlayPluralRoleLabel(roleKind);
  return _behaviorOverlayLayerSection(
    'inherited',
    'Inherited role overlay',
    'group-wide · ' + roleLabel,
    'Read-only guidance inherited from the group role scope. It is applied before the agent-specific overlay below.',
    _behaviorOverlayRolePaneForAgent(group, roleKind)
  );
}

function _behaviorOverlayAgentLayer(title, note, bodyHtml) {
  return _behaviorOverlayLayerSection(
    'agent-specific',
    title || 'Agent-specific overlay',
    'agent-specific · editable',
    note || 'Guidance in this section applies only to the selected agent and layers after inherited role guidance.',
    bodyHtml
  );
}

function _behaviorOverlayArchitectScope(agent) {
  var group = String((agent && agent.group) || '');
  var html = '';
  html += _behaviorOverlayInheritedLayer(group, 'architect');
  html += _behaviorOverlayAgentLayer(
    'Agent-specific architect overlay',
    'Guidance in this section applies only to this architect and layers after the inherited architect role overlay.',
    _behaviorOverlayOwn(agent)
  );
  return html;
}

function _behaviorOverlayEngineerScope(agent) {
  var kind = _behaviorOverlayKind(agent);
  var group = String((agent && agent.group) || '');
  var html = '';
  html += _behaviorOverlayInheritedLayer(group, 'engineer');
  if (kind === 'architect') {
    html += _behaviorOverlayAgentLayer(
      'Hired engineer-specific overlay',
      "Select a hired engineer to inspect or edit that engineer's per-agent overlay. It layers after the inherited engineer role overlay above.",
      _behaviorOverlayHiredGovernance(agent)
    );
  } else {
    html += _behaviorOverlayAgentLayer(
      'Agent-specific engineer overlay',
      'Guidance in this section applies only to this engineer and layers after the inherited engineer role overlay.',
      _behaviorOverlayOwn(agent)
    );
  }
  return html;
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
  html += '<span class="behavior-overlay-count ui-badge ui-badge--compact ui-badge--neutral ui-badge--count">' + _behaviorOverlayEsc(String(hired.length)) + '</span></div>';
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
  var activeInner = _behaviorOverlayInnerTab(agent);
  var html = '<div class="behavior-overlay-tab" data-agent-panel-anchor="behavior-overlay-root" data-agent-panel-behavior-view="'
    + _behaviorOverlayAttr(activeInner) + '">';
  html += '<div class="agent-panel-worklog-header behavior-overlay-header">';
  html += '<span class="agent-panel-worklog-title">Behavior overlays</span>';
  html += '<span class="agent-panel-worklog-note">Additive, governed prompt guidance; role-wide and per-agent scopes are separated below.</span>';
  html += '</div>';
  html += _behaviorOverlayRenderInnerTabs(agent);
  if (activeInner === 'architect') html += _behaviorOverlayArchitectScope(agent);
  else html += _behaviorOverlayEngineerScope(agent);
  html += '</div>';
  return html;
}

function _behaviorOverlayRolePaneMountId(roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  return roleKind ? ('gs-' + roleKind + '-role-behavior-overlay') : '';
}

function _behaviorOverlayRoleTarget(group, roleKind) {
  var scope = _behaviorOverlayRoleScope(group, roleKind);
  if (!scope) return null;
  return Object.assign({}, scope, {
    id: scope.scope_id,
    name: _behaviorOverlayScopeLabel(scope),
    kind: scope.scope_key,
    group: scope.scope_group,
  });
}

function _behaviorOverlayRolePaneHtml(group, roleKind, opts) {
  opts = opts || {};
  var target = _behaviorOverlayRoleTarget(group, roleKind);
  if (!target) {
    return '<div class="agent-panel-empty">Role behavior overlays require a group and role kind.</div>';
  }
  var targetKey = target.id;
  _behaviorOverlayRequestRead(targetKey, true, false);
  _behaviorOverlayRequestVersions(targetKey, false);
  _behaviorOverlayRequestProposals(false);
  var author = { id: 'user', name: 'User', kind: 'user' };
  var proposals = _behaviorOverlayOpenProposalsForScope(targetKey);
  var roleLabel = _behaviorOverlayTitleCase(roleKind);
  var title = opts.title || 'Role Dynamic Behavior overlay';
  var note = opts.note || (roleLabel + ' role overlays apply group-wide and always require user diff approval before activation.');
  var html = '<div class="behavior-overlay-tab behavior-overlay-role-tab" data-behavior-overlay-role="'
    + _behaviorOverlayAttr(roleKind) + '" data-behavior-overlay-scope="'
    + _behaviorOverlayAttr(targetKey) + '">';
  html += '<div class="agent-panel-worklog-header behavior-overlay-header">';
  html += '<span class="agent-panel-worklog-title">' + _behaviorOverlayEsc(title) + '</span>';
  html += '<span class="agent-panel-worklog-note">'
    + _behaviorOverlayEsc(note)
    + '</span>';
  html += '</div>';
  html += '<section class="detail-section-card behavior-overlay-section">';
  html += '<div class="detail-section-card-head"><span class="detail-section-primary">Scope</span>'
    + '<span class="detail-task-status">role · ' + _behaviorOverlayEsc(roleKind) + '</span></div>';
  html += '<div class="behavior-overlay-summary-grid">';
  html += '<div><span class="detail-label">Group</span><span class="detail-val">'
    + _behaviorOverlayEsc(group) + '</span></div>';
  html += '<div><span class="detail-label">Role kind</span><span class="detail-val">'
    + _behaviorOverlayEsc(roleKind) + '</span></div>';
  html += '<div><span class="detail-label">Approval route</span><span class="detail-val">user approval required</span></div>';
  html += '<div><span class="detail-label">Prompt application</span><span class="detail-val">'
    + _behaviorOverlayEsc(roleKind === 'worker'
      ? 'next worker dispatch'
      : 'next launch / relaunch')
    + '</span></div>';
  html += '</div>';
  html += '</section>';
  html += _behaviorOverlayEditor(author, target, 'role', author, false);
  html += _behaviorOverlayDiffSection(targetKey);
  html += _behaviorOverlayProposalsSection(author, proposals, 'Open proposals for this role');
  html += _behaviorOverlayTimeline(author, targetKey, 'user', 'user', false);
  html += '</div>';
  return html;
}

function renderBehaviorOverlayRolePane(group, roleKind) {
  roleKind = _behaviorOverlayNormalizeRoleKind(roleKind);
  group = String(group || '').trim();
  var mountId = _behaviorOverlayRolePaneMountId(roleKind);
  var mount = mountId && typeof document !== 'undefined'
    ? document.getElementById(mountId)
    : null;
  if (!mount) return false;
  var snapshot = null;
  if (typeof _captureSurfaceState === 'function') {
    snapshot = _captureSurfaceState(mount, { scrollSelectors: [':root'] });
  }
  mount.innerHTML = _behaviorOverlayRolePaneHtml(group, roleKind);
  if (typeof _restoreSurfaceState === 'function') {
    _restoreSurfaceState(mount, snapshot, { scrollSelectors: [':root'] });
  }
  return true;
}

function renderGroupBehaviorRolePanes(group) {
  for (var i = 0; i < _behaviorOverlayRoleKinds.length; i++) {
    renderBehaviorOverlayRolePane(group, _behaviorOverlayRoleKinds[i]);
  }
}

function _behaviorOverlayRefreshRolePaneIfOpenForScope(scopeKey) {
  var scope = _behaviorOverlayScopeFromId(scopeKey);
  if (!scope || scope.scope_kind !== 'role') return false;
  if (typeof _settingsGroup === 'undefined'
      || String(_settingsGroup || '').trim() !== String(scope.scope_group || '').trim()) {
    return false;
  }
  return renderBehaviorOverlayRolePane(scope.scope_group, scope.scope_key);
}

function _behaviorOverlayRefreshFocusedBehaviorPanel() {
  if (typeof _agentPanelRefreshCurrentTab === 'function' && _agentPanelRefreshCurrentTab()) return true;
  if (typeof renderAgentPanel === 'function') {
    renderAgentPanel();
    return true;
  }
  return false;
}

function _behaviorOverlayRefreshPanelForVisibleScope(scopeLike) {
  if (!behaviorOverlayDeltaInvalidatesFocusedPanel(scopeLike || {})) return false;
  return _behaviorOverlayRefreshFocusedBehaviorPanel();
}

function _behaviorOverlayRefreshPanelIfFocused(agentId) {
  agentId = String(agentId || '').trim();
  if (!agentId) return false;
  return _behaviorOverlayRefreshPanelForVisibleScope({ agent_id: agentId });
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
    var agentId = _behaviorOverlayScopeKey(msg);
    if (agentId) {
      _behaviorOverlayReadLoadingByAgent[agentId] = false;
      var knownActiveVersionId = String(
        (state.behavior_overlay_active[agentId] || {}).active_version_id || ''
      );
      var msgVersionId = String((msg.version && msg.version.id) || '');
      if (knownActiveVersionId && msgVersionId && msgVersionId !== knownActiveVersionId) {
        _behaviorOverlayInvalidateFullRead(agentId);
        _behaviorOverlayRequestRead(agentId, true, true);
        _behaviorOverlayRefreshPanelIfFocused(agentId);
        _behaviorOverlayRefreshRolePaneIfOpenForScope(agentId);
        return true;
      }
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
      _behaviorOverlayRefreshRolePaneIfOpenForScope(agentId);
    }
    return true;
  }
  if (msg.type === 'behavior_overlay_versions') {
    var vidAgent = _behaviorOverlayScopeKey(msg);
    _behaviorOverlayVersionsLoadingByAgent[vidAgent] = false;
    _behaviorOverlayVersionsByAgent[vidAgent] = Array.isArray(msg.versions) ? msg.versions.slice() : [];
    state.behavior_overlay_versions[vidAgent] = _behaviorOverlayVersionsByAgent[vidAgent];
    _behaviorOverlayRefreshPanelIfFocused(vidAgent);
    _behaviorOverlayRefreshRolePaneIfOpenForScope(vidAgent);
    return true;
  }
  if (msg.type === 'behavior_overlay_proposals') {
    _behaviorOverlayProposalListLoadingKey = '';
    _behaviorOverlayProposalListLoaded = true;
    var proposals = Array.isArray(msg.proposals) ? msg.proposals : [];
    var refreshFocusedPanel = false;
    for (var p = 0; p < proposals.length; p++) {
      if (!refreshFocusedPanel && behaviorOverlayDeltaInvalidatesFocusedPanel(proposals[p])) {
        refreshFocusedPanel = true;
      }
      _behaviorOverlayUpsertProposal(proposals[p]);
    }
    if (refreshFocusedPanel) _behaviorOverlayRefreshFocusedBehaviorPanel();
    if (typeof _settingsGroup !== 'undefined' && _settingsGroup) {
      renderGroupBehaviorRolePanes(_settingsGroup);
    }
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
    var affectedAgent = _behaviorOverlayScopeKey(
      msg.to_proposal || msg.proposal || msg.from_version || msg.to_version || {}
    );
    _behaviorOverlayRefreshPanelIfFocused(affectedAgent);
    _behaviorOverlayRefreshRolePaneIfOpenForScope(affectedAgent);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return true;
  }
  if (msg.type === 'behavior_overlay_proposal') {
    if (msg.proposal) _behaviorOverlayUpsertProposal(msg.proposal);
    var proposal = msg.proposal || _behaviorOverlayProposal(msg.proposal_id) || {};
    var proposalScopeKey = _behaviorOverlayProposalScopeKey(proposal);
    _behaviorOverlayRefreshPanelIfFocused(proposalScopeKey);
    _behaviorOverlayRefreshRolePaneIfOpenForScope(proposalScopeKey);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return true;
  }
  return false;
}

function behaviorOverlayApplyDelta(op) {
  if (!op || !op.op) return;
  behaviorOverlayNormalizeState();
  if (op.op === 'behavior_overlay_active_update') {
    var activeAgentId = _behaviorOverlayScopeKey(op);
    var nextActiveVersionId = String(op.active_version_id || '');
    var previousActiveVersionId = String(
      (
        activeAgentId
        && state.behavior_overlay_active
        && state.behavior_overlay_active[activeAgentId]
      )
        ? state.behavior_overlay_active[activeAgentId].active_version_id
        : ''
    );
    var cachedReadVersionId = _behaviorOverlayReadVersionId(activeAgentId);
    var active = Object.assign({}, op);
    delete active.op;
    if (activeAgentId) state.behavior_overlay_active[activeAgentId] = active;
    if (
      activeAgentId
      && nextActiveVersionId
      && (
        (previousActiveVersionId && previousActiveVersionId !== nextActiveVersionId)
        || (cachedReadVersionId && cachedReadVersionId !== nextActiveVersionId)
      )
    ) {
      _behaviorOverlayInvalidateFullRead(activeAgentId);
    }
    _behaviorOverlayRefreshRolePaneIfOpenForScope(activeAgentId);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return;
  }
  if (op.op === 'behavior_overlay_version_append') {
    var version = Object.assign({}, op);
    delete version.op;
    var agentId = _behaviorOverlayScopeKey(version);
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
    var activeForVersion = state.behavior_overlay_active
      ? (state.behavior_overlay_active[agentId] || {})
      : {};
    if (
      String(activeForVersion.active_version_id || '') === String(version.id || '')
      && _behaviorOverlayReadVersionId(agentId)
      && _behaviorOverlayReadVersionId(agentId) !== String(version.id || '')
    ) {
      _behaviorOverlayInvalidateFullRead(agentId);
    }
    _behaviorOverlayRefreshRolePaneIfOpenForScope(agentId);
    if (_behaviorOverlayApprovalModal.open) renderBehaviorOverlayApprovalModal();
    return;
  }
  if (op.op === 'behavior_overlay_proposal_upsert'
      || op.op === 'behavior_overlay_proposal_resolve') {
    var proposal = Object.assign({}, op);
    delete proposal.op;
    var proposalScopeKey = _behaviorOverlayProposalScopeKey(proposal);
    _behaviorOverlayUpsertProposal(proposal);
    _behaviorOverlayRefreshRolePaneIfOpenForScope(proposalScopeKey);
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
  var agentId = _behaviorOverlayScopeKey(op);
  if (!agentId && op.id && state && state.behavior_overlay_proposals) {
    var cached = state.behavior_overlay_proposals[op.id];
    if (cached) agentId = _behaviorOverlayProposalScopeKey(cached);
  }
  if (!agentId) return false;
  var affectedScope = _behaviorOverlayScopeFromId(agentId);
  var activeInner = _behaviorOverlayInnerTab(focused);
  if (affectedScope && affectedScope.scope_kind === 'role') {
    return String(focused.group || '') === String(affectedScope.scope_group || '')
      && activeInner === String(affectedScope.scope_key || '');
  }
  if (agentId === String(focused.id || '')) return activeInner === focusedKind;
  if (focusedKind === 'architect' && activeInner === 'engineer') {
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
  var target = _behaviorOverlayProposalLabel(proposal);
  var html = '<div class="behavior-overlay-approval-card" data-behavior-overlay-approval="1">';
  html += '<div class="behavior-overlay-approval-card-title">'
    + _behaviorOverlayEsc(String(proposal.scope_kind || '') === 'role'
      ? 'Role behavior overlay approval'
      : 'Behavior overlay approval')
    + '</div>';
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
  var isRoleScope = String(proposal.scope_kind || '') === 'role';
  var html = '';
  html += '<div class="behavior-overlay-approval-summary">';
  html += '<div><span class="detail-label">Task</span><span class="detail-val">'
    + _behaviorOverlayEsc((task && (task.id + ' · ' + task.task)) || _behaviorOverlayApprovalModal.taskId) + '</span></div>';
  html += '<div><span class="detail-label">Target</span><span class="detail-val">'
    + _behaviorOverlayEsc(isRoleScope
      ? (_behaviorOverlayProposalLabel(proposal) || '—')
      : ((targetAgent && (targetAgent.name || targetAgent.id)) || proposal.agent_id || '—')) + '</span></div>';
  html += '<div><span class="detail-label">Scope</span><span class="detail-val">'
    + _behaviorOverlayEsc(isRoleScope
      ? ('role · ' + (proposal.scope_group || '—'))
      : 'agent') + '</span></div>';
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

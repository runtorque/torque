/* Agent panel — Agent Class assignment and trusted capability UI */

function _agentPanelClassDefaultIdForKind(kind) {
  kind = String(kind || '').trim();
  if (kind === 'architect' || kind === 'engineer' || kind === 'worker') {
    return 'default-' + kind;
  }
  return '';
}

function _agentPanelClassVersionSuffix(version) {
  version = String(version || '').trim();
  return version ? ('@' + version) : '';
}

function _agentPanelKindDisplayLabel(kind) {
  kind = String(kind || '').trim();
  if (kind === 'architect') return 'Architect';
  if (kind === 'engineer') return 'Engineer';
  if (kind === 'worker') return 'Worker';
  return kind ? kind.replace(/[-_]+/g, ' ') : 'Agent';
}

function _agentPanelClassRawDisplayName(item, fallback) {
  item = item || {};
  return String(
    item.primary_identity_label
    || item.primary_display_name
    || item.display_name
    || item.name
    || item.id
    || fallback
    || ''
  ).trim();
}

function _agentPanelClassDisplayName(item, fallback) {
  return _agentPanelClassRawDisplayName(item, fallback);
}

function _agentPanelClassSecondaryLabel(item, fallbackKind) {
  item = item || {};
  return String(
    item.secondary_base_kind_label
    || (item.secondary_base_kind_metadata && item.secondary_base_kind_metadata.base_kind_label)
    || _agentPanelKindDisplayLabel(item.base_kind || fallbackKind)
  ).trim();
}

function _agentPanelClassStatusLabel(item) {
  item = item || {};
  if (_agentPanelClassIsArchived(item)) return 'archived';
  return String(item.status || item.lifecycle || '').trim() || 'full';
}

function _agentPanelClassNoticeKey(value) {
  return String(value == null ? '' : value)
    .trim()
    .toLowerCase()
    .replace(/&amp;/g, '&')
    .replace(/\bagent classes?\b/g, 'agent class')
    .replace(/[^\w]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _agentPanelIsExternalConnectorNotice(value) {
  var key = _agentPanelClassNoticeKey(value);
  return !!(
    key
    && (
      key.indexOf('external connector') >= 0
      || key.indexOf('connector exposure') >= 0
    )
    && (
      key.indexOf('not govern') >= 0
      || key.indexOf('not enforced') >= 0
      || key.indexOf('separate') >= 0
      || key.indexOf('manage connector access separately') >= 0
    )
  );
}

function _agentPanelClassConnectorCaveat(item) {
  // Normal Agent Class surfaces intentionally omit connector-governance copy.
  // Connector access is managed outside this UI; avoid presenting it as an
  // actionable Agent Class warning.
  return '';
}

function _agentPanelClassUniqueWarnings(item) {
  item = item || {};
  var warnings = Array.isArray(item.warnings) ? item.warnings : [];
  var seen = {};
  var out = [];
  for (var i = 0; i < warnings.length; i++) {
    var text = String(warnings[i] || '').trim();
    if (!text) continue;
    if (_agentPanelIsExternalConnectorNotice(text)) continue;
    var key = _agentPanelClassNoticeKey(text);
    if (!key || seen[key]) continue;
    seen[key] = true;
    out.push(text);
  }
  return out;
}

function _agentPanelClassIsArchived(item) {
  item = item || {};
  var metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
  return !!(item.archived || item.disabled || metadata.archived || metadata.disabled || metadata.archived_at);
}

function _agentPanelClassLaunchDisabledReason(item, expectedKind) {
  item = item || {};
  expectedKind = String(expectedKind || '').trim();
  if (!item.id) return 'Select an Agent Class first.';
  if (expectedKind && String(item.base_kind || '') !== expectedKind) {
    return 'Agent Class base kind does not match this agent.';
  }
  if (_agentPanelClassIsArchived(item)) return 'Archived/disabled Agent Classes cannot be assigned.';
  if (item.launchable === false) return 'Backend reports this Agent Class is not assignable.';
  if (_agentPanelClassStatusLabel(item) === 'invalid') return 'Invalid Agent Classes cannot be assigned.';
  return '';
}

function _agentPanelClassSnapshot(agent) {
  if (agent && agent.effective_agent_class_snapshot
      && typeof agent.effective_agent_class_snapshot === 'object') {
    return agent.effective_agent_class_snapshot;
  }
  var status = (agent && agent.agent_class_status && typeof agent.agent_class_status === 'object')
    ? agent.agent_class_status
    : {};
  return (status.effective_class && typeof status.effective_class === 'object')
    ? status.effective_class
    : {};
}

function _agentPanelClassBaseDir(agent) {
  return String(
    (agent && (agent.worktree_repo_root || agent.directory || agent.current_path)) || ''
  ).trim();
}

function _agentPanelClassListKey(agent) {
  return _agentPanelClassBaseDir(agent) || '__default__';
}

function _agentPanelClassListCache(agent) {
  var key = _agentPanelClassListKey(agent);
  if (!_agentPanelClassListByKey[key]) {
    _agentPanelClassListByKey[key] = {
      key: key,
      baseDir: _agentPanelClassBaseDir(agent),
      classes: [],
      issues: [],
      requested: false,
      loading: false,
      error: '',
    };
  }
  return _agentPanelClassListByKey[key];
}

function _agentPanelClassFromList(agent, classId) {
  classId = String(classId || '').trim();
  if (!classId) return null;
  var cache = _agentPanelClassListCache(agent);
  var list = Array.isArray(cache.classes) ? cache.classes : [];
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    if (String(item.id || '') === classId) return item;
  }
  return null;
}

function _agentPanelClassPreviewFor(agent, classId) {
  classId = String(classId || '').trim();
  if (!classId) return null;
  return _agentPanelClassPreviewById[classId] || _agentPanelClassFromList(agent, classId);
}

function _agentPanelClassCompatibleClasses(agent) {
  var kind = _agentPanelKind(agent);
  var cache = _agentPanelClassListCache(agent);
  var list = Array.isArray(cache.classes) ? cache.classes : [];
  var compatible = [];
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    if (String(item.base_kind || '') !== kind) continue;
    compatible.push(item);
  }
  compatible.sort(function(a, b) {
    var ab = a && a.builtin ? 0 : 1;
    var bb = b && b.builtin ? 0 : 1;
    if (ab !== bb) return ab - bb;
    return _agentPanelClassDisplayName(a, a && a.id)
      .localeCompare(_agentPanelClassDisplayName(b, b && b.id));
  });
  return compatible;
}

function _agentPanelClassIsDefault(agent, classId) {
  classId = String(classId || '').trim();
  return !!(classId && classId === _agentPanelClassDefaultIdForKind(_agentPanelKind(agent)));
}

function _agentPanelClassEffectiveLabel(agent) {
  var state = _agentPanelClassState(agent);
  return state.effectiveLabel || state.effectiveId || _agentPanelKindDisplayLabel(state.kind);
}

function _agentPanelPrimaryClassIdentity(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return null;
  var state = _agentPanelClassState(agent);
  if (!state.effectiveId || state.effectiveId === state.defaultId) return null;
  var label = String(state.effectiveLabel || state.effectiveId || '').trim();
  if (!label) return null;
  return {
    label: label,
    secondary: state.secondaryLabel || _agentPanelKindDisplayLabel(state.kind),
    kind: state.kind,
  };
}

function _agentPanelRoleTitle(agent, roleLabel) {
  var group = String((agent && agent.group) || '').trim() || '—';
  var identity = _agentPanelPrimaryClassIdentity(agent);
  if (identity) {
    return identity.label + ' · Group: ' + group;
  }
  return roleLabel + ': ' + ((agent && (agent.name || agent.id)) || 'Unknown') + ' · Group: ' + group;
}

function _agentPanelClassState(agent) {
  agent = agent || {};
  var kind = _agentPanelKind(agent);
  var defaultId = _agentPanelClassDefaultIdForKind(kind);
  var status = (agent.agent_class_status && typeof agent.agent_class_status === 'object')
    ? agent.agent_class_status
    : {};
  var snapshot = _agentPanelClassSnapshot(agent);
  var assignedId = String(
    agent.agent_class_id
    || status.assigned_class_id
    || ''
  ).trim();
  var assignedVersion = String(
    agent.agent_class_version
    || status.assigned_class_version
    || ''
  ).trim();
  var effectiveId = String(
    agent.effective_agent_class_id
    || snapshot.id
    || status.effective_class_id
    || defaultId
    || ''
  ).trim();
  var effectiveVersion = String(
    agent.effective_agent_class_version
    || snapshot.version
    || status.effective_class_version
    || ''
  ).trim();
  var assignedPreview = (status.assigned_class && typeof status.assigned_class === 'object')
    ? status.assigned_class
    : _agentPanelClassPreviewFor(agent, assignedId);
  var effectivePreview = snapshot.id
    ? snapshot
    : ((status.effective_class && typeof status.effective_class === 'object')
      ? status.effective_class
      : _agentPanelClassPreviewFor(agent, effectiveId));
  if (assignedPreview && assignedPreview.id) {
    _agentPanelClassPreviewById[String(assignedPreview.id || '')] = assignedPreview;
  }
  if (effectivePreview && effectivePreview.id) {
    _agentPanelClassPreviewById[String(effectivePreview.id || '')] = effectivePreview;
  }
  var desiredId = assignedId || defaultId;
  var desiredVersion = assignedId ? assignedVersion : '';
  var desiredPreview = assignedId
    ? (assignedPreview || _agentPanelClassPreviewFor(agent, assignedId))
    : _agentPanelClassPreviewFor(agent, defaultId);
  var effectivePreviewLabel = _agentPanelClassDisplayName(effectivePreview, '');
  var effectiveStatusLabel = String(
    status.effective_primary_identity_label
    || status.primary_identity_label
    || effectiveId
    || ''
  ).trim();
  var effectiveLabel = _agentPanelClassDisplayName(
    Object.assign({}, effectivePreview || {}, {
      id: effectiveId || (effectivePreview && effectivePreview.id) || '',
      primary_identity_label: effectivePreviewLabel || effectiveStatusLabel,
    }),
    effectiveStatusLabel || effectiveId || ''
  );
  if (effectiveId === defaultId && (!effectivePreview || !effectivePreview.id || effectiveLabel === effectiveId)) {
    effectiveLabel = 'Default ' + _agentPanelKindDisplayLabel(kind);
  }
  if (!effectiveLabel && effectiveId === defaultId) effectiveLabel = 'Default ' + _agentPanelKindDisplayLabel(kind);
  if (!effectiveLabel) effectiveLabel = effectiveId || _agentPanelKindDisplayLabel(kind);
  var desiredPreviewLabel = _agentPanelClassDisplayName(desiredPreview, '');
  var desiredStatusLabel = String(
    status.next_launch_primary_identity_label
    || status.assigned_primary_identity_label
    || desiredId
    || ''
  ).trim();
  var desiredLabel = _agentPanelClassDisplayName(
    Object.assign({}, desiredPreview || {}, {
      id: desiredId || (desiredPreview && desiredPreview.id) || '',
      primary_identity_label: desiredPreviewLabel || desiredStatusLabel,
    }),
    desiredStatusLabel || desiredId || ''
  );
  if (!assignedId && desiredId === defaultId
      && (!desiredPreview || !desiredPreview.id || desiredLabel === desiredId)) {
    desiredLabel = 'Default ' + _agentPanelKindDisplayLabel(kind);
  } else if (!assignedId && desiredId === defaultId) {
    desiredLabel = desiredLabel || ('Default ' + _agentPanelKindDisplayLabel(kind));
  }
  var secondaryLabel = _agentPanelClassSecondaryLabel(effectivePreview || {}, kind);
  var computedPending = !!(
    desiredId
    && (
      desiredId !== effectiveId
      || (desiredVersion && desiredVersion !== effectiveVersion)
    )
  );
  var pending = typeof status.pending_next_launch === 'boolean'
    ? !!(status.pending_next_launch && computedPending)
    : computedPending;
  var effectiveStatus = (effectivePreview && effectivePreview.id)
    ? _agentPanelClassStatusLabel(effectivePreview || {})
    : '';
  return {
    kind: kind,
    defaultId: defaultId,
    assignedId: assignedId,
    assignedVersion: assignedVersion,
    desiredId: desiredId,
    desiredVersion: desiredVersion,
    desiredLabel: desiredLabel,
    effectiveId: effectiveId,
    effectiveVersion: effectiveVersion,
    effectiveLabel: effectiveLabel,
    effectivePreview: effectivePreview || {},
    assignedPreview: assignedPreview || {},
    desiredPreview: desiredPreview || {},
    secondaryLabel: secondaryLabel,
    status: String(effectiveStatus || status.status || '').trim() || 'full',
    warnings: Array.isArray((effectivePreview || {}).warnings) ? effectivePreview.warnings : (Array.isArray(status.warnings) ? status.warnings : []),
    externalConnectorCaveat: String((effectivePreview || {}).external_connector_caveat || status.external_connector_caveat || '').trim(),
    pending: pending,
    statusObject: status,
  };
}

function _agentPanelClassBadgeIntent(status, pending) {
  status = String(status || '').trim().toLowerCase();
  if (pending) return 'warning';
  if (status === 'full' || status === 'active') return 'success';
  if (status === 'draft' || status === 'restricted' || status === 'archived') return 'warning';
  if (status === 'invalid' || status === 'error') return 'danger';
  return 'neutral';
}

function _agentPanelClassMetadataBadgeClass(localClass, intent) {
  var classes = String(localClass || '').trim();
  if (classes) classes += ' ';
  return classes + 'ui-badge ui-badge--compact ui-badge--' + (intent || 'neutral');
}

function _agentPanelClassBadgeHtml(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return '';
  var state = _agentPanelClassState(agent);
  var effectiveId = state.effectiveId;
  if (!effectiveId && !state.assignedId && !state.desiredId) return '';
  var version = state.effectiveVersion;
  var status = state.status || 'full';
  var statusClass = status.replace(/[^a-z0-9_-]/gi, '-').toLowerCase() || 'full';
  var localClasses = 'agent-profile-badge agent-class-badge agent-profile-badge-' + _agentPanelEsc(statusClass);
  if (state.pending) localClasses += ' agent-profile-badge-pending';
  if (status === 'draft' || status === 'restricted' || status === 'archived') localClasses += ' agent-profile-badge-warning';
  var classes = _agentPanelClassMetadataBadgeClass(
    localClasses,
    _agentPanelClassBadgeIntent(status, state.pending)
  );
  var label = state.effectiveLabel || effectiveId || state.desiredLabel || 'Agent Class';
  if (version && !_agentPanelClassIsDefault(agent, effectiveId)) label += _agentPanelClassVersionSuffix(version);
  if (state.pending) label += ' (pending)';
  var titleParts = [
    'Agent Class: ' + (effectiveId || '—') + _agentPanelClassVersionSuffix(version),
    'primary identity: ' + (state.effectiveLabel || '—'),
    'base kind: ' + (state.kind || '—'),
    'secondary metadata: ' + (state.secondaryLabel || '—'),
    'status: ' + status,
  ];
  if (state.desiredLabel) {
    titleParts.push('desired next launch: ' + state.desiredLabel + _agentPanelClassVersionSuffix(state.desiredVersion));
  }
  var snapshot = _agentPanelClassSnapshot(agent);
  var badgeWarnings = _agentPanelClassUniqueWarnings(snapshot);
  for (var i = 0; i < badgeWarnings.length && i < 3; i++) {
    titleParts.push(String(badgeWarnings[i] || ''));
  }
  return '<span class="' + classes + '" title="' + _agentPanelEsc(titleParts.join('\n')) + '">'
    + _agentPanelEsc(label)
    + '</span>';
}

function _agentPanelClassAssignmentSignature(agent) {
  if (!agent) return '';
  var status = agent.agent_class_status && typeof agent.agent_class_status === 'object'
    ? agent.agent_class_status
    : {};
  return [
    agent.kind || '',
    agent.agent_class_id || '',
    agent.agent_class_version || '',
    agent.effective_agent_class_id || '',
    agent.effective_agent_class_version || '',
    status.assigned_class_id || '',
    status.effective_class_id || '',
    status.pending_next_launch === true ? 'pending' : '',
    agent.status || '',
  ].join('|');
}

function _agentPanelClassUi(agent) {
  var agentId = String((agent && agent.id) || '').trim();
  if (!agentId) return {
    open: false,
    selectedClassId: '',
    dirty: false,
    saving: false,
    error: '',
    message: '',
  };
  var ui = _agentPanelClassManagerByAgent[agentId];
  if (!ui) {
    ui = {
      open: false,
      selectedClassId: undefined,
      dirty: false,
      saving: false,
      statusRequested: false,
      statusLoading: false,
      error: '',
      message: '',
      boundSignature: '',
    };
    _agentPanelClassManagerByAgent[agentId] = ui;
  }
  var signature = _agentPanelClassAssignmentSignature(agent);
  if (ui.selectedClassId === undefined
      || (ui.boundSignature !== signature && !ui.dirty && !ui.saving)) {
    var changed = ui.boundSignature !== signature;
    ui.selectedClassId = String(agent.agent_class_id || '').trim();
    ui.boundSignature = signature;
    ui.error = '';
    if (changed && ui.message && !_agentPanelClassState(agent).pending) {
      ui.message = '';
    }
  }
  return ui;
}

function _agentPanelRefreshClassManagerRender() {
  _agentPanelRenderClassModal();
  if (typeof _agentSettingsRefreshClassHost === 'function') {
    _agentSettingsRefreshClassHost();
  }
  if (typeof _agentPanelRefreshCurrentTab === 'function'
      && _agentPanelRefreshCurrentTab()) return;
  if (typeof renderAgentPanel === 'function') renderAgentPanel();
}

function _agentPanelRequestClassList(agent, force) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return;
  var cache = _agentPanelClassListCache(agent);
  if (!force && (cache.loading || cache.requested)) return;
  cache.loading = true;
  cache.requested = true;
  cache.error = '';
  cache.baseDir = _agentPanelClassBaseDir(agent);
  _agentPanelClassLastRequestedListKey = cache.key;
  if (typeof send === 'function') {
    send({
      cmd: 'agent_class_list',
      base_dir: cache.baseDir,
    });
  }
}

function _agentPanelRequestClassStatus(agent, force) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return;
  var ui = _agentPanelClassUi(agent);
  if (!force && (ui.statusLoading || ui.statusRequested)) return;
  ui.statusLoading = true;
  ui.statusRequested = true;
  if (typeof send === 'function') {
    send({
      cmd: 'agent_class_status',
      agent_id: String(agent.id || ''),
      base_dir: _agentPanelClassBaseDir(agent),
    });
  }
}

function _agentPanelEnsureOpenClassManagerData(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return;
  var ui = _agentPanelClassUi(agent);
  if (!ui.open) return;
  _agentPanelRequestClassList(agent, false);
  _agentPanelRequestClassStatus(agent, false);
}

function _agentPanelClassModalOverlay() {
  if (typeof document === 'undefined' || !document || typeof document.getElementById !== 'function') return null;
  return document.getElementById('modal-agent-class');
}

function _agentPanelClassModalBodyEl() {
  if (typeof document === 'undefined' || !document || typeof document.getElementById !== 'function') return null;
  return document.getElementById('agent-class-modal-body');
}

function _agentPanelClassModalSummaryEl() {
  if (typeof document === 'undefined' || !document || typeof document.getElementById !== 'function') return null;
  return document.getElementById('agent-class-modal-summary');
}

function _agentPanelClassModalTitle(agent) {
  var name = _agentPanelAgentDisplayName(agent, 'Agent');
  var state = _agentPanelClassState(agent);
  var effective = _agentPanelClassEffectiveLabelWithVersion(state);
  return name + ' · ' + effective;
}

function _agentPanelRenderClassModal() {
  if (!_agentPanelClassModalAgentId) return false;
  var agent = _agentPanelAgentForId(_agentPanelClassModalAgentId);
  if (!agent) {
    _agentPanelClassModalAgentId = '';
    return false;
  }
  var body = _agentPanelClassModalBodyEl();
  if (!body) return false;
  body.innerHTML = _agentPanelClassModalBodyHtml(agent);
  var summary = _agentPanelClassModalSummaryEl();
  if (summary) summary.textContent = _agentPanelClassModalTitle(agent);
  return true;
}

function _agentPanelOpenClassAssignmentModal(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return false;
  var agentId = String(agent.id || '');
  if (!agentId) return false;
  _agentPanelClassModalAgentId = agentId;
  var ui = _agentPanelClassUi(agent);
  ui.open = true;
  ui.error = '';
  _agentPanelEnsureOpenClassManagerData(agent);
  _agentPanelRenderClassModal();
  var modal = _agentPanelClassModalOverlay();
  if (modal) {
    if (typeof openModalDialog === 'function') {
      openModalDialog(modal, {
        label: 'Change Agent Class',
        initialFocus: '#agent-class-select-' + _agentPanelDomIdToken(agentId),
      });
    } else if (modal.classList && typeof modal.classList.add === 'function') {
      modal.classList.add('visible');
    }
  }
  return true;
}

function agentPanelOpenClassAssignment(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _agentPanelAgentForId(agentId) || _resolveFocusedAgent();
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return false;
  _agentPanelOpenClassAssignmentModal(agent);
  _agentPanelRefreshClassManagerRender();
  return false;
}

function agentPanelToggleClassAssignment(evt, agentId) {
  return agentPanelOpenClassAssignment(evt, agentId);
}

function agentPanelCloseClassAssignmentModal(evt) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _agentPanelAgentForId(_agentPanelClassModalAgentId);
  if (agent) {
    var ui = _agentPanelClassUi(agent);
    ui.open = false;
  }
  _agentPanelClassModalAgentId = '';
  var modal = _agentPanelClassModalOverlay();
  if (modal) {
    if (typeof closeModalDialog === 'function') {
      closeModalDialog(modal, { restoreFocus: true });
    } else if (modal.classList && typeof modal.classList.remove === 'function') {
      modal.classList.remove('visible');
    }
  }
  _agentPanelRefreshClassManagerRender();
  return false;
}

function agentPanelRefreshClasses(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _agentPanelAgentForId(agentId) || _resolveFocusedAgent();
  if (!agent) return false;
  var ui = _agentPanelClassUi(agent);
  ui.open = true;
  ui.error = '';
  _agentPanelRequestClassList(agent, true);
  _agentPanelRequestClassStatus(agent, true);
  _agentPanelRefreshClassManagerRender();
  return false;
}

function agentPanelSelectClass(agentId, classId) {
  var agent = _agentPanelAgentForId(agentId) || _resolveFocusedAgent();
  if (!agent) return false;
  var ui = _agentPanelClassUi(agent);
  ui.open = true;
  ui.selectedClassId = String(classId || '').trim();
  ui.dirty = true;
  ui.error = '';
  ui.message = '';
  _agentPanelRefreshClassManagerRender();
  return false;
}

function _agentPanelClassSelectionState(agent, ui) {
  ui = ui || _agentPanelClassUi(agent);
  var selected = String(ui.selectedClassId || '').trim();
  var kind = _agentPanelKind(agent);
  if (!selected) {
    return {
      ok: true,
      defaultSelected: true,
      selectedId: '',
      kind: kind,
      reason: '',
      item: null,
    };
  }
  var cache = _agentPanelClassListCache(agent);
  if (cache.loading && !cache.requested) {
    return {
      ok: false,
      defaultSelected: false,
      selectedId: selected,
      kind: kind,
      reason: 'Loading Agent Classes before assignment.',
      item: null,
    };
  }
  var item = _agentPanelClassPreviewFor(agent, selected);
  if (!item) {
    return {
      ok: false,
      defaultSelected: false,
      selectedId: selected,
      kind: kind,
      reason: 'Selected Agent Class is no longer available. Choose another class or Default (no explicit Agent Class).',
      item: null,
    };
  }
  var reason = _agentPanelClassLaunchDisabledReason(item, kind || item.base_kind);
  return {
    ok: !reason,
    defaultSelected: false,
    selectedId: selected,
    kind: kind || String(item.base_kind || ''),
    reason: reason || '',
    item: item,
  };
}

function _agentPanelClassAssignmentDisabledReason(agent, ui) {
  ui = ui || {};
  if (ui.saving) return 'Saving assignment…';
  var selection = _agentPanelClassSelectionState(agent, ui);
  if (!selection.ok) return selection.reason || 'Choose a valid Agent Class.';
  var selected = String(ui.selectedClassId || '').trim();
  var assigned = String((agent && agent.agent_class_id) || '').trim();
  var assignedVersion = String((agent && agent.agent_class_version) || '').trim();
  if (selected) {
    var item = selection.item || {};
    var version = String(item.version || '').trim();
    if (selected === assigned && (!version || !assignedVersion || version === assignedVersion)) {
      return 'Desired Agent Class is already set.';
    }
    return '';
  }
  if (!assigned) return 'Default/no explicit Agent Class is already desired.';
  return '';
}

function agentPanelAssignSelectedClass(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _agentPanelAgentForId(agentId) || _resolveFocusedAgent();
  if (!agent) return false;
  var ui = _agentPanelClassUi(agent);
  var selection = _agentPanelClassSelectionState(agent, ui);
  if (!selection.ok) {
    ui.open = true;
    ui.error = selection.reason || 'Choose a valid Agent Class.';
    if (typeof _showToast === 'function') _showToast(ui.error, 'error');
    _agentPanelRefreshClassManagerRender();
    return false;
  }
  var selected = String(ui.selectedClassId || '').trim();
  ui.open = true;
  ui.saving = true;
  ui.error = '';
  ui.message = '';
  if (typeof send === 'function') {
    var payload = {
      cmd: selected ? 'agent_class_assign' : 'agent_class_clear',
      agent_id: String(agent.id || ''),
      actor_label: 'trusted-user-ui',
      base_dir: _agentPanelClassBaseDir(agent),
    };
    if (selected) payload.class_id = selected;
    send(payload);
  }
  _agentPanelRefreshClassManagerRender();
  return false;
}

function agentPanelClearClassAssignment(evt, agentId) {
  if (evt && typeof evt.preventDefault === 'function') evt.preventDefault();
  if (evt && typeof evt.stopPropagation === 'function') evt.stopPropagation();
  var agent = _agentPanelAgentForId(agentId) || _resolveFocusedAgent();
  if (!agent) return false;
  var ui = _agentPanelClassUi(agent);
  ui.selectedClassId = '';
  ui.dirty = true;
  return agentPanelAssignSelectedClass(evt, agentId);
}

function agentPanelReceiveAgentClasses(msg) {
  msg = msg || {};
  var classes = Array.isArray(msg.classes) ? msg.classes : [];
  var issues = Array.isArray(msg.issues) ? msg.issues : [];
  for (var classIndex = 0; classIndex < classes.length; classIndex++) {
    var item = classes[classIndex] || {};
    if (item.id) _agentPanelClassPreviewById[String(item.id || '')] = item;
  }
  var updated = false;
  for (var key in _agentPanelClassListByKey) {
    if (!Object.prototype.hasOwnProperty.call(_agentPanelClassListByKey, key)) continue;
    var cache = _agentPanelClassListByKey[key];
    if (!cache || (!cache.loading && key !== _agentPanelClassLastRequestedListKey)) continue;
    cache.classes = classes.slice();
    cache.issues = issues.slice();
    cache.loading = false;
    cache.requested = true;
    cache.error = '';
    updated = true;
  }
  if (!updated) {
    var fallbackKey = _agentPanelClassLastRequestedListKey || '__default__';
    var fallback = _agentPanelClassListByKey[fallbackKey] || {
      key: fallbackKey,
      baseDir: '',
      classes: [],
      issues: [],
      requested: false,
      loading: false,
      error: '',
    };
    fallback.classes = classes.slice();
    fallback.issues = issues.slice();
    fallback.loading = false;
    fallback.requested = true;
    fallback.error = '';
    _agentPanelClassListByKey[fallbackKey] = fallback;
  }
  _agentPanelRefreshClassManagerRender();
}

function agentPanelReceiveAgentClassPreview(msg) {
  msg = msg || {};
  var item = msg.agent_class && typeof msg.agent_class === 'object' ? msg.agent_class : null;
  if (!item || !item.id) return;
  _agentPanelClassPreviewById[String(item.id || '')] = item;
  _agentPanelRefreshClassManagerRender();
}

function agentPanelReceiveAgentClassStatus(msg) {
  msg = msg || {};
  var status = msg.status && typeof msg.status === 'object' ? msg.status : {};
  var agentId = String(status.agent_id || status.id || '').trim();
  var agent = agentId ? _agentPanelAgentForId(agentId) : _resolveFocusedAgent();
  if (agent) agent.agent_class_status = status;
  if (status.assigned_class && status.assigned_class.id) {
    _agentPanelClassPreviewById[String(status.assigned_class.id || '')] = status.assigned_class;
  }
  if (status.effective_class && status.effective_class.id) {
    _agentPanelClassPreviewById[String(status.effective_class.id || '')] = status.effective_class;
  }
  var ui = _agentPanelClassUi(agent);
  ui.statusLoading = false;
  ui.statusRequested = true;
  ui.boundSignature = agent ? _agentPanelClassAssignmentSignature(agent) : ui.boundSignature;
  _agentPanelRefreshClassManagerRender();
}

function agentPanelReceiveAgentClassAssignment(msg) {
  msg = msg || {};
  var status = msg.status && typeof msg.status === 'object' ? msg.status : {};
  var agentId = String(status.agent_id || status.id || '').trim();
  var agent = agentId ? _agentPanelAgentForId(agentId) : _resolveFocusedAgent();
  if (agent) {
    agent.agent_class_id = String(status.assigned_class_id || '').trim();
    agent.agent_class_version = String(status.assigned_class_version || '').trim();
    agent.agent_class_assigned_at = Number(status.assigned_at || agent.agent_class_assigned_at || 0) || 0;
    agent.agent_class_assigned_by = String(status.assigned_by || agent.agent_class_assigned_by || '').trim();
    agent.agent_class_status = status;
  }
  if (status.assigned_class && status.assigned_class.id) {
    _agentPanelClassPreviewById[String(status.assigned_class.id || '')] = status.assigned_class;
  }
  if (status.effective_class && status.effective_class.id) {
    _agentPanelClassPreviewById[String(status.effective_class.id || '')] = status.effective_class;
  }
  var ui = _agentPanelClassUi(agent);
  ui.saving = false;
  ui.dirty = false;
  ui.error = '';
  ui.statusLoading = false;
  ui.statusRequested = true;
  ui.message = status.pending_next_launch
    ? 'Desired Agent Class updated. It will freeze on the next launch or relaunch.'
    : 'Desired Agent Class updated.';
  ui.selectedClassId = String(status.assigned_class_id || '').trim();
  ui.boundSignature = agent ? _agentPanelClassAssignmentSignature(agent) : ui.boundSignature;
  if (typeof _showToast === 'function') _showToast(ui.message, 'success');
  _agentPanelRefreshClassManagerRender();
}

function agentPanelHandleAgentClassError(msg) {
  var message = String((msg && (msg.message || msg.error)) || '').trim();
  if (!message) return false;
  var handled = false;
  for (var agentId in _agentPanelClassManagerByAgent) {
    if (!Object.prototype.hasOwnProperty.call(_agentPanelClassManagerByAgent, agentId)) continue;
    var ui = _agentPanelClassManagerByAgent[agentId];
    if (!ui) continue;
    if (ui.saving || ui.statusLoading) {
      ui.saving = false;
      ui.statusLoading = false;
      ui.error = message;
      handled = true;
    }
  }
  for (var key in _agentPanelClassListByKey) {
    if (!Object.prototype.hasOwnProperty.call(_agentPanelClassListByKey, key)) continue;
    var cache = _agentPanelClassListByKey[key];
    if (cache && cache.loading) {
      cache.loading = false;
      cache.error = message;
      handled = true;
    }
  }
  if (!handled) return false;
  if (typeof _showToast === 'function') _showToast(message, 'error');
  _agentPanelRefreshClassManagerRender();
  return true;
}


function _agentPanelClassMetaLine(label, value, extraClass) {
  value = String(value || '').trim();
  if (!value) value = '—';
  return '<div class="agent-profile-meta-line' + (extraClass ? ' ' + _agentPanelAttr(extraClass) : '') + '">'
    + '<span class="agent-profile-meta-label">' + _agentPanelEsc(label) + '</span>'
    + '<span class="agent-profile-meta-value">' + _agentPanelEsc(value) + '</span>'
    + '</div>';
}

function _agentPanelClassDesiredLabelWithVersion(state) {
  state = state || {};
  var label = String(state.desiredLabel || '').trim();
  if (!label && state.desiredId) label = state.desiredId;
  if (!label) label = 'Default (no explicit Agent Class)';
  if (state.desiredVersion && label.indexOf('@' + state.desiredVersion) < 0) {
    label += _agentPanelClassVersionSuffix(state.desiredVersion);
  }
  return label;
}

function _agentPanelClassEffectiveLabelWithVersion(state) {
  state = state || {};
  var label = String(state.effectiveLabel || state.effectiveId || '').trim() || '—';
  if (state.effectiveVersion
      && label.indexOf('@' + state.effectiveVersion) < 0
      && !/^Default /.test(label)) {
    label += _agentPanelClassVersionSuffix(state.effectiveVersion);
  }
  return label;
}

function _agentPanelClassPendingLabel(state, effectiveLabel, desiredLabel, agent) {
  state = state || {};
  if (!state.pending) return 'No — running session already matches desired Agent Class';
  var assignedAt = Number((agent && agent.agent_class_assigned_at) || 0) || 0;
  var appliedAt = Number((agent && agent.effective_agent_class_applied_at) || 0) || 0;
  var effective = String(effectiveLabel || state.effectiveLabel || state.effectiveId || '—').trim() || '—';
  var desired = String(desiredLabel || state.desiredLabel || state.desiredId || '—').trim() || '—';
  if (assignedAt > 0 && appliedAt >= assignedAt) {
    return 'Yes — last launch froze ' + effective + ', which does not match desired ' + desired;
  }
  if (state.desiredId && state.effectiveId && state.desiredId !== state.effectiveId) {
    return 'Yes — running session keeps ' + effective + '; next relaunch freezes ' + desired;
  }
  if (state.desiredVersion) {
    return 'Yes — effective version '
      + (state.effectiveVersion || 'unknown')
      + ' differs from desired version '
      + state.desiredVersion;
  }
  return 'Yes — applies on next launch/relaunch';
}

function _agentPanelClassAssignmentStatusHtml(agent) {
  var state = _agentPanelClassState(agent);
  var effectiveLabel = _agentPanelClassEffectiveLabelWithVersion(state);
  var desiredLabel = _agentPanelClassDesiredLabelWithVersion(state);
  var html = '<div class="agent-profile-status-grid agent-class-status-grid">';
  html += _agentPanelClassMetaLine('Primary identity now', effectiveLabel);
  html += _agentPanelClassMetaLine('Desired Agent Class next launch', desiredLabel);
  html += _agentPanelClassMetaLine('Base kind metadata', state.secondaryLabel || _agentPanelKindDisplayLabel(state.kind));
  html += _agentPanelClassMetaLine('Lifecycle/status', state.status || 'full');
  html += _agentPanelClassMetaLine(
    'Pending relaunch',
    _agentPanelClassPendingLabel(state, effectiveLabel, desiredLabel, agent),
    state.pending ? 'agent-profile-meta-pending' : ''
  );
  if (agent && agent.agent_class_assigned_by) {
    html += _agentPanelClassMetaLine('Assigned by', agent.agent_class_assigned_by);
  }
  if (agent && agent.agent_class_assigned_at) {
    html += _agentPanelClassMetaLine('Assigned at', _agentPanelTimestamp(agent.agent_class_assigned_at));
  }
  if (agent && agent.effective_agent_class_applied_at) {
    html += _agentPanelClassMetaLine('Effective frozen', _agentPanelTimestamp(agent.effective_agent_class_applied_at));
  }
  html += '</div>';
  return html;
}

function _agentPanelClassLaunchGuidanceHtml(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return '';
  var status = String(agent.status || '').trim();
  var agentId = String(agent.id || '');
  if (status === 'stopped') {
    return '<div class="agent-profile-launch-guidance agent-profile-launch-guidance-stopped agent-class-launch-guidance">'
      + '<span>Agent is stopped; relaunch when you are ready to apply the desired Agent Class.</span>'
      + '<button type="button" class="agent-profile-secondary-btn"'
      + ' onclick="' + _agentPanelEventAttr('event.stopPropagation();relaunchAgent('
        + _agentPanelJsString(agentId) + ')') + '">Relaunch to apply</button>'
      + '</div>';
  }
  return '<div class="agent-profile-launch-guidance agent-class-launch-guidance">'
    + 'Agent is running; this UI will not stop or relaunch it. The desired Agent Class freezes on the next natural launch/relaunch.'
    + '</div>';
}

function _agentPanelClassOptionsHtml(agent, ui) {
  var kind = _agentPanelKind(agent);
  var defaultId = _agentPanelClassDefaultIdForKind(kind);
  var selected = String((ui && ui.selectedClassId) || '').trim();
  var selection = _agentPanelClassSelectionState(agent, ui);
  var html = '<option value=""' + (!selected ? ' selected' : '') + '>'
    + 'Default (no explicit Agent Class)'
    + (defaultId ? (' — ' + _agentPanelEsc(defaultId)) : '')
    + '</option>';
  var classes = _agentPanelClassCompatibleClasses(agent);
  var sawSelected = false;
  for (var i = 0; i < classes.length; i++) {
    var item = classes[i] || {};
    var classId = String(item.id || '').trim();
    if (!classId) continue;
    if (selected === classId) sawSelected = true;
    var reason = _agentPanelClassLaunchDisabledReason(item, kind);
    var label = _agentPanelClassDisplayName(item, classId)
      + _agentPanelClassVersionSuffix(item.version)
      + ' · ' + _agentPanelClassSecondaryLabel(item, kind)
      + ' · ' + _agentPanelClassStatusLabel(item);
    if (reason) label += ' (disabled)';
    html += '<option value="' + _agentPanelAttr(classId) + '"'
      + (selected === classId ? ' selected' : '')
      + (reason ? ' disabled' : '')
      + '>' + _agentPanelEsc(label) + '</option>';
  }
  if (selected && !sawSelected) {
    var staleReason = selection && selection.reason ? (' — ' + selection.reason) : '';
    html += '<option value="' + _agentPanelAttr(selected) + '" selected disabled>Previously selected: '
      + _agentPanelEsc(selected + staleReason)
      + '</option>';
  }
  return html;
}

function _agentPanelClassSelectionHint(agent, selection) {
  selection = selection || _agentPanelClassSelectionState(agent, _agentPanelClassUi(agent));
  var kind = _agentPanelKind(agent);
  if (!selection.selectedId) {
    return 'No explicit class selected: existing default launch behavior is preserved and Torque freezes '
      + (_agentPanelClassDefaultIdForKind(kind) || ('default-' + kind))
      + ' at launch.';
  }
  if (!selection.ok) return selection.reason || 'Choose a launchable Agent Class or Default (no explicit Agent Class).';
  var item = selection.item || {};
  return 'Next relaunch freezes '
    + _agentPanelClassDisplayName(item, selection.selectedId)
    + _agentPanelClassVersionSuffix(item.version)
    + ' as the primary identity; '
    + _agentPanelClassSecondaryLabel(item, kind)
    + ' remains secondary/base-kind metadata'
    + '.';
}

function _agentPanelClassIssuesHtml(issues) {
  issues = issues || [];
  if (!issues.length) return '';
  var html = '<div class="agent-profile-issues agent-class-issues">';
  for (var i = 0; i < Math.min(issues.length, 4); i++) {
    var issue = issues[i] || {};
    var text = typeof issue === 'string'
      ? issue
      : String((issue.severity || 'issue') + ': ' + (issue.message || issue.code || 'Agent Class validation issue'));
    html += '<div class="agent-profile-issue">' + _agentPanelEsc(text) + '</div>';
  }
  if (issues.length > 4) html += '<div class="agent-profile-issue">+' + (issues.length - 4) + ' more validation issues</div>';
  html += '</div>';
  return html;
}

function _agentPanelClassWarningsHtml(item) {
  item = item || {};
  var warnings = _agentPanelClassUniqueWarnings(item);
  var html = '';
  if (warnings.length) {
    html += '<ul class="agent-profile-warning-list agent-class-warning-list">';
    for (var i = 0; i < warnings.length; i++) {
      html += '<li>' + _agentPanelEsc(warnings[i]) + '</li>';
    }
    html += '</ul>';
  }
  var caveat = _agentPanelClassConnectorCaveat(item);
  if (caveat) {
    html += '<div class="agent-profile-scratch-warning agent-class-caveat">'
      + _agentPanelEsc(caveat)
      + '</div>';
  }
  return html;
}

function _agentPanelClassBucketLabel(bucket, fallback) {
  bucket = bucket || {};
  var id = String(bucket.id || '').trim();
  if (id === 'deny_raw_tool_picker') return 'No arbitrary tool selection';
  if (id === 'deny_high_risk_operations') return 'No powerful actions beyond this class';
  return _agentPanelClassPlainPermissionCopy(bucket.label || bucket.display_name || bucket.name || id || fallback || '');
}

function _agentPanelClassBucketSummary(bucket) {
  bucket = bucket || {};
  var id = String(bucket.id || '').trim();
  if (id === 'deny_raw_tool_picker') return 'Keeps arbitrary tool selection outside this class.';
  if (id === 'deny_high_risk_operations') return 'Blocks powerful or critical actions that are not explicitly allowed here.';
  return _agentPanelClassPlainPermissionCopy(bucket.summary || bucket.description || '');
}

function _agentPanelClassPlainPermissionCopy(text) {
  text = String(text || '').trim();
  if (!text) return '';
  return text
    .replace(/\bDeny raw tool picker\b/gi, 'No arbitrary tool selection')
    .replace(/\braw tool picker authority\b/gi, 'arbitrary tool selection')
    .replace(/\braw tool picker\b/gi, 'arbitrary tool selection')
    .replace(/\braw tools\b/gi, 'arbitrary tool access')
    .replace(/\bDeny remaining high-risk operations\b/gi, 'No powerful actions beyond this class')
    .replace(/\bhigh-risk\/critical operations\b/gi, 'powerful or critical actions')
    .replace(/\bhigh-risk operations\b/gi, 'powerful actions')
    .replace(/\bhigh-risk\b/gi, 'powerful')
    .replace(/\bcapability buckets?\b/gi, 'allowed actions')
    .replace(/\brestriction buckets?\b/gi, 'limits')
    .replace(/\bbuckets?\b/gi, 'actions')
    .replace(/\bMCP call telemetry\b/gi, 'tool activity history')
    .replace(/\bMCP calls?\b/gi, 'tool activity')
    .replace(/\bMCP\b/gi, 'tool')
    .replace(/\braw atoms?\b/gi, 'low-level permissions')
    .replace(/\bcompiler\b/gi, 'internal validation')
    .replace(/\bcompile\b/gi, 'validate');
}

function _agentPanelClassDedupeStrings(values) {
  var out = [];
  var seen = {};
  values = Array.isArray(values) ? values : [];
  for (var i = 0; i < values.length; i++) {
    var text = String(values[i] || '').trim();
    if (!text || seen[text]) continue;
    seen[text] = true;
    out.push(text);
  }
  return out;
}

function _agentPanelClassOperatorAccessHtml(item) {
  item = item || {};
  var acl = item.acl && typeof item.acl === 'object' ? item.acl : {};
  var mode = String(acl.mode || 'allow').trim() === 'deny' ? 'deny' : 'allow';
  var rules = Array.isArray(acl.rules) ? acl.rules : [];
  var entries = rules.map(function(rule) {
    rule = rule || {};
    var capability = String(rule.capability || '').trim();
    if (!capability) return '';
    var scope = String(rule.scope || '').trim();
    return scope ? capability + ' (' + scope + ')' : capability;
  }).filter(Boolean);
  var allowedSummary = mode === 'allow'
    ? (entries.length ? entries.join('; ') : 'No capabilities selected.')
    : 'All base-kind capabilities except the listed denials.';
  var deniedSummary = mode === 'allow'
    ? 'Everything not listed is denied by default.'
    : (entries.length ? entries.join('; ') : 'No explicit denials.');
  var html = '<div class="agent-class-operator-access">';
  html += '<div class="agent-class-block-title">Effective ACL</div>';
  html += '<div class="agent-class-access-summary-grid">';
  html += '<div><span>Mode</span><strong>' + _agentPanelEsc(mode) + '</strong></div>';
  html += '<div><span>Allowed</span><strong>' + _agentPanelEsc(allowedSummary) + '</strong></div>';
  html += '<div><span>Denied</span><strong>' + _agentPanelEsc(deniedSummary) + '</strong></div>';
  html += '</div></div>';
  return html;
}


function _agentPanelClassApplyStateHtml(item) {
  item = item || {};
  var apply = item.apply_state && typeof item.apply_state === 'object' ? item.apply_state : {};
  var mutatesRunning = apply.mutates_running_sessions === true;
  var relaunchRequired = apply.relaunch_required_after_assignment !== false;
  var appliesAt = String(apply.applies_at || '').trim();
  var status = mutatesRunning ? 'May affect running sessions immediately.' : 'Does not change running sessions.';
  var when = appliesAt === 'next_launch_or_relaunch' || relaunchRequired
    ? 'Access freezes on the next launch or relaunch.'
    : (appliesAt ? appliesAt.replace(/_/g, ' ') : 'Next launch/relaunch.');
  return '<div class="agent-class-apply-state">'
    + '<div><span>Apply state</span><strong>' + _agentPanelEsc(status) + '</strong></div>'
    + '<div><span>Relaunch behavior</span><strong>' + _agentPanelEsc(when) + '</strong></div>'
    + '</div>';
}

function _agentPanelClassPreviewHtml(agent, ui) {
  var selection = _agentPanelClassSelectionState(agent, ui);
  var state = _agentPanelClassState(agent);
  var item = selection.item || state.desiredPreview || state.effectivePreview || {};
  if (!selection.selectedId && state.defaultId) {
    item = _agentPanelClassPreviewFor(agent, state.defaultId) || item;
  }
  if (!item || !item.id) {
    return '<div class="agent-profile-preview agent-profile-preview-empty agent-class-preview">'
      + _agentPanelEsc(_agentPanelClassSelectionHint(agent, selection))
      + '</div>';
  }
  var status = _agentPanelClassStatusLabel(item);
  var statusClass = status.replace(/[^a-z0-9_-]/gi, '-').toLowerCase() || 'full';
  var html = '<div class="agent-profile-preview agent-class-preview agent-class-preview-' + _agentPanelAttr(statusClass) + '">';
  html += '<div class="agent-profile-preview-head">';
  html += '<div>';
  html += '<div class="agent-profile-preview-title">'
    + _agentPanelEsc(_agentPanelClassDisplayName(item, item.id))
    + _agentPanelEsc(_agentPanelClassVersionSuffix(item.version))
    + '</div>';
  html += '<div class="agent-profile-preview-description">'
    + _agentPanelEsc(item.purpose || item.description || item.id || '')
    + '</div>';
  html += '</div><div class="agent-profile-preview-chips agent-class-preview-chips">';
  html += '<span class="' + _agentPanelClassMetadataBadgeClass('agent-profile-chip', 'neutral') + '">'
    + _agentPanelEsc(_agentPanelClassSecondaryLabel(item, _agentPanelKind(agent))) + '</span>';
  html += '<span class="' + _agentPanelClassMetadataBadgeClass(
    'agent-profile-chip agent-profile-chip-' + _agentPanelAttr(statusClass),
    _agentPanelClassBadgeIntent(status, false)
  ) + '">' + _agentPanelEsc(status) + '</span>';
  var lifecycle = String(item.lifecycle || 'stable');
  var lifecycleIntent = lifecycle.toLowerCase() === 'draft' ? 'warning' : 'neutral';
  html += '<span class="' + _agentPanelClassMetadataBadgeClass('agent-profile-chip', lifecycleIntent) + '">'
    + _agentPanelEsc(lifecycle) + '</span>';
  if (item.scratch_only || (item.draft && item.draft.scratch_only)) {
    html += '<span class="' + _agentPanelClassMetadataBadgeClass(
      'agent-profile-chip agent-profile-chip-draft',
      'warning'
    ) + '">scratch-only</span>';
  }
  html += '</div></div>';
  html += '<div class="agent-profile-next-launch-note">'
    + _agentPanelEsc(_agentPanelClassSelectionHint(agent, selection))
    + '</div>';
  html += _agentPanelClassOperatorAccessHtml(item);
  html += _agentPanelClassApplyStateHtml(item);
  html += _agentPanelClassWarningsHtml(item);
  return html + '</div>';
}

function _agentPanelClassEffectiveNoticeSource(state) {
  state = state || {};
  var effectiveNoticeSource = state.effectivePreview && state.effectivePreview.id
    ? state.effectivePreview
    : {};
  if (state.warnings && state.warnings.length) {
    effectiveNoticeSource = Object.assign({}, effectiveNoticeSource, { warnings: state.warnings });
  }
  if (state.externalConnectorCaveat) {
    effectiveNoticeSource = Object.assign({}, effectiveNoticeSource, {
      external_connector_caveat: state.externalConnectorCaveat,
    });
  }
  return effectiveNoticeSource;
}

function _agentPanelClassManagerHtml(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return '';
  var kind = _agentPanelKind(agent);
  if (kind !== 'architect' && kind !== 'engineer' && kind !== 'worker') return '';
  var agentId = String(agent.id || '');
  var state = _agentPanelClassState(agent);
  var effectiveLabel = _agentPanelClassEffectiveLabelWithVersion(state);
  var desiredLabel = _agentPanelClassDesiredLabelWithVersion(state);
  var pendingLabel = _agentPanelClassPendingLabel(state, effectiveLabel, desiredLabel, agent);
  var ui = _agentPanelClassUi(agent);
  var html = '<section class="agent-profile-manager agent-class-manager agent-class-manager-compact collapsed"'
    + ' data-agent-class-manager="' + _agentPanelAttr(agentId) + '"'
    + ' data-agent-class-modal-open="' + (_agentPanelClassModalAgentId === agentId ? 'true' : 'false') + '">';
  html += '<div class="agent-profile-manager-head agent-class-manager-compact-head">';
  html += '<div>';
  html += '<div class="agent-profile-manager-title">Agent Class</div>';
  html += '<div class="agent-profile-manager-subtitle">Operator-facing identity and allowed actions; detailed changes open in a modal.</div>';
  html += '</div>';
  html += '<button type="button" class="agent-profile-secondary-btn agent-class-change-btn"'
    + ' title="Change desired Agent Class assignment"'
    + ' onclick="' + _agentPanelEventAttr('return agentPanelOpenClassAssignment(event,'
      + _agentPanelJsString(agentId) + ')') + '">Change Class</button>';
  html += '</div>';
  html += '<div class="agent-profile-status-grid agent-class-status-grid agent-class-status-grid-compact">';
  html += _agentPanelClassMetaLine('Primary identity now', effectiveLabel);
  html += _agentPanelClassMetaLine('Desired Agent Class next launch', desiredLabel);
  html += _agentPanelClassMetaLine('Base kind metadata', state.secondaryLabel || _agentPanelKindDisplayLabel(state.kind));
  html += _agentPanelClassMetaLine('Pending relaunch', pendingLabel, state.pending ? 'agent-profile-meta-pending' : '');
  html += '</div>';
  html += _agentPanelClassWarningsHtml(_agentPanelClassEffectiveNoticeSource(state));
  if (ui.message) html += '<div class="agent-profile-message">' + _agentPanelEsc(ui.message) + '</div>';
  if (ui.error) html += '<div class="agent-profile-error">' + _agentPanelEsc(ui.error) + '</div>';
  html += '</section>';
  return html;
}

function _agentPanelClassModalBodyHtml(agent) {
  if (!agent || String(agent.cell_type || 'agent') !== 'agent') return '';
  var kind = _agentPanelKind(agent);
  if (kind !== 'architect' && kind !== 'engineer' && kind !== 'worker') return '';
  var ui = _agentPanelClassUi(agent);
  ui.open = true;
  _agentPanelEnsureOpenClassManagerData(agent);
  var agentId = String(agent.id || '');
  var safeId = _agentPanelDomIdToken(agentId);
  var cache = _agentPanelClassListCache(agent);
  var selection = _agentPanelClassSelectionState(agent, ui);
  var disabledReason = _agentPanelClassAssignmentDisabledReason(agent, ui);
  var primaryLabel = String(ui.selectedClassId || '').trim()
    ? 'Set desired Agent Class'
    : 'Clear to default/no class';
  var html = '<section class="agent-profile-manager agent-class-manager agent-class-manager-modal open"'
    + ' data-agent-class-manager="' + _agentPanelAttr(agentId) + '">';
  html += '<div class="agent-profile-manager-head">';
  html += '<div>';
  html += '<div class="agent-profile-manager-title">Agent Class assignment</div>';
  html += '<div class="agent-profile-manager-subtitle">'
    + 'Agent Classes describe what an agent is for and which actions it can use. Running sessions keep their current access until launch/relaunch.'
    + '</div>';
  html += '</div>';
  html += '<button type="button" class="agent-profile-secondary-btn"'
    + ' onclick="' + _agentPanelEventAttr('return agentPanelCloseClassAssignmentModal(event)') + '">Close</button>';
  html += '</div>';
  html += _agentPanelClassAssignmentStatusHtml(agent);
  if (ui.message) html += '<div class="agent-profile-message">' + _agentPanelEsc(ui.message) + '</div>';
  if (ui.error) html += '<div class="agent-profile-error">' + _agentPanelEsc(ui.error) + '</div>';
  html += _agentPanelClassLaunchGuidanceHtml(agent);
  if (cache.error) html += '<div class="agent-profile-error">' + _agentPanelEsc(cache.error) + '</div>';
  html += '<div class="agent-profile-controls agent-class-controls">';
  html += '<label for="agent-class-select-' + _agentPanelAttr(safeId) + '">Desired Agent Class</label>';
  html += '<select id="agent-class-select-' + _agentPanelAttr(safeId) + '"'
    + ' class="agent-profile-select agent-class-select"'
    + ' onchange="' + _agentPanelEventAttr('agentPanelSelectClass('
      + _agentPanelJsString(agentId) + ', this.value)') + '"'
    + (cache.loading ? ' disabled' : '') + '>';
  html += _agentPanelClassOptionsHtml(agent, ui);
  html += '</select>';
  if (cache.loading) {
    html += '<span class="agent-profile-loading">Loading Agent Classes…</span>';
  } else if (cache.requested && !_agentPanelClassCompatibleClasses(agent).length) {
    html += '<span class="agent-profile-loading">No compatible Agent Classes found for base kind '
      + _agentPanelEsc(kind) + '.</span>';
  }
  html += '<div class="agent-profile-next-launch-note">'
    + _agentPanelEsc(_agentPanelClassSelectionHint(agent, selection))
    + '</div>';
  html += '</div>';
  if (Array.isArray(cache.issues) && cache.issues.length) html += _agentPanelClassIssuesHtml(cache.issues);
  html += _agentPanelClassPreviewHtml(agent, ui);
  html += '<div class="agent-profile-actions agent-class-actions">';
  html += '<button type="button" class="agent-profile-primary-btn"'
    + (disabledReason ? ' disabled title="' + _agentPanelAttr(disabledReason) + '"' : '')
    + ' onclick="' + _agentPanelEventAttr('return agentPanelAssignSelectedClass(event,'
      + _agentPanelJsString(agentId) + ')') + '">'
    + _agentPanelEsc(ui.saving ? 'Saving…' : primaryLabel)
    + '</button>';
  if (String(agent.agent_class_id || '').trim()) {
    html += '<button type="button" class="agent-profile-secondary-btn"'
      + (ui.saving ? ' disabled' : '')
      + ' onclick="' + _agentPanelEventAttr('return agentPanelClearClassAssignment(event,'
        + _agentPanelJsString(agentId) + ')') + '">Default / no explicit class</button>';
  }
  html += '<button type="button" class="agent-profile-secondary-btn"'
    + ' onclick="' + _agentPanelEventAttr('return agentPanelRefreshClasses(event,'
    + _agentPanelJsString(agentId) + ')') + '">Refresh classes</button>';
  if (disabledReason) {
    html += '<span class="agent-profile-disabled-reason">' + _agentPanelEsc(disabledReason) + '</span>';
  }
  html += '</div>';
  html += '</section>';
  return html;
}


function _agentPanelBodyWithClassManager(agent, bodyHtml, includeClassManager) {
  return (includeClassManager ? _agentPanelClassManagerHtml(agent) : '') + (bodyHtml || '');
}

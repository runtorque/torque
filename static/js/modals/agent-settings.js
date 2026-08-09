/* Per-agent settings for Architect and Engineer cells.
 *
 * The server owns inheritance and origin resolution.  This module only edits
 * sparse overrides and never reconstructs group/default precedence.
 */

var _agentSettingsContext = null;

var _agentSettingsCommonFields = [
  { key: 'provider', label: 'Provider', type: 'provider', section: 'Launch' },
  { key: 'boot_command', label: 'Boot command', type: 'text', section: 'Launch' },
  { key: 'model', label: 'Model', type: 'model', section: 'Launch' },
  { key: 'reasoning_effort', label: 'Reasoning effort', type: 'reasoning', section: 'Launch' },
  { key: 'fast_mode', label: 'Codex Fast startup', type: 'select', section: 'Launch', options: [['', 'Inherited'], ['on', 'Fast on'], ['off', 'Fast off']] },
  { key: 'autonomy_mode', label: 'Autonomy mode', type: 'select', section: 'Behavior' },
  { key: 'custom_instructions', label: 'Custom instructions', type: 'textarea', section: 'Behavior' },
];
var _agentSettingsEngineerFields = [
  { key: 'default_worker_concurrency', label: 'Default worker concurrency', type: 'number', section: 'Behavior' },
  { key: 'wave_size_preference', label: 'Wave size preference', type: 'select', section: 'Behavior', options: [['', 'Inherited'], ['small', 'Small'], ['balanced', 'Balanced'], ['large', 'Large']] },
  { key: 'same_agent_follow_up_preference', label: 'Same-agent follow-up', type: 'select', section: 'Behavior', options: [['', 'Inherited'], ['balanced', 'Balanced'], ['prefer_same_agent', 'Prefer same agent'], ['prefer_fresh_agent', 'Prefer fresh agent']] },
  { key: 'escalation_style', label: 'Escalation style', type: 'select', section: 'Behavior', options: [['', 'Inherited'], ['ask_early', 'Ask early'], ['note_then_ask', 'Note, then ask'], ['keep_moving', 'Keep moving']] },
  { key: 'engineer_can_override_worker_provider', label: 'Worker provider override', type: 'select', section: 'Behavior', options: [['', 'Inherited'], ['true', 'Allowed'], ['false', 'Not allowed']] },
  { key: 'restrict_to_created_agents', label: 'Restrict to created agents', type: 'select', section: 'Behavior', options: [['', 'Inherited'], ['true', 'On'], ['false', 'Off']] },
];
var _agentSettingsDigestFields = [
  { key: 'paused', label: 'Digest delivery', type: 'select', section: 'Delivery', options: [['', 'Inherited'], ['false', 'Enabled'], ['true', 'Paused']] },
  { key: 'push_interval', label: 'Push interval (seconds)', type: 'number', section: 'Delivery' },
  { key: 'max_interval', label: 'Maximum interval (seconds)', type: 'number', section: 'Delivery' },
  { key: 'heartbeat_interval', label: 'Heartbeat interval (seconds)', type: 'number', section: 'Delivery' },
  { key: 'digest_verbosity', label: 'Digest verbosity', type: 'select', section: 'Delivery', options: [['', 'Inherited'], ['compact', 'Compact'], ['balanced', 'Balanced'], ['detailed', 'Detailed']] },
  { key: 'enabled_events', label: 'Enabled events', type: 'list', section: 'Delivery' },
];

function _agentSettingsEsc(value) {
  if (typeof _agentPanelEsc === 'function') return _agentPanelEsc(value);
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
  });
}

function _agentSettingsFields(kind) {
  var fields = _agentSettingsCommonFields.slice();
  var autonomy = fields.filter(function(field) { return field.key === 'autonomy_mode'; })[0];
  autonomy.options = kind === 'architect'
    ? [['', 'Inherited'], ['dispatch_freely', 'Dispatch freely'], ['dispatch_after_confirm', 'Dispatch after confirm'], ['ask_always', 'Ask always']]
    : [['', 'Inherited'], ['suggest_only', 'Suggest only'], ['dispatch_when_clear', 'Dispatch when clear'], ['aggressive_auto_continue', 'Aggressive auto-continue']];
  if (kind === 'engineer') fields = fields.concat(_agentSettingsEngineerFields);
  fields = fields.concat(_agentSettingsDigestFields);
  var verbosity = fields.filter(function(field) { return field.key === 'digest_verbosity'; })[0];
  verbosity.options = kind === 'architect'
    ? [['', 'Inherited'], ['terse', 'Terse'], ['balanced', 'Balanced'], ['verbose', 'Verbose']]
    : [['', 'Inherited'], ['compact', 'Compact'], ['balanced', 'Balanced'], ['detailed', 'Detailed']];
  return fields;
}

function _agentSettingsResolved(key) {
  var resolved = (_agentSettingsContext && _agentSettingsContext.resolved) || {};
  return resolved[key] || { value: '', origin: 'default' };
}

function _agentSettingsDisplayValue(field) {
  if (!_agentSettingsContext || _agentSettingsContext.mode === 'create') return '';
  var value = _agentSettingsResolved(field.key).value;
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return value == null ? '' : String(value);
}

function _agentSettingsOriginText(origin) {
  return origin === 'per-agent' ? 'Overridden for this agent' : 'Inherited · ' + (origin === 'group' ? 'group' : 'default');
}

function _agentSettingsFieldHtml(field) {
  var id = 'agent-settings-' + field.key.replace(/_/g, '-');
  var resolved = _agentSettingsResolved(field.key);
  var overridden = _agentSettingsContext.mode !== 'create' && resolved.origin === 'per-agent';
  var value = _agentSettingsDisplayValue(field);
  var html = '<div class="settings-field agent-settings-field' + (field.type === 'textarea' || field.type === 'list' ? ' settings-field--wide' : '') + '"'
    + ' data-agent-setting="' + _agentSettingsEsc(field.key) + '" data-setting-kind="' + (_agentSettingsDigestFields.indexOf(field) >= 0 ? 'digest' : 'agent') + '"'
    + ' data-overridden="' + (overridden ? 'true' : 'false') + '">';
  html += '<label for="' + id + '">' + _agentSettingsEsc(field.label) + '</label>';
  if (field.type === 'provider') {
    html += '<select id="' + id + '" onchange="onAgentSettingsProviderChange();agentSettingsFieldChanged(this)"></select>';
  } else if (field.type === 'model') {
    html += '<select id="' + id + '-select" onchange="_onModelSelectChange(\'' + id + '\');agentSettingsFieldChanged(document.getElementById(\'' + id + '\'))"></select>';
    html += '<input id="' + id + '" class="provider-custom-input hidden" aria-label="Custom model" autocomplete="off" oninput="_onCustomModelInput(\'' + id + '\');agentSettingsFieldChanged(this)">';
  } else if (field.type === 'reasoning') {
    html += '<select id="' + id + '" onchange="_onReasoningEffortSelectChange(\'' + id + '\');agentSettingsFieldChanged(this)"></select>';
    html += '<input id="' + id + '-custom" class="provider-custom-input hidden" aria-label="Custom reasoning effort" autocomplete="off" oninput="agentSettingsFieldChanged(this)">';
  } else if (field.type === 'select') {
    html += '<select id="' + id + '" onchange="agentSettingsFieldChanged(this)">';
    (field.options || []).forEach(function(option) {
      html += '<option value="' + _agentSettingsEsc(option[0]) + '">' + _agentSettingsEsc(option[1]) + '</option>';
    });
    html += '</select>';
  } else if (field.type === 'textarea' || field.type === 'list') {
    html += '<textarea id="' + id + '" rows="' + (field.type === 'list' ? '3' : '5') + '" oninput="agentSettingsFieldChanged(this)"></textarea>';
  } else {
    html += '<input id="' + id + '" type="' + (field.type === 'number' ? 'number' : 'text') + '" autocomplete="off" oninput="agentSettingsFieldChanged(this)">';
  }
  html += '<div class="agent-settings-origin" data-agent-settings-origin>'
    + '<span>' + _agentSettingsEsc(_agentSettingsOriginText(overridden ? 'per-agent' : (resolved.origin || 'default'))) + '</span>'
    + '<button type="button"' + (overridden ? '' : ' hidden') + ' onclick="agentSettingsUseInherited(\'' + _agentSettingsEsc(field.key) + '\')">Use inherited</button>'
    + '</div>';
  if (field.section === 'Launch') {
    html += '<div class="settings-field-help">'
      + (_agentSettingsContext.mode === 'create'
        ? 'Affects this agent’s first launch.'
        : 'Applies on the next fresh launch or relaunch.')
      + '</div>';
  }
  html += '</div>';
  return html;
}

function _agentSettingsSectionHtml(title, fields) {
  if (!fields.length) return '';
  var description = '';
  if (title === 'Launch') {
    description = _agentSettingsContext.mode === 'create'
      ? 'Provider, command, model, reasoning, and Fast mode are resolved for this first launch.'
      : 'These values apply when the agent next starts a fresh launch or relaunch.';
  } else if (title === 'Behavior') {
    description = 'Stored as per-agent overrides. The current runtime does not yet consume these behavior and custom-instruction preferences.';
  } else if (title === 'Delivery') {
    description = 'Digest delivery uses the existing per-agent digest store and takes effect immediately.';
  }
  return '<section class="gs-settings-section" data-agent-settings-section="' + _agentSettingsEsc(title) + '">'
    + '<div class="gs-settings-section-title">' + _agentSettingsEsc(title) + '</div>'
    + (description ? '<p class="settings-section-description">' + _agentSettingsEsc(description) + '</p>' : '')
    + '<div class="gs-settings-section-body settings-field-grid">'
    + fields.map(_agentSettingsFieldHtml).join('') + '</div></section>';
}

function _agentSettingsSpecializationsHtml() {
  if (!_agentSettingsContext || _agentSettingsContext.kind !== 'engineer') return '';
  var resolved = _agentSettingsResolved('engineer_specializations');
  var value = _agentSettingsContext.mode === 'create' ? '' : (resolved.value || []).join(', ');
  var overridden = _agentSettingsContext.mode !== 'create' && resolved.origin === 'per-agent';
  return '<section class="gs-settings-section" data-agent-settings-section="Specializations">'
    + '<div class="gs-settings-section-title">Specializations</div><div class="gs-settings-section-body">'
    + '<div class="settings-field settings-field--wide agent-settings-field" data-agent-specializations data-overridden="' + (overridden ? 'true' : 'false') + '">'
    + '<label for="agent-settings-specializations">Ordered specialization slugs <span class="label-hint">first is primary</span></label>'
    + '<textarea id="agent-settings-specializations" rows="3" placeholder="ui-ux, frontend" oninput="agentSettingsSpecializationsChanged(this)">' + _agentSettingsEsc(value) + '</textarea>'
    + '<div class="agent-settings-origin" data-agent-settings-origin><span>' + _agentSettingsEsc(_agentSettingsOriginText(overridden ? 'per-agent' : (resolved.origin || 'default'))) + '</span>'
    + '<button type="button"' + (overridden ? '' : ' hidden') + ' onclick="agentSettingsUseInherited(\'engineer_specializations\')">Use inherited</button></div>'
    + '<div class="settings-field-help">Available project specializations are validated by the server. The existing Engineer panel editor remains available.</div>'
    + '</div></div></section>';
}

function _agentSettingsClassHtml() {
  if (!_agentSettingsContext) return '';
  if (_agentSettingsContext.mode === 'create') {
    return '<section class="gs-settings-section" data-agent-settings-section="Agent Class">'
      + '<div class="gs-settings-section-title">Agent Class</div><div class="gs-settings-section-body">'
      + '<div id="agent-settings-agent-class-row" class="agent-class-picker-row">'
      + '<label for="agent-settings-agent-class-select">Agent Class</label><select id="agent-settings-agent-class-select"></select>'
      + '<div id="agent-settings-agent-class-hint" class="label-hint agent-class-picker-hint"></div>'
      + '<div class="agent-settings-origin"><span>Inherited · default</span></div></div></div></section>';
  }
  var agent = state.agents && state.agents[_agentSettingsContext.agentId];
  return '<section class="gs-settings-section" data-agent-settings-section="Agent Class">'
    + '<div class="gs-settings-section-title">Agent Class</div><div id="agent-settings-class-host" class="gs-settings-section-body">'
    + (typeof _agentPanelClassManagerHtml === 'function' ? _agentPanelClassManagerHtml(agent) : '')
    + '</div></section>';
}

function _agentSettingsRender() {
  var body = document.getElementById('agent-settings-body');
  if (!body || !_agentSettingsContext) return;
  var fields = _agentSettingsFields(_agentSettingsContext.kind);
  var sections = ['Launch', 'Behavior', 'Delivery'];
  var html = '';
  if (_agentSettingsContext.mode === 'create') {
    html += '<section class="gs-settings-section"><div class="gs-settings-section-title">Identity</div><div class="gs-settings-section-body settings-field-grid">'
      + '<div class="settings-field settings-field--wide"><label for="agent-settings-name">Name</label>'
      + '<input id="agent-settings-name" autocomplete="off" placeholder="' + (_agentSettingsContext.kind === 'architect' ? 'e.g. Productmind' : 'Engineer') + '"></div></div></section>';
  }
  sections.forEach(function(section) {
    html += _agentSettingsSectionHtml(section, fields.filter(function(field) { return field.section === section; }));
  });
  html += _agentSettingsSpecializationsHtml();
  html += _agentSettingsClassHtml();
  html += '<p class="agent-settings-runtime-note">Launch fields affect this launch for a new agent and the next fresh launch or relaunch for an existing agent. Desired Agent Class follows the same launch boundary; authority for a running session remains frozen. Engineer specializations are included in a new Engineer’s first persistent prompt. Digest delivery takes effect immediately. Behavior and orchestration preferences are stored now but are not yet consumed by the current runtime.</p>';
  body.innerHTML = html;
  fields.forEach(function(field) {
    var id = 'agent-settings-' + field.key.replace(/_/g, '-');
    var value = _agentSettingsDisplayValue(field);
    var el = document.getElementById(id);
    if (field.type === 'provider') {
      _populateProviderSelect(id, value, true);
    } else if (field.type === 'model') {
      // Populated after provider below.
    } else if (field.type === 'reasoning') {
      // Populated together with the model control below.
    } else if (el) {
      el.value = value;
    }
  });
  onAgentSettingsProviderChange(_agentSettingsDisplayValue({ key: 'reasoning_effort' }));
  if (_agentSettingsContext.mode === 'create' && typeof agentClassPickerPrepare === 'function') {
    agentClassPickerPrepare(
      _agentSettingsContext.kind,
      _agentSettingsContext.group,
      agentClassBaseDirForGroup(_agentSettingsContext.group),
      'agent-settings-create',
      { rowId: 'agent-settings-agent-class-row', selectId: 'agent-settings-agent-class-select', hintId: 'agent-settings-agent-class-hint' }
    );
  }
  _agentSettingsContext.baseline = _agentSettingsCollectBaseline();
  _agentSettingsSetDirty(false);
}

function _agentSettingsProvider() {
  return _getProviderValue('agent-settings-provider') || _runtimeDefaultProviderName();
}

function onAgentSettingsProviderChange(currentEffort) {
  if (!document.getElementById('agent-settings-model')) return;
  _populateModelSelect(
    'agent-settings-model',
    _agentSettingsProvider(),
    _agentSettingsDisplayValue({ key: 'model' }),
    'Inherited',
    'agent-settings-reasoning-effort',
    currentEffort == null ? _getReasoningEffortValue('agent-settings-reasoning-effort') : currentEffort
  );
  if (typeof _syncCodexFastMode === 'function') {
    var fastField = document.querySelector('[data-agent-setting="fast_mode"]');
    if (fastField) {
      fastField.id = 'agent-settings-fast-mode-row';
      _syncCodexFastMode('agent-settings-fast-mode-row', _agentSettingsProvider());
    }
  }
}

function _agentSettingsFieldContainer(key) {
  return document.querySelector('[data-agent-setting="' + key + '"]');
}

function _agentSettingsUpdateOrigin(container, overridden) {
  if (!container) return;
  container.dataset.overridden = overridden ? 'true' : 'false';
  var origin = container.querySelector('[data-agent-settings-origin]');
  if (!origin) return;
  var label = origin.querySelector('span');
  var button = origin.querySelector('button');
  if (label) label.textContent = overridden ? 'Overridden for this agent' : _agentSettingsOriginText((_agentSettingsResolved(container.dataset.agentSetting || 'engineer_specializations').origin || 'default'));
  if (button) button.hidden = !overridden;
}

function agentSettingsFieldChanged(control) {
  var container = control && control.closest ? control.closest('[data-agent-setting]') : null;
  if (!container) return;
  container.dataset.touched = 'true';
  _agentSettingsUpdateOrigin(container, true);
  _agentSettingsRecomputeDirty();
}

function agentSettingsSpecializationsChanged(control) {
  var container = control && control.closest ? control.closest('[data-agent-specializations]') : null;
  if (!container) return;
  container.dataset.touched = 'true';
  _agentSettingsUpdateOrigin(container, true);
  _agentSettingsRecomputeDirty();
}

function agentSettingsUseInherited(key) {
  if (key === 'engineer_specializations') {
    var specs = document.querySelector('[data-agent-specializations]');
    if (!specs) return;
    var specsInput = document.getElementById('agent-settings-specializations');
    if (specsInput) specsInput.value = '';
    specs.dataset.touched = 'true';
    _agentSettingsUpdateOrigin(specs, false);
  } else {
    var container = _agentSettingsFieldContainer(key);
    if (!container) return;
    var field = _agentSettingsFields(_agentSettingsContext.kind).filter(function(item) { return item.key === key; })[0];
    var id = 'agent-settings-' + key.replace(/_/g, '-');
    var value = _agentSettingsDisplayValue(field);
    if (field.type === 'provider') _populateProviderSelect(id, value, true);
    else if (field.type === 'model') _populateModelSelect(id, _agentSettingsProvider(), value, 'Inherited', 'agent-settings-reasoning-effort', _getReasoningEffortValue('agent-settings-reasoning-effort'));
    else {
      var control = document.getElementById(id);
      if (control) control.value = value;
    }
    container.dataset.touched = 'true';
    _agentSettingsUpdateOrigin(container, false);
  }
  _agentSettingsRecomputeDirty();
}

function _agentSettingsControlValue(field) {
  var id = 'agent-settings-' + field.key.replace(/_/g, '-');
  if (field.type === 'provider') return _getProviderValue(id);
  if (field.type === 'model') return _getModelValue(id);
  if (field.type === 'reasoning') return _getReasoningEffortValue(id);
  var control = document.getElementById(id);
  var value = control ? control.value : '';
  if (field.type === 'number' && value !== '') return parseInt(value, 10);
  if (field.type === 'list') return String(value || '').split(/[\n,]+/).map(function(item) { return item.trim(); }).filter(Boolean);
  if (['paused', 'engineer_can_override_worker_provider', 'restrict_to_created_agents'].indexOf(field.key) >= 0 && value !== '') return value === 'true';
  return value;
}

function _agentSettingsCollectBaseline() {
  var result = { agent: {}, digest: {}, specializations: null };
  _agentSettingsFields(_agentSettingsContext.kind).forEach(function(field) {
    var container = _agentSettingsFieldContainer(field.key);
    result[container.dataset.settingKind][field.key] = {
      overridden: container.dataset.overridden === 'true',
      value: _agentSettingsControlValue(field),
    };
  });
  var specs = document.querySelector('[data-agent-specializations]');
  if (specs) result.specializations = {
    overridden: specs.dataset.overridden === 'true',
    value: _agentSettingsSpecializationValue(),
  };
  return result;
}

function _agentSettingsEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function _agentSettingsSetDirty(dirty) {
  if (!_agentSettingsContext) return;
  _agentSettingsContext.dirty = !!dirty;
  var button = document.getElementById('agent-settings-save-btn');
  if (button && _agentSettingsContext.mode !== 'create') button.disabled = !dirty;
  var label = document.querySelector('#modal-agent-settings .settings-save-state');
  if (label) {
    label.textContent = dirty ? 'Unsaved changes' : '';
    label.hidden = !dirty;
  }
}

function _agentSettingsRecomputeDirty() {
  if (!_agentSettingsContext || !_agentSettingsContext.baseline) return;
  _agentSettingsSetDirty(!_agentSettingsEqual(_agentSettingsContext.baseline, _agentSettingsCollectBaseline()));
}

function _agentSettingsSpecializationValue() {
  var input = document.getElementById('agent-settings-specializations');
  return String(input ? input.value : '').split(/[\n,]+/).map(function(item) { return item.trim(); }).filter(Boolean);
}

function _agentSettingsSparseDiff(kind) {
  var diff = {};
  var baseline = (_agentSettingsContext.baseline && _agentSettingsContext.baseline[kind]) || {};
  _agentSettingsFields(_agentSettingsContext.kind).filter(function(field) {
    return (kind === 'digest') === (_agentSettingsDigestFields.indexOf(field) >= 0);
  }).forEach(function(field) {
    var container = _agentSettingsFieldContainer(field.key);
    var current = { overridden: container.dataset.overridden === 'true', value: _agentSettingsControlValue(field) };
    if (!_agentSettingsEqual(current, baseline[field.key])) diff[field.key] = current.overridden ? current.value : null;
  });
  return diff;
}

function _agentSettingsSpecializationsChangedForSave() {
  var specs = document.querySelector('[data-agent-specializations]');
  if (!specs) return false;
  var current = { overridden: specs.dataset.overridden === 'true', value: _agentSettingsSpecializationValue() };
  return !_agentSettingsEqual(current, _agentSettingsContext.baseline.specializations);
}

function openAgentSettingsDialog(options) {
  options = options || {};
  var agentId = String(options.agentId || '').trim();
  var agent = agentId && state.agents ? state.agents[agentId] : null;
  var kind = String(options.kind || (agent && agent.kind) || '').trim();
  if (kind !== 'architect' && kind !== 'engineer') return;
  if (agent && (Number(agent.dismissed_at || 0) > 0 || Number(agent.deleted_at || 0) > 0 || agent.tombstoned)) return;
  var mode = options.mode || (agent ? 'edit' : 'create');
  _agentSettingsContext = {
    mode: mode,
    agentId: agentId,
    agent: agent,
    kind: kind,
    group: String(options.group || (agent && agent.group) || '').trim(),
    resolved: agentId && state.resolved_agent_settings ? (state.resolved_agent_settings[agentId] || {}) : {},
    relaunch: !!options.relaunch,
  };
  var title = document.getElementById('agent-settings-title');
  var subtitle = document.getElementById('agent-settings-subtitle');
  var save = document.getElementById('agent-settings-save-btn');
  if (title) title.textContent = mode === 'create' ? 'Create ' + (kind === 'architect' ? 'Architect' : 'Engineer') : (agent.name || (kind === 'architect' ? 'Architect' : 'Engineer')) + ' Settings';
  if (subtitle) subtitle.textContent = mode === 'create'
    ? 'Launch inputs and stored per-agent overrides'
    : 'Per-agent overrides · ' + (_agentSettingsContext.group || 'No group');
  if (save) save.textContent = mode === 'create' ? 'Create ' + (kind === 'architect' ? 'Architect' : 'Engineer') : (_agentSettingsContext.relaunch ? 'Save & relaunch' : 'Save changes');
  _agentSettingsRender();
  if (agentId) send({ cmd: 'get_agent_settings', agent_id: agentId });
  if (kind === 'engineer' && _agentSettingsContext.group) send({ cmd: 'list_specializations', group: _agentSettingsContext.group });
  if (typeof openModalDialog === 'function') {
    openModalDialog('modal-agent-settings', { role: 'dialog', labelledBy: 'agent-settings-title', initialFocus: mode === 'create' ? '#agent-settings-name' : '#agent-settings-provider', cancelOnEscape: true, onCancel: closeAgentSettingsDialog });
  } else {
    document.getElementById('modal-agent-settings').classList.add('visible');
  }
}

function agentSettingsReceive(msg) {
  if (!_agentSettingsContext || !msg || msg.agent_id !== _agentSettingsContext.agentId) return;
  // Never replace a dirty form with asynchronous state and lose a draft.
  if (_agentSettingsContext.dirty) return;
  _agentSettingsContext.resolved = msg.resolved || {};
  _agentSettingsRender();
}

function renderAgentSettingsSpecializations() {
  // The field is intentionally stable DOM; catalog responses must not erase a draft.
}

async function closeAgentSettingsDialog() {
  if (_agentSettingsContext && _agentSettingsContext.dirty && typeof showConfirm === 'function') {
    var discard = await showConfirm('Discard the changes you made in Agent Settings?', { title: 'Unsaved changes', label: 'Discard changes', variant: 'btn-danger' });
    if (!discard) return;
  }
  _agentSettingsContext = null;
  closeModals();
}

function _agentSettingsCreatePayload(agentSettings, digestSettings) {
  var ctx = _agentSettingsContext;
  var name = String((document.getElementById('agent-settings-name') || {}).value || '').trim();
  if (!name) return null;
  var classState = typeof agentClassPickerSubmitSelection === 'function' ? agentClassPickerSubmitSelection('agent-settings-create') : null;
  if (typeof agentClassPickerSubmitSelection === 'function' && !classState) return null;
  var selectedClassId = classState && !classState.defaultSelected ? classState.selectedId : '';
  var payload = {
    cmd: selectedClassId ? 'create_agent_from_class' : (ctx.kind === 'architect' ? 'add_architect' : 'add_engineer'),
    name: name,
    group: ctx.group,
    agent_settings: agentSettings,
    agent_digest_settings: digestSettings,
  };
  if (selectedClassId) {
    payload.class_id = selectedClassId;
    payload.kind = ctx.kind;
  }
  if (ctx.kind === 'engineer' && _agentSettingsSpecializationsChangedForSave()) {
    payload.specializations = _agentSettingsSpecializationValue();
  }
  return payload;
}

function saveAgentSettingsDialog() {
  if (!_agentSettingsContext) return;
  var agentDiff = _agentSettingsSparseDiff('agent');
  var digestDiff = _agentSettingsSparseDiff('digest');
  var ctx = _agentSettingsContext;
  if (ctx.mode === 'create') {
    var createPayload = _agentSettingsCreatePayload(agentDiff, digestDiff);
    if (!createPayload) return;
    send(createPayload);
  } else {
    if (Object.keys(agentDiff).length) send({ cmd: 'update_agent_settings', agent_id: ctx.agentId, settings: agentDiff });
    if (Object.keys(digestDiff).length) send({ cmd: 'update_agent_digest_settings', agent_id: ctx.agentId, settings: digestDiff });
    if (_agentSettingsSpecializationsChangedForSave()) {
      send({ cmd: 'set_engineer_specializations', engineer_id: ctx.agentId, specializations: _agentSettingsSpecializationValue() });
    }
    // Agent Class assignment is intentionally owned by its existing audited
    // nested modal; pressing Save here never emits a class command.
    if (ctx.relaunch && (Object.keys(agentDiff).length || Object.keys(digestDiff).length || _agentSettingsSpecializationsChangedForSave())) {
      send({ cmd: 'relaunch_agent', id: ctx.agentId });
    }
  }
  _agentSettingsContext = null;
  closeModals();
  if (typeof _showToast === 'function') _showToast(ctx.mode === 'create' ? (ctx.kind === 'architect' ? 'Architect requested' : 'Engineer requested') : 'Agent settings saved', 'success');
}

function _agentSettingsRefreshClassHost() {
  if (!_agentSettingsContext || _agentSettingsContext.mode === 'create') return;
  var host = document.getElementById('agent-settings-class-host');
  var agent = state.agents && state.agents[_agentSettingsContext.agentId];
  if (host && agent && typeof _agentPanelClassManagerHtml === 'function') host.innerHTML = _agentPanelClassManagerHtml(agent);
}

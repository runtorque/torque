let _engineerLaunchContext = null;

function _engineerLaunchSpecializationsFor(cell) {
  if (!cell) return [];
  const raw = cell.engineer_specializations || [];
  return Array.isArray(raw) ? raw.slice() : [];
}

function _engineerLaunchGroupDefaultSpecs(group) {
  const gs = (state.group_settings || {})[group] || {};
  const raw = gs.default_engineer_specializations;
  return Array.isArray(raw) ? raw.slice() : [];
}

function renderEngineerLaunchSpecializations() {
  if (!_engineerLaunchContext) return;
  const selectedEl = document.getElementById('engineer-launch-specializations-selected');
  const availableEl = document.getElementById('engineer-launch-specializations-available');
  if (!selectedEl || !availableEl) return;
  const selected = _engineerLaunchContext.specializations || [];
  const available = (state.specializations || [])
    .map(function (s) { return s && s.name; })
    .filter(Boolean);

  selectedEl.innerHTML = '';
  selected.forEach(function (name, idx) {
    const li = document.createElement('li');
    li.className = 'specialization-entry';
    const tag = idx === 0 ? ' (primary)' : '';
    const label = document.createElement('span');
    label.textContent = name + tag;
    li.appendChild(label);

    const controls = document.createElement('span');
    controls.className = 'specialization-controls-row';
    if (idx > 0) {
      const up = document.createElement('button');
      up.type = 'button';
      up.textContent = '↑';
      up.title = 'Move up';
      up.onclick = function () { engineerLaunchMoveSpecialization(idx, -1); };
      controls.appendChild(up);
    }
    if (idx < selected.length - 1) {
      const down = document.createElement('button');
      down.type = 'button';
      down.textContent = '↓';
      down.title = 'Move down';
      down.onclick = function () { engineerLaunchMoveSpecialization(idx, 1); };
      controls.appendChild(down);
    }
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = 'Delete';
    remove.onclick = function () { engineerLaunchRemoveSpecialization(idx); };
    controls.appendChild(remove);
    li.appendChild(controls);
    selectedEl.appendChild(li);
  });

  availableEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = available.length ? 'Pick a specialization...' : 'No specializations available';
  availableEl.appendChild(placeholder);
  available.forEach(function (name) {
    if (selected.indexOf(name) >= 0) return;
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    const meta = (state.specializations || []).find(function (s) {
      return s && s.name === name;
    });
    if (meta && meta.preamble) {
      opt.title = String(meta.preamble).slice(0, 200);
    }
    availableEl.appendChild(opt);
  });

  const resetBtn = document.getElementById('engineer-launch-specializations-reset');
  if (resetBtn) {
    const def = _engineerLaunchGroupDefaultSpecs(_engineerLaunchContext.group);
    const sameAsDefault = def.length === selected.length
      && def.every(function (n, i) { return n === selected[i]; });
    resetBtn.disabled = sameAsDefault;
    if (def.length === 0) {
      resetBtn.title = 'No group-level default is set for this group.';
    } else {
      resetBtn.title = 'Replace the current list with the group-level default ('
        + def.join(', ') + ').';
    }
  }
}

function engineerLaunchAddSpecialization() {
  if (!_engineerLaunchContext) return;
  const availableEl = document.getElementById('engineer-launch-specializations-available');
  if (!availableEl) return;
  const name = availableEl.value;
  if (!name) return;
  const selected = _engineerLaunchContext.specializations || [];
  if (selected.indexOf(name) < 0) {
    selected.push(name);
    _engineerLaunchContext.specializations = selected;
  }
  _engineerLaunchContext.specializations_touched = true;
  renderEngineerLaunchSpecializations();
}

function engineerLaunchRemoveSpecialization(idx) {
  if (!_engineerLaunchContext) return;
  const selected = _engineerLaunchContext.specializations || [];
  if (idx < 0 || idx >= selected.length) return;
  selected.splice(idx, 1);
  _engineerLaunchContext.specializations = selected;
  _engineerLaunchContext.specializations_touched = true;
  renderEngineerLaunchSpecializations();
}

function engineerLaunchMoveSpecialization(idx, delta) {
  if (!_engineerLaunchContext) return;
  const selected = _engineerLaunchContext.specializations || [];
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= selected.length) return;
  const moved = selected.splice(idx, 1)[0];
  selected.splice(newIdx, 0, moved);
  _engineerLaunchContext.specializations = selected;
  _engineerLaunchContext.specializations_touched = true;
  renderEngineerLaunchSpecializations();
}

function engineerLaunchResetSpecializationsToGroupDefault() {
  if (!_engineerLaunchContext) return;
  const def = _engineerLaunchGroupDefaultSpecs(_engineerLaunchContext.group);
  _engineerLaunchContext.specializations = def;
  _engineerLaunchContext.specializations_touched = true;
  renderEngineerLaunchSpecializations();
}

function openNewSpecializationDialog() {
  const modal = document.getElementById('modal-new-specialization');
  if (!modal) return;
  document.getElementById('new-specialization-name').value = '';
  document.getElementById('new-specialization-description').value = '';
  document.getElementById('new-specialization-preamble').value = '';
  document.getElementById('new-specialization-priorities').value = '';
  document.getElementById('new-specialization-scope').value = 'project';
  if (typeof openNestedModal === 'function') {
    openNestedModal('modal-new-specialization');
  } else {
    modal.classList.add('visible');
  }
  document.getElementById('new-specialization-name').focus();
}

function submitNewSpecializationDialog() {
  const name = (document.getElementById('new-specialization-name').value || '').trim();
  if (!name) {
    document.getElementById('new-specialization-name').focus();
    return;
  }
  const description = (document.getElementById('new-specialization-description').value || '').trim();
  const preamble = (document.getElementById('new-specialization-preamble').value || '').trim();
  const prioritiesRaw = (document.getElementById('new-specialization-priorities').value || '').trim();
  const priorities = prioritiesRaw
    ? prioritiesRaw.split(/\n+/).map(function (s) { return s.trim(); }).filter(Boolean)
    : [];
  const scope = document.getElementById('new-specialization-scope').value || 'project';
  const data = { name: name };
  if (description) data.description = description;
  if (preamble) data.preamble = preamble;
  if (priorities.length) data.priorities = priorities;

  const group = (_engineerLaunchContext && _engineerLaunchContext.group)
    || (typeof _settingsGroup !== 'undefined' ? (_settingsGroup || '') : '');
  send({
    cmd: 'save_specialization',
    name: name,
    data: data,
    scope: scope,
    group: group,
  });
  send({ cmd: 'list_specializations', group: group });
  // Clear the GS-source flag if it was set so subsequent dialogs reset.
  const modal = document.getElementById('modal-new-specialization');
  if (modal && modal.dataset) delete modal.dataset.gsEngineerSource;
  if (typeof closeNestedModal === 'function') {
    closeNestedModal('modal-new-specialization');
  } else {
    document.getElementById('modal-new-specialization').classList.remove('visible');
  }
}

function _defaultEngineerLaunchSettings() {
  return {
    engineer_provider: '',
    engineer_boot_command: '',
    engineer_model: '',
    engineer_reasoning_effort: '',
    custom_instructions: '',
    autonomy_mode: 'dispatch_when_clear',
    default_worker_concurrency: 2,
    wave_size_preference: 'small',
    same_agent_follow_up_preference: 'balanced',
    digest_verbosity: 'balanced',
    escalation_style: 'note_then_ask',
    push_interval: 60,
    max_interval: 300,
    heartbeat_interval: 300,
    enabled_events: _defaultEngineerNotificationSettings().enabled_events,
  };
}

function _getEngineerLaunchSettings(group) {
  const current = (state.engineer_settings && state.engineer_settings[group]) || {};
  return Object.assign(_defaultEngineerLaunchSettings(), current || {});
}

function _engineerLaunchProviderForReasoning(group) {
  const groupSettings = (state.group_settings && state.group_settings[group]) || {};
  return (
    _getProviderValue('engineer-launch-provider')
    || groupSettings.agent_provider
    || _runtimeDefaultProviderName()
  );
}

function onEngineerLaunchProviderChange() {
  const input = document.getElementById('engineer-launch-boot-cmd');
  if (!input) return;
  const v = document.getElementById('engineer-launch-provider').value;
  const group = _engineerLaunchContext ? _engineerLaunchContext.group : '';
  const groupSettings = (state.group_settings && state.group_settings[group]) || {};
  if (v === '__custom__') {
    input.placeholder = 'e.g. my-engineer-cli';
  } else {
    const effectiveProvider = _getProviderValue('engineer-launch-provider') || groupSettings.agent_provider || '';
    const meta = effectiveProvider ? _findProviderMeta(effectiveProvider) : null;
    input.placeholder = (meta ? meta.command : _runtimeDefaultCommand()) + ' (default)';
  }
  _populateReasoningEffortSelect(
    'engineer-launch-reasoning-effort',
    _engineerLaunchProviderForReasoning(group),
    document.getElementById('engineer-launch-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
}

function openEngineerLaunchDialog(group, agentId) {
  if (!group) return;
  const cell = agentId && state.agents ? state.agents[agentId] : null;
  const ws = _getEngineerLaunchSettings(group);
  // In create mode, preview the group-level default specializations so the
  // operator sees what will be applied. The server still falls back to the
  // group default when the payload omits specializations entirely.
  const initialSpecs = cell
    ? _engineerLaunchSpecializationsFor(cell)
    : _engineerLaunchGroupDefaultSpecs(group);
  _engineerLaunchContext = {
    group: group,
    agent_id: cell ? cell.id : '',
    mode: cell ? 'relaunch' : 'create',
    specializations: initialSpecs,
    notification_settings: {
      push_interval: ws.push_interval,
      max_interval: ws.max_interval,
      heartbeat_interval: ws.heartbeat_interval,
      enabled_events: (ws.enabled_events || []).slice(),
    },
  };
  send({ cmd: 'list_specializations', group: group });
  if (!cell && typeof agentClassPickerPrepare === 'function') {
    agentClassPickerPrepare('engineer', group, agentClassBaseDirForGroup(group), 'engineer-launch');
  } else {
    const classRow = document.getElementById('engineer-launch-agent-class-row');
    if (classRow) classRow.classList.add('hidden');
  }
  renderEngineerLaunchSpecializations();

  document.getElementById('engineer-launch-title').textContent =
    cell ? 'Relaunch Engineer' : 'Create Engineer';
  document.getElementById('engineer-launch-group').textContent = group;
  document.getElementById('engineer-launch-submit-btn').textContent =
    cell ? 'Save & Relaunch' : 'Create Engineer';

  _populateProviderSelect(
    'engineer-launch-provider',
    ws.engineer_provider || '',
    true
  );
  document.getElementById('engineer-launch-boot-cmd').value = ws.engineer_boot_command || '';
  document.getElementById('engineer-launch-model').value = ws.engineer_model || '';
  document.getElementById('engineer-launch-reasoning-effort').value = ws.engineer_reasoning_effort || '';
  document.getElementById('engineer-launch-custom-instructions').value = ws.custom_instructions || '';
  _setSelectValue('engineer-launch-autonomy-mode', ws.autonomy_mode, 'dispatch_when_clear');
  _setSelectValue(
    'engineer-launch-default-worker-concurrency',
    ws.default_worker_concurrency,
    2
  );
  _setSelectValue(
    'engineer-launch-wave-size-preference',
    ws.wave_size_preference,
    'small'
  );
  _setSelectValue(
    'engineer-launch-same-agent-follow-up-preference',
    ws.same_agent_follow_up_preference,
    'balanced'
  );
  _setSelectValue(
    'engineer-launch-digest-verbosity',
    ws.digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'engineer-launch-escalation-style',
    ws.escalation_style,
    'note_then_ask'
  );
  syncEngineerLaunchNotificationPreset();
  onEngineerLaunchProviderChange();

  if (typeof openModalDialog === 'function') {
    openModalDialog('modal-engineer-launch', {
      role: 'dialog',
      labelledBy: 'engineer-launch-title',
      initialFocus: '#engineer-launch-provider',
      cancelOnEscape: true,
      onCancel: closeModals,
    });
  } else {
    document.getElementById('modal-engineer-launch').classList.add('visible');
    document.getElementById('engineer-launch-provider').focus();
  }
}

function submitEngineerLaunchDialog() {
  if (!_engineerLaunchContext) return;
  const group = _engineerLaunchContext.group;
  const notificationSettings = _getEngineerLaunchNotificationSettings();
  send({
    cmd: 'engineer_update_settings',
    group: group,
    engineer_provider: _getProviderValue('engineer-launch-provider'),
    engineer_boot_command: document.getElementById('engineer-launch-boot-cmd').value.trim(),
    engineer_model: document.getElementById('engineer-launch-model').value.trim(),
    engineer_reasoning_effort: document.getElementById('engineer-launch-reasoning-effort').value,
    custom_instructions: document.getElementById('engineer-launch-custom-instructions').value,
    autonomy_mode: document.getElementById('engineer-launch-autonomy-mode').value,
    default_worker_concurrency: parseInt(document.getElementById('engineer-launch-default-worker-concurrency').value, 10) || 2,
    wave_size_preference: document.getElementById('engineer-launch-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('engineer-launch-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('engineer-launch-digest-verbosity').value,
    escalation_style: document.getElementById('engineer-launch-escalation-style').value,
    push_interval: notificationSettings.push_interval,
    max_interval: notificationSettings.max_interval,
    heartbeat_interval: notificationSettings.heartbeat_interval,
    enabled_events: notificationSettings.enabled_events,
  });

  const specializations = (_engineerLaunchContext.specializations || []).slice();
  const engineerId = _engineerLaunchContext.agent_id;

  if (_engineerLaunchContext.mode === 'create') {
    const selectedClassId = typeof agentClassPickerSelected === 'function'
      ? agentClassPickerSelected('engineer-launch')
      : '';
    const payload = selectedClassId
      ? {
        cmd: 'create_agent_from_class',
        class_id: selectedClassId,
        kind: 'engineer',
        name: 'Engineer',
        group: group,
      }
      : {
        cmd: 'add_agent',
        name: 'Engineer',
        group: group,
        is_engineer: true,
      };
    // Always include specializations so an explicit empty pick doesn't
    // get re-populated from the group default on the server. If the user
    // never touched the picker, this still sends the previewed group
    // default verbatim — same outcome as the server-side fallback.
    payload.specializations = specializations;
    send(payload);
  } else if (engineerId) {
    if (_engineerLaunchContext.specializations_touched) {
      send({
        cmd: 'set_engineer_specializations',
        engineer_id: engineerId,
        specializations: specializations,
      });
    }
    send({ cmd: 'relaunch_agent', id: engineerId });
  }

  _engineerLaunchContext = null;
  closeModals();
}

function _getEngineerLaunchNotificationSettings() {
  const current = Object.assign(
    _defaultEngineerNotificationSettings(),
    (_engineerLaunchContext && _engineerLaunchContext.notification_settings) || {}
  );
  current.digest_verbosity = document.getElementById('engineer-launch-digest-verbosity').value || current.digest_verbosity;
  current.enabled_events = (current.enabled_events || []).slice();
  return current;
}

function syncEngineerLaunchNotificationPreset() {
  const preset = _matchEngineerNotificationPreset(_getEngineerLaunchNotificationSettings());
  _setSelectValue('engineer-launch-notification-preset', preset, 'custom');
  _setEngineerNotificationPresetHint('engineer-launch-notification-preset-hint', preset);
}

function onEngineerLaunchNotificationPresetChange() {
  if (!_engineerLaunchContext) return;
  const preset = document.getElementById('engineer-launch-notification-preset').value;
  if (preset && preset !== 'custom') {
    const settings = _getEngineerNotificationPresetSettings(preset);
    _engineerLaunchContext.notification_settings = {
      push_interval: settings.push_interval,
      max_interval: settings.max_interval,
      heartbeat_interval: settings.heartbeat_interval,
      enabled_events: settings.enabled_events,
    };
    _setSelectValue(
      'engineer-launch-digest-verbosity',
      settings.digest_verbosity,
      'balanced'
    );
  }
  syncEngineerLaunchNotificationPreset();
}

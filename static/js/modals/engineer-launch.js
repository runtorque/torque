let _engineerLaunchContext = null;

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
  _engineerLaunchContext = {
    group: group,
    agent_id: cell ? cell.id : '',
    mode: cell ? 'relaunch' : 'create',
    notification_settings: {
      push_interval: ws.push_interval,
      max_interval: ws.max_interval,
      heartbeat_interval: ws.heartbeat_interval,
      enabled_events: (ws.enabled_events || []).slice(),
    },
  };

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

  document.getElementById('modal-engineer-launch').classList.add('visible');
  document.getElementById('engineer-launch-provider').focus();
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

  if (_engineerLaunchContext.mode === 'create') {
    send({ cmd: 'add_agent', name: 'Engineer', group: group, is_engineer: true });
  } else if (_engineerLaunchContext.agent_id) {
    send({ cmd: 'relaunch_agent', id: _engineerLaunchContext.agent_id });
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

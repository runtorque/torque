let _weaverLaunchContext = null;

function _defaultWeaverLaunchSettings() {
  return {
    weaver_provider: '',
    weaver_boot_command: '',
    weaver_model: '',
    weaver_reasoning_effort: '',
    custom_instructions: '',
    autonomy_mode: 'dispatch_when_clear',
    default_worker_concurrency: 2,
    wave_size_preference: 'small',
    same_agent_follow_up_preference: 'balanced',
    digest_verbosity: 'balanced',
    escalation_style: 'note_then_ask',
  };
}

function _getWeaverLaunchSettings(group) {
  const current = (state.weaver_settings && state.weaver_settings[group]) || {};
  return Object.assign(_defaultWeaverLaunchSettings(), current || {});
}

function _weaverLaunchProviderForReasoning(group) {
  const groupSettings = (state.group_settings && state.group_settings[group]) || {};
  return (
    _getProviderValue('weaver-launch-provider')
    || groupSettings.agent_provider
    || _runtimeDefaultProviderName()
  );
}

function onWeaverLaunchProviderChange() {
  const input = document.getElementById('weaver-launch-boot-cmd');
  if (!input) return;
  const v = document.getElementById('weaver-launch-provider').value;
  const group = _weaverLaunchContext ? _weaverLaunchContext.group : '';
  const groupSettings = (state.group_settings && state.group_settings[group]) || {};
  if (v === '__custom__') {
    input.placeholder = 'e.g. my-weaver-cli';
  } else {
    const effectiveProvider = _getProviderValue('weaver-launch-provider') || groupSettings.agent_provider || '';
    const meta = effectiveProvider ? _findProviderMeta(effectiveProvider) : null;
    input.placeholder = (meta ? meta.command : _runtimeDefaultCommand()) + ' (default)';
  }
  _populateReasoningEffortSelect(
    'weaver-launch-reasoning-effort',
    _weaverLaunchProviderForReasoning(group),
    document.getElementById('weaver-launch-reasoning-effort').value,
    'Provider default',
    'Not supported for this provider'
  );
}

function openWeaverLaunchDialog(group, agentId) {
  if (!group) return;
  const cell = agentId && state.agents ? state.agents[agentId] : null;
  _weaverLaunchContext = {
    group: group,
    agent_id: cell ? cell.id : '',
    mode: cell ? 'relaunch' : 'create',
  };

  const ws = _getWeaverLaunchSettings(group);
  document.getElementById('weaver-launch-title').textContent =
    cell ? 'Relaunch Weaver' : 'Create Weaver';
  document.getElementById('weaver-launch-group').textContent = group;
  document.getElementById('weaver-launch-submit-btn').textContent =
    cell ? 'Save & Relaunch' : 'Create Weaver';

  _populateProviderSelect(
    'weaver-launch-provider',
    ws.weaver_provider || '',
    true
  );
  document.getElementById('weaver-launch-boot-cmd').value = ws.weaver_boot_command || '';
  document.getElementById('weaver-launch-model').value = ws.weaver_model || '';
  document.getElementById('weaver-launch-reasoning-effort').value = ws.weaver_reasoning_effort || '';
  document.getElementById('weaver-launch-custom-instructions').value = ws.custom_instructions || '';
  _setSelectValue('weaver-launch-autonomy-mode', ws.autonomy_mode, 'dispatch_when_clear');
  _setSelectValue(
    'weaver-launch-default-worker-concurrency',
    ws.default_worker_concurrency,
    2
  );
  _setSelectValue(
    'weaver-launch-wave-size-preference',
    ws.wave_size_preference,
    'small'
  );
  _setSelectValue(
    'weaver-launch-same-agent-follow-up-preference',
    ws.same_agent_follow_up_preference,
    'balanced'
  );
  _setSelectValue(
    'weaver-launch-digest-verbosity',
    ws.digest_verbosity,
    'balanced'
  );
  _setSelectValue(
    'weaver-launch-escalation-style',
    ws.escalation_style,
    'note_then_ask'
  );
  onWeaverLaunchProviderChange();

  document.getElementById('modal-weaver-launch').classList.add('visible');
  document.getElementById('weaver-launch-provider').focus();
}

function submitWeaverLaunchDialog() {
  if (!_weaverLaunchContext) return;
  const group = _weaverLaunchContext.group;
  send({
    cmd: 'weaver_update_settings',
    group: group,
    weaver_provider: _getProviderValue('weaver-launch-provider'),
    weaver_boot_command: document.getElementById('weaver-launch-boot-cmd').value.trim(),
    weaver_model: document.getElementById('weaver-launch-model').value.trim(),
    weaver_reasoning_effort: document.getElementById('weaver-launch-reasoning-effort').value,
    custom_instructions: document.getElementById('weaver-launch-custom-instructions').value,
    autonomy_mode: document.getElementById('weaver-launch-autonomy-mode').value,
    default_worker_concurrency: parseInt(document.getElementById('weaver-launch-default-worker-concurrency').value, 10) || 2,
    wave_size_preference: document.getElementById('weaver-launch-wave-size-preference').value,
    same_agent_follow_up_preference: document.getElementById('weaver-launch-same-agent-follow-up-preference').value,
    digest_verbosity: document.getElementById('weaver-launch-digest-verbosity').value,
    escalation_style: document.getElementById('weaver-launch-escalation-style').value,
  });

  if (_weaverLaunchContext.mode === 'create') {
    send({ cmd: 'add_agent', name: 'Weaver', group: group, is_weaver: true });
  } else if (_weaverLaunchContext.agent_id) {
    send({ cmd: 'relaunch_agent', id: _weaverLaunchContext.agent_id });
  }

  _weaverLaunchContext = null;
  closeModals();
}

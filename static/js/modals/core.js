/* Modal framework and shared form helpers. */

/* Modals — add group, add agent/terminal, confirm dialog, color picker */

/* -- Provider cache (populated from get_config response) ------------------ */
let _cachedProviders = [];  // [{name, display_name, command}, ...]

function _providerCommandToken(command) {
  const raw = String(command || '').trim();
  if (!raw) return '';
  return raw.split(/\s+/)[0] || '';
}
function _findProviderMeta(name) {
  return (_cachedProviders || []).find(p => p.name === name) || null;
}

function _detectProviderNameFromCommand(command) {
  const token = _providerCommandToken(command);
  if (!token) return '';
  const match = (_cachedProviders || []).find((p) => _providerCommandToken(p.command) === token);
  return match ? match.name : '';
}

function _runtimeDefaultCommand() {
  return (state && state.runtime && state.runtime.default_command) || 'claude';
}

function _runtimeDefaultProviderName() {
  return _detectProviderNameFromCommand(_runtimeDefaultCommand());
}

const _PROVIDER_CUSTOM_VALUE = '__custom__';

function _providerModels(providerName) {
  const meta = providerName ? _findProviderMeta(providerName) : null;
  return meta && Array.isArray(meta.models) ? meta.models : [];
}

function _findProviderModel(providerName, modelId) {
  const current = String(modelId || '').trim();
  if (!current) return null;
  return _providerModels(providerName).find((model) =>
    String((model && (model.id || model.model)) || '').trim() === current
  ) || null;
}

function _reasoningEffortOptions(rawOptions) {
  if (!Array.isArray(rawOptions)) return [];
  const seen = new Set();
  const options = [];
  rawOptions.forEach((raw) => {
    const value = String(
      raw && typeof raw === 'object'
        ? (raw.value || raw.reasoning_effort || raw.reasoningEffort || '')
        : (raw || '')
    ).trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    options.push({
      value: value,
      description: raw && typeof raw === 'object'
        ? String(raw.description || '').trim()
        : '',
    });
  });
  return options;
}

function _getModelValue(inputId) {
  const input = document.getElementById(inputId);
  return input ? String(input.value || '').trim() : '';
}

function _getReasoningEffortValue(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return '';
  if (sel.value !== _PROVIDER_CUSTOM_VALUE) {
    return String(sel.value || '').trim();
  }
  const custom = document.getElementById(selectId + '-custom');
  return custom ? String(custom.value || '').trim() : '';
}

function _onReasoningEffortSelectChange(selectId) {
  const sel = document.getElementById(selectId);
  const custom = document.getElementById(selectId + '-custom');
  if (!sel || !custom) return;
  const isCustom = sel.value === _PROVIDER_CUSTOM_VALUE;
  custom.classList.toggle('hidden', !isCustom);
  if (isCustom) {
    if (!custom.value && sel.dataset.customValue) {
      custom.value = sel.dataset.customValue;
    }
    if (typeof custom.focus === 'function') custom.focus();
  }
}

function _populateReasoningEffortSelect(
  selectId,
  providerName,
  currentValue,
  emptyLabel,
  unsupportedLabel,
  modelValue,
  preferEmptyLabel
) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const meta = providerName ? _findProviderMeta(providerName) : null;
  const model = _findProviderModel(providerName, modelValue);
  const modelOptions = model
    ? _reasoningEffortOptions(model.reasoning_efforts || model.supported_reasoning_efforts)
    : [];
  const providerOptions = meta ? _reasoningEffortOptions(meta.reasoning_efforts) : [];
  const options = modelOptions.length ? modelOptions : providerOptions;
  const current = String(currentValue || '').trim();
  sel.innerHTML = '';

  const empty = document.createElement('option');
  empty.value = '';
  const modelDefault = model
    ? String(model.default_reasoning_effort || '').trim()
    : '';
  empty.textContent = preferEmptyLabel && emptyLabel
    ? emptyLabel
    : modelDefault
    ? `Model default (${modelDefault})`
    : (emptyLabel || 'Provider default');
  sel.appendChild(empty);

  for (const option of options) {
    const opt = document.createElement('option');
    opt.value = option.value;
    opt.textContent = option.value;
    if (option.description) opt.title = option.description;
    sel.appendChild(opt);
  }

  if (!options.length) {
    empty.textContent = unsupportedLabel || empty.textContent;
    empty.dataset.preserveLabel = 'true';
  }

  const customOption = document.createElement('option');
  customOption.value = _PROVIDER_CUSTOM_VALUE;
  customOption.textContent = 'Custom\u2026';
  sel.appendChild(customOption);

  const known = options.some((option) => option.value === current);
  const custom = document.getElementById(selectId + '-custom');
  if (current && !known) {
    sel.value = _PROVIDER_CUSTOM_VALUE;
    sel.dataset.customValue = current;
    if (custom) {
      custom.value = current;
      custom.classList.remove('hidden');
    }
  } else {
    sel.value = current || '';
    if (custom) {
      custom.classList.add('hidden');
      if (!current) custom.value = '';
    }
  }
}

function _populateModelSelect(
  inputId,
  providerName,
  currentValue,
  emptyLabel,
  effortSelectId,
  currentEffort,
  effortEmptyLabel,
  effortUnsupportedLabel,
  preferEffortEmptyLabel
) {
  const input = document.getElementById(inputId);
  const sel = document.getElementById(inputId + '-select');
  if (!input || !sel) return;
  const models = _providerModels(providerName);
  const current = String(currentValue || '').trim();
  sel.innerHTML = '';

  const empty = document.createElement('option');
  empty.value = '';
  empty.textContent = emptyLabel || 'Provider default';
  sel.appendChild(empty);

  models.forEach((model) => {
    const modelId = String((model && (model.id || model.model)) || '').trim();
    if (!modelId) return;
    const opt = document.createElement('option');
    opt.value = modelId;
    const label = String(model.display_name || model.displayName || modelId).trim();
    opt.textContent = model.is_default ? `${label} (default)` : label;
    if (model.description) opt.title = model.description;
    sel.appendChild(opt);
  });

  const custom = document.createElement('option');
  custom.value = _PROVIDER_CUSTOM_VALUE;
  custom.textContent = 'Custom\u2026';
  sel.appendChild(custom);

  const known = models.some((model) =>
    String((model && (model.id || model.model)) || '').trim() === current
  );
  input.value = current;
  input.dataset.providerName = providerName || '';
  input.dataset.effortSelectId = effortSelectId || '';
  input.dataset.effortEmptyLabel = effortEmptyLabel || 'Provider default';
  input.dataset.effortUnsupportedLabel =
    effortUnsupportedLabel || 'Not supported for this provider';
  input.dataset.preferEffortEmptyLabel = preferEffortEmptyLabel ? 'true' : '';
  if (current && !known) {
    sel.value = _PROVIDER_CUSTOM_VALUE;
    input.dataset.customValue = current;
    input.classList.remove('hidden');
  } else {
    sel.value = current || '';
    input.classList.add('hidden');
  }

  if (effortSelectId) {
    _populateReasoningEffortSelect(
      effortSelectId,
      providerName,
      currentEffort,
      input.dataset.effortEmptyLabel,
      input.dataset.effortUnsupportedLabel,
      current,
      input.dataset.preferEffortEmptyLabel === 'true'
    );
  }
}

function _onModelSelectChange(inputId) {
  const input = document.getElementById(inputId);
  const sel = document.getElementById(inputId + '-select');
  if (!input || !sel) return;
  const isCustom = sel.value === _PROVIDER_CUSTOM_VALUE;
  if (isCustom) {
    input.value = input.dataset.customValue || '';
    input.classList.remove('hidden');
    if (typeof input.focus === 'function') input.focus();
  } else {
    input.value = sel.value || '';
    input.classList.add('hidden');
  }
  const effortSelectId = input.dataset.effortSelectId || '';
  if (effortSelectId) {
    _populateReasoningEffortSelect(
      effortSelectId,
      input.dataset.providerName || '',
      _getReasoningEffortValue(effortSelectId),
      input.dataset.effortEmptyLabel || 'Provider default',
      input.dataset.effortUnsupportedLabel || 'Not supported for this provider',
      _getModelValue(inputId),
      input.dataset.preferEffortEmptyLabel === 'true'
    );
  }
  refreshGsInheritedLaunchPlaceholders();
}

function _onCustomModelInput(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  input.dataset.customValue = input.value || '';
  const effortSelectId = input.dataset.effortSelectId || '';
  if (effortSelectId) {
    _populateReasoningEffortSelect(
      effortSelectId,
      input.dataset.providerName || '',
      _getReasoningEffortValue(effortSelectId),
      input.dataset.effortEmptyLabel || 'Provider default',
      input.dataset.effortUnsupportedLabel || 'Not supported for this provider',
      _getModelValue(inputId),
      input.dataset.preferEffortEmptyLabel === 'true'
    );
  }
  refreshGsInheritedLaunchPlaceholders();
}

function _agentSettingsProviderForReasoning() {
  return _getProviderValue('gs-agent-provider') || _runtimeDefaultProviderName();
}

function _engineerProviderForReasoning() {
  return (
    _getProviderValue('gs-engineer-provider')
    || _getProviderValue('gs-agent-provider')
    || _runtimeDefaultProviderName()
  );
}

function _workerProviderForReasoning() {
  return (
    _getProviderValue('gs-worker-provider')
    || _getProviderValue('gs-agent-provider')
    || _runtimeDefaultProviderName()
  );
}

function _architectProviderForReasoning() {
  return (
    _getProviderValue('gs-architect-provider')
    || _getProviderValue('gs-agent-provider')
    || _runtimeDefaultProviderName()
  );
}

function _gsInputValue(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || '').trim() : '';
}

function _providerDefaultCommand(providerName) {
  const meta = providerName ? _findProviderMeta(providerName) : null;
  return meta ? meta.command : _runtimeDefaultCommand();
}

function _inheritSettingLabel(value) {
  return 'Inherit \u00b7 ' + (String(value || '').trim() || 'system default');
}

function _providerDisplayName(providerName) {
  const meta = providerName ? _findProviderMeta(providerName) : null;
  return meta
    ? String(meta.display_name || meta.name || providerName)
    : (String(providerName || '').trim() || 'system default');
}

function _gsGroupProviderPreview() {
  return _providerDisplayName(
    _getProviderValue('gs-agent-provider') || _runtimeDefaultProviderName()
  );
}

function _gsGroupDefaultModelPreview() {
  const modelId = _gsInputValue('gs-agent-model');
  if (modelId) {
    const model = _findProviderModel(_agentSettingsProviderForReasoning(), modelId);
    return model
      ? String(model.display_name || model.displayName || modelId).trim()
      : modelId;
  }
  const defaultModel = _providerModels(_agentSettingsProviderForReasoning())
    .find((model) => model && model.is_default);
  return defaultModel
    ? String(defaultModel.display_name || defaultModel.displayName
      || defaultModel.id || defaultModel.model).trim()
    : 'provider default';
}

function _gsGroupDefaultReasoningPreview() {
  const effort = _getReasoningEffortValue('gs-agent-reasoning-effort');
  if (effort) return effort;
  const model = _findProviderModel(
    _agentSettingsProviderForReasoning(),
    _getModelValue('gs-agent-model')
  );
  return model && model.default_reasoning_effort
    ? String(model.default_reasoning_effort).trim()
    : 'provider default';
}

function _gsGroupDefaultCommandPreview(providerName) {
  return _gsInputValue('gs-agent-boot-cmd')
    || _providerDefaultCommand(providerName);
}

function _setInputPlaceholder(id, text) {
  const el = document.getElementById(id);
  if (el) el.placeholder = text;
}

function _setSelectEmptyOptionLabel(id, text) {
  const el = document.getElementById(id);
  if (!el || !el.children) return;
  const empty = Array.from(el.children).find((option) => option.value === '');
  if (empty && (!empty.dataset || empty.dataset.preserveLabel !== 'true')) {
    empty.textContent = text;
  }
}

function refreshGsInheritedLaunchPlaceholders() {
  const modelPreview = _gsGroupDefaultModelPreview();
  const reasoningPreview = _gsGroupDefaultReasoningPreview();
  const providerPreview = _gsGroupProviderPreview();
  const groupDirectory = _gsInputValue('gs-directory') || 'current directory';
  const runtimeDirectory = _gsInputValue('gs-agent-directory') || groupDirectory;
  const groupShell = _gsInputValue('gs-shell') || 'system default';
  const runtimeShell = _gsInputValue('gs-agent-shell') || groupShell;
  const groupEnvFile = _gsInputValue('gs-env-file') || 'none';

  ['gs-worker-provider', 'gs-engineer-provider', 'gs-architect-provider']
    .forEach((id) => _setSelectEmptyOptionLabel(
      id,
      _inheritSettingLabel(providerPreview)
    ));
  ['gs-worker-model-select', 'gs-engineer-model-select', 'gs-architect-model-select']
    .forEach((id) => _setSelectEmptyOptionLabel(
      id,
      _inheritSettingLabel(modelPreview)
    ));
  ['gs-worker-reasoning-effort', 'gs-engineer-reasoning-effort',
    'gs-architect-reasoning-effort']
    .forEach((id) => _setSelectEmptyOptionLabel(
      id,
      _inheritSettingLabel(reasoningPreview)
    ));

  _setInputPlaceholder(
    'gs-worker-boot-command',
    _inheritSettingLabel(_gsGroupDefaultCommandPreview(_workerProviderForReasoning()))
  );
  _setInputPlaceholder(
    'gs-engineer-boot-cmd',
    _inheritSettingLabel(_gsGroupDefaultCommandPreview(_engineerProviderForReasoning()))
  );
  _setInputPlaceholder(
    'gs-architect-boot-cmd',
    _inheritSettingLabel(_gsGroupDefaultCommandPreview(_architectProviderForReasoning()))
  );
  _setInputPlaceholder('gs-agent-directory', _inheritSettingLabel(groupDirectory));
  _setInputPlaceholder('gs-engineer-directory', _inheritSettingLabel(runtimeDirectory));
  _setInputPlaceholder('gs-architect-directory', _inheritSettingLabel(runtimeDirectory));
  _setInputPlaceholder('gs-agent-env-file', _inheritSettingLabel(groupEnvFile));
  _setSelectEmptyOptionLabel('gs-agent-shell', _inheritSettingLabel(groupShell));
  _setSelectEmptyOptionLabel('gs-engineer-shell', _inheritSettingLabel(runtimeShell));
  _setSelectEmptyOptionLabel('gs-architect-shell', _inheritSettingLabel(runtimeShell));
}

function _populateProviderSelect(selectId, currentValue, includeGroupDefault) {
  const sel = document.getElementById(selectId);
  sel.innerHTML = '';
  if (includeGroupDefault) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = _inheritSettingLabel(_gsGroupProviderPreview());
    sel.appendChild(opt);
  } else {
    const opt = document.createElement('option');
    const defaultProvider = _findProviderMeta(_runtimeDefaultProviderName());
    opt.value = '';
    opt.textContent = defaultProvider
      ? `Default (${defaultProvider.display_name})`
      : 'Default (Claude Code)';
    sel.appendChild(opt);
  }
  for (const p of _cachedProviders) {
    const opt = document.createElement('option');
    opt.value = p.name; opt.textContent = p.display_name;
    sel.appendChild(opt);
  }
  const cust = document.createElement('option');
  cust.value = '__custom__'; cust.textContent = 'Custom\u2026';
  sel.appendChild(cust);
  sel.value = currentValue || '';
}

function _getProviderValue(selectId) {
  const el = document.getElementById(selectId);
  const v = el ? el.value : '';
  return v === '__custom__' ? '' : v;
}

function _getProviderCommand(selectId) {
  const el = document.getElementById(selectId);
  const v = el ? el.value : '';
  if (!v) return _runtimeDefaultCommand();
  const p = _cachedProviders.find(p => p.name === v);
  return p ? p.command : '';
}

function _populateTemplateSelect(selectId, currentValue, emptyLabel) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  sel.innerHTML = '';
  const opt = document.createElement('option');
  opt.value = '';
  opt.textContent = emptyLabel || 'None';
  sel.appendChild(opt);
  const templates = (_cachedAgentTemplates || []).filter(t => !t.shadowed);
  const project = templates.filter(t => !t.global);
  const user = templates.filter(t => t.global);
  function appendGroup(label, items) {
    if (!items.length) return;
    const group = document.createElement('optgroup');
    group.label = label;
    for (const t of items) {
      const o = document.createElement('option');
      o.value = t.name;
      o.textContent = t.display_name || t.name;
      group.appendChild(o);
    }
    sel.appendChild(group);
  }
  appendGroup('Project', project);
  appendGroup('User', user);
  sel.value = currentValue || '';
}

function _findTemplateMeta(name) {
  return (_cachedAgentTemplates || []).find(t => t.name === name) || null;
}

function onGsProviderChange(currentEffort) {
  const v = document.getElementById('gs-agent-provider').value;
  const row = document.getElementById('gs-agent-boot-cmd-row');
  const label = row.querySelector('label');
  const input = document.getElementById('gs-agent-boot-cmd');
  row.classList.remove('hidden');
  if (v === '__custom__') {
    label.innerHTML = 'Command <span class="label-hint">required for custom providers</span>';
    input.placeholder = 'e.g. my-agent-cli';
  } else {
    label.innerHTML = 'Command override <span class="label-hint">advanced</span>';
    input.placeholder = _getProviderCommand('gs-agent-provider') + ' (default)';
  }
  const providerName = _agentSettingsProviderForReasoning();
  _populateModelSelect(
    'gs-agent-model',
    providerName,
    _getModelValue('gs-agent-model'),
    'Provider default',
    'gs-agent-reasoning-effort',
    currentEffort == null
      ? _getReasoningEffortValue('gs-agent-reasoning-effort')
      : currentEffort
  );
  refreshGsInheritedLaunchPlaceholders();
  _syncCodexFastMode('gs-agent-fast-mode-row', providerName);
  if (!_getProviderValue('gs-worker-provider')) {
    onGsWorkerProviderChange();
  }
  if (!_getProviderValue('gs-engineer-provider')) {
    onGsEngineerProviderChange();
  }
  if (!_getProviderValue('gs-architect-provider')) {
    onGsArchitectProviderChange();
  }
}

function onAddProviderChange(currentEffort) {
  const v = document.getElementById('add-provider-select').value;
  const cmdRow = document.getElementById('add-cmd-row');
  const label = cmdRow.querySelector('label');
  const input = document.getElementById('add-cmd-input');
  cmdRow.classList.remove('hidden');
  document.getElementById('add-model-row').classList.remove('hidden');
  document.getElementById('add-reasoning-row').classList.remove('hidden');
  _syncCodexFastMode('add-fast-mode-row', _getProviderValue('add-provider-select') || _runtimeDefaultProviderName());
  if (v === '__custom__') {
    label.textContent = 'Boot command';
    input.placeholder = 'e.g. npm run dev';
  } else {
    label.textContent = 'Command override';
    input.placeholder = _getProviderCommand('add-provider-select') + ' (default)';
  }
  const providerName = _getProviderValue('add-provider-select') || _runtimeDefaultProviderName();
  _populateModelSelect(
    'add-model-input',
    providerName,
    _getModelValue('add-model-input'),
    'Provider default',
    'add-reasoning-effort',
    currentEffort == null
      ? _getReasoningEffortValue('add-reasoning-effort')
      : currentEffort
  );
}

function onGsEngineerProviderChange(currentEffort) {
  _populateModelSelect(
    'gs-engineer-model',
    _engineerProviderForReasoning(),
    _getModelValue('gs-engineer-model'),
    _inheritSettingLabel(_gsGroupDefaultModelPreview()),
    'gs-engineer-reasoning-effort',
    currentEffort == null
      ? _getReasoningEffortValue('gs-engineer-reasoning-effort')
      : currentEffort,
    _inheritSettingLabel(_gsGroupDefaultReasoningPreview()),
    'Not supported for this provider',
    true
  );
  refreshGsInheritedLaunchPlaceholders();
  _syncCodexFastMode('gs-engineer-fast-mode-row', _engineerProviderForReasoning());
}

function onGsWorkerProviderChange(currentEffort) {
  const reasoning = document.getElementById('gs-worker-reasoning-effort');
  if (!reasoning) return;
  _populateModelSelect(
    'gs-worker-model',
    _workerProviderForReasoning(),
    _getModelValue('gs-worker-model'),
    _inheritSettingLabel(_gsGroupDefaultModelPreview()),
    'gs-worker-reasoning-effort',
    currentEffort == null
      ? _getReasoningEffortValue('gs-worker-reasoning-effort')
      : currentEffort,
    _inheritSettingLabel(_gsGroupDefaultReasoningPreview()),
    'Not supported for this provider',
    true
  );
  refreshGsInheritedLaunchPlaceholders();
  _syncCodexFastMode('gs-worker-fast-mode-row', _workerProviderForReasoning());
}

function onGsArchitectProviderChange(currentEffort) {
  _populateModelSelect(
    'gs-architect-model',
    _architectProviderForReasoning(),
    _getModelValue('gs-architect-model'),
    _inheritSettingLabel(_gsGroupDefaultModelPreview()),
    'gs-architect-reasoning-effort',
    currentEffort == null
      ? _getReasoningEffortValue('gs-architect-reasoning-effort')
      : currentEffort,
    _inheritSettingLabel(_gsGroupDefaultReasoningPreview()),
    'Not supported for this provider',
    true
  );
  refreshGsInheritedLaunchPlaceholders();
  _syncCodexFastMode('gs-architect-fast-mode-row', _architectProviderForReasoning());
}

/* -- Hint popover (for ? buttons) ---------------------------------------- */
function toggleHint(btn) {
  const existing = document.querySelector('.hint-pop');
  if (existing) { existing.remove(); if (existing._src === btn) return; }
  const pop = document.createElement('div');
  pop.className = 'hint-pop';
  pop.textContent = btn.dataset.hint;
  pop._src = btn;
  document.body.appendChild(pop);
  const r = btn.getBoundingClientRect();
  pop.style.left = Math.max(4, Math.min(r.left, window.innerWidth - pop.offsetWidth - 4)) + 'px';
  pop.style.top = (r.top - pop.offsetHeight - 6) + 'px';
  setTimeout(() => {
    function dismiss(e) {
      if (e.target === btn) return;
      pop.remove();
      document.removeEventListener('click', dismiss, true);
    }
    document.addEventListener('click', dismiss, true);
  }, 0);
}

let _confirmResolve = null;
let _inputDialogResolve = null;
let _inputDialogFields = [];
let _inputDialogFieldElements = {};
let _inputDialogFocusRestore = null;
let _addEngineerGroup = '';
let _addEngineerArchitectId = '';
let _addEngineerSpecs = [];
let _addEngineerSpecializationsGroup = null;

// Shared focus/ARIA behavior for custom dialog overlays.
//
// Keep this helper intentionally small: it owns initial focus, focus restore,
// Escape/cancel, optional Enter submit for simple flows, Tab containment, and
// basic dialog ARIA attributes. The first adopters are the custom confirm/input,
// New Group, Edit Agent, and Engineer launch dialogs. Larger multi-section
// modals (task, group settings, artifacts, behavior approval, diff preview) are
// deferred so each can opt in with its own submit/cancel semantics.
let _modalDialogControllers = new Map();

function _modalDialogOverlay(target) {
  if (!target) return null;
  if (typeof target === 'string') return document.getElementById(target);
  return target;
}

function _modalDialogPanel(overlay) {
  if (!overlay) return null;
  if (overlay.classList && overlay.classList.contains('modal')) return overlay;
  if (typeof overlay.querySelector === 'function') {
    return overlay.querySelector('.modal') || overlay;
  }
  return overlay;
}

function _modalDialogSetAttr(el, name, value) {
  if (!el || !name) return;
  if (value === null || value === undefined || value === '') {
    if (typeof el.removeAttribute === 'function') el.removeAttribute(name);
    else if (el.attributes) delete el.attributes[name];
    return;
  }
  if (typeof el.setAttribute === 'function') el.setAttribute(name, String(value));
  else {
    el.attributes = el.attributes || {};
    el.attributes[name] = String(value);
  }
}

function _modalDialogGetAttr(el, name) {
  if (!el || !name) return '';
  if (typeof el.getAttribute === 'function') return el.getAttribute(name) || '';
  return el.attributes ? (el.attributes[name] || '') : '';
}

function _modalDialogEnsureTitleId(panel) {
  if (!panel || typeof panel.querySelector !== 'function') return '';
  const title = panel.querySelector('h1,h2,h3,[data-modal-title]');
  if (!title) return '';
  if (!title.id) {
    const overlay = panel.parentNode && panel.parentNode.id ? panel.parentNode.id : 'modal-dialog';
    title.id = overlay + '-title';
  }
  return title.id || '';
}

function _modalDialogApplyAria(panel, opts) {
  if (!panel) return;
  opts = opts || {};
  _modalDialogSetAttr(panel, 'role', opts.role || _modalDialogGetAttr(panel, 'role') || 'dialog');
  _modalDialogSetAttr(panel, 'aria-modal', 'true');
  const labelledBy = opts.labelledBy || _modalDialogGetAttr(panel, 'aria-labelledby')
    || (opts.label ? '' : _modalDialogEnsureTitleId(panel));
  if (labelledBy) {
    _modalDialogSetAttr(panel, 'aria-labelledby', labelledBy);
    if (!opts.label) _modalDialogSetAttr(panel, 'aria-label', null);
  } else {
    if (opts.label) _modalDialogSetAttr(panel, 'aria-label', opts.label);
  }
  if (opts.describedBy) _modalDialogSetAttr(panel, 'aria-describedby', opts.describedBy);
}

function _modalDialogIsFocusable(el) {
  if (!el || typeof el.focus !== 'function') return false;
  if (el.disabled) return false;
  if (el.hidden) return false;
  if (el.classList && el.classList.contains('hidden')) return false;
  return true;
}

function _modalDialogFocusable(panel) {
  if (!panel || typeof panel.querySelectorAll !== 'function') return [];
  const selector = [
    'a[href]',
    'button',
    'input',
    'select',
    'textarea',
    '[tabindex]:not([tabindex="-1"])',
    '[contenteditable="true"]',
  ].join(',');
  return Array.prototype.slice.call(panel.querySelectorAll(selector))
    .filter(_modalDialogIsFocusable);
}

function _modalDialogResolveFocus(panel, initialFocus) {
  let target = null;
  if (typeof initialFocus === 'function') {
    try { target = initialFocus(panel); } catch (_e) { target = null; }
  } else if (typeof initialFocus === 'string' && panel && typeof panel.querySelector === 'function') {
    target = panel.querySelector(initialFocus);
  } else if (initialFocus && typeof initialFocus.focus === 'function') {
    target = initialFocus;
  }
  if (target && _modalDialogIsFocusable(target)) return target;
  if (panel && typeof panel.querySelector === 'function') {
    target = panel.querySelector('[autofocus]');
    if (target && _modalDialogIsFocusable(target)) return target;
  }
  const focusable = _modalDialogFocusable(panel);
  if (focusable.length) return focusable[0];
  return panel && typeof panel.focus === 'function' ? panel : null;
}

function _modalDialogFocusInitial(controller) {
  if (!controller || controller.closed) return;
  const target = _modalDialogResolveFocus(controller.panel, controller.opts.initialFocus);
  if (!target) return;
  try { target.focus(); } catch (_e) {}
  if (controller.opts.selectInitialFocus && typeof target.select === 'function') {
    try { target.select(); } catch (_e) {}
  }
}

function _modalDialogRestoreFocus(target) {
  if (!target || typeof target.focus !== 'function') return;
  try { target.focus(); } catch (_e) {}
}

function _modalDialogShouldSubmitOnEnter(event) {
  if (!event || event.key !== 'Enter') return false;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return false;
  const target = event.target || document.activeElement;
  const tag = target && target.tagName ? String(target.tagName).toUpperCase() : '';
  if (tag === 'TEXTAREA') return false;
  if (target && target.isContentEditable) return false;
  return true;
}

function openModalDialog(target, opts) {
  opts = opts || {};
  const overlay = _modalDialogOverlay(target);
  if (!overlay) return null;
  const existing = _modalDialogControllers.get(overlay);
  if (existing) existing.cleanup({ restoreFocus: false });
  const panel = _modalDialogPanel(overlay);
  _modalDialogApplyAria(panel, opts);
  if (overlay.classList && opts.show !== false) overlay.classList.add('visible');
  if (overlay.classList && opts.nested) overlay.classList.add('modal-nested');

  const controller = {
    overlay: overlay,
    panel: panel,
    opts: opts,
    restoreTarget: opts.restoreFocusTo || document.activeElement || null,
    closed: false,
    cleanup: null,
    close: null,
  };

  const keydown = function(event) {
    if (!event) return;
    if (event.key === 'Escape' && opts.cancelOnEscape !== false) {
      if (event.preventDefault) event.preventDefault();
      if (event.stopPropagation) event.stopPropagation();
      if (typeof opts.onCancel === 'function') {
        opts.onCancel(event);
      } else {
        closeModalDialog(overlay, { restoreFocus: true });
      }
      return;
    }
    if (event.key === 'Tab' && opts.trapFocus !== false) {
      const focusable = _modalDialogFocusable(panel);
      if (!focusable.length) {
        if (event.preventDefault) event.preventDefault();
        return;
      }
      const active = document.activeElement;
      let idx = focusable.indexOf(active);
      if (event.shiftKey) {
        if (idx <= 0) idx = focusable.length;
        idx -= 1;
      } else {
        idx = idx < 0 || idx >= focusable.length - 1 ? 0 : idx + 1;
      }
      if (event.preventDefault) event.preventDefault();
      try { focusable[idx].focus(); } catch (_e) {}
      return;
    }
    if (opts.submitOnEnter && _modalDialogShouldSubmitOnEnter(event)) {
      if (event.preventDefault) event.preventDefault();
      if (event.stopPropagation) event.stopPropagation();
      if (typeof opts.onSubmit === 'function') opts.onSubmit(event);
    }
  };

  controller.cleanup = function(cleanupOpts) {
    cleanupOpts = cleanupOpts || {};
    if (controller.closed) return;
    controller.closed = true;
    if (overlay && typeof overlay.removeEventListener === 'function') {
      overlay.removeEventListener('keydown', keydown, true);
    }
    _modalDialogControllers.delete(overlay);
    if (cleanupOpts.restoreFocus !== false) {
      _modalDialogRestoreFocus(controller.restoreTarget);
    }
  };
  controller.close = function(closeOpts) {
    closeOpts = closeOpts || {};
    controller.cleanup(closeOpts);
    if (overlay.classList && closeOpts.hide !== false) {
      overlay.classList.remove('visible');
      overlay.classList.remove('modal-nested');
    }
  };

  _modalDialogControllers.set(overlay, controller);
  if (overlay && typeof overlay.addEventListener === 'function') {
    overlay.addEventListener('keydown', keydown, true);
  }
  _modalDialogFocusInitial(controller);
  return controller;
}

function closeModalDialog(target, opts) {
  opts = opts || {};
  const overlay = _modalDialogOverlay(target);
  if (!overlay) return false;
  const controller = _modalDialogControllers.get(overlay);
  if (controller) {
    controller.close(opts);
    return true;
  }
  if (overlay.classList && opts.hide !== false) {
    overlay.classList.remove('visible');
    overlay.classList.remove('modal-nested');
  }
  if (opts.restoreFocusTo) _modalDialogRestoreFocus(opts.restoreFocusTo);
  return false;
}

function cleanupModalDialogs(opts) {
  Array.from(_modalDialogControllers.keys()).forEach(function(overlay) {
    closeModalDialog(overlay, opts || {});
  });
}

// Modal stack for nested modals. When a nested modal is opened on top of
// another (e.g. "New specialization" inside the engineer-launch dialog),
// the opener pushes onto this stack via openNestedModal(), and Cancel/
// Escape pops only the topmost entry instead of dismissing the parent.
let _modalStack = [];

function openNestedModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  // Raise above the parent overlay regardless of DOM order. Without this
  // class, two .visible overlays at the same z-index render in document
  // order — a parent declared later in the DOM (e.g. Group Settings) would
  // paint on top of an earlier-declared child like #modal-new-specialization.
  openModalDialog(el, {
    nested: true,
    onCancel: function() { closeNestedModal(id); },
  });
  if (_modalStack.indexOf(id) === -1) _modalStack.push(id);
}

function closeNestedModal(id) {
  // If id omitted, pop the topmost. Otherwise remove the matching entry.
  let target = id;
  if (!target) {
    target = _modalStack.length ? _modalStack[_modalStack.length - 1] : '';
  }
  if (!target) return false;
  const el = document.getElementById(target);
  if (el) {
    closeModalDialog(el, { restoreFocus: true });
  }
  const idx = _modalStack.lastIndexOf(target);
  if (idx >= 0) _modalStack.splice(idx, 1);
  return true;
}

function setWorktreeDiffModalVisible(visible) {
  const root = document.getElementById('diff-view-root');
  if (!root) return null;
  if (visible) {
    root.classList.add('overlay');
    root.classList.add('visible');
    root.onclick = function(event) {
      if (event && event.target === root && typeof hideDiffView === 'function') {
        hideDiffView();
      }
    };
  } else {
    root.classList.remove('visible');
    root.classList.remove('overlay');
    root.classList.remove('modal-nested');
    root.onclick = null;
  }
  return root;
}

function closeModals() {
  // Nested-modal stack: pop only the topmost when one is active so Cancel/
  // Escape doesn't dismiss the parent dialog underneath.
  if (_modalStack.length > 0) {
    const topId = _modalStack.pop();
    const el = document.getElementById(topId);
    if (el) {
      closeModalDialog(el, { restoreFocus: true });
    }
    return;
  }
  var taskModal = document.getElementById('modal-task');
  if (taskModal && taskModal.classList.contains('visible') && typeof _taskClearDraft === 'function') {
    _taskClearDraft(_taskEditId, _taskDraftScope);
    _taskDraftScope = 'create';
  }
  // Clean up draft attachments if task modal was open in create mode
  if (typeof _cleanupDraftAttachments === 'function') _cleanupDraftAttachments();
  if (typeof _taskHistoryOpen !== 'undefined' && _taskHistoryOpen
      && typeof hideTaskHistory === 'function') {
    hideTaskHistory();
  }
  var diffModalOpen = typeof _diffViewOpen !== 'undefined' && _diffViewOpen;
  if (diffModalOpen) {
    var closedOverlayAboveDiff = false;
    document.querySelectorAll('.overlay').forEach(o => {
      if (o && o.id === 'diff-view-root') return;
      if (o && o.classList.contains('visible')) {
        closeModalDialog(o, { restoreFocus: true });
        closedOverlayAboveDiff = true;
      }
    });
    document.querySelectorAll('.hint-pop').forEach(p => p.remove());
    if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
    if (closedOverlayAboveDiff) return;
    if (typeof hideDiffView === 'function') {
      hideDiffView();
      return;
    }
  }
  document.querySelectorAll('.overlay').forEach(o => {
    closeModalDialog(o, { restoreFocus: true });
  });
  document.querySelectorAll('.hint-pop').forEach(p => p.remove());
  if (_confirmResolve) { _confirmResolve(false); _confirmResolve = null; }
  if (_inputDialogResolve) {
    const resolve = _inputDialogResolve;
    _inputDialogResolve = null;
    _inputDialogFields = [];
    _inputDialogFieldElements = {};
    resolve(null);
    _restoreInputDialogFocus();
  }
  if (typeof _glsCapturing !== 'undefined' && _glsCapturing) _cancelCapture();
  // Display-once relay device-link (TORQUE:603 #3): drop any minted secret +
  // confirm gesture so nothing transient survives the modal close.
  if (typeof _relayDeviceLinkReset === 'function') _relayDeviceLinkReset();
  // Daemon-credential pairing token is a one-time secret pasted into the modal;
  // do not leave it in the DOM after close.
  if (typeof _relayDaemonCredentialReset === 'function') _relayDaemonCredentialReset();
  _modalStack = [];
  _addEngineerGroup = '';
  _addEngineerArchitectId = '';
  _addEngineerSpecs = [];
  _addEngineerSpecializationsGroup = null;
  _addArchitectGroup = '';
  _pendingHireRejectId = '';
  _architectDecisionModalArchitectId = '';
}

/* -- Confirm dialog (replaces window.confirm for WKWebView) ----------- */
function showConfirm(message, opts) {
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    document.getElementById('confirm-message').textContent = message;
    const title = document.getElementById('confirm-title');
    if (title) title.textContent = (opts && opts.title) || 'Confirm action';
    const extras = document.getElementById('confirm-extras');
    extras.innerHTML = '';
    if (opts && opts.checkboxes) {
      for (const cb of opts.checkboxes) {
        const lbl = document.createElement('label');
        lbl.className = 'gs-checkbox';
        const inp = document.createElement('input');
        inp.type = 'checkbox';
        inp.checked = !!cb.checked;
        inp.dataset.key = cb.key;
        lbl.appendChild(inp);
        lbl.appendChild(document.createTextNode(cb.label));
        extras.appendChild(lbl);
      }
    }
    const btn = document.getElementById('confirm-yes-btn');
    const defaultLabel = /^\s*Delete\b/.test(String(message || '')) ? 'Delete' : 'OK';
    btn.textContent = (opts && opts.label) || defaultLabel;
    btn.className = 'btn-primary ' + ((opts && opts.variant) || 'btn-danger');
    openModalDialog('modal-confirm', {
      role: (opts && opts.role) || 'alertdialog',
      labelledBy: 'confirm-title',
      describedBy: 'confirm-message',
      initialFocus: '#confirm-yes-btn',
      cancelOnEscape: true,
      onCancel: confirmNo,
    });
  });
}
function _confirmResult(accepted) {
  closeModalDialog('modal-confirm', { restoreFocus: true });
  if (!_confirmResolve) return;
  if (!accepted) { _confirmResolve(false); _confirmResolve = null; return; }
  const extras = document.getElementById('confirm-extras');
  const boxes = extras.querySelectorAll('input[type="checkbox"]');
  if (boxes.length === 0) { _confirmResolve(true); _confirmResolve = null; return; }
  const result = {};
  for (const b of boxes) result[b.dataset.key] = b.checked;
  _confirmResolve(result);
  _confirmResolve = null;
}
function confirmYes() { _confirmResult(true); }
function confirmNo() { _confirmResult(false); }

/* -- Input dialog (replaces window.prompt for routine operator flows) --- */
function _restoreInputDialogFocus() {
  const target = _inputDialogFocusRestore;
  _inputDialogFocusRestore = null;
  if (target && typeof target.focus === 'function') {
    try { target.focus(); } catch (_e) {}
  }
}

function _inputDialogFieldId(key) {
  return 'input-dialog-field-' + String(key || 'value').replace(/[^a-zA-Z0-9_-]/g, '-');
}

function _setInputDialogError(message) {
  const error = document.getElementById('input-dialog-error');
  if (!error) return;
  error.textContent = String(message || '');
  error.classList.toggle('hidden', !message);
}

function showInputDialog(opts) {
  opts = opts || {};
  return new Promise((resolve) => {
    const modal = document.getElementById('modal-input-dialog');
    const title = document.getElementById('input-dialog-title');
    const summary = document.getElementById('input-dialog-summary');
    const fieldsRoot = document.getElementById('input-dialog-fields');
    const submitBtn = document.getElementById('input-dialog-submit-btn');
    if (!modal || !fieldsRoot || !submitBtn) {
      resolve(null);
      return;
    }
    if (_inputDialogResolve) {
      const previousResolve = _inputDialogResolve;
      _inputDialogResolve = null;
      previousResolve(null);
    }
    _inputDialogResolve = resolve;
    _inputDialogFocusRestore = document.activeElement || null;
    _inputDialogFields = Array.isArray(opts.fields) && opts.fields.length
      ? opts.fields.slice()
      : [{ key: 'value', label: opts.label || '', defaultValue: opts.defaultValue || '' }];
    _inputDialogFieldElements = {};
    if (title) title.textContent = String(opts.title || 'Input');
    if (summary) {
      summary.textContent = String(opts.summary || '');
      summary.classList.toggle('hidden', !opts.summary);
    }
    _setInputDialogError('');
    fieldsRoot.innerHTML = '';
    let firstInput = null;
    let autofocusInput = null;
    for (let i = 0; i < _inputDialogFields.length; i++) {
      const field = _inputDialogFields[i] || {};
      const key = String(field.key || ('value' + i));
      const id = _inputDialogFieldId(key);
      const label = document.createElement('label');
      label.setAttribute('for', id);
      label.textContent = String(field.label || key);
      fieldsRoot.appendChild(label);
      const input = document.createElement(field.multiline ? 'textarea' : 'input');
      input.id = id;
      if (!field.multiline) input.type = field.type || 'text';
      input.value = String(
        field.defaultValue === null || field.defaultValue === undefined ? '' : field.defaultValue
      );
      input.placeholder = String(field.placeholder || '');
      input.autocomplete = field.autocomplete || 'off';
      input.addEventListener('keydown', function(event) {
        if (!event) return;
        if (event.key === 'Escape') {
          if (event.preventDefault) event.preventDefault();
          cancelInputDialog();
          return;
        }
        if (event.key === 'Enter' && !field.multiline) {
          if (event.preventDefault) event.preventDefault();
          submitInputDialog();
        }
      });
      fieldsRoot.appendChild(input);
      _inputDialogFieldElements[key] = input;
      if (!firstInput) firstInput = input;
      if (field.autofocus) autofocusInput = input;
    }
    submitBtn.textContent = String(opts.submitLabel || 'OK');
    submitBtn.className = 'btn-primary ' + (opts.variant || '');
    const focusTarget = autofocusInput || firstInput;
    openModalDialog(modal, {
      role: 'dialog',
      labelledBy: 'input-dialog-title',
      describedBy: opts.summary ? 'input-dialog-summary' : '',
      initialFocus: focusTarget,
      selectInitialFocus: true,
      submitOnEnter: true,
      onSubmit: submitInputDialog,
      onCancel: cancelInputDialog,
    });
  });
}

function submitInputDialog() {
  if (!_inputDialogResolve) return;
  const values = {};
  for (let i = 0; i < _inputDialogFields.length; i++) {
    const field = _inputDialogFields[i] || {};
    const key = String(field.key || ('value' + i));
    const input = _inputDialogFieldElements[key];
    values[key] = input ? String(input.value || '') : '';
    if (field.required && !values[key].trim()) {
      _setInputDialogError((field.label || key) + ' is required.');
      if (input && typeof input.focus === 'function') input.focus();
      return;
    }
  }
  const resolve = _inputDialogResolve;
  _inputDialogResolve = null;
  _inputDialogFields = [];
  _inputDialogFieldElements = {};
  const modal = document.getElementById('modal-input-dialog');
  if (modal) closeModalDialog(modal, { restoreFocus: true });
  _setInputDialogError('');
  resolve(values);
}

function cancelInputDialog() {
  if (!_inputDialogResolve) return;
  const resolve = _inputDialogResolve;
  _inputDialogResolve = null;
  _inputDialogFields = [];
  _inputDialogFieldElements = {};
  const modal = document.getElementById('modal-input-dialog');
  if (modal) closeModalDialog(modal, { restoreFocus: true });
  _setInputDialogError('');
  resolve(null);
}

/* -- Add Group -------------------------------------------------------- */
function openAddGroup() {
  const modal = document.getElementById('modal-group');
  const summary = document.getElementById('modal-group-summary');
  if (summary) {
    const standalone = !!(state && state.runtime && state.runtime.embedded_terminal);
    summary.textContent = standalone
      ? 'Create the workspace first — Torque will open its settings next.'
      : '';
    summary.classList.toggle('hidden', !standalone);
  }
  const inp = document.getElementById('group-name-input');
  inp.value = '';
  const dir = document.getElementById('group-directory-input');
  if (dir) dir.value = '';
  openModalDialog(modal, {
    labelledBy: 'modal-group-title',
    initialFocus: inp,
    selectInitialFocus: true,
    submitOnEnter: true,
    onSubmit: submitGroup,
    onCancel: function() {
      closeModalDialog(modal, { restoreFocus: true });
    },
  });
}
function submitGroup() {
  const name = document.getElementById('group-name-input').value.trim();
  if (!name) return;
  const dirEl = document.getElementById('group-directory-input');
  const directory = dirEl ? dirEl.value.trim() : '';
  const payload = { cmd: 'add_group', group: name };
  if (directory) payload.default_directory = directory;
  if (typeof setActiveGroup === 'function'
      && typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()) {
    setActiveGroup(name, { allowPending: true });
  }
  send(payload);
  closeModals();
  if (typeof openGroupSettings === 'function') openGroupSettings(name, 'group');
}

/* -- Add Engineer ----------------------------------------------------- */

function _syncCodexFastMode(rowId, provider) {
  const row = document.getElementById(rowId);
  if (!row) return;
  row.classList.toggle('hidden', String(provider || '').trim().toLowerCase() !== 'codex');
}

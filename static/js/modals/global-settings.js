/* Global Settings modal. */

var _glsKeybindings = {};     // current keybinding overrides being edited
var _glsDefaults = {};        // default keybinding specs from server
var _glsCapturing = null;     // action name currently capturing a keypress
var _glsPendingConflict = null; // pending custom in-modal reassign confirmation
var _glsKeybindingFilter = '';
var GLS_STATUS_BAR_VISIBILITY_ITEMS = [
  'daemon_status',
  'claude_usage',
  'codex_usage',
  'deploy',
  'health',
  'workload',
  'tasks',
  'attention',
];
var GLS_STATUS_BAR_VISIBILITY_DEFAULTS = {
  daemon_status: false,
  claude_usage: false,
  codex_usage: false,
  deploy: true,
  health: false,
  workload: false,
  tasks: true,
  attention: true,
};

function _glsStatusBarVisibilityDefaults() {
  if (typeof statusBarVisibilityDefaults === 'function') {
    return statusBarVisibilityDefaults();
  }
  var defaults = {};
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    defaults[key] = !!GLS_STATUS_BAR_VISIBILITY_DEFAULTS[key];
  });
  return defaults;
}

function _glsNormalizeStatusBarVisibility(value) {
  if (typeof normalizeStatusBarVisibility === 'function') {
    return normalizeStatusBarVisibility(value);
  }
  var normalized = _glsStatusBarVisibilityDefaults();
  var raw = (value && typeof value === 'object') ? value : {};
  Object.keys(normalized).forEach(function(key) {
    if (Object.prototype.hasOwnProperty.call(raw, key)) {
      var itemValue = raw[key];
      normalized[key] = (typeof itemValue === 'string')
        ? ['1', 'true', 'yes', 'on'].indexOf(itemValue.trim().toLowerCase()) >= 0
        : !!itemValue;
    }
  });
  return normalized;
}

function _glsStatusBarInputId(key) {
  return 'gls-statusbar-' + String(key || '').replace(/_/g, '-');
}

function _syncStatusBarSettingsFromGlobal(settings, opts) {
  opts = opts || {};
  var s = settings || (state && state.global_settings) || {};
  var visibility = _glsNormalizeStatusBarVisibility(s.status_bar_visibility);
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    var input = document.getElementById(_glsStatusBarInputId(key));
    if (!input) return;
    var locked = !opts.force && (
      (typeof document !== 'undefined' && document.activeElement === input)
      || (input.dataset && input.dataset.statusBarDirty === '1')
    );
    if (!locked) input.checked = !!visibility[key];
    if (opts.force && input.dataset) delete input.dataset.statusBarDirty;
  });
  _renderStatusBarSettingsPreview();
}

function _onStatusBarSettingsChange(input) {
  if (input && input.dataset) input.dataset.statusBarDirty = '1';
  _renderStatusBarSettingsPreview();
}

function _renderStatusBarSettingsPreview() {
  var host = document.getElementById('gls-statusbar-preview-items');
  if (!host || !host.replaceChildren || !document.createElement) return;
  var labels = {
    daemon_status: '● Daemon',
    claude_usage: 'Claude 5h 42%',
    codex_usage: 'Codex 5h 20%',
    deploy: 'Deploy +2',
    health: 'Health good',
    workload: 'Agents 3 run',
    tasks: 'Tasks 4 active',
    attention: 'Attention 1',
  };
  host.replaceChildren();
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    var input = document.getElementById(_glsStatusBarInputId(key));
    if (!input || !input.checked) return;
    var chip = document.createElement('span');
    chip.className = 'statusbar-settings-preview-chip';
    chip.dataset.item = key;
    chip.textContent = labels[key] || key;
    host.appendChild(chip);
  });
  if (!host.children.length) {
    var empty = document.createElement('span');
    empty.className = 'statusbar-settings-preview-empty';
    empty.textContent = 'No optional items selected';
    host.appendChild(empty);
  }
}

function _collectStatusBarVisibilitySettings() {
  var visibility = _glsStatusBarVisibilityDefaults();
  GLS_STATUS_BAR_VISIBILITY_ITEMS.forEach(function(key) {
    var input = document.getElementById(_glsStatusBarInputId(key));
    if (input) visibility[key] = !!input.checked;
  });
  return visibility;
}

function _glsXtermScrollbackDefault() {
  return (typeof XTERM_SCROLLBACK_DEFAULT === 'number')
    ? XTERM_SCROLLBACK_DEFAULT : 2000;
}

function _glsXtermScrollbackMin() {
  return (typeof XTERM_SCROLLBACK_MIN === 'number')
    ? XTERM_SCROLLBACK_MIN : 100;
}

function _glsXtermScrollbackMax() {
  return (typeof XTERM_SCROLLBACK_MAX === 'number')
    ? XTERM_SCROLLBACK_MAX : 100000;
}

function _parseGlsXtermScrollback() {
  var input = document.getElementById('gls-xterm-scrollback');
  if (!input) return _glsXtermScrollbackDefault();
  var min = _glsXtermScrollbackMin();
  var max = _glsXtermScrollbackMax();
  var value = Number(input.value);
  if (!Number.isFinite(value) || Math.floor(value) !== value
      || value < min || value > max) {
    var message = 'Terminal scrollback must be an integer between '
      + min + ' and ' + max + ' lines.';
    if (typeof _showToast === 'function') _showToast(message, 'error');
    if (input && typeof input.focus === 'function') input.focus();
    return null;
  }
  return Math.floor(value);
}

function _formatDaemonDurationFromMs(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  var seconds = Math.floor(ms / 1000);
  if (seconds < 60) return seconds + ' second' + (seconds === 1 ? '' : 's');
  var minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + ' minute' + (minutes === 1 ? '' : 's');
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' hour' + (hours === 1 ? '' : 's');
  var days = Math.floor(hours / 24);
  return days + ' day' + (days === 1 ? '' : 's');
}

function _formatDaemonRelativeTime(startedAtSeconds, nowMs) {
  var started = Number(startedAtSeconds);
  if (!Number.isFinite(started) || started <= 0) return '—';
  var current = Number.isFinite(nowMs) ? nowMs : Date.now();
  var elapsedMs = Math.max(0, current - (started * 1000));
  if (elapsedMs < 5000) return 'just now';
  return _formatDaemonDurationFromMs(elapsedMs) + ' ago';
}

function _formatDaemonAbsoluteTime(startedAtSeconds) {
  var started = Number(startedAtSeconds);
  if (!Number.isFinite(started) || started <= 0) return '';
  return new Date(started * 1000).toLocaleString();
}

function _daemonDisplayValue(value, fallback) {
  if (value === null || value === undefined || value === '') return fallback || '—';
  return String(value);
}

function _setDaemonStatusText(id, value, title) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  if (title) el.title = title;
}

function _daemonWsConnected() {
  if (typeof ws !== 'undefined'
      && typeof WebSocket !== 'undefined'
      && ws
      && ws.readyState === WebSocket.OPEN) {
    return true;
  }
  var dot = document.getElementById('conn-dot');
  return !!(dot && dot.classList && dot.classList.contains('ok'));
}

function _wireDaemonStatusActions() {
  var restartBtn = document.getElementById('gls-restart-daemon-btn');
  if (restartBtn && typeof restartDaemon === 'function') {
    restartBtn.onclick = restartDaemon;
  }
  var stopBtn = document.getElementById('gls-stop-daemon-btn');
  if (stopBtn) {
    if (typeof stopDaemon === 'function') {
      stopBtn.onclick = stopDaemon;
      stopBtn.disabled = false;
      stopBtn.classList.remove('disabled');
      stopBtn.title = '';
    } else {
      stopBtn.onclick = null;
      stopBtn.disabled = true;
      stopBtn.classList.add('disabled');
      stopBtn.title = 'Pending daemon stop endpoint (:353)';
    }
  }
}

function loadDaemonStatus() {
  var runtime = (state && state.runtime) || {};
  var connected = _daemonWsConnected();
  var statusDot = document.getElementById('gls-daemon-status-dot');
  if (statusDot) {
    statusDot.classList.toggle('daemon-status-dot-ok', connected);
    statusDot.classList.toggle('daemon-status-dot-offline', !connected);
  }
  _setDaemonStatusText(
    'gls-daemon-status-text',
    connected ? 'Running' : 'Disconnected'
  );
  _setDaemonStatusText('gls-daemon-version', _daemonDisplayValue(runtime.version, 'unknown'));
  _setDaemonStatusText('gls-daemon-pid', _daemonDisplayValue(runtime.pid));
  _setDaemonStatusText('gls-daemon-uptime', _formatDaemonDurationFromMs(
    Date.now() - (Number(runtime.started_at) * 1000)
  ));
  _setDaemonStatusText('gls-daemon-port', _daemonDisplayValue(runtime.port));
  _setDaemonStatusText('gls-daemon-profile', _daemonDisplayValue(runtime.profile, 'default'));
  _setDaemonStatusText('gls-daemon-data-dir', _daemonDisplayValue(runtime.data_dir));
  _setDaemonStatusText('gls-daemon-log-path', _daemonDisplayValue(runtime.log_path));
  _setDaemonStatusText(
    'gls-daemon-started-at',
    _formatDaemonRelativeTime(runtime.started_at),
    _formatDaemonAbsoluteTime(runtime.started_at) || 'Time the daemon started'
  );
  // Relay-connection detail row (TORQUE:560). Driven from
  // `state.relay_connection`; hides itself when the field is absent.
  if (typeof _relayStatusRenderModalRow === 'function') _relayStatusRenderModalRow();
  _wireDaemonStatusActions();
}

function switchGlsTab(name) {
  document.querySelectorAll('#modal-global-settings .gs-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('#modal-global-settings .gs-pane').forEach(p =>
    p.classList.toggle('active', p.dataset.pane === name));
  if (name === 'gls-system') loadDaemonStatus();
  if (typeof settingsShellSyncView === 'function') {
    settingsShellSyncView('modal-global-settings');
  }
}

var _glsActiveSubTabs = {};

function _pickGlsSubTab(pane, preferred) {
  if (!pane || !pane.querySelectorAll) return null;
  var tabs = Array.prototype.slice.call(pane.querySelectorAll('.gs-subtab'));
  if (!tabs.length) return null;
  if (preferred) {
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].dataset && tabs[i].dataset.subtab === preferred && !tabs[i].hidden) {
        return tabs[i];
      }
    }
  }
  for (var j = 0; j < tabs.length; j++) {
    if (!tabs[j].hidden) return tabs[j];
  }
  return tabs[0];
}

function _syncGlsSubTabs(restoreSelection) {
  document.querySelectorAll('#modal-global-settings .gs-pane').forEach(function(pane) {
    var paneName = pane.dataset ? pane.dataset.pane : '';
    var preferred = restoreSelection && paneName ? _glsActiveSubTabs[paneName] : '';
    var tab = _pickGlsSubTab(pane, preferred);
    if (tab) switchGlsSubTab(tab);
  });
}

function switchGlsSubTab(btn) {
  var container = btn.closest('.gs-pane');
  if (!container) return;
  container.querySelectorAll('.gs-subtab').forEach(t =>
    t.classList.toggle('active', t === btn));
  var target = btn.dataset.subtab;
  container.querySelectorAll('.gs-subpane').forEach(p =>
    p.classList.toggle('active', p.dataset.subpane === target));
  if (container.dataset && container.dataset.pane && target) {
    _glsActiveSubTabs[container.dataset.pane] = target;
  }
  if (typeof settingsShellSyncView === 'function') {
    settingsShellSyncView('modal-global-settings');
  }
}

function openGlobalSettings() {
  send({ cmd: 'get_global_settings' });
}

function _showGlobalSettingsModal(data) {
  var s = data.settings;
  var modal = document.getElementById('modal-global-settings');
  var modalWasVisible = modal && modal.classList && modal.classList.contains('visible');
  var activeTab = modalWasVisible
    ? document.querySelector('#modal-global-settings .gs-tab.active')
    : null;
  var activeTabName = activeTab && activeTab.dataset ? activeTab.dataset.tab : '';
  _glsDefaults = typeof keybindingDefaults === 'function'
    ? keybindingDefaults()
    : (data.keybinding_defaults || {});
  _glsKeybindings = typeof sanitizeKeybindingOverrides === 'function'
    ? sanitizeKeybindingOverrides(s.keybindings || {})
    : Object.assign({}, s.keybindings || {});
  _glsPendingConflict = null;

  if (!modalWasVisible && typeof settingsAppearancePopulate === 'function') {
    settingsAppearancePopulate();
  }

  // General > Server
  document.getElementById('gls-default-cmd').value = s.default_command || '';
  document.getElementById('gls-filter-window').checked =
    s.filter_by_window !== undefined ? s.filter_by_window : true;
  document.getElementById('gls-focus-new-tabs').checked =
    s.focus_new_tabs !== undefined ? s.focus_new_tabs : true;
  document.getElementById('gls-focus-on-click').checked =
    s.focus_on_click || false;
  document.getElementById('gls-max-event-log').value =
    s.max_event_log !== undefined ? s.max_event_log : 500;
  document.getElementById('gls-xterm-scrollback').value =
    s.xterm_scrollback !== undefined ? s.xterm_scrollback
      : _glsXtermScrollbackDefault();
  var argsCapture = s.mcp_call_log_args_capture || 'metadata';
  var argsCaptureEl = document.getElementById('gls-mcp-call-log-args-capture');
  if (argsCaptureEl) argsCaptureEl.value = argsCapture;
  var fullCaptureEl = document.getElementById('gls-mcp-call-log-full-capture-tools');
  if (fullCaptureEl) {
    fullCaptureEl.value = (s.mcp_call_log_full_capture_tools || []).join('\n');
  }
  var maxRowsEl = document.getElementById('gls-event-ingest-max-rows');
  if (maxRowsEl) {
    maxRowsEl.value = s.event_ingest_max_rows !== undefined
      ? s.event_ingest_max_rows : 100000;
  }
  var maxDaysEl = document.getElementById('gls-event-ingest-max-days');
  if (maxDaysEl) {
    maxDaysEl.value = s.event_ingest_max_days !== undefined
      ? s.event_ingest_max_days : 14;
  }

  // General > Board
  document.getElementById('gls-max-pipeline-depth').value =
    s.max_pipeline_depth !== undefined ? s.max_pipeline_depth : 10;

  // Status bar
  _syncStatusBarSettingsFromGlobal(s, { force: !modalWasVisible });

  // Keybindings
  _renderKeybindingList();

  // Relay config (TORQUE:603 #1). The get_global_settings response
  // carries a fresh top-level `relay_config` (same shape as the snapshot / the
  // `relay_config` delta); adopt it into `state` and force-populate the editable
  // settings-layer inputs. force=true clears any stale dirty
  // flags so the freshly opened modal reflects the authoritative resolved config.
  if (data.relay_config) state.relay_config = data.relay_config;
  if (typeof refreshRelayConfigModal === 'function') {
    refreshRelayConfigModal({ force: true });
  }

  if (modalWasVisible && activeTabName) switchGlsTab(activeTabName);
  else switchGlsTab('gls-appearance');
  _syncGlsSubTabs(modalWasVisible);

  modal.classList.add('visible');
  if (!modalWasVisible) {
    if (typeof settingsShellCaptureBaseline === 'function') {
      settingsShellCaptureBaseline('modal-global-settings');
    }
    var firstAppearanceControl = document.getElementById('gls-appearance-contrast');
    if (firstAppearanceControl) firstAppearanceControl.focus();
  }
}

function _kbDefaultBinding(action) {
  var def = _glsDefaults[action] || {};
  if (typeof normalizeKeybindingDescriptor === 'function') {
    return normalizeKeybindingDescriptor(def.defaultBinding);
  }
  return def.defaultBinding || null;
}

function _kbOverrideBinding(action) {
  if (!_glsKeybindings || !_glsKeybindings[action]) return null;
  if (typeof normalizeKeybindingDescriptor === 'function') {
    return normalizeKeybindingDescriptor(_glsKeybindings[action]);
  }
  return _glsKeybindings[action] || null;
}

function _kbEffectiveBindingForSettings(action) {
  return _kbOverrideBinding(action) || _kbDefaultBinding(action);
}

function _kbDisplayName(action, binding) {
  var b = binding || _kbEffectiveBindingForSettings(action);
  if (typeof kbBindingDisplayName === 'function') return kbBindingDisplayName(b);
  return b && b.key ? b.key : 'Unassigned';
}

function _kbBindingSame(a, b) {
  if (typeof _kbSameBinding === 'function') return _kbSameBinding(a, b);
  if (!a || !b) return false;
  return String(a.key || '').toLowerCase() === String(b.key || '').toLowerCase()
    && !!a.ctrl === !!b.ctrl
    && !!a.meta === !!b.meta
    && !!a.alt === !!b.alt
    && !!a.shift === !!b.shift;
}

function _kbBindingFingerprintLocal(binding) {
  if (typeof _kbBindingFingerprint === 'function') return _kbBindingFingerprint(binding);
  if (!binding) return '';
  return [String(binding.key || '').toLowerCase(), binding.ctrl ? 1 : 0,
    binding.meta ? 1 : 0, binding.alt ? 1 : 0, binding.shift ? 1 : 0].join('|');
}

function _kbActionLabel(action) {
  var def = _glsDefaults[action] || {};
  return def.label || action;
}

function _kbActionOrder() {
  return Object.keys(_glsDefaults || {}).sort(function(a, b) {
    var ao = typeof _glsDefaults[a].order === 'number' ? _glsDefaults[a].order : 1000;
    var bo = typeof _glsDefaults[b].order === 'number' ? _glsDefaults[b].order : 1000;
    if (ao !== bo) return ao - bo;
    return a < b ? -1 : (a > b ? 1 : 0);
  });
}

function _kbFindConflict(action, binding) {
  var fp = _kbBindingFingerprintLocal(binding);
  if (!fp) return null;
  var actions = _kbActionOrder();
  for (var i = 0; i < actions.length; i++) {
    var other = actions[i];
    if (other === action) continue;
    var def = _glsDefaults[other] || {};
    if (Array.isArray(def.defaultBindings) && def.defaultBindings.length) {
      for (var j = 0; j < def.defaultBindings.length; j++) {
        var fixedBinding = def.defaultBindings[j];
        if (_kbBindingFingerprintLocal(fixedBinding) === fp) {
          return { action: other, binding: fixedBinding, fixed: true };
        }
      }
      continue;
    }
    var otherBinding = _kbEffectiveBindingForSettings(other);
    if (_kbBindingFingerprintLocal(otherBinding) === fp) {
      return { action: other, binding: otherBinding, fixed: !!def.fixed };
    }
  }
  return null;
}

function _kbSetOverride(action, binding) {
  if (!_glsKeybindings) _glsKeybindings = {};
  var normalized = typeof normalizeKeybindingDescriptor === 'function'
    ? normalizeKeybindingDescriptor(binding)
    : binding;
  if (!normalized) return;
  var defBinding = _kbDefaultBinding(action);
  if (defBinding && _kbBindingSame(normalized, defBinding)) delete _glsKeybindings[action];
  else _glsKeybindings[action] = normalized;
  if (typeof settingsShellForceDirty === 'function') {
    settingsShellForceDirty('modal-global-settings');
  }
}

function _kbApplyBindingWithConflictCheck(action, binding, reset) {
  var normalized = typeof normalizeKeybindingDescriptor === 'function'
    ? normalizeKeybindingDescriptor(binding)
    : binding;
  if (!normalized) return;
  var conflict = _kbFindConflict(action, normalized);
  if (conflict) {
    _glsPendingConflict = {
      action: action,
      binding: normalized,
      reset: !!reset,
      conflictAction: conflict.action,
      conflictBinding: conflict.binding,
      fixed: !!conflict.fixed,
      previousBinding: _kbEffectiveBindingForSettings(action),
    };
    _renderKeybindingList();
    return;
  }
  if (reset) {
    delete _glsKeybindings[action];
    if (typeof settingsShellForceDirty === 'function') settingsShellForceDirty('modal-global-settings');
  } else _kbSetOverride(action, normalized);
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _kbConflictWarningHtml() {
  if (!_glsPendingConflict) return '';
  var pending = _glsPendingConflict;
  var actionLabel = _kbActionLabel(pending.action);
  var conflictLabel = _kbActionLabel(pending.conflictAction);
  var combo = _kbDisplayName(pending.action, pending.binding);
  var html = '<div class="kb-conflict-warning" role="alert">';
  html += '<div><strong>' + esc(combo) + '</strong> is already assigned to '
    + '<strong>' + esc(conflictLabel) + '</strong>.</div>';
  if (pending.fixed) {
    html += '<div class="kb-conflict-copy">That shortcut is part of a fixed key cluster. Choose another shortcut for '
      + esc(actionLabel) + '.</div>';
    html += '<div class="kb-conflict-actions">'
      + '<button type="button" class="kb-btn" onclick="_cancelKeybindingConflict()">OK</button>'
      + '</div>';
  } else {
    var previous = _kbDisplayName(pending.conflictAction, pending.previousBinding);
    html += '<div class="kb-conflict-copy">Reassign it to ' + esc(actionLabel)
      + ' and move ' + esc(conflictLabel) + ' to '
      + '<strong>' + esc(previous) + '</strong>?</div>';
    html += '<div class="kb-conflict-actions">'
      + '<button type="button" class="kb-btn kb-btn-primary" onclick="_confirmKeybindingReassign()">Reassign</button>'
      + '<button type="button" class="kb-btn" onclick="_cancelKeybindingConflict()">Cancel</button>'
      + '</div>';
  }
  html += '</div>';
  return html;
}

function _renderKeybindingList() {
  var container = document.getElementById('gls-keybinding-list');
  if (!container) return;
  var scrollTop = container.scrollTop || 0;
  var html = '';
  html += _kbConflictWarningHtml();
  var actions = _kbActionOrder();
  var query = String(_glsKeybindingFilter || '').trim().toLowerCase();
  if (query) {
    actions = actions.filter(function(action) {
      var def = _glsDefaults[action] || {};
      return [action, def.label || '', def.description || ''].join(' ').toLowerCase().indexOf(query) >= 0;
    });
  }
  if (!actions.length) {
    html += '<div class="settings-list-empty">No shortcuts match “' + esc(_glsKeybindingFilter) + '”.</div>';
  }
  for (var ai = 0; ai < actions.length; ai++) {
    var action = actions[ai];
    var def = _glsDefaults[action];
    var current = _kbOverrideBinding(action);
    var display = _kbDisplayName(action, current);
    var label = def.label || action;
    var isCapturing = _glsCapturing === action;
    html += '<div class="kb-row" data-keybinding-action="' + esc(action) + '">';
    html += '  <span class="kb-label">' + esc(label);
    if (def.description) html += '<span class="kb-description">' + esc(def.description) + '</span>';
    html += '</span>';
    if (isCapturing) {
      html += '  <span class="kb-combo kb-capturing">Press keys\u2026</span>';
      html += '  <button class="kb-btn" onclick="_cancelCapture()">Cancel</button>';
    } else if (def.fixed) {
      html += '  <span class="kb-combo">' + esc(def.display || display) + '</span>';
      html += '  <span class="kb-fixed">Fixed</span>';
    } else {
      html += '  <span class="kb-combo">' + esc(display) + '</span>';
      html += '  <button class="kb-btn" onclick="_startCapture(\'' + action + '\')">Rebind</button>';
      if (current) {
        html += '  <button class="kb-btn" onclick="_resetKeybinding(\'' + action + '\')">Reset</button>';
      }
    }
    html += '</div>';
  }
  container.innerHTML = html;
  container.scrollTop = scrollTop;
}

function _filterKeybindingSettings(query) {
  _glsKeybindingFilter = String(query || '');
  _renderKeybindingList();
}

function _resetAllKeybindings() {
  _glsKeybindings = {};
  _glsPendingConflict = null;
  _glsCapturing = null;
  if (typeof settingsShellForceDirty === 'function') {
    settingsShellForceDirty('modal-global-settings');
  }
  _renderKeybindingList();
}

function _startCapture(action) {
  _glsCapturing = action;
  _glsPendingConflict = null;
  _renderKeybindingList();
  document.addEventListener('keydown', _captureKeydown, true);
}

function _cancelCapture() {
  _glsCapturing = null;
  document.removeEventListener('keydown', _captureKeydown, true);
  _renderKeybindingList();
}

function _cancelKeybindingConflict() {
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _confirmKeybindingReassign() {
  var pending = _glsPendingConflict;
  if (!pending || pending.fixed) {
    _cancelKeybindingConflict();
    return;
  }
  if (pending.reset) delete _glsKeybindings[pending.action];
  else _kbSetOverride(pending.action, pending.binding);
  if (pending.previousBinding) _kbSetOverride(pending.conflictAction, pending.previousBinding);
  _glsPendingConflict = null;
  _renderKeybindingList();
}

function _captureKeydown(e) {
  e.preventDefault();
  e.stopPropagation();
  // Ignore bare modifier presses
  if (['Meta', 'Alt', 'Shift', 'Control'].includes(e.key)) return;

  var action = _glsCapturing;
  var binding = typeof keybindingDescriptorFromEvent === 'function'
    ? keybindingDescriptorFromEvent(e)
    : null;
  _glsCapturing = null;
  document.removeEventListener('keydown', _captureKeydown, true);
  if (action && binding) _kbApplyBindingWithConflictCheck(action, binding, false);
  else _renderKeybindingList();
}

function _resetKeybinding(action) {
  var defBinding = _kbDefaultBinding(action);
  if (!defBinding) {
    delete _glsKeybindings[action];
    if (typeof settingsShellForceDirty === 'function') settingsShellForceDirty('modal-global-settings');
    _renderKeybindingList();
    return;
  }
  _kbApplyBindingWithConflictCheck(action, defBinding, true);
}

function _syncKeybindingSettingsFromGlobal(settings) {
  var s = settings || (state && state.global_settings) || {};
  if (!_glsCapturing) {
    _glsKeybindings = typeof sanitizeKeybindingOverrides === 'function'
      ? sanitizeKeybindingOverrides(s.keybindings || {})
      : Object.assign({}, s.keybindings || {});
  }
  _renderKeybindingList();
}

function submitGlobalSettings() {
  var xtermScrollback = _parseGlsXtermScrollback();
  if (xtermScrollback === null) return;

  var settings = {
    default_command: document.getElementById('gls-default-cmd').value.trim(),
    filter_by_window: document.getElementById('gls-filter-window').checked,
    focus_new_tabs: document.getElementById('gls-focus-new-tabs').checked,
    focus_on_click: document.getElementById('gls-focus-on-click').checked,
    xterm_scrollback: xtermScrollback,
    keybindings: typeof sanitizeKeybindingOverrides === 'function'
      ? sanitizeKeybindingOverrides(_glsKeybindings)
      : _glsKeybindings,
    status_bar_visibility: _collectStatusBarVisibilitySettings(),
    max_pipeline_depth: parseInt(document.getElementById('gls-max-pipeline-depth').value) || 0,
    max_event_log: parseInt(document.getElementById('gls-max-event-log').value) || 500,
  };
  var argsCaptureEl = document.getElementById('gls-mcp-call-log-args-capture');
  if (argsCaptureEl) settings.mcp_call_log_args_capture = argsCaptureEl.value || 'metadata';
  var fullCaptureEl = document.getElementById('gls-mcp-call-log-full-capture-tools');
  if (fullCaptureEl) {
    settings.mcp_call_log_full_capture_tools = fullCaptureEl.value
      .split(/\n|,/)
      .map(function(item) { return item.trim(); })
      .filter(Boolean);
  }
  var maxRowsEl = document.getElementById('gls-event-ingest-max-rows');
  if (maxRowsEl) settings.event_ingest_max_rows = parseInt(maxRowsEl.value) || 100000;
  var maxDaysEl = document.getElementById('gls-event-ingest-max-days');
  if (maxDaysEl) settings.event_ingest_max_days = parseInt(maxDaysEl.value) || 0;

  // Relay config (TORQUE:603 #1). Editable settings-layer overrides; the daemon
  // applies on change (stop+restart the connector) and the :601 relay_connection
  // signal reports the result. Text fields are sent trimmed — an EMPTY value is
  // a deliberate "no settings override; inherit from ee_connector.json / env"
  // (the backend only flows NON-EMPTY settings values into the connector config,
  // so re-sending "" for an untouched inherited field preserves its fallback).
  // private_key_path is BY PATH only — never inline PEM.
  //
  // `relay_enabled` is TRI-STATE/inherit (TORQUE:603 #1 review): unlike the text
  // fields (where empty="" is the inherit signal), a checkbox has no empty state
  // and reflects the EFFECTIVE config.enabled — which may be sourced from env /
  // ee_connector.json. Always sending it would silently PROMOTE an inherited
  // enabled into a settings-layer override on any unrelated save (a provenance
  // surprise). So send relay_enabled ONLY when the operator EXPLICITLY toggled
  // it (dataset.relayDirty, set by the checkbox onchange); an untouched checkbox
  // is omitted, and update_global_settings leaves the existing (inherited) value
  // unchanged.
  var relayEnabledEl = document.getElementById('gls-relay-enabled');
  if (relayEnabledEl && relayEnabledEl.dataset
      && relayEnabledEl.dataset.relayDirty === '1') {
    settings.relay_enabled = !!relayEnabledEl.checked;
  }
  var relayUrlEl = document.getElementById('gls-relay-url');
  if (relayUrlEl) settings.relay_url = relayUrlEl.value.trim();
  var relayDaemonIdEl = document.getElementById('gls-relay-daemon-id');
  if (relayDaemonIdEl) settings.relay_daemon_id = relayDaemonIdEl.value.trim();
  var relayCredentialIdEl = document.getElementById('gls-relay-credential-id');
  if (relayCredentialIdEl) settings.relay_credential_id = relayCredentialIdEl.value.trim();
  var relayPrivateKeyPathEl = document.getElementById('gls-relay-private-key-path');
  if (relayPrivateKeyPathEl) settings.relay_private_key_path = relayPrivateKeyPathEl.value.trim();

  if (typeof settingsAppearanceCommit === 'function') settingsAppearanceCommit();
  send({ cmd: 'update_global_settings', settings: settings });
  closeModals();
}

/* ---- Schedule modal -------------------------------------------------- */

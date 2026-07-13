/* Bottom status bar helpers (TORQUE:560)
 *
 * This module deliberately updates only stable text/class/title/hidden fields
 * on pre-existing DOM nodes. It must not wipe the taskbar or panel-button DOM:
 * panel toggles, focus, and button identity are preserved across routine state
 * refreshes.
 */

var STATUS_BAR_CLAUDE_PROVIDER = 'claude-code';
var STATUS_BAR_CODEX_PROVIDER = 'codex';
var STATUS_BAR_DEPLOY_POLL_INTERVAL_MS = 90000;
var STATUS_BAR_VISIBILITY_DEFAULTS = {
  daemon_status: false,
  claude_usage: false,
  codex_usage: false,
  deploy: true,
  health: false,
  workload: false,
  tasks: true,
  attention: true,
};
var _statusBarDeployState = null;
var _statusBarDeployRequestKey = '';
var _statusBarDeployRequestedAt = 0;
var _statusBarRefreshTimer = 0;
var _statusBarDeployPollTimer = 0;
var _statusBarVisibilityListenerInstalled = false;

function _statusBarNowMs() {
  return Date.now();
}

function _statusBarElement(id) {
  if (typeof document === 'undefined' || !document.getElementById) return null;
  return document.getElementById(id);
}

function statusBarVisibilityDefaults() {
  var defaults = {};
  Object.keys(STATUS_BAR_VISIBILITY_DEFAULTS).forEach(function(key) {
    defaults[key] = !!STATUS_BAR_VISIBILITY_DEFAULTS[key];
  });
  return defaults;
}

function normalizeStatusBarVisibility(value) {
  var normalized = statusBarVisibilityDefaults();
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

function statusBarVisibilityFromSettings(settings) {
  var s = settings || (typeof state !== 'undefined' && state
    ? state.global_settings
    : null);
  return normalizeStatusBarVisibility(s && s.status_bar_visibility);
}

function statusBarVisibilityEnabled(key, settings) {
  var visibility = statusBarVisibilityFromSettings(settings);
  return !!visibility[key];
}

function _statusBarViewVisible(key, viewVisible) {
  return viewVisible !== false && statusBarVisibilityEnabled(key);
}

function _statusBarSetElementVisible(el, visible) {
  if (!el) return;
  if ('hidden' in el) el.hidden = !visible;
  if (el.classList) {
    el.classList.toggle('statusbar-chip--pref-hidden', !visible);
  }
}

function _statusBarDocumentHidden() {
  return !!(
    typeof document !== 'undefined'
    && document
    && document.hidden
  );
}

function _statusBarActiveGroup() {
  if (typeof _activeGroup === 'function') {
    try {
      return String(_activeGroup() || '').trim();
    } catch (_) {}
  }
  if (typeof _currentGroup === 'function') {
    try {
      return String(_currentGroup() || '').trim();
    } catch (_) {}
  }
  if (typeof state !== 'undefined' && state) {
    var active = String(state.active_group || '').trim();
    if (active) return active;
    var groups = state.groups || {};
    var names = Object.keys(groups);
    if (names.length) return names.sort()[0];
  }
  return '';
}

function _statusBarFormatPercent(value) {
  var number = Number(value);
  if (!Number.isFinite(number)) return '—';
  if (number < 0) number = 0;
  if (number > 100) number = 100;
  return String(Math.round(number)) + '%';
}

function _statusBarTimestampMs(value) {
  if (value === null || value === undefined || value === '') return 0;
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0;
    return value > 100000000000 ? value : value * 1000;
  }
  var numeric = Number(value);
  if (Number.isFinite(numeric) && String(value || '').trim() !== '') {
    if (numeric <= 0) return 0;
    return numeric > 100000000000 ? numeric : numeric * 1000;
  }
  var parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function _statusBarFormatReset(value, nowMs) {
  var resetMs = _statusBarTimestampMs(value);
  if (!resetMs) return 'reset —';
  var now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : _statusBarNowMs();
  var remainingMs = resetMs - now;
  if (!Number.isFinite(remainingMs)) return 'reset —';
  if (remainingMs <= 0) return 'reset soon';
  var minutes = Math.ceil(remainingMs / 60000);
  if (minutes <= 1) return 'resets <1m';
  if (minutes < 60) return 'resets ' + minutes + 'm';
  var hours = Math.floor(minutes / 60);
  var mins = minutes % 60;
  if (hours < 24) return 'resets ' + hours + 'h' + (mins ? ' ' + mins + 'm' : '');
  var days = Math.floor(hours / 24);
  var remHours = hours % 24;
  if (days < 7) return 'resets ' + days + 'd' + (remHours ? ' ' + remHours + 'h' : '');
  try {
    return 'resets ' + new Date(resetMs).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    });
  } catch (_) {
    return 'reset —';
  }
}

function _statusBarIsTombstonedAgent(agent) {
  if (!agent) return true;
  if (typeof _isTombstonedAgent === 'function') {
    try {
      if (_isTombstonedAgent(agent)) return true;
    } catch (_) {}
  }
  return !!(agent.deleted_at || agent.dismissed_at || agent.tombstoned);
}

function _statusBarAgentCounts(group) {
  var counts = { running: 0, idle: 0, error: 0, total: 0 };
  var wantedGroup = String(group === undefined ? _statusBarActiveGroup() : group || '').trim();
  var agents = (typeof state !== 'undefined' && state && state.agents) ? state.agents : {};
  Object.keys(agents || {}).forEach(function(id) {
    var agent = agents[id];
    if (!agent || _statusBarIsTombstonedAgent(agent)) return;
    if (String(agent.cell_type || '') === 'terminal') return;
    if (wantedGroup && String(agent.group || '') !== wantedGroup) return;
    counts.total += 1;
    var status = String(agent.status || '').toLowerCase();
    if (status === 'error' || agent.error || agent.error_message) counts.error += 1;
    else if (status === 'running' || status === 'busy' || status === 'starting') counts.running += 1;
    else counts.idle += 1;
  });
  counts.label = counts.total
    ? ('Agents ' + counts.running + ' run / ' + counts.idle + ' idle' + (counts.error ? ' / ' + counts.error + ' err' : ''))
    : 'Agents 0';
  counts.title = 'Agent status counts'
    + (wantedGroup ? ' for ' + wantedGroup : '')
    + ': ' + counts.running + ' running, ' + counts.idle + ' idle, ' + counts.error + ' error';
  return counts;
}

function _statusBarTaskIsClosed(task) {
  if (!task) return true;
  var lane = String(task.lane || '').toLowerCase();
  return !!(
    task.archived_at
    || task.deleted_at
    || lane === 'done'
    || lane === 'archived'
  );
}

function _statusBarActiveTaskCount(group) {
  var wantedGroup = String(group === undefined ? _statusBarActiveGroup() : group || '').trim();
  var tasks = (typeof state !== 'undefined' && state && state.board_tasks) ? state.board_tasks : {};
  var count = 0;
  Object.keys(tasks || {}).forEach(function(id) {
    var task = tasks[id];
    if (!task || _statusBarTaskIsClosed(task)) return;
    if (wantedGroup && String(task.group || '') !== wantedGroup) return;
    if (!String(task.agent_id || '').trim()) return;
    count += 1;
  });
  return {
    count: count,
    label: 'Tasks ' + count + ' active',
    title: 'In-flight tasks assigned to an agent' + (wantedGroup ? ' in ' + wantedGroup : '') + ': ' + count,
  };
}

function _statusBarAttentionCount(group) {
  var wantedGroup = String(group === undefined ? _statusBarActiveGroup() : group || '').trim();
  var count = 0;
  if (typeof _eventsGetAttentionItems === 'function') {
    try {
      var items = _eventsGetAttentionItems() || [];
      count = items.filter(function(item) {
        if (!item) return false;
        if (typeof _eventsIsDismissed === 'function' && _eventsIsDismissed(item)) return false;
        var itemGroup = String(
          item.group
          || (item.task && item.task.group)
          || (item.agent && item.agent.group)
          || ''
        );
        return !wantedGroup || !itemGroup || itemGroup === wantedGroup;
      }).length;
      return {
        count: count,
        label: 'Attention ' + count,
        title: 'Pending asks / attention items' + (wantedGroup ? ' for ' + wantedGroup : '') + ': ' + count,
      };
    } catch (_) {
      // Fall through to the minimal state-only fallback below.
    }
  }

  var tasks = (typeof state !== 'undefined' && state && state.board_tasks) ? state.board_tasks : {};
  Object.keys(tasks || {}).forEach(function(id) {
    var task = tasks[id];
    if (!task || _statusBarTaskIsClosed(task)) return;
    if (wantedGroup && String(task.group || '') !== wantedGroup) return;
    var labels = Array.isArray(task.labels) ? task.labels : [];
    if (labels.indexOf('torque:human') >= 0
        && labels.indexOf('torque:non-user-ask') < 0) {
      count += 1;
    }
  });
  var agents = (typeof state !== 'undefined' && state && state.agents) ? state.agents : {};
  Object.keys(agents || {}).forEach(function(id) {
    var agent = agents[id];
    if (!agent || _statusBarIsTombstonedAgent(agent)) return;
    if (String(agent.cell_type || '') === 'terminal') return;
    if (wantedGroup && String(agent.group || '') !== wantedGroup) return;
    if (agent.needs_attention) count += 1;
  });
  return {
    count: count,
    label: 'Attention ' + count,
    title: 'Pending asks / attention items' + (wantedGroup ? ' for ' + wantedGroup : '') + ': ' + count,
  };
}

function _statusBarDeployView(deployState) {
  var payload = (deployState && typeof deployState === 'object') ? deployState : null;
  if (!payload) {
    return {
      visible: false,
      label: 'Deploy —',
      level: 'muted',
      title: 'Deploy-pending state has not been checked yet.',
    };
  }
  if (payload.error) {
    return {
      visible: true,
      label: 'Deploy ?',
      level: 'danger',
      title: 'Deploy-pending check failed: ' + String(payload.error),
    };
  }
  var pending = (payload.pending_deploy && typeof payload.pending_deploy === 'object')
    ? payload.pending_deploy
    : {};
  var count = Number(pending.count || 0);
  if (!Number.isFinite(count) || count < 0) count = 0;
  var ids = Array.isArray(pending.torque_task_ids) ? pending.torque_task_ids : [];
  if (count <= 0) {
    return {
      visible: false,
      label: 'Deploy 0',
      level: 'muted',
      title: 'No merged-but-not-deployed Torque tasks for this daemon boot.',
    };
  }
  var title = 'Merged but not yet deployed: ' + count;
  if (ids.length) title += '\nTasks: ' + ids.join(', ');
  if (Number.isFinite(Number(payload.daemon_uptime_seconds))) {
    title += '\nDaemon uptime: ' + _statusBarFormatDuration(Number(payload.daemon_uptime_seconds));
  }
  return {
    visible: true,
    label: 'Deploy +' + Math.round(count),
    level: 'warn',
    title: title,
  };
}

function _statusBarFormatDuration(seconds) {
  var value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return '—';
  value = Math.floor(value);
  if (value < 60) return value + 's';
  var minutes = Math.floor(value / 60);
  if (minutes < 60) return minutes + 'm';
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ' + (minutes % 60) + 'm';
  var days = Math.floor(hours / 24);
  return days + 'd ' + (hours % 24) + 'h';
}

function _statusBarUsageWindow(providerUsage, keys) {
  if (!providerUsage || typeof providerUsage !== 'object') return null;
  var windows = providerUsage.windows || providerUsage.limits || providerUsage;
  for (var i = 0; i < keys.length; i++) {
    var key = keys[i];
    if (windows && windows[key] && typeof windows[key] === 'object') return windows[key];
  }
  return null;
}

function _statusBarUsageWindowAvailable(windowInfo) {
  return !!(
    windowInfo
    && typeof windowInfo === 'object'
    && windowInfo.available === true
  );
}

function _statusBarUsageUsedPct(windowInfo) {
  if (!windowInfo || typeof windowInfo !== 'object') return NaN;
  var usedPercentage = Number(windowInfo.used_percentage);
  if (Number.isFinite(usedPercentage)) return usedPercentage;
  var usedPercentageCamel = Number(windowInfo.usedPercentage);
  if (Number.isFinite(usedPercentageCamel)) return usedPercentageCamel;
  var used = Number(windowInfo.used_pct);
  if (Number.isFinite(used)) return used;
  var remaining = Number(windowInfo.remaining_pct);
  if (Number.isFinite(remaining)) return 100 - remaining;
  return NaN;
}

function _statusBarProviderUsageFromAgent(agent, provider) {
  if (!agent || typeof agent !== 'object') return null;
  provider = String(provider || '').trim();
  var agentType = String(agent.agent_type || agent.provider || '').trim();
  var payload = agent.provider_usage;
  if (!payload || typeof payload !== 'object') return null;

  // Current backend shape (TORQUE:700): provider_usage is stored directly on
  // provider agents as {five_hour, seven_day}. Keep a compatibility read for
  // an older provider-keyed draft, but do not consult the retired top-level
  // state.provider_usage scaffold.
  if (provider && payload[provider] && typeof payload[provider] === 'object') {
    return payload[provider];
  }
  if (provider && agentType && agentType !== provider) return null;
  return payload;
}

function _statusBarClaudeProviderUsageFromAgent(agent) {
  return _statusBarProviderUsageFromAgent(agent, STATUS_BAR_CLAUDE_PROVIDER);
}

function _statusBarUsageHasAvailableWindow(providerUsage) {
  var five = _statusBarUsageWindow(providerUsage, ['five_hour', '5h', 'fiveHour']);
  var seven = _statusBarUsageWindow(providerUsage, ['seven_day', 'weekly', '7d', 'week', 'sevenDay']);
  return _statusBarUsageWindowAvailable(five) || _statusBarUsageWindowAvailable(seven);
}

function _statusBarClaudeUsageHasAvailableWindow(providerUsage) {
  return _statusBarUsageHasAvailableWindow(providerUsage);
}

function _statusBarAgentFreshness(agent) {
  if (!agent || typeof agent !== 'object') return 0;
  var keys = ['last_heartbeat_at', 'last_activity_at', 'last_event_at', 'last_progress_at'];
  var newest = 0;
  for (var i = 0; i < keys.length; i++) {
    var value = Number(agent[keys[i]]);
    if (Number.isFinite(value) && value > newest) newest = value;
  }
  return newest;
}

function _statusBarSelectProviderUsage(provider) {
  var agents = (typeof state !== 'undefined' && state && state.agents) ? state.agents : {};
  var selected = null;
  Object.keys(agents || {}).forEach(function(id) {
    var agent = agents[id];
    if (!agent || _statusBarIsTombstonedAgent(agent)) return;
    var usage = _statusBarProviderUsageFromAgent(agent, provider);
    if (!_statusBarUsageHasAvailableWindow(usage)) return;

    // 5h/7d limits are account-level quotas shared by every provider's
    // agents for the same user account. Each provider agent reports the same
    // account numbers, so represent the chip with exactly one available agent
    // payload. Never sum or average multiple agents; that would double-count.
    var freshness = _statusBarAgentFreshness(agent);
    if (!selected || freshness >= selected.freshness) {
      selected = {
        agent: agent,
        usage: usage,
        freshness: freshness,
      };
    }
  });
  return selected;
}

function _statusBarSelectClaudeUsage() {
  return _statusBarSelectProviderUsage(STATUS_BAR_CLAUDE_PROVIDER);
}

function _statusBarProviderUsageView(provider, label, unavailableTitle, payloadTitle, providerAgentLabel, providerUsage) {
  var sourceAgent = null;
  var usage = providerUsage;
  if (usage === undefined) {
    var selected = _statusBarSelectProviderUsage(provider);
    usage = selected ? selected.usage : null;
    sourceAgent = selected ? selected.agent : null;
  }
  if (!usage || typeof usage !== 'object' || !Object.keys(usage).length
      || !_statusBarUsageHasAvailableWindow(usage)) {
    return {
      state: 'unknown',
      label: label + ' —',
      level: 'unknown',
      title: unavailableTitle,
      nextResetAt: 0,
    };
  }

  var five = _statusBarUsageWindow(usage, ['five_hour', '5h', 'fiveHour']);
  var seven = _statusBarUsageWindow(usage, ['seven_day', 'weekly', '7d', 'week', 'sevenDay']);
  var parts = [];
  var titleLines = [payloadTitle];
  if (sourceAgent) {
    titleLines.push('Source agent: ' + String(sourceAgent.name || sourceAgent.id || providerAgentLabel));
  }
  titleLines.push('Account-wide quota shared by all ' + providerAgentLabel + ' agents; one agent is selected, not summed.');
  var maxUsed = 0;
  var nextResetAt = 0;

  function addWindow(label, windowInfo) {
    if (!_statusBarUsageWindowAvailable(windowInfo)) return;
    var usedPct = _statusBarUsageUsedPct(windowInfo);
    if (!Number.isFinite(usedPct)) return;
    if (usedPct < 0) usedPct = 0;
    if (usedPct > 100) usedPct = 100;
    maxUsed = Math.max(maxUsed, usedPct);
    parts.push(label + ' ' + _statusBarFormatPercent(usedPct));
    var resetValue = windowInfo.resets_at || windowInfo.reset_at || windowInfo.resetsAt;
    var resetMs = _statusBarTimestampMs(resetValue);
    if (resetMs && (!nextResetAt || resetMs < nextResetAt)) nextResetAt = resetMs;
    var remainingPct = 100 - usedPct;
    titleLines.push(label + ': used ' + _statusBarFormatPercent(usedPct)
      + ' · remaining ' + _statusBarFormatPercent(remainingPct)
      + ' · ' + _statusBarFormatReset(resetValue));
  }

  addWindow('5h', five);
  addWindow('7d', seven);

  if (!parts.length) {
    return {
      state: 'unknown',
      label: label + ' —',
      level: 'unknown',
      title: label + ' usage payload is present but does not include 5h / weekly windows yet.',
      nextResetAt: 0,
    };
  }

  var level = maxUsed >= 90 ? 'danger' : (maxUsed >= 70 ? 'warn' : 'normal');
  return {
    state: 'known',
    label: label + ' ' + parts.join(' · '),
    level: level,
    title: titleLines.join('\n'),
    nextResetAt: nextResetAt,
  };
}

function _statusBarClaudeUsageView(providerUsage) {
  return _statusBarProviderUsageView(
    STATUS_BAR_CLAUDE_PROVIDER,
    'Claude',
    'Claude 5h and weekly usage limits are unavailable until a Claude agent reports available provider_usage.',
    'Claude account usage limits',
    'Claude Code',
    providerUsage
  );
}

function _statusBarCodexUsageView(providerUsage) {
  return _statusBarProviderUsageView(
    STATUS_BAR_CODEX_PROVIDER,
    'Codex',
    'Codex 5h and weekly usage limits are unavailable until a Codex agent reports available provider_usage.',
    'Codex account usage limits',
    'Codex',
    providerUsage
  );
}

function _statusBarMetricsView() {
  if (typeof healthMetricsGetStatusBarView === 'function') {
    return healthMetricsGetStatusBarView();
  }
  return {
    visible: true,
    label: 'Metrics —',
    level: 'unknown',
    title: 'Runtime metrics tick has not arrived yet.',
  };
}

function _statusBarSupervisorPayload(payload) {
  if (payload && typeof payload === 'object') return payload;
  if (typeof state !== 'undefined'
      && state
      && state.runtime
      && state.runtime.supervisor
      && typeof state.runtime.supervisor === 'object') {
    return state.runtime.supervisor;
  }
  return null;
}

function _statusBarFormatSupervisorLatency(value) {
  var n = Number(value);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n >= 100) return Math.round(n) + 'ms';
  return (Math.round(n * 10) / 10).toFixed(n < 10 ? 1 : 0).replace(/\.0$/, '') + 'ms';
}

function _statusBarSupervisorStateView(payload) {
  var supervisor = _statusBarSupervisorPayload(payload);
  var rawState = supervisor ? String(supervisor.state || '').trim().toLowerCase() : '';
  var states = {
    up: { label: 'up', level: 'normal', dotColor: 'green' },
    degraded: { label: 'degraded', level: 'warn', dotColor: 'amber' },
    restarting: { label: 'restarting', level: 'warn', dotColor: 'amber' },
    down: { label: 'down', level: 'danger', dotColor: 'red' },
    unavailable: { label: 'unavailable', level: 'muted', dotColor: 'grey' },
    na_profile: { label: 'n/a', level: 'muted', dotColor: 'grey' },
  };
  var info = states[rawState] || { label: '—', level: 'muted', dotColor: 'grey' };
  var lines = ['Supervisor: ' + info.label];
  if (rawState === 'na_profile') lines[0] = 'Supervisor: not enabled for this profile';
  if (supervisor && typeof supervisor === 'object') {
    if (supervisor.supervisor_pid !== null && supervisor.supervisor_pid !== undefined && supervisor.supervisor_pid !== '') {
      lines.push('PID: ' + String(supervisor.supervisor_pid));
    }
    if (supervisor.uptime !== null && supervisor.uptime !== undefined && supervisor.uptime !== '') {
      lines.push('Uptime: ' + _statusBarFormatDuration(Number(supervisor.uptime)));
    }
    lines.push('Latency: ' + _statusBarFormatSupervisorLatency(supervisor.last_op_latency_ms));
    if (supervisor.session_count !== null && supervisor.session_count !== undefined && supervisor.session_count !== '') {
      lines.push('Sessions: ' + String(supervisor.session_count));
    }
    if (supervisor.connected !== undefined) {
      lines.push('Connected: ' + (supervisor.connected ? 'yes' : 'no'));
    }
    if (supervisor.reconnect_count !== null && supervisor.reconnect_count !== undefined && supervisor.reconnect_count !== '') {
      lines.push('Reconnects: ' + String(supervisor.reconnect_count));
    }
  } else {
    lines.push('Health projection has not arrived yet.');
  }
  return {
    visible: true,
    state: rawState || 'unknown',
    label: rawState === 'up' ? 'Supervisor' : 'Supervisor ' + info.label,
    level: info.level,
    dotColor: info.dotColor,
    title: lines.join('\n'),
  };
}

function _statusBarApplySupervisorDot(dot, dotColor) {
  if (!dot || !dot.classList) return;
  ['green', 'amber', 'red', 'grey'].forEach(function(color) {
    dot.classList.remove('relay-status-dot--' + color);
  });
  dot.classList.add('relay-status-dot--' + (dotColor || 'grey'));
}

function _statusBarSetSupervisorIndicator(view) {
  var root = _statusBarElement('supervisor-status-indicator');
  if (!root || !view) return;
  var label = _statusBarElement('taskbar-supervisor-label');
  var dot = _statusBarElement('taskbar-supervisor-dot');
  var level = _statusBarNormalizeLevel(view.level);
  if ('hidden' in root) root.hidden = view.visible === false;
  root.title = String(view.title || '');
  if (typeof root.setAttribute === 'function') {
    root.setAttribute('data-supervisor-status', view.state || 'unknown');
    root.setAttribute('data-statusbar-level', level);
    root.setAttribute('aria-label', String(view.title || '').replace(/\n/g, ', '));
  }
  if (label) label.textContent = String(view.label || 'Supervisor —');
  if (dot) dot.title = String(view.title || '');
  _statusBarApplySupervisorDot(dot, view.dotColor);
}

function refreshSupervisorStatusIndicator(payload) {
  var view = _statusBarSupervisorStateView(payload);
  view.visible = statusBarVisibilityEnabled('daemon_status');
  _statusBarSetSupervisorIndicator(view);
}

function _statusBarNormalizeLevel(level) {
  level = String(level || '').toLowerCase();
  if (level === 'danger' || level === 'warn' || level === 'normal' || level === 'unknown') return level;
  return 'muted';
}

function _statusBarSetChip(el, view) {
  if (!el || !view) return;
  var level = _statusBarNormalizeLevel(view.level);
  if ('hidden' in el) el.hidden = view.visible === false;
  if (view.label !== undefined) el.textContent = String(view.label);
  if (view.title !== undefined) el.title = String(view.title || '');
  if (el.classList) {
    el.classList.remove('statusbar-chip--normal');
    el.classList.remove('statusbar-chip--warn');
    el.classList.remove('statusbar-chip--danger');
    el.classList.remove('statusbar-chip--muted');
    el.classList.remove('statusbar-chip--unknown');
    el.classList.add('statusbar-chip--' + level);
  }
  if (typeof el.setAttribute === 'function') {
    el.setAttribute('data-statusbar-level', level);
  }
}

function _statusBarScheduleRefresh(view) {
  if (_statusBarRefreshTimer && typeof clearTimeout === 'function') {
    clearTimeout(_statusBarRefreshTimer);
    _statusBarRefreshTimer = 0;
  }
  var resetAt = view && Number(view.nextResetAt || 0);
  if (!resetAt || typeof setTimeout !== 'function') return;
  var delay = Math.max(15000, Math.min(60000, resetAt - _statusBarNowMs()));
  if (!Number.isFinite(delay)) return;
  _statusBarRefreshTimer = setTimeout(function() {
    _statusBarRefreshTimer = 0;
    refreshStatusBar({ timer: true });
  }, delay);
}

function _statusBarNextResetView(views) {
  var selected = null;
  (views || []).forEach(function(view) {
    var resetAt = view && Number(view.nextResetAt || 0);
    if (!resetAt) return;
    if (!selected || resetAt < Number(selected.nextResetAt || 0)) {
      selected = view;
    }
  });
  return selected;
}

function refreshStatusBar(opts) {
  opts = opts || {};
  var group = String(opts.group === undefined ? _statusBarActiveGroup() : opts.group || '').trim();
  var claudeView = _statusBarClaudeUsageView();
  var codexView = _statusBarCodexUsageView();
  var deployView = _statusBarDeployView(_statusBarDeployState);
  var metricsView = _statusBarMetricsView();
  var agentsView = _statusBarAgentCounts(group);
  var tasksView = _statusBarActiveTaskCount(group);
  var attentionView = _statusBarAttentionCount(group);

  _statusBarSetElementVisible(
    _statusBarElement('daemon-status-indicator'),
    statusBarVisibilityEnabled('daemon_status')
  );
  refreshSupervisorStatusIndicator();
  _statusBarSetChip(_statusBarElement('statusbar-claude-usage'), Object.assign({}, claudeView, {
    visible: _statusBarViewVisible('claude_usage', claudeView.visible),
  }));
  _statusBarSetChip(_statusBarElement('statusbar-codex-usage'), Object.assign({}, codexView, {
    visible: _statusBarViewVisible('codex_usage', codexView.visible),
  }));
  _statusBarSetChip(_statusBarElement('statusbar-deploy'), Object.assign({}, deployView, {
    visible: _statusBarViewVisible('deploy', deployView.visible),
  }));
  _statusBarSetChip(_statusBarElement('statusbar-metrics'), Object.assign({}, metricsView, {
    visible: _statusBarViewVisible('health', metricsView.visible),
  }));
  _statusBarSetChip(_statusBarElement('statusbar-workload'), {
    visible: statusBarVisibilityEnabled('workload'),
    level: agentsView.error ? 'danger' : (agentsView.running ? 'normal' : 'muted'),
    label: agentsView.label,
    title: agentsView.title,
  });
  _statusBarSetChip(_statusBarElement('statusbar-tasks'), {
    visible: statusBarVisibilityEnabled('tasks'),
    level: tasksView.count ? 'normal' : 'muted',
    label: tasksView.label,
    title: tasksView.title,
  });
  _statusBarSetChip(_statusBarElement('statusbar-attention'), {
    visible: statusBarVisibilityEnabled('attention'),
    level: attentionView.count ? 'warn' : 'muted',
    label: attentionView.label,
    title: attentionView.title,
  });
  if (typeof refreshRelayStatusIndicator === 'function') {
    refreshRelayStatusIndicator();
  }
  _statusBarRefreshOverview({
    deploy: deployView,
    metrics: metricsView,
    agents: agentsView,
    tasks: tasksView,
    attention: attentionView,
  });
  _statusBarScheduleRefresh(_statusBarNextResetView([claudeView, codexView]));
}

var _statusBarOverviewOpen = false;

function _statusBarOverviewLevel(views) {
  views = views || {};
  if (views.agents && Number(views.agents.error || 0) > 0) return 'danger';
  if (views.metrics && _statusBarNormalizeLevel(views.metrics.level) === 'danger') return 'danger';
  if (views.attention && Number(views.attention.count || 0) > 0) return 'warn';
  if (views.deploy && _statusBarNormalizeLevel(views.deploy.level) === 'warn') return 'warn';
  if (views.metrics && _statusBarNormalizeLevel(views.metrics.level) === 'warn') return 'warn';
  return 'normal';
}

function _statusBarOverviewLabel(views) {
  views = views || {};
  var errorCount = Number(views.agents && views.agents.error || 0);
  if (errorCount > 0) return errorCount + ' agent error' + (errorCount === 1 ? '' : 's');
  var attentionCount = Number(views.attention && views.attention.count || 0);
  if (attentionCount > 0) return attentionCount + ' attention';
  var deployLabel = String(views.deploy && views.deploy.label || '');
  if (views.deploy && views.deploy.visible !== false && /[1-9]/.test(deployLabel)) return deployLabel;
  return 'Status';
}

function _statusBarOverviewSourceRows() {
  var info = _statusBarElement('statusbar-info');
  if (!info || !info.children) return [];
  var rows = [];
  for (var i = 0; i < info.children.length; i++) {
    var source = info.children[i];
    if (!source || source.id === 'statusbar-overview' || source.hidden) continue;
    var label = String(source.textContent || '').trim();
    if (!label) continue;
    rows.push({
      id: source.id || '',
      label: label,
      title: String(source.title || label),
      source: source,
      level: source.getAttribute && source.getAttribute('data-statusbar-level')
        || (source.classList && source.classList.contains('statusbar-chip--danger') ? 'danger'
          : (source.classList && source.classList.contains('statusbar-chip--warn') ? 'warn' : 'normal')),
    });
  }
  return rows;
}

function _statusBarRenderOverviewMenu() {
  var menu = _statusBarElement('statusbar-overview-menu');
  if (!menu) return;
  menu.innerHTML = '';
  var rows = _statusBarOverviewSourceRows();
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var item = document.createElement(row.source && (row.source.tagName === 'BUTTON' || row.source.getAttribute && row.source.getAttribute('role') === 'button') ? 'button' : 'div');
    if (item.tagName === 'BUTTON') item.type = 'button';
    item.className = 'statusbar-overview-row statusbar-overview-row--' + _statusBarNormalizeLevel(row.level);
    item.setAttribute('role', 'menuitem');
    item.title = row.title;
    var dot = document.createElement('span');
    dot.className = 'statusbar-overview-row-dot';
    dot.setAttribute('aria-hidden', 'true');
    var label = document.createElement('span');
    label.textContent = row.label;
    item.appendChild(dot);
    item.appendChild(label);
    if (item.tagName === 'BUTTON') {
      item.onclick = function(source) {
        return function() {
          statusBarCloseOverview();
          if (source && typeof source.click === 'function') source.click();
        };
      }(row.source);
    }
    menu.appendChild(item);
  }
  if (!rows.length) {
    var empty = document.createElement('div');
    empty.className = 'statusbar-overview-empty';
    empty.textContent = 'No status items are enabled.';
    menu.appendChild(empty);
  }
}

function _statusBarRefreshOverview(views) {
  var button = _statusBarElement('statusbar-overview');
  var label = _statusBarElement('statusbar-overview-label');
  if (!button || !label) return;
  var level = _statusBarOverviewLevel(views);
  label.textContent = _statusBarOverviewLabel(views);
  button.setAttribute('data-level', level);
  if (_statusBarOverviewOpen) _statusBarRenderOverviewMenu();
}

function statusBarCloseOverview() {
  _statusBarOverviewOpen = false;
  var menu = _statusBarElement('statusbar-overview-menu');
  var button = _statusBarElement('statusbar-overview');
  if (menu) menu.hidden = true;
  if (button) button.setAttribute('aria-expanded', 'false');
}

function statusBarToggleOverview(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  var menu = _statusBarElement('statusbar-overview-menu');
  var button = _statusBarElement('statusbar-overview');
  if (!menu || !button) return false;
  _statusBarOverviewOpen = !_statusBarOverviewOpen;
  if (_statusBarOverviewOpen) _statusBarRenderOverviewMenu();
  menu.hidden = !_statusBarOverviewOpen;
  button.setAttribute('aria-expanded', _statusBarOverviewOpen ? 'true' : 'false');
  return _statusBarOverviewOpen;
}

if (typeof document !== 'undefined' && document && typeof document.addEventListener === 'function') {
  document.addEventListener('click', function(event) {
    var target = event && event.target;
    if (target && typeof target.closest === 'function'
        && target.closest('#statusbar-overview, #statusbar-overview-menu')) return;
    statusBarCloseOverview();
  });
  document.addEventListener('keydown', function(event) {
    if (event && event.key === 'Escape') statusBarCloseOverview();
  });
}

function statusBarReceiveDeployState(payload) {
  if (!payload || typeof payload !== 'object') return;
  var active = _statusBarActiveGroup();
  var payloadGroup = String(payload.group || '').trim();
  if (payloadGroup && active && payloadGroup !== active) return;
  _statusBarDeployState = payload;
  refreshStatusBar({ deploy: true });
}

function statusBarRequestDeployState(opts) {
  opts = opts || {};
  var group = _statusBarActiveGroup();
  var now = _statusBarNowMs();
  var key = group || '__default__';
  if (!opts.force
      && key === _statusBarDeployRequestKey
      && now - _statusBarDeployRequestedAt < 60000) {
    return false;
  }
  if (typeof send !== 'function') return false;
  _statusBarDeployRequestKey = key;
  _statusBarDeployRequestedAt = now;
  send({ cmd: 'get_deploy_state', group: group });
  return true;
}

function _statusBarClearDeployPollTimer() {
  if (_statusBarDeployPollTimer && typeof clearTimeout === 'function') {
    clearTimeout(_statusBarDeployPollTimer);
  }
  _statusBarDeployPollTimer = 0;
}

function _statusBarScheduleDeployPoll() {
  if (_statusBarDocumentHidden()) {
    _statusBarClearDeployPollTimer();
    return false;
  }
  if (_statusBarDeployPollTimer || typeof setTimeout !== 'function') {
    return false;
  }
  _statusBarDeployPollTimer = setTimeout(function() {
    _statusBarDeployPollTimer = 0;
    if (_statusBarDocumentHidden()) return;
    statusBarRequestDeployState({ interval: true });
    _statusBarScheduleDeployPoll();
  }, STATUS_BAR_DEPLOY_POLL_INTERVAL_MS);
  return true;
}

function _statusBarHandleVisibilityChange() {
  if (_statusBarDocumentHidden()) {
    _statusBarClearDeployPollTimer();
    return;
  }
  statusBarRequestDeployState({ force: true, visibility: true });
  _statusBarScheduleDeployPoll();
}

function _statusBarInstallVisibilityListener() {
  if (_statusBarVisibilityListenerInstalled) return;
  if (typeof document === 'undefined'
      || !document
      || typeof document.addEventListener !== 'function') {
    return;
  }
  document.addEventListener('visibilitychange', _statusBarHandleVisibilityChange);
  _statusBarVisibilityListenerInstalled = true;
}

function statusBarStartDeployPolling(opts) {
  opts = opts || {};
  _statusBarInstallVisibilityListener();
  if (opts.reset) _statusBarClearDeployPollTimer();
  if (_statusBarDocumentHidden()) {
    _statusBarClearDeployPollTimer();
    return false;
  }
  if (opts.immediate) {
    statusBarRequestDeployState({ force: true, start: true });
  }
  return _statusBarScheduleDeployPoll();
}

function statusBarStopDeployPolling() {
  _statusBarClearDeployPollTimer();
}

function statusBarOpenDeploy() {
  statusBarRequestDeployState({ force: true });
  if (_statusBarDeployState
      && _statusBarDeployState.pending_deploy
      && Number(_statusBarDeployState.pending_deploy.count || 0) > 0
      && typeof togglePanel === 'function') {
    togglePanel('board');
  }
}

function statusBarOpenTasks() {
  if (typeof togglePanel === 'function') togglePanel('board');
}

function statusBarOpenAttention() {
  if (typeof togglePanel === 'function') togglePanel('events');
}

function statusBarChipKeydown(event, handler) {
  if (!event || typeof handler !== 'function') return;
  var key = event.key || event.code || '';
  if (key !== 'Enter' && key !== ' ' && key !== 'Spacebar') return;
  if (typeof event.preventDefault === 'function') event.preventDefault();
  handler();
}

statusBarStartDeployPolling();

if (typeof window !== 'undefined'
    && window
    && typeof window.addEventListener === 'function') {
  window.addEventListener('beforeunload', statusBarStopDeployPolling);
}

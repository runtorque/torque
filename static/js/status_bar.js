/* Bottom status bar helpers (TORQUE:560)
 *
 * This module deliberately updates only stable text/class/title/hidden fields
 * on pre-existing DOM nodes. It must not wipe the taskbar or panel-button DOM:
 * panel toggles, focus, and button identity are preserved across routine state
 * refreshes.
 */

var STATUS_BAR_CLAUDE_PROVIDER = 'claude-code';
var STATUS_BAR_PROVIDER_USAGE_FIELD = 'provider_usage';
var _statusBarDeployState = null;
var _statusBarDeployRequestKey = '';
var _statusBarDeployRequestedAt = 0;
var _statusBarRefreshTimer = 0;

function _statusBarNowMs() {
  return Date.now();
}

function _statusBarElement(id) {
  if (typeof document === 'undefined' || !document.getElementById) return null;
  return document.getElementById(id);
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

function _statusBarUsageUsedPct(windowInfo) {
  if (!windowInfo || typeof windowInfo !== 'object') return NaN;
  var used = Number(windowInfo.used_pct);
  if (Number.isFinite(used)) return used;
  var remaining = Number(windowInfo.remaining_pct);
  if (Number.isFinite(remaining)) return 100 - remaining;
  return NaN;
}

function _statusBarClaudeUsageView(providerUsage) {
  var usage = providerUsage;
  if (usage === undefined) {
    // Keep the frontend-provider contract read isolated here. Courier S2 owns
    // producing state.provider_usage; this scaffold only consumes it.
    usage = (typeof state !== 'undefined'
        && state
        && state[STATUS_BAR_PROVIDER_USAGE_FIELD]
        && state[STATUS_BAR_PROVIDER_USAGE_FIELD][STATUS_BAR_CLAUDE_PROVIDER])
      ? state[STATUS_BAR_PROVIDER_USAGE_FIELD][STATUS_BAR_CLAUDE_PROVIDER]
      : null;
  }
  if (!usage || typeof usage !== 'object' || !Object.keys(usage).length) {
    return {
      state: 'unknown',
      label: 'Claude —',
      level: 'unknown',
      title: 'Claude 5h and weekly usage limits are unavailable until provider_usage arrives.',
      nextResetAt: 0,
    };
  }

  var five = _statusBarUsageWindow(usage, ['five_hour', '5h', 'fiveHour']);
  var seven = _statusBarUsageWindow(usage, ['seven_day', 'weekly', '7d', 'week']);
  var parts = [];
  var titleLines = ['Claude usage limits (provisional provider_usage contract)'];
  var maxUsed = 0;
  var nextResetAt = 0;

  function addWindow(label, windowInfo) {
    if (!windowInfo) return;
    var usedPct = _statusBarUsageUsedPct(windowInfo);
    if (!Number.isFinite(usedPct)) return;
    if (usedPct < 0) usedPct = 0;
    if (usedPct > 100) usedPct = 100;
    maxUsed = Math.max(maxUsed, usedPct);
    parts.push(label + ' ' + _statusBarFormatPercent(usedPct));
    var resetValue = windowInfo.resets_at || windowInfo.reset_at || windowInfo.resetsAt;
    var resetMs = _statusBarTimestampMs(resetValue);
    if (resetMs && (!nextResetAt || resetMs < nextResetAt)) nextResetAt = resetMs;
    var remainingPct = Number(windowInfo.remaining_pct);
    var remainingText = Number.isFinite(remainingPct)
      ? (' · remaining ' + _statusBarFormatPercent(remainingPct))
      : '';
    titleLines.push(label + ': used ' + _statusBarFormatPercent(usedPct)
      + remainingText + ' · ' + _statusBarFormatReset(resetValue));
  }

  addWindow('5h', five);
  addWindow('7d', seven);

  if (!parts.length) {
    return {
      state: 'unknown',
      label: 'Claude —',
      level: 'unknown',
      title: 'Claude usage payload is present but does not include 5h / weekly windows yet.',
      nextResetAt: 0,
    };
  }

  var level = maxUsed >= 90 ? 'danger' : (maxUsed >= 70 ? 'warn' : 'normal');
  return {
    state: 'known',
    label: 'Claude ' + parts.join(' · '),
    level: level,
    title: titleLines.join('\n'),
    nextResetAt: nextResetAt,
  };
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

function refreshStatusBar(opts) {
  opts = opts || {};
  var group = String(opts.group === undefined ? _statusBarActiveGroup() : opts.group || '').trim();
  var claudeView = _statusBarClaudeUsageView();
  var deployView = _statusBarDeployView(_statusBarDeployState);
  var agentsView = _statusBarAgentCounts(group);
  var tasksView = _statusBarActiveTaskCount(group);
  var attentionView = _statusBarAttentionCount(group);

  _statusBarSetChip(_statusBarElement('statusbar-claude-usage'), claudeView);
  _statusBarSetChip(_statusBarElement('statusbar-deploy'), deployView);
  _statusBarSetChip(_statusBarElement('statusbar-workload'), {
    visible: true,
    level: agentsView.error ? 'danger' : (agentsView.running ? 'normal' : 'muted'),
    label: agentsView.label,
    title: agentsView.title,
  });
  _statusBarSetChip(_statusBarElement('statusbar-tasks'), {
    visible: true,
    level: tasksView.count ? 'normal' : 'muted',
    label: tasksView.label,
    title: tasksView.title,
  });
  _statusBarSetChip(_statusBarElement('statusbar-attention'), {
    visible: true,
    level: attentionView.count ? 'warn' : 'muted',
    label: attentionView.label,
    title: attentionView.title,
  });
  _statusBarScheduleRefresh(claudeView);
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

function statusBarOpenDeploy() {
  statusBarRequestDeployState({ force: true });
  if (_statusBarDeployState
      && _statusBarDeployState.pending_deploy
      && Number(_statusBarDeployState.pending_deploy.count || 0) > 0
      && typeof togglePanel === 'function') {
    togglePanel('board');
  }
}

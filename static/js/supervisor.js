/* PTY Supervisor panel */

var SUPERVISOR_REFRESH_MS = 2000;
var SUPERVISOR_BACKOFF_MS = 5000;
var SUPERVISOR_STALL_TIMEOUT_MS = 10000;

var supervisorState = {
  sessions: [],
  terminatingSessionIds: {},
  terminatedSessionIds: {},
  loading: false,
  requestInFlight: false,
  requestTimeout: 0,
  requested: false,
  available: null,
  message: '',
  error: '',
  lastUpdated: 0,
  autoRefresh: true,
  timer: 0,
  sortKey: 'owner',
  sortDirection: 'asc',
  selectedSessionId: '',
  expandedSessionId: '',
  scrollPos: 0,
  tableScrollLeft: 0,
  tableScrollTop: 0,
  restartPending: false,
  restartSawRestarting: false,
  restartMessage: '',
  restartError: '',
};
var _supervisorUiStateRegistered = false;
var _supervisorUiStateHydrated = false;
var _supervisorSkipScrollCapture = false;

function _supervisorUiState() {
  return {
    autoRefresh: !!supervisorState.autoRefresh,
    sortKey: supervisorState.sortKey || 'owner',
    sortDirection: supervisorState.sortDirection === 'desc' ? 'desc' : 'asc',
    selectedSessionId: supervisorState.selectedSessionId || '',
    expandedSessionId: supervisorState.expandedSessionId || '',
    scrollPos: Math.max(0, Number(supervisorState.scrollPos) || 0),
  };
}

function _supervisorApplyUiState(raw) {
  if (!raw || typeof raw !== 'object') return false;
  var sortKeys = { state: true, owner: true, session: true, pid: true, started_at: true, command: true, bytes: true, tty: true, path: true };
  if (sortKeys[raw.sortKey]) supervisorState.sortKey = raw.sortKey;
  if (raw.sortDirection === 'desc' || raw.sortDirection === 'asc') supervisorState.sortDirection = raw.sortDirection;
  if (typeof raw.autoRefresh === 'boolean') supervisorState.autoRefresh = raw.autoRefresh;
  supervisorState.selectedSessionId = String(raw.selectedSessionId || '');
  supervisorState.expandedSessionId = String(raw.expandedSessionId || '');
  supervisorState.scrollPos = Math.max(0, Number(raw.scrollPos) || 0);
  _supervisorUiStateHydrated = true;
  _supervisorSkipScrollCapture = true;
  return true;
}

function _supervisorPersistedUiState() {
  if (typeof state !== 'undefined'
      && state
      && state.supervisor_panel_state
      && typeof state.supervisor_panel_state === 'object'
      && Object.keys(state.supervisor_panel_state).length > 0) {
    return state.supervisor_panel_state;
  }
  return null;
}

function _supervisorUiShim(name) {
  return (typeof window !== 'undefined' && typeof window[name] === 'function' && window[name])
    || (typeof globalThis !== 'undefined' && typeof globalThis[name] === 'function' && globalThis[name])
    || null;
}

function _supervisorRegisterUiState() {
  if (_supervisorUiStateRegistered) return;
  var register = _supervisorUiShim('registerPanelUiState') || _supervisorUiShim('_registerPanelUiState');
  if (!register) return;
  _supervisorUiStateRegistered = true;
  var restored = register('supervisor', {
    key: 'supervisor_panel_state',
    getState: _supervisorUiState,
    setState: _supervisorApplyUiState,
  });
  if (restored && typeof restored === 'object') _supervisorApplyUiState(restored);
  var get = _supervisorUiShim('getPanelUiState') || _supervisorUiShim('_getPanelUiState');
  if (get) _supervisorApplyUiState(get('supervisor'));
}

function _supervisorHydrateUiState() {
  if (!_supervisorUiStateHydrated) _supervisorApplyUiState(_supervisorPersistedUiState());
  _supervisorRegisterUiState();
}

function _supervisorPersistUiState() {
  var next = _supervisorUiState();
  if (typeof state !== 'undefined' && state) state.supervisor_panel_state = next;
  var persist = _supervisorUiShim('persistPanelUiState') || _supervisorUiShim('_persistPanelUiState');
  if (persist) {
    persist('supervisor', next);
  } else if (typeof send === 'function') {
    send({ cmd: 'ui_set_supervisor_panel_state', state: next });
  }
}

function _supervisorTableWrap(root) {
  if (!root || typeof root.querySelector !== 'function') return null;
  return root.querySelector('.supervisor-table-wrap');
}

function _supervisorStoredScrollState() {
  return {
    rootTop: Math.max(0, Number(supervisorState.scrollPos) || 0),
    tableLeft: Math.max(0, Number(supervisorState.tableScrollLeft) || 0),
    tableTop: Math.max(0, Number(supervisorState.tableScrollTop) || 0),
  };
}

function _supervisorCaptureScrollState(root) {
  var next = _supervisorStoredScrollState();
  if (root && typeof root.scrollTop === 'number') next.rootTop = Math.max(0, root.scrollTop || 0);
  var wrap = _supervisorTableWrap(root);
  if (wrap && typeof wrap.scrollLeft === 'number') next.tableLeft = Math.max(0, wrap.scrollLeft || 0);
  if (wrap && typeof wrap.scrollTop === 'number') next.tableTop = Math.max(0, wrap.scrollTop || 0);
  return next;
}

function _supervisorRememberScrollState(next) {
  next = next || _supervisorStoredScrollState();
  supervisorState.scrollPos = Math.max(0, Number(next.rootTop) || 0);
  supervisorState.tableScrollLeft = Math.max(0, Number(next.tableLeft) || 0);
  supervisorState.tableScrollTop = Math.max(0, Number(next.tableTop) || 0);
}

function _supervisorRestoreScrollState(root, next) {
  next = next || _supervisorStoredScrollState();
  if (root && typeof root.scrollTop === 'number') root.scrollTop = Math.max(0, Number(next.rootTop) || 0);
  var wrap = _supervisorTableWrap(root);
  if (wrap && typeof wrap.scrollLeft === 'number') wrap.scrollLeft = Math.max(0, Number(next.tableLeft) || 0);
  if (wrap && typeof wrap.scrollTop === 'number') wrap.scrollTop = Math.max(0, Number(next.tableTop) || 0);
}

_supervisorRegisterUiState();

function supervisorApplyPersistedUiState(raw) {
  _supervisorApplyUiState(raw);
  if (_supervisorVisible()) renderSupervisorPanel({ force: true });
}

function _supervisorRoot() {
  return document.getElementById('panel-supervisor');
}

function _supervisorVisible() {
  if (typeof _panelAppVisible === 'function') return _panelAppVisible('supervisor');
  var root = _supervisorRoot();
  return !!(root && root.classList && !root.classList.contains('panel-hidden'));
}

function _supervisorEsc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
  });
}

function _supervisorJs(value) {
  return String(value == null ? '' : value)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r');
}

function _supervisorAbbrev(value) {
  value = String(value || '');
  if (value.length <= 12) return value || '—';
  return value.slice(0, 8) + '…';
}

function supervisorHumanBytes(value) {
  var bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  var units = ['B', 'KB', 'MB', 'GB'];
  var idx = 0;
  while (bytes >= 1024 && idx < units.length - 1) {
    bytes = bytes / 1024;
    idx += 1;
  }
  if (idx === 0) return Math.round(bytes) + ' B';
  var rounded = bytes >= 10 ? Math.round(bytes).toString() : bytes.toFixed(1);
  return rounded + ' ' + units[idx];
}

function _supervisorIsSelfRow(session) {
  return !!(session && (session.row_type === 'supervisor'
    || session.is_supervisor === true
    || session.session_id === '__supervisor__'));
}

function _supervisorOwnerLabel(session) {
  if (_supervisorIsSelfRow(session)) return 'Supervisor';
  var owner = session && session.owner;
  if (owner && owner.name) return owner.name;
  return session && session.cell_id ? session.cell_id : 'Unknown';
}

function _supervisorOwnerMeta(session) {
  if (_supervisorIsSelfRow(session)) return 'PTY sidecar process';
  var owner = session && session.owner;
  var parts = [];
  if (owner && owner.group) parts.push(owner.group);
  if (owner && owner.kind) parts.push(owner.kind);
  else if (owner && owner.cell_type) parts.push(owner.cell_type);
  if (session && session.orphan) parts.push('orphan');
  return parts.join(' · ');
}

function _supervisorSortValue(session, key) {
  if (!session) return '';
  if (key === 'state') return session.alive ? 0 : 1;
  if (key === 'owner') return _supervisorOwnerLabel(session).toLowerCase();
  if (key === 'session') return String(session.session_id || '');
  if (key === 'pid') return Number(session.pid || 0);
  if (key === 'started_at') return Number(session.started_at || 0);
  if (key === 'command') return String(session.display_command || '').toLowerCase();
  if (key === 'bytes') return Number(session.total_bytes || 0);
  if (key === 'tty') return (Number(session.cols || 0) * 10000) + Number(session.rows || 0);
  if (key === 'path') return String(session.current_path || session.cwd || '').toLowerCase();
  return '';
}

function _supervisorSortedSessions() {
  var allRows = (supervisorState.sessions || []).slice();
  var selfRows = allRows.filter(_supervisorIsSelfRow);
  var rows = allRows.filter(function(row) { return !_supervisorIsSelfRow(row); });
  var key = supervisorState.sortKey || 'owner';
  var dir = supervisorState.sortDirection === 'desc' ? -1 : 1;
  rows.sort(function(a, b) {
    var av = _supervisorSortValue(a, key);
    var bv = _supervisorSortValue(b, key);
    if (typeof av === 'number' || typeof bv === 'number') return ((av || 0) - (bv || 0)) * dir;
    return String(av).localeCompare(String(bv)) * dir;
  });
  return selfRows.concat(rows);
}

function _supervisorStatusText() {
  if (supervisorState.loading && !supervisorState.requested) return 'Loading…';
  if (supervisorState.available === false) return 'Unavailable';
  if (supervisorState.available === true) return 'Connected';
  return 'Not loaded';
}

function _supervisorLastUpdatedText() {
  if (!supervisorState.lastUpdated) return '';
  var ms = supervisorState.lastUpdated * 1000;
  var date = new Date(ms);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function _supervisorLastUpdatedAgeText() {
  if (!supervisorState.lastUpdated) return '';
  var seconds = Math.max(0, Math.floor((Date.now() / 1000) - Number(supervisorState.lastUpdated || 0)));
  if (seconds < 60) return seconds + 's ago';
  var minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + 'm ago';
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ago';
  return Math.floor(hours / 24) + 'd ago';
}

function _supervisorRelativeTime(ts) {
  ts = Number(ts || 0);
  if (!ts) return '';
  if (typeof _relativeTime === 'function') return _relativeTime(ts);
  var diff = Math.max(0, Math.floor((Date.now() / 1000) - ts));
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function _supervisorAbsoluteTime(ts) {
  ts = Number(ts || 0);
  if (!ts) return '';
  var date = new Date(ts * 1000);
  if (!Number.isFinite(date.getTime())) return '';
  return date.toLocaleString();
}

function _supervisorStartedAtText(session, includeExact) {
  var ts = Number(session && session.started_at || 0);
  if (!ts) return '—';
  var rel = _supervisorRelativeTime(ts);
  var exact = _supervisorAbsoluteTime(ts);
  if (includeExact && rel && exact) return rel + ' · ' + exact;
  return rel || exact || '—';
}

function _supervisorStartedAtCellHtml(session) {
  var text = _supervisorStartedAtText(session, false);
  var title = _supervisorStartedAtText(session, true);
  return '<span title="' + _supervisorEsc(title) + '">' + _supervisorEsc(text) + '</span>';
}

function _supervisorFindSession(sessionId) {
  sessionId = String(sessionId || '');
  var rows = supervisorState.sessions || [];
  for (var i = 0; i < rows.length; i += 1) {
    if (String(rows[i].session_id || '') === sessionId) return rows[i];
  }
  return null;
}

function _supervisorCanTerminate(session) {
  return !!(session && !_supervisorIsSelfRow(session)
    && session.terminable !== false
    && session.alive !== false
    && String(session.session_id || ''));
}

function _supervisorRuntime() {
  if (typeof state === 'undefined'
      || !state
      || !state.runtime
      || !state.runtime.supervisor
      || typeof state.runtime.supervisor !== 'object') {
    return null;
  }
  return state.runtime.supervisor;
}

function _supervisorRuntimeState() {
  var supervisor = _supervisorRuntime();
  return supervisor ? String(supervisor.state || '').trim().toLowerCase() : '';
}

function _supervisorRestartUnavailableReason(stateName) {
  if (stateName === 'na_profile') {
    return 'Supervisor restart is not available for this profile.';
  }
  if (stateName === 'unavailable') {
    return 'Supervisor restart is unavailable.';
  }
  return '';
}

function _supervisorRestartPhase() {
  var stateName = _supervisorRuntimeState();
  var unavailableReason = _supervisorRestartUnavailableReason(stateName);
  var restarting = stateName === 'restarting';
  var pending = !!supervisorState.restartPending;
  if (restarting) {
    return {
      disabled: true,
      inFlight: true,
      label: 'Restarting…',
      status: 'Re-exec in progress; live worker PTYs will be adopted.',
    };
  }
  if (pending) {
    return {
      disabled: true,
      inFlight: true,
      label: 'Requesting restart…',
      status: supervisorState.restartMessage || 'Restart request sent; waiting for supervisor response.',
    };
  }
  if (unavailableReason) {
    return {
      disabled: true,
      inFlight: false,
      label: 'Restart unavailable',
      status: supervisorState.restartMessage || unavailableReason,
    };
  }
  return {
    disabled: false,
    inFlight: false,
    label: 'Restart supervisor',
    status: supervisorState.restartMessage || '',
  };
}

function _supervisorRestartControlInnerHtml() {
  var phase = _supervisorRestartPhase();
  var buttonClass = 'supervisor-restart' + (phase.inFlight ? ' supervisor-restart-busy' : '');
  var title = phase.disabled
    ? (phase.status || phase.label)
    : 'Re-exec the PTY supervisor in place and adopt live worker sessions.';
  var html = '<button id="supervisor-restart-btn" class="' + buttonClass + '" type="button"'
    + (phase.disabled ? ' disabled aria-disabled="true"' : '')
    + ' title="' + _supervisorEsc(title) + '"'
    + ' onclick="supervisorRestart(event)">'
    + (phase.inFlight ? '<span class="supervisor-spinner" aria-hidden="true"></span>' : '')
    + '<span>' + _supervisorEsc(phase.label) + '</span></button>';
  var statusText = supervisorState.restartError || phase.status || '';
  if (statusText) {
    var statusClass = 'supervisor-restart-status'
      + (supervisorState.restartError ? ' supervisor-restart-status-error' : '');
    html += '<span class="' + statusClass + '" '
      + (supervisorState.restartError ? 'role="alert"' : 'role="status" aria-live="polite"')
      + '>' + _supervisorEsc(statusText) + '</span>';
  }
  return html;
}

function _supervisorRestartControlHtml() {
  return '<div id="supervisor-restart-control" class="supervisor-restart-control">'
    + _supervisorRestartControlInnerHtml()
    + '</div>';
}

function _supervisorUpdateRestartControlDom() {
  if (typeof document === 'undefined' || !document.getElementById) return false;
  var el = document.getElementById('supervisor-restart-control');
  if (!el || !el.parentNode) return false;
  el.innerHTML = _supervisorRestartControlInnerHtml();
  return true;
}

function _supervisorHandleRuntimeRestartState() {
  var stateName = _supervisorRuntimeState();
  if (stateName === 'restarting') {
    supervisorState.restartSawRestarting = true;
    return;
  }
  if ((stateName === 'up' || stateName === 'degraded')
      && supervisorState.restartSawRestarting) {
    supervisorState.restartPending = false;
    supervisorState.restartSawRestarting = false;
    if (!supervisorState.restartError) {
      supervisorState.restartMessage = supervisorState.restartMessage
        || 'Supervisor restart completed; live sessions preserved.';
    }
  }
}

function _supervisorRestartFailureText(msg) {
  var bits = [];
  if (msg && msg.message) bits.push(String(msg.message));
  if (msg && msg.error) bits.push(String(msg.error));
  if (!bits.length) bits.push('PTY supervisor restart failed.');
  return bits.join(' ');
}

function supervisorReceiveRuntime(_payload) {
  _supervisorHandleRuntimeRestartState();
  if (!_supervisorVisible()) return false;
  if (!_supervisorUpdateRestartControlDom()) {
    renderSupervisorPanel({ force: true });
  }
  return true;
}

function supervisorReceiveRestart(msg) {
  msg = msg || {};
  supervisorState.restartPending = false;
  supervisorState.restartSawRestarting = false;
  if (msg.ok === false) {
    supervisorState.restartMessage = '';
    supervisorState.restartError = _supervisorRestartFailureText(msg);
  } else {
    supervisorState.restartError = '';
    supervisorState.restartMessage = msg.message || (
      msg.available === false
        ? 'PTY supervisor restart is unavailable.'
        : 'PTY supervisor restart requested.'
    );
  }
  _supervisorHandleRuntimeRestartState();
  if (_supervisorVisible()) renderSupervisorPanel({ force: true });
}

function _supervisorTerminateLabel(session) {
  if (!session) return 'this session';
  var owner = _supervisorOwnerLabel(session);
  var sid = _supervisorAbbrev(session.session_id || '');
  var pid = session.pid ? 'pid ' + session.pid : '';
  var bits = [];
  if (owner && owner !== 'Unknown') bits.push(owner);
  if (pid) bits.push(pid);
  if (sid && sid !== '—') bits.push('session ' + sid);
  return bits.length ? bits.join(' · ') : 'this session';
}

function _supervisorHiddenTerminated(session) {
  if (!session || _supervisorIsSelfRow(session)) return false;
  var sid = String(session.session_id || '');
  if (!sid) return false;
  var expires = Number((supervisorState.terminatedSessionIds || {})[sid] || 0);
  if (!expires) return false;
  if (Date.now() <= expires) return true;
  delete supervisorState.terminatedSessionIds[sid];
  return false;
}

function _supervisorFilterIncomingSessions(rows) {
  return (Array.isArray(rows) ? rows : []).filter(function(session) {
    return !_supervisorHiddenTerminated(session);
  });
}

function supervisorTerminateSession(sessionId, event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  sessionId = String(sessionId || '');
  var session = _supervisorFindSession(sessionId);
  if (!_supervisorCanTerminate(session)) return Promise.resolve(false);
  var confirmFn = _supervisorUiShim('showConfirm');
  if (!confirmFn) {
    supervisorState.error = 'Confirmation modal is unavailable.';
    renderSupervisorPanel({ force: _supervisorVisible() });
    return Promise.resolve(false);
  }
  var message = 'Terminate PTY session for ' + _supervisorTerminateLabel(session)
    + '? This kills the underlying process and cannot be undone.';
  return Promise.resolve(confirmFn(message, { label: 'Terminate', variant: 'btn-danger' }))
    .then(function(ok) {
      if (!ok) return false;
      supervisorState.terminatingSessionIds[sessionId] = true;
      renderSupervisorPanel({ force: _supervisorVisible() });
      if (typeof send !== 'function') {
        delete supervisorState.terminatingSessionIds[sessionId];
        supervisorState.error = 'WebSocket command sender is unavailable.';
        renderSupervisorPanel({ force: _supervisorVisible() });
        return false;
      }
      try {
        send({ cmd: 'supervisor_session_terminate', session_id: sessionId });
      } catch (err) {
        delete supervisorState.terminatingSessionIds[sessionId];
        supervisorState.error = 'WebSocket command sender failed.';
        renderSupervisorPanel({ force: _supervisorVisible() });
        return false;
      }
      return true;
    });
}

function supervisorRestart(event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  var phase = _supervisorRestartPhase();
  if (phase.disabled) return Promise.resolve(false);
  var confirmFn = _supervisorUiShim('showConfirm');
  if (!confirmFn) {
    supervisorState.restartError = 'Confirmation modal is unavailable.';
    renderSupervisorPanel({ force: _supervisorVisible() });
    return Promise.resolve(false);
  }
  var message = 'Restart the PTY supervisor? Torque will re-exec the supervisor in place '
    + 'and adopt the live PTY sessions afterward, so live workers are preserved. '
    + 'The panel may briefly show restarting while it completes.';
  return Promise.resolve(confirmFn(message, {
    label: 'Restart supervisor',
    variant: 'btn-warning',
  })).then(function(ok) {
    if (!ok) return false;
    supervisorState.restartPending = true;
    supervisorState.restartSawRestarting = _supervisorRuntimeState() === 'restarting';
    supervisorState.restartMessage = 'Restart request sent; waiting for supervisor response.';
    supervisorState.restartError = '';
    renderSupervisorPanel({ force: _supervisorVisible() });
    if (typeof send !== 'function') {
      supervisorState.restartPending = false;
      supervisorState.restartMessage = '';
      supervisorState.restartError = 'WebSocket command sender is unavailable.';
      renderSupervisorPanel({ force: _supervisorVisible() });
      return false;
    }
    try {
      send({ cmd: 'supervisor_restart' });
    } catch (err) {
      supervisorState.restartPending = false;
      supervisorState.restartMessage = '';
      supervisorState.restartError = 'WebSocket command sender failed.';
      renderSupervisorPanel({ force: _supervisorVisible() });
      return false;
    }
    return true;
  });
}

function _supervisorUnavailableBannerText() {
  var base = supervisorState.error || supervisorState.message || 'PTY supervisor is unavailable.';
  var age = _supervisorLastUpdatedAgeText();
  if (age) return base + ' Showing last successful list; last update ' + age + '.';
  return base;
}

function _supervisorSortButton(key, label) {
  var active = supervisorState.sortKey === key;
  var arrow = active ? (supervisorState.sortDirection === 'desc' ? ' ▼' : ' ▲') : '';
  return '<button class="supervisor-sort" type="button" onclick="supervisorSortBy(\''
    + _supervisorJs(key) + '\')">' + _supervisorEsc(label + arrow) + '</button>';
}

function supervisorSortBy(key) {
  key = String(key || 'owner');
  if (supervisorState.sortKey === key) {
    supervisorState.sortDirection = supervisorState.sortDirection === 'desc' ? 'asc' : 'desc';
  } else {
    supervisorState.sortKey = key;
    supervisorState.sortDirection = (key === 'bytes' || key === 'pid' || key === 'started_at') ? 'desc' : 'asc';
  }
  _supervisorPersistUiState();
  renderSupervisorPanel({ force: true });
}

function supervisorSelectSession(sessionId) {
  supervisorState.selectedSessionId = String(sessionId || '');
  _supervisorPersistUiState();
  renderSupervisorPanel({ force: true });
}

function supervisorToggleDetails(sessionId, event) {
  if (event) {
    if (typeof event.preventDefault === 'function') event.preventDefault();
    if (typeof event.stopPropagation === 'function') event.stopPropagation();
  }
  sessionId = String(sessionId || '');
  supervisorState.expandedSessionId = supervisorState.expandedSessionId === sessionId ? '' : sessionId;
  supervisorState.selectedSessionId = sessionId;
  _supervisorPersistUiState();
  renderSupervisorPanel({ force: true });
}

function supervisorCollapseDetails(event) {
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  if (!supervisorState.expandedSessionId) return;
  supervisorState.expandedSessionId = '';
  _supervisorPersistUiState();
  renderSupervisorPanel({ force: true });
}

function _supervisorDetailFieldsHtml(session) {
  var owner = session.owner || {};
  var shell = Array.isArray(session.shell_argv) ? session.shell_argv.join(' ') : '';
  var fields;
  if (_supervisorIsSelfRow(session)) {
    fields = [
      ['Process', 'Supervisor'],
      ['PID', session.pid || '—'],
      ['Started at', _supervisorStartedAtText(session, true)],
      ['Command', session.display_command || 'PTY supervisor'],
    ];
  } else {
    fields = [
      ['Cell', session.cell_id || '—'],
      ['Owner', owner.name || '—'],
      ['Group', owner.group || '—'],
      ['Status', owner.status || '—'],
      ['Current path', session.current_path || session.cwd || '—'],
      ['PTY argv', shell || '—'],
      ['Bootstrap dir', session.bootstrap_dir || '—'],
      ['Started at', _supervisorStartedAtText(session, true)],
    ];
  }
  var html = '<dl class="supervisor-detail-grid">';
  fields.forEach(function(pair) {
    html += '<div><dt>' + _supervisorEsc(pair[0]) + '</dt><dd title="'
      + _supervisorEsc(pair[1]) + '">' + _supervisorEsc(pair[1]) + '</dd></div>';
  });
  if (session.orphan) {
    html += '<div class="supervisor-detail-warning"><dt>Ownership</dt><dd>Orphan or stale owner/session mapping</dd></div>';
  }
  html += '</dl>';
  return html;
}

function _supervisorDetailHtml(session, colSpan) {
  if (supervisorState.expandedSessionId !== session.session_id) return '';
  return '<tr class="supervisor-detail-row" onclick="event.stopPropagation()"><td colspan="' + colSpan + '">'
    + _supervisorDetailFieldsHtml(session) + '</td></tr>';
}

function _supervisorActionHtml(session) {
  if (!_supervisorCanTerminate(session)) return '<span class="supervisor-muted">—</span>';
  var sid = String(session.session_id || '');
  var pending = !!((supervisorState.terminatingSessionIds || {})[sid]);
  return '<button class="supervisor-terminate" type="button" '
    + (pending ? 'disabled ' : '')
    + 'onclick="supervisorTerminateSession(\'' + _supervisorJs(sid) + '\', event)">'
    + (pending ? 'Terminating…' : 'Terminate') + '</button>';
}

function _supervisorRowHtml(session) {
  var sid = String(session.session_id || '');
  var selected = supervisorState.selectedSessionId === sid ? ' selected' : '';
  var expanded = supervisorState.expandedSessionId === sid;
  var stateClass = session.alive ? 'alive' : 'exited';
  var ownerMeta = _supervisorOwnerMeta(session);
  var path = session.current_path || session.cwd || '';
  var command = session.display_command || '—';
  var tty = _supervisorIsSelfRow(session) ? '—' : ((session.cols || 0) + '×' + (session.rows || 0));
  var selfClass = _supervisorIsSelfRow(session) ? ' supervisor-row-self' : '';
  var html = '<tr class="supervisor-row' + selected + selfClass + '" onclick="supervisorToggleDetails(\''
    + _supervisorJs(sid) + '\', event)">'
    + '<td><span class="supervisor-chevron">' + (expanded ? '▾' : '▸') + '</span> '
    + '<span class="supervisor-pill supervisor-pill-' + stateClass + '">'
    + (_supervisorIsSelfRow(session) ? 'Supervisor' : (session.alive ? 'Alive' : 'Exited')) + '</span></td>'
    + '<td><div class="supervisor-owner"><strong>' + _supervisorEsc(_supervisorOwnerLabel(session))
    + '</strong><span>' + _supervisorEsc(ownerMeta) + '</span></div></td>'
    + '<td><code title="' + _supervisorEsc(sid) + '">' + _supervisorEsc(_supervisorAbbrev(sid)) + '</code></td>'
    + '<td>' + _supervisorEsc(session.pid || '—') + '</td>'
    + '<td>' + _supervisorStartedAtCellHtml(session) + '</td>'
    + '<td class="supervisor-command" title="' + _supervisorEsc(command) + '">' + _supervisorEsc(command) + '</td>'
    + '<td>' + _supervisorEsc(supervisorHumanBytes(session.total_bytes)) + '</td>'
    + '<td>' + _supervisorEsc(tty) + '</td>'
    + '<td class="supervisor-path" title="' + _supervisorEsc(path) + '">' + _supervisorEsc(path || '—') + '</td>'
    + '<td>' + _supervisorActionHtml(session) + '</td>'
    + '</tr>';
  html += _supervisorDetailHtml(session, 10);
  return html;
}

function _supervisorCardHtml(session) {
  var sid = String(session.session_id || '');
  var selected = supervisorState.selectedSessionId === sid ? ' selected' : '';
  var expanded = supervisorState.expandedSessionId === sid;
  var stateClass = session.alive ? 'alive' : 'exited';
  var command = session.display_command || '—';
  var tty = _supervisorIsSelfRow(session) ? '—' : ((session.cols || 0) + '×' + (session.rows || 0));
  var path = session.current_path || session.cwd || '';
  var html = '<div class="supervisor-card' + selected + (_supervisorIsSelfRow(session) ? ' supervisor-card-self' : '')
    + '" onclick="supervisorToggleDetails(\'' + _supervisorJs(sid) + '\', event)">'
    + '<div class="supervisor-card-top"><span class="supervisor-pill supervisor-pill-' + stateClass + '">'
    + (_supervisorIsSelfRow(session) ? 'Supervisor' : (session.alive ? 'Alive' : 'Exited')) + '</span><strong>'
    + _supervisorEsc(_supervisorOwnerLabel(session)) + '</strong><span>'
    + _supervisorEsc(_supervisorStartedAtText(session, false)) + '</span></div>'
    + '<div class="supervisor-card-command">' + _supervisorEsc(command) + '</div>'
    + '<div class="supervisor-card-meta">pid ' + _supervisorEsc(session.pid || '—')
    + ' · ' + _supervisorEsc(tty) + ' · <code title="' + _supervisorEsc(sid) + '">'
    + _supervisorEsc(_supervisorAbbrev(sid)) + '</code></div>'
    + '<div class="supervisor-card-path" title="' + _supervisorEsc(path) + '">' + _supervisorEsc(path || '—') + '</div>'
    + '<div class="supervisor-card-actions">' + _supervisorActionHtml(session) + '</div>';
  if (expanded) html += '<div class="supervisor-card-details">' + _supervisorDetailFieldsHtml(session) + '</div>';
  html += '</div>';
  return html;
}

function _supervisorBodyHtml(rows) {
  if (supervisorState.loading && !rows.length) {
    return '<div class="supervisor-empty">Loading supervisor sessions…</div>';
  }
  if (!rows.length) {
    return '<div class="supervisor-empty">No supervisor sessions reported.</div>';
  }
  var table = '<div class="supervisor-table-wrap"><table class="supervisor-table"><thead><tr>'
    + '<th>' + _supervisorSortButton('state', 'State') + '</th>'
    + '<th>' + _supervisorSortButton('owner', 'Owner') + '</th>'
    + '<th>' + _supervisorSortButton('session', 'Session') + '</th>'
    + '<th>' + _supervisorSortButton('pid', 'PID') + '</th>'
    + '<th>' + _supervisorSortButton('started_at', 'Started') + '</th>'
    + '<th>' + _supervisorSortButton('command', 'Command') + '</th>'
    + '<th>' + _supervisorSortButton('bytes', 'Bytes') + '</th>'
    + '<th>' + _supervisorSortButton('tty', 'TTY') + '</th>'
    + '<th>' + _supervisorSortButton('path', 'Path') + '</th>'
    + '<th>Action</th></tr></thead><tbody>'
    + rows.map(_supervisorRowHtml).join('')
    + '</tbody></table></div>';
  var cards = '<div class="supervisor-cards">' + rows.map(_supervisorCardHtml).join('') + '</div>';
  return table + cards;
}

function renderSupervisorPanel(opts) {
  opts = opts || {};
  var root = _supervisorRoot();
  if (!root) return;
  _supervisorHydrateUiState();
  var skipScrollCapture = _supervisorSkipScrollCapture;
  var previousRootScroll = Math.max(0, Number(supervisorState.scrollPos) || 0);
  var scrollState = _supervisorCaptureScrollState(root);
  if (skipScrollCapture) {
    var storedScrollState = _supervisorStoredScrollState();
    scrollState.rootTop = storedScrollState.rootTop;
  }
  _supervisorSkipScrollCapture = false;
  _supervisorRememberScrollState(scrollState);
  if (!skipScrollCapture && scrollState.rootTop !== previousRootScroll) {
    _supervisorPersistUiState();
  }
  if (!opts.force && !_supervisorVisible()) return;

  var rows = _supervisorSortedSessions();
  var sessionCount = rows.filter(function(row) { return !_supervisorIsSelfRow(row); }).length;
  var hasSupervisorRow = rows.length > sessionCount;
  var status = _supervisorStatusText();
  var lastUpdated = _supervisorLastUpdatedText();
  var banner = '';
  if (supervisorState.available === false) {
    banner = '<div class="supervisor-banner supervisor-banner-unavailable">'
      + _supervisorEsc(_supervisorUnavailableBannerText())
      + '</div>';
  } else if (supervisorState.error) {
    banner = '<div class="supervisor-banner supervisor-banner-error">' + _supervisorEsc(supervisorState.error) + '</div>';
  }

  root.innerHTML = '<div class="supervisor-panel">'
    + '<div class="supervisor-header ui-panel-header ui-panel-header--surface"><div class="supervisor-header-copy ui-panel-header__copy"><h2 class="ui-panel-header__title">Supervisor</h2>'
    + '<p class="ui-panel-header__subtitle">PTY sidecar sessions and process controls</p></div>'
    + '<div class="supervisor-status ui-panel-header__actions"><span class="supervisor-dot '
    + (supervisorState.available === true ? 'connected' : 'offline') + '"></span>'
    + '<span>' + _supervisorEsc(status) + '</span>'
    + '<span>' + _supervisorEsc(sessionCount + ' session' + (sessionCount === 1 ? '' : 's')
      + (hasSupervisorRow ? ' + supervisor' : '')) + '</span></div></div>'
    + '<div class="supervisor-toolbar ui-toolbar ui-toolbar--bordered"><button type="button" onclick="supervisorRefresh()">⟳ Refresh</button>'
    + _supervisorRestartControlHtml()
    + '<label class="supervisor-auto"><input type="checkbox" onchange="supervisorSetAutoRefresh(this.checked)"'
    + (supervisorState.autoRefresh ? ' checked' : '') + '> Auto</label>'
    + (lastUpdated ? '<span class="supervisor-updated">Updated ' + _supervisorEsc(lastUpdated) + '</span>' : '')
    + '</div>'
    + banner
    + '<div class="supervisor-body" onclick="supervisorCollapseDetails(event)">'
    + _supervisorBodyHtml(rows)
    + '</div></div>';
  _supervisorRestoreScrollState(root, scrollState);
}

function _supervisorClearTimer() {
  if (supervisorState.timer && typeof clearTimeout === 'function') {
    clearTimeout(supervisorState.timer);
  }
  supervisorState.timer = 0;
}

function _supervisorClearRequestTimeout() {
  if (supervisorState.requestTimeout && typeof clearTimeout === 'function') {
    clearTimeout(supervisorState.requestTimeout);
  }
  supervisorState.requestTimeout = 0;
}

function _supervisorArmRequestTimeout() {
  _supervisorClearRequestTimeout();
  if (typeof setTimeout !== 'function') return;
  supervisorState.requestTimeout = setTimeout(function() {
    supervisorState.requestTimeout = 0;
    supervisorState.requestInFlight = false;
    supervisorState.loading = false;
    if (_supervisorVisible()) renderSupervisorPanel({ force: true });
  }, SUPERVISOR_STALL_TIMEOUT_MS);
}

function supervisorSchedulePolling() {
  _supervisorClearTimer();
  if (!supervisorState.autoRefresh || !_supervisorVisible()) return;
  if (typeof setTimeout !== 'function') return;
  var delay = supervisorState.available === false ? SUPERVISOR_BACKOFF_MS : SUPERVISOR_REFRESH_MS;
  supervisorState.timer = setTimeout(function() {
    supervisorState.timer = 0;
    if (!supervisorState.autoRefresh || !_supervisorVisible()) return;
    supervisorRequestSessions(false);
    supervisorSchedulePolling();
  }, delay);
}

function supervisorRequestSessions(force) {
  if (supervisorState.requestInFlight && !force) return;
  supervisorState.requested = true;
  supervisorState.requestInFlight = true;
  _supervisorArmRequestTimeout();
  supervisorState.loading = true;
  supervisorState.error = '';
  renderSupervisorPanel({ force: _supervisorVisible() });
  if (typeof send === 'function') {
    try {
      send({ cmd: 'supervisor_sessions_list' });
    } catch (err) {
      _supervisorClearRequestTimeout();
      supervisorState.requestInFlight = false;
      supervisorState.loading = false;
      supervisorState.error = 'WebSocket command sender failed.';
      renderSupervisorPanel({ force: _supervisorVisible() });
    }
  } else {
    _supervisorClearRequestTimeout();
    supervisorState.requestInFlight = false;
    supervisorState.loading = false;
    supervisorState.error = 'WebSocket command sender is unavailable.';
    renderSupervisorPanel({ force: _supervisorVisible() });
  }
}

function supervisorReceiveSessions(msg) {
  msg = msg || {};
  _supervisorClearRequestTimeout();
  supervisorState.requestInFlight = false;
  supervisorState.loading = false;
  supervisorState.requested = true;
  var unavailable = msg.available === false || !!msg.error || msg.type === 'error';
  supervisorState.available = unavailable ? false : !!msg.available;
  supervisorState.message = msg.message || '';
  supervisorState.error = msg.error || (unavailable ? (msg.message || 'PTY supervisor is unavailable.') : '');
  var terminateSessionId = String(msg.terminate_session_id || msg.terminated_session_id || '');
  if (terminateSessionId) delete supervisorState.terminatingSessionIds[terminateSessionId];
  if (msg.terminated_session_id) {
    supervisorState.terminatedSessionIds[String(msg.terminated_session_id)] = Date.now() + 10000;
  }
  if (!unavailable) {
    supervisorState.sessions = _supervisorFilterIncomingSessions(msg.sessions);
    supervisorState.lastUpdated = Number(msg.refreshed_at || (Date.now() / 1000));
  }
  if (_supervisorVisible()) renderSupervisorPanel({ force: true });
  supervisorSchedulePolling();
}

function supervisorRefresh() {
  supervisorRequestSessions(true);
  supervisorSchedulePolling();
}

function supervisorSetAutoRefresh(checked) {
  supervisorState.autoRefresh = !!checked;
  _supervisorPersistUiState();
  if (supervisorState.autoRefresh) {
    supervisorSchedulePolling();
  } else {
    _supervisorClearTimer();
  }
  renderSupervisorPanel({ force: _supervisorVisible() });
}

function supervisorEnsureLoaded() {
  renderSupervisorPanel({ force: _supervisorVisible() });
  if (!supervisorState.requested || !supervisorState.requestInFlight) {
    supervisorRequestSessions(false);
  }
  supervisorSchedulePolling();
}

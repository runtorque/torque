/* WebSocket connection and shared state */

function _torqueRandomClientId() {
  try {
    if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch (_err) {}
  return 'client-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
}

const TORQUE_CLIENT_ID = _torqueRandomClientId();

function _torqueClientId() {
  return TORQUE_CLIENT_ID;
}

function _torqueAppendClientId(url) {
  const clientId = (typeof _torqueClientId === 'function') ? _torqueClientId() : '';
  if (!clientId) return url;
  const sep = String(url).indexOf('?') >= 0 ? '&' : '?';
  return url + sep + 'client_id=' + encodeURIComponent(clientId);
}

const WS_PROTOCOL = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = _torqueAppendClientId(`${WS_PROTOCOL}//${location.host}/ws`);
let ws = null;
let state = {
  agents: {},
  groups: {},
  children: {},
  active_session_id: null,
  active_group: '',
  selected_principal_id: '',
  selected_agent_id: '',
  detached_panels: {},
  window_bounds: {},
  workspace_sidebar_width: 0,
  terminal_direct_messages_height: 0,
  terminal_compose_height: 0,
  engineer_panel_split_fraction: 0.30,
  context_panel_split_ratio: 0.38,
  supervisor_panel_state: {},
  agent_message_history: {},
  direct_messages_by_agent: {},
  agent_message_loops: {},
  agent_peer_threads: {},
};
let dragInProgress = false;
let selectedAgentId = null;
let selectedTerminalId = null;
let focusedItemId = null;
let _cachedAgentTemplates = [];
var _selectedAgentGroupSyncedDuringDelta = false;
var _clientScopedFocusActive = false;

function _selectedAgentRecord(agentId) {
  agentId = String(agentId || '').trim();
  if (!agentId || !state || !state.agents) return null;
  var agent = state.agents[agentId] || null;
  if (!agent) return null;
  if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(agent)) {
    return null;
  }
  return agent;
}

function _selectedAgentRootRecord(cell) {
  if (!cell) return null;
  if (cell.cell_type === 'terminal') {
    if (!cell.parent_id) return null;
    return _selectedAgentRecord(cell.parent_id);
  }
  return _selectedAgentRecord(cell.id);
}

function _selectedAgentFocusId(agent) {
  if (!agent || !agent.id) return '';
  return agent.id || '';
}

function _selectedAgentSelectionForActiveSession() {
  if (!state || !state.active_session_id || !state.agents) return null;
  for (const [id, cell] of Object.entries(state.agents)) {
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) continue;
    if (cell.session_id !== state.active_session_id) continue;
    var root = _selectedAgentRootRecord(cell);
    if (!root) return null;
    return {
      agentId: root.id || '',
      terminalId: id,
      cell: cell,
      focusId: cell.cell_type === 'terminal'
        ? id
        : (_selectedAgentFocusId(root) || root.id || ''),
    };
  }
  return null;
}

function _syncActiveGroupToSelectedAgent(agent, opts) {
  opts = opts || {};
  if (opts.persist === undefined) opts.persist = true;
  if (!agent || !agent.group) return;
  if (typeof _singleGroupModeEnabled === 'function'
      && !_singleGroupModeEnabled()) return;
  var group = String(agent.group || '').trim();
  if (!group) return;
  if (state && String(state.active_group || '') !== group) {
    _selectedAgentGroupSyncedDuringDelta = true;
  }
  if (state) state.active_group = group;
  if (typeof _pendingActiveGroup !== 'undefined') _pendingActiveGroup = '';
  if (typeof _writeStoredActiveGroup === 'function') _writeStoredActiveGroup(group);
  if (opts.persist && typeof _persistActiveGroup === 'function') {
    _persistActiveGroup(group);
  }
}

function _applySelectedAgentFromServer(agentId, opts) {
  opts = opts || {};
  var requestedAgentId = String(agentId || '').trim();
  var requestedCell = _selectedAgentRecord(requestedAgentId);
  var agent = _selectedAgentRootRecord(requestedCell);
  if (agent) {
    var nextSelectedAgentId = agent.id || '';
    if (state) state.selected_agent_id = nextSelectedAgentId;
    selectedAgentId = nextSelectedAgentId;
    if (requestedCell && requestedCell.cell_type === 'terminal') {
      selectedTerminalId = requestedCell.id || requestedAgentId;
      focusedItemId = requestedCell.id || (_selectedAgentFocusId(agent) || nextSelectedAgentId);
    } else {
      focusedItemId = _selectedAgentFocusId(agent) || nextSelectedAgentId;
    }
    if (opts.syncGroup !== false) _syncActiveGroupToSelectedAgent(agent, opts);
    return nextSelectedAgentId;
  }
  if (state) state.selected_agent_id = '';
  selectedAgentId = null;
  focusedItemId = null;
  return '';
}

function _wsRoleList(msg) {
  return (msg && (msg.roles || msg.templates)) || [];
}

var _firstStateReceived = false;
var _expectedSeq = 0;
/* Monotonic state revision: bumped whenever shared `state` is mutated by a
 * delta batch, a full snapshot, or a compact lazy-load merge. Derived-view
 * memos (e.g. the events attention feed) key on it instead of rebuilding
 * from full state scans on every read. */
var _torqueStateRevision = 0;
function _torqueBumpStateRevision() { _torqueStateRevision++; }
var _resyncPending = false;
var _awaitingFullState = false;
var _pendingDeltaSurfaceInvalidations = null;
var _pendingDeltaSurfaceRenderFrame = 0;
function _daemonStatusDisplayValue(value, fallback) {
  if (typeof _daemonDisplayValue === 'function') {
    return _daemonDisplayValue(value, fallback);
  }
  if (value === null || value === undefined || value === '') return fallback || '—';
  return String(value);
}

function _daemonStatusDurationFromMs(ms) {
  if (typeof _formatDaemonDurationFromMs === 'function') {
    return _formatDaemonDurationFromMs(ms);
  }
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

function _daemonStatusConnectedFromDom() {
  var dot = document.getElementById('taskbar-conn-dot')
    || document.getElementById('conn-dot');
  return !!(dot && dot.classList && dot.classList.contains('ok'));
}

function _daemonStatusTooltip(connected) {
  var runtime = (typeof state !== 'undefined' && state && state.runtime)
    ? state.runtime
    : {};
  var lines = ['Daemon: ' + (connected ? 'running' : 'disconnected')];
  lines.push('Version: ' + _daemonStatusDisplayValue(runtime.version, 'unknown'));
  var started = Number(runtime.started_at);
  if (Number.isFinite(started) && started > 0) {
    lines.push('Uptime: ' + _daemonStatusDurationFromMs(
      Date.now() - (started * 1000)
    ));
  }
  lines.push('Port: ' + _daemonStatusDisplayValue(runtime.port));
  return lines.join('\n');
}

function refreshDaemonStatusIndicator(connected) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var isConnected = (typeof connected === 'boolean')
    ? connected
    : _daemonStatusConnectedFromDom();
  var root = document.getElementById('daemon-status-indicator');
  var dot = document.getElementById('taskbar-conn-dot');
  var label = document.getElementById('taskbar-daemon-label');
  var tooltip = _daemonStatusTooltip(isConnected);
  if (root) {
    root.title = tooltip;
    if (typeof root.setAttribute === 'function') {
      root.setAttribute('data-daemon-status', isConnected ? 'running' : 'disconnected');
      root.setAttribute('aria-label', tooltip.replace(/\n/g, ', '));
    }
  }
  if (dot) dot.title = tooltip;
  if (label) label.textContent = 'Daemon';
}

function _setConnDotState(connected) {
  var ids = ['conn-dot', 'taskbar-conn-dot'];
  for (var i = 0; i < ids.length; i++) {
    var el = document.getElementById(ids[i]);
    if (!el) continue;
    if (connected) {
      el.classList.add('ok');
      el.title = 'Connected';
    } else {
      el.classList.remove('ok');
      el.title = 'Disconnected';
    }
  }
  refreshDaemonStatusIndicator(connected);
}

// WebSocket liveness watchdog. The daemon broadcasts a metrics tick to every
// client every ~2s, so a healthy socket always receives inbound traffic. If the
// socket still reports OPEN but no message has arrived for a while, it has gone
// half-open ("zombie") — the browser never fired onclose, so reconnect never
// ran and send() was silently dropping everything. Force-close it here so the
// existing onclose → reconnect path takes over.
var _lastWsInboundAt = 0;
var _wsLivenessTimer = null;
var WS_LIVENESS_CHECK_MS = 10000;
var WS_LIVENESS_STALE_MS = 30000;

function _noteWsInbound() {
  _lastWsInboundAt = (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
}

function _stopWsLivenessWatchdog() {
  if (_wsLivenessTimer !== null && typeof clearInterval === 'function') {
    clearInterval(_wsLivenessTimer);
  }
  _wsLivenessTimer = null;
}

function _startWsLivenessWatchdog() {
  _stopWsLivenessWatchdog();
  if (typeof setInterval !== 'function') return;
  _wsLivenessTimer = setInterval(function() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!_lastWsInboundAt) return;
    if (Date.now() - _lastWsInboundAt <= WS_LIVENESS_STALE_MS) return;
    // Stale OPEN socket: tear it down so onclose schedules a reconnect.
    try { ws.close(); } catch (e) { /* onclose/reconnect handles recovery */ }
  }, WS_LIVENESS_CHECK_MS);
}

function connect() {
  _firstStateReceived = false;
  _resyncPending = false;
  _awaitingFullState = false;
  var url = (typeof _compactPrepareWSUrl === 'function')
    ? _compactPrepareWSUrl(WS_URL)
    : WS_URL;
  ws = new WebSocket(url);
  ws.onopen = () => {
    _noteWsInbound();
    _startWsLivenessWatchdog();
    _setConnDotState(true);
    if (typeof _clearDaemonStoppedBanner === 'function'
        && typeof _daemonStopRequestedByUser !== 'undefined'
        && _daemonStopRequestedByUser) {
      _clearDaemonStoppedBanner();
    }
    if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
    if (typeof refreshStatusBar === 'function') refreshStatusBar({ connected: true });
    if (typeof statusBarRequestDeployState === 'function') {
      statusBarRequestDeployState({ force: true });
    }
  };
  ws.onclose = () => {
    _stopWsLivenessWatchdog();
    _resyncPending = false;
    _awaitingFullState = false;
    if (typeof _askResolvePending !== 'undefined') _askResolvePending = {};
    if (typeof _engineerResetSessionMapMeta === 'function') {
      _engineerResetSessionMapMeta({ clearStale: false });
    }
    _setConnDotState(false);
    if (typeof loadDaemonStatus === 'function') loadDaemonStatus();
    if (typeof refreshStatusBar === 'function') refreshStatusBar({ connected: false });
    if (typeof _daemonStopRequestedByUser !== 'undefined'
        && _daemonStopRequestedByUser
        && typeof _showDaemonStoppedBanner === 'function') {
      _showDaemonStoppedBanner();
    }
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = _handleWsMessage;
}

function _handleDelta(msg) {
  if (_awaitingFullState) return;
  if (msg.seq !== _expectedSeq) {
    // Sequence gap — request full resync
    _cancelPendingDeltaSurfaceRender();
    if (!_resyncPending) {
      _resyncPending = true;
      _awaitingFullState = true;
      send({ cmd: 'resync' });
    }
    return;
  }
  const prevGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
  const opGroupHints = _captureDeltaGroupHints(msg.ops);
  const invalidations = _deltaSurfaceInvalidations(msg.ops, opGroupHints);
  _expectedSeq = msg.seq + 1;
  _selectedAgentGroupSyncedDuringDelta = false;
  _applyDelta(msg.ops);
  _applyContextMeterDeltaUpdates(
    _collectContextMeterDeltaAgentIds(msg.ops, opGroupHints)
  );
  if (_statusBarDeltaNeedsRefresh(msg.ops, opGroupHints)) {
    // Routed through the rAF-coalesced surface batch below: the status
    // bar used to rebuild synchronously on every WS message, paying full
    // agent+task scans per socket frame under worker-firehose load.
    invalidations.statusbar = true;
  }
  const selectedAgentGroupSynced = _selectedAgentGroupSyncedDuringDelta;
  _selectedAgentGroupSyncedDuringDelta = false;
  const taskDeltaChanges = _collectBoardTaskDeltaChanges(msg.ops, opGroupHints);
  const agentDeltaChanges = _collectBoardAgentDeltaChanges(msg.ops, opGroupHints);
  if (taskDeltaChanges.length
      && typeof _agentPanelInvalidateWorkerTaskCacheForDeltas === 'function') {
    _agentPanelInvalidateWorkerTaskCacheForDeltas(taskDeltaChanges);
  }
  if (taskDeltaChanges.length
      && typeof _boardPatchDispatchStateTaskDeltas === 'function') {
    _boardPatchDispatchStateTaskDeltas(taskDeltaChanges);
  }
  if (agentDeltaChanges.length
      && typeof _boardPatchAssignedEngineerStatusAgentDeltas === 'function') {
    _boardPatchAssignedEngineerStatusAgentDeltas(agentDeltaChanges);
  }
  if (invalidations.board && typeof _boardQueueTaskDeltas === 'function') {
    _boardQueueTaskDeltas(
      taskDeltaChanges,
      { canPatch: _deltaOpsAreOnlyTaskDeltas(msg.ops) },
    );
  }
  const sessionMapGroups = _collectSessionMapInvalidationGroups(msg.ops, opGroupHints);
  if (sessionMapGroups.length && typeof _engineerMarkSessionMapStale === 'function') {
    _engineerMarkSessionMapStale(sessionMapGroups);
  }
  const nextGroup = (typeof _currentGroup === 'function') ? _currentGroup() : '';
  const activeSurfaces = typeof _currentPanelSurfaces === 'function'
    ? _currentPanelSurfaces()
    : [];
  if (prevGroup !== nextGroup
      && typeof _singleGroupModeEnabled === 'function'
      && _singleGroupModeEnabled()
      && !selectedAgentGroupSynced
      && typeof _activeGroupTransition === 'function') {
    const transition = _activeGroupTransition(prevGroup, nextGroup);
    if (transition && transition.changed) return;
  }
  if (prevGroup !== nextGroup) {
    activeSurfaces.forEach(function(surface) {
      if (surface) invalidations[surface] = true;
    });
    if (typeof healthActiveGroupChanged === 'function') {
      healthActiveGroupChanged();
    }
  } else {
    activeSurfaces.forEach(function(surface) {
      if (surface
          && invalidations[surface]
          && !_opsAffectCurrentSurfaceGroup(surface, nextGroup, msg.ops, opGroupHints)) {
        invalidations[surface] = false;
      }
    });
  }
  if (!dragInProgress) {
    _queueDeltaSurfaceRender(invalidations);
  }
}

function _rebuildChildren() {
  const children = {};
  for (const [id, cell] of Object.entries(state.agents)) {
    if (cell.cell_type === 'agent') children[id] = [];
  }
  for (const [id, cell] of Object.entries(state.agents)) {
    if (cell.parent_id && children[cell.parent_id]) {
      children[cell.parent_id].push(id);
    }
  }
  state.children = children;
}

/* -- Helpers -------------------------------------------------------------- */

function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  try {
    ws.send(JSON.stringify(obj));
    return true;
  } catch (_e) {
    return false;
  }
}

function _syncSelectionToActiveSession() {
  for (const [id, cell] of Object.entries(state.agents)) {
    if (typeof _isTombstonedAgent === 'function' && _isTombstonedAgent(cell)) continue;
    if (cell.session_id !== state.active_session_id) continue;
    selectedTerminalId = id;
    if (cell.cell_type === 'agent') {
      selectedAgentId = id;
      focusedItemId = id;
    } else if (cell.parent_id) {
      selectedAgentId = cell.parent_id;
      focusedItemId = id;
    } else {
      focusedItemId = id;
    }
    return;
  }
}
